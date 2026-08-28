const byId = (id) => document.getElementById(id);
const canvas = byId("faceCanvas");
const ctx = canvas.getContext("2d", { alpha: false });
const controls = {
  style: byId("style"), tag: byId("tag"), accessory: byId("accessory"),
  accessoryScale: byId("accessoryScale"), accessoryX: byId("accessoryX"),
  accessoryY: byId("accessoryY"), accessoryRotation: byId("accessoryRotation"),
  gazeX: byId("gazeX"), gazeY: byId("gazeY"),
  openness: byId("openness"), spacing: byId("spacing"), scale: byId("scale"),
  scaleX: byId("scaleX"), scaleY: byId("scaleY"),
  stroke: byId("stroke"), roundness: byId("roundness"),
  leftOpenness: byId("leftOpenness"), rightOpenness: byId("rightOpenness"),
  tilt: byId("tilt"), leftTilt: byId("leftTilt"), rightTilt: byId("rightTilt"),
  transition: byId("transition"), autoBlink: byId("autoBlink"),
  blinkInterval: byId("blinkInterval"), blinkDuration: byId("blinkDuration"), eyeColor: byId("eyeColor"),
};
const state = {
  active: false, serviceReady: false, statusInitialized: false,
  deviceConnected: false, expressionSupported: false,
  preset: "standby", sending: false, pairing: false, statusBusy: false,
  intentActive: false, resumePending: false, resumeTimer: 0,
  lastFrame: performance.now(), phase: 0, debounce: 0,
};
const presetDefaults = {
  standby: { openness: 1, spacing: .85, tilt: 0, tag: "none" },
  thinking: { openness: .72, spacing: .82, tilt: -7, tag: "thinking" },
  speaking: { openness: .9, spacing: .88, tilt: 0, tag: "none" },
};

function values() {
  return {
    preset: state.preset, style: controls.style.value, tag: controls.tag.value,
    accessory: controls.accessory.value,
    accessory_scale: Number(controls.accessoryScale.value),
    accessory_x: Number(controls.accessoryX.value),
    accessory_y: Number(controls.accessoryY.value),
    accessory_rotation_deg: Number(controls.accessoryRotation.value),
    gaze_x: Number(controls.gazeX.value), gaze_y: Number(controls.gazeY.value),
    openness: Number(controls.openness.value), spacing: Number(controls.spacing.value),
    scale: Number(controls.scale.value),
    scale_x: Number(controls.scaleX.value), scale_y: Number(controls.scaleY.value),
    stroke: Number(controls.stroke.value), roundness: Number(controls.roundness.value),
    left_openness: Number(controls.leftOpenness.value), right_openness: Number(controls.rightOpenness.value),
    tilt_deg: Number(controls.tilt.value), left_tilt_deg: Number(controls.leftTilt.value),
    right_tilt_deg: Number(controls.rightTilt.value), transition_ms: Number(controls.transition.value),
    auto_blink: controls.autoBlink.checked, blink_interval_ms: Number(controls.blinkInterval.value),
    blink_duration_ms: Number(controls.blinkDuration.value), color: controls.eyeColor.value.toUpperCase(),
  };
}

function refreshReadouts() {
  renderAccessoryModule();
  byId("gazeXValue").value = Number(controls.gazeX.value).toFixed(2);
  byId("gazeYValue").value = Number(controls.gazeY.value).toFixed(2);
  byId("opennessValue").value = Number(controls.openness.value).toFixed(2);
  byId("spacingValue").value = Number(controls.spacing.value).toFixed(2);
  byId("scaleValue").value = Number(controls.scale.value).toFixed(2);
  byId("scaleXValue").value = Number(controls.scaleX.value).toFixed(2);
  byId("scaleYValue").value = Number(controls.scaleY.value).toFixed(2);
  byId("accessoryScaleValue").value = Number(controls.accessoryScale.value).toFixed(2);
  byId("accessoryXValue").value = Number(controls.accessoryX.value).toFixed(2);
  byId("accessoryYValue").value = Number(controls.accessoryY.value).toFixed(2);
  byId("accessoryRotationValue").value = `${controls.accessoryRotation.value}°`;
  byId("strokeValue").value = Number(controls.stroke.value).toFixed(2);
  byId("roundnessValue").value = Number(controls.roundness.value).toFixed(2);
  byId("leftOpennessValue").value = Number(controls.leftOpenness.value).toFixed(2);
  byId("rightOpennessValue").value = Number(controls.rightOpenness.value).toFixed(2);
  byId("tiltValue").value = `${controls.tilt.value}°`;
  byId("leftTiltValue").value = `${controls.leftTilt.value}°`;
  byId("rightTiltValue").value = `${controls.rightTilt.value}°`;
  byId("transitionValue").value = `${controls.transition.value} ms`;
  byId("blinkIntervalValue").value = `${controls.blinkInterval.value} ms`;
  byId("blinkDurationValue").value = `${controls.blinkDuration.value} ms`;
  byId("eyeColorValue").value = controls.eyeColor.value.toUpperCase();
  byId("presetReadout").textContent = state.preset.toUpperCase();
  const args = values();
  const lines = Object.entries(args).map(([key, value]) => `    ${key}=${typeof value === "string" ? `"${value}"` : value},`);
  byId("sdkOutput").textContent = `app.robot.expression_runtime.${state.active ? "update" : "start"}(\n${lines.join("\n")}\n)`;
}

