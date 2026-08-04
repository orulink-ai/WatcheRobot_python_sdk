# Application 广场改造实施进度

本文档记录 `WatcheRobot_python_sdk` 中 Application 广场相关改造的工程事实、验证结果和下一步入口。总体设计与跨仓库决策以外部《应用广场改造实施大纲》为准；本文件只记录 SDK 仓库内已经完成并经过验证的内容。

## 不可破坏的边界

- `WatcheRobot_python_sdk` 是 Daemon 的唯一源码仓库。
- Application 分发工具与 Daemon 运行时模块必须解耦；Daemon 不直接登录或访问 Hugging Face。
- Daemon 只负责 Application 的选择、启动、停止、状态、日志与通道路由，不根据业务消息内容增加旁路。
- 每个第三方 Application 使用独立 Python 环境；Daemon 使用桌面端随包提供的公共运行环境。
- Application 广场目录只保存结构化索引；Application 源码位于开发者自己的公开 Hugging Face Space，并按固定 commit 安装。

## 分支与基线

- 实施分支：`codex/application-marketplace`
- 基线分支：`origin/main`
- 基线提交：`6d5710af9258178851e47df2889de60a66c5fb37`
- 本地开发解释器：Python 3.13.5
- 本地隔离环境：仓库根目录 `.venv`（已由 `.gitignore` 排除）

Python 3.13.5 仅是当前 SDK 开发基线，不代表桌面发行包最终锁定的 Python 小版本。发行版 Python 小版本将在每 App 独立环境和默认 Application 拆包实施前单独确认。

## 阶段进度

### S0：工程开工基线——已完成

初次直接使用全局 Python 执行测试时，测试收集因缺少 `bleak` 失败。这是本机开发依赖未安装，并非代码回归。随后在仓库内创建 `.venv`，以 editable 模式安装 `.[test]`，重新完成基线验证。

验证结果：

- `python -m pytest -o addopts='' -q`：285 项通过。
- `python -m mypy src/watcherobot`：57 个源码文件无问题。
- `python -m pip check`：无损坏或冲突依赖。
- `git diff --check`：通过。

修改前行为基线：

- CLI 已提供 `app run/package/install/list/select/start/stop/uninstall`；除本地 `package` 外，`install/list/select/start/stop/uninstall` 都会先确保 Daemon 运行，再调用 Daemon REST。
- `ApplicationManifest.load()` 严格要求同一目录内存在 `app.json` 和 `app.py`，拒绝未知/缺失字段，校验 schema、App id、语义版本、SDK 版本范围、依赖数组和目录内图标路径。
- `ApplicationCatalog` 当前由 Daemon 持有，负责 `.wapp` 解压、校验、版本目录、选择记录和删除；对应 `/daemon/applications*` REST 是后续迁移对象，而不是在线商店最终入口。
- `ApplicationRuntimeManager` 当前默认以 Daemon 自身 `sys.executable` 启动 Application，并通过环境变量注入 App id、运行凭据、Desktop channel 和 Device channel。
- 当前行为没有独立分发模块、JSONL 事件合同、`app check/login/publish/download` 或 Hugging Face 依赖。

### S1：SDK 分发合同——已完成

已完成第一个小步：

- 新增独立 `watcherobot.distribution` 模块；Daemon 启动路径未导入该模块。
- 固定 `progress`、`result`、`error` 三类一行一对象 JSONL 合同。
- 固定校验、认证、远程、内部和取消五类进程退出码。
- 建立首批稳定错误码；事件不包含时间戳、Token、traceback 或普通日志。
- TDD 证据：新增测试首次因模块不存在而收集失败；实现后定向 3 项通过，全量 288 项通过，mypy 59 个源码文件通过。
- 定义最小 `OAuthClient`、`CredentialStore`、`HubClient` Protocol，以及不在 `repr/str` 暴露明文的 `AccessToken` 值对象；测试通过注入 Fake 验证替换边界。
- Protocol TDD 证据：新增测试首次因 `distribution.ports` 不存在而收集失败；实现后分发定向 6 项通过，全量 291 项通过，mypy 60 个源码文件通过。
- 回查既有 C-27 决策后，JSONL 成功事件固定为平铺的 `type=result, ok=true, data`，失败事件固定为平铺的 `type=error, ok=false, code, message`；不使用嵌套 `error` 对象。
- 新增不启动 Daemon 的 `watcherobot app check <directory>`；人类输出与 `--jsonl` 机器输出共用 `check_application()`，后者直接复用唯一 `ApplicationManifest.load()`。
- 有效目录会返回完整结构化 Manifest 信息；TDD 首次因 `distribution.check` 不存在而收集失败，实现后相关定向 6 项通过，全量 294 项通过，mypy 61 个源码文件通过。
- 唯一 Manifest 校验器现在携带稳定错误码，并用 `packaging.Requirement` 校验每条第三方依赖；标准版本范围、extras 和直接 URL 继续允许，不增加依赖源白名单。
- `app check --jsonl` 已分别覆盖 Manifest 缺失、固定 `app.py` 缺失、未知字段、非法 Python requirement 和 SDK 不兼容；失败只输出 JSONL，无 traceback。定向 20 项和全量 301 项通过。
- 新增供 `check` 与后续 `publish` 共用的源码文件选择器：排除 `.venv`/`venv`、缓存、VCS、编辑器配置、`.env` 凭据、构建产物、`.wapp` 和 `pyvenv.cfg`/`.pth` 本机路径文件；`.env.example` 仍允许作为公开模板。
- 本地 `.venv` 的存在不阻止开发者执行 `app check`，但其内容不会进入上传集合；未排除的符号链接会以 `app_content_forbidden` 拒绝，避免固定快照逃逸。定向 11 项和全量 304 项通过，mypy 62 个源码文件通过。
- S1 总门禁：独立进程证明导入 Daemon 入口时没有加载任何 `watcherobot.distribution` 模块；分发测试 18 项、关键路由与 Application 生命周期测试 20 项、SDK 全量 305 项全部通过；迁移守卫 post 模式通过。

