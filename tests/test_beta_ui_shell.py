"""Tests for the coherent UI-shell beta slice.

Covers:
* workbench.css is served at /static/workbench.css
* base.html links workbench.css via url_for('static', filename='workbench.css')
* base.html has responsive tablet/mobile rules (media queries)
* base.html has a:focus-visible rules
* base.html has prefers-reduced-motion rules
* base.html has aria-live/role=alert on flash messages
* Standalone templates extend base.html and use content/title blocks
* run_panel.html has a session link when context exists
* All route forms/controls/test-contracts preserved
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def static_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "agent_workbench" / "web" / "static"


@pytest.fixture
def templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "agent_workbench" / "web" / "templates"


# ---------------------------------------------------------------------------
# workbench.css exists and is linked
# ---------------------------------------------------------------------------


class TestWorkbenchCssExists:
    def test_css_file_exists(self, static_dir: Path):
        css = static_dir / "workbench.css"
        assert css.exists(), "static/workbench.css must exist"
        content = css.read_text()
        assert len(content) > 100, "workbench.css should have substantial CSS content"

    def test_css_has_custom_properties(self, static_dir: Path):
        css = static_dir / "workbench.css"
        content = css.read_text()
        assert "--bg: #0d0d0d" in content or "--bg:" in content
        assert "--accent:" in content or "--accent: #4a9eff" in content

    def test_css_has_responsive_media_queries(self, static_dir: Path):
        css = static_dir / "workbench.css"
        content = css.read_text()
        assert "@media" in content, "workbench.css must have responsive media queries"

    def test_css_has_focus_visible(self, static_dir: Path):
        css = static_dir / "workbench.css"
        content = css.read_text()
        assert "focus-visible" in content, "workbench.css must have :focus-visible rules"

    def test_css_has_prefers_reduced_motion(self, static_dir: Path):
        css = static_dir / "workbench.css"
        content = css.read_text()
        assert "prefers-reduced-motion" in content, "workbench.css must have prefers-reduced-motion rules"


class TestBaseHtmlLinksCss:
    def test_base_html_links_workbench_css(self, templates_dir: Path):
        base = templates_dir / "base.html"
        content = base.read_text()
        # Should link workbench.css via url_for
        assert "url_for('static', filename='workbench.css')" in content or \
               'url_for("static", filename="workbench.css")' in content, \
            "base.html must link workbench.css via url_for"

    def test_base_html_has_aria_live_on_flashes(self, templates_dir: Path):
        base = templates_dir / "base.html"
        content = base.read_text()
        assert "aria-live" in content, "base.html must have aria-live on flash messages"
        assert "role=\"alert\"" in content or "role='alert'" in content, \
            "base.html must have role=alert on flash messages"

    def test_base_html_preserves_chat_poll_js(self, templates_dir: Path):
        base = templates_dir / "base.html"
        content = base.read_text()
        assert "chat-poll.js" in content, "base.html must still load chat-poll.js"

    def test_base_html_preserves_chat_stream_js(self, templates_dir: Path):
        base = templates_dir / "base.html"
        content = base.read_text()
        assert "chat-stream.js" in content, "base.html must still load chat-stream.js"

    def test_base_html_no_inline_design_css(self, templates_dir: Path):
        """The giant design CSS should be extracted; only minimal inline style remains."""
        base = templates_dir / "base.html"
        content = base.read_text()
        # The old inline style block should be gone or dramatically smaller
        style_match = re.search(r'<style>(.*?)</style>', content, re.DOTALL)
        if style_match:
            style_content = style_match.group(1)
            # Should not contain the full design system (custom properties, card classes, etc.)
            assert "--bg:" not in style_content, \
                "Design CSS custom properties should be in workbench.css, not inline in base.html"
            assert ".card {" not in style_content, \
                "Card styles should be in workbench.css, not inline in base.html"


# ---------------------------------------------------------------------------
# Standalone templates now extend base.html
# ---------------------------------------------------------------------------


STANDALONE_TEMPLATES = [
    "replay_view.html",
    "review_list.html",
    "permission_requests.html",
    "run_panel.html",
    "task_spec_view.html",
    "task_spec_form.html",
]


class TestStandaloneTemplatesExtendBase:
    @pytest.mark.parametrize("template_name", STANDALONE_TEMPLATES)
    def test_extends_base_html(self, templates_dir: Path, template_name: str):
        path = templates_dir / template_name
        assert path.exists(), f"{template_name} must exist"
        content = path.read_text()
        assert '{% extends "base.html" %}' in content, \
            f"{template_name} must extend base.html"
        assert "{% block content %}" in content or "{% block body %}" in content, \
            f"{template_name} must use a content block"
        assert "{% block title %}" in content, \
            f"{template_name} must use a title block"

    @pytest.mark.parametrize("template_name", STANDALONE_TEMPLATES)
    def test_no_standalone_html_boilerplate(self, templates_dir: Path, template_name: str):
        """Should not have <!doctype>, <html>, <head>, or <body> tags anymore."""
        path = templates_dir / template_name
        content = path.read_text()
        assert "<!doctype" not in content.lower(), \
            f"{template_name} should not have doctype (extends base.html)"
        assert "<html" not in content, \
            f"{template_name} should not have <html> tag (extends base.html)"
        assert "<head>" not in content, \
            f"{template_name} should not have <head> tag (extends base.html)"
        assert "</head>" not in content, \
            f"{template_name} should not have </head> tag (extends base.html)"
        assert "<body>" not in content, \
            f"{template_name} should not have <body> tag (extends base.html)"
        assert "</body>" not in content, \
            f"{template_name} should not have </body> tag (extends base.html)"

    @pytest.mark.parametrize("template_name", STANDALONE_TEMPLATES)
    def test_no_inline_style_block(self, templates_dir: Path, template_name: str):
        """Standalone templates should not have their own <style> blocks anymore."""
        path = templates_dir / template_name
        content = path.read_text()
        assert "<style>" not in content, \
            f"{template_name} should not have inline <style> block (uses workbench.css)"


# ---------------------------------------------------------------------------
# run_panel.html: session link when context exists
# ---------------------------------------------------------------------------


class TestRunPanelSessionLink:
    def test_has_session_link(self, templates_dir: Path):
        path = templates_dir / "run_panel.html"
        content = path.read_text()
        # Should have a link to the session view
        assert "url_for('sessions.show_session'" in content, \
            "run_panel.html must have a session link"


# ---------------------------------------------------------------------------
# Channel templates use existing design classes
# ---------------------------------------------------------------------------


class TestChannelTemplatesUseDesignClasses:
    def test_channel_list_uses_card_class(self, templates_dir: Path):
        path = templates_dir / "channel_list.html"
        content = path.read_text()
        # Should use .card or .panel classes from the design system
        assert "class=\"card" in content or "class=\"panel" in content or \
               "class=\"card-header" in content or "class=\"card-footer" in content, \
            "channel_list.html should use design system classes"

    def test_channel_view_uses_card_class(self, templates_dir: Path):
        path = templates_dir / "channel_view.html"
        content = path.read_text()
        assert "class=\"card" in content or "class=\"panel" in content, \
            "channel_view.html should use design system classes"

    def test_channel_fork_form_uses_card_class(self, templates_dir: Path):
        path = templates_dir / "channel_fork_form.html"
        content = path.read_text()
        assert "class=\"card" in content or "class=\"panel" in content, \
            "channel_fork_form.html should use design system classes"


# ---------------------------------------------------------------------------
# Route-render smoke tests (where fixtures permit)
# ---------------------------------------------------------------------------


class TestRouteRenderSmoke:
    """Smoke-test that converted templates render without 500 errors.

    These use the same fixture pattern as test_fork_ui.py and
    test_task_spec_ui.py — a real Flask app backed by a migrated DB.
    """

    @pytest.fixture
    def app(self, db, tmp_db):
        from agent_workbench.web import create_app
        app = create_app(db_path=str(tmp_db))
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"
        app.config["WORKBENCH_DB_CONN"] = db
        return app

    @pytest.fixture
    def client(self, app):
        from tests.conftest import make_csrf_client
        return make_csrf_client(app)

    @pytest.fixture
    def workspace_id(self, db):
        from agent_workbench.models.workspace import WorkspaceRepository
        ws = WorkspaceRepository(db).create(tenant_id="test", name="t")
        return ws.workspace_id

    @pytest.fixture
    def session_id(self, db, workspace_id):
        from agent_workbench.models.session_extension import SessionExtensionRepository
        s = SessionExtensionRepository(db).create(
            workspace_id=workspace_id, session_type="chat",
        )
        return s.session_id

    def test_task_spec_view_renders(self, client, db, workspace_id, session_id):
        from agent_workbench.models.task_spec import TaskSpecRepository
        spec = TaskSpecRepository(db).create(
            workspace_id=workspace_id,
            source_session_id=session_id,
            objective="Test objective",
        )
        resp = client.get(f"/task-specs/{spec.task_spec_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Test objective" in body

    def test_task_spec_form_renders(self, client, session_id):
        resp = client.get(f"/sessions/{session_id}/task-spec")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "New TaskSpec" in body or "TaskSpec" in body
        assert 'data-testid="objective"' in body

    def test_review_list_renders(self, client, session_id):
        resp = client.get(f"/sessions/{session_id}/reviews")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Session reviews" in body or "reviews" in body.lower()

    def test_channel_list_renders(self, client, workspace_id):
        resp = client.get(f"/channels?workspace_id={workspace_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Channels" in body or "channel" in body.lower()

    def test_channel_view_renders(self, client, workspace_id):
        resp = client.post(
            "/channels",
            data={"workspace_id": workspace_id, "channel_kind": "chat", "title": "test-ch"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        resp2 = client.get(location)
        assert resp2.status_code == 200
        body = resp2.get_data(as_text=True)
        assert "test-ch" in body

    def test_channel_fork_form_renders(self, client, workspace_id):
        resp = client.post(
            "/channels",
            data={"workspace_id": workspace_id, "channel_kind": "chat", "title": "fork-ch"},
            follow_redirects=False,
        )
        channel_id = resp.headers["Location"].rsplit("/", 1)[-1]
        resp2 = client.get(f"/channels/{channel_id}/fork")
        assert resp2.status_code == 200
        body = resp2.get_data(as_text=True)
        assert "Fork channel" in body
