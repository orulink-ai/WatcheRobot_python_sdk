# SDK 0.1.13 正式版发布审查

## 基本信息
- 创建时间：2026-11-02 17:05:55 +08:00（本机时区）
- 建档作者：agent-server（当前 git user.name）；审查执行：陆骁
- 用户需求：审查相关修改、修复问题并推进 0.1.13 正式发布。
- 分支：release/watcherobot-0.1.13；PR：[#154](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/154)
- 基线：main 0579082606c6619e52d52cce61520a64342d2382；初始版本提交 417b563960493f5f2fc896b93f78835e83d4fd7e。
- 状态：配置锁修复已提交；撤回缓存追加修复经主代理复核及全量验证，准备提交。尚未合并或正式发布。

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

## 2026-11-02 17:33 +08:00｜agent-server／修复子代理｜撤回缓存序列与并发绕过
- 范围：在 HEAD `4f7b3ae514b7b4e4f1be4c4defba9b70e9d27a87` 上本地修改固件操作、相关测试及本档案；未提交、推送、合并或修改发布审批。
- 根因一：远端已返回撤回目录，保存失败仅终止当前调用，旧批准目录仍可供下一次离线下载使用。先扩展既有 `test_catalog_persistence_failure_cannot_fall_back_to_old_approval`，加入下一次离线下载断言。RED：`.venv/bin/python -m pytest tests/distribution/test_firmware_operations.py -k catalog_persistence_failure -q`，两个 action 均 `DID NOT RAISE FirmwareError`（2 failed）；实现写前持久化禁止回退标记后，同命令 2 passed。
- 修复一：在当前用户 `~/.watcherobot/catalog-locks/` 保存 `.invalid` 写前标记，目录新建权限 0700，标记写入 flush/fsync；仅目录成功落盘且标记成功删除才允许回退。目录写失败保留标记；标记写失败则删除旧目录。若标记及删除都失败，当前进程内仍禁止回退并明确报错 `offline safety cannot persist across restart`，不承诺不可写存储的跨重启持久化。
- 根因二：`os.replace` 仅保证单次文件替换，不能协调远端读取、目录写入、离线读取及包交付；旧批准响应可以在新撤回目录之后保存。新增线程及 spawn 独立进程测试，marketplace/download 四种组合、真实目录与 symlink 别名混用。RED：`.venv/bin/python -m pytest tests/distribution/test_firmware_operations.py -k catalog_transactions -q --tb=short`，8 failed，捕获远端事务重叠及最终缓存 `withdrawn=False`；实现事务锁后该组全部通过。
- 修复二：以规范化 `catalog.json` 真实路径（resolve + normcase）SHA-256 标识用户目录中的 FileLock，10 秒超时即失败关闭；marketplace 与 download 从远端读取前一直持锁至持久化、离线选择及包交付完成。其他目录读取通过加锁 `_releases`，锁内使用 `_read_releases`，避免重复获取新 FileLock 造成递归死锁。未声明／not_required 的应用仍在目录访问与锁获取前返回。
- 补充验证（实现后追加，非独立 RED 证据）：新解释器读取验证持久标记；标记不可写时的旧目录删除与双不可写告警；独立离线进程等待正在进行的撤回事务。未删除他人临时文件、未调整他人权限。
- GREEN：`.venv/bin/python -m pytest tests/distribution/test_firmware_*.py tests/application/test_firmware_metadata_bridge.py -o addopts='' -q` → **96 passed in 17.54s**；`.venv/bin/python -m mypy src` → **Success: no issues found in 137 source files**；`git diff --check` 通过。完整仓库回归由主审查代理独立执行；未将此前全量结果冒充本次最终结果。
- 风险／边界：同一 cache/provider 的远端及包获取现在串行，长下载可能使其他操作等待 10 秒后收到重试错误；不同 cache/provider 不共锁。旧 SDK 不识别新锁与禁止回退标记，升级需停止旧进程；不同系统账号共享缓存不在互斥保证内。文件 fsync 不等于目录项在断电时绝对耐久，本次测试覆盖进程重启与正常文件系统错误，不保证突然断电或恶意手动删除状态标记。双不可写时其他进程／重启仍无法获得内存兜底状态，错误已明确说明这一不可保证边界。

## 2026-11-02 17:44 +08:00｜agent-server／陆骁｜主代理复核与最终回归
- 主代理另写临时复现，真实复现了保存失败后离线返回包、并发新撤回被旧批准覆盖两个缺陷，已在 PR 请求修改并暂停合并。复现不改变仓库文件，不将子代理自报当作验证依据。
- 完整复核事务锁范围、非递归读取、标记失效与恢复路径；主代理独立执行固件专项 96 passed in 26.90s，mypy 137 源文件通过。
- 修复后隔离源码全量：**2340 passed / 8 skipped / 2 warnings in 258.57s**。两个告警仍为 Starlette 弃用与重复 ZIP 测试，非新增回归。此前 2329 结果仅为旧阶段，不能替代此结果。
- 最终 wheel/sdist 重新构建及 twine check 均 PASSED；独立 wheel 环境重装最终包后 pip check 无冲突，隔离导入／发行元数据均为 0.1.13，CLI --help 通过。
- 补充恢复检查实际验证：保存失败留下标记，离线重试拒绝；随后成功刷新移除标记，并恢复合法离线缓存复用。Python 3.10 对源码及 Pi 示例 151 文件语法编译通过，不将其记为 3.10 完整运行测试。
- CHANGELOG 与固件正式文档同步事务串行、持久标记及双不可写边界。审查结论转为修复已本地验证，仍须新提交远端矩阵与负责人确认 RTC；未合并、未打 tag、未上传 TestPyPI／PyPI、未审批。
