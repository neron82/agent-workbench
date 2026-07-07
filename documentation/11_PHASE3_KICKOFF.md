# Phase 3 Kickoff Sheet

Status: Ready for explicit Phase 3 approval. This sheet is a short operational handoff, not an implementation artifact.

## 1. Phase 3 objective
Build the product persistence and contract layer for Agent Workbench.

Phase 3 is responsible for:
- introducing the product truth layer around `workbench.db`
- implementing canonical product records and storage contracts
- defining persistence-facing APIs/domain scaffolding required by later phases
- proving IDs, lifecycle states, routing records, and core invariants with tests

## 2. Phase 3 is not
- full UI work
- harness adapter implementation beyond what persistence contracts require
- fake capability claims
- freeform architecture revision
- a license to skip review or tests

## 3. Source-of-truth order for Phase 3
1. current user instruction
2. `open_decisions.md`
3. `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md`
4. `03_DOMAIN_MODEL.md`
5. `02_ARCHITECTURE.md`
6. remaining consolidated specs as needed

## 4. Mandatory decisions already resolved
Phase 3 must assume these as fixed unless the user changes them:
- product persistence lives in separate SQLite database `workbench.db`
- Hermes and Opencode are harnesses/backends, not the product truth layer
- routing metadata is persisted for every message/event
- fork metadata lives in product-layer fork tables
- AgentProfiles are global by default
- effective toolset is negotiated from profile permissions, harness capabilities, and session policy
- every session/run/board carries workspace_id / tenant_id
- Workbench owns its own HarnessRun metadata layer
- replay equivalence = equivalent final state + reviewer-judged outcome

## 5. Required Phase 3 deliverables
- `workbench.db` schema proposal / implementation
- migration strategy
- canonical record implementations for the product layer
- persistence tests for core invariants
- contract documentation updates where code requires it

Minimum canonical records expected in Phase 3:
- Workspace
- Channel
- SessionExtension
- ForkRecord
- AgentProfile
- AgentProfileBinding / AgentInstance
- HarnessRun
- TaskSpec
- RoutedMessage
- EventRecord
- PermissionRequest
- Artifact
- ReviewRecord
- ReplayRecord

## 6. Priority implementation order inside Phase 3
1. database layout and migration framework
2. identity model (`workspace_id`, `channel_id`, `session_id`, `fork_id`, `harness_run_id`, etc.)
3. canonical tables for SessionExtension / ForkRecord / AgentProfile / HarnessRun
4. routing/event persistence tables
5. TaskSpec / Artifact / Review / Replay / PermissionRequest tables
6. repository/service layer for reads/writes
7. invariant and migration tests

## 7. Required invariants to prove in tests
- session type is immutable
- type change requires a fork record
- every persisted message/event has source + target routing metadata
- every HarnessRun belongs to one session and one workspace
- AgentProfile history is append/bind, not destructive overwrite
- fork context is stored product-side, not only in Hermes lineage
- workspace/tenant fields exist across canonical product records

## 8. Suggested worker split for Phase 3
Recommended worker roles:
- Implementer A: schema + migrations
- Implementer B: domain models + repositories
- Reviewer: persistence correctness + invariants
- Critic: scope/contract enforcement
- Consolidator: merge findings and prepare gate review

Recommended task boundaries:
- Task P3-1: schema skeleton + migration framework
- Task P3-2: identity and workspace/tenant records
- Task P3-3: session/fork canonical records
- Task P3-4: agent profile + bindings
- Task P3-5: harness run + routing/event records
- Task P3-6: TaskSpec / Artifact / Review / Replay / PermissionRequest
- Task P3-7: persistence tests + gate verification

## 9. Required evidence before Phase 3 acceptance
- actual schema files or migration files on disk
- actual tests on disk
- real test execution output
- reviewer evidence against the invariants above
- contradiction check against `open_decisions.md` and the orchestrator contract

## 10. Blockers that must stop Phase 3
Stop the phase if any worker:
- tries to implement UI concerns
- redefines `harness`
- treats Hermes or Opencode persistence as the sole product truth
- omits workspace/tenant fields from canonical records
- skips routing metadata persistence
- collapses profile changes into destructive mutation
- produces code without tests for the new invariants

## 11. Exit gate to Phase 4
Phase 3 is complete only when:
- `workbench.db` persistence layer exists and is test-backed
- canonical records are implemented
- routing/fork/profile/run storage is verified
- schema and IDs are stable enough for harness adapters to bind to them
- reviewer/critic findings are resolved or explicitly waived by the user

## 12. First orchestrator move once Phase 3 is approved
On approval, the orchestrator should:
1. create the Phase 3 worker assignment set
2. keep implementation bounded to persistence/contracts only
3. require real test evidence from each implementation worker
4. run a final contradiction review before declaring Phase 3 complete
