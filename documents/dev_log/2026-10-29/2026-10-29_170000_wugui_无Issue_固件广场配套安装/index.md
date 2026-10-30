# 固件广场与应用配套安装

- 创建日期：2026-10-29，Asia/Shanghai；作者：wugui（仓库 git config）。
- 分支：codex/firmware-marketplace；基线：origin/main 8b1ca88ecd880124d687f51583e0bd6de4131ab3。
- 配套仓库：WatcheRobot_client 同名功能分支，基线 origin/dev 5797dc66ae38cbb4e7980ee1e749f4fca4bbc6af。
- 状态：源码链路及自动化验证已完成；真实平台/设备和正式打包验收待执行，未提交、推送或发布。
- 确认方案：https://mcnsslrwxv50.feishu.cn/docx/IndjdCRKNoyiC6xmcH0c68WNnQc

## 目标与边界

两类资源支持应用、固件、应用＋固件三种体验。海外 HF Dataset、国内 Gitee Release；应用引用官方或自定义固件。旧端只读原应用目录，新资源隔离。USB 写入不要求 ESP32/STM32 源码修改；不增加 Daemon 内容路由旁路，不自动刷机或伪造启动版本确认。

## 实施与验证

先建立固件清单、受限版本范围、包校验和依赖判断测试，再实现核心。平台 API、关联目录、下载缓存、任务互斥、桌面入口和双平台实机验证仍需逐项完成。目录和 v4 清单不能在配套执行门禁完成前向旧路径开放。

## 验收

- [x] 双平台发布、获取、下载与重试（源码与模拟 API）。
- [x] ZIP 结构、芯片、写入范围与哈希校验。
- [x] 官方依赖、配套计划和旧版目录隔离。
- [x] 桌面 USB 目标确认、任务与失败重试（模拟设备交互）。
- [ ] Windows/macOS 实机及原应用回归。

## 2026-10-29 首批改动与 TDD 证据

- Red：新增 package/requirement 与 catalog 测试首先因目标模块不存在而失败；CLI 测试因 firmware 子命令不存在失败；畸形引号用例首先暴露未归类 ValueError。
- Green：实现固件包/依赖/独立目录纯合同，以及两个 CLI 的 check/capabilities 入口；畸形引号转换为 FirmwareError。
- 回归：工作区既有 desktop-dev Python 执行 `-m pytest tests/distribution tests/runtime/test_application_manifest.py -q`，515 项通过；原有 Traversable 弃用警告仍存在。
- 静态检查：`ruff check` 新模块及新测试通过；`mypy src/watcherobot/firmware --follow-imports=silent`，5 个源码文件通过。格式化后已重复回归。
- 正式说明：[实施状态](../../../../docs/application-marketplace/firmware-implementation.zh-CN.md)。
- 尚未完成：平台读写、关联目录/v4/下载、写入任务、桌面可见入口、冻结产物和实机验收。没有提交、推送、正式平台修改或设备烧录。

## 2026-10-30 继续实施（wugui）

按用户“继续接通、不提交不推送”要求补齐源实现；上方 10-29 的“尚未完成”是当时状态，现由本节逐项更新，保留原始历史。

- 新增 HF Dataset 和 Gitee Release 适配、不可覆盖版本发布/重试、独立固件目录 PR、匿名流式下载和校验缓存。Gitee 附件路径参照官方 v5 swagger；接口测试使用模拟传输，没有生产写入。
- app.json v4 接入校验、应用分发与安装；沿用 v3 的精确依赖锁。独立 application-list-v2.json 保证旧目录只收录旧格式。官方或作者固件必须先在同来源审核收录，应用引用固定推荐及兼容范围。
- SDK firmware execute 提供受控发布/下载/依赖预览/计划/USB 写入/历史；复用原 esptool，第三方不强制 READY。进程锁同时供桌面旧烧录入口使用；持久化任务不把仍持锁的运行任务误判中断。
- 只记录 last_write，不伪造设备当前版本回读；不改动 MCU 源码、Server 或 Daemon 业务路由。中断使用完整重刷，无自动回滚。
- 新增正式实施文档及目录初始化模板；未向 HF/Gitee 添加实际目录。桌面正式 SDK 来源锁等待真实配套提交后再更新，未编造 SHA。

### TDD 与验证证据

- Red → Green：新增预览哈希与目录断网回退用例首先失败；实现确认失效/独立缓存回退后通过。预先存在的本机缓存会错误通过公开发布检查，用 404 模拟用例复现后改为强制全新匿名核验。v 前缀版本选择用例先失败，统一语义版本比较后通过。
- SDK：`pytest tests/distribution tests/runtime/test_maintenance_service.py tests/runtime/test_maintenance_extensions.py tests/runtime/test_application_manifest.py -o addopts='' -q`：606 passed；2 个已有告警（Traversable 弃用和刻意重复 ZIP 条目）。含双来源匿名下载和 Gitee 元数据已提交但 Release 尚未创建的重试恢复。
- Ruff 新模块/测试及相关修改文件通过；mypy 新模块与 manifest/check/marketplace/install 共 13 个源文件通过，使用当前测试环境 Python 3.12，缺失第三方类型标注按 import-untyped 忽略。
- 配套桌面：92 项 Node 测试通过；Rust lib 157 passed / 4 ignored；维护 worker 12 项 Python 测试通过；tsc 与 renderer 构建通过。浏览器只使用模拟平台与 USB 检查交互，未烧录真实设备。

