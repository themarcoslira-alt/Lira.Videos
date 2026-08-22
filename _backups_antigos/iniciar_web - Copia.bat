@echo off
title ULTRACUT3 WEB v1.0
cd /d "%~dp0"

echo ========================================
echo   ULTRACUT3 WEB v1.0
echo   http://127.0.0.1:5000
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [AVISO] Ambiente virtual nao encontrado.
    echo [INFO] Execute "instalar.bat" primeiro para configurar.
    pause
    exit /b 1
)

echo Iniciando servidor web local (nao exponha a porta 5000 externamente)...
echo.

".venv\Scripts\python.exe" app_web.py

pause
