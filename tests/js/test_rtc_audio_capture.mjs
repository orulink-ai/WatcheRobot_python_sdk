import test from "node:test";
import assert from "node:assert/strict";

import { createRtcMicrophoneConstraints } from "../../examples/sdk_media_lab/web/rtc-audio-capture.mjs";

test("SDK media lab captures unprocessed microphone audio by default", () => {
  const constraints = createRtcMicrophoneConstraints();

  assert.equal(constraints.echoCancellation, false);
  assert.equal(constraints.noiseSuppression, false);
  assert.equal(constraints.autoGainControl, false);
  assert.deepEqual(constraints.channelCount, { ideal: 1 });
  assert.deepEqual(constraints.sampleRate, { ideal: 48000 });
  assert.deepEqual(constraints.latency, { ideal: 0.01 });
});

test("browser speech processing remains an explicit opt-in profile", () => {
  const constraints = createRtcMicrophoneConstraints({ browserProcessing: true });

  assert.equal(constraints.echoCancellation, true);
  assert.equal(constraints.noiseSuppression, true);
  assert.equal(constraints.autoGainControl, true);
});
