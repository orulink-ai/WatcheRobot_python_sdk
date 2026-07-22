# Screen text / 屏幕文字

`robot.display` provides two persistent screen states. It does not create a `Job`; `show_text()` and `clear()` return
`None` after the robot has committed the LVGL update.

`robot.display` 提供两种持久显示状态，不创建 `Job`。机器人完成 LVGL 界面提交并 ACK 后，`show_text()` 和
`clear()` 返回 `None`。

```python
robot.display.show_text(
    "你好，WatcheRobot！",
    mode="page",
    size=24,
    color="#FFFFFF",
    background="#000000",
    align="center",
    wrap=True,
)
```

- `page` displays an opaque full-screen page and cancels the active Behavior or animation. A new visual Job replaces
  it. / `page` 显示全屏不透明文字页，并取消当前 Behavior 或动画；新的视觉 Job 会替换文字页。
- `overlay` requires an active Behavior or animation and displays a top panel. Clearing it does not stop the visual
  Job. / `overlay` 仅在 Behavior 或动画运行时可用；清除叠字不会停止视觉 Job。
- Sizes are 16, 24, and 32 px. Colors use `#RRGGBB`; alignment is left, center, or right. / 字号支持 16、24、32；
  颜色使用 `#RRGGBB`，支持左、中、右对齐。
- Text is limited to 512 UTF-8 bytes and 128 Unicode characters. Newline is the only accepted control character. /
  文本最多 512 个 UTF-8 字节和 128 个 Unicode 字符；控制字符只允许换行。
- Simplified Chinese requires `display.text.zh_cn`. A missing or damaged SD font keeps English available but causes
  Chinese requests to fail explicitly. / 简体中文需要 `display.text.zh_cn`；字体缺失或损坏时英文仍可用，中文会明确失败。
