# 设备 JoyInside STT / TTS

通过 Daemon 管理的 Application 复用设备 NVS 中的 JoyInside 配置。电脑不需要持有 AK/SK，固件也不会返回这些凭据。

```python
async with ApplicationContext.from_environment() as context:
    speech = await asyncio.to_thread(context.robot.speech.open)
    try:
        text = await asyncio.to_thread(speech.recognize, timeout=30)
        reply = await my_agent(text)
        await asyncio.to_thread(speech.say, reply, timeout=60)
    finally:
        await asyncio.to_thread(speech.close)
```

API 同步阻塞，异步程序使用 `asyncio.to_thread`。退出时先取消或等待正在执行的调用，再关闭会话。断开设备连接会唤醒等待者，禁止向重连后的设备发送旧清理命令。

`open(timeout=30)` 等待云连接和音频配置；`recognize(timeout=30)` 等待最终 ASR；`say(text, timeout=60)` 等待物理播放 drain；`cancel()` 取消当前请求；`close(timeout=10)` 等待资源释放和 closed 事件。超时抛出 `TimeoutError`；拒绝/异步错误抛出 `WatcheRobotError`。取消清理期间设备仍可返回 busy，应关闭旧会话后重试。

`close` 明确收到 `session_mismatch` 时，表示设备不再拥有请求的 session_id，SDK 退役这一旧句柄，不向其他设备会话发送关闭命令。其他拒绝及超时仍保留句柄，调用 `robot.speech.close()` 可重试清理。

能力为 `speech.joyinside.v1`。模式为 `conversation_asr`，使用对话通道中的 ASR，云端可能仍推理回复；固件过滤其自动音频。会话独占机器人音频并采用半双工。单次 TTS 1–999 UTF-8 字节，禁止空白及 NUL 文本。

## 线协议

Daemon 不解析或改写以下业务消息。命令遵循现有 SDK 外壳 `{"type":"ctrl.speech.*","data":{...,"command_id":"..."}}`，使用现有 `sys.ack`/`sys.nack`。ACK 仅证明接收命令，事件才证明完成。

| 命令 | data 字段（另有 command_id） | 完成事件 |
| --- | --- | --- |
| `ctrl.speech.open` | session_id | ready, recognition_mode=conversation_asr |
| `ctrl.speech.recognize` | session_id, request_id, timeout_ms | recognition, text |
| `ctrl.speech.say` | 同上，加 text | playback, state=completed |
| `ctrl.speech.cancel` | session_id, request_id | 当前操作 error, reason=cancelled |
| `ctrl.speech.close` | session_id | closed, reason |

事件为 `{"type":"evt.speech","data":{"session_id":"...","kind":"...",...}}`。操作事件包含 request_id，启动失败可以不含 request_id。两种 ID 均为 32 位小写十六进制 UUID；timeout_ms 为 1–120000 的整数。最多一个会话/在途请求，每会话最多 32 个不同请求；重复请求拒绝执行。常规 command_id 重传使用现有 ACK 缓存。

一个任务独占 JoyInside client 与 HAL 音频；回调仅解析，主 SDK tick 发送事件。正常轮次可以复用 SDK 会话；调用方需在每会话 32 个请求预算用尽前关闭重开。成功专用 TTS 后保留云连接供下一句或 ASR 使用；ASR、取消、失败或旧轮次历史将满时退役连接。ASR 后同一工作任务可以预热下一条连接，以隐藏 Pi 生成期间的握手等待；始终只有一条云连接。输出必须是带 roundId 的 JSON/base64 PCM，已退役轮次的 ASR/音频被过滤；未带轮次的迟到 ASR 依靠 ASR 后断开原连接隔离。服务器若不确认配置，接口明确失败，未标记二进制音频不会播放。

[官方协议](https://joyinside.com/handbook/part5-%E9%99%84%E5%BD%95/b-protocol-fields.html) · [本地 Pi 示例](../examples/joyinside_pi/README.md)

混合方向传输只设置 audio.input.binary=true 与 audio.output.binary=false，省略全局 audio.binary，符合官方覆盖规则。
