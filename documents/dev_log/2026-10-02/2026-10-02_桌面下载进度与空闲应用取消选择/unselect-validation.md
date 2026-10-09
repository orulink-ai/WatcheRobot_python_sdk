# 空闲 Application 取消选择实现与验证

2026-10-02，Codex；SDK 开始本子任务时仅其他代理的 distribution 测试存在本地修改，本任务未改动分发源码/测试。SDK 无独立 AGENTS.md 与档案模板，沿用已阅读的 workspace 规范。

## 合同与实现

- POST /daemon/application/unselect，请求 application_id 必填。仅清理匹配的空闲选择、启动规格、退出状态，不停止或重启 Daemon，不重建设备/Desktop 连接。
- ApplicationRuntimeManager 检查 operation lock、closing、进程对象和 active run；运行/启动/停止或仍待回收的进程均拒绝。
- REST 在事件循环中同步执行清理，无 await 间隙；排空更新和 pending start/restart 拒绝。请求 ID 与当前选择不符返回 409 application_selection_changed；无选择时幂等。
- ApplicationSessionRegistry.clear_current_app 不复用 set_current_app 的非空 ID 合同。
- Desktop commands.rs 先定位 distribution，再按实际 Daemon 选择决定是否取消。严格核对 200 的 not_selected / selected=false / current_app=null / process_id=null 状态，成功后才卸载。卸载失败保留本地记录，成功才清除当前目标的本地选择；其他 Daemon 选择不被清除。
- 不修改任何业务消息路由。

## TDD 与验证

1. SDK Red：新增 tests/runtime/test_application_unselect.py 的 6 个用例，在实现前全部因新路由 HTTP 404 失败。
2. SDK Green：同 6 个用例通过。补充残留进程、排空更新、非法目标请求测试。
3. .venv-ci/bin/python -m pytest -o addopts='' -q tests/runtime/test_application_unselect.py tests/runtime/test_application_session.py tests/runtime/test_application_runtime.py tests/runtime/test_control_rest.py tests/runtime/test_daemon_runtime_routing.py：68 passed，1 个现存 Starlette deprecation warning，50.71s。
4. 真实异步服务用例覆盖运行时拒绝 unselect、停止后取消、Daemon 身份与设备状态不变，同一 Desktop/Device 连接继续双向转发文本、二进制、麦克风与未知业务帧。
5. .venv-ci/bin/python -m mypy src/watcherobot/runtime/daemon：38 个源码文件通过。
6. Desktop 使用 TAURI_CONFIG={"bundle":{"resources":[]}}、CARGO_PROFILE_DEV_DEBUG=0、CARGO_PROFILE_TEST_DEBUG=0、CARGO_INCREMENTAL=0，cargo check --lib 通过；cargo test --lib modules::application_store::commands 15/15 通过（此处为实现后补测，不称为先测试）。仅既有 GBK unused import warning。
7. 未执行正式冻结安装包、Windows 或真实硬件验证。生成 schema 恢复为 HEAD；不提交、不发布。

## 发布前置条件与配套

Desktop runtime-source-lock.json 必须在 SDK 真正提交后固定到同时包含下载进度和 unselect 的 commit；未生成虚构 SHA。构建源码能力门禁应检查 REST 路由 /daemon/application/unselect、DaemonRuntime.unselect_application、ApplicationRuntimeManager.unselect_application、ApplicationSessionRegistry.clear_current_app。当前旧锁不能作为包含新接口的正式包来源。

已有旧版共享 Daemon 返回 404/405 时，Desktop 明确返回 runtime_update_required 并停止卸载；不能偷偷回退默认应用或重启共享 Daemon。发布时还需确认真实运行进程包含新能力：只更新资源文件并复用相同 sdk_version 的旧实例不等于完成升级，必要时显式激活新 Runtime。
