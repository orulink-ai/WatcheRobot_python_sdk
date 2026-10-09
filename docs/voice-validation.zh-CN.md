# 语音模板验收记录

日期：2026-09-29。环境：macOS arm64，Python 3.11。本记录不包含账号或凭据。

## 自动验证

- `python -m pytest --tb=short`：1527 通过、1 跳过；随后补充的两项模板/用户凭据测试单独通过。
- `python -m pytest tests/voice tests/application/test_project_init.py tests/distribution/test_source_files.py`：49 通过。
- `python -m mypy src/watcherobot`：115 个源文件通过。
- `python -m pip check`：通过。
- `python -m build`：wheel 与 sdist 通过。
- `python -m twine check <wheel> <sdist>`：通过。
- 将 wheel 安装到仓库外目录后：生成 voice 项目、导入组装入口、读取随包指南、运行生成项目测试均通过。
- 发布文件快照共 18 个文件，不含真实凭据目录；安装快照能通过独立用户凭据目录加载配置。
- 现有应用安装/Runtime 清理测试已覆盖 macOS；本次补齐平台夹具与运行实例隔离。

## 云端验证

全部使用短合成测试文本，不采集用户真实对话。ASR 输入是由测试 TTS 生成并转成 16 kHz PCM 的音频。

| 能力 | 结果 |
| --- | --- |
| 通义 `qwen-flash` 流式聊天 | 通过 |
| 火山 ASR `volc.bigasr.sauc.duration` | 最终识别结果通过 |
| 阿里云 NLS AK/SK 换取 token、流式识别 | 通过 |
| 火山 `seed-tts-2.0` / `zh_female_vv_uranus_bigtts` | 返回 24 kHz PCM，通过 |
| MiniMax `speech-2.8-turbo` / `male-qn-qingse` | 返回 24 kHz PCM，通过 |
| 通义模型目录 | 返回候选目录，通过 |
| MiniMax 音色目录 | 返回音色目录，通过 |
| 默认 ASR → LLM → TTS 的 VoiceSession 整轮 | 通过，提交一轮历史并生成一段回复音频；播放端为测试替身 |
| 方舟 `deepseek-v3-2-251201` | HTTP 401，认证未通过，不能认定可用 |
| Deepgram ASR/TTS、Cartesia、ElevenLabs | 离线协议测试通过，未提供对应凭据，真实调用未执行 |

目录仅代表候选，不代表调用权限。此表仅覆盖列出的参数组合。

## 尚待验收

- 默认组合的真实采音、播放、多轮上下文及停止清理已通过（见下节）；物理断连/重连和长时间压力测试尚未验收；播放中打断的追加检查见下节。
- 方舟需要可用推理凭据；其他三家供应商需要各自凭据进行联调。
- 尚未向真实应用广场发布示例应用；已验证源码过滤、安装快照、用户凭据解析及现有安装自动化测试。
- Windows 真机与不同麦克风环境下的能量阈值需另行验证。
- 本分支没有发布新 SDK 版本；生成项目需运行在包含本功能的 SDK 构建中。

## 安装兼容修复

验证中发现 macOS psutil 不提供 `memory_maps()`，导致安装后的自动清理抛出异常。
现改为无法完整检查引用时保留 Runtime 缓存并延后清理；不会改变 Application 业务路由。

## 追加模板审查

- 复现并修复相对凭据目录错误地按进程工作目录解析的问题；环境变量覆盖和 Python 参数均统一按项目根目录解析。
- 复现并修复 LLM 与 TTS/播放并行时错误阶段被覆盖的问题；记录实际失败任务的阶段，保留安全错误包装，并在每轮开始重置。
- 新增四个回归用例，先确认失败再修复；完整 pytest 1533 项通过、1 项跳过；语音、模板和发布文件筛选共 53 项通过，mypy 115 个源文件通过。
- 前一版 PR 的 Python 3.12 CI 在源码下载阶段发生 curl 56 网络错误，未执行测试；不将其计为兼容验证通过。
- 审查阶段曾被设备配对阻塞；后续使用新配对码连接成功，见下节真机验收。


