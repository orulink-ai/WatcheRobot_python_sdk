# SDK Test Bench

网页将两种屏幕能力分别命名为“SD 动画播放”和“实时程序表情”。组合通话入口显示
“默认通话表情”，复用 JoyInside RADIAL 09 的本地绘制规则，不启动 JoyInside 云端对话。
这些是界面名称；`animation.play`、`expression_runtime.set_audio_follow` 及设备协议保持兼容。

SDK Test Bench is a standalone managed Application for whole-robot hardware
acceptance. It serves a loopback-only browser dashboard, exercises only public
Python SDK domains, and never opens a device connection of its own. The
`sdk_media_lab` directory and Application id remain stable for compatibility.

Version 1.1.0 is ready for distribution through the Watcher Desktop
Application Marketplace. The dashboard starts in English and provides an
**EN / 中文** switch in the header. Its icon, web assets, and PCM sample are all
contained inside this directory; generated photos and recordings stay under
the ignored `artifacts/` directory and are never included in a published
source snapshot.

优雅退出要求使用提供 `ApplicationContext.shutdown_requested` 的当前 SDK
（本轮以 0.1.9 wheel 验证）。Daemon 请求停止后，应用主动关闭 HTTP 服务并释放
上下文；旧 Runtime 内的 0.1.1a4 wheel 不满足此要求，需要更新 Runtime，
不提供旧接口兼容分支。此源码修复需重新发布后才能更新远端应用版本。

The distribution manifest uses schema 2 and currently declares Windows only,
matching the recorded hardware validation. macOS is not advertised until its
runtime and hardware acceptance have been completed.

The tested 2026-09-09 SDK/ESP32 pairing, concurrent video/audio results, and
remaining limits are fixed in the [Himax media stage record](../../docs/himax-media-stage-20260909.md).

## Combined procedural audio and photo scene

The combined scene uses JoyInside's default **RADIAL 09** procedural expression
and reuses the existing full-duplex audio RTC session. Click **Start Call + Live Expression** to confirm procedural startup before starting the audio call;
an independently enabled procedural expression is reused. Computer microphone audio is
played by the robot while the robot microphone returns independently to the
browser. The radial mouth follows successfully played RTC PCM on the device,
not a browser microphone estimate. Silence closes the mouth. Click Capture Photo
during the call to receive a JPEG without stopping the audio or procedural display.
Use headphones to inspect the robot-to-browser direction without a local
acoustic loop.

**End Scene** cancels a pending start or stops the call and the procedural
expression newly started by that scene. An expression enabled independently
before the scene remains running after End Scene or a failed call startup.
The start response includes `started`: `false` means that a concurrent client
already owns the running expression. The scene waits for this acknowledgement
before releasing a cancelled start, and preserves reused expressions.
Unconfirmed releases remain available for retry. Closing the page retains the
cleanup policy and stops its owned procedural expression and RTC session,
including expressions enabled with this page's independent button. Automatic
page/scene cleanup carries the procedural start identity and cannot stop a newer
client's expression; the explicit manual stop button remains a global control.
Opening the page does not start
hardware features automatically. The independent procedural and RTC buttons
remain available for isolated measurements. **SD Animation Playback** is a
separate SD-file playback test; it does not start the JoyInside procedural face.

Procedural start requires `expression.audio_follow.v1` and reserves the display
until stop is acknowledged. Unsupported firmware disables that feature explicitly.
Audio-only RTC reserves microphone and speaker, leaving the camera available.
Video or combined AV RTC still owns the camera and rejects a simultaneous still.
The plain SDK camera capture is used; capture feedback that takes over the display
is excluded. Ordinary SD animation switching is blocked while procedural mode owns
the display. Stop failures keep that lease reserved for retry, and disconnects
require cleanup on reconnection rather than automatic restart. An in-flight plain
photo pins the display lease. A stop during capture returns busy; retry after
capture finishes. Status and recording stay responsive. Application shutdown
stops owned procedural mode; the page also sends a cleanup request on exit.
Display state snapshots use a short state lock separate from command serialization.
Maintenance skips a lifecycle lock already held by a pending command, allowing
status and sampling to continue while its device acknowledgement is delayed.
Scenario sampling also runs in a separate background task, so maintenance's own
cleanup command can wait for an ACK without delaying the recording cadence.

