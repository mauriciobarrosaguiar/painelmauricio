@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado no PATH.
  pause
  exit /b 1
)
start "Painel de Visitas" python -m streamlit run app.py --server.headless true
 timeout /t 4 >nul
start "" http://localhost:8501
exit
