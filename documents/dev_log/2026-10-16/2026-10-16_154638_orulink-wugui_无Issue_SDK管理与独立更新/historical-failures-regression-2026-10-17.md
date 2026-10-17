# 历史失败修复与完整回归（2026-10-17）

## 状态

用户要求修复三项历史失败、两项旧断言，禁止为通过测试放宽校验，并重跑完整测试。五项已修改完成；本报告补充并部分替代 [10 月 16 日本机验收报告](local-acceptance-final-2026-10-16.md) 中对应五项未解决结论。其余平台、设备和发行验收缺口仍保留。没有提交、推送、上传或发版。

## 五项修复及保留的校验

| 原失败 | 根因与本次修正 | 校验强度 |
|---|---|---|
| 内置 v0.0.8 资源哈希 | Git/工作树换行归一化使 22 个动作 JSON 变成 LF。恢复匹配原发行 SHA-256 的 CRLF 字节；按路径设置 `-text` 禁止 Git 转换、`whitespace=cr-at-eol` 正确认定 CR 为换行组成。 | `desktop_catalog.json` 逐字节未修改；50 预览、22 动作、17 音效全部按原哈希验证。新增 Git clean 后 blob 不变、属性必须关闭 text 转换、单字节损坏不再匹配原哈希的回归。不是忽略空白再计算哈希。 |
| 旧 App.Center 常量对齐 | 测试错误要求已删除的 ESP-NOW 支持仍存在。改为验证遗留线格式和当前固件拒绝规则。 | 保留 magic、帧类型、14 字节头、包大小上限对齐；精确校验当前四项权限白名单及旧 esp-now 的拒绝分支，确认旧宏不再启用。默认应用和 ESP32 源码未改。 |
| 旧设备应用签名工具 | 桌面 signer 已随旧设备 App.Center 移除；不恢复已删除发布功能。 | 保留固件规范化 JSON、摘要、公钥摘要和 ECDSA 验证断言；增加无可信密钥、不可信 key/issuer、摘要不符、签名不符、无签名包的拒绝检查。对真实 verifier 源码做四种内存 mutation，去掉规范化/可信密钥/摘要拒绝/ECDSA 任一检查都必须使合同测试失败。属于源码合同测试，不冒称硬件签名执行测试。 |
| 联网目录固定历史成员 | 动态官方目录不能永远要求固定的 marketplace_smoke 历史提交。 | 非空有效目录，每个源提交保持 40 位十六进制、仓库/应用 ID/源 URL/平台等现有严格 validator；获取结果、返回视图和落盘后重新读取的完整应用列表与 catalog commit 必须完全一致。固定 commit 下载/安装仍由 SDK 对应测试验收。 |
| 断网缓存错误文案 | SDK 机器错误误按桌面中文文案断言。 | SDK 精确断言稳定 `remote_error` 错误码及非空错误消息；桌面仍精确断言中文“获取最新的广场内容失败”、warning_code、from_cache、完整旧内容与原缓存字节不变。没有修改生产错误处理。 |

本轮产品源行为仅修复资源字节；Rust 变动限测试模块。管理、兼容和更新逻辑仍只在 SDK。测试未删除或 skip，上述两个 opt-in 都真实执行并通过。

## 验证结果

| 检查 | 最终结果 |
|---|---|
| 桌面 Node 全量 `node --test scripts/*.test.mjs` | 703 项：702 passed、0 failed、1 skipped，6.71 秒。唯一跳过是 Windows 专用 runtime-installer-process 测试。较上一轮新增 Git 字节保真和签名 mutation 两项。 |
| SDK 全量 pytest | 2076 passed、14 skipped、3 warnings，183.39 秒。含 6 个需要制品的 opt-in，另跑见下一行；8 个 Windows 专用跳过。 |
| 真实 SDK wheel 安装/更新/回滚/故障恢复 | 6 passed、1 warning，92.96 秒；真实安装/激活/回滚、ENOSPC 和 kill 后恢复全部通过 |
| Rust `cargo test` | 160 passed、0 failed、6 ignored；二进制测试目标无用例且运行成功。 |
| Rust 另跑 opt-in | 4 passed：真实目录完整缓存读回、断网缓存保留、真实管理桥接并发关联、只读串口发现。前两项共 21.59 秒。 |
| 桌面 TypeScript | 通过 |
| Renderer 生产构建 | 通过，4.45 秒；仍有既有 bundle size 提示 |
| 根工作区源码启动/平台分发 | 12 passed |
| diff、资源目录及用户改动保护 | 三仓 diff --check；发行 catalog 未改；两个用户已有 schema 变更按原始字节保留；Server/ESP32 工作树干净 |

