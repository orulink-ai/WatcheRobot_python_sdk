#!/usr/bin/env bash
# DSH TTS Speaker — 桥接脚本 (macOS / Linux)
# 将文本发送到 WatcheRobot TTS 播报服务
# 用法: ./speak.sh "要播报的文字"
#       echo "文字" | ./speak.sh

set -euo pipefail

HOST="${DSHTTS_HOST:-127.0.0.1}"
PORT="${DSHTTS_PORT:-9876}"
VOICE="${DSHTTS_VOICE:-zh-CN-XiaoxiaoNeural}"
RATE="${DSHTTS_RATE:-+0%}"

# 收集所有输入
if [ $# -gt 0 ]; then
    TEXT="$*"
elif [ ! -t 0 ]; then
    TEXT=$(cat)
else
    echo "用法: speak.sh '要播报的文字'  或  echo '文字' | speak.sh" >&2
    exit 1
fi

TEXT=$(echo "$TEXT" | tr -s ' ' | xargs)

if [ -z "$TEXT" ]; then
    echo "错误: 文本为空" >&2
    exit 1
fi

# 构建 JSON
JSON=$(printf '{"text":"%s","voice":"%s","rate":"%s"}' \
    "$(echo "$TEXT" | sed 's/"/\\"/g')" \
    "$VOICE" \
    "$RATE")

# 发送请求
RESPONSE=$(curl -s -X POST "http://${HOST}:${PORT}/speak" \
    -H "Content-Type: application/json" \
    -d "$JSON" \
    --max-time 60 2>&1) || {
    echo "❌ 无法连接到 TTS 服务 (http://${HOST}:${PORT})" >&2
    echo "请先启动 WatcheRobot Application: conda activate watcherobot && cd dshtts_speaker && watcherobot app run" >&2
    exit 1
}

if echo "$RESPONSE" | grep -q '"ok":true'; then
    echo "✅ 播报完成"
else
    ERROR=$(echo "$RESPONSE" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "❌ 播报失败: ${ERROR:-未知错误}" >&2
    exit 1
fi