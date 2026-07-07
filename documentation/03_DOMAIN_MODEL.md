# Consolidated Domain Model

Status: Consolidated canonical domain model with final decisions applied.

## 1. Canonical persistence
Canonical product records live in `workbench.db`.
Hermes and Opencode records are referenced, not treated as the full product truth.

## 2. Core entities

### Workspace
Fields:
- workspace_id
- tenant_id
- name
- is_default
- created_at

Rule:
- every session, run, board, and artifact belongs to a workspace/tenant context
- MVP may default to a single-user workspace, but the field is not optional

### Channel
Represents a user-visible working lane.
Fields:
- channel_id
- workspace_id
- channel_kind: `chat | research | work | review | system`
- title
- active_session_id
- default_target
- status: `active | paused | stopped | archived`
- created_at
- updated_at

### SessionExtension
Product-layer metadata for a runtime session.
Fields:
- session_id
- workspace_id
- session_type: `chat | research | work`
- agent_profile_binding_id
- fork_id nullable
- task_spec_id nullable
- status: `active | waiting_review | waiting_approval | done | blocked | failed | archived`

Invariant:
- `session_type` is immutable.

### ForkRecord
Fields:
- fork_id
- parent_session_id
- child_session_id
- fork_kind: `branch | type_change | replay | retry`
- fork_reason
- initiated_by: `user | orchestrator | system`
- summary_ref
- decisions_json
- assumptions_json
- open_questions_json
- relevant_artifacts_json
- bootstrap_context_role_internal: `fork_context`
- checkpoint_json
- created_at

### AgentProfile
Canonical 4-axis profile.
Fields:
- agent_profile_id
- name
- version
- provider_ref
- model_ref
- perspective_ref
- function_ref
- harness_ref nullable
- permissions_policy_ref
- capability_hints_json
- created_at
- updated_at

Rule:
- global by default, with later workspace/profile-level overrides

### AgentProfileBinding / AgentInstance
Represents the profile actually bound to a session/run.
Fields:
- binding_id
- session_id
- agent_profile_id
- binding_version
- created_from: `initial | profile_change | replay | retry`
- created_at

Rule:
- changing profile during a session creates a new binding/instance rather than mutating history

### HarnessRun
Product-owned execution/run history record.
Fields:
- harness_run_id
- workspace_id
- session_id
- task_spec_id nullable
- harness_type: `discussion | hermes | opencode | shell | ssh`
- runtime_session_id nullable
- runtime_process_id nullable
- runtime_remote_process_id nullable
- status: `queued | starting | running | blocked | stopping | cancelled | failed | completed | reviewable`
- control_capabilities_json
- artifact_summary_json
- started_at
- ended_at

Rule:
- Workbench owns HarnessRun history even if Opencode or Hermes already store some local history

### TaskSpec
Fields:
- task_spec_id
- workspace_id
- source_session_id
- objective
- scope_in_json
- scope_out_json
- acceptance_criteria_json
- constraints_json
- risk_level
- approval_status: `draft | ready_for_review | approved | rejected | superseded`
- created_at
- updated_at

### RoutedMessage
Persisted routing metadata for every message/event.
Fields:
- routed_message_id
- workspace_id
- session_id nullable
- channel_id
- source_type
- source_id
- target_type
- target_id
- message_kind: `conversation | dispatch | steering | report | system | telemetry | tool_confirmation_request | tool_result`
- payload_ref
- created_at

Decision:
- persist routing metadata for every message/event, not only cross-session traffic

### EventRecord
Fields:
- event_id
- harness_run_id nullable
- routed_message_id nullable
- event_type
- event_source
- event_payload_ref
- event_ts

### PermissionRequest
Fields:
- permission_request_id
- harness_run_id
- scope: `task | tool | command | file | remote_action`
- reason
- requested_action
- requested_by
- decision: `pending | approved | denied | expired`
- escalated_from_auto_approve boolean
- created_at
- decided_at nullable

### Artifact
Fields:
- artifact_id
- workspace_id
- producer_session_id
- producer_harness_run_id nullable
- task_spec_id nullable
- artifact_kind
- title
- content_ref
- content_hash
- predecessor_artifact_id nullable
- created_at

### ReviewRecord
Fields:
- review_id
- workspace_id
- target_kind: `task_spec | artifact | harness_run | session`
- target_id
- reviewer_binding_id nullable
- verdict: `pass | fail | conditional | blocked`
- findings_ref
- criteria_eval_json
- created_at

### ReplayRecord
Fields:
- replay_id
- source_session_id
- source_harness_run_id nullable
- fork_id
- checkpoint_json
- replay_scope
- equivalence_rule: `final_state_plus_reviewer_judgment`
- outcome: `completed | diverged | aborted`
- created_at

## 3. Derived views
Derived views may be materialized later, but are not canonical records:
- worker dashboard view
- run timeline view
- session history view
- channel occupancy view
- review queue view

## 4. Key invariants
- session type never mutates in place
- any type transition requires a ForkRecord
- every HarnessRun belongs to exactly one session
- every sensitive execution path can surface PermissionRequest records
- routing metadata exists for every message/event
- AgentProfile history is append/bind, not destructive overwrite

## 5. Lifecycle outlines
- SessionExtension: `active -> waiting_review -> waiting_approval -> done | blocked | failed | archived`
- TaskSpec: `draft -> ready_for_review -> approved | rejected | superseded`
- HarnessRun: `queued -> starting -> running -> reviewable -> completed` with failure/cancel/blocked branches
- PermissionRequest: `pending -> approved | denied | expired`
- ReviewRecord: append-only

## 6. MVP modeling decisions carried from open_decisions
- AgentProfiles are global by default
- toolsets are negotiated, not fixed blindly
- workbench owns HarnessRun metadata
- every session/run/board has workspace/tenant fields
- replay equivalence is outcome-based, not exact-call-sequence-based
