Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw " & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.fico\mcp_tray.py", 0, False
