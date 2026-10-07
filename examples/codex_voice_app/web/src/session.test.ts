import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"
import { initialSession, reduceEvent, isUnsafeStop, canRunDeviceDiagnostic, endedSessionNotice, sessionStatus, isListening, evidenceTitle, motionRangeSummary, taskKind, taskSummary, canSendText, voiceInputStatus, duplexSummary, bodyFeedbackSummary } from "./session"
import type { Evidence, Session } from "./session"
import App from "./App"

let uiSession = initialSession
vi.mock("./useVoiceSession", () => ({
  useVoiceSession: () => ({ session: uiSession, backendConnected: true, command: vi.fn() }),
}))

const motionRange = {
  panMinDeg: 30, centerDeg: 90, panMaxDeg: 150, tiltDeg: 120,
  speedBudgetDegS: 60, defaultPace: "natural", paces: ["gentle", "natural"],
  limitSource: "firmware-profile", directionCalibrated: false,
}
function photo(values: Partial<Evidence> = {}): Evidence {
  return { id: "a".repeat(32), taskId: "observation", view: "current", capturedAt: "2026-10-07T12:00:00Z", sha256: "real-hash", url: `/api/photos/${"a".repeat(32)}`, device: "配对机器人", ...values }
}
function renderApp(values: Partial<Session> = {}) {
  uiSession = { ...initialSession, connected: true, deviceOnline: true, ...values }
  return renderToStaticMarkup(createElement(App))
}

const duplex = {
  transport: "webrtc", deviceTransport: "webrtc", microphoneConcurrent: true,
  echoCancellation: "unverified", expressionsEnabled: false,
}
function realtime(values: Record<string, unknown> = {}) {
  return reduceEvent(initialSession, {
    type: "snapshot", connected: true, deviceOnline: true, microphone: true,
    voiceMode: "realtime", duplex, bodyFeedback: "suppressed", ...values,
  })
}

