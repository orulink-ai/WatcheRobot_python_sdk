# 桌宠正式 Application：USB 传画与实体输入

状态：候选实现与自动化验证已完成，交互延迟优化未验收；SDK 尚未正式发布，App 尚未提交应用广场审核。

用户确认正式应用路线。保留现有 USB 演示；不改 M5、STM32、Himax；设备升级前重新备份当前应用并验证分区与恢复镜像，仅写 ESP32 application。

## 边界和合同

- App 运行于电脑，ApplicationContext.from_environment，经 watcherobot app run 托管。
- Daemon 只增加通用 USB 文本/二进制帧连接，不解析桌宠或显示业务。RawFrameRouter 保持原样。
- SDK display.open_stream / present / close 管理单个显示会话，固件声明 display.stream.v1 后可用。
- 显示使用 WSPK type 8（现有 type 1-7 均保留）；一次仅一帧在途，按 stream_id 和 sequence 等待真正呈现结果。
- USB WRU1: little-endian <4sBBHIII>, 20-byte header; kind text=1,binary=2,ACK=3; FIRST=1,LAST=2; 1024-byte fragment; maximum logical payload 131072; CRC32 per fragment. Host waits fragment ACK; device sends text fragments without waiting ACK. No business parsing in transport.
- 输入保留单击屏幕、背部短触、滚轮转动及短按；事件带呈现序列，拒绝陈旧菜单选择。
- 分支基于 origin/main 0a5427e，工作树位于 workspace 路径规范要求的 .codex/local/worktrees。原 checkout 不切换、不修改。原生工具不能指定该路径，使用 git worktree 创建。

## 实施顺序

1. TDD 实现 SDK 显示会话与通用 USB transport（两个不重叠模块并行）。
2. 接入 Daemon 管理 API / CLI、Robot 显示域及带上下文的实体输入，验证透明路由回归。
3. 配套固件实现同一传输和 SDK 显示/输入合同；本地 native 协议测试、IDF 构建。
4. 正式 App 迁移原桌宠取画、固定构图和菜单；资源不含角色模型、凭据、备份和日志。
5. 当前设备备份/恢复核验、app-only 升级，经实际 Daemon+App 验证持续播放和菜单交互。
6. 严格检查发布文件集合与依赖可安装性；SDK 未正式分发前不得宣称兼容公开版本或已上架。准备可审阅的 SDK PR 和 App 发布包，再按真实前置条件发布投稿。

## 实验记录

- 已确认本机旧 Daemon 未运行；当前 USB 演示独立运行，不占用 SDK 服务。
- 已确认公开 SDK 0.1.13 缺任意 JPEG 帧显示 API；不能仅加 app.json 宣称正式 App。
- 首次 prepare 的 ensurepip 在沙盒失败，未继续使用失败目录；隔离临时目录后在新项目目录重试。


## 2026-11-03 17:06，Ciel：实现和验证

SDK 新增 display.open_stream / present / close、通用 WRU1 USB transport、USB 管理 REST/CLI，以及可选的输入显示上下文。显示业务保留在 SDK/App/固件，RawFrameRouter 未修改。USB 默认打开前关闭 DTR/RTS；物理验证确认默认握手线会使本次设备复位，修正后稳定收发。

审查发现并修复 USB 专属断开误用通用断开入口、旧连接 offline 通知与新连接竞争两个 P2 问题。USB 断开现在不影响 LAN 会话或绑定；占用保留到旧通知完成。

验证证据：
- 最终聚焦 SDK 测试：223 passed, 1 skipped, 26 subtests passed，含显示、USB、输入、Runtime、REST、CLI 及透明路由。
- 全量回归：2486 passed, 11 skipped；一个子进程导入错误发生在并发重装 SDK 时段。安装结束后该参数化测试两个分支均通过。未把这次全量运行写成全绿。
- mypy：140 个源文件通过；git diff --check 通过。
- 独立 App 最新测试：166 passed，包含固定构图、菜单呈现、桥接安装/恢复、校准、输入映射及点击后丢弃旧预取帧。
- 固件原生 C 协议测试 /W4 /WX 通过，ESP-IDF 6.0.2 构建通过。
- 设备身份、分区、当前镜像备份及读回摘要已核对。仅烧录 application，未写 bootloader/partition/NVS/STM32/Himax。私有备份和硬件身份不提交。
- 实际受管 App 经 Daemon USB 连续呈现数千帧，约 5.2–5.4 fps；用户确认屏幕单击、背部菜单、滚轮选择/确认和退出菜单恢复大小均正常，同时指出反馈延迟。已去除菜单等待无关 renderer capture 的路径，并加入分段耗时；现有 USB 吞吐仍是限制，未宣称延迟已验收。

