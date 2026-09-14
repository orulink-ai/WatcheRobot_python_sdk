# 共享 Daemon 与 Application 版本维护

## 约定

同一操作系统用户使用一个实例目录和一个 Daemon。SDK 是 Daemon 唯一源码来源；桌面端与 SDK CLI 都是启动及管理客户端。Application 只使用 Daemon 注入的 Desktop、Device channel，不另起 Daemon，也不改变业务透明转发规则。

| 组件 | 职责 | 不负责 |
| --- | --- | --- |
| SDK 共享管理模块 | 实例锁、启动记录、Runtime 发布与切换、失败恢复、引用登记与目录回收 | 桌面安装器和 UI |
| SDK Daemon | 唯一常驻实例，管理 Application 进程、连接和透明路由 | 随业务类型绕过 Application |
| SDK 开发/分发工具 | 创建下载源码、安装独立依赖、登记项目、调用共享管理模块 | 为每个项目另起 Daemon或自动抢占运行版本 |
| 桌面端 | 构建锁定 SDK 候选、显示状态、调用 SDK 管理/分发能力、处理 OTA 前置检查 | 维护第二份 Daemon、递归删除用户数据、实现另一套清理规则 |
| Application | 使用自己的 SDK 环境处理业务和注入通道 | 管理常驻 Daemon 或直接依赖内部 Runtime 存储路径 |

三个版本分别管理：桌面版本描述 UI 和安装器；Daemon 状态接口的 sdk_version 描述实际运行代码；Application 的 SDK 来自它自己的虚拟环境。拉取源码或更新桌面安装包不等于正在运行的 Daemon 已切换。即使版本号相同，不同源码提交也可能不同，桌面构建须锁定 SDK commit。

## 启动、联调、切换

- SDK 与桌面任意先后启动，健康实例优先复用。生命周期操作使用 operation.lock，Daemon 自身持有实例锁；多个启动请求不会得到多个健康实例。
- 首次启动成功保存 current-launcher.json。后续冷启动使用记录的启动器，避免应用项目里的 SDK 抢占运行时版本。
- SDK 项目仍用自己的 Python 环境开发应用。CLI 将应用目录和解释器注册给共享 Daemon，由它创建、停止 Application 进程。
- 修改 Daemon 源码后，先停止当前 Application，再在目标 SDK 环境执行 `watcherobot daemon activate`。桌面设置中的切换按钮则激活当前桌面提供的 Runtime。
- 桌面退出保留 Daemon 与 Application。需要完全退出时执行 `watcherobot daemon stop`。不要删除仍被记录的项目虚拟环境，先切换到其他有效启动器。

本地注册通过用户实例目录中的一次性文件精确授权目录与解释器，HTTP 请求不能凭空声明任意启动路径。默认应用配置仅传递白名单环境变量。这不是对同一操作系统用户的安全沙箱。

## 存储与 OTA

默认实例目录为 Windows 的 %LOCALAPPDATA%/WatcheRobot/runtime-instance，macOS/Linux 为 ~/.watcherobot/runtime-instance。测试可显式覆盖 INSTANCE_ROOT、STATE_ROOT 和端口；普通客户端必须使用同一实例目录。

桌面资源先由 SDK 校验、复制到 bundles/内容哈希，再从那里运行。应用安装使用 store 下 runtimes/内容哈希中的 Python；历史 runtime 目录保留。新版本写入新目录，不覆盖运行中的 exe、DLL、Python。哈希用于内容完整性检查，发行签名仍由打包流程负责。

显式切换先阻止新应用启动并确认应用空闲，再停止旧进程、等待退出，启动候选并验证 launch_id，最后原子更新启动记录。候选启动失败时保留旧记录并尝试恢复旧 Runtime；恢复失败报告错误并保留日志。当前实现不提供掉电事务恢复。

## 自动回收与 SDK 独立使用

SDK 的 ensure/activate 成功返回后，以及应用安装/卸载完成后，尝试回收共享 bundles 和已登记应用仓库 runtimes 下的内容哈希目录。清理与启动、安装共用锁；生命周期操作繁忙时跳过。无桌面端也执行同一逻辑，不另设清理 Daemon。

