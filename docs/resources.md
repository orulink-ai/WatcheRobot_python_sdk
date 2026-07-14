# Factory resource IDs / 出厂资源 ID

Resource IDs are installed by the robot firmware; they are not uploaded by the Python SDK. The table below contains
safe examples confirmed against firmware `V3.1`. It is intentionally **not exhaustive** and is not a promise that
every internal firmware state is a public resource.

资源 ID 随机器人固件安装，不由 Python SDK 上传。下表是基于 `V3.1` 固件确认的安全示例，并非完整清单；
固件内部状态也不等于长期公开资源。

| Domain / 能力 | Confirmed IDs / 已确认 ID | Notes / 说明 |
|---|---|---|
| Behavior | `happy` | Used by `hello_robot.py` and the hardware smoke test / 已用于最小示例和真机验收 |
| Animation | `happy`, `smile` | Registered by the V3.1 animation registry / 已进入 V3.1 动画注册表 |
| Installed sound / 内置音效 | `happy` | Present in the V3.1 sound manifest; firmware variants may differ / 不同固件资源可能不同 |
| Light effect / 灯效 | `blink`, `breathing`, `rainbow`, `status_pulse` | Exact effect names accepted by the V3.1 executor / V3.1 执行器接受的准确名称 |
| Named motion / 命名动作 | No stable public factory ID yet / 暂无稳定公开 ID | `play_action()` is supported, but the installed action catalog is firmware-specific |

Example / 示例：

```python
with WatcheRobot.connect(pairing_code="123456") as robot:
    robot.behavior.play("happy").wait(timeout=20)
    robot.animation.play("smile").wait(timeout=10)
    robot.lights.play_effect("breathing", color="#4DA3FF", period_ms=500, repeat=3).wait(timeout=5)
```

`robot.capabilities` reports supported capability domains such as `behavior`, `motion`, and `camera.capture`; it does
not enumerate installed resource IDs in v1. Resource discovery is a known Alpha limitation.

`robot.capabilities` 只报告 `behavior`、`motion`、`camera.capture` 等能力域，v1 尚不能枚举机器人中安装的
具体资源 ID，这是当前 Alpha 版本的已知限制。

If a resource is absent, the device rejects the command with `not_found`. For example, do not assume that
`greeting` exists merely because it appears in a generic SDK example.

如果资源不存在，设备会返回 `not_found`。不要因为某个通用示例使用了 `greeting`，就假定当前固件一定包含它。
