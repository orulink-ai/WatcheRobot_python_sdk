# Hugging Face / Gitee 双平台分发

开发者主动选择平台，两边独立收录；不根据 IP 自动判断、不回退到另一平台、不兼容旧命令和旧目录字段。

## 命令

以下命令在 Windows、macOS 和 Linux 使用相同参数。Gitee 发布、投稿、应用广场读取、下载和安装使用 OpenAPI，无需安装 Git。

```text
watcherobot app login --provider gitee
watcherobot app login --provider gitee --status
watcherobot app publish ./my-app --provider gitee
watcherobot app submit ./my-app --provider gitee --commit <40位SHA>
watcherobot app marketplace --provider gitee
watcherobot app download --provider gitee --repo-id <作者/WatcherRobot-应用ID> --commit <40位SHA> --target <已有空目录>
watcherobot app install --provider gitee --repo-id <作者/WatcherRobot-应用ID> --commit <40位SHA> --store-root <应用存储目录> --runtime-root <已校验Runtime目录>
watcherobot app logout --provider gitee
```

将 gitee 改为 huggingface 即选择 Hugging Face。check、list、uninstall 是本地操作，无需平台参数。远端命令支持 --jsonl。

## 发布和审核

| 环节 | Hugging Face | Gitee |
| --- | --- | --- |
| 登录 | 设备码授权 | 交互终端隐藏输入 Access Token |
| 应用源码 | 开发者的公开 Space | 开发者的公开 Git 仓库 |
| 正式目录 | Orulink/watcherobot-app-store | orulink-sz/watcherobot-app-store |
| 发布 | Hub 原子提交 | OpenAPI 单次提交文件变更，回读固定提交校验完整快照 |
| 投稿 | Hub PR | 开发者 Fork 的新分支 → 官方 PR |
| 收录 | 管理员人工合并 | 管理员人工合并 |
| 下载 | 固定 SHA snapshot | 匿名 OpenAPI 读取固定 SHA 的 tree/blob，校验文件大小及 Git blob 摘要 |

目录 app-list.json 严格使用 repo_id、commit 两个字段。旧 space_id 被拒绝；正式 HF 目录若仍用旧结构，需要管理员同步修改后才能供这一版 SDK 使用。SDK 不替管理员自动改正式目录。

publish 不投稿；submit 不上传源码；必须先发布，且本地 app.json 与指定远端版本一致。重复投稿同一待审版本复用 PR；其他版本已有待审 PR 则提示冲突。官方目录在投稿期间发生变化时停止并提示重试，Fork 上测试/投稿分支可能保留。

Gitee 发布将 SDK 收集的源文件原始字节编码为 base64，在一个 OpenAPI commit 中执行新增、更新、删除操作；属性文件本身仍作为普通源码保留。提交后回读文件树并核对文件清单和 blob 摘要，不一致则报错，远端已创建的提交不会自动回滚。

更新和删除时，`last_commit_id` 使用该文件在选定固定版本内的最近一次提交 SHA，通过带 `sha`、`path` 的提交历史查询获取。投稿先通过分支 API 的 `refs` 从官方目录固定 commit 创建 Fork 分支，并核对返回的分支起点，再向该已存在分支提交；`app-list.json` 的 `last_commit_id` 单独查询，不能用仓库 HEAD 代替。无效或缺失的文件提交记录会阻止后续提交写入。

创建投稿分支前，SDK 会检查开发者 Fork 是否包含此次官方目录的目标 commit。缺少时立即停止，提示打开对应 Fork、同步上游目录后重新投稿；此时尚未创建投稿分支、文件提交或 PR。`--jsonl` 错误保持 `remote_error`，详情中的 `reason=fork_sync_required`、`fork_url`、`upstream_repo_id` 和 `required_commit` 可供客户端显示恢复步骤。权限不足、限流和网络异常不会被误报为 Fork 过旧。SDK 不覆盖开发者现有分支，也不自动执行 Git 同步。

投稿的超时、服务端异常和限流保留远端错误分类，不作为目录版本冲突。限流提示等待后重试；写入结果不明确时，应先检查 Fork 分支及待审 PR，再决定是否重试。SDK 不自动重发写入请求。

## 权限与安全边界

