# Pi 断线后配对被清理状态阻塞

- 创建：2026-10-18 13:42:33 Asia/Shanghai；作者：Mr-KID-github / Codex。
- 用户报告同局域网配对仍显示“连接未成功，请确认同一Wi-Fi”。
- 调查：当前Daemon与Application实例已核对；Daemon idle/offline/last_error=null，最后连接事件为设备主动mode_exit；应用上次ASR结束失败，cleanup_pending与语音session残留，行为清理也待确认。
- 根因：/api/pair在调用Daemon之前被清理互斥409拒绝，前端将服务端中文业务错误当成未知Daemon枚举，误显示网络提示。当前失败尚不能归因为Wi-Fi或SDK不匹配。
- 目标：TDD保留资源所有权门禁，结构化地区分需要重启应用的清理失败和正在停止的忙状态；前端只显示安全已知错误，不透传任意原始异常。重启当前Pi Application并验证新配对。
- 架构：不改Daemon业务路由、不绕过Device channel、不丢弃未确认清理状态、不新建设备业务连接；重启Application是显式恢复生命周期。
- 历史：补充[Pi页面配对](../2026-10-18_013000_Mr-KID-github_无Issue_Pi语音页面配对/index.md)，保留其历史实测结论。本次先定位Application本地门禁，再验证网络。
- 状态：调查完成，等待有效实机配对码，准备Red/Green；未提交。

## 实际修改与TDD

- ConnectionError支持可选安全code，JSON错误响应增加error_code；清理失败与仍在进行的对话分别使用speech_cleanup_required和conversation_busy。未放宽所有权或配对互斥门禁，不丢弃未确认资源。
- 前端保留error_code，按固定枚举显示重启/暂停提示；未知文本/凭据仍不回显，既有Daemon超时细分保留。
- 后端Red：新增两项测试均因缺少error_code失败；Green：配对+Web 52通过。前端真实表单VM Red：3失败/2通过；Green：5通过，覆盖清理/忙状态、未知错误安全回退、配对互斥和旧超时细分。
- 最终命令：`.venv/bin/python -m pytest -o addopts='' -q tests/test_pi_pairing_error_ui.py tests/test_pi_speech_pairing.py tests/test_pi_speech_web.py tests/test_pi_speech_backends.py tests/test_pi_speech_behavior.py`，136 passed，20.72秒，1条既有Starlette弃用警告；`node tests/web/pi_speech_ui.cjs`及git diff --check通过。

## 实际恢复与硬件依据

- 使用用户提供的有效码，在旧Application实测/api/pair得到HTTP409“请先暂停对话并等待当前操作结束，再连接机器人”；Daemon并未新增配对事件。确认是本机拒绝，而非已发送请求但UDP失败。未记录或提交临时配对码。
- 通过Daemon既有Application管理接口重启当前Pi应用，未重启Daemon、未改变路由。应用加载新代码后再次使用同一有效码，HTTP202，约1秒内UDP accepted→WebSocket opened→Device connected；绑定保存成功。Mac与设备同子网，ICMP两次成功。
- 复用固件joyinside_device_probe.py中的无复位串口打开helper，只发送debug.app.status/debug.sdk.pairing/debug.link.status连接诊断命令；读到sdk.control.app活动与MCU READY、ACK timeout=0、CRC error=0。没有烧录、复位或UART业务旁路。
- 串口读取存在瞬时Errno6（Device not configured），重新打开可获取固件日志，USB双串口持续枚举；未据此推断固件崩溃或宣称串口驱动问题已解决。日志还显示内部最大连续块7KB的既有内存告警；本次握手和设备信息查询成功，未将该告警归为本次配对失败根因。
- 最终Daemon online=true，Pi Application connected=true / phase=paused / enabled=false / error为空，behavior.cleanup_pending=false，后端切换可用。页面DOM实测“机器人已连接”；已打开新的本机页面。没有自动开始录音或控制手机；声学链路留给用户交互测试。
- 结论仅针对本次失败：离线后的Application资源所有权残留 + 前端错误泛化；此前档案中的网络/握手故障不能用本次结果全部追溯解释。
- 状态：本地修复已加载、实机配对及设备查询验证完成；尚未commit/push，未更新HF商店版本或重新打桌面包。

## 2026-10-18 13:48:55 Asia/Shanghai｜Mr-KID-github / Codex｜源码交付

- 用户要求将修复推送远程供同事源码运行。交付仅包含配对修复、回归、README修复/源码入口和SOURCE_RUN.zh-CN.md；商店候选manifest、国际商店说明及发布档案保持未提交。
- 提交前再次运行配对/UI/Web/backend/behavior回归136 passed，14.18秒；既有前端VM通过。daemon activate及app run参数均通过真实CLI --help核对；没有在同事机器执行安装，不声称Windows/macOS全新环境已实测。
- 源码交接明确Python3.10–3.12、Node22.19+、Pi1.3.0、editable安装本分支SDK、私有凭据路径和Daemon接管会停止已有Application；运行仍由Daemon管理，不直接执行app.py。
- 代码推送目标codex/pi-voice-android-integration；不自动合并main，不发布新PyPI/商店/桌面安装包。提交与远程结果以实际Git/PR回执为准。


## 后续变更
2026-10-27｜KID / Codex｜[PCM配套合入](../../2026-10-27/2026-10-27_181300_KID_无Issue_PCM配套合入/index.md)：在最新目标分支整合并重新回归；原有硬件记录保留为历史。
