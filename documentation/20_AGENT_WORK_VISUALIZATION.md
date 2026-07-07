# Agent Work Visualization — Implementation Spec

**Status:** Implemented July 2026
**Related PRs/Commits:** (filled on merge)

## 1. Problem

When an agent runs a tool-calling loop (multiple `shell.run_command`, `hermes.write_file`, etc.), the chat UI showed only the final reply. The user had no visibility into:

- What the agent is currently doing ("is it stuck?")
- Which tools were called and in what order
- What each tool returned
- How to stop an agent that is on the wrong path

This was especially painful for long-running work sessions (25+ tool iterations) where a wrong first step could waste minutes before the user noticed.

## 2. Solution Architecture

Two complementary systems:

### 2.1 In-Memory Live Status (`AgentStatusTracker`)

A thread-safe singleton that tracks what each agent is doing *right now*. Used for the live "working" indicator bar and the stop mechanism.

**Data model:**
- `AgentStatus` per (session_id, agent_name): status (idle/working/completed/stopped/error), iteration count, current step, error
- `AgentStep` per tool call: iteration number, tool name, arguments, result, status (running/completed/failed)
- `threading.Event` per running agent for the stop signal

**Lifecycle:**
1. `start_agent()` — called when the runtime enters the tool loop
2. `start_step()` — called before each tool dispatch
3. `complete_step()` — called after each tool dispatch returns
4. `complete_agent()` — called in `finally` block (always), or in `except` block (on error)

### 2.2 Persistent Work History (`routed_messages` with `message_kind='agent_work'`)

Each tool call is persisted as a `routed_messages` row so the chat history shows "work done" bubbles that survive page reloads.

**Payload envelope:**
```json
{
  "envelope": "agent_work",
  "agent_name": "Joe Schmo",
  "iteration": 3,
  "tool_name": "shell.run_command",
  "tool_arguments": {"command": "ls -la"},
  "tool_result": "{\"ok\": true, \"stdout\": \"...\"}",
  "status": "completed",
  "invocation_id": "abc123..."
}
```

**DB changes:**
- Migration `009_agent_work_message_kind`: adds `'agent_work'` to the `routed_messages.message_kind` CHECK constraint
- `VALID_MESSAGE_KINDS` in `routing_service.py` updated accordingly

## 3. Frontend Components

### 3.1 Working Indicator Bar
- Appears below the participants bar when any agent has status `working`
- Shows: "⏳ Working… AgentName — tool_name (#N)"
- Stop button (red, `btn-danger`) appears only while an agent is running
- Auto-hides when all agents reach `completed`/`error`/`stopped` status
- Polls `GET /sessions/<id>/agent-status` every 1 second

### 3.2 Work Step Bubbles (in chat)
- Rendered by `message_row.html` when `envelope == 'agent_work'`
- Compact: tool icon + tool name + iteration number + status dot
- Clickable: opens the work detail panel on the right

### 3.3 Work Detail Panel (right sidebar)
- Title: "Work Step: tool_name"
- Sections: Tool (name + status), Arguments (JSON), Result (raw text)
- Link to full invocation detail page

### 3.4 Stop Mechanism
- `POST /sessions/<id>/stop-agent` with `agent_name` in form body
- Sets `threading.Event` in the tracker
- Runtime checks `tracker.should_stop()` between iterations
- On stop: breaks the loop, returns `"[agent stopped by user]"` as the final reply
- The agent is marked as `stopped` in the tracker

## 4. API Reference

### `GET /sessions/<id>/agent-status`
Returns JSON:
```json
{
  "agents": [
    {
      "agent_name": "Joe Schmo",
      "status": "working",
      "iteration_count": 3,
      "current_step": {
        "iteration": 3,
        "tool_name": "shell.run_command",
        "tool_arguments": {"command": "ls"},
        "tool_result": "{\"ok\": true, ...}",
        "status": "running"
      },
      "error": null,
      "started_at": 1783373726.0
    }
  ]
}
```

### `POST /sessions/<id>/stop-agent`
Form fields: `agent_name` (required)
Returns: redirect to session view with flash message.

## 5. Files Changed

| File | Change |
|------|--------|
| `services/agent_status.py` | **New** — AgentStatusTracker singleton |
| `services/agent_runtime_service.py` | Tracker integration in `_openai_compatible_reply()` |
| `services/routing_service.py` | Added `'agent_work'` to `VALID_MESSAGE_KINDS` |
| `db/migrations/009_agent_work_message_kind.py` | **New** — DB migration for CHECK constraint |
| `web/sessions.py` | Two new routes: `agent_status`, `stop_agent` |
| `web/templates/session_view.html` | Working bar, stop button, work panel, live polling JS |
| `web/templates/message_row.html` | `agent_work` envelope rendering |

## 6. Future Considerations

- **Reasoning traces:** The current implementation captures tool calls and results. Future work could add the LLM's reasoning text between steps (the `reasoning_content` field from providers that support it).
- **Streaming updates:** The 1-second polling interval is fine for most cases. For very long tool calls (30s+), SSE or WebSocket push would be more responsive.
- **Per-session toggle:** Some users may want to disable work step visibility for simple chat sessions. A config toggle on the session config page would be straightforward.
