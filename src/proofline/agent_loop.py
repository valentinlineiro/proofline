"""Orchestration: the model proposes edits, the backend applies them, the verifier alone decides.
At most 1 + MAX_REPAIRS attempts; only a FAIL triggers a repair (INCONCLUSIVE means infra, not code)."""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import model_client
from .evidence import EvidenceLog
from .project import DEFAULT_ID, Project, load
from .verifier import verify
from .workspace import ALLOWED, EditRejected, apply_edits

MAX_REPAIRS = 2
DEFAULT_TASK = load().default_task


NO_USAGE = {"available": False}  # token counts are recorded only as the API reported them


def _inconclusive(log: EvidenceLog, run_id: str, evidence_file: Path, error: str, usage: dict | None = None) -> dict:
    log.append("model_error", error=error, usage=usage or NO_USAGE)
    log.append("verdict", verdict="INCONCLUSIVE", reason=error)
    return {"run_id": run_id, "verdict": "INCONCLUSIVE", "error": error, "evidence_file": str(evidence_file)}


def run_task(task: str, base: Path, workspace: Path, evidence_file: Path, run_id: str = "run",
             verify_first: bool = False, project: Project | None = None) -> dict:
    """verify_first: controlled-demo mode. The workspace starts with a prepared defect; the verifier judges it
    before the model is asked, and the model repairs from that real evidence (does not consume an attempt)."""
    editable = project.editable if project else ALLOWED
    log = EvidenceLog(evidence_file)
    log.append("task", task=task, seeded=verify_first, project=project.id if project else DEFAULT_ID)
    feedback = ""
    result: dict = {}
    if verify_first:
        log.append("seeded_workspace", note="workspace started from a prepared defect, not a model edit")
        result = verify(base, workspace, evidence_file, f"{run_id}.seed", log=log, project=project)
        if result["verdict"] != "FAIL":
            return result
        feedback = "\n".join(f"[{f['name']}]\n{f['tail']}" for f in result["failures"])
    for attempt in range(1 + MAX_REPAIRS):
        files = {p: (workspace / p).read_text() for p in editable}
        usage: dict = {}
        try:
            proposal = model_client.propose_edits(task, files, feedback, on_usage=usage.update)
        except model_client.ModelError as e:
            return _inconclusive(log, run_id, evidence_file, str(e), usage)
        if proposal.clarification:
            log.append("clarification_request", attempt=attempt, question=proposal.clarification, usage=usage or NO_USAGE)
            if attempt == 0 and not verify_first:  # nothing was verified yet: requirements are incomplete
                log.append("verdict", verdict="NEEDS_CLARIFICATION", question=proposal.clarification)
                return {"run_id": run_id, "verdict": "NEEDS_CLARIFICATION", "question": proposal.clarification,
                        "evidence_file": str(evidence_file)}
            break  # asking after a failed verification must not erase the FAIL
        log.append("model_edits", attempt=attempt, proposal=proposal.model_dump(), usage=usage or NO_USAGE)
        try:
            apply_edits(workspace, proposal.edits, editable)
        except EditRejected as e:
            log.append("edit_rejected", attempt=attempt, reason=str(e))
            feedback = f"edit rejected: {e}"
            result = {"run_id": run_id, "verdict": "FAIL", "error": feedback, "evidence_file": str(evidence_file)}
            continue
        result = verify(base, workspace, evidence_file, f"{run_id}.{attempt}", log=log, project=project)
        if result["verdict"] != "FAIL":
            break
        feedback = "\n".join(f"[{f['name']}]\n{f['tail']}" for f in result["failures"])
    return result


def run_new(task: str, seed: str | None, run_id: str, evidence_file: Path, project_id: str = DEFAULT_ID) -> dict:
    """Fresh workspace from the fixture (plus an optional prepared-defect seed), then the full loop."""
    import shutil
    import tempfile

    project = load(project_id)
    base = project.fixture
    with tempfile.TemporaryDirectory(prefix="proofline-ws-") as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(base, ws)
        if seed:
            shutil.copytree(project.seeds / seed, ws, dirs_exist_ok=True)
        return run_task(task, base, ws, evidence_file, run_id, verify_first=bool(seed), project=project)


if __name__ == "__main__":
    from .verifier import ROOT
    load_dotenv()
    # usage: agent_loop [--seed NAME] [TASK]   (--seed = controlled demo with a prepared defect)
    args = sys.argv[1:]
    seed = None
    if args[:1] == ["--seed"]:
        seed, args = args[1], args[2:]
    name = f"run-{seed or 'autonomous'}"
    print(json.dumps(run_new(args[0] if args else DEFAULT_TASK, seed, name, ROOT / "evidence" / f"{name}.jsonl"), indent=2))
