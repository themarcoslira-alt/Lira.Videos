"""Importa o SRT MELHORADO (texto corrigido + tags) no TESTE 007 e regenera
cenas/prompts guiados por tag.

Fluxo (com backup antes de qualquer escrita):
  1. Lê TESTE_007_MELHORADO.srt (Desktop/CANAL) preservando [TAG] por bloco;
  2. Reescreve roteiro_transcricao.json/.txt + srt/roteiro_transcricao.srt;
  3. Reconstrói cenas.json (1 cena por bloco do SRT) com campo srt_tag;
  4. Regenera lira_scene_plan.json (gerar_scene_plan force);
  5. Aplica Estratégia de Retenção (6-9 avatares) + dicas das TAGS
     (BROLL nunca avatar; AVATAR_ACTION = close-up de mãos sem rosto);
  6. Corrige @presenter -> @Lira e regenera prompts por papel/tag;
  7. Salva e emite relatório RetentionAnalyzer em logs/.

Uso:  python scripts/importar_srt_melhorado.py
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

PROJETO = "TESTE 007"
PASTA_PROJ = BASE / "projetos" / PROJETO
SRT_FONTE = Path(r"C:\Users\Administrator\Desktop\CANAL\TESTE_007_MELHORADO.srt")
BACKUPS = BASE / "_backups_antigos"

from services.srt_tag_service import (  # noqa: E402
    ler_srt_caminho, categoria_tag, dica_cena_por_tag, _para_segundos,
)


def _fmt_mmss(seg: float) -> str:
    m, s = divmod(int(seg), 60)
    return f"{m:02d}:{s:02d}"


def _backup(relativo: str, marcador: str) -> Path:
    origem = PASTA_PROJ / relativo
    if not origem.exists():
        return None
    destino = BACKUPS / f"{PROJETO.replace(' ', '_')}_{marcador}_{Path(relativo).name}"
    BACKUPS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(origem), str(destino))
    return destino


def main() -> int:
    if not SRT_FONTE.exists():
        print(f"[ERRO] SRT melhorado não encontrado: {SRT_FONTE}")
        return 1

    cues = ler_srt_caminho(SRT_FONTE)
    n_tags = sum(1 for c in cues if c.get("srt_tag"))
    print(f"[SRT] {len(cues)} blocos | {n_tags} com TAG")
    if len(cues) < 80:
        print("[ERRO] SRT parece incompleto (<80 blocos) — abortado.")
        return 1

    marcador = datetime.now().strftime("%Y%m%d_%H%M%S")
    for rel in ["roteiro_transcricao.json", "roteiro_transcricao.txt",
                "cenas.json", "lira_scene_plan.json"]:
        b = _backup(rel, f"pre_srt_melhorado_{marcador}")
        if b:
            print(f"[BACKUP] {rel} -> {b.name}")

    # 1) roteiro (json é a fonte temporal; tags preservadas)
    duracao = cues[-1]["end"]
    json_seg = []
    linhas_txt = []
    for c in cues:
        json_seg.append({
            "start": c["start"],
            "end": c["end"],
            "text": c["text"],
            "timestamp": _fmt_mmss(c["start"]),
            "srt_tag": c.get("srt_tag"),
        })
        linhas_txt.append(f"[{_fmt_mmss(c['start'])}] {c['text']}")

    (PASTA_PROJ / "roteiro_transcricao.json").write_text(
        json.dumps({
            "project": PROJETO, "segments": json_seg,
            "duration": round(duracao, 3), "language": "en",
            "fonte": "srt_melhorado_tagged",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    (PASTA_PROJ / "roteiro_transcricao.txt").write_text(
        "\n".join(linhas_txt), encoding="utf-8")
    (PASTA_PROJ / "srt").mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(SRT_FONTE), str(PASTA_PROJ / "srt" / "roteiro_transcricao.srt"))
    print("[ROTEIRO] roteiro_transcricao.json/.txt/srt reescritos")

    # 2) cenas.json — 1 cena por bloco do SRT (com srt_tag)
    cenas_json = []
    for i, c in enumerate(cues, start=1):
        cenas_json.append({
            "id": i,
            "texto": c["text"],
            "timestamps": [_fmt_mmss(c["start"])],
            "topic": "",
            "start_time": c["start"],
            "end_time": c["end"],
            "duration": round(c["end"] - c["start"], 3),
            "srt_tag": c.get("srt_tag"),
        })
    (PASTA_PROJ / "cenas.json").write_text(
        json.dumps(cenas_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CENAS] cenas.json reescrito com {len(cenas_json)} cenas")

    # 3) Regenera o scene_plan
    import services.scene_plan_service as sps
    res = sps.gerar_scene_plan(PROJETO, force=True)
    print("[SCENE_PLAN]", "OK" if res.get("success") else res)
    if not res.get("success"):
        print("[ERRO] falha ao regenerar scene_plan:", res.get("error"))
        return 1
    # 4) Reaplica TAG + Estratégia de Retenção no plano recém-gerado
    from services.retention_analyzer_service import (
        aplicar_estrategia_retencao, RetentionAnalyzer,
    )
    from services.prompt_builder_service import get_prompt_para_papel_avatar

    plan = sps.carregar_scene_plan(PROJETO)
    cenas = plan["cenas"]

    # mapeia cena -> cue por overlap do start (tol 0.2s) ou por índice sequencial
    cue_por_start = {round(c["start"], 2): c for c in cues}

    def _cue_para(cena):
        s = round(float(cena.get("tempo_inicio") or 0), 2)
        c = cue_por_start.get(s)
        if not c:
            candidatos = [x for x in cues if abs(x["start"] - s) < 0.35]
            c = candidatos[0] if candidatos else None
        return c

    for cena in cenas:
        cue = _cue_para(cena)
        tag = cue.get("srt_tag") if cue else None
        dica = dica_cena_por_tag(tag)
        cena["srt_tag"] = tag
        cena["categoria_tag"] = dica["categoria_tag"]
        cena["lip_sync_needed"] = dica["lip_sync_needed"]

    video_dur = max(float(c.get("tempo_fim", 0)) for c in cenas)
    aplicar_estrategia_retencao(cenas, video_duration=video_dur, character_ref="@Lira")

    # 5) Enforce das TAGS sobre o papel de retenção
    for cena in cenas:
        tag = cena.get("srt_tag")
        cat = cena.get("categoria_tag")
        if cat == "broll":
            cena["avatar_role"] = None
            cena["should_have_avatar"] = False
            cena["uses_character"] = False
            cena["avatar_required"] = False
            cena["character_ref"] = ""
            cena["scene_type"] = "broll_macro"
            cena["is_pattern_interrupt"] = False
            cena["lip_sync_needed"] = False
        elif cat == "avatar_acao" and cena.get("avatar_role") in (None, "AÇÃO"):
            cena["avatar_role"] = "AÇÃO"
            cena["should_have_avatar"] = True
            cena["uses_character"] = True
            cena["scene_type"] = "avatar_action"
            cena["lip_sync_needed"] = False   # mãos, sem rosto
        elif cat == "cta" and cena.get("avatar_role") != "CTA":
            cena["avatar_role"] = "CTA"
            cena["should_have_avatar"] = True
            cena["uses_character"] = True
            cena["scene_type"] = "avatar_talking"
            cena["lip_sync_needed"] = True
        # garante o personagem certo (nunca @presenter)
        if cena.get("character_ref") in ("@presenter", "@Presente", "presenter"):
            cena["character_ref"] = "@Lira" if cena.get("uses_character") else ""

    # 6) Regenera prompts por papel/tag (determinístico, sem @presenter em BROLL)
    for cena in cenas:
        role = cena.get("avatar_role")
        if not role and (cena.get("uses_character") is True or cena.get("should_have_avatar")):
            role = "AVATAR"
        prompt_key = role if role else "BROLL"
        pr = get_prompt_para_papel_avatar(prompt_key, cena, "@Lira" if cena.get("uses_character") else "")
        cena["prompt_imagem"] = pr["prompt_imagem"]
        cena["visual_prompt"] = pr["prompt_imagem"]
        cena["prompt_animacao"] = pr["prompt_animacao"]

    sps.salvar_scene_plan(PROJETO, plan)

    # 7) Relatório final
    final = sps.carregar_scene_plan(PROJETO)
    rel = RetentionAnalyzer.analyze_scene_plan(final)
    (BASE / "logs").mkdir(parents=True, exist_ok=True)
    (BASE / "logs" / "retencao_TESTE007.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=1), encoding="utf-8")

    from collections import Counter
    print("[OK] TESTE 007 regenerado com SRT MELHORADO + tags")
    print("  cenas:", rel.get("total_cenas"),
          "| avatares:", rel.get("avatar_scenes"),
          f"({rel.get('avatar_scenes_percent')}%)",
          "| score retenção:", rel.get("score_retencao"))
    print("  roles:", dict(Counter(str(c.get('avatar_role')) for c in final['cenas'])))
    print("  tags:", dict(Counter(str(c.get('srt_tag')) for c in final['cenas'])))
    print("  @presenter restante:", sum(1 for c in final['cenas'] if c.get('character_ref') == '@presenter'))
    print("  problemas:", rel.get("problemas", []))
    print("  relatório: logs/retencao_TESTE007.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
