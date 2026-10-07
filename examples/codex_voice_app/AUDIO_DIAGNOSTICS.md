# 持续语音的延迟与响度诊断

## 清晰度优化候选 clear-speech-v3

native 路径现在是 Codex WebRTC 解码48k → 算术平均下混mono →
持续增益/5ms线性前瞻限峰 → 设备48k Opus发送。删除主机内部的
48→24→48往返；这不代表设备物理输出改为48k，也没有绕过Opus。
legacy 输出仍遵守24k合同，不修改SDK Daemon业务路由或设备时钟。

同一音轨输入格式固定，格式改变即失败；支持s16/s16p/flt/fltp和mono/stereo，
浮点非有限值、超范围值明确拒绝。native内部事件必须提供匹配的整数voiceEpoch、
完整样本计数、48k mono s16le数据。设备发送端首次入队后不允许切换采样率。

增益按样本更新，RMS包络约40ms，增益上升80ms/下降40ms，最高16倍，
目标RMS12000只是控制目标，不是保证达到的语音响度。
峰值保护通过未来5ms内的峰值约束平滑降低线性增益，释放约80ms，
不再使用逐样本软拐点波形压缩。自然语音动态仍可能改变，不能声称无失真。
限峰仍留29000峰值余量。正常native路径的设备端最终保护应保持
limitedFrames=0/minScale=1/resampledFrames=0；它仅是异常保护，不是第二级增响。

native连续静音也送入DSP，避免长停顿冻结包络；设备发送轨自行产生按20ms
节奏的空闲零样本，不把启动积攒的纯零输出重复塞入播放FIFO；DSP延迟的
非零尾音仍入队，不裁剪。静音不增加回复
outputBytes、不显示持续speaking、不提前记录firstAudio。大事件以20ms切片
协作让出事件循环，并在每片检查停止及生命周期。紧急停止不flush、不补播5ms尾音；
离线/自然完成flush一次，随后幂等返回空并拒绝继续process。

新版元数据含sampleRate、lookaheadMs、peakConstraintSamples、
speechLimitedSamples与minLimiterGainDb。limitedSamples仅统计至少0.1dB衰减，
不是整数削波数。新版speechRMS按原始逐样本幅度筛选，不是VAD语音段或LUFS，
不能与v2的块级speechRMS直接比较。严谨前后对照使用同一源的共同样本掩码。

2026-10-08同源数字及第一轮实体A-B-B-A对照：

- 固定非私人合成语音9.866秒，48k源SHA-256：
  `90db15ab702ff9687a47e5eeb51e12c0599fae625e5cded6db240e5096ff1134`。
- 同源共同掩码有效段RMS：旧8463.21，新7819.99，约-0.69dB；两者峰值29000。
- 弱→强合成边界测试：旧增益台阶4411 PCM单位，新0；
  新500Hz正弦稳定段等比例拟合误差约0.002%，不当作扬声器THD+N。
- DSP每20ms帧本机平均2.4ms/p95 2.8ms/最大4.5ms，单次微基准，
  不是全链路实时性或所有电脑的性能保证。
- 第一轮相同设备RTC出口、相同摆位和音量，邻近USB麦克风上四分位帧RMS
  电平旧-17.58/-17.74dBFS、新-18.43/-18.13dBFS，平均约-0.62dB。
  无流警告，固件2465 decoded frames、render_errors=0，设备stopped回执及host关闭确认。
- USB入口可能有AGC/降噪，不能把这些未校准电平当声压或扬声器失真认证。
  只保存汇总，不保存录音；测试期间不调用模型、不上传麦克风、不拍照或运动。
- 用户要求增加“第几段/旧版或新版”的语音标识后，另行重播；
  有标签对照不是盲听。用户反馈四段都清楚，但没有听出明显差别；
  不能把本轮记录成“新版清晰度明显改善”。
  带编号第一/四段旧版测得-17.81/-17.16dBFS，第二/三段新版-18.34/-18.26dBFS，
  四段均无USB流警告，render_errors=0，设备stopped回执及host关闭确认。
  旧版两次差0.65dB，说明环境/麦克风变化不可忽略，不据此宣称精准响度差。

当前为已实施的音质优化候选，不是“音质问题已验收修复”或10/10交付。

首轮真实Codex复验发现启动静音积压：native接收145帧/2.9秒纯零PCM，
设备只消费17帧/0.34秒，却入队115帧，触发2秒积压保护；会话安全停止confirmed。
这轮没有语音输出，明确记为失败，不以AB播放通过替代。
修复将空闲零样本处理放在发送轨：缓冲空时不积攒纯零帧，recv按原20ms节奏补零；
缓冲里仍有节目音频时保留零样本，避免缩短词间停顿。DSP仍消费连续源PCM，
不冻结增益状态，不裁掉非零延迟尾音。新增启动3秒静音突发、语音中零间隔保留回归，
最终示例294通过/1跳过；真实运行结果另记，不继承失败样本。

## 分层指标

### 最终native真实下行复验

同一设备、既有ChatGPT登录与managed Application，逻辑静音上行后通过文字请求
真实Codex朗读固定句子。收到回复活动PCM443520字节；这不是主机SAPI对照，
也不是机器人真实麦克风ASR或声学双讲验收。

- 运行profile=clear-speech-v3，源/输出48000，sampleRateConversions=0。
- 设备发送resampledFrames=0、limitedFrames=0、minScale=1，无第二级帧增益竞争。
- 固件末次统计495包/495解码、render_errors=0，停止时bufferedBytes=0。
- 最终stopStatus=confirmed、deviceStoppedReceipt=true、host stopConfirmed=true，
  microphone=false、error为空。该新样本单独通过下行/清理门禁，不追认启动失败。
