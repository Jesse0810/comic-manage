@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist "node_modules\electron\dist\electron.exe" (
  call npm.cmd ci --no-fund
  if errorlevel 1 goto fail
)
call npm.cmd run build
if errorlevel 1 goto fail
call npm.cmd start
if errorlevel 1 goto fail
exit /b 0
:fail
echo Setup or startup failed. Install Node.js 22.12+ and Python 3.11+ and try again.
pause
exit /b 1
