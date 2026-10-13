import assert from 'node:assert/strict';
import test from 'node:test';
import { SpeechFocus } from '../../examples/robot_intercom/web/speech-focus.mjs';
import { createDenoiseFrameProcessor } from '../../examples/robot_intercom/web/rtc-noise-core.mjs';
import createModule from '../../examples/robot_intercom/web/rnnoise-vendor.mjs';
import fs from 'node:fs';

test('non-speech smoothly attenuates with a floor; quiet detected speech opens immediately', () => {
  const focus = new SpeechFocus();
  let last = 1;
  for (let frame = 0; frame < 100; frame++) {
    focus.update(0);
    for (let i = 0; i < 480; i++) {
      const gain = focus.next(); assert.ok(Math.abs(gain - last) < 0.002); last = gain;
    }
  }
  assert.ok(last >= 0.19 && last < 0.21);
  focus.update(0.7);
  for (let i = 0; i < 480; i++) last = focus.next();
  assert.ok(last > 0.9);
  // Short pauses and word endings are retained by a 200ms hangover.
  for (let frame = 0; frame < 15; frame++) {
    focus.update(0); for (let i = 0; i < 480; i++) last = focus.next();
  }
  assert.ok(last > 0.99);
});

test('real RNNoise human focus suppresses steady noise more than natural mix without boosting', () => {
  const natural = createDenoiseFrameProcessor(createModule(), { strength: 70 });
  const focused = createDenoiseFrameProcessor(createModule(), { strength: 85, voiceFocus: true });
  const input = new Float32Array(480), a = new Float32Array(480), b = new Float32Array(480);
  let seed = 29, ea = 0, eb = 0;
  for (let frame = 0; frame < 200; frame++) {
    for (let i = 0; i < 480; i++) {
      seed = (1664525 * seed + 1013904223) >>> 0; input[i] = (seed / 4294967296 - 0.5) * 0.025;
    }
    natural.process(input, a); focused.process(input, b);
    if (frame > 80) for (let i = 0; i < 480; i++) { ea += a[i] ** 2; eb += b[i] ** 2; assert.ok(Number.isFinite(b[i]) && Math.abs(b[i]) <= 1); }
  }
  assert.ok(Math.sqrt(eb / ea) < 0.4);
  natural.dispose(); focused.dispose();
});

test('speech focus retains quiet speech energy with real model and existing one-frame alignment', () => {
  const wav = fs.readFileSync(new URL('../../examples/sdk_media_lab/assets/sample_speech.wav', import.meta.url));
  let rate, channels, data;
  for (let offset = 12; offset + 8 < wav.length;) {
    const size = wav.readUInt32LE(offset + 4), name = wav.toString('ascii', offset, offset + 4);
    if (name === 'fmt ') {
      assert.equal(wav.readUInt16LE(offset + 8), 1); assert.equal(wav.readUInt16LE(offset + 22), 16);
      channels = wav.readUInt16LE(offset + 10); rate = wav.readUInt32LE(offset + 12);
    }
    if (name === 'data') data = wav.subarray(offset + 8, offset + 8 + size);
    offset += 8 + size + size % 2;
  }
  const pcm = Float32Array.from({ length: data.length / (2 * channels) }, (_, i) => {
    let sum = 0;
    for (let c = 0; c < channels; c++) sum += data.readInt16LE((i * channels + c) * 2) / 32768 / channels;
    return sum;
  });
  const peak = pcm.reduce((m, x) => Math.max(m, Math.abs(x)), 0);
  const processor = createDenoiseFrameProcessor(createModule(), { strength: 85, voiceFocus: true });
  const input = new Float32Array(480), output = new Float32Array(480);
  let previousEnergy = 0, before = 0, after = 0;
  const length = Math.ceil(pcm.length * 48000 / rate);
  for (let start = 0; start < length + 960; start += 480) {
    let energy = 0;
    for (let i = 0; i < 480; i++) {
      const pos = (start + i) * rate / 48000, at = Math.floor(pos), f = pos - at;
      input[i] = ((pcm[at] || 0) * (1 - f) + (pcm[at + 1] || 0) * f) / peak * 0.04;
      energy += input[i] ** 2;
    }
    processor.process(input, output);
    if (previousEnergy > 480 * 0.003 ** 2) {
      before += previousEnergy;
      for (const sample of output) { after += sample ** 2; assert.ok(Number.isFinite(sample)); }
    }
    previousEnergy = energy;
  }
  assert.ok(before > 0);
  const ratio = Math.sqrt(after / before);
  assert.ok(ratio > 0.4, `quiet voiced RMS ratio ${ratio}`);
  processor.dispose();
});
