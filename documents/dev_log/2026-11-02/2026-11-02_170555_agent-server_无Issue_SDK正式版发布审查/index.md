# SDK 0.1.13 正式版发布审查

## 基本信息
- 创建时间：2026-11-02 17:05:55 +08:00（本机时区）
- 建档作者：agent-server（当前 git user.name）；审查执行：陆骁
- 用户需求：审查相关修改、修复问题并推进 0.1.13 正式发布。
- 分支：release/watcherobot-0.1.13；PR：[#154](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/154)
- 基线：main 0579082606c6619e52d52cce61520a64342d2382；初始版本提交 417b563960493f5f2fc896b93f78835e83d4fd7e。
- 状态：本地修复及验证完成，准备提交并等待新提交远端 CI；尚未合并或正式发布。

## 范围与边界
- 核对版本源、发布说明、打包、发布门禁及最近业务变更；发现确认缺陷则追加修复。
- 最近正式 GitHub Release/tag 是 v0.1.11；main 已有 0.1.12 元数据但不存在 v0.1.12 tag，不将元数据视为已发布。
- 不代替负责人合并版本 PR，不批准 pypi Environment，不修改或绕过 OIDC、TestPyPI、正式审批门禁。
- 自动测试不替代 RTC、真实固件上传/下载、USB 烧录实机验收。

## 已确认问题与修改
- CHANGELOG 的 0.1.13 初始条目仅有飞书发布指令；补充面向使用者的实际功能、兼容性与验收边界。版本保持 0.1.13，不修改历史发布条目。
- 本项为文档修改，不适用测试先行的行为 TDD；以主线 diff、现有回归和打包检查核验内容，不声称先写了失败测试。

## 验证记录
- 本地 Python 3.11，独立 .venv 安装当前 SDK 和声明测试依赖成功。
- mypy：Success: no issues found in 137 source files。
- pip check：No broken requirements found。
- Node 浏览器助手：225 passed，0 failed。
- 全量 pytest、构建及 wheel 验证正在执行，结果完成后追加。
- 远端原候选 CI 41416060969：Python 3.10 lowest/latest 已 success，其他矩阵尚未完成；不将此结果视为后续提交的检查结果。

## 后续调查与修复
- PyPI JSON 实查：latest=0.1.11，0.1.12/0.1.13 均不存在；GitHub Release/tag 同样最新为 v0.1.11。因此发布说明明确以最近正式版本 0.1.11 为比较基线，保留 0.1.12 历史条目。
- 首轮本地全量：2327 passed / 8 skipped / 1 failed。失败测试会读取相邻固件检出 `/home/hermesadmin/work/WatcheRobot_esp32`；该检出为 8d7e9fc3 且缺 `chat_v1_talk.json`，并非独立 SDK 源码回归失败。没有修改相邻仓库或放宽断言；复制当前源码至无相邻固件检出的隔离目录后，同一全量为 2328 passed / 8 skipped / 2 warnings。此结果仅对应锁修复前代码。
- 远端 Python 3.11 / lowest 作业 125180089221 实际失败：3 个配置用例因 `/tmp/watcherobot-manifest-locks` 内锁文件 PermissionError，2325 passed / 8 skipped。全局目录由其他系统账号先创建时不可写，属于代码的多账号环境缺陷，不仅是 CI 误报。
- TDD Red：新增回归创建只读全局临时锁目录，调用真实 configure，在 FileLock 内稳定 PermissionError（1 failed）。Green：锁目录移至 `Path.home() / '.watcherobot' / 'manifest-locks'`（新建权限 0700），仍以规范化 manifest 路径哈希作为锁名，同一用户跨 cache_root/进程共享互斥；无关闭锁、chmod 他人文件或删除旧锁的旁路。
- 聚焦回归：固件操作 + 发布工具 + 发布合同共 74 passed；mypy 137 源文件通过，diff --check 通过。
- 兼容性边界：新旧版本锁命名空间不同，更新日志要求升级期间退出旧版本配置进程；不承诺不同系统账号对同一共享应用目录的写入互斥。
- 首轮构建与 twine wheel/sdist 通过；全新 venv 安装 wheel、pip check、隔离导入/元数据 0.1.13、两个 CLI --help、内置示例提取通过。此结果对应锁修复前构建，最终修复后正在重新构建及全量回归。

## 最终本地验证
- 修复后隔离源码全量：2329 passed / 8 skipped / 2 warnings，226.61 秒。保留 Starlette 弃用、重复 ZIP 预期测试告警；不声称零告警。
- 最终 wheel/sdist 重新构建及 twine check 均通过；在之前全新安装的 wheel venv 强制替换最终 wheel 后，pip check、隔离导入、模块/发行元数据 0.1.13 及 CLI --help 通过。
- 表情实验室 30 passed、浏览器 Node 225 passed；修改仅影响配置锁与文档，相关固件/应用/下载用例补跑 33 passed。
- 额外尝试安装 filelock 3.16.0 做最低依赖专项时网络下载超时，未获得结果；本机锁回归使用 filelock 3.32.7。远端新提交六组 Python/依赖矩阵仍须验证，不把旧提交检查记为新提交通过。
- 本次审查结论：本地修复与验证完成，尚未合并、打 tag、上传 TestPyPI 或发布 PyPI；正式版仍受 RTC 验收及负责人审批门禁约束。

## 关联历史
- 对[固件广场配套安装审查](../../2026-10-29/2026-10-29_170000_wugui_无Issue_固件广场配套安装/index.md)追加修复：替换全局临时锁目录，不改变该档案并发保护、固件撤回与布局校验的原有结论；原档案已追加反向链接。

## 遗留事项
- 负责人确认与目标版本相符的 RTC 实机验收证据；现有历史记录不自动代表本候选通过。
- 修订后需重新核对远端 CI；负责人审查并合并后才可创建 annotated tag 并执行 TestPyPI 流程，正式 PyPI 仍需人工审批。
