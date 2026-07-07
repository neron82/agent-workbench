# Phase 8 Failure Matrix

Status: Phase 8 evidence — based on the test suite and source files in
`/home/neron/projects/agent-workbench`. No browser or manual QA was
performed; rows marked "code" are validated by automated tests in
`tests/`, rows marked "code-only" are read from source contracts, and
rows marked "NOT TESTED" are flows that have no automated coverage in
this tree.

Test baseline: `python3 -m pytest tests/` → **624 collected, 624
passed in 15.34s** (current repository state after Phase 9 hardening).

## Severity legend

| Code | Meaning                                                     |
| ---- | ----------------------------------------------------------- |
| OK   | Passes in source and is covered by at least one passing test |
| OBS  | Observed working in source; only partly covered by tests     |
| GAP  | Real gap in current evidence — see `13_PHASE8_ISSUE_LIST.md` |

## 1. Product persistence (`workbench.db`)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| Schema migration creates 14 canonical tables | All 14 tables present, all CHECK constraints in effect | `tests/test_migrations.py` (5 tests), `tests/test_schema.py` (5 tests) | OK |
| `get_connection` returns a usable, migration-ready connection | Connection open, row factory in place, idempotent migrations | `tests/test_db_connection.py` (7 tests) | OK |
| Each repository round-trips its own row type | Create → get_by_id → list → update for every table | 17 repository test files (see `13_PHASE8_ISSUE_LIST.md` §B for per-file counts) | OK |
| Workspace / tenant isolation | Default workspace unique per tenant; cross-tenant lookups return None | `tests/test_workspace_repo.py` (17 tests) | OK |

## 2. Harness adapter layer (Phase 4 deliverable)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| `BaseAdapter` abstract contract is implemented by all 5 adapters | `discussion`, `hermes`, `opencode`, `shell`, `ssh` each implement start/stop/cancel/get_runtime_ids/get_transcript | 5 adapter files; test coverage: discussion 15, hermes 28, opencode 20, shell 6, ssh 8 | OK |
| `DiscussionAdapter` rejects side-effect ops | `execute_shell`, `write_file`, `replay`, `steer` raise `NotImplementedError` | `src/agent_workbench/adapters/discussion.py:99-120` (source-only check) | OBS |
| `ShellAdapter` SIGTERM/SIGKILL lifecycle | stop sends SIGTERM, cancel sends SIGKILL, status transitions correctly | `tests/test_shell_adapter.py` (6 tests) | OK |
| `OpencodeAdapter` starts one server per HarnessRun (decision 3) | `start()` raises `ConnectionError` when binary missing; otherwise spawns `opencode serve` and stores PID | `src/agent_workbench/adapters/opencode.py:73-78, 95-103`, `tests/test_opencode_adapter.py` + `tests/test_opencode_adapter_live.py` (20 tests) | OK |
| `SshAdapter` best-effort cancel/reap (decision 8) | `reconnect_and_reap()` checks remote PID and kills it; `remote_host:remote_pid` stored in `runtime_remote_process_id` | `src/agent_workbench/adapters/ssh.py:197-230`, `tests/test_ssh_adapter.py` + `tests/test_ssh_adapter_live.py` (10 tests) | OK |
| `HermesAdapter` capability table reflects backend | `can_steer=True`, `can_pause=False`; SSH backend adds `can_remote=True` | `src/agent_workbench/adapters/hermes_adapter.py:36-44, 55-58`, `tests/test_hermes_adapter.py` (28 tests) | OK |
| Live `opencode serve` subprocess behaviour against a real `opencode` binary | Spawn process, wait, stop, cancel | `tests/test_opencode_adapter_live.py` exercises the real local binary via `OpencodeAdapter.start()` + `stop()`. | OK |
| Live SSH run against a real remote host | End-to-end stop/cancel/reap across network | `tests/test_ssh_adapter_live.py` exercises the real local `ssh mbp` path via `SshAdapter.start()`, transcript fetch, and stop. | OK |

