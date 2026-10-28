# Evaluation results

Round 1 (baseline, commit `ed7d1ea`) is below. Round 2 (after `NEEDS_CLARIFICATION` and requirement scope) and round 3 are after it.
The **second fixture (`retry-policy`, phase 3C)** is the last section: a different project, kept apart from the discount-service rounds.

## Round 1

Muse Spark 1.3 Contributor, real API; verifier and agent loop unmodified during the round. Raw evidence lives in
`evidence/eval/` (git-ignored, hash-chained JSONL). Reproduce: `python -m evaluations.harness real 3 S1 S2 S3 E1 E2 E3 A1 A2`
and `python -m evaluations.harness real 5 U1 U2`; summarise with `python -m evaluations.report <summary.json>`.

Small samples (3–5 runs per task, one model, one fixture). This demonstrates the concept; it does not show that Proofline
verifies arbitrary changes.

## Results

| Group | Runs | Outcome |
|---|---|---|
| Simple S1–S3 | 9 | 9 PASS on first attempt |
| Edge cases E1–E3 | 9 | 9 PASS on first attempt |
| U1 insufficient coverage | 5 | 5 PASS, oracle PASS (the model never introduced the defect) |
| U2 mandatory check cannot run | 5 | 5 INCONCLUSIVE, no repair attempted (correct) |
| A1 underspecified | 3 | 3 `UNGROUNDED_PASS` |
| A2 ambiguous boundary | 3 | 3 × FAIL then PASS (2 attempts each) |

Harness validation with a simulated model (not Muse Spark results): all 8 correct implementations pass their oracle;
7 known-defective implementations are all flagged FAIL; U1 with an injected `>=` defect is accepted by the verifier
(**FALSE_PASS**) and rejected by the full suite.

## Findings

1. **A PASS only means the declared checks passed.** U1 (simulated defect) shows the verifier says PASS when no mandatory
   check exercises the defect. Proofline has no notion of coverage or of which properties of the requirement were verified.
2. **Missing evidence is not success.** When a mandatory check cannot run (U2), the verdict is INCONCLUSIVE and the repair
   loop is not triggered, because the code was never judged.
3. **Real failures can drive a repair.** In A2 the model read `>` as "over 100", received the real acceptance failure
   (`expected: <90.00> but was: <100.00>`) and repaired to `>=`. This is evidence-guided repair, not understanding of intent:
   the failure output revealed the hidden rule.
4. **Underspecified requirements get invented policies.** In A1 the model invented a 10 % loyalty rule in all 3 runs and added
   unrequested scope (boolean overloads, aliases, getters, and in one run a stateful flag), without documenting the
   assumption about what a returning customer is. The agent has no way to ask for clarification.

## Limitations

- **Oracle independence.** For S1–E3 and A2 the oracle runs the same tests as the mandatory acceptance check, on a clean
  second run. It confirms reproducibility, not semantic independence: it cannot reveal gaps in those tests. Only U1's oracle
  is broader than its mandatory checks. Detection rates on S1–E3 therefore say nothing about test completeness.
- **No real defect was observed from the model.** Detection of defects is demonstrated only with simulated or seeded
  defects. With the real model, false PASS = 0 means none occurred in these runs.
- **A1 PASS is not a functional success.** It is reported separately as `UNGROUNDED_PASS` and excluded from success rates.
- **Leak check is limited.** No oracle/hidden-intent strings or API key appeared in the proposals or evidence reviewed;
  this does not prove the absence of every possible leak. In mandatory-check tasks, failure output is intentionally fed back.
- **Evidence chain** detects internal tampering but is not anchored outside the agent-editable area.
- **Contributor tier** trains Meta's models on inputs and outputs; only synthetic fixture code was sent.

## Next steps (not done, to keep this round's object of measurement fixed)

Explicit clarification request (`NEEDS_CLARIFICATION`, distinct from INCONCLUSIVE) and surfacing which requirement
properties each verdict covers; then repeat the same battery.

