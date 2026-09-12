import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

events_file = Path('logs/events.jsonl')
lines = open(str(events_file), 'r', encoding='utf-8', errors='ignore').readlines()
print(f'Total lines: {len(lines)}')

matches = []
for l in lines:
    l = l.strip()
    if not l:
        continue
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(l):
        try:
            e, end = decoder.raw_decode(l, pos)
            pos = end
            if not isinstance(e, dict):
                continue
            msg = str(e.get('message') or '')
            if 'fallback video' in msg.lower() or 'crédito' in msg.lower() and 'vídeo' in msg.lower():
                matches.append(e)






        except json.JSONDecodeError:
            break


print(f'Found {len(matches)} matches:')
for m in matches[-50:]:
    print(f"[{m.get('ts','')}] [{m.get('category','?')}] [{m.get('level','?')}] {m.get('message','')}")