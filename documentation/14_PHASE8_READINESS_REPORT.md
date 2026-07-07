# Phase 8 Readiness Report

Status: Phase 8 evidence — produced by lane C from current repository
state. Verifiable artefacts in this report (file paths, line counts,
test counts) are taken from
`/home/neron/projects/agent-workbench` as of the run that produced
this file.

## TL;DR

- **All automated tests pass.** `python3 -m pytest tests/` →
  **624 collected, 624 passed, 0 failed, 0 skipped** in 15.34 s.
- **No P0/P1 open issues.** One ACCEPTED gap is tracked in
  `13_PHASE8_ISSUE_LIST.md`; it is P2 (browser smoke only).
- **Recommendation: clear the Phase 8 gate and enter Phase 9.**

## 1. What passed

### Persistence (Phase 3)

- `workbench.db` is created on first connect by
  `src/agent_workbench/db/connection.py` and migrated by
  `src/agent_workbench/db/migration_framework.py`. Migration
  `001_initial_schema.py` creates all 14 canonical tables
  (`workspaces`, `channels`, `session_extensions`, `fork_records`,
  `agent_profiles`, `agent_profile_bindings`, `harness_runs`,
  `task_specs`, `routed_messages`, `event_records`,
  `permission_requests`, `artifacts`, `review_records`,
  `replay_records`).
- All 14 repositories round-trip their row types: 17 repository test
  files, every one green (see failure matrix §1 and issue list §C).

### Harness adapters (Phase 4)

- All 5 adapters (`discussion`, `hermes`, `opencode`, `shell`, `ssh`)
  implement the `BaseAdapter` contract and declare their
  `AdapterCapabilities` honestly (per `08_UI_WORKFLOW.md` §12
  "Honest capability rule").
- `OpencodeAdapter` honours decision 3 (one server per HarnessRun).
- `SshAdapter` honours decision 8 (best-effort cancel/reap with
  remote PID tracking; `reconnect_and_reap()` implemented).
- `DiscussionAdapter` correctly rejects side-effect operations with
  `NotImplementedError` (no process ⇒ no shell, no file write, no
  replay, no steer).

### Session, fork, routing, orchestration (Phase 5)

- `RoutingService` enforces decision 6 (default
  `user → orchestrator → worker`), decision 7 (`@all` is broadcast
  to non-execution discussion participants only), and the
  anti-chatter invariant from `07_EVENT_CHANNEL_MODEL.md` §6.
- `OrchestratorService` mediates worker-to-worker communication by
  writing two `RoutedMessage` legs (uplink + downlink) in a single
  transaction; rejects self-messaging.
- `ForkService.create_fork` infers `fork_kind` (`branch` vs
  `type_change`), stamps a versioned checkpoint, and validates
  parent/type/initiator/summary before any insert.
- `ForkService.suggest_fork_if_needed` is conservative — never
  auto-creates a fork, only returns a suggestion that the caller
  must act on.
- `ProfileService.change_profile` creates a new
  `AgentProfileBinding` with `created_from='profile_change'`,
  never mutates the prior binding (decision 12).

### UI workflow and approval (Phase 6)

- All web blueprints (`channels`, `sessions`, `messages`, `forks`,
  `reviews`, `permissions`, `task_specs`, `runs`) are wired into
  `create_app` (`src/agent_workbench/web/app.py:103-119`).
- Server enforces capability gating for `stop` / `cancel` POST
  endpoints — a tampered client cannot bypass the UI's
  capability-aware controls (`src/agent_workbench/web/runs.py:306-364`).
- `pause` and `steer` are rendered as disabled controls with
  precise reasons when unsupported; their endpoints are
  intentionally not routed in MVP (decision 25).
- Fork UI surfaces all four structured inheritance payloads
  (summary, decisions, assumptions, open questions, artifacts).
- TaskSpec UI exercises draft / ready_for_review / approved /
  rejected / superseded transitions.

### Replay, review, verification (Phase 7)

- `VerificationService.get_run_verification_surface` returns all
  required keys per the Phase 7 contract, including
  `replay_equivalence_note` (the exact UI spec §11 wording).
- Verification readiness is a strict AND of: terminal status,
  ≥1 review, every artifact has `content_hash`. Blockers are
  computed in a fixed order and are deterministic
  (`explain_blockers` is stable for identical input).
- `ReplayService` enforces the `equivalence_rule =
  'final_state_plus_reviewer_judgment'` value (decision 24);
  `pass`/`conditional` verdicts do not override a hash match,
  `fail`/`blocked` verdicts do.
- `ArtifactVerifier` rejects empty and mismatched `content_hash`.

### Permission model (decisions 5, 26)

- `PermissionModel.request_permission` auto-approves configured
  scopes; sensitive scopes are never auto-approved; the
  `escalated_from_auto_approve` flag is persisted.

### Code hygiene

