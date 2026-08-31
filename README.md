# FICO AI Tooling — dotfiles

Cross-platform setup for Claude Code + FICO Microsoft 365 integration.
Works on **macOS** and **Windows 10/11**.

## What this includes

| Component | Purpose |
|---|---|
| `fico-scripts/refresh-tokens.py` | Mint fresh MSAL tokens via Azure AD (no browser) |
| `fico-scripts/exchange-skype.py` | Exchange OAuth skypeToken for Skype Exchange Token |
| `fico-scripts/refresh-skype-token-bot.py` | Chrome CDP bot — captures tokens from a live Teams tab |
| `fico-scripts/teams_channel_sound_watcher.py` | Polls a Teams channel; plays sound on new messages |
| `fico-scripts/check-sn-health.py` | Verifies ServiceNow connectivity |
| `claude/CLAUDE.md` | Global Claude Code instructions (session auto-setup) |
| `claude/settings.json` | Claude Code settings (MCP servers, permissions) |
| `platform/mac/launchagents/` | macOS LaunchAgent plists for background daemons |
| `platform/windows/tasks/` | Windows Task Scheduler XMLs for background daemons |

## Prerequisites

### All platforms
- **Python 3.9+** — [python.org](https://python.org)
- **Claude Code** — `npm install -g @anthropic-ai/claude-code`
- **Google Chrome** (needed by `refresh-skype-token-bot.py`)
- A valid **FICO Microsoft 365 account** (`@fico.com`)

### macOS only
- Homebrew (optional but recommended)
- 1Password CLI (`brew install 1password-cli`) — needed by check-sn-health.sh

### Windows only
- PowerShell 5.1+
- `pip install win10toast` for desktop notifications (optional)

## Quick Start

### macOS

```bash
git clone https://github.com/YOUR_ORG/dotfiles-fico.git ~/dotfiles-fico
cd ~/dotfiles-fico
chmod +x install-mac.sh
./install-mac.sh
```

### Windows

```powershell
git clone https://github.com/YOUR_ORG/dotfiles-fico.git $env:USERPROFILE\dotfiles-fico
cd $env:USERPROFILE\dotfiles-fico
powershell -ExecutionPolicy Bypass -File install-windows.ps1
```

## Configuration (required after install)

### 1. Set your FICO tenant ID in claude/CLAUDE.md

Open `~/.claude/CLAUDE.md` and replace all `YOUR_FICO_TENANT_ID` with your actual Azure AD tenant ID.

### 2. Set the Teams channel to watch

Edit `~/.fico/teams_channel_sound_watcher.py`:

```python
YOUR_TEAM_ID    = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
YOUR_CHANNEL_ID = "19:xxxxxx@thread.skype"
```

To find your channel IDs:
- Open Teams → right-click the channel → "Get link to channel"
- Or run: `python3 ~/.fico/refresh-tokens.py` then use the Graph API

### 3. Seed the MSAL refresh token (first time only)

The first time, you need to get the refresh token from Teams browser localStorage:

1. Open `https://teams.microsoft.com` in a browser
2. Open DevTools → Console → paste:
   ```javascript
   for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k?.toLowerCase().includes('refreshtoken')){const v=JSON.parse(localStorage.getItem(k));if(v?.secret)console.log('RT:',v.secret);}}
   ```
3. Copy the printed token value
4. Edit `~/.teams_tokens.json` and set `"msalRefreshToken": "<value>"`

Then run:
```bash
python3 ~/.fico/refresh-tokens.py
```

After this, `refresh-tokens.py` will auto-rotate the token — no manual steps needed.

### 4. ServiceNow credentials

Edit `~/.fico/check-sn-health.py` and set:
```python
SN_USERNAME = "your.username@fico.com"
SN_INSTANCE = "https://your-instance.service-now.com"
```

Credentials are read from 1Password CLI (`op item get "mcp-servicenow"`), macOS Keychain, or Windows `keyring` — see script comments.

## Background Daemons

### macOS (LaunchAgents)

| Label | Script | Schedule |
|---|---|---|
| com.fico.msal-token-refresh | refresh-tokens.py | Every 45 min |
| com.fico.teamschannelsound | teams_channel_sound_watcher.py | Always running |
| com.fico.teamstokenrefresh | refresh-skype-token-bot.py | Every 40 min |
| com.fico.teamswatcherbar | teams_watcher_menubar.py | Always running (menu bar) |

Manage with:
```bash
launchctl list | grep com.fico
launchctl unload ~/Library/LaunchAgents/com.fico.teamschannelsound.plist
launchctl load   ~/Library/LaunchAgents/com.fico.teamschannelsound.plist
```

### Windows (Task Scheduler)

| Task name | Script | Schedule |
|---|---|---|
| fico-msal-token-refresh | refresh-tokens.py | Every 45 min + logon |
| fico-teams-channel-watcher | teams_channel_sound_watcher.py | On logon, auto-restart |
| fico-teams-token-refresh | refresh-skype-token-bot.py | Every 40 min + logon |

Manage with:
```powershell
Get-ScheduledTask | Where-Object {$_.TaskName -like 'fico-*'}
Start-ScheduledTask -TaskName "fico-teams-channel-watcher"
Stop-ScheduledTask  -TaskName "fico-teams-channel-watcher"
```

## Zscaler / Corporate Proxy

If you're on the FICO Zscaler proxy, export your CA bundle path:

```bash
# macOS / Linux
export REQUESTS_CA_BUNDLE=~/.fico/ca-bundle-zscaler.pem

# Windows (PowerShell)
$env:REQUESTS_CA_BUNDLE = "$env:USERPROFILE\.fico\ca-bundle-zscaler.pem"
```

The scripts auto-detect this variable. Ask IT for the Zscaler root CA cert if you don't have it.

## Security notes

- `~/.teams_tokens.json` contains live OAuth tokens — never commit it (it's in `.gitignore`)
- The `.gitignore` blocks `*.pem`, `*.key`, and token JSON files
- CLAUDE.md contains tenant IDs — sanitize or rotate before sharing externally
- All tokens are validated against the FICO tenant (`YOUR_FICO_TENANT_ID`) — guest-tenant tokens are rejected automatically

## Troubleshooting

| Problem | Fix |
|---|---|
| `refresh-tokens.py` exits with HTTP 400 | msalRefreshToken expired — redo Step 3 above |
| Teams watcher shows 403 AclCheckFailed | Token is from guest tenant; re-run refresh-tokens.py |
| `pythonw` not found on Windows | Replace `pythonw` with `python` in the Task XML, or install Python with Launcher |
| Sound doesn't play on Windows | Install Windows Media Feature Pack (N editions) or change `SOUND_WIN` to a `.wav` path |
| Claude Code can't reach FICO services | Set `REQUESTS_CA_BUNDLE` (see Zscaler section above) |
