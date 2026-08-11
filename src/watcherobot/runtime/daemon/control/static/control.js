"use strict";

const GATEWAY_HEALTH_URL = "http://127.0.0.1:3101/api/health";
const REFRESH_INTERVAL_MS = 2000;
const PROBE_TIMEOUT_MS = 1600;

const state = {
  device: null,
  busy: false,
  toastTimer: null,
};

const elements = {
  pairingForm: document.querySelector("#pairing-form"),
  pairingCode: document.querySelector("#pairing-code"),
  pairingError: document.querySelector("#pairing-error"),
  pairButton: document.querySelector("#pair-button"),
  refreshButton: document.querySelector("#refresh-button"),
  cancelButton: document.querySelector("#cancel-button"),
  disconnectButton: document.querySelector("#disconnect-button"),
  sessionSummary: document.querySelector("#session-summary"),
  lastSync: document.querySelector("#last-sync"),
  toast: document.querySelector("#toast"),
};

function serviceCard(name) {
  return document.querySelector(`[data-service="${name}"]`);
}

function setService(name, tone, label, detail, meta) {
  const card = serviceCard(name);
  card.dataset.tone = tone;
  card.querySelector(".status-label").textContent = label;
  if (detail) {
    card.querySelector(".service-detail").textContent = detail;
  }
  if (meta) {
    card.querySelector(".service-meta").textContent = meta;
  }
}

function setBusy(isBusy) {
  state.busy = isBusy;
  elements.pairButton.disabled = isBusy;
  elements.pairButton.dataset.loading = String(isBusy);
  elements.refreshButton.disabled = isBusy;
  updateDeviceActions();
}

function showToast(message, tone = "info") {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.dataset.tone = tone;
  elements.toast.dataset.visible = "true";
  state.toastTimer = window.setTimeout(() => {
    elements.toast.dataset.visible = "false";
  }, 3600);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || data.error || `HTTP ${response.status}`);
  }
  return data;
}

function describeApplication(application) {
  const name = application.current_app || "未选择";
  const running = application.state === "running";
  const selected = application.selected !== false && name !== "未选择";
  const tone = running ? "online" : selected ? "pending" : "offline";
  const label = running ? "运行中" : selected ? "未运行" : "未选择";
  const process = application.process_id
    ? `PID ${application.process_id}`
    : `状态 ${application.state || "unknown"}`;

  setService("application", tone, label, name, process);
  const nameField = document.querySelector('[data-field="application-name"]');
  nameField.textContent = name;
  nameField.title = name;
}

function describeDevice(device) {
  state.device = device;
  const connected = device.state === "connected" && device.online === true;
  const pairing = ["discovering", "connecting", "accepted"].includes(device.state);
  const mode = device.mode || "—";
  const requestId = device.request_id
    ? `请求 ${device.request_id.slice(0, 8)}…`
    : "无活动会话";

  document.querySelector('[data-field="device-mode"]').textContent = mode;

  if (connected) {
    setService("device", "online", "已连接", "ESP32 在线", requestId);
    elements.sessionSummary.textContent = `设备已通过 ${mode} 模式连接。可以主动断开后输入新配对码。`;
  } else if (pairing) {
    setService("device", "pending", "配对中", device.state, requestId);
    elements.sessionSummary.textContent = "正在局域网中发现并连接设备。也可以直接输入新配对码替换本次尝试。";
  } else if (device.last_error) {
    setService("device", "error", "连接异常", device.state || "idle", device.last_error);
    elements.sessionSummary.textContent = `上次连接错误：${device.last_error}。请输入设备当前显示的新配对码。`;
  } else {
    setService("device", "offline", "未连接", device.state || "idle", requestId);
    elements.sessionSummary.textContent = "当前没有设备连接。请输入 ESP32 屏幕上的六位配对码。";
  }
  updateDeviceActions();
}

function updateDeviceActions() {
  const deviceState = state.device?.state || "idle";
  const pairing = ["discovering", "connecting", "accepted"].includes(deviceState);
  elements.cancelButton.disabled = state.busy || !pairing;
  elements.disconnectButton.disabled = state.busy || deviceState !== "connected";
  elements.pairButton.querySelector(".button-label").textContent =
    deviceState === "idle" ? "连接设备" : "重新配对";
}