## 默认组合真机验收

2026-09-29，在 macOS 上由现有 Daemon（SDK 0.1.10 bundle）管理生成的 voice Application，
Application 使用本分支源码；设备 ESP32 固件 v0.4.3、STM32 v0.1.2。未改动 Daemon 业务路由。

- 设备连接：WebSocket 与 Daemon hello ACK 完成，状态为 connected / python_sdk。
- 真实语音：用户对设备讲话，经火山 ASR、通义 qwen-flash、火山 TTS 后由设备扬声器播放；用户确认听到完整回复。
- 多轮上下文：用户继续询问上一轮内容，确认机器人能正确回答。
- 半双工：日志显示麦克风关闭后才开始 TTS 播放，播放资源释放后恢复采音。
- 播放完整性：观测到的两段分别为 97/97、64/64 帧完整播放，dropped_frames=0，DMA 排空 result=0。
- 停止清理：app stop 正常返回 ended；设备 mic=0、tts=0、codec_resident=0；设备连接仍为 connected。
- 凭据仅写入受限的临时目录，测试结束后清除；未保存录音或对话正文。测试应用已停止。

设备仍有 internal_free 偏低和 WebSocket 锁等待告警，本次未观察到对应音频丢帧或断连，
不能据此判定长期稳定。固件内存余量、锁竞争及长时间压力测试需后续独立排查。
最初配对超时缺少设备端同期日志，根因未确定；本轮不宣称修复配对问题。


## 暂停、取消和恢复追加检查

- 通过 Desktop WebSocket 发送业务帧，经 Daemon 转入当前 Application；没有绕过 Application 直达设备。
- `ctrl.microphone.close` 关闭采音，暂停期间未出现新轮次；`ctrl.microphone.open` 后恢复采音。
- 应用停止并重新启动后恢复对话；本轮同时将默认角色名称改为 `watcher`。
- 播放开始后发送关闭麦克风帧：应用侧观测到 play_cancelled、play_released、turn_cancelled，history_messages=0；暂停观察窗口内无迟到播放。
- 再次打开麦克风后，观测到新轮次完整分段播放。以上探针仅记录阶段、计数和清理状态，不记录正文或音频，未加入模板默认日志。
- 后续曾再次需要配对，不能将停止后的即时在线检查等同于长期连接稳定性验证。


## 句间停顿修正

用户确认单句声音完整，但句与句之间停顿明显。原流程在上一段播放完毕后才开始下一段 TTS，
每段都会额外等待云端合成。新增回归测试先复现该串行等待，再拆分合成与播放任务：
当前段播放时预先合成后续段，待播队列容量为一段，单段仍限制为 4 MiB，保持取消和错误清理。
这是内部调度优化，没有新增用户配置；默认交互仍为“说完 → watcher 回答 → 继续听”。
不提供语音抢话打断。真机听感改善仍需用户复测确认，不能等同于设备支持无缝连续流式播放。

本次修正验证：完整 pytest 1534 项通过、1 项跳过；mypy 115 个源文件通过。


## 后续边界审查与本地 CI

