# PCM 配套合入

- 时间：2026-10-27，Asia/Shanghai；作者：KID；参与：Codex。
- 需求：用户授权推进 macos-joyinside-provision 固件及配套 SDK 合并。
- 状态：固件已合入；SDK 已提交并推送原 PR #151，正在完成最终验证与合入。
- 固件来源：618bdb2d；目标 codex/joyinside-integration-20261015（41ca9d1a）。
- SDK 来源：0207d196（4 项独有提交）；目标 main（299dbc5）。
- 范围：注册日志路径、本地 PCM 嘴型、连续播放、配套 Pi 应用与 SDK；保留旧协议和 Daemon 透明路由。
- 不含：固件烧录、SDK 发版、手机操作、根仓库 gitlink 提交。
- 合并策略：在隔离交付工作树整合，保留来源提交，解决冲突后测试、构建与 PR merge；最终核验两个 parent。
- 验收：旧协议/RTC 嘴型、连续流边界与取消、语音回归、Application/Daemon 路由、Python 类型与 Node 网页测试、IDF 构建。
- TDD：本轮以既有功能合并回归为主，不伪称重新执行历史 Red；如发现新增缺陷，先加可失败回归再修复。
- 缺口：尚未在最新集成代码上进行真机回归，不将历史硬件记录视作本轮结果。

## 2026-10-27 合并调查与处理

- 固件仅 main Host CMake 冲突：保留集成分支所有新测试，同时加入 continuous guard 测试。SDK 的 Pi 实现采用后端分层、工具先回应门禁和最终结果确认，保留现有 on_text 回调兼容、0.1.1 manifest 版本及 JoyInside 会话复用/32请求轮换。
- SDK 新增 2 项复用/轮换测试先失败，再修复后通过（25 项 Web 回归）。既有取消抵抗 provider 用例先失败：取消后返回的 PCM 被播放；新增取消 generation 复核后通过。
- Windows 下 10ms 超时与 3ms 模拟帧输入会抢先触发本机调度限制：将云超时用例分离初始采音截止，模拟帧恢复真实 60ms 音频节奏；仍验证阻塞云握手期间完整采集超过32帧且零丢包。
- 注册日志测试补齐最新 ProductId 查询桩，避免测试访问外部接口；MSVC observer 测试补 stdlib.h。
- 整合语义：最终正文待 agent_settled 确认后播报；豆包可提前合成。旧 on_text 增量接口保留；Application 展示最终正文及独立操作前回应，避免把工具中间正文当作最终结果播报。
- 本轮只做合并与回归，不改 GPIO/分区/OTA/NVS/STM32/Daemon业务路由，不烧录。

## 已获得的验证

- 固件：协议 Host 4/4、WebSocket Host 28/28、main音频/RTC/guard 5/5、JoyInside含speech/视觉/Token Host 19/19。注册工具 unittest 20/20（Mac/Linux/Windows路径均由桩测试，无真实注册）。
- IDF 6.0.2 / PTL / no-wake BuildOnly 完整构建通过；应用0x497930字节，6MiB分区余23%；SHA256 cc8de8556725a3d9a63620bd3d649cddfa5274d63c3e0cfba3b51fa9237bbe30。存在继承的Kconfig/未使用函数提示，未新增工具链/依赖升级。
- SDK：首次469项中463通过6失败；完成接口兼容、provider取消修复和环境时序修正后相关56项通过；会话复用/轮换Web25项通过。最终合并大回归运行中。6个SDK源文件mypy通过；Node扩展12/12、网页VM通过。
- 运行日志存本机忽略目录，最终命令/结果将整理为本档案 validation.md。未执行最新固件硬件冒烟；历史设备证据只作为原分支历史。

