#!/usr/bin/env bash
# Pre-flight for recording the demo: fails now, not in the middle of a take.
#   scripts/demo-prep.sh           checks, prepares the U1 example, starts the UI on :8765
#   scripts/demo-prep.sh --check   checks and prepares only
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
fail() { echo "NOT READY: $1" >&2; exit 1; }

docker info >/dev/null 2>&1 || fail "Docker is not running"
grep -q '^MODEL_API_KEY=.\+' .env 2>/dev/null || fail "MODEL_API_KEY is missing in .env"

$PY - <<'EOF' || fail "model API check failed (key invalid, wrong MODEL_NAME, or no network)"
import os, shutil, sys, tempfile
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from proofline.runner import run_maven
from proofline.verifier import ROOT

load_dotenv(ROOT / ".env")
name = os.environ.get("MODEL_NAME", "muse-spark-1.3")
ids = [m.id for m in OpenAI(api_key=os.environ["MODEL_API_KEY"], base_url="https://api.meta.ai/v1", timeout=20).models.list()]
if name not in ids:
    sys.exit(f"model {name!r} not offered by the API")
print(f"model ok: {name}")

with tempfile.TemporaryDirectory() as tmp:  # same offline, read-only-cache path every verification uses
    ws = Path(tmp) / "w"
    shutil.copytree(ROOT / "fixtures" / "discount-service", ws)
    r = run_maven(ws, ["test"], timeout_s=120)
    if r.exit_code != 0:
        sys.exit("offline Maven run failed: run `.venv/bin/python -m proofline.runner` once (needs network)")
print("maven cache ok (offline)")
EOF

# start every take from an empty history, keeping only the deterministic U1 example (simulated model, partial coverage)
mkdir -p evidence/ui evidence/ui-archive evidence/eval-demo
find evidence/ui -name '*.jsonl' ! -name 'u1-sim-partial-coverage.jsonl' -exec mv {} evidence/ui-archive/ \;
PROOFLINE_EVAL_OUT=evidence/eval-demo $PY -m evaluations.harness sim 1 U1 >/dev/null
cp evidence/eval-demo/U1-sim-0.jsonl evidence/ui/u1-sim-partial-coverage.jsonl
echo "U1 partial-coverage example ready (simulated model, not a Muse Spark defect)"

[ "${1:-}" = "--check" ] && { echo "READY"; exit 0; }
echo "READY -> http://localhost:8765  (Ctrl+C to stop)"
exec .venv/bin/uvicorn proofline.api:app --port 8765
