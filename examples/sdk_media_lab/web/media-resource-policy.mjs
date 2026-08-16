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
}) {
  const capabilitySet = new Set(capabilities || []);
  const localSet = localResources instanceof Set ? localResources : new Set(localResources || []);
  const resourceReady = (resource) => Boolean(connected)
    && !resourceOwners?.[resource]
    && !localSet.has(resource);

  const mediaReady = resourceReady("media");
  return {
    motion: resourceReady("motion") && capabilitySet.has("motion"),
    light: resourceReady("light") && capabilitySet.has("light"),
    animation: resourceReady("animation") && capabilitySet.has("animation"),
    standaloneMedia: mediaReady && !rtcActive,
    startRtc: mediaReady && !rtcActive,
    stopRtc: Boolean(connected) && Boolean(rtcActive),
  };
}
