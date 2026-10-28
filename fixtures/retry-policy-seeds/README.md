# Seeds for the retry-policy project

Each directory is an overlay applied on top of `fixtures/retry-policy` for the controlled demo. **The defects are seeded
deliberately by us**; they are not mistakes Muse Spark made on its own. The overlay keeps no comment that reveals the defect:
the agent has to find it from the verifier's real output.

| Seed | Defect | Check that exposes it |
|---|---|---|
| `off-by-one` | `RetryPolicy.decide` retries while `failedAttempts <= maxAttempts` instead of `<`, so a policy of 3 attempts still proposes a fourth | `acceptance_attempt_limit` (R1) |

Everything else in the overlay is a correct implementation, so only R1 fails. Keep documentation here, not inside a seed
directory: files in a seed are copied into the workspace and would count as changes outside the allowlist.
