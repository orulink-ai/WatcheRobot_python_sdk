# 合入前独立审阅与真实分发诊断

- 日期：2026-11-17，Asia/Shanghai；执行者：Codex 独立审阅代理。
- 范围：本任务 SDK diff、离线定向测试、匿名 Hugging Face 读取与临时安装；未启动应用、Daemon、串口或远端写操作。

## 离线复核

基线 `581689e20d20f760c0f9bd0441217afd79d51f39`，审阅 HEAD `73be773b3b03ce79a1b0cc264c15d83ec50e3403`。复核 project 模型/模板/CLI、prepare 的 metadata/full/application 转换和 operations 本地桥，未确认可复现的实质问题。

执行 `.venv/Scripts/python.exe -m pytest -q -o addopts='' tests/application/test_development_projects.py tests/distribution/test_firmware_preparation.py tests/distribution/test_firmware_cli.py`：99 passed、1 skipped、1 warning；跳过为宿主符号链接权限，警告为既有 Traversable 弃用。

## Hugging Face 真实诊断

主任务此前在 CLI 和 UI 观察到 `remote_error`，应用下载报 `Unable to download the reviewed Application source`，UI 目录刷新报告 `http_status=None`。本材料保留该报告，不将后续成功视为对原失败根因的证明。

诊断使用全新 Desktop venv 本任务独立 Desktop 联调 venv，Hugging Face Hub 1.33.0。钩住 `_raise_public_error`，仅输出异常类、栈帧、HTTP 状态及不含查询参数的请求主机/路径；不打印凭据。

- 同一 `yuzou1121/WatcherRobot-example.sdk_media_lab`、commit `cb7064a7fdf43c6cd00a5d84fb1ec135f74e77e8`，真实 snapshot 带进度下载成功，670082 字节。
- `load_official_marketplace(provider='huggingface', hub=HuggingFaceMarketplaceHubClient())` 连续三次成功，均 6 个应用；耗时约 7.94 / 6.20 / 6.47 秒。
- `distribution.cli.main(['app', 'install', ...])` 使用相同 repo/commit、真实 Desktop resources app-runtime 和全新临时 store，完整通过 12 个步骤，返回 0。结果为 SDK Test Bench 1.1.0，runtime_id `win32-x64-python-3.12.13-watcherobot-0.1.14`。临时 store 已由 TemporaryDirectory 清理，未启动安装的应用。
- 模拟桌面显式环境（UTF8、禁止 pyc、HF progress bars 关闭、HF verbosity error），工作目录为 resources/server/win32-x64，连续三轮目录 + snapshot 均成功。目录均为 6 个应用；每轮合计约 15.09 / 13.56 / 13.38 秒。
- CLI 未存在 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY / HF_* 环境变量。Windows 系统代理存在 http/https/ftp scheme，仅记录名称，未输出地址。
- 桌面 `create_command` 仅配置 Windows 无窗口标志并继承环境；distribution 显式设置 UTF8/progress/verbosity，移除 PYTHONHOME/PYTHONPATH，没有清理代理的代码。venv 的 watcher-distribution.exe 入口确认为 `watcherobot.distribution.cli:main`。

## 结论与边界

以上进程没有复现原远程失败，未捕获原失败异常链；当前证据不支持将其归因于 SDK API 不兼容、代理、网络或远端故障中的任何一个。需继续核对实际 UI sidecar 可执行文件与环境。未因未证实的原因修改分发实现，不新增功能限制；本阶段为调查与验证，不适用行为修复 TDD。

## 主任务补充验证

- 完整 UTF-8 测试重新执行：2512 passed、28 skipped、3 warnings（242.72s）。首次重跑因本地 venv 被开发环境准备脚本部分清理而发生依赖缺失；重装依赖后完整回归通过，该失败不归为产品缺陷。
- 主任务真实分发入口安装 SDK Test Bench 1.1.0：首次固定源码下载失败，重试后12步安装成功。使用当前SDK Daemon、本地一次性启动授权及安装器生成的launcher启动，状态running；实际网页与/api/status均HTTP200，随后通过管理POST停止成功。没有用第二条业务设备连接替代注入通道。
- 真实固件目录：Gitee为空；HF成功读取并下载标准full包与application包。官方main对应v0.4.0、已发布v0.5.9 ZIP均成功自动准备，未执行包内脚本。ESP32 dev三固件及桌面GUI烧录验收由配套桌面档案继续记录。
- 远程读取的首次失败原因尚无异常链证明；复测多次成功，不编造网络或代理根因，不为未证实原因增加输入限制。
