# Application 广场：SDK 文档入口

这里是 `WatcheRobot_python_sdk` 中 Application 分发与 Daemon 受控启动能力的统一查询入口。

- [SDK Application Development and Publishing Guide](sdk-application-usage.md): English developer setup, Device Flow, publishing, catalog, and immutable-download acceptance steps.
- [Application CLI Quick Reference](application-cli-reference.md): all supported commands, human output, `--details`, JSONL automation, and Daemon boundaries.
- [SDK Application 使用与测试指南](sdk-application-usage.zh-CN.md)：中文开发环境、最小项目、运行、Device Flow、公开发布、正式名单和固定快照的可执行步骤。
- [分发合同](distribution-contract.md)：当前版本、命令、JSONL、错误码、退出码以及 Desktop/Daemon/Hugging Face 边界。
- [实施进度](implementation-progress.md)：每个阶段已经完成的工程事实、测试证据和历史基线。
- [Hugging Face OAuth](hugging-face-oauth.md)：Public OAuth App、Device Flow、scope 与真实联调记录。

查“现在调用方应当依赖什么”时以分发合同和源码为准；查“为什么形成当前实现”时再进入实施进度与 OAuth 记录。
