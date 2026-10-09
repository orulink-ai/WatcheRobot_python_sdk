import { evaluateRtcAudioHealth, formatRtcPlaybackLevel } from "./rtc-audio-health.mjs";
import { createRtcNoisePlayback, microphoneProcessingStatus } from "./rtc-noise-playback.mjs";
import { recordRtcAudioDiagnostic, selectDiagnosticDeviceStats } from "./rtc-audio-diagnostic.mjs";
import { detectionLabel, testBenchModels, createPreviewLifecycle } from "./model-preview.mjs";
import { createDisplayAudit } from "./display-audit.mjs";
import { createMjpegTransport } from "./mjpeg-transport.mjs";
let displayAudit = createDisplayAudit();
let displayAuditPublishedAt = 0;
let faceRequestPending = false;
let faceFramePending = false;
let facePreviewEpoch = 0;
let facePreviewActive = false;
let inferenceRequestPending = false;
let modelPreviewActive = false;
let modelPreviewEpoch = 0;
let modelPreviewPending = false;
let modelPreviewSequence = null;
let modelPreviewFrameAt = 0;
const modelPreviewLifecycle = createPreviewLifecycle();
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
  readAnimationResidency,
  selectLifecycleBaseline,
  selectLatestReleaseSnapshot,
  selectFeatureResourceSnapshots,
} from "./resource-health.mjs";
import {
  controlAvailability,
  isCurrentRtcGeneration,
  resolveRtcMode,
  rtcModeHasAudio,
  rtcTransportPlan,
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
import {
  appendSceneSample,
  audioDropCounters,
  combinedSceneAvailability,
  createSceneSample,
  createScenePageLifecycle,
  createCombinedSceneLifecycle,
  evaluateProceduralRender,
  sceneRecordingLabel,
  summarizeSceneSamples,
} from "./combined-scene.mjs";
const scenePageLifecycle = createScenePageLifecycle();

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
  scene: { samples: [], recordingPending: false, reportPending: false, audioResultActive: false, operationGeneration: 0 },
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
    requestId: null,
    mode: null,
    peer: null,
    channel: null,
    videoTransport: null,
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
    audioVerified: false,
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
  return params.get("rtc_audio_processing") !== "0";
}

async function createRtcDiagnosticAudioStream(generation) {
  const { createRtcTestSpeech } = await import("/assets/rtc-test-speech.mjs");
  const diagnostic = await createRtcTestSpeech();
  if (state.rtc.generation !== generation) {
    await diagnostic.dispose();
    throw new Error("RTC speech diagnostic was cancelled");
  }
  state.rtc.diagnosticAudio = diagnostic;
  return diagnostic.stream;
}

