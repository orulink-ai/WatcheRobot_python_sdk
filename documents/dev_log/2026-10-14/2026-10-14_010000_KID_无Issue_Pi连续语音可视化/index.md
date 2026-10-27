# Pi 连续语音可视化 Application

后续问题：[2026-10-16 启动超时与错误提示修复](../../2026-10-16/2026-10-16_112431_KID_无Issue_Pi语音启动超时/index.md)，保留原验收范围，补充真实运行故障处理。

- 建档作者：KID；参与者：KID / Codex；2026-10-14，Asia/Shanghai；无 Issue。
- 分支：codex/realtime-expression；状态：进行中，本地未提交。
- 需求：用户希望看见 Application 的网页效果，并点击一次后进行连续多轮对话。
- 边界：网页仅调用本机 Application 服务；设备消息使用 Daemon 注入通道，Daemon 不解析业务内容。半双工连续轮次，不声称实现播放中语音打断。
- 设计：服务与网页分离，待机默认不开麦；状态及识别/回复可见；暂停取消当前操作，清理后释放；Pi 进程跨轮保留上下文；本机 HTTP 限制 Host/Origin。
- 验收：先 Red 后 Green 验证两个连续轮次、暂停时取消、网页接口与来源限制；浏览器实际查看与操作；COM11 现有 PTL Debug 固件复用，无需修改固件。
- 前置实现：[设备语音 SDK](../../2026-10-13/2026-10-13_234500_KID_无Issue_设备JoyInside语音SDK/index.md)。

## 验证与交付

- Red：新增两轮连续对话、默认待机不采音、暂停取消并收尾、HTTP 状态/按钮 API 与跨来源拒绝测试，因尚无 service.py 两项失败。
- Green：实现 ConversationService 与本机 FastAPI 网页，相关新增 2 项及既有 speech/Pi 18 项，共 20 项通过。Node 对 web/app.js 语法检查通过。
- 浏览器真实验证：网页可见“机器人已连接”，一次开始切换至“轮到你说话了”，暂停后先显示“正在暂停”，再显示“待机中，机器人麦克风已关闭”。实际复用 COM11 已验证的 PTL Debug 固件，本次不改固件，不再次烧录。
- 浏览器已向用户展示，Application 留在待机状态，用户可直接点击开始。页面 URL 为运行时自动端口，不写入仓库。
- 本轮连续两轮的自动测试使用受控假设备与假 Pi，验证状态合同；真实中文 STT→Pi→TTS 已在前置功能中验证。本轮没有宣称完成真实声学连续两轮或播放打断验收。
- 界面不适用逻辑 TDD 的部分以浏览器实看验证布局、可访问按钮及真实状态，不伪称完成截图自动比对。
- 当前状态：功能与网页交付完成，本地未提交/推送；前置内存与长期稳定性限制继续有效。

## 2026-10-15 交付收尾（KID / Codex，Asia/Shanghai）

- 用户最终授权：仅提交 SDK 当前代码，以非快进合并提交合入 main 并推送远程；不创建 PR，不升级版本，不发布，不使用 GitHub Actions。
- 本次提交包含 robot.speech、JoyInside Pi Application、连续对话网页及对应测试和文档；前置已提交的对讲与播放嘴型改动随当前分支一起合入。
- 本机复验：`.venv/Scripts/python.exe -m pytest tests/test_speech.py tests/test_pi_speech_application.py tests/test_pi_speech_web.py`：20 passed。此次是已有实现的回归验证，原始 Red/Green 记录保留，不宣称重新进行 TDD。
- SDK 版本保持 0.1.11，Daemon 未增加业务消息类型路由分支；固件源码和 workspace gitlink 不在此次提交范围。
- 交付状态：准备提交；合入后通过两个 parent 和远程 main SHA 验证实际结果。推送期间临时关闭仓库 Actions，完成后恢复原设置。
- 补充验证：语音、Pi 示例与三组 Daemon/Application 路由回归共 38 passed；完整 mypy：Success，125 source files。初次 mypy 因本机 tomli 缺失及 NumPy stub 语法不兼容失败，补齐本机依赖并将本机 NumPy 调整至 2.2.6 后通过；未修改项目依赖声明。

## 后续变更：2026-10-15 Python 3.10 兼容修复

[PR #146 本地验收](../../2026-10-15/2026-10-15_KID_无Issue_PR146本地验收/index.md) 在全量回归中发现 Pi 进程超时清理捕获类型不兼容，修正为 asyncio.TimeoutError 并强化强制终止断言；连续语音设计保持。
