# watcherobot 自动发布说明

`watcherobot` 由陆骁编排、自有 Runner 构建、飞书通知，并通过 GitHub Environment 人工批准后发布。
PyPI 与 TestPyPI 均使用 OIDC Trusted Publishing，不在仓库、Runner 或个人电脑保存长期 PyPI Token。

## 不会直接发布的事件

- 普通 PR 或草稿 PR；
- 没有发布标签的合并 PR；
- 未关联合法版本 PR 的普通 `v*` Tag；
- 任意非 `v*` Tag。

发布入口只能是飞书显式指令，或已合并 PR 上恰好一个发布标签：

- `release:prerelease`
- `release:stable`
- `release:minor`
- `release:major`

多个标签、非法 PEP 440 版本、版本倒退以及 PyPI 已存在的版本都会失败关闭，不会猜测或覆盖。

## 版本 PR 与 Tag

发布请求会创建 `release/watcherobot-<version>` 分支和带 `release:version` 标签的版本 PR。版本 PR 只更新：

- `src/watcherobot/__init__.py` 中的唯一版本源；
- 中文 `CHANGELOG.md`。

版本 PR 必须人工审查和合并。合并后，陆骁才能在该 merge commit 上创建 annotated tag `v<version>`。
Tag 门禁还会验证该 commit 属于 `main`、关联恰好一个合法版本 PR，且 PyPI、TestPyPI 和 GitHub Release
不存在冲突版本。

当前源码版本以 `src/watcherobot/__init__.py` 中的 `watcherobot.__version__` 为唯一真值。每次发布请求必须先
查询 PyPI、TestPyPI、Tag 与 GitHub Release，以最新外部状态计算或校验目标版本，文档不重复维护当前版本号。

## CI 与不可变制品

PR 和 `main` push 属于开发阶段，由 `.github/workflows/sdk-ci.yml` 在自托管 `sdk-ci` Runner 上使用一个
Python 3.11 环境执行。一次任务内完成 pytest、BLE fake backend、mypy、wheel/sdist 构建、`twine check`、
wheel 安装和 `pip check`。`mypy` 仍以 Python 3.10 为最低语言/API 合同，开发反馈不再重复启动所有 Python
版本和依赖组合。

BLE fake backend 按公司自托管 Runner 策略在 Linux 执行契约测试；不使用 GitHub 托管的 Windows/macOS Runner。
这项门禁验证导入和 fake backend 行为，不替代 Windows/macOS 实机蓝牙验收。

PR 语义审查只调度到同时带有 `pr-review` 与 `luxiao-hermes` 能力标签的 Runner。审查 bridge 从 PR
基础提交下载到任务级临时目录，既不依赖 Runner 私有绝对路径，也不执行待审 PR 自己修改过的 bridge；
具备 Hermes SSH 凭据的 Runner 变更时，必须同步迁移该能力标签。发布审查评论遇到 GitHub API
瞬时网络错误时最多重试三次；连续失败仍保持门禁失败，不能静默跳过审查结果。
审查 bridge 未生成有效结果时会发布故障评论并明确失败，不能把空报告视作审查通过。

带 `release:version` 标签的版本 PR 才会切换到发布门禁，在自托管 `sdk-ci` Runner 上执行 Python 3.10、
3.11、3.12 与最低/最新依赖的完整兼容矩阵，并在 Python 3.12 + 最新依赖组合中额外构建、安装候选制品。
六个组合全部通过且版本 PR 合并后，才允许创建合法 Tag。`.github/workflows/release.yml` 随后在隔离的
`sdk-release` Runner 构建一次正式 wheel 与 sdist，生成 `SHA256SUMS` 并上传为 GitHub Actions Artifact。
TestPyPI 与 PyPI 下载并使用同一份 Artifact，正式发布前再次验证哈希，不重新构建。完整矩阵最多并行两个
任务，避免短时间并发下载基础 Action 触发平台限流。

## 发布顺序

1. 版本 PR 通过 Python 3.10–3.12 × 最低/最新依赖完整兼容矩阵并人工合并；
2. 创建 Tag 后校验版本 PR、main 祖先关系及外部版本占用；
3. 构建一次不可变制品并发布、验证 TestPyPI；
4. 创建 Draft GitHub Release；
5. 预发布版直接发布 GitHub prerelease，到此结束，不进入正式 PyPI；
6. 只有稳定版才由飞书群收到正式发布待审批提醒；
7. 负责人在 GitHub `pypi` Environment 批准；
8. 使用原始 Artifact 发布 PyPI；
9. 从正式 PyPI 安装验证；
10. 将 Draft GitHub Release 转为已发布状态。

正式审批默认等待七天。陆骁监视器会在超时后取消运行并标记为 `CANCELLED`，不会自动恢复。陆骁无权合并版本
PR，也无权批准 `pypi` Environment。

## 正式发布恢复

