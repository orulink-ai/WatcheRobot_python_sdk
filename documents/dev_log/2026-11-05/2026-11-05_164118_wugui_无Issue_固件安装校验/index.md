# 固件自动解析及安装校验

- 2026-11-05 Asia/Shanghai；作者 wugui；Codex 执行；无 Issue。
- 分支 codex/firmware-install-validation，基于最新 origin/main 0a5427e。
- 状态：进行中，未提交。目标与验收见根目录 implementation-plan.md。
- 配套 Desktop 独立功能分支同名，基于其 origin/dev。SDK 为解析和策略唯一实现，Desktop 负责 USB 与界面。
- 已阅读本仓规范/模板；历史 firmware-marketplace 档案与现有协议说明作为上下文。TDD证据后续追加。

## 实现与验证（2026-11-05，wugui / Codex）

- 两仓分别使用 origin/main（SDK 0a5427e）与 origin/dev（Desktop 1a4d740）的最新检出建立同名功能分支，原工作区既有修改保留。
- SDK 新增纯函数分区解析、schema v2 参考表、安装策略，自动准备 IDF 构建目录/ZIP；上传和提交均严格检查布局，未通过不接触上传平台。原 v1 读取保留，缺布局不能新发布/写入。
- 自动准备包使用固定 ZIP 条目、稳定排序和无压缩存储，消除时间戳、映射顺序及压缩器差异，支持同版本重试。
- Desktop 上传先执行 SDK inspect，成功才可发布；设置本地包与广场下载包共用设备只读预检、风险确认、物理锁、同一快照与写入前重检。广场额外保留发布摘要/来源核验。
- 不增加定时扫描、固定等待成功或后台自动重试；新步骤按操作结果推进，沿用已有进度查询机制。串口连接保留必要的有界失败处理。
- stdin/stdout/stderr 明确 UTF-8；Windows 长路径使用原生扩展表示，macOS 保持原生路径。中文、emoji、不同初始代码页、长路径均有回归。
- 新增本机下载包删除和作者下架申请：前者不更改设备，后者需账号所有权及目录审核，保留历史制品。按用户要求不增加恢复官方固件入口。

### TDD 与检查

- SDK 首轮分区测试：缺失模块后实现，出现 7 failed/4 passed；修正 v2 解析及分区终止符后通过。准备模块先因缺实现失败；本地缓存删除/作者下架先 2 failed 后通过；自定义分区测试先失败后通过。
- 可重现打包测试先因不同 SHA 失败，固定 ZIP 元数据及顺序后通过。长路径测试先在 worker 的 is_file 检查失败，规范化文件系统路径后通过；测试清理失败属于 Windows 路径环境问题，改为校验临时目录后用原生路径清理。
- 编码边界测试沿用此前真实 Red/Green；新增安装协议测试替换硬件写入，实际解析、任务和输出保留。设备探测纯 helper 先实现后补测，如实记为补测，不冒称所有用例均事前 TDD。
- SDK：110 passed、9 skipped；其中 8 个既有并发测试因 Windows 无符号链接权限跳过，另 1 为既有跳过。mypy firmware 12 源文件通过。
- Desktop：维护/安装 Python 35 项通过；Node 53 项通过（含三语、两入口确认/取消、缓存删除、SDK能力门禁）；tsc --noEmit 通过；cargo check 通过；Vite 生产构建通过，有既有大 chunk 提示。
- Windows PyInstaller 冻结 worker 构建完成；最终冻结管道及长中文路径 smoke 结果在下方追加。
- USB 实机只读检测成功：ESP32-S3、32 MiB，应用区实际从 0x20000 开始，参考用户原包写入 0x10000 的不匹配事实未改变。未向设备写入；未上传/下架线上固件。
- macOS 端口形式和 UTF-8 默认环境在自动化覆盖；当前宿主为 Windows，未执行 macOS 原生构建/真机烧录，不能宣称跨平台真机验收已完成。

## 交付状态与配套关系

