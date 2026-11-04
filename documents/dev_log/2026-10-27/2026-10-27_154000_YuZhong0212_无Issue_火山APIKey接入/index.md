# 火山 API Key 接入
作者：YuZhong0212；执行：Codex。支持新式 X-Api-Key，保留旧鉴权，密钥仅本地保存、接口脱敏。已实测 TTS 成功；继续测试识别与控制台集成。

验证：pytest 未安装，直接执行两项测试函数；Red 为新字段被拒绝，Green 为鉴权、脱敏、保留/清除和旧鉴权两项通过。实际 TTS 生成94662字节 PCM；重采样到16k识别返回HTTP403。部署四个修改文件到本机应用，管理接口重启成功，网页200，配置API密钥脱敏且已保存。暂关闭麦克风，保留TTS和文本输入。未刷固件、未提交。

2026-10-27 更正：对照新版官方 API Key / ASR 文档，同一 Key 使用 volc.seedasr.sauc.duration 实测识别成功（你好呀，很高兴认识你。）。旧 volc.bigasr.sauc.duration 的403不能证明账号未开通服务。控制台配置接口已保存新版资源ID并恢复voice_enabled=true；不改变旧鉴权用户默认资源。
