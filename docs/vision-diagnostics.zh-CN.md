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

## 使用托管预览界面

SDK 测试台通过托管 Application Device channel
显示同帧配对的预览图像和人脸框：

```powershell
watcherobot app run ./examples/sdk_media_lab
```

在人脸跟踪面板选择 **Start with Preview**。独立的无界面预览示例已删除。
在自己的 Application 中，仍可使用本文介绍的 SDK API 查询视觉后端健康状态、
能力，以及自行接收预览帧。

## Vision Debug Lab 下线与迁移

`examples/vision_debug_lab` 因应用过时，已按维护者明确要求下线。
这是示例启动入口的有意删除，不是删除 SDK 的拍照或视觉 API。
SDK 测试台不具备旧工具的全部诊断流程。

| 原有流程 | 当前入口与限制 |
| --- | --- |
| 浏览器同帧人脸框预览 | `sdk_media_lab` 提供人脸预览，但不是原视觉诊断台。 |
| 同帧 JPEG 与遥测接收 | 在托管 Application 中使用 `robot.face_tracking.open_preview()`，见上文 API 示例。 |
| JPEG + JSONL 数据集录制 | 当前没有等价录制工具，需要 Application 通过预览 API 自行实现。 |
| 视觉专用延迟、丢帧报告及导出 | 当前没有等价报告导出；依赖该流程时应保留旧工具。 |
| 最后一个浏览器断开后自动 HOLD | Application 必须显式管理跟踪停止，见[生命周期合同](face-tracking-lifecycle.md)。 |

仍依赖旧工具完整流程时，可在隔离 checkout 中保留删除提交 `2e60466` 的父版本源码。
这只是旧版源码定位方式，不代表旧工具已通过当前固件验证。切换版本前，应单独保留已有数据集。
