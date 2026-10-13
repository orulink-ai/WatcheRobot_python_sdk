import { VoiceEffect } from './voice-effect-core.mjs';
class VoiceEffectProcessor extends AudioWorkletProcessor {
  constructor() {
    super(); this.core = new VoiceEffect(sampleRate); this.disposed = false;
    this.port.onmessage = ({ data }) => {
      if (data?.type === 'dispose') this.disposed = true;
      if (data?.type === 'mode') this.core.setMode(data.mode);
    };
  }
  process(inputs, outputs) {
    if (this.disposed) return false;
    const output = outputs[0]?.[0];
    if (output) this.core.process(inputs[0]?.[0], output);
    return true;
  }
}
registerProcessor('intercom-voice-effect', VoiceEffectProcessor);
