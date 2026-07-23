# -*- coding: utf-8 -*-
import sys, requests, json
sys.path.insert(0, 'C:\\ultracut3')
from config import PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY

print("=" * 60)
print("DIAGNOSTICO API - Busca para cena 1")
print("=" * 60)

query = "plant"
print("\nQuery unica: '%s'" % query)

# 1. Pexels photo
print("\n1. PEXELS PHOTO")
if PEXELS_API_KEY:
    try:
        r = requests.get("https://api.pexels.com/v1/search?query=%s&per_page=3" % query,
                         headers={"Authorization": PEXELS_API_KEY}, timeout=15)
        print("   Status: %d" % r.status_code)
        if r.status_code == 200:
            data = r.json()
            photos = data.get("photos", [])
            print("   Photos: %d" % len(photos))
            if photos:
                for p in photos[:3]:
                    print("     ID=%s  w=%s h=%s" % (p.get("id"), p.get("width"), p.get("height")))
                    print("     large: %s..." % str(p.get("src",{}).get("large",""))[:60])
            else:
                print("   NAO TEM FOTOS")
                print("   Keys:", list(data.keys()))
                print("   total_results:", data.get("total_results"))
        else:
            print("   Erro: %s" % r.text[:200])
    except Exception as e:
        print("   Exception: %s" % str(e)[:100])
else:
    print("   KEY VAZIA")

# 2. Pixabay
print("\n2. PIXABAY PHOTO")
if PIXABAY_API_KEY:
    try:
        r = requests.get("https://pixabay.com/api/?key=%s&q=%s&per_page=3&safesearch=true" % (PIXABAY_API_KEY, query), timeout=15)
        print("   Status: %d" % r.status_code)
        if r.status_code == 200:
            data = r.json()
            hits = data.get("hits", [])
            print("   Hits: %d" % len(hits))
            if hits:
                for h in hits[:3]:
                    print("     ID=%s  w=%s h=%s" % (h.get("id"), h.get("imageWidth"), h.get("imageHeight")))
                    print("     url: %s..." % str(h.get("largeImageURL",""))[:60])
            else:
                print("   NAO TEM HITS")
        else:
            print("   Erro: %s" % r.text[:200])
    except Exception as e:
        print("   Exception: %s" % str(e)[:100])
else:
    print("   KEY VAZIA")

# 3. Unsplash
print("\n3. UNSPLASH PHOTO")
if UNSPLASH_API_KEY:
    try:
        r = requests.get("https://api.unsplash.com/search/photos?query=%s&per_page=3&orientation=landscape" % query,
                         headers={"Authorization": "Client-ID " + UNSPLASH_API_KEY}, timeout=15)
        print("   Status: %d" % r.status_code)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            print("   Results: %d" % len(results))
            if results:
                for res in results[:3]:
                    print("     ID=%s  w=%s h=%s" % (res.get("id"), res.get("width"), res.get("height")))
            else:
                print("   NAO TEM RESULTS")
        else:
            print("   Erro: %s" % r.text[:200])
    except Exception as e:
        print("   Exception: %s" % str(e)[:100])
else:
    print("   KEY VAZIA")

print("\n" + "=" * 60)
print("Conclusao: as 3 APIs estao funcionando?")
print("Verifique os resultados acima.")