S1 已满足门禁，真实 OAuth、凭据库和 Hub 网络实现仍未接入。

S1 已完成小步：

1. JSONL `progress`、`result`、`error` 事件格式及稳定错误码。
2. `app check`，复用现有 Application manifest 解析与校验能力。
3. 为后续 Hugging Face 发布、目录提交和安装定义可替换接口及 fake 实现。

### S2：Hugging Face 登录——已完成

OAuth 前置核对已完成：官方文档确认 Public App 支持 Device Code OAuth，四个既定 scope 均有效；对当前 Client ID 的脱敏实测返回 300 秒设备码，未授权 Token 轮询返回 `authorization_pending`。详细证据见 `hugging-face-oauth.md`。

- 已新增不依赖网络、系统凭据或 Daemon 的登录编排服务，固定当前 Public Client ID 和四个最小 scope。
- `DeviceAuthorization.device_code` 与 `AccessToken` 均不会进入 `repr/str`；等待事件只包含验证网址、用户码和有效期。
- Fake 覆盖成功、`authorization_pending` 等待、服务端 slow down、拒绝、服务端/本地过期、网络失败和用户取消；只有 `whoami` 成功后才保存 Token。
- TDD 首次因 DeviceAuthorization 与 login 模块不存在而收集失败；实现后分发测试 26 项、全量 313 项通过，mypy 63 个源码文件通过。
- 新增 `HuggingFaceOAuthClient` 与可替换表单 HTTP Transport，严格调用官方 Device/Token 端点；缺失 `interval` 时使用实测兼容默认值 5 秒。
- Token 轮询解析 `authorization_pending`、`slow_down`、`access_denied`、`expired_token`，校验 Bearer 类型和授予 scope；HTTP 响应 payload 默认不进入对象 `repr`。
- OAuth HTTP TDD 首次因模块不存在而收集失败；Fake Transport 定向 11 项、全量 324 项通过，mypy 64 个源码文件通过。
- 新增 `SystemCredentialStore`，只操作服务 `ai.orulink.watcher-desktop.huggingface` 下的 `oauth-access-token` 条目；读取、保存、删除错误均脱敏，删除不存在条目幂等。
- 生产依赖锁定 `keyring>=25.7,<26`；Windows 真实后端识别为 `WinVaultKeyring`，使用独立 validation 服务名完成随机值写入/读取/删除闭环且已清理。
- 凭据 TDD 首次因模块不存在而收集失败；Fake Keyring 4 项、全量 328 项、mypy 65 个源码文件和 `pip check` 均通过。
- 新增最小 `HuggingFaceHubClient.whoami()`，只访问官方 `api/whoami-v2`；区分无效/过期 Token、Hub 暂时不可用和身份响应非法，所有异常均不包含 Authorization header 或响应正文。
- Hub HTTP TDD 首次因模块不存在而收集失败；Fake Transport 8 项、全量 336 项通过，mypy 66 个源码文件通过。
- 登录编排现已优先复用 Watcher 专用凭据中的有效 Token，并通过 `whoami` 重新确认身份；`force` 可显式跳过复用，重新执行 Device Flow。
- 新增登录状态查询与退出服务：无效或过期 Token 只清理 Watcher 自己的凭据条目；Hub 网络暂时失败时保留本地 Token 并返回稳定错误；退出同样只删除该精确条目。
- 登录结果通过 `reused` 明确区分缓存复用与新授权；凭据读写异常、身份验证异常和 OAuth 非法响应均映射为稳定、脱敏的认证错误。
- 本小步定向登录测试 14 项、SDK 全量 343 项通过，mypy 66 个源码文件通过。
- CLI 已新增 `app login`、`app login --status`、`app logout`，登录可用 `--force` 跳过缓存；三者均提供人类输出和 `--jsonl` 机器输出，且在 Daemon 启动分支之前直接调用分发服务。
- Device Flow 的 JSONL `progress` 只输出授权网址、用户码和有效期；成功与错误事件均不包含访问 Token、Device Code 或 traceback。远程认证错误按既定退出码返回。
- CLI TDD 首次因 `login/logout` 子命令不存在而 5 项失败；最小实现后 CLI 与登录定向测试 19 项、SDK 全量 348 项通过，mypy 66 个源码文件、`pip check` 与迁移守卫 post 模式均通过。
- 首次使用普通账号执行真实 Device Flow 时，浏览器授权成功，但 Token 轮询遇到一次临时网络错误后立即终止；随后同一端点的脱敏连通性复查成功，确认需要增强瞬时网络容错，而不是修改 OAuth scope 或凭据合同。
- 设备码申请、Token 轮询和身份查询现在分别最多尝试 3 次；只重试网络错误，拒绝、过期、非法响应和身份权限错误仍立即失败。Token 轮询以连续失败计数，收到正常 pending/slow-down 响应后重置计数，并继续服从 Device Code 总有效期。
- 网络重试 TDD 首次新增 5 项全部失败；实现后认证定向测试 24 项、SDK 全量 353 项通过，mypy 66 个源码文件、`pip check` 与迁移守卫 post 模式均通过。
- 使用非 `Orulink` 管理员账号 `tianguiti` 完成第二次真实 Device Flow：浏览器显示的权限仅为创建/管理本 OAuth App 创建的仓库以及创建讨论/PR，并明确不能访问该账号的其他仓库；CLI 返回身份且未输出 Token。
- Windows 系统凭据真实闭环已通过：`login --status` 返回 `logged_in=true`，普通 `login` 返回 `reused=true` 且不发起新授权，`logout` 后连续状态查询返回 `logged_in=false`。验收结束时 Watcher 专用 OAuth 条目已清理。

