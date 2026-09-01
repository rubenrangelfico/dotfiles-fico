#!/usr/bin/env python3
"""Watch ONE Teams channel and play a sound on each new message.

Cross-platform: Mac uses afplay, Windows uses winsound.
Configure TEAM_ID, CHANNEL_ID, and SOUND below for your setup.

Token source: ~/.teams_tokens.json
"""
import json, os, sys, time, base64, subprocess, platform, urllib.request, urllib.error, re
from datetime import datetime, timezone
from pathlib import Path

# ---------------- CONFIG (edit these) ----------------
TEAM_ID       = "YOUR_TEAM_ID"
CHANNEL_ID    = "YOUR_CHANNEL_ID"
CHANNEL_LABEL = "General"
# Sound path: Mac example below; Windows: use a .wav path or leave empty for default beep
SOUND_MAC     = str(Path.home() / "Library" / "Sounds" / "ICQ.mp3")
SOUND_WIN     = r"C:\Windows\Media\chimes.wav"  # built-in Windows sound; loud and clear
POLL_SECS     = 12
PAGE_SIZE     = 15
MSG_HOST      = "https://amer.ng.msg.teams.microsoft.com"
PLAY_ON_OWN_MESSAGES = False
SN_CHECK_INTERVAL    = 20 * 60
# -----------------------------------------------------

IS_MAC  = platform.system() == "Darwin"
IS_WIN  = platform.system() == "Windows"
HOME    = Path.home()
FICO_DIR       = HOME / ".fico"
TOKENS_FILE    = HOME / ".teams_tokens.json"
STATE_FILE     = FICO_DIR / "teams_channel_watch_state.json"
LOG_FILE       = FICO_DIR / "teams_channel_watch.log"
QUEUE_FILE     = FICO_DIR / "mim_incident_queue.json"
SN_REFRESH_PY  = FICO_DIR / "check-sn-health.py"

INC_RE = re.compile(r"INC\d{6,}")


def copy_to_clipboard(text):
    try:
        if IS_MAC:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
        elif IS_WIN:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
            p.communicate(text.encode("utf-16"))
    except Exception as e:
        log(f"WARN clipboard failed: {e}")


def send_notification(title, body):
    try:
        if IS_MAC:
            subprocess.Popen(["osascript", "-e",
                f'display notification {json.dumps(body)} with title {json.dumps(title)} sound name "Hero"'])
        elif IS_WIN:
            try:
                from win10toast import ToastNotifier
                ToastNotifier().show_toast(title, body, duration=5, threaded=True)
            except ImportError:
                pass  # win10toast optional; install with: pip install win10toast
    except Exception as e:
        log(f"WARN notification failed: {e}")


def play_sound():
    try:
        if IS_MAC:
            sound = SOUND_MAC
            if sound and os.path.exists(sound):
                subprocess.Popen(["afplay", sound])
            else:
                subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        elif IS_WIN:
            import winsound
            if SOUND_WIN and os.path.exists(SOUND_WIN):
                winsound.PlaySound(SOUND_WIN, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            # Linux fallback
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                             stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"WARN play_sound failed: {e}")


