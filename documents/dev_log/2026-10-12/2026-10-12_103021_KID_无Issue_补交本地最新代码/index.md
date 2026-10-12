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
