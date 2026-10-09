# 语音模板迁移验证

2026-10-03，Asia/Shanghai；orulink-wugui / Codex。环境：macOS arm64。本记录验证基于 main 30ad9ea 的迁移结果，不替代 Linux CI、Windows 真机或云端/硬件验收。

## 文件与主线保留

- 来源提交：6085566、ab65108、aa3f652；来源 3785485 的 Runtime 修复由当前 main 等价或更完整实现覆盖，未重复移植。
- 59 个迁移文件，57 个与原分支逐字节一致；pyproject.toml 和 src/watcherobot/cli.py 用共同祖先 08d4813、当前 main 和来源分支做三方合并比对，结果与当前文件完全一致。
- main 独有的 52 个变更文件逐字节保留；包括 Runtime 清理、版本选择、取消选择与分发下载行为。
- 除以上迁移文件，只增加本任务档案。初次集合校验误将新增档案算作业务迁移文件而断言失败；修正校验白名单并使用 Git NUL 分隔路径后通过，未因此改动源码。
- `git diff --check`、`git diff --cached --check` 通过。无未完成合并、变基或 cherry-pick 状态。

## 专项验证

执行 `.venv-ci/bin/python -m pytest` 配合以下文件集合：

| 范围 | 测试文件 | 结果 |
| --- | --- | --- |
| 语音核心 | tests/voice/test_asr.py、test_catalog.py、test_device.py、test_providers.py、test_runtime.py | 30 passed，2.34s |
| 模板与凭据命令 | tests/application/test_project_init.py、test_templates.py；tests/test_app_cli_usability.py；tests/voice/test_configuration.py、test_configure.py、test_template_assets.py | 91 passed，1.57s |
| 文档示例 | tests/voice/test_guide_examples.py | 1 passed，0.20s |

本次复用已有测试验证迁移，不声称重新执行测试驱动开发的 Red 阶段。

## 完整开发 CI

依据 `.github/workflows/sdk-ci.yml` 的开发验证与 Python 兼容矩阵执行。

| 检查 | 命令或步骤 | 本次结果 |
| --- | --- | --- |
| Python 3.10.19 依赖与全量 | `python -m pip check`、`python -m pytest` | pip check 通过；1645 passed，5 skipped，2 warnings，138.80s |
| Python 3.11.15 依赖 | `.venv-ci/bin/python -m pip check` | 通过 |
| Python 3.11.15 全量 | `.venv-ci/bin/python -m pytest` | 1645 passed，5 skipped，2 warnings，134.33s |
| Python 3.12.14 依赖与全量 | `python -m pip check`、`python -m pytest` | pip check 通过；1645 passed，5 skipped，3 warnings，138.55s |
| 类型检查 | `.venv-ci/bin/python -m mypy src/watcherobot` | 122 source files 通过 |
| Node 22 浏览器测试 | `node --test tests/js/*.mjs` | 84 passed，0 failed |
| BLE 导入 | 导入 watcherobot.provisioning.bleak_backend.BleakBackend | 通过；provisioning 测试包含在全量 pytest 内 |
| 隔离构建 | `python -m build --outdir <临时构建目录>` | sdist 和从 sdist 构建的 wheel 通过 |
| 制品元数据 | `python -m twine check <sdist> <wheel>` | 两项 PASSED |
| 独立 wheel 安装 | 新建虚拟环境安装本次 wheel，执行 pip check、watcherobot --help、app configure --help | 通过 |
| 安装后模板 | 仓库外分别 app init --template base/voice，执行 app check | 均通过；base 目录精确为五文件 |

3.11 的两个警告：既有 Starlette/AnyIO 弃用提示，以及验证重复 ZIP 条目的测试主动生成同名文件提示。没有测试失败。

## 凭据与边界复核

通用安全扫描器报告凭据赋值和 credential-examples 路径，人工逐项核查：文档为环境变量引用；CLI 为 getpass 读取与参数转交；ASR 为 HTTP 结果字段读取；测试为 test-placeholder、secret-* 等假数据；随包三份凭据示例全为空字符串。没有新增真实密钥或私钥。专用长密钥字面值检查亦通过。

未连接机器人、未调用真实云端服务、未发布 PyPI；不存在针对 Daemon 业务路由的修改。Linux runner、Windows 真机和发布最低依赖矩阵未在本机执行。

Python 3.10 与 3.11 的警告相同；3.12 另有来源模板使用 importlib.abc.Traversable 的既有弃用提示。项目当前声明支持 Python >=3.10,<3.13，该提示未影响支持范围内的执行。本次保持迁移源码一致，不夹带接口重构。三个版本均无失败。
