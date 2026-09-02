# 更新日志

## [0.1.6] - 待发布

- 飞书指令：基于最新 main 发布 Python SDK 0.1.6 正式版

## [0.1.5] - 待发布

- 飞书指令：发布 Python SDK 0.1.5 正式版

## [0.1.4] - 待发布

- 飞书指令：发布0.1.4正式版(显式target)

## [0.1.3] - 待发布

- PR #75：优雅重启 Application 并释放媒体会话

## [0.1.2] - 待发布

- 飞书指令：发布 watcherobot 0.1.2 正式版

## [0.1.2a1] - 待发布

- 飞书指令：发布 watcherobot 0.1.2a1 测试版

## [Unreleased]

## [0.1.1] - 2026-08-18

- Application 首次开发流程简化为 `pip install watcherobot`、`watcherobot app init` 和 `watcherobot app run`；初始化器会生成播放一次 `happy` 行为的 Hello World 项目，并自动补齐本地开发元数据。
- 新增 `watcherobot robot setup`、`robot pair` 与 `robot status`，打通蓝牙配网、六位码配对、连接确认和 Application 首次运行闭环。
- SDK Test Bench 1.1.0 完成 Application Marketplace 发布准备：默认英文界面并支持中文切换，补齐应用图标、作者与英文简介。
- 发布快照改为完全自包含，内置示例音频随 Application 分发，并明确排除照片、录音等运行产物。
- SDK Media Lab 完成 RTC 音频、视频与音视频模式的资源仲裁、真实双向媒体诊断、设备端 AEC 指标和重复启停后的资源恢复检查。
- 修复普通扬声器播放与麦克风录音争用共享音频运行时的问题，冲突操作会被安全串行化。
- 完善 Daemon 控制协议、Application Runtime 完整性诊断及 Windows/macOS 源码联调边界，并保持既有 Device channel 路由合同。

## [0.1.1a6] - 2026-08-17

- 完善 Application Runtime 完整性错误分类、Daemon 控制协议兼容握手及跨机器默认 Application 启动诊断。

## [0.1.1a4] - 2026-08-13

- SDK 测试台新增经 Application Device channel 的 RTC 真全双工音频验证，覆盖电脑麦克风到 Watcher 扬声器及 Watcher 麦克风到浏览器播放器。
- 新增机器人运动、灯光控制与基础整机全检，并以设备采集、RTP 发送和浏览器收发指标避免把连接状态误报为双向媒体成功。
- 建立自托管 SDK CI、TestPyPI 自动发布和陆骁发布编排流程；本预发布版仅用于 TestPyPI 验证，不进入正式 PyPI。

本文件记录 `watcherobot` 面向 SDK 使用者的重要变更。版本发布条目由发布准备工具创建，版本 PR 负责补充和审查具体内容。

## [0.1.1a2] - 2026-08-07

- 完善 Runtime、Application 分发与 BLE 配网能力的预发布验证。

## [0.1.0] - 2026-07-30

- 发布首个可安装的 WatcheRobot Python SDK 版本。
