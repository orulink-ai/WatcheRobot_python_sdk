# Application 广场：SDK 文档入口

这里是 `WatcheRobot_python_sdk` 中 Application 分发与 Daemon 受控启动能力的统一查询入口。

- [SDK Application Development and Publishing Guide](sdk-application-usage.md): English developer setup, Device Flow, publishing, catalog, and immutable-download acceptance steps.
- [Complete CLI reference](../cli-reference.md): every `watcherobot` command, its options, side effects, and Runtime boundary.
- [Application CLI Quick Reference](application-cli-reference.md): Application distribution commands, human output, `--details`, and JSONL automation.
- [Application 发布前质量门禁（待实施）](publishing-quality-gates.zh-CN.md)：已发现的依赖声明、Python 环境误上传问题，以及后续发布提醒/检查的验收清单。
- [SDK Application 使用与测试指南](sdk-application-usage.zh-CN.md)：中文开发环境、最小项目、运行、Device Flow、公开发布、正式名单和固定快照的可执行步骤。
- [分发合同](distribution-contract.md)：当前版本、命令、JSONL、错误码、退出码以及 Desktop/Daemon/Hugging Face 边界。
- [实施进度](implementation-progress.md)：每个阶段已经完成的工程事实、测试证据和历史基线。
- [Hugging Face OAuth](hugging-face-oauth.md)：Public OAuth App、Device Flow、scope 与真实联调记录。
- [Hugging Face / Gitee 双平台分发](gitee.md)：平台独立登录、发布、投稿、收录与固定版本下载。

查“现在调用方应当依赖什么”时以分发合同和源码为准；查“为什么形成当前实现”时再进入实施进度与 OAuth 记录。


## 商店图标传输

目录的可选 `icon_data_url` 由 SDK 使用当前 provider 的 hub，从审核固定提交读取 `icon` 指向的仓库文件生成。国内使用 Gitee Git，国际使用 Hugging Face；桌面不拼接 raw/resolve URL，也不跨 provider 回退。图标随快照缓存，旧缓存无该字段时显示默认图标，下次成功刷新补全。

预览支持 PNG/JPEG/GIF/WebP/SVG；SVG仅接受UTF-8（可含BOM），拒绝NUL、DTD与实体声明后再解析，避免UTF-16/32绕过字节检查；单图不超过256KiB，目录内联图标总预算2MiB（按编码后字符计算）。缺失、不可用、无效或超预算图标不影响目录元数据与安装资格，只显示默认图标；不改变应用源码 manifest 中的 icon 字段或上传限制。
