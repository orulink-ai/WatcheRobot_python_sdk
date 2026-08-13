# SDK Test Bench

SDK Test Bench is a standalone managed Application for whole-robot hardware
acceptance. It serves a loopback-only browser dashboard, exercises only public
Python SDK domains, and never opens a device connection of its own. The
`sdk_media_lab` directory and Application id remain stable for compatibility.

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
decoded microphone recording, capability discovery, artifacts, diagnostic
events, a live camera preview, and full-duplex RTC audio. The live preview uses
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