Start a recording before changing the load. The backend records at most one
sample per second and retains the latest 3600 samples; `sample_count` includes
all samples and `dropped_samples` identifies truncation. Memory minima are
retained across the entire recording even after the sample buffer rolls over.
Export includes device identity/capabilities, baseline, memory snapshots, RTC
stats, procedural mouth evidence, resource owners, and recent action events.
Missing telemetry is unavailable and an unchanged snapshot becomes stale after
five seconds. A free-memory lifetime minimum is not a scenario-local minimum;
compare free bytes and largest blocks against the recorded idle baseline as well.
Freshness uses the host receipt time of a device resource event, rather than
HTTP polling or a changed pairing request. Offline and reconnected sessions keep
cached data unavailable until a new event arrives; mouth and frame-rate readings
follow the same rule. Reports retain the recording's initial device identity and
baseline, and identify the device and connection on each sample when a run crosses
a reconnect or device change.
The SDK exposes `robot.resource_evidence`: a detached event snapshot with its
matching receipt time, device identity and connection generation. Telemetry age,
mouth fields and memory aggregation reuse that evidence. A new device-ready
generation cannot make an older snapshot available before a fresh resource event.
Baseline events retain `baseline_generation` and `baseline_device_id`; recording
initialization binds that source before its first sample and verifies identity
around the baseline copy. An older baseline or an adapter without source metadata
is conservatively marked `baseline_comparable: false`.
Collection checks Daemon connection identity before and after copying SDK evidence;
if it changes, that collection is unavailable rather than combining two sessions.
Samples include `resource_generation`. A recording that crosses device or connection
generations sets `summary.mixed_sources: true` and `baseline_comparable: false`, and
clears global memory minima. Individual samples remain available for separate analysis;
do not compare their mixed sources against the recording's initial idle baseline.
The browser chart also starts a new window on a device, connection or resource
generation change, including when the resource sequence happens to repeat.
Each recording has a generation; an in-flight sample from an older recording is
discarded if that recording was stopped and replaced before collection completed.
One-second samples do not alone prove every camera allocation peak; firmware
capture-stage/lifetime minima and audio error counters provide complementary evidence.

Compare four loads in order: idle, procedural only, RTC audio only, and procedural
+ audio RTC + repeated photos. Speak, pause, and speak while robot uplink is active;
then stop both features and wait for resource release. Record whether counters and
memory recover, and physically confirm sound and mouth motion. Browser or SDK
capability availability by itself is not a successful hardware test.

Local HTTP controls are `POST /api/controls/procedural/start`, `/stop`,
`POST /api/scenario/recording/start` with `{ "label": "combined-scene" }`,
`POST /api/scenario/recording/stop`, and `GET /api/scenario/report`. These wrap
public SDK APIs through the current Application Device channel and leave Daemon
business routing unchanged. See the [audio-follow API](../../docs/procedural-expressions.md).
Procedural start/stop accept an optional `{ "request_id": "..." }`. A mismatched
scoped stop returns `matched: false` without stopping anything; unscoped stop is
reserved for explicit global control or Application shutdown.
During an in-flight photo, a matched scoped stop retains a pending stop intent:
maintenance completes it after the photo without relying on the page to retry.
An unscoped manual stop remains busy and requires another explicit request.
Pending display cleanup retains `cleanup_device_id` and refuses to send stop to a
different device. Reconnect the original device to confirm cleanup; switching
devices does not silently discard its lease. SDK audio-follow cleanup has the same
device identity guard. Stopping a recording remains effective even if its final
diagnostic sample fails, and does not end a concurrently created recording generation.

Browser RTC starts attach a unique `request_id` to the local HTTP request and
use the same ID for stop, failure cleanup, and page-exit cleanup. The Application
matches it under the RTC lifecycle lock before stopping anything. A stale ID
returns `{ "stopped": false, "matched": false }` without affecting a later
session, including one started by an external diagnostic client. An unconfirmed
stop retains ownership for retry. On a back-forward cache restore, the page retries
the scoped stop and clears its retained ID only after acknowledgement, preserving
a newer client's call. This ID stays in the Application and does not change the
Device wire protocol. Diagnostic clients may continue to omit the
ID and use the existing unscoped start/stop controls.

### Fixed SD loop for resource comparisons

`POST /api/controls/sd-baseline/start` and `/api/controls/sd-baseline/stop`
take no request body. The start calls the public
`robot.behavior.play("desktop_expression_panel")` with its default `repeat=1`;
the stop calls `robot.behavior.stop()`. In the paired firmware's SPIFFS behavior
catalog, this behavior has only the SD asset `standby`, with
`loop_until_replaced`, `loop=true`, and `hold_until_replaced=true`. Its motion
and sound lists are empty. Ordinary `robot.animation.play()` uses a one-shot
behavior and does not provide the sustained load needed for this comparison.

