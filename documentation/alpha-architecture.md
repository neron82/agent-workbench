# Agent Workbench Alpha Architecture

Status: implementation contract for the alpha pass.

## Product boundary

The alpha is a single-user, local-first workspace app for running several agent participants across several project sessions without exposing the underlying orchestration machinery as the primary UI.

The app keeps the existing Flask + SQLite + Jinja architecture. No message broker, SPA rewrite, or microservice split is justified at this stage.

## Vocabulary

- Internal `workspace` remains the persistence boundary for tenant isolation.
- UI calls a workspace a `project`; this is the user's durable container for sessions and assets.
- A session is a conversation/workspace lane inside a project.
- `chat`, `research`, and `work` are labels. They are not permission gates.
- Agent profiles are reusable team members. Session participants are the members assigned to one session.
- A run is execution history; agent status is a live projection of the current run.

## Alpha decisions

1. **Projects without a destructive rename**: keep `workspaces` and `workspace_id` internally, expose project navigation and project names in the UI. This uses the existing FK boundary and avoids a pointless migration museum.
2. **Extensible session labels**: add a `session_labels` table with a label string, display name, color, and optional description. Existing `session_type` values are backfilled as labels and remain as a compatibility field. New sessions may choose any safe label; known labels get familiar styling.
3. **Labels do not authorize tools**: remove the runtime dependency on `DEFAULT_SESSION_POLICIES` as a permission source. Effective capabilities come from the agent profile's explicit `allowed_tools` / `denied_tools`, the harness capabilities, and optional session capability overrides. A label only describes context.
4. **Parallel participant execution**: one request creates one background response coordinator; that coordinator launches one worker thread per active participant. Each worker owns a fresh SQLite connection, its own status key, and its own history snapshot. Workers never share a connection.
5. **Live status is per participant**: the tracker returns an entry for every active participant, including idle agents. Runtime marks `queued -> working -> completed/error/stopped`; status polling remains the first alpha transport. Message SSE remains the message transport. Do not pretend SSE is a status bus yet.
6. **Team management**: the existing agent profile settings remain the global catalog. The session participant bar becomes the primary team-management surface. A compact team page may come later; alpha needs add/remove, role, capability summary, and status.
7. **Capabilities are concise**: agent profile hints accept `allowed_tools`, `denied_tools`, and optional `allowed_permission_classes`. The UI renders the effective list as readable chips; raw JSON remains an advanced field in settings.
8. **Fork becomes participant transfer**: a new session can be created from an existing session while transferring selected/all participants and a compact context summary. Keep the old fork records for history and compatibility, but the user-facing action is `Transfer to new session`, not a type transition ceremony.
9. **Identity**: add a tiny local `users` table and a session-backed identity cookie. First visit asks for a display name; the message source is that stable local user id. The old `web-user` fallback is removed. This is intentionally not auth; the table can grow into real user management later.
10. **Session cards**: project dashboard shows active/recent sessions as cards with label, status, participant count, live working count, and last activity. Cards link directly to the session and make switching cheap.
11. **Files and repositories**: add a project-scoped `project_assets` table for local directories, repositories, and files. The alpha browser lists a bounded directory tree and can attach an asset to a session/agent. No arbitrary file mutation is added to this feature.
12. **API shape**: JSON endpoints are added for dashboard/session cards, live status, assets, and identity where useful; existing form routes remain for no-JS operation.

## Implementation lanes

- Persistence/domain lane: migration, repositories, label/project asset/user operations, participant transfer service, dashboard data queries.
- Runtime lane: parallel workers, fresh connections, durable/complete status projection, label-neutral capability negotiation, regression tests.
- UI lane: project dashboard, session cards, participant/status panel, identity prompt, assets browser/linking, restrained design layer.
- Parent integration: reconcile imports, run full tests, run the live server smoke test, exercise two concurrent participants, verify file browser path safety.

## Verification gates

- Existing suite remains green.
- New tests prove two participants overlap in time, idle participants are represented, and one failure does not suppress the other.
- New tests prove arbitrary session labels do not change the effective tool policy.
- New tests prove identity replaces `web-user` in persisted message payloads.
- New tests prove path traversal is rejected by the asset browser.
- Live smoke: start the server in production mode without the Flask reloader, create/open a project, create two sessions, add two mock agents, send a message, observe separate status entries and responses, switch cards, and browse an attached directory.
