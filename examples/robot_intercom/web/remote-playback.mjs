import { createRtcNoisePlayback } from './rtc-noise-playback.mjs';

// Each selection owns its async graph. Raw playback has no denoising graph.
export function createRemotePlayback({ play, process = createRtcNoisePlayback, onState = () => {},
  createSink = () => new Audio() }) {
  let generation = 0, source = null, processor = null, abort = null;
  let profile = 'natural', enabledSelection = false;
  let clockSink = null;
  async function setEnabled(enabled) {
    enabledSelection = enabled === true;
    const token = ++generation;
    abort?.abort(); abort = null;
    const old = processor; processor = null;
    // Switch the consumer before releasing the previous destination tracks.
    if (source) await play(source);
    await old?.dispose();
    if (token !== generation || !source) return;
    onState({ mode: 'raw', algorithmMs: 0 });
    if (!enabled) return;
    const current = () => token === generation && source !== null;
    const cancellation = (abort = new AbortController());
    const processed = await process(source, { enabled: true, strength: profile === 'speech' ? 85 : 70,
      voiceFocus: profile === 'speech',
      keepReceiverClock: false,
      signal: cancellation.signal, isCurrent: current,
      onFallback: () => { if (current()) { play(source); onState({ mode: 'raw', algorithmMs: 0 }); } },
    });
    if (!current()) { await processed?.dispose(); return; }
    processor = processed;
    if (processed) {
      await play(processed.stream);
      if (current()) onState({ mode: 'denoised', profile, algorithmMs: 20 });
    }
  }
  return {
    start(stream, enabled) {
      if (!clockSink) { clockSink = createSink(); clockSink.muted = true; }
      clockSink.srcObject = stream;
      clockSink.play().catch(() => {});
      source = stream; return setEnabled(enabled);
    },
    setEnabled,
    setProfile(value) {
      if (!['natural', 'speech'].includes(value)) throw new RangeError('Invalid playback profile');
      profile = value; return setEnabled(enabledSelection);
    },
    resume: () => processor?.resume(),
    dispose() {
      ++generation; source = null; abort?.abort(); abort = null;
      processor?.dispose(); processor = null;
      if (clockSink) { clockSink.pause(); clockSink.srcObject = null; clockSink = null; }
    },
  };
}
