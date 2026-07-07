# Phase 9 — Final Documentation Pack

Status: Phase 9 evidence — index of the final doc set for the Agent
Workbench. This file is itself a deliverable (file `17`); the other two
deliverables in this lane are `18_PHASE9_RELEASE_CHECKLIST.md` and
`19_PHASE9_FINAL_READINESS_STATUS.md`. Lane A and lane B of Phase 9
have already produced their own deliverables (`15_PHASE9_OPERATOR_RUNBOOK.md`
and `16_PHASE9_INSTALL_AND_RUN.md`); this lane indexes all of them.

All counts in this file come from the current repository state at
`/home/neron/projects/agent-workbench`. The single source of truth for
all numbers is:

```
$ python3 -m pytest tests/ --tb=short -q
... 624 passed in 15.34s
```

`624 collected / 624 passed / 0 failed / 0 skipped / 0 xfailed` on the
last clean run. Repeated orchestrator reruns in this session remained
green, so this doc set does not track any current release-blocking
flakiness note for the new `test_phase9_operational_safeguards.py`
suite.

## 1. How the doc set is organised

The Agent Workbench project package lives in
`/home/neron/projects/agent-workbench/`. Files are numbered to keep the
authoring order visible: lower numbers are upstream / planning, higher
numbers are downstream / evidence.

### 1.1 Source-of-truth order (per `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §2)

1. Current user instruction.
2. `open_decisions.md` — 27 resolved product decisions.
3. `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` — phase gates, deliverables, exit conditions.
4. Consolidated spec files (`01_*` through `09_*`).
5. Historical Phase 1/2 worker outputs (Hermes session history; not in the repo).

If a Phase 9 document appears to disagree with one of these, the higher
entry in the list wins.

## 2. Final doc set (file index)

### 2.1 Upstream / planning (do not rewrite in Phase 9)

| # | File | Purpose |
| --- | --- | --- |
| `README.md` | Project package landing page | One-paragraph mission, source-of-truth hierarchy, file index, recommended reading order, summary of adopted decisions. |
| `open_decisions.md` | Resolved product decisions | 27 numbered decisions adopted at planning time. Read first; every other doc is downstream of it. |
| `01_PHASE1_RESEARCH_FOUNDATIONS.md` | Research baseline | Verified research findings and constraints from Phase 1. |
| `02_ARCHITECTURE.md` | Final architecture | Consolidated architecture with decisions applied. |
| `03_DOMAIN_MODEL.md` | Canonical records | IDs, relationships, lifecycle rules for the 14 canonical tables. |
| `04_SESSION_FORKING.md` | Fork contract | Structured forking, inheritance payloads, versioned checkpoints. |
| `05_AGENT_PROFILES.md` | Agent profiles | Dynamic profile model, compatibility rules, `change_profile` semantics. |
| `06_HARNESS_ADAPTERS.md` | Adapter contract | `BaseAdapter` interface, capabilities matrix, SSH reaping contract. |
| `07_EVENT_CHANNEL_MODEL.md` | Event channel model | Routing, addressing, turn-policy, event persistence, anti-chatter invariant. |
| `08_UI_WORKFLOW.md` | UI workflow | Operator-visible Chat/Research/Work flow, approval model, "honest capability rule" (§12). |
| `09_SUBAGENT_TASK_PROFILES.md` | Worker profiles | Standard subagent task profiles for the orchestrator. |
| `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` | Phase contract | Phase 3-9 gates, deliverables, exit conditions; standing rules; blocker policy. |

### 2.2 Phase 3 kickoff (historical evidence)

| # | File | Purpose |
| --- | --- | --- |
| `11_PHASE3_KICKOFF.md` | Phase 3 kickoff | Records the Phase 3 gate opening; do not overwrite with Phase 9 content. |

### 2.3 Phase 8 evidence (input to Phase 9)

| # | File | Purpose |
| --- | --- | --- |
| `12_PHASE8_FAILURE_MATRIX.md` | Failure matrix | Per-flow status (OK / OBS / GAP) backed by file/line/test references. Three GAP rows are real but low-severity. |
| `13_PHASE8_ISSUE_LIST.md` | Issue list | Severity/owner for each Phase 8 finding. Three ACCEPTED P2 gaps, zero P0/P1 open. |
| `14_PHASE8_READINESS_REPORT.md` | Readiness report | Phase 8 TL;DR; recommendation to clear the gate; per-domain "what passed" narrative. |

### 2.4 Phase 9 deliverables (new in this phase)

| # | File | Lane | Purpose |
| --- | --- | --- | --- |
| `15_PHASE9_OPERATOR_RUNBOOK.md` | A/B (operator) | Day-to-day operating reference. Startup command, health/readiness probes, run/review/replay/forks inspection routes, accepted limitations, safe and forbidden operator actions. |
| `16_PHASE9_INSTALL_AND_RUN.md` | A/B (install) | Python version, virtualenv setup, editable install, Flask launch, env vars, DB location, test invocation. |
| `17_PHASE9_DOCUMENTATION_PACK.md` | **C (docs)** | **This file.** Indexes the final doc set and explains what each file is for. Tells the reader where to start. |
| `18_PHASE9_RELEASE_CHECKLIST.md` | **C (release)** | Pre-release yes/no checklist with the exact commands that back each check. |
| `19_PHASE9_FINAL_READINESS_STATUS.md` | **C (status)** | TL;DR for the user; current state, accepted gaps, and the explicit list of items the user must still accept to exit Phase 9. |

