# SDK 连续 PCM 播放

- 创建时间：2026-10-18 10:49:40 Asia/Shanghai。
- 作者：Mr-KID-github；参与者：Codex SDK 子任务。
- 状态：SDK实现、主机回归及主任务设备验收完成；本轮按用户授权本地提交，不推送。
- 需求：一个回复只打开一次设备音频流，应用可连续提交生成中的 PCM，避免逐句 begin/stop。
- 范围：SDK 音频 API、Application/桌面 transport、语音设备适配器及单元测试；不修改 Daemon 路由、不碰应用实现或固件。
- 合同：能力 audio.stream.continuous.v1；begin mode=continuous、独立偶数 max_bytes 上限 4MiB；24k mono S16；首个非空 FIRST、递增 seq、空 LAST 结束；增量 SHA 在 LAST 前确定并严格核对固件完成 SHA。
- 所有权：连续播放期间拒绝其他 play 覆盖；取消/错误必须 stop 确认，确认失败保留设备 owner 重试；成功完成不额外 stop。
- 验收：独立总预算、格式、FIRST/LAST、SHA、背压、取消、来源异常、原有 v1 回归；不声称硬件验证。

## 历史依据

已阅读仓库规则、档案规范、模板并核对现有 audio.stream/v1 代码。延续首段短切任务：该任务的整段协议仍适用旧固件，新能力有明确协商门禁。

## TDD

- Red：新增 tests/test_audio_continuous.py；8 个测试均因缺少目标 API 失败，包含 begin 合同、SHA、预算及覆盖隔离、两个 transport 的帧序列和设备适配器成功/失败资源合同。
- Green：待实施。

## 实际变更与 Green

- `audio.py`：新增 `ContinuousAudioPlayback`（open/write/finish/Job wait/cancel），每次 write 必须完成再下一次；本地逐次累计限制、样本完整性、增量 SHA、终止后拒写。共享 `ContinuousAudioSender` 只在 transport loop 分成 4096 字节帧并按 credit 等待，不复制业务路由。
- `robot.py`：`robot.audio.open_stream(max_bytes=4MiB)`，能力门禁；continuous owner 未释放时拒绝 v1 PCM/本地音效及另一个流；complete 缺少或不匹配 SHA 均失败，失败保留 owner，显式 stop/cancel 确认后才释放。旧 v1 begin/终态行为保持。
- Application/桌面 transport：新增只传不可变 bytes 的 open/write/close 会话 API；应用异步迭代器不会迁移到 SDK loop。空 LAST 不伪造长度、音频和 SHA。保留当前 authorized Device channel 和既有 credit 算法。
- `SDKVoiceDevice.play_stream(frames)` 在调用方 loop 驱动迭代器；播放真正完成时不额外 stop，错误/取消关闭来源并 stop；stop 确认失败保留 `_playing` 供恢复。新增只读 capability 属性和真实发送时序观测 `last_playback_stats`（begin/首 packet/upload/complete/异常 stop/elapsed 和 uploaded_bytes）。普通 `play` 维持成功也 stop 的原合同，增加同 owner 并发拒绝。
- 观察字段 first_frame_ms 指首 packet 已交给 transport channel，不代表扬声器已发声。

### 补充 Red/Green

- 新增设备并发 v1 play 拒绝用例先 Red：旧行为在打开失败后会执行 stop。添加进入播放前的 `_playing` 门禁后 Green。
- 新增两个 transport 的“旧发送器阻塞后迟到完成不能清除新会话”用例先 Red：旧 finally 无条件清空 sender。改为对象身份核对后 Green。
- 新增真实 Application transport 线程与调用方 asyncio loop 的隔离测试；验证来源在调用方 loop、所有 packet 在 transport loop、真实 SHA 成功结束，并记录首帧耗时。
- 新增流控耗尽阻塞、close 唤醒、跨会话恢复、写入串行与无无界排队、取消关闭来源、来源错误 stop 失败重试。

### 最终相关回归

```
.venv/bin/python -m pytest tests/test_audio_continuous.py tests/test_audio.py tests/test_api.py tests/application/test_transport.py tests/test_desktop_session.py tests/voice/test_device.py -q -o addopts='' --tb=short
```

实际 **195 passed**，其中 `test_audio_continuous.py` **17 passed**；`git diff --check` 通过。

