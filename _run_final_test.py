import sys, json, subprocess, os, time

# Run the test via subprocess with unlimited timeout
cmd = [
    r"C:\ultracut3\.venv\Scripts\python.exe",
    "-c",
    "import sys; sys.path.insert(0,'C:/ultracut3'); from services.broll_director import gerar_storyboard; import json; r=gerar_storyboard('2026',usar_claude=True); open('C:/ultracut3/teste4_output.json','w',encoding='utf-8').write(json.dumps(r,indent=2,ensure_ascii=False))"
]

# Also write results
out_cmd = [
    r"C:\ultracut3\.venv\Scripts\python.exe",
    "-c",
    "import sys; sys.path.insert(0,'C:/ultracut3'); from services.event_logger import ler_eventos; import json; eventos=ler_eventos(2000); ultimos=[e for e in eventos if e.get('category')=='CLAUDE' or 'Storyboard finalizado' in e.get('message','')]; [print(json.dumps(e,ensure_ascii=False)) for e in ultimos[-10:]]"
]

print("Running test (may take up to 5 minutes)...", flush=True)
proc = subprocess.run(cmd, cwd="C:/ultracut3", capture_output=True, text=True, timeout=300)
print("STDOUT:", proc.stdout[:500] if proc.stdout else "(empty)", flush=True)
print("STDERR:", proc.stderr[:500] if proc.stderr else "(empty)", flush=True)

# Check output
if os.path.exists("C:/ultracut3/teste4_output.json"):
    with open("C:/ultracut3/teste4_output.json") as f:
        d = json.load(f)
    print("\nRESULT:", flush=True)
    print("  camada:", d.get("camada"), flush=True)
    print("  confiavel:", d.get("camada_confiavel"), flush=True)
    print("  claude_ok:", d.get("claude_ok"), flush=True)
    print("  local_fallback:", d.get("local_fallback"), flush=True)
    cena3 = [s for s in d.get("storyboard", []) if s.get("id") == 3]
    if cena3:
        print("\nCENA 3:", flush=True)
        print(json.dumps(cena3[0], indent=2, ensure_ascii=False), flush=True)
    else:
        print("\nCENA3 not found - checking ids:", flush=True)
        ids = set(str(s.get("id"))[:5] for s in d.get("storyboard", [])[:5])
        print("ids sample:", ids, flush=True)
else:
    print("\nteste4_output.json NOT created", flush=True)

# Show last logs
print("\nLAST LOGS:", flush=True)
proc2 = subprocess.run(out_cmd, cwd="C:/ultracut3", capture_output=True, text=True, timeout=30)
print(proc2.stdout[-2000:] if proc2.stdout else "(empty)", flush=True)