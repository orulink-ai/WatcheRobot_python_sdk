import { evaluateRtcAudioHealth } from "./rtc-audio-health.mjs";
import { evaluateAnimationConfirmation } from "./animation-confirmation.mjs";
import {
  clampAnimationIntervalMs,
  createAnimationShuffleBag,
  normalizeAnimationCatalog,
} from "./animation-random.mjs";
import {
  calculateRoundTripUs,
  configureLowLatencyAudioReceivers,
  selectMediaRoundTripUs,
  sampleAudioJitterBuffer,
} from "./rtc-audio-latency.mjs";
import {
  evaluateResourceLifecycle,
  selectLifecycleBaseline,
  selectLatestReleaseSnapshot,
} from "./resource-health.mjs";
import {
  controlAvailability,
  isCurrentRtcGeneration,
  resolveRtcMode,
  rtcModeHasAudio,
  rtcModeHasVideo,
} from "./media-resource-policy.mjs";
import {
  createVideoCongestionFeedback,
  deviceVideoCongestionLevel,
  updateVideoCongestionFeedback,
} from "./video-feedback.mjs";
import {
  admitVideoFrame,
  finishVideoFrameDecode,
  takePendingVideoFrame,
} from "./video-frame-queue.mjs";
import {
  acceptMjpegTransportPacket,
  createMjpegChunkReassembler,
} from "./mjpeg-chunk-reassembly.mjs";
import { createRtcMicrophoneConstraints } from "./rtc-audio-capture.mjs";
import { initializeI18n, translateText } from "./i18n.mjs";

const i18n = initializeI18n({
  defaultLocale: "en-US",
  storageKey: "watcherobot.sdk-test-bench.locale",
  englishButton: document.querySelector("#localeEnglish"),
  chineseButton: document.querySelector("#localeChinese"),
});

const state = {
  status: null,
  localResources: new Set(),
  pairingBusy: false,
  hiddenEventIds: new Set(),
  animation: {
    requestedId: null,
    requestedAtMs: 0,
    requestAccepted: false,
    lastState: "idle",
    catalog: [],
    catalogFingerprint: "",
    prefetchPromise: null,
    prefetchedId: null,
    prefetchDebounceTimer: null,
    random: {
      active: false,
      generation: 0,
      intervalMs: 8000,
      lastId: null,
      remainingIds: [],
      switchTimer: null,
      prefetchTimer: null,
    },
  },
  rtc: {
    generation: 0,
    mode: null,
    peer: null,
    channel: null,
    videoSocket: null,
    localStream: null,
    diagnosticAudio: null,
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
    mediaRttUs: 0,
    browserAudioSent: 0,
    browserAudioReceived: 0,
    browserAudioLevel: 0,
    audioConnectedAt: 0,
    audioHealthState: "idle",
    audioJitterCounter: null,
    audioLatency: { sampleValid: false, actualMs: 0, targetMs: 0, minimumMs: 0 },
    feedbackReceivedFrames: 0,
    feedbackDroppedFrames: 0,
    videoCongestionFeedback: createVideoCongestionFeedback(),
    mjpegChunkReassembler: createMjpegChunkReassembler(),
    teardownInProgress: false,
  },
};

function rtcDiagnosticAudioEnabled() {
  const params = new URLSearchParams(window.location.search);
  return window.location.hostname === "127.0.0.1" && params.get("rtc_hil") === "1";
}

function rtcBrowserAudioProcessingEnabled() {
  const params = new URLSearchParams(window.location.search);
  return params.get("rtc_audio_processing") === "1";
}

async function createRtcDiagnosticAudioStream() {
  const audioContext = new AudioContext();
  await audioContext.resume();
  const destination = audioContext.createMediaStreamDestination();
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = 880;
  gain.gain.value = 0.18;
  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start();
  state.rtc.diagnosticAudio = { audioContext, oscillator };
  return destination.stream;
}

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
  panControl: document.querySelector("#panControl"),
  panValue: document.querySelector("#panValue"),
  tiltControl: document.querySelector("#tiltControl"),
  tiltValue: document.querySelector("#tiltValue"),
  motionHead: document.querySelector("#motionHead"),
  applyMotionButton: document.querySelector("#applyMotionButton"),
  stopMotionButton: document.querySelector("#stopMotionButton"),
  motionResult: document.querySelector("#motionResult"),
  lightColor: document.querySelector("#lightColor"),
  lightBrightness: document.querySelector("#lightBrightness"),
  brightnessValue: document.querySelector("#brightnessValue"),
  lightZone: document.querySelector("#lightZone"),
  lightEffect: document.querySelector("#lightEffect"),
  lightVisual: document.querySelector("#lightVisual"),
  applyLightButton: document.querySelector("#applyLightButton"),
  playLightEffectButton: document.querySelector("#playLightEffectButton"),
  lightsOffButton: document.querySelector("#lightsOffButton"),
  lightResult: document.querySelector("#lightResult"),
  animationId: document.querySelector("#animationId"),
  animationSuggestions: document.querySelector("#animationSuggestions"),
  animationCatalogSummary: document.querySelector("#animationCatalogSummary"),
  animationRandomInterval: document.querySelector("#animationRandomInterval"),
  playAnimationButton: document.querySelector("#playAnimationButton"),
  stopAnimationButton: document.querySelector("#stopAnimationButton"),
  startRandomAnimationButton: document.querySelector("#startRandomAnimationButton"),
  stopRandomAnimationButton: document.querySelector("#stopRandomAnimationButton"),
  animationResult: document.querySelector("#animationResult"),
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
  liveVideoPipelineFps: document.querySelector("#liveVideoPipelineFps"),
  liveVideoTransport: document.querySelector("#liveVideoTransport"),
  liveVideoCongestion: document.querySelector("#liveVideoCongestion"),
  liveVideoAnimation: document.querySelector("#liveVideoAnimation"),
  liveVideoResolution: document.querySelector("#liveVideoResolution"),
  liveVideoDrops: document.querySelector("#liveVideoDrops"),
  liveVideoIndicator: document.querySelector("#liveVideoIndicator"),
  liveVideoFrameAge: document.querySelector("#liveVideoFrameAge"),
  rtcAudioPanel: document.querySelector("#rtcAudioPanel"),
  rtcAudioCapability: document.querySelector("#rtcAudioCapability"),
  startRtcAudioButton: document.querySelector("#startRtcAudioButton"),
  startRtcAvButton: document.querySelector("#startRtcAvButton"),
  stopRtcAudioButton: document.querySelector("#stopRtcAudioButton"),
  rtcAudioResult: document.querySelector("#rtcAudioResult"),
  rtcAudioConsole: document.querySelector("#rtcAudioConsole"),
  rtcAudioState: document.querySelector("#rtcAudioState"),
  rtcAudioLocalState: document.querySelector("#rtcAudioLocalState"),
  rtcAudioUpPackets: document.querySelector("#rtcAudioUpPackets"),
  rtcAudioDownPackets: document.querySelector("#rtcAudioDownPackets"),
  rtcAudioDeviceCapture: document.querySelector("#rtcAudioDeviceCapture"),
  rtcAudioDeviceTx: document.querySelector("#rtcAudioDeviceTx"),
  rtcAudioSignal: document.querySelector("#rtcAudioSignal"),
  rtcAudioAec: document.querySelector("#rtcAudioAec"),
  rtcAudioLatency: document.querySelector("#rtcAudioLatency"),
  resourcePanel: document.querySelector("#resourcePanel"),
  resourceState: document.querySelector("#resourceState"),
  resourceStage: document.querySelector("#resourceStage"),
  resourceInternal: document.querySelector("#resourceInternal"),
  resourceLargest: document.querySelector("#resourceLargest"),
  resourceDma: document.querySelector("#resourceDma"),
  resourceDmaLargest: document.querySelector("#resourceDmaLargest"),
  resourcePsram: document.querySelector("#resourcePsram"),
  resourcePsramLargest: document.querySelector("#resourcePsramLargest"),
  resourceMinimum: document.querySelector("#resourceMinimum"),
  resourceOwners: document.querySelector("#resourceOwners"),
  resourceDelta: document.querySelector("#resourceDelta"),
  resourceRelease: document.querySelector("#resourceRelease"),
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
  play_audio: "Speaker Playback",
  stop_audio: "Stop Playback",
  capture_photo: "Camera Capture",
  record_microphone: "Microphone Recording",
  device_pairing: "Device Pairing",
  live_video: "Live Video",
  rtc_audio: "Full-duplex Audio",
  motion_move: "Motion Control",
  motion_stop: "Motion Stop",
  light_color: "Light Settings",
  light_effect: "Light Effect",
  light_off: "Lights Off",
  animation_play: "Animation Playback",
  animation_stop: "Animation Stop",
  rtc_av: "Audio/video Call",
  system: "System",
};

const pairingErrors = {
  invalid_pairing_code: "Pairing code must contain 6 digits",
  device_slot_occupied: "A device is already connected or pairing is in progress",
  pairing_not_found: "Device not found. Check the pairing code and network, then try again",
  device_connect_timeout: "Device connection timed out. Get a new pairing code and try again",
  reconnect_timeout: "Device reconnection timed out. Pair again",
  pairing_unavailable: "Unable to reach the SDK Daemon pairing service",
  "RTC session is not active": "RTC session is not active",
};

