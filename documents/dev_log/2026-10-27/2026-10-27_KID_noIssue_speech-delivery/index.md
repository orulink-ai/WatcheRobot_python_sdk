# 设备语音与 Pi 连续对话配套交付

- 日期及时区：2026-10-27，Asia/Shanghai；作者：KID；参与者：Codex。
- 用户授权：SDK 配套 commit/push 并合入 main；ESP32 合入 codex/joyinside-integration-20261015。
- 基线：SDK main 24e3823；交付分支 codex/pi-speech-delivery-20261027。
- 状态：代码迁移与验证完成，提交及合入待执行。

## 范围与结果

将 Pi 回复流式分句、生成与播报并行、暂停/取消/清理及网页进度提示提交，完善 SDK 关闭不再归属会话的 handle 处理。保留最新 main 已有 Pi 超时和 Runtime/通话生命周期修复。补齐原本未提交的10月16日启动超时、低延迟、商店交付档案及本次聊天摘要。不是 SDK/PyPI 或 Application 商店发布。

配套固件位于 [ESP32 集成分支](https://github.com/orulink-ai/WatcheRobot_esp32/tree/codex/joyinside-integration-20261015)，提供 ctrl.speech.*、独立 TTS、轮次隔离和连接复用。Daemon 源码与路由无修改，Application 使用注入 Device channel。

## TDD 与验证

本次迁移复用原档案记录的 TDD，不把本次回归描述为先写测试。

- `.venv/Scripts/python -m pytest tests/test_speech.py tests/test_pi_speech_application.py tests/test_pi_speech_web.py tests/test_pi_speech_sentences.py`：38 passed。
- `.venv/Scripts/python -m pytest tests/runtime/test_daemon_runtime_routing.py tests/runtime/test_standalone_application_routing_contract.py tests/application/test_channels.py tests/application/test_context.py tests/application/test_transport.py`：28 passed。
- `.venv/Scripts/python -m mypy src/watcherobot/speech.py --follow-imports=silent`：通过。
- `git diff --check HEAD`：通过；原工作区完整备份后保留，不覆盖原修改。
- 本次未重新跑设备人声、长稳或商店发布；历史实机数据保留其原适用范围。

## 材料与关联

- [聊天决策摘要](conversation-summary.md)：用户目标、范围与合入授权；不是逐字聊天导出。
- [低延迟实现](../../2026-10-16/2026-10-16_121202_KID_无Issue_Pi流式语音低延迟/index.md)。
- [启动超时](../../2026-10-16/2026-10-16_112431_KID_无Issue_Pi语音启动超时/index.md)。
- [商店交付历史](../../2026-10-16/2026-10-16_143900_KID_无Issue_Pi语音应用商店更新/index.md)。
