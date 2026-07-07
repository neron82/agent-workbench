"""Schema tests — base product tables plus chat/settings additions exist."""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# Canonical table → required-columns map (from 03_DOMAIN_MODEL.md)
# ---------------------------------------------------------------------------
EXPECTED_TABLES: dict[str, list[str]] = {
    "workspaces": [
        "workspace_id", "tenant_id", "name", "is_default", "created_at",
    ],
    "channels": [
        "channel_id", "workspace_id", "channel_kind", "title",
        "active_session_id", "default_target", "status",
        "created_at", "updated_at",
    ],
    "session_extensions": [
        "session_id", "workspace_id", "session_type",
        "agent_profile_binding_id", "fork_id", "task_spec_id", "status",
        "created_at",
    ],
    "fork_records": [
        "fork_id", "parent_session_id", "child_session_id", "fork_kind",
        "fork_reason", "initiated_by", "summary_ref",
        "decisions_json", "assumptions_json", "open_questions_json",
        "relevant_artifacts_json", "bootstrap_context_role_internal",
        "checkpoint_json", "created_at",
    ],
    "agent_profiles": [
        "agent_profile_id", "name", "version", "provider_ref",
        "model_ref", "perspective_ref", "function_ref", "harness_ref",
        "permissions_policy_ref", "capability_hints_json",
        "created_at", "updated_at",
    ],
    "providers": [
        "provider_id", "name", "provider_kind", "endpoint_url",
        "api_key_env_var", "default_model", "config_json", "is_enabled",
        "created_at", "updated_at",
    ],
    "roles": [
        "role_id", "name", "description", "system_prompt", "is_builtin",
        "created_at", "updated_at",
    ],
    "agent_profile_bindings": [
        "binding_id", "session_id", "agent_profile_id",
        "binding_version", "created_from", "created_at",
    ],
    "session_participants": [
        "participant_id", "workspace_id", "session_id", "binding_id",
        "role", "added_by", "added_at", "removed_at",
    ],
    "harness_runs": [
        "harness_run_id", "workspace_id", "session_id", "task_spec_id",
        "harness_type", "runtime_session_id", "runtime_process_id",
        "runtime_remote_process_id", "status",
        "control_capabilities_json", "artifact_summary_json",
        "started_at", "ended_at",
    ],
    "task_specs": [
        "task_spec_id", "workspace_id", "source_session_id", "objective",
        "scope_in_json", "scope_out_json", "acceptance_criteria_json",
        "constraints_json", "risk_level", "approval_status",
        "created_at", "updated_at",
    ],
    "routed_messages": [
        "routed_message_id", "workspace_id", "session_id", "channel_id",
        "source_type", "source_id", "target_type", "target_id",
        "message_kind", "payload_ref", "created_at",
    ],
    "event_records": [
        "event_id", "harness_run_id", "routed_message_id",
        "event_type", "event_source", "event_payload_ref", "event_ts",
    ],
    "permission_requests": [
        "permission_request_id", "harness_run_id", "scope", "reason",
        "requested_action", "requested_by", "decision",
        "escalated_from_auto_approve", "created_at", "decided_at",
    ],
    "artifacts": [
        "artifact_id", "workspace_id", "producer_session_id",
        "producer_harness_run_id", "task_spec_id", "artifact_kind",
        "title", "content_ref", "content_hash",
        "predecessor_artifact_id", "created_at",
    ],
    "review_records": [
        "review_id", "workspace_id", "target_kind", "target_id",
        "reviewer_binding_id", "verdict", "findings_ref",
        "criteria_eval_json", "created_at",
    ],
    "replay_records": [
        "replay_id", "source_session_id", "source_harness_run_id",
        "fork_id", "checkpoint_json", "replay_scope",
        "equivalence_rule", "outcome", "created_at",
    ],
}


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def test_all_tables_exist(db: sqlite3.Connection) -> None:
    """All expected product tables must be present after migrations."""
    tables = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    tables.discard("_migrations")
    for expected in EXPECTED_TABLES:
        assert expected in tables, f"Missing table: {expected}"


def test_table_columns(db: sqlite3.Connection) -> None:
    """Each table must contain its expected columns."""
    for table, expected_cols in EXPECTED_TABLES.items():
        actual = set(_table_columns(db, table))
        for col in expected_cols:
            assert col in actual, f"{table} missing column {col!r}"


def test_enum_check_constraints(db: sqlite3.Connection) -> None:
    """Verify CHECK constraints exist on enum columns by inserting bad values."""
    import pytest

    # channels.channel_kind
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO channels (channel_id, workspace_id, channel_kind, title) "
            "VALUES ('x','ws','invalid','t')"
        )
        db.commit()

    # session_extensions.session_type
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO workspaces (workspace_id, tenant_id, name) VALUES ('ws','','w')"
        )
        db.commit()
        db.execute(
            "INSERT INTO session_extensions (session_id, workspace_id, session_type) "
            "VALUES ('s','ws','invalid')"
        )
        db.commit()

    # harness_runs.harness_type
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO harness_runs "
            "(harness_run_id, workspace_id, session_id, harness_type) "
            "VALUES ('hr','ws','s','invalid')"
        )
        db.commit()

    # routed_messages.message_kind
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO routed_messages "
            "(routed_message_id, workspace_id, channel_id, source_type, source_id, "
            "target_type, target_id, message_kind) "
            "VALUES ('rm','ws','ch','src','sid','tgt','tid','invalid')"
        )
        db.commit()

    # permission_requests.scope
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO permission_requests "
            "(permission_request_id, harness_run_id, scope, requested_action, requested_by) "
            "VALUES ('pr','hr','invalid','act','by')"
        )
        db.commit()

    # review_records.verdict
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO review_records "
            "(review_id, workspace_id, target_kind, target_id, verdict) "
            "VALUES ('rv','ws','session','s','invalid')"
        )
        db.commit()

    # replay_records.outcome
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO replay_records "
            "(replay_id, source_session_id, fork_id, outcome) "
            "VALUES ('rp','s','fk','invalid')"
        )
        db.commit()

    db.close()


def test_primary_keys_are_text(db: sqlite3.Connection) -> None:
    """All primary key columns should be TEXT type."""
    for table in EXPECTED_TABLES:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            if r["pk"]:
                assert r["type"].upper() == "TEXT", (
                    f"{table}.{r['name']} PK is {r['type']}, expected TEXT"
                )


def test_workspace_id_and_tenant_id_coverage(db: sqlite3.Connection) -> None:
    """Tables that require workspace_id/tenant_id per the domain model."""
    # workspace_id on: channels, session_extensions, harness_runs,
    # task_specs, routed_messages, artifacts, review_records
    workspace_tables = [
        "channels", "session_extensions", "session_participants", "harness_runs",
        "task_specs", "routed_messages", "artifacts", "review_records",
    ]
    for table in workspace_tables:
        cols = set(_table_columns(db, table))
        assert "workspace_id" in cols, f"{table} missing workspace_id"

    # tenant_id on workspaces
    assert "tenant_id" in set(_table_columns(db, "workspaces"))
