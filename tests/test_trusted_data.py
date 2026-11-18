"""Test credential-free Git and inert Terraform file staging."""

import subprocess
import tempfile
from unittest.mock import patch

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))

from trusted_check_guard import GuardError

import terraform_pr_data as PREP
from trusted_git_fixture import FakeGit


class TerraformDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_symlink_and_gitlink_are_rejected(self):
        for mode, kind in [("120000", "blob"), ("160000", "commit")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(GuardError):
                    PREP.copy_terraform(FakeGit(mode=mode, kind=kind), Path(directory))

    def test_unsafe_paths_are_rejected(self):
        for name in ("../outside.tf", "/absolute.tf", ".git/config.tf", "bad\n::error::.tf", "bad\\name.tf"):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(GuardError):
                    PREP.copy_terraform(FakeGit(path=name), Path(directory))

    def test_public_git_has_no_credentials_or_inherited_environment(self):
        result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"ignored")
        with patch.object(PREP.subprocess, "run", return_value=result) as run:
            git = PREP.PublicGit(self.root)
        args, kwargs = run.call_args
        self.assertIn("credential.helper=", args[0])
        self.assertNotIn("GH_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(git.directory, self.root / "public.git")


if __name__ == "__main__":
    unittest.main()
