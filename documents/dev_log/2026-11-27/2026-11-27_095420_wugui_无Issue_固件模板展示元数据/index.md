# 固件模板与桌面展示元数据

2026-11-27 Asia/Shanghai，建档 wugui，开发 Codex。SDK 与桌面分仓实现。验收覆盖固件工程模板、包准备与发布目录、桌面卡片及详情显示、工作区源码联调。

## 实现与验证

2026-11-27，Codex。基于 origin/main 的 9d9196b9 创建 codex/firmware-template-metadata，与桌面分支 codex/marketplace-discovery-dev-20261127 联调。

固件模板复用应用模板图标，在 firmware.project.json 预留 chip_model。准备、打包和发布目录读写展示字段，烧录目标与分区继续从构建产物读取。图标路径限制在工程内，嵌入图标校验格式和大小。旧工程可缺省新字段。正式收录携带新字段的目录记录前需升级旧读取端，兼容范围见 docs/application-marketplace/firmware-implementation.zh-CN.md。

先用工程生成、包准备与目录往返用例复现字段缺失，再实现并回归，SDK 相关测试 186 项通过、10 项跳过。使用桌面开发环境的 CLI 实际生成工程，核实默认版本、图标和空芯片型号。用模拟构建产物打包，桌面预览确实加载该图标，并显示填写的芯片型号。两家提供方的发布读取采用模拟服务，未对公网发布或对设备烧录。

## 2026-11-27 作者自制固件字段复核

Codex。更正上一条关于硬件料号的解释。作者为 WatcheRobot 编写设备端固件，模板改用可空的 inference_model 记录作者声明的片上推理模型。打包模拟示例得到模型 gesture-recognition-v2、独立的目标 esp32s3 和一项分区。已核对产品 Wiki 的嵌入式 C SDK 和烧录说明，字段归属见 docs/application-marketplace/firmware-implementation.zh-CN.md。红测复现新字段被拒，相关 SDK 测试复跑 148 项通过、1 项跳过。

## 提交验收

2026-11-27，Codex。用户确认 inference_model 指芯片上运行的推理模型，由作者选填，可留空。与桌面端 codex/marketplace-discovery-dev-20261127 配套交付。已核对待提交差异，重跑 pytest -o addopts= -q tests/application/test_development_projects.py tests/distribution -k "firmware or development_projects"，287 项通过、10 项跳过。正式发布前需升级读取共享目录的 SDK 与桌面端。
