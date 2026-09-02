#!/usr/bin/env python3
"""
FICO connection-status page generator.

Reads a JSON payload describing each system's live status and writes a
polished HTML dashboard to ~/.fico/fico-status.html.

Usage:
    echo '<json>' | python3 ~/.fico/gen-fico-status.py
    python3 ~/.fico/gen-fico-status.py path/to/data.json

JSON shape:
{
  "systems": [
    {"name": "Teams", "state": "ok|partial|down",
     "label": "CONNECTED", "detail": "Chrome MSAL · amer"},
    ...
  ]
}

If no input is given, a sane default snapshot is used.
"""
import sys, json, os, html
from datetime import datetime

OUT = os.path.expanduser("~/.fico/fico-status.html")

DEFAULT = {
    "systems": [
        {"name": "Teams",           "state": "ok",      "label": "Connected", "detail": "Chrome MSAL (fresh) · region amer"},
        {"name": "Office 365",      "state": "ok",      "label": "Connected", "detail": "Chrome MSAL (fresh) · Graph API"},
        {"name": "ServiceNow",      "state": "ok",      "label": "Connected", "detail": "ficoitservices.service-now.com"},
        {"name": "Grafana",         "state": "ok",      "label": "Connected", "detail": "token-broker session"},
        {"name": "Jira / Atlassian","state": "ok",      "label": "Connected", "detail": "fico-prod.atlassian.net (Cloud)"},
        {"name": "Bitbucket",       "state": "partial", "label": "Partial",   "detail": "project FTB ok · project TDO 401"},
    ]
}

STATE_META = {
    "ok":      {"color": "#2ee6a6", "glow": "rgba(46,230,166,.55)"},
    "partial": {"color": "#ffb454", "glow": "rgba(255,180,84,.55)"},
    "down":    {"color": "#ff5c6c", "glow": "rgba(255,92,108,.55)"},
}

def card(s):
    st = s.get("state", "down")
    m = STATE_META.get(st, STATE_META["down"])
    name = html.escape(s.get("name", "—"))
    label = html.escape(s.get("label", st.upper()))
    detail = html.escape(s.get("detail", ""))
    return f"""
      <div class="card {st}">
        <div class="card-glow"></div>
        <div class="card-row">
          <div class="card-name">
            <span class="dot"></span>{name}
          </div>
          <span class="badge">{label}</span>
        </div>
        <div class="card-detail">{detail}</div>
      </div>"""

