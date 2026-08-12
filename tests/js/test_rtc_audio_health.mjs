import assert from "node:assert/strict";
import test from "node:test";

import {
  RTC_AUDIO_VERIFY_TIMEOUT_MS,
  evaluateRtcAudioHealth,
} from "../../examples/sdk_media_lab/web/rtc-audio-health.mjs";

const healthy = {
  peerConnected: true,
  browserTxPackets: 10,
  browserRxPackets: 8,
  deviceCaptureFrames: 10,
  deviceTxPackets: 8,
  deviceTxErrors: 0,
  deviceCapturePeak: 1200,
  browserAudioLevel: 0.25,
  browserPlaybackActive: true,
  deviceRxPackets: 10,
  deviceDecodedFrames: 10,
  deviceRenderErrors: 0,
  deviceI2sBytes: 3200,
  devicePlaybackPeak: 1400,
  elapsedMs: 1000,
};

test("transport connected is not enough to claim full duplex", () => {
  assert.deepEqual(evaluateRtcAudioHealth({
    ...healthy,
    browserRxPackets: 0,
    deviceTxPackets: 0,
  }), {
    state: "verifying",
    missing: ["device_tx", "browser_rx"],
  });
});

test("missing Watcher microphone uplink fails after the deadline", () => {
  assert.equal(evaluateRtcAudioHealth({
    ...healthy,
    browserRxPackets: 0,
    deviceCaptureFrames: 0,
    deviceTxPackets: 0,
    elapsedMs: RTC_AUDIO_VERIFY_TIMEOUT_MS,
  }).state, "failed");
});

test("both directions and device capture are required for healthy", () => {
  assert.deepEqual(evaluateRtcAudioHealth(healthy), {
    state: "healthy",
    missing: [],
  });
});

test("send errors are visible even when packets get through", () => {
  assert.equal(evaluateRtcAudioHealth({ ...healthy, deviceTxErrors: 1 }).state, "degraded");
});

test("packets containing silence cannot claim healthy full duplex", () => {
  assert.deepEqual(evaluateRtcAudioHealth({ ...healthy, deviceCapturePeak: 8 }), {
    state: "verifying",
    missing: ["device_signal"],
  });
  assert.equal(evaluateRtcAudioHealth({
    ...healthy,
    deviceCapturePeak: 8,
    elapsedMs: RTC_AUDIO_VERIFY_TIMEOUT_MS,
  }).state, "failed");
});

test("paused browser playback cannot claim audible full duplex", () => {
  assert.deepEqual(evaluateRtcAudioHealth({ ...healthy, browserPlaybackActive: false }), {
    state: "verifying",
    missing: ["browser_playback"],
  });
});

test("older firmware without signal metrics remains in verification", () => {
  const { deviceCapturePeak, browserAudioLevel, ...withoutSignalMetrics } = healthy;
  assert.deepEqual(evaluateRtcAudioHealth(withoutSignalMetrics), {
    state: "verifying",
    missing: ["device_signal", "browser_signal"],
  });
});

test("browser RTP send alone cannot prove robot speaker playback", () => {
  assert.deepEqual(evaluateRtcAudioHealth({
    ...healthy,
    deviceRxPackets: 0,
    deviceDecodedFrames: 0,
    deviceI2sBytes: 0,
    devicePlaybackPeak: 0,
  }), {
    state: "verifying",
    missing: ["device_rx", "device_decode", "device_playback", "device_playback_signal"],
  });
});

test("robot audio renderer failures degrade an otherwise audible call", () => {
  assert.equal(evaluateRtcAudioHealth({ ...healthy, deviceRenderErrors: 1 }).state, "degraded");
});
