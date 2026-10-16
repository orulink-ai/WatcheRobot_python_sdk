# 陆骁 PR / CI / CD 通知接入

通知主服务运行在 VM100：`luxiao-notifications.service`，由陆骁应用身份发送。每个 PR 沿用唯一群话题，主卡更新状态，关键结果回复到该话题；PR 作者、最近提交人、合并人和时间从 GitHub API 核实。CI 与 CD 使用统一紧凑卡片，审查详情跳转 GitHub。

## 事件入口

`.github/workflows/luxiao-notify.yml` 在 GitHub 收到 PR 生命周期、被列入监听清单的工作流状态、发布与分支事件后，由内网 Runner 转发到 VM100 的固定 HTTPS 地址。它是事件转发工作流，不是 GitHub 原生公网 Webhook；及时性取决于 Runner 排队。VM100 每 120 秒通过 API 补漏，正常通知处理不调用模型。

- `LUXIAO_NOTIFY_HMAC`：仓库 Secret，用于签名；严禁写入源码或日志。
- `LUXIAO_NOTIFY_CA_CERT`：仓库 Variable，固定信任接收端证书。
- `LUXIAO_NOTIFY_ENDPOINT`：仓库 Variable，只允许固定的内网通知地址。
- `workflow_dispatch`：仅发送签名 ping，验证传输，不发送群消息，不运行业务 CI、审查模型或烧录。
- 新增/重命名业务工作流时，同步更新 `workflow_run.workflows` 监听列表；补漏仍可发现未列入监听的新工作流。

`pull_request_target` 只运行默认分支中这段受信任的转发程序；不 checkout、不执行 PR 内容，事件内容仅作为待签名数据。GITHUB_TOKEN 权限为空，不使用第三方 Action。

## 通知规则与证据

PR 新建、重开、关闭、合并在同一话题更新；重复投递与相同执行状态去重。CI 失败显示失败任务与可核实原因；基础设施阻塞与业务断言失败分开。CD 先报告“发布流程通过”；只有校验 Release、非空制品以及标签对应提交后才标“制品已发布”。未验证的安装、目标环境、真机结果不得标成功。无 PR 关联的分支/版本运行使用独立记录，不伪造 PR 或合并人。

部署期间群消息出口保持关闭，基础设施接入 PR 列入静默清单。最终链路验收后才开启，用户允许仅发一条标明测试的验收通知。陈工模板、容量维护、其它 Agent 技能与业务发布规则保持原样。

## 运维与回退

VM100 只读检查：`systemctl status luxiao-notifications.service`，或以 hermesadmin 执行 `python3 /usr/local/lib/luxiao-notifications/runtime.py health`。状态库位于该用户的 `.local/state/luxiao-notifications/state.db`，不得手工删除历史话题映射。

回退仓库接入：用本机授权账号 `gh workflow disable luxiao-notify.yml --repo <仓库>`；服务器通知切换有独立配置备份与 `rollback_root.py`。回退只恢复通知服务适配，不还原 Agent 技能。禁止为通知问题重跑业务发布或烧录。