若同一个 Tag 已完成构建、TestPyPI 验证并创建 Draft GitHub Release，但正式 PyPI 阶段因平台配置或临时网络
错误中断，不得移动 Tag、重建制品或再次上传 TestPyPI。先修复平台配置，再从 `main` 手工触发
`release.yml`，输入原 Tag（例如 `v0.1.1`）恢复正式发布：

```powershell
gh workflow run release.yml `
  --repo orulink-ai/WatcheRobot_python_sdk `
  --ref main `
  -f recover_tag=v0.1.1
```

恢复门禁会重新验证 annotated Tag、版本 PR merge commit、`main` 祖先关系和匹配的 Draft Release，并只下载
Draft Release 中由原始 `SHA256SUMS` 约束的 wheel/sdist。恢复只能从受保护的 `main` 分支触发，原 Tag 工作流
必须已经结束或取消，不能仍在等待审批。工作流会拒绝额外文件、缺少 wheel/sdist、版本不匹配、哈希不匹配，
并要求全部制品与已发布且不可覆盖的 TestPyPI 文件完全一致。

`uv` 会对照 PyPI 索引中的文件哈希，因此恢复任务可在部分上传后安全重试。恢复 Artifact 以稳定的 workflow
run ID 命名，支持在同一次运行中执行 **Re-run failed jobs**；也可以从 `main` 重新触发一次新的 recovery
workflow。不得重新构建、替换 Draft Release 制品或移动 Tag。只有正式 PyPI 的完整文件集合与哈希再次通过校验，
并在全新隔离环境中强制重新安装成功，Draft Release 才会转为公开 Release。该入口仍需 GitHub `pypi`
Environment 人工批准，且只接受稳定版本。

## 一次性平台配置

GitHub App 仅安装到 `orulink-ai/WatcheRobot_python_sdk`，向仓库提供版本 PR、Tag 和 Actions 监视能力。
工作流使用以下仓库 Secret 获取短期安装令牌：

- `ORULINK_RELEASE_APP_ID`
- `ORULINK_RELEASE_APP_PRIVATE_KEY`

PyPI Trusted Publisher：

| 字段 | 值 |
|---|---|
| Project | `watcherobot` |
| Owner | `orulink-ai` |
| Repository | `WatcheRobot_python_sdk` |
| Workflow | `release.yml` |
| Environment | `pypi` |

TestPyPI 使用相同仓库与 Workflow，Environment 为 `testpypi`。GitHub `pypi` Environment 允许 `v*` Tag
进入标准发布，并允许 `main` 发起受保护的恢复发布；两种入口均配置负责人为 Required Reviewer，不能绕过
人工审核。

## 首次正式发布结果

`watcherobot 0.1.1` 已于 2026-08-19 通过受保护恢复流程发布到
[正式 PyPI](https://pypi.org/project/watcherobot/0.1.1/)，对应产物与校验文件位于
[GitHub Release v0.1.1](https://github.com/orulink-ai/WatcheRobot_python_sdk/releases/tag/v0.1.1)，
成功流水线为
[GitHub Actions #32171032139](https://github.com/orulink-ai/WatcheRobot_python_sdk/actions/runs/32171032139)。
发布过程验证了 TestPyPI 原始产物、OIDC Trusted Publishing、GitHub Environment 人工审核、正式 PyPI
完整文件集合与 SHA256，以及干净环境强制重装、`pip check`、版本导入和 CLI 启动。

后续预发布或稳定版必须递增到新的 PEP 440 版本。实际目标以发起发布时重新读取的 PyPI、TestPyPI、Tag 与
GitHub Release 状态为准，不能复用或覆盖 `0.1.1`。

TestPyPI 安装验证需要让 SDK 来自 TestPyPI，同时从正式 PyPI 解析依赖：

```powershell
python -m pip install `
  --index-url https://test.pypi.org/simple/ `
  --extra-index-url https://pypi.org/simple/ `
  watcherobot==<version>
```

正式发布完成后验证：

```powershell
python -m venv .venv-pypi-test
.venv-pypi-test\Scripts\python -m pip install watcherobot==<version>
.venv-pypi-test\Scripts\python -c "import watcherobot; print(watcherobot.__version__)"
.venv-pypi-test\Scripts\watcherobot --help
```

PyPI 版本一旦发布不得覆盖。问题版本只能 yank，然后递增版本重新修复和发布。

## 实机发布门禁

稳定版发布前必须按[硬件测试说明](hardware-testing.md)记录 ESP32 完整 commit、固件版本、协议版本和 SDK
版本，并完成 Runtime 配对、受管 Application、行为、灯光、Job、相机、麦克风及 RTC 实时视频验收。自动化
Transport/Runtime 替身不能替代实机验收。

实机验收必须通过 `watcherobot app run` 启动当前受管 Application，桌面和设备业务帧均保持经过 Daemon 与
当前 Application 的既定路由，不得为发布验证增加协议旁路。

版本族遵循 PEP 440：Alpha `0.1.1a1`、Beta `0.1.1b1`、RC `0.1.1rc1`、稳定版 `0.1.1`。