describe("session events", () => {
  it("shows conservative speech settling separately from thinking and listening", () => {
    const state = { ...initialSession, connected: true, deviceOnline: true, agentWorking: true, microphone: true, speechStatus: "settling" }
    expect(sessionStatus(state, true)).toContain("确认播报完成")
    expect(sessionStatus({ ...state, speaking: true }, true)).toContain("正在回答")
    expect(sessionStatus({ ...state, agentWorking: false }, true)).toBe("正在聆听")
  })
  it("gives physical stop and unconfirmed stop priority over stale speech state", () => {
    const state = { ...initialSession, connected: true, deviceOnline: true, speaking: true, agentWorking: true, speechStatus: "settling" }
    expect(sessionStatus({ ...state, stopStatus: "stopping" }, true)).toBe("正在停止动作与播放")
    expect(sessionStatus({ ...state, stopStatus: "unconfirmed" }, true)).toContain("停止未确认")
  })
  it("keeps a rejected later voice segment as notice, separate from real errors", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", notice: "后续语音未执行", error: "设备掉线" })
    expect(state.notice).toBe("后续语音未执行")
    expect(state.error).toBe("设备掉线")
    expect(reduceEvent(state, { type: "snapshot", notice: 42 }).notice).toBe("")
  })
  it("shows why an ended session stopped, without mislabelling device confirmation", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", connected: false, stopReason: "runtime-error", stopStatus: "confirmed" })
    expect(endedSessionNotice(state)).toContain("运行异常")
    expect(endedSessionNotice({ ...state, connected: true })).toBe("")
    expect(endedSessionNotice({ ...state, stopReason: "browser-disconnected" })).toContain("页面连接断开")
  })
  it("blocks diagnostic and unsafe controls until stop is confirmed", () => {
    for (const stopStatus of ["stopping", "unconfirmed"]) {
      const state = { ...initialSession, stopStatus, deviceOnline: true }
      expect(isUnsafeStop(state)).toBe(true)
      expect(canRunDeviceDiagnostic(state, true)).toBe(false)
    }
    expect(canRunDeviceDiagnostic({ ...initialSession, deviceOnline: true, stopStatus: "confirmed" }, true)).toBe(true)
    expect(canRunDeviceDiagnostic({ ...initialSession, deviceOnline: true }, false)).toBe(false)
  })
  it("preserves explicit camera failure instead of claiming observation is ready", () => {
    expect(reduceEvent(initialSession, { type: "snapshot", cameraStatus: "unavailable" }).cameraStatus).toBe("unavailable")
  })
  it("starts disconnected without fabricated messages", () => {
    expect(initialSession.connected).toBe(false)
    expect(initialSession.messages).toEqual([])
  })
  it("replaces a transcript by stable id instead of duplicating it", () => {
    const event = { type: "transcript", id: "a", role: "assistant", text: "你好" } as const
    const first = reduceEvent(initialSession, event)
    expect(reduceEvent(first, { ...event, text: "你好，机器人" }).messages).toEqual([
      { id: "a", role: "assistant", content: "你好，机器人" },
    ])
  })
  it("a disconnect clears recording and playback flags", () => {
    const state = { ...initialSession, connected: true, microphone: true, speaking: true, stopStatus: "confirmed" }
    expect(reduceEvent(state, { type: "closed" })).toMatchObject({
      connected: false, microphone: false, speaking: false, stopStatus: "unknown",
    })
  })
  it("ignores unknown events", () => {
    expect(reduceEvent(initialSession, { type: "future-event" })).toBe(initialSession)
  })
  it("never fabricates permissions, tasks or photos and only accepts local photo URLs", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", permissions: { camera: true },
      tasks: [{ id: "task", tool: "observe_scene", status: "capturing", detail: "拍照", photos: [] }],
      evidence: [{ id: "abc", taskId: "task", url: "https://evil.example/photo", view: "current", capturedAt: "now", sha256: "hash" }] })
    expect(state.permissions).toEqual({ camera: true, upload: false, motion: false, lights: false })
    expect(state.tasks[0].status).toBe("capturing")
    expect(state.evidence).toEqual([])
  })
  it("restores the complete server snapshot when reconnecting", () => {
    const state = reduceEvent(initialSession, {
      type: "snapshot", connected: true, busy: false, deviceOnline: true,
      microphone: true, speaking: false, error: "", micFrames: 20, outputBytes: 4800,
      messages: [{ id: "real", role: "user", content: "你好" }],
    })
    expect(state).toMatchObject({ connected: true, deviceOnline: true, micFrames: 20 })
    expect(state.messages[0].content).toBe("你好")
    expect(reduceEvent(state, { type: "closed" }).busy).toBe(false)
  })
})

