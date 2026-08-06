# 一键启动网页版换色工具（app.py 启动后会自动打开浏览器）
$ErrorActionPreference = "Stop"
$p = Start-Process python -ArgumentList "app.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Host "已启动 (PID $($p.Id))，浏览器会自动打开；若端口被占用会自动换空闲端口。"
