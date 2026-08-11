# SDK Media Lab

SDK Media Lab is a standalone managed Application for real-device media
acceptance. It serves a loopback-only browser dashboard and never opens a
device connection of its own.

Start the Runtime, pair the Watcher, then run:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

The Application opens its `http://127.0.0.1:<port>` dashboard automatically.
Set `WATCHER_MEDIA_LAB_NO_BROWSER=1` to suppress automatic browser launch.

The first version tests host-to-device PCM playback, one-shot JPEG capture,
decoded microphone recording, capability discovery, artifacts, and diagnostic
events. Camera and microphone actions capture the surrounding environment;
obtain consent before use and handle generated artifacts appropriately.
