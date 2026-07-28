# -*- coding: utf-8 -*-
import json, os, sys
sys.path.insert(0, "c:/ultracut3")

base = r"c:\ultracut3\projetos"
for nome in os.listdir(base):
    if nome.upper().startswith("YOU"):
        pasta = os.path.join(base, nome)
        print("Projeto:", nome)
        print()
        cenas_json = os.path.join(pasta, "cenas.json")
        story_json = os.path.join(pasta, "storyboard.json")
        if os.path.exists(cenas_json):
            print("=" * 60)
            print("PASSO 1 — PRIMEIRAS 15 CENAS (cenas.json)")
            print("=" * 60)
            cenas = json.load(open(cenas_json, encoding="utf-8"))
            print("ID  | start  | end    | dur    | texto")
            print("-" * 80)
            for c in cenas[:15]:
                cid = c.get("id", "?")
                st = c.get("start_time", 0)
                et = c.get("end_time", 0)
                dur = et - st
                txt = c.get("texto", "")[:80]
                print(f"{cid:3} | {st:6.2f} | {et:6.2f} | {dur:6.2f} | {txt}")
            print(f"\nTotal de cenas: {len(cenas)}")
            print(f"Duracao media: {sum(c['end_time']-c['start_time'] for c in cenas)/len(cenas):.1f}s")
        print()
        if os.path.exists(story_json):
            print("=" * 60)
            print("PASSO 1 (alt) — PRIMEIRAS 15 CENAS (storyboard.json)")
            print("=" * 60)
            sb = json.load(open(story_json, encoding="utf-8"))
            print("ID  | texto")
            print("-" * 80)
            for c in sb[:15]:
                cid = c.get("id", "?")
                txt = c.get("texto", "")[:80]
                print(f"{cid:3} | {txt}")
        print()
        transc_json = os.path.join(pasta, "roteiro_transcricao.json")
        if os.path.exists(transc_json):
            print("=" * 60)
            print("PASSO 3 — PRIMEIROS 20 SEGMENTOS DA TRANSCRICAO")
            print("=" * 60)
            data = json.load(open(transc_json, encoding="utf-8"))
            segs = data.get("segments", [])
            print("seg | start  | end    | texto")
            print("-" * 70)
            for i, s in enumerate(segs[:20]):
                print(f"{i+1:3} | {s['start']:6.2f} | {s['end']:6.2f} | {s.get('text','')[:60]}")
            print(f"\nTotal: {len(segs)} segmentos, duracao={data.get('duration',0):.1f}s")