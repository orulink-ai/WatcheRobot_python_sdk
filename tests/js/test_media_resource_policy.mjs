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
};

test("RTC media ownership only disables other media actions", () => {
  const availability = controlAvailability({
    ...connected,
    resourceOwners: { media: "rtc_av" },
    rtcActive: true,
  });

  assert.equal(availability.motion, true);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
  assert.equal(availability.standaloneMedia, false);
  assert.equal(availability.startRtc, false);
  assert.equal(availability.stopRtc, true);
});

test("a pending local RTC start reserves media without blocking actuators", () => {
  const availability = controlAvailability({
    ...connected,
    rtcActive: true,
  });

  assert.equal(availability.standaloneMedia, false);
  assert.equal(availability.motion, true);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
});

test("a local motion request does not disable lights or media", () => {
  const availability = controlAvailability({
    ...connected,
    localResources: new Set(["motion"]),
  });

  assert.equal(availability.motion, false);
  assert.equal(availability.light, true);
  assert.equal(availability.animation, true);
  assert.equal(availability.standaloneMedia, true);
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
  assert.equal(availability.standaloneMedia, false);
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
  assert.equal(availability.startRtc, true);
  assert.equal(availability.standaloneMedia, true);
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
