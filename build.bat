@echo off
echo ═══════════════════════════════════════════
echo  KAPPA ROADMAP — Package Builder
echo ═══════════════════════════════════════════
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (echo [ERROR] Python not found. Install Python 3.8+ and try again. & pause & exit /b 1)

:: Create .pyz package
echo Creating portable .pyz package...
python launcher.py --pack

echo.
echo Usage: python backups\kappa-roadmap-*.pyz
pause
