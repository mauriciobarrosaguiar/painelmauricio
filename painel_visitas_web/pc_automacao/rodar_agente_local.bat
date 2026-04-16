@echo off
call "%~dp0config_local.bat"
cd /d "%PROJECT_DIR%"
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"
set "LOG_FILE=%LOGS_DIR%\agente_local.log"
%PYTHON_EXE% "%PROJECT_DIR%\agent_local.py" >> "%LOG_FILE%" 2>&1