def build(data):
    systems = data.get("systems", [])
    n_ok = sum(1 for s in systems if s.get("state") == "ok")
    n_part = sum(1 for s in systems if s.get("state") == "partial")
    n_down = sum(1 for s in systems if s.get("state") == "down")
    ts = datetime.now().strftime("%Y-%m-%d · %H:%M:%S")
    cards = "\n".join(card(s) for s in systems)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FICO · Connection Status</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
  :root {{
    --bg0:#070a12; --bg1:#0d1320; --card:#121a2b; --line:#22304a;
    --text:#eef3fb; --muted:#8da2c0; --accent:#5b9dff;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ height:100%; }}
  body {{
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    color:var(--text);
    background:
      radial-gradient(1100px 520px at 50% -8%, #1a2740 0%, transparent 60%),
      radial-gradient(900px 600px at 110% 110%, #122033 0%, transparent 55%),
      linear-gradient(180deg, var(--bg1) 0%, var(--bg0) 100%);
    min-height:100vh;
    padding:56px 24px 40px;
    display:flex; flex-direction:column; align-items:center;
  }}
  .brand {{
    display:flex; align-items:center; gap:14px; margin-bottom:6px;
  }}
  .brand .mark {{
    font-weight:800; font-size:34px; letter-spacing:-1px;
    background:linear-gradient(135deg,#5b9dff,#2ee6a6);
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  .brand .live {{
    font:600 11px 'JetBrains Mono',monospace; color:var(--muted);
    border:1px solid var(--line); border-radius:999px; padding:5px 12px;
    display:flex; align-items:center; gap:7px;
  }}
  .brand .live::before {{
    content:""; width:7px; height:7px; border-radius:50%;
    background:#2ee6a6; box-shadow:0 0 8px #2ee6a6;
    animation:pulse 2s infinite;
  }}
  .sub {{ color:var(--muted); font-size:13.5px; margin-bottom:30px; }}
  .sub b {{ color:var(--text); font-weight:600; }}
  .chips {{ display:flex; gap:12px; margin-bottom:30px; flex-wrap:wrap; justify-content:center; }}
  .chip {{
    background:rgba(255,255,255,.03); border:1px solid var(--line);
    border-radius:12px; padding:10px 18px; font-size:13px; font-weight:600;
    display:flex; align-items:center; gap:9px; backdrop-filter:blur(6px);
  }}
  .chip .n {{ font:800 18px 'Inter'; }}
  .chip.ok .n {{ color:#2ee6a6; }} .chip.part .n {{ color:#ffb454; }} .chip.down .n {{ color:#ff5c6c; }}
  .chip .lbl {{ color:var(--muted); }}
  .grid {{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
    gap:18px; width:100%; max-width:1000px;
  }}
  .card {{
    position:relative; background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01));
    border:1px solid var(--line); border-radius:18px; padding:22px 22px 20px;
    overflow:hidden; backdrop-filter:blur(10px);
    transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
  }}
  .card:hover {{ transform:translateY(-3px); border-color:var(--accent); box-shadow:0 12px 34px rgba(0,0,0,.4); }}
  .card::before {{ content:""; position:absolute; left:0; top:0; bottom:0; width:4px; }}
  .card-glow {{ position:absolute; right:-40px; top:-40px; width:140px; height:140px; border-radius:50%; filter:blur(50px); opacity:.5; }}
  .card.ok::before {{ background:#2ee6a6; }}   .card.ok .card-glow {{ background:#2ee6a6; }}
  .card.partial::before {{ background:#ffb454; }} .card.partial .card-glow {{ background:#ffb454; }}
  .card.down::before {{ background:#ff5c6c; }}  .card.down .card-glow {{ background:#ff5c6c; }}
  .card-row {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; position:relative; }}
  .card-name {{ font-size:18px; font-weight:700; display:flex; align-items:center; gap:11px; }}
  .dot {{ width:11px; height:11px; border-radius:50%; }}
  .card.ok .dot {{ background:#2ee6a6; box-shadow:0 0 12px rgba(46,230,166,.7); animation:pulse 2.4s infinite; }}
  .card.partial .dot {{ background:#ffb454; box-shadow:0 0 12px rgba(255,180,84,.7); animation:pulse 2.4s infinite; }}
  .card.down .dot {{ background:#ff5c6c; box-shadow:0 0 12px rgba(255,92,108,.7); }}
  .badge {{ font:700 10.5px 'Inter'; text-transform:uppercase; letter-spacing:.6px; padding:5px 11px; border-radius:8px; }}
  .card.ok .badge {{ background:rgba(46,230,166,.14); color:#2ee6a6; }}
  .card.partial .badge {{ background:rgba(255,180,84,.14); color:#ffb454; }}
  .card.down .badge {{ background:rgba(255,92,108,.14); color:#ff5c6c; }}
  .card-detail {{ color:var(--muted); font:400 13px 'JetBrains Mono',monospace; line-height:1.5; position:relative; }}
  .foot {{ margin-top:34px; max-width:760px; text-align:center; color:var(--muted); font-size:12.5px; line-height:1.7; }}
  .foot code {{ font:600 11.5px 'JetBrains Mono',monospace; color:var(--accent); background:rgba(91,157,255,.1); border:1px solid var(--line); padding:2px 7px; border-radius:6px; }}
  @keyframes pulse {{ 0%,100%{{opacity:1;}} 50%{{opacity:.45;}} }}
</style>
</head>
<body>
  <div class="brand">
    <div class="mark">FICO</div>
    <div class="live">LIVE · {ts}</div>
  </div>
  <div class="sub">Connection Status &middot; <b>Senior Incident Manager</b> workspace</div>

  <div class="chips">
    <div class="chip ok"><span class="n">{n_ok}</span><span class="lbl">Connected</span></div>
    <div class="chip part"><span class="n">{n_part}</span><span class="lbl">Partial</span></div>
    <div class="chip down"><span class="n">{n_down}</span><span class="lbl">Down</span></div>
  </div>

  <div class="grid">
{cards}
  </div>

  <div class="foot">
    Session-token systems (Teams, Office&nbsp;365, ServiceNow, Grafana) refresh on <code>FICO</code> / <code>actualiza&nbsp;teams</code>.
    Jira and Bitbucket use static tokens (health-check only).<br>
    Regenerated automatically every time you say <code>FICO</code>.
  </div>
</body>
</html>"""

def main():
    data = None
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
    if not data:
        data = DEFAULT
    htmltext = build(data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(htmltext)
    print(OUT)

if __name__ == "__main__":
    main()
