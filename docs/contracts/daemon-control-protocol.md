# Daemon 控制协议

本文档定义 Desktop 与 SDK Daemon 之间的本机管理接口兼容合同。该接口只负责 Daemon、Application 和设备连接的生命周期管理，不承载或解析 Desktop、Application、Device 之间的业务帧。

## 协议版本 3

`GET /daemon/status` 在既有 `application` 字段之外，增加只读的 `runtime` 身份：

```json
{
  "runtime": {
    "control_protocol": 3,
    "sdk_version": "<watcherobot.__version__>",
    "instance_group": "default",
    "instance_id": "sha256:<协调目录身份摘要>",
    "external_url": "ws://127.0.0.1:8765",
    "pid": 1234,
    "started_at": 1788422400.0
  },
  "application": {}
}
```

- `control_protocol` 是管理接口合同版本，不是业务路由版本。
- `sdk_version` 来自 `watcherobot.__version__`，仅用于诊断随包 SDK 身份。
- `instance_group` 用于区分默认协调组和显式隔离组；默认启动器不得复用 `isolated` 实例。
- `instance_id` 是协调目录规范化路径的 SHA-256 摘要，不暴露本机绝对路径；启动器必须精确验证，禁止不同隔离目录交叉复用。
- `external_url` 是 Daemon 实际监听的业务通道地址，发现方不得根据本地配置自行猜测端口。
- `pid` 与 `started_at` 让无法读取状态文件的同机启动器仍可构造真实的运行状态。
- 这些字段均为非敏感元数据，不包含本机路径、环境变量、命令输出或 traceback。
- Desktop 复用已运行的 Daemon 前必须验证 `control_protocol`；缺失或不匹配时，不得把该进程当作当前随包 Runtime 使用。

协议版本 3 将上述身份与发现元数据定义为必填合同。缺少任何字段或协议版本不匹配时均不得复用。它不改变 Application 分发命令，不改变业务帧路由，也不新增 Application 日志读取接口。

仅用于测试或嵌入调用、且未提供 `runtime_metadata` 的 `DaemonControlAPI` 会明确声明兼容协议 2；它不是可发现的协议 3 生产 Daemon。正式 `DaemonRuntime` 始终注入完整元数据并声明协议 3。

## 兼容策略

| Desktop | Daemon | 行为 |
| --- | --- | --- |
| 协议 3 Desktop/SDK | 协议 3 Daemon | 完整身份校验通过，可以复用 Daemon |
| 协议 3 Desktop/SDK | 协议 2 Daemon | 明确提示停止或重启旧 Daemon，不继续启动第二个实例 |
| 协议 3 Desktop/SDK | 未声明或其他版本 Daemon | 明确提示版本不兼容，不猜测端点、不继续抢锁 |
| 旧版 Desktop/SDK | 协议 3 Daemon | 旧启动器无法验证新合同；升级后应重启 Daemon，并统一更新启动方 |

SDK 启动器通过固定管理端点恢复发现时要求协议版本匹配、`instance_group=default`、`instance_id` 匹配，并直接采用
Daemon 返回的 `external_url`。缺少这些元数据时不得猜测端口，也不得把隔离实例当作默认实例复用。

默认组兼容迁移锁始终在共享协调锁之后获取；所有启动器必须先获取共享协调锁，再获取旧状态目录锁，避免不同私有状态目录之间形成环形等待。

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

## Application 原子重启与优雅退出

`POST /daemon/application/restart` 由 Daemon 作为一次生命周期操作执行当前 Application 的停止和重新启动。调用方不得自行拼接两次独立的 `stop` / `start` 请求，以免在页面断开、并发操作或媒体资源尚未释放时留下半完成状态。

Daemon 每次启动 Application 时会注入仅对当前运行实例有效的停止信号。Application 可通过 `ApplicationContext.shutdown_requested` 轮询该信号，并在返回前关闭麦克风、播放器和外部会话。Daemon 会在保持 Desktop channel 与 Device channel 可用的情况下等待优雅退出；等待超过受限窗口后仍会回收 Application 进程树，避免停止操作无限阻塞。

该停止信号只用于进程生命周期协调，不是业务消息，不改变 Desktop、Application 与 Device 之间的业务帧路由。

