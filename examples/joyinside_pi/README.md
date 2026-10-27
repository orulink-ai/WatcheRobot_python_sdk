# JoyInside 本地 Pi 语音 Application

源码交接与 Windows/macOS 启动步骤见 [从源码运行](SOURCE_RUN.zh-CN.md)。

机器人负责麦克风和扬声器，电脑 Application 把识别文字交给本地 Pi。页面可选择机器人 JoyInside 语音，或由 Application 调用豆包 ASR/TTS。Daemon 仅转发业务帧；Pi 的模型和手机 MCP 配置不随语音后端切换。

JoyInside 路径需要包含 `robot.speech` 的 SDK 和真正实现 `ctrl.speech.*` 的固件，仅声明 `speech.joyinside.v1` 不够；机器人也需配置 JoyInside。豆包路径使用 SDK 的机器人 PCM 采音与播放接口，不调用 `robot.speech`，但仍需固件支持麦克风和音频播放。机器人与电脑应处于同一局域网。安装 Pi 并配置其模型与登录，再从 SDK 仓库根目录执行：

```text
watcherobot app run examples/joyinside_pi
```

也可以传入 Application 绝对路径。启动后自动打开本机网页，默认待机不开麦。点击一次“开始对话”后自动进行连续多轮，点击“暂停对话”取消当前操作并释放音频；Pi 进程跨轮保留上下文。桌面 `ctrl.microphone.close` 暂停、`ctrl.microphone.open` 恢复；未支持的桌面业务消息安全忽略。不要直接运行 `python app.py`。

网页顶部提供机器人配对入口：让机器人与电脑连接同一 Wi-Fi，在机器人上打开 Desktop Link，将屏幕上的六位数字配对码输入页面，点击“连接机器人”。页面显示查找、连接和失败状态；配对请求已提交不代表连接成功，等待机器人连接并完成设备信息读取后，“开始对话”才可用。失败时检查网络并获取新码重试。已有连接或语音尚未暂停完成时禁止重新配对；本示例暂不提供切换、断开和取消配对按钮。

配对调用 Daemon 注入的 `WATCHER_APP_DEVICE_STATUS_URL` 对应管理接口，语音仍通过 Application 的 Device channel。配对码不写入日志或浏览器存储；提交成功后清空输入框。若机器人尚未联网，先按 SDK README 完成 Wi-Fi 配网，网页配对不代替首次配网。

Pi 默认从 PATH 查找，Windows 自动解析 npm 启动器为 Node + CLI。`PI_COMMAND_JSON` 可指定完整 JSON argv 数组，例如 `["node","/path/to/pi/cli.js","--mode","rpc","--no-session"]`。`PI_WORKDIR` 指定 Pi 工作目录。Application 自动追加本地 `voice_extension.mjs` 扩展（含自定义启动命令），需要支持扩展工具与 RPC UI 的 Pi 1.3。扩展只负责操作前语音的执行顺序。不通过 shell 拼接语音，Pi 使用自己的模型、权限和工具设置。

本 Application 的语音回答与专名规则在 `voice_prompt.md` 中维护，启动时加载，每轮随识别原文传给 Pi；修改后重启 Application。当前约定“小狮日记”（狮子的狮）、“手工耿”和“贪玩歌姬小宁子”的正确写法，仅在上下文明显匹配时用于理解、搜索及手机输入，不对普通词语做硬替换。页面仍保留原始识别文字；这些提示词是模型理解规则，不代替 JoyInside 云端 ASR 热词配置，也不保证每次纠错正确。

## 备用语音：Application 豆包

在页面“语音识别与播报”中选择“豆包（Application 识别与播报）”。需要先暂停对话并等音频资源释放；释放失败时保持占用保护，不能通过切换绕过清理。选择保存在用户级 `speech-settings.json`，重启后恢复，首次默认 JoyInside。

