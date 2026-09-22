import os
import sys
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MUTATION_DIR = BASE_DIR / "tmp" / "mutation_workspace"

def setup_copy():
    if MUTATION_DIR.exists():
        shutil.rmtree(MUTATION_DIR, ignore_errors=True)
    MUTATION_DIR.mkdir(parents=True, exist_ok=True)

    # Copy needed files
    shutil.copytree(BASE_DIR / "services", MUTATION_DIR / "services", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(BASE_DIR / "config", MUTATION_DIR / "config", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(BASE_DIR / "tests", MUTATION_DIR / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in BASE_DIR.glob("*.py"):
        shutil.copy2(f, MUTATION_DIR / f.name)

def run_pytest_in_copy(test_name):
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        f"tests/test_flow_queue_concorrencia.py::{test_name}",
        "-v"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MUTATION_DIR)
    res = subprocess.run(cmd, cwd=str(MUTATION_DIR), capture_output=True, text=True, env=env)
    return res.returncode, res.stdout, res.stderr

def test_mutations():
    print("================================================================")
    print("EXPERIMENTO B3: Teste de Mutação nos Testes do Cline")
    print("================================================================")

    # -------------------------------------------------------------------------
    # (a) Trocar transacao_fila por ler/salvar separados -> test_concorrencia_50_posts
    # -------------------------------------------------------------------------
    print("\n--- MUTAÇÃO (a): ler/salvar separados em /fila/adicionar ---")
    setup_copy()
    api_file = MUTATION_DIR / "services" / "api_v2.py"
    with open(api_file, "r", encoding="utf-8") as fp:
        content = fp.read()

    target_block = """        # Única transação atômica para leitura -> alteração -> salvamento
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []"""

    mutated_block = """        # MUTAÇÃO: leitura e salvamento separados sem transação única
        from services.playwright_flow import _ler_fila, _salvar_fila
        fila = _ler_fila(projeto_id, PROJETOS_DIR)
        if not fila:
            from services.playwright_flow import _init_fila_vazia
            fila = _init_fila_vazia(projeto_id, PROJETOS_DIR)
        itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []"""

    # Também remover a indentação do bloco da transação e salvar no final
    content_mut_a = content.replace("with transacao_fila(projeto_id, PROJETOS_DIR) as fila:\n", "if True:\n            fila = pw_flow._ler_fila(projeto_id, PROJETOS_DIR)\n")
    # Substituir no arquivo com uma mutação cirúrgica:
    # Ler antes, fazer o loop, salvar depois com _salvar_fila
    mut_a_code = """        from services.playwright_flow import _ler_fila, _salvar_fila, _init_fila_vazia
        _init_fila_vazia(projeto_id, PROJETOS_DIR)
        fila = _ler_fila(projeto_id, PROJETOS_DIR)
        itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []
        itens_map = {int(it.get("scene_id") or it.get("id") or 0): it for it in itens}

        for sid in scene_ids:
            cid = int(sid)
            cena = cenas_map[cid]
            prompt = (cena.get("visual_prompt") or cena.get("prompt_imagem") or cena.get("texto") or "").strip()

            if cid in itens_map:
                it = itens_map[cid]
                st = it.get("status")
                if st in ("enfileirada", "gerando", "pendente"):
                    ignoradas += 1
                    continue
                it["status"] = "enfileirada"
                it["prompt"] = prompt
                it["modo"] = modo
                it["tentativa_atual"] = 0
                it["attempt_count"] = 0
                it["max_tentativas"] = 3
                it["ultimo_erro"] = None
                it["error_msg"] = ""
                it["enfileirada_em"] = agora
                novos_enfileirados += 1
            else:
                item = {
                    "scene_id": cid,
                    "id": cid,
                    "posicao": len(itens) + 1,
                    "modo": modo,
                    "status": "enfileirada",
                    "tentativa_atual": 0,
                    "attempt_count": 0,
                    "max_tentativas": 3,
                    "enfileirada_em": agora,
                    "prompt": prompt
                }
                itens.append(item)
                itens_map[cid] = item
                novos_enfileirados += 1

        fila["itens"] = itens
        fila["total"] = len(itens)
        fila["ativa"] = True
        fila["atualizado_em"] = agora
        total_final = len(itens)
        _salvar_fila(projeto_id, fila, PROJETOS_DIR)"""

    # Localiza o bloco da transação em api_v2.py
    start_str = "        # Única transação atômica para leitura -> alteração -> salvamento\n        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:"
    end_str = "        # Acorda worker thread fora da transação"
    idx1 = content.find(start_str)
    idx2 = content.find(end_str)
    if idx1 != -1 and idx2 != -1:
        content_mut_a = content[:idx1] + mut_a_code + "\n\n" + content[idx2:]
        with open(api_file, "w", encoding="utf-8") as fp:
            fp.write(content_mut_a)
        code, out, err = run_pytest_in_copy("test_concorrencia_50_posts")
        print(f"Retorno pytest (esperado!=0): {code}")
        print("Saída:")
        print(out)
    else:
        print("Erro ao localizar bloco para mutação A")

    # -------------------------------------------------------------------------
    # (b) Voltar a condição para <= len(backoffs) -> test_falha_permanente_3_tentativas
    # -------------------------------------------------------------------------
    print("\n--- MUTAÇÃO (b): tentativas <= len(FLOW_QUEUE_BACKOFFS) ---")
    setup_copy()
    pw_file = MUTATION_DIR / "services" / "playwright_flow.py"
    with open(pw_file, "r", encoding="utf-8") as fp:
        content_b = fp.read()

    # Na fase C: if tentativas < max_tent: -> if tentativas <= len(FLOW_QUEUE_BACKOFFS):
    content_b_mut = content_b.replace("if tentativas < max_tent:", "if tentativas <= len(FLOW_QUEUE_BACKOFFS):")
    with open(pw_file, "w", encoding="utf-8") as fp:
        fp.write(content_b_mut)

    code_b, out_b, err_b = run_pytest_in_copy("test_falha_permanente_3_tentativas")
    print(f"Retorno pytest (esperado!=0): {code_b}")
    print("Saída:")
    print(out_b)

    # -------------------------------------------------------------------------
    # (c) Remover o filtro status == 'gerando' na fase C -> test_readicionar_cena_em_erro
    # -------------------------------------------------------------------------
    print("\n--- MUTAÇÃO (c): remover filtro status == 'gerando' na Fase C ---")
    setup_copy()
    pw_file = MUTATION_DIR / "services" / "playwright_flow.py"
    with open(pw_file, "r", encoding="utf-8") as fp:
        content_c = fp.read()

    content_c_mut = content_c.replace(
        'if int(it.get("scene_id") or it.get("id") or 0) == scene_id and it.get("status") == "gerando":',
        'if int(it.get("scene_id") or it.get("id") or 0) == scene_id:'
    )
    with open(pw_file, "w", encoding="utf-8") as fp:
        fp.write(content_c_mut)

    code_c, out_c, err_c = run_pytest_in_copy("test_readicionar_cena_em_erro")
    print(f"Retorno pytest: {code_c}")
    print("Saída:")
    print(out_c)

    # -------------------------------------------------------------------------
    # (d) Remover o lock de geração -> test_dois_projetos_concorrentes_serializados
    # -------------------------------------------------------------------------
    print("\n--- MUTAÇÃO (d): remover _flow_generation_lock ---")
    setup_copy()
    pw_file = MUTATION_DIR / "services" / "playwright_flow.py"
    with open(pw_file, "r", encoding="utf-8") as fp:
        content_d = fp.read()

    # Substitui _flow_generation_lock por contextlib.nullcontext()
    content_d_mut = content_d.replace(
        "_flow_generation_lock = threading.RLock()",
        "from contextlib import nullcontext\n_flow_generation_lock = nullcontext()"
    )
    with open(pw_file, "w", encoding="utf-8") as fp:
        fp.write(content_d_mut)

    code_d, out_d, err_d = run_pytest_in_copy("test_dois_projetos_concorrentes_serializados")
    print(f"Retorno pytest (esperado!=0): {code_d}")
    print("Saída:")
    print(out_d)

    # Limpeza da pasta de mutação
    shutil.rmtree(MUTATION_DIR, ignore_errors=True)

if __name__ == "__main__":
    test_mutations()
