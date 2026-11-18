# 合入前复审与本地 CI

- 时间：2026-11-18，Asia/Shanghai；作者：wugui / Codex。
- 用户授权：再审查，未发现问题则创建中文 PR；CI 通过后正常合入 main；先跑通本地 CI。
- 代码提交：a6c1feebb46166f67d39c0d6779124c52b8e8d46；远端 main 基线 98bb56078c39fbe13006817beeabf210261652b9。
- 第二轮独立整分支审查：无需要修复的问题；复核发布/上架入口、自定义布局、本地更新兼容性及模板/文档一致性。
- 本地环境：Windows，Python 3.12，UTF-8。按 `.github/workflows/sdk-ci.yml` Development checks 执行等价检查；GitHub Runner 调度、下载源码、Python 3.10/3.11 Linux 环境由远端 CI 验证，本地不冒充执行这些环境。

## 本地结果

18 项检查全部通过。sdist/wheel 输出、安装环境与详细日志位于系统临时目录，不提交制品。

| 检查 | 结果 |
| --- | --- |
| 测试/打包工具准备、pip check | 通过，无依赖冲突 |
| `python -m pytest -o addopts= -q --tb=short` | 2538 passed / 28 skipped / 3 warnings，235.98 秒 |
| `python -m pytest examples/expression_lab/tests` | 30 passed |
| `python -m mypy src/watcherobot` | 145 source files 通过 |
| `node --test tests/js/*.mjs` | 225 passed |
| BleakBackend 导入、`python -m pytest tests/provisioning` | 导入成功；75 passed |
| `python -m build --outdir <临时目录>` | sdist 与 wheel 均构建成功 |
| `python -m twine check <sdist> <wheel>` | 两种制品均 PASSED |
| 新建虚拟环境，安装 wheel 及运行依赖 | 成功；pip check 无依赖冲突 |
| 隔离导入、`watcherobot --help`、`watcherobot demo --help` | 均成功 |
| 隔离环境准备全部内置 demo | 成功 |
| `git diff --check` | 通过 |

临时编排脚本在 JS 步骤完成后因 Windows 默认 GBK 输出特殊字符中断；子检查 exit code 为 0，日志已保存。修正编排进程 UTF-8 配置后，从已保存成功记录继续剩余检查，18 项均获得真实成功结果，没有修改 SDK 产品代码或隐藏测试失败。

## 远端验收

创建 PR 后等待 SDK CI 的 Development checks、Python 兼容矩阵及仓库审查流程；全部完成并核对反馈后，按用户授权以普通 merge 合入 main，不绕过失败或待完成检查。此记录提交时 PR 尚未创建，不预写远端成功或合入结果。
