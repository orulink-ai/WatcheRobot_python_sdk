# 国内应用广场内嵌 Git 获取

- 日期：2026-10-15 Asia/Shanghai；作者：wugui / Codex。
- 用户授权：实施和验证 Dulwich 方案；本次不提交、不推送、不推进 PR。
- 分支：codex/tos-marketplace-20261014。
- 状态：已整理中文提交并迁移最新main；迁移后回归通过，待最终推送；未合入、未发布，干净安装器与macOS验收仍待后续。

## 方案与边界
官方 app-list.json 是审核名单；app.json 位于开发者仓库的审核指定提交。
SDK 内嵌 Dulwich 匿名 HTTPS 获取官方目录和应用源码，缓存固定提交供安装复用。
不要求用户安装 Git/Python；不执行系统 Git、hooks、credential helpers 或自动子模块。
开发者发布和投稿保留现有 API；不修改 Daemon 业务路由；桌面更新 TOS 不变。
移除前一阶段商店专用 TOS 模块、脚本、工作流，已上传对象保留。

## TDD 与验证过程
- Red：新增 tests/distribution/test_gitee_git.py 后模块不存在，测试收集失败。
- Green：实现固定提交读取、持久缓存、安全文件检查后首批 9 项通过。
- 发现 Windows 路径过长导致重开缓存对象丢失；缩短仓库摘要目录并保留完整 SHA，测试通过。
- 后续测试和真实安装结果待追加；不将协议探针当作正式安装验收。

## 历史关系
本任务替代 2026-10-14 的商店 TOS 分发决策；缓存保护、兼容性和并发刷新规则延续。

[前阶段档案](../../2026-10-14/2026-10-14_200000_wugui_无Issue_TOS商店目录/index.md)。

## 2026-10-15 实际实现与验证结果

### 实现
- SDK：GiteeGitRepository + 独立 GitTransport；国内 marketplace/download/install 使用同一固定提交磁盘缓存。
- 匿名 HTTPS，空 Git config，禁用 HTTP 重试与重定向；固定 Gitee origin，保留系统 HTTPS 代理；TLS 证书随冻结程序打包。
- SHA depth=1，有限深度 32/256 回退；收包 256 MiB/获取 180 秒、单次读取 30 秒；源码 100 MiB/1000 文件。
- Git 对象校验和跨平台路径规则；拒绝 symlink/submodule/LFS；缓存损坏重取、容量 1 GiB 淘汰、全局 FileLock 并发保护。
- CLI 业务校验未绕过；兼容性在用户机器计算；错误区分远端失败与非法源码。
- Desktop 移除商店 TOS URL 参数，打包收集 Dulwich/filelock，能力检查同时覆盖浏览、下载和安装。
- 删除本功能分支新增的 TOS 商店模块、发布工作流、双平台发布脚本与专用依赖；旧正式文档标记历史方案，保留记录与远端对象。
- 桌面更新 TOS、开发者发布/投稿 API、Daemon 路由不变。

### TDD / 回归
- Red：新模块不存在导致 test_gitee_git.py 收集失败。实现后首批 9 项通过；新增边界后专项 18 项通过。
- SDK 分发全量：422 passed，1 个既有 importlib.abc.Traversable 弃用警告。
- 专项覆盖固定 SHA/HEAD 区别、跨实例缓存、目录刷新、危险路径/symlink/LFS、非 commit 对象、历史回退、网络失败不重试、失败暂存清理、缓存损坏、淘汰、体积限制、取消和并发。
- ruff check 新模块及测试通过；mypy --follow-imports=skip 检查 gitee_git.py、cli.py、download.py 通过（非全仓严格检查）。
- Node 相关测试 38 项通过，含能力门禁、SDK-only 构建、区域、商店状态与进度。
- Rust CN application_ 回归 68 passed / 3 原有外部依赖测试 ignored；另显式运行新冻结 SDK Git 联网/断网测试 1 passed。

### 真实联网与固定历史提交
- 官方目录 ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631；4 项完整读取与校验；说话动作实验室 0.2.1 / a425b35168a27e77f99224062feb91aeb464a008。
- 无系统 Git PATH、禁止外部进程的直接 SDK 探针：首次 33.19 秒、5 次源码获取（目录+4应用）；二次刷新 0.76 秒、新增源码获取 0；导出48个文件也未新增源码获取。
- 历史提交 f933ae2bf1b22f396bb24b668ad9a392ebf0bd43 真实读取 0.2.0，耗时18秒，没有误取当前 HEAD 的0.2.1。有限历史回退本身由本地对象测试覆盖，不夸称所有远端历史都可获取。
- 临时 PyInstaller onefile 用 Desktop 的 distribution_runtime_entry.py、当前 SDK 源码和 Dulwich/filelock collect 参数构建成功。
- Rust 实际调用冻结程序：联网返回4项含0.2.1；无效HTTPS代理导致刷新失败，from_cache=true，旧缓存字节不变。

### 完整安装
- 新冻结分发程序执行 app install --provider gitee，在独立临时 store 中成功安装说话动作实验室0.2.1，固定提交匹配，退出0，用时52.48秒。
- 子进程 PATH 只有 Windows System32：系统 Git/Python 均不可查找；移除 TOS 凭据和 Python 路径覆盖。
- 使用现有 Desktop app-runtime 的 Python3.12.13、uv0.11.16、SDK0.1.11 wheel（原e2d1170来源），依赖安装、冻结及事务提升均成功。该 wheel 用于应用环境，不将它描述为已含本次 Git 改动的新发行包。
- 所有原始临时报告和日志保存在 workspace 的 _local_artifacts/tos-marketplace/dulwich-*；未纳入 Git。

### 交付限制
- 全部为本地修改；未提交、推送、更新PR、合入或发布客户端；未删除已上传TOS对象。
- source lock 仍是原SDK e2d1170；用户本次未授权提交，因此不能伪造新SHA。正式构建门禁会拒绝旧锁/脏源码，后续授权提交后才能更新真实锁和正式重打包。
- 没有干净Windows VM正式安装器验收，也没有macOS实机验收；PATH隔离与冻结运行时验证不等于上述验收。
- 首次浏览需要下载各应用源码，实测4项约33秒；不能承诺Git协议永不限流。
- Desktop 原有Cargo.toml改动及未跟踪app-runtime备份未操作。

## 2026-10-15 提交与最新基线迁移｜wugui / Codex

用户授权分别提交和推送 SDK/桌面功能分支，迁移最新 main/dev 并清理已合入分支，不创建 PR。
最终交付将旧 TOS 中间提交与本次 Dulwich 改动整理为一个中文 Conventional Commit；SDK 基于最新 origin/main cc0a319（0.1.12）。
配套 Desktop 功能分支为 codex/tos-marketplace-20261014（目标 dev），将使用本次迁移后 SDK 的真实提交更新来源锁与运行时版本。
前文未授权提交的状态由本条新授权替代；已完成的0.1.11运行时安装证据仍保留，不能改写成0.1.12正式安装验收。

### 最新基线回归
- 基于 origin/main cc0a319（SDK0.1.12）重新执行分发测试：422 passed，1个既有弃用警告，用时31.76秒。
- 新模块/测试 ruff check 通过；gitee_git.py、cli.py、download.py 的 mypy --follow-imports=skip 通过。
- 旧TOS中间提交与Dulwich实现已合并为一个最终提交；原历史可从远端旧SHA与临时bundle追溯。
- Desktop配套分支将锁定本提交；按用户本次授权推送，不创建PR，不合入主分支。
