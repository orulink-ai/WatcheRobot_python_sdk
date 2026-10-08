import test from 'node:test';
import assert from 'node:assert/strict';
import { recordRtcAudioDiagnostic, selectDiagnosticDeviceStats } from '../../examples/sdk_media_lab/web/rtc-audio-diagnostic.mjs';

test('audio diagnostics bound device snapshots and omit unrelated video and arbitrary fields', () => {
  const selected = selectDiagnosticDeviceStats({ audio_tx_packets: 2, audio_queue_dropped: 3,
    internal_free_bytes: 14000, video_blob: 'x'.repeat(100000), secret: 'fixture' });
  assert.deepEqual(selected, { audio_tx_packets: 2, audio_queue_dropped: 3, internal_free_bytes: 14000 });
});

test('diagnostic records all three streams, saves bounded evidence, and never stops RTC tracks', async () => {
  let tracksStopped = 0;
  const streams = Object.fromEntries(['computer', 'robot-raw', 'robot-clean'].map(name =>
    [name, { getAudioTracks: () => [{ readyState: 'live', stop: () => tracksStopped++ }] }]));
  const files = [];
  const createRecorder = () => ({ state: 'inactive', mimeType: 'audio/webm',
    start() { this.state = 'recording'; },
    stop() { this.state = 'inactive'; this.ondataavailable({ data: new Blob(['fixture']) }); this.onstop(); } });
  const result = await recordRtcAudioDiagnostic(streams, { durationMs: 5, snapshot: () => ({ drop: 2 }) },
    { createRecorder, upload: async (name, blob) => files.push([name, blob.size]) });
  assert.equal(result.channels.length, 3); assert.equal(files.length, 4);
  assert.equal(tracksStopped, 0); assert.ok(result.samples.length >= 1);
  assert.equal(result.durationMs, result.samples.at(-1).elapsedMs);
});

test('a recorder setup failure stops only already-started recorders and does not upload misleading evidence', async () => {
  let stopped = 0, count = 0, uploaded = 0;
  const stream = { getAudioTracks: () => [{ readyState: 'live' }] };
  await assert.rejects(recordRtcAudioDiagnostic({ computer: stream, 'robot-raw': stream }, {}, {
    createRecorder: () => { if (++count === 2) throw new Error('unsupported'); return {
      state: 'recording', start() {}, stop() { this.state = 'inactive'; stopped++; this.onstop(); } }; },
    upload: async () => { uploaded++; },
  }), /unsupported/);
  assert.equal(stopped, 1); assert.equal(uploaded, 0);
});
