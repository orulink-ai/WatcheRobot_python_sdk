# WatcheRobot Python SDK

SDK 是 WatcheRobot Runtime/Daemon 与 Application API 的唯一实现来源。使用
该 SDK 开发的程序都是受管 Application，可以脱离桌面端通过 CLI 和 Runtime
控制 API 运行。桌面端也使用同一份 Runtime/Daemon 实现启动内置默认
Application 和通过 SDK 开发的 Application。

## 安装与运行

在当前源码仓库中开发时，建议使用独立虚拟环境，并以 editable 方式安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

示例是受 Runtime 管理的 Application，请使用
`watcherobot app run .\examples\<示例目录>` 启动，不要直接执行 `app.py`。
`WATCHER_APP_*` 环境变量由 Runtime 在启动 Application 时注入。

`watcherobot app run` 会启动或复用当前用户会话中的唯一 Runtime。Runtime
持有配对、设备与桌面连接、Application 进程、日志和路由。Application
退出后 Runtime 默认继续运行。
当前会话的 Daemon 日志可通过 `GET /daemon/logs` 读取；无论 Runtime
由桌面端启动还是独立启动，该日志来源都有效。

首次无桌面端运行时，先启动 Runtime，将 `123456` 替换为设备显示的六位码，
通过本地控制 API 完成配对，再运行 Application：

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
$pairBody = '{"pairing_code":"123456","target_mode":"python_sdk"}'
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

也可以在浏览器打开 `$runtime.control_url/control/` 使用本地
[链路测试控制台](docs/control-console.md)。控制台支持输入新配对码、替换旧设备
会话，以及查看 Daemon、设备、Application 和本机 Qwen Gateway 的状态。

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

## 蓝牙 Wi-Fi 配网

Windows 和 macOS 上的 Python 3.10–3.12 可以直接复用 `ESP_ROBOT`
现有 GATT 服务：

```powershell
watcherobot bluetooth scan
watcherobot bluetooth provision --device <id> --ssid MyWiFi
watcherobot bluetooth status --device <id>
watcherobot bluetooth clear --device <id>
```

`provision` 会交互式读取密码，不提供 `--password` 参数。返回
`credentials_saved` 仅表示固件已经确认保存凭据，不代表设备已经成功连接
Wi-Fi。SDK 会在返回前有界尝试停止通知并断开 BLE；清理失败不会覆盖已经
确认的结果，因此该结果不保证 BLE 已断开或固件已经恢复 Wi-Fi 连接尝试。

也可以使用异步 `BluetoothProvisioner` API。API 示例、超时、平台设备标识、
协议限制和当前 GATT 安全边界见
[蓝牙配网说明](docs/bluetooth-provisioning.md)。

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

`open()` 保留原始 Opus 包流，供需要自行处理媒体协议的 Application 使用。原始 Opus 不能按
PCM 字节计算时长，也不能直接保存为 WAV；因此 `record()` 现在会明确报出迁移错误，而不再生成
误导性的录音。需要原始包时使用 `open()`，需要可保存的 PCM 录音时使用 `record_pcm()`。
Runtime/Daemon 只持有设备连接并路由 WSPK 帧，不能解码或改写其中的 Opus payload；解码发生在
SDK 的 Application 媒体层。因此，桌面安装包应携带完整共享 `watcherobot` 环境，但桌面自身仍
只负责控制 Daemon。

确实需要自行处理媒体协议的 Application，可以通过高级 `ApplicationChannels` 的 Device
channel 读取原始 WSPK 帧。

更多内容见 [示例](examples/README.md)、[Runtime 合同](docs/contracts/runtime-profile-index.md)
、[麦克风合同](docs/microphone-audio.md)和 [故障排查](docs/troubleshooting.md)。
