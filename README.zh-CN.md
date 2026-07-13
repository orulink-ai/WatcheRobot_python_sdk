# WatcheRobot Python SDK

`watcherobot` 是面向桌面端和同一局域网的同步 Python SDK。电脑会启动轻量 UDP Discovery 和 WebSocket
网关，机器人上的 **Python SDK** App 主动发现并连接电脑。

```bash
pip install watcherobot
```

## 三步开始

1. 电脑和机器人连接同一个局域网。
2. 在机器人启动器中手动打开 **Python SDK**，记下临时六位配对码。
3. 运行下面的代码：

```python
from watcherobot import WatcheRobot

with WatcheRobot.connect(pairing_code="123456") as robot:
    robot.behavior.play("greeting").wait(timeout=5)
    robot.motion.move_to(pan_deg=110, tilt_deg=120, duration=0.5)
    robot.animation.play("smile")
    robot.audio.play("confirm")
    robot.lights.set_color("#4DA3FF", brightness=0.7)

    with robot.microphone.open() as microphone:
        pcm = microphone.read(timeout=1)

    image = robot.camera.capture()
```

默认端口是 `37021/UDP` 和 `8766/TCP`。v1 使用普通 `ws://`，只适合可信局域网；同一 SDK 实例同时只
控制一台机器人。Behavior、动画和音效资源需要预先安装在机器人中。

Job 的 ACK 只表示机器人接收了命令，`job.wait()` 等待的是设备上报的真正完成事件。麦克风默认输出
PCM 16-bit、16 kHz、单声道；队列满时丢弃最旧帧，并通过 `microphone.dropped_frames` 提供统计。
