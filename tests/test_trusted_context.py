"""Test workflow event and pull request identity validation."""

import copy
import urllib.parse

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))

from trusted_check_guard import GuardError

import terraform_pr_context as PREP
from trusted_pr_fixtures import EVENT, HEAD, FakeAPI




class TerraformContextTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()

    def test_wrong_source_repo_is_rejected_for_normal_pr(self):
        self.api.pr["head"]["repo"]["id"] = 123
        with self.assertRaises(GuardError):
            PREP.verified_context(EVENT, self.api)

    def test_empty_prs_use_exact_fork_owner_branch_sha_and_repo(self):
        self.api.run["pull_requests"] = []
        self.api.run["head_repository"] = {"id": 123, "owner": {"login": "fork-owner"}}
        self.api.pr["head"]["repo"]["id"] = 123
        self.assertEqual(PREP.verified_context(EVENT, self.api)["head"], HEAD)
        query = next(path for path in self.api.queries if path.startswith("/pulls?"))
        self.assertEqual(urllib.parse.parse_qs(query.split("?", 1)[1])["head"], ["fork-owner:feature/data"])

    def test_fork_fallback_rejects_wrong_source_or_ambiguous_pr(self):
        self.api.run["pull_requests"] = []
        self.api.run["head_repository"]["id"] = 123
        with self.assertRaises(GuardError):
            PREP.verified_context(EVENT, self.api)
        self.api.pr["head"]["repo"]["id"] = 123
        self.api.candidates = [self.api.pr, copy.deepcopy(self.api.pr)]
        with self.assertRaises(GuardError):
            PREP.verified_context(EVENT, self.api)

    def test_wrong_event_path_base_or_identity_is_rejected(self):
        for key, value in [("event", "push"), ("path", ".github/workflows/other.yml"), ("status", "in_progress")]:
            with self.subTest(key=key):
                api = FakeAPI()
                api.run[key] = value
                with self.assertRaises(GuardError):
                    PREP.verified_context(EVENT, api)
        self.api.pr["base"]["ref"] = "other"
        with self.assertRaises(GuardError):
            PREP.verified_context(EVENT, self.api)
        event = copy.deepcopy(EVENT)
        event["repository"]["id"] = 1
        with self.assertRaises(GuardError):
            PREP.verified_context(event, FakeAPI())

    def test_authenticated_api_never_follows_redirect(self):
        with self.assertRaises(GuardError):
            PREP.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid")


if __name__ == "__main__":
    unittest.main()
