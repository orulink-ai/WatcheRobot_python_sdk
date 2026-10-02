# Gitee 公开目录限流识别

2026-10-03 Asia/Shanghai；建档作者 orulink-wugui；实施 Codex。用户要求定位并解决桌面应用广场刷新失败。本地已修改，未提交。

真实目录 branch 请求 HTTP403、文本体含 rate limit exceeded。通用 transport 仅按JSON解码导致限流标记丢失；修复为只识别明确限流，返回脱敏标记。GiteePublicRepository 抛 HubRateLimitError，marketplace 转稳定 rate_limited，退出码4。普通403仍按网络/访问错误，不能推断限流。

红测先加入HTTP文本限流和429测试后失败；修复后完整分发测试401通过。新增目录限流码测试属于事后补充回归。合同增加错误码说明。配套桌面实现并发合并、60秒本地冷却与限流提示。

限制：Gitee 仍在限流，代码不能保证60秒后服务端解除；未切换凭据或绕过访问控制。未进行发布。


## 2026-10-03：取消广场固定截止并保留实际HTTP状态｜orulink-wugui / Codex
延续用户要求：不新增服务器；拿到实际响应、不靠固定超时推断。公开读取transport允许timeout=None，Gitee广场CLI目录与manifest全部选用该策略；发布、安装及下载默认超时未改变。HubError可携带可选HTTP状态；广场仅返回状态码与阶段，不透传远端文本。Desktop对应档案为客户端 documents/dev_log/2026-10-02/2026-10-02_应用广场加载失败排查/index.md，双方均已记录配套关系。

TDD：先写无截止参数测试及HTTP详情测试并得到预期失败，再实现。HubError原先可无参数构造，完整分发回归发现兼容问题，修复默认空message后404项通过。源码CLI实测1.746秒返回HTTP403、rate_limited、fetching_catalog，远程仍受限；无虚构恢复状态。文档distribution-contract.md已更新。未提交/发布。
