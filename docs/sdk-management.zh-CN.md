# SDK 独立更新与桌面调用合同

本功能正在功能分支实施，尚未通过正式发布制品、Windows/macOS 安装包和真实设备验收。以[飞书最终方案](https://mcnsslrwxv50.feishu.cn/docx/Zi8tdalvvoAgyuxYLW3cVLxOnve)为完整验收依据；实现和剩余缺口记录在[验收跟踪](../documents/dev_log/2026-10-16/2026-10-16_154638_orulink-wugui_无Issue_SDK管理与独立更新/acceptance.md)。

## 边界和入口

`watcherobot.management` 是 SDK 仓库内的管理实现。桌面只携带 SDK 发行物，通过通用进程/JSONL 调用入口传递用户意图、资源位置和支持声明，并展示 SDK 返回的状态及动作。桌面自己的 OTA 继续使用原生更新系统。

安装包提供 SDK 基线、SDK 管理入口和必要 Python/uv 资源，用户安装后不必先额外下载 SDK。设备发现/连接仍需满足网络和同一局域网条件；镜像检查与下载需要互联网。管理状态查询不依赖 Daemon 正常运行。

普通 Python 交付入口为 `watcherobot-sdk-manager` 或 `python -m watcherobot.management`；冻结 Daemon 通过 `--sdk-manager` 调用相同实现。入口从 stdin 读取一个不超过 1 MiB 的 JSON 请求，stdout 为逐行 JSON：

```json
{"type":"progress","task_id":"...","revision":1,"phase":"downloading","downloaded_bytes":65536,"total_bytes":1000000}
{"type":"result","ok":true,"data":{"contract_version":1}}
```

失败返回 `type=result, ok=false, error={code,message}` 并以非零状态退出。进度可有多条，最终结果只有一条。调用方不可把状态轮询当作启动/接管请求。

请求包含 `action`、桌面声明 `desktop.manager/management/distribution` 的支持范围，以及适用时的 `resources`、`distribution_executable`、`daemon_args`、`application_stores`。路径由 SDK 校验。桌面不计算 SDK 版本推荐或协议兼容。

| action | SDK 行为 |
|---|---|
| status | 返回当前选择、实际 Daemon、合同检查、持久任务和动作可用性 |
| check_update | 读取清华镜像中的签名发行目录，过滤当前平台、Python 和桌面合同，按稳定版本推荐 |
| update / change_version | 更新至推荐新版 / 显式更换指定发行身份；可能返回应用影响确认信息 |
| cancel | 使用 task_id 取消准备或等待阶段；切换和提交后的清理不能取消 |
| recover | 恢复中断事务；识别后来外部接管，不主动抢回 |
| rollback | 在保留记录有效时验证并切换上个 SDK |
| repair | 使用安装包基线修复，入口不依赖被损坏的已下载 SDK |
| restore_default_runtime | 用户明确要求后，恢复当前选定 SDK 的 Daemon |
| cleanup | 清理未引用的 SDK 环境、事务下载和 SDK 私有 uv 缓存，返回统一设置页状态 |
| resolve | 返回 SDK 所选 Daemon/分发执行命令；入口解析不启动服务 |
| control | 检查实际 Daemon 合同后转发本机 `/daemon/` 管理请求；不处理业务帧 |
| begin_activity / end_activity | 登记桌面原生 OTA/维护任务寿命，SDK 统一判断切换时机 |

SDK 更新后，基线入口把管理操作转给所选 SDK 的实现，同时保留安装包基线身份和救援命令；修复始终可走基线。分发入口也由 SDK 自己转到所选 SDK，避免只更新 Daemon 而继续使用内置旧分发实现。损坏所选环境时可回到基线查询/修复；完整损坏与强退矩阵仍待验收。

## 状态与兼容

`status` 的 `selection` 表示桌面当前选定的 SDK，`daemon` 表示实际运行实例；两者可因外部 SDK 显式接管而不同。不能用桌面内置版本、`pip show` 或当前选择替代实际版本。`revision` 用于展示时丢弃旧响应。

`compatibility` 返回 `compatible/incompatible/unverified`，附要求值、实际值和建议动作。`actions` 为每个操作提供 `enabled/reason`；UI 只消费结果。`operation` 持久记录任务 id、阶段、进度和失败信息；重新打开设置页查询同一任务。清理报告说明延期和错误，不把删除失败报告为清理完成。

管理、分发、Application 连接和数据合同分别由 `compatibility.py` 中的中央常量定义，包版本不直接代表合同版本。候选缺少已知合同则不默认兼容；目前阻止数据 schema 改变，尚不支持可逆数据迁移。桌面声明自己使用的业务组合同范围，SDK 根据当前 Application 清单中的 `desktop_contracts` 比较，`status.business.groups` 返回每组的状态、原因和 enabled。桌面仅展示结果及启用/关闭对应功能；不实现版本比较。Application 不运行时由 SDK 给出透明设备控制合同，应用运行时只信任该应用的显式声明，不继承设备能力。缺失声明返回未验证；默认 Application 不设特例，本轮不修改其仓库，也不随桌面安装包发布。Daemon 只在管理状态中暴露声明，仍然不按业务消息 type 路由。

schema 3 应用分别声明 `requires_sdk` 和 `requires_daemon.application_protocol`。新模板使用 schema 3，开发者在应用独立环境执行 `watcherobot-app-lock .`，将精确锁与源码一并发布。启动前探测应用自己的解释器、SDK 和依赖锁；不执行应用业务代码来做探测。应用 SDK 可以不同于 Daemon SDK，只有合同及实际环境校验通过才运行。

schema 1/2 暂保留原有版本范围检查，属于旧格式路径，不能据此宣称具有独立协议或完整业务兼容保证。旧 CLI 不会自动获得新事务锁、恢复或新版管理合同。

## 发行物与更新事务

SDK 本体发布到 PyPI，清华负责同步，不另向清华上传源码。初版选用纯数据 `watcherobot-desktop-release` wheel 承载签名发行目录；该发行目录包尚未正式配置/发布。客户端只读取其中数据，不执行目录包代码。

签名目录包含递增 sequence、平台、Python 范围、全部合同、SDK 及运行依赖的精确版本/文件名/大小/SHA256，以及少数源码依赖需要的精确构建工具 wheel。SDK 本体必须是 wheel。源码依赖只允许在候选内使用已下载、锁定的构建依赖安装，不在线解析构建依赖；发行者须预先在目标平台验证完整锁。目录不可被旧 sequence 回放或同 sequence 替换内容。下载及重定向只访问受限 HTTPS 清华 PyPI 路径。

`watcherobot-sdk-release-catalog` 或 `python -m watcherobot.management.publishing` 可准备签名目录 wheel：参数为 `--catalog --signing-key --trusted-keys --key-id --version --output`。该工具只校验、签名和生成文件，不上传，不覆盖已有制品。私钥由发布方保管，不进入客户端或 Git。SDK 的 `release-keys.json` 必须配置受信任公钥；当前文件为空，检查/更新动作禁用。已补充本地准备、制品检查及受保护的目录发布工作流；平台依赖锁仍需发行者生成和验证，正式公钥、PyPI Trusted Publisher 配置及镜像在线验收尚未完成，不能宣称已可在线 OTA。

SDK 在同一用户共享实例目录的 `sdk-management` 下维护 `state.json` 和独占更新锁：

1. 校验桌面、目录身份、应用影响和空间，一次只有一个事务，不排队管理多个下载目标。
2. 下载唯一候选，逐块报告进度；在最终目录创建环境，离线按哈希安装和自检，不启动第二个共享 Daemon。
3. 等待应用/维护/桌面 OTA 空闲，允许取消；切换前再次检查实际实例及影响。
4. 在生命周期锁内记录 before/target 事务，调用现有 SDK 激活核心停旧启新并检查内容身份；成功后原子提交选择与短期回滚记录。
5. 失败时依现有核心尝试恢复旧启动器，必要时保留事务供恢复。发现外部后启动的 SDK 已接管则不误杀、不循环抢回。
6. 清理下载、无引用 SDK 环境和 SDK 私有 uv 缓存；删除失败保留报告。当前、回滚、候选、应用或运行进程引用不得误删。前版本建议保留 7 天或下次更新前退休，运行中引用仍保护。

显式不同 SDK 版本启动仍可升降级接管，同版本通常复用；普通桌面应用运行只请求 Daemon 启动应用固定入口，不启动该应用环境的 SDK CLI。被动轮询和重连不触发版本争抢。业务通道及透明路由不随这项管理改动改变。

## 当前验证边界

最新 SDK 全量回归 2076 passed / 14 skipped，Rust 160 passed / 6 ignored，TypeScript 检查通过。macOS 使用重新构建的 SDK wheel、清华预下载依赖及随包 Python/uv，在隔离实例完成真实候选安装、激活、签名测试目录更新和回滚、常驻入口转交，共 6 项通过。版本 99.0.0 仅为本地测试 fixture，未发布；镜像 HTTP 使用受控响应，不能据此声称正式镜像 OTA 已验收。用例 `tests/management/test_installer_live.py` 通过环境变量提供预下载制品和运行资源，默认不执行。

最新 CN/macOS 测试 DMG 完整构建、挂载、资源哈希、App 签名及实际单次/常驻管理入口检查通过，不包含默认 Application。桌面全脚本回归 697 passed / 3 failed / 1 skipped；3 项为已有资源摘要和跨仓 App.Center 测试问题，详见验收记录。

多客户端竞争、发行签名部署、完整更新/回滚、硬中断、磁盘不足、系统安装器、设备业务链路和业务能力实际联调仍须对照飞书清单补齐；不能以这些局部通过项替代最终验收。

## 桌面打包验收边界

桌面打包包含 SDK Daemon、分发与管理入口、必要 Python/uv 资源，不包含默认 Application。桌面仓库仅收集 SDK 模块和数据，不能复制 SDK 管理实现。打包后的管理状态、命令解析、签名信任资源和无 Daemon 情况下的救援入口均须验证；本地构建和安装包验证在本轮范围内，对外上传发布单独记录。

## 常驻管理入口与错误结果

单次工具继续使用 `--sdk-manager`；桌面使用 `--sdk-manager-stream` 复用同一个 SDK 管理进程。该进程只通过 stdin/stdout 处理本地管理请求，不监听端口、不连接设备、不转发业务帧，因此不会创建第二个 Daemon。SDK 内部允许状态查询/取消与长操作并发，更新仍由单一事务锁约束。帧包含 `id` 和 `request`，输出进度及结果带同一 `id`；每帧最多 1 MiB，最多接纳 32 个待处理请求。

管理进程在每个请求中读取当前 SDK 选择；更新后由选定 SDK 的管理入口执行操作，安装包基线仍提供救援。桌面通用桥接复用进程、对应请求和响应、展示进度；管理连接失效时失败返回，不自动重放修改操作。后续请求可重新建连，再由 SDK 持久事务决定恢复。该传输机制不替代 Daemon 的单实例和不同版本后启动接管规则。

错误统一返回稳定 `code/message/retryable/suggested_action/details`，持久任务记录使用同一格式。网络更新源不可用、局域网设备发现及本机 Daemon 故障分别处理；`sdk_network_unavailable` 不意味着局域网设备断开。磁盘不足、文件权限、并发任务、任务取消及运行超时各有独立代码。桌面只保留并展示 SDK 结果，不另行决定兼容或恢复策略。

磁盘检查前先退休下一次显式更新允许移除的旧回滚记录，清理无引用环境，再准备唯一候选。无法确认运行引用时延期；不能为了凑成一个目录而删除仍被进程或应用使用的资源。状态写入先验证大小，再原子替换，避免写入随后无法读取的超限文件。


## 源码运行、制品检查和目录发布

所有开发者命令同时提供 `.sh` 和 `.ps1`，参数与退出码一致。下列展示 Shell 入口；Windows 使用同名 `.ps1`。可用 `WATCHER_SDK_PYTHON` 指定已安装本仓库依赖的 Python，默认分别为 `python3` / `python`。

```sh
sh tools/run-sdk.sh --describe-runtime
sh tools/verify-sdk-management-release.sh --wheel /path/to/watcherobot-X.Y.Z-py3-none-any.whl
sh tools/verify-sdk-management-release.sh --wheel /path/to/watcherobot-X.Y.Z-py3-none-any.whl --require-update-trust
sh tools/prepare-sdk-ota-release.sh --sdk-wheel /path/to/watcherobot-X.Y.Z-py3-none-any.whl --catalog /path/to/platform-locks.json --signing-key /secure/external/release-key.pem --key-id production --version X.Y.Z --output /path/to/output
sh tools/publish-sdk-ota-release.sh --sdk-wheel /path/to/watcherobot-X.Y.Z-py3-none-any.whl --catalog-wheel /path/to/output/watcherobot_desktop_release-X.Y.Z-py3-none-any.whl
```

最后一条默认仅验证，不上传；本机不能直接加 `--publish` 绕过受保护工作流。签名前必须在 SDK 的 `release-keys.json` 配置对应公钥，再使用包含该公钥的真实 SDK wheel。私钥只作为外部文件输入，不写入仓库、桌面或目录 wheel。准备脚本把目录中的 SDK 文件名、版本、大小、摘要绑定到输入 wheel，不能拿测试密钥或另一份同版本 SDK 冒充正式发行物。

SDK 正式版本继续走现有版本 PR、受保护 tag、发布验收流程。新建 SDK wheel 在稳定发布前检查管理入口与发行公钥；历史不可变制品恢复流程不重建旧包。目录包是另一项纯数据发行物：先人工完成目标平台精确依赖锁和验证，以已验证 SDK Release 的原始 wheel 准备签名目录，再将签名目录 wheel 附到该 Release。手动执行 `Publish signed SDK OTA catalog` 工作流，输入 SDK tag 与目录版本。该工作流只允许 main，在已有受保护 `pypi` 环境通过 OIDC 发布目录包，不重复发布 SDK；须另行配置目录包的 PyPI Trusted Publisher。

发布器使用 `gh` 检查正式 SDK Release，核对 PyPI 原始 SDK 摘要，并验证目录版本和 sequence 不倒退、同版本内容不可替换。上传后核对 PyPI 和清华镜像的文件摘要及目录签名。清华同步未到达时工作流报告待验证，可用相同不可变制品重试；不能把上传成功当成用户已能下载。未执行任何正式上传或工作流。本轮平台锁自动生成与发行签名部署仍未完成。

状态中的 `diagnostics` 由 SDK 按允许字段生成，仅含版本、构建身份、合同、任务 id/动作/阶段；不包含运行路径、环境变量或原始异常。桌面仅展示，可用于开发者定位实际 SDK 与当前选择不一致的问题。

本轮按用户确认只做本地实现和测试，不执行 SDK 发布或配置生产密钥。增加实际管理进程被 kill、目标 Daemon 启动后提交遇 ENOSPC 的恢复验证；恢复复用实际目标 PID，再可回滚。下载取消与中断恢复会清理临时下载并报告失败。跨进程退出检查区分 zombie 与活进程，不能把 zombie 的 PID 残留误当成仍持有资源。
