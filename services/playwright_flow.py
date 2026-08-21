"""
services/playwright_flow.py — Automação Google Flow via Playwright CDP
======================================================================
"""

import os
import re
import json
import time
import base64
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple

from config import PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc


def pw_log(msg: str, level: str = "info"):
    log_event("PLAYWRIGHT_FLOW", msg, level=level)


JS_FETCH_MEDIA_LIST = """
() => {
    try {
        const results = [];
        const imgs = document.querySelectorAll('img');
        imgs.forEach(img => {
            if (img.src && !img.src.startsWith('data:') && !img.src.includes('avatar') && !img.src.includes('icon') && !img.src.includes('googleusercontent')) {
                const rect = img.getBoundingClientRect();
                if (rect.width > 80 && rect.height > 80) {
                    const src = img.src;
                    const name = src.split('/').pop().split('?')[0] || 'imagem_' + Date.now() + '.png';
                    results.push({ name: name, src: src, type: 'image' });
                }
            }
        });
        const videos = document.querySelectorAll('video');
        videos.forEach(v => {
            const src = v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
            if (src && !src.startsWith('data:')) {
                const name = src.split('/').pop().split('?')[0] || 'video_' + Date.now() + '.mp4';
                results.push({ name: name, src: src, type: 'video' });
            }
        });
        return { ok: true, media: results };
    } catch(e) {
        return { ok: false, error: e.toString() };
    }
}
"""

JS_DOWNLOAD_BLOB_BASE64 = """
async (url) => {
    try {
        const res = await fetch(url);
        const blob = await res.blob();
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve({ ok: true, base64: reader.result, type: blob.type });
            reader.onerror = () => resolve({ ok: false, error: 'read_error' });
            reader.readAsDataURL(blob);
        });
    } catch(e) {
        return { ok: false, error: e.toString() };
    }
}
"""