const elements = {
  sceneCapability: document.querySelector("#sceneCapability"),
  startProceduralButton: document.querySelector("#startProceduralButton"),
  stopProceduralButton: document.querySelector("#stopProceduralButton"),
  startSceneAudioButton: document.querySelector("#startSceneAudioButton"),
  stopSceneAudioButton: document.querySelector("#stopSceneAudioButton"),
  captureScenePhotoButton: document.querySelector("#captureScenePhotoButton"),
  sceneResult: document.querySelector("#sceneResult"),
  sceneAnimationState: document.querySelector("#sceneAnimationState"),
  sceneAnimationFps: document.querySelector("#sceneAnimationFps"),
  sceneMouth: document.querySelector("#sceneMouth"),
  sceneAudioState: document.querySelector("#sceneAudioState"),
  sceneAudioDrops: document.querySelector("#sceneAudioDrops"),
  scenePhotoPreview: document.querySelector("#scenePhotoPreview"),
  scenePhotoEmpty: document.querySelector("#scenePhotoEmpty"),
  scenePhotoCaption: document.querySelector("#scenePhotoCaption"),
  sceneRecordingLabel: document.querySelector("#sceneRecordingLabel"),
  startSceneRecordingButton: document.querySelector("#startSceneRecordingButton"),
  stopSceneRecordingButton: document.querySelector("#stopSceneRecordingButton"),
  exportSceneReportButton: document.querySelector("#exportSceneReportButton"),
  sceneRecordingState: document.querySelector("#sceneRecordingState"),
  sceneMemoryRows: document.querySelector("#sceneMemoryRows"),
  sceneMemoryCaption: document.querySelector("#sceneMemoryCaption"),
  sceneResourceChart: document.querySelector("#sceneResourceChart"),
  sceneSampleSummary: document.querySelector("#sceneSampleSummary"),
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
  capturePhotoWithFeedbackButton: document.querySelector("#capturePhotoWithFeedbackButton"),
  queryVisionButton: document.querySelector("#queryVisionButton"),
  startFaceTrackingButton: document.querySelector("#startFaceTrackingButton"),
  startFacePreviewButton: document.querySelector("#startFacePreviewButton"),
  facePreviewCanvas: document.querySelector("#facePreviewCanvas"),
  facePreviewHint: document.querySelector("#facePreviewHint"),
  facePreviewMetrics: document.querySelector("#facePreviewMetrics"),
  stopFaceTrackingButton: document.querySelector("#stopFaceTrackingButton"),
  queryModelsButton: document.querySelector("#queryModelsButton"),
  startInferencePreviewButton: document.querySelector("#startInferencePreviewButton"),
  inferenceCanvas: document.querySelector("#inferenceCanvas"),
  inferenceMetrics: document.querySelector("#inferenceMetrics"),
  inferenceModel: document.querySelector("#inferenceModel"),
  startInferenceButton: document.querySelector("#startInferenceButton"),
  queryInferenceButton: document.querySelector("#queryInferenceButton"),
  stopInferenceButton: document.querySelector("#stopInferenceButton"),
  inferenceState: document.querySelector("#inferenceState"),
  inferenceResult: document.querySelector("#inferenceResult"),
  faceTrackingCapability: document.querySelector("#faceTrackingCapability"),
  faceTrackingResult: document.querySelector("#faceTrackingResult"),
  faceVisionStatus: document.querySelector("#faceVisionStatus"),
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
  rtcMicrophoneProcessing: document.querySelector("#rtcMicrophoneProcessing"),
  rtcNoiseEnabled: document.querySelector("#rtcNoiseEnabled"),
  rtcNoiseStrength: document.querySelector("#rtcNoiseStrength"),
  rtcNoiseStrengthValue: document.querySelector("#rtcNoiseStrengthValue"),
  rtcNoiseState: document.querySelector("#rtcNoiseState"),
  recordRtcDiagnostic: document.querySelector("#recordRtcDiagnostic"),
  rtcDiagnosticState: document.querySelector("#rtcDiagnosticState"),
  rtcAudioUpPackets: document.querySelector("#rtcAudioUpPackets"),
  rtcAudioDownPackets: document.querySelector("#rtcAudioDownPackets"),
  rtcAudioDeviceCapture: document.querySelector("#rtcAudioDeviceCapture"),
  rtcAudioDeviceTx: document.querySelector("#rtcAudioDeviceTx"),
  rtcAudioDeviceDrops: document.querySelector("#rtcAudioDeviceDrops"),
  rtcAudioSignal: document.querySelector("#rtcAudioSignal"),
  rtcAudioAec: document.querySelector("#rtcAudioAec"),
  rtcAudioPlaybackLevel: document.querySelector("#rtcAudioPlaybackLevel"),
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
  resourceTransitions: document.querySelector("#resourceTransitions"),
  resourceDelta: document.querySelector("#resourceDelta"),
  resourceAnimationResidency: document.querySelector("#resourceAnimationResidency"),
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

const sceneRtcRequestPrefix = crypto.randomUUID();
const sceneRtcRequestId = owner => owner == null ? null : `${sceneRtcRequestPrefix}:${owner}`;
const combinedScene = createCombinedSceneLifecycle({
  isProceduralRunning: () => state.status?.procedural?.state === "running",
  startProcedural: () => proceduralAction("start"),
  stopProcedural: () => proceduralAction("stop"),
  startAudio: async (owner) => {
    state.scene.audioResultActive = true;
    const started = await startRtcAudio(sceneRtcRequestId(owner));
    if (!started) throw new Error(translateText(elements.rtcAudioResult.textContent) || "Full-duplex call did not start");
    return true;
  },
  stopAudio: async (owner) => {
    const retainedSceneRequest = state.rtc.requestId?.startsWith(`${sceneRtcRequestPrefix}:`) ? state.rtc.requestId : null;
    const requestId = sceneRtcRequestId(owner) || retainedSceneRequest;
    if (requestId && await stopRtcSession(requestId) === false) throw new Error("RTC stop is unconfirmed; retry End Scene");
  },
  onChange: () => { if (state.status) updateCombinedScene(state.status); },
});

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
  procedural: "Live Procedural Expression",
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
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return "—";
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "—";
  if (Math.abs(bytes) < 1024) return `${bytes} B`;
  if (Math.abs(bytes) < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}

function formatSignedBytes(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return "—";
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "—";
  return `${bytes > 0 ? "+" : ""}${formatBytes(bytes)}`;
}

function updateResourceMonitor(status) {
  const telemetry = status.resources?.telemetry;
  const fresh = status.connected && (!telemetry || telemetry.status === "available");
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
  elements.resourcePanel.dataset.state = fresh ? health.state : "waiting";
  elements.resourceState.textContent = fresh ? stateLabels[health.state] || health.state
    : telemetry?.status === "stale" ? "Device Telemetry Stale" : "Waiting for Device Snapshot";
  elements.resourceStage.textContent = current
    ? `${fresh ? "Live" : "Last Device Snapshot"} · ${stageLabels[current.stage] || current.stage || "Unknown Stage"} · #${current.sequence || 0}${telemetry?.age_seconds !== null && telemetry?.age_seconds !== undefined ? ` · age ${Number(telemetry.age_seconds).toFixed(1)} s` : ""}`
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
    tts_runtime: "TTS worker resident",
    sfx_playback: "Local sound",
    sfx_runtime: "SFX worker resident",
    codec_resident: "Codec / DMA resident",
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
  elements.resourceTransitions.replaceChildren(...selectFeatureResourceSnapshots(status.resources?.history).map(row => {
    const tr = document.createElement("tr");
    const values = [row.stage,
      formatBytes(row.memory?.internal?.free_bytes),
      formatBytes(row.memory?.internal?.largest_free_block_bytes),
      formatBytes(row.memory?.dma?.largest_free_block_bytes),
      formatBytes(row.resources?.tts_stack_bytes),
      formatBytes(row.resources?.sfx_stack_bytes)];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    return tr;
  }));
  elements.resourceDelta.textContent = lifecycleBaseline
    ? `Against ${hasRtcBaseline ? "RTC pre-start" : "Connection Baseline"}: internal ${formatSignedBytes(health.deltas.internalFreeBytes)} / ${formatSignedBytes(health.deltas.internalLargestBytes)} · DMA ${formatSignedBytes(health.deltas.dmaLargestBytes)} · PSRAM ${formatSignedBytes(health.deltas.psramLargestBytes)}${health.trend?.monotonicDecline ? " · declined after 4 consecutive releases" : ""}`
    : "Waiting for Resource Baseline";

  const residency = readAnimationResidency(current, fresh);
  const residencyStates = { unknown: "Allocation telemetry unavailable", released: "Allocations released",
    resident: "Allocations resident", failed: "Allocation release failed" };
  elements.resourceAnimationResidency.textContent = `${residencyStates[residency.state]} · SD frames ${formatBytes(residency.sdFramePoolBytes)} · SD payload ${formatBytes(residency.sdPayloadCacheBytes)} · SD task ${formatBytes(residency.sdWorkerStackBytes)} · LCD DMA ${formatBytes(residency.sdDirectLcdDmaBytes)} · Procedural canvas ${formatBytes(residency.proceduralFrameBytes)}`;

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

function sceneBytes(value) {
  return value === null || value === undefined ? "—" : formatBytes(value);
}

function updateCombinedScene(status) {
  const controls = combinedSceneAvailability(status, {
    localResources: state.localResources, rtcMode: state.rtc.mode,
    teardownInProgress: state.rtc.teardownInProgress,
  });
  const scene = combinedScene.snapshot();
  elements.sceneCapability.textContent = !status.connected ? "Device Offline"
    : controls.proceduralSupported ? "Speaker-driven animation available" : "Audio-follow Firmware Required";
  elements.sceneCapability.dataset.state = status.connected && controls.proceduralSupported ? "ready" : "waiting";
  elements.startProceduralButton.disabled = !controls.startProcedural || scene.busy;
  elements.stopProceduralButton.disabled = !controls.stopProcedural || scene.busy;
  elements.startSceneAudioButton.disabled = scene.busy || scene.active || !controls.startAudio
    || !controls.proceduralSupported || (status.procedural?.state !== "running" && !controls.startProcedural);
  elements.stopSceneAudioButton.disabled = scene.stopping
    || !(controls.stopAudio || scene.starting || scene.active || scene.ownsProcedural);
  elements.captureScenePhotoButton.disabled = !controls.snapshot;
  const proceduralLabels = {
    idle: "Animation Idle", starting: "Starting Animation", running: "Live Procedural Expression Running",
    stopping: "Stopping Animation", stop_required: "Animation stop unconfirmed; retry stop",
    warming: "Waiting for Rendered Frames", waiting_data: "Waiting for Device Snapshot",
    failed: "Procedural Display Updates Failed", degraded: "Animation Running with Update Errors",
  };
  const render = evaluateProceduralRender(status);
  elements.sceneAnimationState.textContent = !status.connected ? "Device Offline"
    : !controls.proceduralSupported ? "Not Advertised"
      : `${proceduralLabels[render.state] || "Waiting for Telemetry"}${render.updateErrors > 0 ? ` · ${render.updateErrors} errors` : ""}`;
  elements.sceneAnimationState.dataset.tone = ["failed", "degraded"].includes(render.state) ? "error" : "normal";
  const lastDevice = state.scene.samples.at(-1)?.deviceId;
  if (lastDevice && status.device?.device_id && lastDevice !== status.device.device_id) state.scene.samples = [];
  const sample = createSceneSample(status, { rtcMode: state.rtc.mode });
  state.scene.samples = appendSceneSample(state.scene.samples, sample);
  elements.sceneAnimationFps.textContent = sample?.animationFps === null || sample?.animationFps === undefined
    ? render.state === "idle" ? "Animation Idle" : "Waiting for Frame Samples"
    : `${sample.animationFps.toFixed(1)} / ${sample.animationTargetFps?.toFixed(1) ?? "—"} FPS`;
  elements.sceneMouth.textContent = sample?.mouthLevelMilli === null || sample?.mouthLevelMilli === undefined
    ? "Waiting for Speaker Telemetry"
    : `${(sample.mouthLevelMilli / 10).toFixed(1)}% · ${sample.pcmFrames ?? "—"} PCM frames`;
  elements.sceneAudioDrops.textContent = `Uplink drops ${sample?.audioTxDroppedFrames ?? "—"} / Playback queue drops ${sample?.audioQueueDroppedFrames ?? "—"}`;
  elements.sceneAudioDrops.dataset.tone = sample?.audioTxDroppedFrames > 0 || sample?.audioQueueDroppedFrames > 0 ? "error" : "normal";
  const audioMode = state.rtc.mode || (status.rtc?.active ? status.rtc.mode : null);
  elements.sceneAudioState.textContent = !status.connected ? "Device Offline" : rtcModeHasAudio(audioMode)
    ? state.rtc.mode
      ? `${String(state.rtc.audioHealthState || "connecting").toUpperCase()} · ↑ ${state.rtc.browserAudioSent} / ↓ ${state.rtc.browserAudioReceived}`
      : "Device Call Active · browser not connected"
    : "IDLE";
  if (state.scene.audioResultActive) {
    setResult(elements.sceneResult, translateText(elements.rtcAudioResult.textContent), elements.rtcAudioResult.dataset.tone || "running");
  }
  const currentMemory = sample || state.scene.samples.at(-1);
  elements.sceneMemoryCaption.textContent = sample ? "Live Device Memory" : "Last Device Snapshot";
  elements.sceneMemoryRows.replaceChildren(...["internal", "dma", "psram"].map(heap => {
    const row = document.createElement("tr");
    for (const value of [heap === "internal" ? "Internal RAM" : heap.toUpperCase(),
      sceneBytes(currentMemory?.[heap]?.free), sceneBytes(currentMemory?.[heap]?.minimum),
      sceneBytes(currentMemory?.[heap]?.largest)]) {
      const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
    }
    return row;
  }));
  const summary = summarizeSceneSamples(state.scene.samples);
  elements.sceneSampleSummary.textContent = summary.sampleCount
    ? `${summary.sampleCount} samples · ${(summary.durationMs / 1000).toFixed(0)} s · observed lows: internal ${sceneBytes(summary.internalFreeLow)} / DMA block ${sceneBytes(summary.dmaLargestLow)} / PSRAM ${sceneBytes(summary.psramFreeLow)}`
    : "Waiting for Device Snapshot";
  drawSceneResourceChart(state.scene.samples);
  updateSceneRecordingControls(status);
}

function drawSceneResourceChart(samples) {
  const canvas = elements.sceneResourceChart;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const traces = [
    { label: "Internal Free", color: "#d9ff57", value: sample => sample.internal.free },
    { label: "DMA Largest Block", color: "#62e7d7", value: sample => sample.dma.largest },
    { label: "PSRAM Free", color: "#ffb650", value: sample => sample.psram.free },
  ];
  const rowHeight = canvas.height / traces.length;
  traces.forEach((trace, index) => {
    const top = index * rowHeight;
    const values = samples.map(trace.value).filter(value => value !== null);
    const low = values.length ? Math.min(...values) : 0;
    const high = values.length ? Math.max(...values) : 0;
    const span = Math.max(high - low, 1024);
    const margin = span * 0.15;
    ctx.fillStyle = trace.color; ctx.font = "16px sans-serif";
    ctx.fillText(i18n.translate(trace.label), 12, top + 22);
    ctx.fillStyle = "#929b94"; ctx.font = "14px monospace";
    ctx.fillText(values.length ? `${formatBytes(low)} — ${formatBytes(high)}` : "—", 12, top + 47);
    ctx.strokeStyle = "#303a35"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(230, top + rowHeight - 8); ctx.lineTo(canvas.width - 12, top + rowHeight - 8); ctx.stroke();
    ctx.strokeStyle = trace.color; ctx.lineWidth = 2;
    ctx.beginPath(); let connected = false;
    samples.forEach((sample, sampleIndex) => {
      const value = trace.value(sample);
      if (value === null) { connected = false; return; }
      const x = 240 + (canvas.width - 256) * sampleIndex / Math.max(samples.length - 1, 1);
      const y = top + rowHeight - 12 - (rowHeight - 28) * (value - low + margin) / (span + margin * 2);
      if (connected) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      connected = true;
    });
    ctx.stroke();
  });
}

function updateSceneRecordingControls(status = state.status) {
  const recording = status?.scenario_recording || {};
  const active = recording.active === true;
  elements.startSceneRecordingButton.disabled = !status?.connected || active || state.scene.recordingPending;
  elements.stopSceneRecordingButton.disabled = !active || state.scene.recordingPending;
  elements.sceneRecordingLabel.disabled = active || state.scene.recordingPending;
  elements.exportSceneReportButton.disabled = !(Number(recording.sample_count) > 0) || state.scene.reportPending;
  elements.sceneRecordingState.textContent = recording.started_at
    ? `${active ? "Measurement Running" : "Measurement Stopped"} · ${recording.sample_count ?? 0} samples${recording.label ? ` · ${recording.label}` : ""}${recording.dropped_samples > 0 ? ` · ${recording.dropped_samples} older samples trimmed` : ""}`
    : "No measurement recorded";
}

async function proceduralAction(action) {
  state.scene.audioResultActive = false;
  stopRandomAnimation({ quiet: true });
  const request = () => runAction({
    path: `/api/controls/procedural/${action}`,
    result: elements.sceneResult, resource: "animation",
    pending: action === "start" ? "Starting live procedural expression…" : "Stopping live procedural expression…",
    complete: () => action === "start" ? "Procedural animation requested; speak during the call to test the mouth"
      : "Procedural animation stopped",
    interrupt: action === "stop",
  });
  if (action === "start") {
    return scenePageLifecycle.startProcedural(request, () => fetch("/api/controls/procedural/stop", {
      method: "POST", keepalive: true,
    }));
  }
  const result = await request();
  if (result?.state !== "idle") throw new Error("Procedural stop is unconfirmed");
  combinedScene.proceduralStopped();
  return result;
}

async function startCombinedScene() {
  if (elements.startSceneAudioButton.disabled) return;
  const operation = ++state.scene.operationGeneration;
  state.scene.audioResultActive = false;
  setResult(elements.sceneResult, "Starting the live expression and call…", "running");
  try {
    const started = await combinedScene.start();
    if (operation !== state.scene.operationGeneration) return;
    if (!started) {
      state.scene.audioResultActive = false;
      setResult(elements.sceneResult, "Scene start cancelled", "running");
    }
  } catch (error) {
    if (operation !== state.scene.operationGeneration) return;
    state.scene.audioResultActive = false;
    setResult(elements.sceneResult, error.message, "error");
    notify(error.message, "error");
  }
}

async function endCombinedScene() {
  if (elements.stopSceneAudioButton.disabled) return;
  const operation = ++state.scene.operationGeneration;
  state.scene.audioResultActive = false;
  setResult(elements.sceneResult, "Ending the combined scene…", "running");
  try {
    await combinedScene.end();
    await refreshStatus();
    if (operation !== state.scene.operationGeneration) return;
    setResult(elements.sceneResult, "Scene ended; independently enabled animation is preserved", "ok");
  } catch (error) {
    if (operation !== state.scene.operationGeneration) return;
    setResult(elements.sceneResult, error.message, "error");
    notify(error.message, "error");
  }
}

async function settleCombinedAudioStop() {
  try { await combinedScene.audioStopped(); }
  catch (error) {
    state.scene.audioResultActive = false;
    setResult(elements.sceneResult, error.message, "error");
    notify(error.message, "error");
  }
}

async function sceneRecordingAction(action) {
  if (state.scene.recordingPending) return;
  state.scene.recordingPending = true; updateSceneRecordingControls();
  try {
    await api(`/api/scenario/recording/${action}`, {
      method: "POST",
      body: action === "start" ? JSON.stringify({ label: sceneRecordingLabel(elements.sceneRecordingLabel.value) }) : undefined,
    });
    if (action === "start") state.scene.samples = [];
    await refreshStatus();
  } catch (error) {
    setResult(elements.sceneResult, error.message, "error"); notify(error.message, "error");
  } finally {
    state.scene.recordingPending = false; updateSceneRecordingControls();
  }
}

async function exportSceneReport() {
  if (state.scene.reportPending) return;
  state.scene.reportPending = true; updateSceneRecordingControls();
  let objectUrl = null;
  try {
    const report = await api("/api/scenario/report");
    objectUrl = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = objectUrl;
    link.download = `watche-scene-${new Date().toISOString().replaceAll(":", "-")}.json`;
    document.body.append(link); link.click(); link.remove();
  } catch (error) {
    setResult(elements.sceneResult, error.message, "error"); notify(error.message, "error");
  } finally {
    if (objectUrl) window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    state.scene.reportPending = false; updateSceneRecordingControls();
  }
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
  updateCombinedScene(status);
  updateAnimationConfirmation(status);
  const owners = status.resource_owners || {};
  const localActions = [...state.localResources];
  const activeLabels = [...new Set(Object.values(owners))].map(actionLabel);
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
  elements.capturePhotoWithFeedbackButton.disabled = !availability.cameraWithFeedback
    || !hasCapability("camera.capture.feedback.v1");
  const inferenceState = status.inference?.state || "idle";
  const inferenceSupported = status.connected && hasCapability("vision.inference.v1");
  elements.queryModelsButton.disabled = inferenceRequestPending || !status.connected || !hasCapability("vision.models.v1");
  elements.startInferenceButton.disabled = inferenceRequestPending || !inferenceSupported || !availability.camera
    || inferenceState !== "idle" || !elements.inferenceModel.value;
  elements.startInferencePreviewButton.disabled = elements.startInferenceButton.disabled || !hasCapability("vision.inference.preview.v1");
  elements.inferenceModel.disabled = inferenceState !== "idle";
  if (inferenceState === "idle" && modelPreviewActive) {
    modelPreviewActive = false;
    modelPreviewEpoch = modelPreviewLifecycle.stop();
    elements.inferenceCanvas.hidden = true;
  }
  elements.queryInferenceButton.disabled = inferenceRequestPending || !inferenceSupported || inferenceState !== "running";
  elements.stopInferenceButton.disabled = inferenceRequestPending || !status.connected || inferenceState === "idle";
  const faceState = status.face_tracking?.state || "idle";
  const faceSupported = hasCapability("face_tracking.control.v1");
  elements.faceTrackingCapability.textContent = !status.connected ? "Device Offline"
    : !faceSupported ? "Current firmware does not support face tracking"
      : faceState === "running" ? (status.face_tracking?.preview_running && !status.face_tracking?.preview_receiving
        ? "Preview requested; no recent image received" : "Face tracking is running")
        : faceState === "stop_required" ? "Tracking stop is unconfirmed; retry stop"
          : "Face tracking is available";
  elements.queryVisionButton.disabled = faceRequestPending || !status.connected || !hasCapability("vision.status.v1");
  elements.startFaceTrackingButton.disabled = faceRequestPending || !status.connected || !faceSupported
    || faceState !== "idle" || !availability.camera || !availability.motion;
  elements.stopFaceTrackingButton.disabled = faceRequestPending || !status.connected || faceState === "idle";
  const previewSupported = hasCapability("face_tracking.preview.v1");
  elements.startFacePreviewButton.disabled = elements.startFaceTrackingButton.disabled || !previewSupported;
  elements.facePreviewHint.textContent = !status.connected ? "Device Offline" : previewSupported
    ? "Preview includes the matching face boxes. Stop tracking before changing modes."
    : "This firmware supports tracking without preview. Update firmware to enable face preview.";
  const previewActive = !!status.connected && !!status.face_tracking?.preview_running;
  if (previewActive !== facePreviewActive) {
    facePreviewActive = previewActive;
    facePreviewEpoch++;
    elements.facePreviewCanvas.hidden = !previewActive;
    elements.facePreviewCanvas.getContext("2d").clearRect(0, 0, 640, 480);
    elements.facePreviewMetrics.textContent = previewActive ? "Waiting for a preview frame" : "";
  }
  if (previewActive && !status.face_tracking?.preview_receiving) {
    elements.facePreviewMetrics.textContent = "Preview requested; no recent image received";
    elements.facePreviewCanvas.getContext("2d").clearRect(0, 0, elements.facePreviewCanvas.width, elements.facePreviewCanvas.height);
  }
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

  const testPending = document.querySelector("#concurrencyStation").dataset.requesting === "true";
  document.querySelector("#concurrencyStart").disabled = testPending || status.busy || state.localResources.size > 0 || !status.connected || ![
    "expression.runtime.v3", "audio.stream", "camera.capture",
  ].every(hasCapability);
  document.querySelector("#concurrencyStop").disabled = !testPending && status.active_action !== "concurrency_test";
  const cleanupEntries = Object.entries(status.concurrency_cleanup || {});
  const cleanupBusy = document.querySelector("#concurrencyStation").dataset.cleaning === "true";
  document.querySelector("#concurrencyCleanup").disabled = cleanupBusy || testPending || !status.connected || cleanupEntries.length === 0;
  document.querySelector("#concurrencyCleanupState").textContent = cleanupEntries.length
    ? `Cleanup pending: ${cleanupEntries.map(([name, error]) => `${name}: ${error}`).join("; ")}`
    : "No pending cleanup";

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
    const report = await api("/api/concurrency/result").catch(() => null);
    const details = document.querySelector("#concurrencyDetails");
    if (report?.results && details.dataset.pendingRunId !== report.run_id
      && (report.running || details.dataset.running === "true" || details.dataset.runId !== report.run_id)) {
      renderConcurrencyResults(report);
      setResult(document.querySelector("#concurrencyResult"), concurrencySummary(report), report.running ? "running" : report.passed ? "ok" : "error");
    }
  } catch (error) {
    if (state.status) renderStatus({ ...state.status, connected: false,
      resources: { ...state.status.resources, telemetry: { status: "unavailable", age_seconds: null } },
    });
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
  elements.scenePhotoPreview.src = url;
  elements.scenePhotoPreview.hidden = false;
  elements.scenePhotoEmpty.hidden = true;
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
  displayAudit = createDisplayAudit();
  displayAuditPublishedAt = 0;
  elements.liveVideoCanvas.dataset.displayAudit = JSON.stringify(displayAudit.snapshot(performance.now()));
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
  const deviceDrops = audioDropCounters(deviceStats);
  elements.rtcAudioDeviceDrops.textContent = `Uplink drops ${deviceDrops.uplinkFrames ?? "—"} / Playback queue drops ${deviceDrops.playbackFrames ?? "—"}`;
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
  elements.rtcAudioPlaybackLevel.textContent = formatRtcPlaybackLevel(deviceStats);
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
    deviceTxDroppedFrames: deviceDrops.uplinkFrames,
    deviceQueueDroppedFrames: deviceDrops.playbackFrames,
    deviceI2sBytes,
    devicePlaybackPeak,
    elapsedMs: state.rtc.audioConnectedAt ? performance.now() - state.rtc.audioConnectedAt : 0,
    previouslyVerified: state.rtc.audioVerified,
  });
  if (health.state === state.rtc.audioHealthState && !["failed", "degraded"].includes(health.state)) return;
  state.rtc.audioHealthState = health.state;
  if (health.state === "healthy") {
    state.rtc.audioVerified = true;
    setRtcAudioState("connected");
    setResult(elements.rtcAudioResult, "Full-duplex path verified: the browser is playing a non-silent Watcher audio track", "ok");
  } else if (health.state === "quiet") {
    setRtcAudioState("connected");
    setResult(elements.rtcAudioResult, "Call connected · waiting for speech", "running");
  } else if (health.state === "degraded") {
    if (health.missing.length === 0) state.rtc.audioVerified = true;
    setRtcAudioState("connected");
    setResult(
      elements.rtcAudioResult,
      `Connected with Audio Loss · Uplink drops ${deviceDrops.uplinkFrames ?? "—"} / Playback queue drops ${deviceDrops.playbackFrames ?? "—"} · send/render errors ${txErrors}/${deviceRenderErrors}`,
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

async function startRtcSession(mode, requestId = crypto.randomUUID()) {
  const wantsAudio = rtcModeHasAudio(mode);
  const wantsVideo = rtcModeHasVideo(mode);
  if (
    !["audio", "video", "av"].includes(mode)
    || state.rtc.mode
    || state.rtc.peer
    || state.rtc.requestId
    || state.rtc.teardownInProgress
    || state.localResources.has("media")
    || state.status?.resource_owners?.media
    || (wantsAudio && !hasCapability("rtc.audio.full_duplex.v1"))
    || (wantsVideo && !hasCapability("rtc.video.mjpeg.v1"))
  ) return false;
  const generation = state.rtc.generation + 1;
  state.rtc.generation = generation;
  state.rtc.requestId = requestId;
  state.rtc.mode = mode;
  state.localResources.add("media");
  if (state.status) renderStatus(state.status);
  resetLiveVideoMetrics();
  elements.rtcAudioUpPackets.textContent = "0";
  elements.rtcAudioDownPackets.textContent = "0";
  elements.rtcAudioDeviceCapture.textContent = "0";
  elements.rtcAudioDeviceTx.textContent = "0";
  elements.rtcAudioDeviceDrops.textContent = "—";
  elements.rtcAudioSignal.textContent = "0 / 0.000";
  elements.rtcAudioPlaybackLevel.textContent = "—";
  elements.rtcAudioAec.textContent = "Waiting for Device Telemetry";
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.browserAudioLevel = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "starting";
  state.rtc.audioVerified = false;
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
        ? await createRtcDiagnosticAudioStream(generation)
        : await navigator.mediaDevices.getUserMedia({
            audio: createRtcMicrophoneConstraints({
              browserProcessing: rtcBrowserAudioProcessingEnabled(),
            }),
            video: false,
          });
      if (state.rtc.generation !== generation || state.rtc.mode !== mode) {
        for (const track of localStream.getTracks()) track.stop();
        return false;
      }
      state.rtc.localStream = localStream;
      const micSettings = localStream.getAudioTracks()[0]?.getSettings?.() || {};
      const processing = microphoneProcessingStatus(micSettings);
      elements.rtcMicrophoneProcessing.textContent = `Echo cancellation ${processing.echoCancellation} · Noise suppression ${processing.noiseSuppression} · Microphone gain ${processing.autoGainControl}`;
      elements.rtcMicrophoneProcessing.dataset.settings = JSON.stringify(processing);
      elements.rtcAudioLocalState.textContent = micSettings.autoGainControl === true
        ? "Capturing · microphone gain active"
        : micSettings.autoGainControl === false
          ? "Capturing · microphone gain inactive"
          : "Capturing · microphone gain unreported";
    }

    const startPath = mode === "video" ? "/api/video/session/start" : "/api/rtc/session/start";
    if (mode === "video") {
      await api("/api/video/session/start", {
        method: "POST",
        body: JSON.stringify({ mode, request_id: requestId }),
      });
    } else {
      await api("/api/rtc/session/start", {
        method: "POST",
        body: JSON.stringify({ mode, request_id: requestId }),
      });
    }
    if (state.rtc.generation !== generation || state.rtc.mode !== mode) {
      // Match the old HTTP attempt in the Application, including when a
      // diagnostic client has started a later session outside this page.
      const holdTeardown = !state.rtc.mode && !state.rtc.peer && !state.rtc.localStream && !state.rtc.teardownInProgress;
      if (holdTeardown) {
        state.rtc.teardownInProgress = true;
        if (state.status) renderStatus(state.status);
      }
      try {
        await api(`${startPath.slice(0, -5)}stop`, { method: "POST", body: JSON.stringify({ request_id: requestId }) });
        if (state.rtc.requestId === requestId) state.rtc.requestId = null;
      } catch (_) {} finally {
        if (holdTeardown) {
          state.rtc.teardownInProgress = false;
          if (state.status) renderStatus(state.status);
        }
      }
      return false;
    }
    const transport = rtcTransportPlan(mode);
    if (!transport.peer) {
      createMjpegVideoTransport(null, generation);
      startRtcControlLoops(generation);
      await refreshStatus();
      return true;
    }
    const peer = new RTCPeerConnection({ iceServers: [] });
    state.rtc.peer = peer;
    if (wantsAudio && localStream) {
      for (const track of localStream.getAudioTracks()) peer.addTrack(track, localStream);
      peer.addEventListener("track", (event) => {
        if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return;
        if (event.track.kind !== "audio") return;
        const remoteStream = event.streams[0] || new MediaStream([event.track]);
        state.rtc.remoteStream = remoteStream;
        elements.rtcRemoteAudio.srcObject = remoteStream;
        configureLowLatencyAudioReceivers(peer);
        elements.rtcRemoteAudio.play().catch(() => {
          setResult(elements.rtcAudioResult, "Downlink audio arrived. Click the player to enable sound", "running");
        });
        attachRtcNoisePlayback(remoteStream, generation, peer).catch(() => {});
      });
    }
    if (wantsVideo) createMjpegVideoTransport(peer, generation);
    bindRtcPeerEvents(peer, generation);
    startRtcControlLoops(generation);
    const offer = await peer.createOffer();
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return false;
    await peer.setLocalDescription(offer);
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return false;
    await api(rtcEndpoint("signal"), {
      method: "POST",
      body: JSON.stringify({ kind: "offer", sdp: offer.sdp }),
    });
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || state.rtc.peer !== peer) return false;
    if (wantsAudio) setRtcAudioState("signaling", "Computer microphone is active; waiting for Watcher…");
    if (wantsVideo) setLiveVideoState("signaling", wantsAudio
      ? "Audio/video offer sent; waiting for Watcher…"
      : "Browser offer sent; waiting for Watcher…");
    await refreshStatus();
    return isCurrentRtcGeneration(state.rtc.generation, generation) && state.rtc.peer === peer;
  } catch (error) {
    if (!isCurrentRtcGeneration(state.rtc.generation, generation)) return false;
    const message = error?.name === "NotAllowedError"
      ? "Computer microphone permission was denied. Allow access and try again"
      : error?.name === "NotFoundError"
        ? "No computer microphone is available"
        : error.message;
    await failRtcSession(message);
    return false;
  }
}

