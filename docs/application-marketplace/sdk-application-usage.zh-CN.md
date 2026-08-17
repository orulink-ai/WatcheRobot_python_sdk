# WatcheRobot SDK Application 开发与发布测试指南

本文面向使用当前 SDK 开发、运行和发布 Python Application 的开发者，也可作为
`codex/application-marketplace` 当前版本的手工验收步骤。SDK 包版本以
`watcherobot.__version__` 为唯一真值，本文不重复维护具体版本号。

最短链路是：

```text
本地 app.json + app.py
  -> SDK check
  -> SDK Daemon 托管运行
  -> Hugging Face Device Flow 登录
  -> 发布源码到开发者公开 Space
  -> 选择固定 commit 提交官方名单审核
  -> 维护者合并
  -> SDK 从官方固定 commit 安装到独立环境
```

## 1. 先理解三个边界

1. `watcherobot app run` 会启动或复用 SDK Daemon，由 Daemon 注入 Desktop channel 和
   Device channel；不要直接运行 `app.py`，Application 也不要自行连接设备 WebSocket。
2. `check/login/logout/publish/submit/marketplace/download/install/list/uninstall` 是 SDK 分发能力，不启动 Daemon。
   开发环境使用 `watcherobot app ...`；Desktop 发布包使用同一实现生成的受控
   `watcher-distribution` sidecar。
3. `run-installed --store-root ... --app-id ...` 用于 SDK 开发和验收时运行自定义 App Store
   中已安装的应用。它使用应用自己的 `.venv`，启动独立且使用随机端口的 Daemon，不接管
   Desktop 的 Daemon 或 Application Store。
3. SDK 负责下载、安装、已安装列表和卸载。`install` 使用 Desktop 提供的锁定 Runtime，
   创建每 App 独立环境并原子写入本地安装记录；选择、启动和停止当前 Application 仍属于
   Daemon 管理操作。

## 2. Windows 测试环境

在 SDK 仓库根目录执行。直接调用虚拟环境中的可执行文件，可以避免 PowerShell 执行策略和
其他 Python 环境污染。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -c "import watcherobot; print(watcherobot.__version__)"
.\.venv\Scripts\watcherobot.exe app --help
```

版本输出应与当前 checkout 的 `watcherobot.__version__` 一致。如果切换了 SDK 分支，请重新执行
editable 安装，保证控制台入口与当前源码一致。

## 3. 初始化 Application

在终端中使用 SDK 初始化命令：

```powershell
.\.venv\Scripts\watcherobot.exe app init .\my_sdk_test
```

本地开发只需要提供目录；初始化器会生成本地 ID、显示名称、作者和简短说明。验收发布流程时可通过参数覆盖：

```powershell
.\.venv\Scripts\watcherobot.exe app init .\my_sdk_test `
  --id com.example.sdk_test `
  --name "SDK Test" `
  --author "Your team" `
  --description "Verify the current SDK Application flow"
```

目标路径必须不存在。初始化不会启动 Daemon，也不会访问 Hugging Face；默认 `app.py` 会播放一次 `happy` 行为并正常退出。生成内容为：

```text
my_sdk_test/
├─ app.json
├─ app.py
├─ README.md
├─ icon.svg
└─ .gitignore
```

生成后可以直接执行 `app run`；初始版本为 `0.1.0`，`requires_watcherobot` 根据当前
SDK 自动计算。发布前应修改正式唯一 ID，并补齐应用广场信息。

可发布的 `app.json`：

```json
{
  "schema_version": 2,
  "id": "com.example.sdk_test",
  "name": "SDK Test",
  "version": "0.1.0",
  "requires_watcherobot": ">=0.1.0a4,<0.2",
  "dependencies": [],
  "supported_host_platforms": ["windows", "macos"],
  "description": "Verify the current SDK Application flow",
  "author": "Your Hugging Face username",
  "icon": "icon.svg"
}
```

字段规则：

- 必填：`schema_version`、`id`、`name`、`version`、`requires_watcherobot`、`dependencies`、`supported_host_platforms`。
- `description`、`author` 在本地校验、运行和源码发布时仍可选，但执行 `app submit`
  前两项必须为非空；`icon` 在全部阶段均可选。不接受未知字段。
- `id` 长度为 1～64，只能使用小写字母、数字、点、下划线和连字符。
- `version` 使用三段语义版本；`requires_watcherobot` 必须覆盖当前 SDK 版本。
- `dependencies` 使用标准 Python requirement 字符串，例如 `requests>=2.32,<3`。不要通过
  URL 或本地路径替换 `watcherobot`；Desktop 会安装与 Daemon 同版本的随包 SDK wheel。
- `icon` 必须是 Application 根目录内真实存在的普通文件。
- 应用广场上架必须使用 `schema_version: 2`。`supported_host_platforms` 只能填写
  `windows` 和/或 `macos`，且必须基于实际验证结果，不能因为使用 Python 就推断 macOS 可用。

最小 `app.py`：

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("app_id=%s", app.app_id)
        app.logger.info("device=%s", app.robot.device_info)
        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

常用入口：

- `app.robot`：行为、运动、灯光、相机、麦克风和媒体等设备领域 API。
- `app.desktop`：可选的 Desktop 业务消息通道。
- `app.logger`：由 Daemon 捕获、实时转发并持久化的 Application 日志。

## 4. 第一阶段：无公开写入的本地测试

### 4.1 校验项目

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test
```

