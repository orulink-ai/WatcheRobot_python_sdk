# Codex 机器人物理实体 Application（实验版）

默认入口现为 ContinuousVoiceService：机器人原生RTC与Codex持续WebRTC语音前台桥接，对话前台与单并发后台工具分离。assistant-ui显示真实转写、设备状态、工具进度及实拍证据，不使用假图、模拟回答或虚构波形。旧VoiceService任务式PCM路径仅保留为legacy回归，不能作为当前架构说明或新版本验收结论。

## 最新音量修复（2026-10-08，用户复测仍小之后）

前一候选仅证实PCM电平提高，用户听感仍小，不能视为问题已解决。新默认
`maximum-speech-v2`将目标RMS提高到12000、最大增益16倍、上升平滑80 ms；
对高幅样本做软拐点压缩，保留29000编码前峰值保护，不突破BSP硬件防失真上限。
回归复现旧算法单个峰值使同帧正常语音由1000降至885，新算法不再整体压低语音。

已通过managed Application重启并确认运行进程报告新版profile；设备逻辑音量
100再次重应用，无codec应用错误日志（不是寄存器回读）。USB麦克风独立实测
机器人测试语音：高能量四分位RMS由0.016992升至0.0690285，约+12.18 dB。
两次模型合成波形不同，麦克风可能有自动处理，不能把此值当严格同源A/B或声压提升。
新版speechOutputRms=9310.2，render_error=0，测试后stop confirmed且microphone=false。
用户随后确认“变大了，但是不够清晰”：响度改善已获主观确认，清晰度仍未通过，
不能宣称音量／音质整体修复完成。现有强增益／软压缩和48→24→48 kHz重复重采样
是待进一步隔离的音色变化候选；JoyInside同样16 kHz播放，不能仅归因格式／采样率。
Python回归255通过／1跳过，app check通过。

## 前一候选修复与实机验证（2026-10-08，未通过最终音量听感）

- 已修复设备采样时钟漂移导致的误过期丢帧；仅编码队列真正排空时重锚，仍保留连续积压的120 ms保护。`pipeline_age`现在表示连续非空批次的相对积压，不是端到端延迟。
- 音频转发不再等待后台结果交接；Codex发送FIFO只保留新鲜音频。两轮测试中FIFO停留最大值由235 ms降至110 ms，但不是整体ASR缩短125 ms。最终170.2秒测试累计丢弃960 ms旧音频、恢复40次；这是有损实时性恢复，不是无丢帧保证。
- 系统逻辑音量已查询并重应用100；BSP仍封顶codec参数95，未做寄存器回读。新增播放源响度补偿与浮点重采样限峰，同源PCM RMS从1828.9升至3959.7（约+6.7 dB），编码前峰值29000、削波样本0；未测声压，不代表物理响度翻倍或解码后零失真。
- 两轮真实声学输入分别持续430.1秒、170.2秒，均由Insta360 Wave USB扬声器播放固定编号，经机器人麦克风收音，每轮5/5识别。最终版本还独立核对了全部五个编号的assistant回复；没有用文字或PCM注入替代输入。最终固件capture=8542、tx=8542、stale_drop=0、tx_error=0、render_error=0；主机事件停留最大16 ms。
- 两轮均停止confirmed，最终麦克风关闭。示例Python250通过／1跳过；ApplicationRtc与Daemon路由32通过；固件host目标3/3通过；音量静态及共享串口测试7通过；managed app check通过。
- 云端转写仍有秒级等待，首段样本约2.2–2.3秒；未做电脑直接语音同句同网络对照，不能说已达到电脑体验。未发送未经当前Codex协议确认的通用Realtime VAD参数；真实双讲、AEC效果、物理可听度与native视觉闭环仍需另验。

诊断字段、增益和有损恢复边界见[AUDIO_DIAGNOSTICS.md](AUDIO_DIAGNOSTICS.md)，分轮证据见[ACCEPTANCE.md](ACCEPTANCE.md)。

