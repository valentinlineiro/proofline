"""Evaluation harness. Runs a task through the unmodified agent loop + verifier, then judges the outcome with an
independent oracle (the full acceptance suite, run separately). The agent only ever sees `task`, never the rest."""
import json
import os
import re
import shutil
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv
from pydantic import BaseModel

from proofline import model_client, verifier
from proofline.agent_loop import run_task
from proofline.evidence import verify_chain
from proofline.project import DEFAULT_ID, Project, ProjectError, load as load_project
from proofline.runner import RunResult
from proofline.verifier import ROOT, verify

TASKS = Path(__file__).parent / "tasks"
OUT = Path(os.environ.get("PROOFLINE_EVAL_OUT", ROOT / "evidence" / "eval"))


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
    simulated_edit: dict[str, str] | None = None            # legacy single edit; "path" defaults to the first editable file
    simulated_defective_edit: dict[str, str] | None = None
    simulated_edits: list[dict[str, str]] | None = None     # several edits, each may name its own "path"
    simulated_defective_edits: list[dict[str, str]] | None = None
    project: str = DEFAULT_ID                               # which manifest (fixture, checks, protected tests) the task runs on
    seed: str | None = None                                 # prepared-defect overlay: the run starts verify-first
    group: str = "adversarial"
    acceptance_dir: str | None = None       # oracle tests for this task (None: the verifier's default)
    checks: list[list[str]] | None = None   # full check list = the oracle (None: the verifier's default)
    outcome_kind: str = "oracle"            # "ungrounded": no oracle can judge the requirement
    clarification_required: bool = False


    def edits(self, defective: bool = False) -> list[dict[str, str]] | None:
        many, one = ((self.simulated_defective_edits, self.simulated_defective_edit) if defective
                     else (self.simulated_edits, self.simulated_edit))
        return many or ([one] if one else None)


def load(task_id: str) -> EvalTask:
    return EvalTask.model_validate_json((TASKS / f"{task_id}.json").read_text())


def profile(t: EvalTask) -> tuple[Project, Project]:
    """(agent profile, oracle profile) for a task. The agent is verified against the task's mandatory checks only; the oracle always
    keeps the FULL check list. An unknown project, an invalid manifest, or a task that names a check or seed the project does not
    define is an error, never a silent fallback to another project."""
    base = load_project(t.project)
    full = tuple((n, sel) for n, sel in t.checks) if t.checks else base.checks
    unknown = sorted((set(t.mandatory_checks) | ({t.fault} if t.fault else set())) - {n for n, _ in full})
    if unknown:
        raise ProjectError(f"task {t.id}: checks not defined for project {t.project!r}: {unknown}")
    if t.seed and not (re.fullmatch(r"[a-z0-9][a-z0-9-]*", t.seed) and base.seeds and (base.seeds / t.seed).is_dir()):
        raise ProjectError(f"task {t.id}: project {t.project!r} has no seed {t.seed!r}")
    oracle = replace(base, checks=full, acceptance=ROOT / t.acceptance_dir if t.acceptance_dir else base.acceptance)
    return replace(oracle, checks=tuple(c for c in full if c[0] in t.mandatory_checks)), oracle


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


def fake_tool_message(name: str | None, args: dict | str | None = None, text: str | None = None) -> MagicMock:
    """An API message holding one tool call (or none, with `text`). The only thing simulated is the model API boundary."""
    msg = MagicMock(content=text)
    if name is None:
        msg.tool_calls = []
    else:
        call = MagicMock()
        call.function.name = name
        call.function.arguments = args if isinstance(args, str) else json.dumps(args)
        msg.tool_calls = [call]
    return msg


def fake_client(*messages) -> MagicMock:
    c = MagicMock()
    c.chat.completions.create.side_effect = [
        m if isinstance(m, Exception) else MagicMock(choices=[MagicMock(message=m)]) for m in messages]
    return c


def _fake_model(edits: list[dict[str, str]], default_path: str):
    args = {"summary": "simulated", "edits": [{"path": e.get("path", default_path), "old": e["old"], "new": e["new"]} for e in edits]}
    c = MagicMock()
    c.chat.completions.create.return_value.choices = [MagicMock(message=fake_tool_message("apply_edits", args))]
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


def _usage_totals(recs: list[dict]) -> dict:
    """Token counts as the API reported them, summed over the calls that reported them (per-call values stay in the evidence)."""
    calls = [r.get("usage") for r in recs if r["kind"] in ("model_edits", "clarification_request", "model_error")]
    got = [u for u in calls if u and u.get("available")]
    return {"model_calls": len(calls), "calls_with_usage": len(got),
            "input_tokens": sum(u["input_tokens"] for u in got) if got else None,
            "output_tokens": sum(u["output_tokens"] for u in got) if got else None}


