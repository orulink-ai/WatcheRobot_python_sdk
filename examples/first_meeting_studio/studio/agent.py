"""Codex CLI Agent adapter; preserves configured provider and local login."""
import asyncio
import json
import os
import shutil
from pathlib import Path

def resolve_codex():
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    candidates = list(root.glob("*/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    raise RuntimeError("找不到 Codex CLI，请安装并登录 Codex")

def parse_reply(events):
    if any(e.get("type") in ("turn.failed", "error") for e in events):
        raise RuntimeError("Codex Agent 调用失败，请检查登录、服务配置和网络")
    replies = [e["item"].get("text", "") for e in events
               if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    if not replies or not any(e.get("type") == "turn.completed" for e in events):
        raise RuntimeError("Codex 未返回完整回答")
    return replies[-1].strip()[:500]

class CodexAgent:
    def __init__(self, root):
        self.root = root

    async def chat(self, messages):
        prompt = ("你是桌面机器人 Watcher，好奇、温暖、有点小调皮。只输出面向用户的中文口语回答，"
                  "通常一到两句，最多问一个问题。不能宣称拍照、识别人脸、执行动作。"
                  "不要读取文件、使用工具或执行命令。以下是对话数据：\n" +
                  json.dumps(messages[-24:], ensure_ascii=False))
        process = await asyncio.create_subprocess_exec(
            resolve_codex(), "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only", "-C", str(self.root), "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(prompt.encode()), 90)
            if process.returncode:
                raise RuntimeError("Codex Agent 未成功完成，请检查当前 Codex 登录和服务配置")
            events = [json.loads(line) for line in output.decode().splitlines() if line.strip()]
            return parse_reply(events)
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
