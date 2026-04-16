\
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & Replace(WScript.ScriptFullName, "atualizar_bussola_oculto.vbs", "atualizar_bussola.bat") & Chr(34), 0
Set WshShell = Nothing