describe("truthful body and interaction metadata", () => {
  it("restores the supplied motion configuration without inventing default hardware readings", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", motionRange })
    expect(state.motionRange).toEqual(motionRange)
    expect(initialSession.motionRange).toBeNull()
    expect(reduceEvent(state, { type: "snapshot" }).motionRange).toBeNull()
  })
  it("rejects malformed configuration and does not silently supply the expected travel", () => {
    for (const invalid of [null, {}, { ...motionRange, panMinDeg: "30" }, { ...motionRange, centerDeg: 160 }, { ...motionRange, speedBudgetDegS: NaN }, { ...motionRange, paces: ["unlimited"] }]) {
      expect(reduceEvent(initialSession, { type: "snapshot", motionRange: invalid }).motionRange).toBeNull()
    }
    expect(motionRangeSummary(null)).toContain("尚未收到")
    expect(motionRangeSummary(null)).not.toContain("30")
  })
  it("describes supplied travel and pace as configuration rather than real-time limits or measured speed", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", motionRange })
    const summary = motionRangeSummary(state.motionRange)
    for (const copy of ["30–150°", "90°", "120°", "60°/秒", "非实时限位读数", "精确左右未标定", "自然", "轻柔", "非实测速度"]) expect(summary).toContain(copy)
    const changed = reduceEvent(initialSession, { type: "snapshot", motionRange: { ...motionRange, panMinDeg: 40, centerDeg: 95, panMaxDeg: 140 } })
    expect(motionRangeSummary(changed.motionRange)).toContain("40–140°")
  })
  it("retains nullable photo angles and never derives pose from a view name", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", evidence: [photo({ view: "center", panDeg: null, tiltDeg: null })] })
    expect(state.evidence[0]).toMatchObject({ panDeg: null, tiltDeg: null })
    const title = evidenceTitle(state.evidence[0])
    expect(title).toContain("中心")
    expect(title).toContain("未测")
    expect(title).not.toContain("90°")
    expect(title).not.toContain("100°")
  })
  it("labels both endpoints from their own metadata without claiming left or right", () => {
    const min = evidenceTitle(photo({ view: "pan_min", panDeg: 30, tiltDeg: 120 }))
    const max = evidenceTitle(photo({ view: "pan_max", panDeg: 150, tiltDeg: 120 }))
    expect(min).toContain("较小角度端")
    expect(min).toContain("30°")
    expect(max).toContain("较大角度端")
    expect(max).toContain("150°")
    for (const title of [min, max]) {
      expect(title).toContain("120°")
      expect(title).not.toMatch(/左|右|100°/)
    }
  })
  it("shows unknown or legacy photo views conservatively, even alongside a newer range", () => {
    for (const view of ["pan80", "pan100", "future-view"]) {
      const title = evidenceTitle(photo({ view }))
      expect(title).toContain("未识别视角")
      expect(title).not.toMatch(/80°|100°|左|右/)
    }
    const state = reduceEvent(initialSession, { type: "snapshot", evidence: [photo({ view: "pan_min", panDeg: "30", tiltDeg: Infinity } as unknown as Partial<Evidence>)] })
    expect(evidenceTitle(state.evidence[0])).toContain("未测")
    expect(evidenceTitle(state.evidence[0])).not.toMatch(/Infinity|30°/)
  })
  it("accepts motion-only tasks without photos and labels only the SDK completion receipt", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", tasks: [{ id: "turn-only", tool: "aim_camera", status: "completed", detail: "", targetPanDeg: 150, pace: "natural", durationMs: 4000 }] })
    const task = state.tasks[0]
    expect(task.photos).toEqual([])
    expect(task).toMatchObject({ targetPanDeg: 150, pace: "natural", durationMs: 4000 })
    expect(taskKind(task)).toBe("转头")
    const summary = taskSummary(task)
    for (const copy of ["SDK", "回执", "非独立角度测量", "150°", "自然", "4秒"]) expect(summary).toContain(copy)
    expect(summary).not.toMatch(/已看见|实测到位|已回正/)
  })
  it("keeps a failed or cancelled motion task distinct from successful SDK completion", () => {
    for (const status of ["failed", "cancelled"]) {
      const state = reduceEvent(initialSession, { type: "snapshot", tasks: [{ id: "turn", tool: "aim_camera", status, detail: "设备动作未确认", photos: [] }] })
      expect(taskSummary(state.tasks[0])).toContain("设备动作未确认")
      expect(taskSummary(state.tasks[0])).not.toContain("完成回执已收到")
    }
  })
  it("prioritizes every real interaction phase and its detail over stale legacy flags", () => {
    const phases = {
      acknowledging: "文字确认", thinking: "思考", moving: "转头", capturing: "拍照", analyzing: "分析照片",
      connecting: "准备设备语音", speaking: "正在回答", settling: "等待尾段", idle: "等待下一项任务", error: "异常",
    }
    for (const [interactionPhase, copy] of Object.entries(phases)) {
      const state = reduceEvent(initialSession, { type: "snapshot", connected: true, deviceOnline: true, busy: true, microphone: true, speaking: true, agentWorking: true, interactionPhase, interactionDetail: "真实阶段详情" })
      expect(state.interactionPhase).toBe(interactionPhase)
      expect(sessionStatus(state, true)).toContain(copy)
      expect(sessionStatus(state, true)).toContain("真实阶段详情")
      expect(sessionStatus({ ...state, stopStatus: "stopping" }, true)).toBe("正在停止动作与播放")
      expect(sessionStatus({ ...state, stopStatus: "unconfirmed" }, true)).toContain("停止未确认")
      expect(sessionStatus(state, false)).toContain("连接 Application")
    }
  })
  it("does not light the listening dot while a task, playback, mute or stop is active", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", connected: true, deviceOnline: true, microphone: true, interactionPhase: "listening", interactionDetail: "" })
    expect(sessionStatus(state, true)).toBe("正在聆听")
    expect(isListening(state, true)).toBe(true)
    for (const patch of [{ agentWorking: true }, { speaking: true }, { muted: true }, { busy: true }, { stopStatus: "stopping" }, { stopStatus: "unconfirmed" }]) expect(isListening({ ...state, ...patch }, true)).toBe(false)
    expect(isListening(state, false)).toBe(false)
    const moving = reduceEvent(state, { type: "snapshot", connected: true, deviceOnline: true, microphone: true, interactionPhase: "moving" })
    expect(isListening(moving, true)).toBe(false)
  })
  it("clears stale phases on disconnect and falls back safely for an unknown protocol phase", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", connected: true, deviceOnline: true, motionRange, interactionPhase: "moving", interactionDetail: "正在执行本轮动作" })
    expect(reduceEvent(state, { type: "closed" })).toMatchObject({ motionRange: null, interactionPhase: "", interactionDetail: "" })
    const unknown = reduceEvent(initialSession, { type: "snapshot", connected: true, deviceOnline: true, agentWorking: true, interactionPhase: "future", interactionDetail: "不要误示聆听" })
    expect(unknown.interactionPhase).toBe("")
    expect(sessionStatus(unknown, true)).toBe("Codex 正在处理任务")
  })
  it("keeps the backend's actual error phase visible when the device goes offline", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", deviceOnline: false, connected: false, interactionPhase: "error", interactionDetail: "运动期间设备断连" })
    expect(sessionStatus(state, true)).toContain("异常")
    expect(sessionStatus(state, true)).toContain("运动期间设备断连")
    expect(sessionStatus({ ...state, stopStatus: "unconfirmed" }, true)).toContain("停止未确认")
  })
})

