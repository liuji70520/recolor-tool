# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('web\\templates', 'templates'), ('web\\static', 'static')]
binaries = []
hiddenimports = ['recolor', 'clr', 'clr_loader']
tmp_ret = collect_all('fitz')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pikepdf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# pywebview 自带 hook 会处理大头，这里再兜底收齐 pythonnet / clr_loader 的运行时
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['web\\app.py'],
    pathex=['D:\\vibe-coding\\recolor-tool'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'lxml', 'PIL._avif', 'PIL.ImageTk', 'PIL.ImageCms',
        # 构建环境里装有 gradio/pandas 等（供 HF 版开发用），exe 用不到，
        # 排除避免把整棵依赖树（pandas/fsspec/tqdm/tzdata/httpx...）打进包里。
        'gradio', 'spaces', 'pandas', 'fsspec', 'tqdm', 'yaml', 'tzdata',
        'brotli', '_brotli', 'certifi', 'huggingface_hub', 'hf_xet',
        'hf_gradio', 'pydantic', 'anyio', 'httpx', 'httpcore', 'starlette',
        'uvicorn', 'fastapi', 'python_multipart', 'pydub', 'groovy',
        'safehttpx', 'semantic_version', 'shellingham', 'typing_inspection',
        'tomlkit', 'rich', 'typer', 'aiofiles', 'websockets', 'ffmpy', 'ruff',
        'sqlite3', 'dateutil', 'pytz', 'requests', 'urllib3', 'idna',
        'charset_normalizer', 'filelock',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RecolorTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RecolorTool',
)
