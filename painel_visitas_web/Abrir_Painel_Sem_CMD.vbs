Set WshShell = CreateObject("WScript.Shell")
currentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd /c cd /d """ & currentDir & """ && start """" python -m streamlit run app.py --server.headless true && timeout /t 4 >nul && start http://localhost:8501"
WshShell.Run cmd, 0, False