- 延续 [2026-10-17_175918_Mr-KID-github_无Issue_Pi安卓MCP接入](../../2026-10-17/2026-10-17_175918_Mr-KID-github_无Issue_Pi安卓MCP接入/index.md)。
- 延续 [2026-10-18_013000_Mr-KID-github_无Issue_Pi语音页面配对](../../2026-10-18/2026-10-18_013000_Mr-KID-github_无Issue_Pi语音页面配对/index.md)。
- 延续 [2026-10-18_085629_Mr-KID-github_无Issue_Pi语音专名提示词](../../2026-10-18/2026-10-18_085629_Mr-KID-github_无Issue_Pi语音专名提示词/index.md)。
- 延续 [2026-10-18_090955_Mr-KID-github_无Issue_Pi豆包语音后端切换](../../2026-10-18/2026-10-18_090955_Mr-KID-github_无Issue_Pi豆包语音后端切换/index.md)。
- 延续 [2026-10-18_092500_Mr-KID-github_无Issue_Pi动作表情与语音延迟](../../2026-10-18/2026-10-18_092500_Mr-KID-github_无Issue_Pi动作表情与语音延迟/index.md)。
- 延续 [2026-10-18_100400_Mr-KID-github_无Issue_Pi识别握手期间麦克风缓存溢出](../../2026-10-18/2026-10-18_100400_Mr-KID-github_无Issue_Pi识别握手期间麦克风缓存溢出/index.md)。
- 延续 [2026-10-18_102322_Mr-KID-github_无Issue_Pi首段短切与先回应后执行](../../2026-10-18/2026-10-18_102322_Mr-KID-github_无Issue_Pi首段短切与先回应后执行/index.md)。
- 延续 [2026-10-18_103036_Mr-KID-github_无Issue_Pi按语义先回应再操作](../../2026-10-18/2026-10-18_103036_Mr-KID-github_无Issue_Pi按语义先回应再操作/index.md)。
- 延续 [2026-10-18_104940_Mr-KID-github_无Issue_SDK连续PCM播放](../../2026-10-18/2026-10-18_104940_Mr-KID-github_无Issue_SDK连续PCM播放/index.md)。
- 延续 [2026-10-18_105058_Mr-KID-github_无Issue_TTS持续生成与单流播放](../../2026-10-18/2026-10-18_105058_Mr-KID-github_无Issue_TTS持续生成与单流播放/index.md)。
- 延续 [2026-10-18_111939_Mr-KID-github_无Issue_Pi流式文本提前合成](../../2026-10-18/2026-10-18_111939_Mr-KID-github_无Issue_Pi流式文本提前合成/index.md)。
- 延续 [2026-10-18_134233_Mr-KID-github_无Issue_Pi断线后配对被清理状态阻塞](../../2026-10-18/2026-10-18_134233_Mr-KID-github_无Issue_Pi断线后配对被清理状态阻塞/index.md)。

## 最终定向结果与配套
- 483项定向pytest通过；6文件mypy、Node12项及网页VM通过，详见[验证](validation.md)。
- 配套固件[PR #280](https://github.com/orulink-ai/WatcheRobot_esp32/pull/280)，实际交付提交19efbddb77f2f8f38ef81a36dd0c8edf9e04fa38；固件功能与测试/构建已经准备完成。
- SDK使用既有PR #151，准备fast-forward更新原远程分支，保留其原始4项提交与最新main修复。


## 2026-10-27 提交前核对
- 已核对diff：保留原分支4项提交、最新main的speech关闭修复/on_text兼容/manifest0.1.1/JoyInside复用，并整合本文记录的取消修复。
- 固件PR #280已合入，合并提交6e1b667b6e5a2165a9efe648647e299bae702f19；固件远程CI因账户付款/额度未启动，本地全部定向Host和构建通过。
- SDK定向483项、6源文件mypy、Node12项/网页VM均通过；全量pytest已补齐隔离依赖并运行中，本机全量mypy受第三方torch存根编码阻塞。上述限制不冒称通过。
- 接下来提交并快进更新已有PR #151，再依据扩展测试结果执行用户授权的merge；不使用squash或rebase合并，不发版。

## 2026-10-27 最终扩大验证记录

- 作者：KID / Codex；原因：补齐提交后的全量测试结果，区分本次功能验证与本机测试限制。
- SDK 整合提交 f31ff53b174ce4cf25f3664107292431f5f7a222 已推送原 PR #151；其两个 parent 分别保留最新 main 和原 Pi 配套分支。
- 隔离环境全量 pytest：2230 passed、18 skipped、2 failed（375.08s）。一项子进程加载本机旧版安装包，另一项相机反馈超时用例在全量负载下提前到达 busy 截止；未将本次全量运行记为全绿。
- 设置 PYTHONPATH 指向本工作树 src 后复跑上述两项，2 passed（1.33s）；相关功能定向483项仍全部通过。详见 [validation.md](validation.md)。
- 固件最终格式化后重新完成 Host 回归与 IDF BuildOnly；最终二进制 SHA256 为 9234faa33fe7744d3670d20a5cfa85984906f46037ca57191e88ed42014f211c，前文 cc8de855 为首次构建历史值。
- 固件最终 PR 合并提交 6e1b667b6e5a2165a9efe648647e299bae702f19 已确认两个 parent，原 macos-joyinside-provision 分支已在目标集成分支祖先链中。
- 不新增代码，因此本次档案补充不适用新增 TDD；替代验证为核对实际日志、PR状态与提交父节点。SDK 远程 CI 仍运行中，未伪称通过；硬件验收仍待后续实际烧录。
