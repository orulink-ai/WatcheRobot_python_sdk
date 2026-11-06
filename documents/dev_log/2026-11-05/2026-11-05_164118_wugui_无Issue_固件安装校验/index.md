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


## 2026-11-05｜orulink-wugui / Codex｜用户澄清职责后更正启动语义审查

- 用户明确职责：作者对固件构建/bootloader 配套和启动语义负责；桌面按作者声明执行 USB 写入，成功只表示写入与校验完成，不是目标固件运行证明。对照代码确认 manifest 与 flash_args 的地址/路径逐项一致性、镜像结构与摘要、分区/容量/擦除边界均已有客观检查；UI 区分写入记录和当前运行身份，不自动选择或启动 OTA 槽。
- 更正上一节“收窄应用更新启动策略”和“bootloader关联为合入P1阻断”的结论：此前将未承诺的任意固件启动保证提升为工具职责，超出了用户澄清后的边界。该历史审查及其当时真实 Red/Green 证据保留，当前策略以本节及正式文档为准，不声称旧判断从未发生。
- 删除本轮新增的 single-factory 强制限制，允许相同实际/参考布局中的 factory、OTA、test、多应用声明分段原样 USB 写入；不切槽、不改写选择状态、不替作者补包。application 模式禁止数据/bootloader 写入、布局必须一致、分区表写入归完整包等已有检查保持。
- TDD Red：按澄清后的合同重写7种布局的包/计划用例共14项，先被此前 single-factory 限制拒绝；Green：移除限制后14项通过，并断言分段地址/路径保持声明、布局/包字节保持不变。另补3项非法非应用写入拒绝和1项自定义表地址保留回归；installation 36项通过。
- 将“requires a matching bootloader”改为“requires a bootloader image at address zero”，准确表述已执行的存在性检查；不冒称证明二进制编译配置关联。没有添加可信摘要白名单、编译证明、自动OTA策略或固件变更。
- 此更正不免除可核验结构错误的拒绝义务，也不宣称真实启动已验证。真实写入、启动和业务行为仍须如实记录，不能用主机测试替代。

- 更正后最终验证：firmware 全量158 passed、1 skipped（Windows 专用）、2项依赖弃用 warning；3.12 mypy 检查12源码通过，diff检查通过。bootloader编译语义及OTA槽运行选择依用户明确职责不再作为本工具合入阻断，不能据此声称真实固件运行成功；本次未commit或push。

- 主审扩大回归首轮为2403通过、9跳过，唯一失败是以含 `../` 的相对解释器路径启动导致既有 launcher 路径精确比较不一致；以规范绝对解释器路径重跑该项通过，未改业务路由或测试断言。随后用绝对路径重跑全套，不把首轮失败伪称为全绿。


## 2026-11-05 19:52 Asia/Shanghai｜orulink-wugui / Codex｜本机完整开发 CI 与兼容性验证

- 测试源码基线：`6ff7b55c8adf2d28ff1ab28e3013c81abdf2d439`；按 `.github/workflows/sdk-ci.yml` 的 development 和 python-compatibility 作业，在 macOS 26.3 arm64 执行本机可执行步骤。三个 Python 各用干净隔离环境，Node 22.21.1；全量 pytest 串行运行，避免固定端口干扰。依赖、解释器和产物均置于临时目录，不提交缓存或个人路径。
- 原有全量 2404 项通过结论本轮重新执行，未只复用历史日志：

| Python | pip check | 全 SDK pytest | expression lab |
|---|---|---|---|
| 3.10.19 | 通过 | 2404 passed / 9 skipped，265.78s | 30 passed，0.97s |
| 3.11.18 | 通过 | 2404 passed / 9 skipped，247.26s | 30 passed，1.09s |
| 3.12.13 | 通过 | 2404 passed / 9 skipped，254.45s | 30 passed，1.03s |

