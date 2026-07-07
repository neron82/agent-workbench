# Phase 9 Install and Run

Status: Phase 9 evidence — covers Python setup, local Flask launch,
env vars, DB location, and test invocation, all from the current
state of `/home/neron/projects/agent-workbench`.

The package metadata is in `pyproject.toml` at the project root. It
declares:

- `name = "agent-workbench"`
- `version = "0.1.0"`
- `requires-python = ">=3.11"`
- `dependencies = ["Flask>=3.0"]`
- `optional-dependencies.test = ["pytest>=8.0"]`
- `tool.pytest.ini_options.testpaths = ["tests"]`
- `tool.pytest.ini_options.pythonpath = ["src"]`

Everything below is verified against the current commit.

## 1. Python and package setup

### 1.1 Python version

`requires-python = ">=3.11"`. The current host runs CPython 3.12.3
(confirmed via the `pytest` banner in §4). Any 3.11+ interpreter
will work.

### 1.2 Create a virtualenv and install (editable)

The repo is laid out as a `src/`-layout package
(`[tool.setuptools.packages.find] where = ["src"]`), so an editable
install is the cleanest path for local development:

```bash
cd /home/neron/projects/agent-workbench
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

`pip install -e .` is enough if you do not want the test
extras — Flask is the only runtime dependency. The test extras
pull in `pytest>=8.0`.

### 1.3 Layout after install

```
agent-workbench/
├── pyproject.toml
├── src/agent_workbench/   # package source (importable as `agent_workbench`)
├── tests/                 # pytest testpaths
└── workbench.db           # created on first request, see §3
```

`pyproject.toml` already sets `pythonpath = ["src"]` for pytest, so
tests can import the package without an editable install. Run
`python3 -m pytest tests/` from the project root with no venv
activation and it still works (verified — see §4).

## 2. Launching the Flask app for local use

The app factory is `agent_workbench.web.app.create_app` and is
exported at module level. The supported local launch uses the
Flask CLI:

```bash
cd /home/neron/projects/agent-workbench
python3 -m flask --app src.agent_workbench.web.app:create_app run --debug
```

By default Flask serves on `http://127.0.0.1:5000/`. To bind a
different host/port:

```bash
python3 -m flask --app src.agent_workbench.web.app:create_app run \
    --host 0.0.0.0 --port 5000
```

The factory does not start a server itself — it returns a
configured `Flask` object and lets the CLI bind. The factory:

- registers 8 blueprints (`channels`, `sessions`, `messages`,
  `forks`, `reviews`, `permissions`, `task_specs`, `runs`) — see
  `src/agent_workbench/web/app.py:103-119`;
- wires a per-request DB connection that runs pending migrations
  on first use (`app.py:67-83`);
- registers `/healthz` as a liveness probe that also exercises the
  DB (`app.py:95-100`).

If you prefer not to use the Flask CLI, the same factory works
under any WSGI runner:

```python
# launcher.py
from agent_workbench.web.app import create_app

app = create_app()  # uses default workbench.db at project root

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
```

## 3. Environment variables

| Var | Default | Used by | Effect |
| --- | --- | --- | --- |
| `WORKBENCH_SECRET_KEY` | `"workbench-dev-secret"` | `app.create_app` (`app.py:58`) | Flask `SECRET_KEY`, required for `flash()` to use the session-backed message queue. Set this in any non-dev deploy. |
| `FLASK_RUN_HOST` | `127.0.0.1` | Flask CLI | Bind address for the dev server. |
| `FLASK_RUN_PORT` | `5000` | Flask CLI | Bind port for the dev server. |
| `FLASK_DEBUG` | unset | Flask CLI | `--debug` is a CLI flag, not an env var, but `FLASK_DEBUG=1` enables the debugger the same way. |

The factory exposes two more knobs via the function signature and
app config (`app.py:62-65`); they are normally set by code rather
than env:

- `create_app(db_path=...)` — explicit DB path; takes precedence
  over the default. Used by tests with `tmp_path`.
- `app.config["WORKBENCH_DB_PATH"]` — read by the
  `before_request` hook when no `db_path` was passed to
  `create_app`. Set this in a custom launcher to point at a
  non-default DB without editing code.

## 4. Database — location and defaults

The default DB path is `workbench.db` at the project root. The
constant is derived in two places:

