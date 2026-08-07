# WatcheRobot CLI 命令参考

本文档覆盖已安装的 `watcherobot` 命令。可使用
`watcherobot <分组> <命令> --help` 查看当前安装版本的参数。

Application 从创建到发布的完整流程见
[SDK Application 使用与测试指南](application-marketplace/sdk-application-usage.zh-CN.md)；
Desktop 使用的 JSONL 事件和错误合同见
[Application 分发合同](application-marketplace/distribution-contract.md)。

## 命令总览

```text
watcherobot
├─ daemon      start | status | stop
├─ app         init | check | run | run-installed | start | stop
│              login | logout | publish | submit | marketplace
│              download | install | list | uninstall
└─ bluetooth   scan | provision | status | clear
```

## 输出与边界

- 默认输出面向终端使用者；Application 分发命令支持 `--jsonl`，供 Desktop 或自动化调用，stdout 只输出 JSON Lines。
- `app run`、`run-installed`、`start`、`stop` 会按需启动或复用 Runtime；分发命令和蓝牙命令不会启动 Runtime。
- Runtime 独占配对与设备连接，Application 不应另开 Discovery socket 或设备 WebSocket。
- `app install`、`list`、`uninstall` 只操作显式指定的 SDK App Store；生产环境由 Desktop 传入其受管目录和锁定 Runtime。

## Runtime 命令

### `watcherobot daemon start`

启动当前用户的 Runtime；若已经健康运行则复用。输出进程 ID 与本地 `control_url`。该命令不会自动配对设备或启动 Application。

```powershell
watcherobot daemon start
```

### `watcherobot daemon status`

查询当前用户 Runtime 是否存活；运行时输出进程 ID、控制地址与状态。Runtime 未运行时退出码为 `1`，可用于脚本探测。

```powershell
watcherobot daemon status
```

### `watcherobot daemon stop`

请求停止当前用户的 Runtime，同时停止其中运行的受管 Application。若 Watcher Desktop 正在使用该 Runtime，应在 Desktop 中停止 Application 或退出 Desktop，不要从 CLI 强制停止。

```powershell
watcherobot daemon stop
```

## Application 命令

所有 Application 都使用唯一的 `app.json` Manifest 和固定 `app.py` 入口。`app run` 接收源码目录用于开发，并不等同于直接执行 `app.py`。

### 创建、校验与运行

#### `watcherobot app init <目录>`

创建一个可发布的 Application 项目。交互式终端会询问 ID、显示名称、作者和简介；脚本环境必须显式提供四个字段。目标目录不能已经存在。

```powershell
watcherobot app init .\my_app `
  --id com.example.my_app `
  --name "My App" `
  --author "Example Team" `
  --description "An example WatcheRobot Application"
```

会生成 `app.json`、`app.py`、`README.md`、`icon.svg` 和 `.gitignore`。

#### `watcherobot app check <目录>`

校验 Manifest、固定入口、SDK 兼容性、标准 Python 依赖、图标路径和可发布源码集；不启动 Runtime，也不修改本地或远端状态。

```powershell
watcherobot app check .\my_app
watcherobot app check .\my_app --jsonl
```

#### `watcherobot app run <目录>`

启动或复用当前用户 Runtime，选择给定的本地源码 Application，并以 Runtime 注入的 `WATCHER_APP_*` 环境变量启动它。Application 退出后 Runtime 会继续运行。

```powershell
watcherobot app run .\examples\hello_robot
```

#### `watcherobot app run-installed --store-root <路径> --app-id <ID>`

运行已安装在自定义 SDK App Store 中的 Application。这是 SDK 开发与验收路径：它使用该 Store 启动或复用隔离的 Runtime 和临时端口，不会复用或修改 Desktop Runtime。

```powershell
watcherobot app run-installed `
  --store-root .\staging\app-store `
  --app-id com.example.my_app
```

#### `watcherobot app start` 与 `watcherobot app stop`

`start` 启动当前 Runtime 已选中的 Application；`stop` 停止该 Application 但保留 Runtime。两者都不会选择新的 Application，并会按需启动或复用当前用户 Runtime。

```powershell
watcherobot app start
watcherobot app stop
```

### 登录、发布与提交

#### `watcherobot app login [--status | --force]`

通过 Watcher Desktop 的公开 OAuth Device Flow 登录 Hugging Face。默认输出授权地址和用户码；`--status` 只检查已保存身份；`--force` 强制发起新的登录。Token 只保存在 Watcher 专用的操作系统凭据项中。

```powershell
watcherobot app login
watcherobot app login --status
watcherobot app login --force
```

#### `watcherobot app logout`

只删除 Watcher 保存的 Hugging Face 凭据，不会影响 Hugging Face CLI 或其他程序的登录。