const rtcErrors = {
  video_source_timeout: "The camera source produced no video. Confirm that the HX6538 has the matching video-bridge firmware",
  peer_connection_failed: "The browser could not establish a real-time connection to the device",
  mjpeg_start_failed: "The device camera streamer failed to start",
  mjpeg_data_channel_closed: "The live-video data channel disconnected",
  heartbeat_timeout: "Live-video heartbeat timed out",
  audio_capture_failed: "Device audio capture failed to start",
  audio_render_failed: "Device speaker playback failed to start",
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
    throw new Error(localizeError(
      payload.message || payload.detail,
      response.status,
      payload.error,
      payload.owner,
    ));
  }
  return payload;
}

function hasCapability(name) {
  return Boolean(state.status?.capabilities?.includes(name));
}

function actionLabel(action) {
  return actionLabels[action] || action || "Unknown Action";
}

function localizeError(message, status, code = null, owner = null) {
  if (!message) return `Request failed (HTTP ${status})`;
  if (typeof message !== "string") return `Request failed (HTTP ${status})`;
  if (code === "rtc_resource_busy") {
    if (owner === "audio_playback") return "Speaker or animation audio is playing. Stop it before starting this feature";
    if (owner === "face_tracking_preview") return "Face-tracking preview is using the camera. Stop it before starting this feature";
    return "Audio/video resources are busy. Stop the related feature and try again";
  }
  const busyMatch = message.match(/^media lab is busy with (.+)$/);
  if (busyMatch) return `SDK Test Bench is busy with ${actionLabel(busyMatch[1])}`;
  const durationMatch = message.match(/^duration must be (.+)$/);
  if (durationMatch) return `Recording duration must be ${durationMatch[1]}`;
  if (message.startsWith("Robot firmware does not advertise required RTC capabilities:")) {
    return "The current firmware does not advertise the required RTC capabilities. Update it and reconnect";
  }
  if (pairingErrors[message]) return pairingErrors[message];
  if (rtcErrors[message]) return rtcErrors[message];
  return translateText(message, i18n.locale);
}

function localizeEvent(event) {
  if (["Media Lab ready", "SDK Test Bench ready"].includes(event.message)) return "SDK Test Bench ready";
  if (event.message === "Device pairing started") return "Device pairing started";
  if (event.message === "Audio stop requested") return "Playback stop requested";
  const label = actionLabel(event.action);
  if (event.message.endsWith(" started")) return `${label} started`;
  if (event.message.endsWith(" completed")) return `${label} completed`;
  if (event.message.endsWith(" stopped")) return `${label} stopped`;
  const failedAt = event.message.indexOf(" failed:");
  if (failedAt >= 0) return `${label} failed: ${event.message.slice(failedAt + 8).trim()}`;
  return event.message;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "—";
  if (Math.abs(bytes) < 1024) return `${bytes} B`;
  if (Math.abs(bytes) < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}

function formatSignedBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "—";
  return `${bytes > 0 ? "+" : ""}${formatBytes(bytes)}`;
}

function updateResourceMonitor(status) {
  const baseline = status.resources?.baseline;
  const rtcBaseline = status.resources?.rtc_baseline;
  const current = status.resources?.current;
  const latestRelease = selectLatestReleaseSnapshot(status.resources?.history);
  const lifecycleSnapshot = current?.stage === "rtc_running"
    ? current
    : latestRelease || current;
  const hasRtcBaseline = rtcBaseline && Object.keys(rtcBaseline).length > 0;
  const lifecycleBaseline = selectLifecycleBaseline(status.resources);
  const health = evaluateResourceLifecycle(
    lifecycleSnapshot,
    lifecycleBaseline,
    status.resources?.history,
  );
  const stateLabels = {
    waiting: "Waiting for Device Snapshot",
    observing: "Monitoring",
    recovered: "RTC resources returned to baseline",
    context_changed: "Media released; animation cache or context changed",
    degraded: "Resources remain allocated after RTC stopped",
    failed: "Resource release call failed",
  };
  const stageLabels = {
    baseline: "Connection Baseline",
    rtc_pre_start: "RTC Pre-start Baseline",
    periodic: "Idle Periodic Sample",
    rtc_running: "RTC Running",
    rtc_release_200ms: "200 ms After RTC Stop",
    rtc_release_1000ms: "1 s After RTC Stop",
    rtc_release_3000ms: "3 s After RTC Stop",
  };
  elements.resourcePanel.dataset.state = health.state;
  elements.resourceState.textContent = stateLabels[health.state] || health.state;
  elements.resourceStage.textContent = current
    ? `${status.connected ? "Live" : "Device offline, last sample"} · ${stageLabels[current.stage] || current.stage || "Unknown Stage"} · #${current.sequence || 0}`
    : "No evt.sdk.resource_snapshot received";

  const memory = current?.memory || {};
  elements.resourceInternal.textContent = formatBytes(memory.internal?.free_bytes);
  elements.resourceLargest.textContent = formatBytes(memory.internal?.largest_free_block_bytes);
  elements.resourceDma.textContent = formatBytes(memory.dma?.free_bytes);
  elements.resourceDmaLargest.textContent = formatBytes(memory.dma?.largest_free_block_bytes);
  elements.resourcePsram.textContent = memory.psram
    ? formatBytes(memory.psram.free_bytes)
    : "Disabled";
  elements.resourcePsramLargest.textContent = memory.psram
    ? formatBytes(memory.psram.largest_free_block_bytes)
    : "Disabled";
  elements.resourceMinimum.textContent = formatBytes(memory.internal?.minimum_free_bytes);

  const resourceLabels = {
    rtc: "RTC",
    media_system: "Media System",
    tts_playback: "Speaker",
    microphone_runtime: "Microphone",
    voice_runtime: "Voice Task",
    face_tracking_preview: "Face Preview",
    audio_codec: "Audio Codec",
    animation: "Screen Animation",
    animation_runtime: "Animation Runtime",
  };
  const owners = Object.entries(current?.resources || {})
    .filter(([name, active]) => name !== "voice_state" && active === true)
    .map(([name]) => resourceLabels[name] || name);
  elements.resourceOwners.textContent = owners.length > 0 ? owners.join(" / ") : "No Active Media Resources";
  elements.resourceDelta.textContent = lifecycleBaseline
    ? `Against ${hasRtcBaseline ? "RTC pre-start" : "Connection Baseline"}: internal ${formatSignedBytes(health.deltas.internalFreeBytes)} / ${formatSignedBytes(health.deltas.internalLargestBytes)} · DMA ${formatSignedBytes(health.deltas.dmaLargestBytes)} · PSRAM ${formatSignedBytes(health.deltas.psramLargestBytes)}${health.trend?.monotonicDecline ? " · declined after 4 consecutive releases" : ""}`
    : "Waiting for Resource Baseline";

  const release = current?.release;
  if (!release || !release.sequence) {
    elements.resourceRelease.textContent = "No RTC Stop Record";
  } else if (release.complete === false) {
    elements.resourceRelease.textContent = `Failed: ${(release.failures || []).join(" / ") || "Unknown Release Step"}`;
  } else {
    elements.resourceRelease.textContent = `Success · RTC release #${release.sequence}`;
  }
}

function updateAnimationConfirmation(status) {
  if (!state.animation.requestedId) return;
  if (!state.animation.requestAccepted) return;
  const outcome = evaluateAnimationConfirmation({
    animationId: state.animation.requestedId,
    requestedAtMs: state.animation.requestedAtMs,
    nowMs: Date.now(),
    active: status.resources?.current?.resources?.animation === true
      || status.rtc?.stats?.animation_active === true,
  });
  if (outcome.state === state.animation.lastState && outcome.state === "pending") return;
  state.animation.lastState = outcome.state;
  if (outcome.state === "confirmed") {
    setResult(elements.animationResult, `Animation confirmed by device first frame: ${outcome.animationId}`, "ok");
    state.animation.requestedId = null;
    state.animation.requestAccepted = false;
  } else if (outcome.state === "failed") {
    const message = `Device did not confirm the animation first frame within 5 s: ${outcome.animationId}`;
    setResult(elements.animationResult, message, "error");
    notify(message, "error");
    state.animation.requestedId = null;
    state.animation.requestAccepted = false;
  } else if (outcome.state === "pending") {
    setResult(elements.animationResult, `Playback accepted; waiting for the device first frame: ${outcome.animationId}`, "running");
  }
}

function renderAnimationCatalog(value, connected) {
  const catalog = normalizeAnimationCatalog(value);
  const fingerprint = catalog.join("\u0000");
  if (fingerprint !== state.animation.catalogFingerprint) {
    state.animation.catalog = catalog;
    state.animation.catalogFingerprint = fingerprint;
    elements.animationSuggestions.replaceChildren(...catalog.map((animationId) => {
      const option = document.createElement("option");
      option.value = animationId;
      return option;
    }));
    const selectedId = elements.animationId.value.trim();
    if (catalog.length > 0 && !catalog.includes(selectedId)) {
      elements.animationId.value = catalog[0];
    }
  }
  elements.animationCatalogSummary.textContent = !connected
    ? `Device offline · retained ${catalog.length} animations`
    : catalog.length > 0
      ? `Device reported ${catalog.length} playable animations · full shuffled cycle without repeats`
      : "The current firmware has not reported an animation catalog";
}

