#!/usr/bin/env python3
"""
Extrae un token fresco de Microsoft Graph desde Safari/Chrome
y lo guarda en ~/.teams_tokens.json

Uso: python3 ~/.fico/refresh-graph-token.py
"""
import subprocess
import json
import os
import sys
import time
import base64

TOKENS_FILE = os.path.expanduser("~/.teams_tokens.json")

# JS que busca el access token de graph.microsoft.com en MSAL localStorage
JS = (
    "(function(){"
    "var r='';"
    "try{"
    "Object.keys(localStorage).forEach(function(k){"
    "if(k.indexOf('accesstoken')<0)return;"
    "try{"
    "var v=JSON.parse(localStorage[k]);"
    "if(!v||!v.secret)return;"
    "var tgt=(v.target||v.realm||k).toLowerCase();"
    "if(tgt.indexOf('graph.microsoft.com')<0)return;"
    "var exp=parseFloat(v.expiresOn||0);"
    "if(exp>Date.now()/1000+120)r=v.secret;"
    "}catch(e){}"
    "});"
    "}catch(e){}"
    "return r;"
    "})()"
)

def run_applescript(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()

def get_token_from_browser(browser):
    if browser == "Safari":
        script = f'''
tell application "Safari"
    repeat with w in windows
        repeat with t in tabs of w
            set u to URL of t
            if u contains "outlook" or u contains "office.com" or u contains "cloud.microsoft" then
                set tok to do JavaScript "{JS}" in t
                if tok is not "" and tok is not missing value then
                    return tok
                end if
            end if
        end repeat
    end repeat
    return ""
end tell'''
    else:  # Chrome
        script = f'''
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            set u to URL of t
            if u contains "outlook" or u contains "office.com" or u contains "cloud.microsoft" then
                set tok to execute t javascript "{JS}"
                if tok is not "" and tok is not missing value then
                    return tok
                end if
            end if
        end repeat
    end repeat
    return ""
end tell'''
    return run_applescript(script)

def open_outlook_in_safari_and_wait():
    print("Abriendo Outlook en Safari...")
    run_applescript('tell application "Safari" to open location "https://outlook.office.com/mail/inbox"')
    print("Esperando que cargue (20s)...")
    time.sleep(20)
    return get_token_from_browser("Safari")

def is_token_valid(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        return payload.get("exp", 0) > time.time() + 300
    except Exception:
        return False

def main():
    token = None

    # 1. Intentar Safari primero (Outlook nativo coexiste, pero web puede estar abierto)
    print("Buscando token en Safari...")
    token = get_token_from_browser("Safari")

    # 2. Intentar Chrome
    if not token:
        print("Buscando token en Chrome...")
        token = get_token_from_browser("Chrome")

    # 3. Abrir Outlook en Safari si no se encontró
    if not token:
        token = open_outlook_in_safari_and_wait()

    if not token:
        print("ERROR: No se pudo obtener el token.")
        print("Abre https://outlook.office.com en Safari o Chrome y vuelve a intentar.")
        sys.exit(1)

    if not is_token_valid(token):
        print("ADVERTENCIA: El token encontrado puede estar próximo a expirar.")

    # Leer tokens existentes y actualizar graphToken
    data = {}
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE) as f:
            data = json.load(f)

    data["graphToken"] = token
    data["graphTokenUpdated"] = int(time.time())

    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Token guardado en {TOKENS_FILE}")
    print(f"Token: {token[:40]}...")

if __name__ == "__main__":
    main()
