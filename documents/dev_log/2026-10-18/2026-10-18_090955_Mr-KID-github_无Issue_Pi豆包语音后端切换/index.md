# Pi Application 豆包语音后端切换

- 创建时间：2026-10-18T09:09:55.619896+08:00；作者：Mr-KID-github / Codex；无 Issue。
- 用户授权：机器人仅做音频输入输出，由Application增加豆包ASR与TTS；页面切换机器人JoyInside与Application豆包，测试驱动实现并实际验证。
- 边界：机器人采音/扬声器使用Daemon注入Device channel，Daemon业务转发保持透明；Pi仍负责文字Agent/MCP，不替换为豆包LLM；不使用Mac麦克风或扬声器冒充机器人实测。
- 状态：已实现并完成本机部署、真实云端与机器人首轮验收；本地修改，无commit/push。凭据只保存在用户级Application私有目录0600，不进入仓库/日志/API。
- 设计：复用SDK既有火山StreamingASR、CloudTTS与SDKVoiceDevice；Application会话后端与ConversationService解耦；暂停清理完成后才能切换，ASR到Pi再到TTS保持半双工。
- 验收：离线协议/切换/取消/无误读凭据测试；短合成音频→ASR真实云测试；设备在线时录音与播放联测；页面切换真实状态。无真实设备时明确保留硬件验收缺口。
- 官方合同：豆包ASR wss://openspeech.bytedance.com/api/v3/sauc/bigmodel，TTS https://openspeech.bytedance.com/api/v3/tts/unidirectional；AppId/AccessToken旧控制台鉴权，Secret Key不用于这两条语音API，不额外存储未使用的Secret Key。

## 实现与 TDD

- 新增独立 speech_backends.py：JoyInside透传现有speech接口；豆包复用SDKVoiceDevice、StreamingASR、CloudTTS，机器人只做PCM输入输出，不依赖ctrl.speech命令。
- ConversationService统一调用同步/异步会话，保留Pi多轮上下文及专名提示词；会话打开、执行、清理未完成时禁止切换，清理失败保留资源所有权。页面增加模式选择，speech_preferences.py原子保存用户选择，不存服务凭据。
- 配置：用户级doubao.json仅存语音调用必需的AppId/AccessToken，权限0600；扫描本轮源码/文档未发现真实AccessToken。API只提供configured及固定安全提示。
- Backend TDD：最初20项Red（缺模块）→20Green；补充静音与服务超时区别等用例Red→最终25项backend用例通过。覆盖未配置、无效配置、符号链接、PCM格式/大小、流关闭、取消、迟到响应不播放、释放失败重试。
- Service TDD：新增6项切换/异步语音集成测试均先Red；偏好文件4项先Red。追加桌面快捷键在清理未确认时不能终止控制循环的用例，先失败后修复；验证清理保留占用、设置写失败不改变选择。
- 前端先缺少selector的Red，再补状态/交互；Node VM覆盖选择、忙碌锁、缺失配置、POST参数、服务器确认后渲染、失败回滚、配对保持。另复现轮询反复重建下拉菜单问题并修复，测试防止用户选择被刷新打断。
- 最终回归：.venv/bin/python -m pytest tests/test_pi_speech_backends.py tests/test_pi_speech_preferences.py tests/test_pi_speech_web.py tests/test_pi_speech_pairing.py tests/test_pi_speech_application.py tests/test_speech.py tests/voice/test_device.py tests/voice/test_runtime.py tests/voice/test_asr.py tests/voice/test_providers.py --tb=short：129 passed，1条既有Starlette警告。
- node tests/web/pi_speech_ui.cjs、node --check及git diff --check通过。尝试扩大到全部tests/voice时，未改模块test_configure收集依赖dulwich缺失；未安装额外依赖，改用覆盖实际复用模块的上述定向回归，不将失败收集记成通过。

## 真实云端与页面验证

- 用户提供凭据通过豆包TTS：seed-tts-2.0 / zh_female_vv_uranus_bigtts，HTTP200，完整24kHz单声道PCM124998字节（约2.6秒）；转16kHz后送入ASR volc.bigasr.sauc.duration，WS101，最终文本精确为“你好，这是语音连接测试。”。
- 共3次TTS（前2次测试脚本误读AudioChunk.pcm而提前结束，修正为.data后第3次完整）和1次ASR，未重复试探资源或购买权限；合成测试音频与安全报告只保留ignored目录。
- 实际Pi RPC文字测试通过，回复“语音备用方案测试通过。”；该合成验证明确禁用工具/MCP，不触发手机动作。真实Application保持原Pi/MCP配置。
- ego-browser真实页面选择豆包后侧栏同步，重新加载仍为豆包；390px视口宽度与内容宽度一致，入口可见，无横向溢出。浏览器保留结果页供用户继续。

## 机器人首轮验收

- 用户在页面完成当前机器人配对，Daemon模式python_sdk且online=true。用户点击开始后，Application豆包真实识别到其问候，Pi返回回复，豆包合成后设备确认播放完成，turns=1并恢复listening。
- 随后调用正常暂停API，确认paused、enabled=false、error为空、speech_switch_available=true，设备保持connected；音频资源释放已确认，不以进程退出代替正常清理。
- 用户明确确认“听到了，播放完整”，因此首轮实际机器人麦克风→豆包ASR→Pi→豆包TTS→机器人扬声器已验收通过。
- 当前已选豆包、应用暂停、设备在线。JoyInside选项仍使用旧设备speech接口，之前已定位的固件接口缺失未在本轮修复，不能宣称JoyInside模式已实测恢复。
- 未进行长时间稳定性、噪声环境、连续多轮压测或真实播放中抢话验收。本方案保持半双工。

## 官方合同参考

- https://docs.volcengine.com/docs/DoubaoVoice/LargemodelstreamingautomaticspeechrecognitionAPI?lang=zh
- https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-text-to-speech-http?lang=zh

## 后续操作

- 打开当前Application页面，在暂停且资源释放完成后可切换语音后端；按“开始对话”恢复连续轮次。
- 资源/音色可通过私有doubao.json配置，下一次打开会话读取；切换选择保存在相邻用户级speech-settings.json。
- README已补两种后端、私有配置路径、调用资源与测试边界。没有改动Daemon业务路由或固件，没有commit/push。

## 后续变更
2026-10-18｜Mr-KID-github｜补充：[Pi动作表情与语音延迟](../2026-10-18_092500_Mr-KID-github_无Issue_Pi动作表情与语音延迟/index.md)，保留后端切换和已完成播报验证，新增行为及性能改进。


## 后续变更
2026-10-27｜KID / Codex｜[PCM配套合入](../../2026-10-27/2026-10-27_181300_KID_无Issue_PCM配套合入/index.md)：在最新目标分支整合并重新回归；原有硬件记录保留为历史。
