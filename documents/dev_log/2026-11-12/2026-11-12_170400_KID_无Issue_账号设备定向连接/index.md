# 账号设备定向连接

- 建档：2026-11-12 17:04 Asia/Shanghai，KID / Codex。
- 分支：codex/account-device-link。交付状态：SDK worktree 本地修改。
- 目标：桌面可请 Daemon 使用 App 绑定设备的账号连接密钥，在局域网定位设备并建立连接。
- 范围：SDK Daemon REST、UDP 发现及配对握手。验收覆盖正确设备连接、无效凭证拒绝、注销断开和手动配对兼容。App、桌面和 ESP32 的配套改动分别在各自仓库验证。
- 既有依据：[UDP 凭证重连重试](../../2026-11-09/2026-11-09_225000_Mr-KID-github_无Issue_UDP凭证重连重试/index.md)。沿用原后台扫描机制；账号扫描只在内存保留密钥，业务帧路由保持原有合同。

## 实际变更

- 新增原生 IPv4 回环 REST 账号设备定向连接和按编号断开。只有尚未应答的历史绑定扫描可被账号连接抢占。账号断开保留历史手动绑定。
- 每个 UDP 网卡独立签名实际源 IPv4。收到应答时以接收网卡地址验签。UDP 证明、请求 MAC 与 WebSocket 凭证分别使用独立前缀。账号握手取消时在本地使凭证失效。
- 格式、状态和 MAC 字段详见 [Daemon 管理接口合同](../../../../docs/contracts/daemon-control-protocol.md)。SDK 和 ESP32 使用相同的固定测试向量。

## TDD 与验证

- Red：新 REST 流程测试在基线上得到 HTTP 404；协议测试在基线上拒绝账号扩展字段；源 IPv4 加固前新增断言失败；取消泄露检查复现了原 UDP 取消报文携带 WebSocket 凭证。
- Green：相关 runtime、REST、协议、状态机、UDP、WebSocket 和路由共 85 项通过。真实本机回环 REST、UDP 和 WebSocket 验证完整账号握手、无效证明、拒绝复用 UDP 凭证、状态及断开。双网卡测试验证独立 MAC、跨网卡拒绝及取消不发送账号凭证。
- 验证环境：Windows Python 3.12。旧 UDP 唤醒测试改用真实回环网卡，修复从回环向非回环地址发送时报 WinError 10051 的测试环境假设。
- 五个修改的 SDK 模块在跳过外部模块及禁用既有未使用忽略检查时通过 mypy；默认分析被本机安装的 PyTorch 非 UTF-8 类型存根中断。SDK 测试未连接实机。
- 运行时完整套件在隔离环境复跑得到 468 通过、14 跳过、1 失败。失败项 `test_runtime_waits_for_launcher_descendant_to_release_media` 在整套运行时因 Application 启动超时失败，同环境独立复跑通过。首次运行因子进程加载已安装 SDK 或未获得项目声明的 dulwich 依赖而失败的两项，在隔离环境均已通过。最终 `git diff --check` 通过。

- 2026-11-12，KID / Codex。按用户要求提交账号绑定配套改动并在本地合入 dev。局域网配对、Daemon REST 和 WebSocket 定向测试通过，已用真实 Supabase 绑定凭证在 Windows 连接机器人。
