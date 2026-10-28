# Proofline as a usable verification tool with a human review step (design)

Date: 2026-10-28. Status: for approval. Nothing here is built yet.

## Intent and why

Proofline today is a research harness: two toy fixtures bundled inside the repo, a read-only local page, and a verifier that only a developer of Proofline can point at anything else. The owner wants something **people can actually use**: a general tool that verifies a code change (made by an agent or by a person), shows the reviewer exactly what was and was not verified, and records the reviewer's decision to **approve or reject** that change. The platform then uses that decision.

It is a **verification tool**, not an app. The interface exists to help the reviewer verify, not to decorate results. The first (and only, in this project) ecosystem implemented is Java/Maven; the design must not hard-wire Maven into the core so that others can follow.

Deadline: the hackathon form closes 4 Nov 2026 21:00 ET (5 Nov 03:00 Spain); target submission 3 Nov. Work today (28 Oct) leaves about five working days.

## What stays as it is (the core, already built)

Verdicts `PASS / FAIL / INCONCLUSIVE / NEEDS_CLARIFICATION`; per-requirement scope (`VERIFIED / FAILED / NOT_VERIFIED / NOT_CHECKED`) and `fully_verified`; a pristine sandbox per check with model-written code running unprivileged; protected acceptance tests; the file allowlist; the hash-chained evidence log; strict project manifests; the call budget. The interface and any new endpoint never compute or alter a verdict.

## Goals

1. **Usable on a repository that is not the Proofline repo.** A person with a Maven project, protected tests and a manifest can verify a change from the terminal and open it in the interface, without editing Proofline's code.
2. **Verify a change from anyone.** The input is "this working tree against this base", not necessarily a model's output. Muse Spark stays an optional mode (propose, verify, repair), unchanged.
3. **A review screen that helps decide.** For one verification it shows: the diff, every declared requirement with its status and the check behind it, every gap (NOT_CHECKED / NOT_VERIFIED) as a gap, the real output of any check on demand, and whether the evidence chain is intact.
4. **A recorded human decision.** The reviewer approves or rejects; a rejection requires a reason.
5. **A usable gate.** A command that CI or a script runs and that succeeds only for a change that was approved in exactly the state it is now in.
6. **Feedback that is used.** A rejection reason goes back to the agent as repair input; decisions that disagree with the verdict are surfaced as signals that checks are missing.
7. **An adapter seam.** Everything specific to Maven lives in one adapter, so that the core does not know the word "mvn".

## Non-goals and anti-goals

- No hosted service, accounts, authentication, multi-user concurrency or GitHub app. Local, single user, localhost.
- No second ecosystem (Python etc.) in this project. The seam is built; only Maven is implemented. (A claim of generality is therefore limited to "designed for", and the documentation must say so.)
- No auto-merge. The tool produces a verdict, a decision and a gate result; the team merges.
- No learning, training or tuning from decisions. Signals are descriptive counts for people to read.
- No new verdict logic, and no weakening of an existing safeguard (isolation, allowlist, fail-closed paths, call budget) to make usage easier.
- Anti-goals (would make this a failure even if it works): any claim of being the first or only tool of this kind; presenting an unauthenticated reviewer name as identity; letting a decision change or hide a verdict; breaking the 213 existing tests' guarantees.

## Constraints

- No real model calls are made while building or testing. Tests use the simulated model. The original authorization allowed 9 calls; 4 are spent, 5 remain, and any further spend needs the owner's explicit order. The API key stays in the git-ignored `.env`.
- Honesty rules that govern all text: no overclaiming; limitations stay documented (same-container verdict channel, unanchored/unsigned chain, shared kernel, self-declared reviewer).
- Evidence samples and existing results are not rewritten.
- Every change goes by pull request, with CI green and the owner's explicit order before merge.

## The design (decided by the owner unless marked "my call")

### Where it sits in the flow

```
change (agent or person) -> independent verification (verdict + scope + evidence)
   -> HUMAN: approve / reject (+ reason)            <- new
   -> approved: gate succeeds only for this exact change
   -> rejected: reason goes back to the agent for another attempt
   -> verdict-vs-decision disagreements: signals of missing checks
```

### Decision record (owner: approve/reject, platform takes the feedback; the rest is my call)

A new evidence record `human_decision`, appended to the same hash-chained file as the verification it judges. Fields: decision (`approve|reject`), reason (required on reject, optional on approve), reviewer (free text, defaulting to the OS/git user name; **not authenticated**), time, the `run_id` of the verification judged, its `diff_sha256` and `base_revision`, the verdict and `fully_verified` at the time, and the full requirement-by-requirement status the reviewer was looking at. The decision is therefore tied to the exact change and the exact scope on screen.

