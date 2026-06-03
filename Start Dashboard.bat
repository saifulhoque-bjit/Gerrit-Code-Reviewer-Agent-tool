@echo off
echo.
echo  ====================================
echo   Gerrit AI Code Reviewer
echo  ====================================
echo.

set CHECKS_PASSED=1

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [MISSING] Python is not installed or not on PATH.
    echo.
    echo   Please install Python from:
    echo     https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: During install, check "Add Python to PATH"
    echo   Then close and reopen this window.
    echo.
    set CHECKS_PASSED=0
) else (
    echo  [OK] Python is installed.
)

:: Check Hermes (use 'where' to avoid hanging on version check)
where hermes >nul 2>&1
if %errorlevel% neq 0 (
    echo  [MISSING] Hermes Agent is not installed or not on PATH.
    echo.
    echo   Please install Hermes Agent from:
    echo     https://hermes-agent.nousresearch.com
    echo.
    echo   After installing, open a terminal and run:
    echo     hermes setup
    echo   to configure your AI provider, then reopen this window.
    echo.
    set CHECKS_PASSED=0
) else (
    echo  [OK] Hermes Agent is installed.
)

:: Abort if anything is missing
if %CHECKS_PASSED% neq 1 (
    echo.
    echo  ====================================
    echo   Setup incomplete. See above.
    echo  ====================================
    echo.
    pause
    exit /b 1
)

:: All good - launch
echo.
echo  All dependencies found. Starting server...
echo  Opening browser at http://localhost:7474
echo  Press Q then Enter to stop the server.
echo.

start "" /B cmd /C "timeout /T 2 /NOBREAK >nul && start http://localhost:7474"
start "" /B python "%~dp0server.py"

:waitloop
set /p INPUT=
if /i "%INPUT%"=="q" goto quit
goto waitloop

:quit
echo.
echo  Stopping server...
for /f "tokens=1" %%i in ('wmic process where "CommandLine like '%%server.py%%'" get ProcessId /NH 2^>nul') do (
    taskkill /F /PID %%i >nul 2>&1
)
echo  Stopped. Goodbye!
timeout /T 1 /NOBREAK >nul
