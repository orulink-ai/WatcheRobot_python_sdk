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

### S3：Space 发布与官方名单 PR——已完成

> 历史说明：本节记录的是最初用于跑通闭环的合并式 `publish` 行为。当前命令合同已经把源码发布和 Catalog 提交拆开，现状以本文后面的“源码发布与 Catalog 提交职责拆分”以及 `distribution-contract.md` 为准。

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

### S4：官方名单与固定快照下载——已完成

- 已明确职责边界：SDK 提供公开名单读取、严格解析和固定快照交付，Desktop 在后续阶段持有上一次成功名单缓存；SDK 不自行持久化 Desktop 缓存，也不决定正式安装目录。
- 已从唯一 Manifest 实现中抽取不依赖本地源码文件的 `ApplicationManifestMetadata` 与 `parse_application_manifest()`。远端固定 commit 的 `app.json` 现在可复用同一字段白名单、依赖规则、App id、语义版本和 SDK 兼容校验；本地 `ApplicationManifest.load()` 继续额外检查固定 `app.py` 和图标文件。TDD 首次因新类型和解析函数不存在而收集失败，实现后远端元数据、原有 Manifest 和分发检查定向测试 27 项通过。
- 已建立不携带凭据的 `MarketplaceHubClient` 公开读取端口和 `load_official_marketplace()` 聚合服务。服务固定读取官方 Dataset，在严格校验名单后逐条以完整 commit 读取 `app.json`，返回 Manifest 结构化字段、固定源码 URL 和当前 SDK `compatible` 标志；未来 SDK 版本的 App 仍可展示但不可安装。损坏名单、重复 Space、短 SHA、无效 Manifest、重复 App id 和远端失败均停止本次刷新并返回稳定错误，调用者可继续使用自己持有的旧缓存。TDD 首次因 `marketplace` 模块不存在而收集失败，实现后名单、原有提交规划和 Manifest 定向测试 41 项通过。
- 已接入 `HuggingFaceMarketplaceHubClient` 无登录公开读取适配器，默认 `HfApi(token=False)`，明确禁止读取 HF CLI 或环境中的本机 Token。官方 Dataset 先观察 `main` 的完整 SHA，再以该 SHA 读取名单；Space 文件读取先确认仓库存在，再要求完整 commit 精确解析且只下载该 revision。仓库、commit 和必需文件不存在分别映射为内部稳定错误，浮动 revision 在联网前拒绝，传输错误不泄漏原始响应。TDD 首次因适配器模块不存在而收集失败，实现后公开适配器与名单服务 18 项通过；真实无凭据读取返回 Dataset commit `91e3d4d8732a04c21dc50c3ee93914606ee8993a` 和当前正式空名单。
- 已建立 `download_application_snapshot()` 隔离下载与交付事务。调用者必须明确提供现有空目录；SDK 先在同一父目录的临时区域下载，核对返回 commit、文件数/总大小/符号链接、完整 Manifest、固定 `app.py`、SDK 兼容性以及 Space 名称与 App id，再复制到调用者 staging。远端、revision、Manifest 或复制失败均不会把未校验源码写入目标；成功结果不含 `install.json`，SDK 不决定 Desktop 正式目录。TDD 首次因 `download` 模块不存在而收集失败，实现后目标边界、浮动引用、固定交付、commit 不一致、Manifest 错误、身份错配、远端失败和重复固定快照 12 项通过。
- Hugging Face 公开适配器已实现 `snapshot_download` 固定 revision 下载，只写 SDK 事务创建的现有空隔离目录，并再次确认 Space 存在、commit 精确解析和返回路径一致；下载后删除仅由 Hugging Face `local_dir` 模式生成的 `.cache/huggingface` 传输元数据。Fake API 覆盖固定 revision、非空目标和异常返回路径；真实下载 smoke Space 的已审核 commit `18c3966e898d6ca84b1868663d1b5b591f9f7606` 成功，只交付 `README.md`、`app.json`、`app.py`，Manifest id 为 `com.orulink.marketplace_smoke`，临时验收目录已清理。
- CLI 已接入 `watcherobot app download --space-id ... --commit ... --target <staging>`，人类输出展示固定来源、完整 commit、目标目录和 Application 身份；`--jsonl` 只在 stdout 输出结构化进度、结果或稳定错误。命令复用唯一下载服务和无凭据公开 Hub 适配器，不启动 Daemon，不读取 Watcher OAuth 凭据，不创建目标目录，也不写 `install.json`。TDD 首次运行在第一项即因解析器没有 `download` 子命令失败；实现后新增 5 项及下载、公开适配器、发布和认证 CLI 定向共 38 项通过，mypy 73 个源码文件通过。
- 已用真实命令从 `tianguiti/WatcherRobot-com.orulink.marketplace_smoke` 下载已审核固定 commit `18c3966e898d6ca84b1868663d1b5b591f9f7606`：退出码为 0，stdout 的三条进度事件和一条结果事件均为独立 JSON 对象，Hugging Face 下载进度只进入 stderr；staging 仍只包含 `README.md`、`app.json`、`app.py`，结果 commit、固定源码 URL 与 Manifest id 均正确，临时验收目录已清理。
- 用户已确认 Desktop 获取官方名单的公开命令名为 `watcherobot app marketplace --jsonl`；现有 `app list` 继续只表示经 Daemon 查询本机已安装 App，两者不得混用。CLI 直接复用 `load_official_marketplace()` 与无凭据公开 Hub 适配器，提供人类输出和严格 JSONL，不启动 Daemon、不读取登录凭据、不写缓存或修改本地状态；缓存及刷新失败后的旧结果回退继续由 Desktop 负责。TDD 首次运行在第一项即因解析器没有 `marketplace` 子命令失败；实现后新增 5 项、名单/下载/Daemon 边界定向 33 项和 SDK 全量 455 项通过，mypy 73 个源码文件、`pip check`、迁移守卫 post 模式与 `git diff --check` 均通过。
- 真实无登录命令验收返回退出码 0，stdout 仅有一条 `fetching_catalog` 进度事件和一条结果事件，stderr 为空；结果固定官方 Dataset commit 为 `91e3d4d8732a04c21dc50c3ee93914606ee8993a`，正式名单仍为空数组。首次验收脚本尝试使用当前 PowerShell/.NET 不可用的 `ProcessStartInfo.ArgumentList`，导致参数未传入并返回 argparse 退出码 2；改用兼容的 `Arguments` 后命令本身验收通过，该失败不属于 SDK 回归。

