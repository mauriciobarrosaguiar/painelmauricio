@echo off
call "%~dp0config_local.bat"
cd /d "%PROJECT_DIR%"

if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"
set "GIT_LOG=%LOGS_DIR%\git_%date:~-4,4%-%date:~-7,2%-%date:~-10,2%.log"

echo ==== GIT PUSH %date% %time% ==== >> "%GIT_LOG%"

git add data\* >> "%GIT_LOG%" 2>&1
git add logs\* >> "%GIT_LOG%" 2>&1

git diff --cached --quiet
if %errorlevel%==0 (
    echo Nenhuma alteracao para enviar. >> "%GIT_LOG%"
    echo Nenhuma alteracao para enviar.
    exit /b 0
)

git commit -m "Atualizacao automatica da base %date% %time%" >> "%GIT_LOG%" 2>&1
if errorlevel 1 (
    echo Falha no commit. >> "%GIT_LOG%"
    exit /b 1
)

git push %GIT_REMOTE% %GITHUB_BRANCH% >> "%GIT_LOG%" 2>&1
if errorlevel 1 (
    echo Falha no push. >> "%GIT_LOG%"
    exit /b 1
)

echo Push concluido com sucesso. >> "%GIT_LOG%"
echo Push concluido com sucesso.
exit /b 0