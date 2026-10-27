"""Orchestration: the model proposes edits, the backend applies them, the verifier alone decides.
At most 1 + MAX_REPAIRS attempts; only a FAIL triggers a repair (INCONCLUSIVE means infra, not code)."""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import model_client
from .evidence import EvidenceLog
from .verifier import verify
from .workspace import ALLOWED, EditRejected, apply_edits

MAX_REPAIRS = 2


def _inconclusive(log: EvidenceLog, run_id: str, evidence_file: Path, error: str) -> dict:
    log.append("model_error", error=error)
    log.append("verdict", verdict="INCONCLUSIVE", reason=error)
    return {"run_id": run_id, "verdict": "INCONCLUSIVE", "error": error, "evidence_file": str(evidence_file)}


def run_task(task: str, base: Path, workspace: Path, evidence_file: Path, run_id: str = "run") -> dict:
    log = EvidenceLog(evidence_file)
    feedback = ""
    result: dict = {}
    for attempt in range(1 + MAX_REPAIRS):
        files = {p: (workspace / p).read_text() for p in ALLOWED}
        try:
            proposal = model_client.propose_edits(task, files, feedback)
        except model_client.ModelError as e:
            return _inconclusive(log, run_id, evidence_file, str(e))
        log.append("model_edits", attempt=attempt, proposal=proposal.model_dump())
        try:
            apply_edits(workspace, proposal.edits)
        except EditRejected as e:
            log.append("edit_rejected", attempt=attempt, reason=str(e))
            feedback = f"edit rejected: {e}"
            result = {"run_id": run_id, "verdict": "FAIL", "error": feedback, "evidence_file": str(evidence_file)}
            continue
        result = verify(base, workspace, evidence_file, f"{run_id}.{attempt}", log=log)
        if result["verdict"] != "FAIL":
            break
        feedback = "\n".join(f"[{f['name']}]\n{f['tail']}" for f in result["failures"])
    return result


if __name__ == "__main__":
    import shutil
    import tempfile

    from .verifier import ROOT
    load_dotenv()
    base = ROOT / "fixtures" / "discount-service"
    with tempfile.TemporaryDirectory(prefix="proofline-ws-") as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(base, ws)
        print(json.dumps(run_task(sys.argv[1], base, ws, ROOT / "evidence" / "run-002.jsonl", "run-002"), indent=2))