A later decision on the same change supersedes an earlier one; both stay in the chain.

### Gate (my call)

`proofline gate <run>` exits 0 only if: the evidence chain verifies; the latest decision is `approve`; the diff hash of the working tree **now** equals the approved `diff_sha256` and the base is unchanged; and either the verification was `PASS` with `fully_verified`, or the approval is an explicit override (see below). Distinct non-zero codes for: not approved, rejected, change differs from the approved one, chain invalid, no verification. A change edited after approval no longer passes.

### Approving something the verifier did not fully pass (my call)

Allowed, because the human decides, but it is an **override**: it requires a reason and is recorded as such. Overrides are: approve on `FAIL`, on `INCONCLUSIVE`, or on `PASS` with gaps. The interface says so plainly ("approving with 1 of 3 requirements not checked").

### Rejection feeds the agent (my call)

On reject, if an agent is in use, the rejection reason plus the real output of the failed or uncovered checks is given to the model as repair input, exactly like Maven output today. It counts as one more attempt inside the existing attempt and call-budget limits. Without an agent, the reason is just recorded.

### Signals (my call)

A read-only history lists decisions with a label when they disagree with the verdict: `REJECTED_PASS`, `APPROVED_WITH_GAPS`, `APPROVED_FAIL`, `APPROVED_INCONCLUSIVE`. Each links to the requirement(s) involved. It suggests nothing automatically and changes no verdict. The text must say these are counts of human decisions, not a measure of test quality.

### The review screen (owner: the interface must help verification; layout is my call)

One screen per verification, in this order of emphasis: the scope (requirements by status, gaps first and visually loud), the diff, the checks with on-demand real output, the evidence-chain status, and the decision bar (Approve / Reject, reason box, override notice). Actions that exist: open a check's real output; **re-run one check** (a new verification record is appended; earlier ones are never altered); compare two verifications of the same project (which check changed). A history view lists runs and decisions with the signals. It remains a reader of the evidence plus actions that start real verifications or append a decision.

Not in the MVP, though earlier discussed: adding a new check from the interface. A requirement with no check is shown as a gap; closing it means editing the manifest and protected tests by hand.

### Using it on another repository (my call: local CLI + local interface)

- `proofline init` in a Maven repo creates a manifest skeleton (project id, ecosystem `java-maven`, editable files, protected-tests directory, checks, requirements) for the person to complete, and refuses to overwrite one.
- `proofline warm` is the one networked step: fills the dependency cache for that project. Every verification afterwards is offline, as today.
- `proofline verify` verifies the working tree against a base (a git revision or a copy), appends to that project's evidence file, and prints the verdict and scope.
- `proofline gate`, `proofline serve` (the interface, bound to localhost), and the existing agent mode.
- Manifest path rules stay strict (contained in the target repository, no symlinks, no unknown keys) but their root becomes the target repository instead of the Proofline repository.
- Where evidence lives for a foreign repo: inside that repo, in a directory the person can ignore in git (default `.proofline/`).

### The adapter seam (my call)

A single adapter module owns everything Maven-specific: the container image, the command and test selector, how the result is classified, how model code is run unprivileged, how protected tests are placed. The core calls the adapter through a small interface and never references Maven. The manifest names it with `ecosystem: java-maven` (default, so existing manifests keep working). The refactor must not change any existing result; the existing suite is the safety net. No second adapter is written.

## What is left to the builder

Names of commands and modules, the exact manifest schema additions, the API shape, the layout and styling of the screen, how `init` detects test classes, the format of the diff view, and the test strategy, as long as the guarantees above hold: decisions are tamper-evident in the same chain, the gate fails closed, nothing changes a verdict, adversarial and isolation tests keep passing, and the suite plus CI stay green.

## Done means

1. A person who has only this repository's README can run `init`, `warm`, `verify`, open the interface, approve or reject, and run `gate` on a small Maven project that is not part of Proofline, in about 15 minutes. This is tried on a second Maven project created for the purpose, not only the bundled fixtures.
2. The gate refuses a change edited after approval, a rejected change, a tampered chain, and a verification that never ran, each with its own exit code; each has a test.
3. The existing behaviour is unchanged: all current tests still pass, including the isolation tests.
4. README, RESULTS and the form text describe exactly what exists, say it is Java/Maven-only today, that the reviewer is not authenticated, and that signals are descriptive.

