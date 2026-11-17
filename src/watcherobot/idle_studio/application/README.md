# Watcher 行为工作台

在 Watcher Desktop 的本地 Application 中导入此目录并启动，应用自动打开浏览器。停止或切换 Application 会关闭网页后台；浏览器关闭不会保存未提交草稿。首版支持 Windows，SDK 0.1.14 至 0.2 之前版本。

将设备切换到 JoyInside / AI Chat，使用 USB 数据线连接，选择串口并读取。需要配套固件分支 `codex/idle-studio-firmware-20261117` 的 `joy_idle_v1`、`joy_idle_pools_v1`、`idle_catalog_v1` 与 SD 维护能力；普通旧固件仅能读取它实际支持的内容，不会展示虚构默认配置。连接前关闭其他串口监视器、烧录器和维护页面。应用不自动烧录，不修改 JoyInside 密钥。

调整日常待机 / 趣味表现的分组百分比，以及组内行为百分比、表情、动作和声音。三维预览使用当前草稿；只有点击“保存到 Watcher”并回读成功才完成设备更新。恢复默认先生成草稿，需再次保存；不会删除自定义素材文件。

官方预览首次通过 HTTPS 下载公开的 v0.0.10 表情资源并进行 SHA256 校验，无需 GitHub 账号或 CLI。后续使用用户缓存；断网不影响已经缓存的素材。按摘要优先匹配，自定义或不匹配资源可能无法官方预览。声音通过电脑播放，三维动作是视觉模拟。

后台只监听本机随机端口，不对局域网开放；USB 使用 SDK 的 SD 维护协议。应用通过 Daemon 注入的 ApplicationContext 管理生命周期，忽略不支持的 Desktop 业务帧，不新增设备业务旁路。

此目录由 SDK 的 `idle_studio.marketplace.export_application` 生成，编辑器唯一维护源在 SDK；请修改源后重新导出。包含的 Three.js 遵循 `idle_studio/web/vendor/THREE-LICENSE.txt`；Watcher 模型与官方表情为产品资源，归各自权利人所有，来源见 vendor/README.md。
