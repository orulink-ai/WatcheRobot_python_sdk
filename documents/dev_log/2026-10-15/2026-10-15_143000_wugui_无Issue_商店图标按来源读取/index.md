# 商店图标按来源读取

- 作者：wugui / Codex；日期：2026-10-15，Asia/Shanghai。
- 分支：codex/tos-marketplace-20261014；用户授权追加 SDK 和桌面端 commit，不创建 PR。
- 原因：桌面按 Hugging Face resolve URL 拼图片，Gitee 不适用；图标传输应归 SDK，两个 provider 共用展示合同。
- 实现：图标与 manifest 按同一个审核固定提交、通过已选择的 hub 读取，返回可选 icon_data_url；保留 icon 原路径。路径限制在仓库内，拒绝绝对路径、穿越、协议 URL。PNG/JPEG/GIF/WebP/SVG 按内容识别，SVG 禁止 DTD/实体；每图上限256KiB，目录内联图标预算2MiB（含编码后的字符串），缺失/无效/超限图标回退默认，不阻断应用目录。
- TDD：新增图标模块导入失败的红测试后实现；两 provider 固定提交集成覆盖。分发测试447项通过（1个既有 Traversable 弃用警告）。
- 实网：Gitee catalog ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631，4/4图标成功，含说话动作实验室0.2.1；HF catalog 4c042b064178313ee24b10eda43a01cf22d72c2d，6/6图标成功。只读验证，不发布应用。
- 配套桌面提交：同名功能分支，透传/校验/缓存内联图标并更新 runtime-source-lock 到本 SDK 提交。具体 SHA 以该桌面提交来源锁为准。
- 未修改 Daemon 业务路由；未修改固件；无正式安装包或跨系统实机验收。


## 2026-10-15 交付前审查补修（wugui / Codex）

用户改为授权审查、追加提交、推送和中文草稿PR，目标main，禁止合并。沿用阶段性交付工作流，不发布SDK、不修改固件。

- 安全TDD：UTF-16/UTF-16LE/UTF-32LE编码的SVG实体声明绕过旧原始字节检查，先确认新增回归失败再修复；严格UTF-8/BOM解码、拒绝NUL/DTD/ENTITY，失败回退默认图标。
- mypy发现Dulwich 1.2.17 Ref/ObjectID新类型、determine_wants回调与ShaFile/Blob推断不符；使用实际声明的类型与Mapping回调，已由_files运行时验证为Blob的引用进行显式类型收窄，不增加忽略规则或改变Git协议。
- 全量CI暴露受跟踪joyinside_pi正式示例未纳入EXAMPLE_IDS的既有遗漏。核对app.json和ApplicationContext.from_environment入口后补充正确ID，保留完整清单相等及全部受管Application断言。此前口头判断为被忽略的本地残留不准确，完整源码快照复现后更正；未删除或排除该示例。
- 新建受支持Python3.12.14隔离环境（旧工作区venv为不支持的3.13，不作为最终CI证据），受跟踪文件快照模拟CI tarball，无Git历史依赖。完整pytest：1953通过、10跳过、3警告，243.03秒；类型补修后另跑Git/图标34项通过。
- 其他CI步骤：pip check通过；expression_lab示例30通过；mypy 127源码通过；JS225通过；BLE导入成功及provisioning75通过；build sdist/wheel与twine check通过。全新venv安装wheel后pip check、版本导入、CLI帮助、demo帮助、隔离模式提取全部2个内置示例通过。
- 本地证据位于workspace _local_artifacts/tos-marketplace/sdk-ci-*.log，包含初次失败和最终成功，临时日志不提交。Windows/Python3.12/Node24本地检查不替代远端Linux/Python3.10、3.11及Node22矩阵；10项原有跳过未新增修改。未重新验收无预装Git/Python的正式冻结安装环境。
- 配套桌面同名功能分支将来源锁更新到本SDK提交，并提交BLE关联ID/订阅清理及现行CI合同修复。未改变Daemon业务路由，未运行桌面热更新或写入设备。