- 有效回复样本幅度统计inputRms约2395.8/outputRms约9093.3；
  限峰仍有动态代价，不用这两个数值推断主观清晰度。

最终源码SHA-256：

- playback_gain.py：`635776700a76cbfd1388d6b4b59a13ee3fa4003d9d2bfadaaafd9ec7638c1c74`
- realtime_peer.py：`405454f0437e362868ae514ad23f47cb354a779855cf4c2e8e9f7bba35012915`
- continuous_service.py：`17fe67c1a4985cd1cb8315d23e8ba213d6f23a076444b839143d177daebe0dee`
- device_rtc_peer.py：`8e6d9ada53e54128a89f6602ca5f2fff60f885c9fcd0b693b06133292fe2151d`

### 指标含义

- `deviceRtc.media`：固件采集、发送、错误与过期丢帧计数；排查“控制连接在线但麦克风停滞”。
- `deviceRtc.receiveClock`：电脑解码源帧到达间隔、PTS 连续性与两时钟偏差，不包含云端识别。
- `deviceRtc.eventTiming` + `mediaForwarding`：接收到入事件队列、入队到 Application 转发的耗时。
- `voiceDiagnostics.current.upstream`：转发到 Codex 的 PCM FIFO 大小与实际队列停留。
- `playbackGain`：云端原始 PCM 与源响度补偿后的累计 RMS/峰值、当前增益。
- `deviceRtc.downlinkPeakLimiter`：native48k直通/legacy浮点重采样、Opus编码前的最终保护。
- `deviceRtc.downlinkTrackEnergy`：实际送入设备编码器的 PCM；不是声压计或播放完成回执。

所有指标只暴露有界数值元数据，不保存音频、转写、凭据或 SDP。
峰值与 RMS 只能同位置、同采样和同窗口比较，不能把历史最大值与某个静音帧比较。
累积 RMS 包含获准转发的声学尾部，不能直接当作句内响度或 LUFS。

## 实时积压与活性

电脑上行保持连续 RTP 样本时间线。发送前将待处理 PCM 限制到最新 120 ms，
且丢弃在本地 FIFO 停留超过 120 ms 的旧样本；恢复时计入
`freshnessDroppedMs`/`freshnessRecoveryCount`，不能宣称这些样本无损到达。
它用于恢复实时性，不是调快云端 ASR，也不改变 Codex turn detection。
超过 1 秒的瞬时入队仍按过载处理，不无限分配内存。

设备连接建立后，连续超过 3 秒收不到源帧才报告断流；静音帧会续期。
心跳、统计和下行音频不续期，正常长会话没有固定结束时限。
后台结果交接和进度通知不在音频转发循环中等待，所有任务仍受停止生命周期管理。

## 历史候选v2的播放响度

上一候选 `maximum-speech-v2`：目标 RMS 12000，最多16倍（约+24 dB），
增益上升平滑约80 ms，每次最多以20 ms PCM为控制块，不增加等待缓冲。
低于噪声门限的块不放大；已足够响的源不因目标 RMS自动衰减。
22000以上采用对称软拐点压缩，渐近峰值上限29000。这会改变峰均比，
不承诺音色完全不变；它不是硬件增益，也不能无条件达到目标RMS。

旧版目标6500／4倍／300 ms，并按单个峰值压低整帧，短暂32767峰值可使
同块1000样本降到885。用户实际反馈仍小，因此旧版软件电平改善不算音量问题通过。
新版只对高幅样本压缩，不再让一个瞬态将正常语音一起压回去。

`playbackGain.profile`必须确认是上述新版，避免用源码变化冒充进程已生效。
`speechInputRms`/`speechOutputRms`仅统计超过噪声门限的控制块；
`compressedSamples`为软压缩样本数，不是整数削波数。
24→48 kHz重采样仍使用浮点，再整帧限峰到29000
并转换 s16le，避免整数重采样在后续保护前先削波。
这不保证 Opus 解码或真实扬声器绝无失真，也不是硬件音量控制。

硬件/系统音量应通过设备设置或 debug 固件的 `debug.audio.volume` 检查。
逻辑 100 不是寄存器回读，BSP 仍有防失真封顶；不能通过突破该上限解决源幅度问题。
JoyInside 与 RTC 的出口最终都调用 codec writer，目前没有证据证明 JoyInside 有独有归一化。

当时另用邻近Insta360 Wave USB麦克风统计真实机器人扬声器输出，
只保留电平元数据、不保存录音。两次请求同一句内容，但模型合成波形不同，
麦克风也可能自带处理；相对电平仅支持“这次实体出声更强”，
不能等同同源严格A/B、校准声压或用户认可的最大听感。

## 验收注意事项

真实识别验收应使用实体扬声器→机器人麦克风，不能用文本/PCM 注入替代。
分别记录声音开始、有效编号转写、句末与助手音频事件；一句里较早出现的编号，
可能在整句结束前就被识别，不能把它与整句末尾相减当作句末 ASR 延迟。
持续对话也可能先对问候回应，音频字节增长不是最终任务回复的归属证明。
电脑端直接语音尚需同句、同网络的独立对照，不能仅因产品名称相同就认定管线相同。

当前 Codex app-server 本机生成的 realtime/start 合同未提供通用 Realtime API 的
`turn_detection`/转写模型配置。不发送未经该协议证实的参数；参考
[官方 app-server 文档](https://learn.chatgpt.com/docs/app-server)。
