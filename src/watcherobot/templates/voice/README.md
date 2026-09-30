# 语音 Application

这是在 SDK 五文件基础模板上扩展的 voice 语音应用工程，创建命令为 `watcherobot app init my_voice_app --template voice`。
模板资源随 SDK 安装，初始化时从本机包内复制，无需下载其他仓库。

这是你自己的语音应用工程。默认流程为：设备收音 → 火山 ASR → 通义 qwen-flash → 火山 TTS → 设备播放。
默认角色名为 watcher；说完后回答，回答结束继续听。首次使用只需填凭据、连接设备，不需要配置打断、工具或长期记忆。

## 1. 先看目录

项目名以 my_voice_app 为例，实际以你初始化时输入的目录为准：

```text
my_voice_app/
├── app.json                       # 应用 ID、名称、版本、依赖；初始化器生成
├── app.py                         # Runtime 启动的固定入口
├── icon.svg                       # 应用图标
├── README.md                      # 本使用说明
├── .gitignore                     # 忽略本地凭据与缓存
├── application/
│   ├── __init__.py
│   └── voice.py                   # 应用组装和自定义适配器注册入口
├── config/
│   ├── models/
│   │   ├── asr.toml               # 识别服务
│   │   ├── llm.toml               # 语言模型
│   │   └── tts.toml               # 合成服务和音色
│   ├── persona.toml               # 名字、回答风格
│   └── conversation.toml          # 历史、静音结束时间、长度限制
├── credentials/                   # 仅存本机，填写你自己的凭据
│   ├── asr.toml
│   ├── llm.toml
│   └── tts.toml
├── credential-examples/           # 可发布的空白凭据示例
│   ├── asr.toml
│   ├── llm.toml
│   └── tts.toml
└── prompts/
    └── system.md                  # 提示词
```

下面所有命令都在包含 app.json 的项目目录执行。不要把说明中的多个配置块粘贴到同一个文件。

## 2. 配置凭据

在项目目录执行：

```powershell
watcherobot app configure
```


只更新某一项凭据时使用：

```powershell
watcherobot app configure --service asr
watcherobot app configure --service llm
watcherobot app configure --service tts
```

不传 `--service` 时配置全部。指定后只读取和更新所选服务，不要求另外两项已配置；
成功只代表该服务本地校验通过，运行完整应用前仍需补齐三项。模型、音色和供应商仍在配置文件中修改。

按提示填写 ASR、LLM、TTS 的凭据，输入不回显；已有值按回车保留。
向导根据 `config/models/*.toml` 中的供应商提示字段，不改变模型、音色、语言或提示词。
全部输入完成并通过本地配置校验后才保存；输入期间按 Ctrl+C 取消，不保存修改。
命令不启动 Runtime、不连接设备、不调用云服务，因此“配置通过”不等于云服务授权通过。
也可从其他目录执行 `watcherobot app configure ./my_voice_app`（路径替换为项目实际位置）。

默认写入下列三个文件，也可以直接用文本编辑器填写。保持 TOML 格式，密钥必须保留双引号。

`credentials/asr.toml`：

```toml
app_id = ""
access_token = ""
```

`credentials/llm.toml`：

```toml
api_key = ""
```

`credentials/tts.toml`：

```toml
app_id = ""
access_token = ""
```

ASR/TTS 使用火山语音应用的 APP ID 和 Access Token；LLM 使用通义 DashScope API Key。
火山 ASR 资源 `volc.bigasr.sauc.duration` 和 TTS 资源 `seed-tts-2.0` 都需要开通。
同账号凭据可以相同，但两个资源的调用权限要分别满足。不要把火山 Secret Key 当作 Access Token。
凭据支持 `${env:变量名}` 引用，向导回车保留时不会把引用替换成明文；本地校验时该环境变量须已设置。
如果设置了 `WATCHER_VOICE_CREDENTIALS_DIR`，向导写入该目录，与运行时保持一致。
阿里云 ASR 默认提示 AppKey、AccessKey ID 和 AccessKey Secret；已有非空 `token` 时保留 Token 方式。
自定义供应商可先在对应凭据文件定义自己的字段，再运行向导。
这些文件的空值不是真实凭据，不填就不能启动语音对话。不要把密钥写入 config、提示词或 Python 源码。

## 3. 配对设备并运行

设备已联网时，打开设备上的 SDK / Desktop Link 连接页。将 123456 替换为屏幕当前六位码：

```powershell
watcherobot robot pair 123456
watcherobot robot status
```

设备还没有配置 Wi-Fi 时，用下面的引导代替上面的 pair 命令，完成配网和配对：

```powershell
watcherobot robot setup
```

确认 robot status 显示设备已连接后运行：

```powershell
watcherobot app run
```

配对由 SDK Runtime 管理，应用复用注入的设备连接，不保存一次性配对码。USB 调试线不替代网络连接。
不要直接执行 python app.py；它需要 Runtime 注入的应用身份和通道。

对设备说“你好，请介绍自己”，停顿约一秒，确认能听到回复。再问“我刚才问了什么”，检查对话历史。
停止时在运行终端按 Ctrl+C，或另开相同环境的终端执行 watcherobot app stop。

