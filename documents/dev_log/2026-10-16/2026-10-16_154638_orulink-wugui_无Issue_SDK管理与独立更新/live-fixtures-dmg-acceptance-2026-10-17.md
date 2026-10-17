# 可选联网夹具迁移与新 DMG 验收（2026-10-17）

## 当前结论

两个遗留 opt-in 入口已迁移；使用新 DMG 挂载后的 SDK 分发工具跑完六个原生 opt-in，全部通过。新测试包的实际桌面启动、同一 Daemon 的双桌面复用、首个退出保留服务、最后退出清理均通过。没有提交、推送、Tag、上传或发版。

本文补充并部分替代 [历史失败修复报告](historical-failures-regression-2026-10-17.md) 中“另外两个夹具未迁移”和“本轮未重建 DMG”的遗留项；原报告和失败证据保留。本次修改限桌面测试夹具、测试说明及两仓档案，SDK 管理实现未再改动。验收时间截至 2026-10-17 10:14:50 CST。

## 两个入口如何迁移

| 入口 | 原问题与实际核对 | 迁移结果 |
|---|---|---|
| frozen_sdk_git_refresh_and_offline_cache_without_system_tools | macOS 实际执行因读取 Windows SystemRoot 而失败；还固定某个历史应用成员 | Windows/Unix 都使用空工具 PATH，不保留 System32；获取非空官方目录，严格校验整份快照、完整源提交、落盘读回；不可达进程代理触发 remote_error，旧缓存字节、完整列表和中文提示必须保持一致 |
| live_sdk_install_and_uninstall_contract | 固定 Gitee repo，与 Global provider 不一致；夹具只声明支持 Windows；依赖外部传入 store | 每次创建独立 HOME、SDK 状态和应用 store；从当前区域官方目录选择 SDK/宿主均兼容的 SDK Test Bench，固定完整 commit 安装；核对 ID、版本、源 URL、commit、列表、12 步完成进度及实际下载；卸载核对目录、trash、列表和所有计数 |

更正此前笼统的“旧应用已迁移/下架”描述：2026-10-17 实测 Gitee 仍包含 KID_gitee 的 speaking_motion_lab 旧提交，原安装入口在正确 CN 配置下可安装成功。它不是当前可跨平台运行夹具，且固定来源不适用于 Global；本次迁移目的包括平台、来源和隔离目录正确性，不将原入口能安装误说成失败。

共享辅助模块仅在 `cfg(test)` 编译。新增三项确定性测试：固定校验后的完整提交、不兼容/不存在的应用拒绝，以及无效提交/外域 URL/重复记录拒绝。它不进入桌面产品，也不承载 SDK 管理逻辑。

所有 live 入口的 HOME 和操作目录均隔离，退出自动清理；不再使用调用者可能指向用户数据的 WATCHER_TEST_STORE_ROOT。默认 fixture 为 example.sdk_media_lab；缺失或不兼容必须失败，不随机改装其他应用。需要维护时可显式设置 WATCHER_TEST_APPLICATION_ID，仍必须来自已校验的当前区域目录。

安装测试在私有进程中显式设置清华 PyPI 索引下载依赖，不依赖开发机现有缓存；没有改生产 SDK 的安装策略。初次新 HOME 试跑在默认外部索引下载阶段停留，被主动停止后改为明确测试索引；该失败/停止日志保留，不计入最终通过数。

固定实测提交：

- CN/Gitee：mystic-rhythm/WatcherRobot-example.sdk_media_lab，7930d7fcf94560c0c8d2dcd560311bc026fd5afe，1.1.0。
- Global/Hugging Face：yuzou1121/WatcherRobot-example.sdk_media_lab，cb7064a7fdf43c6cd00a5d84fb1ec135f74e77e8，1.1.0。

两者分别验证各自目录及源 URL，不能互换来源，也没有将动态目录成员固定为历史 smoke commit。

## 新 DMG

