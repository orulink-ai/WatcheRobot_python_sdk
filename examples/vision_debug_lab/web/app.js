import { buildPreviewWebSocketUrl, parseVisionPacket } from "./preview-packet.mjs";
import { deadZoneRect, scaleFace, targetPoint } from "./overlay-geometry.mjs";

const $ = (id) => document.getElementById(id);
const canvas = $("visionCanvas");
const context = canvas.getContext("2d");
const state = {
  socket: null,
  lastEvent: 0,
  recording: false,
  pendingFrame: null,
  drawingFrame: false,
};

function fmt(value, suffix = "") {
  return value === null || value === undefined ? "—" : `${value}${suffix}`;
}

function toast(message, tone = "ok") {
  const node = $("toast");
  node.textContent = message;
  node.dataset.tone = tone;
  node.classList.add("visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove("visible"), 3200);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.detail || "操作失败");
  return payload;
}

function setSignal(node, tone) {
  node.dataset.tone = tone;
}

function renderStatus(snapshot) {
  const { connection, vision, session, findings } = snapshot;
  $("connectionState").textContent = connection.online ? "机器人已连接" : "机器人未连接";
  setSignal($("connectionLamp"), connection.online ? "ok" : "error");
  $("backendValue").textContent = (vision.backend || "—").toUpperCase();
  $("backendDetail").textContent = `${vision.health || "unknown"} · code ${vision.status_code ?? "—"}`;
  $("himaxValue").textContent = vision.himax_connected ? "CONNECTED" : "DISCONNECTED";
  $("himaxDetail").textContent = vision.initialized ? "初始化完成" : "未初始化";
  const model = vision.model;
  $("modelValue").textContent = model?.name || "NO MODEL";
  $("modelDetail").textContent = model ? `槽位 ${model.id} · ${model.task}` : "模型元数据不可用";
  const capabilities = vision.capabilities || {};
  const enabled = Object.entries(capabilities).filter(([, value]) => value).map(([key]) => key);
  $("capabilityValue").textContent = `${enabled.length} / ${Object.keys(capabilities).length || 0}`;
  $("capabilityDetail").textContent = enabled.join(" · ") || "无可用能力";
  $("findings").replaceChildren(...findings.map((finding) => {
    const item = document.createElement("p");
    item.dataset.tone = finding.severity;
    const severity = document.createElement("span");
    severity.textContent = finding.severity.toUpperCase();
    item.append(severity, document.createTextNode(finding.message));
    return item;
  }));
  $("fpsMetric").textContent = fmt(session.fps);
  $("gapMetric").textContent = fmt(session.gap_p95_ms, " ms");
  $("ageMetric").textContent = fmt(session.age_p95_ms, " ms");
  $("inferenceMetric").textContent = `${fmt(session.inference_avg_ms)} / ${fmt(session.inference_p95_ms)} ms`;
  $("applicationMetric").textContent = fmt(session.application_p95_ms, " ms");
  $("missingMetric").textContent = fmt(session.missing_sequences);
  $("jpegMetric").textContent = session.jpeg_avg_bytes ? `${(session.jpeg_avg_bytes / 1024).toFixed(1)} KB` : "—";
  $("streamState").textContent = session.running ? "LIVE" : "STANDBY";
  setSignal($("streamLamp"), session.running ? "ok" : "idle");
  state.recording = Boolean(session.recording?.active);
  renderRecording();
}

function renderRecording() {
  $("recordingButton").classList.toggle("active", state.recording);
  $("recordingButton").lastChild.textContent = state.recording ? "停止并保存录制" : "开始录制 JPEG + JSONL";
  $("recordingState").textContent = state.recording ? "● RECORDING" : "NOT RECORDING";
  $("recordingState").classList.toggle("active", state.recording);
}

async function refreshStatus() {
  try { renderStatus(await request("/api/status")); } catch (error) { toast(error.message, "error"); }
}

function drawOverlay(metadata) {
  const width = canvas.width;
  const height = canvas.height;
  context.save();
  context.lineWidth = 3;
  context.font = "22px Cascadia Code, monospace";
  for (const face of metadata.faces || []) {
    const box = scaleFace(face, metadata.width, metadata.height, width, height);
    context.strokeStyle = face.target ? "#ffb547" : "#31d4bd";
    context.strokeRect(box.x, box.y, box.width, box.height);
    context.fillStyle = context.strokeStyle;
    context.fillRect(box.x, Math.max(0, box.y - 28), 154, 28);
    context.fillStyle = "#07100f";
    context.fillText(`${face.target ? "TARGET" : "FACE"} ${face.score}`, box.x + 7, Math.max(21, box.y - 7));
  }
  const zone = deadZoneRect(width, height);
  context.setLineDash([10, 8]);
  context.lineWidth = 2;
  context.strokeStyle = "rgba(255,181,71,.75)";
  context.strokeRect(zone.x, zone.y, zone.width, zone.height);
  context.setLineDash([]);
  context.strokeStyle = "rgba(255,255,255,.42)";
  context.beginPath(); context.moveTo(width / 2 - 16, height / 2); context.lineTo(width / 2 + 16, height / 2); context.moveTo(width / 2, height / 2 - 16); context.lineTo(width / 2, height / 2 + 16); context.stroke();
  const target = targetPoint(metadata, width, height);
  if (target) {
    context.strokeStyle = "#ffb547";
    context.beginPath(); context.moveTo(width / 2, height / 2); context.lineTo(target.x, target.y); context.stroke();
    context.beginPath(); context.arc(target.x, target.y, 7, 0, Math.PI * 2); context.fillStyle = "#ffb547"; context.fill();
  }
  context.restore();
}

async function drawPacket(buffer) {
  const { metadata, jpeg } = parseVisionPacket(buffer);
  const image = await createImageBitmap(new Blob([jpeg], { type: "image/jpeg" }));
  canvas.width = metadata.width * 2;
  canvas.height = metadata.height * 2;
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  drawOverlay(metadata);
  $("emptyState").hidden = true;
  $("frameSequence").textContent = `SEQ ${metadata.sequence}`;
  $("frameSize").textContent = `JPEG ${(metadata.jpeg_bytes / 1024).toFixed(1)} KB`;
  $("browserMetric").textContent = `${Math.max(0, Date.now() - metadata.received_at * 1000).toFixed(1)} ms`;
  const telemetry = metadata.telemetry || {};
  $("errorX").textContent = fmt(telemetry.error_x_percent, "%");
  $("errorY").textContent = fmt(telemetry.error_y_percent, "%");
  $("panVelocity").textContent = fmt(telemetry.pan_velocity_deg_s, "°/s");
  $("tiltVelocity").textContent = fmt(telemetry.tilt_velocity_deg_s, "°/s");
}

async function renderLatestFrame() {
  if (state.drawingFrame) return;
  state.drawingFrame = true;
  try {
    while (state.pendingFrame) {
      const buffer = state.pendingFrame;
      state.pendingFrame = null;
      await drawPacket(buffer);
    }
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.drawingFrame = false;
  }
}

function connectPreviewSocket() {
  if (state.socket && state.socket.readyState < 2) return;
  const socket = new WebSocket(buildPreviewWebSocketUrl());
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      state.pendingFrame = event.data;
      renderLatestFrame();
    }
  };
  socket.onclose = () => { state.socket = null; };
  state.socket = socket;
}

