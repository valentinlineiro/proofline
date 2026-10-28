"""The global cap on real model calls. No API, no credits: the model client is always a fake at the API boundary."""
import json
import shutil
import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from evaluations import harness
from proofline import budget, model_client
from proofline.agent_loop import run_task
from proofline.model_client import ModelError, propose_edits
from proofline.project import load as load_project


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    f = tmp_path / "budget.json"
    budget.init(f, 9)
    monkeypatch.setenv(budget.ENV, str(f))
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-123")
    return f


def used(f):
    return json.loads(f.read_text())["used"]


def fake_api(side_effect=None):
    reply = MagicMock(choices=[MagicMock(message=MagicMock(tool_calls=[MagicMock(**{"function.name": "ask_clarification", "function.arguments": json.dumps({"question": "q?"})})]))])
    c = MagicMock()
    c.chat.completions.create.side_effect = side_effect or (lambda **kw: reply)
    return patch.object(model_client, "OpenAI", return_value=c), c


def test_shouldAllowNineCallsAndBlockTheTenthWithoutContactingTheApi(ledger):
    p, c = fake_api()
    with p:
        for _ in range(9):
            assert propose_edits("t", {"a.java": "x"}).clarification == "q?"
        with pytest.raises(ModelError, match="call budget"):
            propose_edits("t", {"a.java": "x"})
    assert c.chat.completions.create.call_count == 9 and used(ledger) == 9


def test_shouldCountACallThatFailsAndNeverRefundIt(ledger):
    p, c = fake_api(side_effect=TimeoutError("boom"))
    with p:
        for _ in range(9):
            with pytest.raises(ModelError, match="model call failed"):
                propose_edits("t", {"a.java": "x"})
        with pytest.raises(ModelError, match="call budget"):
            propose_edits("t", {"a.java": "x"})
    assert c.chat.completions.create.call_count == 9 and used(ledger) == 9  # errors cannot buy extra calls


def test_shouldDisableHiddenSdkRetriesOnlyWhenABudgetIsActive(ledger, monkeypatch):
    p, _ = fake_api()
    with p as client_class:
        propose_edits("t", {"a.java": "x"})
    assert client_class.call_args.kwargs["max_retries"] == 0
    monkeypatch.delenv(budget.ENV)
    p, _ = fake_api()
    with p as client_class:
        propose_edits("t", {"a.java": "x"})
    assert client_class.call_args.kwargs["max_retries"] == 1  # unchanged outside the guarded phase


def test_shouldShareOneBudgetAcrossProcessesSoRunsCannotResetIt(tmp_path):
    f = tmp_path / "b.json"
    budget.init(f, 9)
    code = "import sys; from proofline.budget import reserve, BudgetError\nn = 0\nfor _ in range(3):\n    reserve(sys.argv[1]); n += 1\nprint(n)"
    for _ in range(3):  # three separate processes (three 'runs'), three calls each
        assert subprocess.run([sys.executable, "-c", code, str(f)], capture_output=True, text=True, check=True).stdout.strip() == "3"
    assert used(f) == 9
    again = subprocess.run([sys.executable, "-c", code, str(f)], capture_output=True, text=True)
    assert again.returncode != 0 and "spent" in again.stderr and used(f) == 9


def test_shouldNeverExceedTheLimitUnderConcurrency(tmp_path):
    f = tmp_path / "b.json"
    budget.init(f, 9)
    ok, refused = [], []
    def worker():
        try:
            ok.append(budget.reserve(f))
        except budget.BudgetError:
            refused.append(1)
    threads = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(ok) == list(range(1, 10)) and len(refused) == 31 and used(f) == 9


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"limit": 9}', '{"limit": "9", "used": 0}', '{"limit": 9, "used": -1}',
                                     '{"limit": 0, "used": 0}', '{"limit": true, "used": 0}', '{"limit": 9, "used": 1.5}'])
