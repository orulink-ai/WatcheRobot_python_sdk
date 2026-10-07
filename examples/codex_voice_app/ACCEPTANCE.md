# Native RTC与legacy验收记录

## 最新最大语音响度候选（2026-10-08）

用户复测前一候选仍然很小。因此前轮的PCM电平+6.7 dB不能算用户可听度通过。
本轮未重新刷机；通过managed Application重启，确认现场snapshot的
`playbackGain.profile=maximum-speech-v2`，保留原生Device RTC与Daemon路由。

### 根因复现与修复

- 旧策略只允许4倍且需要整帧满足单个峰值的headroom。实际生产函数测试中，
  同一块479个1000样本及一个32767峰值，会使正常样本降到885。
- 新策略目标RMS12000、最大16倍、80 ms增益上升，最多20 ms控制块；
  22000以上对单个样本做软拐点压缩，渐近到29000，不再让一个峰值降低整帧。
  压缩改变峰均比，不承诺音色不变或达到所有输入的目标RMS。
- 再次通过设备debug CLI查询并重应用系统逻辑音量100，HAL未报告codec设置错误。
  不突破BSP codec参数95的防失真限制；未读取DAC寄存器。

### 真实机器人扬声器输出测量

同一USB测量入口、请求同一句固定内容：机器人实际经RTC扬声器发声，邻近
Insta360 Wave USB麦克风取电平，只记录有界汇总，不保存录音或私有转写。
机器人上行逻辑静音以避免回授触发；未修改电脑默认设备。

| 指标 | 前一profile | maximum-speech-v2 |
| --- | --- | --- |
| USB麦克风高能量四分位RMS | 0.016992 | 0.0690285 |
| 相同麦克风入口dBFS | -35.40 | -23.22 |
| USB测量peak | 0.1748688 | 0.4836547 |
| USB流警告 | 0 | 0 |
| 应用累计源input / output RMS | 1856.1 / 3870.7 | 1754.3 / 8617.3 |
| 新版仅语音块input / output RMS | 未提供 | 1895.3 / 9310.2 |
| 固件render_error | 0 | 0 |
| 最终stop / microphone | confirmed / false | confirmed / false |

USB电平差约+12.18 dB，支持本次实际出声变强；模型两次波形不同，旧版应用RMS
也包含之前会话，不能把表格视为严格同源A/B或校准声压。测量麦克风可能有内部处理，
没有证明物理响度为多少倍。用户随后明确反馈“变大了，但是不够清晰”；
只能记录响度改善获得主观确认，清晰度未通过，不能写成完整音量／音质问题已解决。
最终编码前peak=28994（应用源）、源限幅上限29000，没有以硬削波或突破codec上限放大。

### 回归与交付边界

按TDD先复现最大响度达不到和单峰压低整帧，再修复；覆盖噪声不放大、
符号对称与软膝单调、分块控制、语音能量排除长静音、RTC重采样峰值保护及服务集成。
示例Python255通过／1跳过，managed app check通过。
当前真实播放测试使用文字请求生成固定测试语音，不是新增ASR声学输入验收；
不借本轮音量结果宣称ASR、AEC双讲或全部产品门禁通过。

### 用户反馈之后的清晰度只读排查

同轮固件audio_packets=661、audio_decoded_frames=661、render_error=0、queue_dropped=0；
这些点未显示解码丢帧或播放错误，不证明全过程不存在网络抖动。
JoyInside现有源码明确以16 kHz mono PCM播放，RTC物理输出也16 kHz，
因此不能仅凭采样率或“用了Opus”断言不清晰的根因。
当前主机链路先将Codex解码48 kHz变成24 kHz，交给DeviceAudioTrack后再变成48 kHz。
新版源软压缩4073/129120个语音块样本；后续重采样保护36/314帧生效，
最大浮点峰值39644.4、最小整帧scale=0.7315。强增益／压缩及重复重采样是
音色变化候选，不能将整数clippedSamples=0理解为绝无可听失真。
尚未完成同一源波形的清晰度A/B或实际扬声器非线性测量；未在本轮盲改固件采样时钟。