SDK 三个 warning 是 Traversable 弃用、Starlette/AnyIO 弃用，以及专门测试重复 ZIP 条目的预期警告；无测试失败。Rust 仍有已有 macOS 下未使用 GBK import 提示，本轮不扩展无关清理。

## Red → Green 与范围

- 修改前定向 Node 执行再次得到同样三项失败；两项联网旧断言的 Red 沿用 10 月 16 日真实 sidecar 测试日志。
- 恢复资源、修正当前边界后，原定向 14 项全部通过；随后增加两项回归，完整 Node 703 项无失败。
- Rust 选测第一次发现新测试缺少私有函数/常量 import，编译失败；补齐 import 后通过。随后桌面提示断言恢复使用独立中文 literal，完整 cargo 及四项 opt-in 再次通过。该编译错误仅在测试模块，未描述为产品缺陷。
- 真实 SDK 功能测试及资源、签名 mutation 是回归保障；新增回归不伪称全部先于修复撰写。SDK 本轮未新增实现，跑完整回归验证不受影响。
- 正式文档更新：creator-sd-works.zh-CN 的发行字节约束、current-architecture.zh-CN 的目录/错误边界验收说明。

## 仍未验收

1. Windows 专用 SDK 8 项、Node 安装进程测试 1 项，以及 PowerShell 入口实际运行、Windows 安装包/原生生命周期。macOS 无法替代。
2. 真实设备 LAN 发现、连接、业务帧经当前 Application 路由及跨应用业务合同；蓝牙授权与连接。只读串口发现不等同于这些验收。
3. 桌面原生 OTA 实际安装、版本互升及安装失败恢复。活动互斥和构建脚本测试不等同于安装器真实更新。
4. 公开镜像中配套新管理 SDK 的正式在线端到端升级：本轮只做本地 SDK wheel 与测试签名，不发版，不要求用户现在提供生产密钥。此前清华真实下载/校验已通过，不冒称当前公开 0.1.11 已含新管理实现。
5. 默认 Application 的业务合同声明与合同测试仍未迁移，属于本次两仓范围外；无声明保持未验证，不放行白名单。
6. Rust 另外两项 opt-in（冻结 CN/Gitee 无系统工具与旧固定应用安装合同）未按原测试入口执行：前者原夹具硬编码 Windows SystemRoot，后者固定已迁移的历史仓库；昨日已通过当前 CN frozen SDK 的空 PATH 目录读取和当前固定提交的安装/卸载等价实测，但原测试入口本身仍需专门迁移，不列为此次五项之一，也不计为通过。
7. 本轮未重新生成 DMG。10 月 16 日 DMG 的挂载/签名记录仍只代表当时制品；本轮已重建 Renderer 并验证资源，正式发行前需从最终代码重建安装包并校验。未发布任何制品。

## 本机原始证据（临时、不作为永久制品）

- `watche-sdk-history-red-20261017.log`：原三项定向失败。
- `watche-sdk-desktop-final-20261017.log`：完整 Node；新增单字节/Git 保真与签名 mutation 含在全量结果中。
- `watche-sdk-full-20261017.log/xml`：SDK 全量及 JUnit。
- `watche-sdk-live-final-20261017.log`：真实 wheel 六项。
- `watche-sdk-cargo-all-20261017.log`：全部 Cargo 测试目标；`watche-sdk-rust-final-20261017.log`：四项额外验收。
- `watche-sdk-tsc-20261017.log`、`watche-sdk-renderer-20261017.log`、`watche-sdk-workspace-20261017.log`：静态、构建、根入口。

上述原始日志暂存本机临时目录；本文固化可审阅结论，不提交原始个人路径、运行缓存或大制品。


## 后续迁移和重打包（2026-10-17）

[可选联网夹具迁移与新 DMG 验收](live-fixtures-dmg-acceptance-2026-10-17.md) 已迁移剩下两个入口，并生成新的 CN arm64 本地测试包。新包挂载后六项原生 opt-in 全部通过；本文对应未迁移/未重建两项已被后续结论替代。首次冷启动探针超时和原预算复验记录一并保留。
