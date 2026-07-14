# WatcheRobot Python SDK examples

运行前，请先让电脑和机器人连接同一局域网，并在机器人 Launcher 中打开 **SDK Control App**。

| 示例 | 用途 | 本地产物 |
|---|---|---|
| `quickstart.py` | 最小连接与 `happy` Behavior | 无 |
| `play_audio_file.py` | 传输并播放 `assets/sample_speech.wav` | 无 |
| `capture_photo.py` | 拍摄单张 JPEG | `artifacts/camera.jpg` |
| `record_microphone.py` | 录制五秒 PCM WAV | `artifacts/microphone.wav` |

安装源码版本后直接运行：

```bash
python -m pip install -e .
python examples/quickstart.py
```

也可以通过环境变量提供配对码，方便本地脚本化：

```powershell
$env:WATCHEROBOT_PAIRING_CODE="123456"
python examples/play_audio_file.py
```

摄像头和麦克风示例会在调用设备前明确提示。请确认现场人员知情，并妥善处理 `artifacts/` 中的照片
和录音；该目录不会进入 Git。

完整台架验收、串口自动打开 App 和自动读取调试配对码位于 `tools/hardware_smoke.py`，不属于用户教学示例。
