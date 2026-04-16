Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & Replace(WScript.ScriptFullName, "atualizar_mercadofarma_oculto_corrigido.vbs", "atualizar_mercadofarma.bat") & Chr(34), 0
Set WshShell = Nothing
