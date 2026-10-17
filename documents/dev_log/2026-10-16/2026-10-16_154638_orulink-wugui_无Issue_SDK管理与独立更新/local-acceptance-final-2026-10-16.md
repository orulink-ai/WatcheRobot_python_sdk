# SDK 独立更新：本机最终验收报告（2026-10-16）

## 结论和范围

核心代码已修改完成，本轮补充的 macOS 本机验收已执行完。不能将此结论表述为跨平台、真实设备和正式线上发行全部验收通过。没有提交、推送、打 Tag、上传或发布 SDK/桌面版本。

实现以 SDK 为唯一管理源码：兼容判断、版本目录、清华下载、安装、切换、恢复、回滚和引用保护清理均在 SDK；桌面仅承担调用、展示及原生操作的活动登记。仅一份选定 SDK、一个下载任务；临时候选及受限回滚备份不作为多版本库暴露。Application 独立环境仍由 SDK 管理；后启动且版本不同的 SDK 可接管共享 Daemon。桌面安装包不包含默认 Application。

代码基线：SDK origin/main `07e1b42c216d2b21343d7400837b8cc24db75577`；桌面 origin/dev `ec9979ce45dacbce1de050d4f6a566fe8a7a6501`；各自在 `codex/sdk-manager-ota-20261016` 分支修改。验收针对未提交工作区，不能用基线 SHA 冒充改动后的提交号。

## 自动化与实际验收

| 项目 | 本轮最终结果 | 覆盖与限制 |
|---|---|---|
| SDK 全量 pytest | 2076 passed、14 skipped、3 warnings，162.09 秒 | 14 个跳过中，6 个 opt-in 已另跑通过，8 个仅支持 Windows |
| 实际 SDK wheel 更新与故障测试 | 6 passed，79.54 秒 | 安装、激活、回滚、入口转交；ENOSPC 提交失败和管理进程被 kill 后恢复，再回滚 |
| Rust 常规测试 | 160 passed、6 ignored | 忽略项见下表；不将 ignored 当作通过 |
| Rust 真实 SDK 桥接 | 1 passed | 同一管理进程处理并发请求并保持请求关联 |
| Rust 实机串口发现 | 1 passed | 只读发现，不刷机、不代表硬件业务联调 |
| 桌面完整脚本测试 | 701 项：697 passed、3 failed、1 skipped | 3 项全部在干净桌面基线上复现，原因见下节 |
| 源码总入口/平台分发 | 12 passed | 根工作区配套脚本，无默认 Application 依赖 |
| 开发 wheel 缓存等选测 | 23 passed | 未提交 SDK 源码变更强制重建开发 wheel |
| 激活说明对应生命周期测试 | 7 passed | 本轮仅更正注释和文档，不改生命周期逻辑 |
| 静态检查 | SDK mypy 145 文件、针对改动的 ruff、桌面 TypeScript 通过 | 三仓 git diff --check 通过 |
| 清华实际下载 | 通过 | 镜像最新稳定 wheel 0.1.11；真实下载 6,279,441 字节并核对索引 SHA-256 |
| macOS DMG | 构建、挂载、hdiutil、签名、资源哈希、SDK 管理入口通过 | CN/test、arm64、未公证；不含默认 Application |
| 实际原生生命周期 | 3 场景通过 | 桌面启动 Daemon 后退出清理；复用先启动 SDK 同一 PID；两个桌面共用同一 PID，首个退出不关服务，最后退出清理 |
| 原生任务与更新互斥 | 通过 | 活跃 desktop_ota lease 阻止切换，所有者被 kill 后回收过期 lease |
| 实际设置页 | 通过 | 选定/实际版本 0.1.12，合同状态、业务组状态；点击清理显示进行中→完成，重开保持状态；退出回收测试 Daemon |
| 实际应用下载/安装/卸载 | 通过 | 新冻结 SDK、空 PATH 无系统 Git；读取 Gitee 当前目录，安装 SDK Test Bench 1.1.0、查询列表、卸载、复查空列表及空 trash |

