# 发布 watcherobot 到 PyPI

项目通过 GitHub Actions 和 PyPI Trusted Publishing 发布，不在 GitHub Secrets 或开发者电脑中保存长期
PyPI Token。

## 发布质量门

每一个不可覆盖的 PyPI/TestPyPI 版本在发布前必须同时满足：

1. PR 与 `main` 的 CI 全绿，包括 Python 3.10/3.11/3.12、`websockets 12.x` 与当前允许最新版、
   pytest、mypy、wheel/sdist 构建、重装和 `pip check`。
2. 记录用于验收的 ESP32 完整 commit、固件版本、协议版本和 SDK 版本，不能只写浮动分支名。
3. 按[硬件测试说明](hardware-testing.md)执行 `tools/hardware_smoke.py --all`，核心能力全部通过。
4. 从目标索引在全新虚拟环境安装并验证版本；TestPyPI 的依赖仍从正式 PyPI 安装。

测试使用的轻量假机器人位于 `tests/fakes/`，不会进入 wheel，也不替代发布前真机验收。

## 一次性配置

在 GitHub 仓库中创建两个 Environment：

- `testpypi`：供手动测试发布使用。
- `pypi`：供 GitHub Release 正式发布使用，必须配置人工审批。

分别在 [PyPI](https://pypi.org/manage/account/publishing/) 和
[TestPyPI](https://test.pypi.org/manage/account/publishing/) 注册 Pending Trusted Publisher：

| 字段 | PyPI | TestPyPI |
|---|---|---|
| PyPI Project Name | `watcherobot` | `watcherobot` |
| Owner | `orulink-ai` | `orulink-ai` |
| Repository | `WatcheRobot_python_sdk` | `WatcheRobot_python_sdk` |
| Workflow | `publish.yml` | `publish.yml` |
| Environment | `pypi` | `testpypi` |

PyPI 与 TestPyPI 是两个独立账号系统，需要分别完成配置。首次成功发布会创建对应项目。

## TestPyPI 验证

发布 workflow 合入 `main` 后，手动触发测试发布：

```powershell
gh workflow run publish.yml --ref main
gh run list --workflow publish.yml --limit 1
gh run watch
```

由于 PyPI 不允许覆盖同名版本，每次重复测试前都必须先递增 `src/watcherobot/__init__.py` 中的版本。

使用全新虚拟环境验证 TestPyPI 产物：

```powershell
python -m venv .venv-release-test
.venv-release-test\Scripts\python -m pip install websockets
.venv-release-test\Scripts\python -m pip install `
  --index-url https://test.pypi.org/simple/ `
  --no-deps watcherobot==0.1.0a4
.venv-release-test\Scripts\python -c "import watcherobot; print(watcherobot.__version__)"
```

安装后还需使用验收记录中的同一固件运行最小连接示例。若版本已经上传但验收失败，不得覆盖上传，
必须递增 Alpha 版本后重新发布。

## 正式发布

1. 修改 `src/watcherobot/__init__.py` 中的版本并通过 PR 合入 `main`。
2. 确认测试、构建、TestPyPI 安装和必要的真机验收全部通过。
3. 创建 Draft GitHub Release，标签必须严格等于 `v` 加包版本：

```powershell
gh release create v0.1.0a4 --target main --draft --prerelease --generate-notes
```

4. 核对 Release 内容后发布：

```powershell
gh release edit v0.1.0a4 --draft=false
```

`release.published` 事件会启动正式发布任务。流水线会再次运行测试，并检查：

- Release 标签与包版本完全一致。
- Release 对应 commit 已经属于 `main`。
- wheel 和 sdist 均能通过 `twine check`。
- `pypi` Environment 已完成人工审批。

发布完成后验证：

```powershell
python -m venv .venv-pypi-test
.venv-pypi-test\Scripts\python -m pip install watcherobot==0.1.0a4
.venv-pypi-test\Scripts\python -c "import watcherobot; print(watcherobot.__version__)"
```

## 版本规则

- Alpha：`0.1.0a1`、`0.1.0a2`、`0.1.0a3`、`0.1.0a4`
- Beta：`0.1.0b1`
- Release Candidate：`0.1.0rc1`
- 正式版：`0.1.0`

PyPI 版本不可覆盖或重新上传。发布失败但文件已经进入索引时，必须递增版本号后重新发布。
