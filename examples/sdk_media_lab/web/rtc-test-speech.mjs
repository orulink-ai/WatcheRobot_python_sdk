// LAN hardware validation: bundled speech and silence, with no local playback.
export async function createRtcTestSpeech({
  createContext = () => new AudioContext(),
  fetchAudio = () => fetch('/api/diagnostics/rtc-speech'),
} = {}) {
  const context = createContext();
  let source, gain, destination, disposed = false;
  async function dispose() {
    if (disposed) return;
    disposed = true;
    try { source?.stop(); } catch (_) {}
    for (const node of [source, gain, destination]) {
      try { node?.disconnect(); } catch (_) {}
    }
    for (const track of destination?.stream.getTracks() || []) track.stop();
    try { await context.close(); } catch (_) {}
  }
  try {
    destination = context.createMediaStreamDestination();
    await context.resume();
    const response = await fetchAudio();
    if (!response.ok) throw new Error('RTC speech fixture is unavailable');
    const speech = await context.decodeAudioData(await response.arrayBuffer());
    if (!speech.length || !speech.numberOfChannels || !(speech.sampleRate > 0)) {
      throw new Error('RTC speech fixture is empty');
    }
    const buffer = context.createBuffer(1, speech.length + Math.ceil(speech.sampleRate * 8), speech.sampleRate);
    const samples = buffer.getChannelData(0);
    for (let channel = 0; channel < speech.numberOfChannels; channel++) {
      const input = speech.getChannelData(channel);
      for (let i = 0; i < speech.length; i++) samples[i] += input[i] / speech.numberOfChannels;
    }
    let peak = 0;
    for (let i = 0; i < speech.length; i++) {
      if (!Number.isFinite(samples[i])) throw new Error('RTC speech fixture has invalid samples');
      peak = Math.max(peak, Math.abs(samples[i]));
    }
    if (!(peak > 0)) throw new Error('RTC speech fixture is silent');
    source = context.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    gain = context.createGain();
    gain.gain.value = .06 / peak;
    source.connect(gain);
    gain.connect(destination);
    source.start();
    return { stream: destination.stream, dispose };
  } catch (error) {
    await dispose();
    throw error;
  }
}
