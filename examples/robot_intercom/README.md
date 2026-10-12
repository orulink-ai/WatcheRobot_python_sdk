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

## 0.1.4 本地候选说明 / Local candidate notes

应用清单当前为 0.1.4，退出诊断区分意外 presence 失联与显式页面退出。本阶段诊断与生命周期验证完成、源码待审，未宣称已合入；不能据版本号推断商店已合入或可下载。用户报告过声音重复并逐渐放大；该精确现象目前尚未在增益 12 候选上复现，啸叫专项验收未完成。本阶段不发布新商店版本，保留 0.1.3 的目录请求 #19，当前为 open。

在 v0.5.6 / `214b4f9145dd` 加待合入 RTC 麦克风增益补丁的设备上，正式页面共完成 10 轮通话与成功释放，其中第 10 轮取得 598 个 running 样本，首末跨度 649.672 秒，全部为 RTC connected、presence 在线。该长测发送 / 接收音频包增量为 32,475 / 32,442，TX、render、上行丢帧和削顶计数为 0，播放队列丢帧为 7；停止请求至应用记录释放为 578 ms。串口分别确认通话增益由 27 限至 12，并在结束后恢复至 27，结果均为 0。详见 `docs/robot-intercom-acceptance.zh-CN.md`。

这些结果支持当前设备的持续连接与资源释放，不证明声学闭环、真人双讲或主观听感通过。另完成的两轮真实麦克风探针虽取得有效采样，但事后核对本机实际默认输出是蓝牙耳机，未覆盖电脑喇叭放在机器人旁的声学路径，因此不用于啸叫专项验收。按用户要求，本阶段先收尾；下一阶段确认喇叭与采集端点后，再检查停止单次音源后的衰减及双讲。降低增益不能单独作为啸叫已解决的结论。

The manifest is currently 0.1.4. Exit diagnostics distinguish unexpected presence loss from explicit page-exit requests. This phase's diagnostic and lifecycle validation is complete, with source review pending; no merge is claimed. The version does not imply marketplace approval or availability. The user reported repeating, growing sound; that precise symptom has not yet been reproduced on the gain-12 candidate. Howling-specific acceptance is incomplete. No new marketplace version is published in this phase; the existing 0.1.3 catalog request #19 remains open.

On the v0.5.6 / `214b4f9145dd` baseline with a pending RTC microphone-gain patch, the formal UI completed 10 calls with successful release. Call 10 produced 598 running samples spanning 649.672 seconds, all RTC connected with presence online. Observed TX / RX growth was 32,475 / 32,442; TX errors, render errors, uplink drops, and clipping were zero, with 7 playback-queue drops. Application stop-to-release time was 578 ms. Serial evidence confirmed microphone gain changed from 27 to 12 during calls and returned to 27 after release, with successful results.

This supports sustained connectivity and release on the tested device, not acoustic-loop stability, double-talk, or subjective quality. Two additional real-microphone probe runs captured valid samples, but later endpoint inspection found that the actual default output was a Bluetooth headset. They did not cover the computer-speaker-near-robot acoustic path and are not howling acceptance. At the user's request, this phase is closing; the next phase must confirm speaker and capture endpoints before testing decay and double-talk. Lower gain alone is not acceptance of the reported howling fix.

## 0.1.3 历史版本说明 / Historical version notes

以下保留 0.1.3 的版本记录与后续分阶段调查；源码版本说明不等同于商店已合入。

The following preserves the 0.1.3 version record and staged investigation. A source-version record is not confirmation of marketplace approval.

新增本应用内的机器人连接入口、中英文切换和 ElevenLabs 声音视觉组件。演示聚焦全双工原声 RTC，默认关闭拍照，不要求相机能力。连接、静音、收听、结束通话及错误反馈支持中英文。

**已知问题：** 当前 v0.5.5 PTL 固件在本次实机测试中多次于连接约 10 秒后出现 ICE 断开。串口同时记录内部内存紧张与 PeerConnection 调度迟滞，原因仍待进一步确认；本版本未修复该固件相关的持续通话问题，不宣称长期稳定性验收通过。掉线后应用会尝试释放通话，可重新开始；若释放未确认，请使用“重试结束通话”。

Adds in-app robot pairing, Chinese / English switching, and ElevenLabs audio visuals. This demo focuses on full-duplex voice RTC; photos are disabled by default and camera capability is optional.