function renderAccessoryModule() {
  const hasAccessory = controls.accessory.value !== "none";
  byId("accessoryControlsModule").dataset.active = String(hasAccessory);
  byId("accessoryModuleState").textContent = hasAccessory ? "装饰可独立变换" : "先选择头部装饰";
  controls.accessoryScale.disabled = !hasAccessory;
  controls.accessoryX.disabled = !hasAccessory;
  controls.accessoryY.disabled = !hasAccessory;
  controls.accessoryRotation.disabled = !hasAccessory;
}

function roundedRect(x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath(); ctx.roundRect(x, y, width, height, r); ctx.fill();
}

function tagCircle(x, y, radius) {
  ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();
}

function drawTag(tag, color) {
  ctx.fillStyle = color;
  if (tag === "thinking") {
    tagCircle(151 * 2, 63 * 2, 3 * 2);
    tagCircle(160 * 2, 53 * 2, 5 * 2);
    tagCircle(173 * 2, 40 * 2, 8 * 2);
  } else if (tag === "question") {
    for (let y = 39; y <= 60; y += 1) {
      const x = y < 46 ? 163 + (y - 39) : (y < 52 ? 170 - (y - 46) : 164);
      tagCircle(x * 2, y * 2, 2 * 2);
    }
    tagCircle(164 * 2, 69 * 2, 3 * 2);
  } else if (tag === "love") {
    tagCircle(166 * 2, 48 * 2, 6 * 2);
    tagCircle(176 * 2, 48 * 2, 6 * 2);
    for (let row = 0; row < 12; row += 1) {
      for (let x = 166 + Math.floor(row / 2); x <= 176 - Math.floor(row / 2); x += 1) {
        ctx.fillRect(x * 2, (52 + row) * 2, 2, 2);
      }
    }
  }
}

const accessoryColors = {
  halo: "#FFD43B", devil_horns: "#FF4B55", ninja_mask: "#39445D",
  hero_mask: "#2878FF", eyepatch: "#667085", antenna: "#5DE4FF",
};

const accessoryAnchors = {
  halo: [206, 56], devil_horns: [206, 100], ninja_mask: [206, 206],
  hero_mask: [206, 206], eyepatch: [206, 160], antenna: [206, 80],
};

