# 初见应用迁移至最新 SDK
创建：2026-10-26 Asia/Shanghai；作者 YuZhong0212；参与者 Codex；无 Issue。
目标：在 origin/main 24e3823 上迁移初见扩展，保留上游清理及相机错误处理；安装包与能力检测一致。保留旧工作区。状态：进行中，未提交发布。
验收：扩展及上游相关测试、独立安装、应用检查；在线真机可用时执行短测。遵守 Daemon 通道架构。

## 2026-10-26 验证与交付记录（Codex）
- 基于 origin/main 24e3823，分支 codex/first-meeting-sdk-latest；保留原旧工作区，未提交/推送。配套版本 0.1.12+firstmeeting.20261026，不冒充官方发行版。
- 移植透明度与配网页 schema、幂等麦克风关闭、断连广播保护、GIF 限制；保留上游累计音频确认、麦克风生命周期、相机错误反馈。
- TDD：新增两种 watermark 等待场景先失败，再修复早于 command_timeout 超时的问题；回归 447 passed / 9 skipped / 1 上游弃用警告（96.06s）。跳过项不计通过。证据位于本机 .temp/sdk-migrated-20261026/regression.txt。
- 修复本地版本约束不能使用 >= 的 PEP440 问题；锁文件加入精确 SDK pin。独立安装 pip check 与 app check 通过。首次依赖下载 TLS 失败，重试恢复；无禁用 TLS。构建隔离安装 hatchling 后 wheel 成功。
- wheel SHA256 ebb590430fcc95a9e4147f3f7ee0553b4762c59cf9c43e3b9ff02f525a8ec37b。ZIP SHA256 f987d984cd9492a45d67320dc3e912919a61a54cc7e99df9ce0f40d364003154；43个校验文件，不包含 artifacts、照片、密钥和虚拟环境。
- 已将本机 Daemon 与 Application 切换至配套 wheel，HTTP 200、sdk_version 精确匹配；两轮管理接口停止/启动成功。个人配置仅迁移到本机 artifacts，auto_boot=false。
- 真机 v0.5.8 配对成功，无需刷固件。连接苏醒短测完成；扫码→密码→失败→返回密码→成功→苏醒通过；实拍 JPEG 19876 字节，网页返回 200；停止返回200，RTC/音频/麦克风/代码表情资源均释放。屏幕和主观听感不以接口成功代替验收。
- Codex 文本检测通过。火山 TTS HTTP403，STT因缺少合成测试音频未执行；完整开场进入首句即被云授权拒绝。未声称语音全链路或长期稳定性通过。
- 配套包独立使用；桌面内置 SDK 发布/锁版本尚未更新，应用广场兼容验收仍未完成。macOS/Linux未验证，应用当前声明Windows。
- 运行中的 bundle/.venv 不可覆盖重建，最终ZIP改用全新 acceptance 目录安装验收，结果另记。

前序调查：[2026-10-26_151452_YuZhong0212_无Issue_SDK固件安装兼容性](../2026-10-26_151452_YuZhong0212_无Issue_SDK固件安装兼容性/index.md)。
`n最终ZIP在全新 acceptance 目录独立安装成功：Bundle install completed，app check通过；控制台与设备保持连接，完整语音因TTS403未验收。当前状态：软件迁移及配套安装完成，待云语音授权与应用广场桌面Runtime集成。
