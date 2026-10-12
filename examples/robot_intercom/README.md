# 机器人对讲 / Robot Intercom

通过同一局域网内的 WatcheRobot 进行双向原声通话。电脑麦克风的声音由机器人扬声器播放，机器人麦克风的现场声音返回电脑；机器人默认程序表情的嘴型跟随**实际播放的音频**。通话中点击拍照，可以查看并保存 JPEG 照片。

这是从 SDK Test Bench 的组合场景抽离的独立 Application，具有自己的应用身份、界面与生命周期。发布包包含全部网页资源，运行时不依赖测试平台目录，也不请求外部 CDN。应用由 SDK Daemon 管理，控制与信令使用注入的 Device channel，不另建机器人业务 WebSocket，不修改 Daemon 路由。

## 使用

1. 在 WatcheRobot 桌面端将电脑和机器人连接到同一局域网，并完成机器人配对。
2. 安装并运行“机器人对讲”，本机会打开应用网页。
3. 点击“开始通话”，允许浏览器使用电脑麦克风。建议戴耳机避免现场声学回声。
4. 使用“麦克风开启 / 已静音”控制向机器人发送声音；“现场声音”控制本机收听。
5. 通话中点击“拍照”，收到照片后点击“保存照片”。不启动持续视频，不使用会占用显示和音频的拍照反馈效果。
6. 点击“结束通话”释放通话与嘴型。关闭页面同样请求释放；浏览器异常退出后，应用会在心跳过期时清理。

只支持局域网，不提供公网穿透、STUN/TURN、AI 回答、语音识别或云端录音。现场声音支持本机 RNNoise 降噪，可通过复选框关闭；处理器不可用时使用原始现场声音。第三方许可见 `web/RNNOISE-NOTICE.md` 与相邻许可证文件。

## 要求与照片存储

- SDK `>=0.1.11,<0.2`；当前分发清单仅声明 Windows。
- 固件需提供 `rtc.audio.full_duplex.v1`、`expression.audio_follow.v1` 和 `camera.capture`。缺少能力时禁止启动并提示升级。
- Chromium / Edge 等支持麦克风与 WebRTC 的浏览器。
- 网页仅监听 `127.0.0.1` 的动态端口。局域网媒体连接由浏览器 WebRTC 与机器人协商。
- Windows 照片位于 `%LOCALAPPDATA%/WatcheRobot/robot-intercom/photos`，按独立文件保存。照片不会进入发布源码，应用也不上传照片。
- 结束应用会停止本应用拥有的通话和音频跟随表情；不宣称恢复固件的随机 SD 表情及自动行为。
- 一次管理一台机器人。通话和资源释放期间不要切换设备；临时断线后，可在原配对会话恢复连接并重试释放。若重新配对产生了新会话，应用拒绝向新会话发送旧通话的释放指令，此时需确认原机器人已结束通话后重启应用。

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
node --test tests/js/test_robot_intercom.mjs
```

服务层测试覆盖能力门禁、实际播放嘴型启用顺序、启动失败与丢失确认后的释放、通话中拍照、旧页面隔离、设备切换保护、停止失败重试、拍照中的心跳及浏览器异常退出。浏览器控制器测试覆盖麦克风权限晚到、启动确认晚到、释放重试与静音轨道。自动测试不能替代实体听感与 LCD 视觉验收。
