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

当前源码版本是 `watcherobot==0.1.1a3`，对应既有 Tag 是 `v0.1.1a3`。自动发布启用后的下一次请求必须先
查询 PyPI、TestPyPI、Tag 与 GitHub Release，以最新外部状态计算或校验目标版本。

## CI 与不可变制品

PR 和 `main` push 由 `.github/workflows/sdk-ci.yml` 在自托管 `sdk-ci` Runner 上执行：

- Python 3.10、3.11、3.12；
- 最低和最新受支持依赖；
- pytest、BLE fake backend、mypy；
- wheel/sdist 构建、`twine check`、安装和 `pip check`。

合法 Tag 由 `.github/workflows/release.yml` 在隔离的 `sdk-release` Runner 上执行。wheel 与 sdist 只构建一次，
随后生成 `SHA256SUMS` 并上传为 GitHub Actions Artifact。TestPyPI 与 PyPI 下载并使用同一份 Artifact，正式发布前
再次验证哈希，不重新构建。

## 发布顺序

1. 发布并验证 TestPyPI；
2. 创建 Draft GitHub Release；
3. 飞书群收到待审批提醒；
4. 负责人在 GitHub `pypi` Environment 批准；
5. 使用原始 Artifact 发布 PyPI；
6. 从正式 PyPI 安装验证；
7. 将 Draft GitHub Release 转为已发布状态。

正式审批默认等待七天。陆骁监视器会在超时后取消运行并标记为 `CANCELLED`，不会自动恢复。陆骁无权合并版本
PR，也无权批准 `pypi` Environment。

## 一次性平台配置

GitHub App 仅安装到 `orulink-ai/WatcheRobot_python_sdk`，向仓库提供版本 PR、Tag 和 Actions 监视能力。
工作流使用以下仓库 Secret 获取短期安装令牌：

- `ORULINK_RELEASE_APP_ID`
- `ORULINK_RELEASE_APP_PRIVATE_KEY`

PyPI Pending Trusted Publisher：

| 字段 | 值 |
|---|---|
| Project | `watcherobot` |
| Owner | `orulink-ai` |
| Repository | `WatcheRobot_python_sdk` |
| Workflow | `release.yml` |
| Environment | `pypi` |

TestPyPI 使用相同仓库与 Workflow，Environment 为 `testpypi`。GitHub `pypi` Environment 只允许 `v*` Tag，
并配置负责人为 Required Reviewer。

## 首次自动演练与验证

`0.1.1a3` 已由人工流程发布，不再作为自动演练目标。RTC 实机验证通过后可请求稳定版 `0.1.1`；若仍需
预发布修复，必须递增到新的 PEP 440 版本。实际目标以启用自动发布时重新读取的索引状态为准。

TestPyPI 安装验证需要让 SDK 来自 TestPyPI，同时从正式 PyPI 解析依赖：

```powershell
python -m pip install `
  --index-url https://test.pypi.org/simple/ `
  --extra-index-url https://pypi.org/simple/ `
  watcherobot==0.1.1a3
```

正式发布完成后验证：

```powershell
python -m venv .venv-pypi-test
.venv-pypi-test\Scripts\python -m pip install watcherobot==0.1.1a3
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
