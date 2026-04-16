@echo off
call "%~dp0config_local.bat"
cd /d "%PROJECT_DIR%"

if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"
if not exist "%BUSSOLA_DOWNLOADS%" mkdir "%BUSSOLA_DOWNLOADS%"

set "LOG_FILE=%LOGS_DIR%\bussola_%date:~-4,4%-%date:~-7,2%-%date:~-10,2%.log"

echo ==== BUSSOLA %date% %time% ==== >> "%LOG_FILE%"
echo Iniciando extracao do Bussola... >> "%LOG_FILE%"

%PYTHON_EXE% "%BUSSOLA_SCRIPT%" --usuario %BUSSOLA_USUARIO% --senha %BUSSOLA_SENHA% --saida "%PROJECT_DIR%\data" --downloads "%BUSSOLA_DOWNLOADS%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo Falha na extracao do Bussola. >> "%LOG_FILE%"
    echo Falha na extracao do Bussola.
    exit /b 1
)

echo Extracao do Bussola concluida. >> "%LOG_FILE%"
call "%~dp0git_push_update.bat"
exit /b %errorlevel%