const state = {
  status: null,
  localBusy: false,
  pairingBusy: false,
  hiddenEventIds: new Set(),
};

const elements = {
  connectionBadge: document.querySelector("#connectionBadge"),
  connectionText: document.querySelector("#connectionText"),
  deviceId: document.querySelector("#deviceId"),
  firmwareVersion: document.querySelector("#firmwareVersion"),
  capabilityCount: document.querySelector("#capabilityCount"),
  lastSync: document.querySelector("#lastSync"),
  activeOperation: document.querySelector("#activeOperation"),
  pairingPanel: document.querySelector("#pairingPanel"),
  pairingForm: document.querySelector("#pairingForm"),
  pairingCode: document.querySelector("#pairingCode"),
  deviceIp: document.querySelector("#deviceIp"),
  pairingButton: document.querySelector("#pairingButton"),
  pairingResult: document.querySelector("#pairingResult"),
  capabilityGrid: document.querySelector("#capabilityGrid"),
  capabilitySummary: document.querySelector("#capabilitySummary"),
  eventLog: document.querySelector("#eventLog"),
  runAllButton: document.querySelector("#runAllButton"),
  playAudioButton: document.querySelector("#playAudioButton"),
  stopAudioButton: document.querySelector("#stopAudioButton"),
  capturePhotoButton: document.querySelector("#capturePhotoButton"),
  recordMicrophoneButton: document.querySelector("#recordMicrophoneButton"),
  recordDuration: document.querySelector("#recordDuration"),
  durationValue: document.querySelector("#durationValue"),
  cameraPreview: document.querySelector("#cameraPreview"),
  cameraEmpty: document.querySelector("#cameraEmpty"),
  downloadPhoto: document.querySelector("#downloadPhoto"),
  downloadRecording: document.querySelector("#downloadRecording"),
  recordingPlayer: document.querySelector("#recordingPlayer"),
  waveform: document.querySelector("#waveform"),
  audioResult: document.querySelector("#audioResult"),
  cameraResult: document.querySelector("#cameraResult"),
  microphoneResult: document.querySelector("#microphoneResult"),
  toast: document.querySelector("#toast"),
  footerClock: document.querySelector("#footerClock"),
};

const actionLabels = {
  play_audio: "扬声器播放",
  stop_audio: "停止播放",
  capture_photo: "相机拍照",
  record_microphone: "麦克风录音",
  device_pairing: "设备配对",
  system: "系统",
};

const pairingErrors = {
  invalid_pairing_code: "配对码必须是 6 位数字",
  device_slot_occupied: "当前已有设备连接或正在配对",
  pairing_not_found: "未发现对应设备，请确认配对码和网络后重试",
  device_connect_timeout: "设备连接超时，请重新获取配对码后重试",
  reconnect_timeout: "设备重连超时，请重新配对",
  pairing_unavailable: "无法连接 SDK Daemon 的配对服务",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { payload = {}; }
  if (!response.ok) {
    throw new Error(localizeError(payload.message || payload.detail, response.status));
  }
  return payload;
}

function hasCapability(name) {
  return Boolean(state.status?.capabilities?.includes(name));
}

function actionLabel(action) {
  return actionLabels[action] || action || "未知操作";
}

function localizeError(message, status) {
  if (!message) return `请求失败（状态码 ${status}）`;
  if (typeof message !== "string") return `请求失败（状态码 ${status}）`;
  const busyMatch = message.match(/^media lab is busy with (.+)$/);
  if (busyMatch) return `媒体实验室正忙于${actionLabel(busyMatch[1])}`;
  const durationMatch = message.match(/^duration must be (.+)$/);
  if (durationMatch) return `录音时长必须为 ${durationMatch[1]}`;
  if (pairingErrors[message]) return pairingErrors[message];
  return message;
}

function localizeEvent(event) {
  if (event.message === "Media Lab ready") return "媒体实验室已就绪";
  if (event.message === "Device pairing started") return "设备配对已开始";
  if (event.message === "Audio stop requested") return "已请求停止播放";
  const label = actionLabel(event.action);
  if (event.message.endsWith(" started")) return `${label}已开始`;
  if (event.message.endsWith(" completed")) return `${label}已完成`;
  const failedAt = event.message.indexOf(" failed:");
  if (failedAt >= 0) return `${label}失败：${event.message.slice(failedAt + 8).trim()}`;
  return event.message;
}