### 发布前剩余验收

真实 HF/Gitee 账号发布、PR 与匿名下载；Windows/macOS 硬件及断线重试；正式 SDK 来源锁和安装包验收。本次所有改动保留未提交状态，不更新 meta repo 的 gitlink。

## 2026-10-30 10:48:10 Asia/Shanghai — 按最终范围收敛与本地交付（wugui）

本节替代前文关于 schema 4、application-list-v2.json、SDK 新增烧录执行器、单独固件顶层页的设计，保留上述历史调查记录。用户最新授权：HF/Gitee 目录可以更新；SDK 和桌面可本地提交，中英文 commit 说明，不推送两仓库。

- 应用与固件独立资源，唯一 app-list.json 保留已有应用；可选 requires_firmware 字段兼容 schema 2/3，缺失不猜测、填写错误拒绝。未为旧桌面二进制另建目录。
- SDK 仅分发/依赖/包校验与 Wi-Fi 元数据；移除本任务先前增加的 flash/plan/ports 动作和设备写入锁，原 SDK 维护源码保持原状。Daemon 路由不变；标准元数据由 Application 的既有通道传递，麦克风帧仍进入 Application。
- 桌面调用设置页原维护 worker 完成 USB 写入；目标重新识别、包哈希核对、私有快照、设备互斥和退出保护。没有新增 ESP32/STM32/Server 源码修改。
- 页面为“应用与固件 / 我的应用 / 上传”，内部应用/固件分类；保留原应用发布向导。固件复用应用卡片，按 ID 聚合版本；点详情再下载、看设备状态、选择 USB 烧录。修复目录刷新误占烧录操作锁。
- Wi-Fi 新鲜版本与历史写入分开；官方 0.5.7 只上报版本不能自动认定官方身份。第三方 USB 更新，保留官方设备原 OTA；未新增桌面 ESP32 OTA 执行器、签名信任链、自动回滚或任意偏移续写。
- 两仓库直接在主检出目录的 codex/firmware-marketplace 分支工作；本任务隔离 worktree 已移除，原文件保留并核验，桌面源码热更新运行。未触碰其他任务 worktree。

### 本轮 TDD 与验收

- 卡片聚合先写测试：同一固件多个版本只一张卡、数值排序、撤回隐藏、搜索覆盖作者和机型；实际卡片渲染检查应用样式及无设备控制。随后完成实现，测试通过。
- 私有快照回归先复现：校验后原 ZIP 被替换，旧执行器会读到替换内容；改为桌面有界快照后通过，保证写入与审核摘要一致。
- SDK distribution/application/manifest：606 passed；既有 Traversable 弃用告警 1 项。Daemon 业务路由专项：20 passed。新模块 Ruff 与 mypy（Python 3.12、忽略缺失第三方类型导入）通过。
- 桌面相关 Node：115 passed；Rust lib：159 passed / 4 ignored；维护 Python：17 passed；tsc 与 renderer 构建通过，构建仍有既有大 chunk 提示。
- 原生桌面看到现有应用仍显示、分类和上传入口保留。真实固件组件用明确标识的测试数据验证卡片、详情版本切换、无 USB 时禁止烧录；未把测试数据写入线上目录、未烧录硬件。

### 平台与文档

- Gitee 新增 firmware-list.json 并更新 README；匿名 SDK 实际读取到提交 3af3d7312e12684242da917f8f1381559a4a6c74 的空目录；原 5 条应用逐字节不变。
- HF 现有凭据不能直写组织主分支（HTTP 403），已建立 https://huggingface.co/datasets/Orulink/watcherobot-app-store/discussions/56；匿名读取 PR 固定 commit 5542cf65cab08afdb0ed3a4b566f65243c35518b 的目录通过，原 9 条应用不变。主分支仍待维护者合入，不宣称生效。
- 完整方案已更新本仓库正式文档，并在 https://mcnsslrwxv50.feishu.cn/docx/IndjdCRKNoyiC6xmcH0c68WNnQc 顶部加入当前方案与两张新流程画板；历史正文保留并标为历史。第三张画板服务端创建失败，保留文字联动流程，没有空白占位。
- 真实固件发布账号/产物、Windows/macOS USB 和断线恢复、正式打包及配套 SDK 来源锁仍需后续验收。未发布新安装包、未伪造官方固件条目。

