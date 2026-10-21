@echo off
chcp 65001 >nul
echo Ryoshuを起動しています。ブラウザーが開くまでお待ちください。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
