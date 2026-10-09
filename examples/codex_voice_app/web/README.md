# Codex Voice Lab 前端

Vite + React + TypeScript + assistant-ui；ExternalStoreRuntime 展示 Python
Application 推送的真实转写。通过同源 `/api/session` 控制启动、静音、
停止和录音回放，不持有模型凭证。

```text
npm ci
npm run test
npm run lint
npm run build
```

完整运行方式见 [Application 说明](../README.md)。构建后由 managed
Application 同源提供 `dist` 和业务接口。`npm run dev` 仅用于页面开发，
尚未配置动态 Application 端口代理，不能独立接通机器人。
