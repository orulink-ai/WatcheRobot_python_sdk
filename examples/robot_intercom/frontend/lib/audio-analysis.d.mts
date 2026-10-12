export interface AudioAnalyserOptions {
  fftSize?: number;
  smoothingTimeConstant?: number;
  minDecibels?: number;
  maxDecibels?: number;
}
export function frequencyBandRanges(bufferLength: number, bands: number, loPass: number, hiPass: number): [number, number][];
export function createAudioAnalyser(mediaStream: MediaStream, options?: AudioAnalyserOptions, Context?: typeof AudioContext): {
  analyser: AnalyserNode;
  audioContext: AudioContext;
  cleanup(): void;
};
