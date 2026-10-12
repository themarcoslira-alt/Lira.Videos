import io, json, os, re

def dump(path, label):
    print("=" * 70)
    print("FILE:", path, "| exists:", os.path.exists(path))
    if not os.path.exists(path):
        return
    s = io.open(path, encoding="utf-8").read()
    L = s.splitlines()
    print("TOTAL LINES:", len(L))
    for i, l in enumerate(L):
        st = l.strip()
        if (st.startswith("def ") or st.startswith("class ")
                or st.startswith("CUSTO") or st.startswith("FLOW_CUSTOS")
                or st.startswith("_LOCK") or "LOCK =" in st
                or st.startswith("MARCADOR") or st.startswith("PROJETOS_DIR")
                or st.startswith("LOGS_DIR") or st.startswith("BASE_DIR")):
            print(f"{i+1:5d} {l}")

dump("services/flow_account_manager.py", "FAM")

# scene_plan_service API
print("=" * 70)
s = io.open("services/scene_plan_service.py", encoding="utf-8").read()
L = s.splitlines()
print("SCENE_PLAN_SERVICE TOTAL", len(L))
for i, l in enumerate(L):
    st = l.strip()
    if st.startswith("def ") or st.startswith("CAMPOS_EDITAVEIS") or "=" in st and re.match(r"^[A-Z_]+ =", st):
        if st.startswith("def ") or st.startswith("CAMPOS_EDITAVEIS"):
            print(f"{i+1:5d} {l}")

# api_v2 relevant defs
print("=" * 70)
s = io.open("services/api_v2.py", encoding="utf-8").read()
L = s.splitlines()
print("API_V2 TOTAL", len(L))
for i, l in enumerate(L):
    if "iniciar_fila" in l or "marcar_hook_video" in l or "def v2_producao" in l:
        print(f"{i+1:5d} {l}")
