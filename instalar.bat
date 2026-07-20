@echo off
title ULTRACUT3 - Instalacao Limpa
cd /d "%~dp0"

echo ========================================
echo   ULTRACUT3 - Instalacao Limpa
echo ========================================
echo.

REM Remove venv existente
if exist ".venv\" (
    echo [INFO] Removendo ambiente virtual existente...
    rmdir /s /q .venv
    echo [OK] Ambiente antigo removido.
    echo.
)

REM Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo [INFO] Instale Python 3.10+ de https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set pyver=%%i
echo [INFO] Python %pyver% encontrado.
echo.

REM Cria venv nova
echo [INFO] Criando ambiente virtual...
python -m venv .venv
if errorlevel 1 (
    echo [ERRO] Falha ao criar ambiente virtual.
    pause
    exit /b 1
)
echo [OK] Ambiente virtual criado.
echo.

REM Atualiza pip
echo [INFO] Atualizando pip...
call .venv\Scripts\python -m pip install --upgrade pip
echo.

REM Instala dependencias
echo [INFO] Instalando dependencias...
call .venv\Scripts\pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)
echo.
echo ========================================
echo   INSTALACAO CONCLUIDA COM SUCESSO!
echo ========================================
echo.
echo Para iniciar o ULTRACUT3, execute:
echo   iniciar.bat
echo.
pause