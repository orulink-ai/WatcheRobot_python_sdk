# Pi 语音页面机器人配对

- 创建日期：2026-10-18，Asia/Shanghai；作者：Mr-KID-github / Codex；无 Issue。
- 状态：页面与后端已完成、本地应用已重启；等待用户输入真实配对码验收。未提交或推送。
- 背景：用户打开 JoyInside 本地 Pi 语音助手后发现页面没有配对码入口，必须另开 SDK CLI 或桌面才能连接。
- 目标：页面输入六位配对码，通过 Daemon 已注入的设备管理 URL 发起配对，显示真实进度、成功及失败；手机控制与语音业务通道保持原样。
- 边界：不新增 Daemon 路由、不建立第二条设备业务连接、不把语音业务伪装成管理请求、不存储或回显配对码。
- 设计：Application 内独立连接适配器处理管理 HTTP、参数校验和响应脱敏；网页异步轮询连接状态；注入依赖便于隔离测试。
- 验收：无效码不发请求；配对 accepted 不等于 connected；拒绝跨站访问；错误不泄漏码或凭据；语音占用期间不发起配对；移动端能看到入口；重启本地应用后页面可用。
- 历史关联：延续 [Pi 安卓 MCP 接入](../../2026-10-17/2026-10-17_175918_Mr-KID-github_无Issue_Pi安卓MCP接入/index.md)，补足机器人连接体验，不替代其声学联测结论。
- TDD：待记录实际 Red / Green；实机配对需用户提供机器人屏幕上的有效临时码，不能以模拟验收冒充实机成功。

## 实际变更与验证

- 新增 `examples/joyinside_pi/connection.py`，仅访问 Daemon 注入的本机管理 URL；六位 ASCII 码校验，HTTP 错误转换为安全提示，状态响应仅保留连接状态、online、设备编号与安全错误枚举。
- `service.py` 新增连接查询及配对 API，沿用 Host/Origin 防护；配对与会话打开/清理互斥，重复提交不并发发起；开始对话检查实际 Daemon online。`app.py` 注入适配器。
- 页面主内容区增加表单，在移动端同样可见；真实异步连接状态、错误中文说明、离线开始禁用、已有连接或会话期间禁止配对。README 更新使用方式。
- Red：新增配对测试最初 17 项失败（模块/接口缺失）；补充离线启动测试在修改前实际失败（200 而非预期 409）。中途纠正测试自身 urllib Request 属性断言为 get_method，不把该测试错误当作产品故障。
- Green：`.venv/bin/python -m pytest tests/test_pi_speech_pairing.py tests/test_pi_speech_web.py tests/test_pi_speech_application.py tests/test_speech.py --tb=short`：55 passed，1 条既有 Starlette AnyIO 弃用警告。
- 覆盖管理 endpoint、参数与前导零、响应脱敏、错误隔离、无效码不出站、跨源拒绝、pending 与 connected 区分、会话占用和重复提交保护、离线开始拒绝。
- 前端由协作 agent 实现后验证（不宣称前端先写测试）：Node VM DOM 模拟验证输入、按钮状态、202 不误报连接、防重、失败重试、中文错误与会话互斥；node --check 和 git diff --check 通过。
- ego-browser 真实页面验收：5 位码提交禁用，6 位码可提交，清空后禁用；未发送虚构配对码。390px 移动视口表单可见，页面宽度与内容宽度均为 390，无横向溢出；已检查真实截图。
- 已通过原 Daemon 管理接口仅重启本 Application，管理状态查询正常；页面处于 paused，设备 offline。既有恢复连接失败显示为中文，引导使用新配对码。
- 未改 Daemon、固件或业务路由；未开启录音；未实测真实配对与声学链路。有效码必须来自用户当前机器人屏幕，不能以模拟测试报告实机成功。

## 用户实测后的连接失败定位

- 页面实测后，Daemon 返回实际错误 `device_connect_timeout`，日志两次记录 `Pairing UDP response accepted`。同一轮电脑与机器人在相同 IPv4 子网，ICMP 两次均收到响应；WebSocket 监听在所有 IPv4 地址，macOS 应用防火墙关闭。确认配对发现已成功，后续连接未完成；不能以“同一 Wi-Fi”解释全部原因，也不能仅凭设备 connection lost 断言 SDK 版本不匹配。
- 发现页面适配器遗漏两个真实 Daemon 错误枚举（device_connect_timeout / pairing_not_found），导致原错误被泛化并误导用户。先补2项实际失败阶段保留测试（均 Red），加入安全枚举后，配对专项测试27项通过；前端同样补了精确中文说明，协作 agent 的实际 Red / Green VM 断言通过。
- 已启动只观察连接状态与 TCP 元数据的短时采样，请用户重新打开 Desktop Link 并在页面重试；尚无新的握手证据。未关闭系统安全功能或改写业务路由，未更换固件。
- 提示文字修改已在磁盘，运行中的 Python 适配器仍需重启应用才能加载；为不打断用户当前重试，暂未重启。

