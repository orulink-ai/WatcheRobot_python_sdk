import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { createRtcMicrophoneConstraints } from "../../examples/sdk_media_lab/web/rtc-audio-capture.mjs";

test("normal calls request echo cancellation and microphone level adaptation", () => {
  const constraints = createRtcMicrophoneConstraints();

  assert.equal(constraints.echoCancellation, true);
  assert.equal(constraints.noiseSuppression, true);
  assert.equal(constraints.autoGainControl, true);
  assert.deepEqual(constraints.channelCount, { ideal: 1 });
  assert.deepEqual(constraints.sampleRate, { ideal: 48000 });
  assert.deepEqual(constraints.latency, { ideal: 0.01 });
});

test("processed calls adapt quiet microphone levels", () => {
  const constraints = createRtcMicrophoneConstraints({ browserProcessing: true });

  assert.equal(constraints.echoCancellation, true);
  assert.equal(constraints.noiseSuppression, true);
  assert.equal(constraints.autoGainControl, true);
});

test("raw microphone capture requires an explicit diagnostic choice", () => {
  const constraints = createRtcMicrophoneConstraints({ browserProcessing: false });
  assert.equal(constraints.echoCancellation, false);
  assert.equal(constraints.noiseSuppression, false);
  assert.equal(constraints.autoGainControl, false);
});

const appSource = readFileSync(new URL("../../examples/sdk_media_lab/web/app.js", import.meta.url), "utf8");
const profileFunction = appSource.match(/function rtcBrowserAudioProcessingEnabled\(\) \{[\s\S]*?\n\}/)?.[0];
assert.ok(profileFunction, "production call profile selector must exist");
for (const [search, expected] of [
  ["", true], ["?rtc_audio_processing=1", true],
  ["?rtc_audio_processing=0", false], ["?rtc_audio_processing=invalid", true],
]) {
  test(`call profile selects echo cancellation for ${search || "the ordinary page"}`, () => {
    const context = { window: { location: { search } }, URLSearchParams };
    assert.equal(vm.runInNewContext(`${profileFunction}\nrtcBrowserAudioProcessingEnabled()`, context), expected);
  });
}
