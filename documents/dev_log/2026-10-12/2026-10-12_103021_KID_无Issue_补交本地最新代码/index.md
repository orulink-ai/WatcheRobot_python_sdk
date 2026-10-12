# SDK 独立机器人对讲补交

## 基本信息

- 创建与最后更新时间：2026-10-12T10:30:21+08:00（Asia/Shanghai）。
- 建档作者：KID；参与者：用户（需求）、Codex（整理与验证）。
- 需求：用户确认本地最新代码此前遗漏提交到远端，要求补交。
- 工作分支：codex/publish-local-dev-20261012。
- 原本地源码提交：2c1a6bac114583f61ce01248b9acf9d01b123bac。
- 状态：补交分支已推送，PR #142 已建立；用户已明确授权合入现有对应主线。合入前修正示例应用清单遗漏，本地回归通过，远端 CI 待复验；未发布。

## 背景、目标与边界

在最新 origin/dev（SDK、Workspace 为 origin/main）上按非快进方式整合本地已提交代码，保留原提交历史及已合入的开发档案规范。源码在所属子仓库提交；Workspace 只更新档案和 submodule gitlink。原工作区与本地 dev 保持原位，本机配置、设备日志、视频及无关文档草稿不进入交付。

## 验收标准

- 本地源码 tip 是交付提交祖先，最新远端基线同样是祖先。
- 原提交历史保留，集成提交恰有两个 parent。
- 有意义的相关回归通过；环境限制、未做的真机或完整构建如实记录。
- 推送独立工作分支并创建 draft PR，不直接向 dev/main 推送。

## 当前进展与验证

已整合本地提交，本次回归结果见下文。原提交中记录的实机与 TDD 结果属于原开发阶段，不能当作本次复跑结果；本次仅整合代码、解决文本冲突并做必要的交付修正，不重新声称已执行真机验收。

## 变更记录

### 2026-10-12T10:30:21+08:00｜KID / Codex

- 建立本次补交档案，检查源提交、远端差异与提交范围。
- 原始源码提交和正式开发文档保留，后续追加当前验证及配套关系。

## 遗留事项

相关专项测试已完成，代码和 PR 已上传；正式合入需完成 CI 复验并检查最终 merge commit 恰有两个 parent。真人听感与硬件回归缺口保持如实记录。

### 2026-10-12 12:00 Asia/Shanghai｜KID / Codex｜合入前 CI 修正

- 用户指定合并来源为 `codex/publish-local-dev-20261012`；SDK 现有主线为 `main`，配套桌面与 ESP32 为 `dev`，Workspace 为 `main`。
- Red：远端 SDK CI 在 Python 3.10、3.11、3.12 均因示例应用总清单未登记 `robot_intercom` 失败；其余 1807 项通过、5 项跳过。本地单独运行 `python -m pytest tests/test_examples.py::test_every_example_is_a_complete_managed_application -q` 复现同一失败。
- Green：仅在 `EXAMPLE_IDS` 增加 `robot_intercom` 及 manifest ID，使现有应用合同测试覆盖该应用的身份、SDK 依赖和注入通道入口，未删除或放松原断言。
- 验证：使用当前 `src`，`python -m pytest tests/test_examples.py tests/test_robot_intercom.py tests/application/test_rtc.py -q` 共 38 项通过。
- 未重构业务代码，Daemon 路由与 Application 所有权边界保持既有合同。远端全套 CI 将在 push 后复验，不提前声称通过或已合入。
- 配套 PR：桌面 #178、ESP32 #259、Workspace #135；Workspace 将在子仓库合入后更新 gitlink。

### 2026-10-12 Asia/Shanghai｜KID / Codex｜自动审查问题修正

