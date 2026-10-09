export function measurePcm(chunks) {
  let energy = 0, samples = 0, clippedSamples = 0;
  for (const chunk of chunks) {
    for (const value of chunk) {
      if (!Number.isFinite(value)) throw new Error('Invalid PCM sample');
      energy += value * value;
      samples++;
      if (Math.abs(value) >= 0.9999) clippedSamples++;
    }
  }
  return { energy, samples, clippedSamples, rms: samples ? Math.sqrt(energy / samples) : null };
}

export function compareEchoProbe(raw, processed, cancellationConfirmed) {
  if (!cancellationConfirmed || !Number.isFinite(raw?.samples) || !(raw.samples > 0)
    || !Number.isFinite(processed?.samples) || !(processed.samples > 0)
    || !Number.isFinite(raw.energy) || !Number.isFinite(processed.energy)
    || !(raw.energy > 0) || !(processed.energy > 0)) {
    return { valid: false, attenuationDb: null };
  }
  return { valid: true, attenuationDb: 10 * Math.log10((raw.energy / raw.samples) / (processed.energy / processed.samples)) };
}

// Diagnostic only: PCM stays in this browser, never enters the Device channel.
export async function captureEchoProbe(file, echoCancellation, deps = {}) {
  const openMicrophone = deps.openMicrophone || (audio => navigator.mediaDevices.getUserMedia({ audio, video: false }));
  const createContext = deps.createContext || (() => new AudioContext());
  const createPlayer = deps.createPlayer || (() => new Audio());
  const makeUrl = deps.makeUrl || (value => URL.createObjectURL(value));
  const releaseUrl = deps.releaseUrl || (value => URL.revokeObjectURL(value));
  const sleep = deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  let stream, context, player, source, processor, url;
  const chunks = [];
  let measuring = false;
  try {
    stream = await openMicrophone({ echoCancellation, noiseSuppression: false, autoGainControl: false, channelCount: 1 });
    const trackSettings = stream.getAudioTracks()[0].getSettings();
    const settings = Object.fromEntries(['echoCancellation', 'noiseSuppression', 'autoGainControl', 'channelCount', 'sampleRate', 'latency']
      .filter(key => trackSettings[key] !== undefined).map(key => [key, trackSettings[key]]));
    context = createContext();
    source = context.createMediaStreamSource(stream);
    processor = context.createScriptProcessor(1024, 1, 1);
    processor.onaudioprocess = event => {
      if (measuring) chunks.push(event.inputBuffer.getChannelData(0).slice());
      event.outputBuffer.getChannelData(0).fill(0); // No microphone monitoring loop.
    };
    source.connect(processor);
    processor.connect(context.destination);
    await context.resume();
    player = createPlayer();
    url = makeUrl(file);
    player.src = url;
    player.volume = 0.08;
    player.loop = true;
    await player.play();
    await sleep(1500);
    measuring = true;
    await sleep(6500);
    if (player.ended) throw new Error('Source playback stopped before the measurement completed');
    return { ...measurePcm(chunks), settings, sampleRate: context.sampleRate };
  } finally {
    measuring = false;
    player?.pause();
    if (player) player.removeAttribute('src');
    if (processor) { processor.onaudioprocess = null; processor.disconnect(); }
    source?.disconnect();
    for (const track of stream?.getTracks() || []) track.stop();
    try { if (context) await context.close(); }
    finally { if (url) releaseUrl(url); }
  }
}

export function mountEchoProbe() {
  const panel = document.createElement('section');
  panel.setAttribute('aria-label', 'Computer echo diagnostic');
  panel.innerHTML = '<h2>Computer echo diagnostic</h2><p>Local 8-second raw / AEC comparison. Speech plays at 8% player volume. No microphone monitoring or upload. Stop any call first.</p><input type="file" accept="audio/wav" aria-label="Diagnostic speech WAV"><button type="button">Run computer echo comparison</button><pre role="status" data-i18n-ignore>Ready</pre>';
  document.querySelector('main').prepend(panel);
  const input = panel.querySelector('input'), button = panel.querySelector('button'), result = panel.querySelector('pre');
  button.addEventListener('click', async () => {
    if (!input.files[0]) { result.textContent = 'Select a speech WAV first'; return; }
    button.disabled = true;
    try {
      const status = await (await fetch('/api/status')).json();
      if (status.rtc.active) throw new Error('Stop the active RTC call first');
      result.textContent = 'Measuring raw microphone…';
      const raw = await captureEchoProbe(input.files[0], false);
      result.textContent = 'Measuring browser AEC…';
      const processed = await captureEchoProbe(input.files[0], true);
      const comparison = compareEchoProbe(raw, processed,
        raw.settings.echoCancellation === false && processed.settings.echoCancellation === true
        && raw.settings.autoGainControl === false && processed.settings.autoGainControl === false);
      result.textContent = JSON.stringify({ raw, processed, comparison, note: 'Ambient sound is included; this is not isolated echo attenuation or double-talk acceptance.' }, null, 2);
    } catch (error) { result.textContent = `Diagnostic failed: ${error.message}`; }
    finally { button.disabled = false; }
  });
}
