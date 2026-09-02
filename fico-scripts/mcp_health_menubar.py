#!/usr/bin/env python3
"""Menu bar health indicator for all MCP connectors.

Title format:
  🟢 MCPs        — all healthy
  🟡 MCPs        — one or more expiring soon (< 10 min)
  🔴 MCPs [N]    — N connectors expired / broken

Connectors monitored (refresh every 60 s):
  Graph/Teams   graphToken in ~/.teams_tokens.json
  O365 Mail     mailGraphToken (must have Mail.Read scope)
  Teams Skype   exchangedSkypeToken used by the Teams MCP
  ServiceNow    basic-auth curl via ~/.fico/check-sn-health.sh (background)
  Grafana       ~/.token-broker/tokens.json

Fix actions:
  "Refresh tokens" — runs ~/.fico/refresh-tokens.py (graph + mail + skype + bearer)
  "Restart Teams watcher" — restarts com.ruben.teamschannelsound via launchctl
"""
import os, json, time, base64, subprocess, threading
from datetime import datetime

import rumps

HOME        = os.path.expanduser("~")
TOKENS_FILE = os.path.join(HOME, ".teams_tokens.json")
BROKER_FILE = os.path.join(HOME, ".token-broker", "tokens.json")
REFRESH_PY  = os.path.join(HOME, ".fico", "refresh-tokens.py")
SN_HEALTH   = os.path.join(HOME, ".fico", "check-sn-health.sh")
LOG_FILE    = os.path.join(HOME, ".fico", "mcp_health_menubar.log")
PYTHON3     = "/Users/rubenrangel/anaconda3/bin/python3"

WATCHER_PLIST  = os.path.join(HOME, "Library", "LaunchAgents", "com.ruben.teamschannelsound.plist")
WATCHER_LABEL  = "com.ruben.teamschannelsound"

GREEN, YELLOW, RED = "\U0001F7E2", "\U0001F7E1", "\U0001F534"
REFRESH_SECS     = 60
WARN_SECS        = 10 * 60      # yellow if TTL < 10 min
FICO_TID         = "f9465cb1-7889-4d9a-b552-fdd0addf0eb1"

STATUS_ICON = {
    "ok":           "✅",
    "expiring":     "⚠️ ",
    "expired":      "❌",
    "missing":      "❌",
    "wrong_tenant": "❌",
    "no_scope":     "⚠️ ",
    "error":        "❌",
}
# worst-first ordering for aggregate
STATUS_ORDER = ["expired", "wrong_tenant", "missing", "error", "no_scope", "expiring", "ok"]


# ── helpers ──────────────────────────────────────────────────────────────────

def log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass


