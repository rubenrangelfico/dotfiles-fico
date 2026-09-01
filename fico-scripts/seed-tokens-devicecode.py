#!/usr/bin/env python3
"""
seed-tokens-devicecode.py - FICO token seeder via device code flow.

Fallback when seed-tokens.py (Playwright) fails to capture the refresh token.
The user visits a Microsoft URL, enters a short code, authenticates with
MFA in their regular browser — no Playwright window needed.

Usage:
    python3 ~/.fico/seed-tokens-devicecode.py

No extra dependencies beyond the standard library.
"""

import json, os, sys, time, base64, urllib.request, urllib.parse

TOKENS_FILE = os.path.expanduser('~/.teams_tokens.json')
FICO_TID    = 'f9465cb1-7889-4d9a-b552-fdd0addf0eb1'
# Microsoft Teams native public client — supports device code flow
CLIENT_ID   = '1fec8e78-bce4-4aaf-ab1b-5451cc387264'
SCOPES      = (
    'https://graph.microsoft.com/.default '
    'https://api.spaces.skype.com/.default '
    'offline_access openid profile'
)
ENDPOINT    = f'https://login.microsoftonline.com/{FICO_TID}/oauth2/v2.0'


def post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}: {e.read(512).decode(errors="replace")}'
    except Exception as ex:
        return None, str(ex)


def decode_claim(token, claim):
    try:
        part = token.split('.')[1]
        part += '=' * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get(claim, '')
    except Exception:
        return ''


def main():
    print('=' * 55)
    print('  FICO Claude Code — device code token seeder')
    print('=' * 55)
    print()

    # Step 1: request device code
    result, err = post(f'{ENDPOINT}/devicecode', {
        'client_id': CLIENT_ID,
        'scope':     SCOPES,
    })
    if err:
        print(f'ERROR getting device code: {err}')
        sys.exit(1)

    print(result['message'])
    print()
    print('Waiting for you to authenticate...')

    interval     = result.get('interval', 5)
    device_code  = result['device_code']
    expires_in   = result.get('expires_in', 900)
    deadline     = time.time() + expires_in

    token_data = None
    while time.time() < deadline:
        time.sleep(interval)
        resp, err = post(f'{ENDPOINT}/token', {
            'client_id':   CLIENT_ID,
            'device_code': device_code,
            'grant_type':  'urn:ietf:params:oauth:grant-type:device_code',
        })
        if resp and 'access_token' in resp:
            token_data = resp
            break
        if resp:
            error = resp.get('error', '')
            if error == 'authorization_pending':
                print('.', end='', flush=True)
                continue
            if error == 'slow_down':
                interval += 5
                continue
            print(f'\nERROR: {error} — {resp.get("error_description", "")}')
            sys.exit(1)

    if not token_data:
        print('\nERROR: Timed out waiting for authentication.')
        sys.exit(1)

    print('\nAuthenticated!')

    access_token  = token_data.get('access_token', '')
    refresh_token = token_data.get('refresh_token', '')
    tid           = decode_claim(access_token, 'tid')

    if tid != FICO_TID:
        print(f'ERROR: Wrong tenant tid={tid} (expected {FICO_TID})')
        sys.exit(1)

    exp = decode_claim(access_token, 'exp') or 0

    captured = {
        'msalRefreshToken': refresh_token,
        'msalClientId':     CLIENT_ID,
        'graphToken':       access_token,
        'graphTokenExp':    exp,
    }

    print()
    print('Token summary:')
    for field in ['msalRefreshToken', 'msalClientId', 'graphToken']:
        val    = captured.get(field, '')
        status = '✅' if val else '❌'
        preview = (val[:20] + '...') if val and len(val) > 20 else (val or 'NOT FOUND')
        print(f'  {status}  {field:<20} {preview}')

    # Load or create tokens file
    existing = {}
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update({k: v for k, v in captured.items() if v})

    with open(TOKENS_FILE, 'w') as f:
        json.dump(existing, f, indent=2)

    print()
    print(f'Saved → {TOKENS_FILE}')
    print()
    print('Next step:')
    print('  python3 ~/.fico/refresh-tokens.py')


if __name__ == '__main__':
    main()
