# Consolidated UI Workflow Specification

Status: Finalized workflow baseline with user decisions applied.

## 1. Product posture
This is a multi-agent workbench.
It is not merely a web chat and not merely an Opencode wrapper.

## 2. Session types
- Chat
- Research
- Work

Transitions happen by structured fork.

## 3. Primary user journey
1. Start in Chat.
2. Fork to Research when structured investigation is required.
3. Research produces findings and optionally a TaskSpec.
4. Promote to Work via a structured fork.
5. Review and approve TaskSpec.
6. Dispatch Work run on selected harness.
7. Observe run panel.
8. Review artifacts/results.
9. Accept, retry, or re-spec.

## 4. Required visible surfaces
For a Work run, show where applicable:
- status
- objective
- assigned agent/profile
- perspective
- function
- harness
- transcript or summary
- tools/events
- files read/written where detectable
- commands where applicable
- warnings/errors
- artifacts
- control availability

## 5. Approval model
- Work requires approval before execution from TaskSpec.
- sensitive Work sessions may escalate to per-tool confirmation even when lower layers are auto-approve capable.
- decomposition approval defaults to dependency-order approval.
- low-risk batches may support batch approval later.

## 6. Pause decision
Pause remains a future capability flag.
MVP UI uses:
- steer where supported
- stop
- cancel
- block-and-re-spec

Do not present a live pause control unless a harness truly supports it.

## 7. Steering decision
- Kanban-backed workers default to block-and-re-spec
- mid-run steering is harness-specific
- UI must never imply universal live steering

## 8. Fork UX decision
- messaging gateways: limited fork creation with auto-summary only
- full editable fork UX: Web / CLI / ACP

## 9. Routing UX decision
- default user action routes to orchestrator
- explicit target selection or `@agent` may route directly to a worker
- `@all` is discussion broadcast only by default

## 10. Run history decision
Workbench maintains its own HarnessRun history and review surfaces.
Do not rely on Opencode runtime history alone for user-visible product history.

## 11. Replay / review decision
Replay equivalence means equivalent final state and reviewer-judged outcome.
Review surfaces must make that clear.

## 12. Honest capability rule
Every unsupported field/control must be:
- hidden with a precise reason, or
- disabled with a precise reason

Never show fake universal controls.
