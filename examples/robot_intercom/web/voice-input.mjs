import { VOICE_MODES } from './voice-effect-core.mjs';
export async function createVoiceInput(raw, { volume = 100, mode = 'original', isCurrent = () => true,
  onState = () => {}, signal } = {}, {
  createContext = () => new AudioContext({ latencyHint: 'interactive' }),
  createNode = context => new AudioWorkletNode(context, 'intercom-voice-effect', {
    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: 'explicit',
  }),
} = {}) {
  let context, source, gain, destination, effect, disposed = false;
  let selected = 'original', percent = 100;
  let sender = null, replacing = Promise.resolve();
  const dry = () => selected === 'original' && percent === 100;
  const activeStream = () => dry() ? raw : destination.stream;
  const current = () => !disposed && !signal?.aborted && isCurrent();
  const publish = () => onState({ mode: selected, volume: percent,
    baseMs: dry() ? 0 : Number.isFinite(context?.baseLatency) ? Math.round(context.baseLatency * 1000) : null,
    captureMs: Number.isFinite(raw.getAudioTracks?.()[0]?.getSettings?.().latency)
      ? Math.round(raw.getAudioTracks()[0].getSettings().latency * 1000) : null,
    effectAvailable: !!effect });
  async function dispose() {
    if (disposed) return; disposed = true;
    signal?.removeEventListener('abort', dispose);
    sender = null;
    try { effect?.port.postMessage({ type: 'dispose' }); } catch {}
    for (const item of [source, effect, gain, destination]) { try { item?.disconnect(); } catch {} }
    try { effect?.port.close(); } catch {}
    for (const track of destination?.stream.getTracks() || []) track.stop();
    try { await context?.close(); } catch {}
  }
  function replace() {
    if (!sender) return Promise.resolve();
    const owner = sender;
    // Resolve the latest selection when each replacement executes. Rapid UI
    // changes cannot leave an older track attached after the final selection.
    replacing = replacing.catch(() => {}).then(async () => {
      if (current() && sender === owner) await owner.replaceTrack(activeStream().getAudioTracks()[0]);
    });
    return replacing;
  }
  function setVolume(value) {
    if (!Number.isFinite(value)) throw new RangeError('Invalid volume');
    if (!current()) return;
    percent = Math.max(0, Math.min(100, value));
    gain.gain.setTargetAtTime(percent / 100, context.currentTime, 0.01); publish(); return replace();
  }
  function setMode(value) {
    if (!VOICE_MODES.includes(value)) throw new RangeError('Invalid voice mode');
    if (!current()) return;
    if (value !== 'original' && !effect) throw new Error('Voice effects unavailable');
    selected = value; source.disconnect(); effect?.disconnect();
    // Native dry route has no pitch-shift delay or worklet buffering.
    if (selected === 'original') source.connect(gain);
    else { effect.port.postMessage({ type: 'mode', mode: selected }); source.connect(effect); effect.connect(gain); }
    publish(); return replace();
  }
  try {
    context = createContext();
    signal?.addEventListener('abort', dispose, { once: true });
    source = context.createMediaStreamSource(raw);
    gain = context.createGain(); destination = context.createMediaStreamDestination();
    gain.connect(destination); source.connect(gain);
    try {
      let deadline;
      try {
        await Promise.race([context.audioWorklet.addModule('/assets/voice-effect-worklet.mjs'),
          new Promise((_, reject) => { deadline = setTimeout(() => reject(Error('Voice effect load timeout')), 5000); })]);
      } finally { clearTimeout(deadline); }
      if (current()) effect = createNode(context);
    } catch { /* Original + volume still work if effects are unsupported. */ }
    if (!current()) { await dispose(); return null; }
    if (effect) effect.onprocessorerror = () => {
      if (current()) {
        const failed = effect;
        setMode('original').catch(() => {});
        failed.port.close(); effect = null; publish();
      }
    };
    setVolume(volume); setMode(effect ? mode : 'original'); await context.resume();
    if (!current()) { await dispose(); return null; }
    return { get stream() { return activeStream(); }, setVolume, setMode, dispose,
      attachSender(value) { sender = value; },
      setMuted(muted) { for (const track of destination.stream.getAudioTracks()) track.enabled = !muted; },
      resume: () => context.resume() };
  } catch (error) { await dispose(); throw error; }
}
