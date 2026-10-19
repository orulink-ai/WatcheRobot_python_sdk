# Pi 流式文本提前合成

- 开始：2026-10-18，Asia/Shanghai；作者 Mr-KID-github / Codex。
- 参与者：root、pi_stream_review（只读协议审查）、pi_async_tts（队列及后端 TDD）。
- 分支：codex/pi-voice-android-integration；状态：实现、TDD回归与真机播放通过，本地修改，未提交。
- 需求：用户要求继续接通 Pi 流式输出与 TTS，并按 TDD 实施。

## 目标、边界与验收

1. 在 Pi 完成生成前，短句即可后台合成；最终回复确认后复用已生成 PCM，继续按原有有界队列单流播放。
2. Pi RPC reader 不等待合成、缓存容量或播放门禁；文本及 PCM 均有上限，容量不足安全回退。
3. 工具、自动重试、正文改写、取消产生的候选不得误播；思考和工具参数永不进入 TTS。
4. voice_reply 仍必须实际播完才放行操作；普通聊天无固定确认语。
5. 保留 JoyInside 兼容路径、透明 Daemon、现有 Device channel；不修改固件、设备协议或用户凭据。

## 协议调查及取舍

本机 Pi 官方 json.md / rpc.md 指明 text_delta 是增量，text_end / message_end 为权威内容；agent_end 后仍可能自动恢复或重试，agent_settled 才没有后续自动工作。因此这次将文本生成与 TTS 合成重叠，但最终正文仍等 settled 校验后放行播放。不能把此优化描述为模型未生成完就保证可播的最终正文；后者需要额外显式 spoken commit 协议。

## TDD 与验证

- Red：11项初始测试全部失败（10项缺候选模块、1项缺 Pi on_event）；补service集成后11通过 / 3失败，确认缺真实事件接线。
- Green：接线后相关50项通过；进一步只读审查定位URL跨delta分段变化与慢取消阻塞reader，两项各自先复现失败后修复。
- 完整首轮：212通过 / 1失败（既有voice_reply取消测试触发并发关闭stdout reader）；新增并发close测试稳定复现后，给Pi请求reader加所有权、close串行取消并join后drain。最终相关 **214 passed in 19.32s**，1项既有AnyIO弃用警告；12项Node扩展、网页VM、compileall和diff检查通过。
- 队列子任务TDD：8项新测试先Red，再Green；补异步源显式关闭、gate期间预算约束后，对应定向126项通过（与214重叠，不累加）。
- 真实Pi＋豆包＋内存sink：通过，单流726830字节，preview复用；首delta3445ms、settled3477ms、TTS首包3978ms、首段完整4422ms、sink开始4423ms。此次模型正文集中返回，生成与合成仅有很短重叠，不能声称普遍明显加速；这项不是真实扬声器验收。
- 真实Pi＋豆包＋机器人：使用临时Daemon管理的同ID Application，固定文本替代recognize以驱动正式ConversationService，其余Pi/豆包/Device channel/动作表情/清理均使用真实实现。1轮成功，516056字节PCM，设备连续流完成，音频/动作清理确认；不把此测试描述为新ASR录音验收。详见 [脱敏设备证据](hardware-validation.json)。
- 本轮真实模型文本集中返回，预合成准备到最终commit约164ms，首段合成请求约提前158ms；没有做同文本AB性能实验，不宣称普遍明显加速。设备完成耗时12051ms，包含约10.75秒朗读；该耗时不是声学首字延迟。

## 实现

- agent.py 提供可选RPC事件观察回调，返回值仍以成功settled为准；并发关闭先取消并join唯一reader再drain。
- streaming_text.py：有界异步文本源、内容块重建、权威文本及分段边界比对、后台失效回收与最终commit门禁。最多一个活跃候选；旧候选尚未退出时不创建更多合成任务。
- tts_pipeline.py / speech_backends.py：支持AsyncIterable文本和before_play，第一段PCM ready后等待许可，生产者按预算继续。许可打开前不启动设备流。
- service.py：正式豆包路径接入候选，握手播报前确认旧任务退出；正常聊天/最终结果仍只播放真实settled内容；JoyInside兼容路径保留。
- 测试覆盖提前合成、最终门禁、重试/工具/改写丢弃、跨delta网址、多文本块、无标点尾句、文本/PCM预算、慢取消不阻塞reader、暂停资源清理、操作前实际播放屏障与并发close。

