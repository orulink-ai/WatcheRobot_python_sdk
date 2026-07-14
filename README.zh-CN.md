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
4. 运行：

```bash
python examples/quickstart.py
```

最小示例只负责连接、打印设备信息并播放出厂自带的 `happy` Behavior：

```python
from watcherobot import WatcheRobot

with WatcheRobot.connect(pairing_code="123456") as robot:
    print(robot.device_info)
    robot.behavior.play("happy", repeat=1).wait(timeout=20)
```

## 能力示例

- `examples/play_audio_file.py`：把电脑 WAV 传给机器人播放。
- `examples/capture_photo.py`：拍摄单张 JPEG。
- `examples/record_microphone.py`：录制五秒麦克风音频。

拍照和录音会先提示用户确认，结果统一写入被 Git 忽略的 `artifacts/`。详细说明见
[examples/README.md](examples/README.md)。

## API 模型

- `robot.behavior.play(...)` 播放机器人上已安装的多轨 Behavior。
- `robot.animation`、`robot.motion`、`robot.audio`、`robot.lights` 提供单领域直接控制。
- `robot.audio.play(sound_id)` 播放内置资源；`robot.audio.play_file(path)` 传输电脑 WAV。
- 有限操作返回 `Job`；`Job.wait()` 等待设备终态，ACK 本身不等于执行完成。
- `motion.set_target(...)` 是 latest-wins 实时命令，不返回 Job。
- `robot.microphone.open()` 提供 PCM S16LE、16 kHz、单声道帧和丢帧统计。
- `robot.camera.capture()` 返回单张 JPEG `ImageFrame`。

`play_file()` 在 v1 接受 PCM S16LE、24 kHz、单声道 WAV，单个音频流最大 4 MB。

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
