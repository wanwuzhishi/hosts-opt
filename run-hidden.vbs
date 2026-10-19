' Run hosts-opt CLI update without showing a console window.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & root & "\hosts-opt.exe"" --cli update --apply", 0, False
