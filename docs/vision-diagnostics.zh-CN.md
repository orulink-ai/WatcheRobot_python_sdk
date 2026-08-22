# 端侧视觉诊断

`robot.vision` 为 Application 提供与具体模型无关的视觉后端健康状态和能力查询。
Application 不需要预先知道设备正在运行 Himax PTL 图像桥接固件还是 SSCMA 推理固件，
即可在启动相机或推理流程前完成预检。

## 查询当前视觉后端

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        status = await asyncio.to_thread(app.robot.vision.status, timeout=5.0)
        app.logger.info(
            "backend=%s health=%s model=%s inference=%s preview=%s",
            status.backend,
            status.health,
            status.model.name if status.model else "unavailable",
            status.capabilities.inference,
            status.capabilities.preview,
        )


asyncio.run(main())
```

也可以使用以下便捷方法获取同一份状态快照中的信息：

- `robot.vision.health()`：返回完整的 `VisionStatus`；
- `robot.vision.active_model()`：返回 `VisionModel | None`；
- `robot.vision.capabilities()`：返回 `VisionCapabilities`。

设备固件必须声明 `vision.status.v1`。旧固件缺少该能力时，SDK 会明确报错，不会猜测
当前后端状态。

## 状态合同

`VisionStatus` 包含：

- 后端名称、健康状态、原始状态码、初始化状态和 Himax 连接状态；
- 当前是否正在传输图像或执行推理；
- 采图、预览、推理、模型信息和模型管理能力；
- 后端支持时，返回当前模型 ID、名称、任务类型以及是否包含 face 类。

这套 API 与模型类型无关。目标检测、姿态、手势和人脸模型都使用相同的状态合同。
但各类模型的推理结果仍由独立能力合同承载。例如
`robot.face_tracking.open_preview()` 返回人脸框和跟踪遥测，因此仍要求设备提供
`face_tracking.preview.v1`。

## 两种后端的预期行为

| 后端 | 采图 / 预览 | 推理 / 模型信息 | 说明 |
| --- | --- | --- | --- |
| `ptl` | 支持 | 不支持 | JPEG 传输固件，适合排查相机链路。 |
| `sscma` | 取决于固件状态 | 初始化后支持 | 返回当前 SSCMA 模型与推理健康状态。 |

当前两个后端的 `model_management` 均为 `False`。本阶段只开放模型元数据只读查询；
模型上传、替换与参数修改需要先完成授权、兼容性校验、回滚和固件恢复合同，暂不开放。

SSCMA 正在占用相机时，状态查询会返回 busy 快照，而不会争抢 Himax 传输链路。
Application 应使用有限退避稍后重试，并且不应绕过 Runtime 另建一条设备连接。

## 人脸跟踪预检

```python
status = app.robot.vision.status(timeout=5.0)
if not status.capabilities.inference:
    raise RuntimeError(f"{status.backend} does not expose device inference")
if status.model is not None and not status.model.contains_face_class:
    raise RuntimeError(f"active model {status.model.name!r} has no face class")

with app.robot.face_tracking.open_preview() as preview:
    frame = preview.read(timeout=5.0)
```

JPEG 与人脸框同帧配对合同见[人脸跟踪预览 API](face-tracking-preview.md)。
