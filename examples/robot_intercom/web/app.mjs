import { createCallController } from "./call-controller.mjs";
import { createRemotePlayback } from "./remote-playback.mjs";
import { createVoiceInput } from "./voice-input.mjs";
import { connectCallPresence } from "./call-presence.mjs";
import { createPhotoController } from "./photo-controller.mjs";
import { createCallClock } from "./call-clock.mjs";
import { createDeviceConnector } from "./device-connector.mjs";
import { createI18n } from "./i18n.mjs";

const $ = (id) => document.getElementById(id);
let storage;
try { storage = window.localStorage; } catch { /* Use browser language if storage is blocked. */ }
const i18n = createI18n({ storage, languages: navigator.languages });
const t = (source, values) => i18n.text(source, values);
const dynamicTexts = new Map();
function setText(id, source, values) {
  dynamicTexts.set(id, { source: String(source), values });
  $(id).textContent = t(String(source), values);
}
let callMessage = "先点击上方「连接机器人」，再开始通话。";
function renderLanguage() {
  document.documentElement.lang = i18n.locale;
  for (const element of document.querySelectorAll("[data-i18n]"))
    element.textContent = t(element.dataset.i18n);
  for (const attribute of ["aria-label", "alt", "placeholder"])
    for (const element of document.querySelectorAll(`[data-i18n-${attribute}]`))
      element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
  $("languageZh").setAttribute("aria-pressed", String(i18n.locale === "zh-CN"));
  $("languageEn").setAttribute("aria-pressed", String(i18n.locale === "en"));
  for (const [id, { source, values }] of dynamicTexts)
    $(id).textContent = t(source, values);
}
let status = null,
  listening = true,
  voice = null,
  refreshTimer;
let lastMediaReport = 0;
let latencyStats = null, voiceStats = null, playbackStats = null;
function renderSound() {
  const ms = value => value === null || value === undefined ? '—' : `${Math.round(value)} ms`;
  $('volumeValue').textContent = `${$('robotVolume').value}%`;
  $('latencyRtt').textContent = ms(latencyStats?.rttMs);
  $('latencyBuffer').textContent = ms(latencyStats?.receiveBufferMs);
  $('latencyTarget').textContent = ms(latencyStats?.receiveTargetMs);
  $('latencyMinimum').textContent = ms(latencyStats?.receiveMinimumMs);
  setText('latencyHint', latencyStats?.receiverHintSupported === true ? '已请求 20ms 接收目标'
    : latencyStats?.receiverHintSupported === false ? '浏览器不支持接收目标设置' : '尚无数据');
  $('latencyCapture').textContent = ms(voiceStats?.captureMs);
  $('latencyContext').textContent = ms(voiceStats?.baseMs);
  setText('playbackMode', playbackStats?.mode === 'denoised'
    ? playbackStats.profile === 'speech' ? '人声优先 · 算法缓冲约 20ms' : '降噪播放 · 算法缓冲约 20ms'
    : '原始播放 · 跳过降噪处理');
  $('listenProfile').disabled = !$('noise').checked;
}
let photoController = null;
const callClock = createCallClock();
let visuals = null,
  localStream = null,
  remoteStream = null;