function renderStatus(status) {
  state.status = status;
  renderAnimationCatalog(status.animations, status.connected);
  if (!status.connected && state.animation.random.active) stopRandomAnimation({ quiet: true });
  elements.connectionBadge.dataset.state = status.connected ? "online" : "offline";
  elements.connectionText.textContent = status.connected ? "Device Online" : "Device Disconnected";
  elements.deviceId.textContent = status.device?.device_id || "Unidentified";
  elements.firmwareVersion.textContent = status.device?.firmware_version || "Unknown";
  elements.capabilityCount.textContent = String(status.capabilities.length).padStart(2, "0");
  elements.lastSync.textContent = new Date().toLocaleTimeString([], { hour12: false });
  updateResourceMonitor(status);
  updateAnimationConfirmation(status);
  const owners = status.resource_owners || {};
  const localActions = [...state.localResources];
  const activeLabels = Object.values(owners).map(actionLabel);
  elements.activeOperation.textContent = !status.connected
    ? "Device disconnected. Reconnect before testing"
    : activeLabels.length > 0
      ? `Running / ${activeLabels.join(" + ")}`
      : localActions.length > 0 ? `Command sent / ${localActions.join(" + ")}` : "System Idle";

  const pairingState = status.connection?.state || "unavailable";
  const pairingInProgress = ["discovering", "connecting", "reconnecting"].includes(pairingState);
  elements.pairingPanel.hidden = status.connected;
  elements.pairingButton.disabled = state.pairingBusy || pairingInProgress;
  elements.pairingCode.disabled = state.pairingBusy || pairingInProgress;
  elements.deviceIp.disabled = state.pairingBusy || pairingInProgress;
  if (status.connected) {
    setResult(elements.pairingResult, "Device paired", "ok");
  } else if (state.pairingBusy || pairingState === "discovering") {
    setResult(elements.pairingResult, "Discovering device…", "running");
  } else if (pairingState === "connecting" || pairingState === "reconnecting") {
    setResult(elements.pairingResult, "Device found; connecting…", "running");
  } else if (status.connection?.last_error) {
    setResult(
      elements.pairingResult,
      pairingErrors[status.connection.last_error] || status.connection.last_error,
      "error",
    );
  }

  document.querySelectorAll("[data-capability]").forEach((station) => {
    const available = status.capabilities.includes(station.dataset.capability);
    station.dataset.available = String(status.connected && available);
    station.querySelector(".capability-state").textContent = !status.connected
      ? "Device Offline"
      : available ? "Ready" : "Not Advertised";
  });

  const activeRtcMode = resolveRtcMode(
    state.rtc.mode,
    status.rtc?.mode,
    owners.camera || owners.microphone || owners.speaker,
    status.rtc?.active === true,
  );
  const rtcActive = Boolean(activeRtcMode || status.rtc?.active);
  const availability = controlAvailability({
    connected: status.connected,
    capabilities: status.capabilities,
    resourceOwners: owners,
    localResources: state.localResources,
    rtcActive,
    rtcMode: activeRtcMode,
  });
  const liveAvailable = status.connected && hasCapability("rtc.video.mjpeg.v1");
  const liveActive = rtcModeHasVideo(activeRtcMode);
  const rtcAudioAvailable = status.connected && hasCapability("rtc.audio.full_duplex.v1");
  const rtcAudioActive = rtcModeHasAudio(activeRtcMode);
  elements.liveVideoPanel.dataset.available = String(liveAvailable);
  elements.liveVideoCapability.textContent = !status.connected
    ? "Device Offline"
    : liveAvailable ? "Ready" : "New Firmware Required";
  elements.startLiveVideoButton.disabled = !availability.startRtcVideo || !liveAvailable || state.rtc.teardownInProgress;
  elements.stopLiveVideoButton.disabled = !availability.stopRtc || !liveActive;
  elements.rtcAudioPanel.dataset.available = String(rtcAudioAvailable);
  elements.rtcAudioCapability.textContent = !status.connected
    ? "Device Offline"
    : rtcAudioAvailable ? "Ready" : "New Firmware Required";
  elements.startRtcAudioButton.disabled = !availability.startRtcAudio || !rtcAudioAvailable || state.rtc.teardownInProgress;
  elements.startRtcAvButton.disabled = !availability.startRtcAv || !liveAvailable || !rtcAudioAvailable
    || state.rtc.teardownInProgress;
  elements.stopRtcAudioButton.disabled = !availability.stopRtc || !rtcAudioActive;
  updateLiveVideoHealth();
  updateRtcAudioHealth();
  elements.playAudioButton.disabled = !availability.speaker || !hasCapability("audio.stream");
  elements.stopAudioButton.disabled = !status.connected || !hasCapability("audio.stream");
  elements.capturePhotoButton.disabled = !availability.camera || !hasCapability("camera.capture");
  elements.recordMicrophoneButton.disabled = !availability.microphone || !hasCapability("microphone");
  elements.applyMotionButton.disabled = !availability.motion;
  elements.stopMotionButton.disabled = !status.connected || !hasCapability("motion");
  elements.applyLightButton.disabled = !availability.light;
  elements.playLightEffectButton.disabled = !availability.light;
  elements.lightsOffButton.disabled = !status.connected || !hasCapability("light");
  elements.playAnimationButton.disabled = !availability.animation;
  elements.stopAnimationButton.disabled = !status.connected || !hasCapability("animation");
  elements.animationId.disabled = !status.connected || !hasCapability("animation");
  elements.animationRandomInterval.disabled = !status.connected || state.animation.random.active;
  elements.startRandomAnimationButton.disabled = !availability.animation
    || state.animation.catalog.length === 0
    || state.animation.random.active;
  elements.stopRandomAnimationButton.disabled = !state.animation.random.active;
  elements.runAllButton.disabled = status.busy || state.localResources.size > 0 || !status.connected || ![
    "motion", "light", "audio.stream", "camera.capture", "microphone",
  ].every(hasCapability);

  elements.capabilityGrid.replaceChildren(...status.capabilities.map((capability) => {
    const chip = document.createElement("span");
    chip.className = "capability-chip";
    chip.dataset.media = String(["audio.stream", "camera.capture", "microphone", "rtc.video.mjpeg.v1", "rtc.audio.full_duplex.v1"].includes(capability));
    chip.textContent = capability;
    return chip;
  }));
  elements.capabilitySummary.textContent = status.connected
    ? `${status.capabilities.length} capabilities online`
    : `Device Offline · ${status.capabilities.length} previously negotiated capabilities`;
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
    elements.connectionText.textContent = "Test Bench Offline";
    if (!quiet) notify(error.message, "error");
  }
}

async function runAction({
  path,
  result,
  pending,
  complete,
  body,
  station,
  resource = "media",
  resources = null,
  interrupt = false,
}) {
  const actionResources = resources || [resource];
  if (actionResources.some((name) => state.localResources.has(name)) && !interrupt) return null;
  if (!state.status?.connected) {
    const error = new Error("Device disconnected. Reconnect before testing");
    notify(error.message, "error");
    throw error;
  }
  const ownsResource = !interrupt;
  if (ownsResource) actionResources.forEach((name) => state.localResources.add(name));
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
    if (ownsResource) actionResources.forEach((name) => state.localResources.delete(name));
    if (station) station.dataset.running = "false";
    await refreshStatus();
  }
}

async function pairDevice() {
  const pairingCode = elements.pairingCode.value.trim();
  const deviceIp = elements.deviceIp.value.trim();
  if (!/^[0-9]{6}$/.test(pairingCode)) {
    const message = "Pairing code must contain 6 digits";
    setResult(elements.pairingResult, message, "error");
    notify(message, "error");
    elements.pairingCode.focus();
    return;
  }
  state.pairingBusy = true;
  setResult(elements.pairingResult, "Submitting pairing request…", "running");
  renderStatus(state.status);
  try {
    await api("/api/device/pair", {
      method: "POST",
      body: JSON.stringify({ pairing_code: pairingCode, device_ip: deviceIp || null }),
    });
    elements.pairingCode.value = "";
    setResult(elements.pairingResult, "Discovering device…", "running");
    notify("Pairing request submitted. Keep the robot powered on", "ok");
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
    context.fillText(i18n.translate("Unable to decode recording waveform"), 20, height / 2);
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
  context.fillText(i18n.translate("Waiting for PCM Audio"), 18, height / 2 - 14);
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
    mediaRttUs: 0,
    feedbackReceivedFrames: 0,
    feedbackDroppedFrames: 0,
    videoCongestionFeedback: createVideoCongestionFeedback(),
    mjpegChunkReassembler: createMjpegChunkReassembler(),
  });
  elements.liveVideoFps.textContent = "0.0 FPS";
  elements.liveVideoResolution.textContent = "—";
  elements.liveVideoDrops.textContent = "0";
  elements.liveVideoFrameAge.textContent = "NO FRAME";
}

function updateMotionPreview() {
  const pan = Number(elements.panControl.value);
  const tilt = Number(elements.tiltControl.value);
  elements.panValue.textContent = `${pan}°`;
  elements.tiltValue.textContent = `${tilt}°`;
  elements.motionHead.style.transform = `rotate(${(pan - 90) * 0.42}deg) translateY(${(tilt - 90) * 0.18}px)`;
}

