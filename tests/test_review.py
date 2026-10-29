import json
import shutil
import subprocess
from pathlib import Path

import pytest

from proofline import review
from proofline.change import change_hash, inputs_hash
from proofline.evidence import EvidenceLog
from proofline.project import ROOT, load_external
from proofline.verifier import tree_hash

SERVICE = "src/main/java/shop/DiscountService.java"
MANIFEST = {"id": "shop", "acceptance_dest": "src/test/java/shop", "editable": [SERVICE],
            "checks": [{"name": "maven_tests", "selector": "!Acceptance*"},
                       {"name": "acceptance_discount_applies", "selector": "AcceptanceDiscountTest#shouldApplyTenPercentWhenSubtotalExceedsLimit"},
                       {"name": "acceptance_discount_rounding", "selector": "AcceptanceDiscountTest#shouldRoundHalfUpToCentsWhenDiscountLeavesFractionOfCent"},
                       {"name": "acceptance_discount_boundary", "selector": "AcceptanceDiscountTest#shouldNotDiscountWhenSubtotalEqualsLimit"}],
            "default_task": "t"}
SCOPE = [{"id": "R1", "text": "x", "status": "VERIFIED", "checks": ["maven_tests"]}]


class Run:
    """A repo with a verified change: the pristine base, the edited working tree, and a run file with real hashes."""

    def __init__(self, tmp_path: Path, verdict="PASS", fully=True, base_kind="dir"):
        self.repo, self.base, self.file = tmp_path / "repo", tmp_path / "base", tmp_path / "run.jsonl"
        shutil.copytree(ROOT / "fixtures/discount-service", self.repo)
        shutil.copytree(ROOT / "acceptance-tests", self.repo / ".proofline/acceptance")
        (self.repo / ".proofline/project.json").write_text(json.dumps(MANIFEST))
        shutil.copytree(self.repo, self.base)
        (self.repo / SERVICE).write_text((self.repo / SERVICE).read_text() + "\n// the change\n")
        self.project = load_external(self.repo)
        base_ref = {"kind": "dir", "path": str(self.base)}
        if base_kind == "git":
            for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(self.base), *c], check=True)
            rev = subprocess.run(["git", "-C", str(self.base), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            shutil.copytree(self.base / ".git", self.repo / ".git")
            base_ref = {"kind": "git", "rev": rev}
        log = EvidenceLog(self.file)
        log.append("task", task="t", project="shop")
        log.append("run_start", run_id="r1", base_revision=tree_hash(self.base), diff_sha256="d", diff="",
                   change_sha256=change_hash(self.base, self.repo), base_ref=base_ref,
                   inputs_sha256=inputs_hash(self.project.manifest, self.project.acceptance))
        log.append("verdict", verdict=verdict, scope=SCOPE, fully_verified=fully)

    def records(self):
        return [json.loads(l) for l in self.file.read_text().splitlines() if l]

    def gate(self, strict=False):
        return review.gate(self.file, self.repo, strict=strict)

    def approve(self, reason="", reviewer="ana"):
        return review.decide(self.file, "approve", reason, reviewer)


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


def test_shouldExit0WhenApprovedAndUnchanged(run):
    run.approve()
    assert run.gate() == review.OK == 0


@pytest.mark.parametrize("base_kind", ["git"])
def test_shouldGateAgainstAGitBase(tmp_path, base_kind):
    r = Run(tmp_path, base_kind=base_kind)
    r.approve()
    assert r.gate() == review.OK


def test_shouldExit10WhenNoDecision(run):
    assert run.gate() == review.NOT_APPROVED == 10


def test_shouldExit11WhenRejected(run):
    review.decide(run.file, "reject", "wrong rounding", "ana")
    assert run.gate() == review.REJECTED == 11


def test_shouldExit12WhenFileEditedAfterApproval(run):
    run.approve()
    (run.repo / SERVICE).write_text("class X {}")
    assert run.gate() == review.CHANGED == 12


def test_shouldExit12WhenFileDeletedAfterApproval(run):
    run.approve()
    (run.repo / SERVICE).unlink()
    assert run.gate() == review.CHANGED


def test_shouldExit12WhenManifestEditedAfterApproval(run):
    run.approve()
    (run.repo / ".proofline/project.json").write_text(json.dumps({**MANIFEST, "default_task": "other"}))
    assert run.gate() == review.CHANGED


def test_shouldExit12WhenProtectedTestEditedAfterApproval(run):
    run.approve()
    t = run.repo / ".proofline/acceptance/AcceptanceDiscountTest.java"
    t.write_text(t.read_text() + "\n// weaker\n")
    assert run.gate() == review.CHANGED


def test_shouldExit12WhenTheBaseChanged(run):
    run.approve()
    (run.base / "pom.xml").write_text("<project/>")
    assert run.gate() == review.CHANGED


def test_shouldExit13WhenChainTampered(run):
    run.approve()
    run.file.write_text(run.file.read_text().replace('"PASS"', '"FAIL"', 1))
    assert run.gate() == review.CHAIN_INVALID == 13


def test_shouldExit13WhenLastLineIsHalfWritten(run):
    run.approve()
    run.file.write_text(run.file.read_text() + '{"kind": "human_dec')
    assert run.gate() == review.CHAIN_INVALID


@pytest.mark.parametrize("content", ["", None])
def test_shouldExit14WhenRunHasNoVerification(run, content):
    if content is None:  # only a task record, no verification
        log = EvidenceLog(run.file)
        log.append("task", task="t", project="shop")
    else:
        run.file.write_text(content)
    assert run.gate() == review.NO_VERIFICATION == 14


def test_shouldExit15WhenOverrideUnderStrict(tmp_path):
    r = Run(tmp_path, verdict="FAIL", fully=False)
    r.approve("known and accepted")
    assert r.gate(strict=True) == review.OVERRIDE_STRICT == 15


def test_shouldExit0WhenOverrideWithoutStrictAndPrintNotice(tmp_path, capsys):
    r = Run(tmp_path, verdict="PASS", fully=False)
    r.approve("gap accepted")
    assert r.gate() == review.OK
    assert "override" in capsys.readouterr().out.lower()


def test_shouldLetLatestDecisionWin(run):
    run.approve()
    review.decide(run.file, "reject", "changed my mind", "ana")
    assert run.gate() == review.REJECTED
    run.approve()
    assert run.gate() == review.OK


def test_shouldNotCountADecisionMadeOnAnEarlierVerification(run):
    run.approve()
    log = EvidenceLog.resume(run.file)
    log.append("run_start", run_id="r2", base_revision="b", diff_sha256="d", diff="", change_sha256="c")
    log.append("verdict", verdict="PASS", scope=SCOPE, fully_verified=True)
    assert run.gate() == review.NOT_APPROVED


def test_shouldRefuseApprovalOfNeedsClarification(tmp_path):
    f = tmp_path / "r.jsonl"
    log = EvidenceLog(f)
    log.append("task", task="t")
    log.append("verdict", verdict="NEEDS_CLARIFICATION", question="?")
    with pytest.raises(review.ReviewError, match="clarification"):
        review.decide(f, "approve", "", "ana")


def test_shouldRequireReasonOnRejectAndOnOverride(tmp_path, run):
    with pytest.raises(review.ReviewError):
        review.decide(run.file, "reject", "  ", "ana")
    gap = Run(tmp_path / "g", verdict="PASS", fully=False)
    with pytest.raises(review.ReviewError, match="override"):
        gap.approve("")


def test_shouldRefuseToDecideOnATamperedChain(run):
    run.file.write_text(run.file.read_text().replace('"PASS"', '"FAIL"', 1))
    with pytest.raises(review.ReviewError, match="chain"):
        run.approve()


def test_shouldComputeOverrideFromVerdictNotFromCaller(tmp_path):
    r = Run(tmp_path, verdict="FAIL", fully=False)
    rec = r.approve("accepted")
    assert rec["override"] is True and rec["verdict"] == "FAIL" and rec["scope"] == SCOPE
    clean = Run(tmp_path / "c")
    assert clean.approve()["override"] is False


def test_shouldRecordWhoWhenAndWhatWasJudged(run):
    rec = run.approve(reviewer="ana")
    last = run.records()[-1]
    assert last["kind"] == "human_decision" and last["reviewer"] == "ana" and last["run_id"] == "r1"
    assert last["change_sha256"] == change_hash(run.base, run.repo) and last["time"] and rec["decision"] == "approve"


def test_shouldLabelSignals(tmp_path):
    rej = Run(tmp_path / "a")
    review.decide(rej.file, "reject", "no", "ana")
    gap = Run(tmp_path / "b", verdict="PASS", fully=False)
    gap.approve("ok")
    fail = Run(tmp_path / "c", verdict="FAIL", fully=False)
    fail.approve("ok")
    inc = Run(tmp_path / "d", verdict="INCONCLUSIVE", fully=False)
    inc.approve("ok")
    clean = Run(tmp_path / "e")
    clean.approve()
    assert review.signals(rej.records()) == ["REJECTED_PASS"]
    assert review.signals(gap.records()) == ["APPROVED_WITH_GAPS"]
    assert review.signals(fail.records()) == ["APPROVED_FAIL"]
    assert review.signals(inc.records()) == ["APPROVED_INCONCLUSIVE"]
    assert review.signals(clean.records()) == []
