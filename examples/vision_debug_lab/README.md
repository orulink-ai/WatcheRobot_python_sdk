# Vision Debug Lab

Vision Debug Lab 是一个由 SDK Runtime 管理的本机视觉调试 Application。它用于确认
Himax 视觉后端、当前模型和人脸追踪链路是否可用，并把同序号 JPEG、检测框和跟踪遥测
显示在同一画面上。

当前公开版本已在 Windows 上完成端到端实机验收，因此 Marketplace Manifest 仅声明
`windows`。macOS 在完成对应 Runtime、浏览器和机器人实机验收后再单独开放，不能仅根据
Python 源码可移植性推断兼容。

## 启动

先确认设备已由 SDK Runtime 管理并处于在线状态，再从 SDK 仓库运行：

```powershell
watcherobot app run .\examples\vision_debug_lab
```

```sh
watcherobot app run ./examples/vision_debug_lab
```

Application 会在随机端口启动只监听 `127.0.0.1` 的网页，并默认打开浏览器。

## 第一版能力

- 查询设备连接、视觉后端、Himax、当前模型和能力健康状态；
- 启动 240×240、416×416 或 640×480 的人脸追踪预览；
- 在同一 JPEG 上显示人脸框、目标、中心向量、参考死区、误差和云台速度；
- 统计 FPS、帧间隔 P95、帧年龄 P95、推理耗时、缺失序号和 JPEG 大小；
- 录制逐帧 JPEG、`frames.jsonl` 和 `manifest.json`；
- 导出包含健康快照、指标、根因和事件的 JSON 诊断报告；
- 使用 HOLD 或 RECENTER 停止；最后一个浏览器断开时自动 HOLD。

## 架构与安全边界

网页只连接当前 Application 的 loopback HTTP/WebSocket 地址。Application 使用 Daemon
注入的 Device channel 调用 `robot.vision.status()` 和
`robot.face_tracking.open_preview()`，不会直接连接设备局域网端口，也不会建立第二条设备
业务连接。

本工具依赖设备声明 `vision.status.v1` 和 `face_tracking.preview.v1`：

- SSCMA 后端且当前模型包含 face 类时，支持端侧人脸推理和同帧叠加；
- PTL 后端只适合确认 JPEG 图像链路，不提供端侧推理；
- Person Detection 等非人脸模型会被明确拒绝，不会误启动人脸跟踪；
- 当前模型 ID、名称、任务和 face 类信息为只读；模型上传、切换、删除和参数修改尚未开放。

生成的录制和报告保存在 `examples/vision_debug_lab/artifacts/`，不随 Application 发布。

当前合同可测量端侧推理、设备帧年龄、Application 入站和 App 到浏览器的延迟。网络与
Daemon 段尚无独立时间戳，不能在业务 Application 中强行拆分；后续应通过通用 transport
观测合同补齐，而不是让 Daemon 按视觉消息类型增加旁路或解析分支。

## Type-C 与双串口

开发板上的 CH342-B 对应 ESP32 日志/烧录通道，CH342-A 可连接 Himax 维护串口。两路串口
可以同时打开；只读 Himax 串口不会阻止 ESP32 通过 SPI 获取推理结果。但 CH342-A 是底层
固件维护与原始诊断通道，不是 Vision Debug Lab 的主数据链路，也不能替代 Managed
Application 验收。

调试时建议把实时画面、框和指标走 Managed Application channel；串口只保留低频日志或
必要的 Himax 底层排障。这样能避免把高带宽预览、设备动画和大量文本日志同时压到同一资源
路径上。
