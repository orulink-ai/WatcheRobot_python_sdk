# 语音 Application 模板

本模板提供设备语音 → ASR → LLM → TTS → 设备播放。无需网页、skill、工具系统或长期记忆。
公共能力在 SDK 的 `watcherobot.voice` 中，生成项目的 `application/voice.py` 是用户拥有的组装入口。

## 基础模板与语音模板

基础模板只生成 `app.json`、`app.py`、`README.md`、`icon.svg`、`.gitignore` 五个文件：

```powershell
watcherobot app init my_app
```

语音模板先生成同一套基础文件，再增加语音代码、模型配置、凭据和提示词。
两者均内置于 SDK。生成新语音项目使用 `--template voice`；不会修改已有项目。
SDK 源码目录是 `src/watcherobot/templates/base/` 和 `src/watcherobot/templates/voice/`。
模板不复制 SDK 的测试或扩展文档。应用自己的使用说明在 README.md；示例应用单独维护在 examples/。
完整语音工程目录与首次运行步骤见[模板 README](../src/watcherobot/templates/voice/README.md)。

## 快速开始

语音模板内置在 SDK 安装包中。安装 SDK 后，初始化命令从本机包内生成工程，无需单独下载模板。
以下说明适用于包含 voice 模板的 SDK：

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
python -m pip install watcherobot

