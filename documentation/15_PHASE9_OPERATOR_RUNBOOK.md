# Phase 9 Operator Runbook

Status: Phase 9 evidence — for operators running the Agent Workbench
locally or in a single-host deployment. All commands and paths in this
document are taken from the current state of
`/home/neron/projects/agent-workbench`.

## 1. What this system is

Agent Workbench is a Flask web application that sits on top of a
SQLite product database (`workbench.db`). It coordinates multi-agent
work across channel, session, fork, run, review, replay, and
verification surfaces.

Layout (all paths relative to project root unless stated):

| Layer | Path | Notes |
| --- | --- | --- |
| Flask app factory | `src/agent_workbench/web/app.py` | `create_app(db_path=None)` |
| Web blueprints | `src/agent_workbench/web/{channels,sessions,messages,forks,reviews,permissions,task_specs,runs}.py` | All wired in `app.create_app` (`app.py:103-119`) |
| Templates | `src/agent_workbench/web/templates/*.html` | Jinja; `base.html` drives the global nav |
| DB connection | `src/agent_workbench/db/connection.py` | WAL mode, FK on, 5 s busy timeout |
| Migrations | `src/agent_workbench/db/migrations/001_initial_schema.py` | Creates the 14 canonical tables |
| Verification service | `src/agent_workbench/services/verification_service.py` | Read-only surface used by the run panel |
| Tests | `tests/` | 624 collected, 624 passed (`pytest -q`) |

The web layer never holds long-lived DB connections — each request
opens a connection via `agent_workbench.db.get_connection`, runs
pending migrations, and closes it at teardown
(`app.py:67-93`).

## 2. Startup path

The canonical local launch is:

```bash
cd /home/neron/projects/agent-workbench
python3 -m flask --app src.agent_workbench.web.app:create_app run --debug
```

`create_app` does not start a server itself; it returns a configured
`Flask` object. The factory resolves the DB path in this order
(`app.py:29, 62-64`):

1. The `db_path=` argument to `create_app(...)` (used by tests).
2. `app.config["WORKBENCH_DB_PATH"]` if set by a caller.
3. The default: `workbench.db` next to the project root.

Migrations are applied lazily on the first request by the
`before_request` hook (`app.py:67-83`). No separate `migrate` command
is required.

To run the development server against a different DB file:

```bash
WORKBENCH_DB_PATH=/var/lib/workbench/prod.db \
  python3 -m flask --app src.agent_workbench.web.app:create_app run \
  --host 0.0.0.0 --port 5000
```

`WORKBENCH_DB_PATH` is read by the factory only when no `db_path` is
passed in code; passing the path explicitly via a thin launcher
script is the supported pattern for non-default DB locations.

## 3. Health and readiness checks

### 3.1 Liveness / DB connectivity

The factory registers a built-in liveness probe at `/healthz`
(`app.py:95-100`). It opens the per-request DB connection and runs
`SELECT 1`.

```bash
$ curl -fsS http://127.0.0.1:5000/healthz
ok=1
```

Verified during this run against a fresh app instance — see
`16_PHASE9_INSTALL_AND_RUN.md` §3 for the captured transcript.

A non-`ok=1` response or a 5xx means the DB is unreachable or the
schema is broken. Inspect the server log; the
`_open_db_connection` hook raises if migration fails.

### 3.2 Test-suite gate

The contract gate for a healthy tree is the full test suite:

```bash
$ python3 -m pytest tests/ -q
............................................................ [ 11%]
............................................................ [ 23%]
... (truncated) ...
624 passed in 15.34s
```

Verified during this run on the current commit. A failure here is a
hard stop — do not deploy.

### 3.3 Honest-capability sanity check (UI)

Per the Phase 8 readiness report, the run panel must render `stop`,
`cancel`, `pause`, and `steer` as either routed buttons or disabled
controls with an explicit reason. A bare `curl` of the run detail
page should contain the literal strings `Stop`, `Cancel`, `Pause`,
`Steer` (case-sensitive in the templates). If any are missing, the
template was edited out of the capability surface — treat as a
regression.

## 4. Where to inspect runs, reviews, and replays

All inspection surfaces are HTML routes. They are read-only unless
explicitly noted.

### 4.1 Run detail panel

- Route: `GET /runs/<harness_run_id>` (`runs.py:326-334`)
- Template: `src/agent_workbench/web/templates/run_panel.html`
- Context built in `runs._load_run_context` (`runs.py:140-227`).
- Surfaces:
  - status, objective, bound profile (`perspective`, `function`,
    `harness`), artifacts, events bucketed into warnings/errors
  - capability-aware controls (stop/cancel/pause/steer); `stop` and
    `cancel` are POSTed and re-checked server-side
    (`runs.py:337-396`); `pause`/`steer` are never routed in MVP
  - raw stdout / stderr via `adapter.get_transcript(...)`
  - the **verification surface** from
    `VerificationService.get_run_verification_surface(...)`
    (`runs.py:190-209`) — see §6

