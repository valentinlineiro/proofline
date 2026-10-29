# Proofline

[![ci](https://github.com/valentinlineiro/proofline/actions/workflows/ci.yml/badge.svg)](https://github.com/valentinlineiro/proofline/actions/workflows/ci.yml)

**An agent saying "done" is a claim. Proofline asks for evidence.**

**Demo video (2 min, with narration and captions):** https://youtu.be/1qrXtrT1ETg — scene 1 is a real Muse Spark run with a defect we prepared; scene 2 uses a simulated model.

Proofline lets a coding agent (Meta's Muse Spark) change a Java/Maven project, then judges the change with checks that
the agent neither writes nor runs ("independent" means independent of the agent, not of the tests; see Limitations): protected acceptance tests executed in a network-less Docker container. The verdict is
`PASS`, `FAIL`, `INCONCLUSIVE` or `NEEDS_CLARIFICATION`, and it comes only from the verifier and a hash-chained evidence
log, never from the model's own account.

What it claims: *a declared set of checks ran and passed.* What it does not claim: that the code is correct. A `PASS`
carries an explicit scope (which requirements had a check that ran) so it cannot be read as more than it is.

## How it works

```
task ──▶ agent_loop ──▶ model_client ──▶ Muse Spark (Meta Model API)
            │                 └─ native tool call: apply_edits  OR  ask_clarification (exactly one)
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
| Model client | `src/proofline/model_client.py` | Muse Spark via the OpenAI-compatible Meta Model API, using **native tool calling** (`tool_choice="auto"` is the only mode the API supports). Edits **or** a question, never both; anything else fails closed. |
| Project manifest | `projects/<id>.json`, `src/proofline/project.py` | Per project: editable files, checks, protected tests, default task. Validated strictly; an invalid manifest is an error, never a permissive run. |
| Workspace | `src/proofline/workspace.py` | Edit allowlist (from the manifest), rejects absolute paths, `..`, symlinks, ambiguous matches; a symlink in the workspace counts as a change. |
| Runner | `src/proofline/runner.py` | One throwaway container per check: no network, 1 GB, 2 CPUs, 300 s. |
| Verifier | `src/proofline/verifier.py` | Verdict + per-requirement scope. Re-checks the whole diff against the allowlist before running Maven. |
| Evidence | `src/proofline/evidence.py` | Hash-chained JSONL; `verify_chain` detects edits inside the chain. Records the project and the token usage the API reported per model call. |
| Call cap | `src/proofline/budget.py` | Optional persistent ledger that caps real model calls across runs (`MODEL_CALL_BUDGET_FILE`); counted before each call, never refunded, fail-closed. |
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
git clone https://github.com/valentinlineiro/proofline.git proofline && cd proofline
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
.venv/bin/pytest -q                                   #  tests, ~9 min, needs Docker + the warm cache, no API key
.venv/bin/uvicorn proofline.api:app --port 8765       # then open http://localhost:8765
```

The API is local-only and unauthenticated, and runs one job at a time. Do not expose it.

## Use it on your own Maven project

Proofline can verify a change in a repository of your own (Java/Maven only today), record a person's decision on it, and
gate on that decision. Nothing here needs a model: the change can come from an agent or from you.

```bash
.venv/bin/pip install -e .          # in the Proofline clone: provides the `proofline` command
cd /path/to/your/maven/repo
proofline init                      # writes .proofline/project.json and .proofline/acceptance/ (a skeleton to complete)
```

Complete the skeleton: `editable` lists the files a change may touch; put your protected tests in `.proofline/acceptance/`
(files named `Acceptance*.java`), one `checks` entry per test method (`Class#method`), and describe each requirement in
`.proofline/acceptance/requirements.json` with the checks that cover it. The manifest is validated strictly (paths inside the repo,
no symlinks, every selector must name a test that exists).

```bash
proofline warm                      # the one step with network: downloads dependencies; your tests must compile and pass here
proofline verify                    # your working tree against HEAD (or --base REV, or --base DIR for a pristine copy)
proofline serve                     # review screen on http://127.0.0.1:8765: gaps first, your decision apart from the verdict
proofline decide approve --run RUN_ID --reason "..."     # or: reject --reason "..." (a reason is required to reject)
proofline gate --latest             # exit 0 only if approved in exactly the current state
```

`verify` exits 0 for a fully verified `PASS`, 3 for a `PASS` with gaps, 4 for `FAIL`, 5 for `INCONCLUSIVE`. Run files are in
`.proofline/runs/` (add it to `.gitignore` if you do not want them committed). To try it without your own project, copy
`examples/inventory/` (it is incomplete on purpose: its protected tests fail until `Stock.reserve` refuses invalid and excessive
reservations), `git init && git add -A && git commit` there, then run the commands above.

| `gate` exit | Meaning |
|---|---|
| 0 | approved by a person in exactly this state (a notice is printed if it was an override) |
| 10 / 11 | not approved yet / rejected |
| 12 | the change, the base, the manifest or the protected tests differ from what was approved |
| 13 | the evidence chain does not verify |
| 14 | the run has no finished verification the gate can use |
| 15 | approved as an override, and `--strict` was given |
| 2 | usage error |

What the decision is, precisely: a record in the same hash chain as the verification, tied to the exact change (a hash of the
changed files, not the text of a diff) and to the scope the reviewer saw. Approving something that is not a fully verified `PASS`
is an *override*: it needs a reason and is recorded as one. The gate does not re-run any check; it trusts an intact chain and
recomputes the hashes. The reviewer's name is typed in, not authenticated, and the chain is unsigned (see Limitations).

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
per task, on the discount-service fixture, three rounds of the same 10-task battery (so 102 runs are repeats, not 102 distinct cases), all on
`muse-spark-1.3-contributor` (see the note on the tier above):

- Simple and edge-case tasks (S1–S3, E1–E3): 18/18 `PASS` on the first attempt, in each round (18 runs per round).
- U2 (a mandatory check cannot run): 5/5 `INCONCLUSIVE`, no repair attempted.
- A2 (ambiguous boundary): 3/3 failed for real first and were repaired from the verifier's output. This is evidence-guided repair,
  not understanding of intent.
- A1 (underspecified): round 1, 3/3 invented a policy (`UNGROUNDED_PASS`); rounds 2 and 3, 3/3 asked for clarification.
- Round 3 repeats the battery with native tool calling instead of prompt-level JSON: identical outcomes, no malformed model replies (34 real runs, 37 tool calls).
- **Known false `PASS` (U1)** exists **only with a simulated defect**: when no mandatory check exercises a defect, the verifier
  accepts it and only the full suite catches it. With real Muse Spark, the model never introduced that defect, so no real false
  `PASS` was observed. That is not evidence that none can occur.

Not to be confused with the test suite: the evaluation is the same 10-task battery run three times with the real model (34 runs per round, 102 runs in
total). The automated suite is a separate set of  tests that needs no API key.

Simulated-model runs (`sim`, `sim-bad`) validate the harness and the oracle, not Muse Spark. Reproduce the real battery with
`.venv/bin/python -m evaluations.harness real 3 S1 S2 S3 E1 E2 E3 A1 A2` and `... real 5 U1 U2`.

### Second fixture: `retry-policy`

A deterministic retry decision (attempt limit counted from the first attempt, transient-error classification, capped exponential backoff)
with its own manifest and protected tests. Results are reported **separately** from the discount-service numbers above:

- 3 real runs of `RP1` (implement from an empty skeleton, full contract in the task): 3/3 `PASS`, all three requirements verified,
  one model call each, no repair; 4,275 input and 10,915 output tokens in total as reported by the API. 3 of a 9-call cap were used.
- **Read with care.** The oracle runs the same checks as the agent's verification here, so it confirms reproducibility, not that the
  tests are sufficient. The task was fully specified, no failure was observed, and it is one model, one task and three runs: a first
  signal, not a success rate.
- 1 real run of `RP2`: a repair of one boundary defect **we** seeded in an already implemented policy (not an implementation from scratch like
  `RP1`; the task text states the correct condition): the verifier failed it first, the model repaired it
  in one call, `PASS` on all five checks. One run, same-checks oracle. 1 of a 2-call cap used.
- Evidence: [`evidence-samples/rp1/`](evidence-samples/rp1/), [`evidence-samples/rp2/`](evidence-samples/rp2/); details and caveats in [`evaluations/RESULTS.md`](evaluations/RESULTS.md).

Select a project and plan a run without spending anything (no workspace, no model call):

```bash
.venv/bin/python -m evaluations.harness real 3 --project retry-policy RP1 --dry-run
# a real run should be capped: create the ledger once, then point the run at it
.venv/bin/python -m proofline.budget init evidence/budget.json 9
MODEL_CALL_BUDGET_FILE=$PWD/evidence/budget.json .venv/bin/python -m evaluations.harness real 3 --project retry-policy RP1
```

## Limitations

- **Verification is only as good as its checks.** `PASS` means the declared checks passed. Requirements are mapped to checks
  by hand (`acceptance-tests/requirements.json`); Proofline does not measure coverage or prove the mapping is complete.
- **Oracle independence.** For most evaluation tasks (and for `RP1`) the oracle runs the same tests as the mandatory checks on a
  clean second run, so it shows reproducibility, not that the tests are sufficient.
- **Evidence chain.** It detects tampering *inside* the chain. With no external anchor, someone who can rewrite the file can
  rewrite the whole chain. It is not proof of immutability.
- **Sandbox: partly hardened, not a security boundary.** Every check gets a pristine copy of the workspace plus the protected
  tests, in its own throwaway container (no network, memory/CPU/time limits, read-only dependency cache, all capabilities
  dropped except the few Maven needs, `no-new-privileges`, PID limit). Maven runs as root; the forked test JVM, where
  model-written code executes, runs as `nobody` and so cannot write the sources, the protected tests, the compiled classes
  or the reports, nor signal Maven. `tests/test_verifier_isolation.py` runs hostile Java that tries exactly that (real
  Docker) and checks that it fails. What this does not stop: code in the test JVM still talks to Maven over surefire's
  channel, so deliberately forged reports are not ruled out, and Docker's shared kernel is the usual container boundary.
  The verifier is independent of the agent and of its side effects between checks, not of the environment that runs the code.
- **Scope.** Two small Java fixtures (a discount service and a retry policy), at most two editable files each, one model, small
  samples; the API and UI still run only the default project. No multi-language or multi-repository support, no memory, no
  multi-agent flow.
- **Human review.** The reviewer is whoever types a name: not authenticated. A green `gate` means "approved in exactly this state
  according to an intact record", not "verified again now". Descriptive signals (a rejected `PASS`, an approval with gaps) are counts
  of human decisions, not a measure of how good the tests are. No hosted service, accounts or merge integration; Maven is the only
  adapter implemented (the core calls an adapter interface, but a second ecosystem has not been written or tried).
- **Clarification is one-shot.** `NEEDS_CLARIFICATION` ends the run; there is no reply flow yet.
- **Spring Boot is not used.** The fixture is plain Maven + JUnit; Spring would add build time without changing what is demonstrated.

## Layout

```
src/proofline/     orchestrator, model client, workspace, runner, verifier, evidence, API, CLI, review (decision + gate), adapters/
ui/index.html      single-page UI (no build step)
examples/          inventory: a Maven project outside the fixtures, used to try Proofline on a repository of its own
projects/          one manifest per project (editable files, checks, protected tests)
fixtures/          discount-service and retry-policy (editable projects) and their seeds (prepared defects)
acceptance-tests*/ protected tests + requirements.json per project, injected by the verifier, outside the editable workspace
evaluations/       task contracts, per-task oracles, harness, report, RESULTS.md
tests/              tests (Docker is real; only the model API boundary is mocked)
```

Generated runs go to `evidence/` (git-ignored).

## License

MIT, see [LICENSE](LICENSE).
