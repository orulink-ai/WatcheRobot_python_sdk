# 机器人对讲 / Robot Intercom

通过同一局域网内的 WatcheRobot 进行双向原声通话。电脑麦克风的声音由机器人扬声器播放，机器人麦克风的现场声音返回电脑；机器人默认程序表情的嘴型跟随**实际播放的音频**。通话中点击拍照，可以查看并保存 JPEG 照片。

这是从 SDK Test Bench 的组合场景抽离的独立 Application，具有自己的应用身份、界面与生命周期。发布包包含全部网页资源，运行时不依赖测试平台目录，也不请求外部 CDN。应用由 SDK Daemon 管理，控制与信令使用注入的 Device channel，不另建机器人业务 WebSocket，不修改 Daemon 路由。

## 使用

1. 在 WatcheRobot 桌面端将电脑和机器人连接到同一局域网，并完成机器人配对。
2. 安装并运行“机器人对讲”，本机会打开应用网页。
3. 点击“开始通话”，允许浏览器使用电脑麦克风。建议戴耳机避免现场声学回声。
4. 使用“麦克风开启 / 已静音”控制向机器人发送声音；“现场声音”控制本机收听。
5. 通话中点击“拍照”，收到照片后点击“保存照片”。不启动持续视频，不使用会占用显示和音频的拍照反馈效果。
6. 点击“结束通话”释放通话与嘴型。关闭页面同样请求释放；浏览器异常退出后，本机连接关闭或失去响应也会触发清理。

只支持局域网，不提供公网穿透、STUN/TURN、AI 回答、语音识别或云端录音。现场声音支持本机 RNNoise 降噪，可通过复选框关闭；处理器不可用时使用原始现场声音。第三方许可见 `web/RNNOISE-NOTICE.md` 与相邻许可证文件。

## 要求与照片存储

- SDK `>=0.1.11,<0.2`；当前分发清单仅声明 Windows。
- 固件需提供 `rtc.audio.full_duplex.v1`、`expression.audio_follow.v1` 和 `camera.capture`。缺少能力时禁止启动并提示升级。
- Chromium / Edge 等支持麦克风与 WebRTC 的浏览器。
- 网页仅监听 `127.0.0.1`。首次分配端口后会在本机保存并在重启时复用，使浏览器能够复用麦克风权限；首选端口占用时，本次使用临时端口。局域网媒体连接由浏览器 WebRTC 与机器人协商。
- Windows 照片位于 `%LOCALAPPDATA%/WatcheRobot/robot-intercom/photos`，按独立文件保存。照片不会进入发布源码，应用也不上传照片。
- 结束应用会停止本应用拥有的通话和音频跟随表情；不宣称恢复固件的随机 SD 表情及自动行为。
- 一次管理一台机器人。通话和资源释放期间不要切换设备；临时断线后，可在原配对会话恢复连接并重试释放。若重新配对产生了新会话，应用拒绝向新会话发送旧通话的释放指令，此时需确认原机器人已结束通话后重启应用。

## 通话保活与诊断

Application 后端每秒通过注入的 RTC channel 发送设备保活，拍照不会阻塞这条链路。页面通过本机 WebSocket 声明通话仍然存在，原生 ping/pong 不依赖页面 JavaScript 定时器。因此页面主线程短暂停顿不会使固件的 5 秒保活期限过期。页面关闭或浏览器崩溃会关闭本机连接并释放通话；连接失去响应时，由 5 秒 ping 周期和 10 秒 pong 超时发现。兼容没有本机连接的旧客户端时，仍执行 10 秒页面租约，设备保活不会延长它。

`/api/status` 的 `diagnostics` 返回最近 128 条连接状态、页面可见性、音频包进度与停止原因。相同事件以 `INTERCOM` 前缀写入 Daemon 管理的 Application 日志，便于区分设备 `heartbeat_timeout`、网络连接失败、页面退出及用户主动结束。诊断不保存 SDP、音频或照片内容。重新开始通话会重置本机诊断窗口，历史 Application 日志仍由 Daemon 管理。

## 开发与发布

在装有 SDK 的环境中，从本目录运行（Windows / macOS / Linux 使用同一 SDK 命令）：

```text
watcherobot app check .
watcherobot app run .
```

国内应用商店发布使用已有 SDK 分发能力：

```text
watcherobot app login --provider gitee --status
watcherobot app publish . --provider gitee
watcherobot app submit . --provider gitee --commit <publish 返回的完整提交 SHA>
```

`publish` 上传公开源码；`submit` 提交固定版本的官方名单审核。审核合入后才会出现在正式应用商店。国际版本可使用 `--provider huggingface` 单独发布与审核。

测试在 SDK 仓库运行：

```text
python -m pytest tests/test_robot_intercom.py tests/application/test_rtc.py
node --test tests/js/test_robot_intercom.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_audio_latency.mjs tests/js/test_rtc_connection_recovery.mjs tests/js/test_rtc_noise_playback.mjs tests/js/test_rtc_noise_suppression.mjs
```

服务层测试覆盖能力门禁、实际播放嘴型启用顺序、启动失败与丢失确认后的释放、通话中拍照、旧页面隔离、设备切换保护、停止失败重试、拍照中的后端保活、页面暂停与退出、本机连接来源校验、诊断以及稳定端口。浏览器控制器测试覆盖麦克风权限晚到、启动确认晚到、本机连接确认晚到、释放重试、页面退出原因与静音轨道。自动测试不能替代实体听感与 LCD 视觉验收。
