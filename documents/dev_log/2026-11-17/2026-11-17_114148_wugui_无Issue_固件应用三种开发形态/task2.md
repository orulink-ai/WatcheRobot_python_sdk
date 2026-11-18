# Task 2：离线固件准备与应用提取

- 日期：2026-11-17 Asia/Shanghai；作者：wugui / Codex；无 Issue。
- 状态：本地实现、分发全套及类型验证完成；全 SDK 默认编码回归有3项语音测试失败，环境定位见末节。未提交、推送、发布或访问真实设备。
- 已阅读本仓 AGENTS、档案规范与模板、既有固件安装校验档案；延续本目录任务，不另建重复档案。
- 边界：仅 Python SDK 准备/检查与分发入口；不新增系统脚本、串口操作、业务路由或 USB 烧录。

## 真实实现

- `prepare_package(source, cache_root, *, metadata=None, mode=None, application=None)`；保留默认成品包字节及摘要，增加 applications 和 metadata。
- 无扩展名 flash_args、构建目录/外层 ZIP；明确 full/application 转换、label 选择；application 输出仅一个应用写段和只读参考表，不移动地址、不写数据或引导。
- 展示 metadata 仅 id/name/version/description/author/notes 字符串；未知及技术字段拒绝；作者可补全缺少的构建名称/版本，技术检查仍强制。
- 标准包覆盖展示信息重新内容寻址，所有非 manifest 字节与原 segments 保持。
- 离线 firmware prepare CLI，不访问 Provider/登录/Daemon；distribution/cli.py 的命令识别同步加入 prepare。
- 现有 execute inspect 透传 metadata/mode/application；新增 local project_inspect，先检查 project_directory 后调用公开项目模型，不访问 Provider。
- capability 增加 firmware_preparation_v3、development_projects_v1；两条原 capabilities 入口测试明确断言完整 capability 列表。

## TDD 依据

1. 首轮 `python -m pytest tests/distribution/test_firmware_preparation.py -k v3 -q`：9 failed；接口不接受 metadata/mode、CLI 没有 prepare，属目标缺失。
2. inspect 离线透传和 project_inspect 参数拒绝：2 failed，分别访问 Provider/不支持 action；加入本地分支后通过。
3. 标准包单行 flash_args 应用转换：先失败 Flash arguments and manifest segments disagree；改用已严格验证的 flash_options 重建选中应用写参数后通过。
4. 缺构建版本由作者补充：先失败 Firmware version must use three release numbers；覆盖移到构建 manifest 校验前后通过。
5. 无项目描述/空描述的自定义 factory@0x10000、16MB 三镜像构建：2 failed，缺描述/名称；允许作者填展示身份后2通过。纯函数 plan_installation 验证32MB设备容量兼容。
6. 无 boot 的构建只能显式 application，full 拒绝；标准包只改展示字段保持所有技术字节；三种项目 execute 检查为集成补测，未冒称所有追加用例均事前 Red。

## 验证

- `.venv/Scripts/python.exe -m pytest -o addopts='' <tests/distribution/test_firmware_*.py 展开的文件列表> -q`：218 passed，9 skipped，1项既有依赖弃用提示。后追加3个项目桥集成测试全部通过。
- `.venv/Scripts/python.exe -m pytest tests/distribution/test_firmware_preparation.py tests/distribution/test_firmware_cli.py -q`：64项通过；含项目桥三种形态、capabilities、原有离线检查和新增准备边界。
- `.venv/Scripts/python.exe -m mypy --no-incremental --python-version 3.12 src/watcherobot/firmware`：12个源码通过。
- `git diff --check`：通过。black 应用于本任务 preparation/firmware CLI/tests；operations 保持原格式以缩小无关 diff。
- `.venv/Scripts/python.exe -m pytest -o addopts='' -q`：全仓回归已结束，实际结果与环境定位见末节。
- 不含真实硬件、账号写操作、发布、macOS宿主验收；Windows既有skip不冒称通过。

## 配套与材料

- 正式接口文档：`docs/application-marketplace/firmware-implementation.zh-CN.md`末节。
- 代码：`src/watcherobot/firmware/{preparation,cli,operations}.py`、`src/watcherobot/distribution/cli.py`。
- 测试：`tests/distribution/test_firmware_preparation.py`、`test_firmware_cli.py`。
- Task1 项目模型通过公开 inspect_project(Path)->dict 对接；Task3 桌面通过既有 execute JSON request 对接，无新业务协议。

## 验证追加

- `.venv/Scripts/python.exe -m pytest -o addopts='' tests/distribution -q`：686 passed，11 skipped，1项既有依赖弃用提示（38.07秒），包含最新三种项目桥集成测试。
- 最终再次检查 firmware mypy：12源码通过；git diff --check通过。
- 兼容性核对：已有 project_name 存在时仍使用原名生成默认 ID；展示 name 覆盖不会隐式改变旧构建的 ID。只有缺少项目名时才以作者补充名称建立身份。

## 全 SDK 回归实际结果与环境定位

- `.venv/Scripts/python.exe -m pytest -o addopts='' -q`：2506 passed，28 skipped，3 failed，3 warnings，259.27秒；**不能声称全仓通过**。
- 失败均位于未修改的 `tests/test_pi_voice_reply.py`：`test_voice_tool_waits_for_playback_then_keeps_final_separate`、`test_failed_or_cancelled_playback_never_releases_tool[failure]`、`test_failed_or_cancelled_playback_never_releases_tool[cancel]`。表现为Pi测试子进程提前退出、等待播放事件超时。
- 独立原环境重跑该文件：3 failed、5 passed。该测试通过默认编码写出含中文的临时 Python 源码；仅为测试进程设置 `PYTHONUTF8=1` 再运行同一文件：8 passed（0.57秒）。该环境差异可复现，未改动语音示例或测试来掩盖问题。
- 本任务实现/固件验证完成。SDK全仓默认Windows编码环境仍有上述3项失败；未进行全仓UTF8模式复跑，不将单文件通过冒称全仓通过。


## 最终 UTF-8 全量回归（主任务复核）

- Windows Python 3.12，设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，执行 `.venv/Scripts/python.exe -m pytest -q -o addopts=''`：**2512 passed、28 skipped、3 warnings，233.54秒**。
- 覆盖最终三种项目与固件准备代码；未修改 Pi 语音测试掩盖默认编码问题。原始默认编码失败和定位记录保留在上节。
- 主任务另用临时目录做跨仓源码集成：三种项目初始化及成员添加，经真实 project_inspect 桥读取；自定义 C SDK 三分区、factory@0x10000、16MiB 完整包由桌面解析；显式应用更新与32MiB设备布局计划通过。无真实USB/平台写入。
