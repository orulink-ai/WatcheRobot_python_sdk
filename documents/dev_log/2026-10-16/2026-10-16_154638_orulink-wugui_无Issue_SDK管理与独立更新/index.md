# SDK 管理与独立更新

- 开始：2026-10-16 15:46:38 Asia/Shanghai；作者：orulink-wugui；参与者：Codex。
- 用户授权：按飞书方案实施，先兼容检查，后SDK更新和设置页。
- 分支：codex/sdk-manager-ota-20261016；最新远端基线：07e1b42。
- 范围：兼容检查、管理入口、更新、Daemon、清理回滚。
- 状态：核心实现与本机功能验收阶段完成；Windows、真实设备及正式发行验收未完成。用户已授权提交并推送对应功能分支；当前正在提交，未合入、未发布。
- 方案：https://mcnsslrwxv50.feishu.cn/docx/Zi8tdalvvoAgyuxYLW3cVLxOnve （本轮读取revision46）；参考资料revision12。

## 验收与边界

以下复选框保留整体验收口径，不将本机通过等同于跨平台全部完成；当前通过项与缺口以最新报告为准。

- [ ] 统一兼容结果，调用方不再各自判断SDK能力。
- [ ] 一个共享更新事务、唯一候选、优先最新兼容稳定版。
- [ ] 显式不同版本SDK可升降级接管，普通应用/观察不换版。
- [ ] 更新失败恢复旧版及必要管理状态，不误杀外部接管。
- [ ] 清理保护引用、应用环境、运行中资源，失败如实报告。
- [ ] 桌面只调用和展示，桌面自身原生OTA继续保留。
- [ ] 安装即具备资源；设备连接仍需同一局域网，在线更新需互联网。

## TDD 与验证

最新结果见 [联网夹具与新 DMG 验收](live-fixtures-dmg-acceptance-2026-10-17.md)：Node 702 passed/1 Windows skipped；CN/Global Cargo 各 163 passed；挂载新 DMG 后六项 opt-in 全通过；实际双桌面生命周期通过。此前 SDK 全量 2076 passed/14 skipped，另跑六项真实更新/回滚通过。保留初次冷启动超时观察；原 30 秒预算复验 22.53 秒通过。Windows、真实设备、其他 Mac 和正式 OTA 尚未验收。

## 初始实施记录与后续更正（按时间保留）

- SDK 新增 management 模块：统一合同检查、持久化单任务、签名发行目录、受限清华镜像下载、候选安装、切换/恢复、引用保护清理、SDK 管理入口、原生任务登记。桌面新增通用 JSONL 调用桥接及设置页展示；兼容、推荐、管理规则未放进桌面。
- 先测后写：compatibility/state/releases/mirror/installer/deployment/cleanup/activity/service/publishing 缺失或目标行为错误时均记录过失败，随后实现。损坏环境状态查询、旧回滚到期保护、未验证 Daemon 拒绝管理也执行 red→green。UI 初版与实现同轮撰写，尚未补专门 UI 测试，不描述为 TDD 已通过。
- 最新 management 选测 42 passed；早先管理+共享 Daemon 管理回归 67 passed，管理+应用运行+安装 84 passed/1 skipped。后续修改后仍需重新回归。
- Rust 桥接输入/输出界限与透传选测 2 passed；TypeScript 初版检查通过。SDK 后续新增代码还需重新 lint/typecheck；Windows 与真实安装包、完整更新和桌面 OTA 尚未验证。
- 开发工具依赖仅安装到 /tmp/watche-sdk-manager-devtools（dulwich、ruff、mypy、types-psutil），未修改用户运行环境。
- 未完成：SDK 发布签名信任配置/发行物工具、实际镜像与候选安装联调、SDK 更新更完整的异常恢复用例、默认/实际环境路由、应用启动环境复检、原生 OTA 活动协调、清理预算/保留期执行、UI 测试和文档同步。
- SDK release-keys.json 当前为空，独立更新在配置可信发布键前禁用；不能声称当前镜像已有配套签名目录。未发布任何包。

### 2026-10-16 16:31 Asia/Shanghai｜orulink-wugui / Codex

- 用户明确以飞书最终版本为验收目标；新增 [完整验收跟踪](acceptance.md)，不将局部代码和测试等同于完成。
- 应用环境检查复用临时目录时被现有私有 shutdown 目录测试发现问题，改为有界异步管道；应用运行回归 22 passed。
- 选定 SDK 分发入口此前用 -m 调用未执行 main，新增真实子进程用例先失败，再改为显式 main；用例 1 passed。
- 未提交、未推送、未发布；双平台安装包与完整 OTA 验收仍未完成。

### 2026-10-16 17:23 Asia/Shanghai｜orulink-wugui｜范围澄清

用户确认本轮仅修改 SDK、桌面；不修改默认 Application 仓库。桌面端打包和本地安装包验收包含在本轮；安装包不包含默认 Application，保留 SDK 与必要运行资源。默认 Application 按普通下载应用处理，缺少业务合同返回未验证，不设白名单。打包验收与上传、对外发版分开记录。

### 2026-10-16 17:41 Asia/Shanghai｜orulink-wugui｜联调与打包

见 [验证记录](validation-2026-10-16.md)。已补业务组声明及桌面入口门禁、稳定错误码、代理流式限制和停止等待时间、更新前旧环境清理。完成第一轮 SDK-only macOS 测试包及挂载检查；最新代码第二轮打包/全量验证正在运行。代码未提交、未推送、未发布。


