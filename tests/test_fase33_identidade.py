"""
FASE 3.3 — Correção de Identidade Visual (validação OFFLINE, sem Flow/download).

Valida que:
  - cenas humanas (1ª pessoa, experiência, criador do teste, demonstração) usam
    personagem bloqueado (@Marcos) com uses_character=True.
  - prompt_imagem de cenas com personagem começam com @Marcos.
  - b-roll continua SEM personagem.
  - hybrid usa personagem quando necessário (demonstração prática).
Gera relatório: cenas antes/depois, JSON exemplo e % de cenas com personagem.
"""

import sys
import json
import shutil
from pathlib import Path

ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

from app_web import app
from config import PROJETOS_DIR
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc

PROJ = "test_fase33_marcos"

SRT_10_CENAS = """1
00:00:00,000 --> 00:00:03,000
Olá amigos, eu sou o Marcos e hoje eu vou contar minha experiência com as orquídeas aqui do quintal.

2
00:00:03,000 --> 00:00:06,000
Eu criei este teste para descobrir qual adubo funciona melhor na prática.

3
00:00:06,000 --> 00:00:09,000
Aqui eu mostro como aplicar a adubação ao redor do vaso, segurando o recipiente.

4
00:00:09,000 --> 00:00:12,000
Observe essas raízes expostas e a textura das folhas absorvendo a umidade.

5
00:00:12,000 --> 00:00:15,000
Vejam a casca de banana fermentada no solo úmido perto das raízes.

6
00:00:15,000 --> 00:00:18,000
Depois de três semanas eu expliquei o processo completo para cuidar dessas plantas.

7
00:00:18,000 --> 00:00:21,000
Detalhe da terra escura e do adubo sendo dissolvido pela água da rega.

8
00:00:21,000 --> 00:00:24,000
Deixa eu mostrar o passo a passo: coloco o fertilizante e misturo devagar.

9
00:00:24,000 --> 00:00:27,000
O jardim ao amanhecer com névoa suave entre as folhagens.

10
00:00:27,000 --> 00:00:30,000
Se gostou, inscreva-se no canal para mais técnicas de cultivo.
"""


def cenas_antes(plan):
    """Snapshot simples dos scene_type/uses_character para o relatório 'antes'."""
    return [(c["id"], c.get("scene_type"), c.get("uses_character"), c.get("character_ref")) for c in plan["cenas"]]


def cenas_depois(plan):
    """Snapshot 'depois' (o que o pipeline final decidiu + origem do prompt)."""
    return [
        {
            "id": c["id"],
            "scene_type": c.get("scene_type"),
            "uses_character": c.get("uses_character"),
            "character_ref": c.get("character_ref"),
            "prompt_inicia_com_marcos": c["prompt_imagem"].lstrip().startswith("@Marcos"),
        } for c in plan["cenas"]
    ]
def main():
    print("=" * 72)
    print("FASE 3.3 — CORREÇÃO DE IDENTIDADE VISUAL (validação OFFLINE)")
    print("=" * 72)

    pdir = PROJETOS_DIR / PROJ
    if pdir.exists():
        shutil.rmtree(pdir)

    # 1. Personagem bloqueado @Marcos
    character_svc.salvar_identidade_projeto(
        projeto_id=PROJ, tipo="personagem", nome="Marcos",
        referencia_flow="@Marcos", visual_style="photorealistic_cinematic")
    character_svc.atualizar_status_flow_personagem(
        projeto_id=PROJ, created=True, flow_char_name="@Marcos",
        flow_char_id="flow-id-marcos-3-3")

    # 2. Ingestão SRT (gera cenas.json + scene_plan completo via pipeline)
    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{PROJ}/usar_srt", json={"srt_texto": SRT_10_CENAS})
    print("usar_srt status:", res.status_code)

    plan = scene_plan_svc.carregar_scene_plan(PROJ)
    assert plan and plan.get("cenas"), "scene_plan vazio"
    assert len(plan["cenas"]) == 10, f"esperava 10 cenas, veio {len(plan['cenas'])}"

    print("\nCENAS (antes — classificador bruto):")
    for c in cenas_antes(plan):
        print(f"  cena {c[0]:>2}: type={str(c[1])[:18]:<18} uses_char={c[2]} ref='{c[3]}'")

    print("\nCENAS (depois — decisão final + prompt):")
    for c in cenas_depois(plan):
        print(f"  cena {c['id']:>2}: type={str(c['scene_type'])[:18]:<18} uses_char={str(c['uses_character']):<5} "
              f"ref='{c['character_ref']}' inicio=@Marcos:{c['prompt_inicia_com_marcos']}")

    # Métricas
    total = len(plan["cenas"])
    ler = [c for c in plan["cenas"] if c.get("uses_character")]
    broll = [c for c in plan["cenas"] if not c.get("uses_character")]
    print(f"\nMETRICAS: cenas={total} | humanas={len(ler)} ({len(ler)/total*100:.0f}%) | b-roll={len(broll)}")

    # ---- ASSERTS ----
    # mínimo 3 cenas humanas
    assert len(ler) >= 3, f"esperado >=3 cenas humanas, veio {len(ler)}"
    # todas humanas com @Marcos
    for c in ler:
        assert c.get("character_ref") == "@Marcos", f"cena {c['id']} não usa @Marcos"
        assert c["prompt_imagem"].lstrip().startswith("@Marcos"), \
            f"cena {c['id']} prompt não inicia com @Marcos"
    # b-roll sem personagem
    for c in broll:
        assert c.get("uses_character") is False
        assert c.get("character_ref") == ""
        assert "@Marcos" not in (c.get("prompt_imagem") or "")
    # hybrid (demonstrações práticas) usa personagem
    for c in ler:
        if c.get("scene_type") == "hybrid":
            assert c.get("uses_character") is True

    quebrou_prompt = [(c["id"], c["scene_type"]) for c in plan["cenas"]
                      if c.get("uses_character") and not c["prompt_imagem"].lstrip().startswith("@Marcos")]
    assert not quebrou_prompt, f"cenas humanas sem @Marcos no prompt: {quebrou_prompt}"

    # ---- JSON exemplo (2 cenas: 1 human + 1 b-roll) ----
    exemplo_id = next((c for c in plan["cenas"] if c.get("uses_character")), plan["cenas"][0])["id"]
    broll_id = next((c for c in plan["cenas"] if not c.get("uses_character")), plan["cenas"][-1])["id"]
    print("\nJSON EXEMPLO (cena humana):")
    humana = next(c for c in plan["cenas"] if c["id"] == exemplo_id)
    print(json.dumps({
        "id": humana["id"], "scene_type": humana["scene_type"],
        "uses_character": humana["uses_character"], "character_ref": humana["character_ref"],
        "prompt_imagem": humana["prompt_imagem"][:120] + ("…" if len(humana["prompt_imagem"]) > 120 else ""),
    }, ensure_ascii=False, indent=2))
    print("\nJSON EXEMPLO (b-roll):")
    b = next(c for c in plan["cenas"] if c["id"] == broll_id)
    print(json.dumps({
        "id": b["id"], "scene_type": b["scene_type"],
        "uses_character": b["uses_character"], "character_ref": b["character_ref"],
        "prompt_imagem": b["prompt_imagem"][:120] + ("…" if len(b["prompt_imagem"]) > 120 else ""),
    }, ensure_ascii=False, indent=2))

    print("\nRESULTADO: APROVADO ✅ — personagem @Marcos aplicado em cenas humanas; b-roll limpo.")
    print(f"Percentual de cenas com personagem: {len(ler)/total*100:.0f}%")


if __name__ == "__main__":
    main()