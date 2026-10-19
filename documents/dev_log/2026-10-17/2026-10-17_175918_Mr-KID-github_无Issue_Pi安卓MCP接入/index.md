# JoyInside 本地 Pi 接入安卓 MCP

## 基本信息

- 创建时间：2026-10-17 17:59:18，Asia/Shanghai。
- 建档作者：Mr-KID-github；参与者：用户 / Codex；无 Issue。
- 范围：检查现有 Pi 安装，配置用户指定的安卓 MCP，验证连接和可逆基本控制，确定 JoyInside Pi Application 的最小接入方式。
- 当前状态：本机 Pi 配置与文字驱动真机控制验证已完成；声学联测发现设备语音握手无 ACK，正在排障。Application 错误显示与暂停状态修复已本地验证；未提交、未推送。
- SDK 当前 HEAD：40026e3；工作于 detached HEAD，开始时 SDK 工作区干净。
- 约束：个人地址与 Token 不进入 Git；不更改 Daemon 路由；不调用发送消息、支付、删除等操作。

## 调查与最小方案

- 现有 Application：`examples/joyinside_pi/agent.py` 启动 Pi RPC，`service.py` 负责 ASR → Pi → TTS，最终完成依赖 `agent_settled`。
- 本机 Pi 安装于 Homebrew，版本 1.3.0。读取安装包内 `docs/mcp.md`、CLI 帮助及源码合同，确认内置 stdio / Streamable HTTP MCP，无需安装第三方适配扩展。
- 当前执行环境 PATH 未包含 Homebrew bin，裸 `pi` 查找失败；Pi 绝对路径可以运行。Application 可通过已有 `PI_COMMAND_JSON` 指向真实 Node + Pi CLI，并保留 RPC 参数，无需为本机路径修改源码。
- Pi 用户级配置放在仓库外；本机 endpoint / 凭据仅保存在权限受限的用户配置中。
- Application 的机器人语音仍经 Daemon 注入 Device channel；Pi 独立连接安卓 HTTP MCP。无需引入 Watcher Server 的 MCP 模块。

## 验证

- 直接协议探测：initialize HTTP 200、initialized HTTP 202、tools/list HTTP 200。
- 手机返回 `android-remote-control-mcp` 1.12.0，共 23 个工具，无分页游标。
- 已确认工具包含读取界面、启动应用、Home / Back、节点点击、输入、滚动和手势。
- Pi 配置前：`pi mcp list --json` 返回空服务列表。配置后：用户级 `watcher_android` 显示 connected，23 tools，errors 为空，退出码 0。
- 首次模型探测：默认 OpenAI 模型返回 HTTP 401；仅说明当前终端凭据不适用于该默认端点，不归因于安卓 MCP。临时使用终端代理地址探测亦返回 Connection error，未将其保存为 Pi 模型配置。
- 用户明确选择 DeepSeek Flash 并提供凭据。官方 `/models` 返回 HTTP 200，模型列表包含 `deepseek-flash` 和 `deepseek-v4-pro`；Pi 本机凭据存储与默认模型配置完成，`pi auth check --provider deepseek --model deepseek-flash --json --no-refresh` 返回 ready。默认推理级别 low。
- 使用 Pi 1.3.0 原生 MCP、DeepSeek Flash 和本仓库原有 `PiAgent`，通过已有 `PI_COMMAND_JSON` 注入 Node + Pi CLI argv，保持 RPC 和无持久化会话；测试会话仅启用必要安卓工具，禁用内置文件 / shell 工具。
- 第一轮：Pi 从主屏幕打开系统设置；独立 MCP 读取确认 focused application 为 `com.android.settings`。
- 第二轮：Pi 点击设置搜索入口，输入 `watcher-mcp-test`；独立读取确认 focused application 为 `com.android.settings.intelligence`，界面中真实存在测试文字。Pi 报告首次英文输入被中文输入法当作拼音，切换英文后完成；已验证最终结果，不将首次尝试描述成一次成功。
- 第三轮：Pi 清空测试文字、Back、Home；独立读取确认 focused application 恢复为 `com.sec.android.app.launcher`，测试文字不存在。真机断言全部通过，探测进程正常退出。
- `./.venv/bin/python -m pytest tests/test_pi_speech_application.py tests/test_pi_speech_web.py --tb=short`：6 passed，1 条既有 Starlette / AnyIO 弃用警告；验证连续对话、取消、RPC 完成语义等既有适配合同。
- 尚未切换现有运行的机器人 Application，未执行真实麦克风 → 手机控制 → TTS 验收，也未宣称验证长期稳定性或所有手机应用。
- 本轮优先使用 Pi 现有功能，无生产代码改动，逻辑 TDD 暂不适用；替代验证为修改前后配置诊断、真机工具调用和既有适配回归。若发现必须修改源码，另补真实 Red / Green。