## 历史关联

补充 [TTS 持续生成与单流播放](../2026-10-18_105058_Mr-KID-github_无Issue_TTS持续生成与单流播放/index.md)：前序优化从完整最终文本开始，本次提前合成至 Pi 生成期间；连续播放、预算和最终结果确认约束仍保留。

## 遗留与限制

单句云端 PCM 仍完整收集后入队；不声称声学零间隙或支持抢话。实现阶段未自动提交；后续用户已明确授权本地 commit，不包含 push 或 PR。

## 串口连接恢复

用户在验证期间反馈Desktop Link已打开却连不上，并要求检查串口。CH342双串口正常，机器人运行sdk.control.app、Wi-Fi已连接且信号较强、绑定目标与Mac相符；Daemon原为idle/offline。串口只读取得当前配对码后，通过既有Daemon配对管理接口恢复python_sdk连接，未烧录、未修改驱动、未通过UART发送音频业务。原会话未恢复的最初触发因素无法从现有日志追溯，未发现SDK不匹配证据。串口已释放。

## 交付状态

真机临时驱动完成后恢复源码目录中的正式Application；未提交、未推送。正式应用默认暂停，供用户点击开始试听。当前Mac磁盘空间很少，未删除用户文件或缓存；未以环境空间不足跳过上述测试。

最终查询确认：正式Application running，页面paused / connected=true / backend=doubao / error为空，允许切换后端，说明资源没有遗留占用。未把本机动态端口和临时驱动源码纳入仓库。

## 可复核命令

SDK虚拟环境Python 3.11，仓库根目录：

```text
python -m pytest -o addopts='' -q tests/test_pi_streaming_text.py tests/test_pi_tts_pipeline.py tests/test_pi_speech_latency.py tests/test_pi_speech_application.py tests/test_pi_voice_reply.py tests/test_pi_speech_orchestration.py tests/test_pi_speech_web.py tests/test_pi_speech_backends.py tests/test_pi_speech_behavior.py tests/test_pi_speech_chunks.py tests/test_pi_speech_pairing.py tests/test_pi_speech_preferences.py
node --test tests/web/pi_voice_extension.cjs
node tests/web/pi_speech_ui.cjs
python -m compileall -q examples/joyinside_pi
git diff --check
```

初始Red与后续两项审查缺陷、并发close的Red/Green均使用同一新增测试文件定向执行；失败不是事后补测。真实部署使用Daemon原有Application选择/启动管理接口，业务始终经正式Application Device channel，未修改路由。

## 2026-10-18 11:54:53 Asia/Shanghai｜Mr-KID-github / Codex｜提交前核对

- 用户要求把本次流式文本提前合成优化commit；本次仅执行本地提交，SDK源码与档案一同纳入，随后在Workspace单独更新SDK gitlink。
- 初始核对12个文件均属于本任务：Pi事件和关闭所有权、候选短句及播放门禁、异步TTS队列、正式对话接线、三份测试、README及双向历史关联、脱敏真机证据；后续复跑增加一份既有网页测试的竞态修正，共13文件。
- 原有214项Python、12项Node扩展、页面VM与真机证据对应当前实现；本轮没有改动业务代码，不新增TDD案例，不再次播放或烧录。
- SDK没有其他无关改动，提交前diff检查通过；不纳入凭据、个人端口、临时测试驱动或本机缓存。生成与合成的实测重叠有限，性能及最终确认边界保持原文说明。

### 提交复跑补充

- 提交前重新执行同一214项回归，结果213通过 / 1失败，67.26秒：`test_one_start_runs_multiple_turns_and_pause_drains_microphone` 观察到session.closed后立刻断言phase，动作清理尚未完成，实际仍为stopping。生产合同允许音频关闭后继续确认动作清理，测试不能把音频关闭当完整暂停的同义信号。
- 保留原有资源释放及phase断言，将等待条件改为音频已关闭且phase=paused；不修改业务代码，不用任意sleep掩盖时序。此为本轮真实Red，随后执行完整相关回归验证，结果在下方记录。
- Node扩展12项、网页VM与敏感资料扫描复跑通过；本机路径、个人端口或令牌未进入提交范围。
- Green：完整相关回归 **214 passed in 45.85s**，只有既有AnyIO弃用警告；13文件暂存检查通过，包含测试等待完整暂停状态的修正。业务源码与已部署真机版本一致，本轮不重复烧录、播放或部署。
