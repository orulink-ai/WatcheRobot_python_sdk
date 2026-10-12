export function microphoneProcessingStatus(settings = {}) {
  return Object.fromEntries(['echoCancellation', 'noiseSuppression', 'autoGainControl'].map(key =>
    [key, settings[key] === true ? 'active' : settings[key] === false ? 'inactive' : 'unreported']));
}

export async function createRtcNoisePlayback(remoteStream, {
  enabled = true, strength = 70, isCurrent = () => true, onState = () => {}, onFallback = () => {}, signal,
} = {}, {
  createContext = () => new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' }),
  createNode = context => new AudioWorkletNode(context, 'robot-noise-suppression', {
    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    processorOptions: { strength: enabled ? strength : 0 },
  }),
  createSink = () => new Audio(),
} = {}) {
  let context, source, destination, node, sink, disposed = false, frames = 0, rejectReady;
  let inputRms = 0, outputRms = 0;
  const current = () => !disposed && !signal?.aborted && isCurrent();
  const options = { enabled, strength: Math.max(0, Math.min(85, Number(strength) || 0)) };
  const publish = state => {
    if (current()) onState({ state, ...options, frames, inputRms, outputRms, delayMs: 20, algorithm: 'RNNoise' });
  };
  async function dispose() {
    if (disposed) return;
    disposed = true;
    rejectReady?.(new Error('Noise suppression cancelled'));
    signal?.removeEventListener('abort', dispose);
    try { node?.port.postMessage({ type: 'dispose' }); } catch (_) {}
    for (const item of [source, node, destination]) { try { item?.disconnect(); } catch (_) {} }
    for (const track of destination?.stream.getTracks() || []) track.stop();
    try { node?.port.close(); } catch (_) {}
    if (sink) { sink.pause(); sink.srcObject = null; }
    try { await context?.close(); } catch (_) {}
  }
  async function fallback() {
    if (!current()) return;
    publish('unavailable'); onFallback(); await dispose();
  }
  try {
    if (!current()) return null;
    publish('loading'); context = createContext();
    signal?.addEventListener('abort', dispose, { once: true });
    if (context.sampleRate !== 48000 || !context.audioWorklet) throw new Error('AudioWorklet unavailable');
    await context.audioWorklet.addModule('/assets/rtc-noise-worklet.mjs');
    if (!current()) { await dispose(); return null; }
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Noise suppression startup timeout')), 5000);
      rejectReady = error => { clearTimeout(timer); reject(error); };
      try {
        node = createNode(context);
        node.onprocessorerror = () => { rejectReady(new Error('Noise processor failed')); fallback(); };
        node.port.onmessage = ({ data }) => {
          if (data?.state === 'ready') { clearTimeout(timer); resolve(); }
          else if (data?.state === 'unavailable') {
            rejectReady(new Error('Noise processor unavailable')); fallback();
          } else if (data?.state === 'processing') {
            frames = data.frames; inputRms = data.inputRms || 0; outputRms = data.outputRms || 0;
            publish(options.enabled ? 'active' : 'bypass');
          }
        };
      } catch (error) { clearTimeout(timer); reject(error); }
    });
    if (!current()) { await dispose(); return null; }
    source = context.createMediaStreamSource(remoteStream);
    destination = context.createMediaStreamDestination();
    // A muted RTC consumer keeps Chromium's receiver playout clock running.
    sink = createSink(); sink.muted = true; sink.srcObject = remoteStream;
    sink.play().catch(() => {});
    source.connect(node); node.connect(destination);
    await context.resume();
    if (!current()) { await dispose(); return null; }
    publish(context.state === 'suspended' ? 'suspended' : options.enabled ? 'active' : 'bypass');
    return {
      stream: destination.stream, dispose,
      async resume() { if (current()) await context.resume(); },
      setOptions({ enabled, strength }) {
        if (!current()) return;
        options.enabled = enabled === true;
        options.strength = Math.max(0, Math.min(85, Number(strength) || 0));
        node.port.postMessage({ type: 'options', ...options });
        publish(options.enabled ? 'active' : 'bypass');
      },
    };
  } catch (_) {
    publish('unavailable'); await dispose(); return null;
  }
}
