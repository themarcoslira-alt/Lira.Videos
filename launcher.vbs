' ULTRACUT3 Launcher
' Executa a GUI em background sem console, sem travar o batch.
' Chamado por iniciar.bat

Dim shell, pythonw, python, scriptDir, cmd

Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Tenta pythonw primeiro (sem console), depois python
pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
python  = scriptDir & "\.venv\Scripts\python.exe"

Dim objFSO
Set objFSO = CreateObject("Scripting.FileSystemObject")

If objFSO.FileExists(pythonw) Then
    cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & scriptDir & "\gui.py" & Chr(34)
ElseIf objFSO.FileExists(python) Then
    cmd = Chr(34) & python & Chr(34) & " " & Chr(34) & scriptDir & "\gui.py" & Chr(34)
Else
    shell.Popup "Python nao encontrado em .venv\Scripts\!" & vbCrLf & _
                "Execute instalar.bat primeiro.", 5, "ULTRACUT3 - Erro", 16
    WScript.Quit 1
End If

' 0 = WindowStyle Hidden, False = nao esperar termino (async)
shell.Run cmd, 0, False