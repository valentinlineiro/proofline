"""Summarises evaluation rows. Metrics are kept apart on purpose; A1/A2 are reported on their own and
simulated-model rows (harness validation) are never mixed with real Muse Spark rows."""
import json
import sys
from collections import Counter
from pathlib import Path

from .harness import OUT


def metrics(rows: list[dict]) -> dict:
    core = [r for r in rows if r["task"] not in ("A1", "A2")]
    final_pass = [r for r in core if r["verdict"] == "PASS"]
    return {
        "runs": len(core),
        "detection": f'{sum(r["outcome"] == "DETECTED" for r in core)}/{sum(r["oracle_verdict"] == "FAIL" and r["verdict"] != "INCONCLUSIVE" for r in core)} known-defective runs flagged',
        "false_pass": sum(r["outcome"] == "FALSE_PASS" for r in core),
        "correct_inconclusive": f'{sum(r["outcome"] == "CORRECT_INCONCLUSIVE" for r in core)}/{sum(r["expected_verdict"] == "INCONCLUSIVE" for r in core)}',
        "first_try_pass": sum(r["attempts"] == 1 for r in final_pass),
        "repaired_after_real_failure": sum(r["attempts"] > 1 for r in final_pass),
        "outcomes": dict(Counter(r["outcome"] for r in core)),
    }


def a_tasks(rows: list[dict]) -> dict:
    out = {}
    for tid in ("A1", "A2"):
        rs = [r for r in rows if r["task"] == tid]
        if rs:
            out[tid] = {"runs": len(rs), "outcomes": dict(Counter(r["outcome"] for r in rs)),
                        "attempts": [r["attempts"] for r in rs], "summaries": sorted({r["model_summary"] for r in rs})}
    return out


if __name__ == "__main__":
    for f in sys.argv[1:] or sorted(str(p) for p in Path(OUT).glob("summary-*.json")):
        rows = json.loads(Path(f).read_text())
        print(f"== {f}\n{json.dumps(metrics(rows), indent=1)}\n{json.dumps(a_tasks(rows), indent=1)}")
