# TOS 国内商店完整目录

- 创建日期：2026-10-14 Asia/Shanghai；建档作者：wugui；实施：Codex。
- 分支：codex/tos-marketplace-20261014，基于 origin/main a97fb10。
- 状态：进行中，本地修改；未提交、未合入、未发布客户端。

## 背景、范围与验收

Gitee API 403 限流令客户端只能显示旧缓存。以官方 Gitee app-list.json 和固定 SHA 的 app.json 生成完整 TOS 快照；SDK 提供生成、上传和读取，Desktop 调用内置 SDK。保留 manifest、目录、应用 ID、提交校验、本机兼容性计算；不改 Daemon 路由，不要求终端用户安装 Git/Python。

验收包括：列表不再请求 Gitee API，显示说话动作实验室 0.2.1；损坏/不完整快照拒绝；TOS 失败回退旧缓存；发布顺序和缓存策略；单独核查安装。正式无 Git/Python 环境尚未验证。

## 决策与验证记录

采用完整快照 latest.json（一个 GET），包含原始目录和每项原始 manifest；版本路径按内容 SHA-256 不可变。生成端使用 Git 对象读取，客户端无需 Git。发布端先上传并验证版本，再原子替换 latest；缓存 max-age=60, must-revalidate。

既有 Gitee 安装通过 commits、git/trees、blob API 下载，本次不能将目录修复描述成安装全面恢复。

关联历史：[Gitee 公开限流识别](../../2026-10-03/2026-10-03_Gitee公开目录限流识别/index.md)。本任务替代国内列表逐项 API 读取；原限流分类仍保留用于安装和其他 API。

## 2026-10-14 实现与实际验证｜wugui / Codex

### Red / Green

- 快照初测：模块不存在导致收集失败，随后 10 项通过。
- CLI 测试先因缺少 --catalog-url、默认仍读取 Gitee API 和错误分类失败；接入后目标 34 项通过。
- 发布顺序测试先因缺失 marketplace_publish 模块失败，随后生成/发布/快照专项 26 项通过。
- 跨平台入口实际执行 --help 和未知参数，Windows PowerShell 和 Git POSIX Shell 共 4 项通过，退出码分别为 0/2。
- `python -m pytest tests/distribution` 串行最终 434 项通过（Python 3.12.13）；首次与真实安装探针并发执行时 433 项通过、1 项因清理协调锁被占用失败。真实探针结束后串行重跑通过，没有绕开或删除锁。
- `python -m mypy --follow-imports=skip` 检查 marketplace_snapshot.py、marketplace_publish.py、marketplace.py、cli.py，4 文件通过。完整导入模式受构建环境缺少 psutil stubs 及 numpy stub Python 语法版本限制，未声称全仓 mypy 通过。
- Git 对象测试验证默认分支已经是 9.9.9 时仍读取审核提交的 0.2.1；禁止分支名代替固定 SHA。

### 真实发布

- 有效 `WATCHER_TOS_*` 凭据的签名读取 HTTP 200；另一组 VOLCENGINE 凭据 HTTP 403。只使用前者，不修改本机环境变量。
- 只读桶策略显示匿名 GetObject/GetObjectVersion/HeadBucket；没有匿名写入的策略授权。未修改桶策略或 ACL，不声称已完成完整 IAM/ACL 审计。
- 官方目录 commit：`ec1d1cf78a455b5ddcc0a2a42b49bbdbfb81e631`；4 个应用；说话动作实验室 0.2.1 对应 `a425b35168a27e77f99224062feb91aeb464a008`。
- 已部署快照 SHA-256：`dc79d2c50f74fdbcb25f27accb4257435413f35ca8695a8dd989bd01b500e0b2`。
- SDK 官方发布器实际执行 --publish：先创建不可变版本、公开读取校验，再条件更新 latest。
- [公开 latest](https://erroright.tos-cn-guangzhou.volces.com/WatcherRobot/marketplace/cn/latest.json) 实测 HTTP 200，Cache-Control 为 `public, max-age=60, must-revalidate`。

### 冻结与安装验证

- 使用 Desktop 现有 distribution_runtime_entry.py 和相同 PyInstaller onefile/collect 参数构建测试冻结程序，产物仅保留本地临时目录，不覆盖正式资源或版本来源记录。
- 子进程 PATH 仅保留 Windows System32，Git/Python 均不可查找，移除所有 TOS 凭据；冻结程序浏览退出 0、返回 4 项、包含说话动作实验室 0.2.1。
- Rust 实际调用该冻结 SDK，联网刷新成功，子进程代理不可用时返回 from_cache=true、warning_code=remote_error，缓存字节不变。详见 Desktop 配套档案。
- 冻结 SDK 的实际 `app download --provider gitee` 退出 4、remote_error；同一固定提交的 Gitee API 实测 HTTP 403，明确 rate_limited=true。现有下载层将此异常归为 remote_error，本任务未更改安装错误分类。
- 完整 `app install` 使用独立临时 store，超过 120 秒探针期限，未通过；没有宣布安装成功。此探针会持有 SDK 全局清理协调锁，后续不能与安装类单元测试并发执行。

## 当前交付状态与遗留事项

- 已实现并测试：SDK 完整快照读/校验、Git 生成、TOS 条件发布、配对脚本、自动发布 workflow 和正式文档；已完成首份 TOS 目录部署。
- 未提交、未推送、未合入、未发布客户端。按仓库规则，档案工作流不构成提交/推送授权。
- 自动同步 workflow 尚未合入默认分支；GitHub 当前仅有 release App 类仓库 secret，尚未配置 marketplace-publish 环境专用 TOS 凭据，因此自动同步未启用。不将本机有效密钥自动复制到 GitHub。
- Desktop 的正式来源锁仍是原提交；须提交本次 SDK 后更新锁并重新打包，禁止伪造提交或宣称已安装版本获得修复。
- 干净 Windows/macOS 的正式安装包验收尚未执行；PATH 隔离不等于干净系统验收。
- 后续源码 TOS 归档方案见 [正式合同](../../../../docs/application-marketplace/tos-catalog.zh-CN.md)。

## 2026-10-14 提交与推送准备｜wugui / Codex

用户已明确授权分别提交 SDK 和 Desktop 相关修复，推送两个功能分支，不创建 PR。
本次 SDK 提交准备包含上文已验证的快照、生成发布、测试、脚本、workflow 和文档；不包含临时制品或凭据。
为配对脚本增加 Git 换行规则，确保 Windows checkout 不会把 Shell 入口改成 CRLF。
配套客户端分支同为 `codex/tos-marketplace-20261014`，将锁定本次 SDK 的真实提交，
并将 Application Runtime 的 SDK 版本从旧锁 0.1.10 同步为当前源码 0.1.11。
原文未授权提交的状态由本条授权替代；尚未合入或发布客户端，自动同步和干净机器安装验收仍待完成。

## 后续变更：2026-10-15

用户决定改用内嵌 Dulwich，替代商店 TOS 分发，保留历史调查与部署记录。见 [后续档案](../../2026-10-15/2026-10-15_090000_wugui_无Issue_Dulwich商店读取/index.md)。