配套档案：[另一仓库同任务](../../../../../WatcheRobot_client/documents/dev_log/2026-10-29/2026-10-29_170000_wugui_无Issue_固件广场配套安装/index.md)。完整方案：[本仓库文档](<../../../../docs/application-marketplace/firmware-implementation.zh-CN.md>)。

## 2026-10-30 全链路复核与推送准备（wugui）

用户本轮允许 SDK 和桌面端提交、推送功能分支，明确不创建 PR；该授权替代前文仅本地提交的限制，其他边界保持。

- 通过真实匿名 Gitee API 复现不存在文件返回 `200 + []`，先补回归用例并看到原发布器 TypeError，再处理此响应；非文件元数据明确拒绝，保留 404 和已存在版本重试行为。
- 补充双平台组合测试：应用实际安装编排（环境 runner 为替身）→ 从安装目录读取固件依赖 → 推荐版本与同平台目录解析 → 首次下载 503 → 应用仍保留 → 匿名重试成功 → 后续复用校验缓存。此部分属于既有实现的组合补测，没有描述为先写功能。
- `pytest -o addopts='' tests/distribution tests/application tests/runtime/test_application_manifest.py tests/runtime/test_application_manifest_metadata.py tests/runtime/test_standalone_application_routing_contract.py -q`：616 passed，既有 Traversable 弃用警告 1 项；原错误测试路径已纠正，没有把路径错误计入通过结果。
- Ruff 检查固件模块及两份相关测试通过；mypy 检查 9 个固件模块通过（Python 3.12，忽略缺失第三方类型）。真实网络读取 HF 9 个应用、Gitee 5 个应用，分别固定提交下载源码成功：HF example.sdk_media_lab@cb7064a7fdf43c6cd00a5d84fb1ec135f74e77e8（25 个文件/目录项）、Gitee com.orulink.expression_lab@f7faae287f78f9ac4f9349b5475916f111e1519f（23 项）；未运行源码或安装其依赖。
- 网络探测的初次调用未创建要求的空 staging 目录，被下载器正确拒绝；修正探测后下载通过，不是业务代码缺陷。
- HF 主目录缺少 firmware-list.json，既有结构 PR 仍待维护者合入；Gitee 空固件目录读取通过。没有发布测试固件或新建平台审核请求，真实固件上传/下载及设备烧录仍需产物和实机验收。
- 配套桌面分支仍为 codex/firmware-marketplace，在此前 4589bd0e 基础上修复配套失败页面跳转、选择上下文及 USB 位置锁，并将在 SDK 推送后更新来源锁。本次不改 ESP32/STM32/Server，不提交 meta gitlink。

## 2026-10-30 合并前审查（wugui）

- 用户允许审查、追加修复提交并合并 SDK main 与桌面 dev，替代前文不创建 PR 的限制。审查入口为 SDK #153、桌面 #187，继续使用当前主检出目录。
- 复核分发、包验证、Wi-Fi 元数据桥接、已安装应用依赖读取与共享 USB 边界，没有新增 Daemon 业务分支。修正 HF 目录模板中的 space_id 误写，统一为真实目录使用的 repo_id；线上 HF #56 已先行修正，此处同步本地模板，防止再生成错误说明。
- 全量 `pytest -o addopts='' -q`：2306 passed / 10 skipped / 2 warnings，耗时约 219 秒。首次收集因本地开发 venv 缺 tomli 失败，补齐 pyproject 已声明的测试依赖后通过，不以环境错误修改业务代码。
- 桌面全量 CI-safe Node：671 passed。远端 CI、自动审查及后续来源锁同步在 PR 中继续跟踪；本条不提前声明合并成功。
- 文档修正不增加镜像断言，使用既有文档与全量回归验收；保留真实固件发布与实机烧录未验收的边界，不因合入代码宣称已发布固件或安装包。

### 合并审查补充修复

- Luxiao 静态审查提出并确认三项边界：configure 的并发覆盖窗口、Flash 擦除范围、显式 null 未声明化。先写回归并复现 5 个失败场景，再追加实现：按清单路径共用 FileLock，临时文件保存前复检原字节；偏移 4KiB 对齐并核对擦除范围；Manifest 区分缺失字段与显式非法值。
- 测试覆盖两个配置请求同时使用同一预览、临时文件写入期间外部编辑、非对齐分段、同扇区不同字节范围、相邻合法扇区，以及 null / 空对象 / 数组 / 字符串。聚焦回归 42 passed，Ruff 通过；最终全量回归正在新代码上重新执行。
- 外部编辑器不参与 SDK 锁，仍存在最终复检到替换之间的极短竞争窗口；正式文档明确合作锁边界，不宣称严格外部 CAS。SDK 没有新增硬件写入代码。
- SDK 远端运行 41016412077 的三个作业均在 Set up job 失败，注解为 Failed to resolve action download info / An error occurred while sending the request，未执行源码测试。未改 workflow 绕过检查；追加修复提交后使用新一轮检查验证。
