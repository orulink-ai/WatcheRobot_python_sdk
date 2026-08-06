# Application 发布前质量门禁（待实施）

> 状态：已记录的发布问题与后续实现清单；不是当前 SDK 已经执行的规则。
>
> 目标：在 `watcherobot app check` 和 `watcherobot app publish` 阶段尽早提示或拦截问题，避免应用进入广场后才能在用户机器上暴露安装或启动失败。

## 1. 已确认的问题

### 1.1 `app.json` 依赖与实际代码/环境文件不一致

安装器只以 `app.json.dependencies` 为准创建 Application 私有 `.venv`；它不会读取或执行 `requirements.txt`、`pyproject.toml`、`uv.lock`、`poetry.lock` 等开发环境文件。

已发生的示例：

- Application `com.orulink.watcherobot_sdk_demo` 的 `app.json` 写为 `"dependencies": []`；
- 同一源码中的 `requirements.txt` 列出了 `Pillow>=10`、`numpy>=1.26`、`opencv-python>=4.8` 等依赖；
- `app.py` 导入 `PIL`，因此安装能完成，但启动时以 `ModuleNotFoundError: No module named 'PIL'` 退出。

开发者当前必须手动把运行需要的包写入 `app.json`，例如：

```json
"dependencies": [
  "websockets>=12,<16",
  "Pillow>=10",
  "numpy>=1.26",
  "opencv-python>=4.8"
]
```

`requirements.txt` 等文件可以保留给开发环境使用，但不得被视为发布安装的依赖来源。

### 1.2 Python 环境/运行时目录被误上传到 Application Space

此前一个 Application 快照曾包含 `.portable-smoke/runtime/Lib/site-packages/...`。这类本机环境内容导致快照有约 2509 个文件、百 MB 级下载量，并显著增加 Hugging Face 的网络重试、限流和安装失败风险。应用源码快照不应携带 Python 可执行文件、`.venv` 或 `site-packages`。

当前源码选择器已经排除常见的 `.venv`、`venv`、缓存、VCS、凭据和 `pyvenv.cfg`。但类似 `.portable-smoke` 的自定义环境目录还没有独立的发布前报错，因此仍需要补齐门禁。

## 2. 后续门禁设计

### 2.1 依赖声明检查

`app check` 与 `app publish` 复用同一检查，按下列优先级执行：

1. 始终校验 `app.json.dependencies` 是有效的 Python requirement 字符串；
2. 若发现 `requirements.txt`、`pyproject.toml`、`uv.lock`、`poetry.lock` 或 `Pipfile.lock`，明确提示：这些文件不会参与安装，安装仅使用 `app.json`；
3. 能可靠解析的 `requirements.txt` 与 `app.json.dependencies` 不一致时，输出逐项差异；
4. 首版可先作为英文警告并要求开发者确认；在广场正式开放第三方发布前，再决定是否升级为阻断错误。

建议的英文提示：

```text
Application dependencies differ from requirements.txt.
Only app.json dependencies are installed for published Applications.
Add the missing requirements to app.json before publishing.
```

不建议通过静态扫描 `import` 自动推断完整依赖：动态导入、可选功能和标准库同名包会造成误报。静态扫描可以作为辅助提示，但不能替代开发者维护 `app.json`。

### 2.2 环境产物检查

在发布文件集合生成后、网络上传前检查候选文件路径。以下内容应直接阻断发布，并给出路径：

- 常规虚拟环境：`.venv/`、`venv/`、`env/`、`pyvenv.cfg`；
- Python 安装产物：任意 `site-packages/`、Python 可执行文件及其标准库目录；
- 已知测试运行时：`.portable-smoke/`；
- 缓存和下载元数据：`__pycache__/`、`.cache/`、Hugging Face 本地下载缓存。

建议的英文错误：

```text
Application source must not include a Python environment or runtime artifact: <path>
Remove it or add it to .wappignore, then run app check again.
```

检查应只针对“即将上传的文件集合”，不能因为开发者本地存在被 `.wappignore` 正确排除的环境目录而拒绝整个项目。

## 3. 验收用例

实现后至少覆盖：

1. `requirements.txt` 有 `Pillow>=10`、`app.json` 缺失时，`check` 和 `publish` 都显示差异；
2. `app.json` 完整声明依赖时，不因保留开发用 `requirements.txt` 而失败；
3. `.portable-smoke/runtime/Lib/site-packages/...` 在上传集合中时被阻断，且不发生网络调用；
4. 本地 `.venv` 已由 `.wappignore` 排除时，`check` 通过且上传集合不包含它；
5. JSONL 保持稳定错误码、路径字段和英文辅助文案，Desktop 可直接展示。

## 4. 待确认的产品决策

- `requirements.txt` 与 `app.json` 不一致：首版只提醒，还是立即阻断发布？
- 是否要求每个发布 App 保留一种可解析的开发环境文件，还是只要求 `app.json`？
- 哪些大型模型、音频或视觉资源属于应用有效资源，不能被“环境产物”规则误拦截？

在上述决定完成前，`app.json` 是唯一发布安装依赖清单；开发者应在提交广场审核前执行 `watcherobot app check <application-directory>` 并人工核对上传文件集合。
