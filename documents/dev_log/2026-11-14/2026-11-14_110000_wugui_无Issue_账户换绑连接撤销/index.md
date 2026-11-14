# 账户换绑连接撤销

- 作者：wugui；时区Asia/Shanghai；2026-11-14开始，进行中。
- 用户要求：从最新远端dev新建分支补充账户连接/撤销；独立SDK不依赖账号，局域网正常时不因互联网断开停止运行。
- 分支：codex/account-binding-replace-20261114；基线origin/dev 28d4df4ea6977a77abd00271176526d983e57443。
- 当前dev已有account-connect/disconnect；桌面旧锁fbe8164缺少接口。沿用新dev，禁止复制Daemon或按业务帧类型绕过Application。
- 本次目标：凭据变化后撤旧，延迟旧disconnect不误伤同设备的新凭据连接；保留手工配对、已记忆凭据和局域网离线使用。云端身份与绑定管理仍由客户端/服务端负责。
- 验证计划：真实回环REST/UDP/WebSocket，旧凭据撤销与重放、独立SDK手工配对保留；完整runtime回归。
- 配套：App PR52、桌面PR196、Supabase PR1及固件功能分支。SDK代码与自动化测试已完成，跨仓真机验收仍进行中。
- 历史：[账号设备定向连接](../../2026-11-12/2026-11-12_170400_KID_无Issue_账号设备定向连接/index.md)。本任务补充换绑生命周期。

## TDD与验证记录

- 旧凭据disconnect测试先得到HTTP400，新增可选凭据参数后通过；同设备新凭据扫描不被旧请求取消，响应不含连接密钥。
- 并发测试先得到device_slot_occupied并提前结束，连接管理串行锁加入后正确等待旧断开完成。
- 真实回环REST/UDP/WebSocket测试复现账户撤销发送disconnect_requested，改为account_binding_revoked后通过，避免固件误清手工配对记录。
- 最终runtime套件471通过、14跳过，100.55秒；已有第三方弃用及ZIP重复条目警告3项。两修改模块mypy通过。只读源码检查和测试未连接实机，不将模拟握手称为硬件验收。
- 尚未提交、合入或发布；桌面锁定SHA及跨仓实机联调待后续提交配套。

- 最终独立审查未发现Critical/Important；审查者复跑3项关键测试通过、9项未选。修正文档旧状态及一处缩进，不改变运行行为。仅覆盖SDK分支，其他仓与真机由主任务继续验收。