- 3.11 开发步骤：`python -m mypy src/watcherobot` 默认配置通过，140 个源码文件，无 `--python-version` 覆盖，目标仍为 `3.10`；`node --test tests/js/*.mjs` 225 passed；`BleakBackend` 导入通过，`pytest tests/provisioning` 75 passed。干净环境没有无关 NumPy；因此本节补齐并替代此前“最低版本 mypy 受本机 NumPy stub 阻塞”的当前验证状态，原历史事实保留。
- `python -m build` 生成 0.1.13 sdist/wheel，`twine check` 两项通过。独立 wheel 环境安装及 pip check、版本导入、`watcherobot --help` / `demo --help` 通过；`python -I` 成功准备 `sdk-test-bench` 和 `expression-lab` 两个内置 demo。另在源码目录外确认 `firmware.installation` / `firmware.preparation` 来自独立安装的 site-packages，`watcher-distribution firmware --help` 通过。产物未发布。
- 机器证据：[local-ci-result.json](local-ci-result.json)，保存版本、命令、计数、状态及构建摘要；完整临时日志和个人绝对路径不进入档案。所有命令退出码均为 0；只有既有依赖弃用提示及重复 ZIP 条目的预期 warning，无测试失败。
- 本轮仅验证和追加档案，没有修改业务代码、测试或 CI，因此不适用新增代码的 Red/Green；以三个实际解释器的完整检查及独立安装 smoke 为替代验证。没有为了通过检查放宽断言、覆盖最低类型目标或改变固件职责边界。
- 平台边界：未执行 Linux 自托管 runner 的清理/源码下载/setup 基础设施；macOS 上保留 9 项平台跳过，不冒称 Windows 原生验证。当前不是 release 标签流程，未运行 release-only 的 lowest/latest 依赖矩阵。未执行实机写入、目标固件启动、真实 BLE、线上上传或发布。
- 交付状态：新增本节及机器结果，本轮未 commit、push、合入，由主审查者统一检查提交；用户要求仅更新分支与中文 PR，不合并。


## 2026-11-06 Asia/Shanghai｜wugui / Codex｜PR #156 再次审查（进行中）

用户要求在 macOS CI 后继续审查并修复、更新中文 PR，保持不合并。基线10bf0e9；沿用同一任务档案。检查中文项目名称、双平台上传和自定义布局等客观校验边界；仅对实际复现问题追加修复和测试，不扩大启动保证。

### 审查修复与 TDD 证据

- P2 中文项目名：ASCII ID 推导发生在展示 ID 覆盖前，纯中文名会清空；未配置/显式配置 ID 两条测试先报 Missing project name。现非空 Unicode 名采用稳定 SHA-256 摘要生成缺省 ID，保留显示名与合法覆盖；目录、ZIP、重复准备保持同一摘要，不同中文名不会都落入同一固定 ID。准备模块23项通过。
- P2 自定义表前分区：原先将所有位于 table_offset+4096 前的分区一律拒绝。有效 nvs@0x8000、table@0xA000 的用例先失败；改为真实表扇区与 bootloader 向上取整擦除范围的交集检查。合法前置分区通过，跨表/位于表内/与 bootloader 重叠仍拒绝。不移动任何作者地址，也不认证启动语义。
- P2 Windows 长缓存路径：真实 execute(inspect) 在进入 prepare_package 前 mkdir 失败（WinError3）；新入口回归先真实失败。现创建前规范化扩展路径，同时所有操作复用该路径。回归发现普通/扩展路径的目录状态摘要不同，新增身份一致性测试先失败后修复：仅在身份摘要时去掉 Windows 扩展别名前缀，保留旧目录锁、跨进程 veto 及 UNC 语义。已有故障注入测试的文件路径比较同步到真实 I/O 表示，未删除或放宽撤销保护断言。首次广回归6失败来自身份差异和测试替身路径比较，修复后重跑通过。
- Windows Python3.12.13：firmware 全套158 passed/9 skipped；Distribution 全套623 passed/11 skipped；mypy firmware12源码通过，diff空白检查通过。跳过保留Windows无符号链接权限/POSIX专用条件，未修改skip门禁。8条新增回归（2中文、4布局、2Windows路径）包含真实Red/Green证据；macOS新增用例尚未本轮原生执行，历史macOS三版本CI为10bf0e9的基线证据。
- 配套 Desktop #190 原代码复审无新的独立缺陷，组合Python41通过（另10subtests）、Node82通过；后续桌面来源锁将跟随本提交。未执行线上上传/下架或USB实机写入，不合并两个PR，不改meta gitlink。


## 2026-11-06 Asia/Shanghai｜wugui / Codex｜真实发布包烧录验收发现的准备问题

