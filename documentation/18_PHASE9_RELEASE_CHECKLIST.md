# Phase 9 — Pre-Release Checklist

Status: Phase 9 evidence — practical pre-release gate. Every row has a
**yes/no** answer and the exact command that justifies it. Run top to
bottom in `/home/neron/projects/agent-workbench`.

All counts in this file come from running the cited commands against
the current repository. The full test run on this commit returns:

```
$ python3 -m pytest tests/ --tb=short -q
... 624 passed in 15.34s
```

> "Current" = the state of the working tree when this checklist was
> produced. Re-run all commands before any new release decision; do
> not rely on the numbers frozen above.

## How to use this checklist

1. Copy the table to a new file or your tracker.
2. Tick each row. **Yes** = the cited command returned the expected
   value on a clean run. **No** = fix the underlying issue before
   shipping; do not tick a row to make the table look green.
3. Every row that ends with "(blocker)" is a release blocker.
4. The "user must explicitly accept" rows are not blockers for ticking
   the box, but they are blockers for declaring **exit** — see
   `19_PHASE9_FINAL_READINESS_STATUS.md` §3.

---

## A. Test suite (blocker)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| A1 | Full test run is green | `python3 -m pytest tests/ --tb=short -q` (workdir: repo root) | exit 0, `624 passed`, 0 failed, 0 skipped, 0 xfailed | ☐ Yes / ☐ No |
| A2 | Test count matches the live collection | `python3 -m pytest tests/ --co -q \| tail -1` | last line shows `624 tests collected` | ☐ Yes / ☐ No |
| A3 | No test was silently skipped / xfailed | `python3 -m pytest tests/ -v 2>&1 \| grep -E 'SKIPPED\|XFAIL'` | empty output | ☐ Yes / ☐ No |
| A4 | Phase 8 scenario suites still exist and pass | `python3 -m pytest tests/test_phase8_main_journey.py tests/test_phase8_failure_matrix.py -q` | both files report all-passed | ☐ Yes / ☐ No |
| A5 | Phase 9 operational safeguards pass in isolation | `python3 -m pytest tests/test_phase9_operational_safeguards.py -q` | `17 passed` | ☐ Yes / ☐ No |
| A6 | Full suite is stable across multiple runs | `for i in 1 2 3; do python3 -m pytest tests/ -q \| tail -1; done` | every line says `624 passed` | ☐ Yes / ☐ No |

## B. Issue hygiene (blocker)

| # | Check | Command / Source | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| B1 | No P0 open | `13_PHASE8_ISSUE_LIST.md` §A | §A reports "no P0 open issues" | ☐ Yes / ☐ No |
| B2 | No P1 open | `13_PHASE8_ISSUE_LIST.md` §A | §A reports "no P1 open issues" | ☐ Yes / ☐ No |
| B3 | Every accepted gap has owner + rationale | `13_PHASE8_ISSUE_LIST.md` §B | each GAP-* row has Owner suggestion + Disposition: ACCEPTED | ☐ Yes / ☐ No |
| B4 | Severity rollup matches current state | `13_PHASE8_ISSUE_LIST.md` §E | P2 accepted = 1 (GAP-3), all other counts = 0 | ☐ Yes / ☐ No |

## C. Source hygiene (blocker)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| C1 | No TODO / FIXME / XXX markers in source | `grep -rn -E 'TODO\|FIXME\|XXX' src/agent_workbench/` | empty output | ☐ Yes / ☐ No |
| C2 | No silent `except: pass` swallowing a control flow the spec depends on | `grep -rn -E 'except[^:]*:\s*pass' src/agent_workbench/` | empty output (or each match justified in writing) | ☐ Yes / ☐ No |
| C3 | Migrations are idempotent and order-stable | `python3 -m pytest tests/test_migrations.py tests/test_db_connection.py -q` | all-passed | ☐ Yes / ☐ No |
| C4 | All 14 canonical tables present after migration | `python3 -m pytest tests/test_schema.py -q` | all-passed (5 tests, one per table group / constraint) | ☐ Yes / ☐ No |

