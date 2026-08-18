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
├─ robot       setup | pair | status
├─ app         init | check | run | run-installed | start | stop
│              login | logout | publish | submit | marketplace
│              download | install | list | uninstall
└─ bluetooth   scan | provision | status | clear
```

使用 `watcherobot --version` 可直接查看已安装的 SDK 版本，不会启动 Runtime，也不读取项目。

## 输出与边界

- 默认输出面向终端使用者；Application 分发命令支持 `--jsonl`，供 Desktop 或自动化调用，stdout 只输出 JSON Lines。
- `app run`、`run-installed`、`start`、`stop` 和 `robot pair` 会按需启动或复用 Runtime；分发命令和蓝牙命令不会启动 Runtime。
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

## 机器人新手引导命令

这是连接硬件的常规用户入口。底层 `bluetooth` 命令只用于配网排障或定制自动化。

### `watcherobot robot setup [--device <ID>] [--ssid <名称>] [--pairing-code <配对码>] [--clear-existing]`

完成首次连接的完整引导。命令先提示用户打开电脑蓝牙，并在机器人上打开
**Settings > Wi-Fi**，确认页面已经打开后才开始扫描。扫描结果显示机器人广播的稳定 **Device ID**；附近有
多台机器人时，使用 **Up/Down** 和回车键按 Device ID 选择，不以设备名作为配网
身份。旧固件未广播 Device ID 时会明确标记为不可用，仅把 Bluetooth ID 作为兼容
信息展示。`--device` 接受 Device ID，同时继续兼容旧固件的 Bluetooth ID。扫描最长约 10 秒，交互式
终端会持续输出进度点，并在结束后显示发现的机器人数量，避免等待期间看起来像命令卡死。

引导流程会区分可恢复状态，不直接向普通用户输出扫描日志：

| 场景 | 命令说明 | 用户下一步 |
|---|---|---|
| 电脑蓝牙关闭、不可用或没有适配器 | 当前电脑无法使用蓝牙 | 打开蓝牙或检查适配器，再重新运行 setup |
| 适配器或系统不支持 BLE Central 模式 | 当前电脑不具备扫描并连接机器人的蓝牙角色 | 更换支持 BLE Central 的适配器，并确认系统支持 BLE 扫描 |
| 系统拒绝蓝牙权限 | 当前终端或 Python 没有蓝牙访问权限 | 在系统隐私设置中授权，再重新运行 setup |
| 未发现机器人 | 没有发现配网广播 | 保持 **Settings > Wi-Fi** 页面打开并靠近电脑；已联网机器人改用 `robot pair` |
| 发现一台或多台新版机器人 | 展示稳定 Device ID | 用 **Up/Down** 选择机器人屏幕上可核对的 Device ID |
| 旧固件没有广播 Device ID | 明确提示 Device ID 不可用，可能需要升级固件 | Bluetooth ID 仅作为兼容信息保留 |
| 蓝牙连接或响应超时 | 蓝牙通信没有完成 | 保持机器人靠近、关闭可能占用连接的应用，然后重试 |
| 机器人拒绝 Wi-Fi 配置 | 网络名称或密码没有被接受 | 检查 Wi-Fi 名称和密码后重试 |
| Wi-Fi 认证失败 | 固件在当前 BLE 会话上报 `auth_failed` | 检查密码后重新运行 setup |
| 找不到兼容网络 | 固件上报 `network_not_found` | 检查网络名称、距离和安全模式 |
| Wi-Fi 验证超时 | 固件上报 `timeout`，或 SDK 的有界等待到期 | 靠近路由器，检查凭据后重试 |
| 固件返回不兼容响应 | 机器人与 SDK 的配网协议不匹配 | 升级固件和 SDK；持续出现时反馈两端版本 |
| Runtime 配对失败 | 六位码配对阶段没有完成 | 保持 **"Python SDK"** 应用打开、确认处于同一网络并输入最新配对码 |
| 用户取消 | 明确显示 setup 已取消 | 不打印 Wi-Fi 密码或配对码 |

`robot setup` 是面向人的引导命令，默认错误采用可执行的普通文本。需要紧凑 JSON 的自动化
应使用底层 `watcherobot bluetooth ...` 命令，其结构化输出合同保持不变。

交互式终端会用颜色辅助区分状态：蓝色表示进行中，绿色表示成功，黄色表示需要确认，红色表示失败，青色
突出 Device ID。所有状态同时保留文字和退出码，颜色不是唯一信息来源。输出重定向或非交互执行时自动
关闭颜色；设置 `NO_COLOR=1` 可显式禁用，`FORCE_COLOR=1` 可用于支持 ANSI 的特殊终端。Windows
PowerShell 的控制台兼容由 SDK 自动处理。

随后命令私密读取 Wi-Fi 密码并写入网络凭据，同时保持 BLE 会话，实时接收固件上报的
`connecting`、`connected`、`auth_failed`、`network_not_found` 或 `timeout`。只有收到
`connected` 才会进入配对。配网后回到机器人启动器，打开
**"Python SDK"** 应用，读取屏幕顶部的六位配对码，并继续在同一个 setup 流程中
输入。交互式终端会询问省略的字段；密码永远不能作为命令行参数传入，也不会打印。

流程不再要求用户观察屏幕后手工按回车确认。固件负责限定连接尝试时长并通过 BLE 返回终态；SDK 还设置了
略长的主机侧截止时间，避免终态通知丢失后命令无限等待。认证失败、找不到网络或超时都会在 Runtime 配对前
停止，并给出对应恢复步骤，且不会打印密码。

```powershell
watcherobot robot setup
```

自动化场景可显式提供非敏感字段，密码仍然交互读取：

```powershell
watcherobot robot setup `
  --device <Device ID> `
  --ssid MyWiFi `
  --pairing-code 123456
```

