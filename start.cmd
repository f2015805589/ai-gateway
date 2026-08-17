@echo off
rem One-click launcher for the AI gateway (start.cmd)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
pause