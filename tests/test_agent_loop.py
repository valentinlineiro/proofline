import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from evaluations.harness import fake_client as _client
from evaluations.harness import fake_tool_message
from proofline import model_client
from proofline.agent_loop import run_task
from proofline.verifier import ROOT
from proofline.workspace import ALLOWED

BASE = ROOT / "fixtures" / "discount-service"
OLD = "return subtotal;"


def impl(op):
    return f'if (subtotal.compareTo(new BigDecimal("100")) {op} 0) {{ return subtotal.multiply(new BigDecimal("0.90")).setScale(2, java.math.RoundingMode.HALF_UP); }} return subtotal;'


def reply(old, new):
    return json.dumps({"summary": "s", "edits": [{"path": ALLOWED[0], "old": old, "new": new}]})


def to_message(r):
    """Test replies stay readable as JSON; the model API boundary receives them as tool calls."""
    if isinstance(r, Exception):
        return r
    try:
        d = json.loads(r)
    except ValueError:
        return fake_tool_message(None, text=r)  # plain text: no tool call
    if isinstance(d, dict) and set(d) == {"clarification"}:
        return fake_tool_message("ask_clarification", {"question": d["clarification"]})
    return fake_tool_message("apply_edits", d)  # malformed arguments reach schema validation on purpose


def fake_client(*responses):
    """Mock only the model API boundary. Each response is a JSON string (edits or clarification), text, or an exception."""
    c = _client(*[to_message(r) for r in responses])
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


def test_shouldRepairSeededRoundingDefectUsingRealVerifierEvidence(tmp_path, ws):
    shutil.copytree(ROOT / "fixtures" / "seeds" / "half-even", ws, dirs_exist_ok=True)
    p, client = fake_client(reply("RoundingMode.HALF_EVEN", "RoundingMode.HALF_UP"))
    with p:
        r = run_task("round half up", BASE, ws, tmp_path / "ev.jsonl", verify_first=True)
    assert r["verdict"] == "PASS" and r["run_id"] == "run.0"
    assert client.chat.completions.create.call_count == 1  # the seeded verification did not consume an attempt
    prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "expected: <90.05> but was: <90.04>" in prompt


ASK = json.dumps({"clarification": "What percentage and which customers count as loyal?"})


