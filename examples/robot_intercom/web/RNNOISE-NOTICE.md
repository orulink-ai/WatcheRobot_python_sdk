# Bundled RNNoise

`rnnoise-vendor.mjs` is an unmodified copy of `dist/rnnoise-sync.js` from
`@jitsi/rnnoise-wasm` **0.2.1** on the npm registry. It embeds the RNNoise 0.2
WebAssembly model and requires no CDN requests, microphone upload, or build step.
The package's asynchronous 0.1 binary is intentionally not used for suppression.

Package source: https://github.com/jitsi/rnnoise-wasm

Upstream algorithm: https://github.com/xiph/rnnoise

Verified npm archive integrity:
`sha512-iEj77www43pS2Yq+cfLZb+hFuI7L5ccisBzzPMcOjjLsG4/LAlkD1CY58/8gc84nHdLBGmD/OPIWGnvYnXvB0A==`

Jitsi's Apache-2.0/MIT notices are preserved in `RNNOISE-LICENSE.txt`.
RNNoise's BSD-3-Clause notice is preserved in `RNNOISE-UPSTREAM-LICENSE.txt`.