describe("body-aware task UI", () => {
  it("shows honest permissions, advanced configuration and task-dialogue expectations", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", motionRange })
    const html = renderApp({ motionRange: state.motionRange })
    for (const copy of ["允许转头观察", "固件行程", "随时可停止", "两侧看看", "30–150°", "非实时限位读数", "精确左右未标定", "非低延迟实时语音"]) expect(html).toContain(copy)
    expect(html).not.toMatch(/80–100|小范围/)
  })
  it("renders a motion-only action and keeps previous observation photos historical", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", tasks: [{ id: "turn-only", tool: "aim_camera", status: "completed", detail: "", targetPanDeg: 150, pace: "natural", durationMs: 4000 }], evidence: [photo({ view: "center", panDeg: 95, tiltDeg: null })] })
    const html = renderApp({ tasks: state.tasks, evidence: state.evidence })
    for (const copy of ["转头", "SDK", "非独立角度测量", "95°", "历史照片"]) expect(html).toContain(copy)
    expect(html).not.toMatch(/舵机90°|舵机100°|本次没有取得照片/)
  })
  it("renders received-task text confirmation without pretending the device has spoken", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", interactionPhase: "acknowledging", interactionDetail: "本轮请求已进入处理", agentWorking: true, microphone: true })
    const html = renderApp({ interactionPhase: state.interactionPhase, interactionDetail: state.interactionDetail, agentWorking: true, microphone: true })
    expect(html).toContain("文字确认")
    expect(html).toContain("本轮请求已进入处理")
    expect(html).not.toContain('class="status-dot listening"')
    expect(html).not.toMatch(/已语音确认|已经说|已播报确认/)
  })
  it("renders stop above the phase and does not invent a range before the backend supplies one", () => {
    const state = reduceEvent(initialSession, { type: "snapshot", interactionPhase: "settling", interactionDetail: "等待最后一段", stopStatus: "stopping", agentWorking: true, microphone: true })
    const html = renderApp({ interactionPhase: state.interactionPhase, interactionDetail: state.interactionDetail, stopStatus: "stopping", agentWorking: true, microphone: true })
    expect(html).toContain("正在停止动作与播放")
    expect(html).not.toContain("确认播报完成中")
    expect(html).not.toContain('class="status-dot listening"')
    expect(html).toContain("尚未收到有效运动配置")
    expect(html).not.toContain("30–150°")
  })
})

