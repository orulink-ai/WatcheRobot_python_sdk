# SDK Media Lab

SDK Media Lab is a standalone managed Application for real-device media
acceptance. It serves a loopback-only browser dashboard and never opens a
device connection of its own.

Start the Runtime, then run:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

The Application opens its `http://127.0.0.1:<port>` dashboard automatically.
Set `WATCHER_MEDIA_LAB_NO_BROWSER=1` to suppress automatic browser launch.

When no device is connected, enter the six-digit code shown on the Watcher in
the dashboard's **Connect robot** panel. The local Application sends that code
only to the SDK Daemon management endpoint; it does not store the code or route
it through an Application business channel. A device paired before launch is
reused automatically. If the LAN suppresses broadcast discovery, the optional
device IPv4 field sends the same pairing request directly to that same-subnet
address while the normal broadcast discovery remains enabled.

The dashboard tests host-to-device PCM playback, one-shot JPEG capture,
decoded microphone recording, capability discovery, artifacts, diagnostic
events, and a live camera preview. The live preview uses `watcher-rtc/1` only
for signaling through the current Application's Device channel; MJPEG frames
travel directly from the Watcher to the browser over an unordered,
partially-reliable WebRTC data channel named `mjpeg-data`.

Live preview requires firmware that advertises `rtc.video.mjpeg.v1`. It keeps a
heartbeat while the page is open, uses latest-frame-wins rendering, and releases
camera resources when stopped, disconnected, or when the page closes. Camera
and microphone actions capture the surrounding environment; obtain consent
before use and handle generated artifacts appropriately.
