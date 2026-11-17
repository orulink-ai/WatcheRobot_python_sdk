# 应用广场行为编辑器交付

- 时间：2026-11-17 Asia/Shanghai；作者：YuZhong0212；协作：Codex；无Issue。
- 用户授权：适配可发布应用，将配套固件提交至远端分支；不公开上架、不合入、不自动烧录。
- SDK分支：codex/idle-studio-marketplace-20261117；固件配套：codex/idle-studio-firmware-20261117。
- 现状：编辑器源码及测试未纳入Git，独立Python后台；预览下载依赖gh。
- 范围：纳入编辑器与测试，提供Application源码包、受管生命周期、动态端口、依赖；去除资源下载的gh依赖。
- 架构：不改Daemon业务路由；USB使用已有SD维护协议，不建设第二条设备业务WebSocket。
- 不改现有DIY配置；92/8仍为默认候选，不擅自推广官方默认。
- 验收：TDD，Python/JS回归，独立源码包检查、受管启动退出、HTTP与串口回读；平台结果如实记录。
- 首版仅声明Windows；macOS未实测。状态：进行中，未提交、未推送。

## 2026-11-17 验证与交付记录｜Codex
- 状态：实现与验证完成，准备独立提交及推送；公开上架和烧录不在本轮范围。
- 新增Application清单、受管生命周期、独立导出器；模板不是完整发布目录。下载改为公开HTTPS，固定v0.0.10 SHA256、大小上限、原子替换，不需gh登录。
- TDD：新增适配用例最初6项失败，实现后7项通过；完整Python72项通过，JS40项通过。旧启动顺序测试的返回值语句锚点失效，改用app_main锚点并验证完整顺序。
- PyPI watcherobot0.1.14独立venv：关闭system-site-packages并补齐依赖，无PYTHONPATH，发布SDK没有idle_studio；导出包通过清单、真实ApplicationRuntimeManager启动、HTTP首页/串口/模型200、Desktop未知帧处理、停止退出0及端口释放。初次共享site环境在隔离子进程缺anyio，纠正环境后通过。
- USB只读：COM6成功，revision17、15行为、92/8；未写配置。官方默认仍50/50。
- 配套ESP-IDF6.0.2 AIChat构建exit0，宿主joy_idle_profile测试1/1通过；未烧录。
- Windows已验证；其他平台未实测，清单不声明支持。未重做物理断电、换机、全槽安装、长时间实机播放；未公开发布或提交审核。
- 前序记录：[双池概率与串口修复](../../2026-11-14/2026-11-14_154615_YuZhong0212_无Issue_待机双池概率与新版固件适配/index.md)。本轮补足应用交付，保留历史验证边界。
