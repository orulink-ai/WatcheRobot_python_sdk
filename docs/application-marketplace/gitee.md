# Hugging Face / Gitee 双平台分发

开发者主动选择平台，两边独立收录；不根据 IP 自动判断、不回退到另一平台、不兼容旧命令和旧目录字段。

## 命令

以下命令在 Windows、macOS 和 Linux 使用相同参数。Gitee 发布、投稿需安装 Git。

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
| 下载 | 固定 SHA snapshot | 固定 SHA tree + contents，校验 Git blob |

目录 app-list.json 严格使用 repo_id、commit 两个字段。旧 space_id 被拒绝；正式 HF 目录若仍用旧结构，需要管理员同步修改后才能供这一版 SDK 使用。SDK 不替管理员自动改正式目录。

publish 不投稿；submit 不上传源码；必须先发布，且本地 app.json 与指定远端版本一致。重复投稿同一待审版本复用 PR；其他版本已有待审 PR 则提示冲突。官方目录在投稿期间发生变化时停止并提示重试，Fork 上测试/投稿分支可能保留。

## 权限与安全边界

- 普通开发者不需要官方仓库写权限；只写自己的源码仓库和 Fork。SDK 没有自动合并入口。
- 两个平台凭据独立保存到系统凭据管理器。SDK 不读取管理员前测凭据，不接受明文 Token 命令行参数。强制登录失败保留原凭据。
- Gitee 账号可能需要完成安全绑定，Token 需具有对应仓库读写权限；403 时检查账号安全设置和授权范围。
- 已存在的私有仓库不会自动公开；应使用明确用于公开发布的应用项目。
- 下载不执行应用。禁止浮动版本、符号链接、子模块、Git 元数据、路径穿越和重复大小写路径；限制 1000 文件、总计 100 MiB，目前单文件读取上限 1 MiB。
- 安装才创建独立 Python 环境并安装依赖。安装来源写入 provider、repo_id、commit；同应用 ID 换平台或作者需先卸载，不能静默覆盖。以上命令不启动 Daemon。
- 正式 Gitee 目录已创建，但服务器端分支保护尚未配置。人工审核是流程约定，不能声称管理员直写已被服务端禁止。

## 本轮验证记录

- 真实 SDK 发布与匿名下载：tianguiti/WatcherRobot-com.orulink.gitee-preflight-20260911，固定版本 e641fcb3ee31fb62340da0fb3a6e42a7a2216e9f。
- 真实 Fork 投稿：[测试 PR !3](https://gitee.com/orulink-sz/watcherobot-catalog-preflight-20260911/pulls/3)，未自动合并。
- 正式 Gitee 空目录只读成功，未写入测试应用。
- 固定版本下载再次通过 tree/blob 哈希校验；重复投稿识别为 pending，未新增 PR。
- 全仓测试排除本机缺少 bleak 的模块后：1135 通过、7 跳过；随后补充的 Fork 写入边界、非法平台和下载回滚测试通过。分发模块 mypy 与 Python 3.10 语法检查通过。执行环境为 Windows / Python 3.13，不能替代声明支持的 Python 3.10–3.12 运行矩阵。
- HF 正式目录匿名读取返回 RepositoryNotFoundError，尚未确认其在线可用性与目录字段；需要管理员核实公开权限、仓库地址及 repo_id 结构，不应视为 HF 线上端到端验收通过。
- 单元测试覆盖服务/CLI、HF 回归、Gitee 安全边界、来源隔离。真实 Runtime 安装与跨操作系统执行仍需实际运行环境验收，不能用模拟环境测试代替。
