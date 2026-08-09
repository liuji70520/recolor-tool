# Nuitka 打包 web 版换色工具为 Windows exe
# 用法：在 recolor-tool 根目录执行  .\build_exe.ps1
$ErrorActionPreference = "Stop"

$py = "python"  # 改成你自己的 Python 可执行文件路径
$out = "dist-nuitka"

& $py -m nuitka `
  --standalone `
  --mingw64 `
  --output-dir=$out `
  --product-name="一键换色" `
  --file-description="SVG / PDF / 图片一键换色工具" `
  --windows-console-mode=disable `
  --include-module=recolor `
  --include-plugin-directory="." `
  --include-data-dir="web\templates=templates" `
  --include-data-dir="web\static=static" `
  "web\app.py"

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host "$out\app\app.exe" -ForegroundColor Green