- 普通开发者不需要官方仓库写权限；只写自己的源码仓库和 Fork。SDK 没有自动合并入口。
- 两个平台凭据独立保存到系统凭据管理器。SDK 不读取管理员前测凭据，不接受明文 Token 命令行参数。强制登录失败保留原凭据。
- Gitee 账号可能需要完成安全绑定，Token 需具有对应仓库读写权限。403 不一定是权限问题：平台也用它返回限流；SDK 对已识别的限流单独提示，不自动重试或切换凭据。
- 已存在的私有仓库不会自动公开；应使用明确用于公开发布的应用项目。
- 下载不执行应用。禁止浮动版本、符号链接、子模块、Git 元数据、路径穿越和重复大小写路径；导出限制采用共用的 1000 文件、总计 100 MiB。已移除旧 REST 路径遗留的单文件 1 MiB 限制，支持中文和带空格、括号的资源路径。
- Gitee 源码及投稿图标读取固定使用匿名 tree/blob API，不读取凭据、不自动重试；禁止 HTTP 重定向。读取 blob 前检查大小上限，解码后校验实际大小和摘要。投稿图标不再受元数据读取器的 1 MiB 限制，仍受共用的 100 MiB 文件大小上限约束；完整快照保持总计 100 MiB 上限。
- 安装才创建独立 Python 环境并安装依赖。安装来源写入 provider、repo_id、commit；同应用 ID 换平台或作者需先卸载，不能静默覆盖。以上命令不启动 Daemon。
- 正式 Gitee 目录已创建，但服务器端分支保护尚未配置。人工审核是流程约定，不能声称管理员直写已被服务端禁止。

## 本轮验证记录

### 2026-09-23 Fork 投稿前检测

- Python 3.11 分发模块 371 项测试通过，分发模块 mypy 通过。新增 12 项测试覆盖缺失提交、权限、限流、超时、异常响应、新建 Fork 和用户恢复提示；已有投稿成功路径继续通过。
- 此次新增检测使用模拟 API 回归验证，未新增真实 Gitee 写入；此前真实发布与投稿验收见下节。

### 2026-09-23 PR #128 审查修复

