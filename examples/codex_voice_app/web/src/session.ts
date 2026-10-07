export type Message = { id: string; role: "user" | "assistant"; content: string }
export type DeviceTest = { frames: number; inputBytes: number; rms: number; playbackCompleted: boolean }
export type Duplex = {
  transport: string; deviceTransport: string; microphoneConcurrent: boolean
  echoCancellation: string; expressionsEnabled: boolean
}
export type Permissions = { camera: boolean; upload: boolean; motion: boolean; lights: boolean }
export type Pace = "gentle" | "natural"
export type MotionRange = {
  panMinDeg: number; centerDeg: number; panMaxDeg: number; tiltDeg: number
  speedBudgetDegS: number; defaultPace: Pace; paces: Pace[]
  limitSource: string; directionCalibrated: boolean
}
export type RobotTask = {
  id: string; tool: string; status: string; detail: string; photos: string[]
  targetPanDeg?: number; pace?: Pace; durationMs?: number
}
export type Evidence = {
  id: string; taskId: string; view: string; capturedAt: string; sha256: string; url: string; device: string
  panDeg?: number | null; tiltDeg?: number | null
}
const interactionLabels = {
  listening: "正在聆听", acknowledging: "已收到任务（文字确认）", thinking: "Codex 正在思考任务",
  moving: "正在转头", capturing: "正在拍照", analyzing: "正在分析照片", connecting: "正在准备设备语音",
  speaking: "正在回答，麦克风暂时关闭", settling: "确认播报完成中，等待尾段，可随时停止",
  idle: "等待下一项任务", error: "本轮任务发生异常",
}
const realtimeInteractionLabels: Partial<Record<keyof typeof interactionLabels, string>> = {
  listening: "实时语音前台已连接", thinking: "后台任务正在思考", moving: "后台工具正在转头",
  capturing: "后台工具正在拍照", analyzing: "后台正在分析照片", connecting: "正在连接RTC语音",
  speaking: "正在回答（RTC前台）", settling: "RTC输出收尾中", idle: "实时语音前台等待对话",
}
export type InteractionPhase = keyof typeof interactionLabels
export type Session = {
  connected: boolean; microphone: boolean; speaking: boolean; busy: boolean; deviceOnline: boolean
  error: string; notice: string; messages: Message[]; micFrames: number; outputBytes: number
  deviceTest?: DeviceTest | null; permissions: Permissions; tasks: RobotTask[]; evidence: Evidence[]
  muted: boolean; agentWorking: boolean; stopStatus: string; stopReason: string; cameraStatus: string; speechStatus: string
  motionRange: MotionRange | null; interactionPhase: InteractionPhase | ""; interactionDetail: string
  voiceMode: "realtime" | "task" | ""; duplex: Duplex | null; bodyFeedback: "suppressed" | ""
}
export const initialSession: Session = {
  connected: false, microphone: false, speaking: false, busy: false, deviceOnline: false,
  error: "", notice: "", messages: [], micFrames: 0, outputBytes: 0,
  permissions: { camera: false, upload: false, motion: false, lights: false }, tasks: [], evidence: [],
  muted: false, agentWorking: false, stopStatus: "idle", stopReason: "", cameraStatus: "unknown", speechStatus: "idle",
  motionRange: null, interactionPhase: "", interactionDetail: "",
  voiceMode: "", duplex: null, bodyFeedback: "",
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function isFiniteNumber(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value) }
function isPace(value: unknown): value is Pace { return value === "gentle" || value === "natural" }
function isInteractionPhase(value: unknown): value is InteractionPhase {
  return typeof value === "string" && Object.hasOwn(interactionLabels, value)
}
function parseDuplex(value: unknown): Duplex | null {
  if (!isRecord(value)) return null
  const { transport, deviceTransport, microphoneConcurrent, echoCancellation, expressionsEnabled } = value
  if (typeof transport !== "string" || !transport || typeof deviceTransport !== "string" || !deviceTransport
    || typeof microphoneConcurrent !== "boolean" || typeof echoCancellation !== "string" || !echoCancellation
    || typeof expressionsEnabled !== "boolean") return null
  return { transport, deviceTransport, microphoneConcurrent, echoCancellation, expressionsEnabled }
}
function parseMotionRange(value: unknown): MotionRange | null {
  if (!isRecord(value)) return null
  const { panMinDeg, centerDeg, panMaxDeg, tiltDeg, speedBudgetDegS, defaultPace, paces, limitSource, directionCalibrated } = value
  if (!isFiniteNumber(panMinDeg) || !isFiniteNumber(centerDeg) || !isFiniteNumber(panMaxDeg)
    || !isFiniteNumber(tiltDeg) || !isFiniteNumber(speedBudgetDegS) || speedBudgetDegS <= 0
    || !(panMinDeg < centerDeg && centerDeg < panMaxDeg) || !isPace(defaultPace)
    || !Array.isArray(paces) || !paces.length || !paces.every(isPace) || !paces.includes(defaultPace)
    || typeof limitSource !== "string" || !limitSource || typeof directionCalibrated !== "boolean") return null
  return { panMinDeg, centerDeg, panMaxDeg, tiltDeg, speedBudgetDegS, defaultPace, paces: [...paces], limitSource, directionCalibrated }
}
function parseTask(value: unknown): RobotTask | null {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.tool !== "string" || typeof value.status !== "string") return null
  return {
    id: value.id, tool: value.tool, status: value.status, detail: typeof value.detail === "string" ? value.detail : "",
    photos: Array.isArray(value.photos) ? value.photos.filter((id): id is string => typeof id === "string") : [],
    targetPanDeg: isFiniteNumber(value.targetPanDeg) ? value.targetPanDeg : undefined,
    pace: isPace(value.pace) ? value.pace : undefined,
    durationMs: isFiniteNumber(value.durationMs) && value.durationMs > 0 ? value.durationMs : undefined,
  }
}
function parseEvidence(value: unknown): Evidence | null {
  if (!isRecord(value) || typeof value.id !== "string" || !/^[a-f0-9]{32}$/.test(value.id)
    || typeof value.taskId !== "string" || typeof value.view !== "string" || typeof value.capturedAt !== "string"
    || typeof value.sha256 !== "string" || value.url !== `/api/photos/${value.id}`) return null
  return {
    id: value.id, taskId: value.taskId, view: value.view, capturedAt: value.capturedAt, sha256: value.sha256,
    url: value.url, device: typeof value.device === "string" ? value.device : "设备信息未提供",
    panDeg: isFiniteNumber(value.panDeg) ? value.panDeg : null,
    tiltDeg: isFiniteNumber(value.tiltDeg) ? value.tiltDeg : null,
  }
}
const paceLabels: Record<Pace, string> = { gentle: "轻柔", natural: "自然" }
export function motionRangeSummary(range: MotionRange | null) {
  if (!range) return "尚未收到有效运动配置，不能确认横向行程或精确左右。"
  const source = range.limitSource === "firmware-profile" ? "固件配置" : `运动配置（来源：${range.limitSource}）`
  const direction = range.directionCalibrated ? "后端报告方向已标定，照片仍按各自角度元数据标记" : "精确左右未标定"
  return `${source}：横向${range.panMinDeg}–${range.panMaxDeg}°，中心${range.centerDeg}°，俯仰${range.tiltDeg}°；速度预算${range.speedBudgetDegS}°/秒，非实测速度。默认节奏${paceLabels[range.defaultPace]}，可选${range.paces.map((pace) => paceLabels[pace]).join("、")}。非实时限位读数；${direction}。`
}
export function evidenceTitle(photo: Evidence) {
  const views: Record<string, string> = { current: "当前视野，不转头", center: "中心", pan_min: "较小角度端", pan_max: "较大角度端" }
  const view = Object.hasOwn(views, photo.view) ? views[photo.view] : "未识别视角"
  const pan = isFiniteNumber(photo.panDeg) ? `横向元数据${photo.panDeg}°` : "横向角度未测"
  const tilt = isFiniteNumber(photo.tiltDeg) ? `俯仰元数据${photo.tiltDeg}°` : "俯仰角度未测"
  return `${view} · ${pan} · ${tilt}`
}
export function taskKind(task: RobotTask) {
  return task.tool === "aim_camera" ? "转头" : task.tool === "observe_scene" ? "观察" : task.tool === "set_lights" ? "灯光" : "设备动作"
}
export function taskSummary(task: RobotTask) {
  const metadata = [
    isFiniteNumber(task.targetPanDeg) ? `目标横向${task.targetPanDeg}°` : "",
    isPace(task.pace) ? `节奏${paceLabels[task.pace]}` : "",
    isFiniteNumber(task.durationMs) && task.durationMs > 0 ? `计划时长${task.durationMs / 1000}秒` : "",
  ].filter(Boolean).join("，")
  const detail = task.tool === "aim_camera" && task.status === "completed"
    ? "SDK 转头完成回执已收到，非独立角度测量；转头不等于视觉观察。"
    : task.detail || (task.status === "failed" ? "本次动作未完成" : task.status === "cancelled" ? "本次动作已取消" : "检查本次操作权限")
  return metadata ? `${detail} ${metadata}` : detail
}
export function isRealtimeVoice(session: Session) { return session.voiceMode === "realtime" }
function hasConcurrentRtc(session: Session) {
  return isRealtimeVoice(session) && session.duplex?.transport === "webrtc"
    && session.duplex.deviceTransport === "webrtc" && session.duplex.microphoneConcurrent
}
export function canSendText(session: Session, backendConnected: boolean) {
  return backendConnected && session.connected && session.deviceOnline && !session.busy && !isUnsafeStop(session)
    && (isRealtimeVoice(session) || (!session.agentWorking && !session.speaking))
}
export function voiceModeLabel(session: Session) {
  return isRealtimeVoice(session) ? "RTC实时语音实验" : "语音任务对话 · 兼容模式"
}
export function voiceModeNotice(session: Session) {
  if (!isRealtimeVoice(session)) return "当前按语音任务兼容模式显示，非低延迟实时语音。文字确认不代表机器人已开口。"
  return hasConcurrentRtc(session)
    ? "RTC语音前台与后台工具分离，后台工具单并发。工具执行期间仍可说话或向RTC发送文字；新任务是否执行以后台回执为准，文字状态不代表设备已开口。"
    : "已报告RTC模式，但媒体并发配置未确认。后台工具单并发，文字输入与工具状态分开显示。"
}
export function duplexSummary(session: Session) {
  if (!isRealtimeVoice(session) || !session.duplex) return "实时RTC媒体配置未确认；AEC待实测，声学全双工尚未验收。"
  const media = hasConcurrentRtc(session)
    ? "RTC媒体同时收发：模型与设备链路均为WebRTC（后端配置报告）"
    : `RTC媒体并发未确认：模型链路${session.duplex.transport}，设备链路${session.duplex.deviceTransport}，并发上行配置${session.duplex.microphoneConcurrent ? "开启" : "关闭"}`
  return `${media}。AEC待实测（后端标记：${session.duplex.echoCancellation}）；声学全双工尚未验收，传输配置不代表真实回声消除效果。`
}
export function bodyFeedbackSummary(session: Session) {
  if (session.bodyFeedback !== "suppressed" || !session.duplex) return "显示接管状态未确认。"
  if (session.duplex.expressionsEnabled) return "显示反馈配置不一致，不能确认黑屏接管，请核对后端。"
  return "黑屏显示：应用以黑色静态画面接管，抑制默认动画（后端报告）；不代表底层renderer关闭或CPU资源已释放。"
}
export function voiceInputStatus(session: Session, backendConnected: boolean) {
  if (!backendConnected) return "上行状态未知，页面连接已断开"
  if (isUnsafeStop(session)) return "上行停止待确认，请使用停止按钮并检查设备"
  if (!session.connected || !session.deviceOnline) return "上行未连接"
  if (session.busy) return "正在连接，上行状态待确认"
  if (isRealtimeVoice(session)) {
    if (!hasConcurrentRtc(session)) return "上行与RTC媒体并发配置未确认"
    if (session.muted) return "上行逻辑静音（零输入），RTC媒体仍运行，下行播报不受此开关影响"
    return session.microphone ? "上行麦克风开启，可继续说话（后端状态，识别效果待实测）" : "上行麦克风未开启，请核对设备RTC链路"
  }
  return isListening(session, backendConnected) ? "正在聆听" : session.muted ? "麦克风已静音" : "任务语音兼容模式，收音状态以任务阶段为准"
}
export function isListening(session: Session, backendConnected: boolean) {
  if (!backendConnected || !session.connected || !session.deviceOnline || !session.microphone
    || session.busy || session.muted || isUnsafeStop(session)) return false
  if (isRealtimeVoice(session)) return hasConcurrentRtc(session)
  return !session.agentWorking && !session.speaking && (!session.interactionPhase || session.interactionPhase === "listening")
}
export function sessionStatus(session: Session, backendConnected: boolean) {
  if (!backendConnected) return "连接 Application 中…"
  if (session.stopStatus === "stopping") return "正在停止动作与播放"
  if (session.stopStatus === "unconfirmed") return "停止未确认，请重试停止并检查设备"
  const phase = session.interactionPhase
  if (isInteractionPhase(phase) && (session.connected || session.busy || phase === "connecting" || phase === "error")
    && (phase !== "listening" || isListening(session, backendConnected))) {
    const label = isRealtimeVoice(session) ? realtimeInteractionLabels[phase] ?? interactionLabels[phase] : interactionLabels[phase]
    const detail = session.interactionDetail.trim()
    return detail && detail !== label ? `${label}：${detail}` : label
  }
  if (session.busy) return isRealtimeVoice(session) ? "正在连接RTC语音…" : "连接 Codex 中…"
  if (!session.deviceOnline) return "机器人离线"
  if (!session.connected) return "机器人在线，等待开始"
  if (session.speaking) return isRealtimeVoice(session) ? "正在回答（RTC前台）" : "正在回答，麦克风暂时关闭"
  if (session.agentWorking) {
    if (isRealtimeVoice(session)) return "后台工具任务处理中"
    if (session.speechStatus === "connecting") return "正在准备设备语音"
    if (session.speechStatus === "requested") return "等待本轮语音输出"
    if (session.speechStatus === "settling") return "确认播报完成中，等待尾段，可随时停止"
    return "Codex 正在处理任务"
  }
  if (isRealtimeVoice(session)) return voiceInputStatus(session, backendConnected)
  return isListening(session, backendConnected) ? "正在聆听" : session.muted ? "麦克风已静音" : "设备操作中，麦克风暂时关闭"
}
export function endedSessionNotice(session: Session) {
  if (session.connected || session.busy || !session.stopReason) return ""
  const reasons: Record<string, string> = { "runtime-error": "运行异常", "start-failed": "启动未完成", "browser-disconnected": "页面连接断开", "device-disconnected": "机器人连接断开", "no-speech-output": "本轮没有收到语音", "permission-revoked": "你撤销了能力权限", "device-action-unconfirmed": "设备动作未确认", "user-stop": "你结束了对话", "user-cancel": "你停止了动作与播放", "application-shutdown": "Application 已关闭" }
  return `会话已结束：${reasons[session.stopReason] ?? "连接已停止"}。重新开始前请检查设备状态。`
}
export function isUnsafeStop(session: Session) { return ["stopping", "unconfirmed"].includes(session.stopStatus) }
export function canRunDeviceDiagnostic(session: Session, backendConnected: boolean) { return backendConnected && session.deviceOnline && !session.connected && !session.busy && !isUnsafeStop(session) }
export function reduceEvent(state: Session, event: Record<string, unknown>): Session {
  switch (event.type) {
    case "snapshot": return {
      connected: event.connected === true, microphone: event.microphone === true,
      speaking: event.speaking === true, busy: event.busy === true, deviceOnline: event.deviceOnline === true,
      error: typeof event.error === "string" ? event.error : "",
      notice: typeof event.notice === "string" ? event.notice : "",
      messages: Array.isArray(event.messages) ? event.messages.filter((m): m is Message =>
        !!m && typeof m.id === "string" && ["user", "assistant"].includes(m.role) && typeof m.content === "string").slice(-100) : [],
      micFrames: typeof event.micFrames === "number" ? event.micFrames : 0,
      outputBytes: typeof event.outputBytes === "number" ? event.outputBytes : 0,
      deviceTest: event.deviceTest as DeviceTest | null | undefined,
      permissions: Object.fromEntries(Object.keys(initialSession.permissions).map((key) => [key, (event.permissions as Record<string, unknown> | undefined)?.[key] === true])) as Permissions,
      tasks: Array.isArray(event.tasks) ? event.tasks.map(parseTask).filter((task): task is RobotTask => task !== null).slice(-20) : [],
      evidence: Array.isArray(event.evidence) ? event.evidence.map(parseEvidence).filter((photo): photo is Evidence => photo !== null).slice(-6) : [],
      muted: event.muted === true, agentWorking: event.agentWorking === true,
      stopStatus: typeof event.stopStatus === "string" ? event.stopStatus : "idle",
      stopReason: typeof event.stopReason === "string" ? event.stopReason : "",
      cameraStatus: typeof event.cameraStatus === "string" ? event.cameraStatus : "unknown",
      speechStatus: typeof event.speechStatus === "string" ? event.speechStatus : "idle",
      motionRange: parseMotionRange(event.motionRange),
      interactionPhase: isInteractionPhase(event.interactionPhase) ? event.interactionPhase : "",
      interactionDetail: isInteractionPhase(event.interactionPhase) && typeof event.interactionDetail === "string" ? event.interactionDetail : "",
      voiceMode: event.voiceMode === "realtime" || event.voiceMode === "task" ? event.voiceMode : "",
      duplex: parseDuplex(event.duplex),
      bodyFeedback: event.bodyFeedback === "suppressed" ? "suppressed" : "",
    }
    case "state": return { ...state, connected: event.connected === true, microphone: event.microphone === true, speaking: event.speaking === true }
    case "closed": return { ...state, connected: false, microphone: false, speaking: false, busy: false, deviceOnline: false, agentWorking: false, motionRange: null, interactionPhase: "", interactionDetail: "", voiceMode: "", duplex: null, bodyFeedback: "", stopStatus: isUnsafeStop(state) ? state.stopStatus : "unknown" }
    case "error": return { ...state, error: typeof event.message === "string" ? event.message : "会话发生错误" }
    case "transcript": {
      if (typeof event.id !== "string" || typeof event.text !== "string" || !["user", "assistant"].includes(String(event.role))) return state
      const message: Message = { id: event.id, role: event.role as Message["role"], content: event.text }
      return { ...state, messages: state.messages.some((m) => m.id === message.id) ? state.messages.map((m) => m.id === message.id ? message : m) : [...state.messages, message] }
    }
    default: return state
  }
}
