import json, pathlib

proj = sorted(pathlib.Path('projetos').iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[0]
sb = proj / 'storyboard.json'
if sb.exists():
    d = json.loads(sb.read_text(encoding='utf-8'))
    cenas = d if isinstance(d, list) else d.get('storyboard', [])
    c = cenas[2] if len(cenas) > 2 else cenas[0]
    print('projeto:', proj.name)
    print('scene_type:', c.get('scene_type'))
    print('keywords:', c.get('keywords', []))
    print('search_queries:', c.get('search_queries', [])[:2])
else:
    print('storyboard.json nao encontrado em', proj.name)