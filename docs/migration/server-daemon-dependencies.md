# Server Daemon 依赖剥离清单

> 基线：`WatcheRobot_server@1ccd7a8`
> 目标：迁入 `watcherobot.runtime.daemon` 后，生产代码不 import `server.src`

## 1. 直接 `src.*` 依赖

当前只有两个 `daemon/` 生产文件直接依赖 Server 基础设施。

### `daemon/discovery/lan_address.py`

| 当前依赖 | 用途 | SDK Runtime 归属 |
|---|---|---|
| `src.utils.logger.get_logger` | 模块日志 | Python `logging` 的 Runtime 日志适配 |
| `src.utils.process_encoding.decode_process_output` | 解码系统命令输出 | `watcherobot.runtime.platform.process` |
| `src.utils.subprocess_window.hidden_subprocess_kwargs` | Windows 隐藏子进程窗口 | `watcherobot.runtime.platform.process` |

### `daemon/discovery/server.py`

| 当前依赖 | 用途 | SDK Runtime 归属 |
|---|---|---|
| `src.config.settings` | discovery/ws 端口、服务名、版本 | 显式 `RuntimeSettings`，不读取 Server 配置 |
| `src.utils.logger.get_logger` | 模块日志 | Runtime 日志适配 |
| `src.utils.port_cleanup.*` | 地址占用检测、释放和等待 | `watcherobot.runtime.platform.ports` |
| `src.utils.process_encoding.decode_process_output` | 系统命令输出 | `watcherobot.runtime.platform.process` |
| `src.utils.structured_log.log_event` | 结构化事件日志 | `watcherobot.runtime.logging.log_event` |
| `src.utils.subprocess_window.hidden_subprocess_kwargs` | Windows 隐藏窗口 | `watcherobot.runtime.platform.process` |

## 2. 第三方运行依赖

迁入完整 Daemon 后，`pip install watcherobot` 基础包至少需要：

- `websockets`
- `fastapi`
- `uvicorn`
- `pydantic`
- `psutil`

这些是 D9 所定义的 Runtime 基础能力，不能只放进 `[dev]` 或 `[runtime]` extra。

## 3. 入口耦合

Server 的 `packaged_main.py` 当前把 Daemon 和默认 Application 冻结入口放在同一程序中。迁移后：

- Runtime 入口属于 SDK 的 `watcherobot` CLI。
- Application 入口固定为应用根目录 `app.py`。
- Server 不保留 `--daemon` 分支或导入 SDK Runtime 内部实现。
- `watcher_default` 只使用公开 `watcherobot.application` API。

## 4. 当前未接入的目录

`daemon/discovery/server.py` 虽然位于 Daemon 目录，但当前 `DaemonRuntime` 没有实例化 `DiscoveryServer`；活跃配对入口是 `daemon.pairing.udp.PairingUdpService`。

迁移要求：

1. 按已确认范围迁移该目录和测试，避免遗漏代码所有权。
2. 不因物理迁移而自动把 `DiscoveryServer` 接入生产启动链。
3. 在后续阶段根据调用链和 Profile 合同决定删除未使用实现或以测试证明其职责；不得形成第二个 UDP 设备 Profile。

## 5. 迁移验收查询

SDK Runtime 完成迁移后必须满足：

```text
rg "^(from|import) src\b" src/watcherobot/runtime
```

无结果；同时 Server 全局不存在第二份 `DaemonRuntime`、外部 WebSocket 所有者或 Application 进程监督实现。
