# 桌宠正式 Application：USB 传画与实体输入

状态：候选实现与自动化验证已完成，交互延迟优化未验收；SDK 尚未正式发布，App 尚未提交应用广场审核。

用户确认正式应用路线。保留现有 USB 演示；不改 M5、STM32、Himax；设备升级前重新备份当前应用并验证分区与恢复镜像，仅写 ESP32 application。

## 边界和合同

- App 运行于电脑，ApplicationContext.from_environment，经 watcherobot app run 托管。
- Daemon 只增加通用 USB 文本/二进制帧连接，不解析桌宠或显示业务。RawFrameRouter 保持原样。
- SDK display.open_stream / present / close 管理单个显示会话，固件声明 display.stream.v1 后可用。
- 显示使用 WSPK type 8（现有 type 1-7 均保留）；一次仅一帧在途，按 stream_id 和 sequence 等待真正呈现结果。
- USB WRU1: little-endian <4sBBHIII>, 20-byte header; kind text=1,binary=2,ACK=3; FIRST=1,LAST=2; 1024-byte fragment; maximum logical payload 131072; CRC32 per fragment. Host waits fragment ACK; device sends text fragments without waiting ACK. No business parsing in transport.
- 输入保留单击屏幕、背部短触、滚轮转动及短按；事件带呈现序列，拒绝陈旧菜单选择。
- 分支基于 origin/main 0a5427e，工作树位于 workspace 路径规范要求的 .codex/local/worktrees。原 checkout 不切换、不修改。原生工具不能指定该路径，使用 git worktree 创建。

## 实施顺序

1. TDD 实现 SDK 显示会话与通用 USB transport（两个不重叠模块并行）。
2. 接入 Daemon 管理 API / CLI、Robot 显示域及带上下文的实体输入，验证透明路由回归。
3. 配套固件实现同一传输和 SDK 显示/输入合同；本地 native 协议测试、IDF 构建。
4. 正式 App 迁移原桌宠取画、固定构图和菜单；资源不含角色模型、凭据、备份和日志。
5. 当前设备备份/恢复核验、app-only 升级，经实际 Daemon+App 验证持续播放和菜单交互。
6. 严格检查发布文件集合与依赖可安装性；SDK 未正式分发前不得宣称兼容公开版本或已上架。准备可审阅的 SDK PR 和 App 发布包，再按真实前置条件发布投稿。

## 实验记录

- 已确认本机旧 Daemon 未运行；当前 USB 演示独立运行，不占用 SDK 服务。
- 已确认公开 SDK 0.1.13 缺任意 JPEG 帧显示 API；不能仅加 app.json 宣称正式 App。
- 首次 prepare 的 ensurepip 在沙盒失败，未继续使用失败目录；隔离临时目录后在新项目目录重试。


## 2026-11-03 17:06，Ciel：实现和验证

SDK 新增 display.open_stream / present / close、通用 WRU1 USB transport、USB 管理 REST/CLI，以及可选的输入显示上下文。显示业务保留在 SDK/App/固件，RawFrameRouter 未修改。USB 默认打开前关闭 DTR/RTS；物理验证确认默认握手线会使本次设备复位，修正后稳定收发。

审查发现并修复 USB 专属断开误用通用断开入口、旧连接 offline 通知与新连接竞争两个 P2 问题。USB 断开现在不影响 LAN 会话或绑定；占用保留到旧通知完成。

验证证据：
- 最终聚焦 SDK 测试：223 passed, 1 skipped, 26 subtests passed，含显示、USB、输入、Runtime、REST、CLI 及透明路由。
- 全量回归：2486 passed, 11 skipped；一个子进程导入错误发生在并发重装 SDK 时段。安装结束后该参数化测试两个分支均通过。未把这次全量运行写成全绿。
- mypy：140 个源文件通过；git diff --check 通过。
- 独立 App 最新测试：166 passed，包含固定构图、菜单呈现、桥接安装/恢复、校准、输入映射及点击后丢弃旧预取帧。
- 固件原生 C 协议测试 /W4 /WX 通过，ESP-IDF 6.0.2 构建通过。
- 设备身份、分区、当前镜像备份及读回摘要已核对。仅烧录 application，未写 bootloader/partition/NVS/STM32/Himax。私有备份和硬件身份不提交。
- 实际受管 App 经 Daemon USB 连续呈现数千帧，约 5.2–5.4 fps；用户确认屏幕单击、背部菜单、滚轮选择/确认和退出菜单恢复大小均正常，同时指出反馈延迟。已去除菜单等待无关 renderer capture 的路径，并加入分段耗时；现有 USB 吞吐仍是限制，未宣称延迟已验收。

App 源码发布集合由实际发布收集器单独检查；不含虚拟环境、设备备份、NVS、凭证、日志、角色素材和本机校准配置。硬件 SDK 作为固定公开提交的外部构建依赖，不把本地 vendor 或固件二进制发布至 App 源码仓库。

交付边界：本分支使用 0.1.14.dev20261103 标记本地构建。公开 0.1.13 不支持显示 API，不能放宽 App 清单伪称兼容。SDK PR 合入与正式版本发布、配套硬件兼容性发行是后续上线前提；源码快照发布不代表应用广场审核通过。


## 2026-11-03 17:12，Ciel：交互延迟优化

App 本地菜单呈现不再等待 Electron capture；点击后丢弃旧预取帧，直接获取点击后的画面。菜单无变化时每 10 ms 检查输入，最多 500 ms 重发一帧以维持显示租约和可选择帧的新鲜度；滚轮或原菜单状态变化会立即生成新帧。158 项 App 测试通过，已重新开启受管实机运行。用户对本轮延迟改善的实屏评价待反馈，不能以单元测试代替验收。

## 2026-11-03，Ciel：Draft PR 前状态校正

本条更新上节的待反馈结论：用户明确反馈第一轮延迟优化没有改善，交互延迟未验收。当前独立 App 自动化测试已扩展至 166 项并通过；测试通过不代表交互延迟已改善。后续固件 UART 调整仍在构建和验证中，不在本次 SDK 提交中，也不记录为已通过或已验收。

本次仅准备基于 main 的 SDK 功能分支提交与 Draft PR。开发版本为 `0.1.14.dev20261103`，不是公开正式发行版。SDK 合入、发布和应用广场投稿均未由本次操作执行；App 源码发布由独立任务管理。提交集合只包含 SDK 源码、测试、显示协议文档和本档案，不包含本机路径配置、硬件身份、备份、运行日志或角色素材。

提交前验证沿用同一候选实现的已完成结果：SDK 聚焦测试 223 passed、1 skipped、26 subtests passed，mypy 140 个源文件通过。全量测试记录为 2486 passed、11 skipped，另有一个并发重装期间的子进程导入错误；重装结束后该用例的两个参数分支均通过，不能将原全量运行记为无失败。显示模块使用 `FRAME_DISPLAY` 常量后的 91 项测试通过。
