@echo off
call "%~dp0config_local.bat"
cd /d "%PROJECT_DIR%"

call "%~dp0atualizar_bussola.bat"
if errorlevel 1 exit /b 1

call "%~dp0atualizar_mercadofarma.bat"
if errorlevel 1 exit /b 1

echo Rotina completa concluida com sucesso.
exit /b 0