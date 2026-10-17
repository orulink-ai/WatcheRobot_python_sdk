# 2026-10-16 验证记录（持续追加）

## 边界

仅修改 SDK、桌面。默认 Application 仓库不修改，安装包不包含默认 Application；缺失业务合同不设兼容白名单。桌面打包及本地安装包验收在范围内；本次未对外上传发版。安装包与在线发行验收分别记录，测试包不等于正式发行包。

## 17:39 前已有证据

- SDK 首次稳定全量快照：2030 passed / 9 skipped，后续代码有变化，不作为最新快照结果。
- 新增业务组状态后的全量：2040 passed、2 failed、11 skipped；两处失败均为旧精确字典断言未包含新增 `desktop_contracts: {}`。核对实际差异后补齐预期，业务路由/取消选择/管理选测 88 passed、3 skipped。
- 管理代理 TDD：停止操作原先只等待 30 秒；大响应原先完整读取后才限制大小。新增两用例先失败，修改为停止等待 120 秒、流式限额后通过；管理选测 15 passed。路径规范化校验另行补测通过，不伪称该部分先有 Red。
- 错误码 TDD：缺少 SDK errors 模块时用例失败；实现稳定代码、建议动作及异常因果链识别。桌面保留 SDK 错误字段，仅展示；错误提示 UI 用例先失败、后通过。
- 更新前清理 TDD：新增“退休旧回滚 -> 清理无引用环境 -> 磁盘检查 -> 候选准备”的顺序测试先失败，按此顺序修改后通过。仍在使用或无法确认引用的资源延期清理，不能报告已删干净。
- 此阶段管理全目录 79 passed / 3 skipped，ruff 通过，mypy 16 个源文件通过；随后增加了更新前清理用例，最终结果另行追加。
- 桌面 UI / 控制 / 冻结入口选测 44 passed；较早组合 82 passed；最新 TypeScript 检查通过。
- 最新 Rust 全量：159 passed / 5 ignored。生成 schema 文件按运行前原始字节保存并恢复，保留用户原有改动。
- 最新本地 wheel 的真实安装/激活/回滚用例：3 passed，49.80 秒。依赖制品来自清华，本地签名测试目录及 HTTP 响应受控；测试版本 99.0.0 仅本地 fixture，未发布。所有进程使用隔离目录及动态端口。

## 第一轮 macOS 测试包

构建类型 `test`，darwin-arm64，CN，内部版本沿用 0.2.13，不是对外发版。Desktop/SDK 的 HEAD 分别为 ec9979ce45dacbce1de050d4f6a566fe8a7a6501 / 07e1b42c216d2b21343d7400837b8cc24db75577，二者均 dirty；构建从本地工作代码生成，不能把 HEAD 当作已提交功能源码。

- 构建及 121 项前置测试通过；冻结管理 status / resolve、生命周期与资源校验通过。
- DMG SHA256：`84e9f7b1a6b4a7e4447319db3941d3f5749c5de3a82602f07f0e9879b39db491`。
- DMG 挂载并核对实际资源：`bundle_profile=sdk-only-v1`，`default_application_included=false`，SDK 0.1.12；资源哈希及 App deep/strict codesign 通过，hdiutil verify 通过。
- 挂载路径 `/tmp` 与 `/private/tmp` 实为同一文件，首次管理 resolve 门禁用字符串比较误报。改用真实路径比较后，在实际挂载包执行管理 status / resolve 通过；查询未启动 Daemon。挂载卷已正常卸载。
- 后续管理代理、错误码、清理等代码有更新，因此以上测试包不能作为最新全部改动的最终制品；已启动第二轮完整构建，结果待追加。
- 测试包使用现有本机签名身份签名；未做 Apple 公证、正式发版或外部机器安装，不能宣称正式交付验收完成。

## 尚未验证

