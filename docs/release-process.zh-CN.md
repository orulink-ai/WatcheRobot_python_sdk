# WatcheRobot Python SDK 发布流程

本文面向 SDK 维护者，说明 `watcherobot` 版本从发布请求到 PyPI 正式可用的完整门禁。普通 Application 开发者只需阅读[安装文档](installation.zh-CN.md)。

## 当前正式版本

- PyPI：[`watcherobot 0.1.1`](https://pypi.org/project/watcherobot/0.1.1/)
- GitHub Release：[v0.1.1](https://github.com/orulink-ai/WatcheRobot_python_sdk/releases/tag/v0.1.1)
- 首次正式发布成功流水线：[GitHub Actions #32171032139](https://github.com/orulink-ai/WatcheRobot_python_sdk/actions/runs/32171032139)

PyPI 和 TestPyPI 均使用 GitHub Actions OIDC Trusted Publishing，不保存长期 API Token。生产环境仍必须通过 GitHub `pypi` Environment 的指定审核人批准。

## 标准发布链路

1. 由明确的版本请求创建 `release/watcherobot-<version>` 分支和带 `release:version` 标签的版本 PR。
2. 版本 PR 经审查后以 merge commit 合入；正式 Tag 必须指向该双亲 merge commit，并使用 annotated `v<version>` Tag。
3. `release.yml` 从 Tag 构建 wheel 和 sdist，执行测试、打包检查并生成 `SHA256SUMS`，随后创建不可变的草稿 GitHub Release。
4. 原始产物先通过 OIDC 发布到 TestPyPI。流水线下载同一 wheel，核对哈希并执行版本、依赖和 CLI 验收。
5. 稳定版进入 GitHub `pypi` Environment，等待指定负责人审核。审核通过后，同一批原始产物通过 OIDC 发布到正式 PyPI。
6. 流水线从正式 PyPI 强制全新安装精确版本，核对完整文件集合与 SHA256，执行 `pip check`、版本导入和 `watcherobot --help`。
7. 全部验收成功后，草稿 GitHub Release 才转为正式发布。

不得跳过版本 PR、双亲 merge commit、TestPyPI 验收或生产环境审核；不得把 PyPI Token 写入 Git、Actions Secret、飞书消息或本机脚本。

## Trusted Publishing 配置

正式 PyPI 项目 `watcherobot` 的 GitHub Publisher 必须与 Actions Token claims 完全一致：

| 字段 | 值 |
|---|---|
| Owner | `orulink-ai` |
| Repository | `WatcheRobot_python_sdk` |
| Workflow | `release.yml` |
| Environment | `pypi` |

TestPyPI 使用相同仓库和工作流，并绑定 `testpypi` Environment。GitHub `pypi` Environment 允许 `v*` Tag 进入标准发布，并允许 `main` 发起受保护的恢复发布；两种入口均不能绕过环境审核。

当 `uv publish --trusted-publishing always` 返回 `invalid-publisher` 时，先比对错误输出中的 `repository`、`job_workflow_ref` 和 `environment` claims，不能通过创建长期 Token 绕过配置错误。

## 正式发布恢复

只有 Tag 构建产物已通过 TestPyPI、正式 PyPI 尚未发布，且同一 Tag 的 GitHub Release 仍是匹配草稿时，才使用 `release.yml` 的 `workflow_dispatch`，从 `main` 传入 `recover_tag`。

恢复任务会重新校验 Tag、版本 PR、双亲 merge commit、TestPyPI 文件集合、草稿 Release 目标 commit 和原始 SHA256；它只下载并发布草稿中的不可变产物，不重新构建。随后仍需 `pypi` Environment 审核，并重复正式 PyPI 文件、哈希、全新安装和 CLI 验收。

如果 PyPI 已存在同版本但文件集合或哈希不一致，必须停止并人工调查，禁止覆盖、删除或重新上传同版本。

## 发布完成检查

- GitHub Actions 结论为 `success`。
- PyPI 精确版本页面同时包含 wheel 和 sdist。
- PyPI 文件 SHA256 与 `SHA256SUMS` 和 GitHub Release 资产一致。
- 干净环境可安装 `watcherobot==<version>`，且 `watcherobot.__version__` 精确匹配。
- `pip check`、`watcherobot --help` 均成功。
- GitHub Release 为正式版本，而不是 Draft 或 Prerelease（预发布版本除外）。
- `CHANGELOG.md`、README、安装文档和飞书知识库已同步。
