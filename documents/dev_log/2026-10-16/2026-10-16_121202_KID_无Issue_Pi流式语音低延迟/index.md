# Pi 流式语音与连续接话延迟

- 开始时间：2026-10-16 12:12:02 Asia/Shanghai；建档作者：KID；实施：Codex。
- 状态：本地实现、自动化回归与新版真实人声连续三轮验证完成，用户已确认收到回复；无声上传与 ASR 序列问题已补测修复。当前已暂停，麦克风关闭且无清理残留。Application 0.1.1 已另行上传并提交 Gitee 商店 PR #24，待审核；SDK / 固件源码仍未提交或发布。长期稳定性与声学时延统计未执行。
- 用户目标：减少“准备下一轮”的等待，Pi 可见正文按标点分段后边生成边播报，并保持暂停可取消。

## 背景与范围

现有 Application 每轮关闭 SDK 会话；固件每个 ASR/TTS 操作都断开 JoyInside。Pi 适配器只在 agent_settled 后返回完整回复。实测正常轮次在播报完成到再次聆听之间约 2.2 秒，随后出现 close 的 session_mismatch 与本地会话残留。

改动范围：SDK 会话释放一致性、Pi RPC 可见文本增量、有限容量断句/播报队列、Application 会话复用与状态展示；配套固件复用成功 TTS 的连接。保持半双工、设备端密钥与音频处理、Daemon 透明路由。ASR 后仍隔离原云端连接，避免自动 Agent 音频和迟到 ASR 混入下一次操作。

## 设计与验收

- close 明确拒绝 session_mismatch 时只退役该本地句柄，不取消设备上的其他会话；超时和其他拒绝仍保留重试能力。
- 读取 Pi text_delta/text_end/message_end；不读 thinking/toolcall；agent_settled 仍决定整轮成功。已经播出的正文如被改写，停止并报错。
- 句末标点优先、逗号等软断点有最小长度；无标点有长度与等待上限。TTS 严格串行，有界队列向 Pi 施加背压。
- 连续轮次复用会话，请求历史满时明确轮换；暂停、故障和退出清理全部后台任务与音频。
- 单测先确认旧实现失败，再实现并回归。固件以主机协议测试、PTL Debug 构建和 目标串口 设备验证补足 FreeRTOS/HAL 行为；首 PCM 提交与物理出声区分记录。

## TDD 与验证

- Red：新增用例在旧实现出现 9 项失败（无 on_text、无断句模块、close mismatch 不释放、多轮重开及未能先播第一句）。之后补充清理重试和并发 Pi 超时归因，两项再次明确失败。
- Green：截至 12:26，36 项 speech/Pi/断句/网页测试通过，另包含 Application transport 与 Daemon 路由合同的组合回归通过。目标 speech.py mypy、Python compileall、网页 JavaScript 语法检查通过。
- 主机固件测试先在“退休 TTS round 的迟到 ASR”断言失败，新增过滤后两套 CTest 通过。MSVC 报既有 strcpy 安全替代警告，非编译失败。
- 真实 Pi RPC：可见增量早于 agent_settled，text_end/message_end 没有重复输出；本次冷启动首段约 15.594 秒、settled 15.656 秒。此测试只验证真实 Pi 事件，未包含麦克风与扬声器，不能据此声称声学响应改善。
- 固件构建与 目标串口 验证进行中。不得将模拟测试记为物理麦克风/扬声器验证。

### 2026-10-16 12:31 Asia/Shanghai｜KID / Codex

- 新增提前启动 Pi 的合同，先明确 Red，再实现并重新回归：36 项相关测试通过。Application transport/Daemon 组合回归总计 60 项；Daemon 源码和路由均未修改。
- 同一真实 Pi 进程两次短回答首段分别为 5593ms、1453ms，完成分别为 5797ms、1531ms。结果说明短回答的正文输出窗口很短，主要等待在首段；不能把流式接入解释为所有请求都会显著变快。已将进程启动提前到开始对话，让初始化与设备建连、用户说话重叠。
- 首次固件构建在编译缓存阶段数分钟未输出新目标，停止该构建的进程树后重试。重试携带 IDF_CCACHE_ENABLE=0，但构建缓存仍显示 CCACHE_ENABLE=True；仅能确认重试后编译持续前进，不能宣称已实际禁用缓存。

