# Himax 媒体阶段配套（2026-09-09）

阶段标签：`stage/himax-media-20260909`。这是研发快照，不是新的 PyPI / Marketplace 正式版本。
本次交付文档不改变已经进行真机测试的 SDK 运行代码。

| 项目 | 固定身份 |
| --- | --- |
| SDK / Test Bench 运行代码 | `9ff1e024b6a18d652291e7649bad42f79591ca30` |
| ESP32 运行代码 | `7eca396f5b4b4d79bc716bb10b208e939dd6b7a7` |
| ESP32 应用镜像 SHA-256 | `f9c9ff727ef61bdec0490c4a38c285fdfe4ad6c169b3698db444806a914da357` |

设备固件、完整实验说明及未完成清单位于嵌入式仓库
`docs/development/himax-media-stage-20260909.md` 和
`docs/development/video-recovery-photo-overlap-hil-20260909.md`。

## 本阶段能力及结果

- Test Bench 通过受管 Application 和既有 Device channel 调用 SDK；Daemon 保持透明路由。
- 纯视频直播时可独立播放 Speaker Stream 或进行 Microphone Recording；两种普通音频操作仍互斥。
- 设备动画继续播放，但不再主动把视频限到 1～6 FPS。
- 同一浏览器直播运行 615.437 秒，期间喇叭和录音各 3 次，总平均 22.576 FPS；无断流或超过 1 秒停顿。
- 单独统计的并发窗口：喇叭平均 17.667 / 20.549 FPS，录音平均 19.835 / 21.633 / 20.084 FPS。
  三次录音均完整，零丢帧、零解码错误。混合长测平均值不能当作所有并发窗口的性能。
- 拍照保留既有快门节点；资源表和实际显示帧计数便于后续定位问题。

封版核对：

```sh
python -m pytest tests/test_sdk_media_lab.py tests/test_face_tracking.py tests/test_vision_inference.py tests/test_vision.py tests/test_audio.py -q
node --test tests/js/test_display_audit.mjs tests/js/test_media_resource_policy.mjs tests/js/test_mjpeg_transport.mjs tests/js/test_resource_health.mjs
```

分别通过 134 项 Python 测试和 40 项 JavaScript 测试。设备 C host 测试及实际构建/烧录证据见嵌入式报告。

## 尚未宣告完成

并发时 DMA 最大连续块仍可能约 7 KiB；短暂 WebSocket 锁竞争和终态通知缺少有界重试尚待处理。
端侧模型 API 已存在，但人脸准确性、真实运动跟随、推理调试 JPEG 和完整跨模式故障验收仍有未完成项。
此阶段不发布正式安装包，不合入默认分支，不改变其他子仓库。
