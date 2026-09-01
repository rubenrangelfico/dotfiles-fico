#!/usr/bin/env python3
"""
seed-tokens.py - One-time FICO token seeder for new Claude Code users.

Opens Teams in a Playwright Chromium browser (headed), waits for the user
to complete MFA login, then captures MSAL tokens from localStorage and
network traffic. Saves msalRefreshToken + msalClientId so that
refresh-tokens.py can run on every subsequent session without a browser.

Usage:
    python3 ~/.fico/seed-tokens.py

Requires:  pip install playwright && python -m playwright install chromium
"""

import json, os, sys, time, base64

TOKENS_FILE = os.path.expanduser('~/.teams_tokens.json')
FICO_TID    = 'f9465cb1-7889-4d9a-b552-fdd0addf0eb1'


def decode_claim(token, claim):
    try:
        part = token.split('.')[1]
        part += '=' * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get(claim, '')
    except Exception:
        return ''


def extract_from_localstorage(page):
    return page.evaluate(f"""() => {{
        const FICO_TID = '{FICO_TID}';
        function tid(tok) {{
            try {{
                let p = tok.split('.')[1];
                p += '='.repeat((4 - p.length % 4) % 4);
                return JSON.parse(atob(p.replace(/-/g,'+').replace(/_/g,'/'))).tid;
            }} catch(e) {{ return null; }}
        }}
        const result = {{}};
        const now = Math.floor(Date.now() / 1000);
        for (let i = 0; i < localStorage.length; i++) {{
            const key = localStorage.key(i);
            if (!key) continue;
            try {{
                const v = JSON.parse(localStorage.getItem(key) || '');
                if (!v || !v.secret) continue;

                if (key.toLowerCase().includes('refreshtoken') && !result.msalRefreshToken) {{
                    const t = tid(v.secret);
                    if (!t || t === FICO_TID) {{
                        result.msalRefreshToken = v.secret;
                        // key format: msal.2|{{clientId}}.{{tenantId}}|...|refreshtoken|...
                        const parts = key.split('|');
                        if (parts.length > 1) {{
                            result.msalClientId = parts[1].split('.')[0];
                        }}
                    }}
                }}

                if (key.toLowerCase().includes('accesstoken')) {{
                    const exp = parseInt(v.expiresOn || v.extended_expires_on || 0);
                    if (exp && exp < now) continue;
                    if (tid(v.secret) !== FICO_TID) continue;
                    const target = (v.target || '').toLowerCase();
                    if (target.includes('graph.microsoft.com') && !result.graphToken) {{
                        result.graphToken    = v.secret;
                        result.graphTokenExp = exp;
                    }}
                    if ((target.includes('ic3.teams') || target.includes('chatsvc')) && !result.bearerToken) {{
                        result.bearerToken = v.secret;
                    }}
                }}
            }} catch(e) {{}}
        }}
        return result;
    }}""")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('ERROR: playwright not installed.')
        print('Run:  pip install playwright && python -m playwright install chromium')
        sys.exit(1)

    print('=' * 55)
    print('  FICO Claude Code — one-time token seeder')
    print('=' * 55)
    print()
    print('A browser window will open. Log in with your FICO')
    print('account and complete MFA. The script will capture')
    print('your tokens automatically once Teams loads.')
    print()

    captured = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=['--no-sandbox'])
        context = browser.new_context()
        page    = context.new_page()

        # Capture skypeToken from network before navigating
        def on_request(req):
            if 'authsvc/v1.0/authz' in req.url and not captured.get('skypeToken'):
                auth = req.headers.get('authorization', '')
                if auth.startswith('Bearer '):
                    tok = auth[7:]
                    if decode_claim(tok, 'tid') == FICO_TID:
                        captured['skypeToken'] = tok

        page.on('request', on_request)

        print('Opening teams.microsoft.com ...')
        page.goto('https://teams.microsoft.com')

        print('Waiting for Teams to load (up to 2 min) ...')
        try:
            page.wait_for_selector('text=Chats', timeout=120_000)
        except Exception:
            print()
            print('ERROR: Timed out. Make sure you completed login and MFA.')
            browser.close()
            sys.exit(1)

        print('Teams loaded! Extracting tokens...')

        # Reload once to trigger fresh authsvc requests for skypeToken capture
        page.reload()
        try:
            page.wait_for_selector('text=Chats', timeout=30_000)
        except Exception:
            pass
        time.sleep(3)

        ls_tokens = extract_from_localstorage(page)
        captured.update({k: v for k, v in ls_tokens.items() if v})

        browser.close()

    print()
    print('Capture summary:')
    for field in ['msalRefreshToken', 'msalClientId', 'graphToken', 'bearerToken', 'skypeToken']:
        val    = captured.get(field, '')
        status = '✅' if val else '❌'
        preview = (val[:20] + '...') if val and len(val) > 20 else (val or 'NOT FOUND')
        print(f'  {status}  {field:<20} {preview}')

    if not captured.get('msalRefreshToken'):
        print()
        print('WARNING: msalRefreshToken not captured.')
        print('The browser may be encrypting tokens (newer Chromium versions do this).')
        print()
        print('Fallback: run the device-code seeder instead:')
        print('  python3 ~/.fico/seed-tokens-devicecode.py')
        sys.exit(1)

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
    print()
    print('After that, every new Claude Code session will refresh')
    print('tokens automatically — no browser needed.')


if __name__ == '__main__':
    main()