The harness run 404s with a useful message if the id is unknown
(`runs.py:331-332`).

### 4.2 Reviews

Two scopes, both owned by `src/agent_workbench/web/reviews.py`:

- Session-level:
  - `GET /sessions/<session_id>/reviews` — list
    (`reviews.py:99-121`)
  - `POST /sessions/<session_id>/reviews` — create
    (`reviews.py:124-177`)
- Run-level:
  - `POST /runs/<harness_run_id>/reviews` — create
    (`reviews.py:180-241`)

Verdicts are constrained to `pass | fail | conditional | blocked`
(`reviews.py:52`). `criteria_eval` must be a JSON object or empty
(`reviews.py:56-68`).

### 4.3 Replays

- `GET /runs/<harness_run_id>/replay` — replay timeline
  (`reviews.py:249-277`)
- `POST /runs/<harness_run_id>/replay` — create a replay record
  (`reviews.py:280-375`)

Outcomes are constrained to `completed | diverged | aborted`
(`reviews.py:54`). The replay view shows the canonical equivalence
note (spec §11) — see §6.2.

### 4.4 Forks

- `GET /sessions/<session_id>/fork` — fork form
  (`forks.py:87-111`)
- `POST /sessions/<session_id>/fork` — create
  (`forks.py:114-193`)
- `GET /forks/<fork_id>` — fork detail (parent, child, inherited
  context, checkpoint) (`forks.py:196-222`)

### 4.5 Direct DB inspection

For deeper triage, open the same SQLite file the app is using:

```bash
sqlite3 /home/neron/projects/agent-workbench/workbench.db
```

Useful one-liners (column names match the schema in
`001_initial_schema.py`):

```sql
-- recent harness runs
SELECT harness_run_id, session_id, harness_type, status, created_at
FROM harness_runs ORDER BY created_at DESC LIMIT 20;

-- runs with their latest review verdict (joined in code by
-- VerificationService._list_reviews_for_run)
SELECT hr.harness_run_id, hr.status, rr.verdict
FROM harness_runs hr
LEFT JOIN review_records rr
  ON rr.target_kind = 'harness_run' AND rr.target_id = hr.harness_run_id
ORDER BY hr.created_at DESC LIMIT 20;

-- artifact rows missing a content_hash (these are verification blockers)
SELECT artifact_id, title, producer_harness_run_id
FROM artifacts WHERE content_hash IS NULL OR content_hash = '';
```

The DB is opened in WAL mode (`PRAGMA journal_mode=WAL` in
`db/connection.py:48`); read-only `sqlite3` sessions are safe to
run alongside the app.

## 5. Accepted limitations from Phase 8

This is the remaining `GAP-*` item from
`13_PHASE8_ISSUE_LIST.md` §B. It is ACCEPTED, not a bug to chase
during routine operation.

| ID | Limitation | Operator impact |
| --- | --- | --- |
| GAP-3 (P2) | No browser-level visual smoke. UI tests assert on HTTP status and HTML strings only. | A pure-CSS regression that breaks the run panel layout will not fail the suite. Mitigation: add a headless browser smoke (Playwright or similar) in Phase 9. |

Live `opencode` binary coverage is no longer a gap: the current tree
includes `tests/test_opencode_adapter_live.py`, and the adapter also
honors `env={"PATH": ...}` when resolving the executable.

Live SSH coverage is no longer a gap: the current tree includes
`tests/test_ssh_adapter_live.py`, which exercises the real local
`ssh mbp` path through `SshAdapter.start()`, transcript fetch, and stop.

Other things to know that are *not* in the issue list but affect
operations (`14_PHASE8_READINESS_REPORT.md` §3):

- `pause` is rendered as a disabled button on every adapter.
- `steer` is rendered as a disabled button on every adapter except
  `hermes` (which has `can_steer=True`).
- Direct `user → worker` routing is not exposed in the web form
  (`explicit_dispatch` is required in `RoutingService.route_message`).
- Worker → worker direct messaging is rejected at the routing
  layer; workers must address the orchestrator or system bus.

## 6. Verification surface — what to read first

When a run is reported as "not verified" or "blocked", read
`runs._load_run_context` in
`src/agent_workbench/web/runs.py:140-227` and
`VerificationService` in
`src/agent_workbench/services/verification_service.py`. The panel
shows the result of
`get_run_verification_surface(harness_run_id)`.

### 6.1 Readiness rule (strict AND)

Per `verification_service.py:382-415` a run is `verification_ready`
iff all three are true:

1. `run.status` is one of `reviewable`, `completed`, `failed`,
   `cancelled` (anything still in flight — `queued`, `starting`,
   `running`, `blocked`, `stopping` — is *not* verifiable).