## Round 2: regression after the clarification contract

Same battery, same model (Muse Spark 1.3 Contributor, real API), new system prompt that lets the model ask instead of
guessing, plus `NEEDS_CLARIFICATION` and per-requirement scope in the verifier. Evidence in `evidence/eval-round2/`
(round 1 kept untouched in `evidence/eval-round1/`).

| Group | Round 1 | Round 2 |
|---|---|---|
| S1–S3 (9 runs) | 9 PASS, first attempt | 9 PASS, first attempt, no questions asked |
| E1–E3 (9 runs) | 9 PASS, first attempt | 9 PASS, first attempt, no questions asked |
| U1 (5 runs) | 5 PASS (no defect introduced) | 5 PASS (no defect introduced) |
| U2 (5 runs) | 5 INCONCLUSIVE, no repair | 5 INCONCLUSIVE, no repair |
| A2 (3 runs) | 3 × FAIL then PASS | 3 × FAIL then PASS (2 attempts) |
| A1 (3 runs) | 3 `UNGROUNDED_PASS`: invented 10 % policy, extra scope | 3 `NEEDS_CLARIFICATION`: asked for the percentage, the signature and how a returning customer is identified; nothing implemented, nothing verified |

What changed: only A1, and in the intended direction. What did not change: the 28 S/E/U runs show no
unnecessary questions, the unresolved-boundary case (A2) still fails for real and is repaired from the verifier's output,
and a check that cannot run still ends in INCONCLUSIVE. All evidence chains validate; no API key or oracle/hidden-intent
strings appear in proposals or questions.

Caveats: same small samples as round 1 (3–5 runs per task). The model asking in 3/3 A1 runs does not show it will always
ask when it should, or never when it should not; it shows no regression on this battery. A2 is still resolved by the
verifier's failure output, not by the model understanding the intent. The oracle limitations of round 1 still apply.
A `NEEDS_CLARIFICATION` ends the run without verifying anything, so it carries no claim about code correctness.

## Round 3: native tool calling

The agent now talks to Muse Spark through the API's native tool calling (`apply_edits` or `ask_clarification`, exactly one call per
reply) instead of asking for JSON in the prompt. Nothing else changed: same battery, same model (Muse Spark 1.3 Contributor, real
API), same contracts. Evidence in `evidence/eval-round3/` (a sample is published in `evidence-samples/round3/`).

| Group | Round 2 | Round 3 |
|---|---|---|
| S1–S3, E1–E3 (18 runs) | 18 PASS, first attempt | 18 PASS, first attempt |
| U1 (5 runs) | 5 PASS (no defect introduced) | 5 PASS (no defect introduced) |
| U2 (5 runs) | 5 INCONCLUSIVE, no repair | 5 INCONCLUSIVE, no repair |
| A2 (3 runs) | 3 × FAIL then PASS | 3 × FAIL then PASS (2 attempts) |
| A1 (3 runs) | 3 `NEEDS_CLARIFICATION` | 3 `NEEDS_CLARIFICATION` |

Outcomes are identical to round 2, run by run. The 34 real runs produced 37 model calls (the A2 repairs add 3; an A1 question counts as a call) and no
`model_error`: every reply was exactly one well-formed tool call. All evidence chains validate; no API key or oracle/hidden-intent strings in proposals or questions.

API behaviour observed: `tool_choice` accepts only `"auto"` (`"required"` and named choices return HTTP 400), so a text-only reply
cannot be forced into a tool call. The client fails closed: no tool call, more than one, an unknown tool or invalid arguments end the run as
`INCONCLUSIVE`, never as a pass.

Caveats: same small samples as before, so this shows no regression, not that tool calling is more reliable than prompt-level JSON.

## Second fixture: retry-policy (phase 3C)

Kept apart from the discount-service rounds: a different project (its own manifest, protected tests and requirements), one task, three
runs. Its numbers are never added to the 102-run totals above.

