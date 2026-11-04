# 初见工作室

开发交付请先阅读 [参考工程交付说明](HANDOFF.md)。

连接衔接短测：显示输入密码设计页，播放 wifi_connected 成功音效，随后用 1.2 秒渐隐并直接接入原有苏醒。不再插入思考或开心表情；短测不收音、不对话、不拍照、不写完成记录，可随时停止。

苏醒开场：`sleepy` 分镜使用当前 neutral 代码眼睛的四片眼皮，编排动作合计 8 秒（实际含通信和完成等待会更长），完成低头闭眼、轻微左右不同步的睁眼、回眯和抬头。闭眼时嘴巴隐藏，随睁眼渐显；回眯阶段保持微弱可见，结束恢复完整嘴巴和自动眨眼。全程无语音。需要配套 original_09 四眼皮及 mouth_opacity_milli 渐显固件；仅有 runtime.v3 能力声明的旧固件不保证该眼睛分支支持眼皮。完整初见和排练包含此段，已完成初见的日常启动跳过。现有 sleepy 分镜自动使用苏醒编排；其 pan 和台词保留，tilt、gesture、duration、hold 由苏醒节奏接管。

独立于 `first_meeting` 的初见设计 Application。通过 SDK Daemon 注入的设备通道控制 Watcher，使用 `expression.runtime.v3` 代码表情、温和转头动作、火山语音识别与合成，以及本机 Codex CLI Agent 对话。

在 SDK 环境执行 `watcherobot app run examples/first_meeting_studio`；网页地址写入忽略提交的 `artifacts/dashboard.url`。无需直接运行 app.py，也无需另开设备连接。

控制台提供六段可编辑分镜、正式初见、完整排练、打字对话、服务检测、音色试听和设备配对。保存音色后可在电脑试听。表情需设备声明 expression.presets.authored.v1 能力，不降级播放旧 GIF。分镜按表情→动作编排→语音→停留执行，嘴型由固件实际播放音频驱动；尚未实现音素嘴型与语音过程中连续动作。

分镜的小动作可选 none（单次移动）、peek（探头）、nod（点头）、settle（停顿回正）。围绕分镜的最终姿态做2–5度轻微变化，每段600ms以上，并在每段及停顿检查停止。旧配置未指定gesture时保持单次移动；新默认开场加入探头、点头、回正。设备跟随只在开场后开启，不与编排争抢运动控制。好奇与讲话共用neutral时保持渲染会话连续，避免反复释放与启动；情绪仍可主动重播。

对话表情使用 JoyInside 的七种代码情绪：neutral（静默）、smile（微笑）、happy（开心）、anger（生气）、concentration（专注）、sad（难过）、speechless（无语）。眼嘴联动增强来自 ESP32 的 origin/codex/joyinside-expression-render-v2，提交 1dd4be7b；仅移植表情渲染及嘴部参数，不引入其未通过稳定性验收的语音采集实验。静默规范见 ESP32 docs/expression-semantics/calm-default-standard.md。
sleepy/curious/listening/speaking 使用 neutral，warm 使用 smile。当前固件声明 expression.audio_follow.v2 时，应用通过 context.rtc 协商 WebRTC，将合成的 24 kHz PCM 以 20 ms 帧播放，设备从实际 RTC 扬声器 PCM 驱动嘴部；静音时闭嘴，七种情绪通过 audio_follow 的 emotion 参数选择。每句话结束确认释放 RTC，再开启原有麦克风收音，避免双重占用。音频与表情同步并非音素识别，接收缓冲可能造成少量延迟。旧配套固件仍支持 expression.chat.audio.v1 的主机 PCM 链路；两套能力均缺失时给出明确错误，不退回唱歌表情。
墨镜、处理思考以及 35 帧 photo 等独立作品继续沿用最新本地代码表情实现；music_voice/sing 仅供明确的唱歌场景使用，不作为普通讲话的替代。