class PlaywrightCDPWorker:
    def __init__(self, port: int = 9222):
        self.port = port
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_running_queue = False
        self.stop_requested = threading.Event()
        self.current_project_id: Optional[str] = None
        self.current_flow_mode: Optional[str] = None
        self.cena_ativa: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    def _check_is_active(self) -> bool:
        if not self.page:
            return False
        try:
            url = self.page.url
            return bool(url and url != "about:blank")
        except Exception:
            return False

    def _handle_start_session(self) -> Tuple[bool, str]:
        try:
            from playwright.sync_api import sync_playwright
            pw_log(f"Conectando ao Chrome via CDP na porta {self.port}...")
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
            contexts = self.browser.contexts
            if not contexts:
                pw_log("Nenhum contexto encontrado no Chrome CDP.", level="error")
                return False, "Nenhum contexto no Chrome."

            self.context = contexts[0]
            pages = self.context.pages
            flow_page = None
            for p in pages:
                try:
                    if "labs.google/fx/tools/flow" in (p.url or "") or "google.com" in (p.url or ""):
                        flow_page = p
                        break
                except Exception:
                    pass

            if flow_page:
                self.page = flow_page
                pw_log(f"Página Google Flow encontrada: {self.page.url}")
            elif pages:
                self.page = pages[0]
                pw_log(f"Usando página aberta: {self.page.url}")
            else:
                self.page = self.context.new_page()
                pw_log("Criada nova página no Chrome.")

            if "labs.google/fx/tools/flow" not in (self.page.url or ""):
                pw_log("Navegando para Google Flow...")
                self.page.goto("https://labs.google/fx/tools/flow", timeout=60000)
                self.page.wait_for_timeout(3000)

            return True, "Conectado com sucesso ao Google Flow via CDP."
        except Exception as e:
            pw_log(f"Erro ao conectar via CDP: {e}", level="error")
            return False, str(e)

    def _ensure_project_open(self, timeout_s: int = 30) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            url = self.page.url or ""
            if "/project/" in url:
                return True

            try:
                proj_card = self.page.locator('a[href*="/project/"], div[data-project-id]').first
                if proj_card.is_visible(timeout=1000):
                    pw_log("Abrindo projeto existente no Google Flow...")
                    proj_card.click()
                    self.page.wait_for_timeout(3000)
                    continue
            except Exception:
                pass

            try:
                for btn_text in ["New project", "Novo projeto", "Create project", "Criar projeto", "New", "Novo"]:
                    new_btn = self.page.locator(f'button:has-text("{btn_text}"), a:has-text("{btn_text}")').first
                    if new_btn.is_visible(timeout=1000):
                        pw_log(f"Clicando em '{btn_text}'...")
                        new_btn.click()
                        self.page.wait_for_timeout(3000)
                        break
            except Exception:
                pass

            self.page.wait_for_timeout(1500)

        return "/project/" in (self.page.url or "")

    def _disable_agent_mode(self):
        """Fecha qualquer gaveta de Agent/Untitled session pelo botão X e NUNCA clica no botão + Agent."""
        try:
            for sel_close in [
                'aside button[aria-label*="Close" i]',
                'aside button[aria-label*="Fechar" i]',
                'aside button:has(i:has-text("close"))',
                'aside button:has(svg path[d*="M19 6.41"])',
                'aside button:has(svg)',
                'button[aria-label*="Close" i]',
                'button[aria-label*="Fechar" i]',
            ]:
                for btn in self.page.locator(sel_close).all():
                    if btn.is_visible():
                        pw_log("Fechando drawer lateral de Agent (Untitled session)...")
                        btn.click()
                        self.page.wait_for_timeout(300)
                        break

            self.page.evaluate("""() => {
                const asides = document.querySelectorAll('aside, [role="dialog"]');
                asides.forEach(a => {
                    const btn = a.querySelector('button[aria-label*="close" i], button[aria-label*="fechar" i], button');
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                    }
                });
            }""")
            self.page.wait_for_timeout(200)
        except Exception as e:
            pw_log(f"Aviso em _disable_agent_mode: {e}", level="warn")

    def _set_output_mode(self, target_mode: str):
        """Configura os parâmetros exatos no Google Flow:
        - Imagem: Aba Image, 16:9, Nano Banana Pro, x1
        - Vídeo: Aba Video, 16:9, Veo 3.1 - Lite, x1
        """
        try:
            pw_log(f"Configurando Google Flow para modo: {target_mode.upper()}...")

            pill_clicked = False
            for sel_pill in [
                'button:has-text("Video -")',
                'button:has-text("Video ·")',
                'button:has-text("Image -")',
                'button:has-text("Image ·")',
                'button:has-text("Nano Banana")',
                'button:has-text("Veo")',
                'button:has-text("720p")',
                'button:has-text("1080p")',
                'button:has-text("16:9")',
                'button:has-text("x1")',
                'button:has(i:has-text("tune"))',
                'button:has(i:has-text("settings"))',
            ]:
                for pill in self.page.locator(f'{sel_pill}:not(aside *)').all():
                    if pill.is_visible():
                        pill.click()
                        pill_clicked = True
                        self.page.wait_for_timeout(400)
                        break
                if pill_clicked:
                    break

            if target_mode == "video":
                btn_vid = self.page.locator('button:has-text("Video"), [role="tab"]:has-text("Video")').first
                if btn_vid.is_visible(timeout=800):
                    btn_vid.click()
                    self.page.wait_for_timeout(250)

                btn_ratio = self.page.locator('button:has-text("16:9"), div:has-text("16:9")').first
                if btn_ratio.is_visible(timeout=600):
                    btn_ratio.click()
                    self.page.wait_for_timeout(200)

                for sel_dd in ['button:has-text("Veo")', '[role="combobox"]:has-text("Veo")', 'button:has-text("Lite")']:
                    dd = self.page.locator(sel_dd).first
                    if dd.is_visible(timeout=500):
                        dd.click()
                        self.page.wait_for_timeout(300)
                        break
                opt_model = self.page.locator('[role="option"]:has-text("Veo 3.1 - Lite"), [role="menuitem"]:has-text("Veo 3.1 - Lite"), button:has-text("Veo 3.1 - Lite"), div:has-text("Veo 3.1 - Lite")').first
                if opt_model.is_visible(timeout=800):
                    opt_model.click()
                    self.page.wait_for_timeout(200)

                btn_x1 = self.page.locator('button:has-text("x1")').first
                if btn_x1.is_visible(timeout=500):
                    btn_x1.click()
                    self.page.wait_for_timeout(150)

            else:
                btn_img = self.page.locator('button:has-text("Image"), [role="tab"]:has-text("Image")').first
                if btn_img.is_visible(timeout=800):
                    btn_img.click()
                    self.page.wait_for_timeout(250)

                btn_ratio = self.page.locator('button:has-text("16:9"), div:has-text("16:9")').first
                if btn_ratio.is_visible(timeout=600):
                    btn_ratio.click()
                    self.page.wait_for_timeout(200)

                for sel_dd in ['button:has-text("Nano Banana")', '[role="combobox"]:has-text("Nano Banana")', 'button:has-text("Banana")']:
                    dd = self.page.locator(sel_dd).first
                    if dd.is_visible(timeout=500):
                        dd.click()
                        self.page.wait_for_timeout(300)
                        break
                opt_model = self.page.locator('[role="option"]:has-text("Nano Banana Pro"), [role="menuitem"]:has-text("Nano Banana Pro"), button:has-text("Nano Banana Pro"), div:has-text("Nano Banana Pro")').first
                if opt_model.is_visible(timeout=800):
                    opt_model.click()
                    self.page.wait_for_timeout(200)

                btn_x1 = self.page.locator('button:has-text("x1")').first
                if btn_x1.is_visible(timeout=500):
                    btn_x1.click()
                    self.page.wait_for_timeout(150)

            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)
                self.page.mouse.click(300, 200)
                self.page.wait_for_timeout(200)
            except Exception:
                pass
        except Exception as e:
            pw_log(f"Erro em _set_output_mode ({target_mode}): {e}", level="warn")

    def _clean_prompt_text(self, prompt: str) -> str:
        if not prompt:
            return ""
        prompt = re.sub(r'\[Arquivo:[^\]]+\]', '', prompt)
        return " ".join(prompt.split()).strip()

    def _get_existing_media_names(self) -> Set[str]:
        try:
            res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
            if res.get("ok"):
                return {item["name"] for item in res.get("media", [])}
        except Exception:
            pass
        return set()

    def _processar_cena_individual(self, projeto_id: str, cena: Dict[str, Any], is_anim: bool = False) -> Tuple[bool, str]:
        cid = int(cena.get("id", 0))
        video_mode = is_anim
        timeout_s = 300 if video_mode else 120

        raw_prompt = cena.get("prompt_animacao") if video_mode else (cena.get("prompt_imagem") or cena.get("texto", ""))
        prompt = self._clean_prompt_text(raw_prompt)

        pw_log(f"Iniciando Cena {cid} (modo={'vídeo' if video_mode else 'imagem'}, timeout={timeout_s}s)...")
        scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_GERANDO)

        # 1. Garante projeto aberto
        if not self._ensure_project_open(timeout_s=30):
            return False, "Não foi possível abrir o projeto no Google Flow."

        # 2. Garante que qualquer chat/painel de agente esteja fechado
        self._disable_agent_mode()

        # 3. Força configuração do modo correto (Imagem vs Vídeo)
        target_mode = "video" if video_mode else "image"
        self._set_output_mode(target_mode)
        self.current_flow_mode = target_mode

        existing_names = self._get_existing_media_names()
        pw_log(f"Mídias existentes no Flow antes do envio: {len(existing_names)}")

        # 4. Localiza e foca o campo de prompt do DOCK PRINCIPAL (estritamente fora de aside)
        editor = None
        for sel in [
            'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[role="textbox"][data-slate-editor="true"]:not(aside *)',
            'div[role="textbox"][contenteditable="true"]:not(aside *)',
            'textarea:not(aside *)',
        ]:
            for loc in self.page.locator(sel).all():
                if loc.is_visible():
                    editor = loc
                    break
            if editor:
                break

        if not editor:
            return False, "Campo de prompt principal do Flow não encontrado na página."

        editor.click()
        self.page.wait_for_timeout(150)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.page.wait_for_timeout(150)

        # 5. Insere o texto do prompt
        if prompt:
            editor.click()
            self.page.wait_for_timeout(150)
            self.page.keyboard.insert_text(prompt)
            self.page.wait_for_timeout(400)

        # 6. Clica no botão Create / Gerar do dock principal
        submitted = False
        try:
            for sel_btn in [
                'button:has(i:has-text("arrow_forward")):not(aside *):not([role="dialog"] *)',
                'button[aria-label="Create"]:not(aside *)',
                'button[aria-label*="Create" i]:not(aside *)',
                'button[aria-label*="Criar" i]:not(aside *)',
                'button:has-text("Create"):not(aside *)',
                'button:has-text("Criar"):not(aside *)',
            ]:
                btn = self.page.locator(sel_btn).first
                if btn.is_visible(timeout=1000) and not btn.is_disabled():
                    btn.click()
                    submitted = True
                    pw_log(f"Prompt da Cena {cid} enviado via botão do canvas.")
                    break
        except Exception:
            pass

        if not submitted and editor:
            editor.focus()
            self.page.keyboard.press("Enter")
            pw_log(f"Prompt da Cena {cid} enviado via tecla Enter.")

        # 7. Aguarda a geração concluir
        start_wait = time.time()
        new_media_item = None
        while time.time() - start_wait < timeout_s:
            if self.stop_requested.is_set():
                return False, "Operação cancelada pelo usuário."

            self.page.wait_for_timeout(2500)

            curr_res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
            if not curr_res.get("ok"):
                continue

            curr_media = curr_res.get("media", [])
            for item in curr_media:
                if item["name"] not in existing_names:
                    if video_mode and item["type"] == "video":
                        new_media_item = item
                        break
                    elif not video_mode and item["type"] == "image":
                        new_media_item = item
                        break

            if new_media_item:
                pw_log(f"Nova mídia detectada no Flow para a Cena {cid}: {new_media_item['name']}")
                break

        if not new_media_item:
            return False, f"Timeout ({timeout_s}s) aguardando nova mídia no Google Flow para a cena {cid}."

        # 8. Baixa o arquivo via base64
        pw_log(f"Baixando mídia da Cena {cid} via CDP...")
        res_blob = self.page.evaluate(JS_DOWNLOAD_BLOB_BASE64, new_media_item["src"])
        if not res_blob.get("ok") or not res_blob.get("base64"):
            return False, f"Erro ao baixar blob da mídia: {res_blob.get('error')}"

        is_video_result = (new_media_item["type"] == "video") or ("video" in res_blob.get("type", ""))
        ext = ".mp4" if is_video_result else ".png"

        ts_ini = float(cena.get("tempo_inicio", 0))
        dur = float(cena.get("duracao", 5))
        ts_fim = float(cena.get("tempo_fim", ts_ini + dur))
        ts_i_str = scene_plan_svc._fmt_ts(ts_ini).replace(":", "-")
        ts_f_str = scene_plan_svc._fmt_ts(ts_fim).replace(":", "-")
        fname = f"{cid:03d}_{ts_i_str}_{ts_f_str}{ext}"

        dest_dir = PROJETOS_DIR / projeto_id / ("videos" if is_video_result else "imagens")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / fname

        user_dest_dir = Path.home() / "Downloads" / "ultracut3_midias" / projeto_id
        user_dest_dir.mkdir(parents=True, exist_ok=True)
        user_dest_path = user_dest_dir / fname

        try:
            raw_b64 = res_blob["base64"].split(",")[1]
            content_bytes = base64.b64decode(raw_b64)
            with open(dest_path, "wb") as f:
                f.write(content_bytes)
            try:
                with open(user_dest_path, "wb") as f_u:
                    f_u.write(content_bytes)
            except Exception:
                pass
            pw_log(f"Arquivo salvo com sucesso em: {dest_path}")
        except Exception as e_save:
            return False, f"Erro ao salvar arquivo em disco: {e_save}"

        # 9. Atualiza scene_plan.json
        new_status = scene_plan_svc.STATUS_ANIMADA if is_video_result else scene_plan_svc.STATUS_PRONTA_PARA_MONTAGEM
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "arquivo_midia": str(dest_path),
            "status": new_status,
            "erro_msg": "",
        })
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)

        return True, f"Cena {cid} gerada e importada com sucesso: {fname}"

    def _handle_run_queue(self, projeto_id: str, scene_ids: Optional[List[int]], modo: str):
        self.is_running_queue = True
        self.stop_requested.clear()

        try:
            if not self._check_is_active():
                ok, msg = self._handle_start_session()
                if not ok:
                    pw_log(f"Não foi possível iniciar sessão CDP para a fila: {msg}", level="error")
                    return

            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
            if not plan or not plan.get("cenas"):
                pw_log("scene_plan não encontrado para execução da fila.", level="warn")
                return

            cenas = plan["cenas"]
            target_ids = set(scene_ids) if scene_ids else None

            cenas_a_processar = []
            for c in cenas:
                cid = int(c.get("id", 0))
                if target_ids is not None and cid not in target_ids:
                    continue
                st = c.get("status")
                if modo == "animacao":
                    e_video = (c.get("tipo") == "video" or c.get("animar") is True)
                    if e_video:
                        cenas_a_processar.append(c)
                else:
                    if st != scene_plan_svc.STATUS_PRONTA_PARA_MONTAGEM:
                        cenas_a_processar.append(c)

            pw_log(f"Processando {len(cenas_a_processar)} cena(s) via Playwright CDP (projeto={projeto_id}, modo={modo}).")

            for cena in cenas_a_processar:
                if self.stop_requested.is_set():
                    pw_log("Fila pausada graciosamente pelo usuário.")
                    break

                cid = int(cena.get("id", 0))
                self.cena_ativa = {
                    "scene_id": cid,
                    "status": "GERANDO",
                    "mensagem": f"Gerando cena {cid} no Flow...",
                    "ts": time.time()
                }

                sucesso = False
                res_msg = ""
                for tentativa in range(2):
                    if self.stop_requested.is_set():
                        break

                    pw_log(f"Processando Cena {cid} (tentativa {tentativa + 1}/2)...")
                    ok, res_msg = self._processar_cena_individual(projeto_id, cena, is_anim=(modo == "animacao"))
                    if ok:
                        sucesso = True
                        break
                    else:
                        pw_log(f"Falha na tentativa {tentativa + 1} para cena {cid}: {res_msg}", level="warn")
                        time.sleep(2)

                if sucesso:
                    self.cena_ativa = {
                        "scene_id": cid,
                        "status": "CONCLUIDO",
                        "mensagem": f"Cena {cid} concluída!",
                        "ts": time.time()
                    }
                else:
                    if not self.stop_requested.is_set():
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "erro_msg": res_msg
                        })

                time.sleep(1)

        except Exception as e:
            pw_log(f"Erro inesperado na execução da fila Playwright CDP: {e}", level="error")
        finally:
            self.is_running_queue = False
            self.cena_ativa = None
            pw_log("Fila de produção Playwright CDP finalizada.")


