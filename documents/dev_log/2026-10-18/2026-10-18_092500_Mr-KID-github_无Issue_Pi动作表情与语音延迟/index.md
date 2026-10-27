# Pi 动作表情与语音延迟

## 基本信息
- 创建：2026-10-18 09:25 Asia/Shanghai；建档作者：Mr-KID-github；实施：Codex/root、pi_expression_plan、pi_latency_plan。
- 类型：功能、性能改进；状态：已本地实现、部署并完成定向验证；未提交、未推送。
- 模块：examples/joyinside_pi、配套 ESP32 本地 PCM audio-follow。

## 背景、目标与边界
用户要求 Pi＋豆包 ASR/TTS 保留现有手机 MCP，并具备 Codex Application 的整套情绪表情和动作，改善响应慢。Application 仍使用 Daemon 注入 Device channel，不改变 Daemon 内容无关路由，不使用第二连接。

验收：七套 chat_v1 动作和情绪语义；聆听、思考、回答和暂停生命周期；动作不阻塞表情和语音；暂停/失败清理所有权；Pi 提前启动、最终结算后按句有界预取；显示阶段耗时；自动化回归及真实设备验证，缺失项如实保留。

## 调查与决策
- 既有真实日志：首轮 ASR 完成到 Pi 结束 11.186 秒，下一轮 1.222 秒，尚不能细分进程/MCP/模型时间；录音到 ASR 包含用户说话与800ms静音；播放总时长包含实际朗读。
- Pi RPC 必须保留 agent_settled 门禁，不能播报可能被工具调用或恢复覆盖的中间回答。
- play_pcm 在开始前需要完整长度及 SHA256，因此采用最终答案分句＋下一句预取，不声称当前协议能直接流播云端PCM。
- Codex motion_assets 与固件官方 chat_v1 七轨迹一致；应用只选择官方ID，无须复制完整Codex/RTC应用。
- 已确认 audio_follow 当前只接RTC PCM，本地PCM无法驱动嘴型；需配套固件修改和实机烧录验证，应用不能伪造已实现。

## 关联历史
- 补充：[豆包后端切换](../2026-10-18_090955_Mr-KID-github_无Issue_Pi豆包语音后端切换/index.md)：保留真实硬件已闭环结论，本任务补动作及延迟。
- 补充：[播放嘴型情绪合同](../../2026-10-13/2026-10-13_013655_KID_无Issue_播放嘴型情绪合同/index.md)：扩展非RTC路径，不改变情绪0–6公开合同。

## TDD 与验证
待实施后追加真实 Red/Green、回归及设备结果。

## 遗留事项
待核实当前机器动作资源、实际眼形/运动及本地PCM嘴型；无USB时不能声称烧录完成。

## 2026-10-18 09:43 Asia/Shanghai｜Mr-KID-github / Codex｜应用实现及第一轮硬件联测

### 实际变更
- 新behavior模块分离本地语义、表情worker和完整动作worker；七个官方动作ID。显式情绪跨Pi等待保留并在播报时保持四秒；新轮、否定清除旧请求。
- 聆听前取消并drain上一段运动、确认stop；暂停/出错先音频清理再动作/表情。失联/换机不能向新设备清理旧owner，未确认释放时阻止重开及后端切换。
- Pi在开始收音时预启动，跨正常轮次/暂停复用；保留agent_settled结算。最终回复按标点/80字符/字节上限分句，容量一PCM预取、失败取消等待。
- 豆包仅TTS复用有界HTTP连接池（60秒空闲），不复用设备/ASR会话；每轮读私有配置，shutdown确认会话关闭后关pool，失败保留owner。性能事件不含文本或凭据。
- 网页新增实际指令状态、七情绪及能力/嘴型提示，显示录音识别、Pi、合成播报各阶段耗时；初次未探测为待确认，不误报不支持。动作完成不等于机械到位。
- 提示词说明机器人已有七种表现能力和简短回答原则，不把表情任务交给手机工具。文档补新local_pcm能力；Daemon源码未因本任务新增业务分支。

### TDD
- root编排2项先因缺behavior注入接口失败，整合后通过；模块17项含缺cap、独立worker、故障清理、进入聆听停止、显式请求等待及新mouth cap正反检测，各批先红后绿。
- 分句/流水线最初7项红，随后追加2项边界；pool4项先红（无shutdown/会关闭client）后绿。
- 网页VM先因缺状态区失败；后补未探测能力不报不可用用例先红后绿。
- 最终回归：Pi backend/latency/preferences/web/pairing/application/orchestration/behavior、speech、voice device/runtime/asr/providers共161 passed，1项既有Starlette AnyIO弃用warning。Node VM、node --check、compileall、git diff --check通过。

### 实际硬件验证（刷嘴型固件前）
通过Daemon管理临时Application运行，未建立第二条设备连接：七个chat_v1动作均完成Job，表情命令无降级，退出cleanup_pending=false。真实Pi回复1679ms，豆包两句播放完成；speech_ms=11726包含朗读。首句合成首包2965ms/完成4916ms，第二句1088ms/1356ms，第二句合成在第一句播放3597ms期间已结束，证明流水线重叠；不同文本/模型和网络未做对照实验，不能声称等比例提速。当前仍明显受首句TTS合成耗时影响。

