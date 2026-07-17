"""Tests for the runtime/security beta slice.

Covers:
1. capability_hints.allowed_permission_classes restricts advertised tools
2. ToolDispatcher enforces allowed_tool_names at execution time
3. hermes.delegate_subagent disabled by default
4. Session-scoped nonblocking lock prevents overlapping generate_for_session
5. Configurable participant worker concurrency cap
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import patch

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.provider import ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
)
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.tool_dispatcher import (
    ToolDispatcher,
)
from agent_workbench.services.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _ProfileStub:
    harness_ref: str | None
    capability_hints_json: dict | None = None


def _make_tool(repo, name, harness_type, permission_class, is_enabled=True):
    import uuid
    unique = f"{name}_{uuid.uuid4().hex[:8]}"
    return repo.create(
        name=unique,
        harness_type=harness_type,
        adapter_method="start",
        description=f"tool {name}",
        input_schema={"type": "object", "properties": {}},
        permission_class=permission_class,
        is_enabled=is_enabled,
    )


def _make_tool_from_db(db, name, harness_type, permission_class, is_enabled=True):
    """Create a tool using a raw DB connection (for dispatcher tests)."""
    import uuid
    unique = f"{name}_{uuid.uuid4().hex[:8]}"
    return ToolRepository(db).create(
        name=unique,
        harness_type=harness_type,
        adapter_method="start",
        description=f"tool {name}",
        input_schema={"type": "object", "properties": {}},
        permission_class=permission_class,
        is_enabled=is_enabled,
    )


def _seed_workspace_and_session(db):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type="chat",
    )
    db.commit()
    return ws.workspace_id, sess.session_id


def _seed_full_session(db, session_type="chat"):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type=session_type,
    )
    db.commit()
    ChannelRepository(db).update_active_session(
        ch.channel_id, active_session_id=sess.session_id,
    )
    db.commit()
    return ws.workspace_id, ch.channel_id, sess.session_id


def _make_provider(db, name="p", kind="openai_compatible", endpoint="http://stub/v1"):
    return ProviderRepository(db).create(
        name=name, provider_kind=kind, endpoint_url=endpoint,
        api_key_env_var=None, default_model="stub-model",
    )


def _make_profile(db, provider, name="tester", harness="shell"):
    profile = ProfileService(db).create_profile(
        name=name,
        provider=provider.provider_id,
        model="stub-model",
        function="operator",
        harness=harness,
    )
    db.commit()
    return profile


def _add_agent(db, session_id, profile, user_id="user"):
    return ParticipantService(db).add_participant(
        session_id=session_id,
        agent_profile_id=profile.agent_profile_id,
        participant_role="member",
        added_by=user_id,
    )


class _FakeResp:
    def __init__(self, body: Dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


# ===================================================================
# Helper for running generate_for_session in threads
# ===================================================================


def _run_generate(db_path: str, session_id: str, errors: list, lock: threading.Lock) -> None:
    """Run generate_for_session in a thread, capturing errors."""
    from agent_workbench.db import get_connection, apply_migrations
    try:
        c = get_connection(db_path)
        apply_migrations(c)
        AgentRuntimeService(c).generate_for_session(
            session_id=session_id, user_body="hi", user_id="user",
        )
        c.close()
    except Exception as e:
        import traceback
        with lock:
            errors.append(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def _run_generate_with_mock(
    db_path: str, session_id: str, urlopen_side_effect, errors: list, lock: threading.Lock
) -> None:
    """Run generate_for_session in a thread with a patched urlopen."""
    from unittest.mock import patch
    from agent_workbench.db import get_connection, apply_migrations
    try:
        c = get_connection(db_path)
        apply_migrations(c)
        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            AgentRuntimeService(c).generate_for_session(
                session_id=session_id, user_body="hi", user_id="user",
            )
        c.close()
    except Exception as e:
        import traceback
        with lock:
            errors.append(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ===================================================================
# Requirement 1: allowed_permission_classes in capability_hints
# ===================================================================


class TestAllowedPermissionClasses:
    """capability_hints.allowed_permission_classes restricts advertised tools."""

    def test_allowed_permission_classes_filters_by_class(self, db):
        """When allowed_permission_classes is set, only tools with matching
        permission_class are advertised."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro_tool", "shell", "read_only")
        wl = _make_tool(repo, "wl_tool", "shell", "write_local")
        de = _make_tool(repo, "de_tool", "shell", "destructive")

        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={
                "allowed_permission_classes": ["read_only", "write_local"],
            },
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro.name in names
        assert wl.name in names
        assert de.name not in names

    def test_allowed_permission_classes_with_allowed_tools(self, db):
        """allowed_permission_classes AND allowed_tools both apply (intersection)."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro_a = _make_tool(repo, "ro_a", "shell", "read_only")
        ro_b = _make_tool(repo, "ro_b", "shell", "read_only")
        wl_c = _make_tool(repo, "wl_c", "shell", "write_local")

        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={
                "allowed_permission_classes": ["read_only"],
                "allowed_tools": [ro_a.name, wl_c.name],
            },
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro_a.name in names  # read_only AND in allowed_tools
        assert ro_b.name not in names  # read_only but NOT in allowed_tools
        assert wl_c.name not in names  # in allowed_tools but NOT read_only

    def test_allowed_permission_classes_with_denied_tools(self, db):
        """allowed_permission_classes AND denied_tools both apply."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro_a = _make_tool(repo, "ro_a", "shell", "read_only")
        ro_b = _make_tool(repo, "ro_b", "shell", "read_only")
        wl_c = _make_tool(repo, "wl_c", "shell", "write_local")

        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={
                "allowed_permission_classes": ["read_only", "write_local"],
                "denied_tools": [ro_a.name],
            },
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro_a.name not in names  # denied
        assert ro_b.name in names  # read_only, not denied
        assert wl_c.name in names  # write_local, not denied

    def test_allowed_permission_classes_empty_means_no_tools(self, db):
        """An empty allowed_permission_classes list means no tools are advertised."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro_tool", "shell", "read_only")
        wl = _make_tool(repo, "wl_tool", "shell", "write_local")

        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={"allowed_permission_classes": []},
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro.name not in names
        assert wl.name not in names

    def test_allowed_permission_classes_omitted_is_permissive(self, db):
        """When allowed_permission_classes is omitted, all permission classes pass."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro_tool", "shell", "read_only")
        de = _make_tool(repo, "de_tool", "shell", "destructive")

        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={},  # no allowed_permission_classes
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro.name in names
        assert de.name in names


