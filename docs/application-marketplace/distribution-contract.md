# Application 分发合同

本文是 Desktop 调用 SDK 分发工具时的当前稳定合同。实现真值位于 `src/watcherobot/distribution/`；本文由测试核对版本、公开命令、错误码与退出码，避免跨仓库只依赖零散实施记录。

## 版本与产物来源

| 项目 | 当前值 | 真值来源 |
| --- | --- | --- |
| SDK 包版本 | 运行时读取 `watcherobot.__version__` | `src/watcherobot/__init__.py` |
| 支持的 Python | `>=3.10` | `pyproject.toml` |
| 分发控制台入口 | `watcher-distribution` | `pyproject.toml` |
| Desktop Runtime/分发工具的 SDK 来源 | 读取 `runtime-build.json` 的 `sdk_commit` | Desktop `src-tauri/resources/server/<platform>/runtime-build.json` |
| 每 App 环境的 SDK wheel 来源 | 读取 `runtime.json` 的 `watcherobot.sdk_commit` | Desktop `src-tauri/resources/server/<platform>/app-runtime/runtime.json` |
| 每 App 环境的 Python/uv 锁 | Python `3.12.13`、uv `0.11.16` | Desktop `scripts/application-runtime-lock.json` |

版本号说明兼容范围，Desktop 的来源 manifest 记录实际随包字节的提交和工具版本。SDK 文档不得自我硬编码当前 SDK commit，否则文档提交会再次改变该 commit；调用方也不得根据仓库当前 HEAD 猜测已经打入发行包的版本。

## 职责边界

- SDK 仓库是 Daemon 的唯一源码仓库，但分发 CLI 与 Daemon 运行时解耦；Daemon 不访问 Hugging Face，也不持有 Hugging Face Token。
- Desktop 只能以受控子进程调用随包 `watcher-distribution`，按参数数组传参并消费 stdout JSONL；不得用 shell 拼接命令。
- 登录、源码发布和 Catalog 提交使用 Watcher 专属系统凭据项。公开广场读取与固定快照下载不得读取 Hugging Face CLI、本机环境变量或 Watcher OAuth 凭据。
- 官方 Dataset 只保存 `space_id + commit` 结构化索引。源码保存在开发者公开 Space，安装必须使用名单中的固定 commit，不能跟随 Space `main`。
- SDK 负责固定快照下载、Runtime 校验与复制、每 App 独立 `.venv`、安装事务、已安装列表和卸载；Desktop 只传入锁定 Runtime 目录、调用受控子进程并展示进度。安装命令不会启动或连接 Daemon。
- 每个第三方 App 使用自己的 Python 环境。Daemon 只接收 Desktop 选择后生成的受控启动规格，不负责创建环境或安装依赖。

## 公开命令

下表使用发行包的独立入口。开发模式下 `watcherobot app ...` 复用同一实现。
开发者手动使用时默认不加 `--jsonl`，会得到英文摘要或表格；`marketplace --details`
用于查看完整固定来源、commit 和 Manifest。全部人工命令见
[Application CLI Quick Reference](application-cli-reference.md)。

| 命令 | 用途 | 登录 | 启动 Daemon |
| --- | --- | --- | --- |
| `watcher-distribution app check <directory> --jsonl` | 校验 `app.json`、`app.py`、SDK 兼容性、依赖与可发布文件 | 不需要 | 否 |
| `watcher-distribution app login --jsonl` | 启动 OAuth Device Flow；可加 `--force` | 建立登录 | 否 |
| `watcher-distribution app login --status --jsonl` | 校验 Watcher 专属凭据并返回身份 | 使用已有凭据 | 否 |
| `watcher-distribution app logout --jsonl` | 只删除 Watcher 专属凭据项 | 使用已有凭据 | 否 |
| `watcher-distribution app publish <directory> --jsonl` | 创建或更新公开 Space 并返回固定源码 commit；不修改官方名单 | 需要 | 否 |
| `watcher-distribution app submit <directory> [--commit <sha>] --jsonl` | 校验已发布固定快照并创建或复用官方名单 PR；不上传源码 | 需要 | 否 |
| `watcher-distribution app marketplace --jsonl` | 匿名读取官方名单及固定 commit 的结构化 `app.json` | 不需要 | 否 |
| `watcher-distribution app download --space-id <id> --commit <sha> --target <empty-dir> --jsonl` | 匿名下载并校验固定快照 | 不需要 | 否 |
| `watcher-distribution app install --space-id <id> --commit <sha> --store-root <dir> --runtime-root <locked-runtime-dir> --jsonl` | 下载固定快照、验证锁定 Runtime、创建每 App 独立环境并原子安装 | 不需要 | 否 |
| `watcher-distribution app list --store-root <dir> --jsonl` | 读取 SDK App Store 已安装记录 | 不需要 | 否 |
| `watcher-distribution app uninstall --store-root <dir> --app-id <id> --jsonl` | 将一个 App 移入可恢复本地回收目录 | 不需要 | 否 |

V1 的 Space 名称固定为 `<hf_username>/WatcherRobot-<app_id>`。发布工具只能管理由当前 OAuth App 创建的同名 Space；遇到其他来源的同名仓库必须返回归属冲突。

`list` 的每个 `applications[]` 项还必须包含 `application_root`、
`application_directory` 和 `launcher.kind/executable`。这些是 Desktop 向 Daemon
提交受控启动规格的唯一安装信息来源；Desktop 不读取或定义 SDK 的 `install.json` 格式。

## JSONL 事件