新网页实际开始进入listening，显示表情/动作可用、旧固件嘴型需更新；实际暂停后资源关闭、保持机器人连接。此为控制链路和telemetry证据，不是机械位置/LCD照片或声学延迟测量。

### 配套固件与审查
固件档案：[本地PCM嘴型接入](../../../../../WatcheRobot_esp32/docs/development/2026-10-18/2026-10-18_092808_Mr-KID-github_无Issue_Pi本地PCM嘴型接入/index.md)。35项相关Host测试及IDF6.0.2构建通过；额外既存WiFi字符串静态测试1项失败已在固件档案记录。独立embedded-pr-robustness-review未发现Blocker/High/Medium代码问题，明确保留实机播放、停止、跨源和断连验证缺口。

用户已连接USB，确认CH342双串口已出现；当前目标芯片MAC推导的canonical设备ID与LAN设备一致，实际分区表匹配。根任务授权原固件agent按项目wrapper AppOnly烧录，保留NVS/agentcfg，备份仅外部私有runtime目录。烧录及嘴型结果待后续追加，不提前声明完成。

## 2026-10-18 09:55 Asia/Shanghai｜Mr-KID-github / Codex｜部署与最终验证

- **状态：应用功能及配套固件已本地实现并部署，未commit/push/合入。** 七套动作命令、自动情绪、Pi＋豆包分句播报、本地PCM嘴型、暂停清理及页面联动已验证。实体观感/声学延迟、RTC跨源实机切换和长期稳定性未统计。
- USB目标canonical身份确认与原LAN设备一致；项目wrapper AppOnly写入6183792bytes、写后hash通过，保留NVS/agentcfg；JoyInside持久配置只读确认存在。镜像sha256 `cf1976c99f91093fe820ada0d3adb3ff2ec25b13946ed210d0d5858e56a22a19`，启动观察无panic/assert/stack overflow。
- 烧后绑定自动恢复在launcher返回reunite_unavailable；曾误选同名的旧client.app网络入口，SDK ready刷新超时，未计作功能通过。对照源码改用launcher真实入口sdk.control.app，通过UART仅打开连接入口/读取当前配对码，再由Daemon管理pair建立python_sdk连接；没有从UART发送业务表情、音频或动作命令。随后Managed Application新ready确认local_pcm capability。此为恢复操作纠正，不改变Daemon路由。
- 新固件真实Pi最终回复→豆包两句完整播放→真实PCM嘴型：Pi2573ms，合成与朗读10898ms；首句合成2851ms、第二句772ms并与首句播放重叠。此结果受文本和网络影响，不与前次不同文本结果作等比例性能结论。
- fresh telemetry：pcm_frames最高82、speaking_frames128、mouth_level最高472、update_errors=0；正常结束/中途取消后tts_playback=false、mouth_level=0。退出后audio_follow=false、cleanup_pending=false。TTS新轮次HTTPclient身份复用通过。证据：[部署前动作联测](pre-firmware-integration.json)、[新固件嘴型与取消联测](local-pcm-integration.json)。计数不能替代LCD照片、声音和机械到位反馈。
- 后续补否定语义12条参数化用例（不会/不要生气、不再感到难过、后续明确分句情绪），先7项失败、修复后全部29行为测试通过。最终完整相关回归 **173 passed, 1 existing Starlette warning**；此前相关固件Host35项通过，构建成功；未将已记录的无关WiFi静态失败说成通过。
- 正式Pi Application经Daemon恢复，仍选豆包；网页实际开始进入聆听，再暂停，等待真实音频及动作/表情清理。网页新能力状态包含本地PCM嘴型。当前待机、机器人保持连接，不持续开麦。

### 保留的验证边界
- 没有本轮人工声学/LCD确认，未声称采样级音画同步或舵机反馈。
- 当前为半双工，播报完成再开麦；本任务没有增加RTC抢话。
- 首句仍需完整合成后再下发，云端首句等待仍有秒级开销；未修改音频传输长度/校验和合同，也未通过关闭MCP或提前播放Pi工具中间回答制造低延迟。

## 后续变更
2026-10-18｜Mr-KID-github｜解决连续识别后续问题：[Pi识别握手期间麦克风缓存溢出](../2026-10-18_100400_Mr-KID-github_无Issue_Pi识别握手期间麦克风缓存溢出/index.md)。旧动作/嘴型/播报证据保持，新增慢云握手导致采音缓存无人消费的修复。

## 后续变更
2026-10-18｜Mr-KID-github｜补充/部分替代：[首段短切与先回应后执行](../2026-10-18_102322_Mr-KID-github_无Issue_Pi首段短切与先回应后执行/index.md)。首段80字符策略调整为约28，增加确认播放完成后才提交Pi任务，原流水线及所有权保持。


## 后续变更
2026-10-27｜KID / Codex｜[PCM配套合入](../../2026-10-27/2026-10-27_181300_KID_无Issue_PCM配套合入/index.md)：在最新目标分支整合并重新回归；原有硬件记录保留为历史。
