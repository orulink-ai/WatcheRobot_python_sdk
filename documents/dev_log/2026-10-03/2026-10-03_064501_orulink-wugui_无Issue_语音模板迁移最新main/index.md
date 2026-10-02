# 语音模板迁移最新 main

## 基本信息

- 创建日期：2026-10-03，Asia/Shanghai；建档作者：orulink-wugui；实施：Codex。
- 需求：将会话中的语音模板改动从最新 main 新建功能分支并提交；无关联 Issue。
- 分支：codex/voice-application-template-20261003。
- 来源：codex/voice-application-template，aa3f652f27620dc8f4afa87044c85d04d1eb7ea7；原 PR：https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/132 。本次未新建 PR。
- 基线：远端 main=30ad9eaef9d22a89d1b276f6fca5f67b9e9cdd45，已通过本机 gh 账号查询并拉取。
- 当前状态：语音核心已提交；模板和配置命令专项通过，准备第二批提交。
- 规范：SDK 无独立 AGENTS.md、documents/dev_log/README.md 或 temp_log.md；已读取工作区的开发档案规范与模板，档案保存在 SDK 内随代码提交。

## 背景、范围与验收

迁移内置基础与可选语音模板、ASR/LLM/TTS 接口及实现、凭据配置命令和使用文档。保持默认五文件基础模板、watcher 名称、独立模型参数与凭据、Application 注入通道和 Daemon 透明路由。

仅修改 SDK；不改根仓库 gitlink 或其他子仓库，不连接硬件、不测试真实云端密钥、不发布 PyPI。本次请求为新建分支与本地提交，不以旧 PR 的推送历史代替新分支验证。

验收：新分支直接基于最新 main；来源改动完整迁移且不回退主线功能；专项回归、本地开发 CI 与打包安装通过；详细中文分组提交；工作区保持干净。

## 调查与迁移决策

- 主线自 08d4813 后已合入 Runtime 版本管理、macOS 清理兼容、下载进度与空闲应用取消选择。
- 来源 3785485 的五个文件已在主线具备等价或更完整修复：memory_maps 可调用性检查、缺失 API 清理回归、macOS 安装标识、实例路径与控制端口隔离。跳过该提交，避免重复或回退主线实现。
- 按来源 6085566（语音核心）、ab65108（模板与配置命令）、aa3f652（文档）分三批迁移并验证。
- 保留主线 huggingface-hub>=1.32,<2 依赖以及新的 Runtime/分发行为。

## TDD 与验证

本次为既有实现迁移，不新增业务行为，未伪造 Red 阶段；使用原有测试、主线回归及迁移差异检查替代。若发生需要修改逻辑的冲突，先定位并补充对应验证。未重构。

## 过程记录

### 2026-10-03｜orulink-wugui / Codex｜开始迁移

已读取工作区规范与 SDK 现有分发、取消选择档案，检索未发现进行中的语音迁移档案。SDK 初始工作区干净；根仓库存在无关改动，保持不动。远端来源分支仍为 aa3f652；最新 main 为 30ad9ea。本次不是对已有档案决策的更正，不改写其他任务历史。

## 遗留与限制

- 云端、硬件和 Windows 真机验证不属于此次迁移范围，不将历史结果表述成本次验收。
- 来源分支与 PR 保留；本次未授权合入主线或发布。

### 2026-10-03｜orulink-wugui / Codex｜语音核心提交前验证

来源 6085566 已迁移，无冲突；pyproject.toml 自动合并后保留主线 huggingface-hub>=1.32,<2。新增 voice 源码与专项测试逐文件来自原提交，未改业务逻辑。

执行 `.venv-ci/bin/python -m pytest tests/voice/test_asr.py tests/voice/test_catalog.py tests/voice/test_device.py tests/voice/test_providers.py tests/voice/test_runtime.py`：30 passed in 2.34s。`git diff --cached --check` 通过；新增行凭据字面值扫描无供应商长密钥或私钥标记。语音核心准备提交；后续迁移模板与文档后再执行完整开发 CI。

### 2026-10-03｜orulink-wugui / Codex｜模板与配置命令提交前验证

语音核心提交为 6512169。来源 ab65108 已无冲突迁移，CLI 自动合并后保留主线新增入口行为；暂存 diff 仅包含模板初始化和配置命令的原有增量。

执行 `.venv-ci/bin/python -m pytest tests/application/test_project_init.py tests/application/test_templates.py tests/test_app_cli_usability.py tests/voice/test_configuration.py tests/voice/test_configure.py tests/voice/test_template_assets.py`：91 passed in 1.57s。`pip check` 与暂存差异格式检查通过。没有新增密钥，credential-examples 保持空白模板。剩余文档与示例测试将在第三批迁移后做完整回归。
