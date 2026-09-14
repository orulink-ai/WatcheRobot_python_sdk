# 共享 Daemon 与 Application 版本维护

## 约定

同一操作系统用户使用一个实例目录和一个 Daemon。SDK 是 Daemon 唯一源码来源；桌面端与 SDK CLI 都是启动及管理客户端。Application 只使用 Daemon 注入的 Desktop、Device channel，不另起 Daemon，也不改变业务透明转发规则。

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

显式切换先阻止新应用启动并确认应用空闲，再停止旧进程、等待退出，启动候选并验证 launch_id，最后原子更新启动记录。候选启动失败时保留旧记录并尝试恢复旧 Runtime；恢复失败报告错误并保留日志。当前实现不提供掉电事务恢复或自动清理旧目录。

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
