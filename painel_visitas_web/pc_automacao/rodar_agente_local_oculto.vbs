Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & Replace(WScript.ScriptFullName, "rodar_agente_local_oculto.vbs", "rodar_agente_local.bat") & Chr(34), 0
Set WshShell = Nothing
