/* chat-stream.js — Server-Sent Events live-update for the session chat.
 *
 * Loaded by base.html *additionally* to chat-poll.js when the browser
 * supports EventSource. This file claims the message list by setting
 * ``data-mode="sse"``; chat-poll.js's mode guard then becomes a no-op
 * for that page so we never double-render.
 *
 * Behaviour:
 *   - On load: open an EventSource against /messages/stream/<id>?after=
 *   - On ``message`` event: parse the JSON frame, append the rendered
 *     HTML fragment to the message list, scroll the thread, and
 *     remember the last id for reconnect-cursor.
 *   - On ``error``: the browser auto-reconnects. We only flip the
 *     data-mode attribute so the UI can show a "reconnecting…" hint.
 *   - On ``open``: flip back to "sse" so the hint disappears.
 */
(function () {
  const list = document.getElementById('message-list');
  if (!list) return;
  // The polling fallback has already claimed the list. Step aside
  // silently. This can happen if a page includes chat-stream.js
  // without the feature-detect wrapper.
  if (list.dataset.mode && list.dataset.mode !== 'polling') {
    return; // Already claimed by another instance — bail.
  }

  const sessionId = list.dataset.sessionId;
  if (!sessionId) return;
  if (typeof window.EventSource === 'undefined') return;

  // Claim the list IMMEDIATELY so the polling fallback (which loads
  // earlier in document order) steps aside. The poller only runs when
  // data-mode is unset or explicitly 'polling'; setting it to 'sse'
  // here makes the poller a no-op.
  let after = parseFloat(list.dataset.after || '0');
  list.dataset.mode = 'sse';

  const thread = document.getElementById('message-thread');
  const es = new EventSource(
    '/messages/stream/' + encodeURIComponent(sessionId) +
    '?after=' + encodeURIComponent(after)
  );

  es.addEventListener('open', function () {
    list.dataset.mode = 'sse';
  });

  es.addEventListener('message', function (e) {
    let payload;
    try {
      payload = JSON.parse(e.data);
    } catch (_err) {
      return; // Malformed frame, skip.
    }
    if (payload && payload.html) {
      // Suppress re-render if our own (polling) client also tried to
      // append this row — id is stable.
      if (payload.id) {
        const existing = list.querySelector('[data-id="' + payload.id + '"]');
        if (existing) return;
      }
      list.insertAdjacentHTML('beforeend', payload.html);
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
    if (payload && typeof payload.created_at === 'number') {
      after = Math.max(after, payload.created_at);
      list.dataset.after = String(after);
    }
  });

  es.addEventListener('error', function () {
    // The browser's EventSource will keep trying; we just tell the
    // user it's not live right now. The list reverts to "polling"
    // mode when the connection is permanently closed.
    list.dataset.mode = 'reconnecting';
  });
})();
