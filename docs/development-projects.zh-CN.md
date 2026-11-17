# 开发项目：应用、固件与组合项目

这里的“应用”指电脑端 Python Application；固件里的应用镜像指设备程序，两者不同。

| 开发内容 | 运行方式 |
| --- | --- |
| 仅应用 | 沿用官方固件，调用 SDK 接口，通过 Daemon 注入的 Device channel 通信；无需自行建立设备连接。 |
| 仅固件 | 用 C SDK／设备工具链构建，Python SDK 准备包，桌面通过 USB 烧录。 |
| 应用＋固件 | 两个成员独立发布，应用用 `requires_firmware` 关联配套版本。自定义固件须兼容现有 SDK／Daemon 协议，否则由作者实现配套通信。 |

以下 `project` 命令仅生成、识别项目，不启动 Daemon。Application 运行时由 Daemon 托管。项目工具不下载 C SDK、不编译固件、不执行烧录。

```console
watcherobot project init "我的项目" --kind application
watcherobot project init "我的固件" --kind firmware
watcherobot project init "配套项目" --kind combined
watcherobot project add firmware "我的项目"
watcherobot project add application "我的固件"
watcherobot project inspect "配套项目" --jsonl
```

`init` 的目标必须不存在，包括空目录。`add` 的 DIRECTORY 是已有项目根目录；它只创建缺少的 application 或 firmware 成员，拒绝已有路径，不迁移或覆盖旧文件。命令成功返回 0，输入或文件操作失败返回 2。生成过程使用临时目录；失败清理本次创建的内容，已有清单更新失败会回滚新成员。项目内短暂出现 `.watcherobot-project.lock`，用于防止两个 SDK 写入者同时修改；进程被强制终止后，应先检查清单与成员状态，再处理遗留锁。

新项目根目录的 `watcherobot.project.json` 示例：

```json
{
  "schema_version": 1,
  "kind": "combined",
  "application_path": "application",
  "firmware_path": "firmware"
}
```

单成员项目只声明对应路径。路径使用相对于根目录的 POSIX 写法，拒绝绝对路径、`..`、符号链接和 Windows Junction。已有根目录 `app.json` 仍按 application 识别；为它添加固件时保留原文件，并用 `application_path: "."` 引用旧应用。原 `watcherobot app init` 行为保持。

应用成员复用现有 Application 模板，包含 `app.json`、`app.py`、图标和说明。运行、检查、打包等原应用命令请传入检查结果的 `application_directory`。固件成员只生成 `firmware.project.json` 与 README，不包含假应用：

```json
{
  "id": "local.demo.firmware",
  "name": "Demo Firmware",
  "version": "0.1.0",
  "description": "Demo device firmware project.",
  "build_path": "build"
}
```

固件 ID 和版本遵循既有固件发布合同。`build_path` 可省略，默认 `build`，相对于固件成员目录；尚未编译、目录不存在时仍是有效项目。构建后可使用 `watcherobot firmware prepare` 准备产物。项目描述不是可烧录包，也不代表固件已发布。

组合模板不自动填写 `requires_firmware`。实际固件发布后，再按既有 Application 配套合同填写真实 `firmware_id`、版本范围、推荐版本与测试版本；组合安装仅关联相同来源的固件版本。不要用占位版本冒充已发布依赖。

Python 公开接口：

```python
from pathlib import Path
from watcherobot.project import inspect_project, init_project, add_project_member

init_project(Path("demo"), kind="application")
add_project_member(Path("demo"), kind="firmware")
info = inspect_project(Path("demo"))  # dict，直接 JSON 序列化
```

`init_project`、`add_project_member` 返回 `DevelopmentProject`，其 `to_dict()` 与 `inspect_project` 的字典格式相同。检查结果与 CLI JSONL 每行一个对象，字段固定为：

| 字段 | 内容 |
| --- | --- |
| `kind` | application / firmware / combined |
| `project_root` | 项目根绝对路径 |
| `application_directory` | 应用成员绝对路径，或 null |
| `firmware_directory` | 固件成员绝对路径，或 null |
| `firmware_metadata` | 仅 id/name/version/description 展示字段，或 null |
| `firmware_build_directory` | 安全且已存在的构建目录绝对路径，或 null |

`build_path` 不混入展示 metadata，不会被作为固件包地址、哈希或 Flash 参数。越界或链接路径明确拒绝；检查过程中不连接设备或网络。
