@echo off
rem Native (no Docker) mode: native-setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0native-setup.ps1"
pause