实际应用测试固定源 `mystic-rhythm/WatcherRobot-example.sdk_media_lab`，提交 `7930d7fcf94560c0c8d2dcd560311bc026fd5afe`。安装 12.28 秒、列表 3.15 秒；具有独立 `.venv`，安装并未启动应用。检查没有残留相关进程；实例根目录存在协调锁及引用元数据，不能将目录存在误判为 Daemon 启动。未修改或发布应用仓库。

本地 SDK 测试 wheel 为 0.1.12，新功能尚未在公开镜像发布。新代码更新/恢复测试采用本地 wheel、临时测试签名和受控目录；与实际清华下载分开记账，不能声称公开镜像已有新管理功能。生产信任配置为空时，设置页禁用在线检查/安装，错误明确；本轮按用户要求不配置生产密钥、不发版。

最新 DMG SHA-256：`0e3417683d545b1fa03eeb2b46a76051feee300051ecef3942622f60f26cc678`。SDK wheel 清华下载 SHA-256：`ce3d6c03f54993e0d485b027fcccd007419888cffc3b7a677e097116b6d40926`。

## 原先三项失败的完整排查

使用 `git archive` 导出未修改的桌面 HEAD，同一 Node 25.2.1 运行三项，全部复现。没有删除断言或更改资源哈希以伪造通过。

| 失败项 | 实际原因 | 本次修改影响 |
|---|---|---|
| bundled official resources are one complete v0.0.8 snapshot | 22 个 action JSON 实际为 LF；目录中记录的哈希全部与转换为 CRLF 后的字节吻合。50 个预览及 17 个声音哈希正常。资源由既有提交 6ed11ef8 引入。 | 既有资源字节一致性缺陷；JSON 语义正常，当前内置资源使用 Vite JSON 解析。与 SDK OTA 包哈希、安装或 Daemon 切换无关，但资源完整性测试仍失败。后续应统一生成/存储的换行策略及目录，不能忽略校验。 |
| App.Center app package protocol constants aligned | 测试期待 ESP32 的 APP_CENTER_REMOTE_APP_ID，但 f40391ca 已移除旧 ESP-NOW Remote 支持；桌面 6064bcfd 已删除设备 App.Center UI。残留策略模块无生产引用。 | 旧设备应用协议测试过时；不是本次 Python Application 的安装或兼容合同。无需为了本次改动恢复已删除功能。 |
| App.Center production signature canonicalization aligned | 同一旧设备 App.Center 删除提交 6064bcfd 已移除 scripts/sign-app-package.mjs，测试还在读取它。 | 旧设备应用签名脚本测试未迁移；与新增 SDK 更新签名目录没有共用此脚本。 |

因此：没有发现这三项由本次 SDK/桌面修改引入；桌面全量套件仍有失败，必须保留这一事实，不能称全绿。

## 补跑 ignored Rust 联网测试的额外发现

- `bundled_sidecar_failure_uses_existing_cache`：真实 sidecar 按预期返回 `remote_error`，但旧测试在进入缓存断言前要求 SDK 错误原文必须为中文；实际 SDK 返回英文 `Unable to load the latest Application marketplace`。桌面展示层已有自己的中文规范化消息；此处是旧边界断言错误，缓存行为的常规单元测试通过。本轮未掩盖或改写该断言。
- `bundled_sidecar_refreshes_the_live_official_catalog`：成功获取当前线上目录后，旧测试固定要求 `com.orulink.marketplace_smoke` 的历史提交 `18c396...`，当前目录已不再提供该快照。不是获取目录失败。本轮已用当前 Gitee 目录及固定实际提交跑通下载、安装、卸载。
- `frozen_sdk_git_refresh_and_offline_cache_without_system_tools`：原用例要求 CN，且无条件读取 Windows SystemRoot 并固定历史 speaking_motion_lab；不能在当前 macOS/global 测试构建中按原样运行。等价的最新 CN frozen SDK 空 PATH 读取 Gitee 目录和安装链路已通过；原用例未标记通过。
- `live_sdk_install_and_uninstall_contract`：固定旧 Gitee repo 和历史提交，不能把当前外部目录已迁移视作新更新功能失败。以当前 SDK Test Bench 的固定提交完成等价实际安装、列表及卸载验证；原用例未冒称通过。
- 其余两项 ignored 分别是真实 SDK 桥接和本机串口发现，均已单独通过。

