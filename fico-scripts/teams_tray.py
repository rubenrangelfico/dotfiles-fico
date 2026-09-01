#!/usr/bin/env python3
"""Teams channel watcher — Windows system tray indicator."""
import json, os, re, subprocess, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import pystray
from PIL import Image, ImageDraw

HOME       = Path.home()
FICO_DIR   = HOME / ".fico"
TOKENS_FILE = HOME / ".teams_tokens.json"
LOG_FILE   = FICO_DIR / "teams_channel_watch.log"
WATCHER_PY = FICO_DIR / "teams_channel_sound_watcher.py"
STATE_FILE = FICO_DIR / "teams_channel_watch_state.json"

POLL_SECS  = 10  # how often to refresh tray tooltip/title


# ── icon drawing ──────────────────────────────────────────────────────────────

def _make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=color)
    return img


ICON_GREEN  = _make_icon("#2ecc40")
ICON_YELLOW = _make_icon("#ffdc00")
ICON_RED    = _make_icon("#ff4136")


# ── helpers ───────────────────────────────────────────────────────────────────

def _jwt_claim(token: str, claim: str):
    try:
        pad  = token.split(".")[1]
        pad += "=" * ((4 - len(pad) % 4) % 4)
        import base64
        payload = json.loads(base64.urlsafe_b64decode(pad))
        return payload.get(claim)
    except Exception:
        return None


def _token_age_min() -> str:
    try:
        data  = json.loads(TOKENS_FILE.read_text())
        token = data.get("skypeToken") or data.get("bearerToken", "")
        exp   = _jwt_claim(token, "exp")
        if not exp:
            return "?"
        remaining = int(exp) - int(time.time())
        if remaining < 0:
            return "EXPIRED"
        return f"+{remaining // 60} min"
    except Exception:
        return "?"


def _last_msg() -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            m = re.search(r"NEW msg.*?@ (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
            if m:
                utc_dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                local_dt = utc_dt.astimezone()
                return local_dt.strftime("%H:%M:%S  %d/%b")
        return "none yet"
    except Exception:
        return "?"


def _last_error() -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        # Only scan lines after the last "watcher started" entry
        start_idx = 0
        for i, line in enumerate(lines):
            if "watcher started" in line:
                start_idx = i
        recent = lines[start_idx:]
        for line in reversed(recent):
            if "ERROR" in line or "FAILED" in line or "401" in line or "403" in line:
                return line.strip()[-80:]
        return "none"
    except Exception:
        return "?"


def _watcher_pid() -> tuple[int | None, str]:
    """Return (pid, started_time_str) of the newest running watcher, or (None, '')."""
    best = None
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
            if "teams_channel_sound_watcher" in cmd:
                if best is None or proc.info["create_time"] > best[0]:
                    best = (proc.info["create_time"], proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if best:
        ct = datetime.fromtimestamp(best[0])
        return best[1], ct.strftime("%d-%m-%H:%M")
    return None, ""


def _channel_label() -> str:
    try:
        return json.loads(STATE_FILE.read_text()).get("channel_label", "General")
    except Exception:
        return "General"


# ── status snapshot ───────────────────────────────────────────────────────────

def _build_status() -> dict:
    pid, started = _watcher_pid()
    alive = pid is not None
    return {
        "alive":   alive,
        "pid":     pid,
        "started": started,
        "channel": _channel_label(),
        "token":   _token_age_min(),
        "last":    _last_msg(),
        "error":   _last_error(),
    }


# ── tray menu items ───────────────────────────────────────────────────────────

def _view_log(icon, item):
    os.startfile(str(LOG_FILE))


def _restart_watcher(icon, item):
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
            if "teams_channel_sound_watcher" in cmd:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(1)
    log_handle = open(LOG_FILE, "a")
    subprocess.Popen(
        [sys.executable, str(WATCHER_PY)],
        stdout=log_handle, stderr=log_handle,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _refresh_token(icon, item):
    subprocess.Popen(
        [sys.executable, str(FICO_DIR / "refresh-tokens.py")],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _quit(icon, item):
    icon.stop()


def _noop(icon, item): pass


def _build_menu(s: dict):
    watcher_str = (
        f"Watcher: alive (PID {s['pid']}, {s['started']})"
        if s["alive"] else "Watcher: STOPPED"
    )
    return pystray.Menu(
        pystray.MenuItem(f"Channel: {s['channel']}",    _noop, enabled=False),
        pystray.MenuItem(watcher_str,                    _noop, enabled=False),
        pystray.MenuItem(f"Token (skype): {s['token']}", _noop, enabled=False),
        pystray.MenuItem(f"Last msg: {s['last']}",       _noop, enabled=False),
        pystray.MenuItem(f"Errors: {s['error']}",        _noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("View live log",        _view_log),
        pystray.MenuItem("Restart watcher",      _restart_watcher),
        pystray.MenuItem("Refresh token (graph)", _refresh_token),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit indicator",       _quit),
    )


# ── background refresh loop ───────────────────────────────────────────────────

def _refresh_loop(icon: pystray.Icon):
    while True:
        time.sleep(POLL_SECS)
        try:
            s = _build_status()
            if not s["alive"]:
                icon.icon  = ICON_RED
                icon.title = "Teams — watcher STOPPED"
            elif s["token"] == "EXPIRED":
                icon.icon  = ICON_YELLOW
                icon.title = f"Teams — token expired | {s['channel']}"
            else:
                icon.icon  = ICON_GREEN
                icon.title = f"Teams — {s['channel']} | last: {s['last']}"
            icon.menu = _build_menu(s)
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    s    = _build_status()
    icon = pystray.Icon(
        name  = "Teams Watcher",
        icon  = ICON_GREEN if s["alive"] else ICON_RED,
        title = f"Teams — {s['channel']} | last: {s['last']}",
        menu  = _build_menu(s),
    )
    threading.Thread(target=_refresh_loop, args=(icon,), daemon=True).start()
    icon.run()
