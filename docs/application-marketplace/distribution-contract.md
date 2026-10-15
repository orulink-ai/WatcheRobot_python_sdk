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
- 官方 Dataset 只保存 `repo_id + commit` 结构化索引。源码保存在开发者公开 Space，安装必须使用名单中的固定 commit，不能跟随 Space `main`。
- SDK 负责固定快照下载、Runtime 校验与复制、每 App 独立 `.venv`、安装事务、已安装列表和卸载；Desktop 只传入锁定 Runtime 目录、调用受控子进程并展示进度。安装命令不会启动或连接 Daemon。
- 每个第三方 App 使用自己的 Python 环境。Daemon 只接收 Desktop 选择后生成的受控启动规格，不负责创建环境或安装依赖。

## 公开命令

国内 `marketplace --provider gitee` 使用 [内嵌 Git 合同](git-catalog.zh-CN.md)，
通过 Git 协议取得官方目录及每项固定提交的 app.json，源码下载和安装复用同一固定提交缓存。
终端用户不需要安装 Git；开发者上传与投稿管理操作继续使用现有 API。

下表使用发行包的独立入口。开发模式下 `watcherobot app ...` 复用同一实现。
开发者手动使用时默认不加 `--jsonl`，会得到英文摘要或表格；`marketplace --details`
用于查看完整固定来源、commit 和 Manifest。全部人工命令见
[Application CLI Quick Reference](application-cli-reference.md)。

| 命令 | 用途 | 登录 | 启动 Daemon |
| --- | --- | --- | --- |
| `watcher-distribution app check <directory> --jsonl` | 校验 `app.json`、`app.py`、SDK 兼容性、依赖与可发布文件 | 不需要 | 否 |
| `watcher-distribution app login --provider huggingface --jsonl` | 启动 OAuth Device Flow；可加 `--force` | 建立登录 | 否 |
| `watcher-distribution app login --provider huggingface --status --jsonl` | 校验 Watcher 专属凭据并返回身份 | 使用已有凭据 | 否 |
| `watcher-distribution app logout --provider huggingface --jsonl` | 只删除 Watcher 专属凭据项 | 使用已有凭据 | 否 |
| `watcher-distribution app publish --provider huggingface <directory> --jsonl` | 创建或更新公开 Space 并返回固定源码 commit；不修改官方名单 | 需要 | 否 |
| `watcher-distribution app submit --provider huggingface <directory> [--commit <sha>] --jsonl` | 校验已发布固定快照并创建或复用官方名单 PR；不上传源码 | 需要 | 否 |
| `watcher-distribution app marketplace --provider huggingface --jsonl` | 匿名读取官方名单及固定 commit 的结构化 `app.json` | 不需要 | 否 |
| `watcher-distribution app download --provider huggingface --repo-id <id> --commit <sha> --target <empty-dir> --jsonl` | 匿名下载并校验固定快照 | 不需要 | 否 |
| `watcher-distribution app install --provider huggingface --repo-id <id> --commit <sha> --store-root <dir> --runtime-root <locked-runtime-dir> --jsonl` | 下载固定快照、验证锁定 Runtime、创建每 App 独立环境并原子安装 | 不需要 | 否 |
| `watcher-distribution app list --store-root <dir> --jsonl` | 读取 SDK App Store 已安装记录 | 不需要 | 否 |
| `watcher-distribution app uninstall --store-root <dir> --app-id <id> --jsonl` | 实际删除 App 源码、独立环境及安装目录 | 不需要 | 否 |
| `watcher-distribution app cleanup --store-root <dir> --jsonl` | 重试清理历史卸载残留，返回删除/失败数量 | 不需要 | 否 |

V1 的 Space 名称固定为 `<hf_username>/WatcherRobot-<app_id>`。发布工具只能管理由当前 OAuth App 创建的同名 Space；遇到其他来源的同名仓库必须返回归属冲突。

`list` 的每个 `applications[]` 项还必须包含 `application_root`、
`application_directory` 和 `launcher.kind/executable`。这些是 Desktop 向 Daemon
提交受控启动规格的唯一安装信息来源；Desktop 不读取或定义 SDK 的 `install.json` 格式。

`marketplace` 的每个 Application 必须包含 `supported_host_platforms` 和
`host_compatible`。前者是应用作者基于实际验证声明的 `windows`/`macos` 支持范围，
后者是 SDK 对当前宿主的提示结果；两者均不阻断安装。Desktop 负责在宿主不适用时取得
用户明确确认后再继续安装。

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

### 安装阶段与依赖实时进度

安装主进度是 12 个检查点的完成比例，不代表下载字节或剩余时间。
依赖安装期间以独立线程同时消费 uv stdout/stderr，主线程将允许名单内的摘要转换为以下结构化阶段：

| stage | 实际活动 | stage_completed / stage_total |
| --- | --- | --- |
| `resolving_dependencies` | 开始解析依赖 | 0 / 3 |
| `preparing_dependencies` | 已完成解析，准备包 | 1 / 3 |
| `downloading_dependencies` | uv 报告开始/完成包下载 | 1 / 3 |
| `installing_dependency_files` | uv 报告 Prepared / Installed 或 Audited | 2 / 3 或 3 / 3 |

