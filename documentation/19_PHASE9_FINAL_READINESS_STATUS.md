# Phase 9 — Final Readiness Status

Status: Phase 9 closed — user accepted K1–K5 on
`2026-07-05T03:43:50+02:00`. Produced from the current repository at
`/home/neron/projects/agent-workbench`.

This is the file the user reads to decide whether to **exit** Phase 9.
For the supporting evidence, see:

- `12_PHASE8_FAILURE_MATRIX.md` — per-flow OK / OBS / GAP table
- `13_PHASE8_ISSUE_LIST.md` — severity, owner, disposition per issue
- `14_PHASE8_READINESS_REPORT.md` — Phase 8 narrative report
- `15_PHASE9_OPERATOR_RUNBOOK.md` — day-to-day operator reference
- `16_PHASE9_INSTALL_AND_RUN.md` — install and launch reference
- `17_PHASE9_DOCUMENTATION_PACK.md` — final doc set index
- `18_PHASE9_RELEASE_CHECKLIST.md` — practical yes/no pre-release gate
- `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §5 Phase 9 — exit condition
  ("user accepts readiness status")

## 1. TL;DR

- **All automated tests pass on repeated clean re-runs.** `python3 -m pytest
  tests/ --tb=short -q` → `624 passed in 15.34s` (0 failed, 0 skipped,
  0 xfailed) on the most recent run. The most recent 4 orchestrator-run
  full-suite checks in this session were all 624/624.
- **No P0 or P1 open issues.** One P2 gap is ACCEPTED (browser smoke).
  It is a *coverage* gap, not a *correctness* gap.
- **No source TODO / FIXME / XXX markers.** Verified by
  `grep -rn -E 'TODO|FIXME|XXX' src/agent_workbench/` returning empty.
- **No silent `except: pass`** swallowing a control flow the spec
  depends on. Verified by
  `grep -rn -E 'except[^:]*:\s*pass' src/agent_workbench/` returning empty.
- **No stale "547-era" test counts** are cited anywhere in `12`–`19`
  outside of the J5-style row in `18_PHASE9_RELEASE_CHECKLIST.md` that
  *describes* the check. Verified by `grep -n '547' 1[2-9]_*.md`.
- **No `/readyz` flakiness reproduced by the orchestrator.**
  `tests/test_phase9_operational_safeguards.py` passes 17/17 in
  isolation and repeated full-suite reruns stayed clean at 624/624.
- **Mechanically ready to ship.**
- **Phase 9 exited on `2026-07-05T03:43:50+02:00`.** The user explicitly
  accepted K1–K5 in chat, so the Phase 9 exit condition is satisfied.

## 2. Current state (numbers from this run)

### 2.1 Test suite

| Metric | Value | Source |
| --- | ---: | --- |
| Collected | 624 | `python3 -m pytest tests/ --co -q \| tail -1` |
| Passed | 624 | `python3 -m pytest tests/ --tb=short -q` (last line) |
| Failed | 0 (on the most recent run) | same |
| Skipped | 0 | same |
| XFailed | 0 | same |
| Wall-clock | 15.34 s | same |
| Test files | 41 | `ls tests/test_*.py \| wc -l` (38 from Phase 8 + `test_phase9_operational_safeguards.py`, `test_opencode_adapter_live.py`, and `test_ssh_adapter_live.py` from Phase 9) |
| Adapters | 5 | `src/agent_workbench/adapters/` (`discussion`, `hermes`, `opencode`, `shell`, `ssh`) |
| Canonical tables | 14 | migration `001_initial_schema.py` |
| Service modules | 10 | `src/agent_workbench/services/` |
| Web Python modules | 10 | `src/agent_workbench/web/` |
| TODO / FIXME / XXX in `src/` | 0 | `grep -rn -E 'TODO\|FIXME\|XXX' src/agent_workbench/` |
| Silent `except: pass` in `src/` | 0 | `grep -rn -E 'except[^:]*:\s*pass' src/agent_workbench/` |

Test file growth from Phase 8 → Phase 9:

- Phase 8 close: 38 files / 603 tests.
- Phase 9 close: 41 files / 624 tests. The +3 files are
  `tests/test_phase9_operational_safeguards.py`,
  `tests/test_opencode_adapter_live.py`, and
  `tests/test_ssh_adapter_live.py`. Together they add 21 collected tests
  covering production-env safeguards plus live opencode and live SSH
  integration paths.

### 2.2 Issue rollup (from `13_PHASE8_ISSUE_LIST.md` §E)

| Severity | Count |
| --- | ---: |
| P0 open | 0 |
| P1 open | 0 |
| P1 accepted | 0 |
| P2 accepted | 1 (GAP-3) |
| P3 | 0 |
| **Total tracked** | **1** |

### 2.3 Accepted gaps (from `13_PHASE8_ISSUE_LIST.md` §B)

| ID | Severity | Title | Disposition |
| --- | --- | --- | --- |
| GAP-3 | P2 | No browser-based visual smoke test | ACCEPTED |

Each of these has a "Why ACCEPTED" paragraph plus an "Owner
suggestion" pointing to the Phase 9 hardening lane. None of them
describes a defect in implementation; each describes a missing test
harness / CI environment.

### 2.4 Honest capability disclosures (from `14_PHASE8_READINESS_REPORT.md` §3, restated in `15_PHASE9_OPERATOR_RUNBOOK.md` §5)

These are **intentional** MVP limits, surfaced as disabled controls
with reason text. They are not gaps to be fixed before ship.

- **Live pause** — disabled on every adapter (decision 25).
- **Universal live steering** — disabled on every adapter except
  `hermes` (which has `can_steer=True`); the `steer` POST is
  intentionally not routed in MVP.
- **Direct chat → worker routing** — requires `explicit_dispatch`; the
  web form does not expose it (decision 6).
- **Worker → worker direct messaging** — rejected at the routing
  layer; workers address the orchestrator or the system bus (decision 6
  + anti-chatter invariant from `07_EVENT_CHANNEL_MODEL.md` §6).

### 2.5 Phase 9 operational safeguards verification

`tests/test_phase9_operational_safeguards.py` covers the new
environment-aware `create_app(...)` behaviour, the production secret
guard, secure cookie defaults, and the `/readyz` readiness probe.

Evidence in this session:

- `python3 -m pytest tests/test_phase9_operational_safeguards.py tests/test_web_app.py -q` → 36/36 pass.
- `python3 -m pytest tests/test_opencode_adapter.py tests/test_opencode_adapter_live.py -q` → 20/20 pass.
- `python3 -m pytest tests/test_ssh_adapter.py tests/test_ssh_adapter_live.py -q` → 10/10 pass.
- `python3 -m pytest tests/ -q` → 624/624 pass on the most recent run.
- `for i in 1 2 3; do python3 -m pytest tests/ -q | tail -1; done` → three consecutive `624 passed` lines.

Operational conclusion: the Phase 9 safeguards are implemented in code,
covered by tests, and did not exhibit reproducible ordering problems in
the orchestrator's verification run.

## 3. User acceptance record (exit gate — completed)

Phase 9's exit condition, per
`10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §5 Phase 9, is **"user accepts
readiness status."** The user has now explicitly accepted readiness
status in chat. The wording below captures the accepted items and the
exit record.

