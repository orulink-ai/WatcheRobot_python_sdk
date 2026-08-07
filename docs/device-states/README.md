# ESP32-S3 v0.3.4 设备行为状态目录

本文档列出 SDK Application 在 WatcheRobot ESP32-S3 **v0.3.4** 上可调用的行为状态 ID。

这是带版本边界的快照，不代表其他固件版本具有相同状态。以下 42 个 ID 已同时依据 v0.3.4 的两个目录核对，二者内容一致：

- 发布 SD 目录：`WatcheRobot_esp32/firmware/s3/release/v0.3.4/sdcard/watche/official/behavior/states.json`
- 固件 SPIFFS 回退目录：`WatcheRobot_esp32/firmware/s3/spiffs/behavior/states.json`

| 兼容项 | 值 |
| --- | --- |
| ESP32-S3 固件 | `v0.3.4` |
| 发布资源包 | `watcher-esp32-v0.3.4` / `res-2026.08.07.1` |
| 状态目录 schema | `1.0` |
| 默认状态 | `standby` |
| SDK 调用入口 | `app.robot.behavior.play(state_id, repeat=1)` |

## 通过 SDK 调用状态

行为状态是设备定义好的“动画、音效、运动和生命周期规则”的组合，不是任意字符串；也不等同于动画、动作、表情或灯光效果的资源 ID。

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        # 进入设备定义的“聆听”状态。
        job = await asyncio.to_thread(app.robot.behavior.play, "listening")

        # 循环状态需要在交互结束时显式替换或停止。
        await asyncio.to_thread(app.robot.behavior.stop)

        # 终止型状态可以等待对应 Job 完成。
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 10.0)


