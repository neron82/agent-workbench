"""002 — Provider/role registry + session participants for chat UI.

Adds the minimum product tables needed for a usable chat-oriented UI:

* ``providers`` — provider registry for LLM backends
* ``roles`` — user-defined / builtin role registry with system prompts
* ``session_participants`` — append-only membership history for agents in a
  session, implemented as soft-delete rows so removal stays auditable

The migration also seeds a small builtin role set and a local ``mock``
provider so the UI is immediately usable without external credentials.
"""

from __future__ import annotations

import sqlite3


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
    provider_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    provider_kind    TEXT NOT NULL
                     CHECK (provider_kind IN ('mock', 'openai_compatible')),
    endpoint_url     TEXT,
    api_key_env_var  TEXT,
    default_model    TEXT,
    config_json      TEXT,
    is_enabled       INTEGER NOT NULL DEFAULT 1
                     CHECK (is_enabled IN (0, 1)),
    created_at       REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at       REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS roles (
    role_id          TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    description      TEXT NOT NULL DEFAULT '',
    system_prompt    TEXT NOT NULL DEFAULT '',
    is_builtin       INTEGER NOT NULL DEFAULT 0
                     CHECK (is_builtin IN (0, 1)),
    created_at       REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at       REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS session_participants (
    participant_id   TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL REFERENCES workspaces(workspace_id),
    session_id       TEXT NOT NULL REFERENCES session_extensions(session_id),
    binding_id       TEXT NOT NULL REFERENCES agent_profile_bindings(binding_id),
    role             TEXT NOT NULL DEFAULT 'member'
                     CHECK (role IN ('member', 'silent')),
    added_by         TEXT NOT NULL DEFAULT 'user'
                     CHECK (added_by IN ('user', 'orchestrator', 'system')),
    added_at         REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    removed_at       REAL
);

CREATE INDEX IF NOT EXISTS idx_session_participants_active
    ON session_participants(session_id, added_at)
    WHERE removed_at IS NULL;
"""


_SEED_SQL = """
INSERT OR IGNORE INTO providers (
    provider_id, name, provider_kind, endpoint_url, api_key_env_var,
    default_model, config_json, is_enabled
) VALUES (
    'provider-mock-default',
    'Mock Provider',
    'mock',
    NULL,
    NULL,
    'mock-model',
    '{"style": "local-demo"}',
    1
);

INSERT OR IGNORE INTO roles (role_id, name, description, system_prompt, is_builtin) VALUES
(
    'role-assistant',
    'assistant',
    'General chat assistant for collaborative sessions.',
    'You are a helpful collaborative AI assistant inside Agent Workbench. Respond clearly, practically, and stay on the current task.',
    1
),
(
    'role-researcher',
    'researcher',
    'Evidence-oriented research specialist.',
    'You are a research-focused AI assistant. Surface relevant facts, uncertainty, and concrete next steps.',
    1
),
(
    'role-critic',
    'critic',
    'Constructive skeptic who finds flaws and edge cases.',
    'You are a constructive critic. Stress-test assumptions, identify risks, and propose tighter alternatives.',
    1
),
(
    'role-implementer',
    'implementer',
    'Execution-oriented builder.',
    'You are an implementation-focused AI assistant. Prefer concrete actions, practical decomposition, and directly useful answers.',
    1
),
(
    'role-reviewer',
    'reviewer',
    'Review and validation specialist.',
    'You are a reviewing AI assistant. Evaluate correctness, completeness, and operational risk before approving changes.',
    1
);
"""


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    conn.executescript(_SEED_SQL)
    conn.commit()
