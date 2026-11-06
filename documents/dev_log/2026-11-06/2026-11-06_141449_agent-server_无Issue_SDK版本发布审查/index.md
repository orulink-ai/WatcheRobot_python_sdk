# SDK 0.1.14 发布审查

## 基本信息与范围
- 开始时间：2026-11-06 14:14:49 +08:00（本机时区）。
- 建档作者：agent-server（git user.name）；执行：陆骁。
- 用户要求：审查并合并 SDK v0.1.14；本任务可审查、修复、提交，但版本 PR 合并与正式 PyPI 审批须负责人执行，不代为合并。
- 版本 PR：[157](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/157)；分支 release/watcherobot-0.1.14；初始 HEAD f37f45dd44bfac50f5017f86741fa5789794fbb8，基线 main 313bed0b5899b9da56659d9eca1573c663c42949。
- 审查目标：版本元数据及自 v0.1.13 以来的固件构建自动准备、安装布局和上传预检；保留作者对构建与启动语义负责、SDK 只校验声明和交付的既有边界。
- 状态：初轮最低依赖测试修复已提交；补充旧资源重试兼容修复已完成本地双环境全量验证，待提交。未合并、打标签或发布。

## 发现与 TDD
- 远端 CI 42157386183：三组 latest 通过；3.10/3.11/3.12 lowest 均在 `test_esptool_generated_image_is_accepted` 同一位置失败，其他2440项通过。早期42157384142为被新轮替换取消，不与本次真实失败混淆。
- 根因：测试使用 esptool 的 `image.save(None)` 获取字节；esptool 4.8.1 要求文件名并返回 None，5.x 才支持该内存调用。本次失败在测试生成独立镜像时发生，未进入 SDK 校验，不是已证实的生产 SDK 缺陷。
- 实际 RED：在独立 target 安装 esptool==4.8.1（不改共享环境或项目依赖声明），用当前仓库执行该现有用例，稳定得到 TypeError: expected str, bytes or os.PathLike object, not NoneType。
- 最小修复方案：始终向 pytest tmp_path 写真实镜像文件，再读取字节供 SDK 检查；保留使用 esptool 独立生成器和实际 inspect_installation 正向断言，不跳过测试，不提高最低依赖，不变更 CI 门禁。
- CHANGELOG 原条目仅记录飞书发布指令，需补充面向使用者的实际功能、兼容及实机边界。纯文档修改不适用行为TDD，以真实累计diff和档案核对。

## 验证计划与边界
- 在 esptool 4.8.1 与现有 5.6.0 环境分别执行目标用例和固件专项，并完成完整回归、mypy、打包与隔离安装。
- 本机复用已有 Python3.11 依赖环境；pytest 从当前目录优先加载本仓 src。最低工具版本使用独立 target，不声称完整六组最低依赖矩阵已在本机执行。
- 累计业务变更另行只读审查，真实平台上传、原生跨平台、冻结包与设备验收不由模拟测试替代；上一版本的用户验收不自动套用到本候选。

## 最终本地验证（2026-11-06 14:34 +08:00）
- esptool4.8.1 与5.6.0 的安装模块各40 passed，mypy 默认3.10目标140源文件通过；这次仅修改测试生成镜像方式和文档，无生产运行代码修改。
- Python3.11 复用依赖环境 + 独立 esptool4.8.1 target 全量：2441 passed / 11 skipped / 2 warnings in 357.32s；这是最低工具版本覆盖，不声称全部运行依赖均为最低版本。
- Python3.11 原 esptool5.6.0 环境全量：2441 passed / 11 skipped / 2 warnings in 262.13s；两套串行执行，避免固定端口干扰。
- 表情实验室30 passed；Node浏览器225 passed；diff --check通过。
- 重新构建0.1.14 wheel/sdist，twine两项通过；独立新venv完整安装wheel及pip check、隔离导入和发行元数据0.1.14、CLI --help通过。新增firmware.preparation/installation明确来自site-packages，固件CLI帮助入口通过。
- 实际修复只保留共有文件输出API，不提高esptool最低依赖，不跳过独立编码器镜像测试；CHANGELOG补齐真实变化和验收边界。
- 准备追加提交，远端六组矩阵须以新提交重跑结果为准。尚未合并或发布；独立业务复审作为补充审查继续跟踪。

## 补充业务审查与旧发布重试兼容修复（2026-11-06）
- 子代理指出历史HF资源元数据新增空默认字段导致幂等重试误拒；主代理以schema1合法完整包、远端历史元数据缺失partition_table，调用真实publish入口独立复现。Gitee已有manifest但Release未完成的续传路径同样受影响。
- 新增两provider×六种元数据形态的12项回归：历史字段缺失且当前为空、显式空值可重试；真实摘要变化、显式null、不同表路径、v2必需表字段缺失必须拒绝，且不覆盖远端元数据/重新上传旧附件。
- 首轮测试还存在JSON tuple/list比较导致的辅助断言失败，改为比较原元数据字节；保留真正RED为2 failed/10 passed，两个provider均因历史空字段缺失误拒。
- 最小修复增加共用元数据比较函数：仅当当前包partition_table确实为空且历史字典没有该key时补空默认值；其余字段仍完整比较，显式null不等价，不改存储的历史元数据。
- GREEN：12项新回归通过；esptool4.8.1下固件及桥接专项209 passed/3 skipped，mypy140源文件通过，diff检查通过。最终全量和构建重新执行中，不复用此前2441计数。
- 该追加包含生产adapter修复，不再把当前PR描述为只有测试/文档变更。未执行真实平台上传或设备写入；暂不放行合并。

## 追加修复最终验证
- 主代理加载 git v0.1.13 真实 inspector，对同一schema1完整包独立比较实际输出，确认唯一新增差异为默认空partition_table、ZIP摘要一致；不是仅按子代理自报判断。
- 新增12项接口回归全部通过；固件及桥接209 passed/3 skipped。最终Python3.11+esptool4.8.1全量2453 passed/11 skipped/2 warnings，325.99秒；esptool5.6.0全量2453 passed/11 skipped/2 warnings，280.14秒。
- mypy默认配置140源文件通过；最终wheel/sdist、twine、新wheel重装、pip check及隔离版本0.1.14检查通过。wheel内platforms.py与当前源码字节一致，隔离安装中直接验证历史空字段兼容和不同摘要拒绝均通过。
- 正式文档与更新日志同步窄范围默认值兼容；保留显式非法值及真实内容冲突检查，没有真实线上覆盖或重新发布旧资源。
- 当前审查无剩余已复现而未修复的业务缺陷；版本PR仍须新提交远端六组矩阵、负责人对本候选验收与人工合并，正式PyPI审批不变。

## 关联历史
- 补充[固件安装校验](../../2026-11-05/2026-11-05_164118_wugui_无Issue_固件安装校验/index.md)的发布依赖兼容验证；不改写原作者此前平台与依赖环境下的真实测试历史，旧档案将追加反向链接。
