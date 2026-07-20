@echo off
title ULTRACUT3 - Pipeline de Video
cd /d "%~dp0"

echo ========================================
echo   ULTRACUT3 - Inicializando...
echo ========================================
echo.

REM Verifica se a venv existe
if not exist ".venv\Scripts\python.exe" (
    echo [AVISO] Ambiente virtual nao encontrado.
    echo [INFO] Criando venv e instalando dependencias...
    echo.
    
    REM Cria a venv
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar ambiente virtual.
        echo [ERRO] Certifique-se de que Python 3.10+ esta instalado.
        pause
        exit /b 1
    )
    
    REM Instala dependencias
    call .venv\Scripts\pip install --upgrade pip
    call .venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERRO] Falha ao instalar dependencias.
        echo [INFO] Tente executar instalar.bat manualmente.
        pause
        exit /b 1
    )
    
    echo.
    echo [OK] Ambiente configurado com sucesso!
)

REM Ativa a venv e executa a GUI
echo [INFO] Iniciando ULTRACUT3...
call .venv\Scripts\python gui.py
if errorlevel 1 (
    echo [ERRO] Aplicacao encerrou com erro.
    pause
    exit /b 1
)