"""Test trusted staging, state, and result reporting."""

import importlib.util
import io
import os
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))

from trusted_check_guard import GuardError

from trusted_git_fixture import FakeGit
from trusted_pr_fixtures import EVENT, HEAD, FakeAPI

SPEC = importlib.util.spec_from_file_location("prepare", ROOT / ".github/scripts/prepare-terraform-pr.py")
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)




class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeAPI()
        self.env = patch.dict(os.environ, {"GITHUB_RUN_ID": "12345"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_prepare(self, git=None):
        git = git or FakeGit()
        return PREP.prepare(EVENT, self.api, self.root, lambda root: git)

    def test_only_regular_tf_data_is_copied_with_no_output(self):
        log = io.StringIO()
        with redirect_stdout(log), redirect_stderr(log):
            changed, context = self.run_prepare()
        self.assertTrue(changed)
        self.assertEqual(context["head"], HEAD)
        self.assertEqual(log.getvalue(), "")
        files = list((self.root / "terraform-data").rglob("*"))
        self.assertEqual([p.name for p in files if p.is_file()], ["main.tf"])
        self.assertEqual((self.root / "terraform-data/module/main.tf").stat().st_mode & 0o777, 0o600)

    def test_no_tf_change_skips_git_and_uses_distinct_status(self):
        self.api.files = [{"filename": "README.md"}]
        changed, _ = PREP.prepare(EVENT, self.api, self.root, lambda root: self.fail("Git must not run"))
        self.assertFalse(changed)
        PREP.post_status(EVENT, self.api, self.root, "success")
        self.assertIn("skipped", self.api.posts[-1][1]["description"])
        self.assertEqual(self.api.posts[-1][0], "/statuses/" + HEAD)

    def test_rename_away_from_tf_still_runs_checks(self):
        self.api.files = [{"filename": "notes.txt", "previous_filename": "main.tf"}]
        self.assertTrue(self.run_prepare()[0])

    def test_stale_after_fetch_is_rejected(self):
        with self.assertRaises(GuardError):
            self.run_prepare(FakeGit(self.api, stale=True))

    def test_status_revalidates_current_head(self):
        self.run_prepare()
        self.api.pr["head"]["sha"] = "d" * 40
        before = len(self.api.posts)
        with self.assertRaises(GuardError):
            PREP.post_status(EVENT, self.api, self.root, "success")
        self.assertEqual(len(self.api.posts), before)

    def test_state_store_never_follows_symlink_or_overwrites_hardlink(self):
        target = self.root / "outside"
        target.write_text("must remain unchanged")
        state = self.root / "verified.json"
        state.symlink_to(target)
        for value in (None, {"head": HEAD}):
            with self.subTest(value=value), self.assertRaises(OSError):
                PREP.state_store(self.root, value)
        state.unlink()
        os.link(target, state)
        with self.assertRaises(GuardError):
            PREP.state_store(self.root, {"head": HEAD})
        self.assertEqual(target.read_text(), "must remain unchanged")


if __name__ == "__main__":
    unittest.main()