let visualGeneration = 0;
function renderVisuals() {
  visuals?.update({
    ...controller.snapshot(),
    listening,
    local: localStream,
    remote: remoteStream,
  });
}
async function mountVisuals() {
  const token = ++visualGeneration;
  try {
    const module = await import("./visuals.js");
    if (token !== visualGeneration) return;
    visuals = module.mountVisuals({
      orb: $("orbMount"),
      input: $("inputWave"),
      output: $("outputWave"),
    });
    renderVisuals();
  } catch {
    // RTC remains usable if the optional visual bundle cannot load.
    document.body.dataset.visualFallback = "true";
  }
}
async function api(path, body, options = {}) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: options.signal || AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "无法连接应用，请重新打开");
  return result;
}
const titles = {
  idle: "准备好，就开始说话",
  starting: "正在准备通话",
  connecting: "正在连接机器人",
  running: "你的声音，已经到达",
  stopping: "正在结束通话",
  cleanup_required: "请重试结束通话",
};
function render() {
  const call = controller.snapshot(),
    busy = call.requestId !== null;
  const supported = [
    "rtc.audio.full_duplex.v1",
    "expression.audio_follow.v1",
  ].every((c) => status?.capabilities?.includes(c));
  setText("connection", status?.connected
    ? "机器人已连接"
    : "机器人未连接");
  $("connection").dataset.connected = String(!!status?.connected);
  $("start").hidden = busy;
  $("start").disabled = !status?.connected || !supported;
  $("stop").hidden = !busy;
  $("stop").disabled = call.state === "stopping";
  setText("stopLabel", call.state === "cleanup_required"
      ? "重试结束通话"
      : call.state === "stopping"
        ? "正在结束…"
        : "结束通话");
  $("mute").disabled = !["running", "connecting"].includes(call.state);
  setText("muteLabel", call.muted ? "麦克风已静音" : "麦克风开启");
  $("mute").setAttribute("aria-pressed", String(call.muted));
  $("listen").disabled = !["running", "connecting"].includes(call.state);
  $('voiceMode').disabled = !!voiceStats && !voiceStats.effectAvailable;
  setText("listenLabel", listening ? "收听现场" : "收听已关闭");
  setText("inputState", call.state === "running" ? (call.muted ? "已静音" : "传输中") : "未开启");
  setText("outputState", call.state === "running" ? (listening ? "收听中" : "已关闭") : "未开启");
  setText("callDuration", callClock.update(call));
  $("callDuration").hidden = !busy;
  const photoEnabled = status?.features?.photo === true;
  $("photoPanel").hidden = !photoEnabled;
  document.body.dataset.photoEnabled = String(photoEnabled);
  setText("photoHint", call.state === "running"
      ? "拍照不打断通话，通常约需 2 秒"
      : "通话连接后即可拍照");
  $("capture").disabled =
    !photoEnabled || !status?.capabilities?.includes("camera.capture") ||
    call.state !== "running" || photoController.snapshot().busy;
  $("callVisual").dataset.state = call.state;
  setText("callTitle", titles[call.state]);
  document.body.dataset.callState = call.state;
  renderVisuals();
  renderDevice();
  renderSound();
  if (!busy && status?.connected && !supported)
    callMessage = "当前固件缺少通话或嘴型能力，请升级机器人固件。";
  else if (
    !busy &&
    status?.connected &&
    ["请在桌面端", "先点击上方"].some((prefix) =>
      callMessage.startsWith(prefix))
  )
    callMessage = "机器人已就绪，点击开始通话。";
  setText("message", callMessage);
}
async function playRemote(stream) {
  remoteStream = stream;
  renderVisuals();
  $("remoteAudio").srcObject = stream;
  $("remoteAudio").muted = !listening;
  try {
    await $("remoteAudio").play();
    $("resumeAudio").hidden = true;
  } catch {
    $("resumeAudio").hidden = false;
  }
}
const controller = createCallController({
  api,
  prepareMicrophone: async (stream, isCurrent, signal) => {
    const prepared = await createVoiceInput(stream, {
      volume: Number($('robotVolume').value), mode: $('voiceMode').value, isCurrent, signal,
      onState: state => {
        if (isCurrent()) {
          voiceStats = state; $('voiceMode').value = state.mode;
          $('voiceMode').disabled = !state.effectAvailable; renderSound();
        }
      },
    });
    if (isCurrent()) {
      voice = prepared;
      if (voiceStats && !voiceStats.effectAvailable) $('voiceMode').value = 'original';
      render();
    }
    return prepared;
  },
  onLocal: (stream) => {
    localStream = stream;
    renderVisuals();
  },
  connectPresence: connectCallPresence,
  report: (event) => {
    const requestId = controller.snapshot().requestId;
    if (requestId)
      api("/api/call/diagnostic", {
        request_id: requestId,
        visibility: document.visibilityState,
        ...event,
      }).catch(() => {});
  },
  onState: (call) => {
    photoController?.updateCall(call);
    if (call.message) callMessage = call.message;
    render();
  },
  onStats: (stats) => {
    latencyStats = stats; renderSound();
    document.body.dataset.audioSent = stats.sent;
    document.body.dataset.audioReceived = stats.received;
    if (performance.now() - lastMediaReport > 5000) {
      lastMediaReport = performance.now();
      const requestId = controller.snapshot().requestId;
      if (requestId)
        api("/api/call/diagnostic", {
          request_id: requestId,
          kind: "media_sample",
          sent: stats.sent,
          received: stats.received,
          lost: stats.lost,
          rtt_ms: stats.rttMs === null ? null : Math.round(stats.rttMs),
          receive_buffer_ms: stats.receiveBufferMs,
          receive_target_ms: stats.receiveTargetMs,
          receive_minimum_ms: stats.receiveMinimumMs,
          receiver_hint_supported: stats.receiverHintSupported,
          capture_ms: voiceStats?.captureMs,
          context_ms: voiceStats?.baseMs,
          playback_mode: playbackStats?.mode,
          voice_mode: voiceStats?.mode,
        }).catch(() => {});
    }
  },
  onRemote: async (stream, isCurrent) => {
    if (isCurrent()) {
      await playback.setProfile($('listenProfile').value);
      if (isCurrent()) await playback.start(stream, $('noise').checked);
    }
  },
  onDispose: () => {
    localStream = remoteStream = null;
    renderVisuals();
    playback.dispose();
    voice = null; latencyStats = voiceStats = playbackStats = null;
    renderSound();
    $("remoteAudio").pause();
    $("remoteAudio").srcObject = null;
    $("resumeAudio").hidden = true;
  },
});
const playback = createRemotePlayback({ play: playRemote,
  onState: state => { playbackStats = state; renderSound(); } });
