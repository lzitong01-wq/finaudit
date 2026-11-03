@echo off
chcp 65001 >nul
title 研鉴 FinAudit - 本地界面

rem Python 解释器：优先环境变量 PYTHON，其次 PATH 中的 python
set "PY=%PYTHON%"
if not defined PY set "PY=python"

cd /d "%~dp0code"

echo.
echo   研鉴 FinAudit 正在启动...
echo   浏览器会自动打开 http://127.0.0.1:8765
echo   关闭此窗口即停止服务
echo.
echo   使用解释器：%PY%
"%PY%" webui.py --port 8765
if errorlevel 1 (
    echo.
    echo   启动失败。请确认已安装 Python 3.11+ 并在 PATH 中，或设置环境变量 PYTHON 指向解释器。
)
pause
