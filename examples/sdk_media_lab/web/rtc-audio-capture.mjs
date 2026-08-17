export function createRtcMicrophoneConstraints({ browserProcessing = false } = {}) {
  const processed = browserProcessing === true;
  return {
    echoCancellation: processed,
    noiseSuppression: processed,
    autoGainControl: processed,
    channelCount: { ideal: 1 },
    sampleRate: { ideal: 48000 },
    latency: { ideal: 0.01 },
  };
}
