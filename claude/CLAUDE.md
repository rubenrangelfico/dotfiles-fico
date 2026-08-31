# Session Auto-Setup

At the start of every new session, before doing anything else, automatically run ALL of the following steps in parallel:

## Step 1 — Teams + Office 365 (refresh-tokens.py first, Chrome only for MFA re-auth)

**Primary path: run `~/.fico/refresh-tokens.py`. Playwright is the fallback, needed only when the MSAL refresh token has expired (roughly quarterly MFA re-auths). Note: Playwright opens a separate browser — Teams login + MFA must be completed there by the user when prompted.**

### CRITICAL — tenant check

The user has TWO identities under the same email:
- Tenant **FICO** `YOUR_FICO_TENANT_ID` (oid `YOUR_FICO_OID`) ← **always use this one**
- Guest tenant `YOUR_GUEST_TENANT_ID` ← **NEVER use this**

All tokens (graph, bearer, skype) MUST have `tid = YOUR_FICO_TENANT_ID`. A skypeToken from the wrong tenant causes `HTTP 403 AclCheckFailed`. Verify the `tid` (JWT claim) of EVERY token before using or writing it.

### 1a — Fast path: run refresh-tokens.py (no Chrome required)

```bash
python3 ~/.fico/refresh-tokens.py
```

This script reads `msalRefreshToken` from `~/.teams_tokens.json` and mints fresh `graphToken`, `mailGraphToken`, `bearerToken`, and `skypeToken` in ~3 seconds using the Azure AD token endpoint (no browser needed). It rotates the refresh token on each run and writes all updated tokens back to the file.

**If the script exits 0 and prints `Saved ~/.teams_tokens.json`:** all tokens are refreshed — skip Steps 1b and 1b-net and proceed directly to Step 1c using the `skypeToken` just written to the file.

**If the script fails** (HTTP 400/401, or `msalRefreshToken missing`): the MSAL refresh token is expired or missing — proceed to Step 1b (Chrome capture) to re-seed it.

### 1b — Playwright fallback: extract tokens from Teams tab (only if Step 1a fails)

1. Call `browser_tabs(action="list")` — look for a tab with `teams.microsoft.com`
2. If none found, call `browser_navigate(url="https://teams.microsoft.com")`. If Teams shows a login page, wait for the user to complete MFA in the Playwright browser window, then `browser_wait_for(text="Chats")` timeout 60000
3. Call `browser_evaluate` with this function — reads the MSAL cache and **filters strictly by the FICO tenant**:
   ```javascript
   () => {
     const FICO_TID = 'YOUR_FICO_TENANT_ID';
     function tid(tok){ try{ let p=tok.split('.')[1]; p+='='.repeat((4-p.length%4)%4); return JSON.parse(atob(p.replace(/-/g,'+').replace(/_/g,'/'))).tid; }catch(e){ return null; } }
     const result = {};
     const now = Math.floor(Date.now() / 1000);
     for (let i = 0; i < localStorage.length; i++) {
       const key = localStorage.key(i);
       if (!key) continue;
       if (!key.toLowerCase().includes('accesstoken')) continue;
       try {
         const v = JSON.parse(localStorage.getItem(key) || '');
         if (!v || !v.secret) continue;
         const exp = parseInt(v.expiresOn || v.extended_expires_on || 0);
         if (exp && exp < now) continue;
         if (tid(v.secret) !== FICO_TID) continue;
         const target = (v.target || '').toLowerCase();
         if ((target.includes('graph.microsoft.com') || target.includes('https://graph.microsoft')) && !result.graphToken) {
           result.graphToken = v.secret; result.graphTokenExp = exp;
         }
         if ((target.includes('chatsvc') || target.includes('ump.teams') || target.includes('ic3.teams')) && !result.bearerToken) {
           result.bearerToken = v.secret;
         }
       } catch(e) {}
     }
     return result;
   }
   ```
