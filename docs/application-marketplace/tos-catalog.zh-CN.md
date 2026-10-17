> 历史方案：2026-10-15 已由 [内嵌 Git 方案](git-catalog.zh-CN.md) 替代。下文保留原始调查记录，其中商店专用命令、脚本与工作流已移除；已上传对象未删除。

# 国内应用商店 TOS 完整目录

源码和审核真源仍是 Gitee `orulink-sz/watcherobot-app-store` 的 `master:app-list.json`。
开发者通过 SDK 发布源码、提交审核的流程不变。TOS 只发布审核后的展示元数据，不开放终端用户写入。

## 数据和读取合同

国内 SDK `watcher-distribution app marketplace --provider gitee --jsonl` 默认 GET：

`https://erroright.tos-cn-guangzhou.volces.com/WatcherRobot/marketplace/cn/latest.json`

可通过 `--catalog-url <public-https-url>` 显式指定同合同地址；该参数仅适用于 Gitee 商店。
Desktop 将构建时配置的 `WATCHER_TOS_PUBLIC_ORIGIN` 传给内置 SDK，不直接请求 TOS。
国际商店继续使用 Hugging Face。列表读取不调用 Gitee API，也不回退到逐项 API 请求。

快照 `snapshot_version=1`、`provider=gitee`，包含 `catalog_commit`、原始 `catalog`
数组，以及按目录顺序排列的 `applications` 数组。每项包含 `repo_id`、`commit`、完整的
`manifest` 对象。manifest 保留名称、版本、描述、作者、平台、依赖、SDK/Daemon 协议要求和图标引用；
源码 URL 由 SDK 按 repo_id 和固定 commit 生成。快照不保存生成机器的 compatible/host_compatible。

SDK 检查完整条目集合与顺序、40 位小写 SHA、目录唯一性、manifest schema、应用 ID 与仓库名、
重复应用 ID、依赖和平台声明，复用现有 marketplace 校验。兼容性由用户机器上的 SDK 计算，
schema 3 保留既有 Daemon 协议兼容性语义。最大响应 1 MiB，禁止 HTTP、带凭据 URL 和重定向。
每次显式刷新发出一个匿名 GET，并要求 HTTP 缓存重新验证；连接超时 30 秒。
这不是客户端刷新冷却；不增加重试或固定 60 秒冷却。

Desktop 只有在内置 SDK 完成校验、Rust 再次校验后才原子更新有效缓存。
网络失败、缺项、无效 manifest、重复应用和损坏响应都不能覆盖有效缓存，回退必须标记旧内容。

## 官方生成与发布

发布环境需要 Python 3.10–3.12、Git 和可读官方公开仓库的网络。这些仅是发布端依赖，客户端不需要安装。

```text
python -m pip install ".[marketplace-publish]"
```

Windows 使用 `scripts/publish-marketplace.ps1`，macOS/Linux 使用
`sh scripts/publish-marketplace.sh`；两个入口共用 SDK 核心，参数、默认行为和退出码一致。
可用 `WATCHER_PYTHON` 指定 Python 可执行文件。

```text
--output <snapshot.json>   只生成并校验，写出完整快照
--publish                 校验后发布到官方 TOS
--publish --output <path>  同时保留本地已校验副本
```

成功退出 0；参数错误退出 2；生成、校验、网络、并发或上传失败退出 1。
错误只输出固定状态和异常类别，不打印密钥、完整远端响应或 traceback。

GitReader 先固定审核目录 SHA，再通过 Git fetch/cat-file 读取各固定提交的 app.json，
验证实际解析的 commit、Git blob SHA 与大小，不 checkout、不执行应用代码，不请求 Gitee API。

1. 生成全部 manifest，整体校验并按规范 JSON 编码。
2. 上传 `releases/<catalog_commit>/<sha256>.json`，禁止覆盖，缓存一年 immutable。
3. 通过公开 HTTPS 地址读回并校验完整版本，与本地字节内容比较。
4. 再检查官方 master 未改变；使用已有 latest 的 ETag 条件写更新 `latest.json`。
   首次创建禁止覆盖；有并发冲突直接失败，不强行重试覆盖他人结果。
5. latest 设置 `Cache-Control: public, max-age=60, must-revalidate`。

前置阶段失败不更新 latest；单次对象写入不会暴露半个 JSON。
上传中断、条件冲突或发布结果未确认都返回失败，保留不可变版本供核查，重跑可恢复。
若更新 latest 后网络断开，不能仅凭本地异常断言远端未更新，须重新读回确认。
不要删除旧版本来恢复；已发布原版本仍可用于受控回滚。

## 自动同步和缓存时效

`.github/workflows/marketplace-tos.yml` 提供 `repository_dispatch: marketplace-approved`、
手动触发和每 5 分钟轮询。审核合入集成应触发 dispatch；轮询通过 Git 检测并生成审核后目录，
无需 Gitee API。发布全流程以 GitHub concurrency 串行，TOS 条件写进一步防止并发覆盖。
workflow 合入默认分支并配置 `marketplace-publish` Environment 的专用密钥后才会生效，
仅添加 workflow 文件不表示自动发布已启用。

成功发布后 HTTP 缓存最长 60 秒；客户端显式刷新会要求重新验证。
采用直连现有广州 TOS 域名，不新增 CDN。将来增加 CDN 必须遵守此响应头，不得配置额外固定长缓存。
轮询常态预期为 5 分钟调度周期 + Git 生成时间 + 最多 60 秒 HTTP 缓存；GitHub 调度可能延迟，
这不是严格 SLA。需及时上架时在审核合入后触发发布，确认 workflow 成功和公开 latest 的 catalog_commit。

## 权限边界

- 普通用户只需要匿名 `GetObject`，不需要桶枚举、写入或任何 AK/SK。
- 官方发布凭据通过发布环境的 `WATCHER_TOS_ACCESS_KEY_ID/ACCESS_KEY_SECRET` 注入。
  推荐专用子账号，只授予桶 `erroright` 中 `WatcherRobot/marketplace/cn/*` 的 GetObject/PutObject。
  凭据不得进入 SDK 文件、客户端构建环境产物、日志或 Git。
- 可用 `WATCHER_TOS_BUCKET/REGION/ENDPOINT/PUBLIC_ORIGIN` 配置发布目标，默认沿用桌面更新链路的
  `erroright`、`cn-guangzhou`、`tos-cn-guangzhou.volces.com` 和公开域名。
- 发布器不修改桶策略/ACL，不授予公共写权限；公共读取可限制为该前缀，不要求整桶开放。
- 当前部署应只读审核桶策略和 ACL；不要用匿名上传来测试权限。

## 安装限制和后续方案

此版本只迁移目录。`GiteeRepository.download_repository_snapshot` 仍访问 Gitee commits、
git/trees 和 blobs API，因此目录正常时安装仍可能返回 rate_limited。
不能据此宣称商店全部恢复，不能要求用户安装 Git 来绕过。

后续可在官方审核发布端额外生成固定源码归档和内容校验清单，上传不可变 TOS 路径；
SDK 下载时保留完整提交身份、树/文件校验、路径与大小限制、安装事务和来源 URL。
这需要独立设计与测试，不在当前目录快照合同中加入未经验证的归档旁路。

正式验收仍须在无预装 Git/Python 的干净 Windows/macOS 环境，用重新内置本次 SDK 的安装包
验证浏览、断网回退和安装；冻结分发程序的 PATH 隔离测试只能作为部分证据。
