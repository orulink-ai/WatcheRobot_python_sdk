# AI Chat 账号连接的桌面 MCP 授权

- 创建时间：2026-11-15 00:30:01 +08:00。
- 建档作者：KID。实施：Codex。
- 需求：ESP32 拆分固件保留 #308 的账户换绑和 SDK 独立配对，接入 JoyInside 与公司小智的桌面 MCP。
- 分支：codex/sdk-account-mcp-integration。基础：SDK #159。

## 变更

已验证设备身份的在线账户会话通过现有的控制接口交付会话派生密钥。凭据只在本地原生请求中读取，保留独立手动配对的授权。账户断开前撤销工具密钥，清理后可以重新使用已保存的手动绑定。Application 通道没有变更。

## 验证

Windows Python 3.12，先扩展现有 UDP、WebSocket、REST 联测。实现前，握手未完成时接口错误返回 200 和手动配对授权，预期是 404。实现后运行 `python -m pytest tests/runtime/test_daemon_reunite_runtime.py tests/runtime/test_control_rest.py`，36 项通过，1 项第三方依赖弃用提示。验证握手完成前拒绝取密钥、账号连接后派生密钥相符、换绑断开后恢复独立手动配对授权。

## 配套

ESP32 同名集成分支采用相同的凭据派生协议。桌面原生桥接继续使用现有回环接口，其安装包需要使用包含本次改动的 SDK。
