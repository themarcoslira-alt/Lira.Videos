# -*- coding: utf-8 -*-
"""
scripts/fix_lira_scene_plan.py — Correção de integridade de JSONs + higienização de cenas/

USO:
    python scripts/fix_lira_scene_plan.py [--projeto Boy] [--dry-run]

O que faz (idempotente e seguro):

  PASSO 1 — lira_scene_plan.json
    * Recupera o prefixo JSON válido via json.JSONDecoder().raw_decode() quando o
      arquivo tem caracteres residuais no final (ex.: cauda duplicada por escrita
      interrompida). A recuperação por raw_decode é mais robusta que truncar em
      ']}' porque campos top-level podem existir DEPOIS do array "cenas".
    * Para CADA cena, sincroniza "arquivo_midia" com o caminho canônico de
      midias_encontradas.json (fonte de verdade do padrão {id:02d}_[MM-SS-MM-SS].ext)
      e ajusta "filename" para o basename correspondente.
    * Salva com escrita atômica (temp + os.replace).

  PASSO 2 — higienização da RAIZ de cenas/
    * Mantém somente os 92 arquivos referenciados por midias_encontradas.json
      (ex.: 01_[00-00-00-05].png ... 92_[07-31-07-36].png).
    * Deleta variantes legadas/residuais da raiz: timecode curto
      (01_[00-00-05].png) e arquivos com extensão/duplicata fora do padrão.
    * NÃO toca nas subpastas cena_XXX/ (auditoria) nem em outras pastas.

Nenhuma mídia é apagada fora da raiz de cenas/. Re-executar o script é seguro.
"""
import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]  # raiz do repositório
PROJETOS_DIR = BASE / "projetos"


