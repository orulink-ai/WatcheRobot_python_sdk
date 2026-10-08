import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { createDenoiseFrameProcessor, DenoiseQuantumBuffer } from '../../examples/sdk_media_lab/web/rtc-noise-core.mjs';
import createModule from '../../examples/sdk_media_lab/web/rnnoise-vendor.mjs';

const load = () => createModule();

test('actual RNNoise suppresses steady noise without clipping or introducing a gain boost', async () => {
  const processor = createDenoiseFrameProcessor(await load(), { strength: 70 });
  const input = new Float32Array(480), output = new Float32Array(480);
  let seed = 19, before = 0, after = 0;
  for (let frame = 0; frame < 150; frame++) {
    for (let i = 0; i < 480; i++) {
      seed = (1664525 * seed + 1013904223) >>> 0;
      input[i] = (seed / 4294967296 - 0.5) * 0.025;
    }
    processor.process(input, output);
    if (frame > 50) for (let i = 0; i < 480; i++) {
      before += input[i] ** 2; after += output[i] ** 2;
      assert.ok(Number.isFinite(output[i]) && Math.abs(output[i]) < 1);
    }
  }
  assert.ok(Math.sqrt(after / before) < 0.55, `noise RMS ratio ${Math.sqrt(after / before)}`);
  processor.dispose(); processor.dispose();
});

test('bypass preserves every sample with a fixed 20ms delay across 128-sample quanta', async () => {
  const processor = createDenoiseFrameProcessor(await load(), { strength: 0 });
  const buffer = new DenoiseQuantumBuffer(processor);
  const original = Float32Array.from({length: 128 * 60}, (_, i) => Math.sin(i * 0.071) * 0.1);
  const received = new Float32Array(original.length);
  for (let start = 0; start < original.length; start += 128) {
    buffer.process([original.subarray(start, start + 128)], received.subarray(start, start + 128));
  }
  for (let i = 960; i < original.length; i++) assert.equal(received[i], original[i - 960]);
  assert.ok(buffer.queuedSamples < 960);
  processor.dispose();
});

test('strength switches keep the model alive and preserve quiet input without a hard gate', async () => {
  const processor = createDenoiseFrameProcessor(await load(), { strength: 70 });
  const input = new Float32Array(480).fill(0.0001), output = new Float32Array(480);
  for (let frame = 0; frame < 12; frame++) processor.process(input, output);
  assert.ok(output.some(value => value > 0.00002), 'dry mix must preserve quiet samples');
  processor.setStrength(0); processor.process(input, output);
  assert.deepEqual(output, input);
  processor.setStrength(100); assert.equal(processor.strength, 85);
  processor.dispose();
});

test('bundled quiet speech and short pauses retain voiced energy with the actual model', async () => {
  const wav = fs.readFileSync(new URL('../../examples/sdk_media_lab/assets/sample_speech.wav', import.meta.url));
  let rate, channels, bits, data;
  for (let offset = 12; offset + 8 <= wav.length;) {
    const size = wav.readUInt32LE(offset + 4), name = wav.toString('ascii', offset, offset + 4);
    if (name === 'fmt ') {
      assert.equal(wav.readUInt16LE(offset + 8), 1);
      channels = wav.readUInt16LE(offset + 10); rate = wav.readUInt32LE(offset + 12);
      bits = wav.readUInt16LE(offset + 22);
    }
    if (name === 'data') data = wav.subarray(offset + 8, offset + 8 + size);
    offset += 8 + size + size % 2;
  }
  assert.equal(bits, 16); assert.ok(data?.length && rate);
  const samples = new Float32Array(data.length / 2 / channels);
  let peak = 0;
  for (let i = 0; i < samples.length; i++) {
    for (let channel = 0; channel < channels; channel++) samples[i] += data.readInt16LE((i * channels + channel) * 2) / channels;
    peak = Math.max(peak, Math.abs(samples[i]));
  }
  const processor = createDenoiseFrameProcessor(await load(), { strength: 70 });
  const input = new Float32Array(480), output = new Float32Array(480);
  const frameEnergy = [];
  let before = 0, after = 0;
  const length = Math.ceil(samples.length * 48000 / rate);
  for (let start = 0; start < length + 960; start += 480) {
    let energy = 0;
    for (let i = 0; i < 480; i++) {
      const pos = (start + i) * rate / 48000, index = Math.floor(pos), fraction = pos - index;
      input[i] = ((samples[index] || 0) * (1 - fraction) + (samples[index + 1] || 0) * fraction) / peak * 0.04;
      energy += input[i] ** 2;
    }
    frameEnergy.push(energy); processor.process(input, output);
    const previousEnergy = frameEnergy.at(-2) || 0;
    if (previousEnergy > 480 * 0.003 ** 2) {
      before += previousEnergy;
      for (const sample of output) { after += sample ** 2; assert.ok(Number.isFinite(sample)); }
    }
  }
  assert.ok(before > 0); assert.ok(Math.sqrt(after / before) > 0.4, `voiced RMS ratio ${Math.sqrt(after / before)}`);
  processor.dispose();
});
