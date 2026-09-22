"""
scripts/gerar_cena_01_completo.py — Executa a TAREFA A3 (Regeneração da Cena 1)
no Google Flow autenticado na conta MARCOS LIRA.
"""

import sys
import time
import json
import base64
import urllib.request
import subprocess
from pathlib import Path

# Garante raiz no path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from services.playwright_flow import (
    PlaywrightCDPWorker,
    JS_FETCH_MEDIA_LIST,
    JS_EXTRACT_CANVAS_DATA_URL,
    pw_log,
)
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc

PROJETO_NOME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
PROJETO_DIR = BASE_DIR / "projetos" / PROJETO_NOME
CENAS_DIR = PROJETO_DIR / "cenas"
VALIDACAO_DIR = CENAS_DIR / "validacao_cena_01"

PROMPT_IMAGEM = (
    "Photorealistic documentary third-person demonstration shot of @Marcos in rustic "
    "garden setting, hands in foreground examining 12 tomato plants, natural morning "
    "daylight with soft contrast, 35mm lens, shallow depth of field"
)

PROMPT_ANIMACAO = (
    "Third-person demonstration. Hands in foreground gently inspecting tomato foliage. "
    "No face-to-camera dialogue. Natural body movement, documentary camera angle, "
    "subtle handheld sway, mouth closed, no lip sync, no speech, natural gestures only."
)


def baixar_e_salvar_midia(worker, media_item: dict, out_path: Path, is_video: bool = False) -> bool:
    content_bytes = None

    # 1. Canvas DataURL
    if "domIndex" in media_item or "mediaKey" in media_item:
        try:
            res_c = worker.page.evaluate(
                JS_EXTRACT_CANVAS_DATA_URL,
                media_item.get("mediaKey") or media_item.get("domIndex", 0)
            )
            if res_c and res_c.get("ok") and res_c.get("dataUrl"):
                content_bytes = base64.b64decode(res_c["dataUrl"].split(",")[1])
        except Exception as e:
            pw_log(f"[DOWNLOAD] Falha no canvas dataUrl: {e}", level="debug")

    # 2. dataUrl direto
    if not content_bytes and media_item.get("dataUrl"):
        try:
            content_bytes = base64.b64decode(media_item["dataUrl"].split(",")[1])
        except Exception as e:
            pw_log(f"[DOWNLOAD] Falha no mediaItem dataUrl: {e}", level="debug")

    # 3. page.request.get
    if not content_bytes and media_item.get("src"):
        try:
            resp = worker.page.request.get(media_item["src"])
            if resp.ok and len(resp.body()) > 500:
                content_bytes = resp.body()
        except Exception as e:
            pw_log(f"[DOWNLOAD] Falha no page.request.get: {e}", level="debug")

    # 4. _baixar_midia_com_retry
    if not content_bytes and media_item.get("src"):
        try:
            res_b = worker._baixar_midia_com_retry(media_item["src"], is_video=is_video)
            if res_b.get("ok") and res_b.get("base64"):
                content_bytes = base64.b64decode(res_b["base64"].split(",")[1])
        except Exception as e:
            pw_log(f"[DOWNLOAD] Falha no _baixar_midia_com_retry: {e}", level="debug")

    # 5. urlretrieve
    if not content_bytes and media_item.get("src") and media_item["src"].startswith("http"):
        try:
            urllib.request.urlretrieve(media_item["src"], str(out_path))
            if out_path.exists() and out_path.stat().st_size > 500:
                return True
        except Exception as e:
            pw_log(f"[DOWNLOAD] Falha no urlretrieve: {e}", level="debug")

    if content_bytes and len(content_bytes) > 500:
        out_path.write_bytes(content_bytes)
        return True

    return False