## 3. What each Phase 9 file is for

### `15_PHASE9_OPERATOR_RUNBOOK.md` (lane A/B)

- **Audience:** an operator running the workbench locally or in a
  single-host deployment.
- **Job:** startup command, health/readiness endpoints, inspection
  routes, safe and forbidden operator actions. Pairs with
  `16_PHASE9_INSTALL_AND_RUN.md` (install) and this index.
- **Update rule:** refresh when an operator-facing surface (route,
  template, env var, or forbidden action) changes.

### `16_PHASE9_INSTALL_AND_RUN.md` (lane A/B)

- **Audience:** a new operator or CI environment.
- **Job:** Python version, venv creation, editable install, Flask CLI
  launch command, env vars, default DB path, test invocation. The
  "first 10 minutes" doc.
- **Update rule:** refresh when `pyproject.toml` or the app factory
  surface changes.

### `17_PHASE9_DOCUMENTATION_PACK.md` (this file, lane C)

- **Audience:** anyone opening the repo for the first time, or anyone
  asking "where is the canonical doc for X?".
- **Job:** index the final doc set, explain the source-of-truth order,
  and tell the reader which file to read for which question.
- **Does not:** repeat the failure matrix, restate the test counts in
  prose, or list every open issue — those live in `12`/`13`/`14`.
- **Update rule:** refresh this file when the doc set itself changes
  (new file added or retired). Counts and statuses belong in
  `19_PHASE9_FINAL_READINESS_STATUS.md`.

### `18_PHASE9_RELEASE_CHECKLIST.md` (lane C)

- **Audience:** a reviewer running the pre-release gate.
- **Job:** practical yes/no checklist with the exact command that
  justifies each answer. Designed to be ticked through top-to-bottom
  in ~10 minutes.
- **Does not:** explain *why* a check matters — that is the job of
  `14`/`19`. The checklist is the **verifier**, the readiness report
  is the **explanation**.
- **Update rule:** add a row when a new release-blocking category is
  identified. Remove a row when the corresponding gate is retired.

### `19_PHASE9_FINAL_READINESS_STATUS.md` (lane C)

- **Audience:** the user, the orchestrator, and the next worker.
- **Job:** one-page summary of current state — what is green, what is
  accepted, what the user must still explicitly accept to exit
  Phase 9. References the other files for detail.
- **Does not:** duplicate the failure matrix or the issue list.
- **Update rule:** refresh every time the test suite, the issue list,
  or the user-acceptance list changes. The numbered counts in §2 of
  this file are produced from the live `pytest` run.

## 4. Cross-references

- Phase 8 gate deliverables: `12_PHASE8_FAILURE_MATRIX.md`,
  `13_PHASE8_ISSUE_LIST.md`, `14_PHASE8_READINESS_REPORT.md`.
- Phase 9 entry recommendation: `14_PHASE8_READINESS_REPORT.md` §5
  ("Phase 9 entry recommendation").
- Phase 9 exit condition: `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` §5
  Phase 9 ("Exit condition: user accepts readiness status") and §9
  ("Completion policy").
- Operator day-to-day reference: `15_PHASE9_OPERATOR_RUNBOOK.md`.
- Install and launch: `16_PHASE9_INSTALL_AND_RUN.md`.
- The 27 product decisions that bind this doc set:
  `open_decisions.md`.
- The "honest capability rule" that explains why the run panel
  disables `pause` and `steer` on every adapter except `hermes`:
  `08_UI_WORKFLOW.md` §12, restated in
  `14_PHASE8_READINESS_REPORT.md` §3 and again in
  `15_PHASE9_OPERATOR_RUNBOOK.md` §5.

## 5. Recommended reading order (new readers)

1. `README.md` — project package landing page.
2. `open_decisions.md` — 27 product decisions.
3. `02_ARCHITECTURE.md` — architecture overview.
4. `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` — phase gates and exit conditions.
5. `16_PHASE9_INSTALL_AND_RUN.md` — install and launch.
6. `15_PHASE9_OPERATOR_RUNBOOK.md` — operator runbook.
7. `19_PHASE9_FINAL_READINESS_STATUS.md` — current readiness.

For deeper background, read `01` through `09` and the Phase 8
deliverables (`12`–`14`).

## 6. Files produced by Phase 9

- `/home/neron/projects/agent-workbench/15_PHASE9_OPERATOR_RUNBOOK.md` (lane A/B)
- `/home/neron/projects/agent-workbench/16_PHASE9_INSTALL_AND_RUN.md` (lane A/B)
- `/home/neron/projects/agent-workbench/17_PHASE9_DOCUMENTATION_PACK.md` (this file, lane C)
- `/home/neron/projects/agent-workbench/18_PHASE9_RELEASE_CHECKLIST.md` (lane C)
- `/home/neron/projects/agent-workbench/19_PHASE9_FINAL_READINESS_STATUS.md` (lane C)

No source code in `src/agent_workbench/` and no test file in `tests/`
was modified by Phase 9 lane C. Lane C is documentation-only.