对话拍照仅在明确同意后开始，暂停跟随后使用 camera.capture.authored_feedback.v1：设备先预热相机，再播放一次原版代码 photo，在实际渲染到 2400 ms 闪光帧后触发一次快门，结束后恢复表情及跟随。相机曝光和传感器延迟仍需真机验收，动画播放成功不代表照片保存成功。网页“拍摄一次 / 调试”是独立的明确拍照操作，不修改初见完成记录；成功后可预览及下载真实 JPEG。照片接口需控制台令牌鉴权，令牌不写入图片 URL。

姓名与明确拍照同意由确定状态机处理，Agent 无权授权拍照。拒绝拍照也能完成初见，拍照失败或中断不能完成。完成记录保存在本机 `artifacts/people.json`，按设备身份关联；该电脑上后续启动跳过开场直接对话。排练不写入完成记录。换电脑或删除 artifacts 后不保留记录，尚未将首见状态写入设备固件。

Codex 使用已安装 CLI 当前登录和服务配置，按轮调用 `codex exec` 并携带最近对话；没有另用 OpenAI API Key。调用有 90 秒超时，停止时结束调用进程。语音录制按停顿结束后送识别，属于轮流讲话，尚未支持边播边打断。

语音凭据只保存在忽略提交的 artifacts/settings.json，GET 配置不回显凭据。新安装需配置火山 APP ID、Token 与匹配资源；现有环境可迁移旧应用的 TTS/STT 设置。

验证：`python -m pytest examples/first_meeting_studio/tests -q`；`watcherobot app check examples/first_meeting_studio`。硬件需在 Desktop Link/Python SDK 页面配对并维持联网，控制台 Application 必须运行才会执行自动初见，尚未接管设备离线开机。

## 调试与验收

1. 默认地址固定为 `http://127.0.0.1:64649/`，Application 重启后保持不变。可用 `FIRST_MEETING_PORT` 指定端口（0 表示动态端口）；实际地址仍写入 `artifacts/dashboard.url`。端口占用时明确启动失败，不静默换地址。保持 Daemon 与 Application 进程运行；自动化启动可通过 SDK Daemon 管理接口启动，避免绑定交互式 Ctrl+C 停止流程。
2. 设备进入 Desktop Link，确认网页显示已连接。重启设备后需重新进入该页面；连接未恢复时使用新配对码。
3. 点击“完整排练”检查六段开场、转头、表情和语音；排练不改变正式初见完成记录。已有完成记录的设备点击正式启动会直接对话。
4. 等待 `listening` 后讲话或提交文字。开场、识别、思考和播放期间暂不接收新文字。可拒绝拍照继续聊天。
5. 点击停止，确认阶段为 `stopped`，再修改分镜、音色或执行服务检测。停止未确认时先恢复设备连接并再次停止，不能直接开始新流程。

网页状态和事件区可查看具体阶段、识别结果、异常位置与麦克风帧数；`artifacts/meeting.log` 为本机运行日志，配置及日志不得提交到仓库。云服务检测只验证服务连接，真机验收还需观察音频持续到达、播放结束及下一轮聆听。

当前真机回归覆盖完整开场、语音识别、Codex 回复、语音播放、三轮启动/停止及原版新增表情接口。表情测得约12–16 FPS，尚未达到20 FPS目标；摄像头实际曝光同步、预览与音频并发及长期稳定性仍需进一步验收。

拍照开始的显示交接要求 ESP32 声明 camera.capture.seamless_feedback.v1：应用保留当前代码表情，由固件完成相机预热后直接切换 photo；不再预先发送 expression stop。预热失败保留原显示面；相机、音频、其他显示作业占用仍执行准入检查。旧固件会提示升级。

audio_follow 占有画面时，普通特殊表情通过 update 切换，返回说话时提交 standby，保持同一画布。拍照需要固件能力 camera.capture.retained_follow.v1：无 RTC 播放、录音和相机占用时借用当前画布完成拍照，结束后返回同一套眼睛；不在拍照前后发送 start/stop 重建画布。停止整个应用仍完整释放资源。控制台 /api/status 提供固件版本、能力、最近错误及资源快照，可核对实际嘴型 PCM 帧、静音帧和释放结果。

## 扫码到初见的完整演示

