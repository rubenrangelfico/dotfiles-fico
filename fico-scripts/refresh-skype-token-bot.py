#!/usr/bin/env python3
"""Headless token refresher for the Teams channel sound watcher.

Connects to the dedicated "bot" Chrome (remote-debugging-port 9333),
reloads the Teams tab, and captures fresh OAuth tokens from network traffic.

Cross-platform: Chrome path auto-detected on Mac and Windows.
Run on a schedule (launchd on Mac, Task Scheduler on Windows) every ~40 min.

Requirements: pip install websocket-client
"""
import json, os, sys, time, base64, platform
import urllib.request
from pathlib import Path

try:
    import websocket
except ImportError:
    print("ERROR: websocket-client not installed. Run: pip install websocket-client", file=sys.stderr)
    sys.exit(1)

DEBUG_PORT  = 9333
FICO_TID    = "f9465cb1-7889-4d9a-b552-fdd0addf0eb1"
TOKENS_FILE = Path.home() / ".teams_tokens.json"
LOG_FILE    = Path.home() / ".fico" / "refresh-skype-token-bot.log"
CAPTURE_SECS = 45
IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


def chrome_path():
    if IS_MAC:
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if IS_WIN:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return "google-chrome"


def profile_dir():
    return str(Path.home() / ".fico" / "chrome-teams-bot")


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def claims(tok):
    try:
        seg = tok.split(".")[1]; seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def port_alive():
    try:
        urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/version", timeout=3).read()
        return True
    except Exception:
        return False


def ensure_chrome_running():
    if port_alive():
        return True
    import subprocess
    args = [
        chrome_path(),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if IS_WIN:
        subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(10):
        time.sleep(1)
        if port_alive():
            return True
    return False


def get_tabs():
    resp = urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json", timeout=5)
    return json.loads(resp.read())


def find_or_open_teams_tab(tabs):
    for t in tabs:
        if "teams.microsoft.com" in t.get("url", ""):
            return t["webSocketDebuggerUrl"], False
    # Open a new Teams tab
    urllib.request.urlopen(
        f"http://localhost:{DEBUG_PORT}/json/new?https://teams.microsoft.com", timeout=5)
    time.sleep(6)
    tabs = get_tabs()
    for t in tabs:
        if "teams.microsoft.com" in t.get("url", ""):
            return t["webSocketDebuggerUrl"], True
    return None, False


def capture_tokens_from_network(ws_url):
    """Capture skypeToken, graphToken, bearerToken from Teams network traffic."""
    captured = {}
    msg_id = [1]

    def send(ws, method, params=None):
        cmd = {"id": msg_id[0], "method": method}
        if params:
            cmd["params"] = params
        ws.send(json.dumps(cmd))
        msg_id[0] += 1

    def on_message(ws, message):
        try:
            data = json.loads(message)
            params = data.get("params", {})
            headers = params.get("headers", {})
            # normalize header keys to lowercase
            hdrs = {k.lower(): v for k, v in headers.items()}
            auth = hdrs.get("authorization", "")
            if not auth.startswith("Bearer "):
                return
            tok = auth[7:]
            c = claims(tok)
            if c.get("tid") != FICO_TID:
                return
            aud = c.get("aud", "")
            if "api.spaces.skype.com" in aud and "skypeToken" not in captured:
                captured["skypeToken"] = tok
                log(f"captured skypeToken (aud={aud})")
            elif "graph.microsoft.com" in aud and "graphToken" not in captured:
                captured["graphToken"] = tok
                log(f"captured graphToken (aud={aud})")
            elif ("ic3.teams" in aud or "chatsvc" in aud) and "bearerToken" not in captured:
                captured["bearerToken"] = tok
                log(f"captured bearerToken (aud={aud})")
        except Exception:
            pass

    ws = websocket.WebSocketApp(ws_url, on_message=on_message)
    import threading
    t = threading.Thread(target=ws.run_forever)
    t.daemon = True
    t.start()

    time.sleep(1)
    # Enable Network domain and reload to force token traffic
    ws.send(json.dumps({"id": 0, "method": "Network.enable"}))
    ws.send(json.dumps({"id": 1, "method": "Page.reload"}))

    deadline = time.time() + CAPTURE_SECS
    WANT = {"skypeToken", "graphToken", "bearerToken"}
    while time.time() < deadline and not WANT.issubset(captured):
        time.sleep(1)

    ws.close()
    return captured


def main():
    log("=== refresh-skype-token-bot starting ===")

    if not ensure_chrome_running():
        log("ERROR: could not start Chrome on debug port %d" % DEBUG_PORT)
        sys.exit(1)

    tabs = get_tabs()
    ws_url, opened = find_or_open_teams_tab(tabs)
    if not ws_url:
        log("ERROR: could not find or open Teams tab")
        sys.exit(1)

    log("capturing tokens from Teams network traffic...")
    captured = capture_tokens_from_network(ws_url)

    if not captured:
        log("ERROR: no tokens captured")
        sys.exit(1)

    # Load and update tokens file
    try:
        with open(TOKENS_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}

    for key, tok in captured.items():
        c = claims(tok)
        exp = c.get("exp", 0)
        data[key] = tok
        data[f"{key}Exp"] = exp
        ttl = (exp - time.time()) / 60
        log(f"  {key}: ttl={ttl:.0f}m")

    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    log(f"Saved {TOKENS_FILE} with {len(captured)} token(s)")


if __name__ == "__main__":
    main()
