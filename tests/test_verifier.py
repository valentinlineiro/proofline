import shutil
from pathlib import Path

import pytest

from proofline.evidence import verify_chain
from proofline.runner import RunResult
from proofline.verifier import ROOT, classify, verify

BASE = ROOT / "fixtures" / "discount-service"
SERVICE = "src/main/java/shop/DiscountService.java"

IMPL = """package shop;
import java.math.BigDecimal;
public class DiscountService {
    public BigDecimal total(BigDecimal subtotal) {
        if (subtotal.compareTo(new BigDecimal("100")) %s 0) {
            return subtotal.multiply(new BigDecimal("0.90")).setScale(2, java.math.RoundingMode.HALF_UP);
        }
        return subtotal;
    }
}
"""


def workspace(tmp_path: Path, op: str | None) -> Path:
    ws = tmp_path / "ws"
    shutil.copytree(BASE, ws)
    if op:
        (ws / SERVICE).write_text(IMPL % op)
    return ws


def run(tmp_path, op):
    ev = tmp_path / "ev.jsonl"
    result = verify(BASE, workspace(tmp_path, op), ev)
    assert verify_chain(ev)
    return result, {c["name"]: c["status"] for c in result["checks"]}


def test_shouldFailWhenBoundaryRegressionPassesNormalTests(tmp_path):
    result, s = run(tmp_path, ">=")
    assert s == {"maven_tests": "PASS", "acceptance_discount_applies": "PASS",
                 "acceptance_discount_rounding": "PASS", "acceptance_discount_boundary": "FAIL"}
    assert result["verdict"] == "FAIL"


def test_shouldFailWhenFeatureMissing(tmp_path):
    result, s = run(tmp_path, None)
    assert s["acceptance_discount_applies"] == "FAIL" and result["verdict"] == "FAIL"


def test_shouldPassWhenImplementationIsCorrect(tmp_path):
    result, _ = run(tmp_path, ">")
    assert result["verdict"] == "PASS"


def test_shouldFailOnlyRoundingCheckWhenHalfEvenIsUsed(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(BASE, ws)
    shutil.copytree(ROOT / "fixtures" / "seeds" / "half-even", ws, dirs_exist_ok=True)
    result = verify(BASE, ws, tmp_path / "ev.jsonl")
    assert {c["name"]: c["status"] for c in result["checks"]} == {
        "maven_tests": "PASS", "acceptance_discount_applies": "PASS",
        "acceptance_discount_rounding": "FAIL", "acceptance_discount_boundary": "PASS"}
    assert result["verdict"] == "FAIL"
    assert "expected: <90.05> but was: <90.04>" in result["failures"][0]["tail"]


@pytest.mark.parametrize("r,expected", [
    (RunResult(0, "Tests run: 0, Failures: 0", 1), "INCONCLUSIVE"),   # green but nothing ran
    (RunResult(-1, "", 1, timed_out=True), "INCONCLUSIVE"),
    (RunResult(125, "docker: error", 1), "INCONCLUSIVE"),
    (RunResult(1, "Could not resolve dependencies", 1), "INCONCLUSIVE"),
    (RunResult(1, "Tests run: 1, Failures: 1 <<< FAILURE!", 1), "FAIL"),
    (RunResult(0, "Tests run: 2, Failures: 0", 1), "PASS"),
])
def test_shouldClassifyRunWhenOutcomeIsAmbiguous(r, expected):
    assert classify(r) == expected
