# 对讲延迟与声音控制

- 创建时间：2026-10-13 09:41:50 Asia/Shanghai；作者：KID；参与者：Codex。
- 无 Issue；状态：声音功能阶段交付，延迟与硬件音量待验证；交付状态：准备提交与商店投稿，未合入。
- 范围：robot_intercom 浏览器音频；保持 Daemon 内容无关路由和现有固件。

## 背景、目标与验收

用户报告延迟较早期变差，并授权推进浏览器机器人播放音量与实时变声。
先区分网络 RTT、浏览器接收缓冲、Web Audio 处理及真实声学单向延迟。

- 关闭现场降噪直接使用原始 RTC stream，快速切换及结束不允许旧异步结果复活。
- 通话音量调节发送音频的数字增益 0–100%，不冒充机器人全局硬件音量读回。
- 原声绕过变声 DSP；低沉、明亮、机器人效果在本机处理，不变更传输速率。
- 验证有限内存、音高、静音、取消与恢复；实机记录 RTC 指标，不将 RTT 作为声学延迟。
- 中英界面及已有通话生命周期回归通过。

## 调查事实

关闭旧降噪复选框仅改变混合比例，仍经过 frame adapter、上一帧 dry 与 MediaStreamDestination；因此存在算法缓冲。浏览器接收目标 20ms 是提示，不能保证实际值。上一阶段未做声音端到端时钟对齐。当前固件存在系统音量内部接口，但已检查的 SDK 业务协议没有系统音量控制命令。

## 验证记录

TDD：先添加新音频模块行为测试，再实现。声音实测依赖实际采集/输出端点，缺少声学时钟对齐时明确保留验收缺口。

## 关联历史

延续 [对讲界面设计](../../2026-10-12/2026-10-12_160422_KID_无Issue_对讲界面设计/index.md)，补充原声与声音控制。既有 UI 与历史验证仍保留。


## 2026-10-13｜KID / Codex｜实现与中途验证

先写音频模块测试，缺少新模块时失败；实现后切换/结束时的异步处理图、有限内存变调、数字增益边界与轨道释放通过。准备麦克风过程中取消的控制器用例在旧实现失败，接入有代际检查的处理图后通过。

用户补充电脑收听机器人时环境音多。先增加人声聚焦测试，再实现 RNNoise 概率驱动软衰减；实际 RNNoise 稳态噪声、轻声语料能量、平滑衰减和短暂停顿测试通过。这是泛人声增强，不识别特定用户，不能把能量保留当作主观可懂度验收。

前两轮正式 UI 通话约 3 分钟、7 分钟，中英切换、声音模式和静音未导致断线。第二轮释放 complete=true、failures=[]。第三轮验证稳定 RTC 消费时钟、0/35/100 数字增益和静音时切回原声。

页面多个样本：接收缓冲 141–312ms，网络 RTT 4–84ms；浏览器实际目标和 minimum 样本 120/140/260ms，高于代码请求的 20ms。关闭降噪仍偏高。机器人采集发送 pipeline 最大值两份记录约 38/41ms，TX/render errors 为零。这不等于完整单向延迟；尚无同端点、时钟对齐的历史 A/B，不能证明高 buffer 完全由网络或某个嵌入式线程造成。

原声且 100% 时原始轨道直通，发送图附加基准 0ms；改变强度/效果时观察图基准 10ms。固件播放自适应增益可提高弱输入，因此数字滑块不等于扬声器系统音量；硬件音量协议未实现。本轮不通过串口调试旁路实现网页能力，不改固件。

## 行业调研

