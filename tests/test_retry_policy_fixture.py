"""The retry-policy fixture end to end through its manifest (real Maven in Docker): the empty skeleton, the seeded defect and
a correct solution must each get the verdict and the per-requirement scope they should."""
import shutil

import pytest

from proofline.project import load
from proofline.verifier import verify
from proofline.workspace import Edit, apply_edits

P = load("retry-policy")


def run(tmp_path, prepare):
    ws = tmp_path / "ws"
    shutil.copytree(P.fixture, ws)
    prepare(ws)
    r = verify(P.fixture, ws, tmp_path / "ev.jsonl", "r", project=P)
    return r, {c["name"]: c["status"] for c in r["checks"]}, {s["id"]: s["status"] for s in r["scope"]}


def seed(ws):
    shutil.copytree(P.seeds / "off-by-one", ws, dirs_exist_ok=True)


def solved(ws):
    seed(ws)
    apply_edits(ws, [Edit(path=P.editable[0], old="failedAttempts <= maxAttempts", new="failedAttempts < maxAttempts")], P.editable)


def test_shouldFailEveryRequirementWhenNothingIsImplemented(tmp_path):
    r, checks, scope = run(tmp_path, lambda ws: None)
    assert r["verdict"] == "FAIL" and checks["maven_tests"] == "PASS"  # the project's own tests cannot tell
    assert set(scope.values()) == {"FAILED"}


def test_shouldFailOnlyTheAttemptLimitWhenTheSeededOffByOneIsPresent(tmp_path):
    r, checks, scope = run(tmp_path, seed)
    assert r["verdict"] == "FAIL" and not r["fully_verified"]
    assert [n for n, s in checks.items() if s == "FAIL"] == ["acceptance_attempt_limit"]
    assert scope == {"R1": "FAILED", "R2": "VERIFIED", "R3": "VERIFIED"}


def test_shouldPassAndVerifyAllRequirementsWhenTheSeededDefectIsRepaired(tmp_path):
    r, checks, scope = run(tmp_path, solved)
    assert r["verdict"] == "PASS" and r["fully_verified"] and set(checks.values()) == {"PASS"}
    assert set(scope.values()) == {"VERIFIED"}


@pytest.mark.parametrize("path,old,new", [  # a wrong but plausible solution must never pass
    ("src/main/java/retry/Backoff.java", "return max;", "return delay;"),                       # no cap
    ("src/main/java/retry/RetryPolicy.java", "t.isInstance(error)", "t.equals(error.getClass())"),  # exact class only
])
def test_shouldNotPassWhenAPlausibleMutationBreaksTheContract(tmp_path, path, old, new):
    r, _, _ = run(tmp_path, lambda ws: (solved(ws), apply_edits(ws, [Edit(path=path, old=old, new=new)], P.editable)))
    assert r["verdict"] == "FAIL"
