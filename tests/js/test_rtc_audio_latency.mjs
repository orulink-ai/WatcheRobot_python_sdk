import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateRoundTripUs,
  configureLowLatencyAudioReceivers,
  selectMediaRoundTripUs,
  sampleAudioJitterBuffer,
} from "../../examples/sdk_media_lab/web/rtc-audio-latency.mjs";

test("measures RTC round trip from the device pong instead of local POST completion", () => {
  assert.equal(calculateRoundTripUs(1_000_000, 1_075_250), 75_250);
  assert.equal(calculateRoundTripUs(1_100_000, 1_075_250), 0);
});

test("prefers the nominated WebRTC media path round trip", () => {
  const reports = [
    { type: "candidate-pair", state: "succeeded", currentRoundTripTime: 0.080 },
    { type: "candidate-pair", state: "succeeded", nominated: true, currentRoundTripTime: 0.0245 },
    { type: "inbound-rtp", currentRoundTripTime: 0.001 },
  ];
  assert.equal(selectMediaRoundTripUs(reports), 24_500);
});

test("requests a 20 ms jitter target across current and legacy Chromium receivers", () => {
  const audio = {
    track: { kind: "audio" },
    jitterBufferTarget: null,
    playoutDelayHint: null,
  };
  const legacyAudio = { track: { kind: "audio" }, playoutDelayHint: null };
  const unsupported = { track: { kind: "audio" } };
  const video = { track: { kind: "video" }, jitterBufferTarget: null };
  const peer = { getReceivers: () => [audio, legacyAudio, unsupported, video] };

  assert.equal(configureLowLatencyAudioReceivers(peer, 20), 2);
  assert.equal(audio.jitterBufferTarget, 20);
  assert.equal(audio.playoutDelayHint, 0.02);
  assert.equal(legacyAudio.playoutDelayHint, 0.02);
  assert.equal(video.jitterBufferTarget, null);
});

test("uses interval deltas instead of lifetime averages for current playout delay", () => {
  const first = sampleAudioJitterBuffer(null, {
    jitterBufferEmittedCount: 100,
    jitterBufferDelay: 5,
    jitterBufferTargetDelay: 4,
    jitterBufferMinimumDelay: 2,
  });
  assert.equal(first.sampleValid, false);

  const second = sampleAudioJitterBuffer(first.counter, {
    jitterBufferEmittedCount: 120,
    jitterBufferDelay: 5.6,
    jitterBufferTargetDelay: 4.4,
    jitterBufferMinimumDelay: 2.2,
  });
  assert.equal(second.sampleValid, true);
  assert.equal(second.actualMs, 30);
  assert.equal(second.targetMs, 20);
  assert.equal(second.minimumMs, 10);
});

test("counter reset starts a fresh latency baseline", () => {
  const previous = {
    emitted: 100,
    delay: 5,
    targetDelay: 4,
    minimumDelay: 2,
  };
  const reset = sampleAudioJitterBuffer(previous, {
    jitterBufferEmittedCount: 2,
    jitterBufferDelay: 0.04,
    jitterBufferTargetDelay: 0.04,
    jitterBufferMinimumDelay: 0.02,
  });
  assert.equal(reset.sampleValid, false);
});