def jwt_claims(tok):
    if not tok:
        return {}
    try:
        seg = tok.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def fmt_ttl(ttl_secs):
    if ttl_secs < 0:
        return "EXPIRED"
    h = int(ttl_secs // 3600)
    m = int((ttl_secs % 3600) // 60)
    if h > 0:
        return f"TTL {h}h {m}m"
    return f"TTL {m}m"


def check_token(tok, need_scope=None):
    """Return (status, detail) for a JWT.  status ∈ STATUS_ORDER."""
    if not tok:
        return "missing", "no token"
    claims = jwt_claims(tok)
    tid = claims.get("tid", "")
    if tid and tid != FICO_TID:
        return "wrong_tenant", f"wrong tid {tid[:8]}…"
    exp = claims.get("exp", 0)
    ttl = exp - time.time()
    if ttl < 0:
        return "expired", "EXPIRED"
    if need_scope and need_scope not in claims.get("scp", ""):
        return "no_scope", f"missing {need_scope}"
    if ttl < WARN_SECS:
        return "expiring", fmt_ttl(ttl)
    return "ok", fmt_ttl(ttl)


def load_tokens():
    try:
        return json.load(open(TOKENS_FILE))
    except Exception:
        return {}


def check_grafana():
    try:
        broker = json.load(open(BROKER_FILE))
        data   = broker.get("grafana-mcp", {}).get("data", {})
        token  = data.get("token", "")
        url    = data.get("baseUrl", "")
        if not token:
            return "missing", "no token in broker"
        host = url.replace("https://", "").split("/")[0][:28]
        return "ok", host or "token present"
    except Exception as e:
        return "missing", str(e)[:40]


# ── ServiceNow background check ───────────────────────────────────────────────

_sn_lock   = threading.Lock()
_sn_result = ("ok", "checking…")   # shared between threads


def _run_sn_check():
    global _sn_result
    try:
        r = subprocess.run(
            ["bash", SN_HEALTH],
            capture_output=True, text=True, timeout=20
        )
        code = (r.stdout + r.stderr).strip()
        if code.startswith("200"):
            status, detail = "ok", "HTTP 200"
        elif code == "":
            status, detail = "error", "no response (op/curl error)"
        else:
            status, detail = "error", f"HTTP {code}"
    except subprocess.TimeoutExpired:
        status, detail = "error", "timeout"
    except Exception as e:
        status, detail = "error", str(e)[:40]
    with _sn_lock:
        _sn_result = (status, detail)
    log(f"SN check: {status} — {detail}")


def _sn_bg():
    """Kick off a new SN check in a daemon thread every REFRESH_SECS."""
    def loop():
        while True:
            _run_sn_check()
            time.sleep(REFRESH_SECS)
    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ── menu bar app ──────────────────────────────────────────────────────────────

class McpHealthBar(rumps.App):
    def __init__(self):
        super().__init__(RED + " MCPs", quit_button=None)

        self.i_graph = rumps.MenuItem("✅  Graph/Teams:  —")
        self.i_mail  = rumps.MenuItem("✅  O365 Mail:    —")
        self.i_skype = rumps.MenuItem("✅  Skype/MCP:    —")
        self.i_sn    = rumps.MenuItem("✅  ServiceNow:   —")
        self.i_graf  = rumps.MenuItem("✅  Grafana:      —")
        self.i_ts    = rumps.MenuItem("Last check: —")

        self.menu = [
            self.i_graph,
            self.i_mail,
            self.i_skype,
            self.i_sn,
            self.i_graf,
            None,
            self.i_ts,
            None,
            rumps.MenuItem("Fix: Graph / Mail / Skype tokens", callback=self.do_refresh_tokens),
            rumps.MenuItem("Fix: ServiceNow (re-sync password)", callback=self.do_fix_sn),
            rumps.MenuItem("Restart Teams watcher",              callback=self.do_restart_watcher),
            rumps.MenuItem("View log",                           callback=self.view_log),
            None,
            rumps.MenuItem("Quit indicator", callback=lambda _: rumps.quit_application()),
        ]

        self._prev = None   # None = first run, skip new-failure notifications
        _sn_bg()            # start background SN loop
        self.timer = rumps.Timer(self.refresh, REFRESH_SECS)
        self.timer.start()
        self.refresh(None)

    # ── main refresh ──────────────────────────────────────────────────────────

    def refresh(self, _):
        toks = load_tokens()

        g_s,  g_d  = check_token(toks.get("graphToken"))
        m_s,  m_d  = check_token(toks.get("mailGraphToken"), need_scope="Mail.Read")
        sk_s, sk_d = check_token(toks.get("skypeToken"))
        gr_s, gr_d = check_grafana()
        with _sn_lock:
            sn_s, sn_d = _sn_result

        checks  = [("Graph", g_s), ("Mail", m_s), ("Skype", sk_s),
                   ("SN", sn_s), ("Grafana", gr_s)]
        details = {"Graph": g_d, "Mail": m_d, "Skype": sk_d, "SN": sn_d, "Grafana": gr_d}

        # Aggregate title
        worst = min(checks, key=lambda x: STATUS_ORDER.index(x[1]) if x[1] in STATUS_ORDER else 99)[1]
        broken  = sum(1 for _, s in checks if s in ("expired", "missing", "wrong_tenant", "error"))
        warning = sum(1 for _, s in checks if s in ("expiring", "no_scope"))

        if broken > 0:
            self.title = f"{RED} MCPs [{broken}]"
        elif warning > 0:
            self.title = f"{YELLOW} MCPs"
        else:
            self.title = f"{GREEN} MCPs"

        ic = STATUS_ICON
        self.i_graph.title = f"{ic[g_s]}  Graph/Teams:  {g_d}"
        self.i_mail.title  = f"{ic[m_s]}  O365 Mail:    {m_d}"
        self.i_skype.title = f"{ic[sk_s]}  Skype/MCP:    {sk_d}"
        self.i_sn.title    = f"{ic[sn_s]}  ServiceNow:   {sn_d}"
        self.i_graf.title  = f"{ic[gr_s]}  Grafana:      {gr_d}"
        self.i_ts.title    = f"Last check: {datetime.now().strftime('%H:%M:%S')}"

        # Desktop notification on newly broken connectors (skip first run to avoid spam)
        if self._prev is not None:
            for name, status in checks:
                prev = self._prev.get(name, "ok")
                if status in ("expired", "missing", "wrong_tenant", "error") and prev not in (
                    "expired", "missing", "wrong_tenant", "error"
                ):
                    self._notify("MCP broken", name, details.get(name, status))
        self._prev = dict(checks)

    # ── actions ───────────────────────────────────────────────────────────────

    def do_refresh_tokens(self, _):
        self._notify("MCP Health", "Refreshing tokens…", "Running refresh-tokens.py")
        def run():
            r = subprocess.run([PYTHON3, REFRESH_PY], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                self._notify("MCP Health", "Tokens refreshed", r.stdout.strip()[:80])
            else:
                self._notify("MCP Health", "Refresh failed", r.stderr.strip()[:80])
            self._queue_refresh()
        threading.Thread(target=run, daemon=True).start()

    def do_fix_sn(self, _):
        """Re-sync the mcp-servicenow password from 1Password into the macOS keychain."""
        self._notify("ServiceNow Fix", "Pulling password from 1Password…", "")
        OP = os.path.expanduser("~/.local/bin/op")
        def run():
            # 1) Try op CLI (needs 1Password app unlocked)
            pw = ""
            try:
                r = subprocess.run(
                    [OP, "item", "get", "mcp-servicenow",
                     "--fields", "label=password", "--reveal"],
                    capture_output=True, text=True, timeout=15
                )
                pw = r.stdout.strip()
            except Exception:
                pass
            if not pw:
                # 2) Keychain fallback
                try:
                    r2 = subprocess.run(
                        ["security", "find-generic-password", "-s", "mcp-servicenow", "-w"],
                        capture_output=True, text=True, timeout=5
                    )
                    pw = r2.stdout.strip()
                except Exception:
                    pass

            if pw:
                # Update keychain so the health script always finds a fresh copy
                subprocess.run(
                    ["security", "add-generic-password", "-U",
                     "-s", "mcp-servicenow", "-a", "rubenrangel@fico.com", "-w", pw],
                    capture_output=True
                )
                _run_sn_check()
                with _sn_lock:
                    s, d = _sn_result
                if s == "ok":
                    self._notify("ServiceNow", "Fixed", d)
                else:
                    self._notify("ServiceNow", "Still broken", d + " — check SN password in 1Password")
            else:
                self._notify("ServiceNow", "No password found",
                             "Unlock 1Password app and try again")
            self._queue_refresh()
        threading.Thread(target=run, daemon=True).start()

    def do_restart_watcher(self, _):
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{WATCHER_LABEL}"],
                       capture_output=True)
        time.sleep(1)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", WATCHER_PLIST],
                       capture_output=True)
        self._notify("MCP Health", "Teams watcher restarted", "")
        self.refresh(None)

    def view_log(self, _):
        subprocess.Popen([
            "osascript", "-e",
            'tell application "Terminal" to do script "tail -50f ~/.fico/mcp_health_menubar.log"',
            "-e", 'tell application "Terminal" to activate',
        ])

    # ── utility ───────────────────────────────────────────────────────────────

    def _queue_refresh(self):
        """Schedule a refresh on the main thread (safe to call from any thread)."""
        def _once(sender):
            self.refresh(None)
            sender.stop()
        rumps.Timer(_once, 0.5).start()

    def _notify(self, title, subtitle, msg):
        try:
            body = f"{subtitle} — {msg}" if subtitle and msg else (subtitle or msg)
            # Sanitize: remove chars that break AppleScript string literals
            safe = lambda s: s.replace('"', "'").replace("\\", "").replace("\n", " ")
            subprocess.Popen([
                "osascript", "-e",
                f'display notification "{safe(body)}" with title "{safe(title)}"',
            ])
        except Exception:
            pass


if __name__ == "__main__":
    McpHealthBar().run()