## 修复前历史证据与未验收项（2026-10-08）

以下现场数据由用户／主代理提供，本次文档更新未操作设备或独立重跑实机验收：

- native现场实例49308已连接；真人通过机器人麦克风说话后出现转写，用户听见Codex扬声器回答，但报告音量偏小。当前公开音量控制不可用，不能暗示应用已经调大音量。
- 较早同一快照中的主机已消费micFrames与DeviceRtcPeer的receivedFrames均为8308，只说明这两个应用计数点一致，不代表全网络、云端接收或识别完成。
- 测试设备只读日志约UTC 2026-10-07 16:03报告AEC active、reference drops=0；pipeline_age为45553/113885us，mic_read为22928/51778us，aec为8402/46532us，opus为6460/58192us。各对数值为设备内部最近值/最大值，不是端到端、云端延迟或分位数。
- 该设备日志capture=13059、tx=13059、err=0、stale_drop=0；rx=13058、decoded=13057、i2s=8353920B、render_err=0、queue=0ms。这些是该时点设备侧媒体计数，不能据此追认整轮网络无丢包或完整可听输出。
- 用户感觉上行ASR文字慢、回复快，尚无分段时间样本；声学全双工尚未验收，AEC效果及延迟均未通过验收。启用AEC和零参考丢弃不是回声消除效果证明。
- 本轮相机缺少权限，未建立native拍照、同轮看图、对应语音回答的完整闭环。legacy的B79／88CE及此前视觉证据仍保留在[验收记录](ACCEPTANCE.md)，不能替代native验收。

## 架构与边界

~~~text
机器人麦克风/喇叭 <-> 原生WebRTC媒体 <-> DeviceRtcPeer
                                               <-> Codex持续RTC语音前台
                                                         -> delegation
                                                         -> 单并发后台Codex任务agent
                                                         -> 受限RobotTools
机器人JPEG -> 本轮turn/steer图像输入 -> 后台真实视觉结果 -> 持续RTC语音前台
SDK注入ApplicationRtc -> Application Device channel -> Daemon -> 设备RTC控制/信令
RobotTools -> SDK注入robot -> Application Device channel -> Daemon -> 设备
浏览器 assistant-ui <-> 本机 Application（对话、证据、权限、独立停止）
Desktop 快捷键 -> Daemon -> 当前 Application -> Daemon -> Device
~~~

- 默认app.py独立选择ContinuousVoiceService，设备RTC控制由ApplicationContext.from_environment().rtc提供的ApplicationRtc管理；工具由context.robot访问。控制与信令仍走注入的Device channel及Daemon，原生WebRTC媒体由协商的peer链路承载，不建立旁路设备业务控制连接，不改变Daemon内容无关路由。
- 设备须协商rtc.audio.full_duplex.v1，未协商时明确失败，不自动回退互斥PCM路径。DeviceRtcPeer协商Opus 48 kHz媒体；主机桥接内部使用16 kHz上行、24 kHz下行单声道PCM，不等于设备在使用旧SDK PCM播放job。
- 持续RTC前台负责对话与委派，后台只有一个任务agent，只有显式delegation才申请后台任务。转写本身不自动执行机器人命令；同一delegation身份拒绝重复执行。后台忙时追加动作会明确未执行，但对话前台仍可继续收发；不使用关键词直接映射SDK命令。
- 任务线程只开放 observe_scene、aim_camera、set_lights；关闭 shell、电脑执行环境、MCP、插件、网页搜索及多 agent。其他服务端执行请求一律拒绝。
- 图像使用同轮turn/steer附入并携带expectedTurnId；写锁内复查照片存活、上传权限及取消代次，不重放旧图片。legacy曾实测动态工具图像被确认但像素不可见，改用直接多模态追加后通过；这解释当前策略来源，不代表本轮缺权限的native视觉闭环已通过。
- 默认native路径不按回答旋转RTC，不通过旧SDK PCM播放job批量播报。任务完成仅说明后台结果已交给持续语音前台；speaking、输出字节、转写done或短暂无新音频均不是物理整段播放完成回执。
- native路径在后台思考、转头、拍照或输出时不主动关闭RTC捕获；资源并行与声学效果仍须实机验收。snapshot报告voiceMode=realtime，duplex的transport/deviceTransport均为webrtc、microphoneConcurrent=true、echoCancellation=unverified；此配置不等于声学全双工验收通过。喊停或语音打断可靠性未验收，请使用顶部独立停止按钮。
- 静音仅为上行逻辑静音：新的用户音频不送给Codex，上行媒体维持零输入；设备捕获与AEC可继续运行，下行也不会因此静音。它不是硬件麦克风关闭或媒体会话结束。
- expressionsEnabled=false、bodyFeedback=suppressed表示应用不再自动触发thinking/speaking表情，以黑色静态画面接管显示、抑制默认动画。公开SDK当前不能保证全部renderer停止或CPU释放；结束语音不会主动恢复表情，退出Application后的显示行为仍需核对。
- 页面监听随机 loopback 端口，WebSocket 同源校验，限制状态/照片跨站读取。最后一个页面断开、设备掉线或上游致命错误时安全结束会话。
- 不修改用户全局 Codex 配置、凭证或系统默认音频设备。

