# PR #146 本地验收与跨平台测试修正

- 作者：KID / Codex；日期：2026-10-15（Asia/Shanghai）；类型：测试修复与合并验收。
- PR：https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/146
- 用户授权：本地执行 CI 等价检查，解决问题后合并 SDK 与配套 Desktop PR #182；远端 CI 不作为本轮阻塞。
- 状态：进行中，尚未提交或合并；不修改 Daemon 路由，不发布安装包。

## Red 与方案

Windows / Python 3.10.20 运行 `python -m pytest tests/distribution tests/test_app_cli_usability.py tests/runtime/test_frozen_windows_paths.py --tb=short`：484 passed、2 failed、2 skipped。

1. test_dependency_progress.py 固定断言 LF，实际子进程正常退出且输出 CRLF。改为 splitlines 比较有效输出，保留进程退出、实时事件和脱敏断言。
2. test_uninstall_cleanup.py 模拟占用时比较普通路径，实现使用 Windows 扩展路径，未命中注入。将目标路径转换为 filesystem_path，与实际 I/O 表示一致，保留失败计数、其它目录清理及重试断言。

## 关联历史

补充 [依赖实时进度与历史卸载残留清理](../2026-10-15_154500_orulink-wugui_无Issue_依赖实时进度与卸载残留清理/index.md) 的 Windows 本地验收；原功能设计保持。历史档案追加反向链接。

## 验收与待办

- 运行修正后的定向回归及 SDK 全量测试、类型检查、依赖检查、示例和 JS 测试。
- 根据实际结果追加记录；SDK 提交后 Desktop 同步真实来源 SHA，最后按 SDK → Desktop 顺序合并并核对双 parent。
- 本次仅修复测试平台假设，不改变用户接口，无需修改正式使用文档。

## 2026-10-15 18:00 Asia/Shanghai｜KID / Codex｜Green 与验收结果

- Windows Python 3.10.20：两处商店测试修正后定向 16 passed / 1 skipped；依赖检查通过。
- `python -m pytest --tb=short`：Python 3.12.4 为 1962 passed / 18 skipped；Python 3.10.20 为 1961 passed / 1 failed / 18 skipped，唯一失败为 Pi 语音示例超时清理。
- 新根因：Python 3.10 的 asyncio.TimeoutError 尚未与内置 TimeoutError 合并；PiAgent.close 捕获内置类型，未进入 kill 分支；原测试也使用了不兼容的内置类型。此问题存在于既有 Pi 示例，不归因为商店实现。
- 修正 close 捕获 asyncio.TimeoutError；测试使用同一跨版本异常，并断言睡眠进程以非零码被终止，防止自然退出掩盖清理失败。未改路由与会话协议。
- 修正后 `python -m pytest tests/test_pi_speech_application.py tests/test_pi_speech_web.py --tb=short`：3.10、3.12 各 6 passed；其余全量结果沿用同次验证，未声称重新运行修正后的完整全量。
- `python -m mypy src/watcherobot`：3.10、3.12 均 128 source files 通过；示例 expression_lab 两环境各 30 passed。
- `node --test tests/js/*.mjs`：225 passed。
- Python 3.12 `python -m build`、`python -m twine check dist/*` 通过。独立 wheel 环境安装、CLI --help / demo --help、BleakBackend 导入与 bundled demos 展开通过；两环境依赖检查通过。Pi 示例未列入 bundled-apps，不影响此前 wheel 内容验证。
- 保留仓库既有平台跳过与警告，未新增跳过。没有完成无 Git/Python 干净电脑、真机商店和正式安装器发布验收；本次交付为源码合入。
- 无新增系统脚本。实际修改为两处商店测试及 Pi 超时兼容修复；无需更新用户安装步骤。
- 当前状态：本地修改与验证完成，准备提交到 PR #146 原分支；随后配套 Desktop #182 同步 SDK main 实际合并提交。

## 补充历史关联

[Pi 连续语音可视化](../../2026-10-14/2026-10-14_010000_KID_无Issue_Pi连续语音可视化/index.md)：补充 Python 3.10 超时异常兼容处理，原连续对话与半双工边界保持。