- SDK 清单修正提交 `2bbe27e` 的完整 CI 已通过 Python 3.10/3.11/3.12；自动审查指出浏览器请求缺少超时以及音频隐藏样本/帧单位混用，先修复再合入。
- Red：新增浏览器请求悬挂与统计单位测试后 4 项失败；新增服务迟到启动与缺失配对身份测试后 2 项失败，均对应真实目标行为。
- Green：通话 API 请求增加 20 秒期限及 AbortSignal；整个连接阶段从麦克风授权开始计时，30 秒截止。停止失败保留请求标识并进入可重试状态，请求期限不会被本机资源清理取消。服务保留最近 256 个取消标识以拒绝迟到启动，不修改 Daemon 路由。
- 根据 RTC codec 的 clockRate 将 concealedSamples 换算成 20 ms 帧数；未知时钟时不报告伪造帧数。
- 缺失配对身份拒绝启动；核对 SDK 的内容无关 Device channel 后确认不提供原子配对发送合同。文档不再承诺隔离检查/发送之间的并发设备切换，明确单设备通话期间切换配对不受支持。此项仍是共享 SDK 通道的边界，未通过新增 Daemon 内容分支绕行。
- 验证：浏览器专项 30/30；examples、robot_intercom、RTC Python 专项 40/40；diff --check 通过。此次是先失败再修正，未重新执行实体听感、LCD 或应用商店发布。
- 正式使用文档补充请求期限、迟到取消、静音与照片存储语义；历史验收文档标注初始 21 项浏览器测试与后续复验阶段差异。先前两段提交前验收记录保留，不视为两次独立实机验收。

### 2026-10-12 12:17 Asia/Shanghai｜KID / Codex｜协商期限与心跳合同

- 自动复审发现服务端原 10 秒心跳清理早于浏览器 30 秒连接期限，慢 offer 有被提前释放的风险。
- Red：新增 Python 协商期限、连接后心跳期限和信令锁持有时的维护测试，3 项预期失败；新增浏览器分配后即发心跳测试，1 项预期失败。
- Green：引入明确 connecting 状态，分配后的协商最多 30 秒，实际 RTC connected 后切换到 10 秒无心跳清理；维护任务不能在心跳/信令仍更新状态时用旧时间判断过期。浏览器在分配成功后立即开始心跳，连接成功取消建立阶段的旧截止定时器，后续短暂断连仍使用独立恢复期限。
- 最新专项验证：Python examples、独立应用与 RTC 43/43；浏览器对讲、采集、恢复及实际降噪 32/32；diff --check 通过。已检查迟到信令不能控制已取消通话，协商超时仍释放资源。完整 CI 以本次新提交为准，旧提交通过记录仅属历史阶段。


### 2026-10-12T10:33:57+08:00｜KID / Codex｜提交前验收

本次未改 Daemon 路由；没有重新执行真人听感、LCD 视觉或硬件回归。原作者实机结果保留在 docs/robot-intercom-acceptance.zh-CN.md，应用商店限流未在本次处理。

- python -m pytest tests/test_robot_intercom.py tests/application/test_rtc.py -q：33 项通过。
- node --test tests/js/test_robot_intercom.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_connection_recovery.mjs tests/js/test_rtc_noise_suppression.mjs tests/js/test_rtc_noise_playback.mjs：25 项通过。
- 使用当前 src 的 watcherobot.cli.main 执行 app check examples/robot_intercom：通过。
- 环境：Windows，Python 3.12.4，Node.js；C 主机测试使用 MSVC 19.40、Debug。仅复跑已有相关回归，不将事后复验包装成原开发的 Red/Green。
- 排除保留原文字节的 RNNoise vendored 模块与 Apache LICENSE 后，git diff --cached --check 通过；二者的上游尾空白保留，符合未修改副本的来源声明；只纳入本仓源码、必要交付修正及档案，本机配置、日志及构建产物保持忽略。
- 配套仓库：Workspace 将单独更新对应 gitlink；各源码 PR 独立审查，本次不直接向 dev/main 推送。


