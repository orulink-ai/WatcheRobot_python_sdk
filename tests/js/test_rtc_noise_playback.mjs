import assert from 'node:assert/strict';
import test from 'node:test';
import { createRtcNoisePlayback, microphoneProcessingStatus } from '../../examples/sdk_media_lab/web/rtc-noise-playback.mjs';

function fixture() {
  let current = true, closes = 0, ownedStops = 0, sourceStops = 0;
  const sourceStream = { getTracks: () => [{ stop: () => sourceStops++ }] };
  const ownedStream = { getTracks: () => [{ stop: () => ownedStops++ }] };
  const node = { connect() {}, disconnect() {}, port: { postMessage() {}, close() {} } };
  const context = { sampleRate: 48000, state: 'running', audioWorklet: { addModule: async () => {} },
    createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
    createMediaStreamDestination: () => ({ stream: ownedStream, disconnect() {} }),
    resume: async () => {}, close: async () => { closes++; } };
  const deps = { createContext: () => context, createSink: () => ({ play: async () => {}, pause() {} }), createNode: () => {
    queueMicrotask(() => node.port.onmessage?.({ data: { state: 'ready' } })); return node;
  } };
  return { sourceStream, ownedStream, node, context, deps, current: () => current,
    cancel: () => { current = false; }, counts: () => ({ closes, ownedStops, sourceStops }) };
}

test('processed playback owns only its output and disposes once without stopping RTC tracks', async () => {
  const f = fixture(), states = [];
  const playback = await createRtcNoisePlayback(f.sourceStream, { isCurrent: f.current, onState: s => states.push(s) }, f.deps);
  assert.equal(playback.stream, f.ownedStream);
  assert.equal(states.at(-1).state, 'active');
  playback.setOptions({ enabled: false, strength: 70 });
  assert.equal(states.at(-1).state, 'bypass');
  await playback.dispose(); await playback.dispose();
  assert.deepEqual(f.counts(), { closes: 1, ownedStops: 1, sourceStops: 0 });
});

test('call cancellation during worklet load releases the context and never connects a late graph', async () => {
  const f = fixture(); let finish;
  f.context.audioWorklet.addModule = () => new Promise(resolve => { finish = resolve; });
  const pending = createRtcNoisePlayback(f.sourceStream, { isCurrent: f.current }, f.deps);
  f.cancel(); finish();
  assert.equal(await pending, null);
  assert.deepEqual(f.counts(), { closes: 1, ownedStops: 0, sourceStops: 0 });
});

test('load failure falls back explicitly without leaking context or touching remote tracks', async () => {
  const f = fixture(), states = [];
  f.context.audioWorklet.addModule = async () => { throw new Error('load failed'); };
  assert.equal(await createRtcNoisePlayback(f.sourceStream, { isCurrent: f.current, onState: s => states.push(s) }, f.deps), null);
  assert.equal(states.at(-1).state, 'unavailable');
  assert.equal(f.counts().closes, 1); assert.equal(f.counts().sourceStops, 0);
});

test('actual browser processing status keeps missing and disabled settings distinct', () => {
  assert.deepEqual(microphoneProcessingStatus({ echoCancellation: true, noiseSuppression: false }),
    { echoCancellation: 'active', noiseSuppression: 'inactive', autoGainControl: 'unreported' });
});