## 前一native修复验收（2026-10-08，音量听感未通过）

实际完成源码修复、ESP32 app-only构建烧录及两轮声学测试。只更新OTA选择记录以启动候选应用，未擦NVS、SD或其他MCU。最终固件SHA-256：`5DE2FC044A4D8BBBFF779E1EE8BB60C694D63C9E0B107AE3439A4953341F55F0`。

### 分轮结果，不能拼作同一候选稳定性验收

| 项目 | 时钟修复候选 | 最终FIFO及播放增益候选 |
| --- | --- | --- |
| 会话时长 | 430.1秒 | 170.2秒 |
| 声学编号识别 | 5/5 | 5/5，另独立核对全部五个编号的assistant回复 |
| 主机micFrames | 21550 | 8557 |
| 固件capture / tx | 21518 / 21518 | 8542 / 8542 |
| 固件stale_drop | 0 | 0 |
| 主机FIFO停留最大值 | 235 ms | 110 ms |
| 最终停止 | confirmed | confirmed，connected=false、microphone=false、error为空 |

两轮均用Insta360 Wave USB实体扬声器播放编号731、842、953、624、516，经机器人实际麦克风输入；无文本／PCM注入替代收音。不同计数来自不同采样点，不能以差值推断网络丢包。最终编号回复由主代理独立检查最终状态确认，不宣称已执行后来补入driver的自动回复gate。

最终候选事件停留最大16 ms，固件tx_error=0、render_error=0。发送FIFO暂时入队高水位240 ms，发送前丢弃停留超过120 ms的旧数据，累计丢弃960 ms、恢复40次；RTP样本时间线保持连续。这是以少量丢弃换取实时性，不证明云端无丢帧或ASR本身更快。

### 音量与延迟边界

- 调试接口查询系统逻辑音量100，并明确重应用100；BSP仍限制codec参数最高95。没有DAC寄存器回读，不将逻辑值当硬件测量。
- 同源PCM累计input RMS=1828.9、output RMS=3959.7，约+6.7 dB；raw peak=15002、output peak=29000。浮点重采样后、Opus编码前peak=29000、clippedSamples=0。没有声压计、同条件JoyInside声学对照或用户主观听感验收，不声称物理响度翻倍、解码后零失真。
- 首段转写样本约2.2秒／2.3秒，云端仍有秒级等待。编号位于句中，匹配可能早于整句结束；先前转写／问候会混入首文本和首回答时间。因此不以所有driver时间作严格ASR分位数，也不使用负数匹配时间作句末延迟。
- 尚未测电脑直接语音同句同网络参考；不能说达到电脑体验。未向当前Codex V3发送未经确认的通用Realtime turn_detection配置；若改为独立低延迟ASR，属于另一个需确认的架构范围。
- 设备源帧超过3秒不续期报一次断流；静音帧可续期，心跳／下行不可续期，没有固定会话结束时限。重复start不清空在途后台任务。

### 自动回归与剩余门禁

示例Python250通过、1跳过（本机未安装对应Shell）；ApplicationRtc及Daemon路由32通过；audio_tx_pacer、debug_audio_volume、sdk_rtc_architecture_static三个host目标3/3通过；音量静态／共享串口7通过；managed app check及限定文件diff检查通过。本轮未改前端代码。

本轮完成基础真实声学收音、对应编号回复、停止确认及音频幅度提升验证；不追认真实双讲／AEC效果、实际可听首音、播放中紧急停止、native视觉与动作闭环或电脑直连延迟对照已通过。下方修复前与legacy记录只作历史，不覆盖本节结果。诊断合同见[AUDIO_DIAGNOSTICS.md](AUDIO_DIAGNOSTICS.md)。

## 修复前native历史状态（2026-10-08）

本节区分源码可见架构、用户／主代理现场报告和仍待验证的结论。本次只更新文档并检查一致性，未操作设备、浏览器或模型，未独立运行代码／声学验收。49308为本次现场实例引用，不是冻结候选的源码指纹。

### 架构与产品边界

