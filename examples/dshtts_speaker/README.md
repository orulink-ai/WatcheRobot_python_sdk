# DSH TTS Speaker — WatcheRobot 语音播报插件

让 [WatcheRobot](https://github.com/orulink-ai/WatcheRobot_python_sdk) 机器人成为 DSH 的语音播报器。

## 工作原理

```
DSH 完整助手回复 → session/event → 自动播报队列 → HTTP API → edge-tts → 机器人喇叭

也支持手动 `/speak`，以及 `/speak stop`、`/speak voice <voice-id>`。自动播报可通过 `DSHTTS_AUTO_SPEAK=false` 关闭。
```

## 快速开始

### 前置条件

- Windows 10+ / macOS
- Python 3.10–3.12（推荐 3.11）
- Conda 环境
- ffmpeg（在 PATH 中）
- WatcheRobot 机器人已配网

### 1. 安装依赖

```powershell
conda activate watcherobot
pip install edge-tts
```

### 2. 启动 WatcheRobot Application

```powershell
conda activate watcherobot
watcherobot robot setup          # 首次配网机器人
watcherobot robot status         # 确认连接

cd examples/dshtts_speaker
watcherobot app run              # 启动 TTS 播报服务
```

服务启动后监听 `http://127.0.0.1:9876`。

### 3. 测试播报

```powershell
# 方式一：桥接脚本
.\scripts\speak.ps1 "你好，这是 WatcheRobot 语音播报测试"

# 方式二：直接 HTTP 调用
Invoke-RestMethod -Uri http://127.0.0.1:9876/speak -Method Post -Body '{"text":"你好世界"}' -ContentType application/json
```

### 4. 安装 DSH `/speak` 命令（可选）

```powershell
# 在当前 DSH profile 中安装插件
dsh plugin --profile web add .\examples\dshtts_speaker\dsh-plugin
```

安装后，在 DSH 对话中输入 `/speak` 即可播报上一条助手回复。

也可以指定文本：`/speak 你好，这是自定义播报内容`

## API 接口

### `POST /speak`

```json
{
  "text": "要播报的文字",
  "voice": "zh-CN-XiaoxiaoNeural",
  "rate": "+0%"
}
```

### `GET /health`

返回机器人连接状态和能力列表。

### `POST /stop`

停止当前播放并清空队列。

### `POST /settings`

切换默认语音，例如：`{"voice":"zh-CN-YunxiNeural"}`。

### `GET /voices`

返回可用语音列表。

### DSH 命令

```text
/speak                          # 播报上一条助手回复
/speak 你好                     # 播报自定义文字
/speak stop                     # 停止当前播报并清空队列
/speak voice zh-CN-YunxiNeural  # 切换默认语音
```

插件只监听最终的 `assistant/message` 事件，不监听流式 chunk，因此不会把同一条回复重复播报。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DSHTTS_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `DSHTTS_PORT` | `9876` | HTTP 监听端口 |
| `DSHTTS_VOICE` | `zh-CN-XiaoxiaoNeural` | 默认 TTS 语音 |
| `DSHTTS_RATE` | `+0%` | 默认语速 |

## 项目结构

```
dshtts_speaker/
├── app.py                  # WatcheRobot Application 主入口
├── app.json                # Application 元数据
├── README.md
├── scripts/
│   ├── speak.ps1           # Windows 桥接脚本
│   └── speak.sh            # macOS/Linux 桥接脚本
└── dsh-plugin/             # DSH 命令插件
    ├── package.json
    ├── cordis.patch.yml
    └── src/
        └── index.js
```

## 注意事项

- 机器人音频格式：PCM S16LE, 24000Hz, Mono
- 单次最大 4MB（约 87 秒）
- 语音播报与动画同时播放可能掉帧
- 需要稳定的 Wi-Fi 连接