- 类型：本地 test / CN / darwin-arm64；不是正式发行物。
- 桌面显示版本：0.2.13；实际 CFBundleVersion：1792201902；SDK：0.1.12。
- 物理大小：313,433,869 字节（约 299 MiB）。
- SHA-256：`5c3ec6727092909470550bfdcda2a7262a936802968859ec4f0997d0fa04801f`。
- 制品路径：`<workspace>/WatcheRobot_client/Watcher Desktop App/.tmp/desktop-build/cn-0.2.13-darwin-arm64-1792201901503/Watcher.Desktop.CN_0.2.13_aarch64.dmg`。
- Source：SDK 07e1b42c216d2b21343d7400837b8cc24db75577，桌面 ec9979ce45dacbce1de050d4f6a566fe8a7a6501；两者均为 codex/sdk-manager-ota-20261016 上的未提交工作区，不用基线 SHA 冒充最终提交。
- SDK-only-v1，default_application_included=false；包含 Daemon、分发、维护、Python/uv，不含默认 Application。
- App 与 DMG Developer ID 签名校验通过，TeamIdentifier LP4D78XZ45；未提交 Apple 公证。Gatekeeper 实测返回 Unnotarized Developer ID，不能标记为正式分发通过。测试包按设计禁用桌面 OTA。

## 最终回归与挂载验收

| 项目 | 结果及边界 |
|---|---|
| 桌面 Node 全量 | 703 项：702 passed，0 failed，1 个 Windows 专用 skipped；6.91 秒 |
| CN Cargo 全量 | 163 passed，0 failed，6 ignored；binary 目标运行成功且无用例 |
| 挂载新 DMG 后六个 opt-in | 6 passed，0 failed，0 ignored；用例执行 165.36 秒（含 Cargo 命令总计 170.94 秒） |
| Global Cargo 全量 | 163 passed，0 failed，6 ignored |
| Global 实际联网 opt-in | 当前目录与断网缓存 2 passed / 28.29 秒；真实安装/卸载 1 passed / 73.21 秒。用冻结资源与 DMG 已核对一致的 SDK，未另外构建 Global DMG |
| SDK 全量 | 沿用本日上一轮未改 SDK 实现后的 2076 passed / 14 skipped，以及另跑 6 项真实 wheel 安装/切换/故障恢复/回滚通过；本轮未重复冒充新执行 |
| Renderer/类型与构建门禁 | 新构建实际重建 Renderer，通过对应门禁；此前 TypeScript 通过，SDK 实现/前端未再改动 |
| DMG 文件 | hdiutil verify、只读挂载、SHA-256、App deep/strict codesign、DMG codesign、arm64 架构、版本核对均通过 |
| 挂载后 SDK 资源 | profile、全部 artifact 哈希、Application Runtime Python/uv/wheel 哈希及锁一致 |
| SDK 管理 | 选定 SDK 0.1.12、兼容、status/resolve 与常驻双请求关联通过；仅查询不启动 Daemon；生产信任配置为空，在线更新禁用符合本地测试边界 |
| Renderer 源快照 | 新包所用的 50 个预览、22 个动作、17 个音效均匹配原发行 catalog 哈希，没有重算放宽目录 |
| 实际 release-mode 桌面 | 原 30 秒预算复验在 22.53 秒内启动；双桌面同 PID、首退保留、末退停止全部通过；无 startup auto-start failed 日志 |
| 退出物理清理 | 0 个本次进程、0 个 runtime-state.json 主/兼容别名、0 个桌面 lease 残留；测试 HOME 删除，所有测试卷已卸载 |

六个 opt-in 逐项包含：正式目录刷新、远端失败缓存、空 PATH frozen Git、实际安装与卸载、只读原生串口发现、实际 SDK 管理桥接并发关联。没有将 ignored 自动视为通过，也没有通过跳过联网安装来凑结果。串口发现不等于设备业务验收。

## 冷启动首次超时记录