async function applyMotion() {
  const pan = Number(elements.panControl.value);
  const tilt = Number(elements.tiltControl.value);
  return runAction({
    path: "/api/controls/motion/move",
    body: { pan_deg: pan, tilt_deg: tilt, duration_ms: 600 },
    result: elements.motionResult,
    pending: `Moving to PAN ${pan}° / TILT ${tilt}°…`,
    complete: () => `Move complete: PAN ${pan}° / TILT ${tilt}°`,
    station: elements.applyMotionButton.closest(".control-card"),
    resource: "motion",
  });
}

async function stopMotion() {
  return runAction({
    path: "/api/controls/motion/stop",
    result: elements.motionResult,
    pending: "Stopping motion…",
    complete: () => "Motion stopped",
    resource: "motion",
    interrupt: true,
  });
}

function updateLightPreview() {
  const color = elements.lightColor.value;
  const brightness = Number(elements.lightBrightness.value);
  elements.brightnessValue.textContent = `${brightness}%`;
  elements.lightVisual.style.setProperty("--light-color", color);
  elements.lightVisual.style.setProperty("--light-alpha", String(Math.max(0.08, brightness / 100)));
}

async function applyLight() {
  const body = {
    color: elements.lightColor.value,
    brightness: Number(elements.lightBrightness.value) / 100,
    zone: elements.lightZone.value,
  };
  return runAction({
    path: "/api/controls/lights/color",
    body,
    result: elements.lightResult,
    pending: "Applying lights…",
    complete: () => `Lights applied: ${body.color.toUpperCase()} / ${Math.round(body.brightness * 100)}%`,
    station: elements.applyLightButton.closest(".control-card"),
    resource: "light",
  });
}

async function playLightEffect() {
  const body = {
    effect: elements.lightEffect.value,
    color: elements.lightColor.value,
    brightness: Number(elements.lightBrightness.value) / 100,
    zone: elements.lightZone.value,
    period_ms: 800,
  };
  return runAction({
    path: "/api/controls/lights/effect",
    body,
    result: elements.lightResult,
    pending: "Starting light effect…",
    complete: () => `Light effect started: ${elements.lightEffect.selectedOptions[0].textContent}`,
    station: elements.applyLightButton.closest(".control-card"),
    resource: "light",
  });
}

async function lightsOff() {
  return runAction({
    path: "/api/controls/lights/off",
    result: elements.lightResult,
    pending: "Turning lights off…",
    complete: () => "Lights off",
    resource: "light",
    interrupt: true,
  });
}

async function prefetchAnimation(animationId, { quiet = true } = {}) {
  if (!hasCapability("animation.prefetch.v1") || !state.status?.connected) return null;
  if (!state.animation.catalog.includes(animationId) || state.animation.prefetchedId === animationId) return null;
  if (state.animation.prefetchPromise) {
    try { await state.animation.prefetchPromise; } catch (_) { /* A later hint may still succeed. */ }
    if (state.animation.prefetchedId === animationId) return null;
  }
  const request = api("/api/controls/animation/prefetch", {
    method: "POST",
    body: JSON.stringify({ animation_id: animationId }),
  });
  state.animation.prefetchPromise = request;
  try {
    const payload = await request;
    state.animation.prefetchedId = animationId;
    return payload;
  } catch (error) {
    if (!quiet) notify(`Animation prefetch failed: ${error.message}`, "error");
    return null;
  } finally {
    if (state.animation.prefetchPromise === request) state.animation.prefetchPromise = null;
  }
}

function clearRandomAnimationTimers() {
  window.clearTimeout(state.animation.random.switchTimer);
  window.clearTimeout(state.animation.random.prefetchTimer);
  state.animation.random.switchTimer = null;
  state.animation.random.prefetchTimer = null;
}

function stopRandomAnimation({ quiet = false } = {}) {
  if (!state.animation.random.active && quiet) return;
  state.animation.random.active = false;
  state.animation.random.generation += 1;
  state.animation.random.remainingIds = [];
  clearRandomAnimationTimers();
  elements.startRandomAnimationButton.disabled = !state.status?.connected || state.animation.catalog.length === 0;
  elements.stopRandomAnimationButton.disabled = true;
  elements.animationRandomInterval.disabled = !state.status?.connected;
  if (!quiet) {
    setResult(elements.animationResult, "Random playback stopped; the current animation remains visible", "ok");
    notify("Random animation playback stopped", "ok");
  }
}

async function playAnimation({ fromRandom = false } = {}) {
  const animationId = elements.animationId.value.trim();
  if (!/^[a-z][a-z0-9_]{0,62}$/.test(animationId)) {
    const message = "Animation ID may contain only lowercase letters, numbers, and underscores";
    setResult(elements.animationResult, message, "error");
    notify(message, "error");
    return null;
  }
  if (state.animation.catalog.length > 0 && !state.animation.catalog.includes(animationId)) {
    const message = `Device did not report animation: ${animationId}`;
    setResult(elements.animationResult, message, "error");
    notify(message, "error");
    return null;
  }
  if (!fromRandom) stopRandomAnimation({ quiet: true });
  await prefetchAnimation(animationId);
  state.animation.requestedId = animationId;
  state.animation.requestedAtMs = Date.now();
  state.animation.requestAccepted = false;
  state.animation.lastState = "idle";
  try {
    return await runAction({
      path: "/api/controls/animation/play",
      body: { animation_id: animationId },
      result: elements.animationResult,
      pending: `Submitting animation ${animationId}…`,
      complete: () => {
        state.animation.requestAccepted = true;
        state.animation.requestedAtMs = Date.now();
        return `Playback accepted; waiting for the device first frame: ${animationId}`;
      },
      station: elements.playAnimationButton.closest(".control-card"),
      resource: "animation",
    });
  } catch (error) {
    state.animation.requestedId = null;
    state.animation.requestAccepted = false;
    state.animation.lastState = "failed";
    setResult(elements.animationResult, error.message, "error");
    throw error;
  }
}

async function runRandomAnimation(generation) {
  const randomState = state.animation.random;
  if (!randomState.active || randomState.generation !== generation) return;
  randomState.remainingIds = randomState.remainingIds.filter(
    (animationId) => state.animation.catalog.includes(animationId),
  );
  if (randomState.remainingIds.length === 0) {
    randomState.remainingIds = createAnimationShuffleBag(
      state.animation.catalog,
      randomState.lastId,
    );
  }
  const animationId = randomState.remainingIds.shift() || null;
  if (!animationId) {
    stopRandomAnimation({ quiet: true });
    setResult(elements.animationResult, "The device has no animations available for randomized playback", "error");
    return;
  }
  elements.animationId.value = animationId;
  try {
    const result = await playAnimation({ fromRandom: true });
    if (!result || !randomState.active || randomState.generation !== generation) return;
  } catch (_) {
    stopRandomAnimation({ quiet: true });
    return;
  }

  randomState.lastId = animationId;
  if (randomState.remainingIds.length === 0) {
    randomState.remainingIds = createAnimationShuffleBag(state.animation.catalog, animationId);
  }
  const nextId = randomState.remainingIds[0] || null;
  const prefetchDelayMs = Math.max(1000, Math.floor(randomState.intervalMs / 2));
  randomState.prefetchTimer = window.setTimeout(() => {
    if (randomState.active && randomState.generation === generation && nextId) {
      prefetchAnimation(nextId).catch(() => {});
    }
  }, prefetchDelayMs);
  randomState.switchTimer = window.setTimeout(() => {
    runRandomAnimation(generation).catch(() => {});
  }, randomState.intervalMs);
}

function startRandomAnimation() {
  if (state.animation.catalog.length === 0) {
    const message = "The device has not reported an animation catalog";
    setResult(elements.animationResult, message, "error");
    notify(message, "error");
    return;
  }
  clearRandomAnimationTimers();
  const randomState = state.animation.random;
  randomState.active = true;
  randomState.generation += 1;
  randomState.intervalMs = clampAnimationIntervalMs(Number(elements.animationRandomInterval.value) * 1000);
  randomState.remainingIds = createAnimationShuffleBag(state.animation.catalog, randomState.lastId);
  elements.animationRandomInterval.value = String(randomState.intervalMs / 1000);
  elements.startRandomAnimationButton.disabled = true;
  elements.stopRandomAnimationButton.disabled = false;
  elements.animationRandomInterval.disabled = true;
  setResult(
    elements.animationResult,
    `Random playback started · switching every ${randomState.intervalMs / 1000} s · this shuffled cycle covers ${randomState.remainingIds.length} animations`,
    "running",
  );
  runRandomAnimation(randomState.generation).catch(() => {});
}

function scheduleAnimationPrefetch() {
  window.clearTimeout(state.animation.prefetchDebounceTimer);
  const animationId = elements.animationId.value.trim();
  if (!state.animation.catalog.includes(animationId)) return;
  state.animation.prefetchDebounceTimer = window.setTimeout(() => {
    prefetchAnimation(animationId).catch(() => {});
  }, 250);
}

