Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw " & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.fico\teams_channel_sound_watcher.py >> " & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.fico\teams_channel_watch.log 2>&1", 0, False