第一次新包原生冒烟使用 30 秒探针，未在期限内取得状态，结果保留为失败。没有因此更改产品代码或放宽最终验收的 30 秒预算。独立新 HOME 诊断实际启动成功，24.22 秒；随后串行按原 30 秒预算重新验收成功，22.53 秒，并完成双桌面和退出清理。较长诊断观察窗口仅用于取证，不计作原 30 秒门禁通过。

当前证据确认功能可启动且复验满足原预算；首次超时的唯一诱因尚未证实，应保留为冷启动时延波动观察，不能宣称所有新机/所有冷启动都稳定小于 30 秒，也不是一次完整性能 SLO 验收。首次失败、诊断与最终通过日志分开存放，没有覆盖首轮记录。正式首次安装的新机器表现仍需专门复验。

release-mode 不启用 debug-only 的 SMOKE_APP_DATA_ROOT；实际隔离使用 HOME 和 SDK 实例/状态目录，App 数据落在测试 HOME 的 Library/Application Support。没有修改用户真实 App 数据、原有 Daemon、系统代理或设备配置。

## 清理与文档

本机开始时仅余约 1.3 GiB，已移除本任务被替代的旧测试 DMG、可再生成的本 crate incremental 缓存及完成后的 PyInstaller 中间目录；保留最终两轮 DMG 与来源/日志。没有清理其他任务的 v0.2.21 制品、个人文件或真实应用环境。安装/卸载实测和退出校验完成后，只删除隔离测试 HOME。SDK 仍在使用的受保护资源延期清理规则不变。

更新 desktop-build.zh-CN.md 的环境变量、跨平台空 PATH、fixture 选择、清华依赖索引及六项入口执行方式。两个用户原有 schema 文件按最初原始字节保留；Server/ESP32 源码未修改。无新的对外系统脚本，因此没有新增单平台入口。

## 还需 Windows 验收

- SDK 的 8 项 Windows 专用：MAX_PATH/长路径、原生扩展加载、CreateProcess、控制台继承及环境启动等。
- Node 的 1 项 Windows 安装进程测试、PowerShell 真实入口、Windows 原生 Rust/进程与文件占用路径。
- Windows NSIS 安装、升级、卸载、签名、原生 OTA 安装及失败恢复；模拟与 macOS 检查不能替代。

## 还需真实设备验收

- 同 LAN 的真实发现、配对、连接、断线重连及对应固件版本兼容。
- 蓝牙权限、扫描目标识别、真实连接和配网；本机看到附近广播不等于已配对设备成功。
- 声明了业务合同的 Application 端到端：Desktop→Daemon→Application→Daemon→Device，无应用时透明转发、SDK 接管/回滚后的业务恢复；麦克风、语音、设备控制等实际链路。
- 维护/OTA 操作与 SDK 切换互斥、失败恢复的真实设备场景。此次串口枚举未打开端口、刷写或发送设备业务指令。

## 另外的上线前事项（不是 Windows/设备限制）

- macOS 真正的原生桌面 OTA 安装及中断恢复，需两个可升级、信任链完整的制品；当前 test 包按设计关闭 OTA，未绕过限制。
- SDK 正式发行签名配置、镜像中新功能版本的正式在线升级；本轮按用户要求只本地测试，不发 SDK 或桌面版本、不索取私钥。
- 默认 Application 合同声明迁移仍在两个仓库之外，无声明继续显示未验证；不通过白名单绕过。
- 本轮只构建 Apple Silicon CN DMG；Intel Mac、新 Mac 的首次安装和 Global 独立安装包未验收。Global provider 的 native 后端联网入口已验证，不能混同为 Global 安装器通过。

## 原始证据索引

结果已在本文固化；原始日志暂存任务临时目录，不作为永久制品或源码提交：旧入口 red；fresh HOME 默认索引中止与清华重测；Node 全量；CN Cargo 全量及 mounted 六项；Global Cargo 全量/三项联网；DMG build-record、挂载 proof、签名与 Gatekeeper；首轮原生超时、24.22 秒冷诊断及原 30 秒严格复验。临时日志不包含需交付的用户密钥；不提交包含个人路径的原始运行输出。
