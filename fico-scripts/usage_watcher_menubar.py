#!/usr/bin/env python3
"""Menu bar indicator for Claude Enterprise usage ($ spent of monthly limit)
and Claude Desktop session spend delta (2-hour rolling window).

Monthly spend: reads claude.ai's /usage endpoint via CDP against the shared
services Chrome (port 9333, profile ~/.fico/chrome-teams-bot).

Session spend: reads ~/Library/Application Support/Claude/plan-usage-history.json
and computes the xu (cumulative $) delta over a 2-hour rolling window.

Title format: 🟢 $9.44 · 🟢 $0.23
  Monthly:  green <70%, yellow 70-90%, red >90% or no session
  Session:  green <$0.50 delta, yellow $0.50-$2.00, red >$2.00
"""
import os, json, time, subprocess
from datetime import datetime

import rumps

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None

HOME       = os.path.expanduser("~")
ORG        = "0c63e343-3c4a-462c-a411-0406f211ca44"
PORT       = 9333
START_SH   = os.path.join(HOME, ".fico", "start-usage-chrome.sh")
STATE_FILE = os.path.join(HOME, ".fico", "usage_watch_state.json")
LOG_FILE   = os.path.join(HOME, ".fico", "usage_watch.log")

GREEN, YELLOW, RED = "\U0001F7E2", "\U0001F7E1", "\U0001F534"
REFRESH_SECS = 90   # 1.5 min


def log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass


def cdp_fetch_usage():
    """Run fetch('/api/organizations/{ORG}/usage') inside the claude.ai tab
    via the Chrome DevTools Protocol, return parsed JSON or None."""
    if websocket is None:
        log("websocket-client no instalado")
        return None
    import urllib.request

    def list_tabs():
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))

    def find_claude(tabs):
        for t in tabs:
            if t.get("type") == "page" and "claude.ai" in (t.get("url") or ""):
                return t.get("webSocketDebuggerUrl")
        return None

    # 1) Is Chrome up? If not, relaunch it via the start script.
    try:
        tabs = list_tabs()
    except Exception as e:
        log(f"Chrome {PORT} no responde: {e} — relanzando")
        try:
            subprocess.Popen(["/bin/bash", START_SH])
        except Exception as e2:
            log(f"no pude relanzar Chrome: {e2}")
        return None

    # 2) Is there a claude.ai tab? If not, self-heal: open one via CDP.
    ws_url = find_claude(tabs)
    if not ws_url:
        log("No hay pestana de claude.ai — reabriendo via CDP")
        opened = False
        for method in ("GET", "PUT"):
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{PORT}/json/new?https://claude.ai/new",
                    method=method)
                urllib.request.urlopen(req, timeout=8)
                opened = True
                break
            except Exception:
                continue
        if opened:
            time.sleep(5)  # let the page load + auth
            try:
                ws_url = find_claude(list_tabs())
            except Exception:
                ws_url = None
        if not ws_url:
            log("no pude reabrir la pestana de claude.ai")
            return None

    expr = (
        "fetch('/api/organizations/%s/usage')"
        ".then(r=>r.json()).then(d=>JSON.stringify(d))" % ORG
    )
    try:
        ws = websocket.create_connection(ws_url, timeout=10, origin="http://localhost:9333")
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "awaitPromise": True, "returnByValue": True},
        }))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                ws.close()
                val = msg.get("result", {}).get("result", {}).get("value")
                return json.loads(val) if val else None
    except Exception as e:
        log(f"CDP fetch fallo: {e}")
        return None


def read_spend():
    """Return dict {spent, limit, pct, severity} in dollars, or None."""
    d = cdp_fetch_usage()
    if not d:
        return None
    sp = d.get("spend") or {}
    used = sp.get("used") or {}
    lim = sp.get("limit") or {}
    try:
        exp_u = 10 ** used.get("exponent", 2)
        exp_l = 10 ** lim.get("exponent", 2)
        spent = used.get("amount_minor", 0) / exp_u
        limit = lim.get("amount_minor", 0) / exp_l
        pct = sp.get("percent", (spent / limit * 100) if limit else 0)
        sev = sp.get("severity", "normal")
        res = {"spent": spent, "limit": limit, "pct": pct, "severity": sev,
               "ts": datetime.now().isoformat(timespec="seconds")}
        try:
            json.dump(res, open(STATE_FILE, "w"))
        except Exception:
            pass
        return res
    except Exception as e:
        log(f"parse spend fallo: {e}")
        return None


DESKTOP_USAGE_FILE = os.path.join(
    HOME, "Library", "Application Support", "Claude", "plan-usage-history.json"
)
SESSION_WINDOW_SECS  = 7200   # 2-hour rolling window
SESSION_GREEN_DELTA  = 0.50   # < $0.50 → green
SESSION_YELLOW_DELTA = 2.00   # $0.50-$2.00 → yellow; >$2.00 → red


STALE_THRESHOLD_SECS = 1800   # show "~" prefix when last xu is >30 min old


