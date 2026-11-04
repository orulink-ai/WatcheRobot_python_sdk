# 开箱行为参考工程交付

本工程复现现有流程和文案，供产品开发移植；不是首次开机量产固件。

## 配套分支
- SDK：orulink-ai/WatcheRobot_python_sdk，codex/first-meeting-sdk-latest。
- ESP32：orulink-ai/WatcheRobot_esp32，codex/special-expression-lifecycle-integration（原固件仓库重定向后的名称）。固件包含特殊表情前置提交，需完整分支配套。

## 查看与修改
直接打开 preview/Watcher-初见体验.html，无需设备或密钥；视觉近似，无真实声音，照片是模拟图。
- studio/onboarding.py：模拟扫码、密码页、失败重试。
- assets/provisioning/：三张原始SVG和PNG。
- studio/scenes.py：六段初见文案、姿态、表情映射。
- studio/waking.py：八秒两次睁眼、眼睛位移、嘴巴渐显和头部动作。
- studio/dialogue.py：姓名、拍照征求同意与后续话术。
- studio/service.py：完整流程与停止释放。
- studio/robot.py：设备动作和表情调用。
- studio/agent.py：Codex对话提示词。

流程：直接配网 → 手机输入密码 → 失败返回输入密码；成功音效 → 页面渐隐 → 苏醒 → 探头/左右观察 → 自我介绍与名字 → 征求拍照同意 → 拍照或跳过 → 对话。当前文案以源码为准，个人运行目录不分发。

## 源码运行
已验证Windows/Python3.11。仓库根目录运行 python -m venv .venv；Windows激活 .venv/Scripts/Activate.ps1，macOS/Linux 使用 source .venv/bin/activate（未实机认证）。
然后依次运行：

    python -m pip install -e .
    python -m pip install "aiortc>=1.15,<2" "numpy>=1.26,<3" "pydantic>=2,<3"
    watcherobot app run examples/first_meeting_studio

配套固件按固件仓库构建说明安装，核对硬件型号。设备进入Desktop Link配对，网页点击完整排练、模拟配网按钮推进。Codex CLI需单独安装登录。填写自己的火山语音API Key，识别资源为 volc.seedasr.sauc.duration，合成默认 seed-tts-2.0，分别检测服务。无识别授权时关闭麦克风、用文字输入；无语音授权先看离线HTML。

## 边界
此分支含源码和资源，不含预编译固件、wheel、个人密钥、照片、配对记录或Codex登录。delivery/setup 仅用于带bundle.json的配套ZIP，不用于直接克隆源码；历史配套目录目前不完整。
后续产品开发需接入真实BLE配网、设备首次开机入口、设备端完成标记、正式云端对话。当前流程由电脑Python驱动，首次记录在电脑，配网仍模拟；不能直接复制到ESP32运行。
本次回归为SDK应用测试及固件主机测试（表情35、协议4、音频跟随1）；未重新刷机或进行量产验收。
本次配套固件提交：e48228ac（codex/special-expression-lifecycle-integration）；相机交接两项主机测试补充通过。
