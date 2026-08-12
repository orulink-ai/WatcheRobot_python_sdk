import { evaluateRtcAudioHealth } from "./rtc-audio-health.mjs";

const state = {
  status: null,
  localBusy: false,
  pairingBusy: false,
  hiddenEventIds: new Set(),
  rtc: {
    generation: 0,
    mode: null,
    peer: null,
    channel: null,
    localStream: null,
    remoteStream: null,
    eventCursor: 0,
    pollTimer: null,
    heartbeatTimer: null,
    feedbackTimer: null,
    remoteCandidates: [],
    decodeBusy: false,
    pendingFrame: null,
    lastSequence: null,
    receivedFrames: 0,
    displayedFrames: 0,
    droppedFrames: 0,
    frameTimes: [],
    lastFrameAt: 0,
    rttUs: 0,
    browserAudioSent: 0,
    browserAudioReceived: 0,
    audioConnectedAt: 0,
    audioHealthState: "idle",
  },
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
  liveVideoPanel: document.querySelector("#liveVideoPanel"),
  liveVideoCapability: document.querySelector("#liveVideoCapability"),
  startLiveVideoButton: document.querySelector("#startLiveVideoButton"),
  stopLiveVideoButton: document.querySelector("#stopLiveVideoButton"),
  liveVideoResult: document.querySelector("#liveVideoResult"),
  liveVideoStage: document.querySelector("#liveVideoStage"),
  liveVideoCanvas: document.querySelector("#liveVideoCanvas"),
  liveVideoState: document.querySelector("#liveVideoState"),
  liveVideoFps: document.querySelector("#liveVideoFps"),
  liveVideoResolution: document.querySelector("#liveVideoResolution"),
  liveVideoDrops: document.querySelector("#liveVideoDrops"),
  liveVideoIndicator: document.querySelector("#liveVideoIndicator"),
  liveVideoFrameAge: document.querySelector("#liveVideoFrameAge"),
  rtcAudioPanel: document.querySelector("#rtcAudioPanel"),
  rtcAudioCapability: document.querySelector("#rtcAudioCapability"),
  startRtcAudioButton: document.querySelector("#startRtcAudioButton"),
  stopRtcAudioButton: document.querySelector("#stopRtcAudioButton"),
  rtcAudioResult: document.querySelector("#rtcAudioResult"),
  rtcAudioConsole: document.querySelector("#rtcAudioConsole"),
  rtcAudioState: document.querySelector("#rtcAudioState"),
  rtcAudioLocalState: document.querySelector("#rtcAudioLocalState"),
  rtcAudioUpPackets: document.querySelector("#rtcAudioUpPackets"),
  rtcAudioDownPackets: document.querySelector("#rtcAudioDownPackets"),
  rtcAudioDeviceCapture: document.querySelector("#rtcAudioDeviceCapture"),
  rtcAudioDeviceTx: document.querySelector("#rtcAudioDeviceTx"),
  rtcRemoteAudio: document.querySelector("#rtcRemoteAudio"),
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
  live_video: "实时视频",
  rtc_audio: "全双工音频",
  system: "系统",
};

const pairingErrors = {
  invalid_pairing_code: "配对码必须是 6 位数字",
  device_slot_occupied: "当前已有设备连接或正在配对",
  pairing_not_found: "未发现对应设备，请确认配对码和网络后重试",
  device_connect_timeout: "设备连接超时，请重新获取配对码后重试",
  reconnect_timeout: "设备重连超时，请重新配对",
  pairing_unavailable: "无法连接 SDK Daemon 的配对服务",
  "RTC session is not active": "RTC 会话尚未开启",
};

const rtcErrors = {
  video_source_timeout: "相机视频源未输出画面；请确认 HX6538 已安装配套视频桥固件",
  peer_connection_failed: "浏览器与设备的实时连接建立失败",
  mjpeg_start_failed: "设备相机推流器启动失败",
  mjpeg_data_channel_closed: "实时视频数据通道已断开",
  heartbeat_timeout: "实时视频心跳超时",
  audio_capture_failed: "设备音频采集启动失败",
  audio_render_failed: "设备扬声器播放启动失败",
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
  if (message.startsWith("Robot firmware does not advertise required RTC capabilities:")) {
    return "当前固件未声明所需 RTC 能力，请更新并重新连接设备";
  }
  if (pairingErrors[message]) return pairingErrors[message];
  if (rtcErrors[message]) return rtcErrors[message];
  return message;
}