- 用户授权对已连接设备实际测试。下载官方v0.5.9完整ZIP，SHA256与GitHub发布digest一致（e2af6033a677588a967138ae1b1e83fc568fd58d59e78fcaf16d33fae19a0e70）；不执行包内tools或skill。原桌宠0.3.2缺少参考布局仍不能作为有效样本。当前设备原为v0.5.12，没有本机同版本完整包，测试前已明确告知使用已发布v0.5.9。
- 真实ZIP包含外层版本目录，SDK准备直接失败于Missing build file，设备未写入。两种flash参数形式的包装目录测试先失败；现有界安全解包后寻找唯一生成参数父目录，多根明确拒绝，不改变作者地址。
- 独立复审发现新增递归选择可能忽略ZIP根损坏成品marker（缺flash_args）而重建子目录；新回归先DID NOT RAISE，现根firmware.json只要有成品marker即走严格检查，不依赖flash_args存在。包装目录内成品marker也拒绝重解释。新增共6例：2条包装构建正向、独立根歧义提示、根成品marker防回退、嵌套bootloader元数据不误判共5例先失败后通过；包装目录成品marker拒绝为补充覆盖，未冒称全部先Red。
- 官方ZIP成功自动准备为完整包，保留6个声明分段及v0.5.9版本；准备包SHA256=30725978188bc4d8c1ebbc3adc7b3fac37fb233f4297c59ae7e47318410767ee。Desktop共享worker真实预检成功并进入写入，结果由配套桌面档案记录。

- 进一步复核：IDF可能附带bootloader子构建元数据，选择独立最外层构建根，避免把嵌套元数据当第二个独立候选。最终Windows Distribution629 passed/11 skipped；固件mypy12源码通过、diff检查通过。实际官方ZIP经最终准备器重新准备摘要保持307259...67ee（由后续执行验证），无需重复写设备。真实Desktop worker烧录成功，重启后实时查询v0.5.9，配套Desktop档案保存脱敏结果；本轮未进行macOS原生或GUI点击验收。

## 2026-11-06 10:31:36 Asia/Shanghai｜Codex｜SDK 原始发布 ZIP 上传入口复验

- 在 SDK `2218076` 基线上确认 `inspect` 可解析外层目录，但直接 `publish` 原先先取凭证，再把输入交给成品包校验。两平台的正常/错址参数化回归共四条先失败。
- `publish` 现先复用 `prepare_package`，在本次请求的临时目录生成并校验标准包，通过后才读取凭证。平台上传与匿名回读沿用原验证，回读失败不落发布凭据。没有写入设备或改变作者声明的分区地址。
- 通过真实 `firmware execute --action publish` CLI 与两平台上传适配器，用测试凭证、HF API 和 Gitee HTTP 替身执行官方 v0.5.9 原始 ZIP。六段地址为 0x0、0x8000、0xf000、0x20000、0xc20000、0xd20000，生成包 SHA-256 为 30725978188bc4d8c1ebbc3adc7b3fac37fb233f4297c59ae7e47318410767ee，与已烧录的包一致。两平台模拟匿名回读与摘要核对均通过。
- 将副本中的应用地址从 0x20000 改为 0x21000，两平台均返回分区布局不匹配，凭证读取与平台调用均为零。未向真实平台发布。
- macOS Distribution 回归 638 passed, 6 skipped；默认配置 mypy 140 个源文件通过。变更及记录留在本机。


## 2026-11-06 Asia/Shanghai｜wugui / Codex｜SDK 直接上传接口与前端请求联合验收

- 用户要求直接将官方v0.5.9构建ZIP传SDK上传接口、非法分段上传前拦截，并验证设置及广场两个前端入口；后续明确不要控制电脑，改用接口测试。停止开发版启动会话，此轮不进行点击、输入或窗口控制。
- 发现公开FirmwarePlatform.publish只接受准备包，依赖UI先inspect。两provider直接传包装构建ZIP的正向测试先报Missing firmware metadata；现该接口在任何平台调用前冻结ZIP、prepare并使用准备后固定文件上传，目录同样支持。operations.execute publish在读取凭据前prepare，两条非法镜像测试先因访问credential而失败，修复后提前拒绝。共新增10例（4例真实Red/Green，其余非法分段断言补强具体原因，不能把原先因缺manifest拒绝误当布局检查覆盖）。
- 正向真实官方ZIP分别调用两provider实际publish适配器，边界用Hf/httpx替身，确实生成同一准备包SHA256=307259...67ee、local.watcherobot-s3、v0.5.9及6个原地址。无SDK登录凭据，不宣称真实平台已上传；用户已收到凭据缺失说明，不在聊天索取token。
- 使用真实官方ZIP改址为0x30000和制造table@0x1000重叠，两个公开接口×两provider均在credential/network前分别报Image write address does not match partition layout与Overlapping flash erase sectors；平台回调设置为触发即失败。首次改为0x10000会先与otadata实际范围重叠，正确收到overlap，因此调整不重叠的0x30000用例单独检验布局门禁，未改校验逻辑。
- 广回归Windows Python3.12：Distribution639 passed/11 skipped，固件mypy12源码通过。匿名公开可访问证明旧测试的unused.zip改成真实合法包，保留404、不能借本地缓存证明公开可访问及不保存receipt的原断言。
- 独立只读审查71通过/9跳过，无新增实际缺陷；原追加发布、版本冲突、匿名重新下载和snapshot边界保留。配套Desktop新增实际handler+API串联测试及真实接口请求验收，详见Desktop同任务档案。