- 保留 current-launcher.json、previous-launcher.json 所指的版本；后者只在成功切换到不同启动命令时轮换。
- 保留登记的应用目录、虚拟环境 pyvenv.cfg、应用安装记录（包括 staging/trash）引用的版本，以及当前用户进程的可执行文件、打开文件和映射文件引用的版本。
- 只回收目录修改时间距今超过 7 天、无上述引用的内容哈希目录。链接和 Windows junction 不作为删除目标。
- 进程权限不足、记录损坏、目录不可读或文件占用时跳过，原因记录在实例目录 cleanup-report.json；后续成功操作重试。无法完整查看进程的机器可能一直延后回收。
- 老版平铺 runtime、源码项目、安装目录和用户数据不自动递归删除，因为它们缺少完整归属信息。不会自动重建应用虚拟环境来解除引用。

仅创建 SDK 项目或下载应用源码不需要启动 Daemon，也不需要创建共享 Runtime。安装应用时登记 store；启动本地开发应用时登记应用和虚拟环境，复用唯一 Daemon。项目 SDK 可以独立更新，不会隐式替换正在工作的 Daemon。手动创建但从未通过工具登记的外部虚拟环境不应直接依赖内部 bundles/runtimes 路径。

| 场景 | 行为与验收 |
| --- | --- |
| 首次安装桌面、此前没有 SDK Daemon | 首次启动发布候选，保存启动记录，只出现一个 Daemon |
| SDK 已先启动，再首次安装桌面 | 桌面复用；不强制切换或中断 SDK 应用 |
| 仅 SDK 创建/下载/安装/运行应用 | 创建下载无需 Daemon；安装登记引用；运行通过共享 Daemon；不依赖桌面 |
| 新架构桌面 OTA | 先确认实际来源，候选独立目录，成功切换后才轮换回退记录 |
| 手动下载新版覆盖安装 | 共享目录内进程不占安装资源；新版首次运行仍优先复用，升级 Runtime 须显式切换 |
| 从旧平铺安装包首次迁移 | 覆盖前先停止旧 Daemon 和占用安装目录的应用；保留数据。旧安装器没有新检查，不能靠新版启动后代码解除覆盖前占用 |

以上矩阵须在受支持 Python 和真实安装包上验收；目录回收单测不能替代安装器覆盖测试。

新桌面 OTA 前检查实际 Runtime 来源。仍从安装目录运行的兼容 Daemon 需先迁移到共享目录；应用忙碌或旧协议无法确认时阻止升级并报告原因。不要通过删除用户数据解决文件占用。升级到本实现前的旧桌面不具备这一检查，首次迁移应停止旧 Daemon 后安装新版。安装器、签名及 macOS 实机升级仍属于发布验收。

## 应用依赖与协议

schema 1/2 保持原有 requires_watcherobot 校验和安装行为，不强制现有应用迁移。schema 3 将两个要求分开：

~~~json
{
  "schema_version": 3,
  "id": "example.camera",
  "name": "Camera",
  "version": "1.0.0",
  "requires_sdk": ">=0.1.9,<0.2",
  "requires_daemon": {"application_protocol": ">=1,<2"},
  "supported_host_platforms": ["windows"],
  "dependencies": []
}
~~~

在干净、已经验证的应用虚拟环境内，用支持 schema 3 的 SDK 工具执行 `python -m watcherobot.distribution.dependency_lock .`，生成 app.lock.json。将清单、锁和源码一起发布到应用 Space 的固定提交。安装器按锁内精确版本安装全部依赖，关闭隐式依赖解析，再执行依赖一致性检查；不会使用桌面 wheel 替换锁定的应用 SDK。

锁当前只支持精确版本，不支持 URL、条件标记和包哈希；包含生成环境内的所有发行包。跨平台依赖不同的应用应分别验证、限制支持平台，不能把单个平台的锁当作跨平台保证。

旧版 CLI 不识别 schema 3，也没有共享启动器管理能力。旧 SDK 的 ApplicationContext 可通过环境注入连接 Daemon，但实际业务兼容仍需应用作者验证；新格式的校验、安装、启动管理须使用本实现之后的工具。不要仅修改清单就宣称旧 SDK 的任意业务功能兼容。后续 SDK 升级只要保持 Application 协议兼容，应用不必重新发布；需要新 SDK API 或协议破坏升级时才更新应用依赖并重新验收。

## 验收与故障定位

检查 /daemon/status 的 PID、executable、source、sdk_version、application_protocol、management_protocol 和当前 Application。桌面版本、pip 中版本、源码版本不能替代实际进程信息。

自动测试覆盖两种启动顺序、并发复用、项目授权、忙碌拒绝切换、空闲切换、失败恢复、不可变目录、协议兼容与原有路由。发布时还须在 Python 3.10–3.12、Windows 安装包 OTA、macOS 签名包和真实设备上验收；当前开发机测试不能替代这些发布检查。