## 能力与安全范围

| 能力 | 首版范围 |
| --- | --- |
| 当前观察 | 不转头，拍摄一张640×480 JPEG，由模型理解 |
| 两侧观察 | 中心与固件配置的两端分别拍照，最后回中心；每轮最多3张，不保证无盲区 |
| 身体转头 | aim_camera只转头，不拍照、不上传、不自动回正；position或pan_deg二选一 |
| 运动 | 横向固件配置30–150°，中心90°，俯仰保持120°；Codex选择natural或gentle节奏；无底盘行走或360°巡视 |
| 灯光 | #RRGGBB 常亮，亮度0–0.25，10秒自动关闭；无闪烁 |

相机、图像分析、运动、灯光权限默认关闭，只对本次会话有效，结束时撤销。图像分析会把照片发送给当前 Codex 模型服务；清除本地照片不能撤回已发送内容。
纯转头只需要运动权限，不因转头自动拍照。Codex可选择30–150°范围内的整数目标，或pan_min/center/pan_max三个语义位置；精确左右对应尚未标定，不能将两端标签当作已验证的左右。
运动边界独立在motion_policy.py：当前配置来自STM32源码，横向servo2为30–150°、俯仰servo1为100–130°，不是实时限位查询，也不是编码器测量。固件仍负责最后限位；不能把协议0–180°当成机械行程。其他硬件/重新校准需核对并替换匹配配置。
natural的速度预算为60°/秒，gentle为20°/秒；因为SDK没有公开当前位置反馈，时长按可能从任一端出发的最坏距离与2倍缓动余量计算，不把上一次命令目标当作当前位置。默认配置natural回中心2秒、端点4秒，gentle分别6秒、12秒。这些是控制预算与命令时长，不是测得的真实速度，也不是人体转头标准；必须实机验证舒适度及回执。
当前固件watcher_motion_move_to未使用SDK传入的profile，实际路径为linear，不能声称已经实现ease-in-out或拟人生物运动。停止的SDK返回也不是STM32停稳回执，中途停止后再次运动的轨迹起点与停稳确认仍需固件侧验证；Application不通过旁路补写这类业务命令。
应用侧原图保存在本地内存，最多6张、15分钟过期，有任务、视角、时间、字节数和 SHA-256。历史照片不代表新任务成功。

