# 发布最低依赖 pin 与运行声明一致性修复

> 当前阶段（2026-10-17）：用户授权在原 PR #148 追加修复，通过 merge main 解决冲突，保留三项既有修复。main 已纳入 Pi 示例登记与超时兼容，因此本阶段重点是正确整合和重新验证。最新结果见[追加修复记录](refresh-20261017.md)。下文第一阶段状态、未验证说明和原始日志均按历史保留，不代表当前最终结论。

## 基本信息

- 创建时间及时区：2026-10-15T01:50:06+08:00（本机 Asia/Shanghai）
- 建档作者：agent-server（当前 git config user.name）
- 参与者：Hermes Agent
- 最后更新时间：2026-10-15T02:05:58+08:00
- 关联需求：用户授权修复 SDK 0.1.12 发布 CI，创建修复 PR，不合并；无 Issue
- 相关分支：fix/release-lowest-huggingface-hub
- 涉及模块：.github/workflows/sdk-ci.yml、tests/test_release.py
- 类型：CI 修复
- 任务状态：最低依赖修复已验证，准备提交及创建 PR；全量基线失败另行说明
- 交付状态：本地修改；本记录在提交前写入，未合入、未打 tag、未发布

## 背景与目标

发布矩阵最低依赖步骤仍指定 huggingface-hub==1.26.*，而当前 pyproject.toml 要求 >=1.32,<2；二次 pip install 可以降级已安装依赖，随后 pip check 检出不一致。历史下载进度档案说明 1.32 下界用于公开字节进度 hook，不能降低运行要求来绕过发布门禁。

- 本次包含：修正 CI pin，新增最低 pin 对项目声明的回归测试，最低依赖和 SDK 一次解析，隔离环境真实安装与测试，保存证据。
- 本次不包含：运行源码、版本变更、发布门禁放宽、合并、tag、PyPI 发布。
- 必须保留：>=1.32,<2 声明，pip check、pytest、mypy、构建及发布矩阵门禁。

### 验收

- [x] 回归先因旧 pin 失败，再修正使其通过。
- [x] 所有现有最低依赖 pin 的下界与真实运行声明比较，不另写一份依赖下界。
- [x] SDK 与最低 pin 同一 pip 解析；真实安装后 pip check 无冲突。
- [x] 执行相关测试、mypy、示例及完整测试，并明确报告失败和跳过。
- [x] 档案双向关联，代码与证据准备同一提交。
- [ ] 创建 main 修复 PR，不合并（提交后由 gh 执行，本文不提前声明成功）。

## 关键发现与决策

- 已阅读 AGENTS.md、dev_log/README.md、temp_log.md 及历史下载进度档案；无对应进行中修复档案。
- tests/test_release.py 原矩阵断言也固定旧 1.26 pin，随 CI 修复改为 1.32。
- 新测试使用 tomli（已有 test extra）和 packaging（已有运行依赖）读取真实声明；按包名规范化后逐一验证工作流 pin 的最低版本满足声明。涵盖当前全部 13 个 pin，支持通配和精确 pin，不声称覆盖未 pin 的依赖或所有未来区间的数学包含关系。
- 最低步骤的 pip install 增加 -e ".[test]"，让 resolver 看到 SDK 约束，不依赖降级后的 pip check 才失败；保留 pip check 作为最终门禁。
- 不引入 YAML 解析依赖、不大幅重构 workflow、不改变 latest 步骤或版本。

## 实际变更

1. .github/workflows/sdk-ci.yml：1.26.* → 1.32.*；最低步骤同次安装 SDK/test extra。
2. tests/test_release.py：同步现有断言，增加 pin/声明一致性和同次解析两个回归测试。
3. 本任务档案与实际输出；历史下载进度档案追加后续关联，原正文保留。
4. 工作区出现他人/并发修改：tests/test_examples.py、tests/test_pi_speech_application.py、examples/joyinside_pi/agent.py。本任务未编写、未回滚这些变更，也不纳入本次提交；全量验证另外使用仅包含本次 CI/测试改动的快照，避免混淆。

## 过程材料索引

