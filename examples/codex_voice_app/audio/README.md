# 固定语音参考音源

`speaker-reference.wav` 是先前清晰度 A/B 测试使用的固定音源，Windows SAPI Huihui 合成，单声道、16-bit PCM、22050 Hz、约 9.87 秒，不是现场录音，也不是 Codex 声线。应用运行时不依赖 Windows SAPI 或外部 TTS。

内容：“你好 Codex，这是语音延迟测试，编号731，请只回复，收到编号731。”

机器人测试不会启动 Codex；麦克风 RTC 帧只丢弃，不转发云端、不保存。当前 DSP 与真实通话共用 `PlaybackGain`，经同一 `DeviceRtcPeer` 和 Application Device channel 播放。浏览器原音试听不经过机器人或 DSP，不可用于比较机器人音量。

转换到 48 kHz 后源 PCM SHA-256：`90db15ab702ff9687a47e5eeb51e12c0599fae625e5cded6db240e5096ff1134`。
