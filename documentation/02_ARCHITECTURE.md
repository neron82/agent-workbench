# Consolidated Architecture

Status: Final Phase 2 architecture consolidated with accepted open decisions.

## 1. Goal
Build a channel-based multi-agent workbench that supports:
- Chat sessions for normal conversation
- Research sessions for structured investigation
- Work sessions for executable delivery
- dynamic AgentProfiles
- visible, reviewable, replayable worker activity
- explicit approval and trust boundaries

## 2. Adopted architectural decisions
1. Product truth lives in a separate product-layer SQLite database: `workbench.db`.
2. Hermes and Opencode are harnesses/backends, not the product truth layer.
3. Opencode is managed per HarnessRun / worker task by default.
4. Research uses Hermes subagent delegation as the default orchestration pattern.
5. A product-layer permission model exists for destructive/sensitive actions.
6. Default routing is `user -> orchestrator -> worker`.
7. `@all` targets active non-execution discussion participants only by default.
8. SSH runs must track remote process identity and support best-effort reconnect/status/reap.

## 3. Major components

### 3.1 Workbench API / product layer
Owns product semantics and all canonical product records.
Responsibilities:
- channel/session typing
- fork contract enforcement
- AgentProfile registry
- HarnessRun metadata
- event routing envelopes
- permission requests
- review/replay records
- approval gates

### 3.2 workbench.db
Canonical product persistence for:
- channels
- session extensions
- fork records
- routing metadata
- AgentProfiles
- AgentInstances / bindings
- HarnessRuns
- TaskSpecs
- Artifacts
- ReviewRecords
- ReplayRecords
- PermissionRequests

### 3.3 Hermes harness
Used for:
- Chat lane runtime
- Research lane runtime
- shell/devops Work mode where Hermes runner is selected
- delegate/subagent orchestration
- toolset-constrained execution

### 3.4 Opencode harness
Used for:
- code/file oriented Work runs
- shell/diff/revert/session primitives where Opencode is the selected harness
- per-HarnessRun isolated lifecycle by default

### 3.5 Orchestrator
The only default scope owner.
Responsibilities:
- decide lane/session transitions
- create structured forks
- assign workers
- enforce approval gates
- consolidate outputs
- mediate worker communication

### 3.6 Event Merger
Combines:
- product-layer routed events from `workbench.db`
- Hermes-visible run/session artifacts
- Opencode harness events
into one reviewable timeline.

## 4. Runtime boundaries

### Domain A: conversation + control
- user-facing
- low-side-effect by default
- includes Chat and orchestrator control plane

### Domain B: Research
- read-mostly
- default harness: Hermes delegation
- default policy: no shell/file-write unless explicitly approved by design

### Domain C: Work
- side effects allowed
- explicit TaskSpec and approval gate required
- harness-specific controls

### Domain D: external systems
- SSH hosts
- Opencode runtime
- MCP servers
- external APIs/tools

## 5. Session lanes

### Chat
- default conversational lane
- normal assistant interaction
- may suggest fork, but does not silently mutate into Research or Work

### Research
- structured investigation lane
- default runtime: Hermes subagents
- outputs findings and, where appropriate, TaskSpec artifacts
- Kanban may mirror status, but is not the primary autonomous orchestration engine

### Work
- executable lane
- begins only from an approved TaskSpec
- chooses harness from AgentProfile + session policy + task needs

## 6. Control flow
1. user interacts with Chat / Research / Work channel
2. orchestrator owns scope and routing
3. if a type change is needed, create a structured fork
4. if Work is requested, require TaskSpec and approval gate
5. dispatch worker via selected harness
6. capture HarnessRun metadata + events in product layer
7. present review/replay surfaces before acceptance

## 7. Data flow
- raw harness outputs may exist in Hermes and/or Opencode
- product layer writes canonical routing, run, artifact, review, and fork records into `workbench.db`
- Hermes/Opencode IDs are referenced as foreign runtime identifiers, not promoted to product truth by themselves

## 8. Trust and safety boundaries
- sensitive actions must pass product-layer permission rules
- Work sessions may escalate to per-tool confirmation even when lower layers auto-approve
- unsupported capabilities must be declared as unsupported, not approximated
- orchestrator mediates execution-worker communication

## 9. Review and replay model
- replay is a product-layer reconstruction / re-run operation keyed by standardized checkpoint records
- review is a first-class product record against TaskSpec, Artifact, or HarnessRun
- replay equivalence means equivalent final state and reviewer-judged outcome, not identical tool-call sequence

## 10. Cross-store strategy
The product owns `workbench.db` and references external runtime IDs:
- Hermes session IDs
- Hermes delegate child IDs
- Opencode session IDs
- process IDs / remote process IDs

This avoids semantic leakage while preserving auditability.

## 11. Architectural non-goals
- not a thin wrapper around Opencode
- not a pure chat UI
- not a benchmark/eval framework
- not a promise of identical capabilities across all harnesses

## 12. Phase 3-9 implementation sequence
- Phase 3: persistence + contracts + API skeleton
- Phase 4: harness adapter layer and permission model
- Phase 5: session/fork/routing/orchestration core
- Phase 6: UI workflow surfaces and approval paths
- Phase 7: replay/review/verification hardening
- Phase 8: end-to-end integration and scenario validation
- Phase 9: polish, hardening, operational readiness, documentation closure
