"""The human decision on a verified change, and the gate that honours it. A decision is one more record in the same hash
chain as the verification it judges; it never changes a verdict, it is recorded next to it."""
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .change import change_hash, inputs_hash, snapshot
from .evidence import EvidenceLog, verify_chain
from .project import ProjectError, load_external
from .verifier import tree_hash

OK, USAGE, NOT_APPROVED, REJECTED, CHANGED, CHAIN_INVALID, NO_VERIFICATION, OVERRIDE_STRICT = 0, 2, 10, 11, 12, 13, 14, 15


class ReviewError(Exception):
    pass


def _chain_ok(path: Path) -> bool:
    try:
        return verify_chain(path)
    except (ValueError, KeyError, TypeError):  # a half-written or non-JSON line is a broken chain, not a crash
        return False


def _records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l]


def latest_verification(records: list[dict]) -> dict | None:
    """The last verification that finished: its `run_start` merged with the `verdict` record that follows it."""
    found = None
    for i, r in enumerate(records):
        if r["kind"] == "run_start":
            v = next((x for x in records[i + 1:] if x["kind"] in ("verdict", "run_start")), None)
            if v and v["kind"] == "verdict":
                found = {**r, "verdict": v["verdict"], "scope": v.get("scope", []), "fully_verified": v.get("fully_verified", False)}
    return found


def _is_override(v: dict) -> bool:
    return v["verdict"] != "PASS" or not v["fully_verified"]


def decide(run_file: Path, decision: Literal["approve", "reject"], reason: str, reviewer: str) -> dict:
    if decision not in ("approve", "reject"):
        raise ReviewError("decision must be approve or reject")
    if not _chain_ok(run_file):
        raise ReviewError("the evidence chain does not verify; refusing to add a decision to it")
    records = _records(run_file)
    v = latest_verification(records)
    if v is None:
        if any(r["kind"] == "verdict" and r.get("verdict") == "NEEDS_CLARIFICATION" for r in records):
            raise ReviewError("this run ended in a clarification request: no change was verified, nothing to decide")
        raise ReviewError("this run has no finished verification to decide on")
    reason = reason.strip()
    override = decision == "approve" and _is_override(v)  # computed here, never taken from the caller
    if decision == "reject" and not reason:
        raise ReviewError("a rejection needs a reason")
    if override and not reason:
        raise ReviewError("approving a change that is not a fully verified PASS is an override and needs a reason")
    record = {"decision": decision, "reason": reason, "reviewer": reviewer.strip() or "unknown",
              "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": v["run_id"],
              "change_sha256": v.get("change_sha256"), "base_revision": v["base_revision"], "verdict": v["verdict"],
              "fully_verified": v["fully_verified"], "scope": v["scope"], "override": override}
    EvidenceLog.resume(run_file).append("human_decision", **record)
    return record


def signals(records: list[dict]) -> list[str]:
    """Decisions that disagree with the verdict. Descriptive only: counts of human decisions, not a measure of test quality."""
    out = []
    for r in records:
        if r["kind"] != "human_decision":
            continue
        if r["decision"] == "reject" and r["verdict"] == "PASS":
            out.append("REJECTED_PASS")
        elif r["decision"] == "approve" and r["verdict"] == "PASS" and not r["fully_verified"]:
            out.append("APPROVED_WITH_GAPS")
        elif r["decision"] == "approve" and r["verdict"] in ("FAIL", "INCONCLUSIVE"):
            out.append(f"APPROVED_{r['verdict']}")
    return out


def _materialise_base(repo: Path, ref: dict, into: Path) -> Path:
    if ref.get("kind") == "dir":
        return Path(ref["path"])
    if ref.get("kind") == "git":
        tar = subprocess.run(["git", "-C", str(repo), "archive", ref["rev"]], capture_output=True)
        if tar.returncode:
            raise ReviewError("the base revision is not in this repository")
        into.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-x", "-C", str(into)], input=tar.stdout, check=True)
        return into
    raise ReviewError("the verification does not say which base it was taken against")


def gate(run_file: Path, repo: Path, strict: bool = False) -> int:
    """0 only for a change that a person approved in exactly the state it is in now, according to an intact record."""
    if not run_file.exists() or not _chain_ok(run_file):
        return CHAIN_INVALID if run_file.exists() else NO_VERIFICATION
    records = _records(run_file)
    v = latest_verification(records)
    if v is None or not v.get("change_sha256"):
        return NO_VERIFICATION
    decisions = [r for r in records if r["kind"] == "human_decision" and r["run_id"] == v["run_id"]]
    if not decisions:
        return NOT_APPROVED
    last = decisions[-1]
    if last["decision"] == "reject":
        return REJECTED
    try:
        project = load_external(repo)
        with tempfile.TemporaryDirectory(prefix="proofline-gate-") as tmp:
            base = _materialise_base(repo, v.get("base_ref") or {}, Path(tmp) / "base")
            work = snapshot(repo.resolve(), Path(tmp) / "snapshot")
            same = (tree_hash(base) == v["base_revision"] and change_hash(base, work) == last["change_sha256"] == v["change_sha256"]
                    and inputs_hash(project.manifest, project.acceptance) == v.get("inputs_sha256"))
    except (ProjectError, ReviewError, OSError, subprocess.CalledProcessError):
        return CHANGED
    if not same:
        return CHANGED
    if last["override"]:
        print("notice: approved as an override (the verdict was not a fully verified PASS): " + last["reason"])
        if strict:
            return OVERRIDE_STRICT
    return OK