**What ran.** 28 October 2026, on `main` at `16b1cdc`, real Muse Spark (`muse-spark-1.3-contributor`, native tool calling), task `RP1`:
implement `RetryPolicy.decide` and `Backoff.delayAfter` from an **empty skeleton**. The task text carries the full contract
(attempts are counted including the first one; transient errors by type; capped exponential backoff that must not overflow). Command:

```
MODEL_CALL_BUDGET_FILE=$PWD/evidence/eval-rp1/budget.json PROOFLINE_EVAL_OUT=$PWD/evidence/eval-rp1 \
  .venv/bin/python -m evaluations.harness real 3 --project retry-policy RP1
```

Three independent runs, each from a clean workspace. Cap: 9 model calls (a persistent ledger, counted before each call); **3 were used**,
6 remain unused. Only synthetic code was sent. `RP2` (repair of the seeded off-by-one) was **not** run with the real model.

| Run | Verdict | Oracle | Attempts / calls | Requirements | Agent loop time | Tokens in / out / total |
|---|---|---|---|---|---|---|
| 0 | `PASS`, fully verified | `PASS` | 1 / 1 | R1, R2, R3 verified | 48.5 s | 1,425 / 3,391 / 4,816 |
| 1 | `PASS`, fully verified | `PASS` | 1 / 1 | R1, R2, R3 verified | 34.5 s | 1,425 / 2,285 / 3,710 |
| 2 | `PASS`, fully verified | `PASS` | 1 / 1 | R1, R2, R3 verified | 64.9 s | 1,425 / 5,239 / 6,664 |
| **Total** | 3/3 | 3/3 | 3 / 3 | | | **4,275 / 10,915 / 15,190** |

Each run passed all five checks: `maven_tests`, `acceptance_attempt_limit`, `acceptance_error_classification`,
`acceptance_backoff_growth`, `acceptance_backoff_saturation`. No repair was needed, no edit was rejected, and every diff touched only
`RetryPolicy.java` and `Backoff.java`.

Notes on the columns: tokens are exactly what the API reported for each call (available for all three); no cost is computed. "Agent loop
time" covers the model call and the verification, not the oracle. The model's own summary of its change (a claim, not evidence) is
in each trace.

**Evidence** (hash-chained, all six chains verify; see [`evidence-samples/README.md`](../evidence-samples/README.md)):
[`evidence-samples/rp1/`](../evidence-samples/rp1/) holds `RP1-real-{0,1,2}.jsonl` (agent runs), `RP1-real-{0,1,2}.oracle.jsonl`
(oracle runs), `summary-real-retry-policy.json` and `budget-ledger.json` (final state of the call ledger: limit 9, used 3).

### What this shows

On a second, differently shaped project, three independent runs of Muse Spark implemented an explicitly specified policy from an empty
skeleton and every run was verified `PASS` on all three requirements. It also exercised the new parts end to end: project selection from a
manifest, per-call token records and the call cap.

### What it does not show

- **The oracle is not an independent check here.** In `RP1` all five checks are mandatory, so the oracle runs the *same* checks as the
  agent's verification. `Oracle: PASS` confirms the result reproduces; it cannot reveal a gap in those tests.
- **A specified task, not an ambiguous one.** The contract was written out in the task. This does not show that the model resolves
  ambiguous requirements, notices its own mistakes, or understands intent.
- **No failure was observed**, so nothing was learned about how the model fails on this fixture. Zero repairs also means the repair loop
  was not exercised on a real model run here.
- **Sample size.** One model, one task, three runs with the same prompt, one small two-file fixture. This is a first signal, not a success
  rate, and does not extend to other projects.
- **How sensitive the tests are** is supported only by a hand-run check of eight plausible wrong implementations of the correct solution
  (exact-class match, retrying unknown errors, missing null/range/argument checks, no cap, exponent off by one, no saturation): the
  protected tests rejected all eight; two are kept in the automated suite. That is a small sanity check, not exhaustive mutation testing.
