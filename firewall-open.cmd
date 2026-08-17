@echo off
rem Allow LAN access to the AI gateway (auto-elevates to admin)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0firewall-open.ps1"
pause