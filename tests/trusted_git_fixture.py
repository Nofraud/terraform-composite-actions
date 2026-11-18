"""Mock public Git objects for Terraform data and integration tests."""

BLOB = "c" * 40


class FakeGit:
    def __init__(self, api=None, path="module/main.tf", mode="100644", kind="blob", stale=False):
        self.path, self.mode, self.kind = path, mode, kind
        self.api, self.stale = api, stale
        self.blob = b'# ::error::PR_DATA_MUST_NOT_BE_EMITTED\nterraform {}\n'
        self.reads = []

    def fetch(self, number, expected):
        assert (number, expected) == (9, 'a' * 40)
        if self.stale:
            self.api.pr["head"]["sha"] = "d" * 40

    def call(self, *args, input_data=None):
        self.reads.append(args)
        if args[0] == "ls-tree":
            return f"{self.mode} {self.kind} {BLOB}\t{self.path}\0".encode() + b"100644 blob " + BLOB.encode() + b"\tevil.sh\0"
        if args[:2] in (("cat-file", "--batch-check"), ("cat-file", "--batch")):
            assert input_data == BLOB.encode() + b"\n"
            header = f"{BLOB} blob {len(self.blob)}\n".encode()
            return header if args[1] == "--batch-check" else header + self.blob + b"\n"
        raise AssertionError("Unexpected Git invocation")
