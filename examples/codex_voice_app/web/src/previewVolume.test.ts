import { describe, expect, it, vi } from "vitest"
import { limitPreviewVolume } from "./previewVolume"

describe("quiet local preview", () => {
  it("clamps an explicit player increase to the current backend ceiling", () => {
    const audio = { volume: .8, pause: vi.fn() }
    limitPreviewVolume(audio, .5) // Called by the native volumechange event.
    expect(audio.volume).toBe(.5)
    const setter = vi.fn()
    const alreadyLimited = { get volume() { return .5 }, set volume(value: number) { setter(value) }, pause: vi.fn() }
    limitPreviewVolume(alreadyLimited, .5)
    expect(setter).not.toHaveBeenCalled() // No recursive volumechange writes.
  })
  it("never increases a manually lowered level on refresh or reconnect", () => {
    const audio = { volume: 1, pause: vi.fn() }
    limitPreviewVolume(audio, .5)
    expect(audio.volume).toBe(.5)
    audio.volume = .1
    limitPreviewVolume(audio, .5)
    expect(audio.volume).toBe(.1)
    limitPreviewVolume(audio, null)
    expect(audio.pause).toHaveBeenCalledOnce()
    limitPreviewVolume(audio, .5)
    limitPreviewVolume(audio, 1)
    expect(audio.volume).toBe(.1)
  })
})
