#!/usr/bin/env python3
"""
refresh-tokens.py - Refresh FICO MSAL tokens without Chrome

Reads msalRefreshToken from ~/.teams_tokens.json, mints fresh
graphToken, mailGraphToken, bearerToken, skypeToken and writes
them back. Handles token rotation (saves new refresh_token each run).

When the 12h sign-in frequency limit is hit (AADSTS70043), automatically
extracts a fresh MSAL token from regular Chrome via AppleScript (Mac only).
"""

import json, os, sys, time, base64, subprocess, platform
import urllib.request, urllib.parse
from pathlib import Path

TOKENS_FILE = Path.home() / ".teams_tokens.json"
FICO_TID    = "f9465cb1-7889-4d9a-b552-fdd0addf0eb1"
ENDPOINT    = f"https://login.microsoftonline.com/{FICO_TID}/oauth2/v2.0/token"
REDIRECT    = "https://teams.microsoft.com/v2/authv2"

SCOPES = {
    "graphToken":  "https://graph.microsoft.com/.default openid profile offline_access",
    "bearerToken": "https://ic3.teams.office.com/.default openid profile offline_access",
    "skypeToken":  "https://api.spaces.skype.com/.default openid profile offline_access",
}


def decode_claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def token_request(client_id, refresh_token, scope):
    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "client_id":     client_id,
        "refresh_token": refresh_token,
        "scope":         scope,
        "redirect_uri":  REDIRECT,
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Origin", "https://teams.microsoft.com")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read(512).decode(errors='replace')}"
    except Exception as ex:
        return None, str(ex)


def get_msal_from_chrome():
    """
    Extract a fresh MSAL refresh token from regular Chrome via AppleScript.
    Mac only — returns (None, error_string) on Windows/Linux.
    """
    if platform.system() != "Darwin":
        return None, "Chrome AppleScript extraction only available on macOS"

    js = (
        "(function(){"
        "var T='f9465cb1-7889-4d9a-b552-fdd0addf0eb1';"
        "function tid(t){try{var p=t.split('.')[1];"
        "p+='='.repeat((4-p.length%4)%4);"
        "return JSON.parse(atob(p.replace(/-/g,'+').replace(/_/g,'/'))).tid;"
        "}catch(e){return null;}}"
        "var r={};"
        "for(var i=0;i<localStorage.length;i++){"
        "var k=localStorage.key(i);"
        "if(!k||k.toLowerCase().indexOf('refreshtoken')<0)continue;"
        "try{var v=JSON.parse(localStorage.getItem(k)||'');"
        "if(v&&v.secret&&!r.rt&&tid(v.secret)===T){"
        "r.rt=v.secret;"
        "r.cid=k.split('-')[0].replace('{','');}}"
        "catch(e){}}"
        "return JSON.stringify(r);"
        "})()"
    )
    js_escaped = js.replace("\\", "\\\\").replace('"', '\\"')

    script = f"""
tell application "Google Chrome"
    set teamsTab to missing value
    set didOpen to false
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "teams.microsoft.com" then
                set teamsTab to t
                exit repeat
            end if
        end repeat
        if teamsTab is not missing value then exit repeat
    end repeat
    if teamsTab is missing value then
        tell front window
            set teamsTab to make new tab with properties {{URL:"https://teams.microsoft.com"}}
        end tell
        set didOpen to true
        delay 7
    end if
    set res to execute teamsTab javascript "{js_escaped}"
    if didOpen then
        delete teamsTab
    end if
    return res
end tell
"""

    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=35
        )
        if out.returncode != 0:
            return None, f"AppleScript error: {out.stderr.strip()}"
        raw = out.stdout.strip()
        parsed = json.loads(raw)
        rt  = parsed.get("rt") or parsed.get("refreshToken")
        cid = parsed.get("cid") or parsed.get("clientId")
        if not rt:
            return None, "No FICO refresh token found in Chrome localStorage"
        return rt, cid
    except Exception as e:
        return None, str(e)


def fetch_all_tokens(client_id, refresh_tok, data, now):
    for key, scope in SCOPES.items():
        label = scope.split("/")[2]
        print(f"  {key:<20} {label}")
        result, err = token_request(client_id, refresh_tok, scope)

        if err:
            if "AADSTS70043" in err:
                print(f"    BLOCKED: sign-in frequency limit (12h policy)")
                return refresh_tok, True
            print(f"    FAIL: {err}")
            continue

        access_token = result.get("access_token", "")
        claims = decode_claims(access_token)
        tid = claims.get("tid", "")

        if tid != FICO_TID:
            print(f"    SKIP: wrong tenant tid={tid}")
            continue

        exp = claims.get("exp", 0)
        ttl = (exp - now) // 60
        aud = claims.get("aud", "?")
        scp = claims.get("scp", "")
        print(f"    OK   aud={aud}  ttl={ttl}m")

        data[key]         = access_token
        data[f"{key}Exp"] = exp

        if key == "graphToken" and "Mail.Read" in scp:
            data["mailGraphToken"]    = access_token
            data["mailGraphTokenExp"] = exp
            print(f"    -> mailGraphToken (Mail.Read present)")

        new_rt = result.get("refresh_token")
        if new_rt:
            refresh_tok = new_rt

    return refresh_tok, False


def main():
    with open(TOKENS_FILE) as f:
        data = json.load(f)

    refresh_tok = data.get("msalRefreshToken")
    client_id   = data.get("msalClientId", "5e3ce6c0-2b1f-4285-8d4b-75ee78787346")

    if not refresh_tok:
        print("ERROR: msalRefreshToken missing from ~/.teams_tokens.json", file=sys.stderr)
        sys.exit(1)

    now = int(time.time())
    print(f"Refreshing tokens  client_id={client_id}")

    refresh_tok, hit_limit = fetch_all_tokens(client_id, refresh_tok, data, now)

    if hit_limit:
        print("\n  12h limit hit — extracting fresh MSAL token from regular Chrome...")
        new_rt, cid_or_err = get_msal_from_chrome()
        if not new_rt:
            print(f"ERROR: could not extract from Chrome: {cid_or_err}", file=sys.stderr)
            print("Make sure regular Chrome is open with a Teams tab logged in.", file=sys.stderr)
            sys.exit(1)
        print("  Got fresh token from Chrome — retrying...")
        if cid_or_err:
            client_id = cid_or_err
            data["msalClientId"] = cid_or_err
        refresh_tok = new_rt
        refresh_tok, still_blocked = fetch_all_tokens(client_id, refresh_tok, data, now)
        if still_blocked:
            print("ERROR: still blocked after Chrome re-seed — manual Okta login required", file=sys.stderr)
            sys.exit(1)

    data["msalRefreshToken"]   = refresh_tok
    data["msalRefreshUpdated"] = now

    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved  {TOKENS_FILE}")


if __name__ == "__main__":
    main()