## 关联历史

- 后续分发：[Pi 语音应用商店更新](../2026-10-16_143900_KID_无Issue_Pi语音应用商店更新/index.md)，用户另行授权将应用更新至 0.1.1 并投稿商店；本档案的 SDK / 固件实现并不因此自动提交、发布或合入。

- 补充与解决遗留问题：[启动超时与设备身份排查](../2026-10-16_112431_KID_无Issue_Pi语音启动超时/index.md)。原设备身份结论仍有效，本次进一步优化稳态延迟。
- 配套固件：[低延迟连接复用](../../../../../WatcheRobot_esp32/docs/development/2026-10-16/2026-10-16_121202_KID_无Issue_Pi流式语音低延迟/index.md)。

### 2026-10-16 12:46 Asia/Shanghai｜KID / Codex

- 补充空白分句与退出时设备释放失败的 Red/Green，最终相关用例 38 项通过；包含 Application transport、standalone 路由合同与 Daemon 路由的组合回归为 62 项通过（27.37 秒）。speech.py、agent.py、service.py、sentences.py 的 mypy 检查通过；Python 编译、网页 JavaScript 语法及 SDK diff 空白检查通过。
- 固件通过 PTL / Debug CLI 构建和项目 AppOnly 烧录流程安装至 目标串口 目标设备，OTA_1、NVS 与资源保留。新固件与实际运行 Application 已重新配对并加载当前源码，未修改 Daemon 路由。新版网页启动、连接并进入 listening，设备身份与目标一致，无启动错误。
- 真实设备探针由 Daemon 管理为独立 Application，仅使用注入 Device channel。连续两句 TTS、接着开麦并取消、关闭后重新开会话、真实 Pi 四句流式串行播报、最终清理均完成。该探针注入文本，physical_asr_input=false，不算新版真实人声端到端验收。
- 设备日志单次样本：第二句播放完成至下一次 Microphone ready 为 188ms；此前正常轮次日志约 2.2 秒。六次 TTS 操作至 First PCM submitted 为 399–431ms，属于 PCM 提交时刻，不代表物理首声。真实 Pi 首句 TTS 在整段 agent_settled 前提交，短回答输出窗口仍仅约 78ms；首段等待仍受 Pi / provider 影响。
- 本机临时证据位于 workspace 的 .codex/local/pi-speech-20261016：probe/result.json、device-latency-summary.json、latency-serial.log、real-pi-stream.jsonl、real-pi-warm.jsonl、streaming-ui.jpg。临时日志、截图、个人地址与固件备份不提交。README 已补齐现状架构与逻辑流水线图。
- 已邀请用户验证新版真实人声与接话感受；此前版本的人声验证不能替代本次。仍为半双工，不支持播报期间语音打断；未执行长期稳定性、声学时延或 ASR 准确率统计。

### 2026-10-16 12:51 Asia/Shanghai｜KID / Codex

- 浏览器开始状态已验证，但约四次无声超时重开后出现 microphone_upload_failed；此结果推翻“主要链路通过即可结束验证”的判断，继续排查而非将页面错误当作通过。
- 串口复测在开麦后约 721ms 重现，无底层 IO/partial-send 错误日志。对照 transport 的返回合同，设备服务错误地将明确未发送的 JI_RETRYABLE_NOT_SENT 视为致命错误。由固件补充单帧有界重试，SDK 不盲目重放识别请求，也不隐藏真实错误。

### 2026-10-16 13:02 Asia/Shanghai｜KID / Codex

- 上传修复后的设备经历至少三次无声重开未再报上传失败，但用户明确反馈“已经说了，但没有反应”，无识别文字进入 Application，本次新版人声验收失败。
- 定位到配套固件的当前 ASR 被 Agent start 误退役的序列问题，已用主机测试先复现再修正；原有迟到 TTS round 过滤仍通过。暂停网页对话，重新烧录后继续真人验证，不把成功开麦或成功上传当成 ASR / Pi / TTS 全链路通过。