The API checks the `behavior`/`animation` capabilities and advertised `standby`
asset. It relies on that fixed firmware catalog contract; ready metadata does
not expose the full behavior definition. Confirm continuous SD playback from
the device screen and fresh animation telemetry during hardware measurements.
`status.sd_baseline` and each recording sample identify `behavior_id`,
`animation_id`, `operation_id`, and `state` (`idle`, `starting`, `running`, or
`stop_required`). A running start is idempotent. The `animation` resource owner
is `sd_baseline`, so SD and procedural modes exclude each other while audio RTC
can run alongside either one. Stop failures retain the lease for a confirmed
retry. Disconnects require cleanup on reconnect; Application shutdown stops
RTC before releasing the display mode. This entry does not add browser buttons.

Use the same RTC audio source, warmup time, and measurement duration for SD-only,
procedural-only, SD+RTC, and procedural+RTC phases; reverse the paired phase order
for a repeat. Compare without photos first, then use identical photo schedules
for a separate combined-load comparison. Report warm runs separately from cold
boot measurements. Record current free bytes and largest blocks independently
from firmware lifetime minima, and compare RTC error/drop counter deltas within
each session. SD FPS telemetry uses a recent measurement window; procedural FPS
is averaged since renderer startup. Identify those windows rather than treating
them as identical per-second measurements.

## Concurrent photo, speaker and custom UI test

The panel follows the lightweight workload from historical commit `809876b`:
three workers start together with no SDK FIFO scheduler. The display starts the
`thinking` / `watcher_pulse` eyes, then cycles four gaze, openness, tilt and color
frames, waiting 0.18 seconds after each update. There is no custom vector or
sphere shading. Atomic photo capture waits **one second after each completed
photo** before requesting another. The bundled audio sample plays **once**.

Duration is 1–120 seconds, default 30, and limits scheduling new photo/UI calls.
Unlike the old script's forced audio cancellation, in-flight operations finish
under SDK timeouts before cleanup; a short window can therefore take longer
than the selected duration. Failure or **Stop Test** stops new work and
interrupts the audio wait; audio is stopped during cleanup. Camera/UI calls
already in flight finish under their SDK timeouts. Each cancelled audio/UI worker
cleans up its own channel without waiting for the camera. Audio is not looped for the whole window; all
three channels overlap only while the single playback is active.

The bench reserves camera, display and both audio directions during the test.
All three initial requests are scheduled together even if another initial
request fails. Atomic capture does not use the photo sound/animation feedback.
If the worker pool cannot submit all three tasks, startup is aborted before any
device calls. Queued workers are released, local leases are returned, and the
report records `started: false` with a setup error. No device cleanup commands
are sent for an unstarted workload; the next test can be attempted normally.

The compact panel below device pairing shows live per-channel successes and
failures, cleanup acknowledgement, a collapsible detail view, the latest photo
and a JSON report download. Every JPEG is decoded and saved with a unique name.
`GET /api/concurrency/result` returns a snapshot while running and the final
report afterwards, including counts, timings, errors and resource telemetry.
If the JSON report cannot be written (for example, the disk is full), the final
result remains available in memory until the next run or Application restart.
`report_saved` is false and `report_save_error` describes the storage error;
device verdicts remain separate. The page shows the error and hides the report
download link rather than leaving the run marked as active.
The removed `ui_stroke_width` diagnostic parameter is no longer part of this
workload; old width comparisons are separate historical diagnostic artifacts.

Success means a validated JPEG, a completed SDK playback job, or acknowledged
UI commands, respectively. Cleanup acknowledgement does not prove the absence
of a device memory leak; inspect the resource panel as well. Screen appearance
and audible sound require manual observation. The checkboxes record local UI
observations separately from the SDK verdict, reset per run, and are not saved
in the SDK JSON report. A channel interrupted by another failure is not marked
as passed. Partial successes remain visible when a later call fails.

UI startup and successful dynamic updates are counted separately. At least one
successful update is required for a passing UI verdict; a window consumed by
startup is reported as **Verification incomplete**, not passed.

An unacknowledged UI stop retains the animation lease; an unacknowledged audio
stop retains microphone and speaker leases. These prevent conflicting starts.
**Retry Cleanup** (`POST /api/concurrency/cleanup`) releases only resources whose
stop command succeeds; confirmed device disconnection releases the remaining
local leases. The original run report retains its errors, while current pending
cleanup is shown independently through `status.concurrency_cleanup`.
An unavailable or malformed status response is not proof of disconnection and
does not release these leases; the Daemon must report a valid offline state.

