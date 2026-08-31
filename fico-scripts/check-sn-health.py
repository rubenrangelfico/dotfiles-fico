#!/usr/bin/env python3
"""Check ServiceNow session health. Returns 0 (ok) or 1 (fail). Cross-platform."""
import base64, subprocess, sys, urllib.request
from pathlib import Path

SN_URL = "https://ficoitservices.service-now.com/api/now/table/incident?sysparm_limit=1&sysparm_fields=number"
ZSCALER_CERT = Path.home() / ".fico" / "ca-bundle-zscaler.pem"
SN_USER = "YOUR_SN_USERNAME"  # replace with your FICO email


def get_password():
    # Try 1Password CLI first
    try:
        r = subprocess.run(
            ["op", "item", "get", "mcp-servicenow", "--fields", "label=password", "--reveal"],
            capture_output=True, text=True, timeout=10
        )
        pw = r.stdout.strip()
        if pw:
            return pw
    except FileNotFoundError:
        pass

    # macOS Keychain fallback
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "mcp-servicenow", "-w"],
            capture_output=True, text=True, timeout=5
        )
        pw = r.stdout.strip()
        if pw:
            return pw
    except FileNotFoundError:
        pass

    # Windows Credential Manager fallback (requires keyring)
    try:
        import keyring
        pw = keyring.get_password("mcp-servicenow", SN_USER)
        if pw:
            return pw
    except ImportError:
        pass

    return None


def main():
    pw = get_password()
    if not pw:
        print("ERROR: could not retrieve ServiceNow password", file=sys.stderr)
        sys.exit(1)

    creds = base64.b64encode(f"{SN_USER}:{pw}".encode()).decode()
    req = urllib.request.Request(SN_URL, headers={
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
    })

    # Use Zscaler cert bundle if present
    import ssl
    ctx = ssl.create_default_context()
    if ZSCALER_CERT.exists():
        ctx.load_verify_locations(str(ZSCALER_CERT))

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            print(r.status)
            sys.exit(0 if r.status == 200 else 1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
