# Consolidated Agent Profile Specification

Status: Finalized 4-axis AgentProfile model with user decisions applied.

## 1. Definition
An agent is a runtime combination of:
1. Provider / Model
2. Perspective
3. Function
4. Optional Harness

## 2. Profile ownership decision
- AgentProfiles are global by default.
- workspace/profile-level overrides may be added later.

## 3. Profile fields
Required:
- `agent_profile_id`
- `name`
- `version`
- `provider`
- `model`
- `perspective`
- `function`

Optional:
- `harness`
- `skill_refs`
- `system_prompt_overrides`
- `permission_policy`
- `capability_hints`
- `preferred_toolsets`
- `fallback_model_chain`

## 4. Function vocabulary decision
For MVP the function vocabulary is closed, but the model should be designed for later registry extension.
Suggested MVP functions:
- orchestrator
- researcher
- implementer
- reviewer
- critic
- consolidator
- operator
- support

## 5. Toolset decision
Effective toolset is negotiated from:
- AgentProfile permissions / preferences
- harness capabilities
- session policy

Therefore:
- an AgentProfile may express preferred/allowed toolsets
- the effective runtime toolset is a resolved view, not a blind static field

## 6. Capability decision
Capabilities remain mostly toolset-driven for MVP.
Minimal first-class model capability metadata may exist, but do not build a large model-catalog dependency into MVP.

## 7. Profile change decision
Agent profile changes during a session do not rewrite history.
They create:
- a new AgentInstance / binding
- a new versioned runtime association

## 8. Compatibility rules
Examples:
- `discussion` harness may not be assigned to side-effect tasks
- SSH harness requires remote-process identity support
- Opencode harness should be selected for code/file work where diff/revert/review surfaces matter
- Research sessions default to Hermes delegation unless explicitly overridden

## 9. Serialization guidance
Product-layer canonical persistence belongs in `workbench.db`.
Optional export/import format may be YAML or JSON.

## 10. Review/display shape
Every profile should display, at minimum:
- profile name/version
- provider/model
- perspective
- function
- harness or `none`
- effective toolset
- permission policy
- capability summary

## 11. Relationship to Hermes
Reused from Hermes:
- provider/model/backend/toolset/config realities
- profile/system-prompt infrastructure where available

Added by product layer:
- first-class 4-axis AgentProfile object
- binding/version history
- negotiated effective toolset resolution
- global registry semantics