停止不等待模型回复或生命周期锁：native关闭设备RTC peer／会话并取消工具，持续drain在途SDK操作后核对清理。实际停止延迟、扬声器尾音和重复取消仍需当前native候选实机验证；SDK控制回执不等于独立声学停播或机械停稳测量。
停止未确认时禁止重开、诊断回放与新任务，保留未关闭资源供显式重试。停止结束整个语音会话，不复用可能含迟到音频的连接。
资源取得或停止回执不明时，不能通过清空本地状态证明设备安全：保留“停止未确认”并显式核对，不绕过SDK或修改Daemon。legacy的PCM开麦lease异常详见历史回归说明，不能直接套作native媒体完成证据。
断连时页面显示当前状态未知；自动结束保留原因及非空错误类型，不能把空异常解释为正常成功。
“拍摄完成”“图像已附入”“回答播完”分别记录，播放成功不等于模型看懂，现场事实仍需核对。

## 交互状态与延迟

native采用持续对话前台，文字发给当前RTC，不因为后台agentWorking或speaking禁用输入；连接忙、断线、停止中或停止未确认仍锁住发送。后台权限与单并发准入不因实时对话而放宽。文字状态不证明机器人已经开口，工具completed也不代表对应回答整段已播完。

修复前ASR显示慢与回复快是用户感受；本轮已取得声学编号输入及首段转写样本，但未建立电脑直连对照或所有轮次严格对齐的ASR统计。turnLatency是应用阶段诊断，相对本轮接收请求记录已发生的阶段，firstTool仍只是工具意图；native的firstAudio标记是收到输出并交给设备peer，不是耳朵听见首音或完整播放证明。持续RTC可能包含普通对话输出，不能将会话累计帧数当成本轮端到端延迟。

设备的pipeline_age、mic_read、aec、opus最近值/最大值仅描述设备管线。编号可能在句中先被识别，问候可能先于编号回答；不能把这些时间当作整句结束到对应最终转写或可听回复的完整延迟，不作低延迟已达标承诺。

### JoyInside与native RTC音量对照：只读源码结论

对照来源为用户指定的已部署源码快照watche-voice-v043-20261007，不使用有用户改动的ESP32主工作树代替。以下是静态路径检查，不是本次测得的声压、PCM幅度或修复结果；源码快照也不替代运行二进制指纹。

- JoyInside在firmware/s3/main/joyinside_app.c:1044将回复PCM交给hal_audio_write；HAL:450经bsp_i2s_write，而sensecap-watcher.c:2204最终调用esp_codec_dev_write。所查JoyInside回复播放路径未发现独有的自动幅度归一化。
- native由media_sys.c:326配置av_render，固定16 kHz物理输出；av_render.c:1040进行必要重采样，audio_render.c:95转交renderer，render_impl/i2s_render.c:64也调用esp_codec_dev_write。重采样不等于响度归一化。i2s_render.c:168的voice-processing设置为空操作，:172返回false；虽有pcm_gain_limiter.c实现文件，当前该播放路径没有启用它。
- 两条路径使用HAL持有的同一speaker handle（hal_audio.c:642、:671）。system_volume.c:52读取NVS音量，:60应用到HAL，BSP:2286统一将最高设置限制为95；esp_codec_dev.c:115在重开时恢复音量。RTC绕过JoyInside的播放任务，不代表绕过system volume。当前公开音量控制不可用是指本示例／公开SDK入口，不否认固件内部音量及JoyInside的旋钮调节存在。
- 精确音量映射：sdkconfig.ci:2283默认请求100，但NVS的settings/volume可覆盖它；HAL保存的是请求百分比，BSP传入codec的是clamp(请求,0,95)。esp_codec_dev.c:77／:90默认曲线对非零音量V给出0.5V−50 dB，0另映射−96 dB；因此请求100且未被NVS覆盖时，传入95，对应目标−2.5 dB，不是PCM幅度乘0.95。BSP:2062配置PA电压5 V、DAC电压3.3 V、pa_gain默认0，esp_codec_dev_vol.c:50计算硬件补偿约−3.609 dB；ES8311:347据此将DAC目标设为约+1.109 dB，寄存器映射截取为0xC1，量化后约+1.0 dB。以上均为默认路径条件推导，不是现场NVS／DAC寄存器测量。
- 音量“已应用”证据限制：esp_codec_dev.c:290调用codec->set_vol后没有传回底层寄存器写入返回值；HAL getter及初始化日志也只反映请求值。不能仅凭显示100或API返回成功确认实机DAC增益。JoyInside与RTC共享该限制，尚无独立证据证明二者在现场被施加不同系统音量。
- 明确的上行差异是JoyInside的hal_audio_read经BSP:2189读取，CONFIG_BSP_AUDIO_MIC_VALUE_GAIN=1配置会对样本左移1位，未溢出的低幅样本约为2倍；native watcher_audio_aec.c:265直接读codec并处理AEC，不经过该BSP左移。此固定增益不是归一化，且左移并非饱和限幅；不能据此认定ASR延迟根因。
- 上行左移1位在不溢出条件下等效约+6.02 dB；BSP的共同麦克风硬件增益配置为27 dB（sensecap-watcher.h:169、sensecap-watcher.c:2262），native重开沿用codec已存设置。固定增益、AEC输出及实测语音电平需分别核对，不将配置值当成已测信噪比或识别性能。
- 当前不能断言“JoyInside有归一化而RTC没有，所以RTC小声”。若RTC源语音PCM电平较低，当前renderer没有自动抬升响度的环节，这是候选解释而非实测结论。需后续同条件核对源／解码／重采样后的peak、RMS、系统音量及可听度；云端RTC排队与ASR分段计时另行调查，不把设备日志当成云端回执。