正式 SDK 签名公钥与目录发布、清华正式镜像端到端、Windows 原生安装/桌面 OTA、设备真实业务链路、完整硬中断/磁盘故障矩阵和连续更新的实际磁盘曲线。默认 Application 合同迁移不在范围内，其缺失声明保持未验证。当前发行公钥为空，在线检查/更新安全禁用。

## 17:54 常驻管理入口

- 常驻改动前全量 SDK：2052 passed / 11 skipped，180.65 秒；第二轮 macOS 测试包构建通过。
- 冻结管理命令实测首次 6.81 秒、再次 3.69 秒，接近桌面状态请求 4 秒上限。为避免每次查询都解压冻结 SDK，新增 SDK 所有的常驻 stdin/stdout 管理入口，桌面仅复用通用通信连接。该进程不监听端口、不连接设备、不承担业务路由，不是第二个 Daemon。
- SDK 流式入口的并发/响应对应先测缺少模块失败，再实现后通过；长操作不阻塞 status。状态文件大小门禁先测写入超限状态失败，修复为替换前检查后通过。
- Rust 真实 SDK 常驻桥接并发/复用用例显式启用执行通过（0.47 秒），普通 Rust 全量 160 passed / 6 ignored；新增响应对应/重复结果的补充回归通过。不伪称所有补充用例均在实现之前编写。
- 管理模块 83 passed / 3 skipped，ruff 与 mypy 17 个源文件通过；包含入口回归的组合 95 passed / 3 skipped。
- 最新 wheel 实际安装、激活、回滚、由常驻主入口转交选定 SDK 并保持请求标识：4 passed，42.92 秒。使用隔离实例，不影响用户现有 Daemon。
- 最新包含常驻入口的 macOS 测试包与 SDK 全量正在运行；结果待追加。


## 18:34 脚本与发布链路同步（尚未正式发布）

- 用户明确要求同步打包、发布和源码运行脚本。SDK 增加 run-sdk、verify-sdk-management-release、prepare-sdk-ota-release、publish-sdk-ota-release 四组成对 Shell/PowerShell 入口；桌面既有平台入口继续委托共享 Node 核心。
- 源码启动以隔离状态执行真实 SDK 合同预检；构建依赖检查包含管理入口、常驻传输和 cryptography。桌面合同声明使用单一 JSON 数据源，SDK 仍负责判断。
- 冻结包门禁检查单次/常驻管理、命令解析和不启动 Daemon，构建记录保存验收证据；正式上传和推进 OTA 拒绝缺失证据、缺少公钥或测试模式记录。
- SDK 目录工具绑定原始 SDK wheel 和嵌入公钥。新增 main/受保护 pypi 环境/OIDC 的手动目录发布工作流，检查正式 SDK Release、PyPI 摘要、目录版本及 sequence 单调性，再验证清华镜像。未触发工作流、未发布或上传。平台依赖锁仍需发行者验证，公钥与 Trusted Publisher 尚未配置。
- SDK 全量常驻入口快照 2055 passed / 12 skipped；真实冻结状态查询冷启动约 6.89 秒，常驻进程后续两次查询各约 0.001 秒。管理进程不监听端口，不构成第二个 Daemon。
- SDK 脚本/诊断选测 27 passed；SDK mypy 145 源文件通过，新增工具 mypy 4 文件通过。桌面 TypeScript 通过，Rust 160 passed / 6 ignored。
- 诊断状态先测缺失 diagnostics 失败，再实现 SDK 允许字段导出；桌面选中/实际合同及诊断展示先失败后通过。发布测试模式拒绝用例先失败后补门禁。
- 桌面全 scripts 扩大回归首轮 700 项中 693 passed、6 failed、1 skipped。其中三项为本轮接口引入后的旧 fixture/断言，已更新并回归相关 30 项全部通过；其余三项为既有官方资源摘要和跨仓 App.Center 协议测试。已确认 listening.json 的 HEAD 内容与工作区相同但不符目录摘要；缺少 sign-app-package.mjs 和 ESP32 常量属于未修改范围，不放宽断言掩盖问题。
- 最新完整打包第一次被旧发布测试 fixture 阻止（120/121）；修正 fixture 后重新打包。此前三份已生成的测试 DMG 保留，清理本任务产生的旧 source/pyinstaller 中间目录约 3 GiB。当前最新完整打包与 SDK 全量仍在运行，结果另行追加。
- Windows 原生 PowerShell/安装器未运行；本机没有 pwsh，不能把结构和模拟测试称为 Windows 原生验收。现有用户 schema 改动保持 66 additions / 510 deletions，各自原始字节已保留。


