# Proofline

**An agent saying "done" is a claim. Proofline asks for evidence.**

Proofline lets a coding agent (Meta's Muse Spark) change a Java/Maven project, then judges the change with checks that
the agent neither writes nor runs: protected acceptance tests executed in a network-less Docker container. The verdict is
`PASS`, `FAIL`, `INCONCLUSIVE` or `NEEDS_CLARIFICATION`, and it comes only from the verifier and a hash-chained evidence
log, never from the model's own account.

What it claims: *a declared set of checks ran and passed.* What it does not claim: that the code is correct. A `PASS`
carries an explicit scope (which requirements had a check that ran) so it cannot be read as more than it is.

## How it works

```
task ──▶ agent_loop ──▶ model_client ──▶ Muse Spark (Meta Model API)
            │                 └─ reply = structured edits  OR  one clarification question
            ▼
        workspace ── allowlist-only, all-or-nothing edits (no shell, no free file access)
            ▼
        verifier ──▶ runner ──▶ Docker: mvn -o, --network none, read-only Maven cache
            │          protected acceptance tests injected AFTER the workspace is copied
            ▼
        evidence ── append-only JSONL, each record carries the previous record's SHA-256
```

| Piece | File | Role |
|---|---|---|
| Orchestrator | `src/proofline/agent_loop.py` | Up to 1 attempt + 2 repairs. Repairs only on `FAIL`, fed with the real failing output. |
| Model client | `src/proofline/model_client.py` | Muse Spark via the OpenAI-compatible Meta Model API. Edits **or** a question, never both. |
| Workspace | `src/proofline/workspace.py` | Edit allowlist (`DiscountService.java` only), rejects absolute paths, `..`, symlinks, ambiguous matches. |
| Runner | `src/proofline/runner.py` | One throwaway container per check: no network, 1 GB, 2 CPUs, 300 s. |
| Verifier | `src/proofline/verifier.py` | Verdict + per-requirement scope. Re-checks the whole diff against the allowlist before running Maven. |
| Evidence | `src/proofline/evidence.py` | Hash-chained JSONL; `verify_chain` detects edits inside the chain. |
| API + UI | `src/proofline/api.py`, `ui/index.html` | Starts runs and renders **what the evidence file says**; the UI never computes a verdict. |
| Evaluation | `evaluations/` | Task contracts, independent oracles, harness, report. |

Verdict rules: `PASS` = every mandatory check ran and passed. `FAIL` = a mandatory check failed (or the diff touched a
protected file). `INCONCLUSIVE` = a mandatory check could not produce a reliable result (infrastructure, timeout, zero
tests run): it is never repaired and never a `PASS`. `NEEDS_CLARIFICATION` = the model asked for missing requirements before
anything was verified; it makes no claim about the code. A question asked after a failed verification does not erase the `FAIL`.

## Requirements

- Linux or WSL2 (developed on WSL2 Ubuntu), Python 3.12+
- Docker (the runner uses the `maven:3.9-eclipse-temurin-21` image; **no local Java or Maven needed**)
- A Meta Model API key, only for real model runs. The tests do not need one.

## Install

```bash
git clone <this-repo> proofline && cd proofline
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# One-time, the only step that uses the network: fills the Maven cache (docker volume "proofline-m2").
# Every verification afterwards runs with --network none and the cache mounted read-only.
.venv/bin/python -m proofline.runner
```

Configure the model (skip this to run only the tests and simulated scenarios):

```bash
cp .env.example .env          # .env is git-ignored
# edit .env:  MODEL_API_KEY=...   (never commit it, paste it in a chat, or put it in logs)
```

`MODEL_NAME` defaults to `muse-spark-1.3`. `muse-spark-1.3-contributor` is cheaper but, per Meta's pricing page, uses inputs and
outputs to train Meta's models: only use it with non-sensitive code (the evaluation sent only the synthetic fixture).
Without a key, a run ends `INCONCLUSIVE` with `MODEL_API_KEY is not set`; it never passes.

## Run

```bash
.venv/bin/pytest -q                                   # 68 tests, ~2.5 min, needs Docker + the warm cache, no API key
.venv/bin/uvicorn proofline.api:app --port 8765       # then open http://localhost:8765
```

The API is local-only and unauthenticated, and runs one job at a time. Do not expose it.

## Demo scenarios

1. **Seeded defect → real failure → evidence-guided repair** (needs the API key).
   In the UI choose *Staged scenario: seeded defect "half-even"* and press Run, or:
   `.venv/bin/python -m proofline.agent_loop --seed half-even`.
   The workspace starts with `RoundingMode.HALF_EVEN`. The verifier rejects it (`100.05 → 90.045` must round to `90.05`; Maven
   reports `expected: <90.05> but was: <90.04>`), the model receives that real output, repairs, and everything is verified again.
   **The defect is prepared by us**; the UI marks it `SEEDED DEFECT`. It is not a spontaneous Muse Spark error.
2. **A `PASS` that is not fully verified** (no API key needed; the defect comes from a *simulated* model, not Muse Spark).
   ```bash
   PROOFLINE_EVAL_OUT=evidence/eval-demo .venv/bin/python -m evaluations.harness sim 1 U1
   mkdir -p evidence/ui && cp evidence/eval-demo/U1-sim-0.jsonl evidence/ui/u1-sim-partial-coverage.jsonl
   ```
   Reload the UI and open *Previous runs → u1-sim-partial-coverage*: verdict `PASS` with a **NOT FULLY VERIFIED** badge, because the
   boundary and rounding requirements had no check in this run, even though the boundary defect (`>=`) is present.
3. **Missing requirements** (needs the API key). In the UI, run autonomously with the task
   `Add a loyalty discount for returning customers to DiscountService.` The model asks which percentage and which customers
   qualify, and the run ends `NEEDS_CLARIFICATION`. (3 of 3 runs in our evaluation; model behaviour is not guaranteed.)

## Evaluation

See [`evaluations/RESULTS.md`](evaluations/RESULTS.md) for the full tables and caveats. Summary, real Muse Spark runs, 3–5 runs
per task, one fixture, two rounds:

- Simple and edge-case tasks (S1–S3, E1–E3): 18/18 `PASS` on the first attempt, in both rounds.
- U2 (a mandatory check cannot run): 5/5 `INCONCLUSIVE`, no repair attempted.
- A2 (ambiguous boundary): 3/3 failed for real first and were repaired from the verifier's output. This is evidence-guided repair,
  not understanding of intent.
- A1 (underspecified): round 1, 3/3 invented a policy (`UNGROUNDED_PASS`); round 2, 3/3 asked for clarification.
- **Known false `PASS` (U1)** exists **only with a simulated defect**: when no mandatory check exercises a defect, the verifier
  accepts it and only the full suite catches it. With real Muse Spark, the model never introduced that defect, so no real false
  `PASS` was observed. That is not evidence that none can occur.

Simulated-model runs (`sim`, `sim-bad`) validate the harness and the oracle, not Muse Spark. Reproduce the real battery with
`.venv/bin/python -m evaluations.harness real 3 S1 S2 S3 E1 E2 E3 A1 A2` and `... real 5 U1 U2`.

## Limitations

- **Verification is only as good as its checks.** `PASS` means the declared checks passed. Requirements are mapped to checks
  by hand (`acceptance-tests/requirements.json`); Proofline does not measure coverage or prove the mapping is complete.
- **Oracle independence.** For most evaluation tasks the oracle runs the same tests as the mandatory check on a clean second
  run, so it shows reproducibility, not that the tests are sufficient.
- **Evidence chain.** It detects tampering *inside* the chain. With no external anchor, someone who can rewrite the file can
  rewrite the whole chain. It is not proof of immutability.
- **Sandbox is not hardened.** Model-written code runs inside a Docker container with no network, memory/CPU/time limits and a
  read-only dependency cache, but as the container's root with Docker's default seccomp profile and no extra capability
  dropping. Treat it as containment for a demo, not as protection against hostile code.
- **Scope.** One fixture (a Java discount service), one editable file, one model, small samples. No multi-language or
  multi-repository support, no memory, no multi-agent flow.
- **Clarification is one-shot.** `NEEDS_CLARIFICATION` ends the run; there is no reply flow yet.
- **Spring Boot is not used.** The fixture is plain Maven + JUnit; Spring would add build time without changing what is demonstrated.

## Layout

```
src/proofline/     orchestrator, model client, workspace, runner, verifier, evidence, API
ui/index.html      single-page UI (no build step)
fixtures/          discount-service (editable project) and seeds/ (prepared defects)
acceptance-tests/  protected tests + requirements.json, injected by the verifier, outside the editable workspace
evaluations/       task contracts, per-task oracles, harness, report, RESULTS.md
tests/             68 tests (Docker is real; only the model API boundary is mocked)
```

Generated runs go to `evidence/` (git-ignored).