def enqueue_incidents(content, who, arrival):
    if not content:
        return
    incs = sorted(set(INC_RE.findall(content)))
    if not incs:
        return
    try:
        queue = json.load(open(QUEUE_FILE)) if QUEUE_FILE.exists() else []
    except Exception:
        queue = []
    already = {e.get("incident") for e in queue}
    new_incs = [inc for inc in incs if inc not in already]
    if not new_incs:
        return
    for inc in new_incs:
        queue.append({
            "incident": inc, "from": who, "arrival": arrival,
            "queued_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    queue = queue[-100:]
    json.dump(queue, open(QUEUE_FILE, "w"), indent=2)
    log("queued INC(s): %s" % ", ".join(new_incs))
    copy_to_clipboard("dame el quick overview de " + ", ".join(new_incs))
    send_notification("MIM General", "Nuevo: %s (de %s)" % (", ".join(new_incs), who))


_skype_cache = {"token": None, "exp": 0}
_my_oid = None
_sn_last_check = 0
FICO_TID = "f9465cb1-7889-4d9a-b552-fdd0addf0eb1"


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def jwt_claims(tok):
    try:
        seg = tok.split(".")[1]; seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(s):
    try:
        json.dump(s, open(STATE_FILE, "w"))
    except Exception as e:
        log("WARN could not save state: %s" % e)


def get_skype_token():
    global _my_oid
    now = time.time()
    if _skype_cache["token"] and now < _skype_cache["exp"] - 300:
        return _skype_cache["token"]
    d = json.load(open(TOKENS_FILE))
    oauth = d.get("skypeToken", "")
    if not oauth:
        raise RuntimeError("no skypeToken in tokens file")
    if _my_oid is None:
        _my_oid = jwt_claims(d.get("graphToken", "")).get("oid")
    exp = jwt_claims(oauth).get("exp", 0)
    if exp and now > exp:
        raise RuntimeError("OAuth skypeToken EXPIRED - run 'actualiza teams' to refresh")
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://authsvc.teams.microsoft.com/v1.0/authz",
                data=b"", method="POST",
                headers={"Authorization": "Bearer " + oauth, "Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=15).read())
            sk = r["tokens"]["skypeToken"]
            if jwt_claims(sk).get("tid") != FICO_TID:
                raise RuntimeError("exchanged skypeToken is NOT FICO tenant")
            skexp = jwt_claims(sk).get("exp", now + 3600)
            _skype_cache.update(token=sk, exp=skexp)
            return sk
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 401 and attempt < 2:
                log("authz 401 (attempt %d/3), retrying..." % (attempt + 1))
                time.sleep(2 + attempt * 3)
                continue
            raise
    raise last_err


def fetch_messages(sk):
    url = "%s/v1/users/ME/conversations/%s/messages?pageSize=%d" % (MSG_HOST, CHANNEL_ID, PAGE_SIZE)
    req = urllib.request.Request(url, headers={"Authentication": "skypetoken=" + sk})
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return resp.get("messages", [])


def parse_ts(s):
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        if "." in s:
            head, rest = s.split(".", 1)
            frac = ""
            for c in rest:
                if c.isdigit():
                    frac += c
                else:
                    rest = rest[len(frac):]
                    break
            s = head + "." + frac[:6] + rest
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_real_message(m):
    mt = (m.get("messagetype") or "")
    if not (mt.startswith("RichText") or mt.startswith("Text")):
        return False
    if not (m.get("content") or "").strip():
        return False
    return True


def is_own(m):
    if not _my_oid:
        return False
    return _my_oid.lower() in (m.get("from") or "").lower()


def maybe_check_sn_session():
    global _sn_last_check
    now = time.time()
    if now - _sn_last_check < SN_CHECK_INTERVAL:
        return
    _sn_last_check = now
    try:
        r = subprocess.run([sys.executable, str(SN_REFRESH_PY)],
                           capture_output=True, timeout=15)
        if r.returncode != 0:
            log("SN health check FAILED — SSO session expired, open ServiceNow in browser to refresh")
    except Exception as e:
        log(f"SN health check error: {e}")


def main():
    log("=== watcher started | channel=%s | poll=%ss ===" % (CHANNEL_LABEL, POLL_SECS))
    FICO_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    last_iso = state.get("last_arrival")
    last_dt = parse_ts(last_iso)
    primed = last_dt is not None
    consecutive_errs = 0

    while True:
        try:
            sk = get_skype_token()
            msgs = fetch_messages(sk)
            consecutive_errs = 0
            newest_dt = last_dt
            new_count = 0
            for m in msgs:
                if not is_real_message(m):
                    continue
                ts = parse_ts(m.get("originalarrivaltime") or m.get("composetime"))
                if ts is None:
                    continue
                if newest_dt is None or ts > newest_dt:
                    newest_dt = ts
                if primed and last_dt is not None and ts > last_dt:
                    if not PLAY_ON_OWN_MESSAGES and is_own(m):
                        continue
                    new_count += 1
                    who = m.get("imdisplayname") or "?"
                    log("NEW msg from %s @ %s" % (who, m.get("originalarrivaltime")))
                    enqueue_incidents(m.get("content"), who, m.get("originalarrivaltime"))
            if new_count > 0:
                play_sound()
            if newest_dt is not None and (last_dt is None or newest_dt > last_dt):
                last_dt = newest_dt
                save_state({"last_arrival": last_dt.astimezone(timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%S.%fZ")})
            if not primed:
                primed = True
                log("primed (baseline set, no sound for existing messages)")
        except urllib.error.HTTPError as e:
            consecutive_errs += 1
            body = ""
            try:
                body = e.read()[:200].decode("utf-8", "ignore")
            except Exception:
                pass
            log("HTTP %s %s" % (e.code, body))
            if e.code in (401, 403):
                _skype_cache["token"] = None
        except Exception as e:
            consecutive_errs += 1
            log("ERROR %s" % e)
        maybe_check_sn_session()
        sleep = POLL_SECS if consecutive_errs == 0 else min(POLL_SECS * (2 ** min(consecutive_errs, 4)), 300)
        time.sleep(sleep)


if __name__ == "__main__":
    main()
