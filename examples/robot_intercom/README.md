# 机器人对讲 / Robot Intercom

通过同一局域网内的 WatcheRobot 进行双向原声通话。电脑麦克风的声音由机器人扬声器播放，机器人麦克风的现场声音返回电脑；机器人默认程序表情的嘴型跟随**实际播放的音频**。演示聚焦连接、全双工原声通话、静音和收听，默认关闭拍照功能，不要求机器人具备相机能力。

这是从 SDK Test Bench 的组合场景抽离的独立 Application，具有自己的应用身份、界面与生命周期。发布包包含全部网页资源，运行时不依赖测试平台目录，也不请求外部 CDN。应用由 SDK Daemon 管理，控制与信令使用注入的 Device channel，不另建机器人业务 WebSocket，不修改 Daemon 路由。

## 使用

1. 将电脑和机器人连接到同一局域网。
2. 安装并运行“机器人对讲”，本机会打开应用网页。
3. 点击页面顶部“连接机器人”。在机器人上打开“Python SDK”，输入其屏幕上的六位连接码；找不到时可展开填写机器人 IP。配对由桌面 Daemon 管理，页面显示查找、连接或失败状态。已在桌面配对的机器人会自动显示，无需重复连接。实际在线后点击“开始通话”，允许浏览器使用电脑麦克风。建议戴耳机避免现场声学回声。
4. 使用“麦克风开启 / 已静音”控制向机器人发送声音；“现场声音”控制本机收听。
5. 点击“结束通话”释放通话与嘴型。关闭页面同样请求释放；浏览器异常退出后，本机连接关闭或失去响应也会触发清理。

页面右上角可切换中文 / English。首次按浏览器首选支持语言选择；切换后在本机记住偏好。切换语言不重连机器人、不结束通话，连接弹窗、按钮、通话状态和错误提示同步切换。

## 0.1.3 发布说明 / Release notes

新增本应用内的机器人连接入口、中英文切换和 ElevenLabs 声音视觉组件。演示聚焦全双工原声 RTC，默认关闭拍照，不要求相机能力。连接、静音、收听、结束通话及错误反馈支持中英文。

**已知问题：** 当前 v0.5.5 PTL 固件在本次实机测试中多次于连接约 10 秒后出现 ICE 断开。串口同时记录内部内存紧张与 PeerConnection 调度迟滞，原因仍待进一步确认；本版本未修复该固件相关的持续通话问题，不宣称长期稳定性验收通过。掉线后应用会尝试释放通话，可重新开始；若释放未确认，请使用“重试结束通话”。

Adds in-app robot pairing, Chinese / English switching, and ElevenLabs audio visuals. This demo focuses on full-duplex voice RTC; photos are disabled by default and camera capability is optional.

**Known issue:** In our current v0.5.5 PTL hardware tests, ICE disconnected repeatedly about 10 seconds after connection. Low internal memory and delayed PeerConnection scheduling were observed; the cause is still under investigation. This release does not fix that firmware-related call stability issue. After disconnection, the app attempts to release the call. If release is unconfirmed, use “Retry ending call”.

只支持局域网，不提供公网穿透、STUN/TURN、AI 回答、语音识别或云端录音。现场声音支持本机 RNNoise 降噪，可通过复选框关闭；处理器不可用时使用原始现场声音。第三方许可见 `web/RNNOISE-NOTICE.md` 与相邻许可证文件。

## 运行要求

