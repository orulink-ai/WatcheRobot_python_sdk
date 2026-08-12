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