## 连接握手可观测性补充

- 用户重新尝试后，第三次同样 UDP accepted 后 device_connect_timeout。既有 Daemon 日志只记录发现与最终授权成功，对 WebSocket 建立后 hello 被拒绝缺少证据，不能据此确认 SDK 或固件不兼容。
- 对照本机源码，双方 hello 均支持 pairing 1.0 / reunite 1.1，mode 为请求中约定值；这仅是源码合同检查，不能证明当前实物固件版本。
- 在 ExternalWebSocketServer 增加独立可选 event_logger，并由 Daemon 现有日志服务承接，仅记录连接来源、hello 等待超时、hello 拒绝错误码/关闭码；不记录首帧原文、会话 Token 或配对码，不改变授权与业务路由。日志回调失败不影响连接。
- TDD Red：新增实际 WebSocket 握手拒绝及不泄漏凭据测试，修改前因缺少 event_logger 失败；Green：接口补齐后外部 WS 与配对测试35项通过。另补日志失败仍可完成握手测试，并回归 Application 有无运行的路由合同。

- 握手日志补充及路由回归：47 passed，1 条既有警告；git diff --check 通过。已重新启动当前 SDK Daemon 与 Pi Application 加载诊断日志及精确错误显示，页面 paused / offline，等待用户实机重试；未宣称故障已修复。

## 重启后的真实连接结果

- 重新启动 Daemon 后，已有绑定的机器人自动完成 reunite，无需再次提交临时码。日志完整出现 UDP accepted → WebSocket connection opened → Device connected，Daemon online=true，Application 实际设备信息查询成功，页面 connected=true、paused。
- 新连接模式为 desktop_link，设备身份与此前语音联测一致；未更换 SDK 版本或烧录固件即可完成此连接，证明当前绑定设备与本机 SDK 可完成握手。此前手动配对使用 python_sdk，且先后网络接口/机器人地址发生变化，尚不能据此断言失败的确切根因或证明所有模式均已实测兼容。
- 当前仅恢复并验证设备连接，麦克风→Pi→TTS 仍未验收。新增日志与页面错误细分已加载，保留用于后续排查。

## 恢复连接后的语音握手复验

- 用户确认自动恢复的是手边机器人，随后开始语音，页面实际错误为语音连接 TimeoutError 和资源释放 TimeoutError，设备连接保持在线。
- 用户再次提供 Bot 凭据文件。按 Bot 凭据合同调用项目现有 acquire_token 与官方端点，鉴权通过；未输出 Token/AK/SK，也未改写机器人 NVS。该文件为 Bot 凭据，不是含 VendorId/ProductId 的注册配置，不能将注册工具字段缺失视为凭据失效。
- 在标准 Daemon 管理的临时诊断 Application 中，设备信息查询获得 sys.ack；当前实物 v0.5.8 / protocol 1.0，声明 speech.joyinside.v1。随后 ctrl.speech.open、ctrl.speech.close 均无 ACK/NACK/evt.speech 并超时。再次确认问题发生在设备语音命令响应阶段，未获得设备端云鉴权失败证据。
- 当前 ESP32 checkout 仅在能力表找到 speech.joyinside.v1，未检出 ctrl.speech 或专用 TTS 处理实现；这是源码检查及接口缺失的重要线索，不能当成已核验实际 Flash 二进制的证明。固件实现/功能启用应优先排查；机器人保存的 Bot 配置仍未读取确认。
- 当前仍未枚举机器人 USB 串口，无法读取串口身份/NVS 或验证固件日志；未烧录固件、未重注册或替换设备 Bot。诊断后恢复 Pi Application，默认待机。

## 后续变更

- 2026-10-18 13:47:00 Asia/Shanghai｜Mr-KID-github / Codex：补充[2026-10-18_134233_Mr-KID-github_无Issue_Pi断线后配对被清理状态阻塞](../2026-10-18_134233_Mr-KID-github_无Issue_Pi断线后配对被清理状态阻塞/index.md)。新一轮失败发生在Application清理门禁前，原Wi-Fi提示掩盖HTTP409；重启Application后同码配对成功。保留本档案先前UDP accepted后连接超时的历史结论，不以本次结果替代未知历史根因。


## 后续变更
2026-10-27｜KID / Codex｜[PCM配套合入](../../2026-10-27/2026-10-27_181300_KID_无Issue_PCM配套合入/index.md)：在最新目标分支整合并重新回归；原有硬件记录保留为历史。