- SDK `>=0.1.11,<0.2`；当前分发清单仅声明 Windows。
- 固件需提供 `rtc.audio.full_duplex.v1` 和 `expression.audio_follow.v1`。缺少能力时禁止启动并提示升级。
- 可选拍照启用时，设备的 ESP32 相机后端必须与 Himax 固件配套。PTL 设备使用工作区 `yarn esp32:ptl:full -Port <ESP32 串口>` 构建并配套烧录；相同版本号不保证相机后端一致。能力声明不等于 RTC 高负载下相机初始化已通过实机验收。
- 可选拍照启用时，拍照失败保持通话，区分相机失败、照片回传失败和获取超时。新版 SDK 收到设备拍照失败事件会立即返回；旧 SDK 仍可能等到 JPEG 超时，但应用会显示照片错误。
- Chromium / Edge 等支持麦克风与 WebRTC 的浏览器。
- 网页仅监听 `127.0.0.1`。首次分配端口后会在本机保存并在重启时复用，使浏览器能够复用麦克风权限；首选端口占用时，本次使用临时端口。局域网媒体连接由浏览器 WebRTC 与机器人协商。
- 可选拍照启用时，Windows 照片位于 `%LOCALAPPDATA%/WatcheRobot/robot-intercom/photos`，按独立文件保存。照片不会进入发布源码，应用也不上传照片。
- 照片不自动过期；需要清理时，结束应用后可在上述目录删除不再需要的 JPEG 文件。
- 结束应用会停止本应用拥有的通话和音频跟随表情；不宣称恢复固件的随机 SD 表情及自动行为。
- 一次管理一台机器人。通话和资源释放期间不要切换设备；临时断线后，可在原配对会话恢复连接并重试释放。应用在发送前检查配对身份，发现新配对或身份缺失时拒绝继续控制，此时需确认原机器人已结束通话后重启应用。SDK 当前通道没有配对会话的原子发送合同，检查与发送之间的并发切换无法完全隔离，因此通话中切换配对属于不支持的操作。
- 请求最多等待 20 秒，整个连接阶段最多等待 30 秒。停止未获确认时保留原通话标识，页面可重试释放；取消后迟到的启动不会恢复本机音频。服务保留最近 256 个已取消标识，阻止迟到启动再次分配资源。
- 设备分配成功后立即发送心跳，服务端将协商阶段的 30 秒期限与连接后的 10 秒无心跳清理分开；协商中的慢信令不会被当作页面退出，协商超时仍会释放资源。
- 协商截止后，新请求会被拒绝并进入资源待清理状态。截止前已进入 SDK 的同步调用不能由浏览器取消；完成后再检查期限并请求清理，因此 30 秒是取消/清理的触发期限，不是机器人资源已确认释放的硬实时保证。释放未确认时页面继续保留重试入口。
- 协商或通话期间收到设备失败、停止事件，后端会停止保活并立即尝试释放，不依赖网页继续轮询。释放失败后的重试保留首次退出原因，各次清理原因记录在诊断历史中。
- 麦克风静音暂停音轨输出，采集权限会在结束通话时释放。可选拍照启用时自动保存到应用照片目录，“保存照片”用于额外下载副本。

## 可选拍照能力（默认关闭）

标准应用入口使用 `create_web_app(service, web_root=...)`，不注册 `/api/photo` 和 `/photos/{name}` 路由，页面隐藏拍照区并使用单列通话布局。`/api/status` 的 `features.photo` 为 `false`。SDK 相机 API 与独立拍照控制器保留，开发时可显式传入 `enable_photo=True`；这一选项不在演示界面提供，启用前需确认固件支持 `camera.capture`。以下等待反馈仅适用于显式启用的开发场景。

点击后立即向机器人请求拍照，不额外等待倒计时结束才触发快门。当前 PTL 相机切换到拍照模式后需要预热曝光、等待有效帧并丢弃旧帧；实机测得预热约 1.2 秒，从点击到照片显示约 1.8～2.0 秒。预热用于避免第一张照片发黑，没有为缩短等待而取消。

界面默认显示“预计还需 2 秒”，并根据当前页面此前成功拍照的耗时调整下一次估计。倒计时是等待估计，不是精确快门时刻。超过估计仍未收到照片时，会显示已经等待的秒数；收到后显示“正在显示照片”，图片解码成功才更新预览与保存链接。拍照中禁止重复点击，通话保活继续。超过 15 秒提示重试，失败时保留上一张照片；关闭页面会取消本机请求和计时器。