The Application signals the test to stop before asking Uvicorn to drain HTTP
requests. Audio waits poll for cancellation every 100 ms instead of blocking
shutdown for the entire playback timeout. New tests are rejected once shutdown
begins. Cleanup still depends on device acknowledgement; an unresponsive device
can exceed the Daemon shutdown grace period.
The CLI allows up to 30 seconds for `app stop` and Ctrl+C cleanup in `app run`
or `app run-installed` to receive the Daemon's stop result. This client-side
timeout does not extend the Daemon's own graceful shutdown deadline.

## Camera capture

The camera panel has two actions:

- **Atomic Capture** calls `robot.camera.capture()` and returns one JPEG.
- **Capture with Feedback** calls `robot.camera.capture_with_feedback()` and
  requires the device capability `camera.capture.feedback.v1`. The firmware
  coordinates the photo animation and shutter sound with capture.

Feedback capture reserves camera, animation, microphone, and speaker resources
until the call finishes or fails. Stop recording, playback, or audio RTC before
using it. Atomic capture remains available during independent audio operations.
Both methods share one camera lock, and their positive, finite `timeout` covers
lock waiting, command acknowledgement, BUSY retries, and receiving the JPEG.
An expired lock wait does not send a new capture command.

For compatibility, the existing `capture()` command does not require a populated
capability snapshot: older firmware or a delayed startup query can still receive
the original command. Feedback capture always requires its explicit capability.
A timeout reports failure to receive a result within the budget; it does not
confirm that an already accepted device operation was cancelled.

## Face tracking test

The Edge Vision panel queries `robot.vision.status()` and exposes
`robot.face_tracking.start()` / `stop(policy="hold")`. Tracking runs on the device
without uploading a preview. Start reserves both camera and motion until stop is
confirmed; stop timeouts keep those resources reserved and allow retry. Application
shutdown stops owned tracking, and a disconnected tracking session is stopped on
reconnection instead of automatically resumed. Closing only the browser tab does
not exit the Application: use Stop Face Tracking to end an intentionally headless run.

The panel requires `face_tracking.control.v1`. Preview-only PTL firmware reports
inference unavailable, so Start is disabled with an explicit explanation; vision
status remains queryable. Unified firmware can advertise inference support;
the panel follows the actual device capability. A button or an existing SDK API
does not add inference to preview-only firmware.
The SDK also provides `robot.face_tracking.open_preview()` for optional diagnostic
frames. **Start with Preview** opens the SDK preview at 640×480, draws matching
face boxes, and shows sequence and frame age. The button requires
`face_tracking.preview.v1`; older PTL firmware keeps headless tracking available.
Stop before switching between headless and preview modes. Stop timeouts retain
the camera/motion lease, and the frame endpoint never returns stale frames after stop.
`POST /api/face-tracking/preview/start` and `GET /api/face-tracking/preview/frame`
serve the loopback dashboard; they use the managed Application Device channel.
See [the SDK lifecycle contract](../../docs/face-tracking-lifecycle.md) and
[preview API](../../docs/face-tracking-preview.md).

Historical validation on preview-only firmware, 2026-09-08: 81 Test Bench Python tests, 30 SDK vision/tracking tests,
and 86 JavaScript tests passed. A real browser queried the connected PTL device
through its Application channel and displayed `inference=false`; unsupported
tracking controls were disabled. Physical face-following motion remains untested
on this PTL firmware. English and Chinese panel text were verified in the browser.

Start the Runtime, then run:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

The Application opens its `http://127.0.0.1:<port>` dashboard automatically and
the `watcherobot app run` terminal echoes the startup log containing the exact
URL. Set `WATCHER_MEDIA_LAB_NO_BROWSER=1` to suppress automatic browser launch;
the URL is still printed so it can be opened manually.

When no device is connected, enter the six-digit code shown on the Watcher in
the dashboard's **Connect robot** panel. The local Application sends that code
only to the SDK Daemon management endpoint; it does not store the code or route
it through an Application business channel. A device paired before launch is
reused automatically. If the LAN suppresses broadcast discovery, the optional
device IPv4 field sends the same pairing request directly to that same-subnet
address while the normal broadcast discovery remains enabled.

The dashboard tests motion, lights, host-to-device PCM playback, one-shot JPEG capture,
decoded microphone recording, animation switching, capability discovery,
artifacts, diagnostic events, a live camera preview, full-duplex RTC audio, and
one combined audio/video RTC session. The live preview uses
`watcher-rtc/1` only
for signaling through the current Application's Device channel; MJPEG frames
travel directly from the Watcher to the browser over an unordered,
partially-reliable WebRTC data channel named `mjpeg-data`.

