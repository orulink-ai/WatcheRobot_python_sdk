/** A backend level is a ceiling, never permission to raise a local preference. */
export function limitPreviewVolume(audio: { volume: number; pause(): void }, level: number | null) {
  if (level === null) {
    audio.pause() // Disconnect must not leave an unattended local preview playing.
    return
  }
  if (audio.volume > level) audio.volume = level
}
