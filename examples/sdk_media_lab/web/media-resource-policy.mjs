export function rtcModeHasAudio(mode) {
  return mode === "audio" || mode === "av";
}

export function rtcModeHasVideo(mode) {
  return mode === "video" || mode === "av";
}

export function isCurrentRtcGeneration(currentGeneration, expectedGeneration) {
  return Number.isInteger(expectedGeneration) && currentGeneration === expectedGeneration;
}

export function resolveRtcMode(localMode, remoteMode, mediaOwner, remoteActive = false) {
  const validModes = new Set(["audio", "video", "av"]);
  if (validModes.has(localMode)) return localMode;
  if (mediaOwner === "rtc_av") return "av";
  if (mediaOwner === "rtc_audio") return "audio";
  if (mediaOwner === "live_video") return "video";
  if (remoteActive && validModes.has(remoteMode)) return remoteMode;
  return null;
}

export function controlAvailability({
  connected,
  capabilities = [],
  resourceOwners = {},
  localResources = new Set(),
  rtcActive = false,
  rtcMode = null,
}) {
  const capabilitySet = new Set(capabilities || []);
  const localSet = localResources instanceof Set ? localResources : new Set(localResources || []);
  const resourceReady = (resource) => Boolean(connected)
    && !resourceOwners?.[resource]
    && !localSet.has(resource);

  // The browser keeps the media marker for the whole local peer lifetime so
  // stop/cleanup stays idempotent. It is an exclusive reservation only while
  // the RTC start is pending; after the server confirms an active mode, the
  // per-resource owners below become the source of truth.
  const pendingRtc = localSet.has("media") && !rtcActive;
  const cameraReady = resourceReady("camera") && !pendingRtc;
  const microphoneReady = resourceReady("microphone") && !pendingRtc;
  const speakerReady = resourceReady("speaker") && !pendingRtc;
  const cameraAvailable = cameraReady && !rtcModeHasVideo(rtcMode);
  const microphoneAvailable = microphoneReady && !rtcModeHasAudio(rtcMode);
  const speakerAvailable = speakerReady && !rtcModeHasAudio(rtcMode);
  return {
    motion: resourceReady("motion") && capabilitySet.has("motion"),
    light: resourceReady("light") && capabilitySet.has("light"),
    animation: resourceReady("animation") && capabilitySet.has("animation"),
    camera: cameraAvailable,
    microphone: microphoneAvailable,
    speaker: speakerAvailable,
    startRtcAudio: !rtcActive && microphoneReady && speakerReady,
    startRtcVideo: !rtcActive && cameraReady,
    startRtcAv: !rtcActive && cameraReady && microphoneReady && speakerReady,
    stopRtc: Boolean(connected) && Boolean(rtcActive),
  };
}