def test_shouldRefuseTheCallWhenTheLedgerCannotBeTrusted(tmp_path, monkeypatch, content):
    f = tmp_path / "b.json"
    f.write_text(content)
    monkeypatch.setenv(budget.ENV, str(f)); monkeypatch.setenv("MODEL_API_KEY", "k")
    p, c = fake_api()
    with p, pytest.raises(ModelError, match="call budget"):
        propose_edits("t", {"a.java": "x"})
    assert c.chat.completions.create.call_count == 0 and f.read_text() == content  # no call, ledger untouched


def test_shouldRefuseTheCallWhenTheLedgerIsMissingOrUnwritable(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "k")
    for setup in ("missing", "readonly"):
        f = tmp_path / f"{setup}.json"
        if setup == "readonly":
            budget.init(f, 9); f.chmod(0o444)
        monkeypatch.setenv(budget.ENV, str(f))
        p, c = fake_api()
        with p, pytest.raises(ModelError, match="call budget"):
            propose_edits("t", {"a.java": "x"})
        assert c.chat.completions.create.call_count == 0


def test_shouldNotLetInitResetAnExistingLedgerOrCreateAnInvalidOne(tmp_path):
    f = tmp_path / "b.json"
    budget.init(f, 9)
    budget.reserve(f)
    with pytest.raises(budget.BudgetError, match="refusing"):
        budget.init(f, 9)
    assert used(f) == 1
    for bad in (0, -3, True, 2.5):
        with pytest.raises(budget.BudgetError):
            budget.init(tmp_path / "other.json", bad)


def test_shouldReturnInconclusiveFromTheAgentLoopOnceTheBudgetIsSpentWithoutCallingTheApi(tmp_path, ledger, monkeypatch):
    spent = tmp_path / "spent.json"
    budget.init(spent, 1); budget.reserve(spent)
    monkeypatch.setenv(budget.ENV, str(spent))
    P = load_project("retry-policy")
    ws = tmp_path / "ws"
    shutil.copytree(P.fixture, ws)
    p, c = fake_api()
    with p:
        r = run_task("t", P.fixture, ws, tmp_path / "ev.jsonl", project=P)
    assert r["verdict"] == "INCONCLUSIVE" and "call budget" in r["error"] and c.chat.completions.create.call_count == 0


def test_shouldStopTheWholeBatchWhenTheBudgetRunsOut(tmp_path, ledger, monkeypatch):
    f = ledger
    f.unlink(); budget.init(f, 2)  # room for two calls; five runs requested
    monkeypatch.setattr(harness, "OUT", tmp_path / "eval")
    p, c = fake_api()  # every call is a clarification: each run costs exactly one call and needs no Maven
    with p, pytest.raises(SystemExit, match="STOPPED"):
        harness.main(["real", "5", "--project", "retry-policy", "RP1"])
    rows = json.loads((tmp_path / "eval" / "summary-real-retry-policy.json").read_text())
    assert c.chat.completions.create.call_count == 2 and used(f) == 2
    assert [r["verdict"] for r in rows] == ["NEEDS_CLARIFICATION", "NEEDS_CLARIFICATION", "INCONCLUSIVE"]  # the 3rd run was refused...
    assert rows[-1]["budget_exhausted"] and len(rows) == 3                                                   # ...and runs 4 and 5 never started


def test_shouldNotSpendTheRealLedgerOnSimulatedRuns(tmp_path, ledger, monkeypatch):
    monkeypatch.setattr(harness, "OUT", tmp_path / "eval")
    harness.main(["sim", "1", "--project", "retry-policy", "RP2"])  # a simulated run calls propose_edits too
    assert used(ledger) == 0


def test_shouldBeUsableFromTheCommandLine(tmp_path):
    f = tmp_path / "b.json"
    run = lambda *a: subprocess.run([sys.executable, "-m", "proofline.budget", *a], capture_output=True, text=True)
    assert run("init", str(f), "9").returncode == 0 and "'remaining': 9" in run("status", str(f)).stdout
    assert run("init", str(f), "9").returncode != 0           # no reset
    assert run("init", str(tmp_path / "x"), "zero").returncode != 0


