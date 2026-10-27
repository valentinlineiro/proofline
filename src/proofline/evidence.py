"""Append-only JSONL evidence log; each record carries the hash of the previous one."""
import hashlib
import json
from pathlib import Path


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


class EvidenceLog:
    def __init__(self, path: Path):
        self.path = path
        self.prev = "0" * 64
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def append(self, kind: str, **fields) -> None:
        record = {"kind": kind, "prev": self.prev, **fields}
        record["sha256"] = sha256(json.dumps(record, sort_keys=True))
        self.prev = record["sha256"]
        with self.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def verify_chain(path: Path) -> bool:
    prev = "0" * 64
    for line in path.read_text().splitlines():
        record = json.loads(line)
        claimed = record.pop("sha256")
        if record["prev"] != prev or sha256(json.dumps(record, sort_keys=True)) != claimed:
            return False
        prev = claimed
    return True
