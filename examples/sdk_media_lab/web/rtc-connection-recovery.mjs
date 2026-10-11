/** Keep a transient transport loss distinct from a terminal failure. */
export function createConnectionRecovery(onFailure, {
  delayMs = 3000, schedule = callback => setTimeout(callback, delayMs), cancel = clearTimeout,
} = {}) {
  let pending = null;
  let ended = false;
  function clear() {
    if (pending) cancel(pending.timer);
    pending = null;
  }
  function fail(reason) {
    if (ended) return;
    ended = true;
    clear();
    onFailure(reason);
  }
  return {
    update(state) {
      if (ended) return;
      if (state === 'connected') clear();
      else if (state === 'failed' || state === 'closed') fail(`WebRTC connection ${state}`);
      else if (state === 'disconnected' && !pending) {
        const token = {};
        pending = token;
        token.timer = schedule(() => {
          if (!ended && pending === token) fail('WebRTC disconnect recovery timed out');
        });
      }
    },
    stop() { ended = true; clear(); },
  };
}
