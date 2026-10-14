# 设备 JoyInside 语音 SDK 与 Pi Application

- 作者：KID / Codex；2026-10-13，Asia/Shanghai；无 Issue。
- 用户授权：实现 SDK、ESP32 固件与 Pi 示例；COM11 仅烧录 ESP32，PTL、Debug，并自行测试。
- 当前状态：进行中；未提交、未推送。
- 目标：Application 通过注入的 Device channel 使用设备 JoyInside 识别与专用 TTS，不导出设备凭据，Daemon 不增加业务分支。
- 识别合同：使用 conversation_asr，收到最终识别即停止采音并中断云端自动输出；自动回复不进入喇叭。不宣称云端完全不执行推理，纯 STT 开关尚未确认。
- TDD：先新增 tests/test_speech.py 与固件 speech_tests.c；接下来执行缺少接口时的失败验证。
- 验收：识别/播放请求关联、旧回包拒绝、资源互斥、断连收尾、UTF-8 长度限制、Pi 子进程异常/取消；主机测试、PTL Debug 编译、COM11 实机测试分别留证。
- 配套：ESP32 档案 docs/development/2026-10-13/2026-10-13_234500_KID_无Issue_设备JoyInside语音SDK/index.md；初始调查见 Workspace documents/dev_log/2026-10-13/2026-10-13_231100_KID_无Issue_JoyInside用户自有Agent接入调研/index.md。

## 2026-10-14 验证进展（KID / Codex，Asia/Shanghai）

- 已实现 SDK `robot.speech`、设备 ctrl/evt speech 协议、专用 TTS 和本机 Pi RPC Application。Daemon 未新增按业务消息内容的路由。
- SDK 语音与 Pi 示例测试 18 项通过；Application、Daemon 路由、设备语音相关回归通过；目标 speech.py mypy 通过。完整 mypy 受本机第三方依赖 stub 问题阻碍，未声明全量通过。
- C host 的既有 JoyInside 通信及 speech 测试通过。先 Red 后 Green 覆盖接口缺失、会话事件发送成功返回正数字节数、关闭事件与 NACK 竞争、NUL 文本拒绝。
- COM11 ESP32 已通过项目 wrapper 构建及 AppOnly 烧录，后端 PTL，叠加 sdkconfig.debug-cli.defaults。Debug 指启用 Debug CLI 与设备配置命令；不是声称编译优化等级为 -Og。未烧录 STM32。
- 设备 JoyInside 凭据通过已有本机 provision 流程保存至 NVS，未导出给 Application；日志与本机配置位于忽略的临时目录，不纳入提交。
- 首轮专用 TTS 完成物理 drain，Application 退出码 0。首轮实机 ASR 返回环境文本 Hello，经本机 Pi 后 TTS 正常完成，退出码 0。这不是固定中文准确性验证。
- 固定中文的电脑声学输入尚未通过：默认输出为蓝牙耳机。已请求旁边用户协助发声；同时增加只记录上传字节与峰值的麦克风诊断。
- 重新读取官方协议 B.4.2，发现混合上下行必须省略全局 audio.binary。原实现同时设置全局 false 与 input true，合同错误；新增测试明确 Red 后改为仅方向开关并 Green。最终修正版正在烧录，最终实机结果继续追加。
- 主线程 response 临时数组移至主 owner 独占的外部 RAM，避免 handler 与通用处理、WebSocket 栈帧叠加。工作任务负责 NVS 读取与云连接。
- 当前交付：本地源码修改；尚未提交、推送或发布。

## 2026-10-14 最终实机结果（KID / Codex，Asia/Shanghai）

- 最终 PTL / Debug CLI 构建、COM11 ESP32 AppOnly 烧录成功，修正版省略全局 audio.binary，云端确认独立上下行格式。二进制大小约 0x5e6xxx，6 MiB app 分区剩余约 2%。
- 由临时 Daemon-managed 验证 Application 经注入 Device channel 执行；没有第二条设备业务连接。
- 516 UTF-8 字节中文 TTS，约 40 秒：等待物理 drain 后成功完成，没有队列溢出或截断错误。
- speech 会话期间普通 microphone.open 返回 busy，音频互斥通过。
- 识别启动后取消，得到 cancelled；2 秒识别等待超时后正常释放，随后重开会话成功；验证 Application 退出码 0。
- 用户随后在旁边协助中文麦克风测试。最终固件多轮返回中文 ASR（包含“OK,你生气的时候是什么样子？”），每轮都交给本机 Pi，并在专用 JoyInside TTS drain 后报告 Agent reply playback completed，退出码 0。固定句子逐字准确性未做单独统计，不编造准确率。
- 示例依赖云端 VAD 断句；收到第一条最终 ASR 即结束本次 capture。头部黄灯表示采音，不是端侧 VAD 指示。没有端侧 VAD、唤醒词或播放期间语音打断；这些属于后续体验增强。
- 活跃与重开期间内部 RAM/连续块偏紧，main 最低栈余量仍观察到 736 字节（空闲/部分验证窗口 2928 字节），speech 工作任务约 2452–2548 字节；未出现 panic。功能测试通过不等同长时间稳定性验收，后续需要定位既有 main/transport 调用栈并压测。保留这一真实限制，不掩盖内存告警。
- 已停止专用测试 Daemon 并请求机器人退出 SDK 应用，麦克风不保持开启。源码仍为本地未提交状态。

关联档案：[2026-10-13_234500_KID_无Issue_设备JoyInside语音SDK](../../../../../WatcheRobot_esp32/docs/development/2026-10-13/2026-10-13_234500_KID_无Issue_设备JoyInside语音SDK/index.md) · [2026-10-13_231100_KID_无Issue_JoyInside用户自有Agent接入调研](../../../../../documents/dev_log/2026-10-13/2026-10-13_231100_KID_无Issue_JoyInside用户自有Agent接入调研/index.md)

## 后续变更

2026-10-14：用户追加网页可视化与连续对话需求，提供默认待机、一次开始多轮与暂停收尾。此前单轮退出仅属于验收设置，新的网页单轮验收会暂停并保持页面运行。详见 [Pi 连续语音可视化](../../2026-10-14/2026-10-14_010000_KID_无Issue_Pi连续语音可视化/index.md)。

## 2026-10-15 交付收尾（KID / Codex，Asia/Shanghai）

- 用户最终授权：仅提交 SDK 当前代码，以非快进合并提交合入 main 并推送远程；不创建 PR，不升级版本，不发布，不使用 GitHub Actions。
- 本次提交包含 robot.speech、JoyInside Pi Application、连续对话网页及对应测试和文档；前置已提交的对讲与播放嘴型改动随当前分支一起合入。
- 本机复验：`.venv/Scripts/python.exe -m pytest tests/test_speech.py tests/test_pi_speech_application.py tests/test_pi_speech_web.py`：20 passed。此次是已有实现的回归验证，原始 Red/Green 记录保留，不宣称重新进行 TDD。
- SDK 版本保持 0.1.11，Daemon 未增加业务消息类型路由分支；固件源码和 workspace gitlink 不在此次提交范围。
- 交付状态：准备提交；合入后通过两个 parent 和远程 main SHA 验证实际结果。推送期间临时关闭仓库 Actions，完成后恢复原设置。
- 补充验证：语音、Pi 示例与三组 Daemon/Application 路由回归共 38 passed；完整 mypy：Success，125 source files。初次 mypy 因本机 tomli 缺失及 NumPy stub 语法不兼容失败，补齐本机依赖并将本机 NumPy 调整至 2.2.6 后通过；未修改项目依赖声明。
