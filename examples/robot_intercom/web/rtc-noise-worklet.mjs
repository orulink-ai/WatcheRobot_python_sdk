import createModule from './rnnoise-vendor.mjs';
import { createDenoiseFrameProcessor, DenoiseQuantumBuffer } from './rtc-noise-core.mjs';

class RobotNoiseProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    if (sampleRate !== 48000) throw new Error('Robot noise suppression requires 48 kHz');
    this.frames = 0; this.samples = 0; this.closed = false;
    this.inputEnergy = 0; this.outputEnergy = 0;
    this.processor = createDenoiseFrameProcessor(createModule(), options.processorOptions);
    this.buffer = new DenoiseQuantumBuffer(this.processor);
    this.port.onmessage = ({ data }) => {
      if (data?.type === 'options') this.processor.setStrength(data.enabled ? data.strength : 0);
      if (data?.type === 'dispose') { this.closed = true; this.processor.dispose(); }
    };
    this.port.postMessage({ state: 'ready' });
  }
  process(inputs, outputs) {
    if (this.closed) return false;
    const output = outputs[0]?.[0];
    if (!output) return true;
    try {
      this.buffer.process(inputs[0] || [], output);
      for (let i = 0; i < output.length; i++) {
        this.inputEnergy += (inputs[0]?.[0]?.[i] || 0) ** 2;
        this.outputEnergy += output[i] ** 2;
      }
      this.samples += output.length;
      if (this.samples >= 48000) {
        this.frames += 100; this.samples -= 48000;
        this.port.postMessage({ state: 'processing', frames: this.frames,
          inputRms: Math.sqrt(this.inputEnergy / 48000), outputRms: Math.sqrt(this.outputEnergy / 48000) });
        this.inputEnergy = 0; this.outputEnergy = 0;
      }
      return true;
    } catch (_) {
      this.closed = true; this.processor.dispose();
      this.port.postMessage({ state: 'unavailable' });
      return false;
    }
  }
}
registerProcessor('robot-noise-suppression', RobotNoiseProcessor);
