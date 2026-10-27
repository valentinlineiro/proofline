import pytest

from evaluations import harness


@pytest.mark.parametrize("verdict,violated,fault,expected", [
    ("PASS", True, False, "FALSE_PASS"),
    ("PASS", False, False, "TRUE_PASS"),
    ("FAIL", True, False, "DETECTED"),
    ("FAIL", False, False, "FALSE_FAIL"),
    ("INCONCLUSIVE", False, False, "UNEXPECTED_INCONCLUSIVE"),
    ("INCONCLUSIVE", False, True, "CORRECT_INCONCLUSIVE"),
    ("PASS", False, True, "FALSE_PASS"),
    ("FAIL", False, True, "UNEXPECTED_FAIL"),
])
def test_shouldClassifyOutcomeWhenVerdictAndOracleAreKnown(verdict, violated, fault, expected):
    assert harness.classify_outcome(verdict, violated, fault) == expected


def test_shouldReportKnownFalsePassWhenDefectIsOutsideMandatoryChecks(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setattr(harness, "OUT", tmp_path)
    r = harness.run_one(harness.load("U1"), "sim", 0)
    # documents current behaviour: the verifier accepts what its mandatory checks cannot see
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("PASS", "FAIL", "FALSE_PASS")
    assert r["missing_evidence"] == []


def test_shouldReportCorrectInconclusiveWhenMandatoryCheckCannotRun(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setattr(harness, "OUT", tmp_path)
    r = harness.run_one(harness.load("U2"), "sim", 0)
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("INCONCLUSIVE", "PASS", "CORRECT_INCONCLUSIVE")
    assert r["missing_evidence"] == []
