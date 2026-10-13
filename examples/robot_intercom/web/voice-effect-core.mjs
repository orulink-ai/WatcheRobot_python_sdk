export const VOICE_MODES = ['original', 'deep', 'bright', 'robot'];

// Two overlapping read heads keep streaming duration unchanged while shifting
// pitch. Bounded delay/storage; no allocations per quantum, no main-thread PCM.
export class VoiceEffect {
  constructor(rate) {
    if (!Number.isFinite(rate) || rate < 8000 || rate > 96000) throw new RangeError('Invalid sample rate');
    this.rate = rate;
    this.window = Math.round(rate * 0.04);
    this.ring = new Float32Array(Math.ceil(rate * 0.08) + 4);
    this.write = 0; this.phase = 0; this.carrier = 0; this.mode = 'original';
  }
  setMode(mode) {
    if (!VOICE_MODES.includes(mode)) throw new RangeError('Invalid voice mode');
    if (this.mode !== mode) { this.ring.fill(0); this.phase = 0; }
    this.mode = mode;
  }
  read(delay) {
    const index = (this.write - delay + this.ring.length) % this.ring.length;
    const base = Math.floor(index), fraction = index - base;
    return this.ring[base] * (1 - fraction) + this.ring[(base + 1) % this.ring.length] * fraction;
  }
  process(input, output) {
    const ratio = this.mode === 'deep' ? 0.8 : 1.25;
    for (let i = 0; i < output.length; i++) {
      const x = input?.[i] || 0;
      this.ring[this.write] = x;
      if (this.mode === 'original') output[i] = x;
      else if (this.mode === 'robot') output[i] = x * Math.sin(this.carrier * 2 * Math.PI);
      else {
        const a = this.phase, b = (a + 0.5) % 1;
        const delayA = 2 + (ratio < 1 ? a : 1 - a) * this.window;
        const delayB = 2 + (ratio < 1 ? b : 1 - b) * this.window;
        const weight = 0.5 - 0.5 * Math.cos(2 * Math.PI * a);
        output[i] = this.read(delayA) * weight + this.read(delayB) * (1 - weight);
        this.phase = (this.phase + Math.abs(1 - ratio) / this.window) % 1;
      }
      this.carrier = (this.carrier + 90 / this.rate) % 1;
      this.write = (this.write + 1) % this.ring.length;
    }
  }
}