async function stopAnimation() {
  stopRandomAnimation({ quiet: true });
  state.animation.requestedId = null;
  state.animation.requestAccepted = false;
  state.animation.lastState = "idle";
  return runAction({
    path: "/api/controls/animation/stop",
    result: elements.animationResult,
    pending: "Stopping animation…",
    complete: () => "Animation stopped",
    resource: "animation",
    interrupt: true,
  });
}

function rtcEndpoint(action, mode = state.rtc.mode) {
  const namespace = mode === "video" ? "video" : "rtc";
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
  if (!rtcModeHasAudio(state.rtc.mode) || !state.rtc.peer) return;
  const deviceStats = state.status?.rtc?.stats || {};
  const captureFrames = Number(deviceStats.audio_capture_frames || 0);
  const txPackets = Number(deviceStats.audio_tx_packets || 0);
  const txErrors = Number(deviceStats.audio_tx_errors || 0);
  const hasRawMicrophonePeak = Number.isFinite(Number(deviceStats.audio_microphone_peak));
  const capturePeak = hasRawMicrophonePeak
    ? Number(deviceStats.audio_microphone_peak)
    : Number(deviceStats.audio_capture_peak || 0);
  const aecActive = deviceStats.audio_aec_active === true;
  const aecReferenceBytes = Number(
    deviceStats.audio_aec_reference_processed_bytes
      ?? deviceStats.audio_aec_reference_bytes
      ?? 0,
  );
  const aecReferenceDrops = Number(deviceStats.audio_aec_reference_drops || 0);
  const devicePipelineAgeUs = Number(deviceStats.audio_pipeline_age_ewma_us || 0);
  const microphoneReadUs = Number(deviceStats.audio_microphone_read_ewma_us || 0);
  const aecProcessUs = Number(deviceStats.audio_aec_process_ewma_us || 0);
  const opusEncodeUs = Number(deviceStats.audio_opus_encode_ewma_us || 0);
  const deviceRxPackets = Number(deviceStats.audio_packets || 0);
  const deviceDecodedFrames = Number(deviceStats.audio_decoded_frames || 0);
  const deviceRenderErrors = Number(deviceStats.audio_render_errors || 0);
  const deviceI2sBytes = Number(deviceStats.audio_i2s_bytes || 0);
  const devicePlaybackPeak = Number(deviceStats.audio_pcm_peak || 0);
  elements.rtcAudioDeviceCapture.textContent = String(captureFrames);
  elements.rtcAudioDeviceTx.textContent = txErrors > 0 ? `${txPackets} / errors ${txErrors}` : String(txPackets);
  elements.rtcAudioSignal.textContent = `${capturePeak} / ${state.rtc.browserAudioLevel.toFixed(3)}`;
  elements.rtcAudioAec.textContent = !hasRawMicrophonePeak
    ? "Legacy firmware: no physical microphone telemetry"
    : !aecActive
      ? "Disabled (raw microphone fallback)"
      : aecReferenceDrops > 0
        ? `Active · reference processed ${formatBytes(aecReferenceBytes)} · dropped ${aecReferenceDrops}`
        : aecReferenceBytes > 0
          ? `Active · reference processed ${formatBytes(aecReferenceBytes)}`
          : "Active · waiting for computer downlink reference audio";
  const browserLatency = state.rtc.audioLatency;
  const estimatedNetworkOneWayMs = state.rtc.rttUs > 0 ? state.rtc.rttUs / 2000 : 0;
  const processingLatency = microphoneReadUs > 0 || aecProcessUs > 0 || opusEncodeUs > 0
    ? ` · microphone frame ${(microphoneReadUs / 1000).toFixed(1)} ms · AEC ${(aecProcessUs / 1000).toFixed(1)} ms · OPUS ${(opusEncodeUs / 1000).toFixed(1)} ms`
    : "";
  const networkLatency = estimatedNetworkOneWayMs > 0
    ? ` · network approx. ${estimatedNetworkOneWayMs.toFixed(1)} ms`
    : "";
  elements.rtcAudioLatency.textContent = browserLatency.sampleValid || devicePipelineAgeUs > 0
    ? `Device queue ${(devicePipelineAgeUs / 1000).toFixed(1)} ms${networkLatency} · Browser ${browserLatency.actualMs} ms (target ${browserLatency.targetMs} ms, minimum ${browserLatency.minimumMs} ms)${processingLatency}`
    : "Waiting for Stage Latency Samples";
  const health = evaluateRtcAudioHealth({
    peerConnected: state.rtc.peer.connectionState === "connected",
    browserTxPackets: state.rtc.browserAudioSent,
    browserRxPackets: state.rtc.browserAudioReceived,
    deviceCaptureFrames: captureFrames,
    deviceTxPackets: txPackets,
    deviceTxErrors: txErrors,
    deviceCapturePeak: capturePeak,
    browserAudioLevel: state.rtc.browserAudioLevel,
    browserPlaybackActive: !elements.rtcRemoteAudio.paused
      && !elements.rtcRemoteAudio.muted
      && elements.rtcRemoteAudio.volume > 0,
    deviceRxPackets,
    deviceDecodedFrames,
    deviceRenderErrors,
    deviceI2sBytes,
    devicePlaybackPeak,
    elapsedMs: state.rtc.audioConnectedAt ? performance.now() - state.rtc.audioConnectedAt : 0,
  });
  if (health.state === state.rtc.audioHealthState && health.state !== "failed") return;
  state.rtc.audioHealthState = health.state;
  if (health.state === "healthy") {
    setRtcAudioState("connected");
    setResult(elements.rtcAudioResult, "Full-duplex path verified: the browser is playing a non-silent Watcher audio track", "ok");
  } else if (health.state === "degraded") {
    setRtcAudioState("connected");
    setResult(
      elements.rtcAudioResult,
      `Two-way audio connected, but the device had ${txErrors} send errors and the speaker had ${deviceRenderErrors} render errors`,
      "error",
    );
  } else if (health.state === "failed") {
    const missingDeviceCapture = health.missing.includes("device_capture");
    const missingDeviceSignal = health.missing.includes("device_signal");
    const missingBrowserSignal = health.missing.includes("browser_signal");
    const missingBrowserPlayback = health.missing.includes("browser_playback");
    const missingDevicePlayback = health.missing.some((item) => [
      "device_rx", "device_decode", "device_playback", "device_playback_signal",
    ].includes(item));
    const message = missingDeviceCapture
      ? "The robot microphone produced no audio frames. Inspect microphone capture and audio resource ownership"
      : missingDeviceSignal
        ? "The robot sent audio packets, but capture is nearly silent. Speak toward the robot microphone and inspect the capture path"
        : missingBrowserSignal
          ? "The browser received robot audio packets, but the decoded signal is nearly silent. Inspect encoding and the browser audio track"
          : missingBrowserPlayback
            ? "Robot audio arrived, but the browser player is paused or muted. Enable sound in the player"
            : missingDevicePlayback
              ? "Computer audio was sent, but the robot did not complete audible decode and speaker output. Inspect device playback metrics"
      : "Robot microphone audio did not reach the computer. Inspect robot transmit counters and error codes";
    setRtcAudioState("failed", message);
  } else if (health.state === "verifying") {
    setRtcAudioState("connecting", "Media connected; validating robot microphone uplink…");
  }
}

function updateLiveVideoHealth() {
  const stats = state.status?.rtc?.stats || {};
  const sourceFps = Number(stats.source_fps_x100 || 0) / 100;
  const targetFps = Number(stats.target_fps || 0);
  const sentFps = Number(stats.sent_fps_x100 || 0) / 100;
  const jpegBytes = Number(stats.jpeg_average_bytes || 0);
  const egressP95Us = Number(stats.video_egress_p95_us || 0);
  const browserCongestion = Number(stats.browser_congestion_level || 0);
  const animationPressure = Number(stats.animation_pressure_level || 0);
  const animationFps = Number(stats.animation_measured_fps_x100 || 0) / 100;
  const animationTargetFps = Number(stats.animation_target_fps_x100 || 0) / 100;
  const animationUnderruns = Number(stats.animation_recent_underruns || 0);
  const animationLateMaxUs = Number(stats.animation_late_max_us || 0);
  elements.liveVideoPipelineFps.textContent = sourceFps || targetFps || sentFps
    ? `${sourceFps.toFixed(1)} / ${targetFps} / ${sentFps.toFixed(1)} FPS`
    : "—";
  elements.liveVideoTransport.textContent = jpegBytes || egressP95Us
    ? `${formatBytes(jpegBytes)} / ${(egressP95Us / 1000).toFixed(1)} MS`
    : "—";
  elements.liveVideoCongestion.textContent = `Browser ${browserCongestion} / Animation ${animationPressure}`;
  elements.liveVideoAnimation.textContent = animationFps || animationTargetFps || animationUnderruns || animationLateMaxUs
    ? `${animationFps.toFixed(1)} / ${animationTargetFps.toFixed(1)} FPS · underruns ${animationUnderruns} · late ${(animationLateMaxUs / 1000).toFixed(1)} MS`
    : "No active animation detected";
}

function setRtcSessionState(value, message = null) {
  if (rtcModeHasAudio(state.rtc.mode)) setRtcAudioState(value, message);
  if (rtcModeHasVideo(state.rtc.mode)) setLiveVideoState(value, message);
}