### `watcherobot robot pair <六位配对码>`

用于机器人已经接入同一网络的情况。先在机器人上打开 **"Python SDK"** 应用并读取
当前配对码；命令会启动或复用 Runtime，以 `python_sdk` 模式发起配对，等待设备
连接，并把常见发现或连接失败转换为用户可理解的提示。

```powershell
watcherobot robot pair 123456
```

### `watcherobot robot status`

查询 Runtime 实际持有的机器人连接。在线时退出码为 `0`，未连接时为 `1`；Runtime
未启动也会明确显示机器人未连接，并给出下一条 `robot setup` 命令。

```powershell
watcherobot robot status
```

## Application 命令

所有 Application 都使用唯一的 `app.json` Manifest 和固定 `app.py` 入口。`app run` 接收源码目录用于开发，并不等同于直接执行 `app.py`。

### 创建、校验与运行

#### `watcherobot app init [目录]`

创建一个可直接运行的 Hello World Application，且不会覆盖已有目标。省略目录时，交互式终端只会询问项目目录；
ID、显示名称、作者和简介都会自动生成，不要求开发者手工填写。例如 `my_app` 默认得到稳定、可读的
`local.my_app`。准备发布时再通过参数覆盖正式元数据。

```powershell
watcherobot app init my_app

watcherobot app init published_app `
  --id com.example.my_app `
  --name "My App" `
  --author "Example Team" `
  --description "An example WatcheRobot Application"
```

如果提供了目录后仍然出现 `Application ID:`、`Application name:` 等逐项提问，说明当前终端调用的是旧版
CLI。先激活安装源码的虚拟环境，再在 Windows 使用 `where.exe watcherobot`，或在 macOS/Linux 使用
`command -v watcherobot` 确认命令来源。Application ID 是升级与覆盖
安装的稳定身份，不使用用户名、时间戳或随机数自动拼接；这些值会泄露本机信息或导致同一项目每次初始化都
变成不同应用。正式发布应使用团队持有的稳定命名空间，例如 `com.example.my_app`。

会生成 `app.json`、`app.py`、`README.md`、`icon.svg` 和 `.gitignore`；默认
`app.py` 一定会输出 Hello World 成功日志。连接兼容机器人后，它只播放一次 `happy`
行为，等待行为真实播放完成后正常退出，不包含随机轮播、灯光或待机逻辑。

#### `watcherobot app check <目录>`

校验 Manifest、固定入口、SDK 兼容性、标准 Python 依赖、图标路径和可发布源码集；不启动 Runtime，也不修改本地或远端状态。

```powershell
watcherobot app check .\my_app
watcherobot app check .\my_app --jsonl
```

#### `watcherobot app run [目录]`

启动或复用当前用户 Runtime，选择本地源码 Application，并以 Runtime 注入的
`WATCHER_APP_*` 环境变量启动它。目录默认是当前工作目录；Application 退出后
Runtime 会继续运行。面向用户的输出会用绿色勾号确认真实的 Runtime、机器人连接和
Application 运行状态；离线时不会误报机器人已连接。没有连接机器人时，CLI 会同时打印首次连接使用的
`watcherobot robot setup` 和已联网设备使用的 `watcherobot robot pair <code>`，
然后继续运行，避免阻断离线 Application。

复用 Runtime 前，CLI 会校验后台的控制协议和 SDK 版本；后台仍是旧版 SDK-owned
Daemon 时会自动重启为当前环境版本，避免新清单被旧 Daemon 拒绝。

```powershell
cd my_app
watcherobot app run
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

## 高级蓝牙 Wi-Fi 配网命令

常规开发者应使用 `watcherobot robot setup`。以下底层命令使用设备现有的
`ESP_ROBOT` BLE GATT 服务，支持 Windows 和 macOS 上的 Python 3.10–3.12。
配网命令会交互式读取密码，绝不接受把密码作为命令行参数传入。

### `watcherobot bluetooth scan`

扫描兼容蓝牙设备并输出其 ID，后续蓝牙命令使用该 ID。

```powershell
watcherobot bluetooth scan
```

### `watcherobot bluetooth provision --device <ID> --ssid <名称> [--clear-existing]`

提示输入 Wi-Fi 密码，并把凭据发送给所选设备。`--clear-existing` 会先请求设备清除已有凭据。只有固件上报 Wi-Fi 已连接才返回 `connected`；认证失败、找不到网络或超时会直接返回错误。

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
