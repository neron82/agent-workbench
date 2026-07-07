# Final Orchestrator Contract for Implementation Phases 3-9

Status: Governing contract for all implementation work after planning/research/spec phases.

## 1. Mission
Implement the Agent Workbench across Phases 3-9 without scope drift, hidden assumptions, or fake capability promises.

## 2. Source-of-truth order
1. user instruction in current session
2. `open_decisions.md`
3. this contract
4. consolidated spec files in this folder
5. historical Phase 1/2 worker outputs

## 3. Standing rules
- no phase may start without explicit user approval
- no scope drift across phases
- every phase must end with verifiable artifacts
- every worker assignment must include acceptance criteria
- every major side-effect path must be tested against real outputs before phase acceptance
- unsupported runtime capability must be surfaced honestly

## 4. Required orchestrator output shape
For every phase interaction, the orchestrator must report:
1. Current objective
2. Active phase / assigned worker
3. Deliverables and acceptance criteria
4. Verification result
5. Next step or blocker

## 5. Phase gates

### Phase 3 — Product persistence and contract layer
Objective:
- create the product truth layer around `workbench.db`
- implement canonical records and storage contracts

Allowed:
- schema work
- migrations
- persistence contracts
- API/domain scaffolding
- tests for canonical records

Forbidden:
- full UI build
- runtime claims beyond what the persistence layer proves

Required deliverables:
- `workbench.db` schema and migration plan
- canonical record implementations
- persistence tests
- documentation updates

Gate to Phase 4:
- schema stable enough for adapter integration
- IDs/lifecycles/routing records verified

### Phase 4 — Harness adapter layer
Objective:
- implement discussion / Hermes / Opencode / shell / SSH adapter contracts
- implement permission hooks and capability declarations

Allowed:
- runtime adapter code
- capability declaration logic
- permission request plumbing
- SSH process identity / reap support

Forbidden:
- UI that promises controls not yet wired end-to-end

Required deliverables:
- adapter interfaces
- runtime-specific implementations
- permission/request flow baseline
- adapter tests and harness capability matrix

Gate to Phase 5:
- adapters expose control/capability state consistently
- SSH best-effort cancel/reap verified
- Opencode per-HarnessRun lifecycle verified or honestly limited

### Phase 5 — Session, fork, routing, orchestration core
Objective:
- implement the product control plane

Allowed:
- structured forking
- routing metadata persistence
- orchestrator dispatch rules
- event persistence
- AgentProfile binding logic

Forbidden:
- uncontrolled worker-to-worker direct messaging

Required deliverables:
- fork contract implementation
- routing/event persistence
- default orchestrator-mediated dispatch
- direct `@agent` and `@all` behavior per decisions
- tests for anti-chatter invariant

Gate to Phase 6:
- Chat/Research/Work core semantics work without UI embellishment
- routing and session transitions verified end-to-end

### Phase 6 — UI workflow and approval surfaces
Objective:
- implement operator-visible workflow for Chat / Research / Work

Allowed:
- review/approval screens
- run panel
- capability-aware control surfaces
- fork UX
- decomposition approval UX

Forbidden:
- fake pause/steer/cancel controls

Required deliverables:
- capability-aware UI workflow
- TaskSpec approval gate
- limited gateway fork support policy reflected in UI
- review/replay visibility

Gate to Phase 7:
- user can execute the main journey without semantic gaps
- control surfaces match real harness capability

### Phase 7 — Replay, review, and verification hardening
Objective:
- make review/replay trustworthy enough for real use

Allowed:
- replay checkpoint logic
- review records and equivalence checks
- artifact integrity checks
- cross-harness review surfaces

Required deliverables:
- standardized checkpoint handling
- review workflow implementation
- replay equivalence policy implementation
- verification tests

Gate to Phase 8:
- replay/review semantics are implemented and testable

### Phase 8 — End-to-end integration and scenario validation
Objective:
- validate the product as a system

Allowed:
- multi-phase scenario tests
- harness integration tests
- failure/retry/re-spec validation
- workspace/tenant validation

Required deliverables:
- end-to-end scenario suite
- failure matrix
- issue list with severity and owner
- user-visible readiness report

Gate to Phase 9:
- critical flows pass
- remaining failures are explicitly accepted or queued

### Phase 9 — Hardening, polish, operational readiness
Objective:
- finish the product for practical use

Allowed:
- hardening
- docs polish
- operational safeguards
- install/runbook material
- observability improvements

Required deliverables:
- hardened defaults
- operator runbook
- final documentation pack
- release/readiness checklist

Exit condition:
- user accepts readiness status

## 6. Worker assignment rules
- use the profiles in `09_SUBAGENT_TASK_PROFILES.md`
- match worker type to phase and deliverable
- keep tasks small and reviewable
- require evidence from implementers and reviewers before acceptance

## 7. Approval and review policy
- Work-like implementation within each phase requires reviewer evidence before phase completion
- critic/reviewer findings are blocking unless explicitly waived by the user
- any contradiction with `open_decisions.md` must be resolved before advancing

## 8. Blocker policy
The orchestrator must block phase advancement if:
- canonical terms are redefined
- a phase leaks into a later phase without approval
- required evidence is missing
- runtime capabilities are being faked in UX or docs
- persistence truth and runtime truth diverge without reconciliation logic

## 9. Completion policy
A phase is complete only when:
- stated deliverables exist
- acceptance criteria are checked against real outputs
- blocker findings are resolved or explicitly accepted by the user
- the orchestrator returns a concise contradiction and readiness review
