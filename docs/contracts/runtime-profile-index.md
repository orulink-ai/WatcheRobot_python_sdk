# Watcher Runtime 合同索引

> 基线：Server `1ccd7a8`，SDK `3fbf2d0`
> 状态：M0 冻结合同；目标 Runtime 只实现 `watcher_lan_pairing_v1`

## 1. 唯一目标 Profile

| 标识 | 值 |
|---|---|
| 内部名称 | `watcher_lan_pairing_v1` |
| UDP 协议名 | `watcher-lan-pairing` |
| UDP/hello 版本 | `1.0` |
| 目标模式 | `desktop_link`、`python_sdk`（由发起配对的应用显式选择） |
| 配对 UDP 端口 | `37021` |
| 设备 WebSocket 端口 | `8765` |
| Runtime 控制 REST | `127.0.0.1:8767` |
| Application 本地桥 | `127.0.0.1` 动态端口 |

`8766` 是当前默认 Application 的业务 HTTP 端口，不属于 Runtime 控制面。旧 SDK 的 `SDK_DISCOVER`、`service=watcher-sdk`、六位码 WebSocket hello/ready 和 `sdk_control` 不属于目标 Profile。

## 2. 配对顺序

```text
Runtime                           Device
   |                                |
   |-- UDP pair.request ----------->|
   |<--------- UDP pair.accept -----|
   |                                |
   |<------ WebSocket connect ------|
   |<------ sys.client.hello -------|
   |------- sys.ack ---------------->|
   |                                |
   |<====== 业务文本/WSPK =========>|
```

### `pair.request`

- Runtime 为每个启用且具备广播语义的本机 IPv4 创建独立 UDP 通道；通道绑定
  该本机 IPv4，并只向由该地址和子网掩码计算出的定向广播地址发送 UDP
  `37021`。
- Runtime 不使用绑定 `0.0.0.0` 的共享发送 socket，也不发送
  `255.255.255.255` 有限广播；不同网卡即使计算出相同广播地址也不会跨网卡
  去重。
- 开始配对时立即刷新通道，并在默认 `1000 ms` 广播循环的每一轮重新比较网卡
  快照。网卡启停、插拔或 DHCP 地址变化会新增、关闭或替换通道，不要求重启
  Runtime。
- 单个网卡绑定或发送失败只关闭该网卡通道，其他网卡继续查找；如果启动时存在
  可用接口但所有接口都无法绑定，则 Runtime 启动失败，避免端口冲突被静默忽略。
- `request_id` 和 `daemon_instance_id` 均为 32 位小写十六进制。
- `pairing_code` 为六位数字。
- `websocket_port` 是设备应连接的 Runtime 外部 WebSocket 端口。

### `pair.accept`

- 必须匹配当前 `request_id`、`daemon_instance_id` 和 `target_mode`。
- `session_token` 为 64 位小写十六进制。
- Runtime 锁定首个有效响应的对端 IP，后续 hello 必须来自同一 IP。
- Runtime 同时记录收到响应的本机 IPv4 和网卡名称；需要取消尚未完成的配对时，
  `pair.cancel` 沿该通道单播返回，不由系统重新选择其他出口。

### 设备 hello

首帧必须是文本 `sys.client.hello`，`role=hardware`。hello 只包含当前内存配对会话字段，不包含 `device_id`、`mac`、`capabilities` 或 `pairing_code`。

校验通过后 Runtime 返回 `sys.ack`，其中固定声明当前音频上行协商：

- codec：`opus`
- sample rate：`16000`
- channels：`1`
- frame duration：`60ms`
- packetization：`one_opus_packet_per_wspk`
- version：`1`

校验失败返回 `sys.nack`，并关闭连接。Application 不参与设备 hello。

### 麦克风 Opus payload 职责

Runtime/Daemon 负责设备连接、WSPK 帧边界和按来源路由，但在 Application 运行时必须将设备
音频 payload 原样交给 Application Device channel；它不解码、不转码，也不依据 Opus 内容做业务
分支。`ApplicationContext.robot.microphone.open_pcm()` / `record_pcm()` 在 SDK 的 Application
媒体层把每个完整 Opus 包解码为 16 kHz、单声道、`pcm_s16le`。只有显式的 `open_pcm()` /
`record_pcm()` 提供 PCM；`open()` 保留原始 Opus 包流，而 `record()` 会明确拒绝压缩包录制，
避免把 Opus 字节伪装成 PCM WAV。

