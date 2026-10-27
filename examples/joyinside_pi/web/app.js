"use strict";
const $ = (id) => document.getElementById(id);
const phases = {
  paused: ["准备好再开始", "待机中，机器人麦克风已关闭。"],
  connecting: ["正在连接语音", "首次连接或会话更新时，稍等片刻。"],
  listening: ["轮到你说话了", "头部黄灯亮起后说话，停顿后会自动开始回答。"],
  thinking: ["Pi 正在处理", "回复会逐步显示，形成一句后开始播报。"],
  speaking: ["机器人正在回答", "Pi 可以继续生成后面的内容，播完后自动聆听。"],
  stopping: ["正在暂停", "等待当前操作取消并释放机器人麦克风。"],
  error: ["暂时无法对话", "请查看下方提示，确认机器人连接和 Pi 配置。"],
};
let state = { enabled: false };
let lastMessageId = -1;
let lastRevision = -1;
let unavailable = false;
let actionPending = false;
async function request(path, method = "GET") {
  const response = await fetch(path, { method, signal: AbortSignal.timeout(5000) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作未完成，请稍后重试");
  return result;
}
function render(next) {
  state = next;
  const [title, detail] = phases[next.phase] || phases.paused;
  $("phase").textContent = title;
  $("phase-detail").textContent = detail;
  $("activity").classList.toggle("active", ["listening", "thinking", "speaking"].includes(next.phase));
  $("connection").textContent = next.connected ? "机器人已连接" : "等待机器人连接";
  $("connection").classList.toggle("online", next.connected);
  $("device-id").textContent = next.device_id || "请先通过 SDK 或桌面端配对";
  $("turns").textContent = next.turns ? `已完成 ${next.turns} 轮对话` : "尚未开始";
  $("toggle").classList.toggle("running", next.enabled);
  $("toggle").querySelector("span").textContent = next.enabled ? "暂停对话" : "开始对话";
  $("toggle").setAttribute("aria-pressed", String(next.enabled));
  $("error").hidden = !next.error;
  $("error").textContent = next.error || "";
  const messages = next.messages || [];
  const newest = messages.at(-1)?.id || 0;
  if ((newest !== lastMessageId || next.revision !== lastRevision) && messages.length) {
    const container = $("messages");
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
    container.replaceChildren();
    for (const message of messages) {
      const article = document.createElement("article");
      article.className = `message ${message.role === "user" ? "user" : "assistant"}`;
      const label = document.createElement("div");
      label.className = "message-label";
      label.textContent = message.role === "user" ? "你 · 机器人麦克风" : `Pi · 本机 Agent${message.streaming ? " · 正在生成" : message.interrupted ? " · 已中断" : ""}`;
      const text = document.createElement("div");
      text.className = "bubble";
      text.textContent = message.text;
      article.append(label, text);
      container.append(article);
    }
    if (nearBottom || lastMessageId < 1) container.scrollTop = container.scrollHeight;
  }
  lastMessageId = newest;
  lastRevision = next.revision;
}
$("toggle").addEventListener("click", async () => {
  actionPending = true;
  $("toggle").disabled = true;
  try { render(await request(state.enabled ? "/api/pause" : "/api/start", "POST")); }
  catch (error) { $("error").hidden = false; $("error").textContent = error.message; }
  finally { actionPending = false; $("toggle").disabled = unavailable; }
});
async function poll() {
  try {
    const next = await request("/api/state");
    if (!actionPending) render(next);
    unavailable = false;
    $("toggle").disabled = actionPending;
  } catch {
    unavailable = true;
    $("toggle").disabled = true;
    $("connection").textContent = "Application 已断开";
    $("connection").classList.remove("online");
    $("activity").classList.remove("active");
    $("phase").textContent = "页面暂时无法连接";
    $("phase-detail").textContent = "请确认 Application 仍在运行。";
  }
  setTimeout(poll, state.enabled ? 250 : 1000);
}
poll();
