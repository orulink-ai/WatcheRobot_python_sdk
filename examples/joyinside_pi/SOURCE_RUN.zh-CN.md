# 从源码运行 Pi 语音应用

代码位于 SDK 仓库的 `codex/pi-voice-android-integration` 分支，入口是 `examples/joyinside_pi`。需要有该私有仓库的读取权限。此分支包含配对错误修复、豆包语音、动作表情、连续播放和流式文本提前合成；不包含任何共享模型或语音凭据。

## 1. 准备环境与源码

安装 Git、Python 3.10–3.12（推荐3.12）、Node.js 22.19.0或以上。以下命令 Windows PowerShell 和 macOS/Linux Shell 相同：

```text
git clone --branch codex/pi-voice-android-integration --single-branch https://github.com/orulink-ai/WatcheRobot_python_sdk.git
cd WatcheRobot_python_sdk
python -m venv .venv
```

激活虚拟环境，Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux Shell（若只有python3命令，前一步使用python3创建虚拟环境）：

```sh
source .venv/bin/activate
```

安装当前仓库SDK和已验证版本的Pi：

```text
python -m pip install --upgrade pip
python -m pip install -e .
npm install -g @earendil-works/pi-coding-agent@1.3.0
pi --version
pi
```

先在Pi内配置自己的模型和登录，确认普通文字对话成功后退出Pi。不要仅安装PyPI旧版SDK再复制Application；这里需要当前分支的SDK能力。

## 2. 配置语音与手机

按[应用README的豆包配置](README.md#备用语音application-豆包)在当前用户的私有凭据目录创建 `doubao.json`，或配置机器人JoyInside。凭据不得提交Git或发进公共运行命令。Android MCP为可选项，配置见[安卓MCP接入](README.md#安卓-mcp-的最小接入)，普通聊天不需要手机MCP。

固件需实际实现机器人PCM采音和播放。连续播放需要 `audio.stream.continuous.v1`；缺失时使用旧分段路径。豆包嘴型需要 `expression.audio_follow.local_pcm.v1`。源码运行不会自动升级固件。

## 3. 启动并配对

从SDK仓库根目录、已激活的虚拟环境执行：

```text
watcherobot daemon activate
watcherobot app run examples/joyinside_pi
```

`daemon activate`使本次安装的SDK接管运行时，**会停止此前运行的Application**；先结束正在进行的任务。不要直接运行 `python app.py`，Application必须使用Daemon注入的通道。

保留终端，打开它打印的本机网页（端口动态分配）。让机器人与电脑处于同一局域网，机器人打开Desktop Link，在网页输入当前六位配对码。等显示连接成功且设备信息读取完成，选择已配置的语音后端，再点击“开始对话”。

## 4. 停止、更新与配对恢复

在另一个已激活同一虚拟环境的终端停止应用：

```text
watcherobot app stop
```

如果设备在语音过程中退出连接，且页面提示上一轮资源未释放：先停止应用，再运行 `watcherobot app run examples/joyinside_pi`，打开新的网页地址重新配对。这是Application资源清理状态，不等于Wi-Fi故障；仍在对话或停止中的情况先暂停并等待。

更新代码时，先停止应用，再执行：

```text
git pull --ff-only
python -m pip install -e .
watcherobot daemon activate
watcherobot app run examples/joyinside_pi
```

更改模型、PATH或MCP环境变量后也要重新激活运行时；单独改变启动CLI终端的环境不能更新已经运行的Daemon。
