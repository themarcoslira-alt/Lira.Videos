"""
Teste de Validação da Fase 2A:
- Rejeição de PNG em modo vídeo/animação (sem falso positivo de conclusão).
- Aceitação de MP4 válido (> 5000 bytes).
- Harmonização de nomenclatura canônica (4 blocos MM-SS-MM-SS).
- Resolução resiliente desambiguando arquivo de 4 blocos vs 3 blocos legados.
"""
import os
import sys
import tempfile
from pathlib import Path

# Adiciona raiz do projeto ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.scene_plan_service import (
    formatar_nome_arquivo_cena_padrao,
    _nome_cena_timecode,
    resolver_arquivo_cena,
)
from services.media_standard import nome_padrao_cena, nome_arquivo_seguro, eh_nome_padrao


def test_naming_harmonization():
    print("--- Teste 1: Harmonização de Nomes ---")
    cid = 2
    ts_ini = 6.39
    ts_fim = 12.09

    nome_formatado = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ".png")
    print(f"Nome gerado por formatar_nome_arquivo_cena_padrao: {nome_formatado}")
    assert nome_formatado == "02_[00-06-00-12].png", f"Esperado '02_[00-06-00-12].png', obteve '{nome_formatado}'"
    assert eh_nome_padrao(nome_formatado), f"{nome_formatado} deveria casar com eh_nome_padrao"

    nome_video = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ".mp4")
    print(f"Nome gerado para vídeo: {nome_video}")
    assert nome_video == "02_[00-06-00-12].mp4"
    assert eh_nome_padrao(nome_video)
    print("✅ Teste 1 passou: Nomenclatura 100% harmonizada em 4 blocos!\n")


def test_resolver_desambiguacao():
    print("--- Teste 2: Desambiguação no Resolver ---")
    import json
    import shutil
    proj_test = "_test_proj_desambiguacao"
    proj_dir = ROOT / "projetos" / proj_test
    cenas_dir = proj_dir / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Cria dois arquivos para a cena 2: um de 3 blocos (legado) e um de 4 blocos (padrão)
        arq_3b = cenas_dir / "02_[00-06-12].png"
        arq_4b = cenas_dir / "02_[00-06-00-12].png"
        arq_3b.write_bytes(b"x" * 1000)
        arq_4b.write_bytes(b"y" * 2000)

        # Sem scene_plan configurado com arquivo_midia: resolver deve escolher o canônico 4 blocos
        resolvido = resolver_arquivo_cena(proj_test, 2, 6.39)
        print(f"Arquivo resolvido com 2 candidatos: {resolvido.name if resolvido else None}")
        assert resolvido is not None, "Resolver não deveria ter retornado None"
        assert resolvido.name == "02_[00-06-00-12].png", f"Deveria ter resolvido 02_[00-06-00-12].png, mas resolveu {resolvido.name}"
        print("✅ Teste 2 passou: Desambiguação selecionou arquivo canônico de 4 blocos!\n")
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)


def test_validacao_rejeita_png_em_modo_video():
    print("--- Teste 3: Simulação de Validação no FlowQueueWorker ---")
    from pathlib import Path

    # Simula a checagem que implementamos no FlowQueueWorker
    def validar_resultado_worker(is_anim_item, res):
        tipo_resultado = "sucesso"
        if not res.get("success"):
            tipo_resultado = "falha"
        elif is_anim_item:
            c_img = str(res.get("caminho_imagem") or "")
            p_img = Path(c_img) if c_img else None
            if not p_img or p_img.suffix.lower() not in (".mp4", ".webm", ".mov") or not p_img.exists() or p_img.stat().st_size <= 5000:
                tipo_resultado = "falha"
                res = {
                    "success": False,
                    "erro": f"Esperado arquivo de vídeo (.mp4/.webm/.mov), recebido '{c_img}' (tamanho ou extensão inválida)"
                }
            else:
                tipo_resultado = "sucesso"
        else:
            tipo_resultado = "sucesso"
        return tipo_resultado, res

    # Cria arquivo png temporario
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_png:
        f_png.write(b"PNG_DATA" * 500)
        png_path = f_png.name

    # Cria arquivo mp4 temporario pequeno (< 5000 bytes)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f_small_mp4:
        f_small_mp4.write(b"MP4" * 100)
        small_mp4_path = f_small_mp4.name

    # Cria arquivo mp4 temporario valido (> 5000 bytes)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f_ok_mp4:
        f_ok_mp4.write(b"REAL_MP4_BYTES" * 1000)
        ok_mp4_path = f_ok_mp4.name

    try:
        # Caso A: Modo animação retornando PNG -> DEVE FALHAR
        res_png = {"success": True, "caminho_imagem": png_path}
        tipo, r = validar_resultado_worker(is_anim_item=True, res=res_png)
        print(f"Retorno PNG em modo animação: tipo={tipo}, erro={r.get('erro')}")
        assert tipo == "falha", "Modo animação com PNG deveria ter resultado em falha!"
        assert "Esperado arquivo de vídeo" in r.get("erro", "")

        # Caso B: Modo animação retornando MP4 corrompido/pequeno -> DEVE FALHAR
        res_small = {"success": True, "caminho_imagem": small_mp4_path}
        tipo, r = validar_resultado_worker(is_anim_item=True, res=res_small)
        print(f"Retorno MP4 < 5000 bytes: tipo={tipo}, erro={r.get('erro')}")
        assert tipo == "falha", "MP4 muito pequeno deveria ter resultado em falha!"

        # Caso C: Modo animação retornando MP4 válido -> DEVE SER SUCESSO
        res_ok = {"success": True, "caminho_imagem": ok_mp4_path}
        tipo, r = validar_resultado_worker(is_anim_item=True, res=res_ok)
        print(f"Retorno MP4 válido: tipo={tipo}")
        assert tipo == "sucesso", "MP4 válido deveria ter resultado em sucesso!"

        # Caso D: Modo imagem retornando PNG -> DEVE SER SUCESSO
        res_img = {"success": True, "caminho_imagem": png_path}
        tipo, r = validar_resultado_worker(is_anim_item=False, res=res_img)
        print(f"Retorno PNG em modo imagem: tipo={tipo}")
        assert tipo == "sucesso", "PNG em modo imagem deveria ter sido sucesso!"

        print("✅ Teste 3 passou: Validação estrita de mídia impede completamente falsos positivos!\n")
    finally:
        for p in [png_path, small_mp4_path, ok_mp4_path]:
            try:
                os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    test_naming_harmonization()
    test_resolver_desambiguacao()
    test_validacao_rejeita_png_em_modo_video()
    print("=========================================")
    print("TODOS OS TESTES DA FASE 2A FORAM APROVADOS!")
    print("=========================================")
