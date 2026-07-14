# Troubleshooting / 常见故障排查

Start by printing the negotiated device information and capabilities / 首先打印设备协商信息：

```python
with WatcheRobot.connect(pairing_code="123456") as robot:
    print(robot.device_info)
    print(robot.capabilities)
```

| Symptom / 现象 | Likely cause / 常见原因 | Action / 处理方式 |
|---|---|---|
| `ConnectionTimeoutError` | Robot is not in SDK Control App, devices are on different LANs, or UDP `37021` / WebSocket `8766` is blocked | Open SDK Control App, confirm the same LAN, and allow Python through the host firewall / 打开 SDK Control App、确认同一局域网并检查防火墙 |
| `AuthenticationError` | Pairing code is stale or incorrect | Use the current six-digit code on the robot screen; a disconnect creates a new code / 使用机器人当前显示的六位码，断线后旧码会失效 |
| `protocol_version_mismatch` | SDK and firmware use different protocol versions | Use the compatibility table in README and update the older side / 按 README 兼容表升级较旧的一端 |
| `CommandError: ... not_found` | Behavior, animation, sound, or action ID is not installed | Use [confirmed resource IDs](resources.md); IDs are case-sensitive / 使用已确认资源 ID，并注意大小写 |
| `TimeoutError` from `Job.wait()` | ACK arrived, but the device did not report a terminal operation event before the timeout | Stop or cancel the operation, capture SDK Control App logs, and avoid treating ACK as completion / 停止或取消操作并采集日志，不要把 ACK 当成完成 |
| `TimeoutError` from camera capture | Camera is busy, firmware is missing `camera.capture`, or the request timed out | Check `robot.capabilities`, use a longer explicit timeout, and retry after reopening SDK Control App / 检查能力、显式延长超时，必要时重开 App |
| Recording is incomplete or `dropped_frames` increases | Network jitter or the consumer is slower than the bounded media queue | Read frames continuously, increase `queue_size` within memory limits, or use `microphone.record()` / 持续消费、谨慎增大队列或使用便捷录音接口 |
| `ValueError` when playing a WAV | File is compressed, stereo, not 16-bit, not 24 kHz, or exceeds 4 MB | Convert it to PCM S16LE, 24 kHz, mono and keep it under 4 MB / 转换为 24 kHz、16-bit、单声道 PCM WAV |

Camera and microphone access may capture people nearby. Obtain consent before testing and protect files written to
`artifacts/` / 摄像头和麦克风可能采集现场人员信息，测试前应取得同意并妥善保管 `artifacts/` 产物。

Serial auto-pairing in `tools/hardware_smoke.py` is only for development firmware with Debug CLI enabled. Production
users should pair through the code displayed by SDK Control App / 串口自动配对仅用于启用 Debug CLI 的开发固件，
普通用户应使用 SDK Control App 屏幕上的配对码。