**Known issue:** In our current v0.5.5 PTL hardware tests, ICE disconnected repeatedly about 10 seconds after connection. Low internal memory and delayed PeerConnection scheduling were observed; the cause is still under investigation. This release does not fix that firmware-related call stability issue. After disconnection, the app attempts to release the call. If release is unconfirmed, use “Retry ending call”.

### 2026-10-12 第一阶段调查 / Initial investigation phase

上方保留 0.1.3 发布时的问题记录。后续对照发现，旧固件 `8fcd310d8802` / v0.5.5 通话期间 DMA 可用内存降到 26 B，HMAC 返回 `-141`（`PSA_ERROR_INSUFFICIENT_MEMORY`）。这确认了一条内存不足导致 RTC 校验失败的路径，但不能解释全部历史退出或回声问题。

第一阶段改用固件 `214b4f91` / v0.5.6 作为测量基线。该版本已有加密 GDMA reset 和录音缓冲迁移至 PSRAM 的改动；保留 Wi-Fi 静态 TX24 配置时，通话中 DMA 可用内存约 3.3 KiB。该阶段两轮各 60 秒的实机 AEC 诊断窗口中，TX、render 和 queue 错误计数均为 0，当时长时间通话与恢复验证尚未完成。后续 10 轮通话与长测结果见上方 0.1.4 候选记录；这些计数不等于主观回声消除效果通过，也不构成全部稳定性验收。运行仍按下方已有能力要求校验，不新增固件版本拒绝规则。

退出诊断现在区分本机连接失联 `presence_lost` 与显式页面退出请求的 `page_closed`。旧日志中的 `page_closed` 曾用于所有 presence 断开，单独出现该字段不能证明真实发生 `pagehide`；诊断语义修正本身不会解决音频或网络故障。

The notes above preserve the issue observed when 0.1.3 was released. Subsequent hardware comparison found that firmware `8fcd310d8802` / v0.5.5 had only 26 B of available DMA memory during a call, and HMAC returned `-141` (`PSA_ERROR_INSUFFICIENT_MEMORY`). This confirms one path from insufficient memory to RTC integrity-check failure; it does not explain every historical exit or echo issue.

The initial measurement phase used firmware `214b4f91` / v0.5.6. It already includes crypto GDMA reset and recording-buffer changes that use PSRAM. With the existing static Wi-Fi TX24 configuration, available DMA memory during a call was approximately 3.3 KiB. TX, render, and queue error counts were all zero in two 60-second hardware AEC diagnostic windows; long-call and recovery testing had not yet been completed at that stage. See the 0.1.4 candidate notes above for subsequent 10-call and long-call evidence. These counters do not establish subjective echo-cancellation quality or complete stability. Existing capability checks still apply, without an added firmware-version rejection rule.

Exit diagnostics now distinguish local connection loss (`presence_lost`) from an explicit page-exit request (`page_closed`). Older logs used `page_closed` for every presence disconnection, so that field alone does not prove a real `pagehide` occurred. This diagnostic correction does not itself fix audio or network failures.

#### RTC 麦克风增益候选补丁 / Candidate RTC microphone-gain patch

后续候选实机使用 **v0.5.6 / `214b4f9145dd` 基线加待合入的 RTC 麦克风增益补丁**，设备报告 `dirty=true`；正式 v0.5.6 基线不包含该候选补丁。候选完成三轮各 60 秒数字 WAV 测量，结束采样均为 running / connected，随后状态为 idle / stopped。三轮 TX、render、上行丢帧及削顶计数均为 0，播放队列丢帧分别为 0 / 1 / 2；参考音频 starved 为 24 / 31 / 23，discard 为 24,576 / 31,744 / 23,040 B，仍有参考连续性缺口。

按完整名义 WAV 内容窗口累加能量并除以 samples，候选返回 PCM 的 RMS 为 0.001022 / 0.001100 / 0.001066，低于无补丁基线三轮的 0.010573 / 0.007978 / 0.006666。基线旧测量缺少显式窗口起点及末尾 flush，分析排除包含测量前音频的首窗，其余窗口按相邻时间端点条件重建；数字音源和返回音频的时钟与延迟未实测。这是短窗客观能量比较，processed input/output 比值不是独立 ERLE，不能替代真人双讲、主观听感、长期稳定性或恢复验收。

