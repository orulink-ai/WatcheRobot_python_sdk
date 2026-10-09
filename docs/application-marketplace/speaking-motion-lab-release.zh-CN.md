# 说话动作实验室 0.1.0 发布记录

截至 2026-10-09，Application 源码已发布到 Hugging Face，投稿已由平台侧合并，匿名回读确认正式目录已收录。Gitee 发布等待 SDK 凭据登录，不能把浏览器登录或国外上架作为国内上架完成的证据。

## Application 与版本

- 应用 ID：`com.orulink.speaking_motion_lab`，版本 `0.1.0`。
- Python Application 与唯一行为编排核心：[源码与使用说明](../../examples/speaking_motion_lab/README.md)。前端源码位于客户端仓库，发布包包含预构建网页、真实 GLB、固件表情 WASM、音频示例与资源许可。
- SDK Application 源码提交：`f92fff21c2d45d90728f9521769c9d1808634122`；合并最新主线：`63fa5e7fc2b3b7d031ef4f3cea3b640ed60b3a26`，两个 parent 为 `f92fff21` 与 `34493b4f`。
- 客户端接入提交：`d3361d38b0cc2a8430a6a1cf57f379a15b68c970`；合并最新开发分支：`2f3e0706e3947a4f6040056e3980730dbccff97e`，两个 parent 为 `d3361d38` 与 `d4b3c4c7`。

当前只声明 Windows 预览支持。默认 JoyInside 聊天接入、流式打断、真机动作执行与硬件角度标定仍待实现；没有采用强化学习。

## 两个平台的状态

| 平台 | 源码发布 | 官方收录 |
| --- | --- | --- |
| Hugging Face | 已发布公开固定版本 | [投稿 #43](https://huggingface.co/datasets/Orulink/watcherobot-app-store/discussions/43)，状态 merged，正式目录回读确认已收录 |
| Gitee | 未发布，SDK 未登录 | 未投稿、未收录 |

Hugging Face 应用仓库为 [yuzou1121/WatcherRobot-com.orulink.speaking_motion_lab](https://huggingface.co/spaces/yuzou1121/WatcherRobot-com.orulink.speaking_motion_lab)。固定源码 SHA 为 `8bf2a2aeb495fdbded8be1e33957932c0f29a4d7`，可查看[完整发布快照](https://huggingface.co/spaces/yuzou1121/WatcherRobot-com.orulink.speaking_motion_lab/tree/8bf2a2aeb495fdbded8be1e33957932c0f29a4d7)。

官方目录 `Orulink/watcherobot-app-store` 的最终收录提交为 `2f430d001cb1585fc4d5be9a974d7342881be13e`。该提交仅向 `app-list.json` 新增上述应用和固定 SHA，保留原条目。经匿名 Git 对象检查，平台侧生成的最终提交只有一个 parent：`d8c0a71faebe5f503819ab070f4080e1e94c78ef`；应明确区分这一平台侧合并结果与本轮两个子仓库各有两个 parent 的本地合并。

本轮标准 SDK OAuth 使用身份、应用仓库贡献和讨论权限，详见 [OAuth 合同](hugging-face-oauth.md)。组织管理员身份不等于发布凭据具有官方目录写权限；代理尝试主分支写入被平台拒绝，官方目录随后由平台侧合并投稿。后续维护者的 PR 合入流程仍应选择能保留两个 parent 的方式。

Gitee 网页会话与 SDK 发布凭据独立。需要通过 `watcherobot app login --provider gitee` 隐藏输入具有仓库读写权限的 Access Token，保存到 Watcher 专用系统凭据条目后，再独立执行 `publish` 和 `submit`。不在聊天、源码或普通配置文件中保存 Token。当前外置 Chrome 自动化连接不可用，未取得或保存网页凭据。

## 验证证据

- Application 校验通过，27 项 Application 测试通过；客户端 61 项 Application 业务测试、15 项动作预览测试、预览范围 TypeScript 检查和生产构建通过。
- 两个子仓库采用非快进合并，并核对合并提交各有两个 parent。SDK 原有未提交语音实验改动已恢复，冲突文件同时保留主线功能和本地改动，相关 Python 与 17 项 JavaScript 回归测试通过。这些其他任务的改动未纳入本次提交。
- 发布筛选保留 21 个应用文件，包括预构建资源，排除测试、缓存和构建临时目录。发布前校验 9 个 Web 文件的 SHA256。
- 通过 SDK 匿名下载上述固定远端版本：21 个发布文件与上传输入逐字节一致，包含由 HF 发布器添加的 README 元数据；9 个 Web 文件的 SHA256 一致。
- 从下载后的快照在隔离 SDK Daemon 中启动 Application：页面和 6 个资源文件成功返回且字节一致；“是的。不可以。”生成肯定与否定动作，末帧闭嘴；未知 Desktop 麦克风业务帧被安全忽略；受管停止释放进程与 HTTP 监听。
- 匿名回读正式 HF 目录，确认固定应用引用存在，投稿状态为 merged；SDK `app marketplace --provider huggingface --jsonl` 返回该固定版本，SDK 与 Windows 主机兼容性均为 true。核对最终收录提交的 parent 数量并记录平台侧的单 parent 结果。

受管运行验收使用当前开发环境中的 SDK，未覆盖已安装桌面 Runtime，也未执行全新商店安装环境、macOS/Linux 或真机验收。

## 后续发布步骤

1. HF 正式目录收录已完成；继续验收客户端商店展示与全新安装环境。后续更新仍需分别核对发布快照、目录回读和最终合并历史，不能将上传成功或 pending 当作已收录。
2. 完成 Gitee SDK 登录，发布相同应用内容，记录该平台独立返回的完整 SHA，再向国内官方目录投稿。
3. 两个平台各自完成正式目录回读和全新安装验收后，更新本记录。继续保持 Daemon 唯一实现与 Application 通道边界。
