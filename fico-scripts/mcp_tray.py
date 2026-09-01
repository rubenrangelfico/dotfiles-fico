#!/usr/bin/env python3
"""MCP Status — Windows system tray indicator for all Claude Code connectors."""
import base64, json, os, subprocess, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import pystray
from PIL import Image, ImageDraw

HOME        = Path.home()
FICO_DIR    = HOME / ".fico"
TOKENS_FILE = HOME / ".teams_tokens.json"
TB_FILE     = HOME / ".token-broker" / "tokens.json"
SN_CHECK_PY = FICO_DIR / "check-sn-health.py"
REFRESH_PY  = FICO_DIR / "refresh-tokens.py"
REFRESH_SN  = FICO_DIR / "refresh-sn-cookies.py"

WARN_MIN   = 15   # warn when token expires in < 15 min
POLL_SECS  = 30   # refresh every 30s

# ── Status constants ──────────────────────────────────────────────────────────
OK      = "ok"
WARN    = "warn"
ERROR   = "error"
UNKNOWN = "unknown"

# ── Icon drawing ──────────────────────────────────────────────────────────────

def _make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=color)
    return img

def _make_multi_icon(states: list[str]) -> Image.Image:
    """Quadrant icon: up to 4 colored dots showing individual MCP states."""
    COLOR = {OK: "#2ecc40", WARN: "#ffdc00", ERROR: "#ff4136", UNKNOWN: "#aaaaaa"}
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    positions = [(2, 2, 30, 30), (34, 2, 62, 30), (2, 34, 30, 62), (34, 34, 62, 62)]
    for i, state in enumerate(states[:4]):
        d.ellipse(positions[i], fill=COLOR.get(state, COLOR[UNKNOWN]))
    return img

ICON_GREEN  = _make_icon("#2ecc40")
ICON_YELLOW = _make_icon("#ffdc00")
ICON_RED    = _make_icon("#ff4136")

# ── JWT helpers ───────────────────────────────────────────────────────────────

def _jwt_remaining_min(token: str) -> int | None:
    try:
        pad = token.split(".")[1]
        pad += "=" * ((4 - len(pad) % 4) % 4)
        exp = json.loads(base64.urlsafe_b64decode(pad)).get("exp")
        if not exp:
            return None
        return (int(exp) - int(time.time())) // 60
    except Exception:
        return None

def _fmt_remaining(mins: int | None) -> str:
    if mins is None:
        return "?"
    if mins < 0:
        return "EXPIRED"
    if mins >= 60:
        return f"+{mins // 60}h{mins % 60:02d}m"
    return f"+{mins}m"

# ── Health checks ─────────────────────────────────────────────────────────────

def _check_teams() -> tuple[str, str]:
    """Returns (status, detail)."""
    try:
        data = json.loads(TOKENS_FILE.read_text())
        token = data.get("skypeToken") or data.get("bearerToken", "")
        mins = _jwt_remaining_min(token)
        if mins is None:
            return ERROR, "no token"
        if mins < 0:
            return ERROR, "token EXPIRED"
        if mins < WARN_MIN:
            return WARN, f"expires in {_fmt_remaining(mins)}"
        return OK, f"token {_fmt_remaining(mins)}"
    except Exception as e:
        return ERROR, str(e)[:50]


def _check_office365() -> tuple[str, str]:
    try:
        data  = json.loads(TOKENS_FILE.read_text())
        token = data.get("graphToken", "")
        mins  = _jwt_remaining_min(token)
        if mins is None:
            return ERROR, "no graph token"
        if mins < 0:
            return ERROR, "graph token EXPIRED"
        if mins < WARN_MIN:
            return WARN, f"expires in {_fmt_remaining(mins)}"
        return OK, f"token {_fmt_remaining(mins)}"
    except Exception as e:
        return ERROR, str(e)[:50]


