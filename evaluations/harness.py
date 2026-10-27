"""Evaluation harness. Runs a task through the unmodified agent loop + verifier, then judges the outcome with an
independent oracle (the full acceptance suite, run separately). The agent only ever sees `task`, never the rest."""
import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv
from pydantic import BaseModel

from proofline import model_client, verifier
from proofline.agent_loop import run_task
from proofline.evidence import verify_chain
from proofline.runner import RunResult
from proofline.verifier import ROOT, verify
from proofline.workspace import ALLOWED

TASKS = Path(__file__).parent / "tasks"
OUT = Path(os.environ.get("PROOFLINE_EVAL_OUT", ROOT / "evidence" / "eval"))
BASE = ROOT / "fixtures" / "discount-service"


class EvalTask(BaseModel):
    id: str
    title: str
    requirement: str
    task: str
    initial_state: str
    mandatory_checks: list[str]
    fault: str | None
    known_defect: str | None
    expected_verdict: str | None
    rationale: str
    expected_evidence: list[str]
    simulated_edit: dict[str, str]
    simulated_defective_edit: dict[str, str] | None = None
    group: str = "adversarial"
    acceptance_dir: str | None = None       # oracle tests for this task (None: the verifier's default)
    checks: list[list[str]] | None = None   # full check list = the oracle (None: the verifier's default)
    outcome_kind: str = "oracle"            # "ungrounded": no oracle can judge the requirement
    clarification_required: bool = False


def load(task_id: str) -> EvalTask:
    return EvalTask.model_validate_json((TASKS / f"{task_id}.json").read_text())


def classify_outcome(verdict: str, oracle_violated: bool, fault_injected: bool, ungrounded: bool = False) -> str:
    if verdict == "NEEDS_CLARIFICATION":  # requirements incomplete: neither a pass nor a failure of the code
        return "NEEDS_CLARIFICATION"
    if ungrounded:  # nothing independent can say whether the requirement was met
        return "UNGROUNDED_PASS" if verdict == "PASS" else f"UNGROUNDED_{verdict}"
    if fault_injected:  # a mandatory check could not run: only INCONCLUSIVE is a correct answer
        return {"INCONCLUSIVE": "CORRECT_INCONCLUSIVE", "PASS": "FALSE_PASS"}.get(verdict, "UNEXPECTED_FAIL")
    if verdict == "PASS":
        return "FALSE_PASS" if oracle_violated else "TRUE_PASS"
    if verdict == "FAIL":
        return "DETECTED" if oracle_violated else "FALSE_FAIL"
    return "UNEXPECTED_INCONCLUSIVE"


def _fake_model(edit: dict[str, str]):
    reply = json.dumps({"summary": "simulated", "edits": [{"path": ALLOWED[0], "old": edit["old"], "new": edit["new"]}]})
    c = MagicMock()
    c.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=reply))]
    return patch.object(model_client, "OpenAI", return_value=c)


def _faulty_runner(selector: str):
    real = verifier.run_maven
    def run(workdir, args, timeout_s=300):
        if any(selector in a for a in args):
            return RunResult(125, "simulated infrastructure failure", 0.0)
        return real(workdir, args, timeout_s)
    return patch.object(verifier, "run_maven", run)


def _evidence_facts(path: Path) -> set[str]:
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    start = max(i for i, r in enumerate(recs) if r["kind"] == "run_start") if any(r["kind"] == "run_start" for r in recs) else 0
    facts = {f"check:{r['name']}={r['status']}" for r in recs[start:] if r["kind"] == "check"}
    facts |= {f"verdict:{r['verdict']}" for r in recs[start:] if r["kind"] == "verdict"}
    return facts | ({"chain_valid"} if verify_chain(path) else set())


def run_one(t: EvalTask, model: str, n: int) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"{t.id}-{model}-{n}"
    full_checks = [tuple(c) for c in t.checks] if t.checks else list(verifier.CHECKS)
    acceptance = ROOT / t.acceptance_dir if t.acceptance_dir else verifier.ACCEPTANCE_DIR
    profile = [c for c in full_checks if c[0] in t.mandatory_checks]
    fault_sel = dict(full_checks)[t.fault] if t.fault else None
    with tempfile.TemporaryDirectory(prefix="proofline-eval-") as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(BASE, ws)
        with ExitStack() as stack:
            stack.enter_context(patch.object(verifier, "CHECKS", profile))
            stack.enter_context(patch.object(verifier, "ACCEPTANCE_DIR", acceptance))
            if fault_sel:
                stack.enter_context(_faulty_runner(fault_sel))
            if model in ("sim", "sim-bad"):  # no real API involved: a dummy key must not be required
                stack.enter_context(patch.dict(os.environ, {"MODEL_API_KEY": "simulated"}))
                stack.enter_context(_fake_model(t.simulated_edit if model == "sim" else t.simulated_defective_edit))
            result = run_task(t.task, BASE, ws, OUT / f"{name}.jsonl", name)
        # oracle: independent, full suite on the final workspace, never seen by the agent
        with patch.object(verifier, "CHECKS", full_checks), patch.object(verifier, "ACCEPTANCE_DIR", acceptance):
            oracle = verify(BASE, ws, OUT / f"{name}.oracle.jsonl", f"{name}.oracle")
    recs = [json.loads(l) for l in (OUT / f"{name}.jsonl").read_text().splitlines()]
    edits = [r["proposal"] for r in recs if r["kind"] == "model_edits"]
    outcome = classify_outcome(result["verdict"], oracle["verdict"] == "FAIL", fault_sel is not None,
                               t.outcome_kind == "ungrounded")
    return {"task": t.id, "group": t.group, "model": model, "run": n, "verdict": result["verdict"],
            "oracle_verdict": oracle["verdict"], "outcome": outcome, "attempts": len(edits),
            "expected_verdict": t.expected_verdict, "clarification_required": t.clarification_required,
            "missing_evidence": None if model == "sim-bad" else sorted(set(t.expected_evidence) - _evidence_facts(OUT / f"{name}.jsonl")),
            "model_summary": edits[-1]["summary"] if edits else None,
            "question": next((r["question"] for r in recs if r["kind"] == "clarification_request"), None)}


if __name__ == "__main__":
    # usage: python -m evaluations.harness sim|sim-bad|real [REPEAT] [TASK_ID ...]
    load_dotenv(ROOT / ".env")
    model, repeat = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1
    ids = sys.argv[3:] or [p.stem for p in sorted(TASKS.glob("*.json"))]
    tasks = [load(i) for i in ids]
    if model == "sim-bad":  # only tasks that have a known defective implementation
        tasks = [t for t in tasks if t.simulated_defective_edit]
    rows = [run_one(t, model, n) for t in tasks for n in range(repeat)]
    (OUT / f"summary-{model}.json").write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(r)
