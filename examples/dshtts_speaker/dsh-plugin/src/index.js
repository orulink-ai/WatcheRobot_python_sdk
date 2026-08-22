/** DSH WatcheRobot TTS command and final assistant-message auto speaker. */

const name = "dsh-speak-watcherobot";
const inject = ["commands"];
const SPEAK_BASE = process.env.DSHTTS_SPEAK_BASE ?? "http://127.0.0.1:9876";
const SPEAK_URL = `${SPEAK_BASE.replace(/\/$/, "")}/speak`;
const TIMEOUT_MS = 60_000;
const AUTO_SPEAK = !["0", "false", "off", "no"].includes(
    String(process.env.DSHTTS_AUTO_SPEAK ?? "true").toLowerCase(),
);
const MAX_TEXT_CHARS = 4000;

function cleanText(value) {
    let text = String(value ?? "");
    text = text.replace(/```(?:[\w+-]+)?\s*[\r\n]([\s\S]*?)```/gu, "$1");
    text = text.replace(/`([^`]+)`/gu, "$1");
    text = text.replace(/!\[([^\]]*)\]\([^)]*\)/gu, "$1");
    text = text.replace(/\[([^\]]+)\]\([^)]*\)/gu, "$1");
    text = text.replace(/https?:\/\/\S+/gu, "");
    text = text.replace(/^\s{0,3}#{1,6}\s*/gmu, "");
    text = text.replace(/^\s*[-*+]\s+/gmu, "");
    text = text.replace(/^\s*\d+[.)]\s+/gmu, "");
    return text.replace(/[\*_~]/gu, "").replace(/\s+/gu, " ").trim().slice(0, MAX_TEXT_CHARS);
}

function textFromMessage(message) {
    const content = message?.content;
    if (typeof content === "string") return cleanText(content);
    if (!Array.isArray(content)) return "";
    return cleanText(content.filter((block) => block?.type === "text" && typeof block.text === "string").map((block) => block.text).join("\n"));
}

async function request(path, body, signal) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    if (signal) signal.addEventListener("abort", () => controller.abort(), { once: true });
    try {
        const response = await fetch(`${SPEAK_BASE}${path}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
            signal: controller.signal,
        });
        const result = await response.json();
        return result.ok ? result : { ok: false, error: result.error || `HTTP ${response.status}` };
    } catch (error) {
        return { ok: false, error: error.name === "AbortError" ? "请求超时" : `无法连接 TTS 服务: ${error.message}` };
    } finally {
        clearTimeout(timer);
    }
}

function lastAssistantText(agent) {
    const log = agent?.session?.log;
    if (!Array.isArray(log)) return "";
    for (let i = log.length - 1; i >= 0; i -= 1) {
        const entry = log[i];
        if (entry?.role === "assistant") {
            const text = cleanText(typeof entry.content === "string" ? entry.content : textFromMessage(entry));
            if (text) return text;
        }
    }
    return "";
}

export function apply(ctx) {
    const seen = new Set();
    ctx.commands.register({
        name: "speak",
        description: "通过 WatcheRobot 播报助手回复；支持 stop、voice <语音名>",
        input: { hint: "[stop|voice <voice-id>|文字]" },
        handler: async (invocation) => {
            const input = invocation.rawInput?.trim() ?? "";
            if (input.toLowerCase() === "stop") {
                const result = await request("/stop", {}, invocation.signal);
                return result.ok ? { kind: "success", text: "✅ 已停止机器人播报并清空队列" } : { kind: "error", text: `❌ 停止失败: ${result.error}` };
            }
            if (input.toLowerCase().startsWith("voice ")) {
                const voice = input.slice(6).trim();
                const result = await request("/settings", { voice }, invocation.signal);
                return result.ok ? { kind: "success", text: `✅ 默认语音已切换为 ${result.voice}` } : { kind: "error", text: `❌ 语音切换失败: ${result.error}` };
            }
            const text = cleanText(input) || lastAssistantText(invocation.agent);
            if (!text) return { kind: "error", text: "没有可播报的内容。请先对话，或输入 /speak 要播报的文字。" };
            const result = await request("/speak", { text }, invocation.signal);
            if (!result.ok) return { kind: "error", text: `❌ 播报失败: ${result.error}` };
            return { kind: "success", text: `✅ 已加入播报队列: ${text.slice(0, 60)}${text.length > 60 ? "…" : ""}` };
        },
    });

    if (!AUTO_SPEAK) return;
    ctx.on("session/event", (_session, event) => {
        if (event?.type !== "assistant/message") return;
        const key = String(event.id ?? event.seq ?? `${event.type}:${event.data?.message?.id ?? ""}`);
        if (seen.has(key)) return;
        seen.add(key);
        if (seen.size > 500) seen.delete(seen.values().next().value);
        const text = textFromMessage(event.data?.message ?? event.message ?? event.data);
        if (!text) return;
        void request("/speak", { text }).then((result) => {
            if (!result.ok) console.error(`[${name}] auto speak failed: ${result.error}`);
        });
    });
}
