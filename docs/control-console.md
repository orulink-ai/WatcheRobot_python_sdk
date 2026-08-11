# 本地链路测试控制台

Python SDK Daemon 自带一个仅绑定本机控制端口的测试页面，用于开发和硬件联调时
恢复 ESP32 连接、查看管理面状态。该页面不承载语音等业务帧，也不改变
Daemon 的透明路由职责。

## 启动与访问

先启动当前源码或已安装 SDK 提供的 Daemon：

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
Start-Process "$($runtime.control_url)/control/"
```

默认地址是：

```text
http://127.0.0.1:8767/control/
```

控制页与管理 API 同源，因此不需要单独启动前端开发服务器，也不需要设置 API
地址。页面每两秒刷新一次状态，也可点击“立即刷新”。

## 重新配对

1. 在 ESP32 屏幕上取得当前六位数字配对码。
2. 在“六位配对码”中输入完整数字。
3. 点击“连接设备”或“重新配对”。
4. 等待 ESP32 卡片变为“已连接”，并确认模式是 `python_sdk`。

当设备槽仍处于旧状态时，页面会先执行以下安全收敛，再发起新配对：

- 已连接：调用 `POST /daemon/devices/disconnect` 释放旧设备会话；
- 正在发现或连接：调用 `POST /daemon/devices/pair/cancel` 取消旧尝试；
- 空闲：直接调用 `POST /daemon/devices/pair`。

配对请求固定使用：

```json
{
  "pairing_code": "六位数字",
  "target_mode": "python_sdk"
}
```

配对码只存在于当前表单和本次请求中。请求被接受后，页面会立即清空输入框，
不会写入 localStorage、sessionStorage 或 URL。

## 状态卡片

| 卡片 | 数据来源 | 含义 |
| --- | --- | --- |
| SDK Daemon | `GET /daemon/status` | 控制 REST 是否正常响应 |
| ESP32 Device | `GET /daemon/devices` | 设备槽状态、在线状态、目标模式和请求 ID |
| Application | `GET /daemon/status` | 当前选中 Application、运行状态和 PID |
| Qwen Gateway | 浏览器探测 `127.0.0.1:3101/api/health` | 仅判断本机 HTTP 端口是否可达 |

Qwen Gateway 默认拒绝跨端口页面读取健康响应。控制台使用 `no-cors` 探测，
因此只能显示“端口可达”或“不可达”，不能读取模型、Provider 或 Realtime 会话
详情。需要完整信息时在 PowerShell 中执行：

```powershell
Invoke-RestMethod http://127.0.0.1:3101/api/health |
  ConvertTo-Json -Depth 8
```

这一限制是 Gateway 的浏览器跨域保护，不应通过在 Daemon 内增加 Qwen 专属代理
来绕过。Daemon 只暴露通用管理状态，Application 专属健康信息仍由 Application
或其 Gateway 自己提供。

## 状态与操作关系

| 设备状态 | 页面呈现 | 可用操作 |
| --- | --- | --- |
| `idle` | 未连接 | 输入配对码并连接 |
| `discovering` / `connecting` / `accepted` | 配对中 | 取消配对，或输入新码替换尝试 |
| `connected` 且 `online=true` | 已连接 | 主动断开，或输入新码重新配对 |
| 携带 `last_error` | 连接异常 | 查看错误并输入设备当前新码 |

所有操作都属于 Daemon 管理面。Application 运行时的业务链路仍然是：

```text
ESP32 Device channel -> Daemon -> 当前 Application
当前 Application -> Daemon -> ESP32 Device channel
```

页面不会根据业务消息类型建立任何直达设备的旁路。

## 故障排查

- 页面无法打开：先运行 `watcherobot daemon status`，确认控制 URL 和 Daemon
  进程状态。
- Daemon 正常但设备不在线：确认电脑与 ESP32 在同一局域网，并重新获取设备
  当前显示的配对码。
- 配对一直处于发现中：点击“取消配对”，确认 Windows 防火墙允许 Daemon 的
  UDP 配对端口和 WebSocket 设备端口，再用新码重试。
- Application 未运行：测试页只展示状态，不会擅自启动或替换 Application；
  使用 CLI 或桌面管理面启动目标 Application。
- Qwen Gateway 不可达：确认 Gateway 正在监听 `127.0.0.1:3101`。Gateway
  可达不等价于 DashScope Realtime 上游已连接，完整状态以其 `/api/health`
  响应为准。