function rtcNoiseOptions() {
  return { enabled: elements.rtcNoiseEnabled.checked, strength: Number(elements.rtcNoiseStrength.value) };
}

function renderRtcNoiseStatus(status) {
  state.rtc.noiseTelemetry = status;
  elements.rtcNoiseState.dataset.processing = JSON.stringify(status);
  const labels = {
    loading: "Noise suppression loading", active: "Noise suppression active",
    bypass: "Noise suppression bypassed", suspended: "Noise suppression waiting for playback",
    unavailable: "Noise suppression unavailable · original audio playing",
  };
  const detail = ["active", "bypass"].includes(status.state)
    ? ` · ${status.strength}% · ${status.frames} frames · RMS ${(status.inputRms || 0).toFixed(4)} → ${(status.outputRms || 0).toFixed(4)}` : "";
  elements.rtcNoiseState.textContent = `${labels[status.state] || status.state}${detail}`;
}

async function attachRtcNoisePlayback(remoteStream, generation, peer) {
  state.rtc.noiseAbort?.abort();
  state.rtc.noisePlayback?.dispose();
  state.rtc.noisePlayback = null;
  const abort = new AbortController();
  state.rtc.noiseAbort = abort;
  const current = () => isCurrentRtcGeneration(state.rtc.generation, generation)
    && state.rtc.peer === peer && state.rtc.remoteStream === remoteStream && state.rtc.noiseAbort === abort;
  const playback = await createRtcNoisePlayback(remoteStream, {
    ...rtcNoiseOptions(), signal: abort.signal, isCurrent: current, onState: renderRtcNoiseStatus,
    onFallback: () => {
      if (!current()) return;
      state.rtc.noisePlayback = null;
      elements.rtcRemoteAudio.srcObject = remoteStream;
      elements.rtcRemoteAudio.play().catch(() => {});
    },
  });
  if (!playback) return;
  if (!current()) { await playback.dispose(); return; }
  state.rtc.noisePlayback = playback;
  playback.setOptions(rtcNoiseOptions());
  elements.rtcRemoteAudio.srcObject = playback.stream;
  elements.rtcRemoteAudio.play().catch(() => {
    setResult(elements.rtcAudioResult, "Downlink audio arrived. Click the player to enable sound", "running");
  });
}

