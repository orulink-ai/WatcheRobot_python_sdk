# 依赖实时进度与历史卸载残留清理

- 创建时间：2026-10-15T15:39:56.721635+08:00（Asia/Shanghai）；作者：orulink-wugui；参与者：Codex。
- 类型：修复；需求：用户要求修复依赖安装阶段 41% 停留并清理历史卸载残留，兼容 Windows/macOS；用户已实测原 Windows 卸载正常。
- 分支：codex/tos-marketplace-20261014；SDK 与 Desktop 配套修改；状态：开发与本机验证完成；已获用户授权在当前分支提交、推送，未打包。
- 边界：不修改 Daemon 路由、不删除用户应用或共享缓存，不改变现有 Windows 卸载合同；Desktop 已有生成 schema 修改原样保留。

## 验收与方案

- uv stdout/stderr 跨平台有界流式读取，实时发送经过允许名单过滤的阶段/计数，不透传原始路径和凭据；保留超时、失败重试和输出上限。
- Desktop 显示解析、下载、准备、安装真实活动及计数；百分比只由实际完成检查点计算。
- SDK 清理只匹配 trash/<32位小写hex>-uninstall 的普通目录；拒绝 root symlink/junction，不跟随子链接；保留 -previous、失败事务、runtime/cache。
- 清理可重试；单个占用不妨碍其它历史残留清理；正常安装/卸载继续运行。提供独立 SDK CLI 清理入口。
- 按 TDD 先加入失败测试，覆盖并发输出、超时/输出上限、路径/链接/只读文件与清理错误。

## 关联历史

补充并修复 [安装结果与卸载进度](../2026-10-15_105949_wugui_无Issue_安装结果与卸载进度/index.md) 的阶段内可观测性与旧 trash 清理缺口；保留原成功卸载行为。该档案已补充反向链接。

## 验证记录

以下为实际执行记录，早期用例与最终结果分别记录。


## 2026-10-15T15:53:53.157748+08:00｜orulink-wugui / Codex｜实现、验证与交付

### Red

- 首次临时测试环境因缺 websockets 无法收集，不作为功能失败依据；补齐现有开发依赖的搜索路径后，SDK 新测试因缺少 dependency_progress 模块和 cleanup 接口而收集失败，符合尚未实现的预期。
- Desktop 新依赖活动测试失败：41% 只有静态进度，缺少 is-active 状态。
- 后续安全用例先证实独立清理错误触发共享 GC、缺少 Windows 只读删除 helper、旧 Python 的 trash reparse parent 未拒绝；修复后同一用例通过。

### Green 与回归

- SDK：`python -m pytest -o addopts= -q tests/distribution tests/test_app_cli_usability.py tests/runtime/test_frozen_windows_paths.py`：最终 **482 passed / 6 skipped**，约 3.86 秒；有一项现存 importlib Traversable 弃用警告。使用临时 Python 3.12 测试环境、当前 SDK src 和现有开发依赖；`WATCHER_RUNTIME_INSTANCE_ROOT` 隔离到临时状态目录，未操作用户运行时 GC。
- 最终全量分发回归前曾有两项子进程找不到 SDK 的环境失败，补齐 PYTHONPATH 的 SDK src 后这两项及分发回归均通过；没有用跳过测试掩盖失败。
- Desktop：广场 UI、展示逻辑与 i18n 三个测试文件 **38 passed / 0 failed**；`tsc --noEmit` 通过。
- 修改的 SDK 三个源码模块 `mypy --follow-imports=silent` 通过（按仓库 Python 3.10 配置检查）；不是整个 SDK 的 mypy 全量结论。
- 修改源码与新增测试 `ruff check --ignore TRY004` 通过；TRY004 是 install.py 原有依赖报告类型错误表达，未扩散修改。新增模块/用例已格式化；两个仓库 `git diff --check` 通过。
- 真实 uv 0.11.16：通过生产 SystemApplicationEnvironmentRunner，在临时 venv 安装 pytest 及其依赖共 5 包，观察到 0.00 秒 resolving、3.71 秒 preparing、4.23 秒 downloading、18.04 秒下载完成与准备完成、18.06 秒安装完成。输出 6 个实际活动事件；使用临时 uv cache，结束后清理测试目录，未向开发/用户环境安装该测试包。
- 本机已执行新 `watcher-distribution app cleanup --store-root <国内store> --jsonl`：真实返回 removed_count=2、failed_count=0；再次只读确认 `*-uninstall` 为 0，原失败安装事务保留。

### 行为与边界

- uv 双管道由两个线程并发读取，有界队列回到主线程发事件；保留 stdout/stderr 各 1 MiB、300 秒超时、失败重试；超时/异常终止进程树。
- 只将允许名单内的 uv 摘要转为阶段和计数，不把包名、URL、路径和凭据带到 UI。未知输出仍保留活动指示，不虚构下载字节、速率或持续增长的百分比。包计数仅是该子阶段明确报告完成的数量；未知总量不显示分母。
- 新清理入口取得既有写锁，不触发共享 Runtime GC；安装/卸载前在原操作锁内重试历史残留。匹配规则为直接普通目录 trash/<32位小写hex>-uninstall，保留 -previous、失败事务、apps、runtime 和缓存；Windows reparse/junction 与符号链接不跟随，普通只读项按需解除只读；占用项保留待重试，显式 CLI 以 error+退出码5报告计数。
- Windows/macOS 两种环境目录布局、CRLF、reparse、只读与占用错误分支已覆盖。用户已实测旧 Windows 正常卸载；**本次新增功能没有在 Windows 真机运行**，不把模拟分支测试描述为真机验收。
- 未新增开发者系统脚本，复用跨平台 Python CLI，未新增单平台 Shell/PowerShell 入口。
- 当前开发分发入口使用工作区源码；尚未形成新 SDK commit，未伪造/更新 Desktop runtime-source-lock，也未重新打包。正式发行需在有提交授权后将 SDK 提交写入来源锁并重建资源。
- 未修改 Daemon 路由、未提交、未推送、未创建 PR；Desktop 既有两份生成 schema 改动保留，未混入本次实现。

Workspace 原核查档案为 meta repo 中的“商店安装进度、卸载与 Gitee 读取核查”；独立子仓库不包含该本机档案，正式证据以本档案的代码、测试与验证记录为准。

## 2026-10-15T15:58:08.585778+08:00｜orulink-wugui / Codex｜提交与推送授权

- 用户明确授权 SDK/Desktop 分别提交并推送当前分支，Desktop 同步锁定新 SDK 真实提交；不创建 PR、不提交 workspace gitlink。
- 本次提交包含功能实现、测试、正式文档与历史档案双向关联。SDK 先提交，Desktop 随后在来源锁与 commit body 引用该 SDK 提交；SDK commit body 指明配套 Desktop 同名分支及预期提交主题。
- 两个远端目标分支在提交前均与本地 HEAD 一致，无未完成 Git 操作，空白检查通过。
- 提交风险扫描的命中已人工核对：SDK 的 token 读取表达式、AccessToken 类型声明，以及 Desktop 已有 bluetooth-smoke 命令参数解析；均为变量或类型，不是真实凭据。本次新增内容不包含凭据或本机缓存。
- Desktop 原有两份生成 schema 改动排除；原分发回归 482 passed/6 skipped、前端 38 passed、tsc/mypy 通过的结果沿用，不因单纯提交而重复全量测试。来源锁更新后另做锁相关验证。
- Windows 新增功能未作真机复测，正式包未重建；推送结果以最终 Git 远端核对为准。