## 变更记录

### 2026-10-16 13:13 Asia/Shanghai｜KID / Codex

- 第三轮真实语音工具查询也已完成，累计三轮。该轮 ASR 完成后首段正文 66391ms、首个 TTS 请求 66453ms、Pi 整轮完成 91500ms、播放完成 111922ms：首个 TTS 在 Pi 整轮完成前约 25 秒请求，属于实际流水线并行证据，不等同于声学首声测量。长工具查询的首段仍等待约 66 秒；用户说明理解直接使用 Pi 带来的速度限制。
- 页面最终为 paused、enabled=false、turns=3、error 为空、cleanup_pending=false；麦克风已关闭，Application 保持运行，可点击开始继续。此次未为了结束验证打断进行中的 Pi 请求。
- 已保存当前真实三轮对话的网页截图，视图停留在前两轮，避免在交付截图中展开后续私人消息摘要。SDK 与相关固件 diff 空白检查通过；临时证据、个人地址与截图继续留在忽略目录，未提交。

### 2026-10-16 13:11 Asia/Shanghai｜KID / Codex

- 配套固件最终构建并通过 AppOnly 烧录校验，6,192,000 字节，SHA256 `b0e97cd8ca25e8590dc14b7c4e6fc1ef073c6d0ebd15f7b9ff403cc1423d2648`。当前 Application 保持使用 SDK 注入的 Device channel，Daemon 与 STM32 未修改。
- 用户真实说话后收到回复，并明确反馈“这次有回复了”；随后另一轮真实语音也完成 ASR → Pi → 六段串行 TTS → 自动重开麦克风。页面累计完成两轮，没有错误或清理残留；用户已继续发起第三轮工具查询，该轮不计入此处已完成样本。
- 两次真实轮次的设备日志：最后一段专用 TTS 完成至下一次 Microphone ready 分别为 202ms（228637 → 228839）与 289ms（266263 → 266552）。此前正常轮次约 2.2 秒；这是两次小样本、设备日志时间，不能等同于整体对话或声学首声延迟。
- 第一轮自 ASR 完成至 Pi 首段为 2390ms；第二轮包含工具查询，首段为 12016ms，首个 TTS 请求 12063ms，Pi 完成 12438ms。第二轮首句在整段完成前约 375ms 提交；首段生成与工具执行仍是明显等待来源。第一轮三段、第二轮六段的播放顺序均与正文一致。
- 第一轮开麦上传期间出现 55 + 69 次 socket 等待，后续上传与最终识别成功，未重现 microphone_upload_failed；保留原帧的有限重试在真实链路中得到覆盖。当前 ASR final 亦未被先到的 Agent start 丢弃。
- 本轮仅追加已有实测记录和正式文档，未新增业务代码，因此不重新执行已经通过的 62 项 Python 回归与 2 套固件主机测试。证据：本机忽略目录中的 asr-order-serial.log、asr-order-manifest.json 和 Application JSONL 按 timestamp 排序的 13:08:53–13:10:07 记录。未执行长期稳定性与声学时延统计，半双工和内存/栈余量限制仍保留。

### 2026-10-16 12:12:02 Asia/Shanghai｜KID / Codex

- 对照运行日志、SDK/Pi/固件源码确认串行等待与会话残留问题，开始独立优化任务。
- 尚未修改业务实现；保留此前修复和其他任务的工作区改动。


## 2026-10-27｜KID / Codex｜配套交付

用户明确授权本 SDK 配套修改 commit、push 并合入 main；ESP32 配套目标为 codex/joyinside-integration-20261015。保留原 main 的超时及通话生命周期修复，以差量三方应用本地分句流式回复、会话复用支持、网页状态与测试。当前在最新 main 24e3823 独立工作树验证，历史实机结果不外推为本次版本已烧录。测试与提交信息继续追加。


本次源码正式交付与验证见[2026-10-27配套交付](../../2026-10-27/2026-10-27_KID_noIssue_speech-delivery/index.md)，历史正文保留。
