@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "SAKUYA_ROOT=%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "SAKUYA_EXIT=%ERRORLEVEL%"
if not "%SAKUYA_EXIT%"=="0" pause >nul
exit /b %SAKUYA_EXIT%