## 3. Session, fork, routing, orchestration (Phase 5 deliverable)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| `RoutingService.route_message` validates source/target/kind non-null | `ValueError` on empty fields | `tests/test_routing_service.py` (32 tests) | OK |
| Anti-chatter: worker → worker hop is rejected | `ValueError` on direct worker addressing | `src/agent_workbench/services/routing_service.py:189-198`, tests | OK |
| Default routing: `user → worker` rejected without `explicit_dispatch` | `ValueError` for direct user→worker | `routing_service.py:204-213`, tests | OK |
| `@all` rejects worker source; broadcast only to non-execution participants | `ValueError` if `source_type=='worker'` and `target_type=='all'` | `routing_service.py:174-179`, tests | OK |
| `OrchestratorService.mediate_worker_communication` writes uplink + downlink | Two `RoutedMessage` rows persisted in one transaction | `tests/test_orchestrator_service.py` (14 tests), `orchestrator_service.py:140-164` | OK |
| `OrchestratorService.dispatch_worker` creates a binding and assigns task_spec | Returns `AgentProfileBinding`; sets `task_spec_id` on session | tests | OK |
| `ForkService.create_fork` infers `type_change` vs `branch` and stamps a versioned checkpoint | `fork_kind` selected from parent type; `checkpoint_json` has `version=1` | `src/agent_workbench/services/fork_service.py:182-207, 374-387`, `tests/test_fork_service.py` (33 tests) | OK |
| `ForkService.suggest_fork_if_needed` is conservative | Returns `None` for chat signals; returns suggestion only for explicit research/work keywords | `fork_service.py:263-337`, tests | OK |
| Session type is immutable in place; transitions create a child session | `SessionExtensionRepository` does not expose a type-update; type changes go through fork | `src/agent_workbench/models/session_extension.py` (read), `session_service.py:80-181` | OBS |
| `ProfileService.change_profile` creates a new binding, not a mutation | `created_from='profile_change'` on the new binding; old binding untouched | `profile_service.py:151-165`, `tests/test_profile_service.py` (16 tests) | OK |

## 4. UI workflow and approval (Phase 6 deliverable)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| Channel list, create, view, fork forms render | `/channels`, `/channels/<id>`, fork GET/POST | `tests/test_web_app.py` (19 tests) | OK |
| Session view shows messages, binding, channel, statuses | `/sessions/<id>` renders correctly; 404 on missing | tests | OK |
| Posting a message via web follows `user → orchestrator` path | `route_message` is called with default targeting; empty body flashes an error | `src/agent_workbench/web/sessions.py:107-171`, tests | OK |
| TaskSpec draft / review / approve / reject status transitions | `approval_status` CHECK constraint; repo round-trips all 5 states | `tests/test_task_spec_repo.py` (6 tests), `tests/test_task_spec_ui.py` (17 tests) | OK |
| Run panel renders capability-aware controls | stop / cancel / pause / steer each carry `supported` + `reason`; pause and steer are never routed in MVP | `src/agent_workbench/web/runs.py:198-256`, `tests/test_run_panel_ui.py` (16 tests) | OK |
| Server re-checks adapter capability before honouring POST | A direct POST to `/runs/<id>/stop` against a `discussion` run returns 403 | `runs.py:306-320`, tests | OK |
| Fork UI surfaces summary, decisions, assumptions, open questions, artifacts | `tests/test_fork_ui.py` (11 tests) | OK |
| Live browser render of the run panel / channel / session / fork / task-spec / review pages | Visual confirmation in a real browser | NOT TESTED — no browser tool available in this run; only HTTP/HTML assertions are exercised. | GAP-3 (low) |

