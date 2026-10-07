import { AssistantRuntimeProvider, useExternalStoreRuntime, ThreadPrimitive, MessagePrimitive, ComposerPrimitive } from "@assistant-ui/react"
import { useVoiceSession } from "./useVoiceSession"
import type { Permissions, RobotTask } from "./session"
import { isUnsafeStop, canRunDeviceDiagnostic, endedSessionNotice, sessionStatus, isListening, evidenceTitle, motionRangeSummary, taskKind, taskSummary, isRealtimeVoice, canSendText, voiceModeLabel, voiceModeNotice, voiceInputStatus, duplexSummary, bodyFeedbackSummary } from "./session"
import { useState } from "react"

const stages: Record<string, string> = { authorizing: "检查权限", moving: "正在转头", capturing: "正在拍照", attaching: "等待发送照片", analyzing: "正在看图", answering: "正在回答", completed: "已完成", failed: "未完成", cancelled: "已取消" }
const permissionCopy: [keyof Permissions, string, string][] = [
  ["camera", "允许拍照", "仅在你请求时拍摄"], ["upload", "允许图像分析", "照片会发送给当前 Codex 模型服务"],
  ["motion", "允许转头观察", "在固件行程内观察两侧，随时可停止；转头不自动拍照或上传"], ["lights", "允许灯光", "低亮度常亮，10 秒自动关闭"],
]

function taskStage(task: RobotTask) {
  return task.tool === "aim_camera" && task.status === "completed" ? "SDK回执已收到" : stages[task.status] ?? task.status
}

function TranscriptMessage() {
  return <MessagePrimitive.Root className="message"><MessagePrimitive.Parts /></MessagePrimitive.Root>
}