def read_desktop_session():
    """Read plan-usage-history.json; return xu delta over the last 2 hours.
    Returns dict {delta, current_xu, stale} or None on error / no data."""
    try:
        with open(DESKTOP_USAGE_FILE, "r") as f:
            raw = json.load(f)
    except Exception as e:
        log(f"read_desktop_session: {e}")
        return None

    # version 2 wraps samples in {version, samples}; version 1 was a bare list
    samples = raw.get("samples", raw) if isinstance(raw, dict) else raw
    if not samples:
        return None

    now_ms = time.time() * 1000
    window_start_ms = now_ms - SESSION_WINDOW_SECS * 1000

    valid = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        t = s.get("t")
        xu = (s.get("u") or {}).get("xu")
        if t is None or xu is None:
            continue
        valid.append((t, xu))

    if not valid:
        return None

    valid.sort(key=lambda x: x[0])
    last_t, current_xu = valid[-1]

    # Baseline = last sample older than window_start; fall back to oldest
    baseline_xu = valid[0][1]
    for t, xu in valid:
        if t < window_start_ms:
            baseline_xu = xu
        else:
            break

    # Counter reset detection: if current < baseline the monthly xu was reset.
    # In that case, current_xu itself is the spend since the reset.
    if current_xu < baseline_xu:
        delta = current_xu
    else:
        delta = current_xu - baseline_xu

    stale = (now_ms - last_t) > STALE_THRESHOLD_SECS * 1000

    return {
        "delta": max(0.0, delta),
        "current_xu": current_xu,
        "stale": stale,
    }


class UsageBar(rumps.App):
    def __init__(self):
        super().__init__("… Usage", quit_button=None)
        self.i_spent  = rumps.MenuItem("Spent: …")
        self.i_rem    = rumps.MenuItem("Remaining: …")
        self.i_pct    = rumps.MenuItem("Used: …")
        self.i_upd    = rumps.MenuItem("Updated: …")
        self.i_sep2   = None
        self.i_sess   = rumps.MenuItem("Desktop session: …")
        self.i_turns  = rumps.MenuItem("Monthly total: …")
        self.menu = [
            self.i_spent, self.i_rem, self.i_pct, self.i_upd, None,
            self.i_sess, self.i_turns, None,
            rumps.MenuItem("Refresh now", callback=self.manual_refresh),
            rumps.MenuItem("Open claude.ai (login)", callback=self.open_login),
            rumps.MenuItem("View log", callback=self.view_log),
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]
        self.timer = rumps.Timer(self.refresh, REFRESH_SECS)
        self.timer.start()
        self.refresh(None)

    def refresh(self, _):
        s = read_spend()
        sess = read_desktop_session()

        # --- session dot ---
        if sess is None:
            sess_dot = "⚪"
            sess_label = "—"
        else:
            d = sess["delta"]
            stale = sess.get("stale", False)
            sess_dot = (GREEN if d < SESSION_GREEN_DELTA
                        else (YELLOW if d < SESSION_YELLOW_DELTA else RED))
            prefix = "~" if stale else ""
            sess_label = f"{prefix}${d:.2f}"
            stale_note = " (stale)" if stale else " (last 2h)"
            self.i_sess.title  = f"Desktop session: {prefix}${d:.2f}{stale_note}"
            self.i_turns.title = f"Monthly total: ${sess['current_xu']:.2f}"

        # --- monthly dot ---
        if not s:
            monthly_dot = RED
            self.i_spent.title = "Spent: (no session)"
            self.i_rem.title   = "Remaining: —"
            self.i_pct.title   = "Used: —"
            self.i_upd.title   = "Updated: login needed → menu"
            self.title = f"{monthly_dot} ? · {sess_dot} {sess_label}"
            return

        pct = s["pct"]
        monthly_dot = GREEN if (pct < 70 and s["severity"] == "normal") else \
                      (YELLOW if pct < 90 else RED)
        if s["severity"] != "normal" and monthly_dot == GREEN:
            monthly_dot = YELLOW
        self.title = f"{monthly_dot} ${s['spent']:.2f} · {sess_dot} {sess_label}"
        self.i_spent.title = f"Spent: ${s['spent']:.2f} of ${s['limit']:.2f}"
        self.i_rem.title   = f"Remaining: ${max(0, s['limit']-s['spent']):.2f}"
        self.i_pct.title   = f"Used: {pct:.0f}%  ({s['severity']})"
        self.i_upd.title   = f"Updated: {s['ts'][11:16]}"

    def manual_refresh(self, _):
        self.refresh(None)
        rumps.notification("Claude Usage", "", self.i_spent.title)

    def open_login(self, _):
        subprocess.Popen(["/bin/bash", START_SH])
        rumps.notification("Claude Usage", "Abriendo Chrome de usage",
                           "Loguéate a claude.ai; la sesión persiste.")

    def view_log(self, _):
        subprocess.Popen(["open", "-a", "Console", LOG_FILE])

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    UsageBar().run()
