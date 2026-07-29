# M0 Server Daemon 依赖清单（历史快照）

> 历史快照基线：`WatcheRobot_server@1ccd7a8`
>
> 记录用途：保留 M0 阶段识别 Server 基础设施耦合的证据，不描述当前 SDK
> 目录结构，也不作为新的迁移待办。
>
> 迁移结果：活跃 Daemon 已进入 `watcherobot.runtime.daemon`，SDK Runtime
> 生产代码不 import `server.src`；未接入的旧 Server Discovery 实现没有形成
> 第二个 Runtime Profile。

## 1. M0 发现的直接 `src.*` 依赖

在上述 Server 基线中，只有两个 `daemon/` 生产文件直接依赖 Server
基础设施。

### `daemon/discovery/lan_address.py`（基线文件）

| 当时依赖 | 用途 | 最终处理 |
|---|---|---|
| `src.utils.logger.get_logger` | 模块日志 | 未随未使用 Discovery 实现进入 SDK |
| `src.utils.process_encoding.decode_process_output` | 解码系统命令输出 | 未随未使用 Discovery 实现进入 SDK |
| `src.utils.subprocess_window.hidden_subprocess_kwargs` | Windows 隐藏子进程窗口 | 未随未使用 Discovery 实现进入 SDK |

### `daemon/discovery/server.py`（基线文件）

| 当时依赖 | 用途 | 最终处理 |
|---|---|---|
| `src.config.settings` | discovery/ws 端口、服务名、版本 | 未接入 SDK Runtime |
| `src.utils.logger.get_logger` | 模块日志 | 未接入 SDK Runtime |
| `src.utils.port_cleanup.*` | 地址占用检测、释放和等待 | 未接入 SDK Runtime |
| `src.utils.process_encoding.decode_process_output` | 系统命令输出 | 未接入 SDK Runtime |
| `src.utils.structured_log.log_event` | 结构化事件日志 | 未接入 SDK Runtime |
| `src.utils.subprocess_window.hidden_subprocess_kwargs` | Windows 隐藏窗口 | 未接入 SDK Runtime |

## 2. Runtime 基础依赖的落地结果

`pip install watcherobot` 的基础依赖已经包含 Runtime/Daemon 所需的
`websockets`、`fastapi`、`uvicorn`、`packaging` 和 `psutil`。
Runtime 没有拆成 `[runtime]` extra；开发与测试工具继续放在 `[test]`。

## 3. 入口解耦结果

M0 基线中的 `packaged_main.py` 同时承载 Daemon 与默认 Application
冻结入口。配套迁移完成后的边界为：

- Runtime 入口属于 SDK 的 `watcherobot` CLI 和
  `python -m watcherobot.runtime.daemon`。
- Application 入口固定为应用根目录 `app.py`。
- Server 默认业务使用公开 `watcherobot.application` API。
- Server 不应保留第二份 Daemon 或 `packaged_main.py --daemon` 入口。

## 4. 未使用 Discovery 实现的处理结果

M0 基线中的 `daemon/discovery/server.py` 没有被当时的 `DaemonRuntime`
实例化；活跃配对入口已经是 `PairingUdpService`。迁移时没有将该未使用
Discovery 实现接入 SDK，也没有保留它的 `DISCOVER/ANNOUNCE` Profile。

当前 SDK Runtime 的唯一设备配对实现位于：

```text
src/watcherobot/runtime/daemon/pairing/
```

其合同是 `watcher-lan-pairing/1.0`，详见
[`runtime-profile-index.md`](../contracts/runtime-profile-index.md)。

## 5. 当前验收查询

以下查询应保持无结果：

```text
rg "^(from|import) src\b" src/watcherobot/runtime
```

旧 Server 基线文件名仅允许出现在本历史记录中，不代表 SDK 仍持有这些实现。
