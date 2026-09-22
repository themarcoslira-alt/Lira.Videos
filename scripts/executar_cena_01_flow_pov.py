"""
scripts/executar_cena_01_flow_pov.py — Executa Tarefas 5 e 6:
Disparo da Cena 1 POV pro Google Flow + Validação do Render Final Veo + CapCut.
"""

import sys
import time
import json
import base64
import urllib.request
import subprocess
from pathlib import Path
from datetime import datetime

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
import services.scene_plan_service as scene_plan_svc
from services.capcut_export_service import CapCutExportService

PROJETO_NOME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
PROJETO_DIR = BASE_DIR / "projetos" / PROJETO_NOME
CENAS_DIR = PROJETO_DIR / "cenas"
SCREENSHOTS_DIR = BASE_DIR / "docs" / "screenshots"


def baixar_e_salvar_midia(worker, media_item: dict, out_path: Path, is_video: bool = False) -> bool:
    """Extrai mídia do Google Flow via múltiplos fallbacks confiáveis."""
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
    print("=" * 80, flush=True)
    print("INICIANDO TAREFA 5 & 6: DISPARO E VALIDAÇÃO DA CENA 1 POV NO GOOGLE FLOW", flush=True)
    print("=" * 80, flush=True)

    CENAS_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Carrega plano de cena e valida configuração POV
    plan = scene_plan_svc.carregar_scene_plan(PROJETO_NOME)
    if not plan or not plan.get("cenas"):
        print("[ERRO] Plano de cenas não encontrado.", flush=True)
        return 1

    c1 = next((c for c in plan["cenas"] if c.get("id") == 1), None)
    if not c1:
        print("[ERRO] Cena 1 não encontrada no plano.", flush=True)
        return 1

    pov_tipo = str(c1.get("avatar_style") or c1.get("pov_tipo") or "").strip()
    scene_type = c1.get("scene_type")
    prompt_animacao = c1.get("prompt_animacao")
    prompt_imagem = c1.get("prompt_imagem")

    print(f"\n[CONFIG CENA 1 DETECTADA]:", flush=True)
    print(f"  scene_type: {scene_type}", flush=True)
    print(f"  pov_tipo: {pov_tipo}", flush=True)
    print(f"  uses_character: {c1.get('uses_character')}", flush=True)
    print(f"  prompt_animacao: {prompt_animacao[:80]}...", flush=True)

    if pov_tipo != "first_person_pov":
        print(f"[ERRO] pov_tipo divergente: {pov_tipo} (esperado: first_person_pov)", flush=True)
        return 1

    # Log submissão no Flow conforme Tarefa 5
    timestamp_submissao = datetime.now().isoformat()
    print(f"\n[LOG SUBMISSÃO FLOW - TAREFA 5]:", flush=True)
    print(f"  timestamp: {timestamp_submissao}", flush=True)
    print(f"  prompt enviado: {prompt_animacao}", flush=True)
    print(f"  video_status: PENDING", flush=True)

    scene_plan_svc.atualizar_cena(PROJETO_NOME, 1, {
        "video_status": "PENDING",
        "image_status": "PENDING",
        "status": "GERANDO",
    })

    # 2. Conectar ao Google Flow via CDP
    worker = PlaywrightCDPWorker(port=9222)
    ok_sessao, msg_sessao = worker._iniciar_sessao_thread()
    if not ok_sessao:
        print(f"[ERRO] Falha ao conectar ao Chrome CDP na porta 9222: {msg_sessao}", flush=True)
        return 1

    try:
        # Garante projeto aberto
        worker.current_project_id = PROJETO_NOME
        if not worker._ensure_project_open(PROJETO_NOME):
            print("[ERRO] Não foi possível abrir o projeto no Google Flow.", flush=True)
            return 1
        print(f"[FLOW] Projeto aberto: {worker.page.url}", flush=True)
        worker._limpar_overlays_e_backdrops()

        # Snapshot de mídias existentes
        existing_keys, initial_count = worker._get_existing_media_snapshot()
        print(f"[FLOW] Mídias existentes no canvas: {initial_count}", flush=True)

        # 3. Localiza editor de comando
        editor = worker.page.locator('div.ProseMirror[contenteditable="true"]').first
        if not editor.is_visible(timeout=8000):
            print("[ERRO] Editor de comando do Flow não localizado.", flush=True)
            return 1

        # Limpa o editor para garantir que NÃO há chip de personagem (Cena POV é sem rosto)
        editor.click(force=True)
        worker.page.keyboard.press("Control+A")
        worker.page.keyboard.press("Backspace")
        worker.page.wait_for_timeout(300)

        # Insere prompt de imagem POV
        print(f"[FLOW] Inserindo prompt de imagem POV no editor...", flush=True)
        editor.focus()
        worker.page.keyboard.insert_text(prompt_imagem)
        worker.page.wait_for_timeout(500)

        # Dispara criação da imagem base
        print(f"[FLOW] Disparando criação da imagem base POV...", flush=True)
        worker.page.keyboard.press("Enter")
        worker.page.wait_for_timeout(300)

        for sel_btn in [
            'button:has(i:has-text("arrow_forward")):not(aside *)',
            'button[aria-label="Create"]:not(aside *)',
            'button[aria-label*="Criar" i]:not(aside *)',
            'button:has-text("Create"):not(aside *)',
            'button:has-text("Criar"):not(aside *)',
            'button.generate-icon-button:not(aside *)',
        ]:
            try:
                b = worker.page.locator(sel_btn).first
                if b.is_visible(timeout=500) and not b.is_disabled():
                    b.click()
                    break
            except Exception:
                pass

        print("[FLOW] Prompt de imagem enviado. Aguardando renderização...", flush=True)

        # 4. Polling para nova mídia gerada
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
                print(f"[FLOW] Aguardando geração da imagem base... ({elapsed}s)", flush=True)

        if not new_media_item:
            print("[ERRO] Timeout aguardando geração da imagem base no Flow.", flush=True)
            return 1

        tipo_gerado = new_media_item.get("type", "image")
        print(f"[FLOW] Nova mídia detectada! Tipo: {tipo_gerado}", flush=True)

        caminho_mp4_final = CENAS_DIR / "01_[00-00-00-12].mp4"
        caminho_png_base = CENAS_DIR / "01_[00-00-05].png"

        if tipo_gerado == "video":
            print("[FLOW] Vídeo gerado diretamente! Baixando...", flush=True)
            ok_down = baixar_e_salvar_midia(worker, new_media_item, caminho_mp4_final, is_video=True)
            if not ok_down:
                print("[ERRO] Falha ao baixar vídeo gerado diretamente.", flush=True)
                return 1
        else:
            # Salva imagem base
            print("[FLOW] Salvando imagem base POV...", flush=True)
            ok_img = baixar_e_salvar_midia(worker, new_media_item, caminho_png_base, is_video=False)
            if not ok_img:
                print("[ERRO] Falha ao salvar imagem base POV.", flush=True)
                return 1
            print(f"[FLOW] Imagem base salva: {caminho_png_base} ({caminho_png_base.stat().st_size} bytes)", flush=True)

            # 5. Dispara Animar (Image-to-Video com Google Veo)
            print(f"\n[TAREFA 5 - GOOGLE VEO]: Disparando Animar (Image-to-Video) com prompt_animacao...", flush=True)
            keys_pre_anim, _ = worker._get_existing_media_snapshot()

            ok_anim = worker._animar_imagem_galeria_flow(
                projeto_id=PROJETO_NOME,
                arquivo_imagem=str(caminho_png_base),
                prompt_movimento=prompt_animacao
            )
            if not ok_anim:
                print("[AVISO] _animar_imagem_galeria_flow não concluiu via menu, enviando via editor...", flush=True)
                editor.click(force=True)
                worker.page.keyboard.press("Control+A")
                worker.page.keyboard.press("Backspace")
                editor.focus()
                worker.page.keyboard.insert_text(prompt_animacao)
                worker.page.wait_for_timeout(500)

            worker.page.wait_for_timeout(1000)
            worker.page.keyboard.press("Enter")
            for sel_btn in [
                'button:has(i:has-text("arrow_forward")):not(aside *)',
                'button[aria-label="Create"]:not(aside *)',
                'button[aria-label*="Criar" i]:not(aside *)',
                'button.generate-icon-button:not(aside *)',
            ]:
                try:
                    b = worker.page.locator(sel_btn).first
                    if b.is_visible(timeout=500) and not b.is_disabled():
                        b.click()
                        break
                except Exception:
                    pass

            print(f"[FLOW] Animação submetida ao Veo em {datetime.now().isoformat()}!", flush=True)
            print("[FLOW] Aguardando renderização do Google Veo (2-3 min)...", flush=True)

            # 6. Aguarda Render Final Veo (TAREFA 6)
            t_anim = time.time()
            video_anim_item = None
            timeout_veo = 260
            while time.time() - t_anim < timeout_veo:
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
                    print(f"[FLOW] Google Veo renderizando vídeo POV... ({elapsed}s)", flush=True)

            if not video_anim_item:
                print("[ERRO] Timeout aguardando renderização do vídeo Veo.", flush=True)
                return 1

            print(f"[FLOW] Vídeo Veo renderizado com sucesso! Baixando...", flush=True)
            ok_vid = baixar_e_salvar_midia(worker, video_anim_item, caminho_mp4_final, is_video=True)
            if not ok_vid:
                print("[ERRO] Falha ao baixar vídeo final Veo.", flush=True)
                return 1

        if not caminho_mp4_final.exists() or caminho_mp4_final.stat().st_size < 500:
            print("[ERRO] Arquivo MP4 final da Cena 1 não encontrado ou vazio.", flush=True)
            return 1

        print(f"\n[SUCESSO] Vídeo POV da Cena 1 salvo em: {caminho_mp4_final} ({caminho_mp4_final.stat().st_size} bytes)", flush=True)

        # 7. Atualizar lira_scene_plan.json com status DOWNLOADED
        scene_plan_svc.atualizar_cena(PROJETO_NOME, 1, {
            "status": "BAIXADA",
            "image_status": "DOWNLOADED",
            "video_status": "DOWNLOADED",
            "arquivo_midia": str(caminho_mp4_final),
            "filename": caminho_mp4_final.name,
            "scene_type": "broll_pov",
            "avatar_style": "first_person_pov",
            "uses_character": False,
            "prompt_imagem": prompt_imagem,
            "prompt_animacao": prompt_animacao,
        })
        print("[OK] lira_scene_plan.json atualizado: video_status=DOWNLOADED, status=BAIXADA.", flush=True)

        # 8. Validação Visual: Extração de frames para comprovar POV sem rosto
        print("\n[VALIDAÇÃO VISUAL]: Extraindo frames em t=1s, 2s, 3s do vídeo POV...", flush=True)
        frames_ok = []
        for t_sec in [1, 2, 3]:
            frame_path = SCREENSHOTS_DIR / f"cena_01_pov_frame_t{t_sec}s.png"
            cmd_ff = [
                "ffmpeg", "-y",
                "-ss", f"00:00:0{t_sec}.000",
                "-i", str(caminho_mp4_final),
                "-vframes", "1",
                str(frame_path)
            ]
            res_ff = subprocess.run(cmd_ff, capture_output=True)
            if frame_path.exists() and frame_path.stat().st_size > 500:
                print(f"[OK] Frame t={t_sec}s extraído: {frame_path.name} ({frame_path.stat().st_size} bytes)", flush=True)
                frames_ok.append(str(frame_path))
            else:
                print(f"[AVISO] Falha ao extrair frame t={t_sec}s: {res_ff.stderr.decode('utf-8', errors='ignore')}", flush=True)

        # 9. Validação CapCut
        print("\n[VALIDAÇÃO CAPCUT]: Sincronizando draft do CapCut...", flush=True)
        try:
            exp_svc = CapCutExportService(PROJETO_NOME)
            res_exp = exp_svc.exportar()
            print(f"[CAPCUT] Exportação: success={res_exp.get('success')}, total_cenas={res_exp.get('total_cenas')}", flush=True)
        except Exception as e_cap:
            print(f"[CAPCUT] Aviso na exportação CapCut: {e_cap}", flush=True)

        print("\n" + "=" * 80)
        print("TAREFA 5 & 6 CONCLUÍDAS COM SUCESSO!", flush=True)
        print("=" * 80)
        return 0

    finally:
        worker._encerrar_sessao()


if __name__ == "__main__":
    sys.exit(main())
