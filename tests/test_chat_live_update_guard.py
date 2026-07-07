"""Tests for the chat-poll.js / chat-stream.js race-condition fix.

These are static-analysis tests because we don't have a JS test runner
in this project.  They verify the *contract* between the two scripts
so a future refactor doesn't accidentally re-introduce the
double-render bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "src/agent_workbench/web/static"


@pytest.fixture
def poll_src() -> str:
    return (STATIC_DIR / "chat-poll.js").read_text(encoding="utf-8")


@pytest.fixture
def stream_src() -> str:
    return (STATIC_DIR / "chat-stream.js").read_text(encoding="utf-8")


class TestChatPollGuard:
    """chat-poll.js must step aside when EventSource is available."""

    def test_checks_for_event_source_early(self, poll_src):
        """The EventSource check must happen BEFORE the poller claims
        the list (sets data-mode='polling') and before setInterval."""
        # Find the EventSource check
        check_idx = poll_src.find("EventSource")
        assert check_idx > 0, "chat-poll.js must mention EventSource"
        # Find where the poller claims the list
        import re
        claim_match = re.search(
            r"\.dataset\.mode\s*=\s*['\"]polling['\"]", poll_src
        )
        assert claim_match is not None, "poller must claim the list"
        claim_idx = claim_match.start()
        # And where setInterval starts
        interval_idx = poll_src.find("setInterval")
        assert interval_idx > 0, "poller must use setInterval"
        # The check must come before both
        assert check_idx < claim_idx, (
            "EventSource check must come before list-claim; "
            f"check={check_idx} claim={claim_idx}"
        )
        assert check_idx < interval_idx, (
            "EventSource check must come before setInterval; "
            f"check={check_idx} interval={interval_idx}"
        )

    def test_returns_immediately_when_event_source_available(self, poll_src):
        """When EventSource is present, chat-poll.js must return before
        starting the poller."""
        # The check block must contain a return inside its if-body.
        # We look for a pattern: typeof window.EventSource !== 'undefined' { return ... }
        import re
        pattern = re.compile(
            r"typeof\s+window\.EventSource\s*!==?\s*['\"]undefined['\"].*?return",
            re.DOTALL,
        )
        assert pattern.search(poll_src), (
            "chat-poll.js must `return` when EventSource is available"
        )


class TestChatStreamClaim:
    """chat-stream.js must claim the list so the poller steps aside."""

    def test_sets_data_mode_to_sse(self, stream_src):
        """chat-stream.js must set list.dataset.mode = 'sse' so the
        poller (or any other claimant) knows the list is in SSE hands."""
        import re
        assert re.search(
            r"\.dataset\.mode\s*=\s*['\"]sse['\"]", stream_src
        ), "chat-stream.js must set data-mode='sse'"

    def test_claim_happens_after_event_source_check(self, stream_src):
        """The SSE claim must only happen when EventSource is defined."""
        check_idx = stream_src.find("window.EventSource")
        import re
        claim_match = re.search(
            r"\.dataset\.mode\s*=\s*['\"]sse['\"]", stream_src
        )
        assert claim_match is not None, "claim not found"
        claim_idx = claim_match.start()
        assert check_idx > 0
        assert claim_idx > 0
        assert check_idx < claim_idx, (
            "SSE claim must come after the EventSource check"
        )

    def test_no_dup_bailout(self, stream_src):
        """The earlier 'already claimed' block must actually return,
        not just be a comment (this was the original bug — the
        comment-block without a return made the guard a no-op)."""
        # Find the block: if (list.dataset.mode && list.dataset.mode !== 'polling')
        import re
        pattern = re.compile(
            r"if\s*\(\s*list\.dataset\.mode\s*&&\s*list\.dataset\.mode\s*!==?\s*['\"]polling['\"]\s*\)\s*\{[^}]*\}",
            re.DOTALL,
        )
        m = pattern.search(stream_src)
        assert m, "guard block not found"
        body = m.group(0)
        assert "return" in body, (
            "guard block must contain a return statement; the original "
            "bug was a comment-only block that made the guard a no-op"
        )


class TestBaseTemplateWiring:
    """base.html must load both scripts and feature-detect EventSource."""

    def test_base_html_loads_poll(self):
        base = (Path(__file__).resolve().parents[1]
                / "src/agent_workbench/web/templates/base.html").read_text()
        assert "chat-poll.js" in base

    def test_base_html_feature_detects_event_source(self):
        base = (Path(__file__).resolve().parents[1]
                / "src/agent_workbench/web/templates/base.html").read_text()
        assert "EventSource" in base
        # chat-stream.js must only be loaded when EventSource is in window
        assert "if ('EventSource' in window)" in base
        assert "chat-stream.js" in base
