# 端侧模型枚举与推理会话

适用于声明 `vision.models.v1`、`vision.inference.v1` 的整合固件。
Application 使用 Daemon 注入的 Device channel；没有增加业务路由旁路。

```python
# robot 为当前 Application 已经取得的 WatcheRobot 实例。
for model in robot.vision.models():
    print(model.model_id, model.name, model.task, model.verified)

with robot.vision.start_inference(2) as inference:
    for result in inference.results(poll_interval=0.1):
        print(result.model_id, result.sequence, result.boxes)
        if result.sequence > 100:
            break
```

`start_inference()` 返回 `InferenceSession`，离开其 `with` 块确认停止。
`robot.vision.stop_inference()` 停止此 SDK 实例持有的会话；`robot.close()` 也尝试清理。

## API 合同

- `models(timeout=10.0)` 只读取目录，不切换模型、不写入模型；返回 `tuple[VisionModel, ...]`。
- `start_inference(model_id, timeout=10.0)` 启动无 JPEG、无云台运动的端侧推理。
  启动前需停止占用相机的预览、拍照或跟随，当前版本尚未实现通用自动抢占。
- `session.latest(timeout=None)` 返回最新 `InferenceResult`，预热尚无结果时返回 `None`。
  重复读取可能得到同一序号；不要把查询次数作为推理帧率。
- `session.results(poll_interval=0.1)` 是拉取式迭代器，只输出变化的序号，不创建无限后台队列。
  消费者慢时跳过旧结果；退出迭代仍应关闭会话。
- `session.close(timeout=5.0)` 幂等。停止失败不将句柄标记为已关闭，可以重试。
  会话 ID 随启动请求发送，因此即使启动 ACK 丢失，也能尝试定向停止；清理仍失败时
  保留句柄，由 `stop_inference()` 或 `robot.close()` 再次清理。

`InferenceResult` 包含会话、模型、序号、设备时间戳、结果坐标空间宽高、任务和检测框。
`DetectionBox.x/y` 是中心坐标，`width/height` 是框尺寸，`score` 为 0～100，
`target` 是模型输出类别索引。它不同于旧 `FaceBox` 的左上角坐标语义。
当前四个已验证模型都是检测任务，结果坐标空间为 320×240；这不是正式视频分辨率。
分类结果不会被转换成检测框；当前解析器明确拒绝不支持的结果任务。

## 模型与能力边界

原厂人员、宠物、手势分别保持在 1～3 号，4 号是独立人脸检测。
`verified=True` 表示当前完整槽位 CRC 与已验证工件一致，并非模型准确率认证。
未知槽位仍能在目录中显示为 `Unverified model`，但通用推理拒绝运行，避免错误后处理。
原厂元数据自动解析、未知模型通用算子适配、推理调试 JPEG 不属于本接口已完成能力。

## 测试网页

`examples/sdk_media_lab` 增加 On-device Model Test：读取模型 → 选择模型 → 启动推理 →
读取最新结果 → 停止推理。页面按真实能力与相机占用启用按钮，结果 JSON 显示真实
模型及会话标识，未就绪显示预热状态。Application 退出及设备断线会清理其推理工作。

固件命令为 `ctrl.vision.models.get`、`ctrl.vision.inference.start/stop`、
`ctrl.vision.inference.result.get`。启动带 `model_id`、`session_id`，停止和读取带
`session_id`；旧会话停止不会停止新会话。错误结果包在 SDK 处被拒绝。