## 18:36 最后回归结果（安装包仍在构建）

- SDK 最新完整代码快照：2066 passed / 12 skipped / 3 warnings，164.69 秒。
- 使用重新构建的真实 SDK wheel：4 passed / 1 warning，47.07 秒，覆盖隔离安装不启动 Daemon、真实激活与回滚、签名测试目录更新、常驻主入口转交选定 SDK。正式镜像目录未发布，不冒称在线 OTA。
- 桌面全 scripts 再回归：696 passed / 3 failed / 1 skipped，共 700 项。仅剩既有 listening.json 与 catalog 摘要不一致、ESP32 ESP-NOW Remote app id 常量缺失，以及旧 sign-app-package.mjs 文件不存在；没有删除或放宽这些测试。
- 新增/修改入口 ruff 通过；SDK mypy 145 文件通过，TypeScript 通过，Rust 160 passed / 6 ignored。SDK 工作流 YAML 语法解析通过（仅本地解析，未触发 CI）。
- schema 文件与运行前备份逐字节相同，保留用户已有改动；SDK/桌面 diff --check 均通过。未提交、推送或上传。


## 18:42 最新脚本快照 macOS DMG 完成

- CN / darwin-arm64 / test / 0.2.13 完整构建通过，前置 121 项测试通过。源代码 dirty，未提交功能代码；不是正式发行物，不用于证明受保护发布流程已执行。
- `Watcher.Desktop.CN_0.2.13_aarch64.dmg` SHA256：`d2e6c16dcbce02a4398ba777c9885fa4aab5873c8120f22c18b3518602079f4f`。
- hdiutil verify 通过；实际挂载后资源哈希、App deep/strict codesign、SDK-only profile 和“不包含默认 Application”检查通过。
- 实际挂载包 SDK status / resolve / 两请求常驻管理校验通过：SDK 0.1.12、compatible、未启动 Daemon；正式信任未配置如实为 false。测试包允许此状态，正式上传/OTA 推进门禁拒绝此状态。
- 挂载验收脚本首次误用 `Contents/Resources/server` 路径，实际 Tauri 资源在 `Contents/Resources/resources/server`；修正测试路径后通过。第一次和最终挂载均正常卸载，未修改产品源码掩盖测试错误。
- 用户现有 Daemon 进程仍为原 PID，未被本轮测试接管。用户原有两个 schema 文件与备份逐字节相同。
- Workspace 最外层源码入口亦已配套去除对 Server/default-app 的强制检查及环境注入，仍只编排 Desktop/SDK，不实现管理功能。先测旧绑定失败及缺少依赖列表导出，再实现；源码与平台分发 12 项通过。仅配套改动根仓脚本/README，不修改 Server 仓库。
- 尚未完成：正式公钥与签名目录部署、平台完整依赖锁、真实清华目录端到端、Windows 原生 PowerShell/安装/OTA、真实设备业务和完整故障矩阵。仍不宣告飞书整体验收完成。无提交、推送、发布或上传。


## 19:00 本地验收继续与发行配置边界

