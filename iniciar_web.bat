@echo off
setlocal
title ULTRACUT3 WEB v1.0 - Google Flow
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
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
REM 5. Studio no NAVEGADOR PADRAO (TAREFA 2)
REM     O Chrome do Flow NAO sobe no boot: ele nasce sob demanda
REM     (geracao/login). O Studio abre no navegador padrao do usuario.
REM --------------------------------------------------
echo [INFO] Abrindo o Lira Studio no navegador padrao...
start "" http://127.0.0.1:5000

REM --------------------------------------------------
REM 6. Confirmacao
REM --------------------------------------------------
echo [OK] Interface Lira Studio aberta no navegador padrao (Chrome do Flow sob demanda).

echo.
echo ==================================================
echo   ULTRACUT3 WEB pronto.
echo   Interface: http://127.0.0.1:5000
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