S4 的公开名单服务、`app marketplace`、固定快照下载服务与 `app download` 路径已经闭合。下一步进入 S5，按既定文档实现 SDK Daemon 的受控 Application 启动合同，不改变内容无关路由边界。

### S5：SDK Daemon 受控启动器合同——已完成

- 修改前事实：`ApplicationRuntimeManager` 构造时固定保存一个 `python_executable`，`select_application()` 只替换 Application 目录；源码模式始终用 Daemon Python 执行 `app.py`，冻结模式始终执行当前 Runtime 二进制的 `--application` 分支，因此尚不能表达每 App Python 或独立默认 App 启动单元。
- 已建立独立 `ApplicationLauncher` / `ApplicationLaunchSpec` 合同。启动器类型只允许 `python` 和 `bundled`；规格只产生 `<受控 Python> <固定 app.py>` 或 `watcher-default-app` 两种命令，不携带调用方参数或 shell 字符串。Python 启动器必须位于启动时固定的 App 受管根，bundled 启动器与默认 Application 目录必须位于固定资源根；普通 Python Application 的已校验源码可以与其环境根分开，以兼容开发者 `app run <源码目录>`。解析真实路径后再次做包含关系检查，路径逃逸、缺失/不可执行文件、任意可执行文件名、第三方 bundled 与默认 App Python 均在生成进程命令前拒绝。
- TDD 首次因 `application.launcher` 模块不存在而收集失败；实现后新增 10 项、Application 启动/Manifest/REST/路由聚焦 40 项和 SDK 全量 465 项通过，mypy 74 个源码文件、`pip check`、迁移守卫 post 模式与 `git diff --check` 均通过。该小步尚未切换现有 Runtime 或 REST，只先固定下一步可复用的安全规格。
- `ApplicationRuntimeManager` 现已支持原子选择目录与受控启动器：校验失败或运行槽占用时不会替换当前 App/规格；启动前重新校验 Manifest、受管根与可执行文件，再只执行规格中的固定参数数组。子进程环境会移除继承的 `PYTHONPATH`、`PYTHONHOME`、`VIRTUAL_ENV`，并固定 `PYTHONNOUSERSITE=1`、`PYTHONUNBUFFERED=1`，现有四个 `WATCHER_APP_*`、channel 就绪、日志和进程树合同保持不变。为便于下一小步迁移 REST，旧目录选择路径暂时仍存在但新规格路径已经真实启动验证。新增规格选择、环境隔离和运行中拒绝切换测试后，启动/日志/路由聚焦 28 项、SDK 全量 468 项、mypy 74 个源码文件、`pip check`、迁移守卫和 `git diff --check` 均通过。
- `/daemon/application/select` 已固定为严格的 `application_dir + launcher.kind + launcher.executable` 请求，未知字段、旧的仅目录请求和调用方参数均返回 422；启动器越界等策略错误返回稳定 `invalid_application_launcher`。Daemon 启动时固定受管 App 根和 bundled 资源根，CLI 开发运行模式显式声明当前 SDK Python 根。REST、Daemon 组合根和真实 CLI 定向 18 项通过，mypy 74 个源码文件通过。
- `ApplicationRuntimeManager` 已删除 Daemon 自身解释器和冻结 `--application` 的旧命令推断；没有受控 `ApplicationLaunchSpec` 时拒绝启动。Daemon 不再持有旧 `ApplicationCatalog`，也不再暴露 `/daemon/applications*` 安装、列表、选择和卸载 REST；旧归档打包、安装及兼容命令均已删除，Application 安装统一由 SDK 分发模块基于 Hugging Face 固定 commit 完成。该变化关闭了绕过受控启动器的目录选择入口，并没有把下载或环境安装迁入 Daemon。
- S5 真实双环境门禁已通过：测试创建 App A、App B 两个真实 venv，两个子进程分别报告各自的 `sys.executable`；A 运行时选择 B 被拒绝，停止 A 后 B 可选择并启动；两个 App PID 不同而同一测试 Daemon PID 保持不变。Application 生命周期、日志、REST、完整路由和 CLI 全量回归共 473 项通过，mypy 74 个源码文件、`pip check`、迁移守卫 post 模式和 `git diff --check` 均通过。

