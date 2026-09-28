import assert from "node:assert/strict";
import test from "node:test";

import {
  controlAvailability,
  isCurrentRtcGeneration,
  resolveRtcMode,
  rtcTransportPlan,
  rtcModeHasAudio,
  rtcModeHasVideo,
} from "../../examples/sdk_media_lab/web/media-resource-policy.mjs";

const connected = {
  connected: true,
  capabilities: [
    "motion",
    "light",
    "animation",
    "audio.stream",
    "microphone",
    "camera.capture",
    "rtc.video.mjpeg.v1",
  ],
  resourceOwners: {},
  localResources: new Set(),
  rtcActive: false,
  rtcMode: null,
};

test("video RTC keeps standalone speaker and microphone actions available", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { camera: "live_video" },
    rtcActive: true,
    rtcMode: "video",
  });

  assert.equal(availability.camera, false);
  assert.equal(availability.speaker, true);
  assert.equal(availability.microphone, true);
  assert.equal(availability.animation, true);
});

test("standalone speaker playback reserves both ordinary audio directions", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { speaker: "play_audio" },
  });

  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
  assert.equal(availability.camera, true);
});

test("pending standalone microphone recording reserves both ordinary audio directions", () => {
  const availability = controlAvailability({
    ...connected,
    localResources: new Set(["microphone"]),
  });

  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
  assert.equal(availability.camera, true);
});

test("a pending local RTC start reserves media without blocking actuators", () => {
  const availability = controlAvailability({
    ...connected,
    localResources: new Set(["media"]),
  });

  assert.equal(availability.camera, false);
  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
  assert.equal(availability.motion, true);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
});

test("an established video RTC ignores its start marker and keeps audio available", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { camera: "live_video" },
    localResources: new Set(["media"]),
    rtcActive: true,
    rtcMode: "video",
  });

  assert.equal(availability.camera, false);
  assert.equal(availability.speaker, true);
  assert.equal(availability.microphone, true);
});

test("a local motion request does not disable lights or media", () => {
  const availability = controlAvailability({
    ...connected,
    localResources: new Set(["motion"]),
  });

  assert.equal(availability.motion, false);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
  assert.equal(availability.camera, true);
  assert.equal(availability.speaker, true);
  assert.equal(availability.microphone, true);
});

test("offline state disables starts but preserves interrupt buttons", () => {
  const availability = controlAvailability({
    ...connected,
    connected: false,
    rtcActive: true,
  });

  assert.equal(availability.motion, false);
  assert.equal(availability.light, false);
  assert.equal(availability.animation, false);
  assert.equal(availability.camera, false);
  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
  assert.equal(availability.stopRtc, false);
});

test("video-only RTC mode helpers reject audio modes", () => {
  assert.equal(rtcModeHasAudio("audio"), false);
  assert.equal(rtcModeHasAudio("av"), false);
  assert.equal(rtcModeHasAudio("video"), false);
  assert.equal(rtcModeHasVideo("video"), true);
  assert.equal(rtcModeHasVideo("av"), false);
  assert.equal(rtcModeHasVideo("audio"), false);
});

test("an orphaned browser session recovers video RTC mode from server state", () => {
  assert.equal(resolveRtcMode(null, null, "live_video"), "video");
  assert.equal(resolveRtcMode("video", "video", "live_video", true), "video");
  assert.equal(resolveRtcMode(null, null, "motion_move"), null);
  assert.equal(resolveRtcMode(null, "audio", "rtc_audio", true), null);
});

test("a stopped server snapshot cannot keep the browser media controls locked", () => {
  const mode = resolveRtcMode(null, "video", null, false);
  const availability = controlAvailability({
    ...connected,
    rtcActive: Boolean(mode),
  });

  assert.equal(mode, null);
  assert.equal(availability.startRtcVideo, true);
  assert.equal(availability.camera, true);
  assert.equal(availability.speaker, true);
  assert.equal(availability.microphone, true);
  assert.equal(availability.motion, true);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
  assert.equal(availability.stopRtc, false);
});

test("an active server snapshot can restore RTC mode without a browser-local peer", () => {
  assert.equal(resolveRtcMode(null, "video", null, true), "video");
});

test("late events from a previous browser RTC generation are rejected", () => {
  assert.equal(isCurrentRtcGeneration(4, 4), true);
  assert.equal(isCurrentRtcGeneration(5, 4), false);
  assert.equal(isCurrentRtcGeneration(4, null), false);
});

test("LAN preview uses JPEG socket without a peer; audio modes are rejected", () => {
  assert.deepEqual(rtcTransportPlan("video"), { peer: false, jpegSocket: true });
  assert.throws(() => rtcTransportPlan("audio"), /mode/);
  assert.throws(() => rtcTransportPlan("av"), /mode/);
  assert.throws(() => rtcTransportPlan("invalid"), /mode/);
});

test("the browser RTC policy exposes video only", () => {
  assert.equal(resolveRtcMode(null, "audio", "rtc_audio", true), null);
  assert.equal(resolveRtcMode(null, "av", "rtc_av", true), null);
  assert.deepEqual(rtcTransportPlan("video"), { peer: false, jpegSocket: true });
});
