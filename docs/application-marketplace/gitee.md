# Gitee 接入进度

本轮已实现平台隔离的登录、状态查询和退出。其他分发命令尚未接入 Gitee，不能据此认为 Gitee 发布已经可用。

## 登录

以下命令在 Windows、macOS 和 Linux 使用相同参数：

```text
watcherobot app login --provider gitee
watcherobot app login --provider gitee --status
watcherobot app login --provider gitee --force
watcherobot app logout --provider gitee
```

在交互终端隐藏输入个人 Access Token。验证身份成功后才写入系统凭据管理器，不接收命令行明文 Token。强制登录失败保留已有凭据。SDK 不读取预测或管理员手动添加的凭据项。

`--status --jsonl` 可供机器查询；非交互登录缺少凭据时返回认证错误，不等待输入。

HF 登录必须显式使用 `--provider huggingface`，仍使用设备码授权。登录和退出不兼容省略平台的旧命令，两平台凭据互不影响。

## 后续接入目标

- 官方目录：https://gitee.com/orulink-sz/watcherobot-app-store
- 默认分支：master；目录文件：app-list.json。
- 开发者发布自己的公开仓库，目录引用 repo_id 和完整 commit。
- Fork 后提交 PR，由团队人工合并；SDK 不自动合并、不要求官方目录写权限。
- 正式目录禁止收录测试数据；集成写入测试使用专用测试仓库。

发布、投稿、目录读取、下载安装和正式目录分支保护尚未完成。
