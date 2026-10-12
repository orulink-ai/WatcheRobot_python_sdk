import test from 'node:test';
import assert from 'node:assert/strict';
import { frequencyBandRanges, createAudioAnalyser } from '../../examples/robot_intercom/frontend/lib/audio-analysis.mjs';

test('25 speech bars cover every selected FFT bin exactly once, including the final bars', () => {
  const ranges = frequencyBandRanges(1024, 25, 2, 128);
  assert.equal(ranges.length, 25);
  assert.ok(ranges.every(([start, end]) => end > start));
  assert.deepEqual(ranges.flatMap(([start, end]) => Array.from({ length: end - start }, (_, i) => start + i)),
    Array.from({ length: 126 }, (_, i) => i + 2));
  // A real high speech-band bin must reach the final bar, not disappear.
  assert.ok(ranges.at(-1)[0] <= 127 && ranges.at(-1)[1] > 127);
});

test('FFT ranges stay within the analyser buffer and reject invalid band counts', () => {
  assert.deepEqual(frequencyBandRanges(4, 2, -10, 100), [[0, 2], [2, 4]]);
  assert.deepEqual(frequencyBandRanges(4, 2, 6, 10), [[4, 4], [4, 4]]);
  for (const bands of [0, -1, 1.5, NaN, Infinity]) {
    assert.throws(() => frequencyBandRanges(4, bands, 0, 4), RangeError);
  }
});

function audioFixture(failAt) {
  const calls = [];
  const stream = { getTracks: () => [{ stop() { calls.push('track.stop'); } }] };
  class Context {
    createMediaStreamSource(received) {
      assert.equal(received, stream);
      if (failAt === 'source') throw new Error('source failed');
      return {
        connect() { if (failAt === 'connect') throw new Error('connect failed'); },
        disconnect() { calls.push('source.disconnect'); },
      };
    }
    createAnalyser() {
      if (failAt === 'analyser') throw new Error('analyser failed');
      return {
        set fftSize(value) { if (failAt === 'options') throw new Error('options failed'); },
        disconnect() { calls.push('analyser.disconnect'); },
      };
    }
    close() { calls.push('context.close'); return Promise.reject(new Error('already closed')); }
  }
  return { calls, stream, Context };
}

test('partial analyser initialization closes owned resources while preserving borrowed tracks', async () => {
  for (const stage of ['source', 'analyser', 'options', 'connect']) {
    const { calls, stream, Context } = audioFixture(stage);
    assert.throws(() => createAudioAnalyser(stream, { fftSize: 32 }, Context), new RegExp(stage));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.filter(x => x === 'context.close').length, 1);
    assert.ok(!calls.includes('track.stop'));
    if (stage !== 'source') assert.ok(calls.includes('source.disconnect'));
    if (['options', 'connect'].includes(stage)) assert.ok(calls.includes('analyser.disconnect'));
  }
});

test('normal cleanup is idempotent and handles rejected close promises', async () => {
  const { calls, stream, Context } = audioFixture();
  const { cleanup } = createAudioAnalyser(stream, { fftSize: 32 }, Context);
  cleanup();
  cleanup();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, ['source.disconnect', 'analyser.disconnect', 'context.close']);
});
