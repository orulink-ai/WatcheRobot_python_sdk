import assert from "node:assert/strict";
import test from "node:test";

import {
  appendSceneSample,
  combinedSceneAvailability,
  createSceneSample,
  createScenePageLifecycle,
  evaluateProceduralRender,
  sceneRecordingLabel,
  summarizeSceneSamples,
} from "../../examples/sdk_media_lab/web/combined-scene.mjs";

const status = {
  connected: true,
  capabilities: ["expression.audio_follow.v1", "rtc.audio.full_duplex.v1", "camera.capture"],
  device: { device_id: "test-device" },
  procedural: { state: "running", telemetry_available: true, mouth_level_milli: 180, pcm_frames: 42 },
  resource_owners: { animation: "procedural", microphone: "rtc_audio", speaker: "rtc_audio" },
  resources: { current: {
    sequence: 7, stage: "rtc_running",
    memory: {
      internal: { free_bytes: 100000, minimum_free_bytes: 90000, largest_free_block_bytes: 32000 },
      dma: { free_bytes: 75000, minimum_free_bytes: 70000, largest_free_block_bytes: 16000 },
      psram: { free_bytes: 6000000, minimum_free_bytes: 5000000, largest_free_block_bytes: 4000000 },
    },
    animation: { audio_follow: true, measured_fps_x100: 1980, target_fps_x100: 2000, sample_valid: true },
  } },
  rtc: { active: true, mode: "audio", stats: { audio_render_errors: 0, audio_tx_errors: 2 } },
};

test("audio RTC and procedural animation permit concurrent capture without interrupting audio", () => {
  const controls = combinedSceneAvailability(status, { rtcMode: "audio" });
  assert.equal(controls.snapshot, true);
  assert.equal(controls.stopProcedural, true);
  assert.equal(controls.startProcedural, false);
  assert.equal(controls.stopAudio, true);
  assert.deepEqual(controls.photoResources, ["camera"]);
});

test("unsupported firmware never offers procedural start or animation-preserving photo", () => {
  const controls = combinedSceneAvailability({ ...status, capabilities: ["camera.capture"] });
  assert.equal(controls.proceduralSupported, false);
  assert.equal(controls.startProcedural, false);
  assert.deepEqual(controls.photoResources, ["camera", "animation"]);
});

test("pending starts, camera owners, video and disconnected states disable conflicting actions", () => {
  assert.equal(combinedSceneAvailability(status, { rtcMode: "av" }).snapshot, false);
  assert.equal(combinedSceneAvailability({ ...status, resource_owners: { camera: "vision" } }).snapshot, false);
  assert.equal(combinedSceneAvailability(status, { localResources: new Set(["camera"]) }).snapshot, false);
  assert.equal(combinedSceneAvailability({ ...status, connected: false }).snapshot, false);
  assert.equal(combinedSceneAvailability({ ...status, procedural: { state: "starting" } }).startProcedural, false);
  assert.equal(combinedSceneAvailability({ ...status, procedural: { state: "stop_required" } }).stopProcedural, true);
});

test("scene measurements retain actual firmware memory, mouth and FPS telemetry", () => {
  const sample = createSceneSample(status, { nowMs: 1000, rtcMode: "audio" });
  assert.equal(sample.internal.free, 100000);
  assert.equal(sample.dma.largest, 16000);
  assert.equal(sample.psram.minimum, 5000000);
  assert.equal(sample.mouthLevelMilli, 180);
  assert.equal(sample.pcmFrames, 42);
  assert.equal(sample.animationFps, 19.8);
  assert.equal(sample.audioTxErrors, 2);
});

test("missing or invalid measurements stay unknown instead of becoming zero or a healthy verdict", () => {
  const missing = createSceneSample({ ...status, procedural: { telemetry_available: false }, resources: { current: {
    sequence: 8, memory: { internal: { free_bytes: null, minimum_free_bytes: "", largest_free_block_bytes: -1 } },
    animation: { sample_valid: false, measured_fps_x100: 0 },
  } }, rtc: {} });
  assert.equal(missing.internal.free, null);
  assert.equal(missing.internal.minimum, null);
  assert.equal(missing.internal.largest, null);
  assert.equal(missing.animationFps, null);
  assert.equal(missing.mouthLevelMilli, null);
  assert.equal(missing.audioTxErrors, null);
  assert.equal(createSceneSample({ connected: false }), null);
});

test("chart history is bounded, deduplicates polls and distinguishes devices", () => {
  const initial = createSceneSample(status, { nowMs: 1 });
  const samples = appendSceneSample([], initial, 3);
  assert.strictEqual(appendSceneSample(samples, createSceneSample(status, { nowMs: 2 }), 3), samples);
  let history = samples;
  for (let sequence = 8; sequence < 12; sequence++) {
    history = appendSceneSample(history, createSceneSample({ ...status, resources: { current: {
      ...status.resources.current, sequence,
    } } }, { nowMs: sequence }), 3);
  }
  assert.deepEqual(history.map(sample => sample.sequence), [9, 10, 11]);
  const other = createSceneSample({ ...status, device: { device_id: "second-device" } });
  assert.equal(appendSceneSample(history, other, 3).at(-1).deviceId, "second-device");
});