因此，桌面安装包需要随 Daemon 和默认 Application 一起提供完整共享 `watcherobot` 环境；桌面 UI
本身仍只调用 Daemon 控制面。需要原始帧的高级 Application 直接使用 `ApplicationChannels` 的
Device channel。

## 3. 配对状态机

```text
idle
  -> discovering
  -> connecting
  -> connected
  -> reconnecting
  -> connected
```

- `discovering` 默认超时 10 秒，错误为 `pairing_not_found`。
- `connecting` 默认超时 10 秒，错误为 `device_connect_timeout`。
- `reconnecting` 默认超时 30 秒，错误为 `reconnect_timeout`。
- 已连接设备异常断开时保留当前会话凭证并进入 `reconnecting`。
- 设备发送 `sys.device.session.end`，且 `pair_request_id` 匹配时，正常释放为 `idle`。
- Application 启停或切换不能重建设备连接。

## 4. 外部角色与路由

外部 WebSocket 只接受：

- `desktop`
- `hardware`

Application 不是第三个外部角色。Runtime 启动 Application 子进程后，通过本机动态端口提供两个受运行凭证保护的通道：

- `ApplicationChannel.DESKTOP`
- `ApplicationChannel.DEVICE`

路由规则：

| 来源 | Application 运行中 | Application 未运行 |
|---|---|---|
| 外部 Desktop | 发给 Application Desktop channel | 直接发给 Device |
| 外部 Device | 发给 Application Device channel | 直接发给 Desktop |
| Application Desktop channel | 发给外部 Desktop | 拒绝 |
| Application Device channel | 发给外部 Device | 拒绝 |

Application 到 Device 的路由不要求外部 Desktop 在线。

Daemon 不按业务消息 `type` 设置保留路由或直达设备的例外。包括
`ctrl.microphone.open` 和 `ctrl.microphone.close` 在内的 Desktop 业务帧，
Application 运行时都透明交给当前 Application；由 Application 决定业务处理，
并通过 Device channel 向设备发送硬件指令。只有 Application 未运行时，
Desktop 业务帧才由 Daemon 直接转发给 Device。

## 5. Daemon 日志合同

- `GET /daemon/logs?after_id=<id>` 返回当前 Runtime 会话中 ID 大于
  `after_id` 的 Daemon 日志。
- 响应格式为 `{"logs":[{"id":...,"message":"...","timestamp_ms":...}]}`。
- Daemon 自己维护最多 500 条近期记录，并追加保存到运行状态目录下的
  `logs/daemon.jsonl`。
- 该接口不依赖桌面端持有 Daemon 子进程 stdout，因此桌面接管一个已独立运行的
  Daemon 后仍可读取实时日志。

## 6. WSPK 二进制合同

当前主格式为 16 字节小端头：

```text
<4sBBHII
magic(4) + type(1) + flags(1) + stream_id(2) + sequence(4) + payload_len(4)
```

- magic：`WSPK`
- `stream_id`：`uint16`
- `sequence`：`uint32`
- payload 长度必须与帧总长度一致

现有领域编解码还能够读取 14 字节旧头 `<4sBBII>`，其 `stream_id=0`。这属于设备二进制帧兼容行为，不等于保留旧 SDK 直连 Profile；M0 只冻结现状，后续迁移不得无测试改变。

权威向量位于：

- `tests/fixtures/contracts/watcher_lan_pairing_v1.json`
- `tests/fixtures/contracts/wspk_v1.json`

## 7. 断线与生命周期

- Device 断线：设备状态进入 `reconnecting`，不重启 Application。
- Application 任一必需本地通道异常断开：当前 Application 进入错误并清理进程树，不自动重连或自动重启。
- Application 正常停止：运行凭证失效，两个本地通道关闭；设备连接继续由 Runtime 持有。
- Runtime 停止：先停止控制面和 Application，再停止配对 UDP 与外部 WebSocket。

## 8. 当前实现边界说明

旧 Server 的 `daemon/discovery/`、`DISCOVER/ANNOUNCE`、UDP `37020` 和
`protocol_version=0.1.6` 已随 Server 直连入口一起删除。SDK 中唯一的活跃
Runtime 使用 `PairingUdpService` 和 `watcher-lan-pairing/1.0`；旧发现协议不属于
目标 Profile，也不会通过兼容分支重新接入。