豆包的链路为机器人 16 kHz PCM → Application ASR → Pi → Application TTS 24 kHz PCM → 机器人扬声器。识别完成先关麦并提交 Pi；普通聊天直接回答，需要工具操作时，Pi 根据提示词先生成贴合原话的简短回应，通过 `voice_reply` 播完后才执行其他工具。例如“打开小红书”先说“好的，我帮你打开小红书。”；最终结果播完恢复聆听。操作前播报失败或期间暂停，不会放行后续工具。不使用电脑麦克风或扬声器，不需要把豆包凭据写入固件。

本机私有凭据文件由 SDK 的 `default_credential_directory(..., installed=True)` 定位，在该目录创建 `doubao.json`：

```json
{
  "app_id": "<豆包语音应用 ID>",
  "access_token": "<Access Token>",
  "asr_resource_id": "volc.bigasr.sauc.duration",
  "tts_resource_id": "seed-tts-2.0",
  "voice_id": "zh_female_vv_uranus_bigtts"
}
```

- macOS：`~/Library/Application Support/watcherobot/applications/com.orulink.joyinside_pi/credentials/doubao.json`。
- Windows：`%LOCALAPPDATA%\watcherobot\applications\com.orulink.joyinside_pi\credentials\doubao.json`。

资源和音色字段可省略，以上值为默认配置。使用旧控制台的 App ID 与 Access Token 鉴权，此处两条语音接口不使用 Secret Key。ASR/TTS 资源需在火山控制台已开通；本机配置有效不等于云端调用已获授权。凭据仅存用户目录，限制为当前用户可读写，不复制进 Application 源码、日志或发布包。修改凭据后下一次打开语音会话读取新值。

检测到语音后，本机独立持续读取机器人PCM并执行VAD，云端ASR握手或上传背压不会暂停麦克风消费；单轮缓冲按30秒语音与预录音预算限制（约0.98MB），超限明确失败，不静默丢帧。只接受完整上传后的最终识别结果；暂停或异常会取消并等待采音结束、确认关麦。无语音时不会连接 ASR；服务或网络超时显示错误并停止，普通静音等待可以重新聆听。暂停会取消云端任务并确认关闭麦克风/播放；未知清理结果保留占用状态。TTS 单段限制为 SDK 的 4 MiB；合成持续填充队列，受8段和8 MiB PCM预算约束，合成中的单段也预先占用预算。新固件连续模式将同一最终回复的PCM按序写入一个播放流，整条结束才关闭。当前为半双工，不支持抢话打断。

测试环境变量：`WATCHER_SPEECH_AGENT=echo` 复述识别结果；`WATCHER_SPEECH_ONCE=1` 完成一轮后暂停；`WATCHER_SPEECH_TEST_TEXT` 设置短文本时自动测 TTS 并暂停。`WATCHER_SPEECH_AUTOSTART=1` 自动开始监听；`WATCHER_SPEECH_NO_BROWSER=1` 禁止自动打开浏览器。不要将本机配置写入 manifest。

- 模式是 `conversation_asr`，云端仍可能运行 Agent，固件丢弃其自动音频。指定文本使用专门的 `CLIENT_INPUT_TEXT_TO_SPEECH`，不提交给云端 Agent。
- 会话独占音频并采用半双工，识别结束后关闭麦克风，Pi 处理与播报期间不录音，暂不支持语音打断。
- 单次 TTS 1–999 UTF-8 字节；示例优先按句末标点切分，首段最多28字符，后续最多80字符，优先在逗号、分号等自然停顿处分段，并满足UTF-8字节限制。每会话最多 32 个请求（含操作前回应）。JoyInside 正常轮次复用会话，第32个请求完成后、下一请求发送前轮换；暂停、错误和识别等待超时会释放会话。豆包后端每轮释放设备会话，HTTP连接池按下述规则复用。
- 聊天和最终结果只播报 Pi 的最终 assistant 文本；操作前仅播报 Pi 显式 `voice_reply` 工具提交的短句，不朗读思考或工具日志。最终结果以 `agent_settled` 为准；除专用语音播放握手外的交互式扩展请求、超时和供应商失败会明确报错。