成功时普通输出包含英文 Manifest 摘要，退出码为 `0`。`check` 不启动 Daemon，也不访问
Hugging Face。Desktop 的机器调用形式为：

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test --jsonl
```

JSONL 最后一行是 `type=result`、`ok=true`。

### 4.2 连接设备

`app run` 会自动启动或复用当前 Runtime。如果设备已经配对并在线，可直接运行 Application。全新独立会话的配对步骤见
仓库根目录 `README.zh-CN.md`；不要让 Application 自己执行 Discovery 或连接设备。

### 4.3 运行 Application

```powershell
.\.venv\Scripts\watcherobot.exe app run .\my_sdk_test
```

预期结果：

- Daemon 选择并启动该目录的 `app.py`；
- 终端能看到 `app.logger` 日志；
- 机器人执行测试行为；
- Application 正常结束时命令退出码为 `0`；
- 按 `Ctrl+C` 会请求停止 Application，退出码为 `130`；
- Application 停止后 Daemon 和设备连接继续存在。

完成独立测试后可停止本次 CLI Daemon：

```powershell
.\.venv\Scripts\watcherobot.exe daemon stop
```

如果当前 Daemon 由 Watcher Desktop 管理，不要使用上述命令终止它；应从 Desktop 退出当前
Application。

## 5. 第二阶段：Hugging Face 登录测试

浏览器中的 Hugging Face 登录不等于 SDK 分发登录。SDK 使用 Watcher Desktop Public OAuth
App 的 Device Flow，并把 Token 只保存到 Watcher 专用系统凭据项。

先用普通模式查询状态：

```powershell
.\.venv\Scripts\watcherobot.exe app login --status
```

第一次使用时执行面向人的交互命令，不要添加 `--jsonl`：

```powershell
.\.venv\Scripts\watcherobot.exe app login
```

终端会明确显示英文提示，例如：

```text
Authorize Hugging Face in your browser
Open: https://hf.co/oauth/device
Enter code: ABCD-EFGH
Code expires in: 300 seconds
```

1. 打开终端显示的 Hugging Face 设备码页面。
2. 输入终端显示的验证码，并在浏览器同意授权。
3. 保持命令运行，等待终端显示登录成功和当前用户名。
4. 不要复制、保存或发送 Access Token；CLI 也不会输出 Token。

Desktop 或其他机器调用方才使用 `--jsonl`：

```powershell
.\.venv\Scripts\watcherobot.exe app login --status --jsonl
.\.venv\Scripts\watcherobot.exe app login --jsonl
```

机器调用方从 `progress.data.verification_uri`、`progress.data.user_code` 和
`progress.data.expires_in` 读取授权信息，并负责在自己的 UI 中清晰展示。未登录状态也是正常
成功结果：

```json
{"type":"result","ok":true,"data":{"logged_in":false}}
```

已有凭据仍需重新授权时使用 `--force`。只清理 Watcher 专用凭据时执行：

```powershell
.\.venv\Scripts\watcherobot.exe app logout
```

该命令不会退出浏览器，也不会删除 Hugging Face CLI 或其他程序保存的登录。

## 6. 第三阶段：源码发布与名单提交测试

源码发布和应用广场审核拆成两个操作：`publish` 只创建或更新开发者公开 Space；`submit`
只选择已经发布的固定 commit 并创建官方名单 PR。请先确认 `app.json.id`、源码和依赖都
允许公开。

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test
.\.venv\Scripts\watcherobot.exe app publish .\my_sdk_test
```

