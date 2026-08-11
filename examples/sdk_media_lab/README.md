# SDK Media Lab

SDK Media Lab is a standalone managed Application for real-device media
acceptance. It serves a loopback-only browser dashboard and never opens a
device connection of its own.

Start the Runtime, pair the Watcher, then run:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

在仓库根目录也可以使用一键 Yarn 脚本。它会复用或启动 SDK Daemon、
选择媒体测试 Application，并自动打开本地中文测试网页：

```powershell
yarn sdk:lab
```

脚本优先使用仓库的 `.venv`；如需指定其他 Python 解释器，可设置
`WATCHEROBOT_PYTHON`。按 `Ctrl+C` 会停止当前测试 Application，Daemon
继续保留，便于设备重连和后续调试。

The Application opens its `http://127.0.0.1:<port>` dashboard automatically.
Set `WATCHER_MEDIA_LAB_NO_BROWSER=1` to suppress automatic browser launch.

The first version tests host-to-device PCM playback, one-shot JPEG capture,
decoded microphone recording, capability discovery, artifacts, and diagnostic
events. Camera and microphone actions capture the surrounding environment;
obtain consent before use and handle generated artifacts appropriately.
