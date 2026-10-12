# 对讲保活与拍照体验

## 基本信息

- 创建：2026-10-12T13:20:43+08:00，Asia/Shanghai；同步最新 main 后补建，早期记录为追溯。
- 建档作者：KID（当前有效 Git 作者）；参与者：用户、Codex。
- 需求：局域网原声对讲与拍照独立应用，排查自动断开、实机重复测试、拍照预计倒计时，提交并合并 PR 后切回最新主分支。无 Issue。
- 分支：codex/robot-intercom；[SDK PR #143](https://github.com/orulink-ai/WatcheRobot_python_sdk/pull/143)。
- 状态：功能及实机验证完成，最终修复待 CI、审查与合入。Gitee 商店合入被现有凭据权限拒绝。
- 边界：Application 使用注入通道，保持 Daemon 内容无关路由；未改变固件曝光预热，没有新增交付系统脚本。

## 调查与决策

页面 JavaScript 保活暂停超过固件 5 秒期限可复现 heartbeat_timeout。设备保活交给 Application 后端，本机原生 presence 判断页面存在；协商始终有 30 秒期限，presence 不延长它。PTL 相机预热约 1.2 秒，照片传回显示约 2 秒；点击立即请求，以预计倒计时解释等待，不延迟快门或取消预热。

presence 注册由线程执行，协程取消不会停止线程，必须保留最终 token 并安排清理；应用持有并等待清理任务，重复取消不遗失收尾。拍照任务绑定通话身份，停止、失败或替换通话中止本机等待，显示前再次检查身份；不把 HTTP abort 表述为设备快门已取消。

## 材料与历史关联

- [正式验收](../../../../docs/robot-intercom-acceptance.zh-CN.md)、[使用说明](../../../../examples/robot_intercom/README.md)。
- [服务回归](../../../../tests/test_robot_intercom.py)、[拍照回归](../../../../tests/js/test_robot_intercom_photo.mjs)、[RTC 合同](../../../../tests/application/test_rtc.py)。
- [此前源码补交](../2026-10-12_103021_KID_无Issue_补交本地最新代码/index.md)：本次补充其对讲功能，保留 #142 的请求期限、取消缓存和设备门禁；原档案已追加反向链接，历史正文不覆盖。

## 变更与验证

### 2026-10-12T13:20:43+08:00｜KID / Codex｜追溯与提交前记录

- b941189 修复页面暂停保活，d005e99 增加拍照预计倒计时；原验收包含连续 600 秒实体通话及随后 20 轮通话。此处追溯正式验收，不宣称本次重新执行 20 轮。
- 8126ec4 非快进同步 #142，保留双方回归并增加两项 presence/协商截止组合测试；Python 74 项、浏览器 49 项通过，实机拍照 1802 ms，正常释放。
- b9dc611 修正 presence 取消与拍照跨通话结果。拍照身份用例修改前失败（旧实现返回成功）、修正后通过。presence 首次 Red 包含未实现清理集合这一辅助字段的错误，不能把该输出冒充独立资源泄漏断言；线程完成后丢失 token 的缺口由生命周期核对确认。注册、握手、重复取消清理用例最终通过。
- ApplicationRtc.start 清空旧事件并过滤旧 session，经实际 SDK 对象回归核验；生产 SDK 合同未改，属于事后补测。
- 修正后 Python 78 项、浏览器 51 项通过。实机拍照中结束，旧照片不显示；新通话等待 4 秒仍不显示旧结果，新拍照 1872 ms、双向包 286/283，正常释放。
- 最新 Red：presence 存在的 connecting 没有后端 ping；慢设备查询跨过截止后仍保留 RTC，两项失败。Green：后端保活覆盖协商阶段，并在 ping 前后复查期限和身份；同一回归通过，相关 Python 共 80 项通过。浏览器生产逻辑未再改变，前次 51 项通过仍对应最终逻辑。
- 执行命令：`python -m pytest tests/test_robot_intercom.py tests/test_examples.py tests/application/test_rtc.py -q`；浏览器执行对讲、拍照、采集、延迟、恢复、播放和 RNNoise 模块测试。Windows、当前源码 SDK 与真实设备；精确实机阶段和限制见正式验收。
- 本机原始日志、照片、音频、临时验收脚本不提交；未新增 Daemon 路由旁路。

## 遗留与收尾

### 2026-10-12 Asia/Shanghai｜KID / Codex｜最终分发快照

协商保活修正 378c2da 已推送 SDK PR #143。正式分发接口上传并验证最终
[应用快照](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.robot_intercom/tree/496c7c9f7ddc61d92fc18486ddd7fc6e834e9e84)，提交
[商店 #16](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/16)，返回 pending。
原 #15 是已核对身份和 SHA 的本账号旧请求，关闭以替换过时快照；不重复尝试
已被权限拒绝的合并，不切换账号。源码和商店各自保留准确状态；最终 SDK CI
与复审尚在进行，未提前标记合入。

有限观察有少量队列丢帧、一次过期上行帧丢弃、削顶和内存余量紧，不宣称听感、零丢帧或长期无泄漏已验收。商店 #15 合入被现有 Gitee 凭据拒绝，未切换账号或绕过权限；最终源码需替换旧待审快照。SDK 使用 merge commit，合入后核对两个 parent，Workspace 单独更新 gitlink。
