# Hugging Face / Gitee 双平台分发

开发者主动选择平台，两边独立收录；不根据 IP 自动判断、不回退到另一平台、不兼容旧命令和旧目录字段。

## 命令

以下命令在 Windows、macOS 和 Linux 使用相同参数。Gitee 发布、投稿、下载和安装需安装 Git。

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
| 发布 | Hub 原子提交 | Git 完整快照提交、非强制 push |
| 投稿 | Hub PR | 开发者 Fork 的新分支 → 官方 PR |
| 收录 | 管理员人工合并 | 管理员人工合并 |
| 下载 | 固定 SHA snapshot | Git 浅拉取固定 SHA，读取对象并校验 Git blob，不检出源码 |

目录 app-list.json 严格使用 repo_id、commit 两个字段。旧 space_id 被拒绝；正式 HF 目录若仍用旧结构，需要管理员同步修改后才能供这一版 SDK 使用。SDK 不替管理员自动改正式目录。

publish 不投稿；submit 不上传源码；必须先发布，且本地 app.json 与指定远端版本一致。重复投稿同一待审版本复用 PR；其他版本已有待审 PR 则提示冲突。官方目录在投稿期间发生变化时停止并提示重试，Fork 上测试/投稿分支可能保留。

## 权限与安全边界

- 普通开发者不需要官方仓库写权限；只写自己的源码仓库和 Fork。SDK 没有自动合并入口。
- 两个平台凭据独立保存到系统凭据管理器。SDK 不读取管理员前测凭据，不接受明文 Token 命令行参数。强制登录失败保留原凭据。
- Gitee 账号可能需要完成安全绑定，Token 需具有对应仓库读写权限。403 不一定是权限问题：平台也用它返回限流；SDK 对已识别的限流单独提示，不自动重试或切换凭据。
- 已存在的私有仓库不会自动公开；应使用明确用于公开发布的应用项目。
- 下载不执行应用。禁止浮动版本、符号链接、子模块、Git 元数据、路径穿越和重复大小写路径；限制 1000 文件、总计 100 MiB，目前单文件读取上限 1 MiB。
- Gitee 源码下载固定使用匿名 Git，不读取凭据、不回退 API、不自动重试；关闭凭据助手、重定向、用户 Git 配置及 hooks，临时对象库用后清理。文件大小限制在拉取后的导出前执行，并非网络 pack 的硬配额；大型恶意仓库的传输资源隔离仍需进一步加固。
- 安装才创建独立 Python 环境并安装依赖。安装来源写入 provider、repo_id、commit；同应用 ID 换平台或作者需先卸载，不能静默覆盖。以上命令不启动 Daemon。
- 正式 Gitee 目录已创建，但服务器端分支保护尚未配置。人工审核是流程约定，不能声称管理员直写已被服务端禁止。

## 本轮验证记录

### SDK Test Bench 普通开发者实测

- qiqi779 发布 examples/sdk_media_lab 1.1.0，共 23 个文件，版本 f2ab6fc55349ca30f4028c82be3e0880aaf7e128。示例清单升级 schema 2，仅声明已有验证记录的 Windows。
- SDK publish/submit 命令处理链路实际执行，凭据与前测目录仅通过测试进程注入，不覆盖系统 SDK 登录或正式目录配置。
- [测试 PR !4](https://gitee.com/orulink-sz/watcherobot-catalog-preflight-20260911/pulls/4) 由 qiqi779 的 Fork 分支向团队前测目录提交；重复投稿返回同一 PR，未自动合并。
- 完整 UI 下载发现 Gitee 目录 mode 返回 40000，已补充测试并修复误拒绝。分发与 UI 示例测试合计 350 项通过，分发类型检查通过。
- 测试 PR !4 已由用户人工合并，回读前测目录确认包含对应 repo_id 与固定 SHA；未修改正式目录。
- 匿名下载的 HTTP 403 已确认是 Gitee 返回 Rate Limit Exceeded。SDK 将同一仓库同一固定版本的重复 commit 查询收敛为一次，缓存仅保留一个已验证版本；23 文件下载从至少 47 次降至 25 次请求，仍逐文件验证内容及 tree/blob 哈希。
- 修复后分发与 UI 示例测试 357 项通过，分发模块 mypy 通过。再次匿名下载仍被平台限流，隔离目录没有残留文件；未使用管理员凭据绕过。完整 23 文件下载一致性、真实安装与启动仍未通过，不能将此前两文件前测替代本次验收。

### Git 下载优化后的复测

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
