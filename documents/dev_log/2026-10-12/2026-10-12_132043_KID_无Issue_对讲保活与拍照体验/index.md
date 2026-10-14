# 对讲保活与拍照体验

## 基本信息

- 创建：2026-10-12T13:20:43+08:00，Asia/Shanghai；同步最新 main 后补建，早期记录为追溯。
- 建档作者：KID（当前有效 Git 作者）；参与者：用户、Codex。
- 需求：局域网原声对讲与拍照独立应用，排查自动断开、实机重复测试、拍照预计倒计时，提交并合并 PR 后切回最新主分支。无 Issue。
- 初始分支与历史 PR：codex/robot-intercom；[SDK PR #143](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/143)。
- 当前状态：presence 诊断语义本地修正并通过自动回归；RTC 增益候选完成三次 60 秒客观采样、10 轮正式通话及约 650 秒持续 connected 观测，10 轮均有成功释放与增益恢复证据。本阶段诊断与生命周期验证完成、源码待审，增益补丁待合入；两轮真实麦克风采样实际使用蓝牙耳机输出，不验收喇叭闭环。按用户要求本阶段先收尾，精确啸叫复现、异常恢复、正常真人音量和主观双讲留待下一阶段；本阶段不发布新商店版本，根代理确认现有 0.1.3 目录请求 #19 仍 open，未宣称已合入。
- 边界：Application 使用注入通道，保持 Daemon 内容无关路由；未改变固件曝光预热，没有新增交付系统脚本。

## 调查与决策

页面 JavaScript 保活暂停超过固件 5 秒期限可复现 heartbeat_timeout。设备保活交给 Application 后端，本机原生 presence 判断页面存在；协商始终有 30 秒期限，presence 不延长它。PTL 相机预热约 1.2 秒，照片传回显示约 2 秒；点击立即请求，以预计倒计时解释等待，不延迟快门或取消预热。

presence 注册由线程执行，协程取消不会停止线程，必须保留最终 token 并安排清理；应用持有并等待清理任务，重复取消不遗失收尾。拍照任务绑定通话身份，停止、失败或替换通话中止本机等待，显示前再次检查身份；不把 HTTP abort 表述为设备快门已取消。

## 材料与历史关联

- [正式验收](../../../../docs/robot-intercom-acceptance.zh-CN.md)、[使用说明](../../../../examples/robot_intercom/README.md)。
- [服务回归](../../../../tests/test_robot_intercom.py)、[拍照回归](../../../../tests/js/test_robot_intercom_photo.mjs)、[RTC 合同](../../../../tests/application/test_rtc.py)。
- [此前源码补交](../2026-10-12_103021_KID_无Issue_补交本地最新代码/index.md)：本次补充其对讲功能，保留 #142 的请求期限、取消缓存和设备门禁；原档案已追加反向链接，历史正文不覆盖。

## 变更与验证

### 2026-10-12T13:20:43+08:00｜KID / Codex｜追溯与提交前记录

- b941189 修复页面暂停保活，d005e99 增加拍照预计倒计时；原验收包含连续 600 秒实体通话及随后 20 轮通话。此处追溯正式验收，不宣称本次重新执行 20 轮。
- 8126ec4 非快进同步 #142，保留双方回归并增加两项 presence/协商截止组合测试；Python 74 项、浏览器 49 项通过，实机拍照 1802 ms，正常释放。
- b9dc611 修正 presence 取消与拍照跨通话结果。拍照身份用例修改前失败（旧实现返回成功）、修正后通过。presence 首次 Red 包含未实现清理集合这一辅助字段的错误，不能把该输出冒充独立资源泄漏断言；线程完成后丢失 token 的缺口由生命周期核对确认。注册、握手、重复取消清理用例最终通过。
- ApplicationRtc.start 清空旧事件并过滤旧 session，经实际 SDK 对象回归核验；生产 SDK 合同未改，属于事后补测。
- 修正后 Python 78 项、浏览器 51 项通过。实机拍照中结束，旧照片不显示；新通话等待 4 秒仍不显示旧结果，新拍照 1872 ms、双向包 286/283，正常释放。
- 最新 Red：presence 存在的 connecting 没有后端 ping；慢设备查询跨过截止后仍保留 RTC，两项失败。Green：后端保活覆盖协商阶段，并在 ping 前后复查期限和身份；同一回归通过，相关 Python 共 80 项通过。浏览器生产逻辑未再改变，前次 51 项通过仍对应最终逻辑。
- 执行命令：`python -m pytest tests/test_robot_intercom.py tests/test_examples.py tests/application/test_rtc.py -q`；浏览器执行对讲、拍照、采集、延迟、恢复、播放和 RNNoise 模块测试。Windows、当前源码 SDK 与真实设备；精确实机阶段和限制见正式验收。
- 本机原始日志、照片、音频、临时验收脚本不提交；未新增 Daemon 路由旁路。

## 遗留与收尾

### 2026-10-12 Asia/Shanghai｜KID / Codex｜最终分发快照

协商保活修正 378c2da 已推送 SDK PR #143。正式分发接口上传并验证最终
[应用快照](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.robot_intercom/tree/496c7c9f7ddc61d92fc18486ddd7fc6e834e9e84)，提交
[商店 #16](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/16)，返回 pending。
原 #15 是已核对身份和 SHA 的本账号旧请求，关闭以替换过时快照；不重复尝试
已被权限拒绝的合并，不切换账号。源码和商店各自保留准确状态；最终 SDK CI
与复审尚在进行，未提前标记合入。

有限观察有少量队列丢帧、一次过期上行帧丢弃、削顶和内存余量紧，不宣称听感、零丢帧或长期无泄漏已验收。商店合入权限仍受限。SDK 使用 merge commit，合入后核对两个 parent，Workspace 单独更新 gitlink。

### 2026-10-12 Asia/Shanghai｜KID / Codex｜协商终止与退出原因修正

- ce1d90a 的完整 CI 通过：Python 3.10 / 3.12 各 1850 项主测试和 30 项表情测试；开发检查同样通过，mypy 124 个源码文件、浏览器 198 项及构建/安装通过。较早的复审服务调用超时，最终复审成功产出报告，指出协商阶段终止事件被消费却不触发清理，属于有效问题。
- Red：新增 connecting / running × failed / stopping / stopped 六项，以及 user / page_closed 首次释放失败后维护重试两项；修改前 5 项失败、3 项通过。失败对应协商不释放和首次原因被 cleanup_retry 覆盖。
- Green：终止事件处理覆盖协商与通话，终止后不再 ping，复用现有维护清理流程；首次退出原因只赋值一次，各次尝试原因保留在历史，下一通话重置。相关 Python 88 项、浏览器 51 项重新通过，Git 空白检查通过。未重构，未伪称完成设备失败注入。
- 重新上传且验证包含本次修正的 [0.1.2 固定源码](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.robot_intercom/tree/721e187ab829e9f9b1dcf95e660c2824e2d78b1a)，[商店 #17](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/17) 返回 pending；核对 #16 的作者、标题和固定 SHA 后关闭旧请求。当前以 #17 为准，不重复尝试已拒绝的目录合并。
- 本次源码仍待最终 CI 与复审，按用户授权完成后继续 SDK merge 及 Workspace gitlink 交付。

## 后续变更

2026-10-12｜KID / Codex｜补充：[ElevenLabs 对讲界面设计](../2026-10-12_160422_KID_无Issue_对讲界面设计/index.md)。改善视觉与操作层级，保持原声通话、保活、释放和拍照身份隔离。

### 2026-10-12T22:00:44+08:00｜KID / Codex｜presence 断开诊断语义更正

- 背景：用户再次报告自动退出，并怀疑移除视觉功能后出现通话与回声消除回归。本条只修复应用层退出原因和关闭证据，供后续硬件对照使用；不把诊断完善当成 RTC、网络或 AEC 根因已解决。
- 更正历史解释：旧 `detach_presence()` 对所有 WebSocket 断开均调用 `stop(reason="page_closed")`，因此历史日志中单独出现 `page_closed` **不能证明**网页执行了真实 `pagehide`、用户关闭网页或浏览器退出。只有显式离开页面发送的 stop/beacon 请求带有该原因；原日志缺少关闭 code/reason，无法回溯证明某一历史断开属于哪个来源。保留原始正文和实机记录，不重写历史结论。
- 状态：本地修改，自动化验证通过；本条没有提交、推送或部署动作。根代理继续固件与硬件对照，后续追加真实实机结果。本次没有操作硬件、运行中的 Daemon 或浏览器。
- 变更范围：[服务与 HTTP/WS 合同](../../../../examples/robot_intercom/service.py)、[presence 连接器](../../../../examples/robot_intercom/web/call-presence.mjs)、[通话控制器](../../../../examples/robot_intercom/web/call-controller.mjs)、[Python 回归](../../../../tests/test_robot_intercom.py)、[Node 回归](../../../../tests/js/test_robot_intercom.mjs)。Daemon 内容无关路由、设备通道、RTC 模式、协商/心跳期限和重连策略保持原有边界。
- 诊断行为：意外 presence 失联记录 `presence_lost`；此前已接受的显式 `page_closed` 首次原因保留。后端 `presence_closed` 记录关闭 code、有限原因、handler 退出类别、shutdown 状态和心跳年龄；停止请求同步记录 shutdown 和心跳年龄。前端增加 `presence_lost` 诊断，并记录 `close_code`、`was_clean`、`error_seen` 与有限关闭原因。只保留已知协议原因短语，未知文本输出 `[redacted]`，避免 URL、配对码或凭据写入日志；不保存 presence token。
- 额外并发发现：detach 先进入 `cleanup_required`，再等待生命周期锁停止；维护线程可能先完成释放并写下 `cleanup_retry`。在同一状态锁内只在首次原因为空时记下 `presence_lost`，避免记录首次原因的竞争，不改变释放锁顺序。

#### TDD：真实 Red 与 Green

- 独立语义 Red：`python -m pytest tests/test_robot_intercom.py -q -k unexpected_presence_disconnect` 修改前失败，实际是 `page_closed`，预期为 `presence_lost`。不是因新增接口尚未实现而失败。
- 关闭证据 Red：WebSocket endpoint 用例修改前访问 `close_code` 得到 `KeyError`；新增前端诊断 kind 修改前返回 HTTP 422。首批 metadata 参数测试中的 3 项 `TypeError` 属于接口未实现的脚手架失败，不能冒充额外根因证据。
- 浏览器语义 Red：执行 `node --test --test-name-pattern 'presence error waits|presence close without|controller reports presence' tests/js/test_robot_intercom.mjs`，3 项修改前失败。原实现收到 error 后立即生成另一轮 close 并失败，真实关闭字段丢失，controller 未报告 presence 证据。
- 并发语义 Red：`python -m pytest tests/test_robot_intercom.py -q -k presence_loss_reason` 在增加日志语义修正后仍失败；人为安排维护先获得释放锁，实际首次原因被写为 `cleanup_retry`。锁内保留首次观察原因后，同一用例通过。
- Green：Windows 当前源码执行 `python -m pytest tests/test_robot_intercom.py tests/test_examples.py tests/application/test_rtc.py -q`，109 项通过，其中对讲服务 83 项。覆盖显式退出原因保留、旧 token 不影响新通话、异常 WS 关闭证据、取消与重复取消清理、原因文本脱敏和字段验证。
- Green：`node --test tests/js/test_robot_intercom.mjs tests/js/test_robot_intercom_photo.mjs tests/js/test_intercom_i18n.mjs tests/js/test_intercom_device.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_connection_recovery.mjs`，49 项通过；其中对讲 21 项，覆盖 error/close 重复时仅失败一次、真实 close metadata、主动关闭、旧 socket 回调和既有资源释放路径。
- `git diff --check` 通过。未扩大 RTC/AEC 业务实现，未执行完整 CI；本条单测结果不能替代真实持续通话、实体声音/嘴型、回声消除、网络恢复或资源余量验收。

#### 只读生命周期自审

- 确认的一处时机变化：原 error 回调立即失败；现在只标记 `error_seen`，等浏览器规范要求随后的 close 事件再执行一次失败清理，避免主动 `socket.close()` 覆盖真实关闭证据。没有增加重连或宽限定时器。握手未 ready 时仍保留原 5 秒期限；ready 后的正常浏览器网络错误遵循 error 后 close 的事件顺序。
- 后端注册的线程任务、`shield`、finally 中安排 cleanup、清理集合和应用退出时等待集合全部保留。取消只增加可识别诊断字段，继续 re-raise 并经过原 finally；旧 request/token 在锁内拒绝，无法清理新通话。
- 前端握手成功、超时、异常 close 和显式 close 均继续结束自身连接；异常 close 后不再主动重复关闭一个已关闭 socket。`ended`、`intentional`、controller generation 及 scoped stop 保留，未发现新增不回收分支。原生浏览器保证 error 后 close；不把不遵循浏览器事件合同的自定义 WebSocket 实现当作本次验收范围。
- 明确限制：后端完成释放后再到达的旧 socket 关闭诊断会被身份门禁忽略；前端 diagnostic HTTP 也可能与 scoped stop 竞争而被拒绝。因此关闭证据用于相互核对，不承诺每一种时序都能同时收到前端和后端两份记录。该限制不会改变实际停止或资源回收。

### 2026-10-12T22:31:47+08:00｜KID / Codex｜追加中英文固件测量基线说明

- 文档范围：[应用 README](../../../../examples/robot_intercom/README.md)、[验证与分发记录](../../../../docs/robot-intercom-acceptance.zh-CN.md) 和本档案；未修改应用清单版本、运行门禁、业务代码或发布状态。原 0.1.3 发布时的 v0.5.5 约 10 秒断连记录保留，另加后续调查，避免把历史样本当作当前所有固件的行为。
- 事实来源：根代理本次实机对照报告。旧 `8fcd310d8802` / v0.5.5 出现 HMAC `-141` 与 DMA 26 B；新 `214b4f91` / v0.5.6 已有加密 GDMA reset 与录音缓冲 PSRAM 改动，保留 TX24 时通话 DMA 约 3.3 KiB，两轮各 60 秒 AEC 诊断中 TX/render/queue 错误计数为 0。本条作者未重新操作硬件，后续由根代理追加完整实机依据。
- 建议与边界：v0.5.6 是当前复测基线；长测和恢复验证尚未完成，不写全部稳定、全部根因修复或主观 AEC 通过。推荐固件版本没有转成 runtime 版本拒绝规则，现有能力门禁继续适用。
- 中英文均说明 `presence_lost` 与显式 `page_closed` 的诊断区别和旧日志的解释限制；现场 RNNoise 处理机器人返回电脑的收听音频，不是 AEC 开关，不控制浏览器麦克风或机器人固件的回声消除。
- 验证方式：本次仅文档修改，TDD 不适用；对照源代码的麦克风约束和 RNNoise 接入位置，人工核对中英文数值、版本、推荐和未完成验收范围一致，并执行 `git diff --check`。未重复声学验收或长时间通话，不沿用旧版本稳定性记录证明当前新版通过。

### 2026-10-12T23:28:07+08:00｜KID / Codex｜增益候选六轮对照与验收边界

- 当前候选明确为 **v0.5.6 / `214b4f9145dd` 基线加待合入的 RTC 麦克风增益补丁**，报告 `dirty=true`；正式基线 `dirty=false`，没有把候选补丁标成已包含在发布版 v0.5.6。当前概述据本次回归更新，不删除历史版本的实机与分发正文。本条没有 commit、push、PR、合入或商店发布操作。
- 范围：[应用 README](../../../../examples/robot_intercom/README.md)、[正式验证记录](../../../../docs/robot-intercom-acceptance.zh-CN.md) 与本档案。依据根代理实机记录及独立离线分析更新，不改 SDK 业务代码、应用清单、固件或冻结探针；离线作者没有操作浏览器、Daemon、串口或硬件。
- 数字 AEC 原始五轮各仅读一次，后生成的候选第三轮单独读取一次，旧数据不重读。原始文件 `aec-latest-1/2/3.json` 与 `aec-gain-1/2/3.json` 仍未改写；每份内容 SHA-256、每窗 energy / samples、名义 fixture 重叠及设备首末 HTTP 样本都保留在独立汇总中。分析脚本仅处于 ignored 临时目录，没有进入正式应用或公共脚本入口。
- 六轮均为 `completed_60_seconds`；候选三轮各约 60.001 秒，结束采样仍 running / connected，随后 idle / stopped。名义完整 WAV 内容窗口中，无补丁三轮返回 RMS 为 0.0105731 / 0.0079784 / 0.0066657，候选为 0.0010215 / 0.0010996 / 0.0010657。每组先累加 energy、再除 samples；不是直接平均每窗 RMS。候选相对基线能量差约 −15.65～−20.30 dB，只是条件性的数字 source 内容时段比较，不是独立 ERLE、可懂度或主观 AEC 通过。
- 关键测量缺口：三个旧基线虽报告 schema 2，但均无 `windowStartSeconds`。首窗 49,152 samples 对应 1.024 秒，却结束于 0.962 / 0.212 / 0.014 秒，含测量前音频；排除首窗，其余窗口按相邻端点条件重建。基线尾部缺少 flush，漏记末尾 0.639 / 0.380 / 0.597 秒；候选有明确起点与末尾 partial flush。跨内容 / 静音边界的窗口不按重叠比例分配能量。实际 source/return 时钟、重采样和回传延迟未实测，WAV 内容本身也含静音，窗口覆盖差异会改变对应 source 能量。
- 设备 HTTP 计数窗口约 59 秒，不假装完整覆盖 60 秒，缺少固件遥测生成时间。候选 processed input/output 比值为 17.3917 / 17.7056 / 17.9466 dB；只覆盖 processed 帧，不包含 bypass，并包含环境声音。三轮 reference starved 为 24 / 31 / 23，discard 为 24,576 / 31,744 / 23,040 B，reference drops、削顶、上行丢帧、TX / render errors 均为 0，queue dropped 为 0 / 1 / 2。参考连续性缺口仍在，不能写全部零丢帧或 AEC 已验收。
- 根代理提供的近端单独测量：候选第二轮同口径 7 个完整内容窗口 RMS 为 0.004085847，播放前 0.001159659，相对原基线内容 −14.3327 dB；全程 WAV 包络相关性 0.887929，内容窗口 0.926552。捕获 / 发送帧增量 773 / 773，参考能量和削顶为 0，结束仍 running / connected。支持近端信号保留，同时记录增益降低带来的幅度代价。候选第一轮播放前背景相对无补丁近端原基线高 15.98 dB，不能据全程相关性较低判断近端静音；未把客观包络相关性写成主观试听或双讲通过。
- 页面退出顺序补充：`leave()` 在真实 `pagehide` 内先 dispose / 关闭 presence，再发 `page_closed` beacon。关闭先被观察时，首次原因可能是 `presence_lost`；资源先释放时，晚到 beacon 不保证新增 stop 记录。任何单个 end_reason 都不能单独证明页面关闭或网络故障。保留真实用户导航、WS 关闭与设备停止时序作为验收要求，本条未伪称完成真实导航验收。

#### 过程证据与验证

- 本机临时汇总标识：`aec-gain3-vs-latest3-window-analysis.json`（严格显式边界及逐窗归一）、`aec-gain3-vs-latest3-reconstructed-phase-analysis.json`（旧基线端点条件重建与首窗排除）。原始数据及汇总没有稳定外部归档，本次只记录文件标识，不编造可下载链接或提交到应用发布包。
- 独立复审命令：`python -m pytest tests/test_robot_intercom.py -q -k 'presence or cleanup_retry or diagnostics_retain_terminal or device_terminal'`，25 项通过；`node --test tests/js/test_robot_intercom.mjs`，21 项通过；`git diff --check` 通过。该执行与上方历史 Green 记录分开，不扩大成完整 CI、实际网络故障注入或声学验收。
- 临时分析使用逐窗均方和相位边界自检，原始记录中的 RMS 全部与重算相符，设备 counter delta 重算与保存值全部一致；Python 语法检查通过。分析属于已有测量的事后离线核对，本次文档 TDD 不适用，不宣称这些是先于固件实现的 Red / Green。
- 中英文 README 和验证摘要同步区分基线、待合入补丁、短窗客观结果及未完成验收。持续通话、异常恢复、正常说话音量、增益恢复与真人双讲仍由根代理继续验证；本次分发状态未重新查询或更新，不猜测最新审核号。

### 2026-10-13T00:02:00+08:00｜KID / Codex｜10 轮正式通话与长测阶段证据

- 范围：[应用 README](../../../../examples/robot_intercom/README.md)、[正式验证记录](../../../../docs/robot-intercom-acceptance.zh-CN.md) 与本档案。README 首节新增 0.1.4 本地候选中英文说明，保留 0.1.3 历史版本和两轮 60 秒的前期调查；源码清单版本不等于商店合入状态。当前候选仍是 v0.5.6 基线加待合入增益补丁，不声称正式发布版已经包含该补丁。
- 根代理通过正式页面完成 10 轮通话，独立离线核对每轮 request、媒体进度、release.sequence 及串口增益标记。10 轮有完整成功释放结果，sequence 为 6、9～17；7 / 8 是中间很短的取消尝试，不计为正式轮次。第 2 / 4 轮没有 idle 采样，用后续快照内保留的成功 sequence 9 / 11 交叉确认，未伪称这两轮观察到 idle。所有正式轮次均有 27 → 12 → 27 的增益限制和恢复，结果为 0。
- 第 10 轮共 598 个 running 样本，首末跨度 649.672 秒，最大轮询间隔 1.25 秒，全部 connected 且 presence=true。单调时钟 call_start 至 stop 为 652.844 秒，stop_requested 至应用 released 为 578 ms。观测窗口捕获 / TX / RX / 解码增量为 32,475 / 32,475 / 32,442 / 32,435，I2S 增量 20,494,720 B。TX / render errors、上行丢帧及削顶为 0，播放队列丢帧为 7。
- 长测采样内 internal / DMA 最低可用为 10,578 / 3,090 B；资源历史 minimum DMA=110 B 是自启动以来的值，不是这次长测最低值。sequence 17 已稳定释放时 internal / DMA / PSRAM 为 38,126 / 30,434 / 5,737,464 B，相对第 1 轮已稳定释放快照 heap / internal / DMA 各差 −8 B、PSRAM 差 0。有限次观察不证明长期无泄漏；rtc_stop 当刻的整 KiB 串口内存和最终精确 resources.memory 不同阶段，停止快照内 rtc.stats.memory 是最后通话统计，未混为释放后的值。
- 证据标识：`gain-formal10-crosschecked-safe-summary.json`，来源为 `gain-candidate-samples.jsonl` / `gain-candidate-serial.log` 与 `gain-product-samples.jsonl` / `gain-product-serial.log`。离线核对重新读取了生命周期原始材料，不沿用前段数字 AEC 数据“每份仅读一次”的限定。分析移除串口 HOST_TIME 标记时仅删除标记 token，保留同一读块的业务行；主机时间是读日志的时间，未当成精确执行时间。汇总白名单过滤地址、配对内容、凭据及任意原始错误文本，材料仍处 ignored 临时目录，暂无稳定外部归档。
- 验收限制：持续 connected、资源成功释放和拾音幅度降低不构成声学闭环或真人双讲通过。用户曾报告声音重复且逐渐放大，根代理当前尚未在增益 12 候选准确复现该精确现象，啸叫专项验收未完成。数字 source、静音发送及只分析不播放返回声音的近端试验均未覆盖真实麦克风闭环；不把浏览器报告 AEC=true 或单流 RNNoise 降噪当作闭环参考有效的证明。分发推进暂停，本条没有 commit、push、PR 合入或商店操作。
- 临时真实麦克风闭环探针由子代理完成并冻结，11 / 11 mock/core 测试、Node 语法及 Python 编译通过；包含真实窗口起止、samples>0 有效标记、contextState、4096-sample 回调粒度、60 秒有界停止和 pagehide 释放。这些是软件测试，探针尚未取得有效实机闭环结果，不写成声学验收。根代理报告第一次独立 origin 运行仍待麦克风权限、RTC idle，被标为 invalid，没有计入闭环测量数量。
- 本条仅记录既有测量与文档，TDD 不适用；替代验证为安全离线汇总核对、当前版本与中英文表述一致性检查及 `git diff --check`。离线作者未操作浏览器、Daemon、串口、烧录或 Git；实际硬件复测由根代理继续推进。

### 2026-10-13T00:24:15+08:00｜KID / Codex｜阶段收尾、耳机端点限制与独立复审

- 用户要求本阶段先结束，新的喇叭场景试验暂停；本条只收尾已有 SDK 诊断改动、验证与档案，不继续硬件试验。本阶段诊断与生命周期验证完成、源码待审；根代理准备分别提交 SDK 与固件并创建 draft PR，尚未据此声称已提交、推送或合入。本阶段不发布新商店版本；根代理核对 0.1.3 的目录请求 [#19](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/19) 仍为 open，不把源码候选 0.1.4 当作商店已发布版本。后续实际 commit / PR 与分发状态由执行者另行追加。
- 两轮独立真实麦克风探针 `loop-adaptive-3.json` / `loop-adaptive-4.json` 取得实际有效采样，AEC / NS / AGC 报告 true、mono 48 kHz，meter 双 48 kHz，RNNoise 分别开启 / bypass，返送播放未被浏览器阻止，均以 60 秒有界停止。源为 SDK `examples/sdk_media_lab/assets/sample_speech.wav`，原生脚本无缩放、重采样或归一化；脚本使用异步 PlaySound、等待约 8.2 秒后停止，API 起止不等于实际 DAC 音频边界。
- 测后根代理查询 Windows 默认输出，实际为蓝牙耳机，并非电脑喇叭在机器人旁的目标路径。两轮原始返送在名义内容期仍接近背景，本机麦克风有不同幅度瞬态，不能据 API 调用、浏览器 AEC=true、瞬态或返回较小认定有效声源进入、闭环衰减或啸叫已修复。SDK README 中英文和正式验证记录已明确：耳机条件下未见持续放大不排除正确喇叭端点下的问题；下一阶段先核对播放 / 采集端点及有效刺激，再做尾声与真人双讲。
- 安全离线能量汇总 `loop-adaptive-3-energy-analysis.json` / `loop-adaptive-4-energy-analysis.json` 使用 samples 加权 RMS，检查保存 RMS 与 energy / samples 一致，只选 running、context running、samples>0、完整落入相位的窗口；pre 每轮仅 1 桶，跨界桶不分配能量。口径与精确数字见正式验证记录，不将其计入前述 10 轮正式释放数量。部分 probe 最终 device snapshot 过早，未借该快照宣称设备释放已确认。
- 子代理独立来源包络核对标识 `loop-adaptive-3-native-envelope-safe-analysis.json`：实际 WAV 为 mono 24 kHz、7.8645 秒，原始 PCM RMS 0.0585206、peak 0.914978。按名义 API 开始对齐，完整内容 7 桶的 source RMS 包络与本机 / 返送相关性为 0.19173 / −0.15417，connected whole 窗 57 桶相关性为 −0.02891 / −0.08623，未辨出可靠源包络。小样本延迟敏感性分析中局部相关性可升高，不代表测得物理延迟、声源有效、PCM 波形相关或主观质量通过。该子代理对原始 loop3 / native 和 WAV 各仅读一次；该限定不扩展到父代理的能量核对读取或此前生命周期原始日志。子代理未读取 loop4，父代理执行其能量核对；所有原始与汇总仍 ignored，没有进入产品发布包。
- 后续 UNITY DSP 候选属于单独的固件实验，未纳入本阶段 SDK 产品变更或啸叫已解决的结论。本阶段正式 App 的麦克风约束、RNNoise 模块、视觉层和 Daemon 路由没有新改动；采集 / 播放模块与 SDK 测试平台当前对应模块哈希一致，这只确认当前源码一致，不回溯证明所有历史执行环境相同。
- 最终当前 SDK 回归重新执行：`python -m pytest tests/test_robot_intercom.py tests/test_examples.py tests/application/test_rtc.py -q`，109 项通过、Exit 0；`node --test tests/js/test_robot_intercom.mjs tests/js/test_robot_intercom_photo.mjs tests/js/test_intercom_i18n.mjs tests/js/test_intercom_device.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_connection_recovery.mjs`，49 / 49 通过。没有扩展成完整 CI、真人听感、真实网络故障注入或喇叭闭环验收。
- 独立只读最终 diff review 未发现新增代码阻断项：Daemon / Application runtime 与 app.py 无 diff；新增字段仅在 Application 本机诊断，没有 Desktop 业务直达设备旁路或按内容分支。service 在状态锁内保存首次 presence_lost 后才等待生命周期锁，stop 保留 request / token 身份门禁及首次原因；WS endpoint 的线程任务、shield、finally 清理、取消重抛保持。JS 等待原生 error 后的真实 close，ended / intentional 单次终止与 controller generation 检查仍在，stop / leave 原有资源释放顺序不变；未知 close reason 静态白名单脱敏。
- 子代理本次仅执行 `git diff --check`（Exit 0），没有重复测试；其历史 25 项 Python / 21 项 Node 与本条父代理最终 109 / 49 项区分。当前 SDK 待提交为 service、controller、presence、app.json、README、两份回归、正式验证记录和本档案共 9 文件；ignored 探针、分析脚本、配对日志、照片、音频及个人端点 / 路径不提交。最终文档属于阶段事实与边界记录，TDD 不适用，替代验证为独立数字核对、代码 diff review、中英文一致性与空白检查。
