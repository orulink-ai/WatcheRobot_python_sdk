# WatcheRobot Python SDK

用 Python 控制 WatcheRobot 桌面机器人：几行代码就能让机器人做动作、说话、看世界。

> 🌐 [English](README.md) | 中文

[![PyPI](https://img.shields.io/pypi/v/watcherobot)](https://pypi.org/project/watcherobot/)
[![Python](https://img.shields.io/pypi/pyversions/watcherobot)](https://pypi.org/project/watcherobot/)

## 🚀 5 分钟上手

```powershell
# 1. 安装（推荐 Python 3.11，独立环境）
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install watcherobot

# 2. 首次配对机器人（引导式，跟着提示走即可）
watcherobot robot setup

# 3. 创建并运行你的第一个应用
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

> 💡 **没有安装 Conda？** 两条路任选：
>
> - **最简路径** —— 不用 Conda，直接装进系统 Python（3.10–3.12）：
>   ```powershell
>   python -m pip install --upgrade pip
>   python -m pip install watcherobot
>   ```
> - **推荐长期使用** —— 先[安装 Miniconda](https://docs.anaconda.com/miniconda/install/)，
>   再执行上面的命令。独立环境可以避免和机器上其他 Python 项目的依赖冲突。
>
> 两条路最终都得到同一个 `watcherobot` 命令，先用哪条都行，之后随时可切换。

跑通后，试试让机器人开心一下：

```python
import asyncio
from watcherobot.application import ApplicationContext

async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)

asyncio.run(main())
```

Application 上下文提供三个常用入口：`app.robot`（机器人能力）、
`app.desktop`（与 Watcher Desktop 的业务消息通道）、`app.logger`（日志）。

没有机器人在手边也能跑：`app run` 会进入离线模式并提示下一步。

随时验证安装是否成功：

```powershell
watcherobot --version
```

首次配置的完整流程（蓝牙扫描、用 **Up/Down** 选择稳定的 **Device ID**、在机器人上打开
**Settings > Wi-Fi**、旧固件的 **Bluetooth ID** 兼容回退，以及通过机器人上的
**"Python SDK"** 应用输入配对码）见[安装指南](docs/installation.zh-CN.md)。

## 🧭 你想做什么？

| 你的需求 | 去这里 |
| --- | --- |
| 让机器人做动作 / 说话 / 亮灯 | [快速开始](#-5-分钟上手) 和 [SDK Application 指南](docs/application-marketplace/sdk-application-usage.md) |
| 看源码、理解核心运行机制 | [架构概览](#️-它是如何工作的runtimedaemon) |
| 用摄像头 / 麦克风 / 人脸跟踪 | [视觉诊断](docs/vision-diagnostics.md)、[人脸跟踪预览](docs/face-tracking-preview.md)、[麦克风音频](docs/microphone-audio.md) |
| 蓝牙配网 Wi-Fi | [蓝牙配网指南](docs/bluetooth-provisioning.md) |
| 发布应用到 Marketplace | [Marketplace 文档](docs/application-marketplace/README.md) |
| 配对 / 连接出问题了 | [Troubleshooting](docs/troubleshooting.md) |
| 查所有命令的用法 | [完整 CLI 参考](docs/cli-reference.md) |
| 从可运行的例子学 | [Application 示例](examples/README.md) |

## ⚙️ 它是如何工作的？（Runtime/Daemon）

这个包有两个互补的角色：

- **Application SDK** — 面向开发者的公开 Python API。
- **Runtime/Daemon** — 唯一的本地运行时，负责与机器人配对、持有设备连接、管理 Application 进程。

你的 Application 只写产品逻辑；Runtime 处理配对、连接、生命周期、日志和传输。桌面端也使用同一份Runtime/Daemon实现——Watcher Desktop 不会内嵌另一份 Daemon。

```text
Your Application
  └─ ApplicationContext / ApplicationChannels
       └─ WatcheRobot Runtime (Daemon)
            ├─ pairing, device connection, logs, process lifecycle
            ├─ Desktop channel ─────────────── Watcher Desktop
            └─ Device channel ──────────────── WatcheRobot device
```

Application 永远不会自己开发现套接字或设备 WebSocket，也拿不到配对凭据。有 Application 在运行时，Desktop 与设备的业务帧经过它；没有时，Runtime 在 Desktop 与设备之间透明转发。

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
| 端到端构建并测试一个 Application | [SDK Application 指南](docs/application-marketplace/sdk-application-usage.md) |
| 发布一个通过审核的 Marketplace 应用 | [Marketplace 文档](docs/application-marketplace/README.md) 及 [分发参考](docs/application-marketplace/application-cli-reference.md) |
| 选择设备行为状态 | [ESP32-S3 v0.3.4 状态目录](docs/device-states/README.md) |
| 官方资源与创作者作品 | [资源与作品指南](docs/resources.md) |
| 诊断配对、连接或运行时问题 | [Troubleshooting](docs/troubleshooting.md) 与 [Runtime 契约](docs/contracts/runtime-profile-index.md) |
| 在官方 Workspace 中做源码集成 | `yarn desktop:dev`（见 [Workspace 说明](docs/installation.md)） |

## ⌨️ 常用命令速查

```powershell
# Runtime 生命周期
watcherobot daemon start / status / stop

# 机器人首次配置与连接
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456      # 123456 换成机器人屏幕上的配对码

# 应用开发与分发
watcherobot app init my_app && cd my_app && watcherobot app run
watcherobot app check .            # 发布前校验
watcherobot app publish .          # 登录后发布到 Marketplace
watcherobot app install <app-id>   # 从 Marketplace 安装
watcherobot app list               # 列出已安装应用
watcherobot app uninstall <app-id> # 卸载
```

诊断运行时可读取 `GET /daemon/logs`（本地控制 API）。全部命令见 [CLI 参考](docs/cli-reference.md)。

## ✅ 环境要求

- Python 3.10–3.12（推荐 3.11）
- Windows 或 macOS（蓝牙 Wi-Fi 配网需要）
- 一台 WatcheRobot 设备（配对和硬件功能需要）

当前稳定版本：[`watcherobot 0.1.1`](https://pypi.org/project/watcherobot/0.1.1/)。维护者发布流程见 [releasing](docs/releasing.md)。许可证：[Apache-2.0](LICENSE)。