> **K1.** I accept the one ACCEPTED P2 gap:
> browser-based visual smoke (GAP-3) is deferred to
> Phase 9 hardening / follow-up work. The product is otherwise
> testable end-to-end at the unit + scenario + HTTP/HTML level, and the
> opencode and SSH harnesses now both have live host-level coverage.

> **K2.** I accept that MVP exposes `pause` as a disabled control on
> every adapter and `steer` as a disabled control on every adapter
> except `hermes` (decision 25). Live pause and universal live steer
> are not part of MVP.

> **K3.** I accept that no load, stress, or performance benchmark is
> included in MVP. The product has not been characterised under
> production load.

> **K4.** I accept that no external security audit has been
> performed. The permission model (decisions 5, 26) is implemented
> and tested at the unit level, but a third-party review is a
> recommended follow-up.

> **K5.** I accept that the workbench is the product truth layer;
> Hermes, Opencode, shell, SSH, and discussion are runtime adapters
> (decisions 2, 23). The harness backends' own run histories are
> not the product history.

All five were accepted by the user in chat on
`2026-07-05T03:43:50+02:00`. Phase 9 is **done**.

### Exit record

Recorded acceptance source: user message in this session —
"Accept K1-K5 and close phase 9. And also, save this as a reusable skill please."

Accepted items:
- K1 — accepted
- K2 — accepted
- K3 — accepted
- K4 — accepted
- K5 — accepted

## 4. What changed since Phase 8

Phase 9 produced new code, new tests, and new documentation. Source
and test changes happened in lanes A/B; lane C is documentation-only.

