@echo off
call "%~dp0config_local.bat"
cd /d "%PROJECT_DIR%"

if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"
if not exist "%MERCADOFARMA_DOWNLOADS%" mkdir "%MERCADOFARMA_DOWNLOADS%"

set "LOG_FILE=%LOGS_DIR%\mercadofarma_%date:~-4,4%-%date:~-7,2%-%date:~-10,2%.log"
set "MERCADOFARMA_INPUT=%PROJECT_DIR%\data\PRODUTOS_MIX.xlsx"
set "MERCADOFARMA_OUTPUT=%PROJECT_DIR%\data\Estoque_preco_distribuidora.xlsx"

echo ==== MERCADO FARMA %date% %time% ==== >> "%LOG_FILE%"
echo Iniciando extracao do Mercado Farma... >> "%LOG_FILE%"

%PYTHON_EXE% "%MERCADOFARMA_SCRIPT%" --usuario %MERCADOFARMA_USUARIO% --senha %MERCADOFARMA_SENHA% --cnpj %MERCADOFARMA_CNPJ% --input "%MERCADOFARMA_INPUT%" --saida "%MERCADOFARMA_OUTPUT%" --downloads "%MERCADOFARMA_DOWNLOADS%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo Falha na extracao do Mercado Farma. >> "%LOG_FILE%"
    echo Falha na extracao do Mercado Farma.
    exit /b 1
)

echo Extracao do Mercado Farma concluida. >> "%LOG_FILE%"
call "%~dp0git_push_update.bat"
exit /b %errorlevel%
