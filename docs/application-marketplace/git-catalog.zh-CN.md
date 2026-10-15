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
安装staging事务目录使用8位十六进制随机编号，独占创建并避开已有暂存、回收和回滚目录；旧16/32位目录仍兼容。
Windows 缓存与源码暂存/交付在内部使用扩展长度路径，覆盖重定向用户目录叠加事务目录超过 MAX_PATH 的情况；不改变公开源码路径身份。
Windows独立应用环境在创建后加入SDK生成的位置无关启动引导，只规范化该环境的绝对导入路径，避免长路径下标准库包被误识别；提升安装目录后继续有效，不修改系统Python。
本地缓存或写入失败通过 HubLocalIOError 归类为 internal_error，不再误归为源码 app_content_forbidden。
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


## 安装与卸载数字进度

安装事件携带 `completed_steps/total_steps`（12 个已定义检查点），到达下一阶段才算前一阶段完成；它不是耗时、依赖下载字节或预计剩余时间。源码导出另报 `downloaded_bytes/total_bytes`，总量未知时只显示已处理字节。
卸载先将指定应用根原子移到独占卸载暂存目录，统计并删除源码、独立环境与根目录项，通过 `completed_items/total_items` 报告实际完成数量；不会沿链接或 junction 删除外部目标。删除失败报告错误并尽力恢复剩余目录，以便检查/重试。成功前检查安装根与该暂存根均已消失。
结果中的 `trash_root` 保留兼容字段名，成功时该路径已删除，不再代表可恢复回收站；共享 runtime/cache 与历史回滚目录不属于本次卸载目标。
8 位随机事务 ID 使用原子 mkdir 独占预留，碰撞重试最多 16 次，避开 staging、trash 和 previous；不会覆盖已有目录。

运行环境准备时通过 `stage_completed/stage_total` 提供实际校验字节与发布检查点的阶段进度；桌面显示折算后的总体百分比。计数用于内部合同，界面不显示步骤数或字节数。总量未知的等待阶段保持忙碌动画和初始 0%，不按时间伪造前进。
