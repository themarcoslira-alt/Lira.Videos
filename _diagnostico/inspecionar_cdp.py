import json
import urllib.request
import sys

for host in ["[::1]", "127.0.0.1", "localhost"]:
    url = f"http://{host}:9222/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode())
            print(f"HOST: {host} -> {len(data)} tabs")
            for t in data:
                print(f"  [{t.get('type')}] {t.get('title')} | {t.get('url')}")
    except Exception as e:
        print(f"HOST: {host} -> Error: {e}")