asyncio.run(main())
```

`behavior.play()` 发送 `ctrl.behavior.play`，在 `data.behavior_id` 中携带状态 ID；ESP32 按当前激活的状态目录校验。设备同一时刻只运行一个行为状态。返回 `Job` 用于观察生命周期；命令被接收不代表画面或动作已经播放完成。

| 需求 | SDK API | 不应替代为 |
| --- | --- | --- |
| 播放组合行为状态 | `app.robot.behavior.play("happy")` | `animation.play()` |
| 播放单个动画资源 | `app.robot.animation.play("smile")` | 行为状态 ID |
| 播放舵机动作 | `app.robot.motion.play_action("...")` | 行为状态 ID |
| 播放官方表情资源 | `app.robot.expressions.play_official("happy")` | 行为状态 ID |

### `ctrl.robot.state.set` 与 `evt.ai.status`

ESP32 v0.3.4 的 WebSocket 路由仍兼容 `ctrl.robot.state.set`，参数为 `data.state_id`。普通 SDK Application 应优先使用 `app.robot.behavior.play()`：它是公开 API，负责标准参数校验，并返回可等待的 `Job`。

`evt.ai.status` 是兼容/业务协议入口，不是 `app.robot` 高层方法。固件的文本映射为：Bluetooth/配对 → `bluetooth`；observing/tool-calling → `custom3`；listening → `listening`；thinking → `thinking`；processing/analyzing → `processing`；speaking → `speaking`；idle → `standby`；done/completed → `happy`；error/fail → `error`。只有自带完整原始业务协议的 Application 才应使用 `ApplicationChannels` 发送原始帧，不能把它当作绕过 SDK API 的常规方式。

## 状态目录

**生命周期**反映 v0.3.4 配置：`循环`表示直到被替换或停止才结束；`单次 → X` 表示绑定动画完成后自动进入 X；`保持`表示单次或固定展示，目录未定义后继状态。

### 核心交互与系统状态

| 状态 ID | 表达的设备状态 | 生命周期 |
| --- | --- | --- |
| `boot` | 开机提示（`happy` 动画、`boot` 音效） | 单次 |
| `standby` | 默认待机（带 `standby` 音效） | 循环 |
| `awake_idle` | 唤醒后的空闲状态 | 循环 |
| `listening_wake` | 进入聆听前的唤醒过渡 | 单次 → `listening`；失败 → `error` |
| `listening` | 聆听中 | 循环 |
| `thinking` | 思考中 | 循环 |
| `processing` | 处理中/分析中 | 循环 |
| `speaking` | 说话中 | 循环 |
| `photo` | 拍照反馈 | 保持 |
| `happy` | 正向完成反馈 | 单次 → `standby_loop` |
| `error` | 错误反馈 | 保持 |
| `network_unavailable` | 网络不可用反馈 | 单次 → `standby_entry` |
| `bluetooth` | 蓝牙或配对反馈 | 单次 |
| `disconnect` | 连接中断反馈 | 循环 |
| `upgrade` | 升级提示 | 循环 |

### 待机、睡眠与展示状态

| 状态 ID | 表达的设备状态 | 生命周期 |
| --- | --- | --- |
| `standby_entry` | 进入待机 | 单次 → `standby_loop`；失败 → `error` |
| `standby_loop` | 睡眠/待机循环 | 循环 |
| `standby_start` | 睡眠启动过渡 | 单次 → `standby_loop` |
| `standby_end` | 离开待机过渡 | 保持 |
| `music` | 音乐展示 | 循环 |
| `standby1` | 待机变体 1 | 循环 |
| `standby2` | 待机变体 2 | 循环 |
| `standby3` | 待机变体 3 | 循环 |
| `standby4` | 待机变体 4 | 循环 |
| `creator_mode` | Creator Mode 展示面 | 保持 |
| `desktop_expression_panel` | Desktop 表情面板展示面 | 循环 / 保持至替换 |

### 表情与交互反馈状态

| 状态 ID | 表达的设备状态 | 生命周期 |
| --- | --- | --- |
| `custom1` | 自定义表情 1 | 循环 |
| `custom2` | 自定义表情 2 | 循环 |
| `custom3` | 自定义表情 3 / 观察、工具调用提示 | 循环 |
| `shock` | 惊讶/震惊表情 | 循环 |
| `sunglasses` | 墨镜表情 | 循环 |
| `sad` | 难过表情 | 循环 |
| `get` | 收到/确认表情 | 循环 |
| `smile` | 微笑表情 | 循环 |
| `recharge` | 充电提示 | 保持 |
| `speechless` | 无语/疑问表情 | 循环 |
| `concentration` | 专注表情 | 循环 |
| `fondle_love` | 背部触摸的正向反馈 | 单次 → `awake_idle` |
| `fondle_anger` | 背部触摸的负向反馈 | 单次 → `awake_idle` |
| `blink` | 点击反馈 | 单次 → `awake_idle` |
| `agent_question` | Agent 提问反馈 | 单次 → `awake_idle` |
| `agent_error_feedback` | Agent 错误反馈 | 单次 → `awake_idle` |

## 选择建议

标准对话流程建议使用：

```text
awake_idle → listening → thinking 或 processing → speaking → happy 或 awake_idle
```

- 只有产品确实空闲时才使用 `standby` 或 `standby_loop`。
- 需要可见唤醒过渡时使用 `listening_wake`，它会自动进入 `listening`。
- `listening`、`thinking`、`processing`、`speaking` 和表情循环状态需要通过下一个状态或 `behavior.stop()` 显式替换。
- 对话完成优先使用 `happy`；故障反馈使用 `error` 或 `network_unavailable`。
- `boot`、`creator_mode`、`desktop_expression_panel` 属于系统或 Desktop 导向状态，除非 Application 明确拥有该流程，否则不建议作为常规业务状态。

## 版本与运行时检查

状态 ID 的校验发生在 ESP32；`app.robot.capabilities` 只报告媒体等能力域，不会枚举可用状态。固件和资源安装版本不匹配时，SDK 仍可能连接成功，但状态会被设备以 `not_found` 拒绝，或缺少该状态对应的显示资源。

在发布 Application 前：

1. 要求 ESP32-S3 固件为 `v0.3.4`，并安装匹配的 SD 资源包。
2. 在 Application 自己的兼容性表或适配层中集中维护状态 ID。
3. 处理命令拒绝，并降级到 `standby` 或 `error` 等安全状态。
4. 每次升级 ESP32 固件或 SD 资源时重新核对发布目录。

资源安装和 Creator 作品见[资源与作品说明](../resources.md)；SDK 命令见[完整 CLI 命令参考](../cli-reference.zh-CN.md)。