4. After capturing tokens: close the Playwright Teams tab with `browser_tabs(action="close")`.

After capturing tokens via Playwright, also capture the MSAL refresh token and save it so Step 1a works next session:
```javascript
() => {
  const result = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (!key.toLowerCase().includes('refreshtoken')) continue;
    try {
      const v = JSON.parse(localStorage.getItem(key) || '');
      if (v && v.secret && !result.refreshToken) {
        result.refreshToken = v.secret;
        result.clientId = (key.split('-')[0] || '').replace('{', '');
      }
    } catch(e) {}
  }
  return result;
}
```
Save `refreshToken` → `msalRefreshToken` and `clientId` → `msalClientId` in `~/.teams_tokens.json`.

### 1b-net — Capture the FICO skypeToken from a network request (Teams v2)

Only needed when Step 1a failed and Step 1b Playwright capture is running.

1. On the open Teams Playwright tab, call `browser_network_requests(static=false, filter="authsvc")`.
2. Find a request `POST https://teams.microsoft.com/api/authsvc/v1.0/authz` with status **200**.
3. Call `browser_network_request(index=N, part="request-headers")` and read the **request header** `authorization: Bearer <TOKEN>`.
4. Verify the `<TOKEN>` JWT claims: `aud` = `https://api.spaces.skype.com` AND `tid` = `YOUR_FICO_TENANT_ID`.
5. That `<TOKEN>` is the OAuth skypeToken.

### 1c — Exchange skypeToken for real Skype Exchange Token

```bash
python3 ~/.fico/exchange-skype.py
```

Use the output as the skypeToken passed to `set_tokens`.

### 1d — Save and apply all tokens
- **Before writing/applying**: assert each token's `tid` claim is `YOUR_FICO_TENANT_ID`. If any token's tid is the guest tenant, DISCARD it.
- Call `set_tokens` with **exchanged** skypeToken (from Step 1c), bearerToken, region: "amer"
- Call `set_office365_tokens` with graph_token: graphToken

### 1e — Teams MCP proxy file (one-time fix, idempotent)
```bash
mkdir -p ~/.teams-mcp-proxy && [ -f ~/.teams-mcp-proxy/session-state.json ] || echo '{}' > ~/.teams-mcp-proxy/session-state.json
```

### 1f — Capture Mail-scoped Graph token from Playwright Outlook (fixes desktop mail read/send)

**Skip if `mailGraphToken` in `~/.teams_tokens.json` is valid.**

1. `browser_navigate` to `https://outlook.cloud.microsoft/mail/inbox`
2. `wait_for` ["Inbox","Focused","Other"] timeout 15000
3. `browser_network_requests(filter="graph.microsoft.com/v1.0/me", static=false)`
4. Find a request to a mail endpoint with status 200
5. Extract `Authorization: Bearer <TOKEN>` header
6. Verify JWT: `tid = YOUR_FICO_TENANT_ID` AND `scp` contains `Mail.Read`
7. Save as `mailGraphToken` in `~/.teams_tokens.json`
8. Call `set_office365_tokens(graph_token=mailGraphToken)`
9. Close the Playwright Outlook page

## Step 2 — ServiceNow
ServiceNow uses HTTP Basic Auth. No token to set. Verify it's live:
1. Run via Bash: `bash ~/.fico/check-sn-health.sh`
2. 200 = ServiceNow ✅.

## Step 3 — Grafana
1. Read `~/.token-broker/tokens.json`
2. Extract `grafana-mcp.data.token`, `grafana-mcp.data.token_type`, and `grafana-mcp.data.baseUrl`
3. Call `set_grafana_token` with token, token_type, and base_url

## Step 4 — Show Connection Status
After completing all setup steps, show the user a connection status table:

| System | Status | Detail |
|---|---|---|
| Teams | ✅/❌ | token source + age |
| ServiceNow | ✅/❌ | instance URL |
| Office 365 | ✅/❌ | token source + age |
| Grafana | ✅/❌ | base URL |