async function refresh() {
  try {
    status = await api("/api/status");
    deviceConnector.update(status.connection);
    render();
  } catch {
    setText("connection", "应用连接中断");
    $("connection").dataset.connected = "false";
    $("start").disabled = true;
    deviceConnector.update(null);
  }
  refreshTimer = setTimeout(refresh, 2000);
}
const deviceConnector = createDeviceConnector({ api, onChange: renderDevice });
function renderDevice() {
  const pair = deviceConnector.snapshot(),
    call = controller.snapshot();
  const online = !!status?.connected;
  const daemonBusy = ["discovering", "connecting", "reconnecting"].includes(
    status?.connection?.state,
  );
  setText("deviceTitle", online
    ? status.device?.name || "机器人已连接"
    : pair.busy || daemonBusy
      ? "正在连接机器人"
      : "尚未连接机器人");
  if (online)
    setText("deviceDetail", "当前机器人 · {device}", {
      device: status.device?.device_id || status.connection?.device_id || t("身份确认中"),
    });
  else setText("deviceDetail", call.requestId
      ? "请恢复原机器人连接并完成通话释放，再重新配对。"
      : pair.busy
        ? pair.message
        : "先连接机器人，再开始双向原声通话。");
  setText("connectDevice", online
    ? "已连接"
    : pair.busy
      ? "查看连接进度"
      : "连接机器人");
  $("connectDevice").disabled = online || call.requestId !== null;
  $("devicePanel").dataset.connected = String(online);
  setText("deviceFeedback", pair.message);
  $("deviceFeedback").dataset.error = String(pair.phase === "error");
  setText("pairSubmit", pair.busy ? "连接中…" : "连接机器人");
  $("pairSubmit").disabled = pair.busy;
  $("pairCode").disabled = $("pairIp").disabled = pair.busy;
  $("cancelPair").hidden = pair.phase !== "waiting";
  $("closePair").disabled = pair.busy;
  if (pair.phase === "connected" && $("deviceDialog").open)
    $("deviceDialog").close();
}
$("connectDevice").onclick = () => $("deviceDialog").showModal();
$("closePair").onclick = () => $("deviceDialog").close();
$("deviceDialog").addEventListener("cancel", (event) => {
  if (deviceConnector.snapshot().busy) event.preventDefault();
});
$("pairForm").onsubmit = async (event) => {
  event.preventDefault();
  await deviceConnector.connect(
    $("pairCode").value.trim(),
    $("pairIp").value.trim(),
  );
  if (deviceConnector.snapshot().phase !== "error") $("pairCode").value = "";
};
$("cancelPair").onclick = () => deviceConnector.cancel();
$("start").onclick = () => controller.start();
$("stop").onclick = () =>
  controller.stop().catch((error) => {
    callMessage = error.message;
    render();
  });
