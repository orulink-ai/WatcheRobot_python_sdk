# Robot Alarm（机器人闹钟）

一个可直接运行的 WatcheRobot Application：**本地网页设置闹钟，到点由机器人
喇叭语音播报**，并播放一次 `happy` 行为引起注意。

- Application ID: `example.scheduled_reminder`
- 依赖 SDK：`watcherobot >=0.1.0a4,<0.2`

## 怎么用

1. 启动应用（任选其一）：
   ```powershell
   watcherobot app run .\examples\scheduled_reminder   # 前台（Ctrl+C 停）
   watcherobot app start                                # 后台（Daemon 已记住选择）
   ```
2. 浏览器打开应用日志里提示的地址（默认 **http://127.0.0.1:8766**）。
3. 在网页上添加闹钟：时间、重复规则（每天/周一至周五/周六日/自定义星期）、
   播报内容；可随时开关、删除，改动立即保存（`alarms.json`）并最快 1 分钟内生效。
4. 到点机器人语音播报 + `happy` 动作。应用启动时还会先播报一次问候验证链路
   （`ANNOUNCE_ON_STARTUP`，不需要改成 `False`）。

## 它是怎么工作的

```
web/index.html ←──(REST)── AlarmWebServer (127.0.0.1:8766)
                                  │ 读写
                              alarms.json（AlarmStore 持久化）
                                  │ 常驻调度循环热重载
                                  ▼
                        到点 → reminder.speech（edge-tts → 24kHz WAV）
                                  │ 失败/缺失 → 内置 fallback.wav
                                  ▼
                    app.robot.audio.play_file → 机器人喇叭播报
                    app.robot.behavior.play("happy") → 动作吸引注意
```

- **存储**：`reminder/alarms.py` —— JSON 原子写盘、损坏自动备份重置、字段校验。
- **调度**：`reminder/schedule.py` —— 纯逻辑（与 SDK 解耦），支持重复规则，
  按闹钟计算下一次触发时间，`asyncio.sleep` 分 60 秒一段休眠以热重载配置。
- **网页**：`reminder/web.py` + `web/index.html` —— 纯标准库 HTTP 服务与 REST
  API（`GET/POST/PATCH/DELETE /api/alarms`），零 Web 框架依赖。
- **语音**：`reminder/speech.py` —— `edge-tts` 合成 MP3 后 `ffmpeg` 转成
  24 kHz / 单声道 / 16-bit PCM WAV；任一环节缺失或失败自动降级内置示例音频。

## 前置条件

1. 安装 SDK 并连接机器人（见仓库根 README 的快速开始）。
2. 动态文案（可选但推荐）：`python -m pip install "edge-tts>=6.1.10"`，
   并安装 `ffmpeg` 加入 PATH；两者任一缺失只影响动态文案，播报会退回内置音频。

## 配置

网页服务默认只监听本机。手机/局域网访问，改 `app.py` 顶部常量或用环境变量：

```powershell
$env:WATCHER_ALARM_WEB_HOST = "0.0.0.0"   # 局域网可访问
$env:WATCHER_ALARM_WEB_PORT = "8766"
watcherobot app run .\examples\scheduled_reminder
```

单元 + API 集成测试（不依赖机器人）：

```powershell
python -m pytest examples/scheduled_reminder/tests -q
```

## 注意

- 到点播报依赖 **Daemon 在运行、机器人已连接**；电脑关机/休眠期间到点不播、
  不补播（当前版本不做错过补偿）。
- 播报走机器人喇叭；需要「人不在也能收到」时，可在 `announce()` 里追加
  飞书/IM 推送。
- 请通过 `watcherobot app run / app start` 运行，不要直接执行 `app.py`。