近端单独测量中，候选第二轮完整 WAV 内容的 7 个窗口 RMS 为 0.004086，播放前为 0.001160；与原基线同口径内容相比为 −14.33 dB。全程和内容窗口的 WAV 能量包络相关系数分别为 0.88793 / 0.92655，支持近端声音仍被传送，同时显示较低的拾音幅度。候选第一轮播放前背景较高，不能据其全程相关性较低判断近端静音。该测量阶段尚未核对增益恢复，后续恢复证据见上方 0.1.4 记录；真人音量与双讲体验仍需实机确认。本次记录没有更新应用商店分发状态。

Subsequent candidate hardware uses **the v0.5.6 / `214b4f9145dd` baseline plus a pending RTC microphone-gain patch**, reported as `dirty=true`; the released baseline does not include this patch. Three 60-second digital-WAV runs completed while running / connected, with subsequent idle / stopped status. TX errors, render errors, uplink dropped frames, and clipping were zero; playback-queue drops were 0 / 1 / 2. Reference starvation was 24 / 31 / 23 chunks and discarded reference bytes were 24,576 / 31,744 / 23,040, so reference-continuity gaps remain.

Sample-normalized return RMS in whole nominal WAV-content windows was 0.001022 / 0.001100 / 0.001066, compared with 0.010573 / 0.007978 / 0.006666 in three unpatched baseline runs. Those earlier records lacked explicit window starts and a final flush: their first windows containing pre-measurement audio were excluded, and remaining starts were conditionally reconstructed from adjacent endpoints. Source/return clock alignment and latency were not measured. These short-window energy observations and processed input/output ratios are not isolated ERLE, subjective quality, double-talk, long-call, or recovery acceptance.

In the second candidate near-end-only run, seven whole WAV-content windows had RMS 0.004086, versus 0.001160 before playback and −14.33 dB relative to the matching original baseline. WAV energy-envelope correlations were 0.88793 for the full record and 0.92655 for content windows. This supports retained near-end transmission with lower pickup amplitude. The first candidate near-end run had a louder pre-playback background, so its lower full-record correlation does not establish silence. Gain restoration had not been checked at that measurement stage; see the 0.1.4 notes above for subsequent restoration evidence. Human speech levels and double-talk still require hardware confirmation. This record does not update marketplace distribution status.

只支持局域网，不提供公网穿透、STUN/TURN、AI 回答、语音识别或云端录音。现场声音支持本机 RNNoise 降噪，可通过复选框关闭；处理器不可用时使用原始现场声音。**“现场降噪”处理机器人返回电脑的收听音频，不是 AEC 开关**：它不改变电脑麦克风请求的浏览器回声消除，也不控制机器人固件的 AEC。第三方许可见 `web/RNNOISE-NOTICE.md` 与相邻许可证文件。

This app supports LAN calls only, without public-network traversal, STUN/TURN, AI replies, speech recognition, or cloud recording. Local RNNoise noise reduction can be disabled with the checkbox; if unavailable, playback uses the original robot audio. **“Noise reduction” processes the robot audio heard on the computer; it is not an AEC switch.** It does not change the browser echo-cancellation request for the computer microphone or control firmware AEC. See `web/RNNOISE-NOTICE.md` and the adjacent license files.

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

本机 WebSocket 异常关闭记录 `presence_lost`，并保留关闭 code、是否正常关闭、是否出现 error，以及有限的协议关闭原因。后端还记录 handler 退出类别、应用 shutdown 状态和心跳年龄；未知关闭文本被脱敏。显式退出请求仍使用 `page_closed`，释放重试保留首次退出原因。关闭证据可能因诊断与停止请求的先后顺序而只出现在一侧，需要结合设备终止原因和媒体进度判断故障。

真实 `pagehide` 会先关闭 presence，再发页面退出 beacon；若关闭先被后端观察，首次原因也可能是 `presence_lost`。释放完成后到达的 beacon 不保证新增停止记录，因此任何一个原因值都不能单独证明用户关闭页面或网络失联，需结合真实页面操作、关闭事件与设备停止时间。

An unexpected local WebSocket closure records `presence_lost`, with the close code, clean-close flag, error-event flag, and a limited protocol reason. The backend also records the handler-exit category, application shutdown state, and heartbeat age; unknown close text is redacted. Explicit page-exit requests still use `page_closed`, and release retries preserve the first exit reason. Diagnostic and stop requests may race, so closure evidence can appear on only one side. Interpret it alongside device termination reasons and media progress.

A real `pagehide` closes presence before sending its page-exit beacon. If the backend observes the closure first, the initial reason can also be `presence_lost`. A beacon arriving after release may add no stop record. Neither reason alone establishes user page closure or network loss; correlate actual page actions, close events, and device-stop timing.

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