## 4. 修改模型、音色和提示词

下面是已生成的默认配置；首次使用无需重写。每段对应一个独立文件。

`config/models/asr.toml`：

```toml
schema_version = 1
provider = "volcengine"
resource_id = "volc.bigasr.sauc.duration"
timeout = 45
```

`config/models/llm.toml`：

```toml
schema_version = 1
provider = "qwen"
model = "qwen-flash"
timeout = 45

[parameters]
temperature = 0.7
max_tokens = 1024
```

`config/models/tts.toml`：

```toml
schema_version = 1
provider = "volcengine"
resource_id = "seed-tts-2.0"
voice_id = "zh_female_vv_uranus_bigtts"
timeout = 45
```

修改 model 切换同供应商模型，修改 voice_id 切换音色；ID 必须是账号可用的资源。
切换 provider 时同时修改服务配置与对应凭据。自定义供应商可在第 5 节的注册器中接入。
TOML 的 `[parameters]` 后面是该表的参数；provider、model 等顶层字段必须写在表标题之前。

`config/persona.toml`：

```toml
name = "watcher"
reply_style = "自然、亲切、简短"
```

修改 `prompts/system.md` 改变提示词，`{{name}}`、`{{reply_style}}` 来自上述文件。
模板没有统一语言选项。回复内容由你编写的提示词决定；ASR 识别参数与 TTS 音色、合成参数在各自的模型配置中独立设置，不自动互相覆盖。
供应商支持的可选 `language` 或专有参数仍可填写；具体能力取决于所选服务、模型与音色。
`config/conversation.toml` 已提供默认历史轮数、静音时长和长度限制，首次不用调整。
修改完成后先停止旧应用，再执行 watcherobot app run；不支持热更新。

## 5. 修改 Python 代码

`app.py` 只负责启动。日常二次开发修改 `application/voice.py`：

- `create_registry()` 返回供应商注册器，在此注册或替换 ASR/LLM/TTS 适配器。
- `main(root)` 读取项目配置，获取受管 Application 上下文并运行对话。
- `VoiceApplication` 是 SDK 提供的默认对话实现。完全自定义流程时可在自己的 application 目录编写实现，并替换该调用。

生成后的文件由你自己维护，SDK 升级不重写项目。不要再次 app init 覆盖已有目录。
ASR 实现 transcribe(audio)，LLM 实现 generate(messages)，TTS 实现 synthesize(text)；三者都是异步迭代接口并提供 async close()。
完整协议类型定义在 SDK 的 watcherobot.voice.contracts；现成适配器可通过 create_registry() 返回的 register_asr、register_llm、register_tts 方法替换。

## 6. 检查自己的应用

```powershell
watcherobot app check .
```

app check 检查清单、入口及发布文件，不验证云服务权限。测试通过后，还需按第 3 节运行并确认真实回复。

## 7. 发布自己的应用

先修改 app.json 中的 id、name、author、description、version，确认 supported_host_platforms 与自己验收的平台一致。
清单是 schema_version=2 的 JSON；不要改变初始化器生成的 SDK 兼容范围来绕过版本检查。
发布将创建或更新公开源码仓库。确认准备分享后执行：

```powershell
watcherobot app check .
watcherobot app login --provider huggingface
watcherobot app publish . --provider huggingface
```

保存 publish 返回的固定 commit，把下一条命令的引号内文字替换为那个 40 位值：

```powershell
watcherobot app submit . --provider huggingface --commit "替换为发布返回的40位commit"
```

publish 发布源码，submit 提交官方审核，不代表已经上架。选择 Gitee 时将以上 provider 一致改为 gitee。
credentials/ 被 SDK 强制排除；空白 credential-examples/ 会保留。模板不生成录音和日志文件。
如果二次开发增加本地录音或日志，发布前创建 .watcherignore 排除这些文件；.gitignore 仅控制 Git。
不要把真实密钥移到其他文件绕过过滤。

## 8. 安装者填写自己的凭据

安装者复制 credential-examples 下的三个 TOML 文件到自己的应用凭据目录并填写：

| 平台 | 目录（将 APP_ID 换为 app.json 的 id） |
| --- | --- |
| Windows | `%LOCALAPPDATA%/watcherobot/applications/APP_ID/credentials/` |
| macOS | `~/Library/Application Support/watcherobot/applications/APP_ID/credentials/` |

安装后的应用不含开发者 credentials 目录，运行时读取上述用户目录，升级不覆盖它。
源码开发读取项目内 credentials。应用安装和运行方式沿用应用广场流程。

## 9. 排查启动和对话问题

| 现象 | 处理 |
| --- | --- |
| 提示 credentials 字段未填写 | 打开报错对应的 TOML，填写有效值并保存 |
| robot status 未连接 | 检查设备联网与当前配对码，再执行 robot pair |
| HTTP 401/403 | 核对供应商、区域、Key 类型和资源权限 |
| 有收音但没有回复 | 查看终端的 ASR/LLM/TTS 阶段错误，检查对应服务 |
| 修改配置后仍是旧效果 | 停止旧应用，再从当前项目目录 app run |
| app check 通过但对话失败 | app check 不调用云服务；继续按阶段检查凭据和设备 |
