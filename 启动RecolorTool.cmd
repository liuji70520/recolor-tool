@echo off
rem 一键换色启动器：把程序自解压目录从系统临时目录（可能被清理）改到用户目录，
rem 避免“双击没反应”。请双击本文件运行，而不是直接双击 RecolorTool.exe。
set "TEMP=%LOCALAPPDATA%\RecolorTool\tmp"
set "TMP=%LOCALAPPDATA%\RecolorTool\tmp"
if not exist "%TEMP%" mkdir "%TEMP%"
start "" "%~dp0RecolorTool.exe"