function renderStatus(status) {
  state.status = status;
  elements.connectionBadge.dataset.state = status.connected ? "online" : "offline";
  elements.connectionText.textContent = status.connected ? "设备在线" : "设备已断开";
  elements.deviceId.textContent = status.device?.device_id || "未识别";
  elements.firmwareVersion.textContent = status.device?.firmware_version || "未知";
  elements.capabilityCount.textContent = String(status.capabilities.length).padStart(2, "0");
  elements.lastSync.textContent = new Date().toLocaleTimeString([], { hour12: false });
  elements.activeOperation.textContent = !status.connected
    ? "设备已断开，请重新连接后再测试"
    : status.active_action
      ? `正在执行 / ${actionLabel(status.active_action)}`
      : state.localBusy ? "操作已下发" : "系统空闲";

  const pairingState = status.connection?.state || "unavailable";
  const pairingInProgress = ["discovering", "connecting", "reconnecting"].includes(pairingState);
  elements.pairingPanel.hidden = status.connected;
  elements.pairingButton.disabled = state.pairingBusy || pairingInProgress;
  elements.pairingCode.disabled = state.pairingBusy || pairingInProgress;
  elements.deviceIp.disabled = state.pairingBusy || pairingInProgress;
  if (status.connected) {
    setResult(elements.pairingResult, "设备配对成功", "ok");
  } else if (state.pairingBusy || pairingState === "discovering") {
    setResult(elements.pairingResult, "正在发现设备…", "running");
  } else if (pairingState === "connecting" || pairingState === "reconnecting") {
    setResult(elements.pairingResult, "已发现设备，正在建立连接…", "running");
  } else if (status.connection?.last_error) {
    setResult(
      elements.pairingResult,
      pairingErrors[status.connection.last_error] || status.connection.last_error,
      "error",
    );
  }

  document.querySelectorAll(".station[data-capability]").forEach((station) => {
    const available = status.capabilities.includes(station.dataset.capability);
    station.dataset.available = String(status.connected && available);
    station.querySelector(".capability-state").textContent = !status.connected
      ? "设备离线"
      : available ? "已就绪" : "设备未声明";
  });

  const busy = status.busy || state.localBusy;
  const unavailable = busy || !status.connected;
  elements.playAudioButton.disabled = unavailable || !hasCapability("audio.stream");
  elements.stopAudioButton.disabled = unavailable || !hasCapability("audio.stream");
  elements.capturePhotoButton.disabled = unavailable || !hasCapability("camera.capture");
  elements.recordMicrophoneButton.disabled = unavailable || !hasCapability("microphone");
  elements.runAllButton.disabled = unavailable || !["audio.stream", "camera.capture", "microphone"].every(hasCapability);

  elements.capabilityGrid.replaceChildren(...status.capabilities.map((capability) => {
    const chip = document.createElement("span");
    chip.className = "capability-chip";
    chip.dataset.media = String(["audio.stream", "camera.capture", "microphone"].includes(capability));
    chip.textContent = capability;
    return chip;
  }));
  elements.capabilitySummary.textContent = status.connected
    ? `${status.capabilities.length} 项能力在线`
    : `设备离线 · ${status.capabilities.length} 项上次协商`;
  renderEvents(status.events || []);
  restoreArtifacts(status.artifacts || {});
}

function renderEvents(events) {
  const visible = events.filter((event) => !state.hiddenEventIds.has(event.id)).slice().reverse();
  elements.eventLog.replaceChildren(...visible.map((event) => {
    const item = document.createElement("li");
    item.dataset.tone = event.tone;
    const time = document.createElement("time");
    time.textContent = new Date(event.timestamp * 1000).toLocaleTimeString([], { hour12: false });
    const message = document.createElement("span");
    message.textContent = localizeEvent(event);
    item.append(time, message);
    return item;
  }));
}

