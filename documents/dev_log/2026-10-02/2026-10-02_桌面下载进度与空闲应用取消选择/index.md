# 桌面下载进度与空闲应用取消选择

- 日期：2026-10-02（Asia/Shanghai）
- 作者：orulink-wugui；参与者：Codex
- 需求：桌面端隐藏 Agent、取消默认 Server 打包、修复应用下载百分比；无 Issue
- 状态：源码修改完成，本地验证通过；未提交、未发布
- 规范：SDK 没有独立 AGENTS.md / 开发档案模板，已阅读 workspace 的 documents/dev_log/README.md 与 temp_log.md，按其记录。

## 背景与目标

桌面下载进度原本按阶段赋固定值，而 SDK 仅发送阶段事件。要让百分比表达真实字节，需在 SDK 分发下载层提供进度事件，Desktop 只解析和显示。同一次下载必须明确累计字节和总量，已缓存文件、未知总量、并发回调不能制造错误百分比。

取消桌面内置默认 Application 后，卸载当前选择的第三方应用不能再依赖切换到默认应用。SDK 增加空闲取消选择的管理操作；运行、启动或停止中的应用不允许取消，设备连接与业务透明路由规则保持不变。

## 范围与配套

- 分发下载事件及其测试；Daemon 应用选择管理接口及其测试。
- 配套桌面修改：WatcheRobot_client/documents/dev_log/2026-10-02/2026-10-02_桌面功能收敛与下载进度修复/index.md。
- 不提交、不生成虚构锁定 SHA。正式打包须在 SDK 提交后，将桌面 runtime-source-lock.json 固定到包含两项能力的提交；本轮只交付源码修改和验证。

## 验收

- [x] SDK 真实下载字节通过 JSONL 发出，兼容既有事件消费者。
- [x] 空闲可取消选择，忙碌拒绝且不丢失选择，设备连接不重建。
- [x] 桌面使用新管理接口卸载当前选中的空闲应用。
- [x] 记录 TDD 与实际验证命令；端到端网络和正式包验收如实标注。

## 验证记录

两个模块的源码与回归已完成，结果如下。未执行的真实下载安装或正式包验证不视为已通过。

### 空闲取消选择子任务验证

见 [实现与验证记录](unselect-validation.md)：SDK 68 项回归、38 文件类型检查通过；桌面管理命令聚焦 15 项通过。正式打包依赖已提交 SDK 锁，尚未发布。

### 2026-10-02｜空闲 unselect 最终验证命令

- SDK（SDK 仓库根目录）：`.venv-ci/bin/python -m pytest -o addopts='' -q tests/runtime/test_application_unselect.py tests/runtime/test_application_session.py tests/runtime/test_application_runtime.py tests/runtime/test_control_rest.py tests/runtime/test_daemon_runtime_routing.py` → **68 passed, 1 warning in 50.71s**。警告为既有 Starlette/AnyIO 弃用提示。新增 unselect 文件包含 8 个用例；其中初始 6 个在实现前均因 HTTP 404 失败，实施后通过，另补 2 个边界用例。
- SDK：`.venv-ci/bin/python -m mypy src/watcherobot/runtime/daemon` → **Success: no issues found in 38 source files**。
- Desktop（Watcher Desktop App 目录）：`TAURI_CONFIG='{"bundle":{"resources":[]}}' CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 cargo check --manifest-path src-tauri/Cargo.toml --lib` → **通过**，仅既有 `encoding_rs::GBK` 未使用警告。
- Desktop：`TAURI_CONFIG='{"bundle":{"resources":[]}}' CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 cargo test --manifest-path src-tauri/Cargo.toml --lib modules::application_store::commands` → **15 passed, 0 failed**。未宣称此聚焦结果包含其他代理在此后新增的测试。
- 前一阶段 Desktop Daemon 聚焦测试：`CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 cargo test --manifest-path src-tauri/Cargo.toml --lib modules::daemon` → **26 passed, 0 failed**；`node --test scripts/daemon-process-supervision.test.mjs` → **7/7**。
- 没有在聚焦验证后追加广泛 Cargo 构建；生成 schema 已恢复到 HEAD，无持续 Cargo 进程。没有提交、推送、发布或真实硬件验收。
- 正式发行仍需 SDK 提交与 Desktop 真实 source-lock 更新、构建能力门禁和实际运行 Runtime 的升级验收；旧共享实例缺少接口时明确返回 `runtime_update_required`，不会自动重启共享实例或选择默认应用。


### 桌面最终编译补充

2026-10-02 主任务对最终进度 attempt 和 unselect 联合源码执行 Rust Store 全模块回归：44/44 通过；TypeScript 与 Vite 最终构建通过。此结果补充早期磁盘不足造成的进度字段重编译缺口，不替代发行包和真实网络验收。


### 下载进度最终验证

- `.venv-ci/bin/python -m pytest tests/distribution -o addopts='' -q`：397 passed in 11.14s。
- `.venv-ci/bin/python -m mypy --cache-dir=/tmp/sdk-progress-mypy-cache src/watcherobot/distribution/byte_progress.py src/watcherobot/distribution/hf_marketplace.py src/watcherobot/distribution/gitee_repository.py src/watcherobot/distribution/download.py`：4 个文件通过。
- 下载层的 TDD Red 记录在 /tmp/sdk-progress-red.log；最新 HF 1.32.0 实际下载器配合 HTTP mock 覆盖缓存、分块、续传、拒绝续传及计数回退，Gitee 适配器到 CLI JSONL 的真实字节元数据测试通过。首次、重试、完成立即上报，中间更新最多每 100ms。
- 最低依赖调整为 huggingface-hub>=1.32,<2，使用该版本已核验的公开字节进度 hook；未知总量保持未知，不伪造百分比。桌面区分 attempt 和阶段，旧事件不会覆盖当前下载。


## 2026-10-03 提交准备｜orulink-wugui / Codex
用户授权从最新origin/main创建codex/desktop-distribution-20261003，按空闲取消选择、分发进度与真实状态分批提交并推送，不创建PR。已核对main=7d9fc51；配套Desktop分支codex/desktop-scope-marketplace-20261003。密钥扫描命中均为AccessToken类型、运行时凭据读取表达式及测试占位值，人工复核无真实凭证。已有定向验证通过，正在执行完整SDK CI。

## 后续变更

- 2026-10-08T15:32:49+08:00｜orulink-wugui｜补充：[SDK 独立开发档案规范接入](../../2026-10-08/2026-10-08_153249_orulink-wugui_无Issue_SDK接入开发档案规范v1.0.0/index.md) 已建立本仓库规则、规范和模板；此前缺少模板是当时事实，原始实现与验证结论保持有效。
