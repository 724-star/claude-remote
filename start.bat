@echo off
title Claude Code 远程控制服务端
cd /d "%~dp0"
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║   Claude Code 远程控制服务端                      ║
echo  ║   正在启动...                                     ║
echo  ╚══════════════════════════════════════════════════╝
echo.
python server.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ❌ 启动失败！请检查：
    echo     1. Python 是否已安装
    echo     2. 依赖是否安装: pip install -r requirements.txt
    echo     3. Claude Code 是否已安装
    pause
)
