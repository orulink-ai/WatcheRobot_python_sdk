# Troubleshooting / 常见故障排查

Start by printing the negotiated device information and capabilities / 首先打印设备协商信息：

```python
with WatcheRobot.connect(pairing_code="123456") as robot:
    print(robot.device_info)
    print(robot.capabilities)
```

Minimal runnable error handling / 最小可运行异常处理：

```python
from watcherobot import (
    AuthenticationError,
    ConnectionTimeoutError,
    JobFailedError,
    WatcheRobot,
)

pairing_code = input("Six-digit pairing code / 六位配对码：").strip()
try:
    with WatcheRobot.connect(pairing_code=pairing_code) as robot:
        robot.behavior.play("happy").wait(timeout=20)
except ConnectionTimeoutError:
    print("Open SDK Control App and check the LAN/firewall / 请检查 App、局域网和防火墙。")
except AuthenticationError:
    print("Use the new code shown on the robot / 请使用机器人当前显示的新配对码。")
except JobFailedError as error:
    print(error.job_id, error.reason, error.error_code)
```

| Symptom / 现象 | Likely cause / 常见原因 | Action / 处理方式 |
|---|---|---|
| `ConnectionTimeoutError` | Robot is not in SDK Control App, devices are on different LANs, UDP `37021` / WebSocket `8766` is blocked, or a VPN selected the wrong interface | Open SDK Control App, confirm the same LAN, allow Python through the host firewall, then retry with `host="192.168.x.x"` / 打开 SDK Control App、确认同一局域网并检查防火墙；VPN 或虚拟网卡存在时传入真实局域网 IP |
| `AuthenticationError` | Pairing code is stale or incorrect | Use the current six-digit code on the robot screen; a disconnect creates a new code / 使用机器人当前显示的六位码，断线后旧码会失效 |
| `protocol_version_mismatch` | SDK and firmware use different protocol versions | Use the compatibility table in README and update the older side / 按 README 兼容表升级较旧的一端 |
| `CommandError: ... not_found` | Behavior, animation, sound, or action ID is not installed | Use [confirmed resource IDs](resources.md); IDs are case-sensitive / 使用已确认资源 ID，并注意大小写 |
| `TimeoutError` from `Job.wait()` | ACK arrived, but the device did not report a terminal operation event before the timeout | Stop or cancel the operation, capture SDK Control App logs, and avoid treating ACK as completion / 停止或取消操作并采集日志，不要把 ACK 当成完成 |
| `TimeoutError` from camera capture | Camera is busy, firmware is missing `camera.capture`, or the request timed out | Check `robot.capabilities`, use a longer explicit timeout, and retry after reopening SDK Control App / 检查能力、显式延长超时，必要时重开 App |
| Camera timed out and a later retry returns an unexpected old image | Legacy firmware sent image frames with stream ID zero, so a very late frame cannot be strongly correlated | Reopen SDK Control App before retrying after a timeout; current 16-byte session-tagged frames avoid this ambiguity / 超时后重开 App 再试；新版带 Session 的帧没有这个歧义 |
| Recording is incomplete or `dropped_frames` increases | Network jitter or the consumer is slower than the bounded media queue | Read frames continuously, increase `queue_size` within memory limits, or use `microphone.record()` / 持续消费、谨慎增大队列或使用便捷录音接口 |
| `ValueError` when playing a WAV | File is compressed, stereo, not 16-bit, not 24 kHz, or exceeds 4 MB | Convert it to PCM S16LE, 24 kHz, mono and keep it under 4 MB / 转换为 24 kHz、16-bit、单声道 PCM WAV |

Camera and microphone access may capture people nearby. Obtain consent before testing and protect files written to
`artifacts/` / 摄像头和麦克风可能采集现场人员信息，测试前应取得同意并妥善保管 `artifacts/` 产物。

Serial auto-pairing in `tools/hardware_smoke.py` is only for development firmware with Debug CLI enabled. Production
users should pair through the code displayed by SDK Control App / 串口自动配对仅用于启用 Debug CLI 的开发固件，
普通用户应使用 SDK Control App 屏幕上的配对码。
