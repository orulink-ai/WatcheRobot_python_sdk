import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceEffect } from '../../examples/robot_intercom/web/voice-effect-core.mjs';
import { createRemotePlayback } from '../../examples/robot_intercom/web/remote-playback.mjs';
import { createVoiceInput } from '../../examples/robot_intercom/web/voice-input.mjs';
const createSink = () => ({ play: async () => {}, pause() {} });

test('raw playback skips processor entirely and stale loads cannot replace it', async () => {
  const plays = [], states = []; let resolve, loads = 0, disposals = 0;
  const raw = {};
  const playback = createRemotePlayback({ createSink, play: s => plays.push(s), onState: s => states.push(s),
    process: () => { loads++; return new Promise(r => { resolve = r; }); } });
  await playback.start(raw, false);
  assert.equal(loads, 0); assert.equal(plays.at(-1), raw);
  const pending = playback.setEnabled(true);
  await new Promise(r => setImmediate(r));
  await playback.setEnabled(false);
  resolve({ stream: {}, dispose: () => disposals++ }); await pending;
  assert.equal(plays.at(-1), raw); assert.equal(disposals, 1);
  assert.equal(states.at(-1).mode, 'raw');
  playback.dispose();
});

test('stop during processor load never plays a late processed stream', async () => {
  let resolve, disposed = 0; const plays = [];
  const playback = createRemotePlayback({ createSink, play: s => plays.push(s),
    process: () => new Promise(r => { resolve = r; }) });
  const pending = playback.start({}, true);
  await new Promise(r => setImmediate(r)); playback.dispose();
  resolve({ stream: {}, dispose: () => disposed++ }); await pending;
  assert.equal(plays.length, 1); assert.equal(disposed, 1);
});

function frequency(mode) {
  // Count steady-state crossings after warmup; output sample count is fixed.
  const core = new VoiceEffect(48000); core.setMode(mode);
  let previous = 0, crossings = 0;
  for (let block = 0; block < 750; block++) {
    const input = Float32Array.from({ length: 128 }, (_, i) => Math.sin(2 * Math.PI * 400 * (block * 128 + i) / 48000));
    const output = new Float32Array(128); core.process(input, output);
    for (const sample of output) {
      assert.ok(Number.isFinite(sample) && Math.abs(sample) <= 1.001);
      if (block > 374 && previous <= 0 && sample > 0) crossings++;
      previous = sample;
    }
  }
  return crossings;
}

test('playback clock stays attached across switches and stops only at teardown', async () => {
  let created = 0, pauses = 0, releases = 0;
  const raw = {}, sink = { play: async () => {}, pause: () => pauses++ };
  const playback = createRemotePlayback({ play: () => {}, createSink: () => { created++; return sink; },
    process: async (_, options) => {
      assert.equal(options.keepReceiverClock, false);
      return { stream: {}, dispose: () => releases++ };
    } });
  await playback.start(raw, true); await playback.setEnabled(false); await playback.setEnabled(true);
  assert.equal(created, 1); assert.equal(pauses, 0); assert.equal(sink.srcObject, raw);
  playback.dispose(); playback.dispose();
  assert.equal(pauses, 1); assert.equal(sink.srcObject, null); assert.equal(releases, 2);
});
test('pitch effects change pitch while output duration and ring storage stay fixed', () => {
  assert.ok(frequency('deep') < 365);
  assert.ok(frequency('bright') > 435);
  const core = new VoiceEffect(48000); const storage = core.ring;
  for (let i = 0; i < 10000; i++) core.process(new Float32Array(128), new Float32Array(128));
  assert.equal(core.ring, storage);
  assert.ok(storage.length <= 8192);
  assert.throws(() => core.setMode('unknown'), RangeError);
});
test('original DSP is sample-exact and robot modulation remains bounded', () => {
  const core = new VoiceEffect(48000), input = new Float32Array([0.2, -0.6, 0, 0.8]), output = new Float32Array(4);
  core.process(input, output); assert.deepEqual(output, input);
  core.setMode('robot'); core.process(input, output);
  assert.ok(output.every((x, i) => Math.abs(x) <= Math.abs(input[i]) + 1e-6));
});

test('voice graph bypasses effects for original, bounds gain and owns only generated tracks', async () => {
  let stopped = 0, closed = 0; const connections = [];
  const source = { connect: x => connections.push(x), disconnect() {} };
  const gain = { gain: { value: 1, setTargetAtTime(v) { this.value = v; } }, connect() {}, disconnect() {} };
  const worklet = { connect() {}, disconnect() {}, port: { postMessage() {}, close() {} } };
  const output = { getTracks: () => [{ stop: () => stopped++ }], getAudioTracks: () => ['processed-track'] };
  const context = { currentTime: 0, state: 'running', audioWorklet: { addModule: async () => {} },
    createMediaStreamSource: () => source, createGain: () => gain,
    createMediaStreamDestination: () => ({ stream: output, disconnect() {} }), resume: async () => {}, close: async () => closed++ };
  const raw = { getTracks: () => [{ stop() { throw Error('source owned by controller'); } }], getAudioTracks: () => ['raw-track'] };
  const voice = await createVoiceInput(raw, {}, { createContext: () => context, createNode: () => worklet });
  assert.equal(connections.at(-1), gain);
  assert.equal(voice.stream, raw);
  const replaced = []; voice.attachSender({ replaceTrack: async track => replaced.push(track) });
  await voice.setVolume(0); assert.equal(gain.gain.value, 0); assert.equal(replaced.at(-1), 'processed-track');
  await voice.setVolume(120); assert.equal(gain.gain.value, 1); assert.equal(replaced.at(-1), 'raw-track');
  assert.throws(() => voice.setVolume(NaN), RangeError);
  await voice.setMode('deep'); assert.equal(connections.at(-1), worklet); assert.equal(replaced.at(-1), 'processed-track');
  await voice.setMode('original'); assert.equal(connections.at(-1), gain); assert.equal(replaced.at(-1), 'raw-track');
  await voice.dispose(); await voice.dispose();
  assert.equal(stopped, 1); assert.equal(closed, 1);
});
