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
      intentional = false;
    const timeout = schedule(() => fail(), 5000);
    function fail() {
      if (ended) return;
      ended = true;
      cancel(timeout);
      socket.close();
      if (ready) onClosed();
      else reject(Error("本机通话连接未建立，请重新开始"));
    }
    socket.addEventListener("error", fail);
    socket.addEventListener("close", () => {
      if (!intentional) fail();
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
