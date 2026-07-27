@echo off
title ULTRACUT3
cd /d "%~dp0"

echo ========================================
echo   ULTRACUT3 - Pipeline de Video
echo ========================================
echo.

REM Cria venv se necessario (com feedback no console)
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Criando ambiente virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar ambiente virtual.
        pause
        exit /b 1
    )
    echo [OK]
    
    echo [2/3] Instalando dependencias...
    call ".venv\Scripts\pip" install --upgrade pip >nul 2>&1
    call ".venv\Scripts\pip" install -r requirements.txt
    if errorlevel 1 (
        echo [ERRO] Falha ao instalar dependencias.
        pause
        exit /b 1
    )
    echo [OK]
)

echo [3/3] Iniciando interface grafica...

REM start /B + pythonw = processo separado, nao trava o batch
start "" /B ".venv\Scripts\pythonw" "gui.py"

echo.
echo ========================================
echo   ULTRACUT3 iniciado com sucesso!
echo   Esta janela pode ser fechada.
echo ========================================
echo.
exit /b 0