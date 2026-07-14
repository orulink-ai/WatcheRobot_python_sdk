# Hardware testing

`tools/hardware_smoke.py` 是 WatcheRobot Python SDK 的维护者台架工具，不是用户 quickstart。它可以顺序验证
灯光、动画、主机 WAV、Behavior、动作、摄像头和麦克风，并把媒体结果统一写入 `artifacts/hardware-smoke/`。

## 人工交互模式

使用机器人屏幕上的配对码：

```bash
python -m pip install -e ".[hardware]"
python tools/hardware_smoke.py --pairing-code 123456 --all
```

脚本会在每项执行后等待确认；输入 `f` 可把当前项目标记为失败。

## 自动台架模式

开发固件启用 `CONFIG_WATCHER_DEBUG_CLI_ENABLE` 后，可以通过串口临时打开 SDK Control App 并读取测试
配对码：

```bash
python tools/hardware_smoke.py --auto-pair-port COM5 --all --non-interactive
```

该能力仅用于本地开发台架。生产固件必须关闭 Debug CLI，不应把配对码写入串口。

## 输出与隐私

- 默认输出目录：`artifacts/hardware-smoke/`
- 摄像头文件：`camera.jpg`
- 麦克风文件：`microphone.wav`
- `artifacts/` 整体被 Git 忽略

拍照和录音前应确认现场人员知情。测试结束后由操作者决定保留或删除本地产物。