## D. Adapter contract (blocker)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| D1 | All 5 adapters implement `BaseAdapter` | `python3 -m pytest tests/test_discussion_adapter.py tests/test_hermes_adapter.py tests/test_opencode_adapter.py tests/test_opencode_adapter_live.py tests/test_shell_adapter.py tests/test_ssh_adapter.py tests/test_ssh_adapter_live.py -q` | all-passed | ☐ Yes / ☐ No |
| D2 | `DiscussionAdapter` rejects side-effect ops | `src/agent_workbench/adapters/discussion.py:99-120` (read) + `tests/test_discussion_adapter.py` | `execute_shell` / `write_file` / `replay` / `steer` raise `NotImplementedError` | ☐ Yes / ☐ No |
| D3 | `OpencodeAdapter` enforces one-server-per-HarnessRun (decision 3) and works against the live local binary | `src/agent_workbench/adapters/opencode.py` (read) + `python3 -m pytest tests/test_opencode_adapter.py tests/test_opencode_adapter_live.py -q` | mocked path and real-binary path both pass; live test starts and stops one `opencode serve` process per HarnessRun | ☐ Yes / ☐ No |
| D4 | `SshAdapter` exposes `reconnect_and_reap` (decision 8) and works against the live local SSH path | `src/agent_workbench/adapters/ssh.py:197-230` (read) + `python3 -m pytest tests/test_ssh_adapter.py tests/test_ssh_adapter_live.py -q` | mocked path and live `ssh mbp` path both pass; remote PID captured and stop path finalizes cleanly | ☐ Yes / ☐ No |
| D5 | Honest capability rule for `pause` and `steer` (decision 25) | `src/agent_workbench/web/runs.py:198-256` (read) + `tests/test_run_panel_ui.py` (16 tests) | pause disabled on every adapter; steer disabled except `hermes` (`can_steer=True`); `pause` and `steer` POSTs are intentionally not routed in MVP | ☐ Yes / ☐ No |
| D6 | Server re-checks capability before honouring POST | `src/agent_workbench/web/runs.py:306-364` (read) + `tests/test_run_panel_ui.py` | a tampered `POST /runs/<id>/stop` against a `discussion` run returns 403 | ☐ Yes / ☐ No |

## E. Routing and orchestration (blocker)

| # | Check | Command / Source | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| E1 | `RoutingService.route_message` validates non-null fields | `tests/test_routing_service.py` (32 tests) | all-passed; `ValueError` on empty source/target/kind | ☐ Yes / ☐ No |
| E2 | Anti-chatter: worker→worker hop rejected | `src/agent_workbench/services/routing_service.py:189-198` + tests | `ValueError` on direct worker addressing | ☐ Yes / ☐ No |
| E3 | Default `user → worker` rejected without `explicit_dispatch` (decision 6) | `src/agent_workbench/services/routing_service.py:204-213` + tests | `ValueError` without `explicit_dispatch=True` | ☐ Yes / ☐ No |
| E4 | `@all` is broadcast-only and never triggers execution (decision 7) | `src/agent_workbench/services/routing_service.py:174-179` + tests | `ValueError` if `source_type=='worker'` and `target_type=='all'` | ☐ Yes / ☐ No |
| E5 | Worker communication is mediated by orchestrator | `tests/test_orchestrator_service.py` (14 tests) | two `RoutedMessage` legs persisted per mediate call | ☐ Yes / ☐ No |

## F. UI workflow (blocker)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| F1 | All web blueprints are wired into `create_app` | `src/agent_workbench/web/app.py:103-119` (read) + `python3 -m pytest tests/test_web_app.py -q` | all-passed | ☐ Yes / ☐ No |
| F2 | TaskSpec draft → review → approve / reject transitions work | `python3 -m pytest tests/test_task_spec_repo.py tests/test_task_spec_ui.py -q` | all-passed | ☐ Yes / ☐ No |
| F3 | Fork UI surfaces summary / decisions / assumptions / open questions / artifacts | `python3 -m pytest tests/test_fork_ui.py -q` | all-passed (11 tests) | ☐ Yes / ☐ No |
| F4 | Server re-checks capability for run POSTs (capability-aware server) | see D6 | already covered | ☐ Yes / ☐ No |
| F5 | Liveness and readiness probes respond as documented | `python3 -m pytest tests/test_phase9_operational_safeguards.py::TestReadyz -q` | all-passed | ☐ Yes / ☐ No |

## G. Replay, review, verification (blocker)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| G1 | Verification surface returns all required keys | `python3 -m pytest tests/test_verification_service.py -q` | all-passed (23 tests) | ☐ Yes / ☐ No |
| G2 | `ArtifactVerifier` rejects tampered or unhashed artifacts | `python3 -m pytest tests/test_artifact_verifier.py -q` | all-passed (14 tests) | ☐ Yes / ☐ No |
| G3 | Replay equivalence uses final-state signals (decision 24) | `src/agent_workbench/services/replay_service.py:262-489` (read) + `python3 -m pytest tests/test_replay_service.py -q` | all-passed (35 tests) | ☐ Yes / ☐ No |
| G4 | `explain_blockers` is deterministic | `python3 -m pytest tests/test_verification_service.py -q` (subset) | same surface input → same output (covered by 23 tests) | ☐ Yes / ☐ No |

