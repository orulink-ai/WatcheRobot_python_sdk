# 商店图标按来源读取

- 作者：wugui / Codex；日期：2026-10-15，Asia/Shanghai。
- 分支：codex/tos-marketplace-20261014；用户授权追加 SDK 和桌面端 commit，不创建 PR。
- 原因：桌面按 Hugging Face resolve URL 拼图片，Gitee 不适用；图标传输应归 SDK，两个 provider 共用展示合同。
- 实现：图标与 manifest 按同一个审核固定提交、通过已选择的 hub 读取，返回可选 icon_data_url；保留 icon 原路径。路径限制在仓库内，拒绝绝对路径、穿越、协议 URL。PNG/JPEG/GIF/WebP/SVG 按内容识别，SVG 禁止 DTD/实体；每图上限256KiB，目录内联图标预算2MiB（含编码后的字符串），缺失/无效/超限图标回退默认，不阻断应用目录。
- TDD：新增图标模块导入失败的红测试后实现；两 provider 固定提交集成覆盖。分发测试447项通过（1个既有 Traversable 弃用警告）。
- 实网：Gitee catalog ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631，4/4图标成功，含说话动作实验室0.2.1；HF catalog 4c042b064178313ee24b10eda43a01cf22d72c2d，6/6图标成功。只读验证，不发布应用。
- 配套桌面提交：同名功能分支，透传/校验/缓存内联图标并更新 runtime-source-lock 到本 SDK 提交。具体 SHA 以该桌面提交来源锁为准。
- 未修改 Daemon 业务路由；未修改固件；无正式安装包或跨系统实机验收。
