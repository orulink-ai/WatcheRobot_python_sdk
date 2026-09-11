# SDK Test Bench

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

The tested 2026-09-09 SDK/ESP32 pairing, concurrent video/audio results, and
remaining limits are fixed in the [Himax media stage record](../../docs/himax-media-stage-20260909.md).

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

### 通用端侧模型调用

测试网页当前仅展示 3 号手势与 4 号人脸模型；人员和宠物模型从测试列表隐藏。
设备模型槽位与 SDK `vision.models()` 完整目录保持不变，不执行擦除。

On-device Model Test 使用 `vision.models()` 和 `vision.start_inference()`，支持读取原厂
1～3 号及人脸 4 号目录、无预览推理、同帧模型预览、最新结果读取和显式停止。
选择 3 号手势模型并点击 Start Model Preview 可查看画面、类别 ID 和置信度，不驱动云台。相机占用期间禁止启动
冲突功能；停止未确认时保留占用。详见 [模型推理合同](../../docs/vision-model-inference.md)。

### 分发清单

应用清单使用 schema 2，显式安装 FastAPI、Uvicorn、Pydantic，声明最低 SDK 0.1.7。当前分发范围为 Windows/macOS；Linux 不在此清单的已声明平台内。本次只修复清单，不发布新的 Marketplace 或 PyPI 版本；干净设备安装仍需发布前验收。
