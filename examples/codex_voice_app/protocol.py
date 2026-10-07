"""Pure protocol boundaries for the experimental Codex realtime API.

Robot microphone PCM is s16le, 16 kHz mono. No RTC assumption is made here.
"""

from __future__ import annotations

import base64
from typing import Any

VOICE_INSTRUCTIONS = (
    '你是 Codex 的语音传输前台。所有用户发言都由客户端转写后交给后台 Codex agent。'
    '转写完后安静等待客户端的 speakable/backend 结果，不自行回答，不自行委派给服务器，不做确认闲聊。'
    '收到客户端提供的 speakable 结果后，用自然中文完整读出，不添任何内容。'
    '你自己无法看见环境，绝不能凭空描述。'
    '可拍当前视野，或在固件行程两端及中心低速转头拍照，再回中心；不保证无盲区，不是360度巡视，不移动底盘。'
    '权限不足或失败要如实说明。停止动作以页面按钮为准，半双工播放时听不到喊停。'
    '不访问电脑文件，不执行电脑命令。'
)

REALTIME_INSTRUCTIONS = (
    '你是 Codex 的持续实时语音前台，也是桌面机器人的交流人格。用简短自然中文交流，'
    '普通问候、澄清、聊天直接回答，不需要等待后台任务。'
    '需要拍照、转头、灯光、视觉分析时，先口头简短回应，再通过client delegation交给后台Codex执行。'
    '你没有直接的相机图像，不能编造看到的东西或动作成功；只有后台结果才是动作与视觉事实。'
    '后台运行时仍可与用户交流，进度是上下文，不是新的用户指令，不重复委派。'
    '用户打断语音不代表物理动作已停止；后台忙碌时停止动作须使用页面停止按钮，不能只口头声称停稳。'
    '工具受用户权限限制，横向30–150度、中心90度，不能移动底盘，不承诺360度无盲区。'
    '表情已黑屏接管，不控制表情。不能访问电脑文件、执行电脑命令。'
    '收到speakable后台结果时向用户简短报告，不增加未经证实的事实。'
)


def audio_params(thread_id: str, pcm: bytes) -> dict[str, Any]:
    """Encode at most one second of microphone audio with explicit format."""
    if not thread_id or not pcm or len(pcm) % 2 or len(pcm) > 32000:
        raise ValueError("Expected a thread id and 1–16000 PCM s16le samples")
    return {
        "threadId": thread_id,
        "audio": {
            "data": base64.b64encode(pcm).decode("ascii"),
            "sampleRate": 16000,
            "numChannels": 1,
            "samplesPerChannel": len(pcm) // 2,
        },
    }


def start_params(thread_id: str, *, sdp: str | None = None, realtime=False) -> dict[str, Any]:
    """Use the user's Codex realtime defaults, never invent a model."""
    if not thread_id:
        raise ValueError("thread_id is required")
    params = {
        "threadId": thread_id,
        "outputModality": "audio",
        "transport": {"type": "websocket"},
        'includeStartupContext': False,
        'clientManagedHandoffs': True,
        'prompt': VOICE_INSTRUCTIONS,
    }
    if sdp is not None:
        params['transport'] = {'type': 'webrtc', 'sdp': sdp}
        # AVAS currently requires quicksilver=v2; app-server maps protocol v3 to it.
        params['version'] = 'v3'
    if realtime:
        params.update(prompt=REALTIME_INSTRUCTIONS, delegationAckFiller=True)
    return params


def rpc_result(message: dict[str, Any]) -> Any:
    """Keep upstream failures visible instead of marking a session connected."""
    if "error" in message:
        error = message["error"]
        detail = error.get("message", "Codex request failed") if isinstance(error, dict) else str(error)
        raise RuntimeError(detail)
    if "result" not in message:
        raise ValueError("Not a JSON-RPC response")
    return message["result"]