## 实际交付与后续接入

- 本机仓库外配置：Pi 用户级 `mcp.json`、`auth.json`、`settings.json`；MCP Token 与模型密钥均留在本机用户配置，文件权限 0600。
- 初始接入仅更新示例说明和本档案；随后声学联测修复 [ConversationService](../../../../examples/joyinside_pi/service.py) 与对应测试，详情见下方故障调查。依赖、系统脚本、Daemon 路由和固件保持不变。
- 最小接入复用现有 Application 和 Pi 原生 MCP；不需要写 Android MCP 插件，也不需要绕经官方默认 Application。
- 下一步语音联测前，确认启动目标 Daemon 的环境能够找到 Pi，或通过已有 `PI_COMMAND_JSON` 提供绝对 argv；环境变量需由 Daemon 启动进程继承。不要为此停止其他正在运行的 Application。
- 单次语音轮次当前 Pi 默认超时 120 秒；先使用短任务。英文输入受手机当前输入法状态影响，模型需读取界面确认文本，不能只依据输入 API 成功就宣称完成。

## 历史关联

- 参考 [Pi 连续语音可视化](../../2026-10-14/2026-10-14_010000_KID_无Issue_Pi连续语音可视化/index.md)。本轮验证手机工具接入，暂不更改其语音与生命周期设计。

## 变更记录

- 2026-10-17 17:59:18，Asia/Shanghai｜Mr-KID-github / Codex：建档，记录当前安装与手机协议探测证据；未保存密钥、私人地址或屏幕正文。
- 2026-10-17，Asia/Shanghai｜Mr-KID-github / Codex：完成 Pi 原生 MCP 和 DeepSeek Flash 本机配置；原有 PiAgent 三轮真机控制、独立界面核对和 6 项回归通过。补充最小接入说明；仅本地文档改动，未提交或推送，声学整链路仍待验。
- 2026-10-17 18:15，Asia/Shanghai｜Mr-KID-github / Codex：用户要求启动语音 Application 亲自测试。先确认现有 SDK Daemon 0.1.12、设备 online，以及 Daemon 环境可解析 Pi / Node；通过标准管理接口停止原说话动作实验室，使用本地启动授权选择并启动 `com.orulink.joyinside_pi`。复用原 Daemon 与 Device channel，没有重启 Daemon 或建立第二条机器人业务连接。运行状态 running，网页已生成并打开供用户开始对话；未替用户录入语音或声称声学整链路验收通过。本机进程、端口与地址保留在运行时日志，不写入 Git。

## 语音启动无响应调查与修复

- 用户点击开始后页面长时间处于 connecting，随后截图显示 `speech session already open or closing`；应用日志先记录语音资源释放 TimeoutError，之后新一轮进入 SDK 旧会话保护。
- 初始根因与连锁故障分开：同一注入 Device channel 的设备信息查询获得 sys.ack，但 `ctrl.speech.open` 和随后 `ctrl.speech.close` 都无 sys.ack / sys.nack / evt.speech，分别命令超时。诊断使用忽略目录内的临时、Daemon 管理的 Application，没有建立旁路设备连接。
- 设备实际上报 firmware_version v0.5.8、protocol_version 1.0，并广告 speech.joyinside.v1。当前 ESP32 checkout 0ae6a618 的 capability 列表有该项，但未查到 ctrl.speech 处理器；这里只能确认设备握手未响应与源码不完整，不能据此声称已验证某个云端鉴权或麦克风硬件根因。
- Application 后续缺陷：所有阶段 TimeoutError 都被当成静音吞掉；close 错误覆盖首个故障；idle 重置 error phase；打开会话时暂停误报资源已释放。SDK 保留未确认 closed 的 session 是合理保护，未移除此机制。
- 修复：只保留 recognize 阶段原有静音重试；open / Pi / say 错误显示具体阶段并停止自动重试；清理错误附加显示；idle 保留 error；opening 期间暂停显示 stopping；空消息异常以异常类型兜底。
- TDD Red：新增5项用例时原实现 2 passed / 5 failed；补充空消息超时的4项用例，修复前4 failed。
- Green：`.venv/bin/python -m pytest tests/test_pi_speech_web.py tests/test_pi_speech_application.py tests/test_speech.py --tb=short -q`，30 passed，1 条既有 Starlette / AnyIO 弃用警告。`git diff --check` 通过。
- 维持边界：SDK recognize 仍未区分命令 ACK 超时与结果静音超时，Application 本轮沿用其既有 recognize 策略；open/close 期间仍可能等待 SDK 原定超时。页面修复不等于恢复真实语音能力。
- 用户表示已接 USB 后，pyserial 列表、IORegistry 和 system_profiler 均未识别机器人 USB 设备；已请用户确认数据线和连接电脑，串口证据尚待采集。没有烧录、重置或修改固件。
- 已停止临时诊断 Application，重新启动修复后的 JoyInside Pi Application 并保持待机，等待设备侧日志定位；网页运行地址仅保存在本机日志及对话。

