# WatcherRobot 应用与固件广场

本仓库维护 gitee 平台经审核的应用与固件目录。应用和固件分别发布、独立展示，通过可选依赖关联。国内外使用同一逻辑结构，各自审核维护，不自动复制第三方内容。

```text
watcherobot-app-store/
├── README.md
├── app-list.json        # 全部应用，保留原字段和固定 commit
└── firmware-list.json   # 固件及其版本，不与应用混成一个条目
```

## 应用目录

app-list.json 继续使用原 JSON 数组，每项只包含 repo_id 和完整的 40 位 commit。应用源码保留在作者自己的公开仓库。无需 application-list-v2.json，也不按客户端版本拆目录。

应用项目 app.json 可以选填 requires_firmware，保留原 schema 2/3 与 SDK 依赖锁。未声明时新客户端继续读取已有字段，不猜测固件要求；声明错误则拒绝。依赖示例：

```json
{"requires_firmware":{"mode":"required","firmware_id":"com.example.motion","version_spec":">=1.0.0,<2.0.0","recommended_version":"1.0.0","tested_versions":["1.0.0"]}}
```

官方或自定义固件都通过同来源固件 ID、兼容范围和推荐版本引用；不把固件 ZIP 重复放进应用仓库，也不读取上传者当前连接设备的版本来推定依赖。纯软件应用可显式声明 {"mode":"not_required"}。

## 固件目录

firmware-list.json 顶层为 {"schema_version":1,"firmwares":[]}。每个版本记录 firmware_id、version、repo_id、完整 commit、size、sha256、withdrawn；可包含名称、作者、更新说明、SKU、硬件修订、publisher_kind 和 ota_supported。

HF 条目使用 path 指向固定版本 ZIP；Gitee 条目使用 asset_id 和 download_url 绑定 Release 附件。新版本追加，同版本不可覆盖。维护者撤回版本使用 withdrawn，不借机改写旧版本哈希。固件卡片按 ID 聚合，版本在详情中选择。

作者固件产物结构：

```text
Hugging Face Dataset 作者/WatcherRobot-Firmware-固件ID
  versions/1.0.0/firmware.zip
  versions/1.0.0/release.json

Gitee 作者/WatcherRobot-Firmware-固件ID
  versions/1.0.0/manifest.json
  Release v1.0.0 @ 固定 commit → firmware.zip 附件
```

桌面下载编译好的固件，不临时编译源码。ZIP 根目录包含 firmware.json、flash_args.txt 和已声明的二进制分段；包声明 ESP32-S3、版本、机型、硬件修订、闪存容量、分段路径/偏移/大小/SHA-256。不执行包内脚本。首次新增空目录只表示结构就绪，不代表已经发布固件。

## 发布与体验流程

作者上传公开产物 → 全新匿名下载校验 → 对相应目录提交审核请求 → 维护者审核合入。先收录固件，再收录引用它的应用。app-list 只审核应用固定源码，firmware-list 另外审核产物、来源、硬件和版本信息。

体验者可仅安装应用、仅下载并烧录固件，或联动安装应用与下载配套固件。USB 设备选择和最终写入确认由桌面端完成，复用设置页烧录执行器；SDK 不承担烧录。未连接 USB 时允许下载、禁止写入。

第三方固件通过 USB 更新；官方固件的 OTA 资格由审核信息和设备身份判定，不信任作者自称“官方”。历史烧录记录与 Wi-Fi 实时版本分别显示，不将写入成功当成运行版本回读成功。

## 审核与安全

维护者核对公开可读、固定 commit、版本、大小、哈希、包结构、机型和实际验证结果。SHA-256 是完整性校验，不等同于发布者数字签名或 Secure Boot。收录不代表安全担保；不得上传 Token、密码或个人配置。

SDK 和桌面端支持以配套功能版本为准；新增目录不会使旧桌面端具备固件能力。应用清单的已有字段与已收录记录保留。