```powershell
watcherobot app logout
```

#### `watcherobot app publish <目录>`

校验本地项目后，将精确源码快照发布到公开的 `<用户名>/WatcherRobot-<app_id>` Hugging Face Space。返回不可变 source commit；不会写入官方 Marketplace，也不启动 Runtime。

```powershell
watcherobot app publish .\my_app
```

#### `watcherobot app submit <目录> [--commit <sha>]`

校验已发布的不可变快照，并创建或复用官方 Marketplace Pull Request。`author` 和 `description` 不能为空；省略 `--commit` 时提交当前 Space HEAD，提供 40 位 commit 时只审核该版本。该命令不会上传源码。

```powershell
watcherobot app submit .\my_app
watcherobot app submit .\my_app --commit <40 位 commit>
```

#### `watcherobot app marketplace [--details | --jsonl]`

读取并校验公开的已审核 Marketplace。默认是紧凑兼容性表；`--details` 展示完整 Manifest、源码 URL、commit 和依赖；`--jsonl` 是机器调用格式。无需登录，不启动 Runtime，也不写本地缓存。

```powershell
watcherobot app marketplace
watcherobot app marketplace --details
```

### 下载与管理已审核 Application

#### `watcherobot app download --space-id <ID> --commit <sha> --target <空目录>`

将一个已审核的不可变 Space 版本下载到已存在的空 staging 目录。交付前会校验 commit、源码限制、Manifest、固定入口、SDK 兼容性和 Space/Application 身份；不会创建目标目录、安装 Application 或写入 `install.json`。

```powershell
watcherobot app download `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40 位 commit> `
  --target .\staging\app
```

#### `watcherobot app install --space-id <ID> --commit <sha> --store-root <路径> --runtime-root <路径>`

下载并校验已审核不可变版本，必要时复制传入的锁定 Runtime，为 Application 创建隔离 Python 环境，并原子写入安装记录。不会启动或连接 Runtime。

```powershell
watcherobot app install `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40 位 commit> `
  --store-root <app-store-directory> `
  --runtime-root <locked-app-runtime-directory>
```

#### `watcherobot app list --store-root <路径>`

列出一个 SDK App Store 中的安装记录。只读取本地记录，不查询 Marketplace，也不启动 Runtime。

```powershell
watcherobot app list --store-root <app-store-directory>
```

#### `watcherobot app uninstall --store-root <路径> --app-id <ID>`

将一个已安装 Application 移动到可恢复的本地回收目录；不会删除 Marketplace 源码，也不启动 Runtime。请先停止正在运行的 Application。

```powershell
watcherobot app uninstall `
  --store-root <app-store-directory> `
  --app-id <application-id>
```

## 蓝牙 Wi-Fi 配网命令

这些命令使用设备现有的 `ESP_ROBOT` BLE GATT 服务，支持 Windows 和 macOS 上的 Python 3.10–3.12。配网命令会交互式读取密码，绝不接受把密码作为命令行参数传入。

### `watcherobot bluetooth scan`

扫描兼容蓝牙设备并输出其 ID，后续蓝牙命令使用该 ID。

```powershell
watcherobot bluetooth scan
```

### `watcherobot bluetooth provision --device <ID> --ssid <名称> [--clear-existing]`

提示输入 Wi-Fi 密码，并把凭据发送给所选设备。`--clear-existing` 会先请求设备清除已有凭据。`credentials_saved` 只表示固件确认保存，不代表设备已经成功连上网络。

```powershell
watcherobot bluetooth provision --device <id> --ssid MyWiFi
```

### `watcherobot bluetooth status --device <ID>`

读取设备当前报告的 Wi-Fi 配网状态。

```powershell
watcherobot bluetooth status --device <id>
```

### `watcherobot bluetooth clear --device <ID>`

请求清除指定设备保存的 Wi-Fi 凭据，并输出处理后的状态。

```powershell
watcherobot bluetooth clear --device <id>
```

BLE 协议、超时、清理行为和安全边界见[蓝牙配网](bluetooth-provisioning.md)。

## `watcher-distribution` 边车命令

Desktop 打包环境可使用 `watcher-distribution app` 执行短生命周期分发操作。它支持且仅支持：`check`、`login`、`logout`、`publish`、`submit`、`marketplace`、`download`、`install`、`list`、`uninstall`。

这些命令与上方同名 `watcherobot app` 命令具有相同语法、参数、行为和 JSONL 输出；它不能执行 `init`、`run`、`run-installed`、`start`、`stop`，并且绝不会导入或启动 Runtime。

```powershell
watcher-distribution app check .\my_app --jsonl
watcher-distribution app marketplace --jsonl
```
