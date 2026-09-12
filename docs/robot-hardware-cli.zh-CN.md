# 机器人硬件 CLI

`watcherobot robot` 提供面向人工调试和自动化脚本的硬件命令。执行前必须已经启动 Daemon、机器人在线，并停止当前 Application：

```powershell
watcherobot app stop
watcherobot robot capabilities
```

Daemon 只透明转发业务文本和 WSPK 二进制帧；CLI 不会建立绕过 Daemon 的设备连接。Application 运行时命令会明确拒绝，避免两个控制方同时占用媒体和硬件资源。

## 常用命令

```powershell
watcherobot robot camera capture -o photo.jpg
watcherobot robot camera record -o video.mp4 --duration 10
watcherobot robot camera record -o video-with-audio.mp4 --with-audio --duration 10
watcherobot robot audio record -o microphone.wav --duration 10
watcherobot robot camera record --storage device -o device-video.mp4 --duration 10
watcherobot robot audio play .\prompt.mp3
watcherobot robot audio stop

watcherobot robot light set --zone side --color '#4DA3FF' --brightness 70
watcherobot robot light effect breathing --zone bottom --color '#00FF80' --period 800
watcherobot robot light status
watcherobot robot light off

watcherobot robot screen list
watcherobot robot screen play idle
watcherobot robot screen play-work demo --clip main
watcherobot robot screen install .\face.gif
watcherobot robot screen delete-work demo --port COM8

watcherobot robot recording list
watcherobot robot recording status rec_123
watcherobot robot recording stop rec_123
watcherobot robot recording download rec_123 -o recovered.mp4
watcherobot robot recording delete rec_123
```

拍照、录像和录音的结果直接写入运行 CLI 的电脑；未传 `-o` 时在当前目录生成带时间戳的文件名。`camera record` 和 `audio record` 默认使用 `--storage host`：设备按单调时间戳生成 WREC 记录，经独立可靠队列实时发送，CLI 先写入 `.wrec.part`，完整收到终止标记并校验后原子重命名，再生成 MP4 或 WAV。该模式不消耗设备 SD 空间。

预览和录制采用不同策略。预览允许覆盖旧帧以维持低延迟；录制不允许静默丢帧。WebSocket/TCP 会在连接存续期间重传网络丢包，WSPK 分片序号和 WREC 媒体序号负责检测遗漏。队列溢出、序号缺口、发送失败或断线都会让录制明确失败，并保留 `.wrec.part` 供诊断，不会输出一个伪成功文件。首版不承诺 Wi-Fi 完全断开后的跨连接续录。

明确传入 `--storage device` 时沿用设备 SD 可靠录制和可续传下载。下载或 MP4/WAV 封装失败会保留本地 WREC 临时目录和设备副本，重新运行 `recording download` 即可继续；只有显式 `recording delete` 才删除设备副本。

`camera record --with-audio` 的 `av` 是设备端 JPEG 与 16 kHz 单声道 PCM 同步采集，不是 RTC 全双工通话。电脑使用记录中的单调时间戳生成 H.264/yuv420p + AAC MP4。无 `--duration` 时持续到 Ctrl+C 或 15 秒控制 heartbeat 超时；只有 `--storage device` 会受 SD 保护线约束，并保留 `max(256 MiB, SD 总容量 5%)`。

音频播放接受 WAV、MP3、OGG，由电脑转换为 24 kHz、16-bit、mono PCM，并继续遵守 4 MiB PCM 上限。灯区为 `side`、`bottom`、`all`，`head` 是 `side` 的兼容别名；摄像头和麦克风隐私指示灯由固件自动控制，`light status` 只读展示，任何 CLI 命令都不能覆盖。

屏幕安装接受 GIF、PNG、JPEG、WebP，复用 Creator Work 构建与维护传输，生成 `main` clip。动画遵守 206×206、最多 120 帧和 AnimPack v2 约束。一个串口候选时自动选择；多个候选时必须传 `--port`，读卡器安装可传 `--volume-id`。

## 机器输出和退出行为

叶子命令支持 `--json` 输出单个稳定 JSON 对象；录制、下载和安装等持续任务支持 `--jsonl` 输出逐行进度。参数错误、Daemon 未启动、机器人离线、能力缺失、Application 正在运行、校验失败和设备拒绝均以非零退出码结束。不要同时传 `--json` 与 `--jsonl`。

## Python SDK

可靠录制能力也可通过 `robot.recordings` 使用：

```python
recording = robot.recordings.start("av", width=640, height=480, fps=5, quality=80)
# 持续任务应每 5 秒调用 heartbeat；停止会等待设备安全封口。
robot.recordings.heartbeat(recording.id)
info = recording.stop()
download = robot.recordings.download(info.id, ".recording-cache")

# 主机实时录制先注册 stream_id，再发起设备采集，避免首包竞态。
host = robot.recordings.start_host("audio", duration=10)
with open("microphone.wrec.part", "wb") as output:
    while True:
        frame = host.read(timeout=1)
        output.write(frame.payload)
        if frame.flags & 0x02:  # FLAG_LAST
            break
host.close()
```

公开类型 `DeviceRecording`、`HostRecording`、`RecordingInfo` 和 `RecordingDownload` 暴露录制 ID、模式、状态、时长、字节数、分段、校验值、停止原因和媒体参数。WREC 解析会校验 CRC32 和每种媒体的序号连续性；主机录制还会校验 WSPK 分片序号，设备下载会校验分段 SHA-256。

## 验收重点

真机发布前至少验证拍照、纯视频、纯音频、带声音视频、音频播放、两个灯区、四种灯效、动画安装/播放，以及 10 分钟录制。长时门禁还应覆盖 1 小时、录至保护线、Wi-Fi 断开、CLI 退出、设备重启、SD 拔出、断点续传和系统播放器/浏览器播放。涉及摄像头或麦克风时先取得周围人员同意并妥善处理产物。
