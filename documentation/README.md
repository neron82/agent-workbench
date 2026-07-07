# Agent Workbench Project Package

Status: Phase 0-2 complete. This folder now contains the consolidated implementation package for Phases 3-9.

Purpose:
- persist the accepted Phase 1 research findings
- persist the resolved Phase 2 specification baseline
- apply the decisions from `open_decisions.md`
- define worker/subagent task profiles for implementation
- define the final orchestrator contract for implementation Phases 3-9

Source of truth hierarchy:
1. `open_decisions.md`
2. `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md`
3. the consolidated spec files in this folder
4. Phase 1/2 historical worker outputs in Hermes session history

Files in this package:
- `01_PHASE1_RESEARCH_FOUNDATIONS.md` — verified research baseline and constraints from Phase 1
- `02_ARCHITECTURE.md` — consolidated final architecture with decisions applied
- `03_DOMAIN_MODEL.md` — canonical records, IDs, relationships, lifecycle rules
- `04_SESSION_FORKING.md` — structured fork contract and inheritance rules
- `05_AGENT_PROFILES.md` — dynamic agent profile model and compatibility rules
- `06_HARNESS_ADAPTERS.md` — runtime adapter contract for Hermes, Opencode, shell, SSH, discussion
- `07_EVENT_CHANNEL_MODEL.md` — routing, addressing, turn-policy, event persistence
- `08_UI_WORKFLOW.md` — operator-visible Chat / Research / Work workflow and approval model
- `09_SUBAGENT_TASK_PROFILES.md` — standard worker profiles and implementation task templates
- `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md` — phase-gated implementation contract
- `open_decisions.md` — resolved product decisions supplied by the user

Implementation stance:
- Hermes and Opencode are harnesses/backends, not the product truth layer.
- The product truth layer is the workbench product layer and its own persistence.
- No uncontrolled agent-to-agent chatter.
- All execution work remains phase-gated and evidence-backed.

Recommended reading order:
1. `open_decisions.md`
2. `01_PHASE1_RESEARCH_FOUNDATIONS.md`
3. `02_ARCHITECTURE.md`
4. `03_DOMAIN_MODEL.md`
5. `10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md`

Quick summary of adopted decisions:
- product-layer persistence: separate SQLite database `workbench.db`
- Research default: Hermes subagent delegation
- Work history: product-owned HarnessRun metadata layer
- routing default: user -> orchestrator -> worker
- `@all`: non-execution discussion broadcast only by default
- fork metadata: product-layer tables
- AgentProfiles: global by default, with later override support
- toolsets: negotiated from profile permissions, harness capabilities, session policy
- SSH cancel: best-effort with remote identity tracking and reap contract
