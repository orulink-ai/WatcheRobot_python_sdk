# {name}

{description}

Application ID：`{app_id}`；作者：{author}。

## 工程目录

```text
my_app/
├── app.json       # Application 元数据、版本和依赖
├── app.py         # 固定程序入口
├── README.md      # Application 自己的说明
├── icon.svg       # 应用图标
└── .gitignore
```

这是基础应用模板。编辑 `app.py` 实现自己的应用；`app.json` 管理身份、版本、依赖及支持的平台。
`ApplicationContext` 提供 `app.robot`、`app.desktop` 和 `app.logger`，退出上下文时释放资源。
默认入口只启动受管应用并等待停止，不调用设备业务功能。

## 运行与开发

在本项目目录执行。设备已联网时，把 123456 替换为屏幕当前六位配对码：

```powershell
watcherobot robot pair 123456
watcherobot robot status
watcherobot-app-lock .
watcherobot app check .
watcherobot app run
```

首次配网使用 `watcherobot robot setup`。不使用设备的应用可以直接运行。
使用 `watcherobot app run` 启动，由 Runtime 注入应用身份和通道，不要直接运行 `python app.py`。
修改代码后停止再运行；Ctrl+C 停止，也可在另一个终端执行 `watcherobot app stop`。
需要分享时，完善元数据后使用现有的 `watcherobot app publish .` 和 `watcherobot app submit` 流程。

应用清单使用 schema 3：`requires_sdk` 约束应用自身环境的 SDK，`requires_daemon.application_protocol` 约束共享 Daemon 的连接合同；两者分别检查。SDK CLI 显式启动的接管规则仍然保留。桌面运行已安装应用时只请求 Daemon 启动固定入口，不另起应用环境中的 SDK CLI。

先在应用独立 Python 环境安装并测试所需依赖，再运行 `watcherobot-app-lock .` 生成 `app.lock.json`；把锁文件一并提交。增加或升级依赖后重新验证和冻结。没有锁文件、实际环境与精确锁不一致时会阻止安装或启动；不会静默改用桌面当前 SDK。