这些事件保持 `completed_steps=5, total_steps=12`，将阶段内实际检查点折算到整体安装进度。
`completed_items` 表示当前子阶段已报告完成的包数；`total_items` 仅在摘要明确给出总数时存在。
下载计数只统计 uv 明确报告完成的包，缓存命中和未单独报告的小包不制造下载计数。
没有可靠总量或字节回调时，只显示活动状态和已知计数，不伪造字节、速率、预计时间或按时间递增的百分比。
旧版/未知 uv 输出不作为协议透传，仍显示解析活动；原始包名、URL、路径与凭据不进入 JSONL。
重试会重新报告解析阶段，反映实际重试而非复用上一次进度。
单流输出上限仍为 1 MiB、单命令超时 300 秒；超时、超限或异常会终止受管子进程树。

### 历史卸载残留

SDK 安装、卸载前在既有写操作锁内尝试清理 `trash/<32位小写hex>-uninstall`。
也可单独运行 `app cleanup --store-root <dir>`；该命令只取得写锁，不触发共享 Runtime GC。
仅删除普通直接子目录，不跟随 symlink/junction；Windows 只读属性在受管普通项上按需清除。
保留 `-previous` 回滚目录、失败安装事务、正在使用的 apps、共享 Runtime 与 Git/uv 缓存。
清理某项失败时继续处理其它项，失败项保留以便下次重试；自动清理不让历史占用阻断新安装或卸载。
显式 cleanup 成功返回 `removed_count`、`failed_count=0`；若有失败则返回相同计数的 `error.details`、`internal_error` 和退出码 5。
成功卸载检查应用根与本次卸载临时根均已消失；删除失败仅尽力恢复尚存文件，不能恢复已经删除的文件。

### 下载字节进度

`downloading_snapshot` 的 `data` 可包含以下字段，均描述当前固定快照，不能按文件数或安装阶段推算百分比：

```json
{"type":"progress","stage":"downloading_snapshot","message":"Downloading immutable Application source","data":{"downloaded_bytes":4096,"total_bytes":16384,"download_attempt":0}}
```

- `downloaded_bytes`：当前尝试已取得的源码字节累计值，不是单块增量。Hugging Face 使用源码重建字节，不能与 Xet 压缩传输字节混算；Gitee 在实际读取并验证 blob 后累计其字节。
- `total_bytes`：下载开始前确定的固定总量；无法确定时为 `null`。Hugging Face dry-run 排除完整缓存文件；续传保留的部分字节计入已取得字节，其完整文件大小仍计入总量。全部命中缓存时总量为零，保持不定进度直到进入校验阶段，不伪造百分比。
- `download_attempt`：从零开始的重试代数。HTTP 续传被拒绝、已取得字节被丢弃时增加代数；新代数允许计数回退，同一代数内累计值不得回退。它不是应用安装 operation ID。
- HF 中间字节回调最多每 100 毫秒上报一次，首次、显式重试和实际达到总量时立即上报；节流只减少事件数量，不插值、不推算字节。Desktop 在新代数显示“重试下载”提示。
- 旧适配器可以仅发送阶段事件；缺少字节字段、总量未知或非正数时，源码字节进度保持未知。整体进度可使用独立的实际安装检查点；校验和安装不继承源码下载百分比。
- Desktop 应拒绝已结束或不匹配 operation 的事件及旧 sequence/attempt；同一 attempt 的分母异常变化应降级为不定进度，不能用历史最大百分比掩盖变化。

Hugging Face 字节回调使用 `snapshot_download(dry_run=True)` 与 `tqdm_class`，依赖下限为 **huggingface-hub 1.32**（小于 2）。1.32.0 的真实下载器已通过 HTTP mock transport 验证分块、缓存、续传和续传拒绝后重试；不能依赖旧版只报告文件数的回调。新 SDK 源码需要通过源码开发运行时或重新构建的分发产物接入；旧的 Desktop SDK commit 锁与冻结程序不会自动获得此能力。

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
| Application 校验 | `app_manifest_missing`、`app_entrypoint_missing`、`app_manifest_invalid`、`app_sdk_incompatible`、`app_dependency_invalid`、`app_lock_missing`、`app_lock_invalid`、`app_lock_incompatible`、`app_content_forbidden` |
| OAuth 与凭据 | `auth_required`、`auth_denied`、`auth_expired`、`auth_invalid_response`、`auth_network_error`、`credential_store_error` |
| Space 与官方名单 | `space_ownership_conflict`、`catalog_invalid`、`catalog_pr_conflict`、`remote_error` |
| Application Runtime | `runtime_manifest_invalid`、`runtime_resources_missing`、`runtime_python_integrity_failed`、`runtime_uv_integrity_failed`、`runtime_sdk_wheel_integrity_failed` |
| 生命周期 | `operation_cancelled`、`internal_error` |

