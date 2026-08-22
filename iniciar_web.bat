@echo off
setlocal
title ULTRACUT3 WEB v1.0 - Google Flow
cd /d "%~dp0"

set "PYTHON=C:\ultracut3\.venv\Scripts\python.exe"
set "FLOW_URL=https://labs.google/fx/tools/flow"
set "CDP_PORT=9222"

echo ==================================================
echo   ULTRACUT3 WEB v1.0
echo   http://127.0.0.1:5000
echo   Google Flow: %FLOW_URL%
echo ==================================================
echo.

REM --------------------------------------------------
REM 1. Verifica a venv (Python 3.11 obrigatorio)
REM --------------------------------------------------
if not exist "%PYTHON%" (
    echo [ERRO] Ambiente virtual nao encontrado: %PYTHON%
    echo [INFO] Execute "instalar.bat" primeiro para configurar.
    pause
    exit /b 1
)

REM --------------------------------------------------
REM 2. Garante inicializacao limpa (libera porta 5000 se houver processo zumbi)
REM --------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = (Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue).OwningProcess; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 1 /nobreak >nul

REM --------------------------------------------------
REM 3. Inicia o servidor Flask em background (venv obrigatoria)
REM --------------------------------------------------
echo [INFO] Iniciando servidor web (nao exponha a porta 5000 externamente)...
start "ULTRACUT3 WEB - Flask" /B "%PYTHON%" app_web.py

REM --------------------------------------------------
REM 4. Aguarda o Flask responder na porta 5000 (ate 30s)
REM --------------------------------------------------
for /L %%i in (1,1,30) do (
    call :check_port 5000
    if not errorlevel 1 goto flask_up
    timeout /t 1 /nobreak >nul
)
goto flask_timeout

:flask_up
echo [OK] ULTRACUT3 WEB iniciado em http://127.0.0.1:5000
goto pos_flask

:flask_timeout
echo [ERRO] O servidor Flask nao respondeu na porta 5000 dentro de 30s.
echo [INFO] Verifique os logs em logs/events.jsonl.

:pos_flask

REM --------------------------------------------------
REM 5. Prepara o Chrome/CDP para o Google Flow
REM     (reutiliza ensure_chrome_cdp de services/playwright_flow.py)
REM --------------------------------------------------
echo [INFO] Preparando Google Flow (Chrome CDP porta %CDP_PORT%)...
"%PYTHON%" _preparar_flow.py
if errorlevel 1 (
    echo [AVISO] Nao foi possivel preparar o Chrome/CDP automaticamente.
    echo [INFO] Abra manualmente: %FLOW_URL%
)

echo.
echo ==================================================
echo   ULTRACUT3 WEB pronto.
echo   Feche esta janela para encerrar o servidor.
echo ==================================================
echo.
pause
exit /b

REM --------------------------------------------------
REM Sub-rotina: verifica se uma porta TCP local esta aberta
REM uso: call :check_port <porta>
REM errorlevel 0 = aberta | 1 = fechada
REM --------------------------------------------------
:check_port
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%1);$c.Close();exit 0}catch{exit 1}" >nul 2>&1
exit /b %errorlevel%

