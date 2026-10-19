# 示例与 Python 3.10 超时兼容修复补充

## 背景与范围

同一 SDK 0.1.12 发布修复任务的第二个提交，承接 CI pin 一致性修复。补齐既有示例测试清单，不删严格集合比较；修复 Pi 示例异步超时类型兼容，不增加超时、不跳测试、不改版本。

## 已观察 RED

- 原版本 GitHub Python3.10/latest：Pi 超时测试捕获内置 TimeoutError，而 asyncio.wait_for 在3.10抛 asyncio.TimeoutError；3.11起两者才是别名（已核对Python官方asyncio-exceptions说明）。
- 原示例清单严格集合比较遗漏 joyinside_pi，三格latest均失败。
- 主会话在隔离 Python3.10.22 环境仅修改测试为捕获 asyncio.TimeoutError 后再次运行，断言 process.returncode is not None 仍失败（实际为None）。确认运行源码 close 的 except TimeoutError 也无法捕获 asyncio.wait_for 超时，导致未正确kill/回收子进程。不是单纯测试失败。

## 最小修复

- tests/test_examples.py：登记 joyinside_pi 与真实app ID。
- tests/test_pi_speech_application.py：明确捕获 asyncio.TimeoutError，保留进程退出/禁止重用旧事件断言，并加强为非正常退出验证。
- examples/joyinside_pi/agent.py：close 捕获 asyncio.TimeoutError，再执行原有kill/wait逻辑。

## 主会话独立验证

Python3.11.15，独立最低依赖安装环境：pip check通过；发布/示例/Pi专项24项通过；pytest全量命令退出0（预期跳过及第三方警告）；expression lab30项通过；mypy125文件通过。构建wheel/sdist及twine检查通过；另新建wheel环境，真实安装、pip check、隔离import/0.1.12版本断言和watcherobot --help通过。全量3.11执行时Pi源码行改动尚未加入，其后Pi专项4项重新通过。

Python3.10.22，独立最低依赖真实安装：pip check通过；应用源码修复前专项23通过1失败（returncode None）；修复后同命令24项全部通过；expression lab30项通过；mypy125文件通过。

## 不隐藏的发布缺口

Python3.10全量仍有两项runtime缓存完整性失败。使用git archive origin/main抽取未修改源码和原测试独立复跑仍为2 failed,18 passed,1 skipped，说明非本修复引入；直接探针多次发现同尺寸快速改写恢复mtime后ctime_ns完全相同。已建Issue [#149](https://github.com/orulink-ai/WatcheRobot_python_sdk/issues/149)记录，不以sleep、放宽断言或跳过掩盖，不宣称完整矩阵通过。

当前没有执行Python3.12验证或实机RTC验收。GitHub CI、修复PR合并、发布候选/Tag与正式审批仍必须真实完成；未打Tag、未上传PyPI。旧版本PR #147的merge commit不包含本次修复，不能通过简单重跑旧commit获取新修复。

## 变更记录

2026-10-15，agent-server / 陆骁：同一发布修复任务追加上述示例与兼容修复；保留之前CI-only提交及基线失败历史，不改写前一阶段验证结论。
