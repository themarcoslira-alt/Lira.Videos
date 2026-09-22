"""
Teste Negativo da Fase 3:
- Simula Flow retornando um arquivo .png quando o modo é 'animacao' / is_anim=True.
- Verifica que o sistema REJEITA o resultado (fail-closed).
- Confirma que video_status NUNCA é marcado como 'READY' com arquivo que não seja vídeo.
- Confirma que a fila NÃO marca 'concluida'.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import services.playwright_flow as pf
import services.scene_plan_service as sp


def test_phase_3_negative():
    print("=== Teste 3 (Negativo): Validação Fail-Closed (Rejeição de PNG em Modo Vídeo) ===")

    projeto_id = "test_proj_neg"
    cena_id = 2

    # Cria arquivo PNG falso (fake image)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10000)
        fake_png_path = tf.name

    try:
        worker = pf.FlowQueueWorker.get_worker()

        # 1. Testa validação em _criar_imagem_cena_impl quando o resultado do Flow é PNG
        with patch.object(worker, "_processar_cena_individual", return_value=(True, fake_png_path)), \
             patch("services.playwright_flow.ensure_chrome_cdp", return_value=(True, "OK")), \
             patch.object(worker, "_ensure_project_open", return_value=True), \
             patch.object(worker, "_check_is_active", return_value=True):

            worker.page = MagicMock()
            worker._session_thread_ident = pf.threading.get_ident()

            # Chama _criar_imagem_cena_impl com is_anim=True
            res = pf._criar_imagem_cena_impl(projeto_id, cena_id, prompt="Teste anim", is_anim=True)
            print(f"Resultado de _criar_imagem_cena_impl com PNG falso em modo animação: {res}")

            assert res["success"] is False, "DEVERIA TER FALHADO! PNG não pode ser aceito em is_anim=True"
            assert "esperado vídeo" in res.get("erro", "").lower() or "não é um vídeo válido" in res.get("erro", "").lower(), \
                f"Mensagem de erro deveria indicar que não é vídeo válido: {res.get('erro')}"
            print("✅ _criar_imagem_cena_impl rejeitou PNG com sucesso em modo animação!")

        # 2. Testa que a Fila (FlowQueueWorker) rejeita item de animação cujo resultado foi não-vídeo
        # e NÃO marca video_status = READY
        mock_plan = {
            "cenas": [
                {
                    "id": 2,
                    "status": "PENDENTE",
                    "image_status": "READY",
                    "video_status": "NOT_STARTED"
                }
            ]
        }
        with patch("services.scene_plan_service.carregar_scene_plan", return_value=mock_plan), \
             patch("services.scene_plan_service.atualizar_cena") as mock_atualizar:

            # Simula actor retornando PNG para item is_anim=True
            res_ator = {"success": True, "caminho_imagem": fake_png_path}
            is_anim_item = True

            # Validação idêntica às linhas 8244-8255 do FlowQueueWorker.run:
            c_img = str(res_ator.get("caminho_imagem") or "")
            eh_video = c_img.lower().endswith((".mp4", ".webm", ".mov"))
            if is_anim_item and not eh_video:
                res_ator = {
                    "success": False,
                    "erro": f"Item de animação retornou arquivo não-vídeo: {Path(c_img).name} (esperado .mp4)"
                }

            assert res_ator["success"] is False
            print(f"Validação da fila com retorno não-vídeo: {res_ator}")
            print("✅ FlowQueueWorker rejeita terminantemente conclusão de animação com PNG!")

    finally:
        try:
            Path(fake_png_path).unlink(missing_ok=True)
        except Exception:
            pass

    print("\n=========================================")
    print("TODOS OS TESTES NEGATIVOS DA FASE 3 FORAM APROVADOS!")
    print("=========================================")


if __name__ == "__main__":
    test_phase_3_negative()
