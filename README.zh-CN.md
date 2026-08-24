# WatcheRobot Python SDK

用 Python 控制 WatcheRobot 桌面机器人：几行代码就能让机器人做动作、说话、看世界。

> 🌐 [English](README.md) | 中文

[![PyPI](https://img.shields.io/pypi/v/watcherobot)](https://pypi.org/project/watcherobot/)
[![Python](https://img.shields.io/pypi/pyversions/watcherobot)](https://pypi.org/project/watcherobot/)

## 🚀 快速开始

开始之前请确认：

- 已安装 Python 3.10–3.12（推荐 3.11）；
- 首次蓝牙配网使用 Windows 或 macOS；
- 硬件步骤需要机器人在身边并保持开机；没有机器人时，仍可离线创建并运行示例 Application；
- 配对时电脑和机器人处于同一 Wi-Fi 网络。

### 1. 安装 SDK

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
python -m pip install watcherobot
```

不使用 Conda 时，请创建隔离的 `venv`，不要直接污染系统 Python：

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install watcherobot
```

```sh
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install watcherobot
```

确认当前终端实际使用的 SDK：

```powershell
watcherobot --version
```

PEP 668、PATH、`python3`、源码 checkout 和 TestPyPI 的处理方法见
[安装指南](docs/installation.zh-CN.md)。

### 2. 配置第一台机器人

`watcherobot robot setup` 是交互式引导，会完成 Wi-Fi 配置、Runtime 配对和最终连接确认：

```powershell
watcherobot robot setup
```

引导过程会要求你：

1. 打开电脑蓝牙，并在机器人上进入 **Settings > Wi-Fi**；
2. 使用 **Up/Down** 按稳定的 **Device ID** 选择机器人，再私密输入 Wi-Fi 凭据；
3. 在机器人上打开 **"Python SDK"** 应用，将屏幕顶部的六位配对码输入同一个引导流程。

如果机器人已经联网，不要重置 Wi-Fi；打开 **"Python SDK"** 应用后直接配对：

```powershell
watcherobot robot pair 123456
watcherobot robot status
```

请把 `123456` 替换为机器人当前显示的配对码。旧固件没有广播 Device ID 时，命令会
明确标记并使用 Bluetooth ID 作为兼容信息。

### 3. 创建并运行第一个 Application

以下命令逐行执行，兼容 Windows PowerShell 5.1、PowerShell 7 和 macOS/Linux Shell：

```powershell
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

初始化器会生成：

```text
hello_robot/
├─ app.json     # Application 身份、版本和依赖
├─ app.py       # 受管 Application 入口
├─ README.md    # 生成项目的使用说明
├─ icon.svg     # Application 图标
└─ .gitignore
```

`watcherobot app run` 会通过 Runtime/Daemon 启动项目。终端会输出 Application 问候；
连接兼容机器人后还会播放一次 `happy` 行为。没有机器人时，Application 会继续以离线模式
运行并提示连接方法。

必须使用 `watcherobot app run`，不要直接运行 `python app.py`：Application 需要由
Daemon 注入 Device channel 和 Desktop channel。配对或连接失败时参见
[故障排查](docs/troubleshooting.md)。

### 4. 理解并修改示例

第 3 步生成的 `hello_robot/app.py` 就是下面这段代码。在 `hello_robot` 目录修改文件后，
重新执行 `watcherobot app run` 即可看到变化：

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

Application 上下文提供 `app.robot`（机器人能力）、`app.desktop`（与 Watcher Desktop
交换业务消息）和 `app.logger`（Application 日志）。准备正式项目时，可以显式生成稳定元数据：

```powershell
watcherobot app init my_app --id com.example.my_app --author "Example Team"
```

## 🧭 你想做什么？

| 你的需求 | 去这里 |
| --- | --- |
| 让机器人做动作 / 说话 / 亮灯 | [快速开始](#-快速开始) 和 [SDK Application 指南](docs/application-marketplace/sdk-application-usage.zh-CN.md) |
| 看源码、理解核心运行机制 | [源码与仓库边界](#-源码与仓库边界)和[运行架构](#️-它是如何工作的runtimedaemon) |
| 用摄像头 / 麦克风 / 人脸跟踪 | [视觉诊断](docs/vision-diagnostics.zh-CN.md)、[人脸跟踪预览（英文）](docs/face-tracking-preview.md)、[麦克风音频（英文）](docs/microphone-audio.md) |
| 蓝牙配网 Wi-Fi | [蓝牙配网指南](docs/bluetooth-provisioning.md) |
| 发布应用到 Marketplace | [Marketplace 文档](docs/application-marketplace/README.md) |
| 配对 / 连接出问题了 | [Troubleshooting](docs/troubleshooting.md) |
| 查所有命令的用法 | [完整 CLI 参考](docs/cli-reference.zh-CN.md) |
| 从可运行的例子学 | [Application 示例](examples/README.md) |

## ⚙️ 它是如何工作的？（Runtime/Daemon）

这个包有两个互补的角色：

- **Application SDK** — 面向开发者的公开 Python API。
- **Runtime/Daemon** — 唯一的本地运行时，负责与机器人配对、持有设备连接、管理 Application 进程。

你的 Application 只写产品逻辑；Runtime 处理配对、连接、生命周期、日志和传输。桌面端也使用同一份 Runtime/Daemon 实现——Watcher Desktop 不会内嵌另一份 Daemon。

```text
你的 Application
  └─ ApplicationContext / ApplicationChannels
       └─ WatcheRobot Runtime（Daemon）
            ├─ 配对、设备连接、日志、进程生命周期
            ├─ Desktop channel ─────────────── Watcher Desktop
            └─ Device channel ──────────────── WatcheRobot 设备
```

Application 永远不会自己开发现套接字或设备 WebSocket，也拿不到配对凭据。有 Application 在运行时，Desktop 与设备的业务帧经过它；没有时，Runtime 在 Desktop 与设备之间透明转发。

## 🧩 源码与仓库边界

- `src/watcherobot/application/`：受管 Application API 与通道合同。
- `src/watcherobot/runtime/daemon/`：Runtime/Daemon 的唯一源码。Watcher Desktop
  安装并拉起这一实现，不维护第二份 Daemon。
- `src/watcherobot/vision.py`：端侧视觉与人脸跟踪的类型化 Application API。
- 独立 Desktop 仓库负责桌面 UI 和打包；官方默认 Application 位于
  `WatcheRobot_server`，SDK 不负责其中的 ASR、LLM 或 TTS 产品逻辑。
- 官方 Workspace 使用 `yarn desktop:dev` 将当前 SDK checkout 绑定到 Workspace
  自管环境。源码开发流程见[安装指南](docs/installation.zh-CN.md)。

## 🛠️ SDK 能做什么？

用它创建受 Runtime 管理的 Application：

- 控制行为、动画、运动、灯光、表情、作品和音频；
- 拍照、查询视觉后端与模型、录麦克风 PCM、读取人脸跟踪预览；
- 接收触摸和滚轮输入事件；
- 与 Watcher Desktop 交换可选业务消息；
- CLI 本地运行，或通过 Marketplace 审核后由 Desktop 安装启动。

另外还提供蓝牙 Wi-Fi 配网、项目脚手架与校验、Marketplace 发布工具，以及产品集成用的 Runtime 控制 API。

## 📦 常用工作流

| 目标 | 从这里开始 |
| --- | --- |
| 端到端构建并测试一个 Application | [SDK Application 指南](docs/application-marketplace/sdk-application-usage.zh-CN.md) |
| 发布一个通过审核的 Marketplace 应用 | [Marketplace 文档（英文）](docs/application-marketplace/README.md)及[分发参考（英文）](docs/application-marketplace/application-cli-reference.md) |
| 选择设备行为状态 | [ESP32-S3 v0.3.4 状态目录](docs/device-states/README.md) |
| 官方资源与创作者作品 | [资源与作品指南（英文）](docs/resources.md) |
| 诊断配对、连接或运行时问题 | [Troubleshooting（英文）](docs/troubleshooting.md)与[Runtime 契约（英文）](docs/contracts/runtime-profile-index.md) |
| 在官方 Workspace 中做源码集成 | `yarn desktop:dev`（见 [Workspace 说明](docs/installation.zh-CN.md)） |

## ⌨️ 常用命令速查

```powershell
# Runtime 生命周期
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# 机器人首次配置与连接
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456      # 123456 换成机器人屏幕上的配对码

# 应用开发与分发
watcherobot app init my_app
cd my_app
watcherobot app run
watcherobot app login
watcherobot app check .            # 发布前校验
watcherobot app publish .          # 上传不可变源码快照
watcherobot app submit .           # 将快照提交 Marketplace 审核
watcherobot app install <app-id>   # 从 Marketplace 安装
watcherobot app list               # 列出已安装应用
watcherobot app uninstall <app-id> # 卸载
```

常规排障先执行 `watcherobot daemon status`。产品集成可从发现到的本地控制地址读取
`GET /daemon/logs`；端点发现和响应合同见 [Runtime 契约（英文）](docs/contracts/runtime-profile-index.md)。
全部命令见 [CLI 参考](docs/cli-reference.zh-CN.md)。

## ✅ 环境要求

- Python 3.10–3.12（推荐 3.11）
- Windows 或 macOS（蓝牙 Wi-Fi 配网需要）
- 一台 WatcheRobot 设备（配对和硬件功能需要）

当前稳定版本以页面顶部 PyPI 徽章为准。维护者发布流程见 [releasing](docs/releasing.md)。许可证：[Apache-2.0](LICENSE)。