App 源码发布集合由实际发布收集器单独检查；不含虚拟环境、设备备份、NVS、凭证、日志、角色素材和本机校准配置。硬件 SDK 作为固定公开提交的外部构建依赖，不把本地 vendor 或固件二进制发布至 App 源码仓库。

交付边界：本分支使用 0.1.14.dev20261103 标记本地构建。公开 0.1.13 不支持显示 API，不能放宽 App 清单伪称兼容。SDK PR 合入与正式版本发布、配套硬件兼容性发行是后续上线前提；源码快照发布不代表应用广场审核通过。


## 2026-11-03 17:12，Ciel：交互延迟优化

App 本地菜单呈现不再等待 Electron capture；点击后丢弃旧预取帧，直接获取点击后的画面。菜单无变化时每 10 ms 检查输入，最多 500 ms 重发一帧以维持显示租约和可选择帧的新鲜度；滚轮或原菜单状态变化会立即生成新帧。158 项 App 测试通过，已重新开启受管实机运行。用户对本轮延迟改善的实屏评价待反馈，不能以单元测试代替验收。

## 2026-11-03，Ciel：Draft PR 前状态校正

本条更新上节的待反馈结论：用户明确反馈第一轮延迟优化没有改善，交互延迟未验收。当前独立 App 自动化测试已扩展至 166 项并通过；测试通过不代表交互延迟已改善。后续固件 UART 调整仍在构建和验证中，不在本次 SDK 提交中，也不记录为已通过或已验收。

本次仅准备基于 main 的 SDK 功能分支提交与 Draft PR。开发版本为 `0.1.14.dev20261103`，不是公开正式发行版。SDK 合入、发布和应用广场投稿均未由本次操作执行；App 源码发布由独立任务管理。提交集合只包含 SDK 源码、测试、显示协议文档和本档案，不包含本机路径配置、硬件身份、备份、运行日志或角色素材。

提交前验证沿用同一候选实现的已完成结果：SDK 聚焦测试 223 passed、1 skipped、26 subtests passed，mypy 140 个源文件通过。全量测试记录为 2486 passed、11 skipped，另有一个并发重装期间的子进程导入错误；重装结束后该用例的两个参数分支均通过，不能将原全量运行记为无失败。显示模块使用 `FRAME_DISPLAY` 常量后的 91 项测试通过。


## 2026-11-04，Ciel：发行前异常路径审查

用户明确授权 PR #155 和版本 PR 在检查通过后合入，按现有流程发行 SDK 0.1.14，再更新 App 的正式依赖并继续关联审核。该授权不取消现有发布门禁，也不包含自行批准固件目录收录。

本轮在同一功能分支上维护代码与档案。PR #155 已转 Ready；原候选 ea4df10 的 Linux 开发及 Python 3.10/3.12 兼容检查成功。Ready 自动审查报告提出三处异常路径问题；检查源码后确认成立，未因工作流结论 success 而忽略报告中的不建议合并意见。报告链接：https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/155#issuecomment-6620078580。

- 显示流迟到 ACK：总超时已经过后仍取得有效流 ID，但调用者没有句柄，无法公开清理。新增 `DisplayDomain.close_stream(timeout=...)`，可有界等待进行中的打开、清理自己持有的流；关闭失败保留所有权可重试，成功后仍可重新打开；无有效所有权不猜测 ID。4 个新增回归先全部失败，修复后显示测试 95 项通过。
- Daemon 收尾：普通 USB 通知错误会提前退出 stop，跳过后续服务。各组件保持原顺序尝试停止，普通失败记录并在完成后抛回首个错误；取消仍立即传播，USB 清理及槽所有权不被过早释放。新增回归先 8 failed/2 passed，修复后 10 passed，与已有 USB Runtime 合计 26 passed。
- USB 接收：读线程逐事件提交事件循环回调，绕过下游 128 帧队列的容量限制；需要将交接缓冲及唤醒数量同时限制，保留 ACK 的及时性和透明业务路由。7 个新增过载/关闭回归已在修改前失败，具体修复与集成验证状态见本节后续追加。