## legacy：保留的任务式架构与超时回归

以下仅说明service.py中的旧VoiceService及旧候选证据，不是默认native行为；也不是自动降级开关。旧路径使用SDK PCM输入16 kHz、输出24 kHz，任务处理／拍照／播报时关闭物理麦克风；回答前建立新voiceEpoch的RTC，再经SDK播放job等待完成。旧BodyFeedback曾显示thinking/speaking表情，新默认路径已改黑色静态接管。

旧路径后台回答最多显示16000字并提示截断，TTS使用节选；这些保证不能自动继承到native。旧路径播放时不能可靠喊停，使用页面停止。其长耗时与收尾记录应继续保留，不能误写为新路径仍每轮切换RTC。

任务与播报分阶段计时，不把拍照、模型分析、RTC连接和设备播放塞进同一个90秒总计时器：

| 阶段 | 超时条件 |
| --- | --- |
| Codex任务 | 连续90秒没有本轮文字或工具进度；仍保留5分钟硬上限 |
| 准备语音连接 | 文字回答完成后单独计60秒 |
| 等待首段语音 | 本地speakable发送成功后45秒，发送成功不代表服务端接受 |
| 已收到语音、等待后续 | 连续30秒无有效音频或播放完成进度 |
| 设备实际播放 | 取得SDK job后按音频时长加10秒等待回执，不用空闲计时中断正常缓冲播放 |
| 整轮语音 | 从文字回答完成起最多3分钟；硬上限不因持续输出续期 |

连接心跳、麦克风状态快照、旧轮次事件不更新任务进度时间戳。90／60／45／30秒是软时限：已有事件排队或getter取出后尚未派发时，先派发再判旧进度；这不是进度续期，任务5分钟及语音3分钟硬顶仍先检查。文字任务完成后立即切换语音期限；done转写先到、RTP后到时，不再另设2秒早停。超过旧90秒而仍有进度的回合不会仅因总时长被截断。
无论done在首段有效音频之前或之后到达，统一使用30秒无新增音频的保守收尾窗口，且等待所有设备播放回执；不能把0.6秒批处理间隔当成整段结束。页面显示“确认播报完成中”，窗口内迟到音频仍接纳并重新计时，后续播放失败不会保持成功。此策略不是原生媒体EOS证明，不保证超过窗口才到达的数据；正常回合也可能额外等待30秒，仍受3分钟语音硬上限约束，达到硬上限不能改判成功。后续应由可靠媒体EOS或可核对音频长度替代此实验策略。
旧voiceDiagnostics只保留有界事件类型/时间、音频帧数/峰值/门禁计数、speakable字节数/哈希及voiceEpoch；停止前保存快照，不保留录音、图像、SDP或凭证。通过peer门禁的字节数不等于设备已经播放。native另有deviceRtc接收／排队及AEC／设备媒体诊断，计数同样不能替代声学验收。

