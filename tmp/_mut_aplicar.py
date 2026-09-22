"""Aplica a mutação pedida na CÓPIA do repositório em tmp/_mut_repo (nunca no original)."""
import shutil
import sys
from pathlib import Path

ORIG = Path(r"c:\Lira Videos\services\playwright_flow.py")
DEST = Path(r"c:\Lira Videos\tmp\_mut_repo\services\playwright_flow.py")

MUT_I_DE = '''                    for it in itens:
                        if exec_id and str(it.get("exec_id") or "") == exec_id:
                            item_atual = it
                            break'''
MUT_I_PARA = '''                    for it in itens:
                        if int(it.get("scene_id") or it.get("id") or 0) == scene_id:
                            item_atual = it
                            break'''

MUT_II_DE = '''    try:
        return _get_cdp_actor().submit(
            _criar_imagem_cena_impl, projeto_id, scene_id, prompt,
            timeout=_timeout_do_ator(),
        )'''
MUT_II_PARA = '''    try:
        return _criar_imagem_cena_impl(projeto_id, scene_id, prompt)'''


def main() -> int:
    mutacao = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if mutacao not in ("i", "ii", "reverter"):
        print("uso: _mut_aplicar.py i|ii|reverter")
        return 2

    # Sempre parte do ORIGINAL (garante cópia limpa a cada mutação).
    shutil.copyfile(ORIG, DEST)
    if mutacao == "reverter":
        print("copia restaurada do original")
        return 0

    texto = DEST.read_text(encoding="utf-8")
    de, para = (MUT_I_DE, MUT_I_PARA) if mutacao == "i" else (MUT_II_DE, MUT_II_PARA)
    if texto.count(de) != 1:
        print(f"ERRO: trecho da mutacao {mutacao} encontrado {texto.count(de)}x (esperado 1)")
        return 3
    DEST.write_text(texto.replace(de, para), encoding="utf-8")
    print(f"mutacao {mutacao} aplicada em {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
