# 安装 WatcheRobot Python SDK

本文把两种用途明确分开：

- **从源码安装**：用于验证尚未发布的 PR、分支或固定 commit，以及参与 SDK 开发。
- **正式安装**：用于普通 Application 开发者安装已经发布到 PyPI 的稳定版或预发布版。

两种方式都建议使用独立 Conda 环境，不要安装到 `base`。项目支持 Python
3.10–3.12，推荐使用 Python 3.11，以获得更稳定的第三方依赖兼容性。

## 方式一：从源码安装

### 1. 创建独立环境

```powershell
conda create -n watcherobot-source python=3.11 -y
conda activate watcherobot-source
python -m pip install --upgrade pip
```

环境名使用 `watcherobot-source`，避免和正式安装环境混用。

### 2. 获取目标源码

```powershell
git clone https://github.com/orulink-ai/WatcheRobot_python_sdk.git
cd WatcheRobot_python_sdk
git fetch origin
```

验证某个 PR 或分支时，切换到明确的远端分支：

```powershell
git switch --track origin/BRANCH_NAME
```

需要验收不可变化的代码快照时，优先固定到 commit：

```powershell
git switch --detach COMMIT_SHA
```

不要只执行 `git clone` 后就假设拿到了待验收改动；必须用 `git status` 和
`git log -1 --oneline` 确认当前分支或 commit。

### 3. 安装源码

只需要使用 SDK 和 CLI 时：

```powershell
python -m pip install -e .
```

需要修改 SDK、运行测试和类型检查时：

```powershell
python -m pip install -e ".[test]"
```

editable 安装会直接引用当前 checkout。切换分支或修改 Python 源码后通常不需要重复
安装；如果依赖或项目元数据发生变化，再重新执行安装命令。

### 4. 确认命令来源

```powershell
python -c "import sys, watcherobot; print(sys.executable); print(watcherobot.__file__)"
where.exe watcherobot
watcherobot --version
watcherobot --help
```

macOS/Linux 使用：

```bash
which watcherobot
```

Python 路径和 `watcherobot` 命令都应来自 `watcherobot-source` 环境，模块路径应指向
当前源码 checkout。源码尚未进入版本发布流程时，`watcherobot --version` 仍可能显示
上一已发布版本；此时以固定 commit、模块路径和待验收的新行为为准。

### 5. SDK 贡献者验证

```powershell
python -m pytest
python -m mypy src/watcherobot
```

普通 Application 开发者不需要安装 `[test]` 依赖，也不需要执行这一步。

## 方式二：正式安装已发布版本

正式版本发布到 PyPI 后，普通开发者不需要克隆 SDK 仓库。

### 1. 创建独立环境

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
```

### 2. 从 PyPI 安装

安装当前正式版本：

```powershell
python -m pip install watcherobot
```

安装指定的已发布版本：

```powershell
python -m pip install "watcherobot==RELEASED_VERSION"
```

后续升级：

```powershell
python -m pip install --upgrade watcherobot
```

### 3. 确认安装

```powershell
python -c "import sys, watcherobot; print(sys.executable); print(watcherobot.__file__)"
where.exe watcherobot
watcherobot --version
watcherobot --help
```

macOS/Linux 使用：

```bash
which watcherobot
```

第一条 `where.exe watcherobot` 结果应位于当前 Conda 环境。若系统存在多个
`watcherobot.exe`，不要直接删除其他环境；先确认已执行 `conda activate watcherobot`。

## TestPyPI 预发布验收

TestPyPI 不镜像完整依赖。测试预发布包时，应让 watcherobot 来自 TestPyPI，同时允许
依赖从正式 PyPI 解析；不要只使用 `-i https://test.pypi.org/simple/`：

```powershell
python -m pip install --pre --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "watcherobot==PRE_RELEASE_VERSION"
```

这条命令只用于发布验收，不是普通开发者的正式安装入口。

## 安装后的下一步

```powershell
watcherobot robot setup
watcherobot robot status
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

生成的 Hello World 会先播放 `happy`，支持灯光时闪烁成功提示，随后持续随机轮播演示行为；
按 `Ctrl+C` 停止。

机器人已经接入同一 Wi-Fi 时，不要重置网络；打开机器人上的 `"Python SDK"` 应用，
改用 `watcherobot robot pair <code>`。

## 升级后的 Runtime 版本

CLI 启动或复用 Runtime 时会核对控制协议和 SDK 版本。源码或安装包升级后，如果后台仍是
旧版 SDK-owned Daemon，CLI 会自动停止旧进程并从当前环境启动匹配版本，避免出现
`unknown fields: supported_host_platforms` 之类的新旧 Application 清单不兼容错误。

可以用下面的命令确认前台与后台版本：

```powershell
watcherobot --version
watcherobot daemon status
```

`daemon status` 输出中的 `runtime.sdk_version` 应与当前 CLI 版本一致。自动恢复前发布的
旧版本若已遇到该错误，可先执行一次 `watcherobot daemon stop`，再重新运行 Application。
