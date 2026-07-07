# Subagent Task Profiles for Phases 3-9

Purpose:
Define the standard worker profiles and assignment templates the orchestrator should use during implementation phases.

## 1. General assignment contract
Every worker assignment must include:
- phase identifier
- objective
- in-scope files/components
- out-of-scope boundaries
- required inputs
- expected artifact/output
- acceptance checks
- harness/runtime expectations
- verification evidence required

## 2. Standard profiles

### A. Orchestrator
Use when:
- scoping phases
- decomposing work
- enforcing gates
- contradiction review

Defaults:
- function: orchestrator
- perspective: project conductor
- harness: discussion or Hermes control plane only
- outputs: phase contract, task graph, acceptance gate, review summary

### B. Research Implementer
Use when:
- Phase 3+ needs bounded discovery before code changes
- validating an assumption against live codebase

Defaults:
- function: researcher
- perspective: evidence gatherer
- harness: Hermes delegation, read-mostly toolset
- outputs: evidence memo, file map, risk note, recommendation

### C. Persistence / Contract Implementer
Use when:
- Phase 3 persistence, schema, canonical record, API-contract work is approved

Defaults:
- function: implementer
- perspective: data model + contract builder
- harness: Hermes shell/file or code harness as approved
- outputs: schema changes, migration plan, tests, contract docs update

### D. Harness Adapter Implementer
Use when:
- Phase 4 runtime adapter work is approved

Defaults:
- function: implementer
- perspective: runtime integration engineer
- harness: shell / Opencode / SSH / Hermes as task requires
- outputs: adapter code, capability declarations, error handling, tests

### E. Session / Routing Implementer
Use when:
- Phase 5 session, forking, routing, event model, orchestrator core work is approved

Defaults:
- function: implementer
- perspective: control-plane engineer
- harness: local code harness
- outputs: routing logic, fork contract enforcement, event persistence, tests

### F. UI Workflow Implementer
Use when:
- Phase 6 visible workflow/UI surfaces are approved for build

Defaults:
- function: implementer
- perspective: product workflow engineer
- harness: local code harness
- outputs: screens/components, capability-aware controls, integration tests

### G. Reviewer / Critic
Use when:
- verifying code against spec
- checking for scope drift or false promises

Defaults:
- function: reviewer or critic
- perspective: contract enforcer
- harness: discussion or read-only local tooling
- outputs: pass/fail report, defect list, acceptance disposition

### H. Integration Verifier
Use when:
- Phase 7-8 scenario validation is approved

Defaults:
- function: reviewer
- perspective: end-to-end validator
- harness: whichever runtime is under test
- outputs: scenario evidence, log captures, artifact validation, failure matrix

### I. Consolidator
Use when:
- merging findings from parallel workers
- summarizing a phase for user sign-off

Defaults:
- function: consolidator
- perspective: synthesis and contradiction review
- harness: discussion
- outputs: integrated report, contradictions, recommended next gate

## 3. Phase-to-profile mapping
- Phase 3: Persistence / Contract Implementer + Reviewer + Consolidator
- Phase 4: Harness Adapter Implementer + Reviewer + Integration Verifier
- Phase 5: Session / Routing Implementer + Reviewer + Critic
- Phase 6: UI Workflow Implementer + Reviewer + Critic
- Phase 7: Integration Verifier + Reviewer + Consolidator
- Phase 8: Integration Verifier + Reviewer + Research Implementer for gap checks
- Phase 9: Consolidator + Reviewer + Orchestrator

## 4. Required output shapes by worker type
- implementer: changed files + test evidence + unresolved issues
- researcher: evidence list + constraints + recommendation
- reviewer/critic: verdict + findings + exact blocking items
- consolidator: merged view + contradiction matrix + gate recommendation
- orchestrator: current objective, active phase, deliverables/criteria, verification, next step/blocker

## 5. Non-negotiable guardrails
- workers do not broaden scope silently
- workers do not redefine canonical terms like `harness`
- execution workers do not talk to each other directly unless phase contract explicitly allows it
- reviewers verify actual outputs, not self-reports
- a worker may block, but must return actionable reasons
