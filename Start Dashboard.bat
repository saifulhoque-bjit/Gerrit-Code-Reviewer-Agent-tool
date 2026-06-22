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

:: ── Check and install codebase-memory-mcp ──────────────────────
echo.
echo  [..] Checking codebase-memory-mcp...

set CBMCP_FOUND=0

:: Check 1: On PATH (global install)
where codebase-memory-mcp >nul 2>&1
if %errorlevel% equ 0 (
    set CBMCP_FOUND=1
    echo  [OK] Found on PATH.
    goto cbmcp_done
)

:: Check 2: D:\tools\
if exist "D:\tools\codebase-memory-mcp.exe" (
    set CBMCP_FOUND=1
    echo  [OK] Found at D:\tools\codebase-memory-mcp.exe
    goto cbmcp_done
)

:: Not found — prompt user to install
echo.
echo  [WARN] codebase-memory-mcp not found.
echo.
echo  This is needed for codebase indexing (improves review quality).
echo.
set /p INSTALL_CBMCP="  Install now? (Y/N): "
if /i not "%INSTALL_CBMCP%"=="Y" (
    echo.
    echo  Skipping installation. Codebase indexing will not be available.
    echo  You can install later by running:
    echo    powershell -ExecutionPolicy Bypass -File install.ps1
    echo.
    goto cbmcp_done
)

echo.
echo  [..] Downloading installer...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.ps1' -OutFile '%TEMP%\cbmcp_install.ps1' -UseBasicParsing"
if %errorlevel% neq 0 (
    echo  [ERROR] Download failed. Please install manually:
    echo.
    echo    1. Go to: https://github.com/DeusData/codebase-memory-mcp/releases/latest
    echo    2. Download: codebase-memory-mcp-windows-amd64.zip
    echo    3. Extract and run: install.ps1
    echo.
    goto cbmcp_done
)

if not exist "%TEMP%\cbmcp_install.ps1" (
    echo  [ERROR] Download failed. Please install manually:
    echo.
    echo    https://github.com/DeusData/codebase-memory-mcp/releases/latest
    echo.
    goto cbmcp_done
)

echo  [..] Running installer...
powershell -ExecutionPolicy Bypass -File "%TEMP%\cbmcp_install.ps1" --skip-config
if %errorlevel% neq 0 (
    echo  [WARN] Installer may have failed.
) else (
    echo  [OK] Installation complete.
)

:: Verify
where codebase-memory-mcp >nul 2>&1
if %errorlevel% equ 0 (
    set CBMCP_FOUND=1
    echo  [OK] codebase-memory-mcp installed and on PATH.
) else (
    echo.
    echo  [WARN] Installation may have failed. PATH may need to be refreshed.
    echo         Try closing and reopening this window.
    echo         Or install manually from:
    echo         https://github.com/DeusData/codebase-memory-mcp/releases/latest
)

:: Cleanup installer
del "%TEMP%\cbmcp_install.ps1" >nul 2>&1

:cbmcp_done
if %CBMCP_FOUND% equ 0 (
    echo.
    echo  [INFO] Codebase indexing will not be available.
    echo         Reviews will still work but without codebase context.
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
echo  Starting server...
echo  Opening browser at http://localhost:7474
echo  Press Q then Enter to stop the server.
echo.

start "" /B cmd /C "timeout /T 2 /NOBREAK >nul && start http://localhost:7474"
cd /d "%~dp0"
start "" /B python -u server.py > server_output.log 2>&1

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