详见 [设备语音接口](../../docs/device-speech.md)。

## 测试与断句说明

测试环境变量必须在启动 Daemon 的环境中设置，Application 继承 Daemon 的环境；只在调用 CLI 的终端设置，不能改变已经运行的 Daemon。改变配置后重启相应 Daemon，避免停止其他项目正在使用的实例。

麦克风头部黄灯亮起后说话。当前使用 JoyInside 云端 VAD；最终 ASR 返回后立即关麦并运行 Pi。房间内其他声音也可能先被识别；没有端侧静音门限、唤醒词或播放中打断。识别等待默认 30 秒，超时会清理并重试。

2026-10-14 已在 ESP32 PTL / Debug CLI 固件上完成中文真实麦克风 → 本机 Pi → 专用 TTS 验证，以及长文本播报、取消、超时、音频互斥和会话重开。没有执行长期稳定性或识别准确率统计；内存与主任务栈余量仍需持续观测。

## 连续对话网页

Application 日志打印本机网页 URL，端口自动分配。网页显示真实设备连接、聆听/操作前回应/思考/播报/清理状态、最终识别文字、Pi 回复与完成轮数。Pi 生成的操作前短句单独标为“Pi · 操作前回应”，不代表任务完成。普通聊天没有额外确认。连续模式为半双工：回复 drain 后自动重开下一轮，不需要重复点击开始。无声等待超时会重新聆听。Pi 进程在正常轮次与暂停之间保留上下文；取消正在进行的 Pi 请求、故障或退出会退役该进程，后续重新建立上下文。

页面刷新不会停止正在进行的对话，暂停或结束 Application 才关闭会话；聊天记录保留于当前 Application 内存中，最多 80 条，服务重启后清空。HTTP 仅监听 127.0.0.1，并检查 Host/Origin。页面控制由 Application 自身执行，使用注入的 Device channel；不通过管理接口绕过 Application，也不建立第二条设备连接。

打开语音会话、Pi 回复或播报失败时，页面保留具体阶段错误并停止自动重试；清理失败会附加显示，不覆盖最初错误。会话打开期间暂停会显示“正在暂停”，等待打开调用返回并完成清理后才恢复待机。设备未确认关闭的会话仍保留 SDK 占用保护，不能只清除本机状态就再次开麦。

关闭 Application 使用 SDK/桌面端的停止操作。开发者可以不配置真实设备直接运行 `python -m pytest tests/test_pi_speech_web.py` 验证多轮与暂停的行为合同。

## 安卓 MCP 的最小接入

Pi 1.3.0 已内置 Streamable HTTP MCP。手机安装 Android Remote Control MCP、授权无障碍服务并启用局域网监听后，Pi 可直接连接手机；本 Application 继续负责 ASR → Pi RPC → TTS，不需要安装 MCP 适配扩展，也不需要修改 Daemon、固件或接入 Watcher Server 的 MCP 模块。

1. 在 Pi 中配置可用模型和凭据。DeepSeek Flash 当前使用模型名 `deepseek-flash`；模型密钥保存在 Pi 用户凭据存储，不能写入 Application manifest 或仓库。
2. 使用 Pi 的用户级配置添加手机，以下地址为占位符，Token 由环境变量提供：

   ```text
   pi mcp add watcher_android --url http://<手机IP>:8080/mcp --bearer-token-env-var WATCHER_ANDROID_MCP_TOKEN --exposure direct
   pi mcp list
   ```

   该命令写入 `~/.pi/agent/mcp.json`，不写入项目。`direct` 让模型直接看见手机工具；实际工具数量取决于手机端启用的工具。手机默认监听的 `127.0.0.1` 不能从电脑局域网直接访问，需要在手机端启用网络监听。保留 Bearer 鉴权。