function updateRtcNoiseOptions() {
  elements.rtcNoiseStrengthValue.textContent = `${elements.rtcNoiseStrength.value}%`;
  elements.rtcNoiseStrength.disabled = !elements.rtcNoiseEnabled.checked;
  state.rtc.noisePlayback?.setOptions(rtcNoiseOptions());
}

async function recordRtcDiagnostic() {
  if (!state.rtc.localStream || !state.rtc.remoteStream || state.rtc.diagnosticRecording) {
    elements.rtcDiagnosticState.textContent = "Diagnostic needs an active call"; return;
  }
  const abort = new AbortController();
  state.rtc.diagnosticRecording = abort;
  elements.recordRtcDiagnostic.disabled = true;
  document.querySelector('#rtcDiagnosticFiles').hidden = true;
  try {
    await recordRtcAudioDiagnostic({ computer: state.rtc.localStream, 'robot-raw': state.rtc.remoteStream,
      'robot-clean': state.rtc.noisePlayback?.stream || state.rtc.remoteStream }, {
      signal: abort.signal,
      onState: value => { elements.rtcDiagnosticState.textContent = value === "recording"
        ? "Recording 20 seconds · speak normally with short pauses"
        : "Diagnostic saved · computer, robot original, robot processed, timing report"; },
      snapshot: () => ({ device: selectDiagnosticDeviceStats(state.status?.rtc?.stats),
        browser: { sent: state.rtc.browserAudioSent, received: state.rtc.browserAudioReceived,
          level: state.rtc.browserAudioLevel, latency: state.rtc.audioLatency,
          receive: state.rtc.audioReceiveStats || {} },
        noise: state.rtc.noiseTelemetry || {} }),
    });
    document.querySelector('#rtcDiagnosticFiles').hidden = false;
  } catch (error) { elements.rtcDiagnosticState.textContent = error.message; }
  finally { state.rtc.diagnosticRecording = null; elements.recordRtcDiagnostic.disabled = false; }
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
  state.rtc.channel = peer ? peer.createDataChannel("rtc-control", { ordered: true }) : null;
  const url = state.status?.connection?.mjpeg_websocket_url;
  if (!url) throw new Error("Device did not provide a direct live-video URL");
  state.rtc.videoTransport = createMjpegTransport({
    url,
    isActive: () => isCurrentRtcGeneration(state.rtc.generation, generation)
      && state.rtc.peer === peer && !state.rtc.teardownInProgress,
    onPacket: (packet, displayed, current) => enqueueMjpegPacket(packet, generation, { displayed, current }),
    onConnecting: () => setLiveVideoState("connecting", "Video connection interrupted; reconnecting"),
    onFailure: message => failRtcSession(message),
  });
}