## 运行

安装SDK、已登录且支持实验性realtime V3的新版Codex；机器人联网并进入Python SDK应用。默认native入口需设备同时协商rtc.audio.full_duplex.v1及黑屏接管所用的expression.runtime.v3；旧PCM可用不代表满足这些条件。
Windows 优先发现 Codex Desktop 原生客户端，再回退 PATH CLI；macOS 使用 PATH CLI。旧 CLI 文本可用不代表语音协议兼容。

在 SDK 仓库根目录执行，两平台命令相同：

~~~text
python -m pip install -e . "aiortc>=1.14,<2" "tomli>=2,<3; python_version < '3.11'"
npm --prefix examples/codex_voice_app/web ci
npm --prefix examples/codex_voice_app/web run build
watcherobot robot pair <设备显示的六位配对码>
watcherobot app check examples/codex_voice_app
watcherobot app run examples/codex_voice_app
~~~

Daemon 管理进程，工作台地址会自动打开并记录在应用日志中。点击「开始对话」，授权必要能力，然后说话或输入文字任务。
文字输入发给持续RTC前台，是否产生后台任务以显式委派及真实回执为准；后台忙不代表不能继续对话，也不保证追加动作会执行。快捷请求缺权限时原位提示，不自动替用户授权。
「停止动作与播放」或「结束对话」结束会话；手动再次开始时权限默认关闭。watcherobot app stop 停止整个 Application。
「设备诊断：录音并回放3秒」只在语音会话未运行时使用旧SDK录音回放资源，不替代native媒体、AEC、声学全双工或真实agent验收。

可选环境变量仅作用于应用进程，不写入仓库：

| 名称 | 用途 |
| --- | --- |
| WATCHER_CODEX_BINARY | 显式指定兼容的原生客户端 |
| WATCHER_CODEX_AGENT_PROVIDER | 显式选择现有任务模型 provider |
| WATCHER_VOICE_NO_BROWSER=1 | 不自动打开页面 |
| WATCHER_CODEX_VOICE_TRANSPORT=webrtc | 默认且唯一支持的模式；websocket 在获取资源前明确拒绝 |

默认语音 WebRTC 使用现有 ChatGPT 登录。任务线程使用显式 provider 或已配置且有环境鉴权的本地 gateway；不打印或复制凭证值。
本机网络/provider 可用性，不代表所有账号或官方服务均兼容此实验协议。

## 相机前提与排查

必须匹配 Himax 固件和 ESP32 相机后端。本次实机 Himax 启动信息是640x480 JPEG/PTL，需配套 PTL；SSCMA 会连接超时，320×240也会被 PTL 明确拒绝。
应用使用640×480，失败时明确报错，禁止同轮自动重试，不拿旧图替代。legacy拍摄前关闭PCM麦克风；native不主动关闭RTC媒体，必须另验授权后拍摄与媒体并行是否可用。本轮尚缺观察权限，不声称native相机端到端通过。
固件烧录属于独立维护流程，不在 Application 内自动执行；不要盲刷其他 MCU、修改 NVS 或路由。

## 测试与证据边界

