# TTS 持续生成与单流播放

- 开始：2026-10-18 10:50:58 Asia/Shanghai；作者：Mr-KID-github / Codex；参与：root、tts_architecture_review、tts_continuous_sdk、tts_continuous_firmware。
- 用户反馈第一句后等待第二句，要求架构审查并优化持续生成队列。
- 状态：Application、SDK和固件实现、回归与本机部署完成；本轮按用户授权本地提交，不推送。

## 根因与方案
真实应用日志显示下一段合成常在前段播放完成4–5秒前就绪，但每段仍独立device.play：v1声明完整长度/SHA，每段begin、启动codec、4帧预缓冲、终态DMA drain/idle，SDK成功后又stop。应用playback_start不是扬声器首样点。因此仅扩大队列不能根治段间设备重启。
- 持续、有界TTS生产者：以PCM字节及段数控制预取，未满就继续生成；取消/失败等待全部任务，不跨轮播放。
- 新能力audio.stream.continuous.v1：显式mode=continuous、max_bytes，真实PCM按一个reply一个stream连续写入，唯一FIRST与LAST，整条回复结束才drain；旧v1校验不变，禁止伪造总长/SHA。
- SDK流句柄与Device channel credit保持；真实SHA增量计算，完成前设期望值后发送EOS，与固件完成报告核对。
- 旧固件降级合并已准备段，明确仍有流边界；新模式一次设备播放链路。区分preface与final耗时，前置回应后的工具/模型执行间隔不伪装成TTS可消除停顿。

## 验收
TDD覆盖顺序、预算/背压、提前持续生成、单stream、hash/EOS/超限、取消/失败/资源释放、旧v1兼容；相关Host与SDK回归；实际相同PCM基线与连续播放对照，检查设备事务数、耗时、终态与嘴型，不以应用日志代替声学无缝测量。

## 历史关联
部分替代[首段短切容量一预取](../2026-10-18_102322_Mr-KID-github_无Issue_Pi首段短切与先回应后执行/index.md)，保持其UTF8切分与取消合同；保持[语义先回应](../2026-10-18_103036_Mr-KID-github_无Issue_Pi按语义先回应再操作/index.md)播放屏障。

## TDD和设备验证
实际结果见下方过程与最终记录。SDK/固件各有专门档案，代码分别在对应子仓，不修改Daemon业务路由。

## 实现与TDD
- 新增tts_pipeline.py，将预算、持续生产、按序消费与transport解耦：8段/8 MiB，合成前预留4 MiB单段预算，消费结束才归还；连续设备只调用一次play_stream，旧设备首段即时、后续合并已ready PCM。
- 新队列8项初始Red为模块缺失，完成后Green；后补独立字节/段数预算与真实Session单stream/取消回归。字节测试初次误把已释放的预留差值当成永久占用，修正预期为总8字节预算下可保留3份2字节PCM；该测试错误不记作生产故障。
- 初次整组233pass/1fail即上述测试预期；修正后SDK＋Pi合并回归 **430 passed**，46.70秒，一项既有Starlette/AnyIO弃用警告。Node扩展12项和页面VM通过。
- SDK独立26项新continuous边界：先行8项缺API红、后续并发/跨轮身份、staging小包饥饿和正确SHA但drop>0分别真实红绿；详见SDK专档。SDK195→208定向检查及mypy5文件通过。
- 固件37相关Host通过，IDF6.0.2 BuildOnly及分区检查通过；实际AppOnly写入校验成功，镜像6184080字节、SHA256 f06ea3e023a2a6d1e7c99dc4351e56c39a661cfe06dd32af1283740add5cf2a9。保留NVS/agentcfg，不重新配置JoyInside。

## 设备验证进展
- 初次旧固件基线在第一段发生audio_send_failed，底层异常未捕获，不能计入性能对照。刷后同机v1与continuous使用相同403730字节PCM（8411ms）：v1 3 begin/3 complete耗时12511ms；continuous 1 begin/1 complete耗时9720ms，少2791ms约22.3%。这是此固定音频的控制耗时对照，不泛化所有回答或声学停顿。
- 实际豆包持续合成3段＋队列＋连续设备流：通过、1 complete、99/99帧、drop/timeout/gap均0；已生成第3段时第一段仍在上传/播放。
- 真实Pi最终回复＋豆包＋动作嘴型：通过，单stream97/97帧、drop0，嘴型PCM计数最高91，结束mouth_level=0；音频/动作资源释放确认。
- 播放中已观察played_frames>2后取消：没有complete伪报，owner释放，后续可重开。
- 动作联测仍见中途buffering，后句已ready，正在细化仅continuous的反馈补帧burst，不把队列有数据等同于设备绝不欠载。

