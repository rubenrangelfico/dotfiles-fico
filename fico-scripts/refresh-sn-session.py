#!/usr/bin/env python3
"""Refresh the ServiceNow session cookie by navigating to SN in the bot Chrome.

Connects to the dedicated bot Chrome (remote-debugging-port 9333) that stays
logged into FICO services via Okta. Navigates to ServiceNow, lets Okta SSO
auto-authenticate, then captures the session cookies and pushes them to:
  - The token broker at http://localhost:3099  (servicenow-mcp reads from here)
  - ~/.token-broker/tokens.json               (fallback / Claude Code setup)

Run on a schedule (launchd) every 4 hours to keep Claude Desktop green.
"""
import json, os, time, sys
import urllib.request
import websocket  # websocket-client

DEBUG_PORT   = 9333
TOKENS_FILE  = os.path.expanduser("~/.token-broker/tokens.json")
LOG_FILE     = os.path.expanduser("~/.fico/refresh-sn-session.log")
BROKER_URL   = "http://localhost:3099/servicenow-mcp/token"
SN_INSTANCE  = "https://ficoitservices.service-now.com"
SN_TEST_PATH = "/api/now/table/incident?sysparm_limit=1&sysparm_fields=number"
WAIT_SECS    = 45   # max time to wait for Okta SSO to complete after navigation

REQUIRED_COOKIES = {"JSESSIONID", "glide_sso_id", "glide_session_store",
                    "glide_user_route"}


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        open(LOG_FILE, "a").write(line + "\n")
    except Exception:
        pass


def all_tabs():
    try:
        raw = urllib.request.urlopen(
            "http://localhost:%d/json" % DEBUG_PORT, timeout=5).read()
        return json.loads(raw)
    except Exception:
        return []


def open_or_reuse_tab(ws_url_for_new=None):
    """Return (wsDebuggerUrl, tabId, is_new) for the SN tab."""
    tabs = all_tabs()
    for t in tabs:
        if t.get("type") == "page" and "ficoitservices.service-now.com" in t.get("url", ""):
            log("reusing existing SN tab: %s" % t.get("url", "")[:60])
            return t["webSocketDebuggerUrl"], t["id"], False

    # Open a new tab via the /json/new endpoint (Chrome 117+ requires PUT)
    try:
        req = urllib.request.Request(
            "http://localhost:%d/json/new?%s" % (DEBUG_PORT, SN_INSTANCE),
            data=b"", method="PUT")
        resp = urllib.request.urlopen(req, timeout=10).read()
        t = json.loads(resp)
        log("opened new SN tab id=%s" % t.get("id", "?"))
        return t["webSocketDebuggerUrl"], t["id"], True
    except Exception as e:
        log("ERROR opening new tab: %s" % e)
        return None, None, False