- [腾讯 RTC AI 降噪](https://trtc.io/document/59658)：本地流插件，抑制键盘、碰撞等非平稳噪声，也可能去掉背景音乐。
- [Teams 降噪](https://support.microsoft.com/en-gb/teams/meetings/reduce-background-noise-in-microsoft-teams-meetings)：默认启用、不同强度，通话与音乐保真目标不同。
- [Teams 人声隔离](https://learn.microsoft.com/en-us/microsoftteams/voice-isolation)、[Zoom 个人人声隔离](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0074698)：特定说话人隔离属于额外模型/语音资料能力，不等同普通降噪。

本应用保持局域网本地处理，不引入商业 RTC 信令、账号或云音频。未查到具体银行内部算法，不能据通用文档推断某家银行实现。


## 本轮结束记录

78 项 Node 专项、110 项 Python 专项、调用 CLI main 的 app check（0.1.4）通过。此前直接执行 cli 模块不执行入口，其 exit 0 不计入验证；已使用实际 main 入口复核。

正式 UI 四轮约 3/7/3.5/3.25 分钟，第三/四轮使用稳定消费时钟版本。第四轮结束后设备释放 complete=true、sequence=25、failures=[]，通话 idle；未刷固件、未提交、未发布。最终版默认人声优先、原声发送、100% 数字强度。最终轮连续 60 个状态样本全部 running/connected，TX/render errors=0；一个末尾样本 packetsReceived=3673、packetsLost=10，存在少量接收丢包，不能称网络完全无损。

最终浏览器明确报告低延迟目标设置受支持，已请求 20ms，但该样本实际接收缓冲 158ms、目标/minimum 均 120ms。至少确认目标不是硬上限，单靠该设置不能解决问题。第二轮完整 media samples 接收缓冲 min/median/max=70/208/415ms（82个）；第三轮=86/159/234ms（41个）。不同测量时长及环境，不能据差值宣称稳定时钟消除了全部延迟或做过严格 A/B。

尝试使用系统 PktMon 查看抓包能力，驱动拒绝访问；未启动抓包、未改过滤器/系统设置。尚缺包到达间隔、物理输出端点重标定、两方向声学时钟对齐和历史同条件对照。保持延迟回归待验证状态，不将已加入的软件功能称为根因彻底解决。系统硬件音量及真人轻声/可懂度/双讲验收仍未完成。

## 2026-10-13｜KID / Codex｜界面复核

用户确认环境声来自机器人到电脑的收听方向。声音设置补齐响应式内边距，音量滑块补齐独立的中英文无障碍名称。此低影响样式改动采用浏览器视觉验证，未添加镜像样式测试。390px 手机宽度与默认桌面宽度均无声音设置横向溢出；英文滑块可通过完整名称唯一定位，最后恢复中文、默认视口，机器人连接正常且通话 idle。4 项 i18n 测试及 git diff --check 通过；本次没有再次修改音频逻辑。第四轮接收缓冲 min/median/max=63/157/277ms（39个样本），仍不能作为历史同条件 A/B。

## 2026-10-13 10:26 Asia/Shanghai｜KID / Codex｜提交与发布准备

用户明确要求先 commit，并提交远程应用商店。应用版本递增至 0.1.5，保留 0.1.4 历史候选正文，更新中英文声音功能说明，不宣称延迟、声学闭环或硬件音量验收完成。本阶段只改独立 Application，不改 SDK Daemon 路由或固件；不需要配套固件提交。

提交前复核改动范围并重跑以下专项，78 项 Node、110 项 Python 全部通过：

```text
node --test tests/js/test_intercom*.mjs tests/js/test_robot_intercom*.mjs tests/js/test_rtc_audio_capture.mjs tests/js/test_rtc_audio_latency.mjs tests/js/test_rtc_noise*.mjs tests/js/test_rtc_connection_recovery.mjs
.venv/Scripts/python.exe -m pytest tests/test_robot_intercom.py tests/test_examples.py tests/application/test_rtc.py -q
```

商店平台为 Gitee，当前开发者账号 KID_gitee。只读核对旧待审 #19 是该账号的 robot_intercom 0.1.3 请求，固定源码 19c80bd1fc96e1c96a61d1939e74e269bd94c830。准备发布新版固定快照并替换旧投稿；实际 SHA 与审核状态待远端返回后追加，不能提前称正式目录可下载。
