"""Read Terraform files from a verified public ref as inert data."""

import subprocess
from pathlib import PurePosixPath

from trusted_check_guard import head_sha, positive_int, require

PUBLIC_URL = "https://github.com/Nofraud/terraform-composite-actions.git"
MAX_FILES = 500
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 10 * MAX_BLOB


class PublicGit:
    def __init__(self, root):
        self.directory = root / "public.git"
        self.home = root / "git-home"
        self.home.mkdir()
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_COUNT": "0", "GIT_TERMINAL_PROMPT": "0"}
        self.call("init", "--bare", str(self.directory))

    def call(self, *args):
        command = ["/usr/bin/git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null"]
        if args[0] != "init":
            command += ["--git-dir", str(self.directory)]
        result = subprocess.run(command + list(args), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, timeout=60)
        require(result.returncode == 0)
        return result.stdout

    def fetch(self, number, expected):
        self.call("fetch", "--quiet", "--no-tags", "--depth=1", PUBLIC_URL,
                  f"refs/pull/{positive_int(number)}/head")
        require(self.call("rev-parse", "FETCH_HEAD").decode().strip() == expected)


def safe_path(raw):
    path = raw.decode("utf-8", errors="strict")
    require(path and all(ord(char) >= 32 and ord(char) != 127 for char in path))
    require("\\" not in path and len(path) <= 240)
    parsed = PurePosixPath(path)
    require(not parsed.is_absolute() and not any(part in ("..", ".", ".git") for part in parsed.parts))
    require(str(parsed) == path)
    return parsed


def copy_terraform(git, destination):
    records = git.call("ls-tree", "-r", "-z", "--full-tree", "FETCH_HEAD").split(b"\0")
    count = total = 0
    for record in filter(None, records):
        metadata, raw_path = record.split(b"\t", 1)
        if not raw_path.endswith(b".tf"):
            continue
        mode, kind, oid = metadata.decode("ascii").split(" ")
        require(mode in ("100644", "100755") and kind == "blob")
        head_sha(oid)
        path = safe_path(raw_path)
        size = int(git.call("cat-file", "-s", oid))
        count += 1
        total += size
        require(count <= MAX_FILES and 0 <= size <= MAX_BLOB and total <= MAX_TOTAL)
        blob = git.call("cat-file", "blob", oid)
        require(len(blob) == size)
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(blob)
        target.chmod(0o600)
    return count