def cdp_call(ws, method, params=None, call_id=1, timeout=15):
    ws.send(json.dumps({"id": call_id, "method": method,
                        "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = json.loads(ws.recv())
            if msg.get("id") == call_id:
                return msg.get("result", {})
        except Exception:
            break
    return {}


def navigate_and_wait(ws):
    """Navigate to SN and poll until Okta SSO cookies appear (or timeout)."""
    cdp_call(ws, "Network.enable", call_id=1)
    cdp_call(ws, "Page.enable", call_id=2)
    log("navigating to SN instance...")
    cdp_call(ws, "Page.navigate",
             {"url": SN_INSTANCE + "/nav_to.do?uri=/"}, call_id=3)

    # Drain events until Page.loadEventFired
    deadline = time.time() + 20
    loaded = False
    while time.time() < deadline:
        try:
            ws.settimeout(2)
            msg = json.loads(ws.recv())
            if msg.get("method") == "Page.loadEventFired":
                loaded = True
                break
        except Exception:
            pass
    if loaded:
        log("page.loadEventFired — polling for Okta SSO cookies (up to %ds)" % WAIT_SECS)
    else:
        log("WARN: no loadEventFired — polling cookies anyway")

    # Poll until glide_sso_id or JSESSIONID appears with a valid SN session
    poll_deadline = time.time() + WAIT_SECS
    while time.time() < poll_deadline:
        time.sleep(3)
        result = cdp_call(ws, "Network.getCookies",
                          {"urls": [SN_INSTANCE]}, call_id=20, timeout=5)
        cookies = {c["name"]: c["value"] for c in result.get("cookies", [])}
        if "glide_sso_id" in cookies and "JSESSIONID" in cookies:
            log("SSO cookies ready after ~%.0fs" % (WAIT_SECS - (poll_deadline - time.time())))
            return
        if "JSESSIONID" in cookies and time.time() > poll_deadline - 10:
            # Last 10s: accept JSESSIONID even without glide_sso_id
            log("JSESSIONID present (glide_sso_id absent); proceeding")
            return
    log("WARN: SSO polling timed out — capturing whatever cookies exist")


def get_cookies(ws):
    result = cdp_call(ws, "Network.getCookies",
                      {"urls": [SN_INSTANCE]}, call_id=10, timeout=10)
    return result.get("cookies", [])


def test_session(cookies_dict):
    """Quick check: does a SN API call return 200 with these cookies?"""
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    url = SN_INSTANCE + SN_TEST_PATH
    req = urllib.request.Request(
        url, headers={"Cookie": cookie_str, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def push_to_broker(cookies_dict):
    payload = json.dumps({
        "cookies": cookies_dict,
        "instanceUrl": SN_INSTANCE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }).encode()
    req = urllib.request.Request(
        BROKER_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log("broker updated: HTTP %d" % r.status)
            return True
    except Exception as e:
        log("WARN broker push failed: %s" % e)
        return False


def update_tokens_file(cookies_dict):
    try:
        try:
            data = json.load(open(TOKENS_FILE))
        except Exception:
            data = {}
        data["servicenow-mcp"] = {
            "data": {
                "cookies": cookies_dict,
                "instanceUrl": SN_INSTANCE,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            },
            "timestamp": int(time.time() * 1000),
        }
        json.dump(data, open(TOKENS_FILE, "w"), indent=2)
        log("tokens.json updated")
    except Exception as e:
        log("ERROR updating tokens.json: %s" % e)


def close_tab(tab_id):
    try:
        urllib.request.urlopen(
            "http://localhost:%d/json/close/%s" % (DEBUG_PORT, tab_id),
            timeout=5).read()
        log("closed SN tab %s" % tab_id)
    except Exception:
        pass


def main():
    log("=== refresh-sn-session start ===")

    # Check if bot Chrome is alive
    try:
        urllib.request.urlopen(
            "http://localhost:%d/json/version" % DEBUG_PORT, timeout=3).read()
    except Exception:
        log("FATAL: bot Chrome not reachable on port %d" % DEBUG_PORT)
        sys.exit(1)

    ws_url, tab_id, is_new = open_or_reuse_tab()
    if not ws_url:
        log("FATAL: could not open/find SN tab")
        sys.exit(1)

    ws = websocket.WebSocket()
    ws.settimeout(20)
    try:
        ws.connect(ws_url)
        navigate_and_wait(ws)
        cookies = get_cookies(ws)
    finally:
        ws.close()

    # getCookies with SN URL already filters to SN domain; keep named cookies only
    KEEP = REQUIRED_COOKIES | {"BIGipServerpool_ficoitservices", "glide_node_id_for_js",
                                "glide_returning_auth_user", "VALK_SESSION_ID"}
    sn_cookies = {c["name"]: c["value"] for c in cookies if c["name"] in KEEP}

    log("captured cookies: %s" % list(sn_cookies.keys()))

    if "JSESSIONID" not in sn_cookies:
        log("ERROR: JSESSIONID not found — Okta SSO may not have completed")
        if is_new:
            close_tab(tab_id)
        sys.exit(1)

    # Validate
    if test_session(sn_cookies):
        log("session validated OK")
    else:
        log("WARN: session test call returned non-200 — cookies may be partial")

    push_to_broker(sn_cookies)
    update_tokens_file(sn_cookies)

    # Close only tabs we opened ourselves
    if is_new:
        close_tab(tab_id)

    log("=== refresh-sn-session done ===")


if __name__ == "__main__":
    main()
