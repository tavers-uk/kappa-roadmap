@echo off
where python >nul 2>&1 && goto :run
echo [*] Python not found. Installing via winget...
winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
if errorlevel 1 (
    echo [!] Auto-install failed. Get Python from https://python.org
    pause
    exit /b 1
)
echo [*] Python installed. Refreshing PATH...
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
:run
for %%f in ("%~dp0kappa-roadmap-*.pyz") do set "PYZ=%%f"
if not defined PYZ (
    echo [!] No .pyz file found next to this script.
    pause
    exit /b 1
)
echo [*] Launching %PYZ%...
python "%PYZ%"