### 2026-10-16 18:34 Asia/Shanghai｜orulink-wugui / Codex

同步源码运行、构建、发布入口与文档，详见 [验证记录](validation-2026-10-16.md#1834-脚本与发布链路同步尚未正式发布)。本轮未提交、未上传；正式信任配置、平台锁及双平台/设备最终验收仍未完成。


### 2026-10-16 18:42 Asia/Shanghai｜orulink-wugui / Codex

最新打包及挂载检查已完成，见 [验证记录](validation-2026-10-16.md)。SDK 全量 2066 passed / 12 skipped；实际 wheel 更新/回滚 4 passed；Rust 160 passed / 6 ignored。桌面全脚本仍有 3 个已记录基线失败，不能标记整体验收通过。工作区源码总入口配套完成；未提交、未上传。


### 2026-10-16 19:11 Asia/Shanghai｜orulink-wugui / Codex

按用户澄清只做本地测试，不要求 SDK 发版本或配置生产密钥。补齐取消/中断下载清理、跨进程 zombie 接管判断及源码开发 wheel 缓存失效。SDK 全量 2076 passed / 14 skipped，真实 wheel 故障/更新/回滚 6 passed；最新测试 DMG 重建中，见验证记录。


### 2026-10-16 19:19 Asia/Shanghai｜orulink-wugui / Codex

本机 SDK/测试 DMG 验证收尾，结果与限制见 [验证记录](validation-2026-10-16.md)。SDK 全量 2076 passed / 14 skipped，真实更新/故障/回滚 6 passed；最新 DMG 已实际挂载验证并卸载。桌面全脚本仍有 3 项基线失败，Windows/设备/原生 OTA 安装未运行。本轮不发布 SDK，不配置生产密钥；无提交、推送或上传。


### 2026-10-16 19:49 Asia/Shanghai｜orulink-wugui / Codex｜本机补充验收收口

核心修改已完成；本轮实际设置页、原生三场景、活动互斥、清华下载、冻结 SDK 当前应用安装/卸载已验证。原先三项在干净桌面基线上完整复现；额外补跑联网 ignored 发现两项旧断言，均保留失败记录。完整结果、影响及剩余事项见 [本机最终验收报告](local-acceptance-final-2026-10-16.md)。更正：激活不是一律 force，同版本可复用、异版本接管。未提交、推送或发版，Windows/真实设备/原生 OTA 安装仍待专门环境验收。


### 2026-10-17｜历史五项修复与完整回归

详见 [历史失败修复与完整回归](historical-failures-regression-2026-10-17.md)。保留原发行哈希恢复 CRLF 字节，迁移已删除功能的测试边界，补 Git 字节保真和签名 mutation 反例。两个真实联网测试通过；Node 全量 702 passed/1 Windows-only skipped，SDK 全量 2076 passed/14 skipped，cargo test 通过。无提交、推送或发版；正式设备、Windows、原生 OTA 等验收缺口见报告。


2026-10-17 09:09 Asia/Shanghai｜收口：真实 wheel 六项全部通过（92.96 秒），五项目标修复和本轮完整回归完成；没有新失败。未提交、推送或发布。


### 2026-10-17｜联网夹具与新 DMG 本机验收收口

见 [可选联网夹具迁移与新 DMG 验收](live-fixtures-dmg-acceptance-2026-10-17.md)。两入口已迁移，新 DMG 挂载后六项 opt-in 全通过；Node 702 passed/1 Windows skipped，CN/Global Cargo 各 163 passed，Global 三项 live 通过。实际桌面原 30 秒限制复验 22.53 秒通过，同 PID 双桌面和退出清理通过。首次 30 秒超时及诊断仍保留，不宣称所有冷启动都稳定小于 30 秒。无提交、推送、上传或发版；平台/设备/正式线上与新机缺口详见报告。


### 2026-10-17 10:21:01 Asia/Shanghai｜orulink-wugui / Codex｜功能分支提交

- 用户明确要求上传对应功能分支；本次只提交、推送源代码及开发档案，不打 Tag、不发版、不合入。此前条目中的“未提交/推送”保留为当时状态。
- SDK 与桌面均使用 `codex/sdk-manager-ota-20261016`，分别基于任务开始时的 main `07e1b42` / dev `ec9979ce`；开发期间主分支新增提交，本轮不重写已验收基线。两仓须配套评审、部署。
- Workspace 同名功能分支提交源码启动脚本、说明及对应 gitlink；不包含 Server 原有 gitlink 或其他任务档案。
- 最近验收依据：[联网夹具与新 DMG 验收](live-fixtures-dmg-acceptance-2026-10-17.md)（Workspace 摘要请见两子仓同任务档案）。SDK 全量 2076 passed/14 skipped，另跑真实更新 6 passed；桌面 Node 702 passed/1 Windows skipped，CN/Global Rust 各 163 passed，挂载 DMG 六项 opt-in 通过。此提交仅整理档案，未再修改产品实现，不重复冒充新跑全量。
- 提交前差异/空白检查通过；疑似凭证扫描命中均人工核对为 getpass 调用、运行时变量赋值或 token 对象，无硬编码凭证。发行公钥配置为空，正式在线更新仍禁用；不能以分支上传代替正式发行验收。
- 两个用户原有 schema 文件排除提交，保留原字节；DMG、临时日志、缓存不纳入 Git；档案个人工作区路径统一替换为 `<workspace>`。Windows、真实设备、正式 OTA 及冷启动波动观察继续保留。
