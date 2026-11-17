# AI Chat 账号连接诊断

> 同日补充：[设备失联检测时限](../2026-11-17_210000_KID_无Issue_设备失联检测时限/index.md) 验证突然失联时注册表清理，不改变本页 UDP 首次配对故障结论。

- 日期：2026-11-17，Asia/Shanghai；作者 KID / Codex。
- 状态：根因已确认，本机源码运行时修复且真机已连接；已获用户本地提交授权，未发布。
- 范围：SDK Daemon 的账号发现、握手及脱敏可观测性；不改变 Application 业务路由。
- 需求：用户确认 AI Chat 下桌面应保持连接，浏览桌面不打断聊天，主动启动其他应用才切换业务。
- 依据：[账号设备定向连接](../../2026-11-12/2026-11-12_170400_KID_无Issue_账号设备定向连接/index.md)、[换绑撤销](../../2026-11-14/2026-11-14_110000_wugui_无Issue_账户换绑连接撤销/index.md)。
- 现象：机器人收到账号挑战并向 Daemon 建立 WebSocket，Daemon 却仍为 discovering，拒绝 hello 为 pairing_session_required。需区分 UDP 未收到与响应认证失败，尚未确认根因。
- 验收：真机在 AI Chat 下完成账号连接；无业务旁路；拒绝日志不包含密钥、令牌或原始报文；相关路由与握手测试通过。

## TDD 与证据

先添加拒绝响应的脱敏诊断测试，再运行确认失败；结果随实际执行追加。诊断不得把最终拒绝直接认定为凭据错误。

### 2026-11-17 18:57—19:02｜KID / Codex

- Red：拒绝日志测试原实现输出 0 条；新增连接重置故障注入测试时，原实现一直复用同一通道，期望重建 2 个、实际 1 个而失败。
- Green：连接重置时关闭失效通道，沿用原广播失败淘汰及下一轮重建机制。不修改 Python 私有实现、不引入 OS 级设置或第二条设备业务连接。
- 测试进一步通过新 socket 发送真实已签名 UDP 应答，确认状态进入 CONNECTING。单独闭端口回环实验原本就通过，不能将它当成实机 ICMP 的复现依据。
- 12 个相关测试文件最终 109 项通过，21.33 秒（配对、协议、UDP、WebSocket、Application 运行/未运行的业务路由）；git diff --check 通过。
- 真机因果证据：18:57:21 开始旧手工设备 python_sdk 探测；运行时收到 WinError 10054；18:57:22 原通道已关闭并由广播失败处理淘汰；18:57:23 重建以太网通道；18:57:24 接受绑定机器人账号 UDP 应答并完成 WebSocket hello，online=true、account_device_id 与绑定编号一致、last_error=null。
- 同机原固件和诊断固件此前均可接受账号挑战；修复 SDK 后无需再次刷机即连接成功。根因在 SDK UDP 故障恢复，不在 Supabase 账号归属、AI Chat 页面或固件缺少账号任务。
- 设备串口确认 active=xiaozhi.ai、page=1、generation=1；只建立桌面连接，没有启动桌面 Application。chat_connected=0，本次没有实际发起云端语音会话，不能声称已验证同时语音对话。
- 当前运行的是本机 SDK 源码调试运行时。正式 SDK/桌面安装包尚未发布；macOS/Linux 未在本次实机测试。
- 桌面前端重载后，设备仍 online=true、last_error=null，request_id 保持 47c1ff03da2dd004b87357170fbfc6fb；设备会话未因浏览桌面或重载前端而切换。
- 桌面“重新检测”只刷新列表、每个绑定版本仅自动尝试一次的既有行为仍是独立后续项，本次未修改桌面代码。实机未执行断电或整机重启后的重新发现验收，不能把前端重载等同于设备冷启动。

## 根因资料

本机实际 Python `_ProactorDatagramTransport._loop_reading` 在 OSError 路径只调用 error_received，没有调度下一次读取。外部参考：[CPython 源码](https://github.com/python/cpython/blob/v3.12.4/Lib/asyncio/proactor_events.py)、[Microsoft UDP 错误语义](https://learn.microsoft.com/en-us/windows/win32/api/mswsock/nc-mswsock-lpfn_wsarecvmsg)。现场日志与故障注入测试是本次修复依据，不以最新版上游实现代替本机版本检查。

## 关联

- [Workspace 调查](../../../../../documents/dev_log/2026-11-17/2026-11-17_180501_KID_无Issue_账号绑定后桌面连接调查/index.md)。
- [固件取证](../../../../../WatcheRobot_esp32/docs/development/2026-11-17/2026-11-17_183200_KID_无Issue_AIChat账号连接诊断/index.md)。