async function startRtcSession(mode) {
  const wantsAudio = rtcModeHasAudio(mode);
  const wantsVideo = rtcModeHasVideo(mode);
  if (
    !["audio", "video", "av"].includes(mode)
    || state.rtc.mode
    || state.rtc.peer
    || state.rtc.teardownInProgress
    || state.localResources.has("media")
    || state.status?.resource_owners?.media
    || (wantsAudio && !hasCapability("rtc.audio.full_duplex.v1"))
    || (wantsVideo && !hasCapability("rtc.video.mjpeg.v1"))
  ) return;
  const generation = state.rtc.generation + 1;
  state.rtc.generation = generation;
  state.rtc.mode = mode;
  state.localResources.add("media");
  if (state.status) renderStatus(state.status);
  resetLiveVideoMetrics();
  elements.rtcAudioUpPackets.textContent = "0";
  elements.rtcAudioDownPackets.textContent = "0";
  elements.rtcAudioDeviceCapture.textContent = "0";
  elements.rtcAudioDeviceTx.textContent = "0";
  elements.rtcAudioSignal.textContent = "0 / 0.000";
  elements.rtcAudioAec.textContent = "Waiting for Device Telemetry";
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.browserAudioLevel = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "starting";
  state.rtc.audioJitterCounter = null;
  state.rtc.audioLatency = { sampleValid: false, actualMs: 0, targetMs: 0, minimumMs: 0 };
  if (wantsAudio) setRtcAudioState("starting", "Requesting computer microphone permission…");
  if (wantsVideo) {
    setLiveVideoState("starting", wantsAudio
      ? "Acquiring camera, audio, and real-time transport resources…"
      : "Acquiring camera and real-time transport resources…");
  }
  elements.startRtcAudioButton.disabled = true;
  elements.startLiveVideoButton.disabled = true;
  elements.startRtcAvButton.disabled = true;
  try {
    let localStream = null;
    if (wantsAudio) {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("This browser does not support microphone capture");
      }
      localStream = rtcDiagnosticAudioEnabled()
        ? await createRtcDiagnosticAudioStream()
        : await navigator.mediaDevices.getUserMedia({
            audio: createRtcMicrophoneConstraints({
              browserProcessing: rtcBrowserAudioProcessingEnabled(),
            }),
            video: false,
          });
      if (state.rtc.generation !== generation || state.rtc.mode !== mode) {
        for (const track of localStream.getTracks()) track.stop();
        return;
      }
      state.rtc.localStream = localStream;
      elements.rtcAudioLocalState.textContent = "Capturing";
    }

    const startPath = mode === "video" ? "/api/video/session/start" : "/api/rtc/session/start";
    if (mode === "video") {
      await api("/api/video/session/start", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
    } else {
      await api("/api/rtc/session/start", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
    }
    if (state.rtc.generation !== generation || state.rtc.mode !== mode) {
      try { await api(`${startPath.slice(0, -5)}stop`, { method: "POST" }); } catch (_) {}
      return;
    }
    const peer = new RTCPeerConnection({ iceServers: [] });
    state.rtc.peer = peer;
    if (wantsAudio && localStream) {
      for (const track of localStream.getAudioTracks()) peer.addTrack(track, localStream);
      peer.addEventListener("track", (event) => {
        if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
        const remoteStream = event.streams[0] || new MediaStream([event.track]);
        state.rtc.remoteStream = remoteStream;
        elements.rtcRemoteAudio.srcObject = remoteStream;
        configureLowLatencyAudioReceivers(peer);
        elements.rtcRemoteAudio.play().catch(() => {
          setResult(elements.rtcAudioResult, "Downlink audio arrived. Click the player to enable sound", "running");
        });
      });
    }
    if (wantsVideo) createMjpegVideoTransport(peer, generation);
    bindRtcPeerEvents(peer, generation);
    startRtcControlLoops(generation);
    const offer = await peer.createOffer();
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
    await peer.setLocalDescription(offer);
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
    await api(rtcEndpoint("signal"), {
      method: "POST",
      body: JSON.stringify({ kind: "offer", sdp: offer.sdp }),
    });
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
    if (wantsAudio) setRtcAudioState("signaling", "Computer microphone is active; waiting for Watcher…");
    if (wantsVideo) setLiveVideoState("signaling", wantsAudio
      ? "Audio/video offer sent; waiting for Watcher…"
      : "Browser offer sent; waiting for Watcher…");
    await refreshStatus();
  } catch (error) {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    const message = error?.name === "NotAllowedError"
      ? "Computer microphone permission was denied. Allow access and try again"
      : error?.name === "NotFoundError"
        ? "No computer microphone is available"
        : error.message;
    await failRtcSession(message);
  }
}

function bindRtcPeerEvents(peer, generation) {
  peer.addEventListener("connectionstatechange", () => {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
    const connectionState = peer.connectionState;
    if (connectionState === "connected" && rtcModeHasAudio(state.rtc.mode)) {
      state.rtc.audioConnectedAt = performance.now();
      state.rtc.audioHealthState = "connecting";
      setRtcAudioState("connecting", "Media connected; validating robot microphone uplink…");
    }
    if (["failed", "disconnected", "closed"].includes(connectionState) && state.rtc.peer) {
      failRtcSession(`WebRTC connection ${connectionState === "failed" ? "Failed" : "disconnected"}`);
    }
  });
  peer.addEventListener("icecandidate", (event) => {
    if (
      !event.candidate
      || !isCurrentRtcGeneration(state.rtc.generation, generation)
      || state.rtc.peer !== peer
    ) return;
    api(rtcEndpoint("signal"), {
      method: "POST",
      body: JSON.stringify({
        kind: "candidate",
        candidate: event.candidate.candidate,
        sdp_mid: event.candidate.sdpMid || "0",
        sdp_mline_index: event.candidate.sdpMLineIndex || 0,
      }),
    }).catch((error) => {
      if (isCurrentRtcGeneration(state.rtc.generation, generation) && state.rtc.peer === peer) {
        failRtcSession(error.message);
      }
    });
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

function createMjpegVideoTransport(peer, generation) {
  const controlChannel = peer.createDataChannel("rtc-control", { ordered: true });
  state.rtc.channel = controlChannel;
  const url = state.status?.connection?.mjpeg_websocket_url;
  if (!url) throw new Error("Device did not provide a direct live-video URL");
  const socket = new WebSocket(url);
  state.rtc.videoSocket = socket;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.videoSocket !== socket) return;
    socket.send("ready");
    setLiveVideoState("connected");
    setResult(
      elements.liveVideoResult,
      rtcModeHasAudio(state.rtc.mode) ? "Audio/video channel connected" : "Live-video channel connected",
      "ok",
    );
  });
  socket.addEventListener("message", (event) => {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.videoSocket !== socket) return;
    enqueueMjpegPacket(event.data, generation);
  });
  socket.addEventListener("close", () => {
    if (
      isCurrentRtcGeneration(state.rtc.generation, generation)
      && state.rtc.videoSocket === socket
      && state.rtc.peer === peer
      && !state.rtc.teardownInProgress
    ) {
      failRtcSession("Live-video channel closed");
    }
  });
}

async function startLiveVideo() {
  return startRtcSession("video");
}

async function startRtcAudio() {
  return startRtcSession("audio");
}

