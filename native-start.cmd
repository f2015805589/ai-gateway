@echo off
rem Native (no Docker) mode: native-start
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0native-start.ps1"
pause