function localizeEvent(event) {
  if (event.message === "Media Lab ready") return "媒体实验室已就绪";
  if (event.message === "Device pairing started") return "设备配对已开始";
  if (event.message === "Audio stop requested") return "已请求停止播放";
  const label = actionLabel(event.action);
  if (event.message.endsWith(" started")) return `${label}已开始`;
  if (event.message.endsWith(" completed")) return `${label}已完成`;
  if (event.message.endsWith(" stopped")) return `${label}已停止`;
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
  const liveAvailable = status.connected && hasCapability("rtc.video.mjpeg.v1");
  const liveActive = state.rtc.mode === "video" || status.active_action === "live_video";
  const rtcAudioAvailable = status.connected && hasCapability("rtc.audio.full_duplex.v1");
  const rtcAudioActive = state.rtc.mode === "audio" || status.active_action === "rtc_audio";
  elements.liveVideoPanel.dataset.available = String(liveAvailable);
  elements.liveVideoCapability.textContent = !status.connected
    ? "设备离线"
    : liveAvailable ? "已就绪" : "需要新固件";
  elements.startLiveVideoButton.disabled = busy || liveActive || !liveAvailable;
  elements.stopLiveVideoButton.disabled = !liveActive;
  elements.rtcAudioPanel.dataset.available = String(rtcAudioAvailable);
  elements.rtcAudioCapability.textContent = !status.connected
    ? "设备离线"
    : rtcAudioAvailable ? "已就绪" : "需要新固件";
  elements.startRtcAudioButton.disabled = busy || rtcAudioActive || !rtcAudioAvailable;
  elements.stopRtcAudioButton.disabled = !rtcAudioActive;
  updateRtcAudioHealth();
  elements.playAudioButton.disabled = unavailable || !hasCapability("audio.stream");
  elements.stopAudioButton.disabled = unavailable || !hasCapability("audio.stream");
  elements.capturePhotoButton.disabled = unavailable || !hasCapability("camera.capture");
  elements.recordMicrophoneButton.disabled = unavailable || !hasCapability("microphone");
  elements.runAllButton.disabled = unavailable || !["audio.stream", "camera.capture", "microphone"].every(hasCapability);

  elements.capabilityGrid.replaceChildren(...status.capabilities.map((capability) => {
    const chip = document.createElement("span");
    chip.className = "capability-chip";
    chip.dataset.media = String(["audio.stream", "camera.capture", "microphone", "rtc.video.mjpeg.v1", "rtc.audio.full_duplex.v1"].includes(capability));
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

function resetLiveVideoMetrics() {
  Object.assign(state.rtc, {
    eventCursor: 0,
    remoteCandidates: [],
    decodeBusy: false,
    pendingFrame: null,
    lastSequence: null,
    receivedFrames: 0,
    displayedFrames: 0,
    droppedFrames: 0,
    frameTimes: [],
    lastFrameAt: 0,
    rttUs: 0,
  });
  elements.liveVideoFps.textContent = "0.0 FPS";
  elements.liveVideoResolution.textContent = "—";
  elements.liveVideoDrops.textContent = "0";
  elements.liveVideoFrameAge.textContent = "NO FRAME";
}

function rtcEndpoint(action) {
  const namespace = state.rtc.mode === "audio" ? "rtc" : "video";
  return `/api/${namespace}/session/${action}`;
}

function setRtcAudioState(value, message = null) {
  const normalized = value === "connected" ? "live"
    : ["starting", "signaling", "connecting"].includes(value) ? "connecting"
      : "idle";
  elements.rtcAudioConsole.dataset.state = normalized;
  elements.rtcAudioState.textContent = String(value || "idle").toUpperCase();
  if (message) setResult(elements.rtcAudioResult, message, normalized === "idle" ? "error" : "running");
}

function updateRtcAudioHealth() {
  if (state.rtc.mode !== "audio" || !state.rtc.peer) return;
  const deviceStats = state.status?.rtc?.stats || {};
  const captureFrames = Number(deviceStats.audio_capture_frames || 0);
  const txPackets = Number(deviceStats.audio_tx_packets || 0);
  const txErrors = Number(deviceStats.audio_tx_errors || 0);
  elements.rtcAudioDeviceCapture.textContent = String(captureFrames);
  elements.rtcAudioDeviceTx.textContent = txErrors > 0 ? `${txPackets} / 错误 ${txErrors}` : String(txPackets);
  const health = evaluateRtcAudioHealth({
    peerConnected: state.rtc.peer.connectionState === "connected",
    browserTxPackets: state.rtc.browserAudioSent,
    browserRxPackets: state.rtc.browserAudioReceived,
    deviceCaptureFrames: captureFrames,
    deviceTxPackets: txPackets,
    deviceTxErrors: txErrors,
    elapsedMs: state.rtc.audioConnectedAt ? performance.now() - state.rtc.audioConnectedAt : 0,
  });
  if (health.state === state.rtc.audioHealthState && health.state !== "failed") return;
  state.rtc.audioHealthState = health.state;
  if (health.state === "healthy") {
    setRtcAudioState("connected");
    setResult(elements.rtcAudioResult, "真全双工已验证：电脑与 Watcher 双向音频都在传输", "ok");
  } else if (health.state === "degraded") {
    setRtcAudioState("connected");
    setResult(elements.rtcAudioResult, `双向音频已建立，但机器人发送出现 ${txErrors} 次错误`, "error");
  } else if (health.state === "failed") {
    const missingDeviceCapture = health.missing.includes("device_capture");
    const message = missingDeviceCapture
      ? "机器人麦克风没有产生音频帧，请检查麦克风采集与音频资源占用"
      : "机器人麦克风音频未到达电脑，请查看机器人发送计数与错误码";
    setRtcAudioState("failed", message);
  } else if (health.state === "verifying") {
    setRtcAudioState("connecting", "媒体连接已建立，正在验证机器人麦克风上行…");
  }
}

function setRtcSessionState(value, message = null) {
  if (state.rtc.mode === "audio") setRtcAudioState(value, message);
  else setLiveVideoState(value, message);
}

async function startRtcAudio() {
  if (state.rtc.mode || state.rtc.peer || !hasCapability("rtc.audio.full_duplex.v1")) return;
  const generation = state.rtc.generation + 1;
  state.rtc.generation = generation;
  state.rtc.mode = "audio";
  resetLiveVideoMetrics();
  elements.rtcAudioUpPackets.textContent = "0";
  elements.rtcAudioDownPackets.textContent = "0";
  elements.rtcAudioDeviceCapture.textContent = "0";
  elements.rtcAudioDeviceTx.textContent = "0";
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "starting";
  setRtcAudioState("starting", "正在请求电脑麦克风权限…");
  elements.startRtcAudioButton.disabled = true;
  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持麦克风采集");
    }
    const localStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    if (state.rtc.generation !== generation || state.rtc.mode !== "audio") {
      for (const track of localStream.getTracks()) track.stop();
      return;
    }
    state.rtc.localStream = localStream;
    elements.rtcAudioLocalState.textContent = "采集中";
    await api("/api/rtc/session/start", {
      method: "POST",
      body: JSON.stringify({ mode: "audio" }),
    });
    if (state.rtc.generation !== generation || state.rtc.mode !== "audio") {
      try { await api("/api/rtc/session/stop", { method: "POST" }); } catch (_) {}
      return;
    }
    const peer = new RTCPeerConnection({ iceServers: [] });
    state.rtc.peer = peer;
    for (const track of localStream.getAudioTracks()) peer.addTrack(track, localStream);
    peer.addEventListener("track", (event) => {
      const remoteStream = event.streams[0] || new MediaStream([event.track]);
      state.rtc.remoteStream = remoteStream;
      elements.rtcRemoteAudio.srcObject = remoteStream;
      elements.rtcRemoteAudio.play().catch(() => {
        setResult(elements.rtcAudioResult, "下行音频已到达，请点击播放器启用声音", "running");
      });
    });
    bindRtcPeerEvents(peer);
    startRtcControlLoops();
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await api("/api/rtc/session/signal", {
      method: "POST",
      body: JSON.stringify({ kind: "offer", sdp: offer.sdp }),
    });
    setRtcAudioState("signaling", "电脑麦克风已开启，等待 Watcher 应答…");
    await refreshStatus();
  } catch (error) {
    const message = error?.name === "NotAllowedError"
      ? "未获得电脑麦克风权限，请允许后重试"
      : error?.name === "NotFoundError"
        ? "未检测到可用的电脑麦克风"
        : error.message;
    await failRtcAudio(message);
  }
}

function bindRtcPeerEvents(peer) {
  peer.addEventListener("connectionstatechange", () => {
    const connectionState = peer.connectionState;
    if (connectionState === "connected" && state.rtc.mode === "audio") {
      state.rtc.audioConnectedAt = performance.now();
      state.rtc.audioHealthState = "connecting";
      setRtcAudioState("connecting", "媒体连接已建立，正在验证机器人麦克风上行…");
    }
    if (["failed", "disconnected", "closed"].includes(connectionState) && state.rtc.peer) {
      failRtcSession(`WebRTC 连接${connectionState === "failed" ? "失败" : "已断开"}`);
    }
  });
  peer.addEventListener("icecandidate", (event) => {
    if (!event.candidate || !state.rtc.peer) return;
    api(rtcEndpoint("signal"), {
      method: "POST",
      body: JSON.stringify({
        kind: "candidate",
        candidate: event.candidate.candidate,
        sdp_mid: event.candidate.sdpMid || "0",
        sdp_mline_index: event.candidate.sdpMLineIndex || 0,
      }),
    }).catch((error) => { failRtcSession(error.message); });
  });
}

function setLiveVideoState(value, message = null) {
  const normalized = ["connected", "live"].includes(value) ? "live"
    : ["starting", "signaling", "connecting"].includes(value) ? "connecting"
      : "idle";
  elements.liveVideoStage.dataset.state = normalized;
  elements.liveVideoState.textContent = String(value || "idle").toUpperCase();
  elements.liveVideoIndicator.textContent = normalized === "live" ? "● LIVE"
    : normalized === "connecting" ? "LINKING" : "STANDBY";
  if (message) setResult(elements.liveVideoResult, message, normalized === "idle" ? "error" : "running");
}

async function startLiveVideo() {
  if (state.rtc.mode || state.rtc.peer || !hasCapability("rtc.video.mjpeg.v1")) return;
  state.rtc.mode = "video";
  resetLiveVideoMetrics();
  setLiveVideoState("starting", "正在申请相机与实时传输资源…");
  elements.startLiveVideoButton.disabled = true;
  try {
    await api("/api/video/session/start", {
      method: "POST",
      body: JSON.stringify({ mode: "video" }),
    });
    const peer = new RTCPeerConnection({ iceServers: [] });
    const channel = peer.createDataChannel("mjpeg-data", {
      ordered: false,
      maxPacketLifeTime: 200,
    });
    state.rtc.peer = peer;
    state.rtc.channel = channel;
    channel.binaryType = "arraybuffer";
    channel.addEventListener("open", () => {
      setLiveVideoState("connected");
      setResult(elements.liveVideoResult, "实时画面通道已连接", "ok");
    });
    channel.addEventListener("message", (event) => { enqueueMjpegPacket(event.data); });
    channel.addEventListener("close", () => {
      if (state.rtc.peer) failLiveVideo("实时画面通道已关闭");
    });
    bindRtcPeerEvents(peer);
    startRtcControlLoops();
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await api("/api/video/session/signal", {
      method: "POST",
      body: JSON.stringify({ kind: "offer", sdp: offer.sdp }),
    });
    setLiveVideoState("signaling", "已发送浏览器协商信息，等待 Watcher 应答…");
    await refreshStatus();
  } catch (error) {
    await failLiveVideo(error.message);
  }
}

function startRtcControlLoops() {
  pollRtcEvents();
  state.rtc.heartbeatTimer = window.setInterval(async () => {
    if (!state.rtc.peer) return;
    const browserSendUs = Math.round((performance.timeOrigin + performance.now()) * 1000);
    const startedAt = performance.now();
    try {
      await api(rtcEndpoint("clock-ping"), {
        method: "POST",
        body: JSON.stringify({ browser_send_us: browserSendUs }),
      });
      state.rtc.rttUs = Math.max(0, Math.round((performance.now() - startedAt) * 1000));
    } catch (error) {
      if (state.rtc.peer) failRtcSession(error.message);
    }
  }, 1500);
  state.rtc.feedbackTimer = window.setInterval(async () => {
    if (!state.rtc.peer) return;
    const fps = currentDisplayFps();
    let audio = { queueMs: 0, packetLossX100: 0, jitterUs: 0, concealedFrames: 0 };
    try {
      if (state.rtc.mode === "audio") audio = await collectRtcAudioStats();
    } catch (_) {}
    api(rtcEndpoint("feedback"), {
      method: "POST",
      body: JSON.stringify({
        display_fps_x100: Math.round(fps * 100),
        frame_age_p95_us: 0,
        rtt_us: state.rtc.rttUs,
        audio_queue_ms: audio.queueMs,
        audio_packet_loss_x100: audio.packetLossX100,
        audio_jitter_us: audio.jitterUs,
        audio_concealed_frames: audio.concealedFrames,
        congestion_level: state.rtc.droppedFrames > 0 ? 1 : 0,
      }),
    }).catch(() => {});
  }, 1000);
}

async function collectRtcAudioStats() {
  let sent = 0;
  let received = 0;
  let lost = 0;
  let jitterUs = 0;
  let queueMs = 0;
  let concealedFrames = 0;
  const reports = await state.rtc.peer.getStats();
  reports.forEach((report) => {
    if (report.kind !== "audio" && report.mediaType !== "audio") return;
    if (report.type === "outbound-rtp") sent += report.packetsSent || 0;
    if (report.type === "inbound-rtp") {
      received += report.packetsReceived || 0;
      lost += Math.max(0, report.packetsLost || 0);
      jitterUs = Math.max(jitterUs, Math.round((report.jitter || 0) * 1_000_000));
      if (report.jitterBufferEmittedCount > 0) {
        queueMs = Math.max(queueMs, Math.round((report.jitterBufferDelay / report.jitterBufferEmittedCount) * 1000));
      }
      concealedFrames += report.concealedSamples || 0;
    }
  });
  elements.rtcAudioUpPackets.textContent = String(sent);
  elements.rtcAudioDownPackets.textContent = String(received);
  state.rtc.browserAudioSent = sent;
  state.rtc.browserAudioReceived = received;
  updateRtcAudioHealth();
  return {
    queueMs,
    packetLossX100: Math.round((lost / Math.max(1, received + lost)) * 10_000),
    jitterUs,
    concealedFrames,
  };
}

async function pollRtcEvents() {
  if (!state.rtc.peer) return;
  try {
    const payload = await api(`${rtcEndpoint("events")}?after=${state.rtc.eventCursor}`);
    for (const event of payload.events || []) {
      state.rtc.eventCursor = Math.max(state.rtc.eventCursor, event.id || 0);
      await handleRtcEvent(event.message || {});
      if (!state.rtc.peer) return;
    }
  } catch (error) {
    if (state.rtc.peer) await failRtcSession(error.message);
    return;
  }
  state.rtc.pollTimer = window.setTimeout(pollRtcEvents, 100);
}

async function handleRtcEvent(message) {
  const data = message.data || {};
  if (message.type === "sys.nack") {
    await failRtcSession(localizeError(data.error || data.reason || "设备拒绝了 RTC 请求"));
    return;
  }
  if (message.type === "evt.rtc.state") {
    setRtcSessionState(data.state || "connecting");
    if (data.state === "failed") await failRtcSession(localizeError(data.reason || "设备 RTC 会话失败"));
    if (data.state === "stopped" && state.rtc.peer) cleanupRtcSession();
    return;
  }
  if (message.type === "evt.rtc.capabilities") {
    const video = data.video || {};
    if (video.width && video.height) elements.liveVideoResolution.textContent = `${video.width} × ${video.height}`;
    return;
  }
  if (message.type !== "evt.rtc.signal" || !state.rtc.peer) return;
  if (data.kind === "answer" && data.sdp) {
    if (!state.rtc.peer.remoteDescription) {
      await state.rtc.peer.setRemoteDescription({ type: "answer", sdp: data.sdp });
      for (const candidate of state.rtc.remoteCandidates.splice(0)) {
        await state.rtc.peer.addIceCandidate(candidate);
      }
    }
  } else if (data.kind === "candidate" && data.candidate) {
    const candidate = new RTCIceCandidate({
      candidate: data.candidate,
      sdpMid: data.sdp_mid,
      sdpMLineIndex: data.sdp_mline_index,
    });
    if (state.rtc.peer.remoteDescription) await state.rtc.peer.addIceCandidate(candidate);
    else state.rtc.remoteCandidates.push(candidate);
  }
}

async function stopLiveVideo() {
  elements.stopLiveVideoButton.disabled = true;
  try {
    await api("/api/video/session/stop", { method: "POST" });
    setResult(elements.liveVideoResult, "实时画面已停止", "ok");
    cleanupLiveVideo();
    await refreshStatus();
  } catch (error) {
    notify(error.message, "error");
    elements.stopLiveVideoButton.disabled = !state.rtc.peer;
    setResult(elements.liveVideoResult, "停止失败，请重试", "error");
  }
}

async function stopRtcAudio() {
  elements.stopRtcAudioButton.disabled = true;
  try {
    await api("/api/rtc/session/stop", { method: "POST" });
    setResult(elements.rtcAudioResult, "全双工通话已结束", "ok");
    cleanupRtcSession();
    await refreshStatus();
  } catch (error) {
    notify(error.message, "error");
    elements.stopRtcAudioButton.disabled = !state.rtc.peer;
    setResult(elements.rtcAudioResult, "停止失败，请重试", "error");
  }
}

async function failRtcSession(message) {
  if (state.rtc.mode === "audio") await failRtcAudio(message);
  else await failLiveVideo(message);
}

async function failRtcAudio(message) {
  const hadSession = Boolean(state.rtc.peer);
  cleanupRtcSession();
  setRtcAudioState("failed", message);
  notify(message, "error");
  try { await api("/api/rtc/session/stop", { method: "POST" }); } catch (_) {}
  if (hadSession || state.status) await refreshStatus();
}

async function failLiveVideo(message) {
  if (!state.rtc.peer) {
    setLiveVideoState("failed", message);
    notify(message, "error");
    try { await api("/api/video/session/stop", { method: "POST" }); } catch (_) {}
    await refreshStatus();
    return;
  }
  const peer = state.rtc.peer;
  cleanupLiveVideo();
  setLiveVideoState("failed", message);
  notify(message, "error");
  try { await api("/api/video/session/stop", { method: "POST" }); } catch (_) {}
  if (peer) await refreshStatus();
}

function cleanupLiveVideo() {
  cleanupRtcSession();
}

function cleanupRtcSession() {
  state.rtc.generation += 1;
  window.clearTimeout(state.rtc.pollTimer);
  window.clearInterval(state.rtc.heartbeatTimer);
  window.clearInterval(state.rtc.feedbackTimer);
  state.rtc.pollTimer = null;
  state.rtc.heartbeatTimer = null;
  state.rtc.feedbackTimer = null;
  const channel = state.rtc.channel;
  const peer = state.rtc.peer;
  const localStream = state.rtc.localStream;
  const mode = state.rtc.mode;
  state.rtc.channel = null;
  state.rtc.peer = null;
  state.rtc.localStream = null;
  state.rtc.remoteStream = null;
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "idle";
  if (channel) {
    channel.onclose = null;
    try { channel.close(); } catch (_) {}
  }
  if (peer) {
    peer.onconnectionstatechange = null;
    try { peer.close(); } catch (_) {}
  }
  if (localStream) {
    for (const track of localStream.getTracks()) track.stop();
  }
  elements.rtcRemoteAudio.pause();
  elements.rtcRemoteAudio.srcObject = null;
  elements.rtcAudioLocalState.textContent = "未占用";
  elements.stopLiveVideoButton.disabled = true;
  elements.stopRtcAudioButton.disabled = true;
  elements.startLiveVideoButton.disabled = !state.status?.connected || !hasCapability("rtc.video.mjpeg.v1");
  elements.startRtcAudioButton.disabled = !state.status?.connected || !hasCapability("rtc.audio.full_duplex.v1");
  if (mode === "video" && elements.liveVideoStage.dataset.state !== "idle") setLiveVideoState("idle");
  if (mode === "audio" && elements.rtcAudioConsole.dataset.state !== "idle") setRtcAudioState("idle");
  state.rtc.mode = null;
}

async function enqueueMjpegPacket(value) {
  try {
    const packet = value instanceof ArrayBuffer ? value : await value.arrayBuffer();
    const frame = parseWjpgPacket(packet);
    state.rtc.receivedFrames += 1;
    if (state.rtc.lastSequence !== null) {
      const expected = (state.rtc.lastSequence + 1) >>> 0;
      const gap = (frame.sequence - expected) >>> 0;
      if (gap > 0 && gap < 10000) state.rtc.droppedFrames += gap;
    }
    state.rtc.lastSequence = frame.sequence;
    if (state.rtc.decodeBusy) {
      if (state.rtc.pendingFrame !== null) state.rtc.droppedFrames += 1;
      state.rtc.pendingFrame = frame;
      return;
    }
    state.rtc.decodeBusy = true;
    let current = frame;
    while (current) {
      await drawMjpegFrame(current);
      current = state.rtc.pendingFrame;
      state.rtc.pendingFrame = null;
    }
  } catch (error) {
    state.rtc.droppedFrames += 1;
  } finally {
    state.rtc.decodeBusy = false;
    elements.liveVideoDrops.textContent = String(state.rtc.droppedFrames);
  }
}

function parseWjpgPacket(packet) {
  const bytes = new Uint8Array(packet);
  if (bytes.byteLength < 24 || bytes[0] !== 0x57 || bytes[1] !== 0x4a || bytes[2] !== 0x50 || bytes[3] !== 0x47) {
    throw new Error("invalid WJPG magic");
  }
  const view = new DataView(packet);
  const headerSize = view.getUint16(6, true);
  const jpegSize = view.getUint32(16, true);
  if (bytes[4] !== 1 || headerSize !== 20 || jpegSize !== bytes.byteLength - headerSize) {
    throw new Error("invalid WJPG header");
  }
  const jpeg = bytes.slice(headerSize);
  if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8 || jpeg[jpeg.length - 2] !== 0xff || jpeg[jpeg.length - 1] !== 0xd9) {
    throw new Error("invalid JPEG payload");
  }
  return {
    sequence: view.getUint32(8, true),
    captureTimestampMs: view.getUint32(12, true),
    jpeg,
  };
}

async function drawMjpegFrame(frame) {
  const bitmap = await createImageBitmap(new Blob([frame.jpeg], { type: "image/jpeg" }));
  try {
    const canvas = elements.liveVideoCanvas;
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      elements.liveVideoResolution.textContent = `${bitmap.width} × ${bitmap.height}`;
    }
    canvas.getContext("2d", { alpha: false }).drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  } finally {
    bitmap.close();
  }
  const now = performance.now();
  state.rtc.lastFrameAt = now;
  state.rtc.displayedFrames += 1;
  state.rtc.frameTimes.push(now);
  state.rtc.frameTimes = state.rtc.frameTimes.filter((time) => now - time <= 1000);
  elements.liveVideoFps.textContent = `${currentDisplayFps().toFixed(1)} FPS`;
  setLiveVideoState("live");
}

