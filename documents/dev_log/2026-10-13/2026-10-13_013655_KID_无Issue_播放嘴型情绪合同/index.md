# 播放嘴型情绪合同

- 创建2026-10-13 01:36:55 Asia/Shanghai；KID / Codex；分支codex/realtime-expression。
- 需求：实时语音实体眼形缺少情绪映射。SDK原set_audio_follow只有enabled；固件每帧强制中性，runtime.update不能可靠覆盖。
- 范围：公开set_audio_follow的可选emotion参数（0–6），表达同一PCM嘴型实例的眼形目标；v2能力门控、严格校验并保留v1兼容和清理所有权。Daemon保持内容无关透明路由，不修改管理接口。
- 配套：Application新增导演/设备适配；ESP32 audio-follow runtime及合同v2；分别在对应仓库保留测试和源码。
- TDD：先写非法值、能力缺失、有效情绪与关闭清理测试，再实现；尚未执行新行为验证。
- 状态：设计与TDD进行中，未提交、未部署。

## 2026-10-13｜KID / Codex｜实现与验证

- `set_audio_follow(enabled, *, emotion=None)`增加严格integer0–6与v2能力门禁，拒bool/fraction/disabled+emotion；省略保持v1。复用现有display清理owner，不增加Daemon业务分支。
- 有效请求先Red（旧方法unexpected keyword），实现后Green。部分非法测试旧unexpected-keyword也被预期异常匹配，只将其记为补充语义保护，不夸大Red。
- `python -m pytest tests/test_api.py -q`：150项通过。公开合同与target/submitted观测语义更新于[正式文档](../../../../docs/procedural-expressions.md)。
- Application运行环境使用此SDK源码，实体v2请求已收到ACK，四种情绪由固件资源快照报告；不以SDK ACK声明LCD呈现或舵机到位。
- 配套固件发现ready占位ID，改复用既有唯一身份服务；本仓SDK清理身份guard不再依赖所有机器共用身份。ready缓存更新仍非原子跨设备隔离。
- 任务验收完成，源码与本档案纳入本地提交；配套Application/ESP32/Client分别提交，不推送或合入。实体质量10/10为本轮软件范围，硬件限制另行保留。

## 后续变更
2026-10-18｜Mr-KID-github｜补充：[Pi动作表情与语音延迟](../../2026-10-18/2026-10-18_092500_Mr-KID-github_无Issue_Pi动作表情与语音延迟/index.md)，增加local_pcm能力说明和Application联动，旧RTC/情绪合同保持。