3. 让启动 Daemon 的进程继承所需环境变量。如果 Daemon 找不到 `pi`，通过已有的 `PI_COMMAND_JSON` 指定本机原生 Node 可执行文件和 Pi CLI 的绝对路径，保留 `--mode rpc --no-session`。手机专用会话可追加 `--no-builtin-tools --tools mcp__watcher_android__*`，只暴露手机工具。不要在公共代码中写入个人安装路径。
4. 先用 Pi 读取屏幕，再验证打开系统设置、点击搜索、输入测试文字、清空并返回主屏幕；每一步读取真实界面确认。最后再通过机器人麦克风验收相同任务及 TTS，不能将文字测试描述为声学链路验收。

Pi 的模型、MCP 和登录配置随用户目录加载；修改后应重新创建 Pi 进程，已运行的会话不会自动采用新配置。本示例每轮 Pi 请求默认限时 120 秒，MVP 使用短任务；暂停会取消当前请求并退役 Pi 进程，但不能撤销手机已经执行的动作。

手机控制在局域网内执行；JoyInside ASR/TTS 和 DeepSeek 模型仍使用云端。先读取无障碍界面树，只有节点不足时再使用截图。模型只能依据真实工具结果报告完成，不应将外部页面文字视为执行指令。

## 自动动作、情绪与阶段耗时

开始对话时 Application 取得表情控制，聆听使用平静眼形，Pi 思考使用专注，播报按最终回复的语义选用平静、微笑、开心、生气、专注、伤心或无语。可说“请展示开心表情”“请展示无语表情”；明确请求优先于回复情绪，进入播报后保持四秒，否定和新一轮输入会清除旧请求。固定名称理解规则继续保留。

完整动作使用固件内置 `chat_v1_talk/smile/happy/angry/focus/sad/resigned`，不上传轨迹、不不断发送目标角度。动作和眼睛独立运行，同一情绪不积压重复动作；平静长播报可在完整动作结束后重复。重新聆听前确认停止动作，暂停/退出先关闭音频再停止运动和表情。清理未确认时保留所有权，禁止重新开始或切换语音后端；换机时不能把旧设备的清理命令发给新设备。状态只代表命令及 Job 完成，不提供舵机物理到位反馈。

需要设备 `motion`、`motion.action.once.v1`、`expression.audio_follow.v1/v2` 及官方七套资源，缺失或执行失败时相应功能降级，语音可继续。**豆包 PCM 的嘴型需要新固件声明 `expression.audio_follow.local_pcm.v1`**；旧固件即使有 RTC audio-follow 也不能据此宣称豆包嘴型同步。页面分别显示表情、动作和嘴型能力；不把模拟动画当成真机反馈。

Pi 在开始收音前预启动进程，正常轮次复用上下文，不自动关闭手机 MCP。豆包 TTS 的 HTTP 连接池跨轮保留最多60秒空闲连接，每轮仍重新读取凭据和创建请求；退出 Application 时确认关闭会话后释放连接池。最终回复仍以 `agent_settled` 为准，避免朗读工具中间结果。豆包按句合成，队列有空间就继续准备后续PCM。设备声明 `audio.stream.continuous.v1` 时，同一最终回复只打开一次播放流，各句依次写入，仅最后发送EOS并校验整流摘要及丢帧统计；总流仍限制4 MiB（约87秒），超限明确失败并停止。旧固件继续走v1，首段就绪即播，后续将已准备段合并为不超过4 MiB的批次，以减少设备重启次数；该降级仍可能有批次间停顿。暂停或失败会取消并等待全部合成/播放任务。每句云端PCM目前仍完整收集后入队，未宣称云端首包直接播放。

操作前回应的内容由 `voice_prompt.md` 指导 Pi 生成，Application 不做关键词模板或意图分类。Pi 扩展按轮重置门禁：任何其他工具（含 MCP 和嵌套调用）都必须等 `voice_reply` 确认播放完成；并行、取消和跨轮晚到不能提前放行。Application 仅自动处理专用 `watcherobot.voice_reply.v1` RPC 握手，实际调用原有语音后端；聊天不调用此工具；操作时有一次等待语音完成的工具往返，不另起意图分类或确认语生成的模型会话。

