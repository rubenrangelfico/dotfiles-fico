---
name: connector-health
description: Pre-flight health check on all AI connector sessions — Teams, ServiceNow, Office 365, and Grafana. Run at session start or before any ops workflow.
---

You are performing a pre-flight health check on all FICO AI connector sessions.

## Steps

Run all checks in parallel, then display a single status table.

### 1. Teams tokens
- Run `python3 ~/.fico/refresh-tokens.py`
- If exit 0: tokens are fresh. Record source as "refresh-tokens.py" and age from `~/.teams_tokens.json`.
- If exit non-zero: MSAL refresh token expired — report ❌ and advise running Playwright fallback.
- After success, run `python3 ~/.fico/exchange-skype.py` to validate skype exchange.

### 2. ServiceNow
- Run `bash ~/.fico/check-sn-health.sh`
- HTTP 200 = ✅. Anything else = ❌ with the status code.

### 3. Office 365 / Graph
- Check `mailGraphToken` expiry in `~/.teams_tokens.json`
- Decode the JWT `exp` claim. If valid and `tid = FICO tenant`: ✅. Expired or wrong tid: ❌.

### 4. Grafana
- Read `~/.token-broker/tokens.json`
- Extract `grafana-mcp.data.token` and `grafana-mcp.data.baseUrl`
- Verify the token is non-empty. Report the base URL in detail column.

## Output format

```
| System        | Status | Detail                            |
|---------------|--------|-----------------------------------|
| Teams         | ✅/❌  | token source + age (e.g. 4m old)  |
| ServiceNow    | ✅/❌  | instance URL                      |
| Office 365    | ✅/❌  | token source + age                |
| Grafana       | ✅/❌  | base URL                          |
```

After the table, list any remediation steps for failed connectors.
