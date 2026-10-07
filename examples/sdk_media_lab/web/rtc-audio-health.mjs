export const RTC_AUDIO_VERIFY_TIMEOUT_MS = 8000;

export function formatRtcPlaybackLevel(stats = {}) {
  const raw = stats.audio_voice_raw_rms;
  const output = stats.audio_voice_output_rms;
  const gain = stats.audio_voice_gain_db_x100;
  if (![raw, output, gain].every(Number.isFinite) || raw < 0 || output < 0) return "—";
  return `RMS ${Math.round(raw)} → ${Math.round(output)} · ${gain >= 0 ? "+" : ""}${(gain / 100).toFixed(1)} dB`;
}

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
  deviceRxPackets,
  deviceDecodedFrames,
  deviceRenderErrors,
  deviceTxDroppedFrames,
  deviceQueueDroppedFrames,
  deviceI2sBytes,
  devicePlaybackPeak,
  elapsedMs,
  previouslyVerified = false,
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
  if (deviceRxPackets <= 0) missing.push("device_rx");
  if (deviceDecodedFrames <= 0) missing.push("device_decode");
  if (deviceI2sBytes <= 0) missing.push("device_playback");
  if (devicePlaybackPeak < 32) missing.push("device_playback_signal");

  if (missing.length > 0) {
    const onlyQuietSignals = missing.every(name => ["device_signal", "browser_signal", "device_playback_signal"].includes(name));
    if (previouslyVerified && onlyQuietSignals) {
      return { state: deviceTxErrors > 0 || deviceRenderErrors > 0 || deviceTxDroppedFrames > 0
        || deviceQueueDroppedFrames > 0 ? "degraded" : "quiet", missing };
    }
    return {
      state: elapsedMs >= RTC_AUDIO_VERIFY_TIMEOUT_MS ? "failed" : "verifying",
      missing,
    };
  }
  if (deviceTxErrors > 0 || deviceRenderErrors > 0 || deviceTxDroppedFrames > 0
    || deviceQueueDroppedFrames > 0) return { state: "degraded", missing: [] };
  return { state: "healthy", missing: [] };
}
