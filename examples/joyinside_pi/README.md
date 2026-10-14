# JoyInside 本地 Pi 语音 Application

机器人采集麦克风、连接 JoyInside、返回最终 ASR 并播放指定文本的 TTS；电脑 Application 把文字交给本地 Pi。密钥保留在设备上，Daemon 仅转发业务帧。

使用包含 `robot.speech` 的 SDK 源码版本和声明 `speech.joyinside.v1` 的固件。机器人需配置 JoyInside，并与电脑处于同一局域网。按 SDK README 完成配对，安装 Pi 并配置其模型与登录，再从 SDK 仓库根目录执行：

```text
watcherobot app run examples/joyinside_pi
```

也可以传入 Application 绝对路径。启动后自动打开本机网页，默认待机不开麦。点击一次“开始对话”后自动进行连续多轮，点击“暂停对话”取消当前操作并释放音频；Pi 进程跨轮保留上下文。桌面 `ctrl.microphone.close` 暂停、`ctrl.microphone.open` 恢复；未支持的桌面业务消息安全忽略。不要直接运行 `python app.py`。

Pi 默认从 PATH 查找，Windows 自动解析 npm 启动器为 Node + CLI。`PI_COMMAND_JSON` 可指定完整 JSON argv 数组，例如 `["node","/path/to/pi/cli.js","--mode","rpc","--no-session"]`。`PI_WORKDIR` 指定 Pi 工作目录。不通过 shell 拼接语音，Pi 使用自己的模型、权限和工具设置。

测试环境变量：`WATCHER_SPEECH_AGENT=echo` 复述识别结果；`WATCHER_SPEECH_ONCE=1` 完成一轮后暂停；`WATCHER_SPEECH_TEST_TEXT` 设置短文本时自动测 TTS 并暂停。`WATCHER_SPEECH_AUTOSTART=1` 自动开始监听；`WATCHER_SPEECH_NO_BROWSER=1` 禁止自动打开浏览器。不要将本机配置写入 manifest。

- 模式是 `conversation_asr`，云端仍可能运行 Agent，固件丢弃其自动音频。指定文本使用专门的 `CLIENT_INPUT_TEXT_TO_SPEECH`，不提交给云端 Agent。
- 会话独占音频并采用半双工，识别结束后关闭麦克风，Pi 处理与播报期间不录音，暂不支持语音打断。
- 单次 TTS 1–999 UTF-8 字节；示例按完整字符分段。每会话最多 32 个请求以防止重放；超长回复超过余额时明确失败。每轮结束释放会话。
- 只播报 Pi 最终 assistant 文本，不播报思考或工具日志。需 Pi 1.1 的 `agent_settled`；交互式扩展请求、超时和供应商失败会明确报错。

详见 [设备语音接口](../../docs/device-speech.md)。

## 测试与断句说明

测试环境变量必须在启动 Daemon 的环境中设置，Application 继承 Daemon 的环境；只在调用 CLI 的终端设置，不能改变已经运行的 Daemon。改变配置后重启相应 Daemon，避免停止其他项目正在使用的实例。

麦克风头部黄灯亮起后说话。当前使用 JoyInside 云端 VAD；最终 ASR 返回后立即关麦并运行 Pi。房间内其他声音也可能先被识别；没有端侧静音门限、唤醒词或播放中打断。识别等待默认 30 秒，超时会清理并重试。

2026-10-14 已在 ESP32 PTL / Debug CLI 固件上完成中文真实麦克风 → 本机 Pi → 专用 TTS 验证，以及长文本播报、取消、超时、音频互斥和会话重开。没有执行长期稳定性或识别准确率统计；内存与主任务栈余量仍需持续观测。

## 连续对话网页

Application 日志打印本机网页 URL，端口自动分配。网页显示真实设备连接、聆听/思考/播报/清理状态、最终识别文字、Pi 回复与完成轮数。连续模式为半双工：回复 drain 后自动重开下一轮，不需要重复点击开始。无声等待超时会重新聆听。Pi 进程在正常轮次与暂停之间保留上下文；取消正在进行的 Pi 请求、故障或退出会退役该进程，后续重新建立上下文。

页面刷新不会停止正在进行的对话，暂停或结束 Application 才关闭会话；聊天记录保留于当前 Application 内存中，最多 80 条，服务重启后清空。HTTP 仅监听 127.0.0.1，并检查 Host/Origin。页面控制由 Application 自身执行，使用注入的 Device channel；不通过管理接口绕过 Application，也不建立第二条设备连接。

关闭 Application 使用 SDK/桌面端的停止操作。开发者可以不配置真实设备直接运行 `python -m pytest tests/test_pi_speech_web.py` 验证多轮与暂停的行为合同。
