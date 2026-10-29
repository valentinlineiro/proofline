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


# ---- retry: a rejection reason goes back to the agent (simulated model only) ----
from proofline.model_client import EditSet  # noqa: E402
from proofline.workspace import Edit  # noqa: E402


@pytest.fixture
def budget(tmp_path, monkeypatch):
    from proofline import budget as b
    ledger = tmp_path / "ledger.json"
    b.init(str(ledger), 3)
    monkeypatch.setenv("MODEL_API_KEY", "simulated")
    monkeypatch.setenv("MODEL_CALL_BUDGET_FILE", str(ledger))
    return ledger


def rejected(repo, reason="the rounding is wrong") -> str:
    verify(repo)
    run = runs(repo)[0].stem
    assert cli.main(["decide", "reject", "--repo", str(repo), "--run", run, "--reason", reason]) == 0
    return run


def retry(repo, run, proposal=None):
    proposal = proposal or EditSet(summary="fixed", edits=[Edit(path=SERVICE, old="// the change", new="// retried")])
    with patch("proofline.verifier.run_maven", return_value=OK_RUN), \
         patch("proofline.model_client.propose_edits", return_value=proposal) as model:
        return cli.main(["retry", "--repo", str(repo), "--run", run]), model


def test_shouldRetryWithRejectionReasonAsFeedback(repo, budget):
    run = rejected(repo)
    code, model = retry(repo, run)
    assert code == 0 and model.call_count == 1
    assert "the rounding is wrong" in model.call_args.args[2]  # propose_edits(task, files, feedback)
    kinds = [json.loads(l)["kind"] for l in (repo / ".proofline/runs" / f"{run}.jsonl").read_text().splitlines()]
    last_edit = len(kinds) - 1 - kinds[::-1].index("model_edits")
    assert last_edit < len(kinds) - 1 - kinds[::-1].index("run_start") and kinds[-1] == "verdict"
    assert "// retried" in (repo / SERVICE).read_text()
    assert cli.main(["gate", "--repo", str(repo), "--run", run]) == review.NOT_APPROVED  # the earlier decision judged the old change


def test_shouldRefuseRetryWithoutBudgetLedger(repo, monkeypatch):
    run = rejected(repo)
    monkeypatch.setenv("MODEL_API_KEY", "simulated")
    monkeypatch.delenv("MODEL_CALL_BUDGET_FILE", raising=False)
    code, model = retry(repo, run)
    assert code == 2 and model.call_count == 0


def test_shouldRefuseRetryWhenLastDecisionIsNotARejection(repo, budget):
    verify(repo)
    run = runs(repo)[0].stem
    code, model = retry(repo, run)
    assert code == 2 and model.call_count == 0
    cli.main(["decide", "approve", "--repo", str(repo), "--run", run])
    code, model = retry(repo, run)
    assert code == 2 and model.call_count == 0


def test_shouldRecordReasonOnlyWhenAttemptsAreExhausted(repo, budget):
    run = rejected(repo)
    marks = ["// the change", "// r1", "// r2"]
    for old, new in zip(marks, marks[1:]):  # MAX_REPAIRS = 2 retries are allowed
        code, model = retry(repo, run, EditSet(summary="s", edits=[Edit(path=SERVICE, old=old, new=new)]))
        assert code == 0 and model.call_count == 1
        cli.main(["decide", "reject", "--repo", str(repo), "--run", run, "--reason", "again"])
    code, model = retry(repo, run)
    assert code == 2 and model.call_count == 0
    assert cli.main(["gate", "--repo", str(repo), "--run", run]) == review.REJECTED  # the reason is recorded, nothing else happens


# ---- fixes from the final review ----
def test_shouldGenerateRunIdsTheApiAccepts(repo):
    import re
    verify(repo)
    assert re.fullmatch(r"[a-z0-9-]{1,40}", runs(repo)[0].stem)


def test_shouldNotFailOrLeakWhenAGitIgnoredFileExists(repo):
    (repo / ".gitignore").write_text(".env\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore env")
    (repo / ".env").write_text("SECRET=hunter2")
    assert verify(repo) == 0
    run = runs(repo)[0]
    assert "hunter2" not in run.read_text()
    cli.main(["decide", "approve", "--repo", str(repo), "--run", run.stem])
    assert cli.main(["gate", "--repo", str(repo), "--run", run.stem]) == review.OK


def test_shouldPickLatestRunByIdNotByModificationTime(repo):
    verify(repo)
    verify(repo)
    older, newer = sorted(runs(repo), key=lambda p: p.stem)
    os.utime(newer, (1, 1))   # a CI checkout gives every file the same mtime; mtime must not decide
    os.utime(older, (2, 2))
    cli.main(["decide", "approve", "--repo", str(repo), "--run", newer.stem])
    assert cli.main(["gate", "--repo", str(repo), "--latest"]) == review.OK
