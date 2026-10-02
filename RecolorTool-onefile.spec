# -*- mode: python ; coding: utf-8 -*-
# 单文件 exe（onefile）：整个程序打进一个 RecolorTool.exe，
# 启动时自解压运行时，不依赖旁边的 _internal 文件夹——
# 无论从正式目录还是临时解压目录启动都不会因文件被清理而失效。
from PyInstaller.utils.hooks import collect_all

datas = [('web\\templates', 'templates'), ('web\\static', 'static')]
binaries = []
hiddenimports = ['recolor']
for pkg in ('fitz', 'pikepdf'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

excludes = [
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
]

a = Analysis(
    ['web\\app.py'],
    pathex=['D:\\vibe-coding\\recolor-tool'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RecolorTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
