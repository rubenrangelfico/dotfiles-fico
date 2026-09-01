Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw " & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.fico\teams_tray.py", 0, False