def run_one(t: EvalTask, model: str, n: int) -> dict:
    agent, oracle_project = profile(t)  # raises before any workspace, model call or Maven run
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"{t.id}-{model}-{n}"
    fault_sel = dict(oracle_project.checks)[t.fault] if t.fault else None
    sim = t.edits(defective=model == "sim-bad") if model in ("sim", "sim-bad") else None
    if model in ("sim", "sim-bad") and not sim:
        raise ProjectError(f"task {t.id}: no {'defective ' if model == 'sim-bad' else ''}simulated edits")
    with tempfile.TemporaryDirectory(prefix="proofline-eval-") as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(agent.fixture, ws)
        if t.seed:
            shutil.copytree(agent.seeds / t.seed, ws, dirs_exist_ok=True)
        started = time.monotonic()
        with ExitStack() as stack:
            if fault_sel:
                stack.enter_context(_faulty_runner(fault_sel))
            if sim:  # no real API involved: a dummy key must not be required
                stack.enter_context(patch.dict(os.environ, {"MODEL_API_KEY": "simulated"}))
                stack.enter_context(_fake_model(sim, agent.editable[0]))
            result = run_task(t.task, agent.fixture, ws, OUT / f"{name}.jsonl", name, verify_first=bool(t.seed), project=agent)
        duration = round(time.monotonic() - started, 1)
        # oracle: independent, full suite on the final workspace, never seen by the agent
        oracle = verify(agent.fixture, ws, OUT / f"{name}.oracle.jsonl", f"{name}.oracle", project=oracle_project)
    recs = [json.loads(l) for l in (OUT / f"{name}.jsonl").read_text().splitlines()]
    edits = [r["proposal"] for r in recs if r["kind"] == "model_edits"]
    outcome = classify_outcome(result["verdict"], oracle["verdict"] == "FAIL", fault_sel is not None,
                               t.outcome_kind == "ungrounded")
    return {"task": t.id, "group": t.group, "model": model, "run": n, "verdict": result["verdict"],
            "oracle_verdict": oracle["verdict"], "outcome": outcome, "attempts": len(edits),
            "expected_verdict": t.expected_verdict, "clarification_required": t.clarification_required,
            "missing_evidence": None if model == "sim-bad" else sorted(set(t.expected_evidence) - _evidence_facts(OUT / f"{name}.jsonl")),
            "model_summary": edits[-1]["summary"] if edits else None,
            "question": next((r["question"] for r in recs if r["kind"] == "clarification_request"), None),
            "project": t.project, "duration_s": duration, "usage": _usage_totals(recs)}


def select(ids: list[str], project: str | None) -> list[EvalTask]:
    """--project filters by each task's own project; it never reassigns a task. An invalid id fails even if nothing matches."""
    if project:
        load_project(project)
    tasks = [load(i) for i in ids or [p.stem for p in sorted(TASKS.glob("*.json"))]]
    return [t for t in tasks if t.project == project] if project else tasks


def main(argv: list[str]) -> None:
    """usage: python -m evaluations.harness sim|sim-bad|real [REPEAT] [--project ID] [TASK_ID ...]"""
    args = list(argv)
    model = args.pop(0)
    project = None
    if "--project" in args:
        i = args.index("--project")
        if i + 1 >= len(args):
            raise SystemExit("error: --project needs an id")
        project = args[i + 1]
        del args[i:i + 2]
    repeat = int(args.pop(0)) if args and args[0].isdigit() else 1
    try:
        tasks = select(args, project)
        if model == "sim-bad":  # only tasks that have a known defective implementation
            tasks = [t for t in tasks if t.edits(defective=True)]
        if not tasks:
            raise SystemExit("error: no tasks selected")
        for t in tasks:  # validate every selected task before the first model call
            profile(t)
    except (ProjectError, FileNotFoundError, ValueError) as e:
        raise SystemExit(f"error: {e}")
    rows = [run_one(t, model, n) for t in tasks for n in range(repeat)]
    for pid in sorted({r["project"] for r in rows}):  # one summary per project: results of different fixtures are never mixed
        mine = [r for r in rows if r["project"] == pid]
        (OUT / (f"summary-{model}.json" if pid == DEFAULT_ID else f"summary-{model}-{pid}.json")).write_text(json.dumps(mine, indent=2))
    for r in rows:
        print(r)


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    main(sys.argv[1:])
