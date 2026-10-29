# Review Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Proofline usable on a Maven repository that is not Proofline's own, with a recorded human approve/reject decision and a gate that fails closed.

**Architecture:** Keep the verifier core. Add (1) a canonical, reproducible change hash, (2) a Maven adapter behind a small seam, (3) an external-repo mode for manifests, (4) a decision record plus `gate` in the same hash chain, (5) a `proofline` CLI, (6) decision endpoints and a review screen in the existing API/UI. Existing projects and tests keep working unchanged.

**Tech Stack:** Python 3.12, pydantic, FastAPI, pytest, Docker + Maven (existing). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-review-workbench-design.md` (read it first, including the final section "Decisions made after the builder check").

## Global Constraints

- No real model calls while building or testing; tests use the simulated model. The call ledger (`evidence/eval-rp1/budget.json`, `evidence/eval-rp2-budget.json`) is never touched. `.env` and keys are never read, printed or committed.
- No verdict logic changes; no safeguard weakened (per-check pristine sandbox, unprivileged test JVM, allowlist, fail-closed paths). Existing tests (213) keep passing, including `tests/test_verifier_isolation.py`.
- Existing evidence-record fields keep their names and meaning; new fields are additive. `diff_sha256` stays as is.
- Run suite commands with `.venv/bin/python -m pytest` (system python lacks the package). Full suite ~9 min needs Docker; run targeted tests while developing and the full suite before each PR.
- One small PR per task (or two adjacent ones); CI green and the owner's explicit order before any merge. Commit messages end with the attribution lines the session specifies.
- Exit codes (one table): 0 ok, 2 usage, 10 not approved, 11 rejected, 12 change differs from approved, 13 chain invalid, 14 no verification, 15 override under `--strict`.
- Honest wording everywhere: Java/Maven only; reviewer is self-declared, not authenticated; chain is unsigned and unanchored; a green gate means "approved in exactly this state per an intact record", not "re-verified".
- Evidence for a foreign repo lives in `<repo>/.proofline/` (`project.json`, `acceptance/`, `runs/<run_id>.jsonl`).

## Review Focus

Input classes the spec implies but no single task owns; each has a test in the task named:
1. File names with spaces, unicode or a leading dot inside the change (Task 1 hash; Task 5 verify).
2. A file **deleted** after approval, or an empty change (no edits at all) (Task 1, Task 4).
3. Approve followed by reject, or reject followed by approve, in one run: latest wins (Task 4).
4. A run file whose last line is half-written, or empty (Task 4: chain invalid / no verification, never a crash).
5. Manifest or protected test edited after approval (Task 4 via `inputs_sha256`); `.proofline/` and `.git/` must never count as the change (Task 1).

## File structure

- Create `src/proofline/change.py`: canonical change hash and inputs hash.
- Create `src/proofline/adapters/__init__.py`, `adapters/maven.py`: the Maven adapter and `get_adapter`.
- Modify `src/proofline/runner.py`, `verifier.py`: delegate Maven specifics to the adapter; keep `run_maven`, `classify`, `warm_cache`, `IMAGE` importable from their current modules (tests patch `proofline.verifier.run_maven`).
- Modify `src/proofline/project.py`: `ecosystem` field, `load_external(repo)`, `Project.root`.
- Create `src/proofline/review.py`: decision record, `latest_verification`, `gate`, exit-code constants, `signals`.
- Create `src/proofline/cli.py`; modify `pyproject.toml` (`[project.scripts] proofline = "proofline.cli:main"`).
- Modify `src/proofline/api.py`, `ui/index.html`: decision endpoint, evidence dir selection, review screen.
- Create `examples/inventory/`: a small Maven project with its own `.proofline/` used as the external-project trial.
- Tests: `tests/test_change_hash.py`, `tests/test_adapter.py`, `tests/test_external_project.py`, `tests/test_review.py`, `tests/test_cli.py`, `tests/test_api_review.py`, `tests/test_external_trial.py`.

---

### Task 1: Canonical change hash

**Files:** Create `src/proofline/change.py`, `tests/test_change_hash.py`. Modify `src/proofline/verifier.py` (record the new hashes in `run_start`), `src/proofline/workspace.py` (use the shared ignore list).

**Interfaces:**
- Produces: `IGNORED_DIRS = ("target", ".git", ".proofline")`; `change_hash(base: Path, work: Path) -> str` (sha256 over the sorted list of changed relative paths, each with the sha256 of its content in `work`, or a deletion marker, plus its file mode; symlinks tagged as in `changed_files`); `inputs_hash(manifest: Path, protected_tests: Path) -> str` (sha256 over the manifest bytes and every file under the protected-tests directory, sorted by relative path).
- Consumes: `workspace.changed_files(base, work) -> set[str]`.

- [ ] **Step 1: Write failing tests** in `tests/test_change_hash.py`: `shouldGiveSameHashWhenBaseAndWorkAreCopiedToOtherDirectories`; `shouldChangeHashWhenContentModeOrDeletionDiffers` (three assertions, one per kind); `shouldIgnoreTargetGitAndProoflineDirectories`; `shouldHandleSpacesUnicodeAndDotfilesInNames`; `shouldGiveStableEmptyChangeHash` (equal trees -> same constant); `shouldChangeInputsHashWhenManifestOrProtectedTestChanges`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_change_hash.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `change.py` as specified; make `workspace.changed_files`, `verifier.tree_hash` and `verifier.diff` (`-x` flags) and the `copytree` ignore in `verifier.verify` use `IGNORED_DIRS`. In `verify(...)` add the optional parameters `base_ref: dict | None = None` and `manifest_path: Path | None = None`, and add `change_sha256`, `inputs_sha256` (only when a manifest path is given) and `base_ref` to the `run_start` record, additively.
- [ ] **Step 4: Run** the new tests and `tests/test_verifier.py tests/test_workspace.py tests/test_project.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "Canonical, reproducible change hash and inputs hash"`.

### Task 2: Maven adapter seam

**Files:** Create `src/proofline/adapters/__init__.py`, `adapters/maven.py`, `tests/test_adapter.py`. Modify `runner.py`, `verifier.py`, `project.py`.

**Interfaces:**
- Produces: `class Adapter(Protocol)` with `name: str`, `run_check(workdir: Path, selector: str) -> RunResult`, `classify(r: RunResult) -> str`, `warm(project_root: Path) -> None`; `get_adapter(name: str) -> Adapter` raising `ProjectError` for an unknown name; `MavenAdapter` (name `"java-maven"`). Manifest field `ecosystem: str = "java-maven"` (strict, so an unknown value fails at load).
- `runner.run_maven`, `runner.warm_cache`, `runner.IMAGE`, `verifier.classify` remain importable and behave exactly as before (re-exported).

- [ ] **Step 1: Write failing tests**: `shouldResolveJavaMavenAdapterByDefault`; `shouldRejectUnknownEcosystemWhenLoadingManifest`; `shouldKeepClassifyBehaviourWhenMovedToAdapter` (reuse the cases of the existing `classify` tests via the adapter); `shouldCallAdapterRunCheckForEachCheckInVerify` (patch `proofline.verifier.run_maven` still works).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_adapter.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** by moving code, not rewriting it: Maven command construction, `classify`, the unprivileged-JVM wrapper and `warm_cache` go into `adapters/maven.py`; `verifier.verify` obtains the adapter from the project's `ecosystem` and calls `run_check`; keep the module-level names so existing `patch(...)` targets still work.
- [ ] **Step 4: Run the full suite** (`.venv/bin/python -m pytest -q`). Expected: all existing tests plus the new ones pass; behaviour and evidence unchanged.
- [ ] **Step 5: Commit** `"Move Maven specifics behind an adapter seam (no behaviour change)"`.

### Task 3: External repository mode

**Files:** Modify `src/proofline/project.py`. Create `tests/test_external_project.py`.

**Interfaces:**
- Produces: `load_external(repo: Path) -> Project` reading `<repo>/.proofline/project.json` (same schema as `projects/*.json`, plus no `seeds`), with every path contained in `repo`; `Project.root: Path` (the Proofline repo for bundled projects, `repo` for external); `Project.manifest: Path`. For an external project `fixture` is the repo root, `acceptance` defaults to `.proofline/acceptance`.
- Consumes: the strict checks in `project.load` (no symlinks, unknown keys, absolute/`..` paths, selectors must name an existing test, requirements reference defined checks). Factor them into one private function used by both loaders; bundled behaviour must not change.

- [ ] **Step 1: Write failing tests** (temporary Maven repo built from `fixtures/discount-service`): `shouldLoadExternalProjectWhenManifestIsValid`; `shouldRejectManifestPathEscapingRepo`; `shouldRejectSymlinkInsideExternalRepo`; `shouldRejectUnknownKeysInExternalManifest`; `shouldFailWhenProoflineDirectoryIsMissing`; `shouldLoadBundledProjectsUnchanged`.
- [ ] **Step 2: Run** the new file. Expected: FAIL.
- [ ] **Step 3: Implement** as described; the symlink scan of the repo must skip `IGNORED_DIRS`.
- [ ] **Step 4: Run** `tests/test_external_project.py tests/test_project.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `"Load a project manifest from an external repository"`.

### Task 4: Decision record and gate

**Files:** Create `src/proofline/review.py`, `tests/test_review.py`.

**Interfaces:**
- Produces: exit-code constants (`OK=0, USAGE=2, NOT_APPROVED=10, REJECTED=11, CHANGED=12, CHAIN_INVALID=13, NO_VERIFICATION=14, OVERRIDE_STRICT=15`); `latest_verification(records: list[dict]) -> dict | None` (the last `run_start`..`verdict` group: run_id, verdict, scope, fully_verified, change_sha256, inputs_sha256, base_ref, base_revision); `decide(run_file: Path, decision: Literal["approve","reject"], reason: str, reviewer: str) -> dict` appending a `human_decision` record (fields per spec: decision, reason, reviewer, time, run_id, change_sha256, base_revision, verdict, fully_verified, scope, `override: bool` computed here); raises `ReviewError` for: reject without reason, override without reason, `NEEDS_CLARIFICATION`, no verification, invalid chain; `gate(run_file: Path, repo: Path, strict: bool = False) -> int`; `signals(records) -> list[str]` returning `REJECTED_PASS | APPROVED_WITH_GAPS | APPROVED_FAIL | APPROVED_INCONCLUSIVE` labels.
- Gate order: chain invalid -> 13; no verification -> 14; no decision -> 10; latest decision reject -> 11; recompute `change_hash(base, repo)` against the base named by `base_ref` (git: materialise with `git archive` into a temp dir; dir: the path) and `inputs_hash`, and compare base tree hash with `base_revision`; any mismatch -> 12; override and `strict` -> 15; else 0 (printing a notice for overrides).

- [ ] **Step 1: Write failing tests**, one per exit code: `shouldExit0WhenApprovedAndUnchanged`, `shouldExit10WhenNoDecision`, `shouldExit11WhenRejected`, `shouldExit12WhenFileEditedAfterApproval`, `shouldExit12WhenFileDeletedAfterApproval`, `shouldExit12WhenManifestEditedAfterApproval`, `shouldExit12WhenProtectedTestEditedAfterApproval`, `shouldExit13WhenChainTampered`, `shouldExit13WhenLastLineIsHalfWritten`, `shouldExit14WhenRunHasNoVerification` (also an empty file), `shouldExit15WhenOverrideUnderStrict`, `shouldExit0WhenOverrideWithoutStrictAndPrintNotice`; plus `shouldLetLatestDecisionWin` (approve then reject -> 11; reject then approve -> 0), `shouldRefuseApprovalOfNeedsClarification`, `shouldRequireReasonOnRejectAndOnOverride`, `shouldComputeOverrideFromVerdictNotFromCaller`, `shouldLabelSignals`. Build run files with the real `EvidenceLog` and a small tmp repo; no Docker needed (write `run_start`/`check`/`verdict` records directly).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_review.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `review.py`; reuse `EvidenceLog.resume` to append, `verify_chain` to check, `change.change_hash` / `inputs_hash`.
- [ ] **Step 4: Run** `tests/test_review.py tests/test_change_hash.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `"Human decision record and fail-closed gate with distinct exit codes"`.

### Task 5: The `proofline` CLI

**Files:** Create `src/proofline/cli.py`, `tests/test_cli.py`. Modify `pyproject.toml`.

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int` with subcommands, all taking `--repo PATH` (default `.`):
  `init` (writes `.proofline/project.json` skeleton from the repo's `pom.xml`, a first main source file as `editable`, one check `maven_tests` with selector `!Acceptance*`, `.proofline/acceptance/requirements.json` = `[]`; refuses to overwrite; prints what to complete);
  `warm` (the networked step; delegates to the adapter);
  `verify [--base REV|DIR] [--task TEXT]` (base defaults to `HEAD` when `--repo` is a git work tree with a clean index, otherwise required; creates `.proofline/runs/<run_id>.jsonl` with a `task` record `source: "human"`, then `verify(...)` with `base_ref` and `manifest_path`; prints verdict and scope; exit 0 for PASS/fully verified, otherwise a non-zero code that is not in the gate table);
  `decide approve|reject --run ID [--reason TEXT] [--reviewer NAME]` (default reviewer from `git config user.name`, else the OS user);
  `gate (--run ID | --latest) [--strict]`; `serve [--port 8765]`.
- Registers `proofline = "proofline.cli:main"` under `[project.scripts]`.

- [ ] **Step 1: Write failing tests** with a tmp Maven repo and `proofline.verifier.run_maven` patched to return passing results: `shouldInitSkeletonAndRefuseOverwrite`; `shouldVerifyAndWriteRunFileInsideProoflineDir`; `shouldRecordBaseRefAndHashesInRunStart`; `shouldDecideAndGateOkEndToEnd`; `shouldReturnUsageErrorOnBadArguments` (exit 2); `shouldPickLatestRun`; `shouldNotTreatProoflineDirAsChange` (verify, then write a new run file, gate still 0); `shouldHandleRepoPathWithSpaces`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `cli.py` with `argparse`; thin wrappers over `project.load_external`, `verifier.verify`, `review.decide`, `review.gate`.
- [ ] **Step 4: Run** `tests/test_cli.py -q`, then `pip install -e .` smoke: `.venv/bin/proofline gate --help`. Expected: PASS and help text.
- [ ] **Step 5: Commit** `"proofline CLI: init, warm, verify, decide, gate, serve"`.

### Task 6: Review screen and decision endpoint

**Files:** Modify `src/proofline/api.py`, `ui/index.html`. Create `tests/test_api_review.py`.

**Interfaces:**
- Consumes: `review.decide`, `review.gate`, `review.signals`.
- Produces: `POST /api/runs/{run_id}/decision` with body `{decision, reason, reviewer}` (appends a `human_decision` via `review.decide`; `422` with the `ReviewError` message; never accepts a verdict or an `override` flag from the client); `GET /api/runs/{run_id}` additionally returns `signals` and `gate` (the integer from `review.gate`, informational). The evidence directory and project come from `serve --repo` (module-level, still patchable by tests); default behaviour for the bundled demo is unchanged.
- UI: below the verdict, a decision bar (Approve / Reject, reason field, an override notice such as "approving with 1 of 3 requirements not checked"); requirements with status other than VERIFIED are rendered as gaps first and visually loud; the verdict and the human decision are shown as two separate things; history list shows decisions with signal labels. The UI computes nothing and never changes a verdict.

- [ ] **Step 1: Write failing tests** (FastAPI `TestClient`, evidence dir patched): `shouldAppendDecisionRecord`; `shouldRejectDecisionWithoutReasonOnReject`; `shouldIgnoreClientSuppliedOverrideField`; `shouldReturnSignalsAndGateInRun`; `shouldKeepExistingRunEndpointsUnchanged`; `shouldReturn404ForUnknownRun`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_api_review.py tests/test_api.py -q`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** endpoint and UI. Verify the page in a browser at desktop width: PASS with gaps, FAIL, and an approved run; screenshot each.
- [ ] **Step 4: Run** `tests/test_api_review.py tests/test_api.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `"Review screen: gaps first, separate human decision, decision endpoint"`.

### Task 7: External project trial (the "done" test)

**Files:** Create `examples/inventory/` (a small Maven project, not under `fixtures/`, with `.proofline/project.json`, `.proofline/acceptance/` protected tests and `requirements.json`, deliberately unlike the discount and retry fixtures), `tests/test_external_trial.py`.

- [ ] **Step 1: Write the failing end-to-end test** (real Docker): copy `examples/inventory` to a tmp dir, `git init` and commit it, run `warm`, then `verify` on a correct change -> PASS fully verified; `decide approve` -> `gate` 0; edit a file -> `gate` 12; restore it -> 0; reject -> 11; tamper one byte of the run file -> 13. Name: `shouldRunExternalProjectFromInitToGateWhenUsingRealMaven`. A second test, `shouldFailVerificationWhenChangeIsWrong`, expects FAIL and no approval possible without override (approve needs a reason and is flagged `override`).
- [ ] **Step 2: Run** it. Expected: FAIL (project missing).
- [ ] **Step 3: Write `examples/inventory`** so the protected tests fail on the starting code and pass on the intended change; keep it two source files.
- [ ] **Step 4: Run** `tests/test_external_trial.py -q`. Expected: PASS. Then follow only the README quickstart (Task 8) from a fresh clone in another directory and record the time taken.
- [ ] **Step 5: Commit** `"External project example and end-to-end trial"`.

### Task 8: Docs and the quickstart

**Files:** Modify `README.md`, `evaluations/RESULTS.md` (only if results change; they do not), add `docs/` nothing else.

- [ ] **Step 1:** Add a "Use it on your own Maven project" section: install, `init`, complete the manifest and protected tests, `warm`, `verify`, `serve`, `decide`, `gate` with the exit-code table; and a Limitations update (Java/Maven only today, reviewer self-declared, unsigned chain, gate does not re-verify, same-JVM verdict channel).
- [ ] **Step 2:** Update test counts (`pytest --collect-only -q | tail -1`) in all places the README states them.
- [ ] **Step 3: Verify** every command in the new section by running it from a fresh clone (in a new empty directory, with `python -I` where it reads untrusted files).
- [ ] **Step 4: Commit** `"README: quickstart for external projects and updated limitations"`.

### Task 9 (cut first if time runs short, in this order): retry, re-run one check, compare

**Files:** Modify `src/proofline/cli.py`, `agent_loop.py`, `api.py`; tests in `tests/test_cli.py`.

- [ ] **9a `proofline retry --run ID`:** feeds the last rejection reason plus the real output of failed/uncovered checks to the agent as repair input; counts as one attempt inside `MAX_REPAIRS` and the call budget; refuses unless `MODEL_API_KEY` and `MODEL_CALL_BUDGET_FILE` are set. Tests use the simulated model only: `shouldRetryWithRejectionReasonAsFeedback`, `shouldRefuseRetryWithoutBudgetLedger`, `shouldRecordReasonOnlyWhenAttemptsAreExhausted`.
- [ ] **9b re-run one check:** appends `check_rerun` (real result, labelled partial, no verdict); the decision still judges the last full verification. Test `shouldAppendRerunWithoutChangingVerdictOrScope`.
- [ ] **9c compare:** `GET /api/compare?a=ID&b=ID` lists checks whose status differs between two full verifications. Test `shouldListChangedChecksBetweenTwoRuns`.
- [ ] **Commit each** separately.

---

## Self-review notes

- Spec coverage: canonical hash and inputs hash (T1), adapter seam (T2), external repo + containment (T3), decision record, override, exit codes, gate, signals (T4), `init/warm/verify/serve/gate` and run layout (T5), review screen with gaps first and separate decision (T6), "done means" 1-4 (T7, T8), deferred items (T9). Not built by design: second ecosystem, authentication, signing, hosting, auto-merge, learning.
- `Project.manifest`/`Project.root` are introduced in Task 3 and consumed in Tasks 4-5; hash function names are fixed in Task 1 and reused unchanged.
