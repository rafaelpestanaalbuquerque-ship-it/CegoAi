@echo off
chcp 65001 >nul
title CegoAi - Verificacao de relatorios
setlocal

rem Pasta do projeto (um nivel acima desta pasta "iniciar")
set "RAIZ=%~dp0.."
set "PY=%RAIZ%\.venv\Scripts\python.exe"
set "PDFS=%~dp0pdfs"

if not exist "%PY%" (
    echo [ERRO] Ambiente Python nao encontrado em "%RAIZ%\.venv".
    echo Veja as instrucoes de instalacao no README.md.
    pause
    exit /b 1
)

if not exist "%PDFS%" mkdir "%PDFS%"

rem Se arrastar PDFs/pastas para cima deste ficheiro, analisa esses.
rem Caso contrario, analisa todos os PDFs da pasta "iniciar\pdfs".
if "%~1"=="" (
    dir /b "%PDFS%\*.pdf" >nul 2>&1 || (
        echo Coloque os PDFs a verificar na pasta:
        echo    %PDFS%
        echo e volte a abrir este ficheiro.
        start "" "%PDFS%"
        pause
        exit /b 0
    )
    "%PY%" "%RAIZ%\cegoai.py" "%PDFS%" --json "%~dp0resultado.json"
) else (
    "%PY%" "%RAIZ%\cegoai.py" %* --json "%~dp0resultado.json"
)

echo.
echo Resultado detalhado guardado em: %~dp0resultado.json
pause
