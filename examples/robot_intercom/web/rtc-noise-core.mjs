// RNNoise operates on 10 ms mono frames at 48 kHz, with one frame of lookahead.
export const NOISE_FRAME_SAMPLES = 480;

export function createDenoiseFrameProcessor(module, { strength = 70 } = {}) {
  const handle = module._rnnoise_create(0);
  const pointer = module._malloc(NOISE_FRAME_SAMPLES * 4);
  if (!handle || !pointer) {
    if (handle) module._rnnoise_destroy(handle);
    if (pointer) module._free(pointer);
    throw new Error('Noise suppression allocation failed');
  }
  const dry = new Float32Array(NOISE_FRAME_SAMPLES);
  let wet = 0, disposed = false;
  function setStrength(value) {
    wet = Math.max(0, Math.min(85, Number.isFinite(Number(value)) ? Number(value) : 70)) / 100;
  }
  setStrength(strength);
  return {
    get strength() { return wet * 100; },
    setStrength,
    process(input, output) {
      if (disposed) throw new Error('Noise suppression is disposed');
      if (input.length !== NOISE_FRAME_SAMPLES || output.length !== NOISE_FRAME_SAMPLES) {
        throw new Error('Noise suppression needs a 480-sample frame');
      }
      const offset = pointer / 4;
      for (let i = 0; i < NOISE_FRAME_SAMPLES; i++) module.HEAPF32[offset + i] = input[i] * 32768;
      // Keep the recurrent state warm even in bypass, avoiding restart artifacts.
      module._rnnoise_process_frame(handle, pointer, pointer);
      for (let i = 0; i < NOISE_FRAME_SAMPLES; i++) {
        const cleaned = module.HEAPF32[offset + i] / 32768;
        output[i] = (1 - wet) * dry[i] + wet * cleaned;
        dry[i] = input[i];
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      module._rnnoise_destroy(handle); module._free(pointer);
    },
  };
}

// Bounded adapters; no frame allocation or growing queues on the audio thread.
export class DenoiseQuantumBuffer {
  constructor(processor) {
    this.processor = processor;
    this.input = new Float32Array(NOISE_FRAME_SAMPLES);
    this.output = new Float32Array(NOISE_FRAME_SAMPLES);
    this.queue = new Float32Array(2048);
    this.inputCount = 0;
    this.read = 0; this.write = NOISE_FRAME_SAMPLES; this.queuedSamples = NOISE_FRAME_SAMPLES;
  }
  process(channels, output) {
    for (let i = 0; i < output.length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      this.input[this.inputCount++] = channels.length ? value / channels.length : 0;
      if (this.inputCount === NOISE_FRAME_SAMPLES) {
        this.processor.process(this.input, this.output);
        this.inputCount = 0;
        for (const sample of this.output) {
          this.queue[this.write] = sample;
          this.write = (this.write + 1) % this.queue.length;
          this.queuedSamples++;
        }
      }
      output[i] = this.queuedSamples ? this.queue[this.read] : 0;
      if (this.queuedSamples) {
        this.read = (this.read + 1) % this.queue.length;
        this.queuedSamples--;
      }
    }
  }
}
