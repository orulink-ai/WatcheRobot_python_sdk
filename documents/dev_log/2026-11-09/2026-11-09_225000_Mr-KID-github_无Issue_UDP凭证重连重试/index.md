# UDP 凭证重连重试

- 时间：2026-11-09 America/Chicago；作者：Mr-KID-github / Codex。
- 用户授权修复 Desktop Link 保存凭证与自动重连，继续 UDP，PTL 不烧录。本子任务仅 SDK Daemon；ESP32 配套由主任务负责。
- 状态：SDK 本地修复与自动测试完成；无提交或推送。实机由主任务继续执行。
- 根因：Daemon 只在启动扫描一次；mode_exit 与扫描超时后没有后续发现。
- 设计：复用已有 UDP 广播协程定时循环，在空闲时每 30 秒发起一个现有 10 秒凭证扫描窗口；mode_exit 后下一 tick 启动。已连接、手动配对、凭证握手期间不覆盖；用户手动配对可抢占尚未应答的自动扫描；明确 disconnect 在所有状态清除绑定并停止重试。
- 资源：不新增线程或后台协程，复用 UDP 循环，仅保留下一扫描时间；不改变业务帧路由或协议。
- 验收：超时后再扫描、mode_exit 重进、显式断开不再找回、手动配对优先、连接不被打断。先写失败测试，再实现，实际记录结果。

## 实际实现与验证

- 修改 runtime.py：30 秒扫描开始间隔；mode_exit 重置调度；明确 disconnect 在所有状态忘记绑定；尚未应答的自动发现可被手动配对替换。
- 修改 pairing/udp.py：已有广播循环在配置 idle_listener 时维持运行，在空闲 tick 调用运行时调度；没有增加第二个定时任务。未配置该回调的独立 UDP 使用者保持原有退出行为。
- Red：新增四项运行时行为测试，实际运行得到 4 failed / 3 passed，分别为扫描超时后没有重试、mode_exit 后没有扫描、扫描/空闲断开不清记录、自动发现阻止手动配对。均为目标行为缺失。
- Green：重连 runtime、session、UDP、协议、绑定存储和路由回归 54 项通过。覆盖重新扫描后凭证应答与 WebSocket hello 实际连通、连接建立后不会被后台扫描覆盖。
- 完整 .venv/bin/python -m pytest：2457 passed，11 skipped，5 warnings，233.56 秒，退出码 0。警告为既有 Rich/Click 弃用、Starlette 弃用与重复 ZIP 条目测试输入；没有失败。
- 最终微调只将重试延后设置移到取消操作的 await 之前，避免取消通知 yield 时被自动发现抢占；随后重跑针对性回归。源码 mypy 两文件通过，git diff --check 通过。
- 协议管理正式文档已说明扫描窗口、间隔、手动优先、退出与断开语义。没有修改业务帧路由、API 字段或协议消息。
- 全套日志位于忽略的本机 runtime 日志目录，不提交机器路径或网络信息。本档案不宣称实机已经通过。

## 主任务实机验收

使用托管 editable SDK 环境重启 Daemon，完成 ESP32 真实手动配对、mode_exit 后免码恢复、晚进入重试、应用烧录重启保留绑定、主动 disconnect 停止自动找回及重新手动绑定。另发现 ESP32 漏处理 WebSocket 正常关闭事件，配套修复后正常停止/重启 Daemon 可免码恢复。主任务独立复跑 reunite runtime/session/UDP 共 31 项通过。详见 [workspace 实机档案](../../../../../documents/dev_log/2026-11-09/2026-11-09_224018_Mr-KID-github_无Issue_Desktop-Link实机重连诊断/index.md)。未提交/推送。

## 2026-11-10 设备进入立即唤醒重连（Codex / sdk_reconnect）

- 用户进一步要求设备有绑定时进入立即尝试连接，客户端未运行时保留等待。SDK 新增严格五字段 `link.reunite.available` UDP 控制报文解析，复用既有随机 nonce/HMAC/request/accept/hello 流程。
- 提示仅在 idle、有最近绑定、Daemon ID/模式/来源 IP 匹配时绕过30秒扫描退避并立即发一次 request。没有 device_id 存储字段；IP 只筛选提示，认证仍依赖 HMAC。手动配对、现有扫描、已连接均不受抢占；显式取消抑制提示30秒，forget 后无绑定不响应。
- TDD 先失败：有效提示未唤醒；随后取消后提示错误抢占的新增断言失败；分别实现后恢复通过。测试覆盖真实 UDP socket 收到 availability、无效字段/版本/模式/身份、非绑定 peer、重复提示、手动优先、取消、forget。
- 保留每网卡绑定 socket：简单把每个地址改成0.0.0.0会引入多socket同端口冲突及收包网卡归属歧义，无法保留现有多网卡模型。按主任务决定采用 last_peer_ip 单播+原主动扫描兜底，没有增加socket。桌面或设备地址变化的即时提示可能无效，最坏仍需下一次30秒扫描，正式合同已说明。
- 三个修改源码文件 mypy 通过；相关 runtime/UDP/protocol 回归通过。完整 pytest 已启动，结果另补；不宣称硬件已验收。未提交/推送。


