# 生成安装包：先确保 build_exe.ps1 已产出 dist\RecolorTool
# 需要 Inno Setup 6：winget install JRSoftware.InnoSetup  （或 choco install innosetup -y）
$ErrorActionPreference = "Stop"

if (-not (Test-Path "dist\RecolorTool\RecolorTool.exe")) {
  Write-Host "未找到 dist\RecolorTool\RecolorTool.exe，请先运行 .\build_exe.ps1" -ForegroundColor Red
  exit 1
}

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
  "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  Write-Host "未找到 Inno Setup 6。安装方式二选一：" -ForegroundColor Red
  Write-Host "  choco install innosetup -y"
  Write-Host "  winget install JRSoftware.InnoSetup"
  exit 1
}

& $iscc "RecolorTool.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "安装包完成：" -ForegroundColor Green
Get-ChildItem "dist-installer\*.exe" | ForEach-Object { Write-Host $_.FullName -ForegroundColor Green }