async function startLiveVideo() {
  return startRtcSession("video");
}

async function startRtcAudio(requestId) {
  return startRtcSession("audio", requestId);
}

function startRtcControlLoops(generation) {
  pollRtcEvents(generation);
  state.rtc.heartbeatTimer = window.setInterval(async () => {
    if (!state.rtc.mode || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    const browserSendUs = Math.round((performance.timeOrigin + performance.now()) * 1000);
    try {
      await api(rtcEndpoint("clock-ping"), {
        method: "POST",
        body: JSON.stringify({ browser_send_us: browserSendUs }),
      });
    } catch (error) {
      if (state.rtc.mode && isCurrentRtcGeneration(state.rtc.generation, generation)) {
        failRtcSession(error.message);
      }
    }
  }, 1500);
  state.rtc.feedbackTimer = window.setInterval(async () => {
    const peer = state.rtc.peer;
    if (!state.rtc.mode || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
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
  state.rtc.browserAudioLevel = Math.max(audioLevel, state.rtc.noiseTelemetry?.inputRms || 0);
  state.rtc.audioReceiveStats = { packetsLost: lost, concealedSamples: concealedFrames, jitterUs };
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
  if (!state.rtc.mode || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
  try {
    const payload = await api(`${rtcEndpoint("events")}?after=${state.rtc.eventCursor}`);
    if (!state.rtc.mode || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    for (const event of payload.events || []) {
      state.rtc.eventCursor = Math.max(state.rtc.eventCursor, event.id || 0);
      await handleRtcEvent(event.message || {}, generation);
      if (!state.rtc.mode || !isCurrentRtcGeneration(state.rtc.generation, generation)) return;
    }
  } catch (error) {
    if (state.rtc.mode && isCurrentRtcGeneration(state.rtc.generation, generation)) {
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
    setRtcSessionState(data.state === "connected" && rtcModeHasVideo(state.rtc.mode) && !state.rtc.lastFrameAt
      ? "connecting" : data.state || "connecting");
    if (data.state === "failed") await failRtcSession(localizeError(data.reason || "Device RTC session failed"));
    if (data.state === "stopped" && state.rtc.mode) cleanupRtcSession();
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

async function stopRtcSession(requestId = state.rtc.requestId) {
  if (!requestId) return true;
  if (state.rtc.requestId !== requestId) {
    // Controller cleanup for an old attempt must preserve a newer local peer.
    try {
      await api("/api/rtc/session/stop", { method: "POST", body: JSON.stringify({ request_id: requestId }) });
      return true;
    } catch (_) { return false; }
  }
  const mode = resolveRtcMode(
    state.rtc.mode,
    state.status?.rtc?.mode,
    state.status?.resource_owners?.media,
    state.status?.rtc?.active === true,
  ) || "audio";
  if (state.rtc.teardownInProgress) return false;
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
    await api(rtcEndpoint("stop", mode), { method: "POST", body: JSON.stringify({ request_id: requestId }) });
    if (state.rtc.requestId === requestId) state.rtc.requestId = null;
    if (hadVideo) setResult(elements.liveVideoResult, "Live video stopped", "ok");
    if (hadAudio) setResult(elements.rtcAudioResult, "Full-duplex call ended", "ok");
    await refreshStatus();
    return true;
  } catch (error) {
    notify(`${error.message}; local audio/video stopped`, "error");
    if (hadVideo) setResult(elements.liveVideoResult, "Local audio/video stopped, but device release confirmation timed out", "error");
    if (hadAudio) setResult(elements.rtcAudioResult, "Local audio/video stopped, but device release confirmation timed out", "error");
    await refreshStatus();
    return false;
  } finally {
    state.rtc.teardownInProgress = false;
    if (state.status) renderStatus(state.status);
    await settleCombinedAudioStop();
  }
}

async function failRtcSession(message) {
  if (state.rtc.teardownInProgress) return;
  const mode = state.rtc.mode;
  const hadSession = Boolean(state.rtc.peer || state.rtc.localStream || mode);
  const hadAudio = rtcModeHasAudio(mode);
  const hadVideo = rtcModeHasVideo(mode);
  const stopPath = rtcEndpoint("stop");
  const requestId = state.rtc.requestId;
  state.rtc.teardownInProgress = true;
  cleanupRtcSession();
  if (hadAudio) setRtcAudioState("failed", message);
  if (hadVideo) setLiveVideoState("failed", message);
  notify(message, "error");
  try {
    if (mode && requestId) {
      await api(stopPath, { method: "POST", body: JSON.stringify({ request_id: requestId }) });
      if (state.rtc.requestId === requestId) state.rtc.requestId = null;
    }
  } catch (_) {
    // The local peer is already closed; status refresh remains the source of truth.
  } finally {
    if (hadSession || state.status) await refreshStatus();
    state.rtc.teardownInProgress = false;
    if (state.status) renderStatus(state.status);
    await settleCombinedAudioStop();
  }
}

function cleanupRtcSession() {
  if (rtcModeHasVideo(state.rtc.mode)) {
    elements.liveVideoCanvas.dataset.displayAudit = JSON.stringify(displayAudit.snapshot(performance.now()));
  }
  state.rtc.generation += 1;
  state.rtc.diagnosticRecording?.abort();
  state.rtc.noiseTelemetry = null;
  state.rtc.audioReceiveStats = null;
  state.rtc.noiseAbort?.abort();
  state.rtc.noiseAbort = null;
  state.rtc.noisePlayback?.dispose();
  state.rtc.noisePlayback = null;
  elements.rtcNoiseState.textContent = "Noise suppression ready for next call";
  elements.rtcNoiseState.dataset.processing = "{}";
  elements.rtcMicrophoneProcessing.textContent = "Waiting for microphone settings";
  elements.rtcMicrophoneProcessing.dataset.settings = "{}";
  state.rtc.videoTransport?.stop();
  state.rtc.videoTransport = null;
  state.localResources.delete("media");
  window.clearTimeout(state.rtc.pollTimer);
  window.clearInterval(state.rtc.heartbeatTimer);
  window.clearInterval(state.rtc.feedbackTimer);
  state.rtc.pollTimer = null;
  state.rtc.heartbeatTimer = null;
  state.rtc.feedbackTimer = null;
  const channel = state.rtc.channel;
  const peer = state.rtc.peer;
  const localStream = state.rtc.localStream;
  const diagnosticAudio = state.rtc.diagnosticAudio;
  const mode = state.rtc.mode;
  state.rtc.channel = null;
  state.rtc.peer = null;
  state.rtc.localStream = null;
  state.rtc.diagnosticAudio = null;
  state.rtc.remoteStream = null;
  state.rtc.browserAudioSent = 0;
  state.rtc.browserAudioReceived = 0;
  state.rtc.browserAudioLevel = 0;
  state.rtc.audioConnectedAt = 0;
  state.rtc.audioHealthState = "idle";
  state.rtc.audioVerified = false;
  state.rtc.rttUs = 0;
  state.rtc.mediaRttUs = 0;
  state.rtc.audioJitterCounter = null;
  state.rtc.audioLatency = { sampleValid: false, actualMs: 0, targetMs: 0, minimumMs: 0 };
  elements.rtcAudioLatency.textContent = "Waiting for Stage Latency Samples";
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
  if (diagnosticAudio) {
    diagnosticAudio.dispose().catch(() => {});
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

async function enqueueMjpegPacket(value, generation, transport = null) {
  let admission = { ownsDecoder: false, replacedPending: false };
  try {
    const packet = value instanceof ArrayBuffer ? value : await value.arrayBuffer();
    if (!isCurrentRtcGeneration(state.rtc.generation, generation) || (transport && !transport.current())) return;
    const completePacket = acceptMjpegTransportPacket(state.rtc.mjpegChunkReassembler, packet);
    if (!completePacket) return;
    const frame = parseWjpgPacket(completePacket);
    frame.transport = transport;
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
    if (!isCurrentRtcGeneration(state.rtc.generation, generation)
      || (frame.transport && !frame.transport.current())) return;
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
  frame.transport?.displayed();
  if (state.rtc.displayedFrames === 0 || elements.liveVideoStage.dataset.state !== "live") {
    setResult(elements.liveVideoResult, "Live camera frames received", "ok");
  }
  state.rtc.lastFrameAt = now;
  displayAudit.record(frame.sequence, now, elements.liveVideoCanvas.width, elements.liveVideoCanvas.height);
  if (now - displayAuditPublishedAt >= 1000) {
    elements.liveVideoCanvas.dataset.displayAudit = JSON.stringify(displayAudit.snapshot(now));
    displayAuditPublishedAt = now;
  }
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

async function capturePhoto({ scene = false } = {}) {
  if (scene) state.scene.audioResultActive = false;
  const controls = combinedSceneAvailability(state.status, {
    rtcMode: state.rtc.mode, localResources: state.localResources,
  });
  const payload = await runAction({
    path: "/api/actions/capture-photo",
    result: scene ? elements.sceneResult : elements.cameraResult,
    pending: "Requesting JPEG frame…",
    complete: (value) => `Photo received · ${formatBytes(value.bytes)}`,
    station: document.querySelector(".station-camera"),
    resources: controls.photoResources,
  });
  if (payload) {
    showPhoto(payload.artifact_url);
    elements.scenePhotoCaption.textContent = `Photo received · ${formatBytes(payload.bytes)}`;
  }
  return payload;
}

async function capturePhotoWithFeedback() {
  const payload = await runAction({
    path: "/api/actions/capture-photo-with-feedback",
    result: elements.cameraResult,
    pending: "Requesting JPEG frame with device feedback…",
    complete: (value) => `Photo with feedback received · ${formatBytes(value.bytes)}`,
    station: document.querySelector(".station-camera"),
    resources: ["camera", "animation", "microphone", "speaker"],
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

function concurrencySummary(report) {
  const counts = `Photos: ${report.counts.camera}, audio: ${report.counts.speaker}, UI: ${report.counts.ui}`;
  const verdict = report.running ? "Concurrent test running" : report.passed ? "SDK checks passed" : report.cancelled ? "Interrupted" : report.incomplete ? "Verification incomplete" : "Failed";
  return `${verdict} · ${Math.round(report.elapsed_s || 0)} s / ${report.duration_requested} s · ${counts}`;
}

function renderConcurrencyResults(report) {
  const container = document.querySelector("#concurrencyDetails");
  const newRun = container.dataset.runId !== report.run_id;
  container.dataset.runId = report.run_id;
  container.dataset.running = String(report.running);
  delete container.dataset.pendingRunId;
  if (newRun) {
    document.querySelector("#concurrencyUiObserved").checked = false;
    document.querySelector("#concurrencyAudioObserved").checked = false;
  }
  container.replaceChildren();
  const labels = { camera: "Continuous Photos", speaker: "Speaker Playback", ui: "Custom UI" };
  const statuses = { incomplete: "Verification incomplete", running: "Running", passed: "SDK checks passed", failed: "Failed", not_started: "Not run", interrupted: "Interrupted", cleanup_failed: "Cleanup failed" };
  const stages = document.querySelector("#concurrencyStages");
  stages.replaceChildren();
  ["ui", "camera", "speaker", "cleanup"].forEach((name, index) => {
    const result = report.results[name];
    const cleanupErrors = report.errors.filter((item) => item.worker.endsWith("_cleanup"));
    const startupAborted = !report.running && report.started === false;
    const status = result?.status || (report.running ? "running" : startupAborted ? "not_started" : cleanupErrors.length ? "cleanup_failed" : "passed");
    const item = document.createElement("li");
    item.dataset.state = status;
    const number = document.createElement("span");
    number.textContent = String(index + 1).padStart(2, "0");
    const title = document.createElement("strong");
    title.textContent = labels[name] || "Resource Recovery";
    const detail = document.createElement("small");
    detail.textContent = name === "cleanup"
      ? (report.running ? "Waiting for operations to finish" : startupAborted ? "Workload did not start; no cleanup commands sent" : cleanupErrors.length ? cleanupErrors.map((entry) => entry.error).join("; ") : "Cleanup commands acknowledged")
      : `${statuses[status]} · Succeeded: ${result.succeeded} / Failed: ${result.failed}${result.last_error ? ` · ${result.last_error}` : ""}`;
    if (name === "ui") detail.textContent += ` · Dynamic updates: ${result.updates_succeeded}`;
    item.append(number, title, detail);
    stages.append(item);
  });
  const preview = document.querySelector("#concurrencyPhoto");
  const photo = report.results.camera.last_image;
  preview.hidden = !photo;
  if (photo && preview.dataset.file !== photo.file) {
    preview.src = `/artifacts/${encodeURIComponent(photo.file)}`;
    preview.dataset.file = photo.file;
  }
  const link = document.querySelector("#concurrencyReportLink");
  link.hidden = report.running || report.report_saved === false;
  if (link.hidden) link.removeAttribute("href");
  else link.href = `/artifacts/${encodeURIComponent(report.report)}`;
  const saveState = document.querySelector("#concurrencyReportState");
  saveState.hidden = !report.report_save_error;
  saveState.textContent = report.report_save_error ? `Report save failed: ${report.report_save_error}` : "";
  for (const name of ["camera", "speaker", "ui"]) {
    const result = report.results[name];
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    heading.textContent = labels[name];
    section.append(heading);
    const lines = [
      statuses[result.status],
      `Attempted: ${result.attempted} / Succeeded: ${result.succeeded} / Failed: ${result.failed}`,
      result.average_ms === null ? "No timing data" : `Average: ${result.average_ms} ms / Maximum: ${result.max_ms} ms`,
      result.evidence,
    ];
    if (name === "camera" && result.last_image) {
      const photo = result.last_image;
      lines.push(`Last image: ${photo.width} x ${photo.height}, ${photo.bytes} bytes`, photo.file);
    }
    if (name === "speaker") lines.push("Actual sound: awaiting device confirmation");
    if (name === "ui") lines.push("Screen appearance: awaiting device confirmation", "Command counts include UI startup and updates", `Dynamic updates: ${result.updates_succeeded}`);
    if (result.last_error !== null) lines.push(`Failure reason: ${result.last_error || "No error detail returned"}`);
    if (result.cleanup_error !== null) lines.push(`Cleanup error: ${result.cleanup_error || "No error detail returned"}`);
    for (const line of lines) {
      const paragraph = document.createElement("p");
      paragraph.textContent = line;
      section.append(paragraph);
    }
    container.append(section);
  }
}

document.querySelector("#concurrencyStart").addEventListener("click", async () => {
  const start = document.querySelector("#concurrencyStart");
  start.disabled = true;
  let receivedReport = false;
  const panel = document.querySelector("#concurrencyStation");
  panel.dataset.requesting = "true";
  const details = document.querySelector("#concurrencyDetails");
  details.dataset.pendingRunId = details.dataset.runId || "";
  document.querySelector("#concurrencyUiObserved").checked = false;
  document.querySelector("#concurrencyAudioObserved").checked = false;
  document.querySelector("#concurrencyReportLink").hidden = true;
  document.querySelector("#concurrencyReportState").hidden = true;
  document.querySelector("#concurrencyPhoto").hidden = true;
  document.querySelector("#concurrencyStop").disabled = false;
  document.querySelectorAll("#concurrencyStages li").forEach((item) => {
    item.dataset.state = "running";
    item.querySelector("small").textContent = "Running";
  });
  details.textContent = "Concurrent test running";
  try {
    await runAction({
      path: "/api/concurrency/start",
      body: { duration: Number(document.querySelector("#concurrencyDuration").value) },
      result: document.querySelector("#concurrencyResult"),
      station: document.querySelector("#concurrencyStation"),
      resources: ["camera", "animation", "microphone", "speaker"],
      pending: "Concurrent test running",
      complete: (report) => {
        renderConcurrencyResults(report);
        receivedReport = true;
        const counts = `Photos: ${report.counts.camera}, audio: ${report.counts.speaker}, UI: ${report.counts.ui}`;
        if (!report.passed) throw new Error(`${counts}. ${report.errors.map((item) => `${item.worker}: ${item.error}`).join("; ") || (report.incomplete ? "Dynamic UI updates not verified" : "Test stopped")}`);
        if (report.report_save_error) return `${counts}. SDK checks passed`;
        return `${counts}. Report: ${report.report}`;
      },
    });
  } catch (error) {
    if (!receivedReport) document.querySelector("#concurrencyDetails").textContent = `No test report: ${error.message}`;
  }
  finally {
    delete panel.dataset.requesting;
    delete details.dataset.pendingRunId;
    await refreshStatus();
  }
});
document.querySelector("#concurrencyCleanup").addEventListener("click", async () => {
  const panel = document.querySelector("#concurrencyStation");
  panel.dataset.cleaning = "true";
  document.querySelector("#concurrencyCleanup").disabled = true;
  try {
    const response = await api("/api/concurrency/cleanup", { method: "POST" });
    if (Object.keys(response.pending).length) {
      notify(`Cleanup pending: ${Object.values(response.pending).join("; ")}`, "error");
    } else {
      notify("Cleanup commands acknowledged", "ok");
    }
  } catch (error) {
    notify(error.message, "error");
  } finally {
    delete panel.dataset.cleaning;
    await refreshStatus();
  }
});
document.querySelector("#concurrencyStop").addEventListener("click", () => {
  api("/api/concurrency/stop", { method: "POST" }).catch((error) => notify(error.message, "error"));
});

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
elements.startProceduralButton.addEventListener("click", () => { proceduralAction("start").catch(() => {}); });
elements.rtcNoiseEnabled.addEventListener("change", updateRtcNoiseOptions);
elements.recordRtcDiagnostic.addEventListener("click", recordRtcDiagnostic);
elements.rtcNoiseStrength.addEventListener("input", updateRtcNoiseOptions);
elements.rtcRemoteAudio.addEventListener("play", () => { state.rtc.noisePlayback?.resume().catch(() => {}); });
elements.stopProceduralButton.addEventListener("click", () => { proceduralAction("stop").catch(() => {}); });
elements.startSceneAudioButton.addEventListener("click", startCombinedScene);
elements.stopSceneAudioButton.addEventListener("click", endCombinedScene);
elements.captureScenePhotoButton.addEventListener("click", () => { capturePhoto({ scene: true }).catch(() => {}); });
elements.startSceneRecordingButton.addEventListener("click", () => { sceneRecordingAction("start"); });
elements.stopSceneRecordingButton.addEventListener("click", () => { sceneRecordingAction("stop"); });
elements.exportSceneReportButton.addEventListener("click", exportSceneReport);
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
elements.capturePhotoWithFeedbackButton.addEventListener("click", () => {
  capturePhotoWithFeedback().catch(() => {});
});

async function inferenceAction(action, preview = false) {
  if (inferenceRequestPending) return;
  inferenceRequestPending = true;
  if (state.status) renderStatus(state.status);
  try {
    const isRead = action === "models" || action === "result";
    const path = action === "models" ? "/api/vision/models" : `/api/vision/inference/${action}`;
    const response = await api(path, {method: isRead ? "GET" : "POST",
      ...(action === "start" ? {body: JSON.stringify({model_id: Number(elements.inferenceModel.value), preview})} : {})});
    if (action === "models") {
      response.models = testBenchModels(response.models);
      elements.inferenceModel.replaceChildren();
      for (const model of response.models) {
        const option = document.createElement("option");
        option.value = String(model.model_id);
        option.textContent = `${model.model_id} · ${model.name}${model.verified ? "" : " (unverified)"}`;
        option.disabled = !model.verified;
        elements.inferenceModel.append(option);
      }
      elements.inferenceModel.value = String(response.models.find(model => model.verified)?.model_id || "");
    }
    if (action === "start" || action === "stop") {
      modelPreviewActive = action === "start" && preview;
      modelPreviewEpoch = modelPreviewActive ? modelPreviewLifecycle.start(Date.now()) : modelPreviewLifecycle.stop();
      modelPreviewSequence = null;
      modelPreviewFrameAt = Date.now();
      elements.inferenceCanvas.hidden = !modelPreviewActive;
      elements.inferenceCanvas.getContext("2d").clearRect(0, 0, 640, 480);
      elements.inferenceMetrics.textContent = modelPreviewActive ? "Waiting for a preview frame" : "";
    }
    const {jpeg_base64, ...details} = response;
    elements.inferenceResult.textContent = JSON.stringify(details, null, 2);
    setResult(elements.inferenceState, action === "start" ? "Inference is running" : action === "stop"
      ? "Inference stopped" : action === "result" && !response.ready ? "Model is warming up; read again shortly" : "Vision data updated", "ok");
  } catch (error) {
    setResult(elements.inferenceState, error.message, "error");
  } finally {
    inferenceRequestPending = false;
    await refreshStatus();
  }
}
elements.queryModelsButton.addEventListener("click", () => { inferenceAction("models").catch(() => {}); });
elements.startInferencePreviewButton.addEventListener("click", () => { inferenceAction("start", true).catch(() => {}); });
elements.startInferenceButton.addEventListener("click", () => { inferenceAction("start").catch(() => {}); });
elements.queryInferenceButton.addEventListener("click", () => { inferenceAction("result").catch(() => {}); });
elements.stopInferenceButton.addEventListener("click", () => { inferenceAction("stop").catch(() => {}); });
elements.inferenceModel.addEventListener("change", () => { if (state.status) renderStatus(state.status); });

async function faceTrackingAction(action) {
  if (faceRequestPending) return;
  faceRequestPending = true;
  if (state.status) renderStatus(state.status);
  try {
    const result = await api(action === "status" ? "/api/vision/status" : `/api/face-tracking/${action}`,
      { method: action === "status" ? "GET" : "POST" });
    if (action === "status") elements.faceVisionStatus.textContent = JSON.stringify(result, null, 2);
    setResult(elements.faceTrackingResult, action === "status" ? "Vision status updated"
      : action === "preview/start" ? "Preview requested; waiting for the first image"
        : action === "start" ? "Face tracking is running" : "Face tracking stopped; position held", "ok");
  } catch (error) {
    setResult(elements.faceTrackingResult, error.message, "error");
  } finally {
    faceRequestPending = false;
    await refreshStatus();
  }
}
elements.queryVisionButton.addEventListener("click", () => { faceTrackingAction("status").catch(() => {}); });
elements.startFaceTrackingButton.addEventListener("click", () => { faceTrackingAction("start").catch(() => {}); });
elements.startFacePreviewButton.addEventListener("click", () => { faceTrackingAction("preview/start").catch(() => {}); });
elements.stopFaceTrackingButton.addEventListener("click", () => { faceTrackingAction("stop").catch(() => {}); });

setInterval(async () => {
  if (!facePreviewActive || faceFramePending || faceRequestPending) return;
  faceFramePending = true;
  const epoch = facePreviewEpoch;
  try {
    const frame = await api("/api/face-tracking/preview/frame");
    if (!frame.ready || epoch !== facePreviewEpoch) return;
    const image = new Image();
    image.src = `data:image/jpeg;base64,${frame.jpeg_base64}`;
    await image.decode();
    if (!facePreviewActive || epoch !== facePreviewEpoch) return;
    const canvas = elements.facePreviewCanvas;
    canvas.width = frame.width; canvas.height = frame.height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    context.strokeStyle = "#45e6a3"; context.lineWidth = 2;
    for (const face of frame.faces) context.strokeRect(face.x, face.y, face.width, face.height);
    canvas.dataset.sequence = String(frame.sequence);
    setResult(elements.faceTrackingResult, "Face tracking is running", "ok");
    elements.facePreviewMetrics.textContent = `#${frame.sequence} · ${frame.width} × ${frame.height} · ${frame.faces.length} faces · ${frame.telemetry.age_ms} ms`;
  } catch (error) {
    if (epoch === facePreviewEpoch) elements.facePreviewMetrics.textContent = error.message;
  } finally {
    faceFramePending = false;
  }
}, 150);
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
  if (state.rtc.lastFrameAt > 0 && state.rtc.mode) {
    elements.liveVideoCanvas.dataset.displayAudit = JSON.stringify(displayAudit.snapshot(performance.now()));
    const age = Math.max(0, Math.round(performance.now() - state.rtc.lastFrameAt));
    elements.liveVideoFrameAge.textContent = `${age} MS AGO`;
    elements.liveVideoFps.textContent = `${currentDisplayFps().toFixed(1)} FPS`;
  }
}, 1000);
window.addEventListener("pagehide", () => {
  state.scene.operationGeneration++;
  combinedScene.cancelForPageExit();
  stopRandomAnimation({ quiet: true });
  window.clearTimeout(state.animation.prefetchDebounceTimer);
  if (scenePageLifecycle.close(state.status?.procedural?.state)) {
    navigator.sendBeacon("/api/controls/procedural/stop");
  }
  const mode = resolveRtcMode(
    state.rtc.mode,
    state.status?.rtc?.mode,
    state.status?.resource_owners?.media,
    state.status?.rtc?.active === true,
  );
  const requestId = state.rtc.requestId;
  if (!requestId) return;
  navigator.sendBeacon(rtcEndpoint("stop", mode), new Blob([JSON.stringify({ request_id: requestId })], { type: "application/json" }));
  cleanupRtcSession();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) restoreRtcPageSession().catch(error => notify(error.message, "error"));
});

async function restoreRtcPageSession() {
  scenePageLifecycle.reopen();
  // A beacon is best effort. Retry with its original identity; the backend's
  // identity guard preserves a newer session and clears ours only on an ACK.
  if (state.rtc.requestId) await stopRtcSession(state.rtc.requestId);
  else await refreshStatus();
}
setInterval(refreshStatus, 1000);
refreshStatus({ quiet: false });
drawEmptyWaveform();
updateMotionPreview();
updateLightPreview();

// Each JPEG and its center-based boxes share one latest result.
setInterval(async () => {
  // This clock keeps running even when fetch/decode or a control request is pending.
  if (modelPreviewLifecycle.expired(Date.now())) {
    elements.inferenceCanvas.getContext("2d").clearRect(0, 0, 640, 480);
    elements.inferenceMetrics.textContent = "Preview requested; no recent image received";
  }
  if (!modelPreviewActive || modelPreviewPending || inferenceRequestPending) return;
  modelPreviewPending = true;
  const epoch = modelPreviewEpoch;
  try {
    const result = await api("/api/vision/inference/result");
    if (epoch !== modelPreviewEpoch) return;
    if (!result.ready || !result.jpeg_base64 || result.sequence === modelPreviewSequence) {
      if (Date.now() - modelPreviewFrameAt > 2000) {
        elements.inferenceCanvas.getContext("2d").clearRect(0, 0, 640, 480);
        elements.inferenceMetrics.textContent = "Preview requested; no recent image received";
      }
      return;
    }
    const image = new Image();
    image.src = `data:image/jpeg;base64,${result.jpeg_base64}`;
    await image.decode();
    if (!modelPreviewActive || epoch !== modelPreviewEpoch) return;
    if (!modelPreviewLifecycle.accept(epoch, Date.now())) return;
    const canvas = elements.inferenceCanvas;
    canvas.width = result.frame_width; canvas.height = result.frame_height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#45e6a3"; ctx.fillStyle = "#45e6a3";
    ctx.lineWidth = 2; ctx.font = "16px sans-serif";
    for (const box of result.boxes) {
      const x = box.x - box.width / 2, y = box.y - box.height / 2;
      ctx.strokeRect(x, y, box.width, box.height);
      const label = i18n.translate(detectionLabel(result.model_id, box.target));
      ctx.fillText(`${label} (${box.target}) - ${box.score}%`, Math.max(0, x), Math.max(18, y - 4));
    }
    canvas.dataset.sequence = String(result.sequence);
    modelPreviewSequence = result.sequence;
    modelPreviewFrameAt = Date.now();
    elements.inferenceMetrics.textContent = `#${result.sequence} - ${result.frame_width} x ${result.frame_height} - ${result.boxes.length} detections`;
    const {jpeg_base64, ...details} = result;
    elements.inferenceResult.textContent = JSON.stringify(details, null, 2);
  } catch (error) {
    if (epoch === modelPreviewEpoch) {
      elements.inferenceMetrics.textContent = error.message;
      elements.inferenceCanvas.getContext("2d").clearRect(0, 0, 640, 480);
    }
  } finally { modelPreviewPending = false; }
}, 200);

if (new URLSearchParams(window.location.search).get('rtc_echo_probe') === '1') {
  import('./rtc-echo-probe.mjs').then(({ mountEchoProbe }) => mountEchoProbe());
}
