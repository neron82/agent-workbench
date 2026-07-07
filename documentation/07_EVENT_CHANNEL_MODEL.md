# Consolidated Event and Channel Model

Status: Finalized routing model with user decisions applied.

## 1. Core rule
Every persisted message/event has a source and a target.
Routing metadata is persisted for every message/event.

## 2. Addressing model
Supported target forms:
- `@orchestrator`
- `@agent_name`
- `@all`
- `@system`
- harness-run targeted internal control references

## 3. Routing decision
Default routing is:
- `user -> orchestrator -> worker`

Direct worker dispatch is allowed only when explicitly requested via:
- explicit `@agent` addressing
- explicit UI target selection

It is not the silent default.

## 4. @all decision
`@all` means broadcast to active non-execution discussion participants by default.
It does not trigger execution workers by default.

## 5. Channel kinds
Minimum channel/event categories:
- conversation
- dispatch
- steering
- report
- system
- telemetry

## 6. Anti-chatter invariant
Workers do not address other workers directly by default.
Inter-worker coordination is mediated by the orchestrator.

## 7. Turn policy
- multi-agent communication is turn-based by default
- worker execution turns may run concurrently when orchestrator dispatches multiple workers intentionally
- steering does not imply a full new conversational turn model unless the harness supports it

## 8. Event persistence
Persist at least:
- source_type/source_id
- target_type/target_id
- event/message kind
- payload ref
- session/channel/run linkage
- timestamp

## 9. Visibility surfaces implied by the model
The product must be able to render:
- conversation panel
- run panel
- artifact panel
- review queue
- replay timeline
- system/permission events

## 10. Relationship to Work sessions
Execution workers produce:
- dispatch events
- status/report events
- artifact events
- permission events
- telemetry where available

## 11. Non-goals
- no uncontrolled broadcast to execution workers
- no hidden source-less system actions
- no assumption that all event sources come from one runtime