def main():
    print("=" * 70, flush=True)
    print("INICIANDO TAREFA A3: REGENERAÇÃO DA CENA 1", flush=True)
    print("=" * 70, flush=True)

    CENAS_DIR.mkdir(parents=True, exist_ok=True)
    VALIDACAO_DIR.mkdir(parents=True, exist_ok=True)

    worker = PlaywrightCDPWorker(port=9222)
    ok_sessao, msg_sessao = worker._iniciar_sessao_thread()
    if not ok_sessao:
        print(f"[ERRO] Falha ao iniciar sessão CDP: {msg_sessao}", flush=True)
        return 1

    try:
        # 1. Saldo inicial
        saldo_inicial = worker.get_exact_balance()
        print(f"\n[SALDO INICIAL] {saldo_inicial} créditos disponíveis no Google Flow.", flush=True)
        worker._limpar_overlays_e_backdrops()

        # 2. Garante projeto aberto
        worker.current_project_id = PROJETO_NOME
        if not worker._ensure_project_open(PROJETO_NOME):
            print("[ERRO] Não foi possível abrir o projeto no Google Flow.", flush=True)
            return 1
        print(f"[FLOW] Projeto aberto: {worker.page.url}", flush=True)
        worker._limpar_overlays_e_backdrops()

        # 3. Snapshot de mídias existentes
        existing_keys, initial_count = worker._get_existing_media_snapshot()
        print(f"[FLOW] Mídias existentes no canvas: {initial_count}", flush=True)

        # 4. Localiza editor e garante chip @Marcos
        editor = worker.page.locator('div.ProseMirror[contenteditable="true"]').first
        if not editor.is_visible(timeout=5000):
            print("[ERRO] Editor de prompt não localizado.", flush=True)
            return 1

        worker._limpar_overlays_e_backdrops()
        editor.focus()
        tem_chip = worker._verificar_chip_personagem_no_editor(editor)

        if not tem_chip:
            # Limpa editor e anexa personagem @Marcos
            editor.click(force=True)
            worker.page.keyboard.press("Control+A")
            worker.page.keyboard.press("Backspace")
            worker.page.wait_for_timeout(300)

            char_info = character_svc.obter_personagem_cena(PROJETO_NOME, {"uses_character": True, "character_ref": "@Marcos"})
            print(f"[FLOW] Anexando personagem: {char_info.get('nome')}...", flush=True)
            ok_char = worker._selecionar_referencia_flow(
                projeto_id=PROJETO_NOME,
                nome_personagem=char_info.get("nome", "Marcos"),
                tipo=char_info.get("tipo", "personagem"),
                ref_tag=char_info.get("referencia_flow", "@Marcos"),
                arquivo_flow=char_info.get("arquivo_flow", ""),
                imagem_abs=char_info.get("imagem_abs", ""),
                flow_character_id=char_info.get("flow_character_id", ""),
            )
            print(f"[FLOW] Personagem anexado: {ok_char}", flush=True)
        else:
            print("[FLOW] Chip @Marcos já está presente no editor!", flush=True)

        # Insere prompt visual puro
        prompt_visual_puro = worker._clean_prompt_text(PROMPT_IMAGEM, ref_tag="@Marcos", strip_character_tag=True)
        editor.focus()
        worker.page.keyboard.insert_text(" " + prompt_visual_puro)
        worker.page.wait_for_timeout(500)

        # Dispara criação
        print(f"[FLOW] Disparando comando para Cena 1...", flush=True)
        worker.page.keyboard.press("Enter")
        worker.page.wait_for_timeout(300)

        for sel_btn in [
            'button:has(i:has-text("arrow_forward")):not(aside *)',
            'button[aria-label="Create"]:not(aside *)',
            'button[aria-label*="Criar" i]:not(aside *)',
            'button:has-text("Create"):not(aside *)',
            'button:has-text("Criar"):not(aside *)',
        ]:
            try:
                b = worker.page.locator(sel_btn).first
                if b.is_visible(timeout=500) and not b.is_disabled():
                    b.click()
                    break
            except Exception:
                pass

        print("[FLOW] Prompt enviado com sucesso. Aguardando geração...", flush=True)

        # 5. Polling para nova mídia
        t0 = time.time()
        timeout_s = 180
        new_media_item = None
        while time.time() - t0 < timeout_s:
            try:
                res = worker.page.evaluate(JS_FETCH_MEDIA_LIST)
                if res and res.get("ok"):
                    for it in res.get("media", []):
                        mk = it.get("mediaKey") or it.get("id") or it.get("src")
                        if mk and mk not in existing_keys:
                            new_media_item = it
                            break
                if new_media_item:
                    break
            except Exception:
                pass
            time.sleep(2)
            elapsed = int(time.time() - t0)
            if elapsed % 10 == 0:
                print(f"[FLOW] Aguardando renderização... ({elapsed}s)", flush=True)

        if not new_media_item:
            print("[ERRO] Timeout aguardando nova mídia no Flow.", flush=True)
            return 1

        tipo_gerado = new_media_item.get("type", "image")
        print(f"[FLOW] Nova mídia detectada! Tipo: {tipo_gerado}", flush=True)

        caminho_mp4_final = CENAS_DIR / "01_[00-00-00-12].mp4"

        if tipo_gerado == "video":
            print("[FLOW] Baixando vídeo gerado diretamente...", flush=True)
            ok_down = baixar_e_salvar_midia(worker, new_media_item, caminho_mp4_final, is_video=True)
            if not ok_down:
                print("[ERRO] Falha ao baixar vídeo direto.", flush=True)
                return 1
        else:
            print("[FLOW] Extraindo imagem gerada com @Marcos...", flush=True)
            caminho_png = CENAS_DIR / "01_[00-00-05].png"
            ok_img = baixar_e_salvar_midia(worker, new_media_item, caminho_png, is_video=False)
            if not ok_img:
                print("[ERRO] Falha ao salvar imagem gerada.", flush=True)
                return 1
            print(f"[FLOW] Imagem salva em: {caminho_png} ({caminho_png.stat().st_size} bytes)", flush=True)

            print(f"[FLOW] Disparando Animar (Image-to-Video) com prompt_animacao...", flush=True)
            keys_pre_anim, _ = worker._get_existing_media_snapshot()
            ok_anim = worker._animar_imagem_galeria_flow(
                projeto_id=PROJETO_NOME,
                arquivo_imagem=str(caminho_png),
                prompt_movimento=PROMPT_ANIMACAO
            )
            if not ok_anim:
                print("[AVISO] _animar_imagem_galeria_flow retornou False, tentando submissão manual do editor...", flush=True)

            worker.page.wait_for_timeout(1000)
            worker.page.keyboard.press("Enter")
            for sel_btn in [
                'button:has(i:has-text("arrow_forward")):not(aside *)',
                'button[aria-label="Create"]:not(aside *)',
                'button[aria-label*="Criar" i]:not(aside *)',
            ]:
                try:
                    b = worker.page.locator(sel_btn).first
                    if b.is_visible(timeout=500) and not b.is_disabled():
                        b.click()
                        break
                except Exception:
                    pass

            print("[FLOW] Animação submetida. Aguardando vídeo Veo...", flush=True)

            t_anim = time.time()
            video_anim_item = None
            while time.time() - t_anim < 240:
                try:
                    res_anim = worker.page.evaluate(JS_FETCH_MEDIA_LIST)
                    if res_anim and res_anim.get("ok"):
                        for it in res_anim.get("media", []):
                            mk = it.get("mediaKey") or it.get("id") or it.get("src")
                            if it.get("type") == "video" and mk not in keys_pre_anim:
                                video_anim_item = it
                                break
                    if video_anim_item:
                        break
                except Exception:
                    pass
                time.sleep(3)
                elapsed = int(time.time() - t_anim)
                if elapsed % 15 == 0:
                    print(f"[FLOW] Aguardando renderização do vídeo Veo... ({elapsed}s)", flush=True)

            if not video_anim_item:
                print("[ERRO] Timeout aguardando vídeo da animação.", flush=True)
                return 1

            print(f"[FLOW] Vídeo Veo gerado com sucesso! Baixando...", flush=True)
            ok_vid = baixar_e_salvar_midia(worker, video_anim_item, caminho_mp4_final, is_video=True)
            if not ok_vid:
                print("[ERRO] Falha ao baixar vídeo final.", flush=True)
                return 1

        if not caminho_mp4_final.exists() or caminho_mp4_final.stat().st_size == 0:
            print("[ERRO] Arquivo MP4 final não foi salvo em disco.", flush=True)
            return 1

        print(f"\n[SUCESSO] Vídeo da Cena 1 salvo em: {caminho_mp4_final} ({caminho_mp4_final.stat().st_size} bytes)", flush=True)

        # 6. Extrair frames t=1s, 3s, 5s via ffmpeg
        print("\n[VALIDAÇÃO VISUAL] Extraindo frames t=1s, 3s, 5s...", flush=True)
        for t_sec in [1, 3, 5]:
            frame_path = VALIDACAO_DIR / f"frame_t{t_sec}s.png"
            cmd_ff = [
                "ffmpeg", "-y",
                "-ss", f"00:00:0{t_sec}.000",
                "-i", str(caminho_mp4_final),
                "-vframes", "1",
                str(frame_path)
            ]
            res_ff = subprocess.run(cmd_ff, capture_output=True)
            if frame_path.exists():
                print(f"[OK] Frame t={t_sec}s extraído: {frame_path.name} ({frame_path.stat().st_size} bytes)", flush=True)
            else:
                print(f"[ERRO] Falha ao extrair frame t={t_sec}s: {res_ff.stderr.decode('utf-8', errors='ignore')}", flush=True)

        # 7. Saldo final e métricas
        saldo_final = worker.get_exact_balance()
        creditos_gastos = (saldo_inicial - saldo_final) if (saldo_inicial is not None and saldo_final is not None) else "N/A"
        print(f"\n[SALDO FINAL] {saldo_final} créditos (Consumidos nesta geração: {creditos_gastos})", flush=True)

        # 8. Atualizar lira_scene_plan.json
        scene_plan_svc.atualizar_cena(PROJETO_NOME, 1, {
            "status": "PRONTA",
            "image_status": "DOWNLOADED",
            "video_status": "DOWNLOADED",
            "arquivo_midia": str(caminho_mp4_final),
            "filename": caminho_mp4_final.name,
            "scene_type": "avatar_hands_on",
            "avatar_style": "third_person_hands_on",
            "prompt_imagem": PROMPT_IMAGEM,
            "prompt_animacao": PROMPT_ANIMACAO,
        })
        print("[OK] lira_scene_plan.json atualizado com status PRONTA.", flush=True)
        print("=" * 70, flush=True)
        print("TAREFA A3 CONCLUÍDA COM SUCESSO!", flush=True)
        print("=" * 70, flush=True)
        return 0

    finally:
        worker._encerrar_sessao()


if __name__ == "__main__":
    sys.exit(main())