def _escrita_atomica(path: Path, data: dict) -> None:
    """Grava JSON com escrita atômica (temp + flush/fsync + os.replace)."""
    tmp = path.with_suffix(".json.tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def recuperar_json_valido(raw: str, caminho: Path):
    """Retorna o dict recuperado do raw, descartando resíduos do final.

    Tenta json.loads direto; se falhar (Extra data / lixo no fim), usa
    raw_decode para achar onde o JSON válido termina e tenta de novo.
    Levanta erro se a recuperação não for possível.
    """
    try:
        return json.loads(raw)
    except Exception as e1:
        print(f"  [info] parse direto falhou: {e1}")
        try:
            obj, fim = json.JSONDecoder().raw_decode(raw)
        except Exception as e2:
            raise RuntimeError(f"{caminho}: impossível recuperar JSON válido: {e2}") from e2
        print(f"  [info] recuperado: {len(raw) - fim} char(s) residuais removidos do final")
        print(f"  [info] lixo removido: {raw[fim:fim + 60]!r}")
        return obj


def carregar_midias_canonicas(projeto_dir: Path) -> dict:
    """Mapeia scene_id -> caminho canônico a partir de midias_encontradas.json."""
    midias_path = projeto_dir / "midias_encontradas.json"
    canon = {}
    if not midias_path.exists():
        print(f"  [aviso] {midias_path.name} não existe — passos limitados")
        return canon
    with open(midias_path, encoding="utf-8") as f:
        dados = json.load(f)
    if isinstance(dados, dict):
        # Formato {id: {path: ...}} também é aceito
        for k, v in dados.items():
            if isinstance(v, dict):
                p = v.get("path") or v.get("arquivo") or ""
                if p:
                    canon[int(k)] = str(p)
            else:
                canon[int(k)] = str(v)
    elif isinstance(dados, list):
        for m in dados:
            sid = m.get("scene_id")
            p = m.get("arquivo") or m.get("path") or ""
            if sid is not None and p:
                canon[int(sid)] = str(p)
    return canon


def passo1_fix_plan(projeto: str, dry_run: bool = False) -> tuple:
    """Corrige lira_scene_plan.json (recuperação + sync arquivo_midia). Retorna (plan, caminhos_canonicos)."""
    projeto_dir = PROJETOS_DIR / projeto
    plan_path = projeto_dir / "lira_scene_plan.json"
    if not plan_path.exists():
        print(f"[PASSO 1] {plan_path} não existe — pulando.")
        return None, {}

    raw = plan_path.read_text(encoding="utf-8")
    plan = recuperar_json_valido(raw, plan_path)
    cenas = plan.get("cenas", [])
    print(f"[PASSO 1] {len(cenas)} cenas no plano")

    canon = carregar_midias_canonicas(projeto_dir)
    print(f"[PASSO 1] midias_encontradas.json: {len(canon)} caminhos canônicos")

    mudancas = 0
    sem_canon = 0
    for c in cenas:
        cid = int(c.get("id") or 0)
        caminho_canonico = canon.get(cid, "")
        atual = str(c.get("arquivo_midia") or "").strip()
        if caminho_canonico and Path(caminho_canonico).name != Path(atual).name:
            print(f"  [OK] Cena {cid}: arquivo_midia '{Path(atual).name or '(vazio)'}' -> '{Path(caminho_canonico).name}'")
            c["arquivo_midia"] = caminho_canonico
            c["filename"] = Path(caminho_canonico).name
            mudancas += 1
        elif not caminho_canonico:
            sem_canon += 1
            print(f"  [aviso] cena {cid}: sem caminho canônico em midias_encontradas.json")

    # Validação pós-correção: todo arquivo_midia deve existir em disco
    ausentes = [int(c.get("id") or 0) for c in cenas
                if not (c.get("arquivo_midia") and Path(c["arquivo_midia"]).exists())]
    print(f"[PASSO 1] arquivo_midia atualizados: {mudancas} | sem canônico: {sem_canon} | ausentes em disco: {len(ausentes)}")
    if ausentes:
        print(f"  [aviso] cenas sem arquivo em disco: {ausentes}")

    if not dry_run:
        _escrita_atomica(plan_path, plan)
        verif = json.loads(plan_path.read_text(encoding="utf-8"))
        print(f"[PASSO 1] salvo com integridade: {plan_path} (parse OK, {len(verif.get('cenas', []))} cenas)")
    else:
        print("[PASSO 1] --dry-run: arquivo NÃO foi salvo")
    return plan, canon


def passo2_higienizar_cenas(projeto: str, canon: dict, dry_run: bool = False) -> None:
    """Mantém na RAIZ de cenas/ apenas os 92 arquivos canônicos; deleta o resto."""
    cenas_dir = PROJETOS_DIR / projeto / "cenas"
    if not cenas_dir.exists():
        print(f"[PASSO 2] {cenas_dir} não existe — pulando.")
        return

    nomes_canonicos = {Path(p).name for p in canon.values() if p}
    if not nomes_canonicos:
        print("[PASSO 2] sem nomes canônicos (midias_encontradas.json vazio/ausente) — nada a fazer.")
        return

    arquivos_raiz = [f for f in cenas_dir.iterdir() if f.is_file()]
    residuais = [f for f in arquivos_raiz if f.name not in nomes_canonicos]
    esperado = len(arquivos_raiz) - len(nomes_canonicos)
    print(f"[PASSO 2] arquivos na raiz: {len(arquivos_raiz)} | canônicos: {len(nomes_canonicos)} | residuais: {len(residuais)}")

    for f in sorted(residuais):
        print(f"  [DEL] residual: {f.name}")

    if dry_run:
        print(f"[PASSO 2] --dry-run: {len(residuais)} residuais NÃO foram apagados")
        return

    if len(residuais) != esperado:
        print(f"[ERRO] Abortando: contagem residual ({len(residuais)}) != esperado ({esperado}).")
        sys.exit(1)

    for f in residuais:
        try:
            f.unlink()
            print(f"  [OK] deletado: {f.name}")
        except OSError as e:
            print(f"  [aviso] não foi possível deletar {f.name}: {e}")

    restantes = [f for f in cenas_dir.iterdir() if f.is_file()]
    print(f"[PASSO 2] arquivos em cenas/ após limpeza: {len(restantes)}")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Corrige integridade de lira_scene_plan.json e higieniza cenas/")
    parser.add_argument("--projeto", default="Boy", help="Nome do projeto (padrão: Boy)")
    parser.add_argument("--dry-run", action="store_true", help="Apenas diagnostica, não modifica nada")
    args = parser.parse_args()

    print(f"== Fix integridade — projeto '{args.projeto}' {'(DRY-RUN)' if args.dry_run else ''} ==")
    plan, canon = passo1_fix_plan(args.projeto, dry_run=args.dry_run)
    passo2_higienizar_cenas(args.projeto, canon, dry_run=args.dry_run)
    print("== Concluído ==")


if __name__ == "__main__":
    main()