Live preview requires firmware that advertises `rtc.video.mjpeg.v1`. It keeps a
heartbeat while the page is open, uses latest-frame-wins rendering, and releases
camera resources when stopped, disconnected, or when the page closes. Full-duplex
audio requires `rtc.audio.full_duplex.v1`, requests the computer microphone only
after the user starts the call, enables browser echo cancellation, and releases
all local tracks on stop, failure, disconnect, or page close. Its healthy
verdict also requires non-silent capture reported by the device, non-silent
audio decoded by the browser, and an active remote player; this verifies the
robot-to-browser path. The browser-to-robot path additionally requires device
receive, decode, I2S output, and non-silent playback evidence with no renderer
errors. The operator still confirms the selected OS
output device and physical earphones. Packet counters alone do not prove that
the robot microphone is audible. Camera and
microphone actions capture the surrounding environment; obtain consent
before use and handle generated artifacts appropriately.

Controls are arbitrated by hardware resource rather than by one page-wide busy
flag. Motion, body lights, and animation each have an independent lease, so all
three remain available during live video, full-duplex audio, or combined AV.
Camera has an independent lease. The standalone 24 kHz speaker path and 16 kHz
microphone path share one ordinary-audio lease because the firmware routes both
through the same audio runtime; playback and recording therefore cannot overlap.
Audio-only RTC owns the microphone and speaker but leaves one-shot camera capture
available. Video-only RTC owns the camera but leaves one standalone audio
direction available at a time. Combined AV owns all three media leases and is
one firmware session (`mode=av`), not two peer connections competing for the
same codec, camera, network, and teardown resources.

The animation selector is populated from the connected device's
`evt.sdk.ready.data.animations` catalog, so every animation actually installed
on the current SD resource set is available without a hard-coded browser list.
**Start random** cycles through that catalog at the selected interval, avoids an
immediate repeat, and prefetches the next animation when the firmware advertises
`animation.prefetch.v1`. Random playback remains available during live video,
full-duplex audio, and combined AV, and its timers are released on stop,
disconnect, or page close.

Current full-duplex firmware negotiates mono Opus with a 48 kHz WebRTC clock
while the robot microphone, speaker, and device-side AEC remain at 16 kHz. The
browser never attaches its local microphone track to the local audio player.
Ordinary browser calls request echo cancellation, noise suppression, and automatic
microphone gain control. `?rtc_audio_processing=0` explicitly selects raw microphone
capture for headphone diagnostics; `=1` and an ordinary URL use the processed profile.
Updated firmware adapts robot RTC playback levels before the codec write: quiet
voice receives a gradual boost of at most +18 dB (8× amplitude), normal
voice is preserved, and peaks are limited to 29203/32768. RMS at or below 104 is
not additionally amplified. The shared device volume remains user controlled.
Short pauses retain the learned voice gain while noise passes unchanged; one
second of silence clears it. This prevents every syllable from starting at unity.
The target speech RMS is 8000/32768; gain rises with a 12% step toward the target
per 20 ms frame and falls immediately when required by the target or peak limiter.
The console shows input/output PCM RMS and effective gain; these values are
digital levels, not acoustic loudness measurements. Expression callbacks and
the hardware AEC reference receive the processed playback PCM. The processor
adds no task or audio buffer and resets when the renderer opens a new session.
Inspect the actual microphone track's `getSettings()` to confirm what the browser
applied. The device AEC remains enabled independently. Close-range speakerphone use
can still feed sound between both endpoints; compare with headphones and low volume.
When the computer microphone is heard again in the headphones, inspect the
robot's acoustic echo path: healthy playback makes `audio_aec_chunks` advance,
keeps `audio_aec_reference_drops` at zero, and leaves
`audio_render_errors`/`audio_queue_dropped` at zero. With no far-end playback,
`audio_aec_bypass_chunks` advances so AEC nonlinear processing does not color
near-end robot speech.

The resource panel is backed by the public `Robot.resource_baseline`,
`Robot.resource_rtc_baseline`, `Robot.resource_snapshot`, and
`Robot.resource_history` properties. Compare the idle baseline, the snapshot
immediately before RTC starts, and the post-stop snapshots when checking for a
resource leak. The dashboard checks free bytes, minimum free bytes, and largest
contiguous blocks independently for internal RAM, DMA RAM, and PSRAM; this keeps
fragmentation visible even when total free RAM still looks healthy. A stable
reusable high/low range is acceptable; four monotonically declining post-stop
samples across repeated start/stop cycles are reported as a fragmentation trend.
The live-video panel also shows source/target/sent FPS, transport latency,
browser congestion, and animation FPS/underrun/late-frame pressure so a smooth
idle animation cannot hide contention that appears only under AV load.