## H. Permissions (decision 5 / decision 26)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| H1 | `PermissionModel` auto-approves configured scopes, never sensitive ones | `python3 -m pytest tests/test_permission_model.py -q` | all-passed (19 tests) | ☐ Yes / ☐ No |
| H2 | `escalated_from_auto_approve` flag is persisted | `src/agent_workbench/adapters/permission.py:69` (read) + tests | row stores `1`/`0` per the persisted value | ☐ Yes / ☐ No |
| H3 | Server-side `/permissions` blueprint lists pending requests | `python3 -m pytest tests/test_web_app.py -q` (one assertion) | covered in `test_web_app.py` | ☐ Yes / ☐ No |

## I. Tenant isolation (decision 22)

| # | Check | Command | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| I1 | Default workspace unique per tenant; cross-tenant lookups return `None` | `python3 -m pytest tests/test_workspace_repo.py -q` | all-passed (17 tests) | ☐ Yes / ☐ No |
| I2 | Workspace / tenant validation in failure-matrix scenarios | `python3 -m pytest tests/test_phase8_failure_matrix.py -q` | all-passed (35 tests) | ☐ Yes / ☐ No |

## J. Documentation (blocker for declaring **shipped**, not for declaring **green**)

| # | Check | Source | Pass criterion | Result |
| --- | --- | --- | --- | --- |
| J1 | `17_PHASE9_DOCUMENTATION_PACK.md` exists and indexes the final doc set (including `15` and `16`) | this file's sibling | present; §2.4 lists files 15, 16, 17, 18, 19 | ☐ Yes / ☐ No |
| J2 | `18_PHASE9_RELEASE_CHECKLIST.md` exists with yes/no checks | this file | present, every row has a command and a pass criterion | ☐ Yes / ☐ No |
| J3 | `19_PHASE9_FINAL_READINESS_STATUS.md` exists with the user-acceptance list | this file's sibling | present, §3 names the items the user must still accept | ☐ Yes / ☐ No |
| J4 | `15_PHASE9_OPERATOR_RUNBOOK.md` and `16_PHASE9_INSTALL_AND_RUN.md` exist | repo | both files present and reference current source paths | ☐ Yes / ☐ No |
| J5 | No stale "547-era" test counts are cited in any `12`–`19` file | `grep -n '547' 1[2-9]_*.md` | the only matches are inside the J4-style row that *describes* the check, not the data | ☐ Yes / ☐ No |

## K. User acceptance (gate to **exit**, not to **ship**)

These rows are not blockers for ticking "green"; they are the items the
user must explicitly accept before Phase 9 closes. See
`19_PHASE9_FINAL_READINESS_STATUS.md` §3 for the wording to capture in
the exit record.

| # | Item | Result |
| --- | --- | --- |
| K1 | User accepts the 1 ACCEPTED P2 gap (browser smoke) | ☐ Accepted / ☐ Not accepted |
| K2 | User accepts the "honest capability rule" consequence: `pause` is not exposed in MVP, `steer` is exposed only for `hermes` | ☐ Accepted / ☐ Not accepted |
| K3 | User accepts that there is no load / stress / performance benchmark in MVP | ☐ Accepted / ☐ Not accepted |
| K4 | User accepts that no external security audit has been performed | ☐ Accepted / ☐ Not accepted |
| K5 | User accepts that the workbench is the product truth layer, and harness backends (Hermes / Opencode / shell / SSH / discussion) are adapters, not the source of truth (decisions 2, 23) | ☐ Accepted / ☐ Not accepted |


## L. Rollup

- **A1–A6 + B1–B4 + C1–C4 + D1–D6 + E1–E5 + F1–F5 + G1–G4 + H1–H3 + I1–I2 + J1–J5 all green** → repository is mechanically ready to ship.
- **K1–K5 all explicitly accepted** → Phase 9 can **exit**; record the acceptance in `19_PHASE9_FINAL_READINESS_STATUS.md` §3.
- **Any blocker row No** → do not ship. File a new row in
  `13_PHASE8_ISSUE_LIST.md` (or a successor) before re-running this
  checklist.

## M. Re-run policy

Re-run this entire checklist whenever any of the following change:

- The source under `src/agent_workbench/`.
- The tests under `tests/`.
- A `open_decisions.md` entry.
- A row in `13_PHASE8_ISSUE_LIST.md` (severity or disposition).
- The Phase 9 doc set (added or removed a file).
- The `WORKBENCH_ENV` / `WORKBENCH_SECRET_KEY` env-var surface (drives
  F5 / A5 / A6).

Run the full suite at least twice in a row before shipping.