这两项补跑失败属于本次额外发现，未计入常规 701 项脚本中的“三项”；最终结果明确保留，不将其隐藏在通过数中。后续将在线目录断言改为当前目录结构/固定可维护 fixture，错误文本应按 SDK 与桌面展示边界分别断言。

## 清理、回滚及用户数据保护

已验证取消最后一个下载块后仍清理、进程中断后的候选/缓存恢复、引用保护/清理预算/回滚保留期、真实更新失败恢复及回滚；不能承诺正在使用或无法确认引用的文件立即删除。设置页显示延期清理和错误，避免假报清理完成。

本轮所有联调使用隔离 HOME、SDK 状态、应用仓库及动态端口。未终止用户已有 Daemon；未触碰用户设备和业务数据。构建过程中生成的两个 schema 文件已按开始前字节恢复，保留用户原有未提交修改。清理仅涉及本任务中间构建文件和测试隔离资源。

## 更正之前激活描述

此前档案里“激活一律强制重启”的说法不准确。实际 `--activate-shared` 选择调用方 SDK：同版本可以复用，版本不同才按现有规则接管；管理事务在确有需要时显式 force。已更正文档及 smoke 注释，未更改现有规则，历史正文保留并由此补充纠正。

## 尚未完成的验收/后续事项

1. Windows 的 8 项平台专用 SDK 测试、PowerShell 实际执行、Windows 安装包与原生运行，需 Windows 环境；本轮不冒称完成。
2. 真实设备同局域网发现/连接、设备与 Application 的业务协议端到端，以及蓝牙授权/连接。串口只读发现和模拟协议测试不替代这些场景。
3. 桌面原生 OTA 真正安装更新及失败恢复、双版本安装器互升，需相应安装环境/制品；已测活动互斥和构建/发布入口，不等同于真实安装完成。
4. 正式在线 SDK 签名目录与生产密钥、公开镜像中新管理版本的端到端升级留到未来发行验收；本次明确不发版，不作为继续本地测试的前置条件。
5. 三项常规历史失败及两项额外联网旧断言需要独立整理。不得为此越界修改 ESP32 或默认 Application，不恢复已删除的旧设备应用发布链路。
6. 默认 Application 当前无业务合同，SDK 将其显示为未验证并按合同阻止相关受保护入口。补齐其声明和合同测试不在此次两个仓库范围；不能给无声明应用设置白名单来凑验收通过。

本次补充阶段仅更正文档/注释并增加验收材料，无新增产品行为修改；不适用新增功能 TDD，采用实际运行和基线对照验证。功能实现的 Red→Green 记录见前续 validation 文档。

## 证据定位

完整本机日志保存在任务临时证据目录，非长期制品，也不提交含本机路径的原始日志。关键结果已在本文固化：SDK 全量 JUnit/日志、六项真实 wheel 测试日志、未修改基线三项复现日志、Rust 常规及 opt-in 日志、原生三场景/lease JSON、DMG 挂载结果、清华下载 JSON、实际应用安装/卸载 JSONL。后续提交时应将本文和相关代码一起审阅，不将临时文件视为永久链接。


## 后续修复（2026-10-17）

本文对应三项历史失败和两项旧断言已由 [历史失败修复与完整回归](historical-failures-regression-2026-10-17.md) 处理；保留本文作为修复前证据。平台、设备和正式发行等未验收边界继续有效，以后续报告为准。
