# Hugging Face OAuth 实施合同

本文档记录 Watcher Application 分发工具使用 Hugging Face OAuth 的当前官方事实和已验证端点行为。它只约束 SDK 分发工具，不属于 Daemon 运行时合同。

## 已确认配置

- OAuth App：`Watcher Desktop`
- 类型：Public OAuth App，无 Client Secret
- Client ID：`65c05ae4-072e-425b-98e9-06aa89bab970`
- scopes：`openid profile contribute-repos write-discussions`
- Device endpoint：`POST https://huggingface.co/oauth/device`
- Token endpoint：`POST https://huggingface.co/oauth/token`
- grant type：`urn:ietf:params:oauth:grant-type:device_code`

官方文档：

- https://huggingface.co/docs/hub/en/oauth
- https://huggingface.co/.well-known/openid-configuration

官方文档确认 Public App 可以只使用 Client ID 执行 Device Code OAuth，不需要也不得在 Desktop/CLI 中嵌入 Client Secret。当前四个 scope 的用途分别是身份、公开资料、创建/访问本 OAuth App 创建的仓库，以及创建/操作讨论和 Pull Request；不申请 `write-repos` 或 `manage-repos`。

## 2026-08-04 脱敏实测

使用已创建 Client ID 和上述 scope 请求 Device endpoint，响应包含：

- `device_code`：字符串，只保留在登录流程内存中。
- `user_code`：字符串，可以显示给用户。
- `verification_uri`：`https://hf.co/oauth/device`。
- `expires_in`：300 秒。

当前响应没有 `interval` 字段，因此实现必须提供保守默认轮询间隔，同时兼容服务端未来返回 `interval`。在用户尚未授权时立即请求 Token endpoint，官方返回 `authorization_pending` 和非敏感说明。

实测过程没有输出或保存 `device_code`、`user_code` 或 Token。

## 实现边界

- SDK 自己的 OAuth Client 使用上述 Public Client ID，不调用会把 Token 写入 Hugging Face 普通缓存文件的通用 `huggingface_hub.login()`。
- `device_code`、Access Token 和可选 Refresh Token 不进入 JSONL、stderr、异常字符串、普通配置或日志。
- JSONL 的等待事件只包含用户需要的 `verification_uri`、`user_code` 和过期秒数。
- Token 只由 Watcher 专用系统凭据条目保存；退出只删除这一条，不调用会清空其他 Hugging Face 登录的全局 logout。
- OAuth HTTP、凭据库、Hub `whoami` 和事件输出继续通过可替换接口注入，单元测试不得访问真实网络或真实系统凭据。

## 下一验证顺序

1. Fake 覆盖成功、等待、拒绝、过期、网络失败和取消。
2. 实现 HTTP Device Flow 和 scope/响应校验。
3. 实现 Watcher 专用系统凭据存储与精确删除。
4. 实现 `whoami` 身份确认、登录状态和退出。
5. 最后使用普通非组织管理员账号进行一次真实登录验收。
