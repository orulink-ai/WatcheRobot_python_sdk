# Pi 语音 Application 商店更新

- 开始时间：2026-10-16 14:39 Asia/Shanghai；建档作者：KID；实施：Codex。
- 需求与授权：用户要求将本轮已验证的 Application 重新提交到应用商店。范围为应用版本、源码快照与商店投稿；不包含 SDK/PyPI 发布、固件发布、官方目录合入或 workspace gitlink 提交。
- 状态：0.1.1 源码已上传，官方 Gitee 商店 PR #24 已创建并核验，待维护者审核；固定版本下载与十个文件校验通过。未合入官方目录，SDK 与固件均未发布，SDK 仓库源码仍为本地未提交。
- 配套实现与实测：[Pi 流式语音低延迟](../2026-10-16_121202_KID_无Issue_Pi流式语音低延迟/index.md)。原功能与真实三轮验证结论保留，本任务仅分发其 Application 部分。

## 发布边界与检查

- 当前 SDK 登录身份为 Gitee 的 KID_gitee；Hugging Face 未登录。选择国内 Gitee 官方商店，不复制或切换其他账号凭据。
- 发布前正式目录为 ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631，四个应用，没有 joyinside_pi；当前账号的 Pi 源码仓库返回 404。保留“未正式收录”与“更新源码”的区别，不声称已经上架。
- Application ID 保持 com.orulink.joyinside_pi，将 0.1.0 更新至 0.1.1。只上传 SDK 选择器确认的应用源码、网页、图标与说明，不包含 SDK Daemon、固件二进制、个人配置、日志或 Python 环境。
- 使用 SDK 的 publish / submit 合同，投稿固定 40 位 commit，保留官方目录其他条目并检查重复投稿。远端源码与目录投稿提交使用详细中文描述；PR 标题保留 SDK 用于识别与复用的稳定格式。
- 元数据和分发操作不适用新增业务逻辑 TDD；通过既有 manifest 测试、app check、源码文件清单、上传后固定快照逐文件验证和 PR diff 验证替代。此前 62 项回归、固件 2/2 与三轮真人验证详见关联档案，不重新编造 Red/Green。

## 变更与验证

### 2026-10-16 14:39 Asia/Shanghai｜KID / Codex

- 阅读 SDK 发布、投稿、源码选择和官方目录合同，确认本机凭据可用；公开正式目录尚无本应用。开始版本与商店发布准备，尚未创建源码或目录提交。

### 2026-10-16 14:42 Asia/Shanghai｜KID / Codex

- Application 元数据升为 0.1.1，补充更新说明、半双工限制、用户安装配置 Pi 和设备配套固件要求。`app check examples/joyinside_pi --jsonl` 通过；既有 manifest 回归 1 passed（0.15 秒）、上传 Python AST 与 diff 空白检查通过。元数据分发任务未新增业务逻辑，不将此次检查伪称为新的 TDD Red/Green。
- SDK 选择器确认仅十个应用文件，共 55,154 字节。冻结受检文件后使用 SDK publish 合同上传，服务端 Git blob 集合与受检文件一致。凭据仅由本机 SDK 系统凭据库读取，没有加入路径、日志或上传文件。
- 固定来源：[Gitee Application 0.1.1](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.joyinside_pi/tree/089e6e0cf8fa7f417e7625fa699e18ef5b96ae21)，commit `089e6e0cf8fa7f417e7625fa699e18ef5b96ae21`。远端应用提交使用详细中文描述，未提交 SDK Daemon 或固件。
- SDK submit 已创建 [官方商店审核 PR #24](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/24)，状态 open / pending，未合入。审核分支 commit `f14cb853130c00da7498b8f0876363c3f23abf65`，只改 app-list.json；对照 base `ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631`，原四条记录全部保持，仅新增本应用固定来源，目录五条。没有执行 PR 合入。
- 使用 SDK download 实际获取该固定版本，十个文件 SHA256 全部与上传前冻结清单一致，下载后的 Python 语法检查通过。下载、上传和投稿各阶段结果保留在忽略目录 `.codex/local/pi-speech-20261016/store-release`，不提交本机缓存或个人路径。
- 尝试按工具要求将审核 PR 附加为任务工件，但附件工具不支持 Gitee PR URL；交付使用上述真实审核链接。未使用 GitHub、未创建 GitHub PR、未推送 SDK 分支，也未发布 SDK/PyPI 或固件版本。


## 2026-10-27｜KID / Codex｜配套交付

用户明确授权本 SDK 配套修改 commit、push 并合入 main；ESP32 配套目标为 codex/joyinside-integration-20261015。保留原 main 的超时及通话生命周期修复，以差量三方应用本地分句流式回复、会话复用支持、网页状态与测试。当前在最新 main 24e3823 独立工作树验证，历史实机结果不外推为本次版本已烧录。测试与提交信息继续追加。


本次源码正式交付与验证见[2026-10-27配套交付](../../2026-10-27/2026-10-27_KID_noIssue_speech-delivery/index.md)，历史正文保留。
