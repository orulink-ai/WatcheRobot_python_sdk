# Pi 识别握手期间麦克风缓存溢出

- 建档：2026-10-18 10:04 Asia/Shanghai；作者：Mr-KID-github；参与：Codex/root、asr_buffer_audit。
- 状态：本地修复、回归及真机定向验证通过；已重启正式Application部署，未提交/推送。
- 需求：用户在实际连续对话时出现“ASR 麦克风丢帧或解码失败”。

## 目标与边界
修正Application持续采音被云端ASR握手/上传反压阻塞的问题，保留无语音不调用云、VAD、最大单轮限制、暂停/失败清理所有权，不改Daemon透明路由，不仅增大SDK底层32帧缓存、不掩盖真实损坏音频。区分缓存溢出和解码失败计数，补确定性回归及真实硬件验证。

## 调查证据
- 正式Application日志：前一轮识别4042ms、Pi1315ms、合成播报5645ms且播放完成；下一轮09:57:02开始收音，09:57:08报合并错误。异常来自SDKVoiceDevice.utterance的dropped_frames/decode_failures合并检查，旧日志未保存各项计数，不能仅由此断言现场一定是解码失败或Wi-Fi丢包。
- media.MicrophoneSession.dropped_frames在本地queue.Full时增加，覆盖最旧PCM；它不是网络sequence-gap统计。协商Opus60ms，32帧约1.92秒。
- DoubaoSession检测首PCM后调用StreamingASR.transcribe；该provider先等TLS/WebSocket及服务初始response才开始消费frames，故握手期间SDK缓存无人读取。发送背压也有同类风险。
- 子agent使用真实SDKVoiceDevice＋MicrophoneSession＋DoubaoSession内存复现：模拟握手期间33个60ms帧进入32帧队列，得到同样异常，dropped_frames=1、decode_failures=0、close已确认。

## 方案
应用层持续采音producer与ASR上传消费者分离，按最大单轮PCM字节预算有界缓冲；source由单一producer所有，取消/失败join后确认释放。真实丢帧/解码错误仍终止本轮，但显示各自计数和类别。

## 关联历史
解决后续问题：[Pi动作表情与语音延迟](../2026-10-18_092500_Mr-KID-github_无Issue_Pi动作表情与语音延迟/index.md)。保留上一轮动作/嘴型/播报验证，补充其尚未暴露的慢ASR握手背压缺陷。

## TDD与验证
待追加真实Red/Green、回归与硬件证据。

## 实际变更与 TDD
- Application新增单一采音owner，首帧检测、持续读取/VAD和有界PCM缓冲不受云端握手/发送背压影响；只有采音producer关闭完整、上传读取到EOF才接受final。source异常会中止挂住的握手。
- 保持最大单轮音频976000字节限制，不调大底层32帧队列，不改变协议/固件/Daemon路由。
- 取消/ASR失败会cancel并join采音producer，关麦失败保留原owner；source open/close逃逸TimeoutError转设备VoiceError，避免被当成普通无语音静默重试。
- SDK麦克风真实错误分为缓存溢出与解码失败，并附dropped_frames/decode_failures计数，不把两种故障混成同一无细节提示。
- Red：新增真实MicrophoneSession 32帧缓存测试在慢握手、慢发送时原实现各丢22帧、解码0；原代码还会接受未完整消费音频的early final。source open/close TimeoutError旧行为分类错误。根任务3项诊断用例原错误无类别/计数失败。
- Green：新增11个backend用例覆盖以上场景、采音/握手/上传三处取消、云握手异常和关闭ACK失败重试；相同慢握手/慢发送测试音频逐字节完整、0丢帧。诊断3项通过。
- 最终相关回归187 passed、1项既有Starlette/AnyIO弃用warning。范围Pi所有语音/行为/配对/网页/编排及voice device/runtime/asr/providers；git diff --check通过。

## 真实硬件与云端验证
- 通过Daemon管理临时Application，Device channel使用实际机器人16k Opus→PCM，豆包真实StreamingASR；macOS本地Tingting作为外部声学测试音源，不改变正式应用机器人input/output链路。
- 每轮人为在ASR启动前增加3秒等待（超过底层32帧约1.92秒缓存），连续三轮完整识别“你好，这是录音测试。”；PCM97920/99840/97920字节，dropped_frames=0、decode_failures=0，逐轮关麦确认。
- 三轮后机器人实际TTS playback完成，最终mic_released=true。具体证据：[真机验证](hardware-validation.json)。这些耗时包含人为三秒延迟、说话/VAD和云端，不是生产延迟基准。
- 第一轮长声学测试句未完整匹配预期（识别非空9字符），但0丢帧/0解码且释放成功，未把该轮计入完整识别验收。短固定句重测三轮均完整，未更改生产VAD阈值。声学识别准确率统计仍不在本次故障修复验收范围。
- 正式Pi Application已恢复，豆包模式、机器人连接，默认待机；不需要重新烧录固件。不会用忽略真实音频损坏或提前播放Pi回复来掩盖错误。

## 验证边界
旧现场日志没有保存原始两项计数，无法事后证明那一轮一定不是解码故障。本次确定性复现并修复了会触发相同报错的云握手背压缺陷；今后真实解码失败或采音故障仍会停止并给出独立计数。没有进行长时间网络拥塞/识别准确率统计，已有动作/嘴型能力保持。