def _check_servicenow() -> tuple[str, str]:
    try:
        result = subprocess.run(
            [sys.executable, str(SN_CHECK_PY)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return OK, "HTTP 200"
        out = (result.stderr or result.stdout or "").strip()[-60:]
        return ERROR, out or f"exit {result.returncode}"
    except subprocess.TimeoutExpired:
        return ERROR, "timeout"
    except Exception as e:
        return ERROR, str(e)[:50]


def _check_zoom() -> tuple[str, str]:
    try:
        tb   = json.loads(TB_FILE.read_text())
        data = tb.get("zoom-mcp", {}).get("data", {})
        if not data:
            return ERROR, "no zoom-mcp token"
        ts_raw = data.get("timestamp")
        if ts_raw:
            ts = datetime.fromtimestamp(int(ts_raw) / 1000, tz=timezone.utc) if int(ts_raw) > 1e10 else datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h > 24:
                return WARN, f"cookies {int(age_h)}h old"
            return OK, f"cookies {int(age_h)}h old"
        return UNKNOWN, "no timestamp"
    except Exception as e:
        return ERROR, str(e)[:50]


def _check_all() -> dict[str, tuple[str, str]]:
    return {
        "Teams":        _check_teams(),
        "Office 365":   _check_office365(),
        "ServiceNow":   _check_servicenow(),
        "Zoom":         _check_zoom(),
    }

# ── Auto-fix ─────────────────────────────────────────────────────────────────

_fix_lock = threading.Lock()
_fixing   = False

def _run_fix(cmd: list[str], label: str):
    global _fixing
    if not _fix_lock.acquire(blocking=False):
        return
    _fixing = True
    try:
        subprocess.run(cmd, timeout=60, capture_output=True)
    except Exception:
        pass
    finally:
        _fixing = False
        _fix_lock.release()


def _fix_teams_o365(icon, item):
    threading.Thread(
        target=_run_fix,
        args=([sys.executable, str(REFRESH_PY)], "refresh-tokens"),
        daemon=True,
    ).start()


def _fix_servicenow(icon, item):
    threading.Thread(
        target=_run_fix,
        args=([sys.executable, str(REFRESH_SN)], "refresh-sn"),
        daemon=True,
    ).start()


def _fix_all(icon, item):
    _fix_teams_o365(icon, item)
    _fix_servicenow(icon, item)

# ── Icon & title selection ────────────────────────────────────────────────────

def _overall(statuses: dict) -> str:
    vals = [s for s, _ in statuses.values()]
    if any(v == ERROR for v in vals):
        return ERROR
    if any(v == WARN for v in vals):
        return WARN
    return OK

def _icon_for(status: str) -> Image.Image:
    return {OK: ICON_GREEN, WARN: ICON_YELLOW, ERROR: ICON_RED}.get(status, ICON_YELLOW)

def _label(name: str, status: str, detail: str) -> str:
    sym = {"ok": "✅", "warn": "⚠️", "error": "❌", "unknown": "❓"}.get(status, "❓")
    return f"{sym} {name}: {detail}"

# ── Menu build ────────────────────────────────────────────────────────────────

def _noop(icon, item): pass


def _build_menu(statuses: dict, fixing: bool):
    items = []

    # ── Status rows (always visible) ──
    for name, (status, detail) in statuses.items():
        items.append(pystray.MenuItem(_label(name, status, detail), _noop, enabled=False))

    items.append(pystray.Menu.SEPARATOR)

    # ── Fix actions (always visible, disabled while a fix is running) ──
    items.append(pystray.MenuItem(
        "\U0001f504 Refreshing Teams / O365..." if fixing else "Refresh Teams / O365 tokens",
        _noop if fixing else _fix_teams_o365,
        enabled=not fixing,
    ))
    items.append(pystray.MenuItem(
        "\U0001f504 Refreshing ServiceNow..." if fixing else "Refresh ServiceNow cookies",
        _noop if fixing else _fix_servicenow,
        enabled=not fixing,
    ))
    items.append(pystray.MenuItem(
        "\U0001f504 Fixing all..." if fixing else "Fix All",
        _noop if fixing else _fix_all,
        enabled=not fixing,
    ))

    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("Quit MCP Status", lambda icon, item: icon.stop()))
    return pystray.Menu(*items)


# ── Refresh loop ──────────────────────────────────────────────────────────────

def _refresh_loop(icon: pystray.Icon):
    while True:
        time.sleep(POLL_SECS)
        try:
            statuses = _check_all()
            overall  = _overall(statuses)
            ok_count = sum(1 for s, _ in statuses.values() if s == OK)
            total    = len(statuses)
            icon.icon  = _make_multi_icon([s for s, _ in statuses.values()])
            icon.title = (
                f"MCP: all {total} OK" if overall == OK
                else f"MCP: {ok_count}/{total} OK — issues detected"
            )
            icon.menu = _build_menu(statuses, _fixing)
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    statuses = _check_all()
    overall  = _overall(statuses)
    ok_count = sum(1 for s, _ in statuses.values() if s == OK)
    total    = len(statuses)

    icon = pystray.Icon(
        name  = "MCP Status",
        icon  = _make_multi_icon([s for s, _ in statuses.values()]),
        title = f"MCP: {ok_count}/{total} OK",
        menu  = _build_menu(statuses, False),
    )
    threading.Thread(target=_refresh_loop, args=(icon,), daemon=True).start()
    icon.run()
