@echo off
REM 더블클릭으로 렌더 실행
powershell -ExecutionPolicy Bypass -File "%~dp0render.ps1"
pause