- app.py默认选择ContinuousVoiceService；模型持续RTC前台与DeviceRtcPeer原生设备WebRTC媒体桥接，由SDK注入ApplicationRtc控制设备会话，不使用旧PCM播放job冒充原生双向媒体。未协商rtc.audio.full_duplex.v1时不自动回退legacy。
- 真实转写不自动提交机器人动作，后台只接收显式delegation，单并发工具仍受camera/upload/motion/lights权限保护；文字可发给当前RTC，不因后台忙或输出而停用。
- snapshot的echoCancellation仍为unverified。AEC启用、媒体收发、主机帧计数均不是声学全双工验收证书；后台任务完成也不等于设备整段回答播完。
- expressionsEnabled=false、bodyFeedback=suppressed为黑色静态接管与应用自动表情抑制，不是全部renderer停止或CPU释放；语音停止不主动恢复动画。
- 静音是上行逻辑静音／零输入，不关闭硬件捕获或持续RTC媒体，也不静音下行。当前公开音量控制不可用，偏小反馈尚未解决。

### 49308现场报告：基础收音与可听输出

- native RTC已连接，真人经机器人麦克风说话后被转写，用户听见Codex扬声器回答，报告声音偏小。
- 较早快照中主机已消费micFrames=8308，DeviceRtcPeer.receivedFrames=8308。只核对这两个计数点，不能证明全网络／云端收齐、正确识别、无回声或完整播放。
- 用户感觉上行ASR文字慢、回复快。没有输入结束、首段／最终转写、首输出音频及可听首音的对齐样本；延迟未通过验收，不能填入臆测的端到端时延。
- 本轮相机缺少所需权限，未取得native新JPEG、同轮分析和对应可听回答的完整闭环；不能继承下文legacy照片或成功结论。

### 测试设备管线诊断（用户提供的只读日志）

日志时间约UTC 2026-10-07 16:03（北京时间2026-10-08约00:03）。AEC active、reference drops=0。下列成对数值是设备内部最近值/最大值，不是p50/p95、端到端或云端延迟；也不是此前8308帧快照的同一采样时点。

| 设备阶段 | 最近值 / 最大值 |
| --- | --- |
| pipeline_age | 45553 / 113885 us |
| mic_read | 22928 / 51778 us |
| aec | 8402 / 46532 us |
| opus | 6460 / 58192 us |

该时点capture=13059、tx=13059、err=0、stale_drop=0；rx=13058、decoded=13057、i2s=8353920B、render_err=0、queue=0ms。计数支持该设备日志时点存在捕获、发送、接收、解码和I2S输出活动；零错误／零排队不能证明云端时延、整轮无丢包、实际声学并发或AEC抑制效果。声学全双工尚未验收。

### JoyInside对照：静态归因，不算修复验收

只读检查用户指定的已部署源码快照watche-voice-v043-20261007；未修改、构建、烧录固件，未读取运行时音量或执行新声学测试。ESP32主工作树存在用户改动，未使用它代替该快照。路径下列行号仅指所查源码，不证明运行二进制与当前文件完全匹配。

| 项目 | 所查固件路径与结论 |
| --- | --- |
| JoyInside播放 | joyinside_app.c:1044 → hal_audio.c:450 → sensecap-watcher.c:2204 → esp_codec_dev_write；所查回复路径未发现独有自动归一化 |
| native播放 | media_sys.c:326／:359配置renderer及16 kHz物理输出，av_render.c:1040重采样，audio_render.c:95转交写入，render_impl/i2s_render.c:64直接写同一codec；:168设置为空操作、:172报告voice processing未启用，不能因pcm_gain_limiter.c存在就算增益已生效 |
| 系统音量 | hal_audio.c:642／:671取得并共享codec handles；system_volume.c:52／:60读取NVS并应用音量，sensecap-watcher.c:2286最高限制95，esp_codec_dev.c:115重开恢复设置；无证据支持RTC绕过system volume |
| 上行增益 | JoyInside经sensecap-watcher.c:2189的BSP读路径；sdkconfig.ci／sdkconfig.defaults配置MIC_VALUE_GAIN=1，低幅未溢出样本约2倍；native watcher_audio_aec.c:265直接读codec并处理AEC，不经过该BSP左移。固定增益不是归一化，不证明ASR慢的原因 |