## 当前状态及缺口

SDK 本地实现和主机测试完成，未提交、未推送、未烧录、未做硬件验证。主任务负责 Application 队列、固件配套能力部署及真实音频验收。固件同队确认不新增 buffer_status reason，完成使用既有 `complete` 和真实 `audio_sha256`；非法参数仍经原 sys.nack/既有帧拒绝处理。

## 接口使用

```python
if device.supports_continuous_playback:
    await device.play_stream(pcm_async_iterator)
```

此迭代器可由应用的有界生产者队列提供；整条回复独立累计上限 4MiB。底层显式句柄 API 为 `robot.audio.open_stream()`，逐次等待 `asyncio.wrap_future(playback.write(pcm))`，结束时 `finish()` 后等待 Job 终态；不允许省略真实 EOS 或使用上一轮 SHA。

### 类型与最终复核

- 针对五个变更 SDK 源文件执行 mypy（项目 Python 3.10 目标、follow-imports=silent）；首次发现 optional SHA、连续 transport 协议和 optional condition 注解缺失，补齐专用 `ContinuousAudioTransport` Protocol、显式 sender 类型及 condition 局部断言后结果：**Success: no issues found in 5 source files**。
- 类型修正后相同 pytest 命令再次 **195 passed**，diff whitespace 检查通过；未改变以上硬件验证缺口。

### 架构复核后修正：跨 write 槽位合并与丢帧终态

- 复核发现每次 provider 小块各占一个设备槽，会在累计字节不足预缓冲时耗尽 credit。两 transport 新增“100 次 2 字节 write”先 Red（等待 credit 超时）；改为共享 sender 跨 write 保留 `<4096` 字节 staging，满槽才发送，finish 末残片 + 空 LAST，close 清 staging。两个真实槽数/字节预缓冲模型及取消残片释放测试通过。SHA 继续按实际输入累计，不填补静音或伪造字节。
- 复核发现固件 RX SHA 在入队前计算。新增三个正确 SHA 但 dropped_frames / drop_timeout_frames / seq_gap_frames > 0 的完成事件用例，先 Red（误判 COMPLETED），修为 continuous FAILED/audio_frames_lost 且保留 owner 待确认 stop。旧 v1 终态保持原合同。
- 当前完整相关命令在此前列表加 `tests/test_audio_credit_window.py`，实际 **208 passed**，其中连续新测试 **26 passed**；5 个源文件 mypy 和 diff check 通过。
- 为排查主任务 v1 实机基线 audio_send_failed，以 AST 比较 HEAD 与当前两 transport：Application `_send_audio_stream`/`_take_audio_credit`/`_update_audio_flow`、Desktop `_send_audio`/`_take_audio_credit`/`_update_audio_flow` 六个旧方法均完全一致；本子任务未改 v1 credit 算法。实机失败原因由主任务继续查看固件真实 reason，不能用主机回归冒充硬件通过。

### 实机反馈后连续流补偿窗口

- 主任务实机发现64槽设备队列的 target_pending=32，但既有单次 credit 授权上限8，在慢状态回报时无法快速补足剩余缓冲。仅对活跃 continuous sender 提升上限到32，并严格取 target_pending-pending、free_frames 的最小值，再由既有 receive watermark 扣除未 ACK 在途数据。
- 旧 v1 仍使用8；初始4 credit、设备队列大小和起播 prefill 不变。该阶段起 `_update_audio_flow` 有显式 continuous 分支，前文“六方法完全一致”仅描述当时诊断点，并非当前版本。
- 新增8例（两 transport × queue64/queue16/free3/free0），其中64槽目标与 free3 两组先 Red，再实现后 Green；重复状态不重复授权、旧流取消后的迟到状态不影响新流。
- 当前 **216 passed**，五源文件 mypy 和 diff check 通过。主任务负责重新验证实机音频连续性；本子任务未自行运行硬件。

## 主任务硬件验收
[持续生成与单流播放](../2026-10-18_105058_Mr-KID-github_无Issue_TTS持续生成与单流播放/index.md)完成新固件部署、相同PCM对照、真实Pi/豆包/嘴型与播放中取消；主任务记录完整结果与Wi-Fi波动边界，SDK此前未部署的子任务状态仅为当时历史。