describe("continuous RTC foreground", () => {
  it("preserves the exact RTC and black-screen contract without defaulting an unknown backend to realtime", () => {
    const state = realtime()
    expect(state).toMatchObject({ voiceMode: "realtime", duplex, bodyFeedback: "suppressed" })
    expect(initialSession).toMatchObject({ voiceMode: "", duplex: null, bodyFeedback: "" })
    expect(reduceEvent(state, { type: "snapshot" })).toMatchObject({ voiceMode: "", duplex: null, bodyFeedback: "" })
  })
  it("does not infer concurrent media or display suppression from malformed or partial metadata", () => {
    for (const invalid of [null, {}, { ...duplex, deviceTransport: null }, { ...duplex, microphoneConcurrent: "true" }, { ...duplex, expressionsEnabled: "false" }]) {
      const state = realtime({ duplex: invalid })
      expect(state.duplex).toBeNull()
      expect(duplexSummary(state)).toContain("未确认")
      expect(isListening({ ...state, speaking: true, agentWorking: true }, true)).toBe(false)
    }
    const state = realtime({ voiceMode: "future-mode", bodyFeedback: "disabled" })
    expect(state.voiceMode).toBe("")
    expect(state.bodyFeedback).toBe("")
  })
  it("allows RTC text during tools and speech, but preserves busy, disconnection and unsafe-stop guards", () => {
    const state = realtime({ agentWorking: true, speaking: true, interactionPhase: "moving" })
    expect(canSendText(state, true)).toBe(true)
    for (const patch of [{ busy: true }, { connected: false }, { deviceOnline: false }, { stopStatus: "stopping" }, { stopStatus: "unconfirmed" }]) expect(canSendText({ ...state, ...patch }, true)).toBe(false)
    expect(canSendText(state, false)).toBe(false)
    expect(canSendText({ ...state, muted: true }, true)).toBe(true)
    const legacy = { ...initialSession, connected: true, deviceOnline: true, agentWorking: true }
    expect(canSendText(legacy, true)).toBe(false)
  })
  it("shows the RTC upstream as active during background phases and downlink playback", () => {
    for (const interactionPhase of ["thinking", "moving", "capturing", "analyzing", "speaking", "settling"]) {
      const state = realtime({ agentWorking: true, speaking: true, interactionPhase })
      expect(isListening(state, true)).toBe(true)
      expect(voiceInputStatus(state, true)).toContain("上行麦克风开启")
      expect(voiceInputStatus(state, true)).toContain("可继续说话")
      expect(sessionStatus(state, true)).not.toContain("麦克风暂时关闭")
    }
  })
  it("treats muted as logical zero input, not physical microphone shutdown or RTC disconnection", () => {
    const state = realtime({ muted: true, microphone: true, speaking: true })
    expect(state.connected).toBe(true)
    expect(state.microphone).toBe(true)
    expect(isListening(state, true)).toBe(false)
    const status = voiceInputStatus(state, true)
    for (const copy of ["逻辑静音", "零输入", "RTC媒体仍运行"]) expect(status).toContain(copy)
    expect(status).not.toMatch(/硬件麦克风已关闭|RTC已断开/)
    expect(sessionStatus(state, true)).not.toContain("麦克风暂时关闭")
  })
  it("never advertises active upstream or permits text while stop is pending or unconfirmed", () => {
    for (const stopStatus of ["stopping", "unconfirmed"]) {
      const state = realtime({ speaking: true, agentWorking: true, stopStatus, interactionPhase: "speaking" })
      expect(sessionStatus(state, true)).toContain(stopStatus === "stopping" ? "正在停止" : "停止未确认")
      expect(isListening(state, true)).toBe(false)
      expect(canSendText(state, true)).toBe(false)
      expect(voiceInputStatus(state, true)).not.toContain("可继续说话")
    }
  })
  it("clears stale RTC and display configuration when the page disconnects", () => {
    const state = reduceEvent(realtime({ speaking: true }), { type: "closed" })
    expect(state).toMatchObject({ voiceMode: "", duplex: null, bodyFeedback: "", connected: false, microphone: false, speaking: false })
    expect(isListening(state, true)).toBe(false)
    expect(voiceInputStatus(state, false)).toContain("未知")
  })
  it("describes both WebRTC legs and leaves AEC and acoustic full duplex pending evidence", () => {
    const copy = duplexSummary(realtime())
    for (const text of ["RTC媒体同时收发", "WebRTC", "设备", "AEC待实测", "声学全双工尚未验收"]) expect(copy).toContain(text)
    const configuredOnly = duplexSummary(realtime({ duplex: { ...duplex, echoCancellation: "verified" } }))
    expect(configuredOnly).toContain("声学全双工尚未验收")
    expect(configuredOnly).not.toContain("AEC验收通过")
    expect(duplexSummary(realtime({ duplex: { ...duplex, deviceTransport: "uart" } }))).toContain("未确认")
  })
  it("describes suppressed body feedback as black static takeover, never renderer shutdown or CPU savings", () => {
    const copy = bodyFeedbackSummary(realtime())
    for (const text of ["黑屏显示", "黑色静态", "不代表", "renderer", "CPU"]) expect(copy).toContain(text)
    expect(copy).not.toMatch(/全部表情已关闭|全部renderer已关闭|已释放CPU/)
    expect(bodyFeedbackSummary(realtime({ duplex: { ...duplex, expressionsEnabled: true } }))).toContain("不一致")
    expect(bodyFeedbackSummary(initialSession)).toContain("未确认")
  })
})

