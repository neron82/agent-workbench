"""001 — Initial schema: all 14 canonical product tables.

Creates the full set of canonical tables defined in the domain model
(03_DOMAIN_MODEL.md).  Every table uses a ``uuid4`` string as its primary
key (stored as ``TEXT``), ``REAL`` for timestamps (Unix epoch), and ``TEXT``
for JSON blobs.  Enum columns carry ``CHECK`` constraints.
"""

from __future__ import annotations

import sqlite3


_SCHEMA_SQL = """
-- 1. workspaces
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id  TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    is_default    INTEGER NOT NULL DEFAULT 0
                  CHECK (is_default IN (0, 1)),
    created_at    REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 2. channels
CREATE TABLE IF NOT EXISTS channels (
    channel_id        TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL REFERENCES workspaces(workspace_id),
    channel_kind      TEXT NOT NULL
                      CHECK (channel_kind IN ('chat', 'research', 'work', 'review', 'system')),
    title             TEXT NOT NULL DEFAULT '',
    active_session_id TEXT,
    default_target    TEXT,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'paused', 'stopped', 'archived')),
    created_at        REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at        REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 3. session_extensions
CREATE TABLE IF NOT EXISTS session_extensions (
    session_id                 TEXT PRIMARY KEY,
    workspace_id               TEXT NOT NULL REFERENCES workspaces(workspace_id),
    session_type               TEXT NOT NULL
                              CHECK (session_type IN ('chat', 'research', 'work')),
    agent_profile_binding_id   TEXT REFERENCES agent_profile_bindings(binding_id),
    fork_id                    TEXT REFERENCES fork_records(fork_id),
    task_spec_id               TEXT REFERENCES task_specs(task_spec_id),
    status                     TEXT NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'waiting_review', 'waiting_approval', 'done', 'blocked', 'failed', 'archived')),
    created_at                 REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 4. fork_records
CREATE TABLE IF NOT EXISTS fork_records (
    fork_id                     TEXT PRIMARY KEY,
    parent_session_id           TEXT NOT NULL,
    child_session_id            TEXT NOT NULL,
    fork_kind                   TEXT NOT NULL
                               CHECK (fork_kind IN ('branch', 'type_change', 'replay', 'retry')),
    fork_reason                 TEXT NOT NULL DEFAULT '',
    initiated_by                TEXT NOT NULL DEFAULT 'user'
                               CHECK (initiated_by IN ('user', 'orchestrator', 'system')),
    summary_ref                 TEXT,
    decisions_json              TEXT,
    assumptions_json            TEXT,
    open_questions_json         TEXT,
    relevant_artifacts_json     TEXT,
    bootstrap_context_role_internal TEXT NOT NULL DEFAULT 'fork_context',
    checkpoint_json             TEXT,
    created_at                  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 5. agent_profiles
CREATE TABLE IF NOT EXISTS agent_profiles (
    agent_profile_id        TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    version                 TEXT NOT NULL DEFAULT '1',
    provider_ref            TEXT,
    model_ref               TEXT,
    perspective_ref         TEXT,
    function_ref            TEXT,
    harness_ref             TEXT,
    permissions_policy_ref  TEXT,
    capability_hints_json   TEXT,
    created_at              REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at              REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 6. agent_profile_bindings
CREATE TABLE IF NOT EXISTS agent_profile_bindings (
    binding_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent_profile_id TEXT NOT NULL REFERENCES agent_profiles(agent_profile_id),
    binding_version TEXT NOT NULL DEFAULT '1',
    created_from    TEXT NOT NULL DEFAULT 'initial'
                    CHECK (created_from IN ('initial', 'profile_change', 'replay', 'retry')),
    created_at      REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 7. harness_runs
CREATE TABLE IF NOT EXISTS harness_runs (
    harness_run_id               TEXT PRIMARY KEY,
    workspace_id                 TEXT NOT NULL REFERENCES workspaces(workspace_id),
    session_id                   TEXT NOT NULL,
    task_spec_id                 TEXT REFERENCES task_specs(task_spec_id),
    harness_type                 TEXT NOT NULL
                                CHECK (harness_type IN ('discussion', 'hermes', 'opencode', 'shell', 'ssh')),
    runtime_session_id           TEXT,
    runtime_process_id           TEXT,
    runtime_remote_process_id    TEXT,
    status                       TEXT NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'starting', 'running', 'blocked', 'stopping', 'cancelled', 'failed', 'completed', 'reviewable')),
    control_capabilities_json    TEXT,
    artifact_summary_json        TEXT,
    started_at                   REAL,
    ended_at                     REAL
);

-- 8. task_specs
CREATE TABLE IF NOT EXISTS task_specs (
    task_spec_id          TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL REFERENCES workspaces(workspace_id),
    source_session_id     TEXT,
    objective             TEXT NOT NULL DEFAULT '',
    scope_in_json         TEXT,
    scope_out_json        TEXT,
    acceptance_criteria_json TEXT,
    constraints_json      TEXT,
    risk_level            TEXT,
    approval_status       TEXT NOT NULL DEFAULT 'draft'
                         CHECK (approval_status IN ('draft', 'ready_for_review', 'approved', 'rejected', 'superseded')),
    created_at            REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at            REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 9. routed_messages
CREATE TABLE IF NOT EXISTS routed_messages (
    routed_message_id TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL REFERENCES workspaces(workspace_id),
    session_id        TEXT,
    channel_id        TEXT NOT NULL,
    source_type       TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    target_type       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    message_kind      TEXT NOT NULL
                     CHECK (message_kind IN ('conversation', 'dispatch', 'steering', 'report', 'system', 'telemetry')),
    payload_ref       TEXT,
    created_at        REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 10. event_records
CREATE TABLE IF NOT EXISTS event_records (
    event_id            TEXT PRIMARY KEY,
    harness_run_id      TEXT REFERENCES harness_runs(harness_run_id),
    routed_message_id   TEXT REFERENCES routed_messages(routed_message_id),
    event_type          TEXT NOT NULL,
    event_source        TEXT NOT NULL,
    event_payload_ref   TEXT,
    event_ts            REAL NOT NULL
);

-- 11. permission_requests
CREATE TABLE IF NOT EXISTS permission_requests (
    permission_request_id TEXT PRIMARY KEY,
    harness_run_id        TEXT NOT NULL REFERENCES harness_runs(harness_run_id),
    scope                 TEXT NOT NULL
                         CHECK (scope IN ('task', 'tool', 'command', 'file', 'remote_action')),
    reason                TEXT NOT NULL DEFAULT '',
    requested_action      TEXT NOT NULL,
    requested_by          TEXT NOT NULL,
    decision              TEXT NOT NULL DEFAULT 'pending'
                         CHECK (decision IN ('pending', 'approved', 'denied', 'expired')),
    escalated_from_auto_approve INTEGER NOT NULL DEFAULT 0
                               CHECK (escalated_from_auto_approve IN (0, 1)),
    created_at            REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    decided_at            REAL
);

-- 12. artifacts
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id              TEXT PRIMARY KEY,
    workspace_id             TEXT NOT NULL REFERENCES workspaces(workspace_id),
    producer_session_id      TEXT NOT NULL,
    producer_harness_run_id  TEXT REFERENCES harness_runs(harness_run_id),
    task_spec_id             TEXT REFERENCES task_specs(task_spec_id),
    artifact_kind            TEXT NOT NULL,
    title                    TEXT NOT NULL DEFAULT '',
    content_ref              TEXT,
    content_hash             TEXT,
    predecessor_artifact_id  TEXT REFERENCES artifacts(artifact_id),
    created_at               REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 13. review_records
CREATE TABLE IF NOT EXISTS review_records (
    review_id          TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL REFERENCES workspaces(workspace_id),
    target_kind        TEXT NOT NULL
                      CHECK (target_kind IN ('task_spec', 'artifact', 'harness_run', 'session')),
    target_id          TEXT NOT NULL,
    reviewer_binding_id TEXT REFERENCES agent_profile_bindings(binding_id),
    verdict            TEXT NOT NULL
                      CHECK (verdict IN ('pass', 'fail', 'conditional', 'blocked')),
    findings_ref       TEXT,
    criteria_eval_json TEXT,
    created_at         REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 14. replay_records
CREATE TABLE IF NOT EXISTS replay_records (
    replay_id              TEXT PRIMARY KEY,
    source_session_id      TEXT NOT NULL,
    source_harness_run_id  TEXT REFERENCES harness_runs(harness_run_id),
    fork_id                TEXT NOT NULL REFERENCES fork_records(fork_id),
    checkpoint_json        TEXT,
    replay_scope           TEXT NOT NULL DEFAULT '',
    equivalence_rule       TEXT NOT NULL DEFAULT 'final_state_plus_reviewer_judgment',
    outcome                TEXT NOT NULL DEFAULT 'completed'
                          CHECK (outcome IN ('completed', 'diverged', 'aborted')),
    created_at             REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