- 用户明确：尚无正式公钥，先完成本地实现与验收。正式签名配置/目录部署与在线发布留待后续；不生成生产私钥，不把测试密钥写入 SDK 信任资源。当前独立在线更新仍禁用，不将本地签名 fixture 当作正式 OTA。
- 补充故障测试先复现两项残留：中断恢复遗漏下载目录；最后下载完成后的安全检查点取消遗漏下载目录。两个新增用例先失败；修复 SDK 内的检查点位置，并让恢复与手动清理共用资源清理流程，24 项相关回归通过。
- 新增实现后的故障回归，不伪称先写 TDD：状态原子替换遭 ENOSPC 保留旧状态且无 tmp 遗留；激活提交遇 ENOSPC 保留 durable transaction 并可恢复；真实管理子进程在切换前/切换后被 kill 后释放锁，用文件表示的 Daemon 模型恢复状态，不抢占后来外部接管。此组 34 项通过，随后加强模型状态断言并再次回归。
- 上述 hard-exit 用例使用真实进程与 SDK 文件锁、状态文件，但 Daemon 是文件模型，不代替真实设备或完整安装包断电验收。最新修复后需重新构建本地 SDK wheel/测试安装包。


## 19:06 本轮只做测试与真实跨进程接管缺陷

- 用户再次明确 SDK 不需要发布版本，测试即可；清华作为实际下载源。本轮不以配置公钥或执行 SDK 发布作为完成前置条件，不执行上传。产品正式在线链路留作后续发布验收，本地测试用隔离密钥和制品。
- 使用 SDK Mirror 下载器直接访问清华真实索引与制品，当前最新稳定 wheel 为 0.1.11，6,279,441 bytes，SHA256 `ce3d6c03f54993e0d485b027fcccd007419888cffc3b7a677e097116b6d40926`，索引哈希及实际 download 校验通过。该已发布 wheel 不含本轮管理入口；新增功能使用本地 0.1.12 wheel 验证，不发布测试版本。
- 新增真实 Daemon 故障回归首轮 5 passed / 1 failed。ENOSPC 在激活提交时失败、保留事务、恢复时复用已启动 PID、再回滚通过。管理子进程 hard-exit 用例在准备接管旧 Daemon 时失败，尚未达到计划 kill 检查点，不能称硬退出恢复已通过。
- 定位真实失败：旧 Daemon 是原管理进程的子进程，其他管理进程请求其退出后，原进程尚未回收使其短期为 zombie。process_shutdown 把不可再持有文件的 zombie 当作仍存活并拒绝接管。新增单元用例先失败；另覆盖真实存活和 AccessDenied 仍必须阻止切换。修复与重测待追加。


## 19:11 本地故障验证与源码缓存更新

- 修复进程退出判断：SDK 只将确认 STATUS_ZOMBIE/NoSuchProcess 的已退出进程视为资源释放；AccessDenied 或运行状态仍阻止切换。未更改业务路由、实例身份验证或不同版本后启动接管规则。相应进程/生命周期/事务回归 56 passed。
- 修复后真实 SDK wheel 全组 6 passed，79.54 秒。新增真实 ENOSPC 提交失败和管理进程 kill 两场景：目标 Daemon 已启动但选择尚未提交，recover 完成状态提交且 PID 不变，再 rollback 恢复基线。全部端口与实例隔离，不涉及正式发布或生产密钥。
- 最新全 SDK：2076 passed / 14 skipped / 3 warnings，166.49 秒；mypy 145 文件与新增代码 ruff 通过。6 项 opt-in 真实测试另行显式运行，不将默认跳过算作通过。
- 源码脚本再查出缓存边界：同一 SDK commit 下的未提交代码变化，旧 app-runtime wheel 可能继续通过版本/commit 校验。新增 forceRebuild 回归先失败；工作区启动检查 SDK src/pyproject/hatch_build/examples 的 Git 改动，存在变化则重建开发 wheel，再按既有资源合同验证。此项只为构建缓存失效，不比较 SDK 业务兼容或管理版本；正式来源仍保留真实 Git commit，不伪造 SHA。相关脚本 23 passed，TypeScript 通过。
- 前一快照测试 DMG 构建通过，但不包含此次进程退出修复。已启动包含最新修复的测试包重建，结果待追加。不运行正式 SDK 或桌面发布。


