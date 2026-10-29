"""`proofline`: use the verifier on a repository that is not Proofline's own. Run files live in <repo>/.proofline/runs/."""
import argparse
import getpass
import json
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import review
from .adapters import get_adapter
from .change import snapshot
from .evidence import EvidenceLog
from .project import ID_RE, ProjectError, load_external
from .verifier import verify

VERIFY_EXIT = {("PASS", True): 0, ("PASS", False): 3, "FAIL": 4, "INCONCLUSIVE": 5}  # outside the gate's table on purpose


def _runs(repo: Path) -> Path:
    return repo / ".proofline" / "runs"


def _git(repo: Path, *args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def cmd_init(a) -> int:
    repo = Path(a.repo).resolve()
    manifest = repo / ".proofline" / "project.json"
    if manifest.exists():
        print(f"{manifest} already exists; not overwriting it", file=sys.stderr)
        return 2
    if not (repo / "pom.xml").is_file():
        print("no pom.xml here: this version only supports Maven projects", file=sys.stderr)
        return 2
    mains = sorted((repo / "src/main/java").rglob("*.java"))
    if not mains:
        print("no Java sources under src/main/java", file=sys.stderr)
        return 2
    rel = mains[0].relative_to(repo).as_posix()
    pid = re.sub(r"[^a-z0-9-]+", "-", repo.name.lower()).strip("-") or "project"
    pid = pid if re.fullmatch(ID_RE, pid) else "project"
    (repo / ".proofline/acceptance").mkdir(parents=True, exist_ok=True)
    (repo / ".proofline/acceptance/requirements.json").write_text("[]\n")
    manifest.write_text(json.dumps({
        "id": pid, "acceptance_dest": str(Path(rel).parent).replace("src/main/java", "src/test/java", 1),
        "editable": [rel], "checks": [{"name": "maven_tests", "selector": "!Acceptance*"}],
        "default_task": "Verify the change."}, indent=2) + "\n")
    print(f"wrote {manifest.relative_to(repo)}. Now: list the files a change may touch in `editable`, put protected tests "
          f"(named Acceptance*) in .proofline/acceptance/ with a checks entry each, and describe requirements in "
          f".proofline/acceptance/requirements.json. Add .proofline/runs/ to .gitignore if you do not want run files committed.")
    return 0


def cmd_warm(a) -> int:
    p = load_external(Path(a.repo))
    get_adapter(p.ecosystem).warm(p.root)
    return 0


def cmd_verify(a) -> int:
    repo = Path(a.repo).resolve()
    p = load_external(repo)
    with tempfile.TemporaryDirectory(prefix="proofline-base-") as tmp:
        if a.base and Path(a.base).is_dir():
            base, ref = Path(a.base).resolve(), {"kind": "dir", "path": str(Path(a.base).resolve())}
        else:
            rev = a.base or ("HEAD" if (repo / ".git").exists() else None)
            sha = rev and _git(repo, "rev-parse", "--verify", rev + "^{commit}")
            if not sha:
                print("give --base REV (a git revision) or --base DIR (a pristine copy)", file=sys.stderr)
                return 2
            tar = subprocess.run(["git", "-C", str(repo), "archive", sha], capture_output=True, check=True)
            (Path(tmp) / "base").mkdir()
            subprocess.run(["tar", "-x", "-C", str(Path(tmp) / "base")], input=tar.stdout, check=True)
            base, ref = Path(tmp) / "base", {"kind": "git", "rev": sha}
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%dt%H%M%S}-{secrets.token_hex(2)}"  # lowercase: the API accepts [a-z0-9-]
        f = _runs(repo) / f"{run_id}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        log = EvidenceLog(f)
        log.append("task", task=a.task or p.default_task, source="human", project=p.id)
        result = verify(base, snapshot(repo, Path(tmp) / "snapshot"), f, run_id, log=log, project=p, base_ref=ref, manifest_path=p.manifest)
    print(f"{result['verdict']}  fully_verified={result['fully_verified']}  run={run_id}")
    for s in result["scope"]:
        print(f"  {s['status']:<13} {s['id']}  {s['text']}")
    key = (result["verdict"], result["fully_verified"]) if result["verdict"] == "PASS" else result["verdict"]
    return VERIFY_EXIT.get(key, 5)


def _run_file(a) -> Path | None:
    repo = Path(a.repo).resolve()
    if a.run:
        return _runs(repo) / f"{a.run}.jsonl"
    files = sorted(_runs(repo).glob("*.jsonl"), key=lambda p: p.stem) if _runs(repo).exists() else []
    return files[-1] if files else None