## 2026-11-10 进入即重连最终验收（Codex）

沿用 UDP/HMAC，不引入 mDNS。ESP 进入已绑定 Desktop Link 立即向历史电脑 IPv4 发 available 提示，随后 0.5/1/2/5 秒退避，等待期间每 5 秒重试；SDK 仅在空闲且最近绑定、来源 IP、Daemon ID、模式匹配时启动原 HMAC 握手。提示不构成身份认证。手动配对、在线状态与取消抑制不受提示抢占。已保存绑定等待或超时不再显示配对码。SDK Control 的 python_sdk 绑定提示模式也已修正并通过 host 测试。

验证：SDK 六个相关文件 57 项通过；完整 pytest 原会话 exit 0（配置未打印汇总计数）；ESP 两个 lan_pairing host 及三个主程序/WS/helper host 通过。全量 ESP host 仍有此前 JoyInside atomic 夹具阻塞，不宣称全套通过。

实机：最终固件已写入、hash 验证成功，应用入口 client.app。Daemon 停止后进入，binding=armed、reconnecting=1，等待约 54 秒无配对码回退，启动服务后无需手动输入就 connected。两次刻意等待旧扫描窗口结束后进入，监听开始至接受分别 35ms/164ms，至 hello ACK 1465ms/772ms。Daemon 日志新扫描间隔分别 22.7s/25.6s，小于原 30s，证明提示唤醒。反复退出重进成功。以上时间是本机局域网样本，不是延迟保证。设备屏幕状态以实际 UI 调用日志和 host 断言验证，未拍摄物理屏幕。

资源：同配置应用 4877712 -> 4878640 字节（+928）；map DRAM data=0x8147、BSS=0xe098 均不变。没有新增任务/socket/堆缓冲或周期 NVS 写。等待 internal_free=35454、largest=16384；在线约 16KB/最大块4.5～5KB，退出恢复约41162/17408。现有内存仍紧张，未做长时间压力验收，不能由短测认定无泄漏。

限制：电脑或设备 IP 改变时，历史 IP 提示/来源筛选可能失效，仍由原每30秒启动的扫描兜底，本轮没有宣称跨地址变化即时连接。只烧 ESP32 AppOnly 和 OTA 启动选择区，不烧 PTL、STM32，不擦 NVS。构建输出的 PTL 表示保留已有相机配置。第一次最终烧录后附加 JoyInside 配置因过早打开串口失败，已释放串口并重新执行同产物 AppOnly 确认；最终结果另附。

应用广场真实 GUI 已再次显示 6 个应用和 Watcher 在线；根因是手动开发启动缺少分发入口环境变量，恢复既有配置，无产品源码修复。所有改动未提交、未推送。原始串口、烧录、测试日志位于被忽略的 .runtime/analysis-mdns，不提交本机敏感信息。


最终确认：同一最新产物 AppOnly 重刷及 JoyInside 附加配置均成功，wrapper exit 0；PTL 未烧录。


## 2026-11-10 合入准备（Mr-KID-github / Codex）

用户授权本地合并并普通推送远程，不等待 CI。本次核对全部 SDK diff、正式协议及测试，fetch origin/main 后确认远程基线仍为 ac18fb2，无新增主线提交。配套固件为 WatcheRobot_esp32 的 Desktop-Link 凭证重连与 available 提示改动；分别在各自仓库提交，Workspace 后续单独记录 gitlink。保留上文真实实机与测试结果及 IP 变化兜底限制，不改变业务路由，不包含客户端新增 UI 设计。提交前追加针对性回归结果。

2026-11-10 01:18 CST 提交前复核：七个相关 runtime/session/UDP/protocol/store/routing 测试文件 70 passed（26.94 秒）；三个修改源码文件 mypy 通过。首次复跑误用不存在的测试文件名，没有运行测试，已修正为真实文件名后得到上述结果。git diff --check 通过。修正本档案一处控制字符为 Markdown 代码标记，仅修正文档排版。既有完整测试和实机验收沿用上文证据，本次没有重复烧录。

## 后续变更

- [账号设备定向连接](../../2026-11-12/2026-11-12_170400_KID_无Issue_账号设备定向连接/index.md) 为同账号设备新增定向发现及账号握手，沿用本档案的手动绑定凭证和重连协议。