### 2026-10-12T10:34:32+08:00｜KID / Codex｜提交前验收

本次未改 Daemon 路由；没有重新执行真人听感、LCD 视觉或硬件回归。原作者实机结果保留在 docs/robot-intercom-acceptance.zh-CN.md，应用商店限流未在本次处理。

- python -m pytest tests/test_robot_intercom.py tests/application/test_rtc.py -q：33 项通过。
- node --test tests/js/test_robot_intercom.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_connection_recovery.mjs tests/js/test_rtc_noise_suppression.mjs tests/js/test_rtc_noise_playback.mjs：25 项通过。
- 使用当前 src 的 watcherobot.cli.main 执行 app check examples/robot_intercom：通过。
- 环境：Windows，Python 3.12.4，Node.js；C 主机测试使用 MSVC 19.40、Debug。仅复跑已有相关回归，不将事后复验包装成原开发的 Red/Green。
- 排除保留原文字节的 RNNoise vendored 模块与 Apache LICENSE 后，git diff --cached --check 通过；二者的上游尾空白保留，符合未修改副本的来源声明；只纳入本仓源码、必要交付修正及档案，本机配置、日志及构建产物保持忽略。
- 配套仓库：Workspace 将单独更新对应 gitlink；各源码 PR 独立审查，本次不直接向 dev/main 推送。


### 2026-10-12 Asia/Shanghai｜KID / Codex｜协商截止边界与分发复验

- 复审指出维护先将 connected 提升为 running 会绕过 30 秒截止。Red：截止时刻、截止后观察到 connected，以及阻塞 start 在截止后返回三项失败；截止前成功的控制用例通过。
- Green：维护先使用同一时钟快照判过期，再允许转换；start 返回同样核对期限，超时进入既有释放路径。最新 Python examples/独立应用/RTC 47/47，浏览器专项 32/32。
- 通过正式 Application source collector 生成临时 ZIP 并读回逐字节核对：20 个源文件，网页、全部媒体模块和三方许可均纳入；使用当前 src 的 app check 通过。此验证未发布应用，独立应用不属于 SDK 默认 bundled demos。
- RNNoise SHA256：d8679704d55effe88b10c39ad133b58ce27d9b861c6402efaaff9634d09234a7，与此前记录一致。
- 网页增加通话/释放期间勿切换机器人的提示；README 说明照片不自动过期，结束应用后由用户删除 JPEG。最近 256 个取消标识仍是有界缓存，不承诺无限期防重放。
- 未改 Daemon 路由，未进行真人听感、LCD 或正式分发发布；完整 CI 与复审将针对本次 head 运行。


### 2026-10-12 Asia/Shanghai｜KID / Codex｜请求入口截止门禁

- 复审发现维护尚未执行时，截止后的信令仍可进入 SDK。Red：signal、heartbeat、feedback、events、capture、幂等 start 六类入口及在途信令跨截止共 7 项失败。
- Green：在状态锁内统一所有权/协商期限检查，截止进入 cleanup_required；设备调用前拒绝迟到请求，信令/心跳/反馈/照片返回后重新检查，待维护或用户重试安全释放。Python 专项 54/54。
- 同步 SDK 调用无法由 fetch AbortController 取消；文档明确 30 秒触发取消与清理，不保证硬实时释放。在途请求可以完成，但不会被当作成功协商或续期。
- 另从 npm 下载 @jitsi/rnnoise-wasm 0.2.1，SHA512 integrity 与 NOTICE 完全一致。Git 保存的 vendor blob 逐字节匹配上游，SHA256 为 05a553f523d59502d133a6d05dbf1878137c9e7bcff06edf5561f7001b62f95f。此前 d8679704... 是 Windows autocrlf 检出后的 CRLF 工作文件散列，内容一致；本段澄清不把不同换行的散列误当上游原字节。
- 最终 CI 与复审重新运行；未新增 Daemon 业务路由，未烧录或发布。
