/** Recover only the media socket within its owning Application session. */
export function createMjpegTransport({ url, isActive, onPacket, onFailure,
  onConnecting = () => {}, createSocket = address => new WebSocket(address),
  setTimer = (cb, ms) => setTimeout(cb, ms), clearTimer = id => clearTimeout(id),
}) {
  let socket = null, stopped = false, retry = null, deadline = null;
  const active = () => !stopped && isActive();
  function stop() {
    stopped = true;
    clearTimer(retry); clearTimer(deadline);
    retry = deadline = null;
    const old = socket; socket = null;
    try { old?.close(); } catch (_) {}
  }
  function armDeadline() {
    if (deadline !== null) return;
    deadline = setTimer(() => {
      const report = active(); stop();
      if (report) onFailure("Live-video channel did not recover within 5 seconds");
    }, 5000);
  }
  function reconnect() {
    if (!active()) { stop(); return; }
    armDeadline();
    onConnecting();
    if (retry === null) retry = setTimer(() => { retry = null; connect(); }, 250);
  }
  function connect() {
    if (!active()) { stop(); return; }
    let current;
    try { current = createSocket(url); } catch (_) { reconnect(); return; }
    socket = current;
    const owns = () => active() && socket === current;
    current.binaryType = "arraybuffer";
    current.addEventListener("open", () => {
      if (!owns()) return;
      try { current.send("ready"); } catch (_) { lost(); }
    });
    current.addEventListener("message", event => {
      if (!owns()) return;
      onPacket(event.data, () => {
        // A successful handshake or an undecodable JPEG is not recovery.
        if (!owns()) return;
        clearTimer(deadline); deadline = null;
      }, owns);
    });
    function lost() {
      if (!owns()) return;
      socket = null;
      try { current.close(); } catch (_) {}
      reconnect();
    }
    current.addEventListener("close", lost);
    current.addEventListener("error", lost);
  }
  armDeadline(); connect();
  return { stop };
}
