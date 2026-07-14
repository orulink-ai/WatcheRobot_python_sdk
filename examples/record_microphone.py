"""录制五秒机器人麦克风 PCM，并保存为标准 WAV。"""

from __future__ import annotations

import math
import os
import time
import wave
from pathlib import Path

from watcherobot import WatcheRobot


DURATION_SECONDS = 5.0
OUTPUT_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "microphone.wav"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )
    input("即将录音五秒并保存到本机；确认环境允许录音后按回车继续。")

    frames: list[bytes] = []
    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        with robot.microphone.open() as microphone:
            audio_format = microphone.format
            bytes_per_second = (
                audio_format.sample_rate_hz
                * audio_format.channels
                * audio_format.sample_width_bytes
            )
            target_bytes = round(bytes_per_second * DURATION_SECONDS)
            deadline = time.monotonic() + DURATION_SECONDS + 2.0
            recorded_bytes = 0
            last_countdown = 0
            while recorded_bytes < target_bytes and time.monotonic() < deadline:
                countdown = max(1, math.ceil((target_bytes - recorded_bytes) / bytes_per_second))
                if countdown != last_countdown:
                    print(f"正在录音，还剩约 {countdown} 秒……")
                    last_countdown = countdown
                try:
                    frame = microphone.read(timeout=1.0)
                except TimeoutError:
                    continue
                frames.append(frame.data)
                recorded_bytes += len(frame.data)
            dropped_frames = microphone.dropped_frames

    pcm = b"".join(frames)[:target_bytes]
    if len(pcm) != target_bytes:
        raise RuntimeError(f"录音数据不足：期望 {target_bytes} 字节，实际 {len(pcm)} 字节")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT_FILE), "wb") as wav_file:
        wav_file.setnchannels(audio_format.channels)
        wav_file.setsampwidth(audio_format.sample_width_bytes)
        wav_file.setframerate(audio_format.sample_rate_hz)
        wav_file.writeframes(pcm)
    print(
        f"录音已保存：{OUTPUT_FILE}（{DURATION_SECONDS:.1f} 秒，"
        f"dropped_frames={dropped_frames}）"
    )


if __name__ == "__main__":
    main()
