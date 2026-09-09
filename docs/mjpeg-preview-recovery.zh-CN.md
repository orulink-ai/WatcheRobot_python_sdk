# 视频预览通道恢复

SDK Test Bench 的 MJPEG WebSocket 在当前 Application 会话内恢复短暂断链。
重试不调用 RTC start，不改变 session ID，也不恢复已经停止的功能。

- 断链后 250 ms 重试，整个连接/恢复窗口最多 5 秒。
- 只有当前 socket 的 JPEG 解码并完成 Canvas 绘制才能确认恢复。
- 旧 socket 事件、迟到的解码结果不能确认新连接成功。
- 主动停止、会话代次改变、Application 退出取消重试。
- 超过 5 秒无法恢复时显示失败并执行原有会话清理。

需要配套 ESP32 固件：视频发送失败关闭媒体 socket、保持产品会话，并在缺失通道时
暂停发送。旧固件仍可能直接报告 `mjpeg_send_failed`，这种明确的设备失败不会被网页
隐瞒。Daemon 维持透明业务路由，控制请求仍通过 Application。

测试入口沿用 `node --test tests/js/test_*.mjs`，新增行为测试覆盖恢复、超时、主动停止、
旧会话失效及迟到解码。2026-09-08 回归 86 项通过；实际替换并关闭视频 socket 后，
会话 ID 不变且画面恢复，随后主动停止成功。长期性能以嵌入式配套验收报告为准。
