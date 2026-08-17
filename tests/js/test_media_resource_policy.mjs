import assert from "node:assert/strict";
import test from "node:test";

import {
  controlAvailability,
  isCurrentRtcGeneration,
  resolveRtcMode,
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
    "rtc.audio.full_duplex.v1",
    "rtc.video.mjpeg.v1",
  ],
  resourceOwners: {},
  localResources: new Set(),
  rtcActive: false,
  rtcMode: null,
};

test("audio RTC keeps camera capture available but owns both audio directions", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { microphone: "rtc_audio", speaker: "rtc_audio" },
    rtcActive: true,
    rtcMode: "audio",
  });

  assert.equal(availability.motion, true);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
  assert.equal(availability.camera, true);
  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
  assert.equal(availability.startRtcAudio, false);
  assert.equal(availability.startRtcVideo, false);
  assert.equal(availability.startRtcAv, false);
  assert.equal(availability.stopRtc, true);
});

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

test("combined RTC owns camera, microphone, and speaker", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { camera: "rtc_av", microphone: "rtc_av", speaker: "rtc_av" },
    rtcActive: true,
    rtcMode: "av",
  });

  assert.equal(availability.camera, false);
  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
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

test("an established audio RTC ignores its start marker and keeps camera available", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { microphone: "rtc_audio", speaker: "rtc_audio" },
    localResources: new Set(["media"]),
    rtcActive: true,
    rtcMode: "audio",
  });

  assert.equal(availability.camera, true);
  assert.equal(availability.speaker, false);
  assert.equal(availability.microphone, false);
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

test("combined RTC mode is represented by one audio-video session", () => {
  assert.equal(rtcModeHasAudio("audio"), true);
  assert.equal(rtcModeHasAudio("av"), true);
  assert.equal(rtcModeHasAudio("video"), false);
  assert.equal(rtcModeHasVideo("video"), true);
  assert.equal(rtcModeHasVideo("av"), true);
  assert.equal(rtcModeHasVideo("audio"), false);
});

test("an orphaned browser session recovers its RTC mode from server state", () => {
  assert.equal(resolveRtcMode(null, "av", "rtc_av", true), "av");
  assert.equal(resolveRtcMode(null, null, "live_video"), "video");
  assert.equal(resolveRtcMode(null, null, "rtc_audio"), "audio");
  assert.equal(resolveRtcMode("video", "av", "rtc_av", true), "video");
  assert.equal(resolveRtcMode(null, null, "motion_move"), null);
});

test("a stopped server snapshot cannot keep the browser media controls locked", () => {
  const mode = resolveRtcMode(null, "video", null, false);
  const availability = controlAvailability({
    ...connected,
    rtcActive: Boolean(mode),
  });

  assert.equal(mode, null);
  assert.equal(availability.startRtcAudio, true);
  assert.equal(availability.startRtcVideo, true);
  assert.equal(availability.startRtcAv, true);
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
