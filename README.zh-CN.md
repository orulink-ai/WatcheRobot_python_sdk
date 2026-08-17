# WatcheRobot Python SDK

用于构建、运行和分发 WatcheRobot Application 的 Python SDK。

这个包承担两类职责：

- **Application SDK**：面向开发者的机器人能力 API，用来编写机器人体验。
- **Runtime/Daemon**：唯一的本地运行时，负责配对、设备连接和 Application 进程管理。

开发者只需要实现产品逻辑；Runtime 负责配对、连接、生命周期、日志和传输。桌面端也使用同一份 Runtime/Daemon 实现，而不是另行维护一份 Daemon。

## SDK 能做什么？

你可以用它创建受 Runtime 管理的 Application，并实现：

- 控制行为、动画、运动、灯光、表情、作品和音频；
- 拍照、录制麦克风 PCM、读取人脸跟踪预览；
- 接收触摸和滚轮等输入事件；
- 与 Watcher Desktop 交换可选的业务消息；
- 通过 CLI 本地运行，或在通过 Marketplace 审核后由 Watcher Desktop 安装和启动。

SDK 还提供蓝牙 Wi-Fi 配网、Application 项目创建与校验、Marketplace 发布工具，以及供产品集成使用的 Runtime 控制 API。

## 架构概览

```text
你的 Application
  └─ ApplicationContext / ApplicationChannels
       └─ WatcheRobot Runtime（Daemon）
            ├─ 配对、设备连接、日志、进程生命周期
            ├─ Desktop channel ─────────── Watcher Desktop
            └─ Device channel ──────────── WatcheRobot 设备
```

Application 不会自行打开发现 socket 或设备 WebSocket，也拿不到配对凭据。Application 运行时，来自 Desktop 和设备的业务帧会先进入该 Application；未运行 Application 时，Runtime 在 Desktop 与设备之间透明转发。

## 在官方 Workspace 中运行

完整仓库联调统一从 `WatcheRobot-Workspace` 根目录执行 `yarn desktop:dev`。根脚本将当前
SDK checkout 作为 Daemon 唯一源码，以 editable 方式安装到 workspace 自管 venv，并在
桌面端启动前验证 Daemon 运行依赖。该入口不会使用调用者 shell 中遗留的 Conda Python、
系统 SDK 或桌面安装包内 Runtime。

SDK 仓库只负责 Application API、Daemon、Runtime 控制面和分发工具；它不负责桌面 UI、
桌面安装包编排，也不实现官方默认 Application 的 ASR/LLM/TTS 业务。

## 模块划分

| 模块 | 主要入口 | 职责 |
| --- | --- | --- |
| Application 开发 | `watcherobot.application.ApplicationContext` | 编写受管 Application 的常规入口，提供 `app.robot`、`app.desktop` 和 `app.logger`。 |
| 机器人能力 | `app.robot` | 行为、动画、运动、音频、灯光、表情、作品、麦克风、相机、人脸跟踪和输入等高层能力。 |
| 高级接入 | `ApplicationChannels` | 供拥有完整业务协议的 Application 使用，按来源接收原始 Desktop / Device 帧。 |
| Runtime / Daemon | `watcherobot daemon ...` | 配对、设备与 Desktop 连接、通用帧路由、Application 生命周期、日志和本地控制 REST API。 |
| Application 分发 | `watcherobot app ...` | 创建、检查、发布、提交、浏览、下载、安装和运行经过审核的 Application 快照。 |
| 蓝牙配网 | `watcherobot bluetooth ...` / `BluetoothProvisioner` | 扫描设备、写入 Wi-Fi 凭据、查询状态和清除凭据。 |
| 设备维护 | Daemon maintenance REST API | 面向 Desktop 的固件、SD 资源和便携作品维护，详见[资源与作品说明](docs/resources.md)。 |

## 快速开始

### 1. 安装 SDK

```powershell
python -m pip install watcherobot
```

只有参与 SDK 本身开发时，才需要克隆仓库并执行 `pip install -e ".[test]"`。

### 2. 创建并运行第一个 Application

```powershell
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

生成的 Hello World Application 会播放一次 `happy` 行为，然后正常退出。`app run`
会自动启动或复用 Runtime；如果机器人尚未连接，请先在 Watcher Desktop 中完成配对，再次运行即可。

不使用 Watcher Desktop 做 SDK 独立测试时，可启动 Runtime，并使用设备显示的六位码配对：

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
$pairBody = '{"pairing_code":"123456","target_mode":"python_sdk"}'
Invoke-RestMethod `
  -Method Post `
  -Uri "$($runtime.control_url)/daemon/devices/pair" `
  -ContentType "application/json" `
  -Body $pairBody
```

请将 `123456` 替换为设备配对码。本地控制 API 还提供 `GET /daemon/logs`；完整独立运行与排障接口见 [Runtime 合同](docs/contracts/runtime-profile-index.md)。

### 3. 编写 Application

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

初始化器已经把同样的代码生成到 `hello_robot/app.py`。准备发布时，仍可通过参数覆盖正式元数据：

```powershell
watcherobot app init my_app --id com.example.my_app --author "Example Team"
```

## 常见目标与文档入口

| 目标 | 从这里开始 |
| --- | --- |
| 查看可运行代码 | [Application 示例](examples/README.md) |
| 端到端创建、运行和测试 Application | [SDK Application 使用指南](docs/application-marketplace/sdk-application-usage.zh-CN.md) |
| 查询 SDK 的全部命令 | [完整 CLI 命令参考](docs/cli-reference.zh-CN.md) |
| 发布 Marketplace Application | [Application Marketplace 文档](docs/application-marketplace/README.md) 与 [Application 分发命令参考](docs/application-marketplace/application-cli-reference.md) |
| 通过蓝牙配置 Wi-Fi | [蓝牙配网](docs/bluetooth-provisioning.md) |
| 使用相机、麦克风或人脸跟踪 | [人脸跟踪预览](docs/face-tracking-preview.md) 与 [麦克风音频](docs/microphone-audio.md) |
| 选择设备行为状态 | [ESP32-S3 v0.3.4 设备行为状态目录](docs/device-states/README.md) |
| 使用官方资源或 Creator 作品 | [资源与作品说明](docs/resources.md) |
| 排查配对、连接或 Runtime 问题 | [故障排查](docs/troubleshooting.md) 与 [Runtime 合同](docs/contracts/runtime-profile-index.md) |

## 常用命令

```powershell
# Runtime 生命周期
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# Application 开发与分发
watcherobot app init my_app
cd my_app
watcherobot app run
watcherobot app check .
watcherobot app login
watcherobot app publish .
watcherobot app submit .
watcherobot app marketplace
watcherobot app install <app-id>
watcherobot app list
watcherobot app uninstall <app-id>

# 蓝牙 Wi-Fi 配网
watcherobot bluetooth scan
watcherobot bluetooth provision --device <id> --ssid MyWiFi
watcherobot bluetooth status --device <id>
```

全部 `watcherobot` 命令的参数、作用、副作用和 Runtime 边界请查看[完整 CLI 命令参考](docs/cli-reference.zh-CN.md)。

## 环境要求

- Python 3.10–3.12
- 使用蓝牙 Wi-Fi 配网时需要 Windows 或 macOS
- 使用配对与硬件能力时需要 WatcheRobot 设备

本项目使用 [Apache-2.0](LICENSE) 许可证。