function restoreArtifacts(artifacts) {
  const photo = artifacts["camera.jpg"];
  if (photo && !elements.cameraPreview.src) showPhoto(photo.url);
  const recording = artifacts["microphone.wav"];
  if (recording && !elements.recordingPlayer.src) showRecording(recording.url, true);
}

async function refreshStatus({ quiet = true } = {}) {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    elements.connectionBadge.dataset.state = "offline";
    elements.connectionText.textContent = "实验室离线";
    if (!quiet) notify(error.message, "error");
  }
}

async function runAction({ path, result, pending, complete, body, station }) {
  if (state.localBusy) return null;
  if (!state.status?.connected) {
    const error = new Error("设备已断开，请重新连接后再测试");
    notify(error.message, "error");
    throw error;
  }
  state.localBusy = true;
  if (station) station.dataset.running = "true";
  setResult(result, pending, "running");
  await refreshStatus();
  try {
    const payload = await api(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
    const message = complete(payload);
    setResult(result, message, "ok");
    notify(message, "ok");
    return payload;
  } catch (error) {
    setResult(result, error.message, "error");
    notify(error.message, "error");
    throw error;
  } finally {
    state.localBusy = false;
    if (station) station.dataset.running = "false";
    await refreshStatus();
  }
}

async function pairDevice() {
  const pairingCode = elements.pairingCode.value.trim();
  const deviceIp = elements.deviceIp.value.trim();
  if (!/^[0-9]{6}$/.test(pairingCode)) {
    const message = "配对码必须是 6 位数字";
    setResult(elements.pairingResult, message, "error");
    notify(message, "error");
    elements.pairingCode.focus();
    return;
  }
  state.pairingBusy = true;
  setResult(elements.pairingResult, "正在提交配对请求…", "running");
  renderStatus(state.status);
  try {
    await api("/api/device/pair", {
      method: "POST",
      body: JSON.stringify({ pairing_code: pairingCode, device_ip: deviceIp || null }),
    });
    elements.pairingCode.value = "";
    setResult(elements.pairingResult, "正在发现设备…", "running");
    notify("配对请求已提交，请保持机器人开机", "ok");
  } catch (error) {
    setResult(elements.pairingResult, error.message, "error");
    notify(error.message, "error");
  } finally {
    state.pairingBusy = false;
    await refreshStatus();
  }
}

function setResult(element, message, tone) {
  element.textContent = message;
  element.dataset.tone = tone;
}

function notify(message, tone = "ok") {
  elements.toast.textContent = message;
  elements.toast.dataset.tone = tone;
  elements.toast.dataset.visible = "true";
  window.clearTimeout(notify.timer);
  notify.timer = window.setTimeout(() => { elements.toast.dataset.visible = "false"; }, 3800);
}

function showPhoto(url) {
  elements.cameraPreview.src = url;
  elements.cameraPreview.hidden = false;
  elements.cameraEmpty.hidden = true;
  elements.downloadPhoto.href = url;
  elements.downloadPhoto.hidden = false;
}

async function showRecording(url, redraw = true) {
  elements.recordingPlayer.src = url;
  elements.recordingPlayer.hidden = false;
  elements.downloadRecording.href = url;
  elements.downloadRecording.hidden = false;
  if (redraw) await drawWaveform(url);
}

async function drawWaveform(url) {
  const context = elements.waveform.getContext("2d");
  const width = elements.waveform.width;
  const height = elements.waveform.height;
  context.fillStyle = "#090c0b";
  context.fillRect(0, 0, width, height);
  try {
    const audioContext = new AudioContext();
    const bytes = await (await fetch(url, { cache: "no-store" })).arrayBuffer();
    const buffer = await audioContext.decodeAudioData(bytes.slice(0));
    const samples = buffer.getChannelData(0);
    const bucket = Math.max(1, Math.floor(samples.length / width));
    context.strokeStyle = "#ffb650";
    context.lineWidth = 1.4;
    context.beginPath();
    for (let x = 0; x < width; x += 1) {
      let peak = 0;
      const start = x * bucket;
      for (let index = start; index < Math.min(samples.length, start + bucket); index += 1) {
        peak = Math.max(peak, Math.abs(samples[index]));
      }
      const amplitude = Math.max(1, peak * height * 0.46);
      context.moveTo(x, height / 2 - amplitude);
      context.lineTo(x, height / 2 + amplitude);
    }
    context.stroke();
    await audioContext.close();
  } catch (error) {
    context.fillStyle = "#929b94";
    context.font = "14px Cascadia Mono, monospace";
    context.fillText("无法解码录音波形", 20, height / 2);
  }
}

function drawEmptyWaveform() {
  const context = elements.waveform.getContext("2d");
  const width = elements.waveform.width;
  const height = elements.waveform.height;
  context.fillStyle = "#090c0b";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "#303a35";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(0, height / 2);
  context.lineTo(width, height / 2);
  context.stroke();
  context.fillStyle = "#929b94";
  context.font = "13px Cascadia Mono, monospace";
  context.fillText("等待 PCM 音频信号", 18, height / 2 - 14);
}

async function playAudio() {
  return runAction({
    path: "/api/actions/play-audio",
    result: elements.audioResult,
    pending: "正在传输 PCM 示例音频…",
    complete: (payload) => `播放完成 · ${formatBytes(payload.bytes)}`,
    station: document.querySelector(".station-audio"),
  });
}

async function capturePhoto() {
  const payload = await runAction({
    path: "/api/actions/capture-photo",
    result: elements.cameraResult,
    pending: "正在请求 JPEG 画面…",
    complete: (value) => `照片接收完成 · ${formatBytes(value.bytes)}`,
    station: document.querySelector(".station-camera"),
  });
  if (payload) showPhoto(payload.artifact_url);
  return payload;
}

async function recordMicrophone() {
  const duration = Number(elements.recordDuration.value);
  const payload = await runAction({
    path: "/api/actions/record-microphone",
    body: { duration },
    result: elements.microphoneResult,
    pending: `正在录制 ${duration} 秒…`,
    complete: (value) => `${value.duration_seconds.toFixed(3)} 秒 · 丢帧 ${value.dropped_frames} · 解码失败 ${value.decode_failures}`,
    station: document.querySelector(".station-microphone"),
  });
  if (payload) await showRecording(payload.artifact_url);
  return payload;
}

async function runAll() {
  const allowed = window.confirm("媒体全检将播放声音、拍摄一张照片并录制麦克风。是否继续？");
  if (!allowed) return;
  try {
    await playAudio();
    await capturePhoto();
    await recordMicrophone();
    notify("媒体全检通过：三条链路均已完成", "ok");
  } catch (_) {
    notify("媒体全检已在首个失败环节停止", "error");
  }
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(2)} MiB`;
}

elements.playAudioButton.addEventListener("click", () => { playAudio().catch(() => {}); });
elements.pairingForm.addEventListener("submit", (event) => {
  event.preventDefault();
  pairDevice();
});
elements.pairingCode.addEventListener("input", () => {
  elements.pairingCode.value = elements.pairingCode.value.replace(/[^0-9]/g, "").slice(0, 6);
});
elements.stopAudioButton.addEventListener("click", async () => {
  try { await api("/api/actions/stop-audio", { method: "POST" }); notify("已请求停止播放"); }
  catch (error) { notify(error.message, "error"); }
});
elements.capturePhotoButton.addEventListener("click", () => { capturePhoto().catch(() => {}); });
elements.recordMicrophoneButton.addEventListener("click", () => { recordMicrophone().catch(() => {}); });
elements.runAllButton.addEventListener("click", runAll);
elements.recordDuration.addEventListener("input", () => { elements.durationValue.textContent = elements.recordDuration.value; });
document.querySelector("#clearVisualLog").addEventListener("click", () => {
  (state.status?.events || []).forEach((event) => state.hiddenEventIds.add(event.id));
  renderEvents(state.status?.events || []);
});

setInterval(() => {
  elements.footerClock.textContent = new Date().toLocaleTimeString([], { hour12: false });
}, 1000);
setInterval(refreshStatus, 1000);
refreshStatus({ quiet: false });
drawEmptyWaveform();
