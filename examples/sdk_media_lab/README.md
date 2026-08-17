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
Camera, microphone, and speaker also have separate leases. Audio-only RTC owns
the microphone and speaker but leaves one-shot camera capture available.
Video-only RTC owns the camera but leaves either standalone speaker playback or
standalone microphone recording available. Combined AV owns all three media
leases. A standalone speaker action still excludes another speaker action, and
the same rule applies independently to camera and microphone. Combined AV is one
firmware session (`mode=av`), not two peer connections competing for the same
codec, camera, network, and teardown resources.

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
