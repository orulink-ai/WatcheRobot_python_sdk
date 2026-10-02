# Gitee 公开目录限流识别

2026-10-03 Asia/Shanghai；建档作者 orulink-wugui；实施 Codex。用户要求定位并解决桌面应用广场刷新失败。本地已修改，未提交。

真实目录 branch 请求 HTTP403、文本体含 rate limit exceeded。通用 transport 仅按JSON解码导致限流标记丢失；修复为只识别明确限流，返回脱敏标记。GiteePublicRepository 抛 HubRateLimitError，marketplace 转稳定 rate_limited，退出码4。普通403仍按网络/访问错误，不能推断限流。

红测先加入HTTP文本限流和429测试后失败；修复后完整分发测试401通过。新增目录限流码测试属于事后补充回归。合同增加错误码说明。配套桌面实现并发合并、60秒本地冷却与限流提示。

限制：Gitee 仍在限流，代码不能保证60秒后服务端解除；未切换凭据或绕过访问控制。未进行发布。


## 2026-10-03：取消广场固定截止并保留实际HTTP状态｜orulink-wugui / Codex
延续用户要求：不新增服务器；拿到实际响应、不靠固定超时推断。公开读取transport允许timeout=None，Gitee广场CLI目录与manifest全部选用该策略；发布、安装及下载默认超时未改变。HubError可携带可选HTTP状态；广场仅返回状态码与阶段，不透传远端文本。Desktop对应档案为客户端 documents/dev_log/2026-10-02/2026-10-02_应用广场加载失败排查/index.md，双方均已记录配套关系。

TDD：先写无截止参数测试及HTTP详情测试并得到预期失败，再实现。HubError原先可无参数构造，完整分发回归发现兼容问题，修复默认空message后404项通过。源码CLI实测1.746秒返回HTTP403、rate_limited、fetching_catalog，远程仍受限；无虚构恢复状态。文档distribution-contract.md已更新。未提交/发布。


## 2026-10-03 完整CI与提交审查｜orulink-wugui / Codex
用户已授权提交与推送，不创建PR。分支codex/desktop-distribution-20261003基于最新origin/main=7d9fc51；49b337a为空闲取消选择，3afac46为分发进度与远端状态。

本地SDK CI对照sdk-ci.yml执行：Python3.11全量pytest 1546通过、5跳过；mypy 109个源文件无问题；pip check通过；浏览器辅助JS测试84通过；sdist/wheel构建及twine check通过；全新隔离环境安装wheel、pip check与watcherobot --help通过。Python3.10/3.12全矩阵和Windows硬件并非本轮执行范围。

审查确认取消选择不更改业务帧路由，拒绝运行中/启动中/目标变化；下载按字节与重试代次报告；HTTP状态只保留数字，不输出凭据或响应正文。通用扫描命中AccessToken类型、运行时读取表达式及假测试数据，人工复核无真实密钥；diff --check通过。没有为绕过403新增服务、轮换凭据或伪造成功。配套Desktop分支codex/desktop-scope-marketplace-20261003会锁定此SDK分支最终提交。