发布规则：

- Space 固定命名为 `<hf_username>/WatcherRobot-<app_id>`，类型为公开 static Space。
- 上传的是校验后的完整源码集合；`.git`、`.venv`、缓存、凭据和 `.watcherignore` 命中的内容
  不会发布。
- 成功结果只包含 `space_id`、完整 40 位 `commit` 和固定源码 URL，不包含 Catalog 或 PR 状态。
- 同一源码重复发布不会制造无意义的新 commit。
- `publish` 不读取或修改官方 Catalog。

源码确认无误后，再单独提交审核：

```powershell
.\.venv\Scripts\watcherobot.exe app submit .\my_sdk_test
```

默认提交 Space 当前 HEAD。如需精确提交 `publish` 返回的版本：

```powershell
.\.venv\Scripts\watcherobot.exe app submit .\my_sdk_test `
  --commit <40-character-commit>
```

提交规则：

- `description`、`author` 必须为非空；`icon` 可选。填写时图标必须同时存在于本地项目和固定
  commit；省略或留空时，由展示端使用默认 WatcherRobot Application 图标。
- 固定 commit 上的 `app.json` 必须与本地项目一致；不一致时应重新发布当前项目，或者检出与
  该 commit 匹配的源码后再提交。
- `submit` 只读取固定源码并修改官方 Catalog PR，绝不上传或改写 Application 源码。
- 成功结果包含 `space_id`、`commit`、固定 `source_url`、`pr_status` 和可选 `pr_url`。
- 同一固定 commit 的开放 PR 会复用。
- 同一 App 已有不同 commit 的开放 PR 时返回 `catalog_pr_conflict`；先处理原 PR，再提交新版本。
- 新建的名单 PR 会直接展示 App 名称、ID、版本、作者、简介、SDK 要求、依赖和固定源码链接；
  用户提供自选图标时额外展示图标路径和固定版本图标，否则标记使用默认图标。Catalog 文件
  本身仍只保存 `space_id + commit`。
- 普通开发者不能直接修改官方名单。只有维护者合并 PR 后，App 才会出现在正式应用广场。

## 7. 第四阶段：正式名单与固定快照测试

读取正式名单不要求登录：

```powershell
.\.venv\Scripts\watcherobot.exe app marketplace
.\.venv\Scripts\watcherobot.exe app marketplace --details
```

默认输出是紧凑兼容性表格，`--details` 会显示完整固定源码 URL、commit、SDK 要求、依赖、
作者和说明。每个 Application 都有固定 `space_id + commit`、
结构化 `app.json` 字段、`source_url` 和 `compatible`。不要把 Space `main` 当作安装版本。

Desktop 使用机器形式：

```powershell
.\.venv\Scripts\watcherobot.exe app marketplace --jsonl
```

当前公开测试锚点为：

```text
space_id = tianguiti/WatcherRobot-com.orulink.marketplace_smoke
commit   = 18c3966e898d6ca84b1868663d1b5b591f9f7606
```

把该固定快照下载到新建的空 staging：

```powershell
$staging = Join-Path ([IO.Path]::GetTempPath()) ("watcher-sdk-download-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $staging | Out-Null
.\.venv\Scripts\watcherobot.exe app download `
  --space-id tianguiti/WatcherRobot-com.orulink.marketplace_smoke `
  --commit 18c3966e898d6ca84b1868663d1b5b591f9f7606 `
  --target $staging
Get-ChildItem -LiteralPath $staging
```

预期 `result.data.commit` 与请求值完全相同，目录中至少存在 `app.json` 和 `app.py`。目标必须是
调用者预先创建的现有空目录；失败时不能把候选内容发布为正式安装。
Desktop 在同一命令末尾添加 `--jsonl`，并只读取结构化事件字段。

将审核过的同一个固定 commit 安装到 SDK App Store 时，传入本地商店目录和 Desktop 随包的
锁定 Application Runtime。首次安装时 SDK 会校验并复制 Runtime，每个 App 都在商店中拥有
独立 `.venv`：

```powershell
.\.venv\Scripts\watcherobot.exe app install `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications `
  --runtime-root <Desktop-app-runtime-directory>

.\.venv\Scripts\watcherobot.exe app list `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications

.\.venv\Scripts\watcherobot.exe app uninstall `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications `
  --app-id <app_id>
```

这三个命令不会启动或连接 Daemon；后续 Desktop 只负责调用 SDK、展示进度，以及管理 Daemon
对当前 Application 的选择、启动和停止。

若使用的是自定义 App Store（开发或验收测试），可从同一个商店运行已安装应用：

```powershell
.\.venv\Scripts\watcherobot.exe app run-installed `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications `
  --app-id <app_id>
```

该命令读取 SDK 的安装记录，使用应用自己的 `.venv`，并启动或复用绑定该商店的独立
Daemon。它使用随机端口，不连接、不修改 Desktop Daemon；Desktop 的产品商店仍由
Desktop 自己进行选择和启动。

确认不再需要后，可只删除本次创建的随机临时目录：

```powershell
$resolvedStaging = (Resolve-Path -LiteralPath $staging).Path
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
if (-not $resolvedStaging.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to remove a directory outside the system temp root"
}
Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
```

## 8. 错误判断

使用 `--jsonl` 时只按 `type`、`code` 和进程退出码判断，不匹配中英文 `message`。

| 退出码 | 类别 | 常见原因 |
| --- | --- | --- |
| `0` | 成功 | 最后一行是 `result` |
| `2` | 本地校验失败 | Manifest、入口、依赖、兼容范围或文件集合不合法 |
| `3` | 登录失败 | 未登录、授权拒绝/过期或系统凭据错误 |
| `4` | 远端失败 | 网络、Space、固定 commit、官方名单或 PR 冲突 |
| `5` | 内部失败 | 未归类的 SDK 内部错误 |
| `130` | 取消 | 用户取消登录、发布、下载或运行 |

常见稳定错误码：

- `app_manifest_missing` / `app_entrypoint_missing`：缺少 `app.json` / `app.py`。
- `app_manifest_invalid`：字段、ID、版本或图标不合法。
- `app_sdk_incompatible`：`requires_watcherobot` 不覆盖当前 SDK。
- `app_dependency_invalid`：Python requirement 不合法或试图替换随包 `watcherobot`。
- `auth_required`：发布前尚未完成 SDK Device Flow 登录。
- `space_ownership_conflict`：同名 Space 不是本 OAuth App 创建，工具拒绝覆盖。
- `catalog_pr_conflict`：同一 App 已有不同 commit 的开放名单 PR。
- `remote_error`：Hugging Face 或网络返回不可用结果。

## 9. 建议测试顺序与回传记录

先完成无外部写入步骤，再决定是否发布：

1. 版本与 `app --help`。
2. `check` 并阅读 Manifest 摘要。
3. Daemon 配对与 `app run`。
4. `login --status --jsonl`，必要时 Device Flow 登录。
5. 确认允许公开后执行 `publish`。
6. 检查返回的固定 commit，再执行 `submit`。
7. 等维护者合并 PR 后执行 `marketplace` 和 `marketplace --details`。
8. 对名单中的完整 commit 执行 `download`。
9. 最后在 Watcher Desktop 中测试刷新、安装、启动、停止、日志、卸载和默认 App 回退。

测试反馈建议保留以下非敏感字段，Token、Device Code 和本机凭据不要回传：

```text
SDK version:
Python executable:
check result / exit code:
daemon status:
app run result / exit code:
HF username / login status:
publish space_id / commit / exit code:
submit commit / pr_url / exit code:
marketplace catalog_commit / target app:
download commit / exit code:
Desktop install / run / stop / log / uninstall:
```

机器合同、全部稳定错误码与 Desktop 集成边界见
[Application 分发合同](distribution-contract.md)；OAuth scope 与凭据边界见
[Hugging Face OAuth 实施合同](hugging-face-oauth.md)。全部命令和输出模式的简表见
[Application CLI Quick Reference](application-cli-reference.md)。