function drawAccessory(accessory, layer, t, transform) {
  if (accessory === "none") return;
  ctx.save();
  const [anchorX, anchorY] = accessoryAnchors[accessory] || [206, 206];
  ctx.translate(anchorX + transform.accessory_x * 206, anchorY + transform.accessory_y * 206);
  ctx.rotate(transform.accessory_rotation_deg * Math.PI / 180);
  ctx.scale(transform.accessory_scale, transform.accessory_scale);
  ctx.translate(-anchorX, -anchorY);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = accessoryColors[accessory];
  ctx.fillStyle = accessoryColors[accessory];
  ctx.lineWidth = 8;
  if (layer === "back" && accessory === "halo") {
    ctx.beginPath(); ctx.ellipse(206, 56, 108, 18, 0, 0, Math.PI * 2); ctx.stroke();
  } else if (layer === "back" && accessory === "devil_horns") {
    ctx.beginPath(); ctx.moveTo(86, 126); ctx.lineTo(110, 48); ctx.lineTo(146, 126); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(266, 126); ctx.lineTo(302, 48); ctx.lineTo(328, 126); ctx.closePath(); ctx.fill();
  } else if (layer === "back" && accessory === "antenna") {
    ctx.beginPath(); ctx.moveTo(206, 112); ctx.lineTo(206, 48); ctx.stroke();
    ctx.beginPath(); ctx.arc(206, 32, 16, 0, Math.PI * 2); ctx.fill();
  } else if (layer === "back" && accessory === "ninja_mask") {
    ctx.fillRect(40, 152, 334, 126);
    ctx.beginPath(); ctx.moveTo(352, 164); ctx.lineTo(406, 144); ctx.lineTo(374, 194); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(352, 184); ctx.lineTo(406, 208); ctx.lineTo(366, 226); ctx.closePath(); ctx.fill();
  } else if (layer === "back" && accessory === "hero_mask") {
    ctx.beginPath(); ctx.moveTo(48, 152); ctx.lineTo(184, 136); ctx.lineTo(206, 164); ctx.lineTo(176, 268); ctx.lineTo(76, 264); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(364, 152); ctx.lineTo(228, 136); ctx.lineTo(206, 164); ctx.lineTo(236, 268); ctx.lineTo(336, 264); ctx.closePath(); ctx.fill();
  } else if (layer === "front" && accessory === "eyepatch") {
    ctx.strokeStyle = accessoryColors.eyepatch; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.moveTo(18, 160); ctx.lineTo(394, 100); ctx.stroke();
    ctx.fillStyle = "#050607"; ctx.beginPath(); ctx.arc(132, 206, 64, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.restore();
}

function render(now) {
  if (now - state.lastFrame < 50) { requestAnimationFrame(render); return; }
  const dt = Math.min(100, now - state.lastFrame); state.lastFrame = now; state.phase += dt / 1000;
  const p = values();
  let openness = p.openness;
  if (p.auto_blink) {
    const interval = p.blink_interval_ms / 1000;
    const duration = p.blink_duration_ms / 1000;
    const blink = state.phase % interval;
    const blinkStart = interval - duration;
    if (blink >= blinkStart) openness *= Math.max(.06, Math.abs(blink - (blinkStart + duration / 2)) / (duration / 2));
  }
  if (p.preset === "speaking" || p.style === "watcher_pulse") openness *= .83 + Math.sin(state.phase * 7.4) * .14;
  const styleScale = { watcher: 1, watcher_compact: .94, watcher_focus: .9, watcher_open: 1.12, watcher_pulse: 1 }[p.style];
  const gazeX = p.gaze_x * 20; const gazeY = p.gaze_y * 20;
  const segments = [
    [-30.75, 0, 5, 30], [-18.25, 0, 6, 58], [-6.25, -23.5, 6, 25], [-6.25, 24, 6, 24],
    [6.25, -23.5, 5, 25], [6.25, 23.5, 5, 25], [18.25, 0, 5, 58], [30.75, 0, 6, 30],
  ];
  const eyeSpacing = 88 * p.spacing;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawAccessory(p.accessory, "back", state.phase, p);
  ctx.fillStyle = p.color;
  [-1, 1].forEach((side) => {
    const center = canvas.width / 2 + side * eyeSpacing + gazeX;
    const independentTilt = side < 0 ? p.left_tilt_deg : p.right_tilt_deg;
    const eyeOpenness = side < 0 ? p.left_openness : p.right_openness;
    const angle = (side * (p.tilt_deg + (p.style === "watcher_focus" ? 4 : 0)) + independentTilt) * Math.PI / 180;
    ctx.save(); ctx.translate(center, canvas.height / 2 + gazeY); ctx.rotate(angle);
    segments.forEach(([x, y, width, height]) => {
      const scaledWidth = width * p.scale * p.scale_x * p.stroke;
      const scaledHeight = height * p.scale * p.scale_y * openness * styleScale * eyeOpenness;
      const scaledX = x * p.scale * p.scale_x;
      const scaledY = y * p.scale * p.scale_y * openness * styleScale * eyeOpenness;
      roundedRect(scaledX - scaledWidth / 2, scaledY - scaledHeight / 2, scaledWidth, scaledHeight, scaledWidth / 2 * p.roundness);
    });
    ctx.restore();
  });
  drawAccessory(p.accessory, "front", state.phase, p);
  drawTag(p.tag, p.color);
  byId("fpsReadout").textContent = `${(1000 / Math.max(1, dt)).toFixed(1)} FPS`;
  requestAnimationFrame(render);
}

function toast(message, tone = "ok") {
  const node = byId("toast"); node.textContent = message; node.dataset.tone = tone; node.classList.add("visible");
  window.clearTimeout(toast.timer); toast.timer = window.setTimeout(() => node.classList.remove("visible"), 2800);
}

async function api(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const contentType = response.headers.get("content-type") || "";
  let payload;
  if (contentType.includes("application/json")) {
    payload = await response.json();
  } else if (state.resumePending) {
    title = "正在恢复代码表情";
    hint = "设备通道已重新连接，正在恢复上次的参数";
    status = "正在恢复同步";
    sync = "RESUMING";
    guideState = "connected";
    startLabel = "正在恢复…";
  } else {
    const detail = (await response.text()).trim();
    payload = { detail: detail || `请求失败（HTTP ${response.status}）` };
  }
  if (!response.ok) throw new Error(payload.detail || "设备拒绝了参数");
  return payload;
}

function renderConnectionState() {
  const guide = byId("connectionGuide");
  const start = byId("startButton");
  const stop = byId("stopButton");
  const pairingForm = byId("pairingForm");
  const pairingCode = byId("pairingCode");
  const pairingButton = byId("pairingButton");
  let title; let hint; let status; let sync; let guideState = "preview"; let startLabel = "请从 SDK 启动";

  if (!state.statusInitialized) {
    title = "正在连接 SDK";
    hint = "正在读取 Application 与 Watcher 通道状态";
    status = "正在连接 SDK";
    sync = "CHECKING SDK";
  } else if (!state.serviceReady) {
    title = "仅本地预览";
    hint = "请通过 watcherobot app run 启动 Expression Lab";
    status = "SDK 未启动";
    sync = "PREVIEW ONLY";
  } else if (!state.deviceConnected) {
    title = "等待 Watcher";
    hint = "在 Watcher 打开 Desktop Link；首次连接需要屏幕上的 6 位配对码";
    status = "等待 Watcher";
    sync = "WAITING FOR DEVICE";
    guideState = "waiting";
    startLabel = "等待 Watcher";
  } else if (!state.expressionSupported) {
    title = "Watcher 已连接，固件不兼容";
    hint = "当前设备缺少 expression.runtime.v2 能力，请更新实验固件";
    status = "固件需更新";
    sync = "FIRMWARE MISMATCH";
    guideState = "error";
    startLabel = "固件需更新";
  } else {
    title = state.active ? "代码表情运行中" : "Watcher 已连接";
    hint = state.active ? "参数正在通过 SDK Device channel 同步" : "设备通道已就绪，可以发送代码表情";
    status = state.active ? "代码表情运行中" : "Watcher 已连接";
    sync = state.active ? "DEVICE SYNC" : "DEVICE READY";
    guideState = "connected";
    startLabel = "发送到 Watcher";
  }

  guide.dataset.state = guideState;
  byId("connectionTitle").textContent = title;
  byId("connectionHint").textContent = hint;
  byId("statusText").textContent = status;
  byId("statusLamp").parentElement.dataset.active = String(state.deviceConnected && state.expressionSupported);
  byId("syncState").textContent = sync;
  start.textContent = state.sending ? "正在连接…" : startLabel;
  start.disabled = !state.serviceReady || !state.deviceConnected || !state.expressionSupported || state.active || state.sending || state.resumePending;
  stop.disabled = !state.active || state.sending;
  pairingForm.hidden = !state.serviceReady || state.deviceConnected;
  pairingCode.disabled = state.pairing;
  pairingButton.disabled = state.pairing || !/^[0-9]{6}$/.test(pairingCode.value);
  pairingButton.textContent = state.pairing ? "连接中…" : "连接";
  refreshReadouts();
}

function applyStatus(snapshot) {
  const wasActive = state.active;
  const wasConnected = state.deviceConnected;
  const snapshotConnected = Boolean(snapshot.device_connected);
  const snapshotActive = Boolean(snapshot.active) && snapshotConnected;
  state.statusInitialized = true;
  state.serviceReady = true;
  state.deviceConnected = snapshotConnected;
  state.expressionSupported = Boolean(snapshot.expression_supported);
  state.active = snapshotActive;
  if (snapshotActive) {
    state.intentActive = true;
    state.resumePending = false;
  } else if (state.intentActive && (wasActive || (!wasConnected && snapshotConnected))) {
    state.resumePending = true;
  }
  const performance = snapshot.performance || {};
  byId("deviceFps").textContent = performance.sample_valid ? `${Number(performance.measured_fps).toFixed(1)} / ${Number(performance.target_fps).toFixed(1)}` : "—";
  byId("deviceDraw").textContent = performance.sample_valid ? `${Number(performance.draw_ms).toFixed(1)} ms` : "—";
  byId("devicePsram").textContent = Number(performance.psram_free_bytes) > 0 ? `${(Number(performance.psram_free_bytes) / 1048576).toFixed(2)} MB` : "—";
  renderConnectionState();
  scheduleExpressionResume();
}

function scheduleExpressionResume() {
  window.clearTimeout(state.resumeTimer);
  if (!state.resumePending || state.sending || !state.deviceConnected || !state.expressionSupported) return;
  state.resumeTimer = window.setTimeout(() => startExpression({ resume: true }), 180);
}

async function refreshConnectionStatus() {
  if (state.statusBusy) return;
  state.statusBusy = true;
  try {
    const response = await fetch("./api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("SDK service unavailable");
    applyStatus(await response.json());
  } catch (_) {
    if (state.active && state.intentActive) state.resumePending = true;
    state.statusInitialized = true;
    state.serviceReady = false;
    state.deviceConnected = false;
    state.expressionSupported = false;
    state.active = false;
    renderConnectionState();
  } finally {
    state.statusBusy = false;
  }
}

async function pairWatcher(event) {
  event.preventDefault();
  const input = byId("pairingCode");
  const pairingCode = input.value.replace(/\D/g, "").slice(0, 6);
  input.value = pairingCode;
  if (!/^[0-9]{6}$/.test(pairingCode) || state.pairing) return;
  state.pairing = true; renderConnectionState();
  try {
    applyStatus(await api("./api/pair", { pairing_code: pairingCode }));
    input.value = "";
    toast("配对成功，正在建立设备通道");
    await refreshConnectionStatus();
  } catch (error) {
    toast(error.message, "error");
    input.focus();
  } finally {
    state.pairing = false; renderConnectionState();
  }
}

async function startExpression({ resume = false } = {}) {
  if (state.sending) return;
  state.sending = true; renderConnectionState();
  try {
    const snapshot = await api("./api/expression/start", values());
    state.intentActive = true;
    state.resumePending = false;
    applyStatus(snapshot);
    toast(resume ? "连接恢复，代码表情已重新同步" : "Watcher 已切换到代码表情");
  }
  catch (error) {
    state.active = false;
    state.intentActive = false;
    state.resumePending = false;
    toast(error.message, "error");
  }
  finally { state.sending = false; renderConnectionState(); }
}

async function stopExpression() {
  state.intentActive = false;
  state.resumePending = false;
  window.clearTimeout(state.resumeTimer);
  state.sending = true; renderConnectionState();
  try { applyStatus(await api("./api/expression/stop")); toast("已恢复 Watcher 默认显示"); }
  catch (error) { toast(error.message, "error"); }
  finally { state.sending = false; renderConnectionState(); }
}

function queueUpdate() {
  refreshReadouts();
  if (!state.active) return;
  window.clearTimeout(state.debounce);
  state.debounce = window.setTimeout(async () => {
    try { await api("./api/expression/update", values()); byId("syncState").textContent = "DEVICE SYNC"; }
    catch (error) {
      state.active = false;
      state.resumePending = state.intentActive;
      byId("syncState").textContent = "SYNC ERROR";
      renderConnectionState();
      scheduleExpressionResume();
      toast(error.message, "error");
    }
  }, 80);
}

document.querySelectorAll(".preset").forEach((button) => button.addEventListener("click", () => {
  state.preset = button.dataset.preset;
  document.querySelectorAll(".preset").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
  const defaults = presetDefaults[state.preset];
  controls.openness.value = defaults.openness; controls.spacing.value = defaults.spacing; controls.tilt.value = defaults.tilt; controls.tag.value = defaults.tag;
  queueUpdate();
}));
Object.values(controls).forEach((control) => control.addEventListener("input", queueUpdate));
byId("startButton").addEventListener("click", startExpression);
byId("stopButton").addEventListener("click", stopExpression);
byId("pairingForm").addEventListener("submit", pairWatcher);
byId("pairingCode").addEventListener("input", (event) => {
  event.target.value = event.target.value.replace(/\D/g, "").slice(0, 6);
  renderConnectionState();
});
byId("layout").addEventListener("change", (event) => { document.body.dataset.layout = event.target.value; });
byId("scanlines").addEventListener("change", (event) => document.body.classList.toggle("scanlines", event.target.checked));
byId("closeTweaks").addEventListener("click", () => { byId("tweaks").hidden = true; byId("openTweaks").hidden = false; });
byId("openTweaks").addEventListener("click", () => { byId("tweaks").hidden = false; byId("openTweaks").hidden = true; });
window.addEventListener("pagehide", () => { if (state.active) navigator.sendBeacon("./api/expression/stop"); });

document.body.classList.add("scanlines");
refreshReadouts();
renderConnectionState();
refreshConnectionStatus();
window.setInterval(refreshConnectionStatus, 1500);
requestAnimationFrame(render);
