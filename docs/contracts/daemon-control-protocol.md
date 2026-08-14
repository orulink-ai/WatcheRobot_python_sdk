# Daemon 控制协议

本文档定义 Desktop 与 SDK Daemon 之间的本机管理接口兼容合同。该接口只负责 Daemon、Application 和设备连接的生命周期管理，不承载或解析 Desktop、Application、Device 之间的业务帧。

## 协议版本 2

`GET /daemon/status` 在既有 `application` 字段之外，增加只读的 `runtime` 身份：

```json
{
  "runtime": {
    "control_protocol": 2,
    "sdk_version": "<watcherobot.__version__>"
  },
  "application": {}
}
```

- `control_protocol` 是管理接口合同版本，不是业务路由版本。
- `sdk_version` 来自 `watcherobot.__version__`，仅用于诊断随包 SDK 身份。
- 两个字段均为固定、非敏感元数据，不包含本机路径、环境变量、命令输出或 traceback。
- Desktop 复用已运行的 Daemon 前必须验证 `control_protocol`；缺失或不匹配时，不得把该进程当作当前随包 Runtime 使用。

协议版本 2 的唯一升级原因是增加上述显式身份握手。它不改变 Application 分发命令，不改变业务帧路由，也不新增 Application 日志读取接口。

## 兼容策略

| Desktop | Daemon | 行为 |
| --- | --- | --- |
| 旧版 Desktop | 协议 2 | `runtime` 是附加字段；按既有 JSON 宽松解析继续工作 |
| 协议 2 Desktop | 协议 2 Daemon | 身份校验通过，可以复用 Daemon |
| 协议 2 Desktop | 未声明或其他版本 Daemon | 身份校验失败；停止复用并按 Desktop 生命周期策略启动随包 Daemon |

## 启动失败与日志安全边界

`POST /daemon/application/start` 启动失败时只返回稳定的结构化错误：

```json
{
  "error": "application_start_failed",
  "message": "<稳定公开消息>"
}
```

Daemon 控制接口不得返回 Application 的原始 stdout/stderr、历史日志、启动命令、环境信息或 traceback。Application 日志仍可在 Daemon 内部持久化并通过既有运行时通道做受控转发，但不能作为启动失败 REST 响应或独立查询接口暴露给 Desktop。

`GET /daemon/logs` 是既有的 Daemon 自身结构化日志接口，不等同于 Application 进程输出。

