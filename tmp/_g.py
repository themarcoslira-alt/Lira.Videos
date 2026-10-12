import os, sys
pats = sys.argv[1].split("|")
roots = sys.argv[2].split(",")
out = []
for root in roots:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".venv", ".git", "chrome_profile", "node_modules", "__pycache__", "projetos", "logs", "tmp", "_backups_antigos", "assets_cache", "output")]
        for f in files:
            if f.endswith((".py", ".js", ".html", ".json")):
                p = os.path.join(base, f)
                try:
                    txt = open(p, encoding="utf-8", errors="replace").read().splitlines()
                except Exception:
                    continue
                for i, l in enumerate(txt):
                    if any(x in l for x in pats):
                        out.append("%s:%d: %s" % (p.replace("\\", "/"), i + 1, l.strip()[:170]))
txt = "\n".join(out)
open("tmp/_g_out.txt", "w", encoding="utf-8").write(txt)
print(len(out))
