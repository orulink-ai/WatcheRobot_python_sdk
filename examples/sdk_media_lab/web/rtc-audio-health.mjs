export const RTC_AUDIO_VERIFY_TIMEOUT_MS = 8000;

export function evaluateRtcAudioHealth({
  peerConnected,
  browserTxPackets,
  browserRxPackets,
  deviceCaptureFrames,
  deviceTxPackets,
  deviceTxErrors,
  deviceCapturePeak,
  browserAudioLevel,
  browserPlaybackActive,
  elapsedMs,
}) {
  if (!peerConnected) return { state: "connecting", missing: [] };

  const missing = [];
  if (browserTxPackets <= 0) missing.push("browser_tx");
  if (deviceCaptureFrames <= 0) missing.push("device_capture");
  if (deviceTxPackets <= 0) missing.push("device_tx");
  if (browserRxPackets <= 0) missing.push("browser_rx");
  if (!Number.isFinite(deviceCapturePeak) || deviceCapturePeak < 32) missing.push("device_signal");
  if (!Number.isFinite(browserAudioLevel) || browserAudioLevel < 0.001) missing.push("browser_signal");
  if (!browserPlaybackActive) missing.push("browser_playback");

  if (missing.length > 0) {
    return {
      state: elapsedMs >= RTC_AUDIO_VERIFY_TIMEOUT_MS ? "failed" : "verifying",
      missing,
    };
  }
  if (deviceTxErrors > 0) return { state: "degraded", missing: [] };
  return { state: "healthy", missing: [] };
}
