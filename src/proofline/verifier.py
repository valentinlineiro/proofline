"""Independent verifier: decides PASS / FAIL / INCONCLUSIVE from real Maven runs, never from the model."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .evidence import EvidenceLog, sha256
from .project import Project, load
from .runner import RunResult, run_maven
from .workspace import ALLOWED, changed_files

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT = load()
# Defaults of the default project. Kept as module attributes: the evaluation harness narrows them per task.
ACCEPTANCE_DIR = _DEFAULT.acceptance
CHECKS = list(_DEFAULT.checks)  # (check name, surefire -Dtest selector). Acceptance checks run against protected tests.


@dataclass
class Check:
    name: str
    status: str
    exit_code: int


def classify(r: RunResult) -> str:
    ran = [int(n) for n in re.findall(r"Tests run: (\d+), Failures", r.stdout)]
    if r.timed_out or r.exit_code in (125, 126, 127) or r.exit_code == -1:
        return "INCONCLUSIVE"
    if r.exit_code == 0:
        return "PASS" if ran and ran[-1] > 0 else "INCONCLUSIVE"  # green with zero tests proves nothing
    if r.exit_code == 1 and ("COMPILATION ERROR" in r.stdout or "FAILURE!" in r.stdout):
        return "FAIL"
    return "INCONCLUSIVE"  # e.g. dependency resolution failed: infra, not code


def requirement_scope(requirements: list[dict], ran: dict[str, str]) -> list[dict]:
    """What the evidence actually covers. `ran` maps each check that was part of this run to its status.
    VERIFIED needs every mapped check to have run and passed; a requirement with no check that ran is NOT_CHECKED."""
    scope = []
    for r in requirements:
        statuses = [ran[c] for c in r["checks"] if c in ran]
        if not statuses:
            status = "NOT_CHECKED"
        elif "FAIL" in statuses:
            status = "FAILED"
        elif len(statuses) == len(r["checks"]) and set(statuses) == {"PASS"}:
            status = "VERIFIED"
        else:
            status = "NOT_VERIFIED"
        scope.append({"id": r["id"], "text": r["text"], "status": status, "checks": r["checks"]})
    return scope


def load_requirements(acceptance_dir: Path | None = None) -> list[dict]:
    f = (acceptance_dir or ACCEPTANCE_DIR) / "requirements.json"
    return json.loads(f.read_text()) if f.exists() else []


def tree_hash(d: Path) -> str:
    files = sorted(p for p in d.rglob("*") if p.is_file() and "target" not in p.parts)
    return sha256("".join(f"{p.relative_to(d)}:{sha256(p.read_bytes())}\n" for p in files))


def diff(base: Path, work: Path) -> str:
    out = subprocess.run(["diff", "-ruN", "-x", "target", base, work], capture_output=True, text=True)
    return out.stdout


def verify(base: Path, workspace: Path, evidence_file: Path, run_id: str = "run", log: EvidenceLog | None = None,
           project: Project | None = None) -> dict:
    """`project` None = the default project, with the module-level CHECKS / ACCEPTANCE_DIR (patchable by the harness)."""
    allowed, checks_to_run, acceptance, dest_rel = ((project.editable, project.checks, project.acceptance, project.acceptance_dest)
                                                    if project else (ALLOWED, CHECKS, ACCEPTANCE_DIR, _DEFAULT.acceptance_dest))
    log = log or EvidenceLog(evidence_file)
    patch = diff(base, workspace)
    base_rev = tree_hash(base)
    log.append("run_start", run_id=run_id, base_revision=base_rev, diff_sha256=sha256(patch), diff=patch)

    checks: list[Check] = []
    outside = sorted(changed_files(base, workspace) - set(allowed))
    if outside:  # defense in depth: the edit tool already blocks this, the verifier does not trust it
        log.append("check", name="allowlist", status="FAIL", exit_code=1, files=outside)
        scope = requirement_scope(load_requirements(acceptance), {})
        log.append("verdict", verdict="FAIL", scope=scope)
        return {"run_id": run_id, "verdict": "FAIL", "base_revision": base_rev, "scope": scope, "fully_verified": False,
                "checks": [Check("allowlist", "FAIL", 1).__dict__], "failures": [{"name": "allowlist", "tail": f"modified protected files: {outside}"}],
                "diff_sha256": sha256(patch), "evidence_file": str(evidence_file)}
    failures: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="proofline-") as tmp:
        sandbox = Path(tmp) / "w"
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns("target"))
        # inject protected tests last so the workspace cannot shadow or alter them
        dest = sandbox / dest_rel
        dest.mkdir(parents=True, exist_ok=True)
        for t in acceptance.glob("*.java"):
            shutil.copy(t, dest / t.name)
        for name, selector in checks_to_run:
            r = run_maven(sandbox, ["test", f"-Dtest={selector}", "-Dsurefire.failIfNoSpecifiedTests=true"])
            status = classify(r)
            checks.append(Check(name, status, r.exit_code))
            if status == "FAIL":
                failures.append({"name": name, "tail": r.stdout[-1500:]})
            log.append("check", name=name, status=status, exit_code=r.exit_code, duration_s=round(r.duration_s, 2),
                       timed_out=r.timed_out, stdout_sha256=sha256(r.stdout), stdout_tail=r.stdout[-3000:])

    statuses = {c.status for c in checks}
    verdict = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    scope = requirement_scope(load_requirements(acceptance), {c.name: c.status for c in checks})
    fully = verdict == "PASS" and bool(scope) and all(s["status"] == "VERIFIED" for s in scope)
    log.append("verdict", verdict=verdict, scope=scope, fully_verified=fully)
    return {"run_id": run_id, "verdict": verdict, "base_revision": base_rev, "scope": scope, "fully_verified": fully, "checks": [c.__dict__ for c in checks], "failures": failures,
            "diff_sha256": sha256(patch), "evidence_file": str(evidence_file)}


if __name__ == "__main__":
    import json
    base, work = Path(sys.argv[1]), Path(sys.argv[2])
    print(json.dumps(verify(base, work, ROOT / "evidence" / "run-001.jsonl", "run-001"), indent=2))
