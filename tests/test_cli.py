import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from proofline import cli, review
from proofline.project import ROOT
from proofline.runner import RunResult

SERVICE = "src/main/java/shop/DiscountService.java"
OK_RUN = RunResult(0, "Tests run: 3, Failures: 0, Errors: 0", 0.1)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    """A git repo with spaces in its path, initialised for Proofline, committed, then given one uncommitted change."""
    r = tmp_path / "my repo"
    shutil.copytree(ROOT / "fixtures/discount-service", r)
    git(r, "init", "-q")
    assert cli.main(["init", "--repo", str(r)]) == 0
    (r / ".proofline/acceptance/requirements.json").write_text(
        json.dumps([{"id": "R1", "text": "unit tests pass", "checks": ["maven_tests"]}]))
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    (r / SERVICE).write_text((r / SERVICE).read_text() + "\n// the change\n")
    return r


def verify(repo: Path, *extra: str) -> int:
    with patch("proofline.verifier.run_maven", return_value=OK_RUN):
        return cli.main(["verify", "--repo", str(repo), *extra])


def runs(repo: Path) -> list[Path]:
    return sorted((repo / ".proofline/runs").glob("*.jsonl"))


def test_shouldInitSkeletonAndRefuseOverwrite(tmp_path):
    r = tmp_path / "r"
    shutil.copytree(ROOT / "fixtures/discount-service", r)
    assert cli.main(["init", "--repo", str(r)]) == 0
    manifest = json.loads((r / ".proofline/project.json").read_text())
    assert manifest["editable"] == [SERVICE] and manifest["checks"] == [{"name": "maven_tests", "selector": "!Acceptance*"}]
    assert (r / ".proofline/acceptance/requirements.json").read_text().strip() == "[]"
    before = (r / ".proofline/project.json").read_text()
    assert cli.main(["init", "--repo", str(r)]) == 2
    assert (r / ".proofline/project.json").read_text() == before


def test_shouldRefuseInitWithoutAPom(tmp_path):
    assert cli.main(["init", "--repo", str(tmp_path)]) == 2


def test_shouldVerifyAndWriteRunFileInsideProoflineDir(repo):
    assert verify(repo) == 0  # PASS and fully verified
    [f] = runs(repo)
    assert f.parent == repo / ".proofline/runs"
    kinds = [json.loads(l)["kind"] for l in f.read_text().splitlines()]
    assert kinds[0] == "task" and "run_start" in kinds and kinds[-1] == "verdict"


def test_shouldRecordBaseRefAndHashesInRunStart(repo):
    verify(repo)
    start = next(r for r in map(json.loads, runs(repo)[0].read_text().splitlines()) if r["kind"] == "run_start")
    assert start["base_ref"] == {"kind": "git", "rev": git(repo, "rev-parse", "HEAD")}
    assert start["change_sha256"] and start["inputs_sha256"]


def test_shouldDecideAndGateOkEndToEnd(repo):
    assert verify(repo) == 0
    run_id = runs(repo)[0].stem
    assert cli.main(["gate", "--repo", str(repo), "--run", run_id]) == review.NOT_APPROVED
    assert cli.main(["decide", "approve", "--repo", str(repo), "--run", run_id, "--reviewer", "ana"]) == 0
    assert cli.main(["gate", "--repo", str(repo), "--run", run_id]) == review.OK
    (repo / SERVICE).write_text("class Other {}")
    assert cli.main(["gate", "--repo", str(repo), "--run", run_id]) == review.CHANGED


def test_shouldReturnUsageErrorOnBadArguments(repo):
    assert cli.main(["gate", "--repo", str(repo)]) == 2  # neither --run nor --latest
    assert cli.main(["decide", "reject", "--repo", str(repo), "--run", "nope"]) == 2


def test_shouldPickLatestRun(repo):
    verify(repo)
    verify(repo)
    first, second = runs(repo)
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))
    cli.main(["decide", "approve", "--repo", str(repo), "--run", second.stem])
    assert cli.main(["gate", "--repo", str(repo), "--latest"]) == review.OK


def test_shouldNotTreatProoflineDirAsChange(repo):
    verify(repo)
    run_id = runs(repo)[0].stem
    cli.main(["decide", "approve", "--repo", str(repo), "--run", run_id])
    verify(repo)  # writes a second run file inside .proofline/
    assert len(runs(repo)) == 2
    assert cli.main(["gate", "--repo", str(repo), "--run", run_id]) == review.OK


def test_shouldVerifyAgainstADirectoryBase(repo, tmp_path):
    base = tmp_path / "pristine"
    shutil.copytree(repo, base)
    (base / SERVICE).write_text(git(repo, "show", f"HEAD:{SERVICE}"))
    assert verify(repo, "--base", str(base)) == 0
    start = next(r for r in map(json.loads, runs(repo)[0].read_text().splitlines()) if r["kind"] == "run_start")
    assert start["base_ref"]["kind"] == "dir"


def test_shouldNeedABaseWhenRepoIsNotGit(tmp_path):
    r = tmp_path / "r"
    shutil.copytree(ROOT / "fixtures/discount-service", r)
    cli.main(["init", "--repo", str(r)])
    assert verify(r) == 2
