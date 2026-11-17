"""Exercise action run blocks with local command stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {
    name: yaml.safe_load((ROOT / name / "action.yml").read_text())
    for name in ("pre-commit", "commit")
}


def step(action, name):
    return next(s for s in ACTIONS[action]["runs"]["steps"] if s["name"] == name)


class ActionInputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.bin = self.cwd / "bin"
        self.bin.mkdir()
        (self.cwd / ".venv/bin").mkdir(parents=True)
        (self.cwd / ".venv/bin/activate").touch()
        self.env = dict(os.environ, HOME=str(self.cwd), PATH=f"{self.bin}:{os.environ['PATH']}")
        self.env["ARGV_LOG"] = str(self.cwd / "argv.jsonl")
        self.stub("pre-commit", "import json, os, sys\nwith open(os.environ['ARGV_LOG'], 'a') as f:\n    f.write(json.dumps(sys.argv[1:]) + '\\n')\n")

    def stub(self, name, code):
        target = self.bin / name
        target.write_text(f"#!{sys.executable}\n{code}")
        target.chmod(0o755)

    def run_block(self, block, **env):
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", block],
            cwd=self.cwd, env=dict(self.env, **env), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def logged(self):
        path = Path(self.env["ARGV_LOG"])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_args(self, args):
        result = self.run_block(step("pre-commit", "Execute pre-commit")["run"], PRE_COMMIT_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.logged()[-1]

    def test_default_arguments(self):
        args = ACTIONS["pre-commit"]["inputs"]["args"]["default"]
        self.assertEqual(self.run_args(args), ["run", "--all-files", "--color", "always", "--show-diff-on-failure"])

    def test_quoted_spaces_and_empty_argument(self):
        self.assertEqual(self.run_args('--files "a b.tf" \'c d.tf\' ""'), ["run", "--files", "a b.tf", "c d.tf", ""])

    def test_glob_preserves_filename_spaces(self):
        (self.cwd / "a b.tf").touch()
        (self.cwd / "z.tf").touch()
        self.assertEqual(self.run_args("--files *.tf"), ["run", "--files", "a b.tf", "z.tf"])

    def test_quoted_directory_with_unquoted_glob(self):
        (self.cwd / "with space").mkdir()
        (self.cwd / "with space/main.tf").touch()
        self.assertEqual(self.run_args('--files "with space"/*.tf'), ["run", "--files", "with space/main.tf"])

    def test_quoted_and_escaped_wildcards_stay_literal(self):
        (self.cwd / "match.tf").touch()
        self.assertEqual(self.run_args('--files "*.tf" \'*.tf\' \\*.tf'), ["run", "--files", "*.tf", "*.tf", "*.tf"])

    def test_unmatched_glob_stays_literal(self):
        self.assertEqual(self.run_args("--files missing/*.tf"), ["run", "--files", "missing/*.tf"])

    def test_legacy_root_selection_uses_glob(self):
        (self.cwd / "a b.tf").touch()
        (self.cwd / "z.tf").touch()
        (self.cwd / "other.txt").touch()
        self.assertEqual(self.run_args("terraform_validate --files $(ls *.tf)"), ["run", "terraform_validate", "--files", "a b.tf", "z.tf"])

    def test_legacy_root_selection_without_matches(self):
        self.assertEqual(self.run_args("--files $(ls *.tf)"), ["run", "--files"])

    def test_quoted_legacy_expression_stays_literal(self):
        (self.cwd / "main.tf").touch()
        self.assertEqual(self.run_args('--files "$(ls *.tf)"'), ["run", "--files", "$(ls *.tf)"])

    def test_metacharacters_and_substitutions_never_execute(self):
        tick = chr(96)
        args = '--files "$(touch marker)" "' + tick + 'touch marker' + tick + '" ";touch marker" "${HOME}"'
        self.assertEqual(self.run_args(args), ["run", "--files", "$(touch marker)", tick + "touch marker" + tick, ";touch marker", "${HOME}"])
        self.assertFalse((self.cwd / "marker").exists())

    def test_unquoted_command_substitution_never_executes(self):
        self.assertEqual(self.run_args("--files $(touch marker)"), ["run", "--files", "$(touch", "marker)"])
        self.assertFalse((self.cwd / "marker").exists())

    def test_malformed_quotes_fail_before_execution(self):
        result = self.run_block(step("pre-commit", "Execute pre-commit")["run"], PRE_COMMIT_ARGS='--files "unfinished')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid pre-commit arguments", result.stderr)
        self.assertEqual(self.logged(), [])

    def version_env(self):
        result = {}
        inputs = ACTIONS["pre-commit"]["inputs"]
        for key, expression in step("pre-commit", "Validate version inputs")["env"].items():
            input_name = expression.removeprefix("${{ inputs.").removesuffix(" }}")
            result[key] = str(inputs[input_name].get("default", ""))
        result["TERRAFORM_VERSION"] = "1.9.8"
        return result

    def test_release_versions_and_opentofu_defaults(self):
        block = step("pre-commit", "Validate version inputs")["run"]
        for overrides in ({}, {"USE_OPENTOFU": "true", "TERRAFORM_VERSION": ""}, {"INSTALL_HCLEDIT": "true", "INSTALL_TFSEC": "true", "INSTALL_TRIVY": "true", "TFLINT_VERSION": "v0.59.0"}):
            result = self.run_block(block, **dict(self.version_env(), **overrides))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_terraform_installer_valid_version_reaches_extract_and_cleanup(self):
        for command in ("terraform", "rm", "curl", "sudo"):
            self.stub(command, "import json, os, sys\nwith open(os.environ['ARGV_LOG'], 'a') as f:\n    f.write(json.dumps([" + repr(command) + ", *sys.argv[1:]]) + '\\n')\n")
        installer = next(item for item in ACTIONS["pre-commit"]["runs"]["steps"] if item["name"].startswith("Install Terraform"))
        block = installer["run"].replace("${{ env.OS }}", "linux").replace("${{ env.ARCH }}", "amd64")
        result = self.run_block(block, TERRAFORM_VERSION="1.9.8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.logged()
        self.assertIn(["sudo", "unzip", "-qq", "terraform.zip", "terraform", "-d", "/usr/bin/"], calls)
        self.assertEqual(calls[-1], ["rm", "terraform.zip"])
        self.assertEqual(calls[-3][-1], "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip")

    def test_invalid_installer_inputs_fail(self):
        block = step("pre-commit", "Validate version inputs")["run"]
        for overrides in (
            {"TERRAFORM_VERSION": ""},
            {"TERRAFORM_VERSION": "1.9.8;touch marker"},
            {"TERRAFORM_DOCS_VERSION": "v0.20.0/../../other"},
            {"TFLINT_VERSION": "latest;touch marker"},
            {"USE_OPENTOFU": "true", "OPENTOFU_VERSION": "$(touch marker)"},
            {"INSTALL_TRIVY": "true", "TRIVY_VERSION": "0.69.2 bad"},
            {"INSTALL_HCLEDIT": "yes"},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_block(block, **dict(self.version_env(), **overrides))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.cwd / "marker").exists())

    def stub_git(self):
        real_git = shutil.which("git")
        code = (
            "import json, os, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['check-ref-format']:\n"
            f"    sys.exit(subprocess.run([{real_git!r}, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode)\n"
            "with open(os.environ['ARGV_LOG'], 'a') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
        )
        self.stub("git", code)

    def test_commit_uses_target_repository_and_one_ref_argument(self):
        self.stub_git()
        result = self.run_block(
            step("commit", "Commit files")["run"],
            GITHUB_ACTOR="test-actor", GITHUB_TOKEN="test-credential",
            GITHUB_REPOSITORY="Nofraud/caller", TARGET_REPOSITORY="Nofraud/target",
            GIT_BRANCH="fix/safe-inputs",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logged()[-1], ["push", "https://test-actor:test-credential@github.com/Nofraud/target.git", "HEAD:fix/safe-inputs"])

    def test_invalid_repository_and_branch_fail_before_commit(self):
        self.stub_git()
        for repo, branch in (("Nofraud/target;touch marker", "fix/safe"), ("Nofraud/target", "$(touch marker)"), ("../target", "fix/safe")):
            result = self.run_block(
                step("commit", "Commit files")["run"], GITHUB_ACTOR="test-actor",
                GITHUB_TOKEN="test-credential", TARGET_REPOSITORY=repo, GIT_BRANCH=branch,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.logged(), [])
            self.assertFalse((self.cwd / "marker").exists())

    def test_inputs_are_not_interpolated_into_shell(self):
        for action in ACTIONS.values():
            for item in action["runs"]["steps"]:
                self.assertNotIn("${{ inputs.", item.get("run", ""))


    def reader_workflow(self):
        return yaml.safe_load((ROOT / ".github/workflows/pre-commit.yml").read_text())

    def reader_env(self):
        workflow = self.reader_workflow()
        action = next(s for s in workflow["jobs"]["preCommitMaxVersion"]["steps"]
                      if s.get("uses") == "./pre-commit")
        env = dict(os.environ, **action["env"], HOME=str(self.cwd),
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0")
        env.pop("GIT_CONFIG_PARAMETERS", None)
        env["CI_READER_TOKEN"] = "fixture-reader-value"
        return env

    def test_private_hook_jobs_mint_only_scoped_read_tokens(self):
        workflow = self.reader_workflow()
        for name, job in workflow["jobs"].items():
            checkouts = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")]
            self.assertTrue(all(s["with"]["persist-credentials"] is False for s in checkouts))
            if name == "collectInputs":
                self.assertNotEqual(job.get("permissions", {}).get("id-token"), "write")
                self.assertFalse(any(s.get("id") == "ci-reader" for s in job["steps"]))
                continue
            self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write"})
            mint = next(s for s in job["steps"] if s.get("id") == "ci-reader")
            self.assertEqual(mint["with"], {
                "broker": "ci-reader", "scope": "Nofraud/terraform-custom-checks",
                "identity": "terraform-composite-actions",
            })
            self.assertEqual(mint["uses"], "Nofraud/reusable-gh-actions/.github/actions/octo-sts-token@production")
            for index, action in enumerate(job["steps"]):
                if action.get("uses") != "./pre-commit":
                    continue
                self.assertLess(job["steps"].index(mint), index)
                self.assertEqual(action["env"]["CI_READER_TOKEN"], "${{ steps.ci-reader.outputs.token }}")

    def test_private_hook_credential_works_in_fresh_cache_only_for_target(self):
        declared = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
        self.assertIn("https://github.com/Nofraud/terraform-custom-checks",
                      [repo["repo"] for repo in declared["repos"]])
        env = self.reader_env()
        for host, path, expected in (
            ("github.com", "Nofraud/terraform-custom-checks", True),
            ("github.com", "Nofraud/other", False),
            ("other.invalid", "Nofraud/terraform-custom-checks", False),
        ):
            with self.subTest(host=host, path=path):
                result = subprocess.run(
                    ["git", "credential", "fill"], cwd=self.cwd, env=env,
                    input=f"protocol=https\nhost={host}\npath={path}\n\n",
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode == 0, expected)
                self.assertEqual("password=fixture-reader-value" in result.stdout, expected)
        self.assertFalse((self.cwd / ".git-credentials").exists())

    def test_private_hook_credential_is_not_saved_on_approval(self):
        result = subprocess.run(
            ["git", "credential", "approve"], cwd=self.cwd, env=self.reader_env(),
            input="protocol=https\nhost=github.com\npath=Nofraud/terraform-custom-checks\nusername=x-access-token\npassword=fixture-reader-value\n\n",
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.cwd / ".git-credentials").exists())



if __name__ == "__main__":
    unittest.main()
