/** Accepted discovery is distinct from an authenticated, online device. */
export function createDeviceConnector({
  api,
  onChange = () => {},
  now = () => performance.now(),
}) {
  let phase = "idle",
    message = "输入机器人屏幕上的六位连接码。",
    requestId = null,
    deadline = 0;
  const busy = () => ["submitting", "waiting", "canceling"].includes(phase);
  const snapshot = () => ({ phase, message, busy: busy() });
  function change(next, text) {
    phase = next;
    message = text;
    onChange(snapshot());
  }
  return {
    snapshot,
    async connect(code, ip) {
      if (busy()) return;
      if (!/^[0-9]{6}$/.test(code)) {
        change("error", "请输入六位数字连接码。");
        return;
      }
      change("submitting", "正在提交连接请求…");
      try {
        const result = await api("/api/device/pair", {
          pairing_code: code,
          ...(ip ? { device_ip: ip } : {}),
        });
        requestId = result.connection?.request_id;
        deadline = now() + 30000;
        change("waiting", "正在局域网查找机器人，请保持机器人连接页面开启…");
        if (result.connection?.online) this.update(result.connection);
      } catch (error) {
        change("error", error.message);
      }
    },
    update(connection) {
      if (phase === "submitting" || phase === "canceling") return;
      if (connection?.online) {
        if (
          phase === "waiting" &&
          requestId &&
          connection.request_id !== requestId
        )
          change("error", "当前连接已变化，请确认页面顶部的机器人。");
        else change("connected", "连接成功，可以开始通话。");
      } else if (phase === "waiting") {
        if (
          now() >= deadline ||
          (connection?.state === "idle" && connection.last_error)
        )
          change(
            "error",
            "未能连接机器人。请检查连接码和同一 Wi-Fi，也可展开下方填写机器人 IP 后重试。",
          );
        else if (connection?.state === "connecting")
          change("waiting", "已找到机器人，正在建立连接…");
      } else if (phase === "connected")
        change("idle", "机器人已断开，请重新输入屏幕上的连接码。");
    },
    async cancel() {
      if (phase !== "waiting") return;
      change("canceling", "正在取消连接…");
      try {
        const result = await api("/api/device/pair/cancel", {});
        change(
          result.connected ? "connected" : "idle",
          result.connected
            ? "连接成功，可以开始通话。"
            : "已取消连接，可以重新输入连接码。",
        );
      } catch (error) {
        change("error", error.message);
      }
    },
  };
}
