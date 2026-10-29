"""Local API for the UI. It only starts runs and serves what the evidence files say: the UI never invents a verdict."""
import getpass
import json
import os
import re
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from typing import Annotated, Literal

from .agent_loop import DEFAULT_TASK, run_new
from . import review
from .evidence import EvidenceLog, verify_chain
from .verifier import ROOT

load_dotenv(ROOT / ".env")
REPO = Path(os.environ["PROOFLINE_REPO"]) if os.environ.get("PROOFLINE_REPO") else None  # set by `proofline serve`
EVIDENCE = REPO / ".proofline" / "runs" if REPO else ROOT / "evidence" / "ui"
SEEDS = ROOT / "fixtures" / "seeds"
app = FastAPI(title="Proofline")
_running: dict[str, bool] = {}   # run_id -> still running
_lock = threading.Lock()         # one run at a time: each run uses Docker and the model


class StartRun(BaseModel):
    task: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] = DEFAULT_TASK
    seed: str | None = None


class Decision(BaseModel):
    """Only what a person decides. The verdict, the override flag and the hashes are never taken from the client."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    reason: Annotated[str, StringConstraints(max_length=2000)] = ""
    reviewer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", run_id):
        raise HTTPException(404)
    return EVIDENCE / f"{run_id}.jsonl"


def _work(run_id: str, req: StartRun) -> None:
    try:
        run_new(req.task, req.seed, run_id, _path(run_id))
    except Exception as e:  # keep the failure visible instead of a run that looks stuck
        EvidenceLog.resume(_path(run_id)).append("run_error", error=type(e).__name__)
    finally:
        _running[run_id] = False
        _lock.release()


@app.get("/api/seeds")
def seeds() -> list[str]:
    return sorted(p.name for p in SEEDS.iterdir() if p.is_dir())


@app.post("/api/runs", status_code=202)
def start(req: StartRun) -> dict:
    if REPO:
        raise HTTPException(409, "runs are started with `proofline verify`; this screen only reviews them")
    if req.seed and req.seed not in seeds():
        raise HTTPException(422, "unknown seed")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "a run is already in progress")
    run_id = f"{'seeded' if req.seed else 'auto'}-{uuid.uuid4().hex[:8]}"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    _path(run_id).write_text("")
    _running[run_id] = True
    threading.Thread(target=_work, args=(run_id, req), daemon=True).start()
    return {"run_id": run_id, "task": req.task, "seed": req.seed}


@app.get("/api/runs")
def runs() -> list[str]:
    return [p.stem for p in sorted(EVIDENCE.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)] if EVIDENCE.exists() else []


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict:
    f = _path(run_id)
    if not f.exists():
        raise HTTPException(404)
    try:  # a record may be mid-write while the run is live
        records = [json.loads(l) for l in f.read_text().splitlines() if l]
        chain_valid = verify_chain(f)
    except json.JSONDecodeError:
        records, chain_valid = [], False
    running = _running.get(run_id, False)
    return {"run_id": run_id, "running": running, "chain_valid": chain_valid, "records": records,
            "signals": review.signals(records) if chain_valid else [],
            "gate": review.gate(f, REPO) if REPO and not running else None}  # informational: what `proofline gate` would return now


@app.post("/api/runs/{run_id}/decision")
def decide(run_id: str, body: Decision) -> dict:
    f = _path(run_id)
    if not f.exists():
        raise HTTPException(404)
    try:
        return review.decide(f, body.decision, body.reason, body.reviewer or getpass.getuser())
    except review.ReviewError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/mode")
def mode() -> dict:
    return {"repo": REPO.name if REPO else None}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "ui" / "index.html", headers={"Cache-Control": "no-store"})
