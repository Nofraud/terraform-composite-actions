"""Run the trusted helper and checker wrapper with synthetic local fixtures."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/scripts/mint-ci-reader-token.sh"
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/private-terraform-checks.yml").read_text())
RULES = next(step["run"] for step in WORKFLOW["jobs"]["check"]["steps"] if step.get("id") == "rules")


class TrustedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.output = self.root / "outputs"
        self.output.touch()
        self.log = self.root / "requests.jsonl"
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin:/opt/homebrew/bin",
                    "RUNNER_TEMP": str(self.root), "GITHUB_OUTPUT": str(self.output),
                    "REQUEST_LOG": str(self.log), "BROKER": "gitops", "SCOPE": "other/repo",
                    "IDENTITY": "other", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request",
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.invalid/request?job=1"}
        curl = self.bin / "curl"
        curl.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["REQUEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if "--data-urlencode" in args:
    Path(args[args.index("-o") + 1]).write_text(os.environ.get("MOCK_BODY", '{"token":"synthetic-reader"}'))
    print(os.environ.get("MOCK_STATUS", "200"), end="")
    sys.exit(int(os.environ.get("MOCK_EXIT", "0")))
else:
    print('{"value":"synthetic-oidc"}')
''')
        curl.chmod(0o755)

    def helper(self, **overrides):
        self.output.write_text("")
        return subprocess.run(["bash", str(HELPER)], cwd=self.root,
                              env=dict(self.env, **overrides), text=True, capture_output=True)

    def test_fixed_broker_scope_identity_override_inherited_values(self):
        result = self.helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        requests = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn("https://oidc.invalid/request?job=1&audience=ci-reader.sts.nofraud.com", requests[0])
        self.assertIn("https://ci-reader.sts.nofraud.com/sts/exchange", requests[1])
        self.assertIn("scope=Nofraud/terraform-custom-checks", requests[1])
        self.assertIn("identity=terraform-composite-actions", requests[1])
        self.assertEqual(self.output.read_text(), "token=synthetic-reader\n")

    def test_invalid_or_partial_broker_responses_never_disclose_data(self):
        marker = "SYNTHETIC_PRIVATE_RESPONSE"
        fixtures = [
            {"MOCK_BODY": json.dumps({"token": {"nested": marker}})},
            {"MOCK_BODY": json.dumps({"token": marker + "\n"})},
            {"MOCK_BODY": json.dumps({"token": marker}) + "\n{}"},
            {"MOCK_BODY": '{"token":"' + marker, "MOCK_EXIT": "18"},
            {"MOCK_BODY": json.dumps({"token": marker}), "MOCK_STATUS": "403"},
        ]
        for fixture in fixtures:
            with self.subTest(kind=tuple(fixture)):
                result = self.helper(**fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(marker, result.stdout + result.stderr)
                self.assertEqual(self.output.read_text(), "")

    def test_checker_wrapper_withholds_raw_pr_output_and_credentials(self):
        (self.root / "main.tf").write_text("::error::UNTRUSTED_TF_DIRECTIVE\n")
        checker = self.root / "trusted-checker.sh"
        checker.write_text('''#!/bin/sh
cat main.tf
if [ -n "${GH_TOKEN+x}${ACTIONS_ID_TOKEN_REQUEST_TOKEN+x}" ]; then
  echo 'unexpected inherited credential'
fi
exit 1
''')
        checker.chmod(0o755)
        result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", RULES], cwd=self.root,
                                env=dict(self.env, CHECKER=str(checker), GH_TOKEN="synthetic-github"),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "result=failure\n")
        self.assertNotIn("UNTRUSTED_TF_DIRECTIVE", result.stdout + result.stderr)
        raw = (self.root / "terraform-check-output.txt").read_text()
        self.assertIn("UNTRUSTED_TF_DIRECTIVE", raw)
        self.assertNotIn("unexpected inherited credential", raw)

    def test_workflow_uses_only_main_and_fixed_checker_revision(self):
        job = WORKFLOW["jobs"]["check"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 2)
        self.assertTrue(all(step["with"]["persist-credentials"] is False for step in checkouts))
        self.assertEqual(checkouts[1]["with"]["ref"], "9d4191fca477b29dc824736ad01867f602f172b0")
        self.assertNotIn("pull_request_target", WORKFLOW.get("on", WORKFLOW.get(True, {})))


if __name__ == "__main__":
    unittest.main()