- [实际官方ZIP上传接口证据](upload-interface-validation.json)保存两provider结果、6段大小/地址、坏包具体原因及网络替身边界，不包含token或设备标识。配套Desktop设置请求链已实际写入并实时查询v0.5.9，广场请求链还在本轮验收中；不提前宣称其完成。

### 推送前整合并行远端修复

- 推送被正常拒绝，发现远端73da181已追加同一execute publish准备顺序修复及macOS Distribution638通过/6跳过、默认mypy140源码证据。没有强推或覆盖远端提交；将本地未推送提交rebase到其上。
- operations保留远端的请求专用临时准备目录方案；公开FirmwarePlatform.publish的ZIP快照/自动准备、直接适配器正负向测试保留本轮实现。两边新增测试、历史档案均保留，不覆盖历史正文。
- 冲突整合后Windows Distribution643 passed/11 skipped，固件mypy12源码通过、diff检查通过。本轮新公开适配器修复未冒称在macOS重新跑过。上传接口证据更新最终广回归计数；Desktop两条接口桥均已完成实际写入及实时版本查询，配套桌面将追加同步最终SDK来源。


## 2026-11-06 Asia/Shanghai｜wugui / Codex｜用户授权正常合并后的最终审查修复

- 用户明确要求普通合并两个PR，先检查最新head、CI及审查意见，不使用admin绕过。最新自动审查经源码与独立测试复核，确认目录firmware.json成品marker会被忽略重建；stat后无界read可被文件增长绕过。
- TDD四例先失败：目录schema_version/segments两例DID NOT RAISE；镜像增长在1024字节预算下仍被接受；metadata增长直到成品检查才以generic错误拒绝。修复后构建目录早期一次读取展示metadata并拒绝成品marker，后续展示覆盖复用该snapshot；统一实际字节有界读取用于JSON、flash args、累计镜像及准备成品ZIP复制，保留现有容量限额。
- 清理并行整合残留的重复package导入与无用upload.zip生成；原publication公开匿名回读失败断言不变。
- Windows Distribution647 passed/11 skipped，准备模块33通过，固件mypy12源码通过，diff检查通过。真实官方v0.5.9 ZIP重准备SHA256仍307259...67ee。独立只读复审79通过/9跳过，没有新增实际问题。未重复写设备，不改SDK/Daemon职责。
- 当前尚未合并，远端CI仍待最终提交结果；后续桌面来源锁跟随本次修复，不提前声称合并完成。

- 同轮默认配置`python -m mypy src/watcherobot`重新执行通过，140源码，无版本覆盖；不只依赖固件模块的follow-imports检查。


### 最终合法数据分段识别修复

- 最新自动审查指出AA50仅靠魔数判断，会将普通数据误当第二表。目录与包装ZIP真实正向两例先失败；现构建准备及legacy完整包扫描均将魔数只作初筛，候选必须通过parse_partitions结构/MD5/容量验证。合法数据保持原字节和地址；两张真表、缺有效表仍拒绝。
- 新增6条回归，2条普通数据先失败后通过，并覆盖legacy v1正常识别；4条双真表/坏表保持拒绝。独立复审79通过，没有新增缺陷。
- 前一19f700d源代码集合本机全SDK2418 passed/27 skipped（241.17s），并非把新增6例算入此前计数。此补修后Distribution653 passed/11 skipped、默认mypy140源码通过、diff检查通过。真实平台、GUI及设备不重复操作。
- 远端Linux任务仍处于依赖安装/兼容检查大步骤，未产生最终测试结论；普通合并授权已明确，不允许使用admin或自动合并绕过。后续状态以实际GitHub结果记录。
