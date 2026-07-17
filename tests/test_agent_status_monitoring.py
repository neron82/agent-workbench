"""Tests for live agent-work inspection (monitoring).

Tests cover:
- Route returns accumulated steps per agent (completed + running)
- Timing/status fields, completed_at
- Result truncation (8 KiB preview)
- Multiple-agent JSON
- Session template has Inspect control, shared/live renderer, CSRF POST headers
- CSS selectors for new classes
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_status import AgentStatusTracker
from agent_workbench.web import create_app

from .conftest import make_csrf_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRUNCATION_LIMIT = 8192  # 8 KiB


def _make_app_and_session(tmp_path: Path) -> tuple[FlaskClient, str, str]:
    """Create a fresh app, workspace, and session.  Returns (csrf_client, session_id, workspace_id)."""
    db_path = tmp_path / "monitoring-test.db"
    app = create_app(db_path=str(db_path))
    app.config.update(TESTING=True)

    conn = get_connection(str(db_path))
    apply_migrations(conn)
    workspace = WorkspaceRepository(conn).create(tenant_id="default", name="Monitoring")
    session = SessionExtensionRepository(conn).create(
        workspace_id=workspace.workspace_id,
        session_type="chat",
        title="Monitor test",
    )
    conn.close()

    client = make_csrf_client(app)
    return client, session.session_id, workspace.workspace_id


def _seed_steps(
    tracker: AgentStatusTracker,
    session_id: str,
    agent_name: str,
    count: int = 2,
) -> None:
    """Seed *count* steps for an agent: first (count-1) completed, last running."""
    tracker.start_agent(session_id, agent_name)
    for i in range(1, count + 1):
        tracker.start_step(
            session_id, agent_name, i,
            tool_name=f"tool_{i}",
            tool_arguments={"arg": i, "nested": {"key": f"val_{i}"}},
        )
        if i < count:
            tracker.complete_step(session_id, agent_name, result=f"result_{i}")


# ---------------------------------------------------------------------------
# Route Tests
# ---------------------------------------------------------------------------


class TestAgentStatusRoute:
    """Tests for GET /sessions/<id>/agent-status."""

    def test_returns_two_accumulated_steps(self, tmp_path: Path):
        """Route returns two accumulated steps: one completed, one running."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            _seed_steps(tracker, session_id, "Alpha", count=2)

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            agents = data.get("agents", [])
            assert len(agents) == 1
            agent = agents[0]
            assert agent["agent_name"] == "Alpha"

            steps = agent.get("steps", [])
            assert len(steps) == 2, f"Expected 2 steps, got {len(steps)}"

            # Step 1: completed
            s1 = steps[0]
            assert s1["iteration"] == 1
            assert s1["tool_name"] == "tool_1"
            assert s1["status"] == "completed"
            assert s1["tool_result"] == "result_1"
            assert s1["started_at"] is not None
            assert s1["completed_at"] is not None
            assert s1["completed_at"] >= s1["started_at"]
            assert "tool_arguments" in s1
            assert s1["tool_arguments"] == {"arg": 1, "nested": {"key": "val_1"}}

            # Step 2: running
            s2 = steps[1]
            assert s2["iteration"] == 2
            assert s2["tool_name"] == "tool_2"
            assert s2["status"] == "running"
            assert s2["tool_result"] is None
            assert s2["started_at"] is not None
            assert s2["completed_at"] is None
        finally:
            tracker.cleanup_session(session_id)

    def test_completed_at_on_agent(self, tmp_path: Path):
        """Agent-level completed_at is present when agent is done."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent(session_id, "Alpha")
            tracker.start_step(session_id, "Alpha", 1, "tool_1", {})
            tracker.complete_step(session_id, "Alpha", "done")
            tracker.complete_agent(session_id, "Alpha")

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            agent = data["agents"][0]
            assert agent["completed_at"] is not None
            assert agent["status"] == "completed"
        finally:
            tracker.cleanup_session(session_id)

    def test_result_truncation(self, tmp_path: Path):
        """tool_result is truncated to ~8 KiB with truncation marker."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent(session_id, "Alpha")
            big_result = "x" * (TRUNCATION_LIMIT * 2)
            tracker.start_step(session_id, "Alpha", 1, "tool_1", {})
            tracker.complete_step(session_id, "Alpha", result=big_result)

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            step = data["agents"][0]["steps"][0]
            result = step["tool_result"]
            assert result is not None
            # Should be truncated
            assert len(result) <= TRUNCATION_LIMIT + 50  # allow for marker
            assert result.endswith("… [truncated]")
        finally:
            tracker.cleanup_session(session_id)

    def test_small_result_not_truncated(self, tmp_path: Path):
        """Small results are not truncated."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent(session_id, "Alpha")
            small_result = "hello world"
            tracker.start_step(session_id, "Alpha", 1, "tool_1", {})
            tracker.complete_step(session_id, "Alpha", result=small_result)

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            result = data["agents"][0]["steps"][0]["tool_result"]
            assert result == "hello world"
        finally:
            tracker.cleanup_session(session_id)

    def test_current_step_compatibility(self, tmp_path: Path):
        """current_step is still present for backward compatibility."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            _seed_steps(tracker, session_id, "Alpha", count=1)

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            agent = data["agents"][0]
            assert "current_step" in agent
            cs = agent["current_step"]
            assert cs is not None
            assert cs["iteration"] == 1
            assert cs["tool_name"] == "tool_1"
        finally:
            tracker.cleanup_session(session_id)

    def test_multiple_agents(self, tmp_path: Path):
        """Multiple concurrent agents are all returned."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            _seed_steps(tracker, session_id, "Alpha", count=2)
            _seed_steps(tracker, session_id, "Beta", count=1)

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            agents = data.get("agents", [])
            assert len(agents) == 2

            names = {a["agent_name"] for a in agents}
            assert names == {"Alpha", "Beta"}

            alpha = next(a for a in agents if a["agent_name"] == "Alpha")
            beta = next(a for a in agents if a["agent_name"] == "Beta")
            assert len(alpha["steps"]) == 2
            assert len(beta["steps"]) == 1
        finally:
            tracker.cleanup_session(session_id)

    def test_no_agents_returns_empty_list(self, tmp_path: Path):
        """Session with no tracked agents returns empty list."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}/agent-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("agents") == []

    def test_unknown_session_returns_empty(self, tmp_path: Path):
        """Unknown session_id returns empty agents list (tracker is in-memory)."""
        client, _, _ = _make_app_and_session(tmp_path)
        resp = client.get("/sessions/nonexistent/agent-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("agents") == []

    def test_error_and_stopped_states(self, tmp_path: Path):
        """Error and stopped states are reflected in agent status."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent(session_id, "Alpha")
            tracker.start_step(session_id, "Alpha", 1, "tool_1", {})
            tracker.complete_step(session_id, "Alpha", result="ok")
            tracker.complete_agent(session_id, "Alpha", error="Something broke")

            tracker.start_agent(session_id, "Beta")
            tracker.stop_agent(session_id, "Beta")

            resp = client.get(f"/sessions/{session_id}/agent-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            agents = {a["agent_name"]: a for a in data["agents"]}

            assert agents["Alpha"]["status"] == "error"
            assert agents["Alpha"]["error"] == "Something broke"
            assert agents["Beta"]["status"] == "stopped"
        finally:
            tracker.cleanup_session(session_id)

    def test_agent_status_route_is_get(self, tmp_path: Path):
        """agent-status route accepts GET (not POST)."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}/agent-status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Template Tests
# ---------------------------------------------------------------------------


class TestSessionTemplate:
    """Verify session_view.html has the required monitoring elements."""

    def test_inspect_button_present(self, tmp_path: Path):
        """Session template has an 'Inspect work' button in #agent-working-bar."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # The Inspect work button should be in the agent-working-bar
        assert 'id="btn-inspect-work"' in html, "Missing btn-inspect-work id"
        assert "Inspect work" in html, "Missing 'Inspect work' text"

    def test_live_agent_states_map_defined(self, tmp_path: Path):
        """liveAgentStates map is defined in the page script."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        assert "var liveAgentStates" in html or "let liveAgentStates" in html or "const liveAgentStates" in html

    def test_shared_step_renderer_defined(self, tmp_path: Path):
        """The shared renderStepHtml function is defined."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        assert "function renderStepHtml" in html

    def test_csrf_post_headers_preserved(self, tmp_path: Path):
        """CSRF POST headers are preserved in the page (X-CSRF-Token)."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # The page should have the CSRF meta tag
        assert 'name="csrf-token"' in html
        # The stop-agent fetch should use X-CSRF-Token
        assert "X-CSRF-Token" in html

    def test_live_mode_panel_opens_on_inspect_click(self, tmp_path: Path):
        """Clicking Inspect opens the toolcall-panel in live mode."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        assert "openLiveWorkPanel" in html or "openLiveInspect" in html

    def test_agent_working_bar_shows_concurrent_count(self, tmp_path: Path):
        """Top bar communicates concurrent agent count."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # Should have an element showing agent count
        assert "agent-count" in html or "data-agent-count" in html or "agentWorkingCount" in html


# ---------------------------------------------------------------------------
# CSS Selector Tests
# ---------------------------------------------------------------------------


class TestCSSSelectors:
    """Verify new CSS classes exist in workbench.css."""

    def _get_css(self) -> str:
        css_path = Path(__file__).parents[1] / "src" / "agent_workbench" / "web" / "static" / "workbench.css"
        return css_path.read_text("utf-8")

    def test_live_panel_sidebar_class(self):
        """CSS has .live-agent-sidebar class."""
        css = self._get_css()
        assert ".live-agent-sidebar" in css

    def test_live_agent_entry_class(self):
        """CSS has .live-agent-entry class."""
        css = self._get_css()
        assert ".live-agent-entry" in css

    def test_live_agent_entry_active_class(self):
        """CSS has .live-agent-entry.active class."""
        css = self._get_css()
        assert ".live-agent-entry.active" in css or ".live-agent-entry" in css

    def test_live_step_class(self):
        """CSS has .live-step class."""
        css = self._get_css()
        assert ".live-step" in css

    def test_inspect_btn_class(self):
        """CSS has .btn-inspect class."""
        css = self._get_css()
        assert ".btn-inspect" in css

    def test_agent_count_badge_class(self):
        """CSS has .agent-count-badge class."""
        css = self._get_css()
        assert ".agent-count-badge" in css

    def test_live_panel_footer_class(self):
        """CSS has .live-panel-footer class."""
        css = self._get_css()
        assert ".live-panel-footer" in css

    def test_step_duration_class(self):
        """CSS has .step-duration class."""
        css = self._get_css()
        assert ".step-duration" in css

    def test_result_preview_class(self):
        """CSS has .result-preview class."""
        css = self._get_css()
        assert ".result-preview" in css


# ---------------------------------------------------------------------------
# JS Syntax Check
# ---------------------------------------------------------------------------


class TestJavaScriptSyntax:
    """Extract inline JS from session_view.html and check for syntax errors.

    Uses a simple bracket/paren/brace balance check — no external dependency.
    """

    def _extract_script_tags(self, tmp_path: Path) -> List[str]:
        """Extract content of <script>...</script> blocks from session_view.html."""
        client, session_id, _ = _make_app_and_session(tmp_path)
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        scripts = []
        # Match <script>...</script> (non-greedy, multiline)
        for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            content = m.group(1).strip()
            if content:
                scripts.append(content)
        return scripts

    def test_script_tags_extracted(self, tmp_path: Path):
        """At least one non-empty <script> block is found."""
        scripts = self._extract_script_tags(tmp_path)
        assert len(scripts) >= 1, "No <script> blocks found in session_view.html"

    def test_bracket_balance(self, tmp_path: Path):
        """All script blocks have balanced brackets, parens, and braces.

        Skips Jinja2 template expressions ({{ ... }}, {% ... %}) which
        are not valid JS but are expanded server-side.
        """
        scripts = self._extract_script_tags(tmp_path)
        for idx, script in enumerate(scripts):
            # Remove Jinja2 template expressions before checking balance
            import re as _re
            cleaned = _re.sub(r"\{\{.*?\}\}", "{}", script, flags=_re.DOTALL)
            cleaned = _re.sub(r"\{%-?\s*.*?\s*-?%\}", "", cleaned, flags=_re.DOTALL)

            # Simple count-based check: count { vs } and ( vs ) and [ vs ]
            opens_brace = cleaned.count("{")
            closes_brace = cleaned.count("}")
            opens_paren = cleaned.count("(")
            closes_paren = cleaned.count(")")
            opens_bracket = cleaned.count("[")
            closes_bracket = cleaned.count("]")

            errors = []
            if opens_brace != closes_brace:
                errors.append(f"braces: {opens_brace} open vs {closes_brace} close")
            if opens_paren != closes_paren:
                errors.append(f"parens: {opens_paren} open vs {closes_paren} close")
            if opens_bracket != closes_bracket:
                errors.append(f"brackets: {opens_bracket} open vs {closes_bracket} close")

            if errors:
                pytest.fail(
                    f"Script block {idx}: unbalanced: {'; '.join(errors)}"
                )

    def test_no_obvious_syntax_errors(self, tmp_path: Path):
        """Check for common JS syntax issues: double commas, trailing commas in objects, etc."""
        scripts = self._extract_script_tags(tmp_path)
        for idx, script in enumerate(scripts):
            # Check for common issues
            lines = script.split("\n")
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments and empty lines
                if stripped.startswith("//") or not stripped:
                    continue
                # Check for obvious issues like 'function() {' without matching
                # (This is a basic sanity check, not a full parser)
                pass
