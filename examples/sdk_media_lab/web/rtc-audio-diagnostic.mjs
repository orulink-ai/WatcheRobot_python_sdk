// Explicit, local-only recording. MediaRecorder starts are near-synchronous,
// not sample-accurate; each channel's start timestamp is retained in the report.
const DEVICE_KEYS = [
  'audio_capture_frames', 'audio_tx_packets', 'audio_tx_errors', 'audio_tx_dropped_frames',
  'audio_packets', 'audio_queue_dropped', 'audio_decoded_frames', 'audio_render_errors', 'audio_queue_ms',
  'audio_microphone_peak', 'audio_microphone_clipped_samples', 'audio_pcm_peak',
  'audio_voice_raw_rms', 'audio_voice_output_rms', 'audio_voice_gain_db_x100', 'audio_voice_limiter_hits',
  'audio_aec_active', 'audio_aec_chunks', 'audio_aec_bypass_chunks', 'audio_aec_reference_drops',
  'audio_aec_reference_starved_chunks', 'audio_aec_reference_discarded_bytes',
  'audio_pipeline_age_ewma_us', 'audio_pipeline_age_max_us',
  'audio_microphone_read_ewma_us', 'audio_microphone_read_max_us',
  'audio_aec_process_ewma_us', 'audio_aec_process_max_us', 'audio_opus_encode_ewma_us', 'audio_opus_encode_max_us',
  'internal_free_bytes', 'internal_largest_block_bytes', 'dma_free_bytes', 'psram_free_bytes',
];
export function selectDiagnosticDeviceStats(stats = {}) {
  return Object.fromEntries(DEVICE_KEYS.filter(key => typeof stats[key] === 'boolean' || Number.isFinite(stats[key]))
    .map(key => [key, stats[key]]));
}

export async function recordRtcAudioDiagnostic(streams, {
  durationMs = 20000, snapshot = () => ({}), signal, onState = () => {},
} = {}, {
  createRecorder = stream => new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 96000 }),
  upload = async (name, blob) => {
    const response = await fetch(`/api/diagnostics/rtc-audio/${name}`, { method: 'POST',
      headers: { 'Content-Type': blob.type || 'audio/webm' }, body: blob });
    if (!response.ok) throw new Error('Diagnostic recording could not be saved');
  },
} = {}) {
  const recorders = [], recordings = [], channels = [], samples = [];
  const start = performance.now();
  let interval, timer, finish;
  const stop = () => { for (const recorder of recorders) if (recorder.state !== 'inactive') recorder.stop(); };
  const sample = () => samples.push({ elapsedMs: performance.now() - start, ...snapshot() });
  const cancelled = () => finish?.();
  try {
    for (const [name, stream] of Object.entries(streams)) {
      if (!stream?.getAudioTracks().some(track => track.readyState === 'live')) throw new Error('Diagnostic needs an active call');
      const recorder = createRecorder(stream), chunks = [];
      const promise = new Promise((resolve, reject) => {
        recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
        recorder.onerror = () => { finish?.(); reject(new Error('Diagnostic recorder failed')); };
        recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
      });
      promise.catch(() => {}); // A later setup failure still performs owned cleanup.
      recorders.push(recorder); recordings.push(promise);
      channels.push({ name, startMs: performance.now() - start }); recorder.start(1000);
    }
    sample(); interval = setInterval(sample, 1000);
    onState('recording');
    await new Promise(resolve => {
      finish = resolve; timer = setTimeout(resolve, Math.min(30000, Math.max(1, durationMs)));
      signal?.addEventListener('abort', cancelled, { once: true });
      if (signal?.aborted) resolve();
    });
    clearInterval(interval); clearTimeout(timer); sample(); stop();
    const blobs = await Promise.all(recordings);
    for (let i = 0; i < channels.length; i++) {
      if (!blobs[i].size || blobs[i].size > 2 * 1024 * 1024) throw new Error('Diagnostic audio size is invalid');
      channels[i].bytes = blobs[i].size; await upload(channels[i].name, blobs[i]);
    }
    const report = { durationMs: samples.at(-1).elapsedMs, cancelled: signal?.aborted === true,
      channels, samples, synchronization: 'near-synchronous MediaRecorder starts; not sample-accurate' };
    await upload('report', new Blob([JSON.stringify(report)], { type: 'application/json' }));
    onState('saved'); return report;
  } finally {
    clearInterval(interval); clearTimeout(timer); signal?.removeEventListener('abort', cancelled); stop();
  }
}
