# 国内应用广场：内嵌 Git 获取

2026-10-15 起，本功能分支以 Dulwich 替代 Gitee OpenAPI 公共读取和商店 TOS 快照。

## 数据流

开发者源码及 app.json 继续在开发者 Gitee 仓库维护。官方 master 的 app-list.json 是人工审核后的仓库和固定提交名单。
SDK 通过匿名 HTTPS Git smart protocol 解析官方 master，取得目录固定提交；按名单取得每个应用固定提交中的 app.json。
目录格式、manifest、仓库与应用 ID、重复 ID 校验保留。兼容性和操作系统适配在用户机器计算。
浏览、下载、安装源码不调用 Gitee OpenAPI；上传、登录、投稿等管理操作保持现有 API 合同。

## 获取与缓存

SDK 集成 dulwich 1.2.17，不启动系统 Git、不读取 Git 凭据助手、不执行 hooks，不自动递归子模块。
先直接 SHA depth=1；仅提交不可直接获取或对象缺失时，尝试公开分支 depth=32、256。到达上限仍缺失则报错，不替换为 HEAD。
每次手动刷新查询官方 master；固定提交按仓库摘要与完整 SHA 缓存，安装可直接复用。
跨进程文件锁串行保护缓存；临时下载完整验证后原子发布；损坏缓存重新获取，崩溃遗留暂存下次清理。
缓存采用近期使用顺序淘汰，上限 1 GiB；默认 Windows LocalAppData/WatcheRobot/distribution/gitee-git，其他平台 ~/.cache/watcherobot/gitee-git。

单次获取及历史回退共享 256 MiB 接收上限、180 秒读取截止；连接/读取超时 30 秒，超时不自动重试。
文件数上限 1000、源码总大小 100 MiB、目录深度 32；元数据单文件上限 1 MiB。
Git 对象和内容 ID 校验后，安全导出普通文件，拒绝路径穿越、大小写冲突、Windows 保留文件名、符号链接、子模块与 Git LFS 指针。
不以浅拉取保证只下载 app.json：首次浏览仍可能取得每项全部源码，新版本也会占用下载流量。

## 桌面端

调用 watcher-distribution app marketplace --provider gitee --jsonl；download/install 也使用相同 SDK 获取层。
不传 --catalog-url，不要求用户安装 Git/Python。冻结分发程序必须收集 dulwich/filelock 和 TLS 证书。
目录仅在完整校验成功后替换；失败展示上次内容并明确标记，首次无缓存失败明确报错。
保留并发刷新合并，不增加固定 60 秒冷却。不修改 Daemon 业务路由、国际版或桌面 OTA 的 TOS 配置。

## 交付门禁

配套 SDK 尚未提交时不能伪造 source lock。正式打包必须先提交 SDK、将 Desktop 锁更新到真实提交，再构建。
本地验证冻结二进制可直接使用当前脏源码，但不代表正式安装包已发布。
无 Git/Python PATH 测试与干净系统正式安装环境验收分别记录；任一未执行不得宣称已完成。
