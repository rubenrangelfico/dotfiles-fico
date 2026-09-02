#!/usr/bin/env python3
"""Menu bar status indicator for the Teams channel sound watcher.

Shows a colored dot in the macOS menu bar reflecting the live health of
`teams_channel_sound_watcher.py`:

  green   watcher alive + skypeToken valid (>10 min) + no recent errors
  yellow  token expiring soon (<10 min) OR a recent error in the log
  red     watcher process not running OR skypeToken expired

Click the dot to see channel, uptime, token expiry, last message seen, and
recent errors, plus actions to view the log, restart the watcher, or refresh
the graph token.
"""
import os, json, time, base64, subprocess, re
from datetime import datetime, timezone

import rumps

HOME        = os.path.expanduser("~")
TOKENS_FILE = os.path.join(HOME, ".teams_tokens.json")
STATE_FILE  = os.path.join(HOME, ".fico", "teams_channel_watch_state.json")
LOG_FILE    = os.path.join(HOME, ".fico", "teams_channel_watch.log")
WATCHER_PY  = os.path.join(HOME, ".fico", "teams_channel_sound_watcher.py")
REFRESH_PY  = os.path.join(HOME, ".fico", "refresh-graph-token.py")
PLIST       = os.path.join(HOME, "Library", "LaunchAgents", "com.ruben.teamschannelsound.plist")
AGENT_LABEL = "com.ruben.teamschannelsound"
CHANNEL_LABEL = "General"

GREEN, YELLOW, RED = "\U0001F7E2", "\U0001F7E1", "\U0001F534"
REFRESH_SECS = 15


def jwt_claims(tok):
    try:
        seg = tok.split(".")[1]; seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def watcher_proc():
    """Return (pid, etime_str) of the running watcher, or (None, None)."""
    try:
        out = subprocess.check_output(["pgrep", "-f", "teams_channel_sound_watcher.py"],
                                      text=True).split()
        if not out:
            return None, None
        pid = out[0]
        et = subprocess.check_output(["ps", "-p", pid, "-o", "etime="], text=True).strip()
        return pid, et
    except Exception:
        return None, None


def skype_minutes():
    """Minutes until the OAuth skypeToken (the one the watcher uses) expires."""
    try:
        d = json.load(open(TOKENS_FILE))
        exp = jwt_claims(d.get("skypeToken", "")).get("exp", 0)
        if not exp:
            return None
        return (exp - time.time()) / 60.0
    except Exception:
        return None


def last_seen():
    try:
        s = json.load(open(STATE_FILE)).get("last_arrival", "")
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone()  # local tz
    except Exception:
        return None


def recent_error():
    """Return last error line text if the most recent log line (excluding
    startup banners) is an HTTP/ERROR entry within the last 3 minutes."""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()[-30:]
    except Exception:
        return None
    now = time.time()
    for line in reversed(lines):
        line = line.strip()
        if not line or "watcher started" in line or "primed" in line or "NEW msg" in line:
            continue
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)", line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            continue
        body = m.group(2)
        is_err = body.startswith("HTTP") or body.startswith("ERROR") or body.startswith("WARN")
        # only the single most recent informative line matters
        if is_err and (now - ts) < 180:
            return body[:60]
        return None
    return None


def notify(title, subtitle, msg):
    """Native macOS notification via osascript (avoids rumps' CFBundleIdentifier
    requirement, which crashes under anaconda python)."""
    try:
        text = (subtitle + " - " + msg) if subtitle else msg
        subprocess.Popen(["osascript", "-e",
            'display notification %s with title %s' % (json.dumps(text), json.dumps(title))])
    except Exception:
        pass


def fmt_min(mins):
    if mins is None:
        return "?"
    if mins < 0:
        return "EXPIRED"
    if mins >= 60:
        return "+%dh %dm" % (int(mins // 60), int(mins % 60))
    return "+%d min" % int(mins)


class WatcherBar(rumps.App):
    def __init__(self):
        super().__init__(RED + " Teams", quit_button=None)
        self.i_channel = rumps.MenuItem("Channel: " + CHANNEL_LABEL)
        self.i_watcher = rumps.MenuItem("Watcher: ...")
        self.i_token   = rumps.MenuItem("Token: ...")
        self.i_last    = rumps.MenuItem("Last msg: ...")
        self.i_errors  = rumps.MenuItem("Errors: ...")
        self.menu = [
            self.i_channel,
            self.i_watcher,
            self.i_token,
            self.i_last,
            self.i_errors,
            None,
            rumps.MenuItem("View live log", callback=self.view_log),
            rumps.MenuItem("Restart watcher", callback=self.restart_watcher),
            rumps.MenuItem("Refresh token (graph)", callback=self.refresh_token),
            None,
            rumps.MenuItem("Quit indicator", callback=self.quit_app),
        ]
        self.timer = rumps.Timer(self.refresh, REFRESH_SECS)
        self.timer.start()
        self.refresh(None)

    def refresh(self, _):
        pid, et = watcher_proc()
        mins = skype_minutes()
        ls = last_seen()
        err = recent_error()

        # title color
        if pid is None or (mins is not None and mins < 0):
            self.title = RED + " Teams"
        elif (mins is not None and mins < 10) or err:
            self.title = YELLOW + " Teams"
        else:
            self.title = GREEN + " Teams"

        self.i_watcher.title = ("Watcher: alive (PID %s, %s)" % (pid, et.strip())) if pid else "Watcher: STOPPED"
        self.i_token.title   = "Token (skype): " + fmt_min(mins)
        self.i_last.title    = ("Last msg: " + ls.strftime("%H:%M:%S  %d/%b")) if ls else "Last msg: -"
        self.i_errors.title  = ("Recent error: " + err) if err else "Errors: none"

    def view_log(self, _):
        subprocess.Popen([
            "osascript", "-e",
            'tell application "Terminal" to do script "tail -f ~/.fico/teams_channel_watch.log"',
            "-e", 'tell application "Terminal" to activate'])

    def restart_watcher(self, _):
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", "gui/%s/%s" % (uid, AGENT_LABEL)],
                       capture_output=True)
        time.sleep(1)
        subprocess.run(["launchctl", "bootstrap", "gui/%s" % uid, PLIST], capture_output=True)
        time.sleep(1)
        self.refresh(None)
        notify("Teams Watcher", "Restarted", "The watcher was restarted.")

    def refresh_token(self, _):
        if os.path.exists(REFRESH_PY):
            r = subprocess.run(["/Users/rubenrangel/anaconda3/bin/python3", REFRESH_PY],
                               capture_output=True, text=True)
            ok = r.returncode == 0
            notify(
                "Teams Watcher",
                "Graph token " + ("refreshed" if ok else "failed"),
                "For the skypeToken run 'actualiza teams' in Claude.")
        else:
            notify("Teams Watcher", "No refresh script",
                   "Run 'actualiza teams' in Claude to refresh.")
        self.refresh(None)

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    WatcherBar().run()
