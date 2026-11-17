# 三种开发形态与自定义固件安装｜Python SDK

- 日期：2026-11-17 Asia/Shanghai；作者：wugui，Codex；无 Issue。
- 分支：codex/firmware-projects-dev-20261117；状态：Python SDK 实现与验证完成，准备提交；配套桌面验证中。
- 用户授权：按飞书方案修改 Python SDK 和桌面并测试，同步设计文档；C SDK、ESP32 不动。
- 设计：https://mcnsslrwxv50.feishu.cn/docx/BCUhd4XLIofT9QxKMHMcQT4in10
- 配套仓库：Python SDK / WatcheRobot_client 各自独立工作区；尚无配套提交。
- 边界：SDK 只准备与分发；桌面 USB 执行；不强制自定义固件重连官方协议。
- 过程：按TDD记录各任务Red/Green命令与结果，证据追加到本目录。未执行结果不得编造。

## 实现与验证更新

- 基线：远端 dev `581689e20d20f760c0f9bd0441217afd79d51f39`。用户已授权功能分支提交并推送。
- Task1/Task2独立审阅通过，未发现待修代码问题。
- UTF-8环境全仓2512 passed / 28 skipped；原默认编码的3个Pi用例失败及定位保留task2.md，不改变无关代码。
- Python SDK代码与档案就绪；配套桌面审阅修复中。C SDK/ESP32源码无本任务修改。

- 最终 mypy：project、firmware、根CLI共17源码通过；diff空白检查通过。
- 本次提交包含SDK实现、测试与同仓档案；配套桌面分支 `codex/firmware-projects-20261117`，桌面来源锁待引用本次真实提交。