结束通话、通话失败或换成新通话时，同样停止本机拍照等待并隔离旧结果。中止本机 HTTP 等待不代表设备快门已经取消；后端仍会完成在途拍照并收尾，然后释放通话资源。

## 通话保活与诊断

Application 后端每秒通过注入的 RTC channel 发送设备保活，拍照不会阻塞这条链路。页面通过本机 WebSocket 声明通话仍然存在，原生 ping/pong 不依赖页面 JavaScript 定时器。因此页面主线程短暂停顿不会使固件的 5 秒保活期限过期。页面关闭或浏览器崩溃会关闭本机连接并释放通话；连接失去响应时，由 5 秒 ping 周期和 10 秒 pong 超时发现。兼容没有本机连接的旧客户端时，仍执行 10 秒页面租约，设备保活不会延长它。

`/api/status` 的 `diagnostics` 返回最近 128 条连接状态、页面可见性、音频包进度与停止原因。相同事件以 `INTERCOM` 前缀写入 Daemon 管理的 Application 日志，便于区分设备 `heartbeat_timeout`、网络连接失败、页面退出及用户主动结束。诊断不保存 SDP、音频或照片内容。重新开始通话会重置本机诊断窗口，历史 Application 日志仍由 Daemon 管理。

## 开发与发布

### 声音界面

界面采用居中的单列通话布局，顶部保留机器人连接状态与配对入口。开发时显式启用拍照，才显示侧边照片区；窄屏自动排为单列。通话连接后显示时长，恢复网络时保持同一次计时；麦克风静音与现场收听分别控制，波形反映对应的真实音频流。可选拍照启用时显示预计等待时间，释放失败时保留“重试结束通话”。

采用 ElevenLabs UI 的 Orb 与 BarVisualizer，来源、适配与许可见 `web/VISUALS-NOTICE.md`。静止状态和系统要求减少动态效果时显示静态球体；活动通话使用真实麦克风音量，视觉层复用 RTC 已取得的媒体流。视觉组件不可用时保留静态反馈，通话控制继续可用。

发布源码包含构建后的本地 `web/visuals.js` 与许可证，运行不需要 Node.js 或 CDN。仅修改 HTML/CSS 无需构建；修改视觉组件后，在 Windows、macOS、Linux 使用相同命令：

```text
cd examples/robot_intercom/frontend
npm ci
npm run typecheck
npm run build
```

将更新后的 `web/visuals.js`、`web/visuals.js.LEGAL.txt`、`web/VISUALS-LICENSES.txt` 与源码同时提交。`frontend/` 的构建代码、依赖和工具不会进入应用商店发布包，完整开发源码仍在 SDK 仓库。构建会核对实际被打包的依赖许可证，缺失时失败，不联网补取许可。

### 应用运行与商店提交

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
node --test tests/js/test_intercom*.mjs tests/js/test_robot_intercom*.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_audio_latency.mjs tests/js/test_rtc_connection_recovery.mjs tests/js/test_rtc_noise_playback.mjs tests/js/test_rtc_noise_suppression.mjs
```

服务层测试覆盖能力门禁、实际播放嘴型启用顺序、启动失败与丢失确认后的释放、通话中拍照、旧页面隔离、设备切换保护、停止失败重试、拍照中的后端保活、页面暂停与退出、本机连接来源校验、诊断、稳定端口、协商截止门禁与取消后迟到启动。浏览器控制器测试覆盖麦克风权限晚到、启动确认晚到、本机连接确认晚到、释放重试、页面退出原因、静音轨道、请求悬挂及按 codec 时钟转换隐藏样本数。自动测试不能替代实体听感与 LCD 视觉验收。

拍照控制器独立测试覆盖立即请求、防重复点击、超出预计时间、等待图片解码、请求和解码失败、超时中止、页面退出、过期结果隔离及下一次耗时估计。