watcherobot --version
watcherobot --help
```

已安装 SDK 时可跳过安装，直接执行 `watcherobot app init my_voice_app --template voice`。
可用 `watcherobot app init --help` 查看本机 SDK 支持的参数。

机器人已联网时，在设备打开 Python SDK 应用，将以下配对码替换为屏幕当前六位码：

```powershell
watcherobot robot pair 123456
watcherobot robot status
```

首次配网可改用 `watcherobot robot setup` 完成 Wi-Fi 配置与配对。
创建语音应用只需以下命令，ID、作者和描述使用默认值，发布前再完善 `app.json`：

```console
watcherobot app init my_voice_app --template voice
cd my_voice_app
watcherobot app configure
```

按提示配置凭据，输入不回显，已有值按回车保留；也可直接编辑 `credentials/asr.toml`、`credentials/llm.toml`、`credentials/tts.toml`。
向导按当前 `config/models/*.toml` 的供应商提示字段，全部输入并通过本地校验后保存；输入期间 Ctrl+C 取消不保存。
命令不启动 Runtime 或连接设备，不调用云服务，不修改模型、音色、语言或提示词。更改后重启应用生效。
可指定项目路径：`watcherobot app configure ./my_voice_app`。基础模板没有凭据向导，仍可直接运行。
`${env:变量名}` 引用会保留，校验时需设置对应变量；设置 `WATCHER_VOICE_CREDENTIALS_DIR` 时与运行时使用同一凭据目录。
阿里云 ASR 默认提示 AppKey、AccessKey ID/Secret；已有非空 token 时使用 Token 方式。自定义供应商按凭据文件已有字段提示。

只更新某一项凭据时使用：

```powershell
watcherobot app configure --service asr
watcherobot app configure --service llm
watcherobot app configure --service tts
```

不传 `--service` 时配置全部。指定后只读取和更新所选服务，不要求另外两项已配置；
成功只代表该服务本地校验通过，运行完整应用前仍需补齐三项。模型、音色和供应商仍在配置文件中修改。

默认火山 ASR/TTS 各需要 `app_id` 和 `access_token`，通义需要 `api_key`。
在供应商控制台开通对应资源，凭据有效不等于已购买或获准使用全部模型/音色。

填写凭据且设备连接后，在项目目录执行：

```console
watcherobot app run
```

不要直接运行 `python app.py`：入口需要 Daemon 注入 Device channel 和 Desktop channel。
初始化只生成本地工程，不发布 SDK 或应用；应用开发完成后是否发布，由应用作者决定。

## 目录与配置合同

| 文件/目录 | 用途 | 发布 |
| --- | --- | --- |
| `config/models/{asr,llm,tts}.toml` | 供应商、模型、音色、端点、参数 | 是 |
| `credentials/{asr,llm,tts}.toml` | 本地凭据 | 否 |
| `credential-examples/` | 空白凭据示例 | 是 |
| `config/persona.toml` | `name`、`reply_style` 等用户提示词变量 | 是 |
| `prompts/system.md` | 默认提示词正文 | 是 |
| `config/conversation.toml` | 历史与录音限制 | 是 |
| `application/voice.py` | 注册适配器、组装应用 | 是 |
| `README.md` | Application 自己的说明 | 是 |

模板不提供统一语言选择，也不在默认提示词中固定回答语言。提示词、ASR 识别参数和 TTS 音色/合成参数由用户独立配置；可选的模型 `language` 字段仅用于支持它的服务适配器，不会自动写入提示词或其他模型配置。

模型配置支持 `schema_version = 1`；修改后重启生效。路径相对项目根目录解析。
启动前检查必填字段、类型、常见参数范围、未知顶层字段和未定义提示词变量。
供应商专有字段放入 `options` 或 `parameters` 表，由对应服务协议验证；不能覆盖运行库规定的输入、流模式和音频格式。
模型和音色没有白名单；新 ID 是否可调用由供应商决定。NLS 模型由项目 AppKey 选择。

凭据可以直接填写，也可以引用进程环境变量：

```toml
api_key = "${env:QWEN_API_KEY}"
```

不会自动读取 `.env`。提示词仅替换 `{{name}}` 等变量，不执行 Python；用户说的话不进行二次模板解析。

## 首批供应商与配置

下列 provider 名称为内置工厂注册名。ASR 的 `base_url` 是完整 WebSocket 端点；
火山 TTS 的 `base_url` 是完整 HTTP 合成端点；其他 HTTP 适配器是 API 根路径（见下表）。
自定义端点不附带查询、片段或 URL 用户密码。请使用与账号区域匹配的 HTTPS/WSS 端点。

| 环节/provider | 模型配置示例 | `credentials/<环节>.toml` 字段 | 默认端点 |
| --- | --- | --- | --- |
| ASR `volcengine` | `resource_id = "volc.bigasr.sauc.duration"` | `app_id`, `access_token` | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` |
| ASR `aliyun` | `provider = "aliyun"`；模型在 NLS 项目中配置 | `app_key`, `token`；或 `app_key`, `access_key_id`, `access_key_secret` | `wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1` |
| ASR `deepgram` | `model = "nova-2"`, `language = "zh-CN"` | `api_key` | `wss://api.deepgram.com/v1/listen` |
| LLM `qwen` | `model = "qwen-flash"` | `api_key` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| LLM `ark` | `model = "deepseek-v3-2-251201"`，也可填 `ep-…` | `api_key` | `https://ark.cn-beijing.volces.com/api/v3` |
| LLM `openai_chat` | 必填 `model`、`base_url` | `api_key`（本地无认证服务可留空） | 无，必须填写 |
| TTS `volcengine` | `resource_id = "seed-tts-2.0"`, `voice_id = "zh_female_vv_uranus_bigtts"` | `app_id`, `access_token` | `https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| TTS `deepgram` | `model = "aura-2-thalia-en"`（英语预设） | `api_key` | `https://api.deepgram.com/v1` |
| TTS `cartesia` | `model = "sonic-3.5"`, `voice_id = "账号选择的音色 ID"` | `api_key` | `https://api.cartesia.ai` |
| TTS `elevenlabs` | `model = "eleven_multilingual_v2"`, `voice_id = "账号选择的音色 ID"` | `api_key` | `https://api.elevenlabs.io/v1` |
| TTS `minimaxi` | `model = "speech-2.8-turbo"`, `voice_id = "male-qn-qingse"` | `api_key` | `https://api.minimax.cn/v1` |

切换供应商时同时替换模型配置和凭据文件。以上是示例，不承诺所有账号、语言、音色组合均可调用。
火山 TTS 音色标识不是模型名，需匹配资源版本。ElevenLabs PCM 输出需满足账号套餐权限。
阿里云 AK/SK 会通过官方 CreateToken 接口换取并按过期时间缓存临时 token；手填 token 过期后须替换。
Cartesia 默认请求版本为 `2026-08-14`，可在 `[options]` 中设置 `api_version`。

例如通义配置：

```toml
schema_version = 1
provider = "qwen"
model = "qwen-flash"
timeout = 45

[parameters]
temperature = 0.7
max_tokens = 1024

[options]
enable_thinking = false
```

TTS 专有参数按服务原生嵌套结构配置，例如 MiniMax 的 `[parameters.voice_setting]` 中设置 `speed = 1.1`；
火山用 `[parameters.audio_params]`，ElevenLabs 用 `[parameters.voice_settings]`，Cartesia 用 `[parameters.generation_config]`。
不要把 Key 填进 `parameters` 或 `options`。内置 TTS 固定请求设备所需 PCM 格式。

## 可选模型/音色查询

查询是独立 Python API，不是启动前必经步骤，不修改任何配置文件：

```python
import asyncio
from pathlib import Path
from watcherobot.voice.configuration import load_configuration
from watcherobot.voice.catalog import discover

async def inspect():
    config = load_configuration(Path('.'))
    result = await discover('llm', config.llm)
    print(result.supported, result.scope, result.note)
    for item in result.items:
        print(item.id, item.name)
    # TTS 音色：await discover('tts', config.tts, voices=True)

asyncio.run(inspect())
```

| 供应商 | 查询实现 |
| --- | --- |
| Deepgram | ASR/TTS 公共模型目录；TTS 模型即音色选择。目录接口固定为官方公共 API，不复用 ASR WebSocket 端点 |
| 通义 / OpenAI 兼容 | 根路径 `/models`；返回供应商目录，需自行确认所选模型支持文本聊天 |
| ElevenLabs | TTS 模型与账户音色；音色分页读取 |
| Cartesia | 账户可见音色，分页读取；模型手动填写 |
| MiniMax | 系统及账户音色；模型手动填写 |
| 火山语音 / 方舟 | 当前适配器明确返回不支持查询；管理面查询可能需要额外 AK/SK，此版不代为获取管理权限 |
| 阿里云 NLS | 在控制台项目中选择识别模型，无通用模型列表 |

公共目录、账户资源和“已验证可调用”是不同概念。查询失败直接报错，用户仍可手动填写 ID。
分页最多 100 页，异常/重复游标会报错，不把截断结果伪装成完整目录。

## 会话行为

- 默认交互为“说完 → 回答 → 继续听”，不提供语音抢话打断或额外的打断配置。半双工：收到真实语音才连接 ASR；录音输入结束释放麦克风，回答期间不采集设备音频。
- 三家 ASR 边接收音频边上传，统一输出临时文本和整轮最终文本；空文本不调用 LLM。
- LLM 使用 SSE 流，按中文句末标点或长度分段；文本队列最多两段，自然施加背压。
- TTS 按段合成、按顺序播放；播放时提前合成后续段，待播队列最多一段，减少句间等待。当前设备 API 需要完整单段音频，因此不是无限连续播放流。
- 内置输出为 24 kHz、单声道、S16LE PCM；不把 WAV 头作为 PCM 播放。单段最大 4 MiB。
- 自定义 TTS 必须在输出前完成解码/重采样；会话拒绝不匹配格式，避免噪音。
- MiniMax 使用非流式单段合成；其他内置 TTS 接收流式响应，设备仍按完整段播放。
- 静音默认 800 ms 结束，最长输入 30 秒，前置音频 300 ms；能量阈值默认 550，需按环境调节。
- 最多保留六轮历史、回答最多 4000 字符；取消或未完整播放的回复不会提交到历史。
- 关闭桌面麦克风会取消整轮（包括回答和播放）；打开后恢复聆听。未知桌面消息安全忽略。
- 每两秒通过 SDK 公共 ready 请求检查设备；断连取消轮次并清空历史，恢复后建立新会话。
- 停止、取消均等待正在进行的 SDK 阻塞操作返回，再清理资源；不会留下晚到的开麦/播放任务。
- 默认仅记录阶段性状态及安全错误，不记录音频、正文或凭据。服务端仍按各自数据政策处理请求。

业务始终位于 Application，经 Daemon 注入的通道通信。没有修改 Daemon 路由，未创建第二条设备业务连接。

## 应用定义与 Python 接口

`app.json` 是应用清单，使用 JSON，`schema_version` 为 **2**；模型配置使用 TOML，`schema_version` 为 **1**。两者不能互换。
`app.py` 是固定入口文件，不在清单中填写 `entrypoint`、配对码或模型 API Key。
保留初始化器生成的 `requires_watcherobot` 兼容范围，不要手工降级到不含语音功能的 SDK。

| app.json 字段 | 类型与含义 |
| --- | --- |
| `schema_version` | 整数，当前为 `2` |
| `id` | 字符串，应用唯一 ID；本地自动生成，发布前确认 |
| `name`、`description`、`author` | 字符串，应用名称、描述、作者 |
| `version` | 字符串，应用自己的版本号 |
| `requires_watcherobot` | 字符串，SDK 版本约束，由初始化器生成 |
| `dependencies` | 字符串数组，应用额外的 Python 依赖；无额外依赖时为 `[]` |
| `supported_host_platforms` | 字符串数组，当前支持 `windows`、`macos` |
| `icon` | 字符串，项目相对路径，默认 `icon.svg` |

配置文件必须保持独立：`config/models/asr.toml`、`llm.toml`、`tts.toml` 不能合成一个 TOML 文件。
`provider`、`model`、`voice_id`、`resource_id`、`base_url` 是字符串，`timeout` 是 1–300 秒的数值。
`parameters` 和 `options` 是 TOML 表。顶层字段必须写在 `[parameters]` / `[options]` 之前；表标题之后的键属于该表。
例如 `[parameters]` 之后再写 `provider` 不会切回顶层。凭据只写到对应的 `credentials/*.toml`。

`application/voice.py` 中的 `async def main(root: Path) -> None` 是此模板自己的组装函数，不是 SDK 要求所有应用实现的接口。
模板的 `app.py` 调用它；用户从终端执行 `watcherobot app run`，由 Runtime 启动入口并注入连接信息。
`ApplicationContext` 从 `watcherobot.application` 导入，使用 `async with ApplicationContext.from_environment() as app:` 管理生命周期。
`app.robot` 是设备接口，`app.desktop` 是桌面通道；二者均使用已注入的连接。

适配器合同从 `watcherobot.voice.contracts` 导入；注册器从 `watcherobot.voice.providers` 导入 `ProviderRegistry`。

| 接口 | 准确签名 | 返回内容 |
| --- | --- | --- |
| ASR | `transcribe(audio: AsyncIterator[bytes]) -> AsyncGenerator[Transcript, None]` | 输入为 16 kHz、单声道 S16LE PCM；最终 `Transcript(text, final=True)` 是整轮完整文本，不是增量 |
| LLM | `generate(messages: Sequence[Message]) -> AsyncGenerator[str, None]` | 输入 `Message(role, content)` 序列；逐次 yield 文本增量 |
| TTS | `synthesize(text: str) -> AsyncGenerator[AudioChunk, None]` | 逐次 yield 音频块；播放要求 24 kHz、单声道、S16LE PCM |
| 资源释放 | `async def close(self) -> None` | 关闭适配器持有的连接和其他资源 |

上面三个流接口均使用 `async def` 加 `yield` 实现，调用方使用 `async for`；不能写成返回列表的普通函数或仅 return 的协程。
`register_asr/llm/tts(name, factory)` 的 factory 是接收 `ModelConfig` 并返回适配器实例的同步工厂。
配置中的 `provider` 必须与注册名称一致；先注册，再运行 `VoiceApplication(app, config, registry).run()`。

## 二次开发：一个完整的本地 LLM 适配器

在生成项目中新建 `application/local_llm.py`，内容如下。这个适配器用于验证扩展接口，返回固定文本，不调用云模型：

```python
from collections.abc import AsyncGenerator, Sequence
from watcherobot.voice.configuration import ModelConfig
from watcherobot.voice.contracts import Message


class LocalLLM:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    async def generate(self, messages: Sequence[Message]) -> AsyncGenerator[str, None]:
        yield "你好，我是 watcher。自定义 LLM 适配器已接入。"

    async def close(self) -> None:
        pass
```

在 `application/voice.py` 顶部增加 `from application.local_llm import LocalLLM`，
在 `create_registry()` 中，紧接已有 `registry = ProviderRegistry.builtin()` 后增加 `registry.register_llm("local_demo", LocalLLM)`。
将 `config/models/llm.toml` 的顶层 `provider` 改为 `"local_demo"`。
`credentials/llm.toml` 文件仍需存在，此本地适配器不使用密钥，可留为空文件。
ASR/TTS 继续使用原配置和有效凭据。执行 `watcherobot app run` 后，设备识别到语音会播放上述固定回复。

切回云模型时恢复原 provider 和凭据。实际自定义云适配器应在 generate 中调用服务，并在 close 中释放客户端；
不得吞掉取消异常。工厂负责自己的凭据校验，错误使用不包含密钥、正文或服务原始响应的 VoiceError。

## 发布与升级

先执行 `watcherobot app check .`，通过 `watcherobot app login --provider huggingface` 登录，
再用 `watcherobot app publish . --provider huggingface` 发布公开源码快照。
发布后执行 `watcherobot app submit . --provider huggingface --commit "替换为发布返回的40位commit"` 提交应用广场审核；
发布源码本身不会修改广场目录。也可将上述命令的供应商统一改为 `gitee`。
真实 `credentials/` 被发布收集器强制排除；
模板不记录音频或对话正文。二次开发如增加本地录音或日志，发布前用 `.watcherignore` 排除；`.gitignore` 仅控制 Git。保留源码、提示词、默认模型配置与空白凭据示例。
不能把真实凭据写入源码或其他配置来绕过排除规则。

安装者将空白示例复制到下列目录并填写自己的凭据：

- Windows：`%LOCALAPPDATA%/watcherobot/applications/<app-id>/credentials/`
- macOS：`~/Library/Application Support/watcherobot/applications/<app-id>/credentials/`
- Linux：`$XDG_CONFIG_HOME/watcherobot/applications/<app-id>/credentials/`，未设置时使用 `~/.config/`。

`WATCHER_VOICE_CREDENTIALS_DIR` 可显式覆盖目录；相对路径始终按项目根目录解析，与启动进程的工作目录无关。本地源码开发优先使用项目 `credentials/`；已发布包无该目录，
自动使用应用用户目录。升级包不覆盖用户凭据。生成项目记录当前 SDK 兼容范围，SDK 升级不会重写用户源码。
语音模块使用跨平台 Python；现有应用清单仅接受 Windows、macOS，Linux 不在当前应用广场的主机支持声明中。
Linux 凭据路径可供 SDK 开发测试使用。本功能未新增平台系统脚本。

## 验证与排障

在生成的应用项目目录执行：

```powershell
python -m pip install pytest
python -m pytest tests
watcherobot app check .
watcherobot app run
```

生成的测试只检查基础配置，不会调用云服务。运行后对设备说“你好，请介绍自己”，确认听到回复；
再问“我刚才问了什么”，确认多轮上下文。修改模型、提示词或 Python 代码后重复检查。
`app check` 检查应用工程，不证明凭据权限或真实设备链路可用。

SDK 维护者在 SDK 源码仓库执行协议与运行测试，以及完整 pytest、mypy 和 wheel/sdist 构建。

测试包含协议帧、服务错误、目录分页、半双工、有界队列、取消迟到结果、阻塞设备操作清理、凭据过滤及配置校验。
模拟协议通过不能证明供应商账号权限。真实云端与真机结果应分别记录。

- 缺配置：按报错给出的文件和字段补齐。
- HTTP 401/403：检查当前供应商和区域、Key 类型、Token 过期与资源权限。
- 没有设备：先 `watcherobot robot status`，再按现有配对流程连接；应用会等待。
- ASR 失败：检查资源、NLS 项目配置、麦克风丢帧和录音能量阈值。
- TTS 失败：确认模型与音色/资源匹配，以及 PCM 权限。
- 替换适配器：先用生成项目测试，再验证取消、异常和实际设备。

### 协议参考

- [阿里云 NLS WebSocket](https://help.aliyun.com/zh/isi/developer-reference/websocket)
- [阿里云 Token](https://help.aliyun.com/zh/isi/getting-started/use-http-or-https-to-obtain-an-access-token)
- [Deepgram ASR](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
- [Deepgram 模型目录](https://developers.deepgram.com/reference/manage/models/list)
- [Deepgram PCM 输出](https://developers.deepgram.com/docs/tts-media-output-settings)
- [Cartesia TTS](https://docs.cartesia.ai/api-reference/tts/bytes)
- [ElevenLabs TTS](https://elevenlabs.io/docs/api-reference/text-to-speech/stream)
- [MiniMax TTS](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)
- 火山 ASR/TTS 实现同时参考仓库已有 `first_meeting` 协议，并通过默认组合真实调用校验。

## 应用开发闭环

模板是创建项目的起点。生成的文件属于你自己的 Application，可以修改、提交到自己的仓库和发布；SDK 升级不会自动重写这些文件。
现有项目不能再次运行 `app init` 覆盖，直接编辑生成的项目即可。

二次开发通常从修改 `config/models/`、`config/persona.toml` 和 `prompts/system.md` 开始。
进一步修改 `application/voice.py`，可以注册自己的供应商适配器，或者替换当前 `VoiceApplication` 的组装逻辑。
通用供应商和会话实现位于 SDK 的 `watcherobot.voice`，不会全部复制到项目；需要完全自定义流程时，可在自己的 Application 中编写并维护实现。
保留 `ApplicationContext.from_environment()` 获取 Daemon 注入的通道，通过 Application 的设备接口操作机器人。

修改后在项目目录执行 `watcherobot app check .`，停止旧应用后重新执行 `watcherobot app run`。
应用完成修改后，完善 app.json 的身份、版本和说明，使用前述 publish / submit 流程发布；安装者填写自己的凭据，再运行对话。
