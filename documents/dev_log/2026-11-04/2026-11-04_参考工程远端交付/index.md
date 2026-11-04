# 参考工程远端交付
作者：YuZhong0212；执行：Codex；2026-11-04 Asia/Shanghai。
用户授权上传参考工程，不做产品移植。配套ESP32分支为codex/special-expression-lifecycle-integration。
保留流程、文案、资源及SDK扩展；排除运行数据。现有代码整理不新增业务逻辑，TDD不适用，使用回归验证。系统Python缺依赖，改用配套环境安装pytest后相关测试通过。未刷机。
提交前复核：应用与SDK相关回归全部通过；固件表情35、协议4、音频跟随1、相机交接2项通过。配套固件提交e48228ac。暂存内容排除个人密钥、artifacts、虚拟环境、日志和构建缓存，git diff --cached --check通过。历史记录保留原始状态，本次作为参考交付补充，不宣称产品移植完成。