## 2026-10-18 11:10:04 Asia/Shanghai｜Mr-KID-github / Codex｜最终回归与部署
- SDK连续流补帧由仅8包burst改为最多32包，仍受既有目标pending、free_frames、接收水位的在途扣减限制；初始4包及设备队列/起播门槛不改，旧v1仍8。新增两transport × q64/q16/free3/free0 八组边界，4例先Red后Green；全部相关SDK216通过。
- 最终Application＋SDK整组 **430 passed**，31.72秒；12扩展Node、网页VM、JS检查通过，5 SDK源文件mypy通过；固件相关37 Host及构建成功。此前430项不与216重复相加。
- 同PCM连续流重复结果9.720秒和11.134秒，v1基线12.511秒；重复有波动，不挑最快值当普遍加速比例。最终continuous仍单stream99/99帧、drop0，但同PCM这轮有中途buffering；网络波动不能声称已彻底消除。
- 最终Pi＋豆包＋动作嘴型复测单流103/103帧，drop0，嘴型PCM最高103，结束mouth_level0；设备路径10026ms、PCM419634bytes（模型文字/合成与前轮不同，不作等比例性能比较），这轮buffering均在首次真正播放前。播放中取消再次通过，owner释放。
- [设备对照证据](hardware-validation.json)仅保留控制/遥测与合成固定文本的数据，不纳入PCM、凭据、串口/个人URL、私有配置备份或临时脚本。
- 正式Application已恢复，新进程使用连续能力；网页connected、doubao、paused、error为空，音频/动作资源释放。当前待机供用户试听，未自动commit/push。

## 配套材料
- [SDK连续PCM接口实现](../2026-10-18_104940_Mr-KID-github_无Issue_SDK连续PCM播放/index.md)。
- [固件连续协议实现](../../../../../WatcheRobot_esp32/docs/development/2026-10-18/2026-10-18_105008_Mr-KID-github_无Issue_连续PCM播放协议/index.md)。
- [正式连续播放接口](../../../../docs/continuous-audio.md)与[示例README](../../../../examples/joyinside_pi/README.md)。

## 遗留边界
- 云端合成、Wi-Fi抖动和自然语句停顿仍可能产生等待；当前逐句PCM完整入队，未实现同句云端首包直播。
- 操作前回复后的模型/手机执行时间保留，不能提前合成未知操作结果。
- 本轮以设备完成与嘴型遥测验收，未声称声学0ms空隙、采样级同步或长期压测通过；SDK全量其他模块测试未执行。

## 2026-10-18 11:14:17 Asia/Shanghai｜Mr-KID-github / Codex｜提交前核对
- 用户明确要求将本次连续播放优化commit；核对当前19个SDK变更文件，均属于本任务，无额外业务修改；代码及真实TDD/设备证据一同纳入SDK提交。
- 配套固件提交：`618bdb2dc46e0ad626fee9ebf749878ab01b1e12`（codex/macos-joyinside-provision），提供audio.stream.continuous.v1显式mode/预算/FIRST/EOS合同。SDK按capability启用，旧固件继续降级，不修改Daemon透明路由。
- 验证复用最后源码对应430项合并回归、216项SDK专项、37项固件Host、mypy与Node/UI结果；源文件自上次验证未变化，本轮不重复测试、构建或烧录。所有计数按各执行范围记录，不累加为独立测试总量。
- 提交前git diff/新增文件/敏感资料扫描通过，没有凭据、个人绝对路径/端口、PCM制品、临时脚本、运行时日志或缓存。仅本地提交，无push/PR/合入。

## 后续变更

2026-10-18｜Mr-KID-github / Codex｜补充 [Pi流式文本提前合成](../2026-10-18_111939_Mr-KID-github_无Issue_Pi流式文本提前合成/index.md)：把TTS合成提前到Pi生成期间；原有单流播放、预算、最终settled及操作前播报屏障仍保留。


## 后续变更
2026-10-27｜KID / Codex｜[PCM配套合入](../../2026-10-27/2026-10-27_181300_KID_无Issue_PCM配套合入/index.md)：在最新目标分支整合并重新回归；原有硬件记录保留为历史。
