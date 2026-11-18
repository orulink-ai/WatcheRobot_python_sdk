# 固件发布要求完整安装包

- 创建：2026-11-18 16:30:12 Asia/Shanghai；作者：wugui；参与：Codex；无 Issue。
- 分支：codex/firmware-full-publication-main-20261118；基线：最新 origin/main 98bb56078c39fbe13006817beeabf210261652b9。
- 状态：已提交功能分支；合入前二次审查与本地 CI 通过，按最新授权准备中文 PR，等待远端 CI 后正常合入 main。

## 目标与边界

发布人从构建产物自动提取完整分区及烧录信息；新发布与上架审核只接受完整安装包。允许自定义分区名称、数量、地址与大小，不强制官方布局。不从旧设备推断布局、不补造镜像或 OTA 数据。作者负责构建及启动验证。

保留 prepare/inspect、历史读取、本地应用更新能力。桌面端不增加选项；C SDK、ESP32 不修改。固件、应用、应用＋固件项目继续支持。

## 实施计划

1. 先补失败测试：两个 Provider 的 publish、直接底层发布、submit 均拒绝 application 包，execute 在读取凭证前拒绝；自定义完整包仍可发布。
2. 复用现有安装检查，增加共享发布检查，并接入所有发布/提交入口；不改通用安装策略。
3. 更新固件和组合项目模板、作者指南及接口文档，说明构建清单自动提取、完整包要求和作者责任。
4. 定向测试、全仓 pytest、相关 mypy、diff 检查和整分支审阅；据实际结果更新档案。

## 验收标准

- 不完整包在上传前被拒绝，错误说明如何提供完整构建产物。
- 历史应用更新包仍可读取和本地准备，不再接受新发布及上架。
- 完整 ZIP/构建目录保留自定义布局，国内/海外发布入口一致。
- 模板、文档与实现一致；测试结果如实记录。

## 关联历史

部分替代 [三种开发形态档案](../../2026-11-17/2026-11-17_114148_wugui_无Issue_固件应用三种开发形态/index.md)：仅收紧新发布与上架的完整性要求，原应用更新和自定义布局能力保留。原档案已追加反向链接。

## TDD 与验证

### Red

修改前执行 `.venv/Scripts/python.exe -m pytest tests/distribution/test_firmware_publication.py -q`：8 failed、1 passed。8 个失败均证实 application 更新包进入了凭证读取、平台身份查询或目录访问，未按新发布要求拦截；本地准备/检查用例通过。

### Green 与回归

- 同一新增文件修改后 9 个用例通过。
- 完整定向回归命令：`.venv/Scripts/python.exe -m pytest tests/distribution/test_firmware_publication.py tests/distribution/test_firmware_platform.py tests/distribution/test_firmware_operations.py tests/distribution/test_firmware_preparation.py tests/distribution/test_firmware_installation.py tests/application/test_development_projects.py -o addopts='' -q --tb=short`：219 passed / 10 skipped / 1 warning，17.02 秒。
- 全源码 mypy：`python -m mypy src/watcherobot`，145 source files 通过；`pip check` 无依赖冲突。
- examples/expression_lab/tests：30 passed；`node --test tests/js/*.mjs`：225 passed。
- `pip wheel . --no-deps --wheel-dir <系统临时目录>` 成功，ZIP 内容检查确认新 publication 模块及模板进入 wheel。未修改版本或发布制品。
- `git diff --check` 通过。
- 全仓 `.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short`：2537 passed / 28 skipped / 3 warnings，242.42 秒。该次收集后补入的 Gitee 正向 submit 参数用例由上述最终定向回归覆盖；生产代码未再变动。测试环境为本机 Windows、Python 3.12、UTF-8。

### 审查与取舍

独立整分支审查未发现 Critical / Important / Minor 问题。原发布相关测试改用真实结构的完整包，安装/下载等历史更新包 fixture 默认保持不变。新增国内正向 submit 用例初次漏模拟身份查询，返回 401；补齐测试隔离后又暴露 HF fixture 缺少 Gitee 附件字段，按真实目录合同修正 fixture，双平台正向用例复测通过。上述为测试扩展缺陷，不改动产品网络或身份逻辑。

未改变安装策略；将发布策略拆为共享纯检查，调用安装检查后只要求 full 模式。模板/文案变动使用现有项目行为回归与 wheel 内容检查，不增加与文案逐字绑定的测试。没有进行真实平台发布或重复 USB 写入：本次只改变发布入口，已有安装器未修改。任意固件实际启动及必要初始化数据由作者硬件验收，通用结构检查不冒充运行证明。

## 实际变更

- 新增 `firmware/publication.py`；`operations.execute publish` 在凭证读取前校验，平台上传及 submit 匿名下载后复查均使用相同策略。
- 固件与组合模板说明完整构建产物、自动提取、自定义布局及启动验证责任；开发指南、固件合同、国内外目录模板同步。
- 拦截 application 包的双平台 publish/_publish/submit/execute；验证完整构建目录、外层 ZIP、标准 ZIP 的自定义表地址、大应用分区、用户数据类型、factory/OTA/test subtype；保留本地更新读取与准备。

## 变更记录

- 2026-11-18 16:37 Asia/Shanghai｜wugui / Codex：按用户要求从最新远端 main 创建功能分支，实现发布完整性要求；Red/Green 与审查证据如上，全仓验证通过，准备提交至独立功能分支，不合入 main。

- 2026-11-18 16:38 Asia/Shanghai｜wugui / Codex：全仓测试通过，最终定向回归包含新增 Gitee 正向 submit；按用户要求保留新 main 基线功能分支，不操作桌面、C SDK 或 ESP32。无已知未修问题；真实发布/硬件启动验收不在本次验证范围。

## 合入前复审与本地 CI（2026-11-18）

用户追加授权审查、中文 PR 和 CI 后正常合入 main；替代上一阶段“保留分支、不合入”的交付状态。第二轮独立审查无待修问题，本地 18 项 CI 全部通过（全仓 2538 passed / 28 skipped），生产代码无追加修改。命令、环境、构建安装证据与远端验收边界见 [复审与本地 CI](ci-review.md)。本次仅补充真实验证档案，不适用 TDD；通过 diff 和链接检查验证记录。