## 19:19 本机验证收尾（SDK 不发布）

- 最新 CN / darwin-arm64 / test / 0.2.13 DMG 完整构建通过，包含下载清理与 zombie 接管修复。制品 SHA256：`0e3417683d545b1fa03eeb2b46a76051feee300051ecef3942622f60f26cc678`。hdiutil verify、实际挂载 App deep/strict 签名、资源哈希、SDK-only 且无默认 Application 检查通过。
- 实际挂载包的 SDK 单次 status/resolve 和两请求常驻入口验证通过，SDK 0.1.12、compatible、daemon_started=false。挂载已正常卸载且临时挂载目录已删除；正式信任未配置如实记录为 false，仅本地测试不需要配置。
- SDK 全量：2076 passed / 14 skipped / 3 warnings；实际本地 wheel 六项更新、回滚及 ENOSPC/kill 故障恢复：6 passed；进程/生命周期/事务选测：56 passed；mypy 145 文件、ruff、TypeScript 均通过。
- 桌面全 scripts 最新：697 passed / 3 failed / 1 skipped，共 701 项。三个基线失败仍为官方 listening.json 与 catalog 摘要不一致、ESP32 ESP-NOW Remote app id 缺失、旧 sign-app-package.mjs 不存在；本轮没有放宽或删除这些断言。
- 源码开发缓存及相关入口选测 23 passed；根仓源码/平台分发 12 passed。早先 Rust 160 passed / 6 ignored，本轮之后只改 SDK/源码脚本，最新打包 Rust release 编译亦通过。
- 用户两个既有 schema 文件与原始备份逐字节相同，原用户 Daemon PID 保持不变。只删除本任务旧构建 source/pyinstaller 中间目录，保留测试 DMG 和构建记录；不删除应用环境或用户运行资源。
- 本轮完成的口径为本机实现与验证，不执行 SDK 或桌面正式发布、不生成生产私钥、不上传 PyPI/GitHub/TOS。Windows 原生安装/PowerShell、真实设备业务与桌面自身 OTA 实际安装仍未运行，保留后续验收状态，不称全部飞书产品验收完成。


### 2026-10-16 19:49 Asia/Shanghai｜orulink-wugui / Codex｜本机补充验收收口

核心修改已完成；本轮实际设置页、原生三场景、活动互斥、清华下载、冻结 SDK 当前应用安装/卸载已验证。原先三项在干净桌面基线上完整复现；额外补跑联网 ignored 发现两项旧断言，均保留失败记录。完整结果、影响及剩余事项见 [本机最终验收报告](local-acceptance-final-2026-10-16.md)。更正：激活不是一律 force，同版本可复用、异版本接管。未提交、推送或发版，Windows/真实设备/原生 OTA 安装仍待专门环境验收。


### 2026-10-17｜历史五项修复与完整回归

详见 [历史失败修复与完整回归](historical-failures-regression-2026-10-17.md)。保留原发行哈希恢复 CRLF 字节，迁移已删除功能的测试边界，补 Git 字节保真和签名 mutation 反例。两个真实联网测试通过；Node 全量 702 passed/1 Windows-only skipped，SDK 全量 2076 passed/14 skipped，cargo test 通过。无提交、推送或发版；正式设备、Windows、原生 OTA 等验收缺口见报告。


### 2026-10-17｜联网夹具与新 DMG 本机验收收口

见 [可选联网夹具迁移与新 DMG 验收](live-fixtures-dmg-acceptance-2026-10-17.md)。两入口已迁移，新 DMG 挂载后六项 opt-in 全通过；Node 702 passed/1 Windows skipped，CN/Global Cargo 各 163 passed，Global 三项 live 通过。实际桌面原 30 秒限制复验 22.53 秒通过，同 PID 双桌面和退出清理通过。首次 30 秒超时及诊断仍保留，不宣称所有冷启动都稳定小于 30 秒。无提交、推送、上传或发版；平台/设备/正式线上与新机缺口详见报告。
