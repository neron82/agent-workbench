(function () {
  const list = document.getElementById('message-list');
  if (!list) return;
  // If the browser supports EventSource, chat-stream.js (loaded in
  // document order after us) will claim the list. We mark the list
  // as "polling" here but the SSE script will overwrite it with "sse"
  // and then its own guard prevents us from running. To make sure
  // we never race against SSE, we wait one tick: if data-mode has
  // become "sse" by then, SSE is in charge and we step aside.
  //
  // The simpler fix: if EventSource is available, defer to SSE entirely
  // by not starting the poller at all. The SSE script is always loaded
  // after us in document order and will take over.
  if (typeof window.EventSource !== 'undefined') {
    return; // SSE fallback will be live in a moment.
  }

  list.dataset.mode = 'polling';
  let after = parseFloat(list.dataset.after || '0');
  const sessionId = list.dataset.sessionId;

  async function poll() {
    try {
      const response = await fetch(
        `/messages/list/${sessionId}/since?after=${encodeURIComponent(after)}`,
        {
          cache: 'no-store',
          headers: { 'X-Requested-With': 'fetch' }
        }
      );
      if (!response.ok) return;
      const payload = await response.json();
      if (payload.html) {
        list.insertAdjacentHTML('beforeend', payload.html);
      }
      if (typeof payload.next_after === 'number') {
        after = payload.next_after;
        list.dataset.after = String(after);
      }
      const thread = document.getElementById('message-thread');
      if (thread && payload.html) {
        thread.scrollTop = thread.scrollHeight;
      }
    } catch (_err) {
      // Quiet retry on next interval. A chat poller should not scream.
    }
  }

  setInterval(poll, 3000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) poll();
  });
})();