### 4.1 Code added (lanes A/B, not by this lane)

- `create_app(...)` is now environment-aware: a `WORKBENCH_ENV` env
  var (and an explicit `environment=` argument) selects `production`
  / `development` / `testing`, with `unknown` raising
  `ValueError`. In `production` mode, secure cookie defaults are
  applied automatically; `development` and `testing` do not
  over-apply them. See `src/agent_workbench/web/app.py` (read during
  this lane) and `tests/test_phase9_operational_safeguards.py`.
- A new `/readyz` readiness probe is registered alongside the
  existing `/healthz` liveness probe. `/readyz` exercises
  `SECRET_KEY` validation, the DB connection, and reports a JSON
  payload with `db_ok` and an HTTP 503 on failure
  (`src/agent_workbench/web/app.py:14, 213, 219-...`).
- 17 new tests cover the production guard, the cookie defaults, and
  the `/readyz` probe behaviour.

### 4.2 Docs added (this lane + lanes A/B)

- `/home/neron/projects/agent-workbench/15_PHASE9_OPERATOR_RUNBOOK.md` (lane A/B)
- `/home/neron/projects/agent-workbench/16_PHASE9_INSTALL_AND_RUN.md` (lane A/B)
- `/home/neron/projects/agent-workbench/17_PHASE9_DOCUMENTATION_PACK.md` (this lane)
- `/home/neron/projects/agent-workbench/18_PHASE9_RELEASE_CHECKLIST.md` (this lane)
- `/home/neron/projects/agent-workbench/19_PHASE9_FINAL_READINESS_STATUS.md` (this lane)

### 4.3 Source and test counts

| Metric | Phase 8 close | Phase 9 close | Delta |
| --- | ---: | ---: | ---: |
| Test files | 38 | 41 | +3 (`test_phase9_operational_safeguards.py`, `test_opencode_adapter_live.py`, `test_ssh_adapter_live.py`) |
| Collected tests | 603 | 624 | +21 |
| Passed | 603 | 624 | +21 |
| Failed | 0 | 0 (current run) | 0 |
| TODO/FIXME/XXX in `src/` | 0 | 0 | 0 |
| Silent `except: pass` in `src/` | 0 | 0 | 0 |

## 5. Recommendation

**Recommendation: clear the Phase 9 gate after the user accepts K1–K5.**

Justification:

1. The automated test suite is 100% green (624/624) on the most
   recent run, and the most recent 4 orchestrator-run full-suite
   checks in this session were all 624/624.
2. The one ACCEPTED gap is a coverage / environment
   gap; every implemented behaviour that the test harness can reach
   behaves per spec. The "Why ACCEPTED" paragraph in
   `13_PHASE8_ISSUE_LIST.md` §B is the contract that the user signs
   on to by accepting K1.
3. The honest-capability rule is enforced in code and in tests — no
   fake pause/steer controls leak into the UI.
4. The release checklist (`18_PHASE9_RELEASE_CHECKLIST.md`) is
   self-contained and tickable in a single sitting; a reviewer can
   re-run it before any future release to confirm no regression.
5. The documentation pack (`17_PHASE9_DOCUMENTATION_PACK.md`)
   indexes the final doc set and tells the next reader where to
   start.
6. The new operational safeguards (env-aware `create_app`,
   `production`-mode secure cookie defaults, `/readyz` probe) are
   real hardening work and are covered by 17 new tests; the
   runbook (`15_PHASE9_OPERATOR_RUNBOOK.md`) and the install doc
   (`16_PHASE9_INSTALL_AND_RUN.md`) are written against the current
   source.

## 6. What is *not* in this file

- The per-flow OK / OBS / GAP table — see
  `12_PHASE8_FAILURE_MATRIX.md`.
- The per-file test inventory — see
  `13_PHASE8_ISSUE_LIST.md` §C.
- The Phase 8 narrative report — see
  `14_PHASE8_READINESS_REPORT.md`.
- Operator day-to-day actions — see
  `15_PHASE9_OPERATOR_RUNBOOK.md`.
- Install and launch — see `16_PHASE9_INSTALL_AND_RUN.md`.
- The release-gate commands — see
  `18_PHASE9_RELEASE_CHECKLIST.md`.
- The Phase 1-2 spec baseline — see `01`–`09` and `open_decisions.md`.

This file is intentionally short. Its job is to be the one page the
user reads to decide **yes / no / not yet**.
