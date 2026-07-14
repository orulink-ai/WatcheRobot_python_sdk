# WatcheRobot Python SDK

[English](README.md) | 简体中文

`watcherobot` 是面向桌面端和同一局域网的同步 Python SDK。电脑启动 UDP Discovery 和 WebSocket
网关，机器人上的 **SDK Control App** 主动发现并连接电脑。

> v0.1 面向可信局域网，使用普通 `ws://`、单次六位配对码和单机器人控制会话。

## 安装

从源码仓库开发或试用：

```bash
python -m pip install -e .
```

正式发布到 PyPI 后可使用：

```bash
python -m pip install watcherobot
```

需要 Python 3.10 或更高版本。

## 第一次连接

1. 电脑和机器人连接同一个局域网。
2. 在机器人 Launcher 中打开 **SDK Control App**。
3. 记下机器人屏幕显示的六位临时配对码。
4. 运行最小连接示例：

```bash
python examples/hello_robot.py
```

最小示例只负责连接、打印设备信息并播放出厂自带的 `happy` Behavior：

```python
from watcherobot import WatcheRobot

with WatcheRobot.connect(pairing_code="123456") as robot:
    print(robot.device_info)
    robot.behavior.play("happy", repeat=1).wait(timeout=20)
```

## 能力示例

- `examples/quickstart.py`：在一个文件中直接调用主要 SDK 能力，并在动作、拍照和录音前等待确认。
- `examples/play_audio_file.py`：把电脑 WAV 传给机器人播放。
- `examples/capture_photo.py`：拍摄单张 JPEG。
- `examples/record_microphone.py`：录制五秒麦克风音频。

拍照和录音会先提示用户确认，结果统一写入被 Git 忽略的 `artifacts/`。详细说明见
[examples/README.md](examples/README.md)。

## 当前支持的功能

| 能力 | SDK 函数 | 返回值 / 执行方式 | v1 说明 |
|---|---|---|---|
| 连接与关闭 | `WatcheRobot.connect(...)`<br>`robot.close()`<br>`robot.device_info` / `robot.capabilities` | `WatcheRobot` / 立即执行 / 只读属性 | 启动局域网 Discovery 与 WebSocket 网关；同一实例控制一台机器人 |
| Behavior | `robot.behavior.play(id, repeat=1)`<br>`robot.behavior.stop()` | `Job` / 立即执行 | 播放机器人中已安装的多轨 Behavior |
| 动画 | `robot.animation.play(id)`<br>`robot.animation.stop()` | `Job` / 立即执行 | 动画资源必须已安装在机器人中 |
| 定点动作 | `robot.motion.move_to(pan_deg=..., tilt_deg=..., duration_ms=...)` | `Job` | `duration_ms` 使用 `1..65535` 的整数毫秒 |
| 实时动作 | `robot.motion.set_target(pan_deg=..., tilt_deg=...)` | 立即执行 | latest-wins，不等待动作完成 |
| 命名动作 | `robot.motion.play_action(id)`<br>`robot.motion.stop()` | `Job` / 立即执行 | 命名动作必须已安装在机器人中 |
| 内置音效 | `robot.audio.play(sound_id)` | `Job` | 音效资源必须已安装在机器人中 |
| 电脑音频 | `robot.audio.play_file(path)`<br>`robot.audio.play_pcm(data, ...)`<br>`robot.audio.stop()` | `AudioPlayback` / 立即执行 | PCM S16LE、24 kHz、单声道；单次最多 4 MB |
| 灯光 | `robot.lights.set_color(...)`<br>`robot.lights.play_effect(...)`<br>`robot.lights.off()` | 立即执行 / `Job` / 立即执行 | 颜色使用 `#RRGGBB`，亮度范围 `0..1`；`period` 当前使用秒 |
| 麦克风会话 | `robot.microphone.open()`<br>`MicrophoneSession.read(timeout=...)`<br>`MicrophoneSession.close()` | `MicrophoneSession` / `AudioFrame` / 立即执行 | 当前默认 PCM 16 kHz、16-bit、单声道；有界队列会统计丢帧 |
| 便捷录音 | `robot.microphone.record(duration=...)`<br>`AudioRecording.save(path)` | `AudioRecording` / `Path` | `duration` 使用秒；保存为标准 WAV |
| 摄像头拍照 | `robot.camera.capture(...)`<br>`ImageFrame.save(path)` | `ImageFrame` / `Path` | 单张 JPEG；连续视频流不属于 v1 |
| Job 生命周期 | `Job.wait(timeout=...)`<br>`Job.cancel()` | `Job` / 立即请求取消 | `STARTING → RUNNING → COMPLETED / FAILED / CANCELLED` |

有限操作返回 `Job` 或兼容 `Job` 的 `AudioPlayback`。ACK 只表示设备已经接收命令；使用
`Job.wait()` 才能等待设备上报最终执行结果。

## 维护者硬件验收

完整灯光、动画、音频、Behavior、动作、摄像头和麦克风验收不属于公开 quickstart，位于：

```bash
python -m pip install -e ".[hardware]"
python tools/hardware_smoke.py --auto-pair-port COM5 --all --non-interactive
```

串口自动配对只用于启用 `CONFIG_WATCHER_DEBUG_CLI_ENABLE` 的开发固件；生产固件必须关闭该选项。
详见 [docs/hardware-testing.md](docs/hardware-testing.md)。

## v1 边界

- Behavior、动画和 `audio.play(sound_id)` 要求资源已安装在机器人上。
- 支持临时传输电脑 WAV，但不支持持久安装任意资源。
- 暂不支持连续视频、Python 内联 Behavior、公开异步 API、TLS 和远程唤起。
- SDK、机器人 App 或网络连接关闭时，设备会停止 Job、媒体和输出并释放资源。

协议说明见 [docs/protocol-v1.md](docs/protocol-v1.md)。

## 开发验证

```bash
python -m pytest
python -m build
```

维护者发布流程见 [docs/releasing.md](docs/releasing.md)。项目使用 GitHub Actions 与 PyPI Trusted
Publishing，不保存长期上传 Token。
