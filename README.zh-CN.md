# WatcheRobot Python SDK

[English](README.md) | 简体中文

`watcherobot` 让桌面端 Python 程序通过局域网控制 WatcheRobot 的 Behavior、动作、动画、音频、灯光、
麦克风和摄像头，也可以接收背部触摸、屏幕点按和滚轮旋转事件。对外提供简单的同步 API，Discovery
和 WebSocket 网关在内部运行。

> v0.1 面向可信局域网，使用普通 `ws://`、单次六位配对码和单机器人控制会话。

六位配对码同时用于 Discovery `1.1` 的过滤：SDK 只响应配对码一致的 UDP 请求，并在 `ANNOUNCE` 中回传相同的
配对码和请求 ID；WebSocket 的 `sys.client.hello` 还会再次校验该码。这能避免同一可信局域网中的其他 SDK 进程抢先
响应并被设备误连；它不提供加密，也不适合不可信网络。

## 使用前提

| 项目 | 需要确认 |
|---|---|
| 机器人 | 基于 SenseCAP Watcher、Wi-Fi 工作正常的 WatcheRobot |
| 固件 | `V3.1`，且 Launcher 中存在 **SDK Control App**；当前源码候选版本见 [ESP32 PR #96](https://github.com/orulink-ai/WatcheRobot_esp32/pull/96) |
| 网络 | 电脑与机器人位于同一局域网；本机防火墙允许 UDP `37021` 和 TCP `8766` |
| Python | CPython 3.10、3.11 或 3.12 |

目前 ESP32 最新正式 Release 尚未包含 SDK Control App，也没有可公开下载并受支持的 `V3.1` 二进制包。
项目贡献者可以使用 ESP-IDF `v5.2.1` 构建候选固件：

```powershell
gh repo clone orulink-ai/WatcheRobot_esp32
Set-Location WatcheRobot_esp32
gh pr checkout 96
& C:\Espressif\frameworks\esp-idf-v5.2.1\export.ps1
Set-Location firmware\s3
idf.py set-target esp32s3
powershell -ExecutionPolicy Bypass -File .\tools\flash-monitor.ps1 COMx -NoWake
```

请将 `COMx` 替换成机器人的实际串口，并查看候选分支的
[V3.1 发布指南](https://github.com/orulink-ai/WatcheRobot_esp32/blob/codex/esp32-v3.1-release-prep/firmware/s3/docs/V3_1_RELEASE_GUIDE.md)
确认构建配置和晋级状态。普通使用者应等待 [ESP32 Releases](https://github.com/orulink-ai/WatcheRobot_esp32/releases)
发布 `V3.1` 固件包，不要烧录缺少 SDK Control App 的旧正式版。

## 安装

当前源码正在准备 `0.1.0a4` 测试版本。由于 PyPI 版本不可覆盖，请先在
[TestPyPI 项目页](https://test.pypi.org/project/watcherobot/)确认该版本已经由发布流水线生成。TestPyPI
不保证包含完整依赖，因此先从正式 PyPI 安装依赖，再从 TestPyPI 安装 SDK 本身：

```bash
python -m pip install "websockets>=12,<16"
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps watcherobot==0.1.0a4
```

从源码仓库开发或试用：

```bash
python -m pip install -e .
```

正式 PyPI 版本尚未发布。正式发布后可使用：

```bash
python -m pip install watcherobot
```

需要 Python 3.10 或更高版本。

## 兼容性

| Python SDK | 协议 | 已验证 ESP32 固件 | Python | 发布状态 |
|---|---|---|---|---|
| `0.1.0a4` | `1.0` | `V3.1`，包含 SDK 输入事件扩展 | `>=3.10`（CI：3.10 / 3.11 / 3.12） | Alpha 候选版 / 发布后进入 TestPyPI |

连接后应读取 `robot.device_info` 和 `robot.capabilities`，以设备实际协商结果为准。当前尚未承诺低于
`V3.1` 的固件兼容性。

## 最小首次连接

1. 电脑和机器人连接同一个局域网。
2. 在机器人 Launcher 中打开 **SDK Control App**。
3. 记下机器人屏幕显示的六位临时配对码。
4. 将下面代码保存为 `hello_robot.py`，然后运行 `python hello_robot.py`：

```python
from watcherobot import WatcheRobot

pairing_code = input("请输入六位配对码：").strip()
with WatcheRobot.connect(pairing_code=pairing_code) as robot:
    print("已连接：", robot.device_info)
    print("设备能力：", robot.capabilities)
    robot.behavior.play("happy", repeat=1).wait(timeout=20)
```

正常输出类似：

```text
已连接： {'device_id': 'watcher-AF8C', 'firmware_version': 'V3.1', ...}
设备能力： ('behavior', 'animation', 'motion', ..., 'camera.capture')
```

`ConnectionTimeoutError`、`AuthenticationError` 以及结构化 `JobFailedError`（`error.job_id`、
`error.reason`、`error.error_code`）的完整可运行处理示例见[常见故障排查](docs/troubleshooting.md)。

## 能力协商

调用可选能力前可以使用 `robot.supports(...)`。能力名称是可扩展字符串，不使用封闭枚举。

| 设备协商能力 | 对应 API |
|---|---|
| `behavior` | `robot.behavior.*` |
| `animation` | `robot.animation.*` |
| `motion` | `robot.motion.*` |
| `audio` | `robot.audio.play(...)` |
| `audio.stream` | `robot.audio.play_file(...)`、`robot.audio.play_pcm(...)` |
| `light` | `robot.lights.*` |
| `microphone` | `robot.microphone.*` |
| `camera.capture` | `robot.camera.capture(...)` |
| `input.back_touch` | `robot.inputs.wait(...)` → `BackTouchEvent` |
| `input.screen_touch` | `robot.inputs.wait(...)` → `ScreenTouchEvent` |
| `input.roller` | `robot.inputs.wait(...)` → `RollerEvent` |

```python
if robot.supports("camera.capture"):
    image = robot.camera.capture(timeout=10)
```

例如，一个桌面小游戏可以等待用户下一次操作，再按事件类型作出反应：

```python
from watcherobot import BackTouchEvent, RollerEvent, ScreenTouchEvent

event = robot.inputs.wait(timeout=30)
if isinstance(event, BackTouchEvent) and event.action == "press":
    print("用户摸了机器人背部")
elif isinstance(event, ScreenTouchEvent):
    print(f"用户点了屏幕坐标 ({event.x}, {event.y})")
elif isinstance(event, RollerEvent):
    print("滚轮转动量：", event.delta)
```

输入队列保留最新 64 条事件；`robot.inputs.dropped_events` 可查看溢出数量，`robot.inputs.clear()`
可清空缓存。连接断开时，正在等待的 `wait()` 会收到 `WatcheRobotError`，不会一直卡住。

## 仓库示例

运行以下文件前，需要先 clone 本仓库并执行 `python -m pip install -e .`：

- `examples/quickstart.py`：在一个文件中直接调用主要 SDK 能力，并在动作、拍照和录音前等待确认。
- `examples/play_audio_file.py`：把电脑 WAV 传给机器人播放。
- `examples/capture_photo.py`：拍摄单张 JPEG。
- `examples/record_microphone.py`：录制五秒麦克风音频。

拍照和录音会先提示用户确认，结果统一写入被 Git 忽略的 `artifacts/`。详细说明见
[examples/README.md](examples/README.md)。

## 当前支持的功能

| 能力 | SDK 函数 | 返回值 / 执行方式 | v1 说明 |
|---|---|---|---|
| 连接与关闭 | `WatcheRobot.connect(...)`<br>`robot.close()`<br>`robot.device_info` / `robot.capabilities`<br>`robot.supports(capability)` | `WatcheRobot` / 立即执行 / 只读属性 | 启动局域网 Discovery 与 WebSocket 网关；同一实例控制一台机器人 |
| Behavior | `robot.behavior.play(id, repeat=1)`<br>`robot.behavior.stop()` | `Job` / 立即执行 | 播放机器人中已安装的多轨 Behavior |
| 动画 | `robot.animation.play(id)`<br>`robot.animation.stop()` | `Job` / 立即执行 | 动画资源必须已安装在机器人中 |
| 定点动作 | `robot.motion.move_to(pan_deg=..., tilt_deg=..., duration_ms=...)` | `Job` | `duration_ms` 使用 `1..65535` 的整数毫秒 |
| 实时动作 | `robot.motion.set_target(pan_deg=..., tilt_deg=...)` | 立即执行 | latest-wins，不等待动作完成 |
| 命名动作 | `robot.motion.play_action(id)`<br>`robot.motion.stop()` | `Job` / 立即执行 | 命名动作必须已安装在机器人中 |
| 内置音效 | `robot.audio.play(sound_id)` | `Job` | 音效资源必须已安装在机器人中 |
| 电脑音频 | `robot.audio.play_file(path)`<br>`robot.audio.play_pcm(data, ...)`<br>`robot.audio.stop()` | `AudioPlayback` / 立即执行 | PCM S16LE、24 kHz、单声道；单次最多 4 MB |
| 灯光 | `robot.lights.set_color(...)`<br>`robot.lights.play_effect(..., period_ms=500)`<br>`robot.lights.off()` | 立即执行 / `Job` / 立即执行 | 颜色使用 `#RRGGBB`，亮度范围 `0..1`；`period_ms` 使用 `0..65535` 的整数毫秒 |
| 麦克风会话 | `robot.microphone.open()`<br>`MicrophoneSession.read(timeout=...)`<br>`MicrophoneSession.close()` | `MicrophoneSession` / `AudioFrame` / 立即执行 | 当前默认 PCM 16 kHz、16-bit、单声道；有界队列会统计丢帧 |
| 便捷录音 | `robot.microphone.record(duration=...)`<br>`AudioRecording.save(path)` | `AudioRecording` / `Path` | `duration` 使用秒；保存为标准 WAV |
| 摄像头拍照 | `robot.camera.capture(...)`<br>`ImageFrame.save(path)` | `ImageFrame` / `Path` | 单张 JPEG；连续视频流不属于 v1 |
| 物理输入事件 | `robot.inputs.wait(timeout=...)`<br>`robot.inputs.clear()`<br>`robot.inputs.dropped_events` | `BackTouchEvent` / `ScreenTouchEvent` / `RollerEvent` | 背部按下/松开、屏幕点按坐标和带方向的滚轮转动量；有界队列保留最新 64 条 |
| Job 生命周期 | `Job.wait(timeout=...)`<br>`Job.cancel()`<br>`Job.reason` / `Job.error_code` | `Job` / 立即请求取消 | `STARTING → RUNNING → COMPLETED / FAILED / CANCELLED`；终态异常提供结构化诊断信息 |

有限操作返回 `Job` 或兼容 `Job` 的 `AudioPlayback`。ACK 只表示设备已经接收命令；使用
`Job.wait()` 才能等待设备上报最终执行结果。

当前固件中可直接尝试的资源 ID 见 [出厂资源说明](docs/resources.md)。遇到配对、`not_found`、超时、
音频格式或媒体丢帧问题时，见 [常见故障排查](docs/troubleshooting.md)。

## v1 边界

- Behavior、动画和 `audio.play(sound_id)` 要求资源已安装在机器人上。
- 支持临时传输电脑 WAV，但不支持持久安装任意资源。
- 暂不支持连续视频、Python 内联 Behavior、公开异步 API、TLS 和远程唤起。
- 滚轮短按继续用于本机退出，长按继续用于系统关机；v1 只向 Python 提供滚轮旋转事件。
- SDK、机器人 App 或网络连接关闭时，设备会停止 Job、媒体和输出并释放资源。

协议说明见 [docs/protocol-v1.md](docs/protocol-v1.md)。

## 开发验证

```bash
python -m pytest
python -m build
```

网关集成测试使用 `tests/fakes/` 中的轻量机器人协议测试替身，在没有硬件时覆盖配对、指令 ACK、Job
生命周期、断线和失败路径。它只服务于自动化测试：wheel 不包含模拟器，公开 SDK 也不提供模拟 API。
CI 会在 Python 3.10-3.12 上分别验证最低版本 `websockets 12.x` 和当前允许的最新版
`websockets <16`。

维护者发布流程见 [docs/releasing.md](docs/releasing.md)。项目使用 GitHub Actions 与 PyPI Trusted
Publishing，不保存长期上传 Token。仅供维护者使用的串口自动化台架见
[docs/hardware-testing.md](docs/hardware-testing.md)。
