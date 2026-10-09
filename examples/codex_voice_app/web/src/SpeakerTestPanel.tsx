import type { Session } from "./session"
import { useEffect, useRef } from "react"
import { limitPreviewVolume } from "./previewVolume"
import { canRunDeviceDiagnostic, speakerTestSummary, isSpeakerTestActive } from "./session"

export function SpeakerTestPanel({ session, backendConnected, command }: {
  session: Session; backendConnected: boolean; command: (payload: Record<string, unknown>) => void
}) {
  const active = isSpeakerTestActive(session)
  const volume = backendConnected ? session.playbackVolume : null
  const preview = useRef<HTMLAudioElement>(null)
  useEffect(() => {
    if (preview.current) limitPreviewVolume(preview.current, volume)
  }, [volume])
  return <section className="speaker-test" aria-labelledby="speaker-test-title">
    <div className="speaker-test-copy">
      <span className="eyebrow">01 / 听感对照</span>
      <h2 id="speaker-test-title">用同一段声音，听听机器人。</h2>
      <p>先前 A/B 测试的本地固定音源，不是 Codex 声线。机器人播放使用当前清晰度版本，与真实通话共用增益处理和 RTC 通道。</p>
      <p className="speaker-test-status" role="status" aria-live="polite">{speakerTestSummary(session, backendConnected)}</p>
    </div>
    <div className="speaker-test-controls">
      <div className="playback-volume" role="group" aria-label="临时播放音量">
        <small role="status">{volume === null ? "播放音量待确认" : `临时播放音量 · ${Math.round(volume * 100)}%`}</small>
        <div>
          <button disabled={!backendConnected || volume === null} aria-pressed={volume === .5} onClick={() => command({ type: "playbackVolume", level: .5 })}>安静调试 50%</button>
          <button disabled={!backendConnected || volume === null} aria-pressed={volume === 1} onClick={() => command({ type: "playbackVolume", level: 1 })}>恢复 100%</button>
        </div>
        <small>同时降低机器人通话、测试与电脑原音；电脑试听可再单独调低。应用重启恢复100%。</small>
      </div>
      <button disabled={!canRunDeviceDiagnostic(session, backendConnected)} onClick={() => command({ type: "speakerTest" })}>机器人播放测试语音</button>
      {active && <button className="danger" disabled={!backendConnected} onClick={() => command({ type: "cancel" })}>停止测试</button>}
      <details><summary>电脑试听原音</summary>
        <audio ref={preview} onVolumeChange={(event) => limitPreviewVolume(event.currentTarget, volume)} controls preload="none" src="/api/speaker-reference.wav" aria-label="电脑试听固定语音原音" />
        <small>从电脑当前输出设备播放，不经过机器人或增益处理，不能用于比较机器人音量。</small>
      </details>
      <small>机器人测试不启动模型；RTC麦克风帧仅丢弃，不上传、不保存。</small>
    </div>
  </section>
}