function startRtcControlLoops(generation) {
  pollRtcEvents(generation);
  state.rtc.heartbeatTimer = window.setInterval(async () => {
    if (!state.rtc.peer || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    const browserSendUs = Math.round((performance.timeOrigin + performance.now()) * 1000);
    try {
      await api(rtcEndpoint("clock-ping"), {
        method: "POST",
        body: JSON.stringify({ browser_send_us: browserSendUs }),
      });
    } catch (error) {
      if (state.rtc.peer && isCurrentRtcGeneration(state.rtc.generation, generation)) {
        failRtcSession(error.message);
      }
    }
  }, 1500);
  state.rtc.feedbackTimer = window.setInterval(async () => {
    const peer = state.rtc.peer;
    if (!peer || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    const fps = currentDisplayFps();
    const frameAgeMs = state.rtc.lastFrameAt > 0
      ? Math.max(0, performance.now() - state.rtc.lastFrameAt)
      : 0;
    const targetFps = Number(state.status?.rtc?.stats?.target_fps || 0);
    const sentFps = Number(state.status?.rtc?.stats?.sent_fps_x100 || 0) / 100;
    const videoCongestion = updateVideoCongestionFeedback(state.rtc.videoCongestionFeedback, {
      receivedFrames: state.rtc.receivedFrames,
      previousReceivedFrames: state.rtc.feedbackReceivedFrames,
      droppedFrames: state.rtc.droppedFrames,
      previousDroppedFrames: state.rtc.feedbackDroppedFrames,
      displayFps: fps,
      targetFps,
      sentFps,
      frameAgeMs,
    });
    state.rtc.videoCongestionFeedback = videoCongestion;
    state.rtc.feedbackReceivedFrames = state.rtc.receivedFrames;
    state.rtc.feedbackDroppedFrames = state.rtc.droppedFrames;
    let audio = { queueMs: 0, packetLossX100: 0, jitterUs: 0, concealedFrames: 0 };
    try {
      if (rtcModeHasAudio(state.rtc.mode)) audio = await collectRtcAudioStats(peer, generation);
    } catch (_) {}
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
    api(rtcEndpoint("feedback"), {
      method: "POST",
      body: JSON.stringify({
        display_fps_x100: Math.round(fps * 100),
        frame_age_p95_us: Math.round(frameAgeMs * 1000),
        rtt_us: state.rtc.rttUs,
        audio_queue_ms: audio.queueMs,
        audio_packet_loss_x100: audio.packetLossX100,
        audio_jitter_us: audio.jitterUs,
        audio_concealed_frames: audio.concealedFrames,
        congestion_level: rtcModeHasVideo(state.rtc.mode) ? deviceVideoCongestionLevel(videoCongestion) : 0,
      }),
    }).catch(() => {});
  }, 1000);
}

async function collectRtcAudioStats(peer, generation) {
  let sent = 0;
  let received = 0;
  let lost = 0;
  let jitterUs = 0;
  let queueMs = 0;
  let concealedFrames = 0;
  let audioLevel = 0;
  const reports = await peer.getStats();
  if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) {
    return { queueMs: 0, packetLossX100: 0, jitterUs: 0, concealedFrames: 0, audioLevel: 0 };
  }
  const mediaRttUs = selectMediaRoundTripUs(reports);
  if (mediaRttUs > 0) {
    state.rtc.mediaRttUs = mediaRttUs;
    state.rtc.rttUs = mediaRttUs;
  }
  reports.forEach((report) => {
    if (report.kind !== "audio" && report.mediaType !== "audio") return;
    if (report.type === "outbound-rtp") sent += report.packetsSent || 0;
    if (report.type === "inbound-rtp") {
      received += report.packetsReceived || 0;
      lost += Math.max(0, report.packetsLost || 0);
      jitterUs = Math.max(jitterUs, Math.round((report.jitter || 0) * 1_000_000));
      const latency = sampleAudioJitterBuffer(state.rtc.audioJitterCounter, report);
      state.rtc.audioJitterCounter = latency.counter;
      state.rtc.audioLatency = latency;
      if (latency.sampleValid) queueMs = Math.max(queueMs, latency.actualMs);
      concealedFrames += report.concealedSamples || 0;
      if (Number.isFinite(report.audioLevel)) audioLevel = Math.max(audioLevel, report.audioLevel);
      if (report.totalSamplesDuration > 0 && report.totalAudioEnergy >= 0) {
        audioLevel = Math.max(audioLevel, Math.sqrt(report.totalAudioEnergy / report.totalSamplesDuration));
      }
    }
  });
  elements.rtcAudioUpPackets.textContent = String(sent);
  elements.rtcAudioDownPackets.textContent = String(received);
  state.rtc.browserAudioSent = sent;
  state.rtc.browserAudioReceived = received;
  state.rtc.browserAudioLevel = audioLevel;
  updateRtcAudioHealth();
  return {
    queueMs,
    packetLossX100: Math.round((lost / Math.max(1, received + lost)) * 10_000),
    jitterUs,
    concealedFrames,
    audioLevel,
  };
}

async function pollRtcEvents(generation) {
  if (!state.rtc.peer || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
  try {
    const payload = await api(`${rtcEndpoint("events")}?after=${state.rtc.eventCursor}`);
    if (!state.rtc.peer || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    for (const event of payload.events || []) {
      state.rtc.eventCursor = Math.max(state.rtc.eventCursor, event.id || 0);
      await handleRtcEvent(event.message || {}, generation);
      if (!state.rtc.peer || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    }
  } catch (error) {
    if (state.rtc.peer && isCurrentRtcGeneration(state.rtc.generation, generation)) {
      await failRtcSession(error.message);
    }
    return;
  }
  state.rtc.pollTimer = window.setTimeout(() => pollRtcEvents(generation), 100);
}

async function handleRtcEvent(message, generation) {
  if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return;
  const peer = state.rtc.peer;
  const data = message.data || {};
  if (message.type === "sys.nack") {
    await failRtcSession(localizeError(
      data.error || data.reason || "Device rejected the RTC request",
      undefined,
      data.error === "busy" ? "rtc_resource_busy" : null,
      data.owner,
    ));
    return;
  }
  if (message.type === "evt.rtc.state") {
    setRtcSessionState(data.state || "connecting");
    if (data.state === "failed") await failRtcSession(localizeError(data.reason || "Device RTC session failed"));
    if (data.state === "stopped" && state.rtc.peer) cleanupRtcSession();
    return;
  }
  if (message.type === "evt.rtc.capabilities") {
    const video = data.video || {};
    if (video.width && video.height) elements.liveVideoResolution.textContent = `${video.width} × ${video.height}`;
    return;
  }
  if (message.type === "evt.rtc.clock.pong") {
    const browserReceiveUs = Math.round((performance.timeOrigin + performance.now()) * 1000);
    if (state.rtc.mediaRttUs <= 0) {
      state.rtc.rttUs = calculateRoundTripUs(data.browser_send_us, browserReceiveUs);
    }
    return;
  }
  if (message.type !== "evt.rtc.signal" || !peer) return;
  if (data.kind === "answer" && data.sdp) {
    if (!peer.remoteDescription) {
      await peer.setRemoteDescription({ type: "answer", sdp: data.sdp });
      if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
      for (const candidate of state.rtc.remoteCandidates.splice(0)) {
        await peer.addIceCandidate(candidate);
        if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
      }
    }
  } else if (data.kind === "candidate" && data.candidate) {
    const candidate = new RTCIceCandidate({
      candidate: data.candidate,
      sdpMid: data.sdp_mid,
      sdpMLineIndex: data.sdp_mline_index,
    });
    if (peer.remoteDescription) await peer.addIceCandidate(candidate);
    else state.rtc.remoteCandidates.push(candidate);
  }
}

async function stopRtcSession() {
  const mode = resolveRtcMode(
    state.rtc.mode,
    state.status?.rtc?.mode,
    state.status?.resource_owners?.media,
    state.status?.rtc?.active === true,
  );
  if (state.rtc.teardownInProgress || !mode) return;
  const hadAudio = rtcModeHasAudio(mode);
  const hadVideo = rtcModeHasVideo(mode);
  state.rtc.teardownInProgress = true;
  elements.stopLiveVideoButton.disabled = true;
  elements.stopRtcAudioButton.disabled = true;
  /* Browser media must never depend on the device stop acknowledgement. A
   * congested or restarting device can miss the REST deadline; keeping the
   * local peer alive in that case leaks the microphone and leaves the page in
   * a false "still calling" state. The backend remains the source of truth
   * for the device-side resource lock and can still expose a retry. */
  cleanupRtcSession();
  try {
    await api(rtcEndpoint("stop", mode), { method: "POST" });
    if (hadVideo) setResult(elements.liveVideoResult, "Live video stopped", "ok");
    if (hadAudio) setResult(elements.rtcAudioResult, "Full-duplex call ended", "ok");
    await refreshStatus();
  } catch (error) {
    notify(`${error.message}; local audio/video stopped`, "error");
    if (hadVideo) setResult(elements.liveVideoResult, "Local audio/video stopped, but device release confirmation timed out", "error");
    if (hadAudio) setResult(elements.rtcAudioResult, "Local audio/video stopped, but device release confirmation timed out", "error");
    await refreshStatus();
  } finally {
    state.rtc.teardownInProgress = false;
    if (state.status) renderStatus(state.status);
  }
}

async function failRtcSession(message) {
  if (state.rtc.teardownInProgress) return;
  const mode = state.rtc.mode;
  const hadSession = Boolean(state.rtc.peer || state.rtc.localStream || mode);
  const hadAudio = rtcModeHasAudio(mode);
  const hadVideo = rtcModeHasVideo(mode);
  const stopPath = rtcEndpoint("stop");
  state.rtc.teardownInProgress = true;
  cleanupRtcSession();
  if (hadAudio) setRtcAudioState("failed", message);
  if (hadVideo) setLiveVideoState("failed", message);
  notify(message, "error");
  try {
    if (mode) await api(stopPath, { method: "POST" });
  } catch (_) {
    // The local peer is already closed; status refresh remains the source of truth.
  } finally {
    if (hadSession || state.status) await refreshStatus();
    state.rtc.teardownInProgress = false;
  }
}

function cleanupRtcSession() {
  state.rtc.generation += 1;
  state.localResources.delete("media");
  window.clearTimeout(state.rtc.pollTimer);
  window.clearInterval(state.rtc.heartbeatTimer);
  window.clearInterval(state.rtc.feedbackTimer);
  state.rtc.pollTimer = null;
  state.rtc.heartbeatTimer = null;
  state.rtc.feedbackTimer = null;
  const channel = state.rtc.channel;
  const videoSocket = state.rtc.videoSocket;
  const peer = state.rtc.peer;
  const localStream = state.rtc.localStream;
  const diagnosticAudio = state.rtc.diagnosticAudio;
  const mode = state.rtc.mode;
  state.rtc.channel = null;
  state.rtc.videoSocket = null;
  state.rtc.peer = null;
  state.rtc.localStream = null;
  state.rtc.diagnosticAudio = null;
  state.rtc.remoteStream = null;
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.browserAudioLevel = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "idle";
  state.rtc.rttUs = 0;
  state.rtc.mediaRttUs = 0;
  state.rtc.audioJitterCounter = null;
  state.rtc.audioLatency = { sampleValid: false, actualMs: 0, targetMs: 0, minimumMs: 0 };
  elements.rtcAudioLatency.textContent = "Waiting for Stage Latency Samples";
  if (channel) {
    channel.onclose = null;
    try { channel.close(); } catch (_) {}
  }
  if (videoSocket) {
    try { videoSocket.close(); } catch (_) {}
  }
  if (peer) {
    peer.onconnectionstatechange = null;
    try { peer.close(); } catch (_) {}
  }
  if (localStream) {
    for (const track of localStream.getTracks()) track.stop();
  }
  if (diagnosticAudio) {
    try { diagnosticAudio.oscillator.stop(); } catch (_) {}
    diagnosticAudio.audioContext.close().catch(() => {});
  }
  elements.rtcRemoteAudio.pause();
  elements.rtcRemoteAudio.srcObject = null;
  elements.rtcAudioLocalState.textContent = "Available";
  elements.stopLiveVideoButton.disabled = true;
  elements.stopRtcAudioButton.disabled = true;
  elements.startLiveVideoButton.disabled = state.rtc.teardownInProgress
    || !state.status?.connected || !hasCapability("rtc.video.mjpeg.v1");
  elements.startRtcAudioButton.disabled = state.rtc.teardownInProgress
    || !state.status?.connected || !hasCapability("rtc.audio.full_duplex.v1");
  elements.startRtcAvButton.disabled = state.rtc.teardownInProgress
    || !state.status?.connected
    || !hasCapability("rtc.video.mjpeg.v1")
    || !hasCapability("rtc.audio.full_duplex.v1");
  if (rtcModeHasVideo(mode) && elements.liveVideoStage.dataset.state !== "idle") setLiveVideoState("idle");
  if (rtcModeHasAudio(mode) && elements.rtcAudioConsole.dataset.state !== "idle") setRtcAudioState("idle");
  state.rtc.mode = null;
}

async function enqueueMjpegPacket(value, generation) {
  let admission = { ownsDecoder: false, replacedPending: false };
  try {
    const packet = value instanceof ArrayBuffer ? value : await value.arrayBuffer();
    if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    const completePacket = acceptMjpegTransportPacket(state.rtc.mjpegChunkReassembler, packet);
    if (!completePacket) return;
    const frame = parseWjpgPacket(completePacket);
    state.rtc.receivedFrames += 1;
    if (state.rtc.lastSequence !== null) {
      const expected = (state.rtc.lastSequence + 1) >>> 0;
      const gap = (frame.sequence - expected) >>> 0;
      if (gap > 0 && gap < 10000) state.rtc.droppedFrames += gap;
    }
    state.rtc.lastSequence = frame.sequence;
    admission = admitVideoFrame(state.rtc, frame);
    if (admission.replacedPending) state.rtc.droppedFrames += 1;
    if (!admission.ownsDecoder) return;
    let current = frame;
    while (current) {
      if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return;
      try {
        await drawMjpegFrame(current, generation);
      } catch (_) {
        if (isCurrentRtcGeneration(state.rtc.generation, generation)) state.rtc.droppedFrames += 1;
      }
      current = takePendingVideoFrame(state.rtc);
    }
  } catch (_) {
    if (isCurrentRtcGeneration(state.rtc.generation, generation)) state.rtc.droppedFrames += 1;
  } finally {
    if (isCurrentRtcGeneration(state.rtc.generation, generation)) {
      finishVideoFrameDecode(state.rtc, admission.ownsDecoder);
      elements.liveVideoDrops.textContent = String(state.rtc.droppedFrames);
    }
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
  const jpeg = bytes.subarray(headerSize);
  if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8 || jpeg[jpeg.length - 2] !== 0xff || jpeg[jpeg.length - 1] !== 0xd9) {
    throw new Error("invalid JPEG payload");
  }
  return {
    sequence: view.getUint32(8, true),
    captureTimestampMs: view.getUint32(12, true),
    jpeg,
  };
}

async function drawMjpegFrame(frame, generation) {
  const bitmap = await createImageBitmap(new Blob([frame.jpeg], { type: "image/jpeg" }));
  try {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return;
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
    pending: "Streaming PCM sample…",
    complete: (payload) => `Playback complete · ${formatBytes(payload.bytes)}`,
    station: document.querySelector(".station-audio"),
    resources: ["microphone", "speaker"],
  });
}

async function capturePhoto() {
  const payload = await runAction({
    path: "/api/actions/capture-photo",
    result: elements.cameraResult,
    pending: "Requesting JPEG frame…",
    complete: (value) => `Photo received · ${formatBytes(value.bytes)}`,
    station: document.querySelector(".station-camera"),
    resources: ["camera", "animation"],
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
    pending: `Recording ${duration} s…`,
    complete: (value) => `${value.duration_seconds.toFixed(3)} s · drops ${value.dropped_frames} · decode failures ${value.decode_failures}`,
    station: document.querySelector(".station-microphone"),
    resources: ["microphone", "speaker"],
  });
  if (payload) await showRecording(payload.artifact_url);
  return payload;
}

async function runAll() {
  const allowed = window.confirm(i18n.translate(
    "The basic check moves the gimbal, lights the body, plays audio, captures a photo, and records the microphone. Make sure the robot has clear space. Continue?",
  ));
  if (!allowed) return;
  try {
    await applyMotion();
    await applyLight();
    await playAudio();
    await capturePhoto();
    await recordMicrophone();
    notify("Basic check passed: actuator and media paths completed", "ok");
  } catch (_) {
    notify("Basic check stopped at the first failed stage", "error");
  }
}

elements.playAudioButton.addEventListener("click", () => { playAudio().catch(() => {}); });
elements.panControl.addEventListener("input", updateMotionPreview);
elements.tiltControl.addEventListener("input", updateMotionPreview);
document.querySelectorAll("[data-motion-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    const preset = button.dataset.motionPreset;
    elements.panControl.value = preset === "left" ? "30" : preset === "right" ? "150" : "90";
    elements.tiltControl.value = "115";
    updateMotionPreview();
  });
});
elements.applyMotionButton.addEventListener("click", () => { applyMotion().catch(() => {}); });
elements.stopMotionButton.addEventListener("click", () => { stopMotion().catch(() => {}); });
elements.lightColor.addEventListener("input", updateLightPreview);
elements.lightBrightness.addEventListener("input", updateLightPreview);
elements.applyLightButton.addEventListener("click", () => { applyLight().catch(() => {}); });
elements.playLightEffectButton.addEventListener("click", () => { playLightEffect().catch(() => {}); });
elements.lightsOffButton.addEventListener("click", () => { lightsOff().catch(() => {}); });
elements.playAnimationButton.addEventListener("click", () => { playAnimation().catch(() => {}); });
elements.stopAnimationButton.addEventListener("click", () => { stopAnimation().catch(() => {}); });
elements.startRandomAnimationButton.addEventListener("click", startRandomAnimation);
elements.stopRandomAnimationButton.addEventListener("click", () => { stopRandomAnimation(); });
elements.animationId.addEventListener("input", scheduleAnimationPrefetch);
elements.animationId.addEventListener("focus", scheduleAnimationPrefetch);
elements.animationRandomInterval.addEventListener("change", () => {
  const intervalMs = clampAnimationIntervalMs(Number(elements.animationRandomInterval.value) * 1000);
  elements.animationRandomInterval.value = String(intervalMs / 1000);
});
elements.pairingForm.addEventListener("submit", (event) => {
  event.preventDefault();
  pairDevice();
});
elements.pairingCode.addEventListener("input", () => {
  elements.pairingCode.value = elements.pairingCode.value.replace(/[^0-9]/g, "").slice(0, 6);
});
elements.stopAudioButton.addEventListener("click", () => {
  runAction({
    path: "/api/actions/stop-audio",
    result: elements.audioResult,
    pending: "Stopping playback…",
    complete: () => "Playback stop requested",
    interrupt: true,
  }).catch(() => {});
});
elements.capturePhotoButton.addEventListener("click", () => { capturePhoto().catch(() => {}); });
elements.startLiveVideoButton.addEventListener("click", () => { startLiveVideo(); });
elements.stopLiveVideoButton.addEventListener("click", () => { stopRtcSession(); });
elements.startRtcAudioButton.addEventListener("click", () => { startRtcAudio(); });
elements.startRtcAvButton.addEventListener("click", () => { startRtcSession("av"); });
elements.stopRtcAudioButton.addEventListener("click", () => { stopRtcSession(); });
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
  stopRandomAnimation({ quiet: true });
  window.clearTimeout(state.animation.prefetchDebounceTimer);
  const mode = resolveRtcMode(
    state.rtc.mode,
    state.status?.rtc?.mode,
    state.status?.resource_owners?.media,
    state.status?.rtc?.active === true,
  );
  if (!mode && !state.rtc.peer && !state.rtc.localStream) return;
  navigator.sendBeacon(rtcEndpoint("stop", mode));
  cleanupRtcSession();
});
setInterval(refreshStatus, 1000);
refreshStatus({ quiet: false });
drawEmptyWaveform();
updateMotionPreview();
updateLightPreview();
