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
echo.
echo [INFO] A GUI abrira em alguns segundos.
echo [INFO] Nao feche esta janela - logs do terminal aparecem aqui.
echo [INFO] Para encerrar, feche a janela da GUI ou pressione Ctrl+C aqui.
echo.

.venv\Scripts\python gui.py 2>&1
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% NEQ 0 (
    echo.
    echo ========================================
    echo   ULTRACUT3 - ERRO
    echo ========================================
    echo.
    echo [ERRO] Aplicacao encerrou com codigo: %EXITCODE%
    echo [ERRO] Verifique os logs em: logs\events.jsonl
    echo [ERRO] Verifique o console da GUI para detalhes.
    echo.
    pause
    exit /b %EXITCODE%
) else (
    echo.
    echo [INFO] ULTRACUT3 encerrado normalmente.
    timeout /t 2 >nul
)