async function refreshDaemonState() {
  const [statusResult, deviceResult] = await Promise.allSettled([
    fetchJson("/daemon/status"),
    fetchJson("/daemon/devices"),
  ]);

  if (statusResult.status === "fulfilled") {
    setService("daemon", "online", "正常", "127.0.0.1:8767", "REST API 可用");
    describeApplication(statusResult.value.application || {});
  } else {
    setService("daemon", "error", "不可用", "127.0.0.1:8767", statusResult.reason.message);
    setService("application", "offline", "未知", "无法读取 Daemon", "等待 API 恢复");
  }

  if (deviceResult.status === "fulfilled") {
    describeDevice(deviceResult.value.device || {});
  } else {
    state.device = null;
    setService("device", "offline", "未知", "无法读取设备状态", deviceResult.reason.message);
    updateDeviceActions();
  }

  elements.lastSync.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
}

async function probeGateway() {
  const abortController = new AbortController();
  const timeoutId = window.setTimeout(() => abortController.abort(), PROBE_TIMEOUT_MS);
  try {
    await fetch(GATEWAY_HEALTH_URL, {
      cache: "no-store",
      mode: "no-cors",
      signal: abortController.signal,
    });
    setService(
      "gateway",
      "online",
      "端口可达",
      "127.0.0.1:3101/api/health",
      "健康详情受跨域策略保护",
    );
  } catch (error) {
    const message = error.name === "AbortError" ? "探测超时" : "服务未响应";
    setService("gateway", "offline", "不可达", "127.0.0.1:3101/api/health", message);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function refreshAll() {
  await Promise.all([refreshDaemonState(), probeGateway()]);
}

async function postAction(url) {
  return fetchJson(url, { method: "POST", body: "{}" });
}

async function releaseCurrentDeviceSlot() {
  const deviceState = state.device?.state || "idle";
  if (deviceState === "idle") {
    return;
  }
  if (deviceState === "connected") {
    await postAction("/daemon/devices/disconnect");
    return;
  }
  await postAction("/daemon/devices/pair/cancel");
}

elements.pairingCode.addEventListener("input", () => {
  const digits = elements.pairingCode.value.replace(/\D/g, "").slice(0, 6);
  elements.pairingCode.value = digits;
  elements.pairingCode.setAttribute("aria-invalid", "false");
  elements.pairingError.textContent = "";
});

elements.pairingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pairingCode = elements.pairingCode.value.trim();
  if (!/^\d{6}$/.test(pairingCode)) {
    elements.pairingCode.setAttribute("aria-invalid", "true");
    elements.pairingError.textContent = "请输入设备屏幕上完整的六位数字配对码。";
    elements.pairingCode.focus();
    return;
  }

  setBusy(true);
  elements.pairingError.textContent = "";
  try {
    await releaseCurrentDeviceSlot();
    await fetchJson("/daemon/devices/pair", {
      method: "POST",
      body: JSON.stringify({
        pairing_code: pairingCode,
        target_mode: "python_sdk",
      }),
    });
    elements.pairingCode.value = "";
    showToast("配对请求已发出，正在等待 ESP32 建立连接。", "success");
  } catch (error) {
    elements.pairingCode.setAttribute("aria-invalid", "true");
    elements.pairingError.textContent = error.message;
    showToast(`配对失败：${error.message}`, "error");
  } finally {
    setBusy(false);
    await refreshAll();
  }
});

elements.cancelButton.addEventListener("click", async () => {
  setBusy(true);
  try {
    await postAction("/daemon/devices/pair/cancel");
    showToast("已取消当前配对尝试。", "success");
  } catch (error) {
    showToast(`取消失败：${error.message}`, "error");
  } finally {
    setBusy(false);
    await refreshAll();
  }
});

elements.disconnectButton.addEventListener("click", async () => {
  setBusy(true);
  try {
    await postAction("/daemon/devices/disconnect");
    showToast("设备已断开，可以输入新配对码重新连接。", "success");
  } catch (error) {
    showToast(`断开失败：${error.message}`, "error");
  } finally {
    setBusy(false);
    await refreshAll();
  }
});

elements.refreshButton.addEventListener("click", refreshAll);

refreshAll();
window.setInterval(refreshAll, REFRESH_INTERVAL_MS);