function currentDisplayFps() {
  const now = performance.now();
  state.rtc.frameTimes = state.rtc.frameTimes.filter((time) => now - time <= 1000);
  return state.rtc.frameTimes.length;
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
elements.startLiveVideoButton.addEventListener("click", () => { startLiveVideo(); });
elements.stopLiveVideoButton.addEventListener("click", () => { stopLiveVideo(); });
elements.startRtcAudioButton.addEventListener("click", () => { startRtcAudio(); });
elements.stopRtcAudioButton.addEventListener("click", () => { stopRtcAudio(); });
elements.recordMicrophoneButton.addEventListener("click", () => { recordMicrophone().catch(() => {}); });
elements.runAllButton.addEventListener("click", runAll);
elements.recordDuration.addEventListener("input", () => { elements.durationValue.textContent = elements.recordDuration.value; });
document.querySelector("#clearVisualLog").addEventListener("click", () => {
  (state.status?.events || []).forEach((event) => state.hiddenEventIds.add(event.id));
  renderEvents(state.status?.events || []);
});

setInterval(() => {
  elements.footerClock.textContent = new Date().toLocaleTimeString([], { hour12: false });
  if (state.rtc.lastFrameAt > 0 && state.rtc.peer) {
    const age = Math.max(0, Math.round(performance.now() - state.rtc.lastFrameAt));
    elements.liveVideoFrameAge.textContent = `${age} MS AGO`;
    elements.liveVideoFps.textContent = `${currentDisplayFps().toFixed(1)} FPS`;
  }
}, 1000);
window.addEventListener("pagehide", () => {
  if (!state.rtc.peer && !state.rtc.localStream) return;
  navigator.sendBeacon(rtcEndpoint("stop"));
  cleanupRtcSession();
});
setInterval(refreshStatus, 1000);
refreshStatus({ quiet: false });
drawEmptyWaveform();