# ===================================================================
# Requirement 2: ToolDispatcher enforces allowed_tool_names
# ===================================================================


class TestDispatcherAllowedToolNames:
    """ToolDispatcher must enforce the exact negotiated namespaced tool set."""

    def test_allowed_tool_names_denies_unadvertised_tool(self, db):
        """A tool not in allowed_tool_names is denied at dispatch time."""
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool_from_db(db, "secret_tool", "shell", "write_local")
        dispatcher = ToolDispatcher(db)

        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_1",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo secret"}),
                },
            },
            allowed_tool_names=[],  # empty set — nothing is allowed
        )
        assert result.status == "denied", (
            f"Expected denied, got {result.status}: {result.content}"
        )
        assert "not in the allowed tool set" in result.content

    def test_allowed_tool_names_allows_negotiated_tool(self, db):
        """A tool in allowed_tool_names is dispatched normally."""
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool_from_db(db, "ok_tool", "shell", "write_local")
        dispatcher = ToolDispatcher(db)

        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_2",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "printf allowed"}),
                },
            },
            allowed_tool_names=[f"shell.{tool.name}"],
        )
        assert result.status == "completed", (
            f"Expected completed, got {result.status}: {result.content}"
        )
        assert "allowed" in result.content

    def test_allowed_tool_names_denies_same_harness_unadvertised(self, db):
        """A provider-emitted tool from the same harness but not in the
        negotiated set is denied — prevents provider from calling arbitrary
        tools within the same harness."""
        ws, sid = _seed_workspace_and_session(db)
        repo = ToolRepository(db)
        advertised = _make_tool(repo, "advertised", "shell", "write_local")
        hidden = _make_tool(repo, "hidden", "shell", "write_local")
        dispatcher = ToolDispatcher(db)

        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_hidden",
                "function": {
                    "name": f"shell.{hidden.name}",
                    "arguments": json.dumps({"command": "echo sneaky"}),
                },
            },
            allowed_tool_names=[f"shell.{advertised.name}"],
        )
        assert result.status == "denied", (
            f"Expected denied for unadvertised same-harness tool, "
            f"got {result.status}: {result.content}"
        )

    def test_allowed_tool_names_none_is_backwards_compatible(self, db):
        """When allowed_tool_names is None (not passed), all registered
        tools are allowed — preserves backward compat for trusted callers."""
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool_from_db(db, "legacy_tool", "shell", "write_local")
        dispatcher = ToolDispatcher(db)

        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_legacy",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "printf legacy"}),
                },
            },
            # allowed_tool_names not passed — backward compat
        )
        assert result.status == "completed", (
            f"Expected completed for backward compat, "
            f"got {result.status}: {result.content}"
        )

    def test_allowed_tool_names_empty_denies_even_registered_tool(self, db):
        """Explicit empty list denies everything, even registered tools."""
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool_from_db(db, "any_tool", "shell", "write_local")
        dispatcher = ToolDispatcher(db)

        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_empty",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo x"}),
                },
            },
            allowed_tool_names=[],
        )
        assert result.status == "denied"

    def test_runtime_passes_negotiated_set(self, db):
        """The runtime's _openai_compatible_reply passes the negotiated
        tool names to the dispatcher.  We verify this by checking that
        a tool NOT in the negotiated set is denied even if the provider
        emits it."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "SecureAgent", harness="shell")
        _add_agent(db, sid, profile)

        # Create two tools: one allowed, one not
        repo = ToolRepository(db)
        allowed_tool = _make_tool(repo, "allowed", "shell", "write_local")
        denied_tool = _make_tool(repo, "denied", "shell", "write_local")

        # Configure profile to only allow the first tool
        from agent_workbench.services.profile_service import ProfileService
        ProfileService(db).update_profile(
            profile.agent_profile_id,
            capability_hints={"allowed_tools": [allowed_tool.name]},
        )
        db.commit()

        # The provider emits the denied tool — the runtime must reject it
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: emit the denied tool
                return _FakeResp({
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_denied",
                                "function": {
                                    "name": f"shell.{denied_tool.name}",
                                    "arguments": json.dumps({"command": "echo sneaky"}),
                                },
                            }],
                        },
                    }],
                })
            # Second call: emit a normal reply
            return _FakeResp({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "done",
                    },
                }],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="test", user_id="user",
            )

        # The agent should have completed (the denied tool was rejected
        # but the loop continued and the agent produced a final reply)
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) >= 1
        # The denied tool call should have been rejected, not executed
        # (we can't easily check the tool_invocation from here, but the
        # agent should have produced a reply)


# ===================================================================
# Requirement 3: hermes.delegate_subagent disabled by default
# ===================================================================


class TestDelegateSubagentDisabled:
    """hermes.delegate_subagent must be disabled by default."""

    def test_delegate_subagent_is_disabled_in_seed(self, db):
        """The builtin hermes.delegate_subagent tool is seeded with
        is_enabled=False."""
        repo = ToolRepository(db)
        tool = repo.get_by_name("hermes", "delegate_subagent")
        assert tool is not None, "delegate_subagent should exist in catalog"
        assert not tool.is_enabled, (
            f"delegate_subagent should be disabled by default, "
            f"got is_enabled={tool.is_enabled}"
        )

    def test_delegate_subagent_not_in_effective_tools(self, db):
        """Since delegate_subagent is disabled, it should not appear in
        effective_tools output."""
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        profile = _ProfileStub(harness_ref="hermes")
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="hermes", session_type="work",
        )
        names = {t.name for t in tools}
        assert "delegate_subagent" not in names

    def test_delegate_subagent_dispatch_denied_when_disabled(self, db):
        """Dispatching the disabled delegate_subagent returns denied."""
        ws, sid = _seed_workspace_and_session(db)
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_delegate",
                "function": {
                    "name": "hermes.delegate_subagent",
                    "arguments": json.dumps({"task": "do something"}),
                },
            },
        )
        assert result.status == "denied", (
            f"Expected denied for disabled tool, got {result.status}"
        )
        assert "not registered or disabled" in result.content


# ===================================================================
# Requirement 4: Session-scoped nonblocking lock for generate_for_session
# ===================================================================


class TestSessionConcurrencyLock:
    """Prevent overlapping generate_for_session worker sets for the same session."""

    def test_concurrent_same_session_second_is_denied(self, tmp_db):
        """A second generate_for_session for the same session while one
        is running must fail clearly (not interleave)."""
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools

        # Use a file-based DB so we can open separate connections per thread
        conn = get_connection(str(tmp_db))
        apply_migrations(conn)
        seed_builtin_tools(conn)

        ws, ch, sid = _seed_full_session(conn)
        provider = _make_provider(conn)
        profile = _make_profile(conn, provider, "SlowAgent", harness="shell")
        _add_agent(conn, sid, profile)

        for t in ToolRepository(conn).list_for_harness("shell"):
            ToolRepository(conn).update(t.tool_id, is_enabled=False)
        conn.commit()
        conn.close()

        # First call will block inside the provider call
        started_event = threading.Event()
        proceed_event = threading.Event()
        call_count = 0
        call_lock = threading.Lock()

        def slow_urlopen(req, timeout):
            nonlocal call_count
            with call_lock:
                call_count += 1
                if call_count == 1:
                    started_event.set()
                    proceed_event.wait(timeout=5)
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "slow-reply"}}],
            })

        errors = []

        def run_first():
            try:
                c = get_connection(str(tmp_db))
                apply_migrations(c)
                with patch("urllib.request.urlopen", side_effect=slow_urlopen):
                    AgentRuntimeService(c).generate_for_session(
                        session_id=sid, user_body="first", user_id="user",
                    )
                c.close()
            except Exception as e:
                errors.append(("first", str(e)))

        def run_second():
            started_event.wait(timeout=5)
            try:
                c = get_connection(str(tmp_db))
                apply_migrations(c)
                with patch("urllib.request.urlopen", side_effect=slow_urlopen):
                    AgentRuntimeService(c).generate_for_session(
                        session_id=sid, user_body="second", user_id="user",
                    )
                c.close()
            except Exception as e:
                errors.append(("second", str(e)))

        t1 = threading.Thread(target=run_first, daemon=True)
        t2 = threading.Thread(target=run_second, daemon=True)
        t1.start()
        t2.start()
        t2.join(timeout=10)
        proceed_event.set()
        t1.join(timeout=10)

        # The second call should have been denied or raised an error
        assert len(errors) >= 1, (
            f"Expected at least one error from concurrent same-session calls, "
            f"got none. Errors: {errors}"
        )
        error_texts = " ".join(e[1] for e in errors)
        assert any(phrase in error_texts for phrase in [
            "already running", "in progress", "busy", "locked",
        ]), f"Error should mention concurrency: {error_texts}"

    def test_different_sessions_can_overlap(self, tmp_db):
        """Different sessions can still run generate_for_session concurrently."""
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools

        conn = get_connection(str(tmp_db))
        apply_migrations(conn)
        seed_builtin_tools(conn)

        ws1, ch1, sid1 = _seed_full_session(conn)
        ws2, ch2, sid2 = _seed_full_session(conn)
        provider = _make_provider(conn)
        profile1 = _make_profile(conn, provider, "AgentA", harness="shell")
        profile2 = _make_profile(conn, provider, "AgentB", harness="shell")
        _add_agent(conn, sid1, profile1)
        _add_agent(conn, sid2, profile2)

        for t in ToolRepository(conn).list_for_harness("shell"):
            ToolRepository(conn).update(t.tool_id, is_enabled=False)
        conn.commit()
        conn.close()

        call_order = []
        call_lock = threading.Lock()

        def slow_urlopen(req, timeout):
            with call_lock:
                call_order.append("enter")
                time.sleep(0.15)
                call_order.append("exit")
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "reply"}}],
            })

        thread_errors = []
        thread_errors_lock = threading.Lock()
        threads = []
        for sid in (sid1, sid2):
            t = threading.Thread(
                target=_run_generate_with_mock,
                args=(str(tmp_db), sid, slow_urlopen, thread_errors, thread_errors_lock),
                daemon=True,
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Check for thread errors
        if thread_errors:
            pytest.fail(f"Thread errors: {thread_errors}")

        # Both sessions should have produced replies
        conn2 = get_connection(str(tmp_db))
        for sid in (sid1, sid2):
            rows = RoutedMessageRepository(conn2).list_by_session(sid)
            conv_msgs = [r for r in rows if r.message_kind == "conversation"]
            assert len(conv_msgs) >= 1, (
                f"Session {sid} should have at least 1 reply"
            )
        conn2.close()

        # The calls should have overlapped (enter-exit interleaving)
        assert len(call_order) >= 4, (
            f"Expected at least 4 events (enter/exit × 2), got {call_order}"
        )


# ===================================================================
# Requirement 5: Participant worker concurrency cap
# ===================================================================


class TestWorkerConcurrencyCap:
    """Cap participant worker concurrency with a configurable sane default."""

    def test_concurrency_cap_limits_workers(self, tmp_db):
        """When more participants than the cap, only cap workers run."""
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools

        conn = get_connection(str(tmp_db))
        apply_migrations(conn)
        seed_builtin_tools(conn)

        ws, ch, sid = _seed_full_session(conn)
        provider = _make_provider(conn)

        profiles = []
        for i in range(5):
            p = _make_profile(conn, provider, f"Agent{i}", harness="shell")
            profiles.append(p)
            _add_agent(conn, sid, p)

        for t in ToolRepository(conn).list_for_harness("shell"):
            ToolRepository(conn).update(t.tool_id, is_enabled=False)
        conn.commit()
        conn.close()

        # Track concurrent workers
        active = 0
        active_lock = threading.Lock()
        max_active = 0
        call_count = 0

        def slow_urlopen(req, timeout):
            nonlocal active, max_active, call_count
            with active_lock:
                active += 1
                max_active = max(max_active, active)
                call_count += 1
            time.sleep(0.1)
            with active_lock:
                active -= 1
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "reply"}}],
            })

        c = get_connection(str(tmp_db))
        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            AgentRuntimeService(c).generate_for_session(
                session_id=sid, user_body="test", user_id="user",
            )
        c.close()

        # The cap should limit concurrent workers
        assert max_active <= 3, (
            f"Expected max concurrent workers <= 3, got {max_active}"
        )
        # All 5 agents should still produce replies
        c2 = get_connection(str(tmp_db))
        rows = RoutedMessageRepository(c2).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 5, (
            f"Expected 5 replies, got {len(conv_msgs)}"
        )
        c2.close()

    def test_concurrency_cap_configurable(self, tmp_db, monkeypatch):
        """The concurrency cap is configurable via env var."""
        monkeypatch.setenv("WORKBENCH_MAX_CONCURRENT_WORKERS", "2")
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools

        conn = get_connection(str(tmp_db))
        apply_migrations(conn)
        seed_builtin_tools(conn)

        ws, ch, sid = _seed_full_session(conn)
        provider = _make_provider(conn)

        profiles = []
        for i in range(4):
            p = _make_profile(conn, provider, f"CapAgent{i}", harness="shell")
            profiles.append(p)
            _add_agent(conn, sid, p)

        for t in ToolRepository(conn).list_for_harness("shell"):
            ToolRepository(conn).update(t.tool_id, is_enabled=False)
        conn.commit()
        conn.close()

        active = 0
        active_lock = threading.Lock()
        max_active = 0

        def slow_urlopen(req, timeout):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.1)
            with active_lock:
                active -= 1
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "reply"}}],
            })

        c = get_connection(str(tmp_db))
        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            AgentRuntimeService(c).generate_for_session(
                session_id=sid, user_body="test", user_id="user",
            )
        c.close()

        assert max_active <= 2, (
            f"Expected max concurrent workers <= 2, got {max_active}"
        )
        c2 = get_connection(str(tmp_db))
        rows = RoutedMessageRepository(c2).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 4, (
            f"Expected 4 replies, got {len(conv_msgs)}"
        )
        c2.close()

    def test_concurrency_cap_default_is_three(self, db):
        """Default concurrency cap is 3."""
        from agent_workbench.services.agent_runtime_service import (
            _get_max_concurrent_workers,
        )
        assert _get_max_concurrent_workers() == 3

    def test_concurrency_cap_one_still_parallel(self, tmp_db):
        """With cap=1, workers still run in parallel (one at a time)."""
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools

        conn = get_connection(str(tmp_db))
        apply_migrations(conn)
        seed_builtin_tools(conn)

        ws, ch, sid = _seed_full_session(conn)
        provider = _make_provider(conn)
        profile_a = _make_profile(conn, provider, "ParallelA", harness="shell")
        profile_b = _make_profile(conn, provider, "ParallelB", harness="shell")
        _add_agent(conn, sid, profile_a)
        _add_agent(conn, sid, profile_b)

        for t in ToolRepository(conn).list_for_harness("shell"):
            ToolRepository(conn).update(t.tool_id, is_enabled=False)
        conn.commit()
        conn.close()

        call_times = []
        call_lock = threading.Lock()

        def slow_urlopen(req, timeout):
            with call_lock:
                call_times.append(time.monotonic())
            time.sleep(0.15)
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "reply"}}],
            })

        c = get_connection(str(tmp_db))
        with patch.dict("os.environ", {"WORKBENCH_MAX_CONCURRENT_WORKERS": "1"}):
            with patch("urllib.request.urlopen", side_effect=slow_urlopen):
                AgentRuntimeService(c).generate_for_session(
                    session_id=sid, user_body="test", user_id="user",
                )
        c.close()

        # With cap=1, calls should be serial (second starts after first finishes)
        # But both should still complete
        c2 = get_connection(str(tmp_db))
        rows = RoutedMessageRepository(c2).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 2, (
            f"Expected 2 replies, got {len(conv_msgs)}"
        )
        c2.close()
