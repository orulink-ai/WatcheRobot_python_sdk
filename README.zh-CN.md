# WatcheRobot Python SDK

运行时维护：[共享 Daemon、应用依赖与升级指南](docs/shared-runtime.zh-CN.md)。

用 Python 控制 WatcheRobot 桌面机器人：几行代码就能让机器人做动作、说话、看世界。

> 🌐 [English](README.md) | 中文

[![PyPI](https://img.shields.io/pypi/v/watcherobot)](https://pypi.org/project/watcherobot/)
[![Python](https://img.shields.io/pypi/pyversions/watcherobot)](https://pypi.org/project/watcherobot/)

<a id="quick-start-zh"></a>

## 🚀 快速开始

开始之前请确认：

- 已安装 Python 3.10–3.12（推荐 3.11）；
- 首次蓝牙配网使用 Windows 或 macOS；
- 硬件步骤需要机器人在身边并保持开机；没有机器人时，仍可离线创建并运行基础 Application；
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

确认 SDK 已安装，并核对命令确实来自当前激活的环境：

```powershell
# Windows PowerShell
Get-Command watcherobot
watcherobot --version
python -m pip show watcherobot
```

```sh
# macOS / Linux
command -v watcherobot
watcherobot --version
python -m pip show watcherobot
```

PEP 668、PATH、`python3`、源码 checkout 和 TestPyPI 的处理方法见
[安装指南](docs/installation.zh-CN.md)。

### 一行启动内置示例

包含此功能的下一版 SDK 安装后，可以直接运行：

```text
watcherobot demo
```

菜单中输入 `1` 启动 SDK 测试台，输入 `2` 启动表情实验台；启动后菜单继续保留，
再次选择即可直接切换，无需重输命令。切换会通过 Daemon 停止当前 Application、
启动所选示例并打开网页。输入 `0` 停止当前应用，输入 `q` 只退出菜单，应用继续运行。
无需下载源码或安装 Node.js；已有兼容 Daemon 的设备连接会继续复用，未连接时可在网页里配对。

自动化脚本仍可指定应用名：`watcherobot demo sdk-test-bench` 或
`watcherobot demo expression-lab`，执行一次后返回终端。

使用 `watcherobot app stop` 停止当前示例。这两个示例目前声明支持 Windows，
macOS/Linux 真机运行尚未验收。照片等运行数据写入用户状态目录的
`bundled-demos` 子目录，不写入 Python 安装目录；再次启动相同版本会保留数据。
升级后若示例内容变化，会使用新的目录，旧照片不会自动删除。

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
watcherobot app init my_app
cd my_app
watcherobot app run
```

初始化器会生成：

```text
my_app/
├─ app.json     # Application 身份、版本和依赖
├─ app.py       # 受管 Application 入口
├─ README.md    # 生成项目的使用说明
├─ icon.svg     # Application 图标
└─ .gitignore
```

`watcherobot app run` 通过 Runtime/Daemon 启动项目，输出启动日志并等待停止。
基础应用不内置设备演示；在 app.py 中编写自己的应用逻辑。

必须使用 `watcherobot app run`，不要直接运行 `python app.py`：Application 需要由
Daemon 注入 Device channel 和 Desktop channel。配对或连接失败时参见
[故障排查](docs/troubleshooting.md)。

### 4. 选择模板并开发

`watcherobot app init my_app`（或显式指定 `--template base`）生成五文件基础应用。
编辑 `my_app/app.py`，停止后重新运行即可应用修改。
`ApplicationContext.from_environment()` 获取 Runtime 注入的受管上下文。
可运行的演示应用另见 [examples](examples/README.md)。

需要语音功能时，直接选择语音模板创建另一个项目：

```powershell
watcherobot app init my_voice_app --template voice
cd my_voice_app
watcherobot app configure
# 可选：只配置某一项凭据
watcherobot app configure --service asr
```

配置命令隐藏输入，已有值按回车保留；仅校验本地配置，不调用云服务，也支持直接编辑 TOML。
按生成的 README 配对和运行。两个模板独立选择，
无需先创建基础应用再转换。语音模板复用五文件基础结构，增加语音代码、配置、凭据和提示词。
SDK 的测试和扩展文档不复制到生成工程。

Application 上下文提供 `app.robot`（机器人能力）、`app.desktop`（与 Watcher Desktop
交换业务消息）和 `app.logger`（Application 日志）。准备正式项目时，可以显式生成稳定元数据：

```powershell
watcherobot app init my_app --id com.example.my_app --author "Example Team"
```

## 🧭 你想做什么？

| 你的需求 | 去这里 |
| --- | --- |
| 让机器人做动作 / 说话 / 亮灯 | [快速开始](#quick-start-zh) 和 [SDK Application 指南](docs/application-marketplace/sdk-application-usage.zh-CN.md) |
| 看源码、理解核心运行机制 | [源码与仓库边界](#source-boundaries-zh)和[运行架构](#runtime-daemon-zh) |
| 用摄像头 / 麦克风 / 人脸跟踪 | [视觉诊断](docs/vision-diagnostics.zh-CN.md)、[人脸跟踪预览（英文）](docs/face-tracking-preview.md)、[跟随退出清理](docs/face-tracking-lifecycle.md)、[麦克风音频（英文）](docs/microphone-audio.md) |
| 蓝牙配网 Wi-Fi | [蓝牙配网指南（英文）](docs/bluetooth-provisioning.md) |
| 发布应用到 Marketplace | [Marketplace 文档（英文）](docs/application-marketplace/README.md) |
| 配对 / 连接出问题了 | [故障排查（英文）](docs/troubleshooting.md) |
| 查所有命令的用法 | [完整 CLI 参考](docs/cli-reference.zh-CN.md) |
| 从可运行的例子学 | [Application 示例（英文）](examples/README.md) |

<a id="runtime-daemon-zh"></a>

## ⚙️ 它是如何工作的？（Runtime/Daemon）

这个包有两个互补的角色：

- **Application SDK** — 面向开发者的公开 Python API。
- **Runtime/Daemon** — 唯一的本地运行时，负责与机器人配对、持有设备连接、管理 Application 进程。

你的 Application 只写产品逻辑；Runtime 处理配对、连接、生命周期、日志和传输。桌面端也使用同一份 Runtime/Daemon 实现——Watcher Desktop 不会内嵌另一份 Daemon。

Daemon 使用与 `--state-root` 无关的用户级协调目录保证单实例。迁移期间，
运行状态会同时发布到共享协调目录和旧版 SDK 默认目录。固定本地管理端点会报告真实的
实例组和外部通道地址作为恢复发现路径，启动器不会猜测地址，也不会误用显式隔离的 Daemon。
只有明确需要隔离实例的测试或开发场景才应设置 `--instance-root` 或
`WATCHER_RUNTIME_INSTANCE_ROOT`；修改它们会有意脱离默认单实例组，并需要配置不同端口才能并行运行。

```text
你的 Application
  └─ ApplicationContext / ApplicationChannels
       └─ WatcheRobot Runtime（Daemon）
            ├─ 配对、设备连接、日志、进程生命周期
            ├─ Desktop channel ─────────────── Watcher Desktop
            └─ Device channel ──────────────── WatcheRobot 设备
```

Application 永远不会自行打开发现套接字或设备 WebSocket，也拿不到配对凭据。有 Application 在运行时，Desktop 与设备的业务帧经过它；没有时，Runtime 在 Desktop 与设备之间透明转发。

<a id="source-boundaries-zh"></a>

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
watcherobot app publish --provider huggingface .          # 上传不可变源码快照
watcherobot app submit --provider huggingface .           # 将快照提交 Marketplace 审核
watcherobot app install --provider huggingface com.example.my_app   # 替换为实际 Application ID
watcherobot app list               # 列出已安装应用
watcherobot app uninstall com.example.my_app
```

常规排障先执行 `watcherobot daemon status`。产品集成可从发现到的本地控制地址读取
`GET /daemon/logs`；端点发现和响应合同见 [Runtime 契约（英文）](docs/contracts/runtime-profile-index.md)。
全部命令见 [CLI 参考](docs/cli-reference.zh-CN.md)。

## ✅ 环境要求

- Python 3.10–3.12（推荐 3.11）
- Windows 或 macOS（蓝牙 Wi-Fi 配网需要）
- 一台 WatcheRobot 设备（配对和硬件功能需要）

当前稳定版本以页面顶部 PyPI 徽章为准。维护者发布流程见 [releasing](docs/releasing.md)。许可证：[Apache-2.0](LICENSE)。
