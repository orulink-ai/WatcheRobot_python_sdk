# SDK 配套整合验证

## 定向回归

环境：Windows / Python3.12 / Node（本机PATH）。

```powershell
$piTests = Get-ChildItem tests/test_pi*.py | ForEach-Object FullName
python -m pytest $piTests tests/test_audio.py tests/test_audio_continuous.py tests/test_audio_credit_window.py tests/test_api.py tests/application/test_transport.py tests/test_desktop_session.py tests/test_speech.py tests/voice/test_device.py tests/runtime/test_external_websocket_routing.py tests/application/test_channels.py tests/runtime/test_daemon_runtime_routing.py tests/runtime/test_standalone_application_routing_contract.py -q -o addopts='' --tb=short
```

实际483 passed（79.42s）。覆盖JoyInside/豆包、normal/continuous音频、信用背压/校验/取消/源失败、会话复用/轮换、Pi工具回应、配对/网页和Daemon路由。

```text
python -m mypy src/watcherobot/audio.py src/watcherobot/robot.py src/watcherobot/application/transport.py src/watcherobot/desktop_session.py src/watcherobot/voice/device.py src/watcherobot/speech.py --follow-imports=silent
node --test tests/web/pi_voice_extension.cjs
node tests/web/pi_speech_ui.cjs
```

6源文件mypy成功；Node扩展12/12及真实网页VM成功；YAML解析并验证所有pytest job的setup-node先于pytest。

## Red / Green

- 首次469项：463通过6失败。两项是既有on_text回调在整合时缺失，恢复兼容实现后通过。
- test_cancelled_provider_cannot_start_late_playback实际发现provider吞取消后仍输出音频；记录Red，新增generation复核后Green。
- 新增JoyInside正常轮次复用、32请求轮换两项先Red，补回主干既有语义后Web25/25通过。
- ASR测试初始采音期限10ms与Windows线程调度竞争，云超时测试改为1s采音期限；回压测试按每960采样60ms真实节奏输入，保留超过32帧完整输入、慢云握手/上传、零丢帧断言。

## CI修正

读取既有PR #151的39101881152运行日志：Python3.12共2220通过、8跳过、6失败；5项因node缺失，1项因取消后播放。取消缺陷本轮已修复；sdk-ci三类job与release构建job在pytest前准备Node22，未改变发布授权或环境保护。没有触发发布。

## 扩大验证环境限制

全量mypy在本机Anaconda的torch类型文件UTF-8解码处停止，不是本项目类型错误；6个相关SDK文件检查通过，完整检查交由干净CI环境。

全量pytest首次因环境缺少项目锁定dulwich依赖而产生23个收集错误，随后在忽略目录创建独立校验venv，仅补齐dulwich1.2.17，没有修改全局环境。

隔离环境执行 `python -m pytest -q -o addopts='' --tb=short`：**2230 passed、18 skipped、2 failed，375.08s**。实际失败如下：

- `tests/runtime/test_daemon_import_dependencies.py::test_daemon_initializes_without_loading_audio_codecs`：用例启动子进程未继承 pytest 的 pythonpath 设置，实际导入全局旧版安装包，其 work_assets 提前导入 av。错误栈指向全局 site-packages；本工作树对应源码已有延迟导入。
- `tests/test_api.py::test_camera_capture_with_feedback_reports_jpeg_timeout_with_context`：预期 JPEG 超时，但在全量负载下先触发 camera busy 超时。此前本轮定向483项已覆盖并通过此用例。

设置 `PYTHONPATH` 为当前工作树 `src` 后，使用同一隔离 Python 复跑上述两个完整 node id：**2 passed，1.33s**。这次复跑确认当前源码的对应行为，但不把前述全量结果改写成全部通过。

原始日志保留于本机忽略交付目录：`pcm-sdk-full-tests-venv.log`、`pcm-sdk-expanded-failures-corrected.log`，不提交个人环境路径和临时日志。

## 硬件边界

未执行手机MCP、固件烧录或新集成版本真机验收。源分支硬件记录保留为历史；最终结果确认后才播放，半双工、不支持抢话。配套固件PR #280，提交19efbddb，audio.stream.continuous.v1 / expression.audio_follow.local_pcm.v1按能力启用；旧固件按旧播放合同降级。