`auth_network_error` 属于远程错误并返回退出码 4；`operation_cancelled` 返回 130；
Application Runtime 完整性错误与未单独分类的 `internal_error` 返回 5。Runtime 错误只区分
清单、缺失资源和三个受锁定内容保护的组件，不在 JSONL 中包含本机路径、原始命令输出或
traceback。Desktop 应以稳定错误码选择 UI 状态，以退出码判断进程类别，不能匹配
`message` 文案。

schema v3 Application 必须提供 `app.lock.json`。文件缺失时返回 `app_lock_missing`；
文件不可读、JSON 或结构非法、依赖不是精确 pin 时返回 `app_lock_invalid`；锁定的 SDK
或其他依赖不满足 Manifest 声明时返回 `app_lock_incompatible`。三者均属于本地校验失败并
返回退出码 2，消息会提示开发者使用 `watcherobot-app-lock` 重新生成依赖锁。

## 发布、名单与固定快照

1. `check` 先确定公开文件集合，排除 `.venv`、凭据、缓存、VCS 与本机路径文件。
2. `publish` 创建或更新公开 static Space，并用完整文件集合形成提交；相同内容不制造新 commit。它只返回 `repo_id + commit + source_url`，不读取或修改官方 Catalog，也不要求应用广场展示字段完整。
3. `submit` 要求 `description`、`author` 均为非空，`icon` 可选。在任何 Catalog 写入前读取并校验已发布 commit 上的 `app.json`；填写图标时额外校验该固定 commit 上的图标文件，未填写时由展示端使用默认 WatcherRobot Application 图标。同时要求本地 Manifest 与固定快照一致。省略 `--commit` 时提交当前 Space HEAD；显式传入时只接受完整 40 位小写 SHA。
4. `submit` 向官方公开 Dataset `Orulink/watcherobot-app-store` 创建名单 PR，但绝不上传或改写 Space 源码。PR 描述直接展示固定快照的 Manifest 摘要和源码链接；存在自选图标时展示固定版本图标，否则标记使用默认图标。Catalog 文件仍只保存 `repo_id + commit`。相同 commit 的开放 PR 复用；不同 commit 遇到已有开放 PR 返回 `catalog_pr_conflict`，V1 不自动改写旧 PR，也不创建第二个 PR。
5. 维护者合并 PR 后，`marketplace` 才会从 Dataset 主分支看到该记录。
6. `download` 必须同时收到完整 40 位 commit 和空目录，校验实际解析 commit、文件边界、Manifest、固定 `app.py`、SDK 兼容性以及 Space/App 身份一致性。`install` 复用此校验，并在 SDK App Store 中校验或复制锁定 Runtime、创建该 App 的隔离环境、安装依赖、编译入口、校验 SDK 导入，最后原子写入 `install.json`。失败候选不得成为已安装 App。
7. Space `main` 后续变化不影响已安装固定快照；名单删除只阻止新的官方安装，不应禁用已经安装的 App。

## Desktop 集成与验证证据

Daemon 管理接口的版本握手、兼容矩阵和日志安全边界见
[Daemon 控制协议](../contracts/daemon-control-protocol.md)。Application Runtime 缺少
`runtime.json` 时返回 `runtime_resources_missing`；文件存在但无法解析、结构错误或路径声明非法时返回
`runtime_manifest_invalid`。所有 Runtime 错误只公开稳定错误码和消息，不透传底层异常内容。

- Desktop 当前实现说明：`WatcheRobot_client/Watcher Desktop App/docs/application-store-implementation.zh-CN.md`。
- Desktop 当前发行检查：`npm run test:packaged-runtime`、`npm run test:application-marketplace-ui`、`npm run test:application-marketplace-s9-live`。
- SDK 合同检查：`.venv\\Scripts\\python -m pytest -o addopts='' -q tests/distribution`。
- SDK 静态与依赖检查：`.venv\\Scripts\\python -m mypy src/watcherobot`、`.venv\\Scripts\\python -m pip check`。
- 跨仓路由边界检查：`python C:\\Users\\Administrator\\.codex\\skills\\watcher-daemon-app-migration-guard\\scripts\\verify_migration_state.py --mode post`。

实施阶段的详细 TDD 数量、真实 OAuth 和 Hugging Face 联调记录见 [实施进度](implementation-progress.md) 与 [OAuth 记录](hugging-face-oauth.md)。

### 请求限流
`rate_limited`（退出码 4）：公开应用源明确返回 429，或 403 响应包含限流标记。不得把所有 403 都推断为限流。SDK 不自动重试，不切换凭据；Desktop 只合并进行中的并发刷新；后续手动刷新重新请求，不再以60秒本地冷却返回上一次失败。持续显示可用缓存。


### 广场请求截止与实际状态

国内 marketplace、download、install 使用 Dulwich 匿名 Git HTTPS 获取，具体截止、历史提交回退与缓存上限见 [内嵌 Git 合同](git-catalog.zh-CN.md)。桌面商店仍没有固定 120 秒进程截止；用户可以取消。失败不会覆盖有效展示缓存，SDK 不自动切换凭据或 Gitee API。

没有实际 HTTP 响应时不填虚构状态，不暴露 Git 服务端原始错误正文。上传与投稿仍使用既有 API；Git 服务也可能受平台独立限制，不能承诺永不限流。
