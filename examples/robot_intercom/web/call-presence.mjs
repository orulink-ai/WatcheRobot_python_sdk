const safeCloseReasons = new Set([
  "", "normal closure", "going away", "ping timeout", "keepalive ping timeout",
  "service restart", "server restart", "server shutdown", "shutdown",
  "internal error", "internal server error", "policy violation", "connection closed",
]);

/** Native WebSocket ping/pong survives a paused page's JavaScript timers. */
export function connectCallPresence(
  requestId,
  onClosed,
  {
    WebSocketClass = WebSocket,
    baseUrl = location.href,
    schedule = setTimeout,
    cancel = clearTimeout,
  } = {},
) {
  return new Promise((resolve, reject) => {
    const url = new URL("/api/call/presence", baseUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("request_id", requestId);
    const socket = new WebSocketClass(url.href);
    let ready = false,
      ended = false,
      intentional = false,
      errorSeen = false;
    const timeout = schedule(() => fail(), 5000);
    function fail(event, closeSocket = true) {
      if (ended) return;
      ended = true;
      cancel(timeout);
      if (closeSocket) socket.close();
      if (ready) onClosed({
        code: event?.code ?? 1006,
        wasClean: event?.wasClean === true,
        errorSeen,
        reason: safeCloseReasons.has(event?.reason ?? "") ? (event?.reason ?? "") : "[redacted]",
      });
      else reject(Error("本机通话连接未建立，请重新开始"));
    }
    socket.addEventListener("error", () => {
      // Browser network errors are followed by close; preserve that event's
      // actual code/reason instead of generating another close here.
      errorSeen = true;
    });
    socket.addEventListener("close", (event) => {
      if (!intentional) fail(event, false);
    });
    socket.addEventListener("message", (event) => {
      if (ended || ready) return;
      try {
        if (JSON.parse(event.data).ready !== true) return;
      } catch {
        return;
      }
      ready = true;
      cancel(timeout);
      resolve({
        close() {
          intentional = true;
          ended = true;
          cancel(timeout);
          socket.close();
        },
      });
    });
  });
}