describe("realtime page adaptation", () => {
  it("keeps the RTC composer and quick requests enabled while a background tool and speech are active", () => {
    const html = renderApp(realtime({ agentWorking: true, speaking: true, interactionPhase: "moving", interactionDetail: "转头工具执行中" }))
    const input = html.match(/<textarea\b[^>]*>/)?.[0]
    expect(input).toBeTruthy()
    expect(input).not.toContain("disabled")
    expect(html).toContain('<button>两侧看看</button>')
    expect(html).toContain('class="status-dot listening"')
    expect(html).toContain("上行麦克风开启")
    expect(html).toContain("转头工具执行中")
  })
  it("shows experimental RTC, AEC and black-screen copy without obsolete half-duplex claims", () => {
    const html = renderApp(realtime({ speaking: true, interactionPhase: "speaking" }))
    for (const copy of ["RTC实时语音实验", "RTC媒体同时收发", "AEC待实测", "黑屏显示", "后台工具单并发", "声学全双工尚未验收"]) expect(html).toContain(copy)
    expect(html).not.toMatch(/半双工|非低延迟实时语音|播放时听不到喊停|麦克风暂时关闭|相机、麦克风和喇叭按任务切换使用|全部表情已关闭/)
    expect(html).toContain("请用顶部停止按钮")
  })
  it("labels logical upstream mute while keeping text entry and downlink independent", () => {
    const html = renderApp(realtime({ muted: true, speaking: true, agentWorking: true }))
    expect(html).toContain("恢复上行")
    expect(html).toContain("逻辑静音")
    expect(html).toContain("零输入")
    expect(html).not.toContain('class="status-dot listening"')
    expect(html.match(/<textarea\b[^>]*>/)?.[0]).not.toContain("disabled")
  })
  it("retains immediate stop, permission gates and real-photo evidence in RTC mode", () => {
    const state = realtime({ stopStatus: "unconfirmed", speaking: true, agentWorking: true, evidence: [photo({ view: "pan_min", panDeg: 30, tiltDeg: 120 })] })
    const html = renderApp(state)
    expect(html).toContain('<button class="danger">重试停止</button>')
    expect(html).toContain("停止未确认")
    expect(html.match(/<textarea\b[^>]*>/)?.[0]).toContain("disabled")
    expect(html).not.toContain('class="status-dot listening"')
    expect(html).toContain('type="checkbox" disabled=""')
    expect(html).toContain(`/api/photos/${"a".repeat(32)}`)
    expect(html).toContain("较小角度端")
    expect(html).toContain("SHA-256 real-hash")
  })
})