def cmd_decide(a) -> int:
    f = _run_file(a)
    if not f or not f.exists():
        print("no such run", file=sys.stderr)
        return 2
    reviewer = a.reviewer or _git(Path(a.repo), "config", "user.name") or getpass.getuser()
    try:
        r = review.decide(f, a.decision, a.reason or "", reviewer)
    except review.ReviewError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"{r['decision']} recorded by {r['reviewer']}" + (" (override)" if r["override"] else ""))
    return 0


def cmd_gate(a) -> int:
    f = _run_file(a)
    if not f:
        print("no runs found", file=sys.stderr)
        return review.NO_VERIFICATION
    return review.gate(f, Path(a.repo).resolve(), strict=a.strict)


def cmd_retry(a) -> int:
    """Give the agent one more attempt after a rejection: the reviewer's reason and the real check output are its feedback."""
    import os
    import tempfile as _tf

    from . import agent_loop, budget
    f = _run_file(a)
    if not f or not f.exists() or not review._chain_ok(f):
        print("no such run, or its evidence chain does not verify", file=sys.stderr)
        return 2
    if not os.environ.get("MODEL_API_KEY") or not os.environ.get(budget.ENV):
        print(f"retry calls the model: set MODEL_API_KEY and {budget.ENV} (a call-budget ledger) first", file=sys.stderr)
        return 2
    records = review._records(f)
    v = review.latest_verification(records)
    decisions = [r for r in records if r["kind"] == "human_decision" and v and r["run_id"] == v["run_id"]]
    if not decisions or decisions[-1]["decision"] != "reject":
        print("nothing to retry: the latest verification was not rejected", file=sys.stderr)
        return 2
    attempts = sum(r["kind"] == "model_edits" for r in records)
    if attempts >= agent_loop.MAX_REPAIRS:
        print(f"the attempt limit ({agent_loop.MAX_REPAIRS} retries) is used up; the rejection reason stays recorded", file=sys.stderr)
        return 2
    repo = Path(a.repo).resolve()
    p = load_external(repo)
    start = max(i for i, r in enumerate(records) if r["kind"] == "run_start" and r["run_id"] == v["run_id"])
    failing = [f"[{c['name']}]\n{c.get('stdout_tail', '')[-1500:]}" for c in records[start:] if c["kind"] == "check" and c["status"] != "PASS"]
    gaps = [f"- {s['text']} ({s['status']})" for s in v["scope"] if s["status"] != "VERIFIED"]
    feedback = "\n\n".join([f"A reviewer rejected the last change: {decisions[-1]['reason']}", *failing] + (["Not verified:\n" + "\n".join(gaps)] if gaps else []))
    task = next(r["task"] for r in records if r["kind"] == "task")
    with _tf.TemporaryDirectory(prefix="proofline-base-") as tmp:
        base = review._materialise_base(repo, v.get("base_ref") or {}, Path(tmp) / "base")
        result = agent_loop.retry_after_rejection(task, p, base, repo, EvidenceLog.resume(f), f"{v['run_id']}.r{attempts + 1}", feedback,
                                                  attempts, v.get("base_ref"), p.manifest, snap=lambda: snapshot(repo, Path(tmp) / "snapshot"))
    print(f"{result['verdict']}  run={f.stem}  (decide on it again, then gate)")
    return 0 if result["verdict"] in ("PASS", "FAIL") else 5


def cmd_serve(a) -> int:
    import os

    import uvicorn
    os.environ["PROOFLINE_REPO"] = str(Path(a.repo).resolve())
    uvicorn.run("proofline.api:app", host="127.0.0.1", port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="proofline", description="Verify a change, record a human decision, gate on it.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--repo", default=".")
        sp.set_defaults(fn=fn)
        return sp
    add("init", cmd_init, help="write a manifest skeleton in .proofline/")
    add("warm", cmd_warm, help="download dependencies (the only step with network)")
    v = add("verify", cmd_verify, help="verify the working tree against a base")
    v.add_argument("--base")
    v.add_argument("--task")
    d = add("decide", cmd_decide, help="record approve or reject for a run")
    d.add_argument("decision", choices=["approve", "reject"])
    d.add_argument("--run", required=True)
    d.add_argument("--reason")
    d.add_argument("--reviewer")
    g = add("gate", cmd_gate, help="exit 0 only for a change approved in exactly its current state")
    grp = g.add_mutually_exclusive_group(required=True)
    grp.add_argument("--run")
    grp.add_argument("--latest", action="store_true")
    g.add_argument("--strict", action="store_true")
    rt = add("retry", cmd_retry, help="give the agent another attempt after a rejection (needs a model key and a call-budget ledger)")
    rt.add_argument("--run", required=True)
    s = add("serve", cmd_serve, help="open the review screen on localhost")
    s.add_argument("--port", type=int, default=8765)
    try:
        a = ap.parse_args(argv)
        return a.fn(a)
    except SystemExit as e:
        return int(e.code or 0)
    except ProjectError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
