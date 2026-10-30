# 固件目录初始化

维护者通过 PR 将 firmware-list.json 加入 HF Dataset `Orulink/watcherobot-app-store` 和 Gitee `orulink-sz/watcherobot-app-store` 根目录，仅在文件不存在时初始化，禁止空模板覆盖现有内容。

所有应用继续使用唯一 app-list.json。应用依赖是现有 app.json 的可选 requires_firmware 字段；不新增带 v2 的应用目录，不强制升级 schema。

先收录固件，再发布引用它的应用。此次仅提供本地模板，没有写入生产目录。完整合同见[实施文档](../firmware-implementation.zh-CN.md)。

平台目录说明： [Hugging Face](huggingface/README.md)、[Gitee](gitee/README.md)。Gitee 结构已同步；HF 对应 PR #56 待维护者合入。空固件索引不代表已有固件发布。
