# WatcheRobot Python SDK

SDK 是 WatcheRobot Runtime/Daemon 与 Application API 的唯一实现来源。使用
该 SDK 开发的程序都是受管 Application，可以脱离桌面端通过 CLI 和 Runtime
控制 API 运行。桌面端也使用同一份 Runtime/Daemon 实现；Daemon 可以在未选择
Application 的状态下启动，之后由桌面端通过 Catalog 与控制 API 安装、选择和启动
SDK Application。

当前版本从项目创建、运行到 Hugging Face 发布的逐步测试方法见
[SDK Application 使用与测试指南](docs/application-marketplace/sdk-application-usage.zh-CN.md)；
发给国际开发者时使用
[English SDK Application Guide](docs/application-marketplace/sdk-application-usage.md)；
稳定 JSONL、错误码和跨仓职责见
[Application 广场文档入口](docs/application-marketplace/README.md)。

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

watcherobot app init .\my_app
watcherobot app check .\my_app
watcherobot app login
watcherobot app login --status
watcherobot app publish .\my_app
watcherobot app submit .\my_app
watcherobot app marketplace
watcherobot app marketplace --details
watcherobot app download --space-id <user>/WatcherRobot-<app_id> --commit <40-char-sha> --target .\staging\app
watcherobot app install --space-id <user>/WatcherRobot-<app_id> --commit <40-char-sha> --store-root <app-store-dir> --runtime-root <locked-app-runtime-dir>
watcherobot app list --store-root <app-store-dir>
watcherobot app run-installed --store-root <app-store-dir> --app-id <app_id>
watcherobot app uninstall --store-root <app-store-dir> --app-id <app_id>
watcherobot app logout
watcherobot app start
watcherobot app stop
```

`app run` 只接受源码目录，用于本地开发。SDK 负责下载、安装、已安装列表和
卸载：它在一个 SDK App Store 根目录下安装审核过的固定快照，为每个 App 创建独立 `.venv`，
并记录安装的 commit。`--runtime-root` 必须指向 Desktop 随包的锁定 Application Runtime。
当前 Application 的选择、启动和停止仍属于 Daemon 管理操作。

`watcherobot app run-installed --store-root ... --app-id ...` 是自定义 App Store 的 SDK
开发和验收运行路径。它读取 SDK 安装记录，使用应用自己的 `.venv`，并启动或复用绑定
该商店、使用随机端口的独立 Daemon；它不会复用或修改 Desktop Daemon。Desktop 正式版仍
只使用自己的受管商店，并在 UI 中负责选择、启动和停止。

`watcherobot app init <new-directory>` 会创建一个完整、可直接进入发布流程的项目，
不会启动 Daemon，也不会覆盖已有路径。终端中会依次询问 Application ID、显示名称、
作者和简介；脚本可以使用 `--id`、`--name`、`--author`、`--description` 一次性提供。
生成的 `app.json` 初始版本为 `0.1.0`，SDK 兼容范围根据当前安装版本自动计算，并包含
默认 `icon.svg`。

`watcherobot app check <directory>` 会校验唯一的 `app.json`、固定入口
`app.py`、SDK 兼容范围、标准 Python 依赖、图标路径和允许发布的源码集合，
但不会启动 Daemon。Desktop 调用时使用 `--jsonl`，stdout 只包含逐行 JSON
事件。

`watcherobot app login` 使用 Watcher Desktop 公共 OAuth Device Flow 登录
Hugging Face，验证身份后只把 Token 保存到 Watcher 专用的操作系统凭据项。
`app login --status` 校验已保存身份，`app logout` 只删除 Watcher 凭据；这些
命令均不启动 Daemon，并支持 `--jsonl`。

`watcherobot app publish <directory>` 会先执行相同的本地检查，再把精确源码快照
发布到公开的 `<hf_username>/WatcherRobot-<app_id>` Hugging Face Space。Space
只作为源码仓库，不生成网页；成功结果只包含 Space 和固定源码 commit，不读取或
修改官方 Catalog。该命令不启动 Daemon，并支持 `--jsonl`。

`watcherobot app submit <directory>` 要求 `description`、`author` 两项应用广场信息
为非空，校验已经发布的固定快照，然后在不上传源码的前提下创建或复用官方名单 PR。
`icon` 可选：填写时校验固定版本中的图标文件，未填写时由展示端使用默认 WatcherRobot
Application 图标。可用 `--commit <40-char-sha>` 指定已发布版本；省略时提交 Space 当前
HEAD。PR 会展示审核用 Manifest、固定源码链接，以及用户提供时的固定版本图标。

`watcherobot app marketplace` 会公开读取并严格校验官方 Application 名单，
以及每个审核固定 commit 上的 `app.json`。结果包含本次观察到的 Dataset commit、
结构化 Application 信息、SDK 兼容性和固定源码链接。该命令无需登录 Hugging
Face，不启动 Daemon，也不修改本地状态；Desktop 使用 `--jsonl`，并自行保存
上一次成功结果作为本地缓存。
开发者默认看到兼容性表格，使用 `--details` 查看完整 Manifest、固定源码 URL、commit
和依赖；只有 Desktop 或其他机器调用方才使用 `--jsonl`。

`watcherobot app download --space-id ... --commit ... --target ...` 无需登录
Hugging Face，会把 Space 的一个不可变固定版本下载到调用者预先创建的现有
空 staging 目录。交付前会核对实际 commit、源码限制、唯一 Manifest、固定
`app.py`、SDK 兼容性以及 Space 与 App 身份。该命令不启动 Daemon，不决定最终
App Store 目录，也不写入 `install.json`；需要原子安装时使用 `app install`，两者均支持
Desktop 使用 `--jsonl`。

`watcherobot app install --space-id ... --commit ... --store-root ... --runtime-root ...`
会下载审核固定快照，校验并在必要时复制锁定 Runtime，创建该 App 的独立 Python 环境，
校验依赖和入口后原子写入安装记录。`app list --store-root ...` 读取已安装记录；
`app uninstall --store-root ... --app-id ...` 将一个 App 移到可恢复的本地回收目录。这些命令
不会启动或连接 Daemon。

大型 Application 可以增加 `.watcherignore`，使用 `tests/`、`.venv*/`、
`*.tmp` 等 glob 规则排除非运行文件；`.watcherignore` 本身不会进入发布快照。

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