stdout 每行必须是一个完整 JSON 对象，事件类型只允许 `progress`、`result`、`error`。JSONL 使用 ASCII 安全的 Unicode 转义，确保 Windows 打包进程不受活动控制台代码页影响且每行始终是有效 UTF-8。`message` 统一使用英文，但仍然只是辅助文案；人类进度和第三方库进度只能进入 stderr。事件不包含 Token、Device Code、时间戳或 traceback。

```json
{"type":"progress","stage":"checking","message":"Validating Application"}
{"type":"result","ok":true,"data":{"app_id":"com.example.demo"}}
{"type":"error","ok":false,"code":"app_manifest_invalid","message":"Application manifest is invalid"}
```

- 一次成功命令以一条 `result` 结束。
- 一次失败命令以一条 `error` 结束，并同时返回下表中的进程退出码。
- `progress.data`、`result.data` 和 `error.details` 是命令相关结构化字段；调用方必须按事件类型读取，不能解析 `message` 文案判断业务状态。
- Desktop 取消任务时应先终止分发子进程树；若命令自行观察到取消，则返回 `operation_cancelled`。

## 退出码

| 名称 | 数值 | 含义 |
| --- | --- | --- |
| `SUCCESS` | `0` | 成功 |
| `VALIDATION_ERROR` | `2` | 本地 Application、参数或内容校验失败 |
| `AUTH_ERROR` | `3` | 未登录、授权拒绝/过期或凭据存储失败 |
| `REMOTE_ERROR` | `4` | Hugging Face 网络、远程资源、名单或 PR 冲突 |
| `INTERNAL_ERROR` | `5` | 未归入前述类别的内部失败 |
| `CANCELLED` | `130` | 用户取消 |

## 稳定错误码

| 类别 | 错误码 |
| --- | --- |
| Application 校验 | `app_manifest_missing`、`app_entrypoint_missing`、`app_manifest_invalid`、`app_sdk_incompatible`、`app_dependency_invalid`、`app_content_forbidden` |
| OAuth 与凭据 | `auth_required`、`auth_denied`、`auth_expired`、`auth_invalid_response`、`auth_network_error`、`credential_store_error` |
| Space 与官方名单 | `space_ownership_conflict`、`catalog_invalid`、`catalog_pr_conflict`、`remote_error` |
| 生命周期 | `operation_cancelled`、`internal_error` |

`auth_network_error` 属于远程错误并返回退出码 4；`operation_cancelled` 返回 130；未单独分类的 `internal_error` 返回 5。Desktop 应以稳定错误码选择 UI 状态，以退出码判断进程类别，不能匹配 `message` 文案。

## 发布、名单与固定快照

1. `check` 先确定公开文件集合，排除 `.venv`、凭据、缓存、VCS 与本机路径文件。
2. `publish` 创建或更新公开 static Space，并用完整文件集合形成提交；相同内容不制造新 commit。它只返回 `space_id + commit + source_url`，不读取或修改官方 Catalog，也不要求应用广场展示字段完整。
3. `submit` 要求 `description`、`author` 均为非空，`icon` 可选。在任何 Catalog 写入前读取并校验已发布 commit 上的 `app.json`；填写图标时额外校验该固定 commit 上的图标文件，未填写时由展示端使用默认 WatcherRobot Application 图标。同时要求本地 Manifest 与固定快照一致。省略 `--commit` 时提交当前 Space HEAD；显式传入时只接受完整 40 位小写 SHA。
4. `submit` 向官方公开 Dataset `Orulink/watcherobot-app-store` 创建名单 PR，但绝不上传或改写 Space 源码。PR 描述直接展示固定快照的 Manifest 摘要和源码链接；存在自选图标时展示固定版本图标，否则标记使用默认图标。Catalog 文件仍只保存 `space_id + commit`。相同 commit 的开放 PR 复用；不同 commit 遇到已有开放 PR 返回 `catalog_pr_conflict`，V1 不自动改写旧 PR，也不创建第二个 PR。
5. 维护者合并 PR 后，`marketplace` 才会从 Dataset 主分支看到该记录。
6. `download` 必须同时收到完整 40 位 commit 和空目录，校验实际解析 commit、文件边界、Manifest、固定 `app.py`、SDK 兼容性以及 Space/App 身份一致性。`install` 复用此校验，并在 SDK App Store 中校验或复制锁定 Runtime、创建该 App 的隔离环境、安装依赖、编译入口、校验 SDK 导入，最后原子写入 `install.json`。失败候选不得成为已安装 App。
7. Space `main` 后续变化不影响已安装固定快照；名单删除只阻止新的官方安装，不应禁用已经安装的 App。

## Desktop 集成与验证证据

- Desktop 当前实现说明：`WatcheRobot_client/Watcher Desktop App/docs/application-store-implementation.zh-CN.md`。
- Desktop 当前发行检查：`npm run test:packaged-runtime`、`npm run test:application-marketplace-ui`、`npm run test:application-marketplace-s9-live`。
- SDK 合同检查：`.venv\\Scripts\\python -m pytest -o addopts='' -q tests/distribution`。
- SDK 静态与依赖检查：`.venv\\Scripts\\python -m mypy src/watcherobot`、`.venv\\Scripts\\python -m pip check`。
- 跨仓路由边界检查：`python C:\\Users\\Administrator\\.codex\\skills\\watcher-daemon-app-migration-guard\\scripts\\verify_migration_state.py --mode post`。

实施阶段的详细 TDD 数量、真实 OAuth 和 Hugging Face 联调记录见 [实施进度](implementation-progress.md) 与 [OAuth 记录](hugging-face-oauth.md)。
