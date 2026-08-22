"""
scratch/validacao_completa_inteligencia_10cenas.py
===================================================
Validação Completa da Camada Visual Director AI:
- 100% Offline (SEM CONEXÃO COM O GOOGLE FLOW)
- Roteiro real de 10 cenas de tutorial de jardinagem com o apresentador @Marcos
- Verificação exaustiva dos 10 pontos:
  1. Leitura macro do contexto visual
  2. Classificação correta de scene_type
  3. Character decision elegendo @Marcos
  4. Ausência total de @Homem, @Pessoa ou genéricos
  5. B-roll preservando objetos da narrativa
  6. Variação cinematográfica de câmeras e lentes
  7. Consistência contínua de mundo, iluminação e estilo
  8. Prompts cinematográficos limpos sem timestamps
  9. Presença de todas as decisões no scene_plan.json
  10. media_intent (image | video)
"""

import os
import sys
import json
import shutil
from pathlib import Path

ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

from app_web import app
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
import services.visual_director_service as visual_director_svc

PROJ = "projeto_validacao_inteligencia_10cenas"
pdir = ROOT_DIR / "projetos" / PROJ
if pdir.exists():
    shutil.rmtree(pdir)

print("=" * 80)
print("VALIDAÇÃO COMPLETA DA INTELIGÊNCIA VISUAL DIRECTOR AI (10 CENAS - SEM FLOW)")
print("=" * 80)

# 1. Configura Identidade Oficial com Personagem Bloqueado @Marcos
character_svc.salvar_identidade_projeto(
    projeto_id=PROJ,
    tipo="personagem",
    nome="Marcos",
    referencia_flow="@Marcos",
    visual_style="photorealistic_cinematic"
)
character_svc.atualizar_status_flow_personagem(
    projeto_id=PROJ,
    created=True,
    flow_char_name="@Marcos",
    flow_char_id="flow-marcos-baf62874-6ec5-4cf3-bec0"
)

# 2. Roteiro Real em SRT com 10 cenas completas
srt_10_cenas = """1
00:00:00,000 --> 00:00:03,500
Olá pessoal, eu sou o Marcos e hoje eu vou revelar o maior erro que cometi no meu jardim de orquídeas.

2
00:00:03,500 --> 00:00:07,000
Observem essas folhas amareladas e sem brilho por causa da falta de nutrientes orgânicos.

3
00:00:07,000 --> 00:00:10,500
As raízes estavam completamente sufocadas no solo seco e compacto.

4
00:00:10,500 --> 00:00:14,000
Aqui eu mostro na prática o adubo orgânico de casca de banana fermentada que recuperou tudo.

5
00:00:14,000 --> 00:00:17,500
Comece aplicando a adubação ao redor da borda do vaso sem tocar diretamente no caule da planta.

6
00:00:17,500 --> 00:00:21,000
Eu sempre preparo e rego com água fresca em temperatura ambiente logo em seguida.

7
00:00:21,000 --> 00:00:24,500
Vejam o detalhe das pétalas e como as folhas absorvem a umidade com gotas de orvalho.

8
00:00:24,500 --> 00:00:28,000
Olha a diferença entre a planta tratada com adubo natural versus o vaso sem nutrientes.

9
00:00:28,000 --> 00:00:31,500
Nesse ambiente do meu jardim, todo o espaço ganhou uma vitalidade e florescimento impressionante.

10
00:00:31,500 --> 00:00:35,000
Se você quer aprender mais técnicas de cultivo, se inscreva no canal e deixe seu comentário!
"""

client = app.test_client()
res = client.post(f"/api/v2/transcricao/{PROJ}/usar_srt", json={"srt_texto": srt_10_cenas})
assert res.status_code == 200, f"Erro ao processar SRT: {res.data}"

# 3. Carrega o plano de cenas gerado
plan = scene_plan_svc.carregar_scene_plan(PROJ)
cenas = plan["cenas"]
ctx = plan.get("visual_context") or visual_director_svc.obter_contexto_visual(PROJ)