test("sample summary reports observed lows and ignores unavailable values", () => {
  const first = createSceneSample(status, { nowMs: 1000 });
  const second = { ...first, timestampMs: 2000, internal: { ...first.internal, free: 80000 },
    dma: { ...first.dma, largest: null }, animationFps: 17.2 };
  const summary = summarizeSceneSamples([first, second]);
  assert.equal(summary.durationMs, 1000);
  assert.equal(summary.internalFreeLow, 80000);
  assert.equal(summary.dmaLargestLow, 16000);
  assert.equal(summary.animationFpsLow, 17.2);
  assert.equal(summarizeSceneSamples([]).internalFreeLow, null);
});

test("device reboot starts a new chart instead of mixing samples from two boots", () => {
  const before = { ...createSceneSample(status), capturedAtMs: 50000 };
  const after = { ...before, sequence: 1, capturedAtMs: 1000 };
  const history = appendSceneSample([before], after);
  assert.deepEqual(history, [after]);
});

test("an empty recording label has a valid default and long labels stay bounded", () => {
  assert.equal(sceneRecordingLabel("  "), "combined-scene");
  assert.equal(sceneRecordingLabel("  animation + RTC  "), "animation + RTC");
  assert.equal(sceneRecordingLabel("a".repeat(200)).length, 80);
});

test("stale device telemetry does not become a new memory or animation measurement", () => {
  for (const freshness of ["stale", "unavailable"]) {
    assert.equal(createSceneSample({ ...status, resources: { ...status.resources, telemetry: { status: freshness } } }), null);
  }
});

test("a start acknowledgement after page exit triggers cleanup, including browser history restoration", async () => {
  const lifecycle = createScenePageLifecycle();
  let completeStart;
  let cleanups = 0;
  const started = lifecycle.startProcedural(() => new Promise(resolve => { completeStart = resolve; }), async () => { cleanups++; });
  assert.equal(lifecycle.close("idle"), true);
  lifecycle.reopen();
  completeStart({ state: "running" });
  await started;
  assert.equal(cleanups, 1);
  assert.equal(lifecycle.close("idle"), false);
  assert.equal(lifecycle.close("stop_required"), true);
});

test("an accepted animation lease cannot claim rendering when the device reports update failures", () => {
  const actual = { ...status, resources: { ...status.resources, current: {
    ...status.resources.current,
    animation: { active: false, sample_valid: false, update_errors: 3, audio_follow: true },
  } } };
  assert.equal(evaluateProceduralRender(actual).state, "failed");
  assert.equal(evaluateProceduralRender(actual).updateErrors, 3);
  const running = { ...actual, resources: { ...actual.resources, current: {
    ...actual.resources.current, animation: { active: true, sample_valid: true, update_errors: 0 },
  } } };
  assert.equal(evaluateProceduralRender(running).state, "running");
  assert.equal(evaluateProceduralRender({ ...running, resources: { ...running.resources, telemetry: { status: "stale" } } }).state, "waiting_data");
});

test("device pre-RTP and speaker queue drops remain visible and missing counters stay unknown", () => {
  const dropped = createSceneSample({ ...status, rtc: { ...status.rtc, stats: {
    audio_tx_dropped_frames: 1399, audio_queue_dropped: 6, audio_tx_errors: 0, audio_render_errors: 0,
  } } });
  assert.equal(dropped.audioTxDroppedFrames, 1399);
  assert.equal(dropped.audioQueueDroppedFrames, 6);
  const unknown = createSceneSample({ ...status, rtc: { ...status.rtc, stats: {} } });
  assert.equal(unknown.audioTxDroppedFrames, null);
  assert.equal(unknown.audioQueueDroppedFrames, null);
});

test("idle SD playback FPS cannot be attributed to the procedural audio-follow scene", () => {
  const idle = { ...status, procedural: { ...status.procedural, state: "idle" }, resources: {
    ...status.resources, current: { ...status.resources.current, animation: {
      active: true, sample_valid: true, audio_follow: false, measured_fps_x100: 1000, target_fps_x100: 1000,
    } },
  } };
  const sample = createSceneSample(idle);
  assert.equal(sample.animationFps, null);
  assert.equal(sample.animationTargetFps, null);
  assert.equal(sample.internal.free, 100000);
  const stoppedFollower = createSceneSample({ ...status, resources: { ...status.resources, current: {
    ...status.resources.current, animation: { ...status.resources.current.animation, audio_follow: false },
  } } });
  assert.equal(stoppedFollower.animationFps, null);
  assert.equal(stoppedFollower.animationTargetFps, null);
});
