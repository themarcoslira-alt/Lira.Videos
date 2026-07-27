@echo off
title ULTRACUT3
cd /d "%~dp0"

echo ========================================
echo   ULTRACUT3 - Pipeline de Video
echo ========================================
echo.

REM Verifica se a venv existe
if not exist ".venv\Scripts\python.exe" (
    echo [AVISO] Ambiente virtual nao encontrado.
    echo [INFO] Execute "instalar.bat" primeiro para configurar.
    pause
    exit /b 1
)

echo Iniciando interface grafica...

REM start /B + pythonw = processo separado, sem console, nao trava
start "" /B ".venv\Scripts\pythonw" "gui.py"

echo.
echo ========================================
echo   ULTRACUT3 iniciado com sucesso!
echo   Esta janela pode ser fechada.
echo ========================================
exit /b 0