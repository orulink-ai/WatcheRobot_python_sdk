# WatcheRobot Python SDK examples

- `display_text.py` shows a persistent Simplified Chinese text page and clears it after confirmation.

Before running an example, connect the computer and robot to the same LAN and open **SDK Control App** from the
robot Launcher. / 运行前，请先让电脑和机器人连接同一局域网，并在 Launcher 中打开 **SDK Control App**。

| Example / 示例 | Purpose / 用途 | Local output / 本地产物 |
|---|---|---|
| `hello_robot.py` | Minimal connection and `happy` Behavior / 最小连接与 `happy` Behavior | None / 无 |
| `quickstart.py` | Behavior, animation, lights, motion, host audio, photo, and recording / 主要能力导览 | `artifacts/quickstart/` |
| `play_audio_file.py` | Transfer and play `assets/sample_speech.wav` / 传输并播放 WAV | None / 无 |
| `capture_photo.py` | Capture one JPEG / 拍摄单张 JPEG | `artifacts/camera.jpg` |
| `record_microphone.py` | Record five-second PCM WAV / 录制五秒 PCM WAV | `artifacts/microphone.wav` |

Clone the repository, install the source version, and run / clone 仓库并安装源码版本后运行：

```bash
python -m pip install -e .
python examples/hello_robot.py
```

After the minimal example succeeds, run the full capability tour / 最小连接成功后运行完整能力导览：

```bash
python examples/quickstart.py
```

`quickstart.py` calls the public SDK directly without the hardware smoke framework. It waits for confirmation before
motion, camera, and microphone access. / 它直接调用公开 SDK，并在动作、拍照和录音前等待人工确认。

The pairing code can also come from an environment variable / 也可通过环境变量提供配对码：

```powershell
$env:WATCHEROBOT_PAIRING_CODE="123456"
python examples/play_audio_file.py
```

Camera and microphone examples request confirmation before access. Handle photos and recordings under `artifacts/`
responsibly; Git ignores this directory. / 摄像头和麦克风示例会先提示确认，请妥善处理 `artifacts/` 中的
照片和录音；该目录不会进入 Git。

Maintainer bench validation and serial-assisted pairing live in `tools/hardware_smoke.py`; they are not user examples.
/ 维护者台架验收和串口自动配对位于 `tools/hardware_smoke.py`，不属于用户教学示例。
