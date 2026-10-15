# 国内商店实际安装 Windows 长路径失败

- 创建：2026-10-15 Asia/Shanghai；作者：wugui / Codex。
- 分支：codex/tos-marketplace-20261014，基于SDK312466b。
- 状态：本地修复、实际安装和回归通过，未提交、未推送、未更新来源锁或PR。

## 背景与根因
用户实际客户端安装说话动作实验室0.2.1失败，UI显示app_content_forbidden，操作编号ed1e9b3c-7f3b-4732-bfd6-b09aac742238。
按编号未找到持久化安装日志，不能声称该编号已从日志直接关联；改用相同SDK开发入口、实际应用目录和桌面字节码环境复现。
源码SDK0.1.12/Dulwich1.2.17，开发入口是workspace托管venv的watcher-distribution；旧资源目录runtime-build.json并非本次源码分发入口。
读取目录、缓存和独立短目录下载成功；完整安装在导出web/assets/joyinside-face-B7uaxS3a.wasm时FileNotFoundError。
实际AppData路径经过OpenAI.Codex包的LocalCache/Roaming解析，叠加staging/事务ID/candidate/临时下载/snapshot使文件路径超过普通Windows MAX_PATH。
GiteeGitRepository把OSError误归为HubInvalidResponse，再被映射为app_content_forbidden；实际是本地写入失败，不是Gitee限流或源码违规。
之前完整安装验证使用D盘短路径，未覆盖实际桌面存储路径；本次明确更正这一验收缺口。

## TDD和实现
- Red：Windows长安装路径导出失败；本地写入异常分类测试因HubLocalIOError不存在失败，2项失败真实记录。
- 在runtime.windows_paths公开filesystem_path复用现有扩展路径规则；仅内部I/O采用Windows扩展路径，不更改源码路径校验及公开下载目标表示。
- Git缓存/导出与下载暂存/校验/交付使用长路径；继续拒绝符号链接和非法源码路径。
- HubLocalIOError区分本地I/O故障，下载层映射internal_error，保留无敏感路径的公开消息。
- 新增Git长路径导出、本地写入失败、完整暂存验证/交付长资源和公开错误分类测试。
- 目标Git+download测试34项通过；分发全量和原生路径回归共434项通过，1个既有弃用警告。

## 实际验证
同一实际用户store、SDK0.1.12随包Python/uv，完整安装0.2.1成功，commit为a425b35168a27e77f99224062feb91aeb464a008。
workspace开发入口app list返回status=installed；不是只验证临时目录，也未通过放宽源码校验绕过失败。
本次实际安装由诊断入口调用同一SDK实现完成；没有替用户启动应用，也未完成截图UI点击重试验收。
原始临时诊断、失败trace和成功安装日志位于workspace _local_artifacts/tos-marketplace/desktop-install-*，不提交个人绝对路径或日志。

## 关联与边界
延续并修正 [Dulwich商店读取验收](../2026-10-15_090000_wugui_无Issue_Dulwich商店读取/index.md)。
SDK代码更新后，当前源码开发入口新进程直接加载；正式冻结产物、来源锁和正式安装包尚未更新。
本次未更改Daemon路由、用户仓库源码、TOS权限或已安装应用启动状态。干净系统和macOS验收仍未执行。

## 最终核对
- 失败trace记录的实际.wasm路径长度为261字符，定位不是仅凭截图猜测。
- 同一用户store的完整SDK安装成功，workspace watcher-distribution app list返回installed、0.2.1和审核固定提交。
- ruff检查全部变更代码与测试通过，4个变更源码mypy --follow-imports=skip通过，git diff --check通过。
- 修改仅在SDK功能分支本地，桌面业务代码无需改动。未提交、未推送、未更新正式冻结产物和来源锁。

## 2026-10-15 后续要求：缩短事务编号并保留长路径支持

用户要求缩短32位安装事务目录。改用secrets.token_hex(8)生成16位十六进制编号（64位随机量），通过mkdir(exist_ok=False)独占占位。
分配时同时避开staging同名目录、失败回收trash同名目录和trash/<id>-previous回滚目录；碰撞有限重试16次，耗尽时明确内部错误，不覆盖或删除既有文件。
保持UUID旧目录兼容；不修改卸载目录命名，不缩减原有安装验证、事务提交、回滚和取消清理边界。
长路径I/O修复继续保留，不以编号缩短替代Windows扩展路径支持。
TDD先运行3个缺失辅助函数/依赖的失败测试，实现后3项通过，覆盖短编号占位、暂存/回收/回滚碰撞和重试耗尽保留原数据。

### 加深目录端到端验证与Python导入修复
- 新增独立应用store验证16位事务编号，不覆盖此前用户实际store中已经安装成功的0.2.1。
- 首次加深目录安装通过源码下载，但在importing_watcherobot失败；真实stderr为concurrent.futures缺少FIRST_COMPLETED。其标准库包__init__.py在普通Windows路径下超出MAX_PATH，被误识别为namespace包。
- 单独测试显式扩展lpApplicationName以及pyvenv.cfg home路径都未解决；只记录为诊断尝试，没有将其写入实现。
- SDK在自有隔离应用环境创建后、依赖安装前生成位置无关的_w.pth，仅规范化Windows绝对导入根，保留相对路径和已有扩展路径，兼容UNC。
- 该启动文件只存在于SDK管理的应用venv，不修改系统Python，也不放宽应用源码发布规则；不引用绝对安装位置，可随候选目录提升到正式目录。
- TDD先运行启动引导测试因缺少helper失败，实现后通过；已有安装测试增加启动引导随提升保留的检查。
- 加深目录完整安装最终成功，日志确认事务ID长度16，版本0.2.1、固定提交正确、SDK0.1.12。
- 安装目录提升后再次用其Python导入concurrent.futures和watcherobot成功，输出FIRST_COMPLETED、0.1.12。
- 所有诊断日志留在workspace临时制品目录，不纳入Git；本次未提交、未推送或更新PR。

### 最终回归
- 分发全量与原有Windows原生路径测试：438 passed，1个既有弃用警告，43.12秒。
- 5个变更源码mypy定向检查、全部变更代码与测试ruff、git diff --check均通过。
- 完成16位短事务目录、碰撞保护、源码长路径I/O和隔离Python启动路径修复；代码仍为本地未提交状态。

## 2026-10-15 提交授权与配套交付｜wugui / Codex

用户授权将本次修复提交并推送SDK功能分支，更新桌面SDK来源锁并提交推送，不创建PR。
按本条授权覆盖前述未授权提交状态；提交包含长路径I/O、16位事务目录、隔离环境启动引导、错误分类、测试及文档，不含临时日志和个人路径。
配套桌面端分支codex/tos-marketplace-20261014（目标dev），将锁定本次SDK真实提交；SDK包版本保持0.1.12，不自行发布新包或客户端。
验证沿用最终代码已执行的438项通过、mypy定向、ruff和加深目录真实安装/提升后导入记录；干净系统和macOS正式安装器验收仍未执行。


## 后续变更：2026-10-15 安装结果与卸载进度

[后续档案](../2026-10-15_105949_wugui_无Issue_安装结果与卸载进度/index.md)：事务目录由 16 位改为 8 位，新增可测量进度与实际卸载删除；原长路径规则保留。
