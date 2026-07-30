# WatcheRobot Python SDK

SDK 是 WatcheRobot Runtime/Daemon 与 Application API 的唯一实现来源。使用
该 SDK 开发的程序都是受管 Application，可以脱离桌面端通过 CLI 和 Runtime
控制 API 运行。桌面端也使用同一份 Runtime/Daemon 实现启动内置默认
Application 和通过 SDK 开发的 Application。

## 安装与运行

在当前源码仓库中开发时，先以 editable 方式安装：

```powershell
python -m pip install -e ".[test]"
```

`watcherobot app run` 会启动或复用当前用户会话中的唯一 Runtime。Runtime
持有配对、设备与桌面连接、Application 进程、日志和路由。Application
退出后 Runtime 默认继续运行。
当前会话的 Daemon 日志可通过 `GET /daemon/logs` 读取；无论 Runtime
由桌面端启动还是独立启动，该日志来源都有效。

首次无桌面端运行时，先启动 Runtime，将 `123456` 替换为设备显示的六位码，
通过本地控制 API 完成配对，再运行 Application：

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
$pairBody = '{"pairing_code":"123456","target_mode":"desktop_link"}'
Invoke-RestMethod `
  -Method Post `
  -Uri "$($runtime.control_url)/daemon/devices/pair" `
  -ContentType "application/json" `
  -Body $pairBody
do {
  $device = (Invoke-RestMethod `
    -Uri "$($runtime.control_url)/daemon/devices").device
  if ($device.state -eq "idle") {
    throw "Pairing failed: $($device.last_error)"
  }
  if ($device.state -ne "connected") {
    Start-Sleep -Milliseconds 250
  }
} while ($device.state -ne "connected")
watcherobot app run .\examples\hello_robot
```

如果当前 Runtime 已持有在线设备会话，可以跳过配对请求，直接运行
Application。

```powershell
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

watcherobot app package .\examples\hello_robot .\dist\hello_robot.wapp
watcherobot app install .\dist\hello_robot.wapp
watcherobot app list
watcherobot app select example.hello_robot --version 1.0.0
watcherobot app start
watcherobot app stop
```

大型 Application 可以增加 `.wappignore`，使用 `tests/`、`.venv*/`、
`*.tmp` 等 glob 规则排除非运行文件；`.wappignore` 本身不会进入安装包。

## Application API

Application 使用固定的 `app.json + app.py`：

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("capabilities=%s", app.robot.capabilities)
        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

- `app.robot` 保留领域、Job、输入与媒体 API，只能使用 Daemon 授权的
  Device channel。
- `app.desktop` 用于可选的桌面消息。
- `app.logger` 输出的日志由 Runtime 捕获并持久化。

已经拥有完整业务协议栈的高级 Application 可以使用 `ApplicationChannels`
直接接收带来源的桌面/设备原始帧；普通 SDK Application 应使用
`ApplicationContext`。

Application 不自行监听 Discovery 端口、不直连设备 WebSocket，也不会拿到
设备配对凭证。

## 麦克风 PCM

设备麦克风上行使用 Opus（`16 kHz`、单声道、每个 WSPK 帧一个包）。普通 Application
通过高层 SDK 得到解码后的 `pcm_s16le`：

```python
with app.robot.microphone.open_pcm() as microphone:
    frame = microphone.read(timeout=1.0)  # frame.data 是 16 kHz 单声道 PCM

recording = app.robot.microphone.record_pcm(duration=3.0)
recording.save("microphone.wav")
```

`open()` 和 `record()` 为兼容保留，继续返回原始 Opus payload。需要按字节计算 PCM 时长或
保存 PCM WAV 时，必须使用显式 PCM API，不能把原始 Opus 字节当作 PCM。Runtime/Daemon 只持有
设备连接并路由 WSPK 帧，不能解码或改写其中的 Opus payload；解码发生在 SDK 的 Application
媒体层。因此，桌面安装包应携带完整共享 `watcherobot` 环境，但桌面自身仍只负责控制 Daemon。

确实需要自行处理媒体协议的 Application，可以通过高级 `ApplicationChannels` 的 Device
channel 读取原始 WSPK 帧。

更多内容见 [示例](examples/README.md)、[Runtime 合同](docs/contracts/runtime-profile-index.md)
、[麦克风合同](docs/microphone-audio.md)和 [故障排查](docs/troubleshooting.md)。
