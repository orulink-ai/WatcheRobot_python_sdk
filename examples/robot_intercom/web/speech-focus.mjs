// RNNoise speech probability drives a soft expander, never a hard mute gate.
// No lookahead/storage beyond the denoiser's existing frames. Fixed at 48kHz.
export class SpeechFocus {
  constructor() { this.gain = 1; this.target = 1; this.hold = 0; }
  update(probability) {
    if (Number.isFinite(probability) && probability >= 0.5) this.hold = 20;
    else this.hold = Math.max(0, this.hold - 1);
    this.target = this.hold > 0 ? 1 : 0.2;
  }
  next() {
    // ~4ms opening, ~80ms closing; interpolate on the audio thread.
    this.gain += (this.target - this.gain) * (this.target > this.gain ? 1 / 192 : 1 / 3840);
    return this.gain;
  }
}
