# SDK 与固件安装兼容性核查

- 创建时间：2026-10-26 15:14:52 Asia/Shanghai；建档作者：YuZhong0212；参与者：Codex。
- 状态：进行中；仅本地调查，未提交、部署或发布。
- 目标：拉取最新 SDK，验证正式版和主分支与初见应用、配套固件、桌面安装链路的兼容性。
- 边界：保留现有未提交工作；隔离安装与测试，不覆盖运行中的 Daemon，不改变业务路由、不刷机。
- 验收：确认远端版本和制品校验、隔离安装、真实 API 差异及现有设备能力，记录缺口。
- TDD 不适用：本任务为版本调查与安装验证，尚无产品代码变更，以安装、接口检查及回归作为证据。

## 已完成

origin 分支和 tags 已 fetch；最新正式版 v0.1.11；origin/main 为 24e3823。已建立独立 detached worktree，保留当前 codex/sdk-kuroblob-expression-poc 的全部修改。

## 关联历史

补充[发布准备](../2026-10-26_发布准备/index.md)中 SDK/固件分发门禁的实际版本证据。

## 验证结果与结论

2026-10-26，YuZhong0212 / Codex：调查完成；未合并、未替换运行环境。

- GitHub 最新正式 release v0.1.11（2026-10-10），wheel SHA256 ce3d6c03f54993e0d485b027fcccd007419888cffc3b7a677e097116b6d40926，与官方 SHA256SUMS 一致。
- origin/main 24e3823 的源码版本为 0.1.12，独立 checkout 位于 `.worktrees/sdk-latest-20261026`；不是已发布 0.1.12。
- Windows Python 3.11 独立 venv：先安装官方 wheel，再安装最新主分支，两次 pip check 均无依赖冲突。现用 .venv 未变。
- 对比 ExpressionRuntimeDomain.update：正式版及主分支均具备四眼皮坐标，但都缺 mouth_opacity、accessory_opacity，且不允许 provision_scan/network/failed 内置配网页面资源名。
- 正式版 set_audio_follow 不支持 emotion；主分支已支持。不能以“SDK 新版”推定包含本地扩展。
- 同一组18项扩展测试：现用本地 SDK 全通过；正式版14失败4通过；主分支10失败8通过。主分支2项 audio-follow 失败来自测试替身缺 _closed/_closing 私有属性，不计作产品缺陷；其余8失败为 opacity 接口不兼容。无产品代码修改。
- 原始安装及测试输出保存在本机 `.temp/sdk-compat-20261026/`，不作为可发布源码。
- 当前控制台 SDK 为0.1.9（本地扩展），phase=stopped、device.online=false。返回固件版本 v0.5.8 及能力列表为已有状态，不能视为本轮真机在线证明。列表含 runtime.v3、audio_follow.v2、provisioning_screens.v1、camera.capture.seamless_feedback.v1、camera.capture.retained_follow.v1，但未含 expression.chat.audio.v1；完整喜怒哀乐支持仍需在线核实。
- 桌面仓库 application-runtime-lock.json 标0.1.3，runtime-source-lock.json SDK c333bbf（源代码也为0.1.3）；本地已构建 runtime-build.json 实际来源 cf5c9c5（源代码0.1.1a2）。三者不应混同，更不能当作用户实际已安装桌面版本。应用 requires_sdk >=0.1.9,<0.2，锁定版本不足，且版本满足也不代表上述扩展满足。

## 建议与遗留

保留当前配套 SDK+固件作为可重现效果的本地基线。后续将必要扩展迁移至最新 main，结合上游相机错误与音频清理修复做定向回归；统一正式 SDK、固件能力和桌面 Runtime 锁/构建产物后，再验收应用广场安装。禁止直接覆盖 robot.py 导致上游修复丢失。

本轮未做固件烧录、真机动作及有声完整流程复测，未验证 macOS/Linux 安装，不声称应用广场可直接安装。

后续迁移：[迁移初见到最新SDK](../2026-10-26_迁移初见到最新SDK/index.md)。
