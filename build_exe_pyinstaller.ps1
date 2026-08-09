# 单文件打包（onefile）：产出单个 RecolorTool.exe，启动时自解压运行时，
# 不依赖旁边的 _internal 文件夹——从任何目录（包括临时解压目录）都能正常跑。
$ErrorActionPreference = "Stop"

$py = "python"  # 改成你自己的 Python 可执行文件路径

& $py -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "RecolorTool" `
  --add-data "web\templates;templates" `
  --add-data "web\static;static" `
  --paths "." `
  --collect-all fitz `
  --collect-all pikepdf `
  --hidden-import recolor `
  --exclude-module lxml `
  --exclude-module PIL._avif `
  --exclude-module PIL.ImageTk `
  --exclude-module PIL.ImageCms `
  --exclude-module gradio `
  --exclude-module spaces `
  --exclude-module pandas `
  --exclude-module fsspec `
  --exclude-module tqdm `
  --exclude-module yaml `
  --exclude-module tzdata `
  --exclude-module huggingface_hub `
  "web\app.py"

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host "dist\RecolorTool.exe" -ForegroundColor Green
