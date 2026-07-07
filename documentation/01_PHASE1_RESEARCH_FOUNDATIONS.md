# Phase 1 Research Foundations

Purpose:
Capture the verified findings from Phase 1 that must constrain Phases 3-9.

## 1. Verified platform facts

### Hermes
- Hermes provides durable session persistence via `state.db` with SQLite + FTS5.
- Session lineage exists via `parent_session_id`.
- ACP exposes session primitives including create/get/remove/fork/list.
- `delegate_task` creates isolated worker sessions and active-subagent visibility surfaces.
- Hermes Kanban provides tasks, runs, events, attachments, dispatch, status, and replay-adjacent surfaces.
- Hermes toolsets are explicit and can be restricted.
- Hermes profiles/config already carry provider/model/toolset/backend settings, but not a first-class 4-axis agent profile.
- Hermes gateway/ACP/session surfaces provide enough underlying primitives for the product layer.

### Opencode
- Opencode was not locally installed in this environment during research.
- Public docs confirm server/session/message/shell/abort/diff/revert/SSE/event surfaces.
- Public docs do not justify pretending Opencode already has a first-class product run history model.
- Therefore Workbench must maintain its own HarnessRun metadata layer even when Opencode is used.

### Provider / model layer
- Hermes provider/model resolution is layered and transport-coupled.
- Provider/model/API mode/base URL/normalization are distributed across multiple concerns.
- Hermes does not already expose one clean object that equals:
  - provider/model
  - perspective
  - function
  - harness
- That gap justified introducing the new product-layer AgentProfile object.

### Runner / host execution
- Local and SSH execution semantics differ materially.
- SSH cancellation cannot be treated as perfectly reliable.
- Remote process identity and reap/reconciliation must be modeled explicitly.

## 2. Verified product implications
- The workbench must be a product layer above Hermes/Opencode, not a skin around one existing primitive.
- Session type cannot be inferred from raw Hermes sessions alone; Chat / Research / Work must be explicit product semantics.
- Structured forking must be stricter than raw lineage.
- Visibility, replay, review, stop, and steering must be harness-aware.
- Unsupported control capabilities must be surfaced honestly rather than normalized into fake common buttons.

## 3. Research outputs accepted in Phase 1
- Hermes integration surface map
- Opencode integration surface map
- Provider/model integration map
- Runner/host execution map
- Critic pass on research-plan rigor

These outputs established the baseline reused by Phase 2.

## 4. Risks inherited into Phase 3+
- cross-store consistency if multiple backing systems are involved
- Opencode runtime behavior may differ from public docs until locally verified
- SSH orphan-risk remains real
- capability promises can drift unless the harness contract is authoritative
- direct use of Hermes internals without a product-layer contract would create semantic leakage

## 5. Hard constraints for later phases
- Do not treat benchmark/eval harnesses as the meaning of `harness`.
- Do not mutate session type in place.
- Do not allow uncontrolled worker-to-worker chatter.
- Do not use Hermes/Opencode persistence as the sole product truth.
- Do not promise pause/resume where it does not exist.

## 6. What Phase 1 leaves open
Phase 1 established feasibility, not final product policy. The unresolved policy choices were later resolved in `open_decisions.md` and are integrated into the consolidated specs in this folder.