## CH342 驱动排查与本机安装

- 用户明确要求安装 CH342 驱动。安装前未发现 WCH 应用、安装包收据或已注册的 WCH 系统扩展。
- 从 WCH 官方地址下载 CH341SER_MAC 2.1（官网标注 2026-08-31），检查 pkg 的 Apple Developer ID Installer / 公证以及 DMG 内应用签名、公证与 universal x86_64 / arm64 架构。使用官方 DMG 的原生应用路径，不安装 Rosetta，不降低系统安全设置。
- 应用复制至系统应用目录，安装按钮返回 success；驱动扩展 cn.wch.CH34xVCPDriver 已注册。首次等待用户批准，后续系统检查仍为 activated disabled；已打开系统设置对应扩展详情，请用户完成开关与系统认证。不能将安装应用或 success 对话框等同于驱动已启用。
- 本轮新出现的 USB CDC 串口设备识别为 SAMSUNG_Android（VID 04E8），不是 CH342。尚未识别机器人的 WCH 双串口，不将此手机串口当成机器人连接成功。
- 这些是本机部署操作，无生产源码或固件修改；安装验收以签名、系统扩展状态及硬件枚举为准，最终语音握手恢复仍需设备日志与实机验证。
- 用户再次完成系统授权后，复核 WCH 驱动 cn.wch.CH34xVCPDriver 为 activated enabled（enabled / active 均已标记），官方驱动本机安装与启用完成。此时 USB / 串口清单仍只有三星手机、集线器及转接器，未发现 WCH 芯片或机器人双串口；驱动启用成功不等于机器人数据链路已接入，尚不能采集其串口日志。

## 与既往烧录记录交叉核对

- 用户引用“打开最新国际版客户端”对话后，读取其最近烧录任务并核对本机忽略日志：同日 14:29 的既往任务确实完成 ESP32-S3 应用镜像写入、Hash of data verified、JoyInside 注册及重启持久化检查。不能把当前缺少串口解释为之前未成功烧录。
- 既往通过 USB 双串口中的 ESP32 通道完成烧录；当前该双串口均不存在，USB 树也未枚举机器人桥接芯片。当前唯一 USB CDC 串口属于三星手机。WCH 扩展复核为 activated enabled，不能继续归因为尚未启用驱动。
- 既往烧录日志的设备身份与本轮语音应用曾连接的设备身份不同；尚未确定是另一台实物还是身份配置发生变化，已请用户核对，不据此断言硬件被替换。
- 最新 Daemon 设备状态为 offline / reconnect_timeout；应用保持 paused。当前只能确认 USB 设备未枚举且网络连接已断开，尚无法从现有证据区分供电、线缆、接口或其他硬件链路原因。
- 本轮为只读排障，不适用新增代码 TDD；替代验证为历史烧录校验记录、systemextensionsctl、pyserial、IORegistry 与 Daemon 管理状态交叉检查。未再烧录或重置设备。

## 后续变更

- 2026-10-18｜Mr-KID-github / Codex：补充 [Pi 语音页面配对入口](../../2026-10-18/2026-10-18_013000_Mr-KID-github_无Issue_Pi语音页面配对/index.md)，通过既有 Daemon 管理接口配对，未改变语音业务路由，也未替代本档案声学链路尚未恢复的结论。
