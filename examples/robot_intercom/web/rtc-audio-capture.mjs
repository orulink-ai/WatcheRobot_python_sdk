export function createRtcMicrophoneConstraints({ browserProcessing = true } = {}) {
  const processed = browserProcessing === true;
  return {
    echoCancellation: processed,
    noiseSuppression: processed,
    // Ordinary calls adapt variable microphone levels; raw diagnostics opt out.
    autoGainControl: processed,
    channelCount: { ideal: 1 },
    sampleRate: { ideal: 48000 },
    latency: { ideal: 0.01 },
  };
}