$("mute").onclick = () => controller.setMuted(!controller.snapshot().muted);
$("listen").onclick = () => {
  listening = !listening;
  $("remoteAudio").muted = !listening;
  $("listen").setAttribute("aria-pressed", String(!listening));
  render();
};
$("noise").onchange = () => { renderSound(); playback.setEnabled($("noise").checked).catch(() => {}); };
$('listenProfile').onchange = () => playback.setProfile($('listenProfile').value).catch(() => {});
$('robotVolume').oninput = () => {
  voice?.setVolume(Number($('robotVolume').value))?.catch(soundError); renderSound();
};
function soundError() {
  callMessage = '声音切换失败，请结束后重新开始通话。'; render();
}
$('voiceMode').onchange = () => {
  try { voice?.setMode($('voiceMode').value)?.catch(soundError); }
  catch { $('voiceMode').value = 'original'; soundError(); }
};
$("resumeAudio").onclick = async () => {
  await playback.resume();
  await voice?.resume();
  await playRemote($("remoteAudio").srcObject);
};
photoController = createPhotoController({
  isCurrent: (requestId) => {
    const call = controller.snapshot();
    return call.requestId === requestId && call.state === "running";
  },
  capture: (requestId, signal) =>
    api("/api/photo", { request_id: requestId }, { signal }),
  load: async (photo, signal) => {
    const probe = new Image();
    const cancelImage = () => {
      probe.src = "";
    };
    signal.addEventListener("abort", cancelImage, { once: true });
    probe.src = photo.url;
    try {
      await probe.decode();
      return probe;
    } finally {
      signal.removeEventListener("abort", cancelImage);
    }
  },
  onProgress: ({ phase, remainingSeconds, elapsedSeconds }) => {
    const loading = phase === "loading";
    $("photoProgress").hidden = false;
    $("photoFrame").setAttribute("aria-busy", "true");
    setText("captureLabel", "拍照中…");
    setText("photoProgressTitle", loading ? "正在显示照片" : "正在拍照");
    setText("photoCountdown", loading || remainingSeconds === 0 ? "…" : remainingSeconds);
    const message = loading
      ? "照片已收到，正在显示…"
      : remainingSeconds > 0
        ? "预计还需 {seconds} 秒，通话继续。"
        : "照片仍在处理中，已等待 {seconds} 秒。";
    const values = { seconds: remainingSeconds > 0 ? remainingSeconds : elapsedSeconds };
    setText("photoProgressDetail", message, values);
    setText("photoStatus", message, values);
    render();
  },
  onPhoto: (photo, probe, elapsedMs) => {
    $("photo").src = photo.url;
    $("photo").hidden = false;
    $("emptyPhoto").hidden = true;
    $("download").href = photo.url;
    $("download").download = photo.artifact;
    $("download").hidden = false;
    setText("photoStatus", `${new Date().toLocaleTimeString(i18n.locale, { hour: "2-digit", minute: "2-digit" })} · ${probe.naturalWidth} × ${probe.naturalHeight}`);
    document.body.dataset.photoElapsedMs = Math.round(elapsedMs);
  },
  onError: (error) => {
    setText("photoStatus", error.message);
  },
  onCancel: () => {
    setText("photoStatus", "已停止等待照片。通话中可重新拍照。");
  },
  onIdle: () => {
    $("photoProgress").hidden = true;
    $("photoFrame").setAttribute("aria-busy", "false");
    setText("captureLabel", "拍照");
    render();
  },
});
$("capture").onclick = () => {
  if (status?.features?.photo === true)
    photoController.take(controller.snapshot().requestId);
};
window.addEventListener("pagehide", () => {
  ++visualGeneration;
  visuals?.dispose();
  visuals = null;
  clearTimeout(refreshTimer);
  photoController.cancel();
  controller.leave();
});
document.addEventListener("visibilitychange", () => {
  const requestId = controller.snapshot().requestId;
  if (requestId)
    api("/api/call/diagnostic", {
      request_id: requestId,
      kind: "visibility",
      visibility: document.visibilityState,
    }).catch(() => {});
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    mountVisuals();
    controller.stop().catch(() => {});
    refresh();
  }
});
$("languageZh").onclick = () => {
  i18n.setLocale("zh-CN");
  renderLanguage();
  render();
};
$("languageEn").onclick = () => {
  i18n.setLocale("en");
  renderLanguage();
  render();
};
renderLanguage();
mountVisuals();
refresh();