- 在 Python 3.11 上，分发模块 359 项测试通过，包含新增的 24 项大图标、错误分类、文件提交合同、投稿分支起点及安全边界回归测试；分发模块 mypy 通过。
- 真实匿名读取官方目录的文件历史成功，确认仓库 HEAD 与 `app-list.json` 最近提交可以不同。
- 新版 tree/blob 文件读取真实通过：`mystic-rhythm/WatcherRobot-com.orulink.expression_lab` 固定提交 `f7faae287f78f9ac4f9349b5475916f111e1519f` 的清单 478 字节、图标 1569 字节均通过大小及摘要校验；这不代表线上大图标写入验收。
- 真实写入测试使用独立应用仓库 `qiqi779/WatcherRobot-com.orulink.gitee-regression-20260923-143230-cb4c20`：发布 1,471,287 字节 PNG 后插入无关提交，再更新清单、删除旧文件。最终提交为 `38df32b33e9986635a0f5b97095de232a4d38b69`，匿名下载与本地源文件逐字节一致。
- 旧前测 Fork 缺少官方测试目录目标 commit；验收准备阶段通过新建基线分支引入完整历史，未修改现有分支。随后 SDK 仅通过 OpenAPI 创建投稿分支、提交和 [测试 PR !5](https://gitee.com/orulink-sz/watcherobot-catalog-preflight-20260911/pulls/5)，再次投稿复用同一 PR。回读确认 PR 只修改 `app-list.json`，保持待审，未合并；正式应用广场未写入测试数据。
- 以上证明本轮测试仓库的真实发布、更新、删除、下载及投稿链路；不代表旧 Fork 自动同步、跨平台完整安装或设备运行验收。后续历史记录描述对应版本。

### 2026-09-14 正式 Gitee 链路复验

验收代码为 SDK `513f29fc74080d341ffef7b561b38e5cc07e67d4`，Windows / Python 3.12.13。以下结果优先于后文历史记录。

- qiqi779 重新发布当前 UI 示例，固定提交 `cba9f1216c1a9d3a15408a14b0a14b1c3b280641`，包含 shutdown_requested 停止处理。
- SDK 向正式目录创建 [PR !1](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/1)，返回 pending；随后平台侧于 09:42:23 合并，验收进程未执行合并。
- 再次投稿返回 already_listed，无新增 PR；匿名 marketplace 回读正式目录提交 `b4d5e0e8659782af5e1c57902c59fd4698537f86`，显示该固定版本，SDK 与主机兼容性均为 true。
- 从当前 SDK 构建 0.1.9 wheel，SHA256 为 `9702b134408d3af68cd21359fb4626dfbe8c3d8dd20c94ba8222da6e025e2275`；配合重新下载且源包哈希、解包树哈希均匹配原清单的 Python 3.12.13，组装隔离 Runtime。未覆盖桌面 Runtime。
- 旧验收 Python 副本完整性不匹配被安装器拒绝；未修改校验规则或以重算哈希放行，改用已校验的干净源包后安装成功。
- 真实 CLI install 完成匿名固定版本下载、独立环境、依赖安装及来源记录；23 个源文件与本地发布源逐字节一致，安装副本未补丁修改。
- 用安装后的 wheel 提供的 ApplicationRuntimeManager 受管启动该应用：running；首页、JS、CSS 均 HTTP 200；停止退出码 0、进程释放、监听端口关闭。没有连接硬件；注入的设备状态地址不提供真实设备服务，不计作设备功能验收。
- 验收脚本最初使用父进程端口枚举未发现 Windows 子进程监听，改为解析本次启动日志的地址后完成 HTTP 检查；未修改 SDK 或示例来绕过。

剩余项：正式目录 master 的 API 返回 protected=false，需要组织管理员明确并启用分支保护策略。当前只证明投稿经 PR 收录，不证明服务端已禁止管理员或写入成员直写。Desktop 正式打包、硬件和 macOS/Linux 不在本次验收范围。标准 SDK 登录槽未改动，前测凭据仅用于验收进程。

以下按验证阶段保留历史结果，不代表每个阶段的限制仍然存在。当前源码以后续补齐结果为准；正式发布验收仍需核实 HF 线上目录、Gitee 分支保护和新版 Runtime/示例发布。这些验收与 SDK 本轮功能对齐区分记录。

### HF / Gitee 功能对齐结论

| 功能 | 当前合同 |
| --- | --- |
| 平台选择 | 开发者显式选择 `--provider huggingface` 或 `--provider gitee`，不按地区推断，不自动回退 |
| 登录、状态、退出 | 都支持；凭据按平台隔离。HF 为设备授权，Gitee 为交互式 Token 输入，不承诺相同登录交互 |
| 发布与更新 | 上传应用源码、返回固定提交；不自动投稿。共用源码筛选，排除 `.env` 等文件 |
| 投稿与更新收录 | 向各自官方目录提交固定版本；相同待审投稿复用，已收录版本不重复提交；人工合并 |
| 目录读取、下载、安装 | 共用清单校验及安装服务；固定提交、记录平台与仓库来源，禁止跨来源静默替换 |
| 资源文件 | Gitee 已补齐普通中文文件名和大于 1 MiB 的资源；保留安全路径及快照总量校验 |

对齐指 SDK 的应用代码分发流程，不指平台所有能力完全相同：Gitee 不提供 HF Space 同等托管运行能力；本实现未承诺 Git LFS 对象下载。Gitee 的 Windows 安全路径约束也不等价于接受任意 Unix 文件名。

传输期间的磁盘、内存硬配额属于后续共用加固，不作为此次 HF/Gitee 功能对齐的额外门禁。桌面端调用适配属于后续接入验收，不在本轮修改桌面端。自动化合同测试不替代两个平台正式目录的线上验收。

### PR 审查修复

- 发布文件由 SDK 收集器及 `.watcherignore` 决定；临时 Git 仓库不得再按 `.gitignore` 静默丢弃已选文件。真实本地 Git 发布回归覆盖被忽略的 UI 静态资源。
- Gitee API 错误响应读取中断时统一返回已脱敏网络错误，不将底层异常直接暴露给 CLI。

### SDK Test Bench 普通开发者实测

- qiqi779 发布 examples/sdk_media_lab 1.1.0，共 23 个文件，版本 f2ab6fc55349ca30f4028c82be3e0880aaf7e128。示例清单升级 schema 2，仅声明已有验证记录的 Windows。
- SDK publish/submit 命令处理链路实际执行，凭据与前测目录仅通过测试进程注入，不覆盖系统 SDK 登录或正式目录配置。
- [测试 PR !4](https://gitee.com/orulink-sz/watcherobot-catalog-preflight-20260911/pulls/4) 由 qiqi779 的 Fork 分支向团队前测目录提交；重复投稿返回同一 PR，未自动合并。
- 完整 UI 下载发现 Gitee 目录 mode 返回 40000，已补充测试并修复误拒绝。分发与 UI 示例测试合计 350 项通过，分发类型检查通过。
- 测试 PR !4 已由用户人工合并，回读前测目录确认包含对应 repo_id 与固定 SHA；未修改正式目录。
- 匿名下载的 HTTP 403 已确认是 Gitee 返回 Rate Limit Exceeded。SDK 将同一仓库同一固定版本的重复 commit 查询收敛为一次，缓存仅保留一个已验证版本；23 文件下载从至少 47 次降至 25 次请求，仍逐文件验证内容及 tree/blob 哈希。
- 修复后分发与 UI 示例测试 357 项通过，分发模块 mypy 通过。再次匿名下载仍被平台限流，隔离目录没有残留文件；未使用管理员凭据绕过。完整 23 文件下载一致性、真实安装与启动仍未通过，不能将此前两文件前测替代本次验收。

### Git 下载优化后的复测

后续补齐：目录默认分支与固定版本 app.json 均改用匿名 Git 对象读取，目录与文件校验保持不变，写入和 PR 操作仍用 API。正式空目录完整加载通过；前测目录及 qiqi UI 清单读取通过，但其中旧条目 tianguiti/watcherobot-app-preflight-20260911 缺少 app.json，使整表按严格校验失败，未擅自清理远端条目。

UI 示例已补充 Daemon 停止信号监听。隔离安装副本应用该修复并安装当前源码构建的 SDK 0.1.9 wheel 后，受管启动、首页和静态资源 HTTP 200、退出码 0、停止后端口关闭均通过。旧 Runtime 内 SDK 0.1.1a4 缺少 shutdown_requested 接口；不提供旧版兼容，正式交付必须更新 Runtime wheel 并重新发布示例。此验证不代表原远端固定 SHA 已包含修复。

- 完整源码下载不再逐文件调用 REST API；浅拉取指定 SHA 后从本地 Git 对象导出，保留树模式、路径、大小和 blob 哈希校验。目录查询和投稿仍使用原 API，不改变人工审核流程。
- qiqi779 的上述固定版本匿名下载成功：23 个文件，与本地 SDK UI 示例逐文件字节对比无差异。
- 使用真实已校验 Runtime（Python 3.12.13 / watcherobot 0.1.1a4），通过真实安装器在全新隔离目录安装成功，未注入模拟环境安装器，未覆盖桌面应用；安装来源保留 Gitee、作者仓库和固定 SHA。
- 362 项分发及 UI 示例测试通过，24 个分发源码文件 mypy 通过；新增真实本地 Git 仓库的二进制快照、符号链接拒绝、导出限额与非空目录保护测试。
- 前测目录匿名 API 查询再次失败，故不能宣称应用广场浏览已恢复；受管 UI 启动与硬件行为本轮未验收。

- 真实 SDK 发布与匿名下载：tianguiti/WatcherRobot-com.orulink.gitee-preflight-20260911，固定版本 e641fcb3ee31fb62340da0fb3a6e42a7a2216e9f。
- 真实 Fork 投稿：[测试 PR !3](https://gitee.com/orulink-sz/watcherobot-catalog-preflight-20260911/pulls/3)，未自动合并。
- 正式 Gitee 空目录只读成功，未写入测试应用。
- 固定版本下载再次通过 tree/blob 哈希校验；重复投稿识别为 pending，未新增 PR。
- 全仓测试排除本机缺少 bleak 的模块后：1135 通过、7 跳过；随后补充的 Fork 写入边界、非法平台和下载回滚测试通过。分发模块 mypy 与 Python 3.10 语法检查通过。执行环境为 Windows / Python 3.13，不能替代声明支持的 Python 3.10–3.12 运行矩阵。
- HF 正式目录匿名读取返回 RepositoryNotFoundError，尚未确认其在线可用性与目录字段；需要管理员核实公开权限、仓库地址及 repo_id 结构，不应视为 HF 线上端到端验收通过。
- 单元测试覆盖服务/CLI、HF 回归、Gitee 安全边界、来源隔离。真实 Runtime 安装与跨操作系统执行仍需实际运行环境验收，不能用模拟环境测试代替。
