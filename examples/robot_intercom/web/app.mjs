import { createCallController } from "./call-controller.mjs";
import { createRtcNoisePlayback } from "./rtc-noise-playback.mjs";

const $ = (id) => document.getElementById(id);
let status = null,
  photoPending = false,
  listening = true,
  noise = null,
  noiseAbort = null,
  refreshTimer;
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
    "camera.capture",
  ].every((c) => status?.capabilities?.includes(c));
  $("connection").textContent = status?.connected
    ? "机器人已连接"
    : "机器人未连接";
  $("start").hidden = busy;
  $("start").disabled = !status?.connected || !supported;
  $("stop").hidden = !busy;
  $("stop").disabled = call.state === "stopping";
  $("mute").disabled = !["running", "connecting"].includes(call.state);
  $("mute").textContent = call.muted ? "麦克风已静音" : "麦克风开启";
  $("mute").setAttribute("aria-pressed", String(call.muted));
  $("capture").disabled = call.state !== "running" || photoPending;
  $("callVisual").dataset.state = call.state;
  $("callTitle").textContent = titles[call.state];
  document.body.dataset.callState = call.state;
  if (!busy && status?.connected && !supported)
    $("message").textContent = "当前固件缺少通话或嘴型能力，请升级机器人固件。";
  else if (
    !busy &&
    status?.connected &&
    $("message").textContent.startsWith("请在桌面端")
  )
    $("message").textContent = "机器人已就绪，点击开始通话。";
}
async function playRemote(stream) {
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
  onState: (call) => {
    if (call.message) $("message").textContent = call.message;
    render();
  },
  onStats: (stats) => {
    document.body.dataset.audioSent = stats.sent;
    document.body.dataset.audioReceived = stats.received;
  },
  onRemote: async (stream, isCurrent) => {
    noiseAbort?.abort();
    const abort = (noiseAbort = new AbortController());
    await playRemote(stream);
    const processed = await createRtcNoisePlayback(stream, {
      enabled: $("noise").checked,
      strength: 70,
      signal: abort.signal,
      isCurrent,
      onFallback: () => {
        if (isCurrent()) playRemote(stream);
      },
    });
    if (!isCurrent()) {
      await processed?.dispose();
      return;
    }
    noise = processed;
    if (processed) await playRemote(processed.stream);
  },
  onDispose: () => {
    noiseAbort?.abort();
    noiseAbort = null;
    noise?.dispose();
    noise = null;
    $("remoteAudio").pause();
    $("remoteAudio").srcObject = null;
    $("resumeAudio").hidden = true;
  },
});
async function refresh() {
  try {
    status = await api("/api/status");
    render();
  } catch {
    $("connection").textContent = "应用连接中断";
    $("start").disabled = true;
  }
  refreshTimer = setTimeout(refresh, 2000);
}
$("start").onclick = () => controller.start();
$("stop").onclick = () =>
  controller.stop().catch((error) => {
    $("message").textContent = error.message;
  });
$("mute").onclick = () => controller.setMuted(!controller.snapshot().muted);
$("listen").onclick = () => {
  listening = !listening;
  $("remoteAudio").muted = !listening;
  $("listen").textContent = listening
    ? "收听中 · 点击关闭"
    : "已关闭 · 点击收听";
  $("listen").setAttribute("aria-pressed", String(!listening));
};
$("noise").onchange = () =>
  noise?.setOptions({ enabled: $("noise").checked, strength: 70 });
$("resumeAudio").onclick = async () => {
  await noise?.resume();
  await playRemote($("remoteAudio").srcObject);
};
$("capture").onclick = async () => {
  if (photoPending) return;
  photoPending = true;
  render();
  $("photoStatus").textContent = "正在拍照…";
  try {
    const photo = await api("/api/photo", {
      request_id: controller.snapshot().requestId,
    });
    const probe = new Image();
    probe.src = photo.url;
    await probe.decode();
    $("photo").src = photo.url;
    $("photo").hidden = false;
    $("emptyPhoto").hidden = true;
    $("download").href = photo.url;
    $("download").download = photo.artifact;
    $("download").hidden = false;
    $("photoStatus").textContent =
      `${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} · ${probe.naturalWidth} × ${probe.naturalHeight}`;
  } catch (error) {
    $("photoStatus").textContent = error.message;
  } finally {
    photoPending = false;
    render();
  }
};
window.addEventListener("pagehide", () => {
  clearTimeout(refreshTimer);
  controller.leave();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    controller.stop().catch(() => {});
    refresh();
  }
});
refresh();