稳定版的实机要求仍按 `docs/releasing.md` 与 `docs/hardware-testing.md` 执行。当前桌宠专用固件 0.3.2 的 USB 画面和输入有真实验收，App 173 项测试通过；这些证据不能代替常规固件的配对、行为、灯光、Job、相机、麦克风和 RTC 实时视频验收。本轮未更换固件、未操作 M5，未发布正式 SDK 或宣称应用上架。

原开发阶段档案与上次 Windows 基线失败记录保留，不将历史结果挪作本候选的全量验证。待集成检查、完整差异复核和最新 CI 后再更新实际合入状态。


### 集成复核与第四处竞争修复

2026-11-04，Ciel。USB 交接修复后的 7 个新增过载回归通过，USB 完整聚焦为 38 passed、6 subtests passed；帧数与 Python payload 存储量在读线程处受限，活动回调仍计费，ACK 使用匹配单槽，事件循环唤醒合并。Daemon 停止流程和迟到 open ACK 的公开清理入口也已集成。

完整独立复核发现第四处竞争：旧 close 已通过本地检查，但尚未送入 transport 时观察到离线；新流重用了相同 ID，旧请求随后可能误关新流。先用真实 Display 类确定性复现，再用真实 Robot 和两种实际 transport 写 4 个回归，覆盖提交前、协程已提交但尚未发送两个窗口。RED 为 4 failed；修复将 ownership guard 放在 transport loop 的命令 admission 处，等待 ACK 不持显示条件锁。4 项转 GREEN，组合回归 112 passed。文档明确已 admission 或在途字节无法撤回，未作超出本地保证的承诺。

最终新候选本地聚焦为 265 passed、26 subtests passed；mypy 为 140 个源码文件通过。最新完整 Windows 回归为 2525 passed、3 failed、11 skipped、26 subtests passed；三个失败均为此前隔离基线已复现的 Pi voice timeout，没有新增失败，也没有继续扩展 Pi 诊断。完整套件不标记全绿。独立复核覆盖整个功能差异及新增修复后，没有剩余 Critical/Important；另独立执行显示与两种 transport 回归 113 passed。

本地证据存于父任务工作区的 `.codex-artifacts/pet-release-20261104/`：`focused.log`、`mypy-final.log`、`full-final.log` 与 JUnit、`reconnect-red.log`、`reconnect-green.log`、`full-review.md`、`sdk-final-local-evidence.json`。源码指纹与命令记录均保留。本提交后仍需核对新 HEAD 的远端 CI 和自动审查报告；旧 HEAD 的成功不能代替。常规固件实机门禁、固件目录审核保持待完成，未打 tag、未上传 SDK 或提交 App 审核。


### 打开与呈现的发送窗口补齐

