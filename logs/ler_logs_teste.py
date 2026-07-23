import sys, json
sys.path.insert(0, 'C:\\ultracut3')
from services.event_logger import EVENTS_FILE

lines = open(str(EVENTS_FILE), 'r', encoding='utf-8').readlines()
print(f'Total lines: {len(lines)}')

category_counts = {}
warns = 0
errors = 0
last_25 = []

for l in lines:
    l = l.strip()
    if not l:
        continue
    # Trata linhas com multiplos JSONs grudados
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(l):
        try:
            e, end = decoder.raw_decode(l, pos)
            cat = e.get('category', '?')
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if e.get('level') == 'WARN':
                warns += 1
            if e.get('level') == 'ERROR':
                errors += 1
            last_25.append(e)
            pos = end
        except json.JSONDecodeError:
            break

last_25 = last_25[-25:]

print()
print('Categories:')
for k, v in sorted(category_counts.items()):
    print(f'  {k}: {v}')
print(f'WARN count: {warns}')
print(f'ERROR count: {errors}')

print()
print('=== Last 25 events ===')
for e in last_25:
    print(f"  [{e.get('ts','')}] [{e.get('category','?')}] [{e.get('level','?')}] {e.get('message','')}")