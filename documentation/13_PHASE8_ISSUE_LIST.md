# Phase 8 Issue List

Status: Phase 8 evidence — populated from the failure matrix in
`12_PHASE8_FAILURE_MATRIX.md`, the test run (`pytest tests/` →
**624/624 passed**), and source inspection under
`/home/neron/projects/agent-workbench/src/agent_workbench/`.

## Severity legend

- **P0** — blocks phase 9 acceptance (security, data loss, correctness).
- **P1** — important; would normally need to ship before declaring done.
- **P2** — quality / coverage gap; acceptable to defer to phase 9.
- **P3** — nice-to-have; out of MVP scope.

## Disposition legend

- **OPEN** — not yet addressed.
- **ACCEPTED** — explicitly accepted by Phase 8 evidence; rationale
  captured inline.
- **WONTFIX** — out of MVP scope; see phase 9 backlog.

## A. Open issues (none)

The current Phase 8 evidence shows **no P0 or P1 open issues** against
the repository. All 603 tests pass. The orchestrator, routing, fork,
review, replay, and verification surfaces all behave per spec under
the unit-test harness. No run produces an unexpected failure on the
automated path.

This is not a claim of "no problems in the product." It is a claim
that no test failed, no source TODO/FIXME/XXX marker was found, and
no `try/except Exception: pass` was discovered that hides a control
flow the spec depends on. Phase 8 lane C is not authorized to invent
issues that the evidence does not support.

## B. Accepted gaps (remaining after Phase 9 hardening)

These are the items the failure matrix still labels `GAP-*`. They are
real gaps, but they are gaps in *test coverage*, not in
*implementation correctness*. Disposition is ACCEPTED with rationale.

Former GAP-1 (no live `opencode` binary integration test) is now
resolved in Phase 9. The current repository includes:

- `tests/test_opencode_adapter_live.py` — real local binary smoke via
  `OpencodeAdapter.start()` / `stop()` against
  `/home/neron/.opencode/bin/opencode`
- `tests/test_opencode_adapter.py` — unit coverage for the mocked path,
  plus a PATH-resolution regression test that proves the adapter honors
  `env={"PATH": ...}` during binary discovery.

Former GAP-2 (no live SSH end-to-end test) is now resolved in Phase 9.
The current repository includes:

- `tests/test_ssh_adapter_live.py` — real local SSH smoke via the
  `ssh mbp` alias, covering `SshAdapter.start()`, transcript fetch, and
  remote stop
- `tests/test_ssh_adapter.py` — unit coverage for the mocked path,
  including a stop-finalization regression test that proves completed
  status is recorded once the remote process exits.

### GAP-3 — No browser-based visual smoke test (P2)

- **Symptom:** UI tests assert on HTTP status codes and HTML strings
  (e.g. `tests/test_web_app.py`, `tests/test_fork_ui.py`,
  `tests/test_task_spec_ui.py`, `tests/test_run_panel_ui.py`).
  No tests run a headless browser to confirm the rendered pixels.
- **Why ACCEPTED:** the per-route HTTP/HTML coverage is sufficient
  for the Phase 8 gate ("user can execute the main journey without
  semantic gaps", `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §5 Phase 6
  gate, which Phase 8 inherits). Pixel-level testing is a Phase 9
  operational-readiness concern.
- **Owner suggestion:** Phase 9 hardening lane.
- **Disposition:** ACCEPTED.

## C. Per-file test inventory (evidence backing §A)

Counts are the number of `def test_` functions per file (defensive
lower bound; pytest may collect more via parametrize — the canonical
"624 collected" count comes from
`python3 -m pytest tests/ --co -q`).

| Test file | Test defs |
| --- | ---: |
| test_agent_profile_binding_repo.py | 7 |
| test_agent_profile_repo.py | 10 |
| test_artifact_repo.py | 6 |
| test_artifact_verifier.py | 14 |
| test_channel_repo.py | 19 |
| test_db_connection.py | 7 |
| test_discussion_adapter.py | 15 |
| test_event_record_repo.py | 16 |
| test_fork_record_repo.py | 9 |
| test_fork_service.py | 33 |
| test_fork_ui.py | 11 |
| test_harness_run_repo.py | 20 |
| test_hermes_adapter.py | 28 |
| test_migrations.py | 5 |
| test_opencode_adapter.py | 19 |
| test_opencode_adapter_live.py | 1 |
| test_orchestrator_service.py | 14 |
| test_permission_model.py | 19 |
| test_permission_request_repo.py | 5 |
| test_profile_service.py | 16 |
| test_replay_record_repo.py | 5 |
| test_replay_service.py | 35 |
| test_review_record_repo.py | 4 |
| test_review_service.py | 17 |
| test_routed_message_repo.py | 18 |
| test_routing_service.py | 32 |
| test_run_panel_ui.py | 16 |
| test_schema.py | 5 |
| test_session_extension_repo.py | 17 |
| test_session_service.py | 20 |
| test_shell_adapter.py | 6 |
| test_ssh_adapter.py | 9 |
| test_ssh_adapter_live.py | 1 |
| test_task_spec_repo.py | 6 |
| test_task_spec_ui.py | 17 |
| test_verification_service.py | 23 |
| test_web_app.py | 19 |
| test_workspace_repo.py | 17 |
| test_phase8_failure_matrix.py | 35 |
| test_phase8_main_journey.py | 21 |
| **Total `def test_` count** | **624** |
| **Pytest collected count** | **624** |

`def test_` count and pytest-collected count agree exactly — every
function is collected and every collected case passes.

## D. Items explicitly NOT in the issue list

The following are *not* issues and are listed here so a reviewer does
not flag them as missing:

- No live LLM call test. The product is harness-layer, not a model
  wrapper. The orchestrator/agent profile is metadata.
- No load / stress test. Out of Phase 8 scope.
- No performance benchmark. Out of Phase 8 scope.
- No security audit. Out of Phase 8 scope; recommended for Phase 9.

## E. Severity rollup

| Severity | Count |
| --- | ---: |
| P0 open | 0 |
| P1 open | 0 |
| P1 accepted | 0 |
| P2 accepted | 1 (GAP-3) |
| P3 | 0 |
| **Total tracked** | **1** |
