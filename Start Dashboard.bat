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

:: Check and install codebase-memory-mcp
echo.
echo  [..] Checking codebase-memory-mcp...
where codebase-memory-mcp >nul 2>&1
if %errorlevel% neq 0 (
    if exist "D:\tools\codebase-memory-mcp.exe" (
        echo  [OK] codebase-memory-mcp found at D:\tools\codebase-memory-mcp.exe
    ) else (
        echo  [WARN] codebase-memory-mcp not found.
        echo         Download from: https://github.com/DeusData/codebase-memory-mcp/releases/latest
        echo         Extract to D:\tools\ and add to PATH.
        echo         Codebase indexing will not be available until installed.
    )
) else (
    echo  [OK] codebase-memory-mcp is installed.
)

:: Check MCP server exists
if not exist "%~dp0gerrit_mcp_server.py" (
    echo  [WARN] gerrit_mcp_server.py not found. MCP tools will not be available.
) else (
    echo  [OK] Gerrit MCP server found.
)

:: Check rules engine exists
if not exist "%~dp0rules_engine.py" (
    echo  [WARN] rules_engine.py not found. Using basic rules.
) else (
    echo  [OK] Rules engine found.
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
for /f "tokens=1" %%i in ('wmic process where "CommandLine like '%%server.py%%'"" get ProcessId /NH 2^>nul') do (
    taskkill /F /PID %%i >nul 2>&1
)
echo  Stopped. Goodbye!
timeout /T 1 /NOBREAK >nul
