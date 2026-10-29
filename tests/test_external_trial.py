"""The 'done' test: a Maven project that is not one of Proofline's fixtures, from verification to gate, with real Maven in Docker."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from proofline import cli, review
from proofline.project import ROOT

STOCK = "src/main/java/inventory/Stock.java"
FIXED = """package inventory;

public class Stock {
    private int available;

    public Stock(int available) {
        if (available < 0) throw new IllegalArgumentException("available must be >= 0");
        this.available = available;
    }

    /** Reserves qty units and returns how many remain. */
    public int reserve(int qty) {
        if (qty <= 0) throw new IllegalArgumentException("qty must be positive");
        if (qty > available) throw new IllegalStateException("not enough stock");
        available -= qty;
        return available;
    }

    public int available() {
        return available;
    }
}
"""


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "inventory repo"
    shutil.copytree(ROOT / "examples/inventory", r)
    for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(r), *c], check=True)
    return r


def last_run(repo: Path) -> str:
    return sorted((repo / ".proofline/runs").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-1].stem


def cmd(repo, *args):
    return cli.main([*args, "--repo", str(repo)])


def test_shouldRunExternalProjectFromVerificationToGateWhenUsingRealMaven(repo):
    (repo / STOCK).write_text(FIXED)
    assert cmd(repo, "verify") == 0  # PASS, all three requirements verified
    run = last_run(repo)
    assert cmd(repo, "gate", "--run", run) == review.NOT_APPROVED
    assert cmd(repo, "decide", "approve", "--run", run, "--reviewer", "ana") == 0
    assert cmd(repo, "gate", "--run", run) == review.OK

    (repo / STOCK).write_text(FIXED + "// edited after approval\n")
    assert cmd(repo, "gate", "--run", run) == review.CHANGED
    (repo / STOCK).write_text(FIXED)
    assert cmd(repo, "gate", "--run", run) == review.OK

    assert cmd(repo, "decide", "reject", "--run", run, "--reason", "changed my mind") == 0
    assert cmd(repo, "gate", "--run", run) == review.REJECTED

    f = repo / ".proofline/runs" / f"{run}.jsonl"
    f.write_text(f.read_text().replace('"PASS"', '"FAIL"', 1))
    assert cmd(repo, "gate", "--run", run) == review.CHAIN_INVALID


def test_shouldFailVerificationAndRequireAnOverrideToApproveWhenTheChangeIsWrong(repo):
    assert cmd(repo, "verify") == 4  # the stub does not satisfy the protected tests
    run = last_run(repo)
    records = [json.loads(l) for l in (repo / ".proofline/runs" / f"{run}.jsonl").read_text().splitlines()]
    verdict = records[-1]
    assert verdict["verdict"] == "FAIL" and verdict["fully_verified"] is False
    statuses = {s["id"]: s["status"] for s in verdict["scope"]}
    assert statuses == {"R1": "VERIFIED", "R2": "FAILED", "R3": "FAILED"}
    assert cmd(repo, "decide", "approve", "--run", run) == 2  # an override needs a reason
    assert cmd(repo, "decide", "approve", "--run", run, "--reason", "known, tracked elsewhere") == 0
    assert cmd(repo, "gate", "--run", run) == review.OK
    assert cmd(repo, "gate", "--run", run, "--strict") == review.OVERRIDE_STRICT
