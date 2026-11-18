"""Identify the current PR and Terraform changes for a trusted workflow run."""

import json
import re
import urllib.parse
import urllib.request

from trusted_check_guard import GuardError, head_sha, positive_int, require

REPO = "Nofraud/terraform-composite-actions"
REPO_ID = 1143516791
OWNER_ID = 15035518
SOURCE_WORKFLOW = ".github/workflows/pre-commit.yml"


class API:
    def __init__(self, token):
        self.token = token

    def call(self, path, data=None):
        require(path.startswith("/") and not path.startswith("//"))
        headers = {"Accept": "application/vnd.github+json", "Content-Type": "application/json",
                   "User-Agent": "trusted-terraform-checks"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
                                         data=body, headers=headers)
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            return json.load(response)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise GuardError()


def public_repository(repo):
    require(repo.get("id") == REPO_ID and repo.get("full_name") == REPO)
    require(repo.get("private") is False and repo.get("owner", {}).get("id") == OWNER_ID)


def verified_context(event, api):
    require(event.get("action") == "completed")
    public_repository(event["repository"])
    run_id = positive_int(event["workflow_run"]["id"])
    run = api.call("/actions/runs/" + str(run_id))
    public_repository(run["repository"])
    require(run.get("event") == "pull_request" and run.get("status") == "completed")
    require(run.get("path") == SOURCE_WORKFLOW)
    source_id = positive_int(run["head_repository"]["id"])
    run_head = head_sha(run["head_sha"])
    prs = run.get("pull_requests", [])
    if not prs:
        owner = run["head_repository"]["owner"]["login"]
        branch = run["head_branch"]
        require(isinstance(owner, str) and re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner))
        require(isinstance(branch, str) and 0 < len(branch) <= 255)
        query = urllib.parse.urlencode({"state": "open", "base": "main",
                                       "head": owner + ":" + branch, "per_page": 100})
        candidates = api.call("/pulls?" + query)
        require(isinstance(candidates, list) and len(candidates) < 100)
        prs = [pr for pr in candidates if pr.get("state") == "open"
               and pr.get("head", {}).get("sha") == run_head
               and pr.get("head", {}).get("repo", {}).get("id") == source_id]
    require(len(prs) == 1)
    number = positive_int(prs[0]["number"])
    pr = api.call("/pulls/" + str(number))
    require(pr.get("state") == "open" and pr.get("number") == number)
    public_repository(pr["base"]["repo"])
    require(pr["base"]["ref"] == "main")
    head = head_sha(pr["head"]["sha"])
    require(run_head == head and pr["head"]["repo"]["id"] == source_id)
    return {"run": run_id, "pr": number, "head": head, "base": head_sha(pr["base"]["sha"])}


def terraform_changed(api, number):
    # Match the private hook's native .tf scope; it does not parse .tf.json.
    for page in range(1, 11):
        files = api.call(f"/pulls/{number}/files?per_page=100&page={page}")
        require(isinstance(files, list))
        if any(isinstance(item.get(key), str) and item[key].endswith(".tf")
               for item in files for key in ("filename", "previous_filename")):
            return True
        if len(files) < 100:
            return False
    raise GuardError()