export default function App() {
  const { session, backendConnected, command } = useVoiceSession()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [permissionHint, setPermissionHint] = useState("")
  const [failedPhotos, setFailedPhotos] = useState<string[]>([])
  const unsafeStop = isUnsafeStop(session)
  const realtime = isRealtimeVoice(session)
  const canAsk = canSendText(session, backendConnected)
  const latestTask = session.tasks.at(-1)
  const latestObservation = session.tasks.filter((task) => task.tool === "observe_scene").at(-1)
  function ask(text: string, required: (keyof Permissions)[]) {
    const missing = required.filter((key) => !session.permissions[key])
    if (missing.length) {
      setSettingsOpen(true)
      setPermissionHint(`此请求需要：${missing.map((key) => permissionCopy.find(([name]) => name === key)?.[1]).join("、")}。请自行勾选后，再发送请求。`)
      return
    }
    setPermissionHint("")
    command({ type: "text", text })
  }
  const runtime = useExternalStoreRuntime({
    messages: session.messages,
    convertMessage: (message) => ({ id: message.id, role: message.role, content: message.content }),
    // Background tools and RTC downlink are not composer submission locks.
    // Their actual progress stays in the separate status and task panels.
    isRunning: !realtime && (session.speaking || session.agentWorking),
    isDisabled: !canAsk,
    onNew: async (message) => {
      command({ type: "text", text: message.content.filter((part) => part.type === "text").map((part) => part.text).join("\n") })
    },
  })
  const status = sessionStatus(session, backendConnected)
  return <AssistantRuntimeProvider runtime={runtime}>
    <div className="workspace">
      <header className="topbar">
        <div className="identity"><span className="wordmark">WATCHE</span><span className="divider">/</span><h1>Codex，来到桌边。</h1></div>
        <div className="controls">
          <button className="primary" disabled={!backendConnected || unsafeStop || (!session.connected && (!session.deviceOnline || session.busy))} onClick={() => command({ type: session.connected ? "stop" : "start" })}>{session.connected ? "结束对话" : session.busy ? "正在连接…" : "开始对话"}</button>
          <button disabled={!backendConnected || !session.connected || unsafeStop} title={realtime ? "逻辑静音仅使上行零输入，不停止RTC媒体或下行播报" : undefined} onClick={() => command({ type: "mute", muted: !session.muted })}>{realtime ? session.muted ? "恢复上行" : "静音上行" : session.muted ? "恢复聆听" : "静音"}</button>
          <button className="danger" disabled={!backendConnected} onClick={() => command({ type: "cancel" })}>{session.stopStatus === "unconfirmed" ? "重试停止" : "停止动作与播放"}</button>
        </div>
      </header>
      <div className="statusbar" role="status" aria-live="polite"><span className={`status-dot ${isListening(session, backendConnected) ? "listening" : ""}`} /><span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{status}</span><span className="status-note">{!backendConnected ? "当前设备停止状态未知" : session.stopStatus === "confirmed" ? "设备已确认停止" : session.stopStatus === "stopping" ? "停止请求已收到…" : session.stopStatus === "unconfirmed" ? "停止未确认，请检查设备" : voiceModeLabel(session)}</span></div>
      <p className="hint">{voiceModeNotice(session)}</p>
      {realtime && <p className="hint" role="status" aria-live="polite" aria-label="RTC上行状态">{voiceInputStatus(session, backendConnected)}。AEC待实测，声学全双工尚未验收。</p>}
      {latestTask && <p className="hint" role="status" aria-live="polite" aria-label="最近任务状态">{realtime ? "最近后台任务" : "任务"} {latestTask.id.slice(0, 8)} · {taskKind(latestTask)} · {taskStage(latestTask)}：{taskSummary(latestTask)}</p>}
      {session.error && <div className="error" role="alert">{session.error}</div>}
      {session.notice && <p className="hint" role="status">{session.notice}</p>}
      {endedSessionNotice(session) && <p className="hint" role="status">{endedSessionNotice(session)}</p>}
      <div className="content-layout">
        <main>
          <ThreadPrimitive.Root className="thread"><ThreadPrimitive.Viewport className="viewport">
            <ThreadPrimitive.Empty><div className="empty"><span className="eyebrow">语音与受限工具实验版</span><h2>“Codex，看看我面前有什么。”</h2><p>机器人负责拍摄，Codex 分析真实照片。<br />只有取得照片，才会回答看见了什么。</p><p className="hint">{realtime ? "先开始对话。RTC语音保持前台连接，后台拍照、图像上传和转头仍需分别授权。" : "先开始对话，再授权所需能力。相机、麦克风和喇叭按任务切换使用。"}</p></div></ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages components={{ UserMessage: TranscriptMessage, AssistantMessage: TranscriptMessage }} />
          </ThreadPrimitive.Viewport>
          <div className="suggestions" aria-label="示例请求">{[
            { text: "看看前面有什么", required: ["camera", "upload"] as (keyof Permissions)[] },
            { text: "两侧看看", required: ["camera", "upload", "motion"] as (keyof Permissions)[] },
            { text: "把灯设为绿色，亮度0.2", required: ["lights"] as (keyof Permissions)[] },
          ].map(({ text, required }) => <button key={text} disabled={!canAsk} onClick={() => ask(text, required)}>{text}</button>)}</div>
          <ComposerPrimitive.Root className="composer"><ComposerPrimitive.Input placeholder={session.connected ? realtime ? "继续说话，或向RTC发送文字…" : "说给机器人听，或在这里发送任务…" : "开始对话后，可以说话或发送文字任务…"} aria-label="发送给 Codex 的消息" /><ComposerPrimitive.Send>发送</ComposerPrimitive.Send></ComposerPrimitive.Root>
          <p className="safety-note">{realtime ? "语音打断与喊停可靠性仍待实测，请用顶部停止按钮；停止后重新开始对话。静音上行是逻辑静音，不关闭RTC媒体或下行播报。" : "播放时听不到喊停，请用顶部停止按钮；停止后重新开始对话。长回答节选播报，对话最多显示16000字，超出会提示截断。"}暂不支持唤醒词与底盘行走。</p>
          </ThreadPrimitive.Root>
          {permissionHint && <p className="permission-hint" role="status">{permissionHint}</p>}
          <details className="settings" open={settingsOpen} onToggle={(event) => setSettingsOpen(event.currentTarget.open)}><summary>权限与设备设置 <span>仅本次会话有效</span></summary><div className="permissions">{permissionCopy.map(([key, label, description]) => <label key={key}><input type="checkbox" checked={session.permissions[key]} disabled={!backendConnected || unsafeStop} onChange={(event) => command({ type: "permissions", permissions: { [key]: event.target.checked } })} /><span>{label}<small>{description}</small></span></label>)}</div><details><summary>辅助说明：RTC链路、静音与显示</summary><p className="hint">{duplexSummary(session)}</p><p className="hint">{bodyFeedbackSummary(session)}</p>{realtime && <p className="hint">静音上行仅发送零输入，不关闭RTC媒体与硬件麦克风，也不静音下行播报。需要结束媒体和设备动作时，请使用停止按钮。</p>}</details><details><summary>高级：转头配置与限制</summary><p className="hint">{motionRangeSummary(session.motionRange)}</p></details><p className="hint">应用侧原图保存在本地内存，最多6张、15分钟自动过期；结束会话会撤销权限。已发送给 Codex 的内容不能通过清除本地照片撤回。</p><button disabled={!canRunDeviceDiagnostic(session, backendConnected)} onClick={() => command({ type: "deviceTest" })}>设备诊断：录音并回放 3 秒</button>{session.deviceTest?.playbackCompleted && <p className="hint">设备录音回放已完成，收到 {session.deviceTest.frames} 帧。</p>}</details>
        </main>
        <aside className="evidence-panel">
          <div className="panel-heading"><h2>动作与视觉证据</h2><span className="eyebrow">任务回执与真实照片</span></div>
          {session.cameraStatus === "unavailable" && <div className="camera-warning" role="status"><p>未取得照片，视觉观察不可用。</p><small>语音与灯光仍可使用；检查相机后可显式重试。</small><button disabled={!canAsk} onClick={() => ask("请重新拍摄当前视野，失败不要自行重试。", ["camera", "upload"])}>重试当前观察</button></div>}
          {latestObservation?.id === latestTask?.id && latestObservation?.status === "failed" && latestObservation.photos.length === 0 && <p className="hint">任务 {latestObservation.id.slice(0, 8)}：本次没有取得照片。</p>}
          {session.tasks.length > 0 && <ol className="task-list" aria-label="机器人任务进度">{session.tasks.slice(-4).reverse().map((task) => <li key={task.id} data-state={task.status}><div><strong>{taskStage(task)}</strong><span>{taskKind(task)}</span></div><p>{taskSummary(task)}</p><small>任务 {task.id.slice(0, 8)}</small></li>)}</ol>}
          {session.evidence.length === 0 ? <div className="photo-empty"><svg aria-hidden="true" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="M4 6h4l2-2h4l2 2h4v14H4z" /><circle cx="12" cy="12" r="3.5" /></svg><p>暂无可用照片</p><small>转头本身不生成照片。成功拍摄后的照片、时间和视角会显示在这里。</small></div> : <div className="photos">{session.evidence.slice().reverse().map((photo) => {
            const title = evidenceTitle(photo)
            return <figure key={photo.id}>{failedPhotos.includes(photo.id) ? <p className="photo-unavailable">照片已过期、清除或暂时无法加载</p> : <a href={photo.url} target="_blank" rel="noreferrer" aria-label={`打开${title}的原图`}><img src={photo.url} alt={`机器人实拍，${title}，任务${photo.taskId.slice(0, 8)}`} loading="lazy" onError={() => setFailedPhotos((ids) => [...ids, photo.id].slice(-6))} /></a>}<figcaption><strong>{title}</strong><time dateTime={photo.capturedAt}>{new Date(photo.capturedAt).toLocaleTimeString("zh-CN")}</time><small>{photo.taskId === latestTask?.id ? "本任务照片" : "历史照片"} · {photo.device} · 任务 {photo.taskId.slice(0, 8)}</small><details><summary>照片来源与校验</summary><small>照片 {photo.id}<br />视角标记 {photo.view}<br />角度为照片元数据，非独立角度测量<br />SHA-256 {photo.sha256}</small></details></figcaption></figure>
          })}</div>}
          <div className="evidence-footer"><p>照片只代表各次拍摄视野，不是360°巡视，也不保证无盲区。角度元数据与SDK回执不是独立测量，不据角度大小猜测左右。</p><button disabled={!backendConnected || session.evidence.length === 0} onClick={() => command({ type: "clearPhotos" })}>清除本地照片</button></div>
        </aside>
      </div>
    </div>
  </AssistantRuntimeProvider>
}
