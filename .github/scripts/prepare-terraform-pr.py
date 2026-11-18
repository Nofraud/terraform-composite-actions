#!/usr/bin/env python3
"""Prepare and report trusted checks for a verified Terraform PR."""

import json
import os
from pathlib import Path
import stat
import sys

from terraform_pr_context import API, REPO, terraform_changed, verified_context
from terraform_pr_data import PublicGit, copy_terraform
from trusted_check_guard import require

STATUS_CONTEXT = "Trusted Terraform checks"


def state_store(root, value=None):
    # The hosted runner supplies this directory; PR data never sets the filename.
    require(root.is_absolute())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        flags = os.O_RDONLY if value is None else os.O_WRONLY | os.O_CREAT
        descriptor = os.open("verified.json", flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1)
        if value is not None:
            os.fchmod(descriptor, 0o600)
            os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "r" if value is None else "w") as stream:
            descriptor = None
            if value is None:
                return json.load(stream)
            json.dump(value, stream)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def prepare(event, api, root, git_factory=PublicGit):
    context = verified_context(event, api)
    state_store(root, dict(context, changed=True))
    post_status(event, api, root, "pending")
    changed = terraform_changed(api, context["pr"])
    state_store(root, dict(context, changed=changed))
    destination = root / "terraform-data"
    destination.mkdir()
    if changed:
        git = git_factory(root)
        git.fetch(context["pr"], context["head"])
        copy_terraform(git, destination)
    require(verified_context(event, api) == context)
    return changed, context


def post_status(event, api, root, state):
    require(state in ("pending", "success", "failure"))
    saved = state_store(root)
    require(type(saved["changed"]) is bool)
    require(verified_context(event, api) == {key: saved[key] for key in ("run", "pr", "head", "base")})
    description = {"pending": "Trusted checks are running", "success": "Trusted checks passed",
                   "failure": "Trusted checks failed; inspect the trusted job"}[state]
    if state == "success" and not saved["changed"]:
        description = "No Terraform changes; trusted checks skipped"
    api.call("/statuses/" + saved["head"], {"state": state, "context": STATUS_CONTEXT,
             "description": description,
             "target_url": f"https://github.com/{REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"})


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    temporary = Path(os.environ["RUNNER_TEMP"])
    require(temporary.is_absolute())
    root = temporary / "trusted-terraform-pr"
    api = API(os.environ["GH_TOKEN"])
    if len(sys.argv) == 2 and sys.argv[1] == "prepare":
        root.mkdir()
        try:
            changed, context = prepare(event, api, root)
        except Exception:
            try:
                post_status(event, api, root, "failure")
            except Exception:
                pass
            raise
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"changed={str(changed).lower()}\nhead={context['head']}\npr={context['pr']}\n")
    else:
        require(len(sys.argv) == 3 and sys.argv[1] == "status")
        post_status(event, api, root, sys.argv[2])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Trusted Terraform preparation/status failed closed.", file=sys.stderr)
        sys.exit(1)