豆包成功播放操作前短句后，在 Application 内存保留一条 PCM，仅当下次文本及完整 TTS 配置相同才复用；普通最终回复不缓存。音色、资源或凭据等配置变化时不命中旧缓存，退出后清除；取消或播放失败的音频不写入缓存。日志记录 `acknowledgement`、缓存命中及分段合成/播放耗时，缓存不落盘。

页面最近一轮耗时分为“录音与识别”（含用户说话和静音判定）、“Pi处理”（含操作前回应与工具）、“合成与播报”（含实际朗读），不能将三者相加当作纯响应延迟。日志另记录合成首包、完成、播放开始与结束，不记录这些性能事件对应的文本或凭据。当前仍为半双工，不提供 RTC 的抢话打断。

## 连续播放诊断

`tts_pipeline.py` 独立管理合成生产者、字节/段数预算及设备消费者；不并行打开多个设备播放流。新SDK以4096字节跨write聚合，沿用Device channel credit背压，避免小包填满缓冲槽却未达到起播字节数。连续API显式声明mode/max_bytes，不伪造v1总长度或SHA。

性能日志区分 `kind=preface` / `kind=final`，报告队列准备、等待、PCM时长，以及设备begin、实际首包发送、上传和完成耗时。应用 `playback_start` 表示开始调用设备接口，不是扬声器声学起点。“操作前回应→最终结果”的等待包含Pi和手机执行时间，与同一回复内部段间停顿分别评估。

## Pi 流式文本提前合成

豆包后端会在 Pi 的 `message_update.text_delta` 到达时后台准备短句，不再等整轮结束才开始请求 TTS。切句复用 `speech_chunks`：按句末、长度和字节预算分段，保留末段作为前瞻，等待后续文字补全引号、网址和数字；`message_end` 冲刷无标点尾句。思考、工具参数和工具返回不进入合成。

**提前合成与最终播放分别确认。** Pi 可能改写 `text_end` / `message_end`，也可能在 `agent_end` 后自动重试或继续调用工具，因此仍等成功的 `agent_settled`，并核对最终文本及完整分段边界，才放行设备播放。候选被改写、进入工具调用或自动重试时立即作废；旧候选的取消在后台回收，不阻塞 RPC 继续读取。操作前 `voice_reply` 和最终播放取得同一会话所有权前，会等待旧合成任务退出。单纯修改提示词不能保证尚未结束的正文不会变化。

候选文本最多16384字符、最多32段待合成文本；PCM仍遵守8段 / 8 MiB预算（含合成预留和消费者持有）。候选超限或提交前合成失败时，回退为最终文本的正常TTS；已开始播放后的错误不会自动重播，以免重复。JoyInside 后端保持最终文本分句播报，不做本机预合成。

性能日志 `speech_preview stage=prepare/discard/commit/fallback` 仅记录阶段、分段数与耗时，不记录文本或凭据。Pi 生成期间的合成耗时已与“Pi处理”重叠，页面“合成与播报”表示最终确认后的剩余等待及实际朗读，不能把各阶段相加当作没有重叠的纯延迟。若模型将全文集中在少数 delta 返回，能节约的等待也会较少；未宣称模型未生成完就朗读最终正文，或同一句云端音频首包直通扬声器。

### Pairing after an interrupted conversation

If the robot exits Desktop Link while speaking or listening, audio cleanup may remain unconfirmed. The local page reports that Pi Application must be restarted before pairing again. Restart this Application through the Daemon/Desktop application manager, then open Desktop Link and pair using the robot’s current code. An active or stopping conversation instead asks you to pause and wait. These local resource checks run before network discovery; they do not imply a Wi-Fi failure.