文件均相对于firmware/s3：HAL在components/hal/hal_audio/src，BSP在components/sensecap-watcher，renderer在components/av_render，codec在components/esp_codec_dev，AEC在components/watcher_audio_aec，应用及system_volume在main。

精确增益补充仍为源码推导，非新增现场测量：

- 默认CONFIG_WATCHER_AUDIO_VOLUME=100（sdkconfig.ci:2283）；system_volume.c:52的NVS settings/volume可覆盖。hal_audio.c:280保存请求值，BSP:2286传给codec的值被限制为0–95。请求100未被覆盖时实际函数参数是95，不是100。
- esp_codec_dev.c:77／:90的默认百分比曲线为非零V对应0.5V−50 dB，0对应−96 dB；V=95得到−2.5 dB。这是codec目标增益，不是线性PCM幅度95%。
- BSP:2062的5 V／3.3 V及默认pa_gain=0，经esp_codec_dev_vol.c:50得到约−3.609 dB硬件补偿；ES8311:347将DAC目标设为约+1.109 dB，:144的范围及esp_codec_dev_vol.c:9截取映射得到DAC寄存器0xC1，量化约+1.0 dB。仅适用于此默认曲线与成功寄存器写入条件，不代表已读取实机寄存器或测得声压。
- esp_codec_dev.c:290未将codec->set_vol的底层返回值传回；HAL getter／初始化日志仅表示请求值。API成功或日志100不足以独立证明实际应用增益。当前没有现场NVS值、DAC回读或同条件JoyInside／RTC电平差异证据。
- 共用麦克风硬件增益配置27 dB（sensecap-watcher.h:169、BSP:2262），codec重开恢复已存设置；JoyInside另有左移1位，在不溢出时约+6.02 dB，native直接codec读不经过该固定增益。它不是自动归一化或ASR延迟测量。

结论：不能写成“JoyInside有归一化而RTC没有”。当前两条播放路径共享系统音量，RTC没有启用自动响度抬升；源PCM幅度差异与实际音量状态仍待测。公开音量控制不可用指本示例／SDK，不否认JoyInside可在固件内调节system volume。后续仅凭同条件peak／RMS、音量状态、可听度及对齐的ASR／云端队列时序新增测量证据；本轮不记录任何改善或延迟通过。

### 自动回归报告与当前门禁

主代理报告全部示例Python为221通过／1跳过，managed app check通过；本次未复跑或独立核对总数，以主代理最终测试输出为准。这些结果不能替代实机验收，下文160／12／21等计数仅属于legacy历史候选。

当前native候选仍需保留以下门禁，不用量产目标扩张v0，也不因基础对话已通而关闭：

1. 实际扬声器输出中停止、重复取消及迟到输出清理；停止未确认须锁住重开与动作，重开四权限全关闭。legacy的1.843秒不能继承。
2. 同时说听、AEC回声／自触发、逻辑静音及恢复的真实声学验证；以真人输入和可听输出核对，不只看active或帧计数。
3. 分段测量ASR与首个可听回答延迟，以及当前偏小音量的可听度；设备管线最近值/最大值不能替代。
4. 显式授权后的native current与nearby新图、同轮分析、两端与回中心、对应可听输出；缺权限或部分失败不计完整成功。
5. 冻结同一候选后10轮至少9轮完整成功，覆盖current与nearby，并复验native页面窄屏／键盘；不拼接legacy或不同版本样本。

## legacy：2026-10-07任务式超时修复与历史证据

以下原始候选哈希、成功、失败及未验证样本全部保留，仅适用于旧VoiceService任务式PCM路径。不证明当前native架构通过，不把不同候选的样本合并成稳定性结论。

### 修复范围

