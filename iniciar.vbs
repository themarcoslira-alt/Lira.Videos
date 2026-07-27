' ULTRACUT3 - Launcher Silencioso
' Duplo-clique ou chamado por iniciar.bat
' Cria venv se necessario, depois abre GUI em background

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
dir = FSO.GetParentFolderName(WScript.ScriptFullName)

' Se venv nao existe, cria em background (cmd oculto)
If Not FSO.FolderExists(dir & "\.venv") Then
    WshShell.Run "cmd /c cd /d """ & dir & """ && python -m venv .venv && .venv\Scripts\pip install --upgrade pip && .venv\Scripts\pip install -r requirements.txt", 0, True
End If

' Inicia GUI (pythonw = sem console)
Dim cmd
If FSO.FileExists(dir & "\.venv\Scripts\pythonw.exe") Then
    cmd = Chr(34) & dir & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & dir & "\gui.py" & Chr(34)
Else
    cmd = Chr(34) & dir & "\.venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & dir & "\gui.py" & Chr(34)
End If

' 0 = janela oculta, False = nao esperar (assincrono)
WshShell.Run cmd, 0, False