| 材料 | 用途 |
|---|---|
| [验证报告](validation.md) | 环境、命令、结果、基线问题与缺口 |
| [pin RED](tdd-red.log) | 修改 CI 前，明确因 1.26 与 >=1.32 冲突失败 |
| [第一轮 GREEN](tdd-green.log) | 修正 pin 后发布测试 13 项通过（第二个测试尚未添加） |
| [同次解析 RED](resolver-red.log) | SDK 尚未参与最低步骤解析时失败 |
| [最终发布回归](release-regression.log) | 最终 14 项发布测试 + 45 项工具测试通过 |
| [真实 pip 证据](pip-validation.log) | 旧 pin ResolutionImpossible、新 pin 安装成功、pip check、安装版本、JS 结果摘录 |
| [mypy](mypy.log) | 125 个源码文件通过 |
| [示例](expression-lab.log) | expression lab 30 项通过及弃用警告 |
| [第一次全量](pytest-full.log) | 含并发工作区修改，1911 通过、3 失败、5 跳过，不作为本次 PR 全量成功证明 |
| [仅本次改动快照全量](pytest-owned-snapshot.log) | 基于 cc0a319，排除并发修改；1911 通过、3 失败、5 跳过 |
| [Runtime 基线复现](runtime-baseline.log) | 不修改 Runtime 代码及测试，基线也有同样失败 |
| [示例清单基线复现](examples-baseline.log) | 原测试漏列已存在的 joyinside_pi |

## TDD 与验证

### Red → Green

- 第一轮：先加 pin/声明测试，运行仅该测试，1 failed；错误明确为 huggingface-hub==1.26.* conflicts with huggingface-hub<2,>=1.32。再修改 CI pin 和已有矩阵断言，13 passed。
- 第二轮：先加同次解析测试，1 failed；再为最低步骤加入 -e ".[test]"。最终发布及工具回归 59 passed。
- 隔离 venv：旧 pin 的同次解析 dry-run 返回 ResolutionImpossible（不执行降级）；新 pin 的工作流真实命令执行安装，huggingface-hub 1.32.0、watcherobot 0.1.12，pip check 返回 No broken requirements found。
- 无运行代码重构；测试提取 regex 支持精确 pin，元组解包仅做格式整理，整理后再次运行 59 项通过。
- 完整测试未全绿，详见验证报告；不宣称远端 CI 或发布已通过。

## 变更记录

### 2026-10-15T01:50:06+08:00｜agent-server

读取规范、历史档案及当前 workflow、声明、发布测试；确认 1.32 下界必须保留，创建本任务档案，按 TDD 开始修复。

### 2026-10-15T02:05:58+08:00｜agent-server

修复两个 CI 行变更和两项回归测试，完成实际最低依赖安装、59 项专项、30 项示例、225 项 JS、125 文件 mypy；全量快照 3 项失败已在基线复现，5 项 Windows 条件测试跳过。更新真实输出和双方档案链接，核对 diff；仅提交本任务文件，不混入并发修改。尚未合入、tag、发布；远端 CI 和其他 Python 版本等待后续验证。

## 关联历史

- 补充并解决 CI 未同步问题：[桌面下载进度与空闲应用取消选择](../../2026-10-02/2026-10-02_桌面下载进度与空闲应用取消选择/index.md)。保留 1.32 下界及原下载进度能力，不替代历史功能结论；原档案后续区已补充反向链接。

## 后续阶段：示例清单与 Python 3.10

2026-10-15，agent-server / 陆骁：CI-only 修复已提交并创建 [PR #148](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/148)。同一任务继续补充示例与 Python3.10兼容修复，详见 [补充验证记录](examples-and-py310.md)。前述第一阶段“未纳入示例修改”的说明仍是其提交时事实；本次追加将这三处改动纳入第二个提交，不覆盖原始日志。已确认3.10专项通过，另有原main可复现的runtime缓存问题见Issue #149，仍阻止宣称发布验收完成。

## 遗留事项与后续提醒

- 完整快照失败：Runtime 同尺寸写入并恢复 mtime 的缓存失效测试两项；基线示例清单缺 joyinside_pi 一项。Runtime 另一用例在第一次全量和 tmpfs 重试中间歇失败，保留证据，不在本次 CI pin 修复中扩展源码范围。
- 未在本机验证 Python 3.10/3.12、Windows、真实设备或六格远端发布兼容性矩阵；未合并、打 tag、审批或发布。
