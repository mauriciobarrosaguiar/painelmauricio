\
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & Replace(WScript.ScriptFullName, "rotina_atualizacao_completa_oculto.vbs", "rotina_atualizacao_completa.bat") & Chr(34), 0
Set WshShell = Nothing
