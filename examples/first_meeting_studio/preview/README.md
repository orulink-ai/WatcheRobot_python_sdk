# Watcher 初见离线预览

直接用现代浏览器打开 **Watcher-初见体验.html**。转发这一个文件即可；无服务器、依赖安装、账号、密钥或机器人要求。点击“自动演示全程”可连续观看；也可手动扫码、模拟成功/失败、输入名字、同意/拒绝拍照。支持暂停、半速/双速、重播和分段查看。

## 对应范围

- 三张配网 PNG 直接来自当前应用 assets/provisioning。
- 苏醒参数对应 studio/waking.py：8 秒、两次睁眼、gaze_y 从 1 到 0、嘴巴最终渐显。
- 基础眼睛和嘴巴重绘自配套固件 watcher_expression_core.c / watcher_transition_visual.c。色值 RGB565 0xA787。
- 思考40帧、拍照35帧来自固件压缩资源，来源摘要保存在 assets/provenance.json。每帧100ms，拍照第25帧开始全屏闪光；只触发一次纪念卡。
- 分镜与固定台词对应当前 studio/scenes.py / dialogue.py；后续聊天为固定示例。正式应用文案中的“照片保存”在此仅指模拟纪念卡，不会拍摄或保存用户照片。
- 浏览器渲染、表情淡入淡出、文字嘴型和机身倾斜是视觉近似，不能替代真机逐像素、音频同步和电机验收。此版本无声音；不调用语音服务、Codex、摄像头或设备。

## 维护与重建

源码分别为 flow.js（纯状态机）、app.js（绘制和交互）、template.html（版式）。不改动运行中的控制台。维护修改应改源码后重新生成。

安装 Python 3 + Pillow。Windows 执行 ./build.ps1，macOS/Linux 执行 sh ./build.sh；参数、默认输出和退出码相同。可用 --output 文件路径 指定输出。仅需刷新固件帧时传 --firmware 固件源码根目录，通常重建使用已经导出的 PNG，无需固件仓库。

测试：node --test flow.test.cjs。浏览器测试：安装 Playwright 后执行 node --test browser.test.cjs；可用 PLAYWRIGHT_PATH / CHROMIUM_PATH 指向测试机已有工具。构建测试：python -m unittest build_test.py。不包含任何个人路径配置。