RACER = """
import sys, time
from pathlib import Path
from proofline.budget import reserve, BudgetError
ledger, go = sys.argv[1], Path(sys.argv[2])
while not go.exists():          # every process spins here until the same instant, then they all hit the ledger together
    pass
try:
    reserve(ledger); print("OK")
except BudgetError:
    print("REFUSED")
"""


@pytest.mark.parametrize("limit,racers", [(1, 24), (9, 30)])
def test_shouldGiveTheLastSlotsToExactlyTheRightNumberOfSimultaneousProcesses(tmp_path, limit, racers):
    for round_ in range(3):  # repeat: a race that is lost once in a while must show up
        f, go = tmp_path / f"b{round_}.json", tmp_path / f"go{round_}"
        budget.init(f, limit)
        procs = [subprocess.Popen([sys.executable, "-c", RACER, str(f), str(go)], stdout=subprocess.PIPE, text=True) for _ in range(racers)]
        go.touch()
        outs = [p.communicate()[0].strip() for p in procs]
        assert outs.count("OK") == limit and outs.count("REFUSED") == racers - limit, outs
        assert used(f) == limit


def test_shouldHaveASingleCountedRouteToTheModelApiInTheSourceTree():
    """No module other than model_client may build an API client or send a completion; the one documented exception is the
    pre-flight listing in scripts/demo-prep.sh, which lists models and never generates text."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    clients = [p.relative_to(root).as_posix() for d in ("src", "evaluations", "scripts") for p in (root / d).rglob("*")
               if p.is_file() and p.suffix in (".py", ".sh") and "OpenAI(" in p.read_text()]
    assert sorted(clients) == ["scripts/demo-prep.sh", "src/proofline/model_client.py"]
    demo = (root / "scripts/demo-prep.sh").read_text()
    assert ".models.list()" in demo and "chat.completions" not in demo and ".create(" not in demo
    sends = [p.relative_to(root).as_posix() for d in ("src", "evaluations", "scripts") for p in (root / d).rglob("*")
             if p.is_file() and p.suffix in (".py", ".sh") and "chat.completions.create(" in p.read_text()]
    assert sends == ["src/proofline/model_client.py"]  # (the harness only configures a MagicMock attribute: no call syntax)
    for d in ("src", "evaluations", "scripts"):        # no other HTTP route to the API host
        assert not [p for p in (root / d).rglob("*") if p.is_file() and p.suffix in (".py", ".sh") and p.name != "model_client.py"
                    and "api.meta.ai" in p.read_text() and p.name != "demo-prep.sh"]


def test_shouldPrintTheLaunchPlanWithoutSpendingAnythingOnADryRun(tmp_path, ledger, monkeypatch, capsys):
    monkeypatch.setattr(harness, "OUT", tmp_path / "eval")
    monkeypatch.setattr(model_client, "OpenAI", lambda **kw: pytest.fail("a dry run must not create a model client"))
    harness.main(["real", "3", "--project", "retry-policy", "RP1", "--dry-run"])
    plan = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in plan["tasks"]] == ["RP1"] and plan["tasks"][0]["seed"] is None  # three runs of RP1, and no RP2
    assert (plan["runs"], plan["max_calls_per_run"], plan["worst_case_model_calls"]) == (3, 3, 9)
    assert plan["call_budget"] == {"limit": 9, "used": 0, "remaining": 9} and plan["uses_real_api"] and plan["model"]
    assert used(ledger) == 0 and not (tmp_path / "eval").exists()  # nothing was created, nothing was counted


def test_shouldRefuseADryRunOfAnInvalidSelectionToo(ledger, monkeypatch):
    with pytest.raises(SystemExit, match="unknown project"):
        harness.main(["real", "3", "--project", "nope", "--dry-run"])
