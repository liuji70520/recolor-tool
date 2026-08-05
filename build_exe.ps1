# Nuitka 打包 web 版换色工具为 Windows exe
# 用法：在 recolor-tool 根目录执行  .\build_exe.ps1
$ErrorActionPreference = "Stop"

$py = "D:\envs\py312\python.exe"
$out = "D:\vibe-coding\recolor-tool\dist-nuitka"

& $py -m nuitka `
  --standalone `
  --mingw64 `
  --output-dir=$out `
  --product-name="一键换色" `
  --file-description="SVG / PDF / 图片一键换色工具" `
  --windows-console-mode=disable `
  --include-module=recolor `
  --include-plugin-directory="D:\vibe-coding\recolor-tool" `
  --include-data-dir="D:\vibe-coding\recolor-tool\web\templates=templates" `
  --include-data-dir="D:\vibe-coding\recolor-tool\web\static=static" `
  "D:\vibe-coding\recolor-tool\web\app.py"

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host "$out\app\app.exe" -ForegroundColor Green