## Decisions made after the builder check (all my calls, to be checked by the owner)

1. **Unit of work.** One `verify` invocation creates one evidence file (`.proofline/runs/<run_id>.jsonl`). It may contain several verification records (agent attempts), as today. A decision judges the **last verification record in that file** and is appended to the same file. `gate` takes a `run_id`, or `--latest` (most recent run of the project); `serve` lists runs from that directory. A later decision in the same file supersedes the earlier one.
2. **What the gate recomputes.** The existing `diff_sha256` is the hash of `diff -ruN` output, which contains the absolute paths of the base and workspace (checked in `evidence-samples/rp2/`), so it cannot be reproduced later. A new **canonical change hash** replaces it for the gate: the sorted list of changed files as relative paths, each with the SHA-256 of its content in the workspace (or a deletion marker) and its mode, hashed together; independent of directories, timestamps and tool output. `diff_sha256` stays in the records, unchanged, for display. `verify` additionally records that canonical hash and `inputs_sha256`, covering the manifest and every protected test file, next to the existing `diff_sha256` and `base_revision`. `gate` recomputes: the diff hash of the working tree against the same base, and `inputs_sha256`; any difference fails (changed after approval). The change is the editable files; edits outside them would already be a FAIL at verification. Untracked files inside the project count; `.proofline/` and build output do not. The base is a git revision (materialised with `git archive`) or a directory; the record stores which, so `gate` uses the same one. The tree hash of the base must match `base_revision`.
3. **Gate trust model.** `gate` does not re-run any check; it trusts the chain plus the recomputed hashes. A fabricated chain by someone with write access is not detected (documented limitation: unsigned, unanchored). The README must say a green gate means "approved by a person in exactly this state, according to an intact record", not "re-verified now".
4. **Overrides.** Stored explicitly on the record as `override: true` plus a required reason, computed at decision time from the verdict and scope (never supplied by the client). `NEEDS_CLARIFICATION` has no verified change and cannot be approved. The gate exits 0 for an override but prints a notice and an override flag; `gate --strict` fails on overrides (distinct code), for CI users who want that.
5. **Reject and the agent.** Recording a rejection never triggers a model call. A separate explicit action (a button in the interface, or `proofline retry <run_id>`) starts the agent with the rejection reason plus the real check output as repair input. For a foreign repo the task text is the one stored in the run's `task` record (`verify` accepts `--task`, optional). Retry exists only if a model key and the call-budget ledger (`MODEL_CALL_BUDGET_FILE`) are configured, otherwise it refuses; during development and testing it uses the simulated model only. After the attempt limit the reason is just recorded.
6. **Exit codes (one table).** 0 approved and unchanged; 10 not approved yet; 11 rejected; 12 change differs from the approved one; 13 chain invalid; 14 no verification in the run; 15 override approval under `--strict`; 2 usage error. Each has a test.
7. **Re-run one check.** It appends a record `check_rerun` with that check's real result, clearly labelled partial. It has no verdict and does not replace the verification. The decision always judges the last full verification; the screen shows the re-run next to it. "Compare" shows, for two full verifications of the same project, which checks changed status. Re-running never alters a verdict.
8. **Existing projects.** `projects/*.json` and every current test and command keep working unchanged: with no `--repo`, the manifest root is the Proofline repository as today. The target-repo root is an additional, explicit mode, and the containment checks apply to whichever root is used.

## What blocks the MVP and what waits (scope tiers)

Added after review of the design, to keep six days realistic. Timeline: design approved 28-29 Oct; core flow 29-30 Oct (small PRs with tests and CI); external-project trial 31 Oct (approve, reject, change after approval, tampered chain); stabilise and update README/form 1 Nov; freeze and submit 2-3 Nov.

| Blocks the MVP | Can wait (cut first if time runs out) |
|---|---|
| Canonical change hash, stable and reproducible | Signed or externally anchored hashes |
| Gate detects a change after approval; broken chain or missing decision fails | Reviewer authentication |
| The screen separates the verdict from the human decision | Signals history beyond a plain list; visual compare of two runs |
| One external Maven project works end to end | A second language or adapter |
| Decision recorded in the chain; exit-code table; `init`/`warm`/`verify`/`serve`/`gate` | `retry` from the interface (the CLI path is enough); re-run one check from the interface |

Cut order if time runs short: compare view, then signals, then interface `retry`, then re-run one check. The core flow is never cut.
