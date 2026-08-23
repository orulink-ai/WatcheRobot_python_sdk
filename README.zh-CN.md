# WatcheRobot Python SDK

用于构建、运行和分发 WatcheRobot Application 的 Python SDK。

这个包承担两类职责：

- **Application SDK**：面向开发者的机器人能力 API，用来编写机器人体验。
- **Runtime/Daemon**：唯一的本地运行时，负责配对、设备连接和 Application 进程管理。

开发者只需要实现产品逻辑；Runtime 负责配对、连接、生命周期、日志和传输。桌面端也使用同一份 Runtime/Daemon 实现，而不是另行维护一份 Daemon。

## SDK 能做什么？

你可以用它创建受 Runtime 管理的 Application，并实现：

- 控制行为、动画、运动、灯光、表情、作品和音频；
- 拍照、查询当前视觉后端与模型、录制麦克风 PCM、读取人脸跟踪预览；
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
| 机器人能力 | `app.robot` | 行为、动画、运动、音频、灯光、表情、作品、麦克风、相机、视觉诊断、人脸跟踪和输入等高层能力。 |
| 高级接入 | `ApplicationChannels` | 供拥有完整业务协议的 Application 使用，按来源接收原始 Desktop / Device 帧。 |
| Runtime / Daemon | `watcherobot daemon ...` | 配对、设备与 Desktop 连接、通用帧路由、Application 生命周期、日志和本地控制 REST API。 |
| 机器人新手引导 | `watcherobot robot ...` | 首次 Wi-Fi 配置、六位码配对和连接状态查询。 |
| Application 分发 | `watcherobot app ...` | 创建、检查、发布、提交、浏览、下载、安装和运行经过审核的 Application 快照。 |
| 高级蓝牙配网 | `watcherobot bluetooth ...` / `BluetoothProvisioner` | 底层扫描、Wi-Fi 凭据维护和 BLE 排障。 |
| 设备维护 | Daemon maintenance REST API | 面向 Desktop 的固件、SD 资源和便携作品维护，详见[资源与作品说明](docs/resources.md)。 |

## 快速开始

开始之前请确认：

- 电脑已安装 Python 3.10–3.12（推荐 3.11），且可用蓝牙；
- 机器人在身边并已开机（还没有机器人也没关系，第 3 步可以离线跑通）；
- 配网时电脑和机器人需要连接同一个 Wi-Fi。

### 1. 安装 SDK

建议使用独立 Conda 环境，不要安装到 `base`。SDK 支持 Python 3.10–3.12，推荐
Python 3.11：

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
python -m pip install watcherobot
```

上面是正式版本发布到 PyPI 后的普通安装方式。需要验收尚未发布的 PR/commit 时，应
另外创建 `watcherobot-source` 环境并从目标 checkout 执行
`python -m pip install -e .`；参与 SDK 开发时才安装 `.[test]`。

两条完整路径、TestPyPI 依赖解析和命令来源检查见
[安装指南](docs/installation.zh-CN.md)。不要用 PyPI 安装命令验收尚未发布的源码。

需要确认安装版本时执行：

```powershell
watcherobot --version
```

### 2. 配置第一台机器人

`watcherobot robot setup` 是一条交互式引导命令，会带着你完成三件事：
通过蓝牙为机器人配网（写入 Wi-Fi 名称和密码）、与机器人配对（输入六位配对码）、
并确认最终连接状态。跟着终端里的提示逐步操作即可：

```powershell
watcherobot robot setup
```

引导过程如下：

1. 命令提示你打开电脑蓝牙，并在机器人上打开 **Settings > Wi-Fi**；确认页面已经
   打开后才开始扫描。
2. 扫描结果展示机器人上可核对的稳定 **Device ID**；附近有多台机器人时，使用
   **Up/Down** 和回车键按 Device ID 选择目标设备。旧固件未广播 Device ID 时，命令会
   明确标记 Device ID 不可用，并仅把 Bluetooth ID 作为兼容信息展示。随后命令会私密
   读取 Wi-Fi 密码并写入网络配置。
3. 配网后回到机器人启动器，打开 **"Python SDK"** 应用，读取屏幕顶部的六位配对码，
   并继续在同一个 `robot setup` 流程中输入，完成配对。

配对属于一次性的设备初始化；`app run` 只负责启动 Application。随时可查询连接状态：

```powershell
watcherobot robot status
```

如果机器人已经连接到同一 Wi-Fi，不要重置网络；打开 **"Python SDK"** 应用，直接
使用当前六位码配对：

```powershell
watcherobot robot pair 123456
```

请将 `123456` 替换为机器人当前显示的配对码。

### 3. 创建并运行第一个 Application

`watcherobot app init` 会用内置模板在当前目录创建一个 Application 项目。执行：

```powershell
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

`app init hello_robot` 会在 `./hello_robot/` 下生成一个可直接运行的 Hello World 应用，
包含以下文件：

```text
hello_robot/
├─ app.json     # 应用元数据（ID、名称、版本等）
├─ app.py       # 应用入口，包含完整可运行的示例代码
├─ README.md    # 项目说明
├─ icon.svg     # 应用图标
└─ .gitignore
```

`watcherobot app run` 会通过 Runtime（Daemon）启动这个应用：终端会输出成功问候；
如果已连接兼容的机器人，还会播放一次 `happy` 动画——这就是"让机器人开心一下"。
还没有连接机器人时，`app run` 会明确提示执行 `watcherobot robot setup`，
同时继续以离线模式运行。配对和设备连接始终由 Runtime 独占管理。

遇到找不到设备、配对码位置或未配对提示等问题时，参见[故障排查](docs/troubleshooting.md)。

本地控制 API 还提供 `GET /daemon/logs`；产品集成与排障接口见
[Runtime 合同](docs/contracts/runtime-profile-index.md)。

### 4. 修改 Application

第 3 步生成的 `hello_robot/app.py` 内容就是下面这段代码——刚才机器人播放
`happy` 动画，正是它的效果。修改这个文件后重新执行 `watcherobot app run`
即可看到变化：

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

初始化器已经把同样的代码生成到 `hello_robot/app.py`。注意：请始终通过
`watcherobot app run` 启动应用，不要直接运行 `app.py`——Application 必须由
Daemon 托管，才能获得设备连接和 Desktop 通道。准备发布时，
仍可通过参数覆盖正式元数据：

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
| 使用相机、视觉模型、麦克风或人脸跟踪 | [端侧视觉诊断](docs/vision-diagnostics.zh-CN.md)、[人脸跟踪预览](docs/face-tracking-preview.md)与[麦克风音频](docs/microphone-audio.md) |
| 选择设备行为状态 | [ESP32-S3 v0.3.4 设备行为状态目录](docs/device-states/README.md) |
| 使用官方资源或 Creator 作品 | [资源与作品说明](docs/resources.md) |
| 排查配对、连接或 Runtime 问题 | [故障排查](docs/troubleshooting.md) 与 [Runtime 合同](docs/contracts/runtime-profile-index.md) |

## 常用命令

```powershell
# Runtime 生命周期
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# 首次配置与连接机器人
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456

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

# 高级蓝牙 Wi-Fi 排障
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