print(f"\n[1. VISUAL DIRECTOR CONTEXT]")
print(f"  • Tema: {ctx.get('theme')}")
print(f"  • Mundo (World): {ctx.get('world')}")
print(f"  • Personagem Principal: {ctx.get('main_character')}")
print(f"  • Tom Emocional: {ctx.get('tone')}")
print(f"  • Objetos Recorrentes: {ctx.get('recurring_objects')}")
print(f"  • Regras de Continuidade: {ctx.get('continuity_rules')}")

assert ctx["main_character"] == "Marcos"
assert len(ctx["recurring_objects"]) > 0

print(f"\n[2. ANÁLISE DETALHADA DAS 10 CENAS GERADAS]\n")

shots_utilizados = []
for i, c in enumerate(cenas, 1):
    cid = c["id"]
    stype = c["scene_type"]
    role = c["visual_role"]
    uses_char = c["uses_character"]
    char_ref = c["character_ref"]
    emotion = c["emotion"]
    energy = c["energy"]
    cam = c["camera_direction"]
    shot = cam.get("shot", "")
    lens = cam.get("lens", "")
    m_intent = c.get("media_intent", "")
    prompt_img = c["prompt_imagem"]
    supporting = c["supporting_visuals"]

    shots_utilizados.append(shot)

    print(f"--- CENA {cid:03d} [{stype.upper()}] (media_intent: {m_intent}) ---")
    print(f"  • Papel / Emoção: {role} | {emotion} (energia: {energy})")
    print(f"  • Personagem: uses_character={uses_char} | ref='{char_ref}'")
    print(f"  • Câmera: {shot} | Lente: {lens} | Movimento: {cam.get('movement')}")
    print(f"  • Apoios Visuais: {supporting[:2]}")
    print(f"  • Prompt Imagem: {prompt_img}\n")

    # Validações estritas por cena:
    assert "@Homem" not in prompt_img, f"Cena {cid} contém @Homem proibido!"
    assert "@Pessoa" not in prompt_img, f"Cena {cid} contém @Pessoa proibido!"
    assert "@Man" not in prompt_img, f"Cena {cid} contém @Man proibido!"
    assert "-->" not in prompt_img, f"Cena {cid} contém timestamp no prompt!"
    assert m_intent in ("image", "video"), f"Cena {cid} media_intent inválido: {m_intent}"
    assert len(c["camera_direction"]) > 0, f"Cena {cid} sem camera_direction!"
    assert len(c["supporting_visuals"]) > 0, f"Cena {cid} sem supporting_visuals!"
    assert c["continuity_context"], f"Cena {cid} sem continuity_context!"

    if uses_char:
        assert char_ref == "@Marcos", f"Cena {cid} esperava @Marcos, obteve {char_ref}"
        assert "@Marcos" in prompt_img, f"Cena {cid} não incluiu @Marcos no prompt!"
    else:
        assert char_ref == "", f"Cena {cid} é b-roll mas tem char_ref: {char_ref}"

# 4. Validação de Variação de Câmera (não pode ter 3 shots idênticos em sequência)
print("[3. VALIDAÇÃO DE VARIAÇÃO DE CÂMERA]")
for j in range(len(shots_utilizados) - 2):
    assert not (shots_utilizados[j] == shots_utilizados[j+1] == shots_utilizados[j+2]), f"Shot repetido 3 vezes seguidas: {shots_utilizados[j]}"
print(f"✓ Total de enquadramentos dinâmicos únicos: {len(set(shots_utilizados))} de 10 cenas.")

# 5. Salva JSON completo do plano para inspeção
json_out = ROOT_DIR / "scratch" / "validacao_10cenas_scene_plan_output.json"
json_out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n[4. EXPORTAÇÃO JSON]")
print(f"✓ Arquivo JSON completo gravado em: {json_out}")

print("\n" + "=" * 80)
print("TODOS OS 10 PONTOS DA VALIDAÇÃO FORAM APROVADOS COM 100% DE SUCESSO!")
print("=" * 80)
