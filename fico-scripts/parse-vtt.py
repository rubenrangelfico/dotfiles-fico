#!/usr/bin/env python3
"""
Parse a Zoom WebVTT transcript file and output clean text with absolute MX timestamps.

Usage:
  parse-vtt.py <file.vtt>           # plain text, timestamps in MX (UTC-6)
  parse-vtt.py <file.vtt> --json    # JSON array of cue objects
  parse-vtt.py <file.vtt> --merge   # combine consecutive cues from same speaker
"""

import sys
import re
from datetime import datetime, timedelta, timezone
import json
import os

MX_TZ = timezone(timedelta(hours=-6))


def filename_utc(path):
    m = re.search(r'GMT(\d{8})-(\d{6})', os.path.basename(path))
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)


def parse_ts(s):
    parts = s.strip().split(':')
    h, m, sec = (parts if len(parts) == 3 else ['0'] + parts)
    return timedelta(hours=int(h), minutes=int(m), seconds=float(sec))


def parse_vtt(path):
    start_utc = filename_utc(path)
    with open(path, encoding='utf-8') as f:
        content = f.read()

    cues = []
    for block in re.split(r'\n{2,}', content):
        lines = block.strip().splitlines()
        # Find timestamp line
        ts_line = next((l for l in lines if '-->' in l), None)
        if not ts_line:
            continue
        ts_idx = lines.index(ts_line)
        rel = parse_ts(ts_line.split('-->')[0])

        text = ' '.join(lines[ts_idx + 1:]).strip()
        if not text:
            continue

        # "Speaker Name (Team): sentence" or "Speaker Name: sentence"
        m = re.match(r'^([^:]+?):\s+(.+)$', text, re.DOTALL)
        speaker = m.group(1).strip() if m else None
        body = m.group(2).strip() if m else text

        abs_mx = None
        if start_utc:
            abs_mx = (start_utc + rel).astimezone(MX_TZ).strftime('%H:%M:%S')

        cues.append({'rel': str(rel), 'abs': abs_mx, 'speaker': speaker, 'text': body})

    return cues, start_utc


def merge_consecutive(cues):
    merged = []
    for c in cues:
        if merged and merged[-1]['speaker'] == c['speaker']:
            merged[-1]['text'] += ' ' + c['text']
        else:
            merged.append(dict(c))
    return merged


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    path = args[0]
    as_json = '--json' in args
    do_merge = '--merge' in args

    cues, start_utc = parse_vtt(path)
    if do_merge:
        cues = merge_consecutive(cues)

    if start_utc:
        start_mx = start_utc.astimezone(MX_TZ).strftime('%Y-%m-%d %H:%M:%S MX')
        print(f"# Recording start: {start_mx}", file=sys.stderr)

    if as_json:
        print(json.dumps(cues, ensure_ascii=False, indent=2))
        return

    for c in cues:
        ts = f"[{c['abs']}]" if c['abs'] else f"[+{c['rel']}]"
        speaker = c['speaker'] or 'Unknown'
        print(f"{ts} {speaker}: {c['text']}")


if __name__ == '__main__':
    main()
