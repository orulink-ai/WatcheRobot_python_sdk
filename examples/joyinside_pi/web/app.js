"use strict";
const $ = (id) => document.getElementById(id);
const phases = {
  paused: ["准备好再开始", "待机中，机器人麦克风已关闭。"],
  connecting: ["正在准备下一轮", "连接机器人语音能力，稍等片刻。"],
  listening: ["轮到你说话了", "请对机器人说话，停顿后会自动开始回答。"],
  acknowledging: ["Pi 正在回应", "这句话播完后，Pi 会开始执行操作。"],
  thinking: ["Pi 正在思考", "正在处理你刚才说的话，机器人麦克风已关闭。"],
  speaking: ["机器人正在回答", "回复播报完毕后，会自动再次聆听。"],
  stopping: ["正在暂停", "等待当前操作取消并释放机器人麦克风。"],
  error: ["暂时无法对话", "请查看下方提示，确认机器人连接和 Pi 配置。"],
};
let state = { enabled: false, connected: false };
let connection = { state: "idle", online: false };
let pairingPending = false;
let connectionUnavailable = true;
let pairingFeedback = "";
let backendPending = false;
let backendFeedback = "";
let backendOptionsSignature = "";
const speechBackends = {
  joyinside: { label: "机器人 JoyInside（设备端识别与播报）", short: "JoyInside 识别与播报", detail: "使用机器人已有的 JoyInside 配置" },
  doubao: { label: "豆包（Application 识别与播报）", short: "豆包识别与播报", detail: "Application 调用豆包云端语音服务" },
};
const emotions = [
  ["平静", "自然待机"], ["微笑", "友好回应"], ["开心", "愉快表达"],
  ["生气", "表达不满"], ["专注", "认真思考"], ["伤心", "低落情绪"], ["无语", "无奈停顿"],
];
const emotionAliases = { calm: 0, neutral: 0, smile: 1, happy: 2, angry: 3, focused: 4, focus: 4, sad: 5, speechless: 6, resigned: 6 };
const behaviorPhases = { connecting: "准备联动", listening: "聆听联动", thinking: "思考联动", speaking: "播报联动", stopping: "正在结束联动" };
const motionEmotions = { chat_v1_talk: 0, chat_v1_smile: 1, chat_v1_happy: 2, chat_v1_angry: 3, chat_v1_focus: 4, chat_v1_sad: 5, chat_v1_resigned: 6 };
const busyConnectionStates = new Set(["discovering", "pairing", "connecting", "reconnecting", "reuniting"]);
const connectionErrors = {
  device_connect_timeout: "已找到机器人，但连接未完成，请重新打开 Desktop Link 后重试。",
  pairing_not_found: "未找到机器人，请检查电脑与机器人是否连接同一 Wi-Fi，并使用机器人当前显示的配对码重试。",
  discovery_timeout: "没有找到机器人。请确认连接同一 Wi-Fi，并使用机器人当前显示的新配对码重试。",
  pairing_timeout: "配对等待超时。请确认连接同一 Wi-Fi，重新打开 Desktop Link 获取新配对码。",
  connect_timeout: "连接机器人超时。请检查同一 Wi-Fi 网络后，用新配对码重试。",
  reconnect_timeout: "机器人重新连接超时。请检查同一 Wi-Fi 网络后，用新配对码重试。",
  device_busy: "机器人正在处理其他连接。请稍后重新打开 Desktop Link，用新配对码重试。",
  pairing_cancelled: "配对已取消。请在机器人上重新打开 Desktop Link，使用新配对码重试。",
  reunite_unavailable: "暂时无法恢复连接。请确认连接同一 Wi-Fi，使用新的配对码重新连接。",
  connection_failed: "连接未成功。请确认连接同一 Wi-Fi，并使用新配对码重试。",
};
function connectionErrorMessage(code) {
  return Object.hasOwn(connectionErrors, code) ? connectionErrors[code] : connectionErrors.connection_failed;
}
function conversationBusy() {
  return state.enabled || ["connecting", "stopping"].includes(state.phase);
}
let lastMessageId = -1;
let unavailable = false;
let actionPending = false;
async function request(path, method = "GET", body) {
  const options = { method, signal: AbortSignal.timeout(5000) };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作未完成，请稍后重试");
  return result;
}
function updateControls() {
  $("toggle").disabled = actionPending || backendPending || unavailable || (!state.enabled && !state.connected);
  $("speech-backend").disabled = backendPending || actionPending || unavailable || conversationBusy() || state.speech_switch_available === false || pairingPending;
  const busy = pairingPending || busyConnectionStates.has(connection.state);
  const blocked = connection.online || state.connected || conversationBusy();
  $("pair").disabled = connectionUnavailable || busy || blocked || !/^[0-9]{6}$/.test($("pairing-code").value);
  $("pairing-code").disabled = busy || blocked;
  $("pair").textContent = connection.online || state.connected ? "已连接" : busy ? "正在连接…" : "连接机器人";
}
function configuredBackends() {
  return state.speech_backends || [{ id: "joyinside", configured: true }, { id: "doubao", configured: false }];
}
function renderSpeechBackend() {
  const selected = state.speech_backend || "joyinside";
  const available = configuredBackends();
  const select = $("speech-backend");
  const signature = Object.keys(speechBackends).map((id) => `${id}:${available.some((item) => item.id === id && item.configured)}`).join("|");
  if (signature !== backendOptionsSignature) {
    select.replaceChildren();
    for (const [id, backend] of Object.entries(speechBackends)) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = backend.label;
      option.disabled = !available.some((item) => item.id === id && item.configured);
      select.append(option);
    }
    backendOptionsSignature = signature;
  }
  select.value = selected;
  const backend = speechBackends[selected];
  $("voice-backend-label").textContent = backend?.short || "语音服务不可用";
  $("voice-backend-detail").textContent = backend?.detail || "请检查 Application 语音配置";
  const doubaoConfigured = available.some((item) => item.id === "doubao" && item.configured);
  $("speech-backend-status").textContent = backendPending ? "正在切换语音服务…"
    : conversationBusy() || state.speech_switch_available === false ? "请暂停对话并等待麦克风释放后再切换。"
      : !doubaoConfigured ? "豆包凭据尚未配置，当前可使用机器人 JoyInside。"
        : "豆包已配置，可在暂停对话时切换。";
  $("speech-backend-error").hidden = !backendFeedback;
  $("speech-backend-error").textContent = backendFeedback;
}
function backendErrorMessage(code) {
  if (["speech_busy", "speech_backend_busy", "conversation_busy", "请先暂停对话并等待音频资源释放，再切换语音后端"].includes(code)) return "请先暂停对话，等待麦克风释放后再切换。";
  if (["speech_backend_unconfigured", "speech_backend_not_configured", "doubao_not_configured", "该语音后端尚未配置可用凭据"].includes(code)) return "豆包凭据尚未配置，请先完成 Application 语音配置。";
  return "切换语音服务未完成，请稍后重试。";
}
function renderBehavior(next) {
  const behavior = next.behavior || {};
  const active = Boolean(behavior.active);
  const releasing = !active && behavior.cleanup_pending;
  const emotion = Number.isInteger(behavior.emotion) ? behavior.emotion : emotionAliases[behavior.emotion];
  const selectedEmotion = emotions[emotion];
  $("behavior-status").textContent = releasing ? "等待释放确认"
    : active ? behaviorPhases[behavior.phase] || "自动联动中"
      : behavior.error ? "联动暂不可用" : "待机";
  $("behavior-status").classList.toggle("active", active);
  $("behavior-emotion").textContent = selectedEmotion?.[0] || "待确认";
  const motionLabel = emotions[motionEmotions[behavior.motion_action]]?.[0];
  $("behavior-motion").textContent = behavior.motion_action && behavior.motion_feedback === "commanded"
    ? `已发送${motionLabel ? "：" + motionLabel + "动作" : "动作指令"}（尚未确认到位）`
    : active ? "等待下一条动作指令" : "暂无动作指令";
  const observed = active || Boolean(behavior.device_id) || Boolean(behavior.degraded);
  const capability = (label, available) => `${label}${available === true ? "可用" : available === false ? "不可用" : "待确认"}`;
  $("behavior-capabilities").textContent = [capability("表情", observed ? behavior.expression_available : undefined), capability("动作", observed ? behavior.motion_available : undefined),
    !observed ? "嘴型待确认" : behavior.mouth_sync_available ? "嘴型联动可用" : "嘴型联动需要固件支持"].join(" · ");
  $("behavior-device").textContent = behavior.device_id ? `联动设备：${behavior.device_id}` : "连接机器人后确认联动能力";
  const notices = [];
  if (behavior.degraded) notices.push("部分能力已降级，可用的语音与联动仍会继续。");
  if (releasing) notices.push("动作或表情释放尚未确认，请等待设备连接恢复并完成清理。");
  else if (behavior.error) notices.push("动作表情联动遇到问题，请检查机器人连接与支持的能力。");
  $("behavior-notice").hidden = notices.length === 0;
  $("behavior-notice").textContent = notices.join(" ");
  const legend = $("emotion-legend");
  if (!legend.children.length) {
    for (const [label, description] of emotions) {
      const item = document.createElement("span");
      item.textContent = label;
      item.setAttribute("title", description);
      legend.append(item);
    }
  }
  for (let index = 0; index < legend.children.length; index++) {
    legend.children[index].classList.toggle("selected", active && index === emotion);
  }
  const timings = next.timings_ms || {};
  for (const name of ["recognition", "pi", "speech"]) {
    const value = timings[name];
    $("timing-" + name).textContent = typeof value === "number" && Number.isFinite(value) && value >= 0
      ? `${(value / 1000).toFixed(2)} 秒` : "—";
  }
}
$("speech-backend").addEventListener("change", async () => {
  if ($("speech-backend").disabled) return;
  const selected = $("speech-backend").value;
  if (!Object.hasOwn(speechBackends, selected) || selected === state.speech_backend) return;
  if (!configuredBackends().some((item) => item.id === selected && item.configured)) {
    renderSpeechBackend();
    return;
  }
  backendPending = true;
  backendFeedback = "";
  renderSpeechBackend();
  updateControls();
  try { render(await request("/api/speech/backend", "POST", { backend: selected })); }
  catch (error) { backendFeedback = backendErrorMessage(error.message); }
  finally { backendPending = false; renderSpeechBackend(); updateControls(); }
});
function renderConnection(next) {
  connection = next;
  const busy = pairingPending || busyConnectionStates.has(next.state);
  $("pairing-status").textContent = next.online ? "机器人已连接" : busy ? "正在连接机器人…" : next.last_error ? "连接未成功" : "等待配对";
  $("pairing-status").classList.toggle("online", Boolean(next.online));
  $("pairing-detail").textContent = conversationBusy()
    ? "对话正在进行或暂停中，请先暂停对话并等待麦克风释放后再配对。"
    : next.online
      ? `已连接 ${next.device_id || "机器人"}，可以开始语音对话。`
      : next.last_error ? connectionErrorMessage(next.last_error)
        : pairingFeedback || (busy ? "正在查找并连接设备，请稍候。" : "输入配对码后连接；失败后可重新输入并重试。");
  $("pairing-detail").classList.toggle("failed", Boolean(next.last_error));
  updateControls();
}
$("pairing-code").addEventListener("input", () => {
  $("pairing-code").value = $("pairing-code").value.replace(/[^0-9]/g, "").slice(0, 6);
  updateControls();
});
$("pairing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("pair").disabled) return;
  const code = $("pairing-code").value;
  if (!/^[0-9]{6}$/.test(code)) return;
  pairingPending = true;
  pairingFeedback = "正在提交配对请求…";
  renderConnection({ ...connection, last_error: null });
  try {
    const next = await request("/api/pair", "POST", { pairing_code: code });
    $("pairing-code").value = "";
    pairingFeedback = "配对请求已提交，正在等待机器人连接。";
    renderConnection(next);
  } catch (error) {
    pairingFeedback = connectionErrorMessage(error.message);
    renderConnection({ ...connection, last_error: error.message });
  } finally {
    pairingPending = false;
    renderConnection(connection);
  }
});
function render(next) {
  state = next;
  renderSpeechBackend();
  renderBehavior(next);
  const [title, detail] = phases[next.phase] || phases.paused;
  $("phase").textContent = title;
  $("phase-detail").textContent = detail;
  $("activity").classList.toggle("active", ["listening", "acknowledging", "thinking", "speaking"].includes(next.phase));
  $("connection").textContent = next.connected ? "机器人已连接" : "等待机器人连接";
  $("connection").classList.toggle("online", next.connected);
  $("device-id").textContent = next.device_id || "请在页面输入机器人配对码";
  $("turns").textContent = next.turns ? `已完成 ${next.turns} 轮对话` : "尚未开始";
  $("toggle").classList.toggle("running", next.enabled);
  $("toggle").querySelector("span").textContent = next.enabled ? "暂停对话" : "开始对话";
  $("toggle").setAttribute("aria-pressed", String(next.enabled));
  $("error").hidden = !next.error;
  $("error").textContent = next.error || "";
  const messages = next.messages || [];
  const newest = messages.at(-1)?.id || 0;
  if (newest !== lastMessageId && messages.length) {
    const container = $("messages");
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
    container.replaceChildren();
    for (const message of messages) {
      const article = document.createElement("article");
      article.className = `message ${message.role === "user" ? "user" : "assistant"}`;
      const label = document.createElement("div");
      label.className = "message-label";
      label.textContent = message.role === "user" ? "你 · 机器人麦克风" : message.kind === "preface" ? "Pi · 操作前回应" : "Pi · 本机 Agent";
      const text = document.createElement("div");
      text.className = "bubble";
      text.textContent = message.text;
      article.append(label, text);
      container.append(article);
    }
    if (nearBottom || lastMessageId < 1) container.scrollTop = container.scrollHeight;
  }
  lastMessageId = newest;
  renderConnection(connection);
}
$("toggle").addEventListener("click", async () => {
  actionPending = true;
  $("toggle").disabled = true;
  try { render(await request(state.enabled ? "/api/pause" : "/api/start", "POST")); }
  catch (error) { $("error").hidden = false; $("error").textContent = error.message; }
  finally { actionPending = false; updateControls(); }
});
async function poll() {
  try {
    const next = await request("/api/state");
    if (!actionPending && !backendPending) render(next);
    unavailable = false;
    updateControls();
  } catch {
    unavailable = true;
    $("toggle").disabled = true;
    $("connection").textContent = "Application 已断开";
    $("connection").classList.remove("online");
    $("activity").classList.remove("active");
    $("phase").textContent = "页面暂时无法连接";
    $("phase-detail").textContent = "请确认 Application 仍在运行。";
    updateControls();
  }
  try {
    const next = await request("/api/connection");
    connectionUnavailable = false;
    if (!pairingPending) renderConnection(next);
  } catch {
    connectionUnavailable = true;
    $("pairing-status").textContent = "连接状态暂不可用";
    $("pairing-detail").textContent = "请确认 Application 仍在运行，稍后会自动重试。";
    updateControls();
  }
  setTimeout(poll, 700);
}
poll();