S5 已完成。下一步唯一入口：进入 S6，在 `WatcheRobot_client` 建立独立 Desktop Application Store/Environment 模块，先固定 App Data 目录、每 App 根目录与安装事务合同，再接入真实 Python/uv 环境创建；不能把这些职责放回 Daemon。

### S6 配套收口：平台 SDK 依赖来源门禁

- 每 App 环境会始终先安装 Desktop 随包、且与 Daemon 同 commit 构建的本地 `watcherobot` wheel。
- Manifest 继续允许普通 `watcherobot>=...` 版本约束，由 uv 将其与 `requires_watcherobot` 及本地 wheel 合并解析。
- `watcherobot @ https://...`、`watcherobot @ file://...` 等直接引用现在由唯一 `ApplicationManifest` 校验入口拒绝，错误码保持 `app_dependency_invalid`；其他第三方包的标准直接 URL 不受影响。
- TDD 首次新增 4 个用例时有 3 个失败；实现后 Manifest/CLI 定向 25 项及 SDK 全量 477 项通过，mypy 74 个源码文件、仓库 `.venv` 的 `pip check`、迁移守卫 post 模式和 `git diff --check` 均通过。

该变更只补齐 S6 已确认的平台 wheel 边界，不把环境安装迁回 SDK 或 Daemon。

### Application CLI 可用性收口