- 本地源码、测试、文档已修改；未 commit、push、合入或发布，未改根仓 gitlink。
- 同名配套分支 codex/firmware-install-validation；正式打包要求 SDK capability firmware_installation_v2。
- 配套提交尚不存在，因此没有编造 SDK commit/source lock。用户授权提交后，应先提交并推送 SDK，再以真实可获取 SHA 更新 Desktop runtime-source-lock.json，构建两平台安装包。
- 已安装客户端未被替换。当前完成的是源码和主机测试，实际 UI 写入/断线恢复/macOS 硬件验收仍待兼容固件和相应宿主。

## 关联历史

- 补充并部分替代 [固件广场配套安装](../../2026-10-29/2026-10-29_170000_wugui_无Issue_固件广场配套安装/index.md) 的包校验/安装计划：保留原分发与 Daemon 边界，扩展自动解析及双入口设备布局核验；原文保留。
- [实施计划](../../../../implementation-plan.md)。

### 最终验证补充

配套 Desktop 的最终冻结 EXE 已完成真实管道 UTF-8/emoji/长中文路径验证；原始失败请求现被准确判定为缺参考分区布局，未连接 USB 或执行写入。SDK firmware 全套仍为 110 passed/9 skipped，12 源文件 mypy 通过；没有新增原生脚本入口。

## 2026-11-05：旧包布局状态提前返回

开发版 UI 重试旧包时发现布局核验过晚，导致先停止 Application 再被预检拒绝。cache_status 和 download 现返回 layout_verified/layout_error；仍允许获取旧包，但不会把下载完整误当作可安装。Desktop 使用实际状态禁用写入，运行时最终检查仍保留。

新增两条缓存布局状态测试先失败后通过；最终 firmware 回归 112 passed/9 skipped，operations mypy 通过。未修改旧包、未发布固件或目录。

## 2026-11-05 18:15:08 Asia/Shanghai｜wugui｜审查基线
用户授权分别保存功能分支并追加审查修复 commit。本次先提交此前功能实现和验证记录，审查发现的镜像完整性和路径边界问题将在后续独立修复提交处理，不发布或烧录。

## 2026-11-05｜wugui｜功能分支审查修复

功能基线已保存为 caa992b。用户明确要求审查并追加修复 commit，且国内 Gitee/国际 Hugging Face 上传入口均需检查。

- [P1，已修复] 固件只校验 24 字节头部，截断、长度伪造、坏 checksum/附加 SHA-256 在包哈希重新生成后仍可上传。新增镜像分段边界、XOR checksum 和可选 SHA-256 完整检查，应用与 bootloader 共用；四种损坏用例先失败后通过，并接受 esptool 自身编码器生成的独立镜像。
- [P1，已修复] 构建目录检查遍历到所选根之外，会误拒绝 macOS /var 等系统符号链接祖先。现仅拒绝根内引用的符号链接，并保留 resolve 后的边界检查。外部祖先模拟先失败后通过，内部链接仍拒绝。初版模拟正好落在被排除的 root.parent，不能证明问题，已修正为更外层祖先后确认真实失败。
- [P2，已修复] 下载缓存删除只检查文件本身，未检查 packages 目录的链接。现提前拒绝链接目录；测试证明拒绝后原文件保留。
- 国内外上传：新增两 provider × 两类坏包的真实 publish 入口测试，验证校验失败不会认证/联网；既有 HF 与 Gitee 正常上传、重试和冲突用例全部回归。作者下架所有权与审核 API 契约也分别覆盖两 provider。

验证：SDK firmware 全套 125 passed / 9 skipped；8 项因 Windows 无符号链接权限跳过，另 1 为既有跳过。mypy firmware 12 个源码通过；black 与 diff 检查完成。未执行真实上传/下架、硬件写入或 macOS 原生验收。

配套 Desktop 基线为 191ad645，独立修复提交会同步真实 SDK 来源锁。当前提交/测试在本地完成，不推送、不合入、不发布，不改变 meta repo gitlink。



## 2026-11-05｜orulink-wugui / Codex｜macOS 分支合入前审查修复