def test_shouldReturnNeedsClarificationWhenModelAsksBeforeAnyVerification(tmp_path, ws):
    p, client = fake_client(ASK)
    with p, patch("proofline.verifier.run_maven") as maven:
        r = run_task("add a loyalty discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "NEEDS_CLARIFICATION" and "percentage" in r["question"]
    assert maven.call_count == 0 and client.chat.completions.create.call_count == 1  # nothing verified, no loop


def test_shouldKeepFailWhenModelAsksAfterAFailedVerification(tmp_path, ws):
    p, client = fake_client(reply(OLD, impl(">=")), ASK)
    with p:
        r = run_task("add discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "FAIL"  # a question must not launder a real failure


@pytest.mark.parametrize("content", ['{"summary": "x"}', json.dumps({"clarification": "q", "edits": [{"path": ALLOWED[0], "old": "a", "new": "b"}]})])
def test_shouldReturnInconclusiveWhenReplyIsNeitherEditsNorExactlyOneQuestion(tmp_path, ws, content):
    p, _ = fake_client(content)
    with p:
        assert run_task("t", BASE, ws, tmp_path / "ev.jsonl")["verdict"] == "INCONCLUSIVE"


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


# ---- explicit project, token usage -------------------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402

from proofline.evidence import EvidenceLog, verify_chain  # noqa: E402
from proofline.project import load as load_project  # noqa: E402

RP = load_project("retry-policy")


def records(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


@pytest.fixture
def rp_ws(tmp_path):
    w = tmp_path / "ws"
    shutil.copytree(RP.fixture, w)
    return w


def test_shouldShowTheModelOnlyTheSelectedProjectsEditableFiles(tmp_path, rp_ws):
    p, client = fake_client(ASK)
    with p:
        r = run_task("t", RP.fixture, rp_ws, tmp_path / "ev.jsonl", project=RP)
    prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert r["verdict"] == "NEEDS_CLARIFICATION"
    assert all(f in prompt for f in RP.editable) and "DiscountService" not in prompt
    assert records(tmp_path / "ev.jsonl")[0]["project"] == "retry-policy"


def test_shouldRecordTheDefaultProjectWhenNoneIsGiven(tmp_path, ws):
    p, _ = fake_client(ASK)
    with p:
        run_task("t", BASE, ws, tmp_path / "ev.jsonl")
    assert records(tmp_path / "ev.jsonl")[0]["project"] == "discount-service"


def test_shouldRejectEditsToProtectedFilesOfTheSelectedProjectWithoutRunningMaven(tmp_path, rp_ws):
    protected = "src/test/java/retry/RetryPolicyTest.java"
    evil = json.dumps({"summary": "weaken the test", "edits": [{"path": protected, "old": "assertThrows", "new": "// assertThrows"}]})
    before = (rp_ws / protected).read_text()
    p, client = fake_client(evil, evil, evil)
    with p, patch("proofline.verifier.run_maven") as maven:
        r = run_task("t", RP.fixture, rp_ws, tmp_path / "ev.jsonl", project=RP)
    assert r["verdict"] == "FAIL" and maven.call_count == 0 and (rp_ws / protected).read_text() == before
    assert [x["kind"] for x in records(tmp_path / "ev.jsonl")].count("edit_rejected") == 3


def reply_with_usage(usage):
    resp = MagicMock(choices=[MagicMock(message=to_message(ASK))])
    resp.usage = usage
    c = MagicMock()
    c.chat.completions.create.return_value = resp
    return patch.object(model_client, "OpenAI", return_value=c)


@pytest.mark.parametrize("usage,expected", [
    (SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
     {"available": True, "input_tokens": 120, "output_tokens": 30, "total_tokens": 150}),
    (SimpleNamespace(prompt_tokens=120, completion_tokens=30), {"available": True, "input_tokens": 120, "output_tokens": 30, "total_tokens": None}),
    (None, {"available": False}),
    (SimpleNamespace(prompt_tokens="120", completion_tokens=30), {"available": False}),   # not an int: never coerced or guessed
    (SimpleNamespace(prompt_tokens=True, completion_tokens=1), {"available": False}),
    (MagicMock(), {"available": False}),
])
def test_shouldRecordTokenUsageExactlyAsReportedOrMarkItUnavailable(tmp_path, ws, usage, expected):
    with reply_with_usage(usage):
        run_task("t", BASE, ws, tmp_path / "ev.jsonl")
    assert next(r for r in records(tmp_path / "ev.jsonl") if r["kind"] == "clarification_request")["usage"] == expected


def test_shouldRecordUsagePerModelCallWhenThereAreRepairs(tmp_path, ws):
    def resp(text, n):
        r = MagicMock(choices=[MagicMock(message=to_message(text))])
        r.usage = SimpleNamespace(prompt_tokens=n, completion_tokens=n // 10, total_tokens=n + n // 10)
        return r
    c = MagicMock()
    c.chat.completions.create.side_effect = [resp(reply(OLD, impl(">=")), 100),
                                             resp(reply("compareTo(new BigDecimal(\"100\")) >= 0", "compareTo(new BigDecimal(\"100\")) > 0"), 200)]
    with patch.object(model_client, "OpenAI", return_value=c):
        r = run_task("add discount", BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "PASS"
    assert [(x["attempt"], x["usage"]["input_tokens"]) for x in records(tmp_path / "ev.jsonl") if x["kind"] == "model_edits"] == [(0, 100), (1, 200)]


def test_shouldNotLetTheModelWriteItsOwnUsageIntoTheEvidence(tmp_path, ws):
    forged = json.dumps({"summary": "s", "edits": [{"path": ALLOWED[0], "old": OLD, "new": impl(">")}],
                         "usage": {"available": True, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
    p, _ = fake_client(forged)
    with p:
        run_task("t", BASE, ws, tmp_path / "ev.jsonl")
    edits = next(r for r in records(tmp_path / "ev.jsonl") if r["kind"] == "model_edits")
    assert edits["usage"] == {"available": False} and "usage" not in edits["proposal"]  # the API's numbers only, never the model's


def test_shouldKeepTracesWithAndWithoutTheProjectFieldVerifiable(tmp_path):
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    EvidenceLog(old).append("task", task="t", seeded=False)                       # shape written before the field existed
    EvidenceLog(new).append("task", task="t", seeded=False, project="retry-policy")
    assert verify_chain(old) and verify_chain(new)