## PTL preview candidate validation (2026-09-08 historical baseline)

Pure video now uses the device's existing LAN MJPEG socket and Application
session/control APIs without creating a browser WebRTC peer. Audio and AV
sessions retain their WebRTC peer. This requires the paired ESP32 candidate
that starts video from JPEG-client readiness. The Daemon routing is unchanged.
That historical candidate did not implement model enumeration or inference mode
switching. The current paired firmware and SDK add both (see the generic model
section below); official SSCMA model-maintenance compatibility is still separate.

The page marks video LIVE only after a JPEG has decoded and drawn. Inspect the
`data-display-audit` attribute on `#liveVideoCanvas` for cumulative unique forward-sequence
Canvas draws, duration, new-frame FPS, P95/max content-update gap, idle tail, duplicate and
sequence errors, dimensions, and a bounded-storage truncation flag. The final
snapshot is retained on stop and reset for a new session. Duplicate/backward
frames are counted as anomalies but excluded from the new-frame rate; this is
not the total number of Canvas draw calls. This is browser draw
evidence, not sensor-to-browser CRC verification or physical monitor scanout.
A short snapshot above 15 FPS is not a ten-minute acceptance result.

For the 2026-09-08 hardware investigation, failure records and the complete
remaining integration checklist are maintained in the embedded repository's
`docs/himax-unified-hil-2026-09-08.md`. The candidate remains experimental;
legacy clients that require a video-only WebRTC offer need compatibility
validation before product rollout.


### 功能切换资源诊断

资源面板的 Recent Feature Resource Table 展示最近的功能启动前、启动后、完成和释放快照，
包括内部 RAM、最大连续块、DMA 最大块，以及 TTS/SFX 常驻任务栈字节数。
`tts_playback=false` 只代表当前没有播放；应结合 `tts_runtime` 和 `tts_stack_bytes`
判断工作任务是否已经释放。音频编解码器常驻状态由 `codec_resident` 单独表示；
DMA 剩余量和最大连续块见内存列，不能由一个常驻布尔值推算。
配套固件也在这些功能边界输出串口 `resource_table`，周期采样不重复打印表格。

Boot Minimum Internal RAM 是本次设备启动以来的低水位，不会在停止视频时复原。
判断回收应比较当前剩余量和最大连续块，不能把启动以来的最低值当作当前剩余量。
表格仅展示内存历史窗口中最近的功能边界；完整排障记录应同时保存设备串口日志。

程序表情测试固件的 SDK 空闲状态使用静态界面，停止功能后不会自动重启 SD 待机动画。
资源面板另外显示实际 SD 帧池、素材缓存、读取任务栈、LCD DMA 条带与程序画布字节数；
遥测缺失或过期时显示未知，不能当作已经释放。约 331.5 KiB 的程序画布与 LCD DMA
条带在表情运行时是正常分配。配套固件的 SD 读取任务改为首次播放／预取时创建，
程序表情接管前等待读取任务退出，释放 SD 帧池和素材缓存；较旧固件仍可能保留读取栈，
应以设备遥测确认。全部显示功能关闭并成功解绑后，这些动态播放器资源应归零；
动画服务自身仍有静态基础设施。

上述静态空闲是分项诊断基线。产品模式合同要求 RTC 关闭后恢复随机表情及自动行为，
可以按需重新启用 SD 动画；这条恢复路径尚需实现和验收，不能用静态空闲冒充。
通话模式固定本地默认程序表情，只根据机器人实际播放音频更新说话／静默嘴形，
期间不随机更换表情，不为两种嘴形状态重建任务。模式切换验收须先确认 SD 专用
资源释放再启动 RTC，以及 RTC 动态资源释放后才恢复静默行为；手动运动始终保留。

### 2026-10-08 配套固件验收边界

本轮配套程序表情优化的同条件实测为：单独运行约 13.12 → 19.98 FPS，
双向 RTC 音频加偶尔拍照约 6.92 → 12.12 FPS；后者仍未达到 20 FPS 目标。
60 秒自动双向媒体用例完成两次 640 × 480 JPEG 拍照，拍照期间反向音频持续到达。
用例通过不代表零丢帧，末次设备播放队列仍记录 18 帧丢弃。

