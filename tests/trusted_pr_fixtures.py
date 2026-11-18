"""Mock GitHub API fixtures shared by the trusted checks tests."""

import copy

REPO = "Nofraud/terraform-composite-actions"
REPO_ID = 1143516791
OWNER_ID = 15035518
SOURCE_WORKFLOW = ".github/workflows/pre-commit.yml"
HEAD, BASE = "a" * 40, "b" * 40
REPOSITORY = {"id": REPO_ID, "full_name": REPO, "private": False,
              "owner": {"id": OWNER_ID}}
EVENT = {"action": "completed", "repository": REPOSITORY, "workflow_run": {"id": 42}}


class FakeAPI:
    def __init__(self):
        self.run = {"repository": REPOSITORY, "event": "pull_request", "status": "completed",
                    "path": SOURCE_WORKFLOW, "head_sha": HEAD,
                    "head_repository": {"id": REPO_ID, "owner": {"login": "Nofraud"}},
                    "head_branch": "feature/data", "pull_requests": [{"number": 9}]}
        self.pr = {"number": 9, "state": "open", "head": {"sha": HEAD, "repo": {"id": REPO_ID}},
                   "base": {"repo": REPOSITORY, "ref": "main", "sha": BASE}}
        self.files = [{"filename": "main.tf"}]
        self.candidates = [self.pr]
        self.posts, self.queries = [], []

    def call(self, path, data=None):
        self.queries.append(path)
        if path.startswith("/statuses/"):
            self.posts.append((path, data))
            return {}
        if path == "/actions/runs/42":
            return copy.deepcopy(self.run)
        if path == "/pulls/9":
            return copy.deepcopy(self.pr)
        if path.startswith("/pulls/9/files?"):
            return copy.deepcopy(self.files)
        if path.startswith("/pulls?"):
            return copy.deepcopy(self.candidates)
        raise AssertionError("Unexpected API path")
