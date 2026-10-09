import test from 'node:test';
import assert from 'node:assert/strict';
import { createRtcTestSpeech } from '../../examples/sdk_media_lab/web/rtc-test-speech.mjs';

function fakeContext() {
  const events = [];
  const stream = { getTracks: () => [{ stop: () => events.push('track.stop') }] };
  const node = (name) => ({ connect: (to) => events.push([name, to]), disconnect: () => events.push(name + '.disconnect') });
  const destination = { ...node('destination'), stream };
  const gain = { ...node('gain'), gain: { value: 1 } };
  const source = { ...node('source'), start: () => events.push('start'), stop: () => events.push('stop') };
  const context = {
    events, destination: {}, source, gain, output: destination, sampleRate: 10,
    resume: async () => {}, close: async () => events.push('close'),
    createMediaStreamDestination: () => destination, createGain: () => gain,
    createBufferSource: () => source,
    decodeAudioData: async () => ({ length: 3, numberOfChannels: 1, sampleRate: 10, getChannelData: () => new Float32Array([.5, -.25, 0]) }),
    createBuffer: (_, length, sampleRate) => ({ length, sampleRate, data: new Float32Array(length), getChannelData() { return this.data; } }),
  };
  return context;
}
const fetchOk = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) });

test('quiet speech has an eight second silent gap and never connects to local speakers', async () => {
  const ctx = fakeContext();
  const result = await createRtcTestSpeech({ createContext: () => ctx, fetchAudio: fetchOk });
  assert.equal(result.stream, ctx.output.stream);
  assert.equal(ctx.source.loop, true);
  assert.equal(ctx.source.buffer.length, 83);
  assert.equal(ctx.gain.gain.value, .12); // Fixture peak .5 -> maximum .06.
  assert.equal(ctx.source.buffer.data[0], .5);
  assert.equal(ctx.source.buffer.data[3], 0);
  assert.equal(ctx.events.some(e => Array.isArray(e) && e[1] === ctx.destination), false);
  await result.dispose(); await result.dispose();
  assert.equal(ctx.events.filter(e => e === 'close').length, 1);
  assert.equal(ctx.events.filter(e => e === 'track.stop').length, 1);
});
test('missing fixture closes the context and stops destination tracks', async () => {
  const ctx = fakeContext();
  await assert.rejects(createRtcTestSpeech({ createContext: () => ctx, fetchAudio: async () => ({ ok: false }) }), /speech fixture/);
  assert.equal(ctx.events.includes('start'), false);
  assert.ok(ctx.events.includes('track.stop'));
  assert.ok(ctx.events.includes('close'));
});
test('resume failure also closes the context', async () => {
  const ctx = fakeContext(); ctx.resume = async () => { throw new Error('resume failed'); };
  await assert.rejects(createRtcTestSpeech({ createContext: () => ctx, fetchAudio: fetchOk }), /resume failed/);
  assert.ok(ctx.events.includes('close'));
});
