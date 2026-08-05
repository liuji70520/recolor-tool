# PyInstaller 打包 web 版为 exe（onedir 模式，规避杀软误报与启动慢）
$ErrorActionPreference = "Stop"

$py = "D:\envs\py312\python.exe"

& $py -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --name "RecolorTool" `
  --add-data "web\templates;templates" `
  --add-data "web\static;static" `
  --paths "D:\vibe-coding\recolor-tool" `
  --collect-all fitz `
  --collect-all pikepdf `
  --hidden-import recolor `
  "web\app.py"

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host "dist\RecolorTool\RecolorTool.exe" -ForegroundColor Green
