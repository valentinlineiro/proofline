# Evidence samples

Raw, unmodified evidence from real runs, so the numbers in [`evaluations/RESULTS.md`](../evaluations/RESULTS.md) can be
checked. A sample, not everything: the full set of runs is not published.

| Path | What it is |
|---|---|
| `demo/seeded-repair-live.jsonl` | **Real Muse Spark run** (native tool calling, current client), staged scenario: a prepared `HALF_EVEN` defect, real Maven failure, repair, re-verification, `PASS`. |
| `demo/u1-partial-coverage-SIMULATED-model.jsonl` | `PASS` with `fully_verified: false`. **The defect comes from a simulated model, not from Muse Spark.** |
| `round3/traces/*-real-0.jsonl` | One real Muse Spark trace per evaluation task, **native tool calling** (the current client). `A1` ends `NEEDS_CLARIFICATION`; `U2` ends `INCONCLUSIVE` because a mandatory check was made to fail on purpose. |
| `round2/traces/*-real-0.jsonl` | Same tasks with the previous client (JSON asked for in the prompt). |
| `round1/`, `round2/`, `round3/` `summary-*.json` | One row per run (all repetitions): verdict, independent-oracle verdict, outcome class, attempts. |

Each line is a record with the SHA-256 of the previous one. To verify a file:

```bash
.venv/bin/python -c "import sys; from pathlib import Path; from proofline.evidence import verify_chain; print(verify_chain(Path(sys.argv[1])))" evidence-samples/demo/seeded-repair-live.jsonl
```

`True` means no record inside the chain was altered, inserted or removed. It does not prove the file is the original: the chain
has no external anchor, so someone who can rewrite the file can rewrite the whole chain.

Rounds 1 and 2 were produced before the switch to native tool calling; round 3 and `demo/seeded-repair-live.jsonl` use the current client.

Records are kept exactly as produced, so the diffs contain the absolute paths of the machine the runs were made on.
