@echo off
chcp 65001 >nul
echo Ryoshuの起動を準備しています。ブラウザーで画面が開くまでお待ちください。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
