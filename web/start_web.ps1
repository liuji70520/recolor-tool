# 开发用：启动本地网页版换色工具
# 默认弹独立窗口（pywebview + WebView2）；设 $env:RECOLOR_UI="browser" 改用浏览器调试。
$ErrorActionPreference = "Stop"
python "$PSScriptRoot\app.py"