- 删除覆盖拍摄、分析、语音连接和播放的90秒统一总期限。
- 任务连续90秒无本轮真实进度才失败，任务硬上限5分钟；完成文字回答后进入独立语音期限。
- 语音连接60秒、请求后首音频45秒、流中断30秒分别判定；实际播放等待SDK回执，语音总硬上限3分钟。
- 所有done/PCM顺序使用一致的30秒无新增音频收尾窗口，等待入站事件队列、缓冲、播放队列和设备播放回执排空；达到硬上限不能改判完成。
- 页面区分“确认播报完成中”和失败。忙碌期间追加请求明确未执行，仅发notice，不覆盖真实错误或新增任务。

30秒收尾是实验协议的保守策略，不是远端媒体EOS证明。可能增加正常交互等待，迟到新音频会重新计时；不保证窗口结束后到达的音频完整性。设备DMA排空只证明已发送的播放段完成，不证明上游整段音频已生成。

### 自动回归

| 范围 | 结果 |
| --- | --- |
| 示例Python | 160通过，1跳过（本机未安装Shell入口所需sh） |
| 页面状态 | 12通过，含停止状态优先级回归 |
| 页面lint、生产构建 | 通过；仍有555.09KB包体警告 |
| SDK路由及ApplicationContext | 21通过 |
| managed Application配置检查 | 通过 |

新增测试先复现再修复：PCM先到后done时提前完成、已排队未消费的尾段、第二段播放失败，以及watchdog在排队／getter交接期间按旧进度误软超时。另覆盖179.999/180/180.001秒边界及未完成播放、任务与实际播放跨旧90秒期限、旧epoch和心跳不续期、忙碌提示与真实错误隔离。Python3.10 getter交接由定向测试模拟，未在3.10解释器实测。待派发事件只暂缓软判定，不更新进度时间戳或硬期限。

### 实机环境及证据

SDK0.1.10、ESP32 v0.4.3 debug/PTL、STM32 v0.1.3、Codex Desktop0.162.0-alpha.2。主机指定Insta360 Wave USB输出，扬声器位于机器人附近。声音经机器人实际麦克风输入，不注入文本、PCM或伪造转写。业务设备访问全部经managed Application注入通道，未修改Daemon路由。

#### 候选B79：当前视野声学闭环通过

service.py SHA-256：`B79B3752C0F87B9E24D68218B109C5261861156C1F14A61F278AE015DD539A4A`。
turn_deadline.py SHA-256：`228BEE09966B71C37DCE835280562EF0B0A710FB73BB57F6CDF44E2C630C8E0C`。

- 编号937声学请求被机器人转写并提交一次，约80.0秒从刺激到本轮完成（包含转写和保守收尾，不是纯模型延迟）。
- 任务`c07d5217503b4460a82d0e048a14c0c8`，Codex轮次`01a116db-3b27-7253-aa7c-3ff8be8b98c8`。
- 新JPEG 18,180字节，照片`c03eb41c05f54d69896244f77ca53636`；读取原图并核对SHA-256：`e2b3af283279c15db76fa05c15f5e67306a31dc25c06b11a296a23bddd23b3ba`。
- 同轮分析给出当前视野中的人、椅子、办公桌和显示器，并明确不能确认其他方向。
- 已接收语音552,000字节经设备确认完成；UART记录135帧入队、135帧播放、0丢帧、DMA排空result=0。
- 收尾后任务completed、error为空、麦克风恢复；最终停止confirmed、麦克风关闭、四项权限全部撤销。

#### 前一候选：附近观察失败，不能计为成功

service.py SHA-256：`27B238155DEB7462F64FBDBEF6AB1AB60DE93D2057B0B48B36B8CB6E1344FF0F`。

- 编号937真实声学输入已验证，约98.9秒完成失败反馈，没有因旧90秒总期限打断。
- 任务`1ce58f7bd2ae4970910e3aa8f8075cad`，轮次`01a116d9-0614-76a0-a720-bf5f7db734e3`。
- 只取得中心JPEG 16,937字节，80°运动Job30返回error_code=263，任务failed；未发送不完整照片集假装三视角完成。
- 失败说明546,240字节在设备播完，UART134/134帧、0丢帧、DMA排空result=0；麦克风恢复，最终停止confirmed。
- 该结果只能证明失败反馈与清理路径，不能证明附近观察成功。运动完成回执异常仍需独立定位，不能通过放大超时掩盖。