async function startPreview() {
  const [width, height] = $("resolution").value.split("x").map(Number);
  try {
    connectPreviewSocket();
    await request("/api/preview/start", { method: "POST", body: JSON.stringify({ width, height, frame_stride: Number($("stride").value), stop_policy: "hold" }) });
    toast("端侧人脸预览已启动");
    await refreshStatus();
  } catch (error) { toast(error.message, "error"); }
}

async function stopPreview(policy) {
  try {
    await request("/api/preview/stop", { method: "POST", body: JSON.stringify({ policy }) });
    toast(policy === "hold" ? "预览已停止，机器人保持当前位置" : "预览已停止，机器人回到中心");
    await refreshStatus();
  } catch (error) { toast(error.message, "error"); }
}

async function toggleRecording() {
  try {
    const path = state.recording ? "/api/recording/stop" : "/api/recording/start";
    const result = await request(path, { method: "POST" });
    state.recording = !state.recording;
    renderRecording();
    toast(state.recording ? "数据集录制已开始" : `录制已保存：${result.relative_path}`);
  } catch (error) { toast(error.message, "error"); }
}

async function exportReport() {
  try {
    const result = await request("/api/diagnostics/export", { method: "POST" });
    const link = document.createElement("a"); link.href = result.artifact_url; link.download = "vision-diagnostic-report.json"; link.click();
    toast("诊断报告已导出");
  } catch (error) { toast(error.message, "error"); }
}

async function pollEvents() {
  try {
    const payload = await request(`/api/events?after=${state.lastEvent}`);
    if (payload.events.length) {
      const list = $("eventList");
      if (list.querySelector(".muted")) list.replaceChildren();
      for (const event of payload.events) {
        state.lastEvent = Math.max(state.lastEvent, event.id);
        const item = document.createElement("li"); item.dataset.tone = event.tone;
        const timestamp = document.createElement("time");
        timestamp.textContent = new Date(event.timestamp * 1000).toLocaleTimeString();
        const message = document.createElement("span");
        message.textContent = event.message;
        item.append(timestamp, message);
        list.prepend(item);
      }
    }
  } catch (_) { /* status polling reports connection errors */ }
}

$("startPreview").addEventListener("click", startPreview);
$("stopHold").addEventListener("click", () => stopPreview("hold"));
$("stopRecenter").addEventListener("click", () => stopPreview("recenter"));
$("recordingButton").addEventListener("click", toggleRecording);
$("exportReport").addEventListener("click", exportReport);
$("refreshStatus").addEventListener("click", refreshStatus);
connectPreviewSocket();
refreshStatus();
pollEvents();
window.setInterval(refreshStatus, 2000);
window.setInterval(pollEvents, 1000);
