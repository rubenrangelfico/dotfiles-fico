#!/usr/bin/env python3
"""Exchange OAuth skypeToken for real Skype Exchange Token (Teams MCP needs this)."""
import json, sys, urllib.request
from pathlib import Path

TOKENS_FILE = Path.home() / ".teams_tokens.json"

with open(TOKENS_FILE) as f:
    data = json.load(f)

skype_oauth = data["skypeToken"]
req = urllib.request.Request(
    "https://authsvc.teams.microsoft.com/v1.0/authz",
    data=b"",
    method="POST",
    headers={"Authorization": f"Bearer {skype_oauth}", "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    print(result["tokens"]["skypeToken"])
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