下一步唯一入口：进入 S3，先用 Fake Hub 固定公开 Space 的创建/更新、源码上传、完整 commit 和官方名单 PR 的服务合同，再接入真实 Hugging Face HTTP 实现。

### S3：Space 发布与官方名单 PR——进行中

- 已确认 Space 唯一命名规则为 `<hf_username>/WatcherRobot-<app_id>`；例如 `tianguiti/WatcherRobot-com.orulink.demo`。
- 同一 Hugging Face 身份与同一 `app.json.id` 始终映射到同一个 Space；若同名 Space 不是由 Watcher Desktop OAuth App 创建，则发布必须失败，不得改写开发者已有仓库。
- CLI 仍保持 `watcherobot app publish <directory>`，V1 不增加要求开发者填写 Space ID 的参数。
- Space 只作为公开源码仓库：创建为 `static` 类型，但发布工具不生成 `index.html`、落地页或其他运行页面。开发者 README 正文保留，只在远端上传快照补齐 Hugging Face 必需元数据；没有 README 时生成远端最小仓库说明，不修改本地项目。Desktop 后续直接打开固定 commit 文件树。
- 名单申请采用单开放 PR 规则：正式名单已是相同 commit 时返回 `already_listed`；相同 commit 的开放 PR 返回原 URL 和 `pending`；不同 commit 遇到开放 PR 时返回 `catalog_pr_conflict`，等待原 PR 合并或关闭。V1 不自动改写开放 PR，也不创建第二个并行 PR。
- 已建立独立 `PublishHubClient` 发布端口，固定公开 Space 创建、精确源码替换、完整 commit 读取、带父 commit 的名单读取、开放 PR 枚举和名单 PR 创建六个远端边界；本地路径和生成内容不会出现在值对象表示中，commit 值对象只接受 40 位小写 SHA。TDD 首次因发布端口类型不存在而收集失败，最小实现后新旧端口测试 7 项和定向 mypy 通过。
- 已实现确定性的 Space 上传快照准备：继续复用 S1 源码选择器；开发者 README 正文只在远端副本补齐 `sdk: static`，本地文件不变；无 README 时仅生成远端仓库说明；不生成 `index.html` 或落地页。未闭合的 README YAML 和非 UTF-8 内容作为本地源码错误拒绝。TDD 首次因 `publish_files` 模块不存在而收集失败，实现后上传快照与源码选择定向测试 5 项通过。
- 已固定官方名单的纯函数合同：根对象必须是数组，每项只允许 `space_id + 40 位小写 commit`，重复 Space、短 SHA、未知字段和损坏 UTF-8/JSON 全部拒绝；首次收录追加记录，新版本原位更新且不重排其他 App。PR 标题编码 Space 与 commit；正式名单相同返回 `already_listed`，相同开放 PR 返回 `pending`，不同开放 PR 返回 `catalog_pr_conflict`。TDD 首次因模块不存在而收集失败，实现后 14 项名单规划测试通过。
- 已完成不依赖真实网络的发布编排：严格检查和上传快照准备先于凭据/远端调用；登录身份唯一生成 `<username>/WatcherRobot-<app_id>`；随后依次确认 Space、替换源码、读取完整 commit、读取名单/开放 PR 并创建最小 PR。Fake 同时覆盖新建与更新 Space、缺少登录、同名仓库归属冲突、Space/上传/commit/PR 失败、名单损坏、名单父 commit 冲突、已收录、相同开放 PR 复用和不同开放 PR 冲突。新增 `space_ownership_conflict`、`catalog_invalid`、`catalog_pr_conflict` 稳定错误并统一映射远程退出码。编排、事件、名单和快照定向测试 34 项通过。
- 已接入 `huggingface-hub>=1.26,<2` 的真实发布适配器；当前验证版本 1.26.0 明确要求 Python 3.10+，与 SDK 支持范围一致。每次 HfApi 实例都显式使用 Watcher 系统凭据 Token，不读取 HF CLI 默认登录；Space 强制公开且为 `static`，上传先列出远端文件、删除陈旧路径并提交精确文件集，名单下载固定到先观察到的完整 SHA，名单 PR 使用 `parent_commit + create_pr=True`。库自身会移除内容未变化的 add 操作并在空变更时返回现有 HEAD，保证相同源码重发不制造新 commit。TDD 首次因依赖和适配器不存在而收集失败；Fake API 14 项覆盖创建/更新、精确替换、固定 commit、名单下载、开放 PR、最小 PR、401/403/409 和网络错误脱敏。
- CLI 已接入 `watcherobot app publish <directory>`：人类输出展示 Space、固定 commit 源码地址和名单 PR 状态；`--jsonl` 只输出结构化进度、结果或稳定错误，并保留源码已上传但名单冲突时的部分结果。命令直接复用唯一发布服务，不启动 Daemon；本地校验失败、远端失败、名单冲突和用户取消均有稳定退出码。TDD 首次新增 6 项全部失败，其中 5 项因解析器没有 `publish`、1 项因真实依赖构造器不存在；实现后发布与认证 CLI 定向测试 11 项通过。
- 已使用非 `Orulink` 管理员账号 `tianguiti` 完成真实发布闭环。首次发布创建公开源码 Space `tianguiti/WatcherRobot-com.orulink.marketplace_smoke`，固定 commit 为 `18c3966e898d6ca84b1868663d1b5b591f9f7606`；远端只有 `README.md`、`app.json`、`app.py`，没有 `index.html`。官方名单 PR 为 `Orulink/watcherobot-app-store` #1，PR 与主分支之间唯一变化是 `app-list.json`，主分支在未合并时仍为 `[]`。
- 相同源码连续重发保持 commit `18c3966e898d6ca84b1868663d1b5b591f9f7606`，复用 PR #1 并返回 `pending`；修改源码后 Space 产生新 commit `082eae52e04948ea1e9e1578fac7a3af9ef745e2`，发布返回 `catalog_pr_conflict` 和原 PR URL，开放 PR 仍只有一个。`huggingface_hub` 的空提交提示只进入 stderr，JSONL stdout 逐行解析保持纯净。
- 最小权限真实门禁通过：先用普通网页会话创建不属于 Watcher OAuth 的同名公开 Space，记录 HEAD `588d39d94c62d74cbe1b1ce81d55e38b80117aa1` 和四个文件；Watcher 发布返回 `space_ownership_conflict`、退出码 4，尝试后 HEAD 与文件清单完全不变。临时 Space 随后已删除；Watcher 专用系统凭据也已清理并确认 `logged_in=false`。

S3 已完成。下一步唯一入口：进入 S4，先用 TDD 固定官方名单读取、缓存回退和完整 commit 快照下载的独立端口与错误合同，不接触 Daemon 运行时。

## 提交纪律

每个小步必须依次完成失败测试、最小实现、定向验证、全量回归、本文档更新和独立 commit。任何新增架构问题先记录并与项目负责人确认，不在实现中静默扩大范围。
