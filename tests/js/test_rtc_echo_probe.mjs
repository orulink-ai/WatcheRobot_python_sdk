import test from 'node:test';
import assert from 'node:assert/strict';
import { measurePcm, compareEchoProbe, captureEchoProbe } from '../../examples/sdk_media_lab/web/rtc-echo-probe.mjs';

test('PCM energy includes negative samples and reports saturation without overflow', () => {
  const result = measurePcm([new Float32Array([0.5, -0.5, 1, -1])]);
  assert.equal(result.samples, 4);
  assert.equal(result.energy, 2.5);
  assert.equal(result.clippedSamples, 2);
});
test('silence or unavailable browser processing never proves successful cancellation', () => {
  assert.equal(compareEchoProbe({energy:0,samples:4}, {energy:0,samples:4}, true).valid, false);
  assert.equal(compareEchoProbe({energy:4,samples:4}, {energy:1,samples:4}, false).valid, false);
  assert.equal(compareEchoProbe({energy:Infinity,samples:4}, {energy:1,samples:4}, true).valid, false);
  assert.equal(compareEchoProbe({energy:4,samples:Infinity}, {energy:1,samples:4}, true).valid, false);
  assert.equal(compareEchoProbe({energy:4,samples:-4}, {energy:1,samples:4}, true).valid, false);
});

test('successful comparison keeps processing settings but excludes microphone identifiers', async () => {
  let processor;
  const track = {getSettings: () => ({echoCancellation: true, sampleRate: 48000,
    deviceId: 'private-device', groupId: 'private-group'}), stop() {}};
  const context = {sampleRate: 48000, destination: {}, createMediaStreamSource: () => ({connect() {}, disconnect() {}}),
    createScriptProcessor: () => (processor = {connect() {}, disconnect() {}}), resume: async () => {}, close: async () => {}};
  const player = {play: async () => {}, pause() {}, removeAttribute() {}, ended: false};
  const result = await captureEchoProbe({}, true, {
    openMicrophone: async () => ({getAudioTracks: () => [track], getTracks: () => [track]}),
    createContext: () => context, createPlayer: () => player, makeUrl: () => 'blob:test', releaseUrl() {},
    sleep: async ms => { if (ms === 6500) processor.onaudioprocess({
      inputBuffer: {getChannelData: () => new Float32Array([0.5, -0.5])},
      outputBuffer: {getChannelData: () => new Float32Array([1, 1])},
    }); },
  });
  assert.deepEqual(result.settings, {echoCancellation: true, sampleRate: 48000});
  assert.equal(result.samples, 2);
  assert.equal(result.energy, 0.5);
});

test('failed playback releases microphone, nodes, context and local source URL', async () => {
  const events = [];
  const track = {getSettings: () => ({}), stop: () => events.push('stop')};
  const stream = {getAudioTracks: () => [track], getTracks: () => [track]};
  const node = {connect() {}, disconnect: () => events.push('disconnect')};
  const context = {createMediaStreamSource: () => node, createScriptProcessor: () => ({...node}),
    resume: async () => {}, close: async () => events.push('close')};
  const player = {play: async () => {throw new Error('play failed');}, pause: () => events.push('pause'), removeAttribute() {}};
  await assert.rejects(captureEchoProbe({}, true, {openMicrophone: async () => stream,
    createContext: () => context, createPlayer: () => player, makeUrl: () => 'blob:test',
    releaseUrl: () => events.push('release')}), /play failed/);
  assert.deepEqual(events, ['pause','disconnect','disconnect','stop','close','release']);
});
test('comparison normalizes different sample counts and preserves degradation', () => {
  const result = compareEchoProbe({energy:8,samples:8}, {energy:2,samples:8}, true);
  assert.equal(result.valid, true);
  assert.ok(Math.abs(result.attenuationDb - 6.020599913) < 0.00001);
  assert.ok(compareEchoProbe({energy:2,samples:8}, {energy:8,samples:8}, true).attenuationDb < 0);
});
