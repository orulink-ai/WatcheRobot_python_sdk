# ElevenLabs UI visual components

Orb and BarVisualizer originate from https://github.com/elevenlabs/ui at
commit `23c31bd3088814215a8b5d0bfb56b327cad1a578`, installed from the official
registry JSON using the shadcn CLI. See `ELEVENLABS-LICENSE.txt` (MIT).

Adaptations: Orb uses a deterministic, locally generated smooth noise texture
instead of downloading the upstream CDN texture. Device pixel ratio is bounded,
the texture is released on unmount, and non-active/reduced-motion views use a
static CSS orb. BarVisualizer skips animation scheduling for a constant frame
sequence; its FFT slice is adapted to bins 2–128 to include speech fundamentals
and formants. Audio uses the existing call streams, never demo amplitudes or another
microphone acquisition. Components only borrow streams and release their own
analysers. Visual errors fall back independently of RTC.

`visuals.js` is built from `../frontend/` with npm's cross-platform build command.
The pinned dependency tree is in its lockfile. `VISUALS-LICENSES.txt` contains
licenses and versions of packages included in the bundle; `visuals.js.LEGAL.txt`
retains source license comments. React Three Fiber's license is vendored from
its matching upstream release because the npm package omits the license file.
Runtime does not contact a CDN or the ElevenLabs service.