## 5. Replay, review, verification (Phase 7 deliverable)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| `ReviewService.summarize_review_state` marks latest verdict | `fail`/`blocked` → `blocking=True`; `pass`/`conditional` → `blocking=False` | `tests/test_review_service.py` (17 tests) | OK |
| `ReviewService.build_review_bundle` aggregates artifacts + runs for a target | Composite dict for `task_spec` / `artifact` / `harness_run` / `session` targets | tests | OK |
| `ReplayService.normalize_checkpoint` produces versioned envelope | `{version, source_session_id, source_message_offset}` | `tests/test_replay_service.py` (35 tests) | OK |
| Replay equivalence uses final-state signals only | Same artifact content hashes + same review verdict ⇒ equivalent, regardless of tool-call order | `src/agent_workbench/services/replay_service.py:262-489`, tests | OK |
| `VerificationService.get_run_verification_surface` returns required keys | `artifacts`, `reviews`, `replays`, `latest_review_verdict`, `replay_equivalence_note`, `verification_ready`, `blockers` | `tests/test_verification_service.py` (23 tests) | OK |
| Verification ready = status ∉ active AND ≥1 review AND all artifacts hashed | `_compute_blockers` covers all three | `src/agent_workbench/services/verification_service.py:382-415` | OK |
| `VerificationService.get_session_verification_surface` aggregates multiple runs | `verification_ready_run_count` matches per-run `verification_ready`; session-level reviews included | tests | OK |
| `VerificationService.explain_blockers` is deterministic | Same surface input → same output | tests | OK |
| `ArtifactVerifier` rejects tampered or unhashed artifacts | Hash mismatch → fail; empty `content_hash` → fail | `tests/test_artifact_verifier.py` (14 tests) | OK |

## 6. Permission model (decision 5 / decision 26)

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| `PermissionModel.request_permission` auto-approves configured scopes | `auto_approve_scopes` set → `decision='approved'` | `tests/test_permission_model.py` (19 tests) | OK |
| Sensitive scopes never auto-approve (decision 26) | `sensitive_scopes` set → `decision='pending'` even if also in `auto_approve_scopes` | `src/agent_workbench/adapters/permission.py:106-113`, tests | OK |
| `escalated_from_auto_approve` is recorded when caller flagged escalation | Persisted as `1`/`0` on the row | `permission.py:69`, tests | OK |
| Server-side `permissions` blueprint surfaces pending requests | `tests/test_web_app.py` exercises 200 on the list route (one assertion) | OK |

## 7. Cross-cutting / integration

| Flow | Expected behaviour | Evidence | Status |
| ---- | ------------------ | -------- | ------ |
| Full flow: workspace → channel → session → message → fork → task_spec → run → review → replay → verification | All layers compose end-to-end and verification becomes ready only when evidence is present | `tests/test_phase8_main_journey.py` (21 tests), `tests/test_phase8_failure_matrix.py` (35 tests) | OK |
| `app.healthz` reflects DB liveness | Returns `ok=1` | `tests/test_web_app.py::TestIndex::test_healthz` | OK |
| `app.create_app(db_path=...)` honors an injected path | Tests pass `tmp_path` db | `tests/test_web_app.py` fixtures | OK |
| Concurrent multi-worker communication through orchestrator | Two workers mediated → two `RoutedMessage` rows; no chatter path | `tests/test_orchestrator_service.py::TestMediateWorkerCommunication` (multiple) | OK |
| Migration framework is idempotent and order-stable | Re-running migrations does not error or duplicate | `tests/test_migrations.py`, `tests/test_db_connection.py` | OK |

## 8. Summary of evidence gaps

The 1 GAP above is real but not blocking. It is tracked in
`13_PHASE8_ISSUE_LIST.md` with severity, owner suggestion, and
disposition. The short version:

- **GAP-3** (low): browser-based visual smoke. UI tests assert on
  HTTP status codes and HTML structure only, not on rendered pixels.

## 9. Counts at a glance

- Source modules: `src/agent_workbench/{adapters,db,migrations,models,services,web}/` — 5 adapters, 14 model tables, 10 service modules, 10 web Python modules, 1 migration.
- Test files: 41 `test_*.py` modules under `tests/`.
- Test cases: **624 collected, 624 passed, 0 failed, 0 skipped, 0 xfailed**.
- Suite runtime: 15.34 s wall-clock on this host.
- TODO / FIXME / XXX markers in `src/agent_workbench/`: **0** (verified via grep).