配套固件可额外使用约 331.5 KiB PSRAM 保存上一帧，跳过不变条带的屏幕传输；
它与程序画布是两项独立分配，分别由 `procedural_cache_bytes` 和
`procedural_frame_bytes` 上报。停止后两项及 LCD DMA 均已确认归零，不能只看当前
内部剩余量来推断缓存是否释放，也不能凭一轮记录宣称全系统无泄漏。

自动用例结束后恢复浏览器真实麦克风通话时，WebRTC 建连失败；失败后的重试还出现
设备启动确认超时。设备重启后浏览器建连仍失败，因此网页真人通话恢复和反复切换
尚未验收通过。当前保留程序表情单独运行供观察，不将自动媒体用例通过描述为网页
真人全流程已经通过。持续视频、STM32 真实运动和静默随机表情恢复也不在本轮验收内。
详细测量、失败记录及固件二进制校验见配套 ESP32 仓库的
`docs/development/sdk-procedural-expression-fps-optimization-20261008.md`。

### 回声排查工具

SDK `ApplicationRtc` 的默认发送与会话确认等待窗口为每阶段 10 秒，覆盖设备停止媒体和释放
资源所需的时间；调用者显式传入的 `send_timeout` 仍然生效。超时仍报告失败，只有设备确认
停止或上报停止状态才释放会话所有权，不能用延长等待掩盖握手失败或设备掉线。

普通通话默认请求浏览器回声消除、降噪和自动增益；实际是否启用应读取麦克风轨道设置，
不能把请求参数当作生效证据。设备 AEC 是否处理音频、参考缺失、麦克风削波及处理前后
数字能量由配套固件 `evt.rtc.stats` 提供，均可保存在综合场景 JSON 报告中。

在测试页 URL 后添加 `?rtc_echo_probe=1` 可打开本地电脑回声对照面板。先停止 RTC，
选择一份语音 WAV，再运行对照：分别使用原始收音和浏览器 AEC 收音，播放音量为播放器的
8%，每段预热 1.5 秒、记录 6.5 秒。工具不把麦克风回接到扬声器，也不上传 PCM；
结果只包含能量、样本数、削波计数及不含设备标识的处理设置。正常页面不加载这个工具。

结果中的能量变化包含环境噪声，不等于纯回声衰减；原始与处理后的声道配置也可能不同。
无声音、采样异常或 AEC 未确认时不能得出有效比较。这个对照不能替代两个端点同时
说话的双讲测试，以及正常音量下的真人回音听感验收。

本机自动硬件验证可添加 `?rtc_hil=1`，把浏览器麦克风替换为仓库自带语音，并在每轮语音后
插入 8 秒静音。发送峰值限制为 PCM 满幅的 6%，语音源只接 RTC，不接电脑扬声器；已移除
原先的 880 Hz 持续尖音。停止或启动失败会关闭音频上下文及轨道。该模式验证真实浏览器
RTC、程序表情和拍照路径，不验证电脑麦克风 AEC，也不能代替真人双讲验收。

2026-10-08 最终候选的浏览器组合长测运行 30 分钟，30 次拍照均回传；发送丢帧约 1.38%，
结束后设备动态资源及 Application 所有权释放。但播放队列丢帧、约 0.5～1.4 秒的浏览器
播放缓冲、程序表情更新失败仍存在，不能将脚本完成解释为完整实时性或回声听感通过。
详细指标、失败证据和验收边界见配套 ESP32 仓库的
`docs/development/sdk-rtc-echo-e2e-validation-20261008.md`。

同一候选随后完成 5 轮网页启动/通话/拍照/结束，每轮约 45 秒，动态资源和所有权释放成功，
内部 RAM / DMA 停止后数值一致。恢复普通麦克风入口时停在权限等待阶段，自动测试未能
完成真实麦克风复测，已经取消场景；明早需先允许麦克风，再验证真实回声和双讲听感。

### 通用端侧模型调用

测试网页当前仅展示 3 号手势与 4 号人脸模型；人员和宠物模型从测试列表隐藏。
设备模型槽位与 SDK `vision.models()` 完整目录保持不变，不执行擦除。

On-device Model Test 使用 `vision.models()` 和 `vision.start_inference()`，支持读取原厂
1～3 号及人脸 4 号目录、无预览推理、同帧模型预览、最新结果读取和显式停止。
选择 3 号手势模型并点击 Start Model Preview 可查看画面、类别 ID 和置信度，不驱动云台。相机占用期间禁止启动
冲突功能；停止未确认时保留占用。详见 [模型推理合同](../../docs/vision-model-inference.md)。

### 机器人来音降噪

