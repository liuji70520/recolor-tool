# PyInstaller 打包 web 版换色工具为 Windows exe（onedir：exe + _internal 文件夹）
# onedir 无自解压、启动快；配置都在 RecolorTool.spec 里。
# 用法：在 recolor-tool 根目录执行  .\build_exe.ps1   （需 pip install pyinstaller）
$ErrorActionPreference = "Stop"

& python -m PyInstaller --noconfirm --clean RecolorTool.spec

# $ErrorActionPreference 管不住原生命令的退出码，这里显式检查
if ($LASTEXITCODE -ne 0) {
  Write-Host "PyInstaller 构建失败（exit $LASTEXITCODE）" -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host "dist\RecolorTool\RecolorTool.exe" -ForegroundColor Green