### 当时尚未闭合的门禁

最新源码service `88CE3475`、deadline `49EBCC77`又补齐了getter交接与软超时保护。其第一次声学复验在测试脚本35秒转写等待结束时停止：随后诊断显示最终输入转写／delegation在停止边界到达；没有执行任务。这一轮保留为未验证，不继承B79通过结论。测试脚本转写等待调整为90秒，后续实机复验单独记录。

B79停止回归：UI speaking后双击停止，1.843秒确认清理，连续3秒无迟到输出、麦克风或任务活动，error为空并撤销权限。UART的“TTS started, preparing playback”比停止早约3.09秒，但缺少停止前实际播放帧计数，故只证明播放启动／清理路径，不追认“实际扬声器播放中停止”门禁通过。

#### 当时最新候选88CE：当前视野声学复验通过

service.py完整SHA-256：`88CE347587F2E02E91E011BE05E8D479ED761D4A972C2D0FAB6A9668C70130DE`。
turn_deadline.py完整SHA-256：`49EBCC777DCCE0ECC6F6608F23BC669166F50C1EF43C4DF3A1FA7F53752F6E1E`。

- 转写等待90秒的独立新样本，编号937识别、一次提交，87.2秒从刺激到完成。
- 任务`7ccf30c4243f48919d0f1cb0ea06d47f`，轮次`01a116e2-7240-7e70-9b80-26cbc548e64b`。
- 新照片`6b10de1936de4b76a502cad430b9d18d`，JPEG15,580字节，SHA-256 `c89bb7bae880bfc5ef5ee5b4514d60ca302c310ee44b24dc115db3cef8e73737`；原图读取与校验通过。
- 同轮回答描述手表、桌椅、显示器、柜子等，明确前方视野和模糊物品限制。
- 已接收语音688,320字节播完，UART169/169帧、0丢帧、DMA排空result=0，随后恢复麦克风。
- completed、error为空；最终停止confirmed、麦克风关闭、全部权限撤销。没有继承前一未验证样本。

只读架构评审已关闭本轮具体P1，评分9.2/10（受限实验v0）；产品评审8.5/10，仍要求正常nearby、实际播放中停止和重复性门禁。评分不是独立现场认证。

- 最终候选正常三视角观察与返回中心全闭环。
- 冻结版本10轮至少9轮完整成功，覆盖current与nearby；不同版本不能拼样本。
- 实际扬声器播放中停止的可核对证据（不是仅UI speaking或TTS准备事件）。
- 可靠远端媒体EOS或可核对音频长度，以替代固定收尾窗口。

当前不是10/10或生产级交付。保留此前失败及未验证样本，不因新代码修复而追认旧样本通过。

### 页面与键盘回归

- 新增停止文案优先级先红后绿：stopping显示正在停止，unconfirmed保持错误提示及锁定，不继续显示旧的settling或speaking主状态。12页面测试、lint、构建通过。
- 完整独立键盘流程：Enter开始、Tab到静音并Enter、Enter展开设置、Tab/Space授权camera/upload（motion/lights仍关闭）、键盘输入并Tab/Enter发送、新任务拍照分析、原图链接Tab到来源并Enter展开、Tab/Enter停止、Enter重开并检查四权限默认全关闭，最后Enter结束。
- 此键盘任务`a492d62976ca4fb09b97ef6986a8ea56`取得新照片`8a17dd30037d4fb58e8a1c17204a4d64`，页面显示哈希`8735697e178a2fa703efec89170f010936c361bb685e3fbe94216f68510e5bf6`。主动在settling停止，任务cancelled，不把它算作完整播报成功。
- 发送、静音、停止按钮的键盘焦点为2px可见轮廓。此前360px窗口无横向溢出，当前758px窗口照片加载且无溢出；最后保持会话confirmed、麦克风关闭、全部权限撤销。
- 第一次键盘尝试被环境语音触发的非验收请求打断，已停止，不计完整流程；上述隔离后第二次流程单独记录。