2. At least one `ReviewRecord` exists that targets the run, one of
   its artifacts, or its task spec
   (`_list_reviews_for_run`, `verification_service.py:338-366`).
3. Every artifact linked to the run has a non-null `content_hash`.

The `blockers` list is produced in a fixed order — status, reviews,
artifact hashes — and is deterministic for identical state
(`VerificationService.explain_blockers`,
`verification_service.py:299-312`).

### 6.2 Replay equivalence note

Every verification surface emits the literal string

> "Replay equivalence means equivalent final state and
> reviewer-judged outcome, not identical tool-call sequence."

defined as `REPLAY_EQUIVALENCE_NOTE` in
`verification_service.py:67-70` and re-exported by `runs.py:54-57`
and `reviews.py:39-42`. This is the spec §11 wording and is shown
verbatim on the run panel and replay view.

### 6.3 Session-level aggregation

`VerificationService.get_session_verification_surface(session_id)`
returns the same shape plus a `runs` list and counts. A session is
`verification_ready` only when every one of its runs is ready and
the union of blocker strings is empty
(`verification_service.py:203-293`).

## 7. Safe operator actions

These are the only operations an operator should run by hand. None
mutate state that the product has not been designed to mutate.

### 7.1 When a run is blocked

`run.status` is still in flight. Action:

1. Open `GET /runs/<harness_run_id>`.
2. Look at `run.status`. If it is one of `queued`, `starting`,
   `running`, `stopping` — the run is *not* yet blocked from a
   verification standpoint; it is just in flight. Do nothing.
3. If `run.status == 'blocked'` (an active stuck state, not the
   "verification blocker" list), look at the events on the page for
   the underlying reason.
4. If the adapter supports `cancel`, the run panel exposes the
   Cancel button. The POST endpoint re-checks the adapter
   capability server-side and returns 403 if not supported
   (`runs.py:369-396`).
5. If `cancel` is not supported (e.g. `discussion`, `hermes`
   adapters), the button is disabled with the reason
   "Cancel is not supported by this harness." Do not bypass this
   by POSTing manually — the server enforces the same check.

### 7.2 When a run is failed

A `failed` run is one of the `VERIFIABLE_RUN_STATUSES`. Verification
can still become ready if the run produced at least one review
(failed runs are reviewable) and every artifact is hashed.

Action:

1. Confirm the failure mode from the events list on the run panel
   (warnings are bucketed separately from errors in
   `runs._extract_warnings_errors`, `runs.py:306-318`).
2. If the failure is operator-induced (wrong tool, wrong input),
   re-issue the work by creating a new harness run via the
   orchestrator flow; do not retry the failed run in place — there
   is no resume path in MVP.
3. Add a review against the run to seed verification. Use
   `POST /runs/<harness_run_id>/reviews` with verdict `pass` (or
   `fail` if the failure is the finding).

### 7.3 When a run is not verification-ready

Read the `blockers` list shown on the run panel. The list is in
fixed order — status, reviews, artifact hashes — and each entry
names the exact gap.

| Blocker shape | Safe action |
| --- | --- |
| `Run status is '...'; verification requires one of ['cancelled', 'completed', 'failed', 'reviewable'].` | Wait for the run to reach a terminal status, or cancel it (if the adapter supports it). |
| `No review record exists for this run; verification requires at least one reviewer judgment.` | `POST /runs/<harness_run_id>/reviews` with a verdict from `pass | fail | conditional | blocked`. |
| `N artifact(s) linked to the run are missing a content_hash and cannot be integrity-verified: <ids>.` | The artifact producer did not write a hash. Re-run the producing pipeline; hashes are written by the producer, not by an operator. |

Never edit `content_hash` or `review_records` by hand — both are
governed by the `ArtifactVerifier` and the
`review_records.verdict` CHECK constraint respectively, and
hand-patching them produces verification surfaces that do not match
the real run.

### 7.4 When a replay looks wrong

`/runs/<id>/replay` shows `completed | diverged | aborted` outcomes.
Replay equivalence is by final state, not by tool-call order
(`REPLAY_EQUIVALENCE_NOTE`). If two replays diverge, the cause is
either a content-hash mismatch on an artifact or a verdict change
on a review — both are visible in the verification surface, not in
the replay rows.

### 7.5 Forbidden actions (do not do these by hand)

- Patch `workbench.db` to flip a status, verdict, or hash. The
  product is the source of truth.
- POST to `/runs/<id>/stop` or `/runs/<id>/cancel` against a
  harness whose `AdapterCapabilities` has `can_stop=False` or
  `can_cancel=False`. The server returns 403; bypassing it would
  call a method the adapter did not implement.
- Treat the test suite as advisory. The gate is 624/624
  (`pytest -q`).
