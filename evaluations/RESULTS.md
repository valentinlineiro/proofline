# Evaluation results (round 1)

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