- 用户真实执行 `app marketplace --jsonl` 后确认机器事件不适合作为开发者日常界面，因此输出模式现已明确分层：默认模式面向开发者，`--details` 面向人工完整审阅，`--jsonl` 只面向 Desktop 或自动化。
- `app marketplace` 默认输出英文兼容性表格，只保留状态、版本、名称和 Application ID；`--details` 输出完整 SDK 要求、依赖、作者、说明、固定源码 URL 与 40 位 commit。真实官方 Dataset 的两条已合并记录已分别通过紧凑视图、详细视图和 JSONL 读取验证。
- `check`、`login/logout`、`publish`、`marketplace`、`download` 的默认结果与错误前缀统一为英文标签；发布、广场和下载进度进入 stderr，成功摘要保留在 stdout。JSONL 的事件类型、stage、code、data、details 和退出码不变，`message` 统一为英文辅助文案。
- `app --help` 现在说明开发、运行、认证、发布、广场、下载和当前 Application 启停的真实用途及 Daemon 边界；旧版目录 Catalog 的 `package/select` 不再注册或提供迁移兼容，安装、列表和卸载只保留 SDK 分发模块的新版固定快照合同。
- 新增 [Application CLI Quick Reference](application-cli-reference.md)，英文/中文指南均改为默认人工模式优先，并把 `--jsonl` 单独标记为 Desktop 机器合同。
- 应用广场最小信息门禁现由 `app submit` 持有：本地 `check/run` 和 `app publish` 允许 `description`、`author`、`icon` 缺省；提交 Catalog 审核时只要求 `description`、`author` 非空，`icon` 可选。新建 Catalog PR 直接展示固定快照的名称、ID、版本、作者、简介、SDK 要求、依赖和源码链接；存在自选图标时额外展示图标路径和固定版本图标，否则标记使用默认 WatcherRobot Application 图标。官方 `app-list.json` 继续只保存 `space_id + commit`，避免产生第二份可漂移元数据。
- 新增开发者入口 `watcherobot app init <new-directory>`，交互式收集或通过参数接收 ID、名称、作者、简介，并生成可直接 `check/run/publish` 的 `app.json`、`app.py`、README、默认 SVG 图标和 `.gitignore`。初始 App 版本固定为 `0.1.0`，SDK 范围根据当前版本计算；目标已存在、字段非法或生成校验失败时拒绝覆盖。该命令不加入 Desktop `watcher-distribution` sidecar，也不启动 Daemon。
- TDD 首次运行新增与修改的帮助、表格、详细视图和英文摘要用例时有 8 项失败；实现后 Application CLI 聚焦用例、分发目录用例和 Runtime CLI 用例全部通过。SDK 全量 499 项、mypy 75 个源码文件、`pip check` 和 `git diff --check` 通过。
- 真实公开调用已验证两个入口：`watcherobot app marketplace` 和 `--details` 分别显示当前两条正式记录的表格与完整固定来源；`watcher-distribution app marketplace --jsonl` 返回相同 Dataset commit `8ccb4394ef76284a61a9bb0c49c499174843efda`，事件字段保持原合同且所有辅助消息为英文。

### 源码发布与 Catalog 提交职责拆分——当前合同

- `watcherobot app publish <directory>` 现在只做本地校验、Hugging Face 登录校验、公开 Space 创建/更新、完整源码上传和固定 commit 解析；结果只包含 `space_id`、`commit`、`space_url`、`source_url`，不读取或修改官方 Catalog。
- 新增 `watcherobot app submit <directory> [--commit <sha>]`。该命令不调用 Space 创建或源码上传，只读取固定 commit 上的 `app.json`；当 Manifest 提供 `icon` 时再读取并校验该图标。它要求远端 Manifest 与本地项目一致，再创建或复用官方 Catalog PR。省略 `--commit` 时解析当前 Space HEAD；显式参数只接受 40 位小写 SHA。
- `description`、`author` 的完整性门禁从 `publish` 移到 `submit`；`icon` 在全部阶段均可选，填写时严格校验，未填写时由展示端使用默认图标。因此开发者可以先反复发布测试源码，稳定后再单独发起应用广场审核。
- `watcher-distribution app` 同步公开 `submit`，继续保持短进程、JSONL 和不启动 Daemon 的边界。Desktop 后续应把“发布到 Hugging Face”和“提交应用广场审核”呈现为两个独立动作。
- TDD 首次运行因 `watcherobot.distribution.submit` 不存在而在收集阶段失败；实现后服务与 CLI 聚焦测试证明 `publish` 只发生 `ensure/upload/head`，`submit` 不发生 `ensure/upload`，并覆盖显式 commit、固定源码核对、元数据门禁、已收录、PR 复用和 PR 冲突。

### 合并前审查修复

- Application 安装记录改用 Python 3.10 可用的 `timezone.utc`，并将下载快照参数收紧为 `DownloadResult`；`mypy` 的 Python 3.10 门禁恢复通过。
- Windows Python Application 仍优先使用无控制台的 `pythonw.exe`，但实际执行路径现在会在生成 `ApplicationLaunchSpec` 时固定，并重新校验文件类型、名称和受管根，拒绝符号链接逃逸。
- 新增 `pythonw.exe` 受管根逃逸回归测试，修复前可稳定复现未拒绝问题，修复后启动器聚焦测试通过。
- CLI Runtime 测试补齐 `WATCHER_RUNTIME_PREVIEW_UDP_PORT=0`，避免本机已运行 Desktop Daemon 占用默认预览端口时产生与本次改动无关的失败。

## 提交纪律

每个小步必须依次完成失败测试、最小实现、定向验证、全量回归、本文档更新和独立 commit。任何新增架构问题先记录并与项目负责人确认，不在实现中静默扩大范围。
