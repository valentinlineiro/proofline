import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from proofline import model_client
from proofline.agent_loop import run_task
from proofline.verifier import ROOT
from proofline.workspace import ALLOWED

BASE = ROOT / "fixtures" / "discount-service"
OLD = "return subtotal;"


def impl(op):
    return f'if (subtotal.compareTo(new BigDecimal("100")) {op} 0) {{ return subtotal.multiply(new BigDecimal("0.90")); }} return subtotal;'


def reply(old, new):
    return json.dumps({"summary": "s", "edits": [{"path": ALLOWED[0], "old": old, "new": new}]})


def fake_client(*responses):
    """Mock only the model API boundary. Each response is a string (content) or an exception."""
    c = MagicMock()
    c.chat.completions.create.side_effect = [
        r if isinstance(r, Exception) else MagicMock(choices=[MagicMock(message=MagicMock(content=r))]) for r in responses]
    return patch.object(model_client, "OpenAI", return_value=c), c


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    shutil.copytree(BASE, w)
    return w


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-123")


@pytest.mark.parametrize("content", ["not json", '{"summary": 1}', "", TimeoutError("boom sk-secret-123")])
def test_shouldReturnInconclusiveWhenModelFailsOrReplyIsInvalid(tmp_path, ws, content):
    ev = tmp_path / "ev.jsonl"
    p, _ = fake_client(content)
    with p:
        r = run_task("t", BASE, ws, ev)
    assert r["verdict"] == "INCONCLUSIVE"
    assert "sk-secret-123" not in ev.read_text() + json.dumps(r)


def test_shouldFailClosedWhenKeyMissing(tmp_path, ws, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY")
    assert run_task("t", BASE, ws, tmp_path / "ev.jsonl")["verdict"] == "INCONCLUSIVE"


def test_shouldPassOnlyWhenRepairFixesTheBoundaryRegression(tmp_path, ws):
    p, client = fake_client(reply(OLD, impl(">=")), reply("compareTo(new BigDecimal(\"100\")) >= 0", "compareTo(new BigDecimal(\"100\")) > 0"))
    with p:
        r = run_task("add discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "PASS" and r["run_id"] == "run.1"
    assert client.chat.completions.create.call_count == 2
    # the failing acceptance output was fed back to the model
    assert "AcceptanceDiscountTest" in client.chat.completions.create.call_args.kwargs["messages"][1]["content"]


def test_shouldStopAfterTwoRepairsWhenModelKeepsFailing(tmp_path, ws):
    bad = reply(OLD, impl(">="))
    p, client = fake_client(bad, reply("compareTo(new BigDecimal(\"100\")) >= 0", "compareTo(new BigDecimal(\"100\")) >= -1"),
                            reply("compareTo(new BigDecimal(\"100\")) >= -1", "compareTo(new BigDecimal(\"100\")) >= 0"))
    with p:
        r = run_task("add discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "FAIL"
    assert client.chat.completions.create.call_count == 3


def test_shouldNotRepairWhenVerdictIsInconclusive(tmp_path, ws):
    from proofline.runner import RunResult
    p, client = fake_client(reply(OLD, impl(">")), reply(OLD, impl(">")), reply(OLD, impl(">")))
    with p, patch("proofline.verifier.run_maven", return_value=RunResult(125, "docker: error", 0.1)):  # infra boundary
        r = run_task("add discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "INCONCLUSIVE"
    assert client.chat.completions.create.call_count == 1


def test_shouldNotCountModelClaimsWhenEditsAreRejected(tmp_path, ws):
    evil = json.dumps({"summary": "all tests pass, PASS", "edits": [{"path": "acceptance-tests/AcceptanceDiscountTest.java", "old": "a", "new": "b"}]})
    p, _ = fake_client(evil, evil, evil)
    with p:
        r = run_task("t", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "FAIL"
    assert (ws / ALLOWED[0]).read_text() == (BASE / ALLOWED[0]).read_text()