2026-11-04，Ciel。提交 c408c61 后的新自动审查 [R1](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/155#issuecomment-6620551919) 指出打开流仍使用普通命令，已观察离线后尚未发送的旧 open 可能在新设备分配无归属的流。独立复核确认成立，并按同一边界核对呈现：发送线程在调用真实 Future.cancel 前被调度暂停时，已提交但未执行的旧 JPEG 也可能发给复用 ID 的新流。两项都不是已经 admission 的在途字节问题。

先用真实 Robot 与两个实际 transport 补 10 个确定性回归：打开的提交前/提交后窗口 × 离线/显示域关闭 × 两种 transport 为 8 项；呈现的真实 Future.cancel 调度边界为 2 项。修复前 10 项均在旧命令或旧画面真正到达内存 socket 时失败，修复后 10 passed。打开改用已有 guarded command，核对 reservation、generation、domain 和原截止时间；呈现给既有 send_device 增加可选 keyword-only admission guard，普通发送不变，guard 在 transport loop 进入既有发送前执行。关闭域仍允许已归属流的清理，未持条件锁等待传输 ACK。

本次聚焦含 Desktop session 为 276 passed、26 subtests passed；mypy 140 source files 通过。完整 Windows 回归另存 `full-admission.log`，不覆盖上候选 c408c61 的 2525 passed/3 个既有 Pi timeout 结果；提交前以对应日志核对最终完成状态。已取消 c408c61 的旧 CI，后续以新提交检查为准。完整差异独立复核及新 HEAD 远端检查的结论分别记录，不能用工作流 success 代替报告内容。

本次仍未发行 SDK，未改变实机固件或当前 App；常规固件稳定版实机门禁和固件审核 #58 没有豁免。


本次发送窗口补齐后的完整 Windows 回归结束：2538 passed、11 skipped、26 subtests passed，无失败。本轮进程显式启用 PYTHONUTF8=1，先前的三个 Windows Pi timeout 也通过；未修改 Pi 源码、未继续扩展诊断，不据此单独断言旧失败的根因。旧候选失败记录保留。独立复核最终窄差异无 Critical/Important，另行回归 123 passed，源码指纹前后一致；命令和指纹见 `full-review.md`、`admission-review-evidence.json`、`sdk-admission-local-evidence.json`。提交后等待新 HEAD 的远端检查；尚未合入或发行。


### USB 回调关闭与连接通知的事务边界

2026-11-04，Ciel。提交 8bb0528 后的自动审查 [报告](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/155#issuecomment-6620806314) 提出两个真实生命周期问题，已独立确认并分别按 TDD 修复：

- 外部关闭取消并等待 dispatcher，而 dispatcher 的 on_frame 正在 send()，send 的异常清理反过来等待同一个 close task，形成循环等待。新增四个 ACK 等待、受控串口 write、关闭失败、通知失败回归，原实现四项都在有限关闭断言失败；修复后四项通过。仅在外部关闭拥有收集工作时让被收集的 dispatcher 直接展开；普通发送和 dispatcher 自发关闭仍等待原有 shielded 清理。disconnect 仍等待回调、串口工作线程和预算释放。
- usb.start() 后的在线通知在原 try 之外，通知错误或取消会把失败的 attach 留成活动连接。将在线发布纳入事务，失败时关闭捕获的本次 USB，保留槽位至其 offline 回调完成，清理异常只记日志并重抛最初 attach 错误。新增二十项测试在原实现 19 failed/1 passed，修复后全部通过；覆盖在线失败/取消、离线通知失败、清理期间取消、重连及旧回调隔离。

同时更正文档：端口显式指定，省略 baudrate 仍默认 1000000，桌宠配套固件 0.3.2 必须显式传 2000000。未把旧 API 默认值改为强制参数。

最终冻结源码的整合验证：聚焦 300 passed、26 subtests passed；完整 Windows 回归 2562 passed、11 skipped、26 subtests passed（PYTHONUTF8=1）；mypy 140 source files 通过。独立复核未发现 Critical/Important，新增测试独立 24 passed；实际 Runtime 与 UsbConnection 的纯内存交叉检查 4 passed。精确日志分别保存在发布验收工作目录的 focused-usb-lifecycle.log、full-usb-lifecycle.log、mypy-usb-lifecycle.log 和 full-review.md；没有将机器路径或私有媒体放进仓库。

用户提供另一台普通固件设备后，已通过独立 Runtime 和 `watcherobot app run` 启动仓库现有 sdk_media_lab。普通设备已配对，报告固件 v0.5.12/协议 1.0；小角度 motion Job、灯光以及行为启动/停止通过。相机、麦克风、RTC 与完整固件 commit 身份继续核验，不能提前声称稳定版硬件门禁通过。桌宠专用固件未被改写，尚未合并本 PR 或发布 SDK。


### 公共 close 入口的回调重入

2026-11-04，Ciel。提交 c0ff864 的自动审查 [报告](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/155#issuecomment-6621107443) 发现公开 close() 仍存在与 send 异常路径同类的循环：外部 collector 等待 dispatcher，callback 的 finally 又 await 同一 collector。先新增五项有界事件测试，原实现 4 failed/1 passed；生产修复后五项通过。只有被外部 collector 收集的 dispatcher 直接调用 close 时返回展开；普通外部调用者继续 shield 等待串口、reader、回调和 offline 清理，callback 首先发起关闭及 on_disconnect 重入 close 的既定语义保留。

最终源码冻结后：聚焦 305 passed、26 subtests passed；完整 Windows 回归 2567 passed、11 skipped、26 subtests passed；mypy 140 source files 通过。独立复核本次生产与测试完整差异及实际路由调用链，无剩余 Critical/Important；九项 close 回归独立通过。callback 重入 wait_closed 或等待子 Task 再委托 close/send 不属于本次直接 await send/close 契约，额外复现证据保留，未宣称这些组合已支持。日志为发布验收目录的 focused-usb-public-close.log、full-usb-public-close.log、mypy-usb-public-close.log、usb-close-public-review/evidence.json 和 full-review.md。

普通设备的受管测试台目前加载 c0ff864 候选。配对、小角度运动 Job、行为、灯光、扬声器、相机 JPEG、5 秒 PCM 和浏览器 RTC 视频已实际执行；用户确认动作、灯光、扬声器正常。JPEG 解码正常；PCM 无传输解码失败及丢帧，录音听感仍待用户确认。RTC 实屏约 17–24 fps，停止后资源 owner 已释放；不宣称全链路零丢帧或 RTC 双向音频通过。设备报告 v0.5.12、协议 1.0、clean commit 130bf018c332，已在固件仓库唯一解析为 130bf018c332f4e24175702db0b2849dfed8da63。最新 close 修复尚需重启独立候选 Runtime 后复核加载版本；不把旧进程冒充本次源码。PR 尚未合入，SDK 尚未发行。


### callback 先关闭后的完整收集与错误可观察性

2026-11-04，Ciel。[ca0245a 的自动审查](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/155#issuecomment-6621331592)指出：callback 先 close 时底层 collector 必须排除自己，但普通外部 close 也只等待该 collector，可能在 callback 的异步收尾退出前返回。保留上述历史验证，不将已有 callback-first 测试误称已覆盖此窗口。

第一阶段新增七项事件 barrier 回归，原实现 6 failed/1 passed，修复后七项通过。普通外部 close 在底层 collector 后 shield 等待 gather(dispatcher, return_exceptions=True)，随后才重抛原清理错误；单个外部 caller 的取消不取消共享收集。callback 自发 close、外部先关闭时的 callback 展开和 on_disconnect 重入边界保留。模块合同与 docstring 区分 callback 自身 close、外部完整 close，以及 wait_closed 的底层清理观察边界。

第一阶段冻结源码聚焦 312 passed、26 subtests passed，完整 2574 passed、11 skipped、26 subtests passed，mypy 140 source files 通过；独立复核仍为未通过：callback 在自己 close 后的 async finally 抛出普通异常，dispatcher 捕获后调用已有 _request_close，错误会被丢失，外部 close/wait_closed/error 均误报正常。独立确定性复现保存在 reproduce_usb_callback_cleanup_error.py 及 usb-dispatcher-independent-evidence.json（approved=false），未将已有测试通过代替此项错误传播验收。

第二阶段仅修复真实 callback 收尾错误的保留与向外可观察性，不把关闭后的任意发送当作 callback failure；保留已有串口、通知失败身份和取消控制流。RED/GREEN 证据保存在 usb-close-callback-error-review，最终生产与测试差异经独立复核通过，指纹见 usb-callback-error-independent-evidence.json。最终冻结整合：聚焦 320 passed、28 subtests passed；完整 Windows 2582 passed、11 skipped、28 subtests passed；mypy 140 source files 通过。对应日志为 focused-usb-close-final.log、full-usb-close-final.log、mypy-usb-close-final.log；历史失败及中间通过记录均保留。

本次未重刷设备、改动桌宠实例或将私有媒体入库。普通固件先前实机记录与用户确认仍保留；录音听感和最终源码加载复核未完成，不宣称稳定版已验收或发行。