_worker_instance: Optional[PlaywrightCDPWorker] = None
_worker_lock = threading.Lock()


class FlowQueueWorker:
    @staticmethod
    def get_worker() -> PlaywrightCDPWorker:
        global _worker_instance
        with _worker_lock:
            if _worker_instance is None:
                _worker_instance = PlaywrightCDPWorker(port=9222)
            return _worker_instance

    @staticmethod
    def start_worker(projeto_id: str, scene_ids: Optional[List[int]] = None, modo: str = "imagem") -> bool:
        worker = FlowQueueWorker.get_worker()
        if worker.is_running_queue:
            pw_log("Worker CDP já está em execução.", level="warn")
            return False

        t = threading.Thread(
            target=worker._handle_run_queue,
            args=(projeto_id, scene_ids, modo),
            daemon=True,
            name=f"FlowCDP-{projeto_id}"
        )
        t.start()
        pw_log("PlaywrightCDPWorker iniciado com sucesso.")
        return True

    @staticmethod
    def stop_worker() -> bool:
        worker = FlowQueueWorker.get_worker()
        if worker.is_running_queue:
            worker.stop_requested.set()
            pw_log("Solicitada parada da fila Playwright CDP.")
            return True
        return False

    @staticmethod
    def get_status() -> Dict[str, Any]:
        worker = FlowQueueWorker.get_worker()
        return {
            "conectado": worker._check_is_active(),
            "rodando_fila": worker.is_running_queue,
            "cena_ativa": worker.cena_ativa,
            "modo": worker.current_flow_mode or "desconhecido"
        }