- `grep -rn 'TODO\|FIXME\|XXX' src/agent_workbench/` returns no
  matches.
- No `try/except Exception: pass` that hides a control flow the
  spec depends on (audit performed during evidence collection).

## 2. What remains risky

### Scenario-level end-to-end coverage now exists

Phase 8 added two scenario suites:

- `tests/test_phase8_main_journey.py` — happy-path Chat → Research → Work → Review → Verify journey
- `tests/test_phase8_failure_matrix.py` — failure/retry/re-spec/workspace-isolation scenarios

This closes the earlier scenario-level coverage gap. The remaining accepted gap is visual-smoke coverage, not missing product-flow composition tests.

### Resolved gap — live SSH integration now covered

`tests/test_ssh_adapter_live.py` exercises the real local `ssh mbp`
path through `SshAdapter.start()`, transcript fetch, and remote stop.
In addition, `tests/test_ssh_adapter.py` now includes a stop-finalization
regression test for the mocked path.

### Accepted gap — no browser-level visual smoke (P2, accepted)

UI tests assert on HTTP status and HTML strings. They do not
render pixels. A CSS regression that breaks the run panel layout
would not fail the suite.

### Acceptable residual risk

- Workspace / tenant isolation now has scenario-level validation in
  `tests/test_phase8_failure_matrix.py`, but no hostile multi-process
  concurrency or fuzz testing exists. That remains a Phase 9 hardening
  concern rather than a Phase 8 blocker.
- No load / stress / performance benchmarks. Out of Phase 8
  scope.
- No external security audit. Out of Phase 8 scope; recommended
  for Phase 9.

## 3. Honest capability disclosures (per `08_UI_WORKFLOW.md` §12)

The following capabilities are NOT supported in MVP and are
intentionally not exposed in the UI:

- **Live pause** — `08_UI_WORKFLOW.md` §6, decision 25. The
  `pause` control is rendered as a disabled button with reason
  "Pause not supported by this harness" on every adapter.
- **Universal live steering** — `08_UI_WORKFLOW.md` §7. The
  `steer` control is rendered as a disabled button with reason
  "Steering not supported by this harness" on every adapter
  except `hermes` (which has `can_steer=True`).
- **Direct chat → worker routing** — requires `explicit_dispatch`
  in `RoutingService.route_message`; the web form does not expose
  this option. This is correct per decision 6.
- **Worker → worker direct messaging** — rejected at the routing
  layer; workers must address the orchestrator or system bus.

The run panel renders these as disabled buttons with tooltip
reasons, never as fake universal controls, per the "Honest
capability rule".

## 4. Phase 8 gate checklist (from `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §5)

| Gate criterion | Status |
| --- | --- |
| End-to-end scenario suite | OK — `tests/test_phase8_main_journey.py` + `tests/test_phase8_failure_matrix.py` |
| Failure matrix | **This document's sibling `12_PHASE8_FAILURE_MATRIX.md`** |
| Issue list with severity and owner | **This document's sibling `13_PHASE8_ISSUE_LIST.md`** |
| User-visible readiness report | **This document** |
| Critical flows pass | OK — 624/624 |
| Remaining failures explicitly accepted | OK — 1 ACCEPTED gap, tracked with owner suggestion |

All four deliverables exist. All critical flows are green. All
remaining failures are ACCEPTED with rationale.

## 5. Phase 9 entry recommendation

**Recommendation: clear the Phase 8 gate and proceed to Phase 9.**

Justification:

1. The automated test suite is 100% green (624/624) on the current
   commit.
2. The one ACCEPTED gap is a *coverage / environment* gap, not a *correctness*
   gap — every implemented behaviour that is covered by tests
   behaves per spec.
3. No `try/except` swallows a control flow the spec depends on, and
   no source-code TODO/FIXME/XXX marker exists.
4. The honest-capability rule is enforced in code and in tests —
   no fake pause/steer controls leak into the UI.
5. The remaining ACCEPTED item is an appropriate Phase 9 input and
   should stay in the hardening backlog.

## 6. Test run transcript (verbatim)

```
$ python3 -m pytest tests/ --tb=short
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/neron/projects/agent-workbench
configfile: pyproject.toml
collected 624 items

tests/test_agent_profile_binding_repo.py .......                         [  1%]
...
tests/test_workspace_repo.py .................                           [100%]

============================= 624 passed in 15.34s =============================
```

## 7. Files produced by Phase 8 lane C

- `/home/neron/projects/agent-workbench/12_PHASE8_FAILURE_MATRIX.md`
- `/home/neron/projects/agent-workbench/13_PHASE8_ISSUE_LIST.md`
- `/home/neron/projects/agent-workbench/14_PHASE8_READINESS_REPORT.md`
  (this file)

No source code was modified by lane C. Lane C is read-only with
respect to `src/agent_workbench/` and `tests/`.