网页启动正式初见或完整排练后，设备显示演示二维码；网页依次点击“模拟扫码成功”和“模拟输入完成 · 配网成功”。这些按钮只推进演示，不改写 Wi-Fi，不表示设备刚刚完成真实配网。真实设备连接另行检查。二维码为设计稿演示图形，不作为真实配网凭据。

点击模拟联网成功后，设备自动进入苏醒，不再显示呼吸邀请或等待触摸。重复或过期步骤请求不会触发下一段。苏醒保留慢睁眼、隐藏嘴巴渐显、低头到抬头及后续初见。中途停止后重新排练从扫码开始。

已完成初见的设备在 Application 自动启动时保持就绪，不自动开麦；主动点击日常启动后可对话。完整排练仍不修改首次完成记录。需要配套支持 expression.provisioning_screens.v1 的固件；仅收到 update 成功不能替代屏幕视觉验收。


### 三张配网设计稿（2026-10-20）

设备与网页预览采用 assets/provisioning 中 scan/network/failed 三份原始 SVG。网页模拟扫码后显示输入密码页；扫码页只进入输入密码页；只有输入密码页可模拟成功或失败，失败页仅返回输入密码页重试。每次页面就绪生成新 revision，旧页面请求和重复点击不能推进新阶段。停止后需要重新排练。

固件要求 expression.provisioning_screens.v1，缺少能力会提示更新，不降级显示旧页面。成功音效播放期间保留密码页，随后渐隐 1.2 秒并接入两次睁眼的苏醒；同一画布无需重新分配整屏内存。Home Wi-Fi 及设计稿文字是演示内容；网页不输入或保存 Wi-Fi 密码。

资源生成记录：resvg-py 0.5.0 渲染 422×463 SVG，以 (5,46,417,458) 裁切到 412×412，透明底合成黑色，PNG 保存在同目录。固件 watcher_provision_assets.h 按 RGB565、逐行顺序编码为 uint16 的 (count,color) RLE 对，单段不超过65535；头文件注释记录原 SVG SHA256。资源为编译期只读数据，约147KB，不增加整屏堆分配。当前生成工具为一次性开发操作，日后更新须重生成并重新核对三张预览与固件像素。


## 配套分发与个人语音配置（2026-10-26）

网页“声音与聆听”可填写自己的火山 TTS/STT App ID、Access Token、资源 ID、音色。保存后检测服务；密钥留空保留原值，清除按钮删除本机配置。当前仅支持火山，不接受阿里云密钥。关闭麦克风不会关闭 TTS。新安装默认关闭自动启动。

发布选择使用 .watcherignore，排除 artifacts 内的密钥、照片、配对与运行记录。不要直接压缩运行过的应用目录发给同事。实际发布包应使用 SDK 的文件选择器。

配套安装说明见 [delivery/README.md](delivery/README.md)。本地扩展 SDK wheel 与 ESP32 固件须成套使用。普通官方 SDK 0.1.9 和桌面锁定 Runtime 不保证支持四眼皮、渐隐配网页与无缝拍照；目前不能将 app check 通过等同于广场安装验收完成。


## 最新 SDK 迁移版（2026-10-26）

基于上游 main 24e3823，配套 SDK 为 0.1.12+firstmeeting.20261026，属于本地扩展构建，不是官方发布版本。应用与锁文件精确匹配此版本。保留上游音频累计确认、相机错误通知及麦克风生命周期；补齐苏醒透明度、配网页资源接口和旧固件音频节流。Windows / Python 3.11 独立安装及 app check 已验证。其他平台尚未认证，当前应用声明 Windows。

本次真机确认 v0.5.8 能力、苏醒短测、失败重试和照片预览；云端 TTS 返回 HTTP 403，完整语音验收仍需有效语音授权。桌面内置 Runtime 尚未升级到此配套版本，因此不能将本配套包直接视为应用广场安装验收通过。

火山语音新版 API Key：声音与聆听支持填写共用 API Key，优先使用 X-Api-Key 鉴权，无需 App ID。旧 App ID / Token 仍兼容。合成与识别资源需要分别获得授权，TTS 成功不代表 STT 可用。API Key 不在配置读取接口回显；清除凭据时一并删除。