- 延续同一任务审查，针对 SDK `origin/main...codex/firmware-install-validation` 检查包解析、分区/镜像验证、两端发布、缓存删除及路径边界；未改变 Daemon 路由。
- [P2，已修复] IDF 构建 ZIP 包含 `flash_args.txt` 并按现有规则提供可选 `firmware.json` 展示元数据时，原实现仅凭两文件存在就误判为预制安装包。相同构建目录可准备，ZIP 却报 `Invalid firmware manifest fields`，阻断有效固件上传预检。
- 现对有界 JSON 元数据检查包格式标记（`schema_version` / `segments`），普通展示元数据继续走构建准备；含包格式标记的成品包始终保留严格校验，不以重新打包修复坏 manifest。
- TDD Red：新增 JSON flash args / 纯文本 flash args 两种真实构建 ZIP 回归，修复前均失败于 `Invalid firmware manifest fields`；Green：修复后两条通过，目录与 ZIP 的内容 SHA 相同。另补损坏成品 manifest 不回退为 build 的防回退用例。
- macOS Python 3.12：`python -m pytest -o addopts='' tests/distribution/test_firmware_*.py -q`，136 passed、1 skipped（Windows 专用路径用例）；2 项依赖弃用 warning。第一次完整回归因磁盘空间耗尽失败，释放可重建构建缓存后重跑通过，不把环境失败计作功能通过。
- 默认 Python 3.10 目标 mypy 受本机 NumPy stub 的 Python 3.12 `type` 语法阻塞（`numpy/__init__.pyi:737`）；原增量缓存一度 INTERNAL ERROR，关闭增量后得到明确依赖语法错误。该限制不能描述为项目 3.10 类型检查通过。另以 `mypy --no-incremental --python-version 3.12 src/watcherobot/firmware` 检查当前宿主，12 个源码全部通过。
- 未新增系统脚本入口；未进行真实账号上传、线上下架或设备写入；未 commit、push、合入，由主审查者统一检查提交。


## 2026-11-05｜orulink-wugui / Codex｜PR #156 外部审查：收窄应用更新启动策略

- 外部自动审查指出 P1：相同分区布局不能证明设备当前/下次启动哪个 OTA 槽；原 application 模式接受只写 OTA 槽或 factory 的包，可能报告完成但继续运行其他槽。独立复核确认原实现仅比较布局，没有 OTA 状态或槽切换计划。
- 本轮明确不实现不完整 OTA 策略。`inspect_installation` 和 `plan_installation` 共用边界：application 模式必须恰好一个 type=app/subtype=factory，且没有 type=data/subtype=ota。含任何 OTA 槽、test/其他应用、多个 factory 应用或 OTA 选择数据均拒绝；单 factory 与普通 NVS/custom data 组合保留，检查与标签名称无关。设备实际布局仍须与包内布局一致。
- TDD Red：7 种布局 × 包检查/计划检查共14例先出现 DID NOT RAISE，包括写 ota_0、写 ota_1、写 factory 但保留 OTA 槽、保留选择数据、ota_15、test、多 factory；1条合法非OTA数据布局通过。Green：共享策略生效后 installation 32项通过。计划入口另核验已构造 Installation，避免旧解析结果绕过新边界。
- 正式文档已更新支持边界及 ESP-IDF 官方 type/subtype 来源；SDK 不打开 USB、不改变业务帧路由、不写 OTA 数据。
- bootloader 与分区表编译地址关联问题独立处理，本轮未把限制应用更新冒称完整包问题已解决。仅限制表地址为默认值仍不足以证明 bootloader 编译配置，等待明确可信配置/支持边界决策。
- 本轮未进行设备写入、线上发布、commit 或 push；由主审查者统一检查提交。

- 同轮主审查者修复构建元数据对象类型：`extra_esptool_args` / `flash_settings` / `flash_files` 的数组输入先3例失败，再于 `.get` / `.values` 前检查 dict，返回包含字段名的 `FirmwareError`；该项与本轮 OTA 修复统一提交。
- 本轮最终验证：`pytest -o addopts='' tests/distribution/test_firmware_*.py -q` 为154 passed、1 skipped（Windows 专用）、2项依赖弃用 warning；`mypy --no-incremental --python-version 3.12 src/watcherobot/firmware` 12源码通过；`git diff --check` 通过。bootloader/表地址关联 P1 仍为未解决合入阻断，不能据这些通过结果声称分支可合入。

- 主审最终重跑 Distribution 全套：618 通过、4 跳过；Python 3.12 firmware mypy 12 源码通过。当前只保存已验证的 OTA 与元数据修复，不将剩余 bootloader 风险带入目标分支。
