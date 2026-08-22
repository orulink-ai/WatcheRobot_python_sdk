# DSH TTS Speaker — 桥接脚本
# 将文本发送到 WatcheRobot TTS 播报服务
# 用法: .\speak.ps1 "要播报的文字"
#       .\speak.ps1 -Text "要播报的文字" -Voice "zh-CN-YunxiNeural"
#       Get-Content some.txt | .\speak.ps1

param(
    [Parameter(ValueFromPipeline = $true, Position = 0)]
    [string]$Text,

    [string]$Voice = $env:DSHTTS_VOICE,
    [string]$Rate = $env:DSHTTS_RATE,
    [int]$Port = 9876,
    [string]$Host = "127.0.0.1"
)

begin {
    $Text = ""
}

process {
    $Text += $_ + " "
}

end {
    $Text = $Text.Trim()

    if ([string]::IsNullOrEmpty($Text)) {
        Write-Error "用法: speak.ps1 '要播报的文字'  或  echo '文字' | speak.ps1"
        exit 1
    }

    $body = @{ text = $Text }
    if ($Voice) { $body.voice = $Voice }
    if ($Rate) { $body.rate = $Rate }

    $json = $body | ConvertTo-Json -Compress

    try {
        $response = Invoke-RestMethod `
            -Uri "http://${Host}:${Port}/speak" `
            -Method Post `
            -Body $json `
            -ContentType "application/json" `
            -TimeoutSec 60

        if ($response.ok) {
            Write-Host "✅ 播报完成: $($response.text.Substring(0, [Math]::Min(50, $response.text.Length)))..." -ForegroundColor Green
        } else {
            Write-Error "❌ 播报失败: $($response.error)"
            exit 1
        }
    } catch {
        Write-Error "❌ 无法连接到 TTS 服务 (http://${Host}:${Port}): $_"
        Write-Host "请先启动 WatcheRobot Application: conda activate watcherobot && cd dshtts_speaker && watcherobot app run"
        exit 1
    }
}