- 复现并修复暂停收音后断连仍保留历史的问题：设备不可用时独立清空会话历史，不依赖是否有正在运行的轮次，避免重连沿用旧连接上下文。
- 模板 README 与随包指南补齐 publish 必填的 --provider，并明确 publish 上传源码、submit 提交应用广场审核的区别。
- 用户确认一次 LLM 超时后对话已恢复；单次独立请求约一秒完成，不据此认定超时根因已修复。
- 逐步命令文档已创建并回读：[飞书使用指南](https://mcnsslrwxv50.feishu.cn/docx/TyM7dHcltoFTHAxbOjFcmWjPnSf)。

本地按 sdk-ci.yml 的开发与 Python 兼容任务验证；执行主机为 macOS arm64，不替代远端 Linux 环境或 Windows 真机验收。

| 本地 CI 检查 | 结果 |
| --- | --- |
| Python 3.10.19 全量 pytest | 1535 通过、1 跳过 |
| Python 3.11.15 全量 pytest | 1535 通过、1 跳过 |
| Python 3.12.14 全量 pytest | 1535 通过、1 跳过 |
| mypy（最低语法合同为 Python 3.10） | 115 个源文件通过 |
| Node 22 浏览器辅助测试 | 84 通过 |
| BleakBackend 导入与 provisioning 测试 | 75 通过 |
| pip check（开发、兼容及全新 wheel 环境） | 通过 |
| wheel/sdist 构建、twine check | 通过 |
| 全新环境安装 wheel、导入及 CLI 帮助 | 通过 |
| 安装后模板生成、watcher 默认名、入口导入、app check、凭据过滤 | 通过 |

各 Python 测试均出现现有依赖弃用提示及重复 ZIP 条目测试的预期警告，没有测试失败。

## 多模板初始化追加审查

集中维护 hello/voice 模板定义后，补充模板运行异常及 Ctrl+C 中断测试，先复现隐藏暂存目录残留，再修复异常清理。
清理只针对本次初始化创建的暂存目录，保留原始异常，既有文件不受影响。

本轮本地验证：Python 3.10、3.11、3.12 各 1540 通过、1 跳过；定向测试 103 项通过；
mypy 116 个源文件通过；wheel/sdist 构建、twine 检查及安装后的 wheel 异常清理验证通过。
未重新执行真实云端与设备验收，没有重启设备。

## 真实模板工程与使用文档重写

将嵌入 Python 字符串字典的模板拆成 `src/watcherobot/templates/voice/` 实际文件树，
初始化器复制随包资源，并生成应用清单、图标、独立本地凭据及扩展指南。
应用 README 包含完整目录、逐文件凭据和配置、配对、启动验收、二次开发、测试与发布。
飞书上手指南以这份实际 README 重写并回读核验全部章节，明确当前功能分支安装与正式 PyPI 发布的区别。

新增模板文件复制、README 配置一致性和生成工程完整离线语音链路测试。
Python 3.10/3.11/3.12 全量测试各 1545 通过、1 跳过；mypy 116 个 SDK 源文件通过。
模板中的 Python 文件由生成工程测试验证，不作为 SDK 自身模块进行 mypy 扫描。
wheel/sdist 构建与 twine 检查通过，wheel 中确认包含全部模板资源；
独立环境安装 wheel 后生成项目、app check、运行生成项目自身测试通过。
本轮没有执行新的云端或硬件验收，没有重启设备。


## 基础模板与语音模板分离验收

2026-09-29，两个模板现在可独立选择：默认 `app init my_app`（或 `--template base`）生成五文件基础应用；`--template voice` 直接生成语音应用。
基础模板资源保存在 `templates/base/`，语音模板复用基础资源并叠加自己的配置与代码。
基础入口启动受管 Application 并等待停止，设备行为演示留在 examples 中。

生成工程已移除 SDK 测试、扩展文档和预置 `.watcherignore`；应用 README 保留具体使用步骤。
上面的历史验收涉及的“生成项目测试”和“随包指南”不再是当前模板内容。
SDK 测试保留在 tests，扩展指南移回 docs。凭据仍强制从发布快照排除，空白凭据示例保留。

- 本地 macOS arm64：Python 3.10、3.11、3.12 各 1549 通过、1 跳过；三个环境 pip check 通过。
- mypy 116 个源文件通过；Node 22 测试 84 通过；BLE 后端导入通过，provisioning 测试包含在完整测试中。
- wheel/sdist 构建和 twine 检查通过；wheel 含五个基础资源文件、十四个语音扩展资源文件。
- 从仓库外独立 wheel 环境分别生成 base 和 voice，并执行 app check，均通过。
- 新增基础模板文件集合、基础与语音共享结构、基础入口导入安全及停止释放回归；先复现失败再实现。
- 飞书使用文档已同步两个独立选项、五文件基础目录与精简后的语音目录，并回读核验。
- 本轮未进行云端或硬件复测，也未发布 PyPI；没有更改 Daemon 业务路由。


## 凭据配置命令验收

新增 `watcherobot app configure [目录]`，按模板分派配置向导，语音实现与 CLI、供应商调用解耦。
先复现 12 项缺少命令的失败，再补齐实现与边界验证；最终新增 20 项回归，覆盖隐藏输入、已有值和环境变量引用保留、
阿里云两种认证、无认证本地 LLM、自定义凭据字段、输入取消、保存失败回滚、并发编辑检测及凭据符号链接拒绝。
向导只做本地配置校验，不启动 Runtime、连接硬件或测试云服务；模型、音色与提示词保持文件配置。

- macOS arm64：Python 3.10 / 3.11 / 3.12 全量测试各 1571 通过、1 跳过，三个环境 pip check 通过。
- mypy：117 个源文件通过；Node 22：84 项通过；BLE 后端导入通过。
- 隔离环境构建 wheel/sdist 及 twine 检查通过；独立 wheel 环境 pip check 通过。
- 仓库外安装 wheel 后，实际终端 PTY 验证生成项目、隐藏输入、凭据保存、回车保留、配置读取及 app check 均通过。
- 模板 README、中英文 CLI 参考及飞书凭据章节已同步。未修改 Daemon 路由、未连接硬件、未发布 PyPI。

## 使用实际凭据验证配置命令与跨平台合同

在 macOS 的真实终端 PTY 中，使用用户提供的凭据，通过 `app init --template voice` 与 `app configure` 分别配置默认组合、阿里云 ASR、方舟 LLM 和 MiniMax OpenAI 兼容 LLM。
四组均验证命令成功退出、输入不回显、写入值与 SDK 运行配置读取一致、回车保留原文件、app check 通过。
临时凭据在测试后清除，未调用云服务、未连接硬件；方舟和 MiniMax 此前的云端 HTTP 401 结论不因本地配置成功而改变。

追加 Windows 标准库 win_getpass 路径测试，使用模拟控制台驱动验证 Unicode、退格、回车保留及 Ctrl+C 不保存；
同时验证 Windows LOCALAPPDATA 与 macOS Application Support 的含空格/中文用户凭据路径和运行时一致。
Python 3.10、3.11、3.12 的配置命令专项测试各 24 项通过。生产实现无新增变更，原全量 CI 结果见上一节。
当前没有可访问的 Windows 测试主机，Windows PowerShell/Terminal 真机验收仍待完成；模拟控制台测试不替代真机验收。

## 按服务独立配置凭据

新增 `app configure --service asr|llm|tts`，不传参数仍配置全部。所选服务独立读取和校验，
另外两项凭据为空、配置缺失或无效、提示词未完成时，都不会阻止单项保存；未选文件保持原样。
共享运行时的单服务校验，仍拒绝所选服务缺少字段、无效参数或未设置的环境变量。

- TDD：先复现 13 项失败；配置专项最终 41 项通过，含新增单项隔离、取消与所选服务校验，并覆盖阿里云两种认证及 Windows 模拟控制台单项输入。
- 本地 macOS：Python 3.10/3.11/3.12 全量各 1592 通过、1 跳过；mypy 117 个源文件通过；Node 22 测试 84 项及 pip check、BLE 导入通过。
- wheel/sdist 构建、twine、独立 wheel 安装通过。安装后在 macOS 真实终端用提供的火山语音、阿里云 ASR 与通义凭据，分别执行三条单项命令；隐藏输入、保存、保留值、其他文件不变和 SDK 配置读取均通过。
- 临时凭据已清除；本轮不连接硬件，不调用云服务，不测试火山或 MiniMax LLM。Windows 真机环境仍不可用，不将模拟测试计为真机验收。
- 飞书凭据章节和本地中英文文档已同步三个命令及校验范围，并回读确认。
