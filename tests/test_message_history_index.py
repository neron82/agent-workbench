from agent_workbench.db import apply_migrations, get_connection


def test_routed_message_history_has_keyset_index(tmp_path):
    db_path = tmp_path / "history-index.db"
    conn = get_connection(str(db_path))
    try:
        apply_migrations(conn)
        indexes = {
            row["name"]: row["sql"]
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'routed_messages'"
            )
        }
        assert "idx_routed_messages_session_history" in indexes
        sql = indexes["idx_routed_messages_session_history"].lower()
        assert "session_id" in sql
        assert "created_at" in sql
        assert "routed_message_id" in sql
    finally:
        conn.close()
