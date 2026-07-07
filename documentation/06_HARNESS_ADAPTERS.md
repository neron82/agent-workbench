# Consolidated Harness Adapter Specification

Status: Finalized harness contract with user decisions applied.

## 1. Meaning of harness
Harness means the execution/runtime interface used when the agent must interact with:
- tools
- files
- shell
- code
- remote hosts
- Hermes tooling
- Opencode
- external systems

A pure discussion agent may have no harness.

## 2. Adapter types
- `discussion`
- `hermes`
- `opencode`
- `shell`
- `ssh`

## 3. General adapter contract
Each adapter must declare:
- adapter type
- lifecycle states
- control capabilities
- artifact capture behavior
- error classes
- review/replay implications
- runtime identifiers

## 4. Lifecycle expectations
Common phases:
- ready
- starting
- running
- stopping
- cancelled
- failed
- completed
- reviewable

## 5. Control semantics

### discussion
- no side effects
- no execution controls beyond conversational routing

### hermes
- supports Hermes-native execution where selected
- steering and control only as actually supported by the runtime path
- no fake pause primitive

### opencode
- default lifecycle is one harness instance per HarnessRun / worker task
- no shared global server default
- run metadata must still be persisted in Workbench

### shell
- local process model
- explicit process IDs if execution is backgrounded or persistent

### ssh
- remote process identity is mandatory
- best-effort cancel/reap is mandatory
- long-term contract requires reconnect/status/reap support
- do not promise perfect kill semantics

## 6. Permission model decision
Sensitive and destructive actions must surface a product-layer permission model.
This may be minimal in Phase 3, but it must exist architecturally from the start.

## 7. Metadata the product must surface
Per HarnessRun, surface:
- harness type
- runtime session/process identifiers
- remote host identifiers where applicable
- declared control capabilities
- active permission state
- artifact refs / diff refs
- command visibility where applicable
- warnings / errors

## 8. Product-owned history decision
Workbench maintains its own HarnessRun metadata layer.
Opencode runtime history alone is insufficient as product history.

## 9. Unsupported/common caveats
- pause is future-only unless a harness truly supports it
- steer is harness-specific
- cancel and stop are not identical
- replay does not require identical tool-call sequence; it requires equivalent final state + reviewer judgment

## 10. Adapter selection rules
Select harness from:
- AgentProfile harness preference
- task requirements
- session policy
- permission/risk policy
- workspace/runtime availability

Default examples:
- code/file work -> prefer Opencode
- infrastructure/shell work -> shell or ssh as appropriate
- research -> Hermes delegation
- discussion-only tasks -> no harness
