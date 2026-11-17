# Task 1：三种开发项目模型与模板

- 时间：2026-11-17，Asia/Shanghai；建档作者：wugui（git config）；实施：Codex。
- 状态：本地实现与聚焦验证完成，未提交、未推送；主控制器负责合并 SDK 子任务并审阅交付。
- 基线：origin/dev 581689e；隔离分支 codex/firmware-projects-dev-20261117。
- 已阅读 AGENTS.md、开发档案 README/temp_log、现有 Application 生成器及测试、本任务 index、2026-10-29 固件配套安装档案。历史固件配套合同保持，本次仅新增项目入口，不替代其发布语义。
- 范围：project 包、根 CLI、development project 测试和文档；不修改 firmware 或 distribution 实现，不修改 C SDK/ESP/STM32，不烧录、不联网发布。

## 目标与实现

提供 application/firmware/combined 三种离线模板与 add/inspect。新根清单 schema_version=1；旧根 app.json 可直接识别并原位添加固件。Application 完整复用原生成器；固件模板只有身份描述、build_path 与 README。组合模板不伪造 requires_firmware。

公开 inspect_project(Path)->dict，字段为 kind/project_root/application_directory/firmware_directory/firmware_metadata/firmware_build_directory。后者依据固件根 build_path（缺省 build），未编译返回 null；仅四个身份字段进入 metadata。内部 dataclass 供 init/add 返回。

路径边界拒绝绝对路径、父目录、symlink 与 Windows reparse point。排他创建目标和成员，add 临时锁串行化 SDK 写入，清单替换失败回滚新成员。旧清单首次创建使用原子硬链接以免覆盖并发出现的文件。本次没有新增开发者系统脚本。

## TDD 证据

1. 初始 Red：新增 tests/application/test_development_projects.py，执行 `.venv/Scripts/python.exe -m pytest tests/application/test_development_projects.py -q`：15 failed。原因分别是 project 模块不存在、CLI 无 project 命令，符合新增能力预期。
2. 首次 Green：同文件加原 test_project_init.py：25 passed。mypy 五个文件通过。
3. 字典桥接与 build_directory Red：添加公开 dict 与构建目录测试，出现 2 failed，明确 dataclass 不支持字典访问；实施桥接后通过。追加构建字段合同后 6 failed，修复返回字段并分离 metadata 后通过。
4. 兼容性 Red：模拟无新版 pathlib Junction API，`-k without_new`：1 failed，未拒绝 Junction 外部目标。改为 lstat st_file_attributes reparse 检查；真实 Windows Junction 回归通过。
5. 审阅要求 Red：`-k 'schema or identity'`：9 failed，发现新清单无 schema_version、bool/未知版本未拒绝、长目录名固件 ID 超64、固件版本未沿用合同。加入 schema_version=1 严格类型校验、ID 稳定截短与既有 firmware.contract 校验后通过。
6. 后续回归新增失败回滚、元数据非法值、已有成员、Windows Junction 和默认构建目录场景；这些是补充回归，未声称均独立经历 Red。

## 验证

Windows Python 3.12.10、工作区 .venv（editable SDK[test]）。

- `.venv/Scripts/python.exe -m pytest tests/application/test_development_projects.py tests/application/test_project_init.py tests/test_app_cli_usability.py -ra`：58 passed，1 skipped（本机无创建真实 symlink 权限），1 既有 Traversable 弃用告警；两个真实 Junction 用例通过。
- `.venv/Scripts/python.exe -m mypy src/watcherobot/project src/watcherobot/cli.py`：Success，5 source files。
- `.venv/Scripts/python.exe -m ruff check src/watcherobot/project tests/application/test_development_projects.py`：通过；格式化仅自己的新增文件。
- ruff 原未安装，仅在本地 .venv 安装用于检查，未更改包依赖。
- 未执行真实 macOS/Linux、Python3.10/3.11 或冻结桌面打包；通过模拟无新 pathlib API 覆盖 Python 3.10/3.11 Junction 防护风险。桌面冻结桥由配套子任务调用公开 inspect 函数。

## 材料

正式接口文档：../../../../docs/development-projects.zh-CN.md。
测试：../../../../tests/application/test_development_projects.py。
代码：../../../../src/watcherobot/project/。

## 遗留与边界

未声称防御任意外部进程在每个文件系统操作间恶意替换目录；覆盖用户误操作、已有路径、路径越界和 SDK 并发写入。强杀进程可能遗留锁或未登记成员，文档要求先检查状态，不自动删除用户内容。真实 POSIX symlink 与 macOS 实机尚待后续环境验证。