RTC 音频区新增本机 RNNoise 降噪开关和强度滑块，默认开启、强度 70%，
上限 85%。作用于机器人传回的声音；电脑麦克风原有浏览器 AEC/NS/AGC
仍分别处理电脑采集。页面显示麦克风 `getSettings()` 的实际状态，缺失值
明确标为“未报告”，不把请求参数当作生效证据。

处理链：RTC 来音 → 48 kHz 单声道 AudioWorklet → RNNoise → 原通话播放器。
开关和强度在现有图中更新，不重启 RTC，不改变 ESP32 音量或嘴形。
关闭开关播放对齐后的原声；算法保持热状态。适配和算法共引入约 20 ms
固定样本延迟，浏览器输出设备延迟另计。强度保留至少 15% 原声，不使用
按电平切断人声的门限。降噪不是回声消除，也不保证消除背景谈话。

模块和模型随页面本地提供，降噪不上传音频、不经 Python 中转、不增加设备内存。
浏览器不支持或处理器失败时明确提示并恢复原来音；停止/取消/离开页面会释放
处理图、输出轨道和 AudioContext，保留原 RTC 轨道的正常会话所有权。
CSP 仅新增 WebAssembly 编译许可 `wasm-unsafe-eval`，未开放 JavaScript eval。
依赖版本和许可见 `web/RNNOISE-NOTICE.md`。

验收时，在同一通话中切换开关，分别比较风扇等持续噪声、轻声、短暂停顿和
双方同时讲话；同步观察通话丢帧。降噪不能修复传输丢帧和声学回声同步问题。

### 本机三路通话录音诊断

通话中点击“录制 20 秒音频诊断”，并正常说话、轻声和稍作停顿。页面近同步
录制电脑麦克风、机器人 RTC 原声、机器人降噪后声音，附带每秒设备计数、
浏览器抖动缓冲、丢包/丢样补偿计数和处理前后 RMS。电脑轨道已经过浏览器
采集处理，机器人原声已经过固件处理；两者都不代表未经处理的物理麦克风 PCM。

录音仅提交到本机 Application 的 loopback HTTP 服务保存，不传云端。单路
上限 2 MiB，报告上限 64 KiB；仅允许校验后的 UUID 与固定通道组合，不能写任意路径。
每轮使用独立文件，三路音频与同编号报告齐全后才原子发布清单和“最近一次”指针；
部分失败或并行录制不会混用轮次，也不覆盖上一轮完整结果。网页下载链接固定指向本轮，
后续录制不会覆盖这些文件。同一编号的重复音频通道上传被拒绝。
报告内容完全一致时允许重试未完成的清单发布；不同内容仍拒绝覆盖。单文件发布失败
保留上一轮完整结果，重试不会需要重新录制音频。
WebM 上传只检查 EBML 签名，不宣称完整容器或音频轨道校验；服务不会解码这些上传文件。
停止通话会结束并保存已录制片段，不停止非本模块拥有的轨道。
历史批次目前不自动删除；需要释放本机空间时，在停止应用后归档本机 `artifacts/` 目录。

MediaRecorder 启动为近同步而非样本级同步；录音编码及浏览器处理有额外延迟，
不能把两个录音的相关峰差直接当成 RNNoise 算法延迟。对比应在同一固件、音量、
麦克风位置和降噪强度下，先测通话加程序表情，再测仅通话。结合计数增量分析
断续；没有削顶只能排除数字削顶迹象，不能排除扬声器硬件失真或 AEC 失真。
2026-10-09 同会话人工对照：关闭程序表情时用户确认喇叭清晰，20 秒内播放
队列新增丢帧为 0；仅重新开启表情后用户确认又断续/失真，20 秒内新增丢帧
24，限幅触发增量为 0。线索指向表情与音频并发时的调度或资源竞争，具体
瓶颈当时尚未完成定位。两轮为真实讲话，不能代替固定源负载基准测试。

同日后续配套固件改为复用固定中性眼睛、暂停通话随机视线，并按 SPD2010 的
4 像素横向边界传输变化区域。用户确认画面完整、喇叭清晰；约 100 秒观察
平均 20 FPS，绘制与传输合计平均 4.54 ms，完成两次通话中拍照和两轮退出重开。
20 秒真实语音窗口播放队列和上行新增丢帧均为 0；更长窗口仍累计播放队列
丢帧 5、上行丢帧 22，不能宣告全程零丢帧或所有网络环境稳定性通过。
实现、回归测试和验收边界见配套 ESP32 仓库的
`docs/development/sdk-rtc-procedural-incremental-render-20261009.md`。