当前native分轮实机结果、待验门禁与legacy分阶段超时修复的候选哈希分别见[验收记录](ACCEPTANCE.md)。
音质优化候选clear-speech-v3使用native48k直通、连续增益与5ms线性前瞻限峰，
保持静音时间线且停止不补播尾音；legacy24k合同不变。示例Python294通过／1跳过及
app check通过。指标及同源对照边界见[音频诊断](AUDIO_DIAGNOSTICS.md)；
代码通过不等于用户认可的清晰度、声学双讲、云端延迟或完整产品验收。

~~~text
python -m pytest examples/codex_voice_app/tests -ra
npm --prefix examples/codex_voice_app/web run test
npm --prefix examples/codex_voice_app/web run lint
npm --prefix examples/codex_voice_app/web run build
python examples/codex_voice_app/probe.py --input-wav <测试语音.wav>
~~~

probe.ps1 / probe.sh 透传相同参数、默认行为和退出码，WATCHER_VOICE_PYTHON 可指定解释器。
probe仍是legacy／模型协议诊断，不是默认native入口验收。WAV必须为16 kHz单声道s16le PCM，最多30秒。探测不打开机器人麦克风，只在转写、真实任务回答及匹配新RTC音频均到达后返回0；不能证明native设备链路、声学输入或设备整段播完。
Windows 未安装 sh 时，Shell 入口执行测试明确跳过，不把跳过算跨平台实测通过。

## legacy：历史限定实机证据

2026-10-07的以下记录属于旧任务式路径：SDK0.1.10、ESP32 v0.4.3 debug/PTL、Codex Desktop0.162.0-alpha.2。B79／88CE的真实声学current、nearby失败及停止未闭合项保留在ACCEPTANCE.md，不迁移为native通过结论。
当前观察取得20,332字节新JPEG，模型识别办公区桌椅和麦克风，669,120字节语音经设备等待确认完成后恢复麦克风。
小范围观察取得三张不同哈希照片（18,488／17,409／18,310字节），运动回到中心；模型识别现场变化，设备898,560字节／220帧、零丢帧、DMA排空后恢复聆听。
这些是限定闭环证据，不等于10次稳定性验收、声学模拟测试全通过或生产级认证；早期失败不计入成功。

## 已知限制

- app-server realtime V3、delegation、前台context与speakable仍属实验协议，可能变化；不是稳定公开的“桌面Codex语音嵌入API”。身份去重和忙碌拒绝都需要当前native候选回归。
- 原生RTC具备媒体同时收发及设备AEC路径，但声学全双工尚未验收，AEC效果、语音打断、喊停和延迟仍待实测。未实现Hey Codex唤醒词、自定义TTS、声线克隆或后台常驻。会话开启会转写附近说话；逻辑静音不关闭硬件捕获，需要停止媒体时请结束会话。
- 黑屏是应用静态显示接管，不是全部renderer停止，不声称CPU已释放。应用退出后的显示恢复行为需独立核对。
- 此debug设备已通过既有设备设置将逻辑音量设为100，仍遵守BSP防失真封顶；
  不突破硬件上限，不把逻辑100当作DAC寄存器回读。用户已确认上一候选变响，
  但反馈不够清晰；当前v3需独立主观确认，不继承旧版音量或清晰度通过结论。
- native单条转写及后台结果目前截取至16000字，不能继承legacy的全文主区保留与明确截断提示保证；后台结果到前台的改述及长文本呈现需另验。原生语音不是严格逐字TTS。
- legacy的独立RTC切换、按静音间隔批量播放及≤500 UTF-8字节节选属于旧路径限制，不是native当前输出机制。
- 固件内部连续内存偏紧，配网后首轮曾出现 no_capacity；应用不伪造成功或自动重启。PTL偶有拒绝残留帧头告警，需独立固件压力回归。
- 电脑音频接口存在不代表有实体扬声器。模拟声学测试必须看到机器人转写及对应任务，不能仅凭主机TTS调用成功宣称通过。
- npm run dev 只供页面开发，设备入口必须由 managed Application 同源提供 web/dist 与 /api/session。