- `src/agent_workbench/db/connection.py:16` —
  `Path(__file__).resolve().parents[3] / "workbench.db"`. The file
  lives at `src/agent_workbench/db/connection.py`, so
  `parents[3]` is the project root.
- `src/agent_workbench/web/app.py:29` — same calculation,
  re-derived for the web layer.

Per-request connection settings
(`db/connection.py:47-51`):

- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=5000` (5 seconds, configurable via
  `get_connection(busy_timeout_ms=...)`)
- `PRAGMA foreign_keys=ON`
- `row_factory = sqlite3.Row`

Migrations are applied automatically by the
`before_request` hook on the first request of each process
(`app.py:81-82`), via
`agent_workbench.db.apply_migrations(...)`. The framework
discovers any module under `src/agent_workbench/db/migrations/`
whose filename starts with a digit and calls its `up(conn)`
function. Applied names are recorded in a `_migrations` table, so
re-running is idempotent. The current tree ships exactly one
migration: `001_initial_schema.py` (creates the 14 canonical
tables).

### 4.1 Verifying the default location

```bash
$ ls -la /home/neron/projects/agent-workbench/workbench.db
# (file does not exist until the first request)

$ cd /home/neron/projects/agent-workbench
$ python3 -c "
import sys; sys.path.insert(0, 'src')
import tempfile, os
from agent_workbench.web.app import create_app
with tempfile.TemporaryDirectory() as td:
    app = create_app(db_path=os.path.join(td, 'demo.db'))
    with app.test_client() as c:
        print(c.get('/healthz').get_data(as_text=True))
"
ok=1
```

Running the above in a tempdir proves the factory creates the DB
on first use and `/healthz` returns `ok=1` (the `SELECT 1` from
`app.py:99-100`).

## 5. Running the test suite

The contract gate is the full pytest run:

```bash
cd /home/neron/projects/agent-workbench
python3 -m pytest tests/
```

or, for a quieter progress view:

```bash
python3 -m pytest tests/ -q
```

### 5.1 Verified transcript on the current commit

```
$ python3 -m pytest tests/ -q
........................................................................ [ 11%]
........................................................................ [ 23%]
........................................................................ [ 35%]
........................................................................ [ 47%]
........................................................................ [ 59%]
........................................................................ [ 71%]
........................................................................ [ 83%]
........................................................................ [ 95%]
...........................                                              [100%]
624 passed in 15.34s
```

624 collected, 624 passed, 0 failed, 0 skipped. The test
configuration in `pyproject.toml` (`testpaths = ["tests"]`,
`pythonpath = ["src"]`) means no extra flags are required.

### 5.2 Targeted runs

Useful filters when iterating:

```bash
# one file
python3 -m pytest tests/test_verification_service.py -q

# one test by node id
python3 -m pytest tests/test_web_app.py::TestIndex::test_healthz -q

# collect-only, to enumerate ids without running
python3 -m pytest tests/ --co -q

# stop on first failure with short tracebacks
python3 -m pytest tests/ -x --tb=short
```

### 5.3 Lint-style hygiene

The Phase 8 readiness report (`14_PHASE8_READINESS_REPORT.md` §1)
asserts no `TODO | FIXME | XXX` markers in the source tree. You can
re-verify locally:

```bash
grep -rn 'TODO\|FIXME\|XXX' src/agent_workbench/
# (no output = pass)
```

## 6. End-to-end smoke after install

After `pip install -e ".[test]"` and a clean `workbench.db`, a
60-second smoke:

```bash
# 1. start the dev server
python3 -m flask --app src.agent_workbench.web.app:create_app run --debug &

# 2. wait for it to bind, then hit /healthz
until curl -fsS http://127.0.0.1:5000/healthz >/dev/null; do sleep 0.2; done
curl -fsS http://127.0.0.1:5000/healthz
# -> ok=1

# 3. confirm the channel list route renders
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/channels
# -> 200

# 4. run the contract gate
python3 -m pytest tests/ -q
# -> 624 passed in ~15s
```

If steps 2 or 3 fail, see the `15_PHASE9_OPERATOR_RUNBOOK.md`
§3 (health and readiness) and §7 (safe operator actions) for
diagnosis. The 8 routes registered by the factory are listed in
`15_PHASE9_OPERATOR_RUNBOOK.md` §1.
