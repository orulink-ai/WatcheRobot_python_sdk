export function frequencyBandRanges(bufferLength, bands, loPass, hiPass) {
  if (!Number.isInteger(bands) || bands <= 0 || !Number.isInteger(bufferLength) || bufferLength < 0 ||
      !Number.isFinite(loPass) || !Number.isFinite(hiPass)) {
    throw new RangeError('Invalid FFT band bounds');
  }
  const start = Math.max(0, Math.min(bufferLength, Math.floor(loPass)));
  const end = Math.max(start, Math.min(bufferLength, Math.floor(hiPass)));
  const length = end - start;
  return Array.from({ length: bands }, (_, i) =>
    [start + Math.floor(i * length / bands), start + Math.floor((i + 1) * length / bands)]);
}

export function createAudioAnalyser(mediaStream, options = {}, Context = globalThis.AudioContext || globalThis.webkitAudioContext) {
  const audioContext = new Context();
  let source, analyser, closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    // These nodes are owned here; the MediaStream and its tracks are borrowed.
    try { source?.disconnect(); } catch {}
    try { analyser?.disconnect(); } catch {}
    try { Promise.resolve(audioContext.close()).catch(() => {}); } catch {}
  };
  try {
    source = audioContext.createMediaStreamSource(mediaStream);
    analyser = audioContext.createAnalyser();
    if (options.fftSize) analyser.fftSize = options.fftSize;
    if (options.smoothingTimeConstant !== undefined) analyser.smoothingTimeConstant = options.smoothingTimeConstant;
    if (options.minDecibels !== undefined) analyser.minDecibels = options.minDecibels;
    if (options.maxDecibels !== undefined) analyser.maxDecibels = options.maxDecibels;
    source.connect(analyser);
    return { analyser, audioContext, cleanup };
  } catch (error) {
    cleanup();
    throw error;
  }
}
