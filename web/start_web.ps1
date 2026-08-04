# 一键启动网页版换色工具，并打开浏览器
$ErrorActionPreference = "Stop"
$p = Start-Process python -ArgumentList "app.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:8377/"
Write-Host "已启动 (PID $($p.Id)): http://127.0.0.1:8377/"
