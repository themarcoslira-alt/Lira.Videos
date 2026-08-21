"""
services/playwright_flow.py — Lira Studio (Playwright CDP Flow Automation)
========================================================================
Motor de automação embutido para o Google Flow via Chrome DevTools Protocol (CDP).
Controla o Chrome real com perfil persistente e porta de depuração remota 9222.
"""

import os
import sys
import time
import json
import re
import base64
import logging
import queue
import urllib.request
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple

from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page

from config import PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc

# ---------------------------------------------------------------------------
# Configurações e Logs
# ---------------------------------------------------------------------------

CHROME_PROFILE_DIR = Path("C:/ultracut3/chrome_profile")
FLOW_URL = "https://labs.google/fx/tools/flow"
CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

LOG_FILE = Path("C:/ultracut3/logs/playwright.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("playwright_flow")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("[PLAYWRIGHT] %(message)s"))
    logger.addHandler(ch)

def pw_log(msg: str, level: str = "info"):
    if level == "error":
        logger.error(msg)
    elif level == "warn":
        logger.warning(msg)
    else:
        logger.info(msg)
    try:
        log_event("PLAYWRIGHT", msg, level=level)
    except Exception:
        pass


def find_chrome_executable() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chrome.exe",
        r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return "chrome.exe"


# ---------------------------------------------------------------------------
# Scripts JS para Executar no Contexto da Página Flow
# ---------------------------------------------------------------------------

MEDIA_REDIRECT_URL = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name="

JS_FETCH_MEDIA_LIST = """
async () => {
    try {
        const m = location.pathname.match(/\\/project\\/([0-9a-f-]{36})/i);
        if (!m) return { ok: false, error: "Sem /project/<id>" };
        const projectId = m[1];
        const input = encodeURIComponent(JSON.stringify({ json: { projectId } }));
        const resp = await fetch("/fx/api/trpc/flow.projectInitialData?input=" + input, {
            credentials: "same-origin",
        });
        if (!resp.ok) return { ok: false, error: "HTTP " + resp.status };
        const data = await resp.json();
        const pc = data?.result?.data?.json?.projectContents || {};
        const media = Array.isArray(pc.media) ? pc.media : [];
        const list = media.map(item => {
            const req = item.mediaMetadata?.requestData || {};
            const isImage = !!item.image || !!req.imageGenerationRequestData;
            const prompt = item.image?.generatedImage?.prompt ||
                           item.video?.generatedVideo?.prompt ||
                           (Array.isArray(req.promptInputs) && req.promptInputs[0]?.textInput) || "";
            return {
                name: item.name,
                kind: isImage ? "image" : "video",
                prompt: String(prompt),
                createTime: item.mediaMetadata?.createTime || "",
                workflowId: item.workflowId || "",
                url: "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=" + item.name
            };
        });
        return { ok: true, media: list, projectId };
    } catch(e) {
        return { ok: false, error: String(e.message || e) };
    }
}
"""

JS_PASTE_IMAGE = """
(dataUrl) => {
    try {
        const arr = dataUrl.split(",");
        const mime = arr[0].match(/:(.*?);/)[1];
        const bstr = atob(arr[1]);
        let n = bstr.length;
        const u8arr = new Uint8Array(n);
        while (n--) {
            u8arr[n] = bstr.charCodeAt(n);
        }
        const file = new File([u8arr], "reference.png", { type: mime });
        
        const el = document.querySelector('[data-slate-editor="true"][contenteditable="true"]') ||
                   document.querySelector('div[role="textbox"][data-slate-editor="true"]') ||
                   document.querySelector('[contenteditable="true"]');
        if (!el) return { ok: false, error: "Editor não encontrado" };

        const dt = new DataTransfer();
        dt.items.add(file);
        const ev = new ClipboardEvent("paste", {
            bubbles: true,
            cancelable: true,
            composed: true,
            clipboardData: dt
        });
        el.dispatchEvent(ev);
        return { ok: true };
    } catch (e) {
        return { ok: false, error: String(e.message || e) };
    }
}
"""

JS_FETCH_BLOB_BASE64 = """
async (url) => {
    try {
        const resp = await fetch(url, { credentials: "same-origin" });
        if (!resp.ok) return { ok: false, error: "HTTP " + resp.status };
        const blob = await resp.blob();
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve({ ok: true, base64: reader.result });
            reader.onerror = () => resolve({ ok: false, error: "Falha ao ler blob" });
            reader.readAsDataURL(blob);
        });
    } catch (e) {
        return { ok: false, error: String(e.message || e) };
    }
}
"""


# ---------------------------------------------------------------------------
# Worker Thread Dedicado do Playwright / CDP
# ---------------------------------------------------------------------------

class PlaywrightWorkerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="PlaywrightCDPWorker")
        self.cmd_queue = queue.Queue()
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.stop_requested = threading.Event()
        self.is_running_queue = False
        self.cena_ativa: Dict[str, Any] = {}

    def run(self):
        pw_log("PlaywrightCDPWorker iniciado com sucesso.")
        while True:
            try:
                cmd_data = self.cmd_queue.get()
                if cmd_data is None:
                    break

                cmd_type = cmd_data.get("type")
                reply_event = cmd_data.get("reply_event")
                result_box = cmd_data.get("result_box", {})

                try:
                    if cmd_type == "START_SESSION":
                        ok, msg = self._handle_start_session()
                        result_box["ok"] = ok
                        result_box["msg"] = msg

                    elif cmd_type == "CLOSE_SESSION":
                        self._handle_close_session()
                        result_box["ok"] = True

                    elif cmd_type == "CHECK_ACTIVE":
                        result_box["active"] = self._check_is_active()

                    elif cmd_type == "RUN_QUEUE":
                        projeto_id = cmd_data.get("projeto_id", "")
                        scene_ids = cmd_data.get("scene_ids")
                        modo = cmd_data.get("modo", "imagem")
                        result_box["ok"] = True
                        if reply_event:
                            reply_event.set()
                        self._handle_run_queue(projeto_id, scene_ids, modo)
                        continue

                except Exception as e_cmd:
                    pw_log(f"Erro ao executar {cmd_type}: {e_cmd}", level="error")
                    result_box["ok"] = False
                    result_box["error"] = str(e_cmd)
                finally:
                    if reply_event and not reply_event.is_set():
                        reply_event.set()

            except Exception as e_loop:
                pw_log(f"Exceção no loop do worker CDP: {e_loop}", level="error")
                time.sleep(1)

    def _is_port_open(self) -> bool:
        try:
            req = urllib.request.Request(f"{CDP_URL}/json/version")
            with urllib.request.urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _check_is_active(self) -> bool:
        try:
            if not self._is_port_open():
                return False
            if self.page is None or self.page.is_closed():
                # Tenta reconectar a página ativa
                self._find_or_create_flow_page()
            if self.page and not self.page.is_closed():
                _ = self.page.evaluate("1 + 1")
                return True
            return False
        except Exception:
            return False

    def _find_or_create_flow_page(self) -> Optional[Page]:
        if not self.browser:
            return None
        try:
            contexts = self.browser.contexts
            if not contexts:
                return None
            ctx = contexts[0]
            for p in ctx.pages:
                if not p.is_closed() and "labs.google/fx" in (p.url or ""):
                    self.page = p
                    return p
            # Se não achou com labs.google, pega a primeira página aberta
            for p in ctx.pages:
                if not p.is_closed():
                    self.page = p
                    if "labs.google" not in (p.url or ""):
                        p.goto(FLOW_URL, wait_until="domcontentloaded", timeout=45000)
                    return p
            # Cria nova página
            self.page = ctx.new_page()
            self.page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=45000)
            return self.page
        except Exception as e:
            pw_log(f"Erro ao buscar página Flow: {e}", level="warn")
            return None

    def _handle_start_session(self) -> Tuple[bool, str]:
        pw_log("Iniciando conexão CDP com o Chrome do Flow...")
        try:
            if self.playwright is None:
                self.playwright = sync_playwright().start()

            # 1. Se a porta 9222 não estiver aberta, inicia o Chrome com a porta 9222 e perfil persistente
            if not self._is_port_open():
                CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                chrome_exe = find_chrome_executable()
                pw_log(f"Iniciando Chrome em {chrome_exe} com perfil persistente e porta {CDP_PORT}...")
                
                cmd = [
                    chrome_exe,
                    f"--remote-debugging-port={CDP_PORT}",
                    f"--user-data-dir={str(CHROME_PROFILE_DIR)}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                    FLOW_URL,
                ]
                subprocess.Popen(cmd)

                # Aguarda a porta 9222 responder
                start_wait = time.time()
                while time.time() - start_wait < 15:
                    if self._is_port_open():
                        break
                    time.sleep(0.5)

                if not self._is_port_open():
                    return False, f"Timeout aguardando Chrome iniciar na porta {CDP_PORT}."

            # 2. Conecta via CDP
            pw_log(f"Conectando Playwright via CDP em {CDP_URL}...")
            self.browser = self.playwright.chromium.connect_over_cdp(CDP_URL)
            self._find_or_create_flow_page()

            if self.page:
                try:
                    self.page.bring_to_front()
                except Exception:
                    pass

            pw_log("Conexão CDP com o Flow estabelecida com sucesso!")
            return True, "Conectado ao Chrome do Flow via CDP."

        except Exception as e:
            err = f"Falha na conexão CDP com o Flow: {e}"
            pw_log(err, level="error")
            return False, err

    def _handle_close_session(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.page = None
        self.browser = None
        self.playwright = None
        pw_log("Sessão CDP Playwright encerrada.")

    def _ensure_project_open(self, timeout_s: int = 30) -> bool:
        if not self._check_is_active():
            self._handle_start_session()

        if not self.page:
            return False

        url = self.page.url or ""
        if "/project/" in url:
            return True

        pw_log("Aba não está em um projeto Flow. Abrindo ou criando projeto...")
        start = time.time()
        while time.time() - start < timeout_s:
            url = self.page.url or ""
            if "/project/" in url:
                pw_log(f"Projeto Flow aberto: {url}")
                return True

            # Tenta clicar em projeto existente
            try:
                proj_card = self.page.locator('a[href*="/project/"], [data-testid*="project-card"]').first
                if proj_card.is_visible(timeout=1500):
                    pw_log("Abrindo projeto existente encontrado na tela...")
                    proj_card.click()
                    self.page.wait_for_timeout(3000)
                    continue
            except Exception:
                pass

            # Tenta botão de novo projeto
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
        try:
            for btn in self.page.locator('button').all():
                if not btn.is_visible():
                    continue
                txt = (btn.text_content() or "").strip().lower()
                if "agent" in txt or "agente" in txt:
                    if btn.get_attribute("aria-pressed") == "true":
                        pw_log("Desativando modo Agent no Flow...")
                        btn.click()
                        self.page.wait_for_timeout(400)
                        break
        except Exception:
            pass

    def _set_output_mode(self, target_mode: str):
        """Configura os parâmetros exatos solicitados no Google Flow:
        - Vídeo: Aba Video, Ingredients, 16:9, Veo 3.1 - Lite, x1
        - Imagem: Aba Image, 16:9, Nano Banana Pro, x1
        """
        try:
            # 1. Abre o menu/popover de parâmetros do prompt se necessário
            for sel_pill in [
                'button:has-text("Video ·")',
                'button:has-text("Nano Banana")',
                'button:has-text("Veo")',
                'button:has-text("720p")',
                'button:has-text("1080p")',
                'button:has-text("x1")',
            ]:
                pill = self.page.locator(sel_pill).first
                if pill.is_visible(timeout=500):
                    pill.click()
                    self.page.wait_for_timeout(350)
                    break

            if target_mode == "video":
                pw_log("Configurando Google Flow para VÍDEO (Veo 3.1 - Lite, 16:9, x1)...")
                # Aba Video
                btn_vid = self.page.locator('button:has-text("Video"), [role="tab"]:has-text("Video")').first
                if btn_vid.is_visible(timeout=800):
                    btn_vid.click()
                    self.page.wait_for_timeout(250)

                # Aba Ingredients
                btn_ing = self.page.locator('button:has-text("Ingredients"), [role="tab"]:has-text("Ingredients")').first
                if btn_ing.is_visible(timeout=600):
                    btn_ing.click()
                    self.page.wait_for_timeout(250)

                # Aspect ratio 16:9
                btn_ratio = self.page.locator('button:has-text("16:9"), div:has-text("16:9")').first
                if btn_ratio.is_visible(timeout=600):
                    btn_ratio.click()
                    self.page.wait_for_timeout(200)

                # Modelo: Veo 3.1 - Lite
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

                # Multiplicador x1
                btn_x1 = self.page.locator('button:has-text("x1")').first
                if btn_x1.is_visible(timeout=500):
                    btn_x1.click()
                    self.page.wait_for_timeout(150)

            else:
                pw_log("Configurando Google Flow para IMAGEM (Nano Banana Pro, 16:9, x1)...")
                # Aba Image
                btn_img = self.page.locator('button:has-text("Image"), [role="tab"]:has-text("Image")').first
                if btn_img.is_visible(timeout=800):
                    btn_img.click()
                    self.page.wait_for_timeout(250)

                # Aspect ratio 16:9
                btn_ratio = self.page.locator('button:has-text("16:9"), div:has-text("16:9")').first
                if btn_ratio.is_visible(timeout=600):
                    btn_ratio.click()
                    self.page.wait_for_timeout(200)

                # Modelo: Nano Banana Pro
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

                # Multiplicador x1
                btn_x1 = self.page.locator('button:has-text("x1")').first
                if btn_x1.is_visible(timeout=500):
                    btn_x1.click()
                    self.page.wait_for_timeout(150)

            # Fecha o popover pressionando Escape
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)

        except Exception as e:
            pw_log(f"Aviso ao configurar parâmetros no Flow: {e}", level="warn")

    def _clean_prompt_text(self, prompt: str) -> str:
        p = re.sub(r"^\[\d+:\d+\]\s*(\[(IMAGEM|VIDEO|IMAGE)\])?\s*", "", prompt, flags=re.IGNORECASE).strip()
        p = p.replace("@personagem", "").strip()
        return p

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
        tipo_cena = cena.get("tipo", "image")
        video_mode = is_anim or tipo_cena == "video"
        timeout_s = 300 if video_mode else 120

        raw_prompt = cena.get("prompt_animacao") if video_mode else (cena.get("prompt_imagem") or cena.get("texto", ""))
        prompt = self._clean_prompt_text(raw_prompt)

        # Referência do personagem ou da imagem a animar
        tem_persona = scene_plan_svc._cena_tem_personagem(raw_prompt) or scene_plan_svc._cena_tem_personagem(cena.get("texto", ""))
        ref_path = cena.get("personagem_ref", "") if (tem_persona and not is_anim) else (cena.get("arquivo_midia", "") if is_anim else "")

        pw_log(f"Iniciando Cena {cid} (modo={'vídeo' if video_mode else 'imagem'}, timeout={timeout_s}s)...")
        scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_GERANDO)

        # 1. Garante projeto aberto
        if not self._ensure_project_open(timeout_s=30):
            return False, "Não foi possível abrir o projeto no Google Flow."

        target_mode = "video" if video_mode else "image"
        if getattr(self, "current_flow_mode", None) != target_mode:
            self._disable_agent_mode()
            self._set_output_mode(target_mode)
            self.current_flow_mode = target_mode
        else:
            pw_log(f"Modo {target_mode} já configurado. Pulando _set_output_mode e agent check.")

        existing_names = self._get_existing_media_names()
        pw_log(f"Mídias existentes no Flow antes do envio: {len(existing_names)}")

        # 2. Localiza e foca o campo de prompt
        editor = None
        for sel in [
            'div[role="textbox"][data-slate-editor="true"][contenteditable="true"]',
            '[data-slate-editor="true"][contenteditable="true"]',
            'div[role="textbox"][contenteditable="true"]',
            'textarea',
        ]:
            loc = self.page.locator(sel).first
            if loc.is_visible(timeout=1500):
                editor = loc
                break

        if not editor:
            return False, "Campo de prompt do Flow não encontrado na página."

        editor.click()
        self.page.wait_for_timeout(200)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.page.wait_for_timeout(200)

        # 3. Injeção de imagem de referência removida (foco total em geração por prompt)


        # 4. Insere o texto do prompt
        if prompt:
            editor.click()
            self.page.wait_for_timeout(150)
            self.page.keyboard.insert_text(prompt)
            self.page.wait_for_timeout(400)

        # 5. Clica no botão Create / Gerar
        submitted = False
        try:
            for sel_btn in [
                'button:has(i:has-text("arrow_forward"))',
                'button[aria-label="Create"]',
                'button[aria-label*="Create" i]',
                'button[aria-label*="Criar" i]',
                'button:has-text("Create")',
                'button:has-text("Criar")',
            ]:
                btn = self.page.locator(sel_btn).first
                if btn.is_visible(timeout=1000) and not btn.is_disabled():
                    btn.click()
                    submitted = True
                    pw_log(f"Prompt da Cena {cid} enviado via botão.")
                    break
        except Exception:
            pass

        if not submitted:
            editor.focus()
            self.page.keyboard.press("Enter")
            pw_log(f"Prompt da Cena {cid} enviado via tecla Enter.")

        # 6. Aguarda a geração concluir
        start_wait = time.time()
        nova_midia: Optional[Dict[str, Any]] = None

        while time.time() - start_wait < timeout_s:
            if self.stop_requested.is_set():
                return False, "Fila pausada pelo usuário."

            self.page.wait_for_timeout(2000)

            try:
                res_list = self.page.evaluate(JS_FETCH_MEDIA_LIST)
                if res_list.get("ok"):
                    for item in res_list.get("media", []):
                        if item["name"] not in existing_names:
                            nova_midia = item
                            pw_log(f"Nova mídia detectada via Flow API: {item['name']} ({item['kind']})")
                            break
            except Exception:
                pass

            if nova_midia:
                break

        if not nova_midia:
            return False, f"Timeout ({timeout_s}s) aguardando conclusão no Google Flow."

        # 7. Tenta download de alta qualidade 2K (com fallback 1K para imagens)
        if not video_mode:
            try:
                # Tenta acionar o menu de download 2K / 1K na UI do Flow
                cards = self.page.locator('[data-testid="media-card"], div[role="article"], .media-item').all()
                if cards:
                    last_card = cards[-1]
                    last_card.hover()
                    self.page.wait_for_timeout(200)
                    btn_more = last_card.locator('button[aria-label*="more" i], button:has-text("more_vert"), button:has(svg)').first
                    if btn_more.is_visible(timeout=600):
                        btn_more.click()
                        self.page.wait_for_timeout(300)
                        btn_down = self.page.locator('[role="menuitem"]:has-text("Download"), button:has-text("Download")').first
                        if btn_down.is_visible(timeout=600):
                            btn_down.hover()
                            btn_down.click()
                            self.page.wait_for_timeout(300)
                            btn_2k = self.page.locator('[role="menuitem"]:has-text("2K"), button:has-text("2K")').first
                            btn_1k = self.page.locator('[role="menuitem"]:has-text("1K"), button:has-text("1K")').first
                            if btn_2k.is_visible(timeout=600) and "upgrade" not in (btn_2k.text_content() or "").lower():
                                pw_log("Solicitando download na qualidade 2K (Upscaled)...")
                                btn_2k.click()
                            elif btn_1k.is_visible(timeout=600):
                                pw_log("Solicitando download na qualidade 1K (Original size)...")
                                btn_1k.click()
                            self.page.wait_for_timeout(800)
            except Exception as e_dq:
                pw_log(f"Aviso no menu de qualidade 2K/1K: {e_dq}", level="warn")

        # Baixa via TRPC Blob para garantir persistência imediata
        download_url = nova_midia.get("url") or (MEDIA_REDIRECT_URL + nova_midia["name"])
        pw_log(f"Baixando mídia gerada: {download_url}")

        res_blob = self.page.evaluate(JS_FETCH_BLOB_BASE64, download_url)
        if not res_blob.get("ok") or not res_blob.get("base64"):
            return False, f"Falha ao baixar mídia: {res_blob.get('error')}"

        is_video_result = nova_midia.get("kind") == "video" or video_mode
        ext = ".mp4" if is_video_result else ".jpg"
        fname = scene_plan_svc._nome_arquivo_cena(cena, ext)

        dest_dir = PROJETOS_DIR / projeto_id / "midias"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / fname

        # Pasta de vídeos padrão do usuário solicitada (C:\Users\Administrator\Videos\PROJETO)
        user_videos_dir = Path(r"C:\Users\Administrator\Videos\PROJETO")
        user_videos_dir.mkdir(parents=True, exist_ok=True)
        user_dest_path = user_videos_dir / fname

        try:
            raw_b64 = res_blob["base64"].split(",")[1]
            content_bytes = base64.b64decode(raw_b64)
            with open(dest_path, "wb") as f:
                f.write(content_bytes)
            try:
                with open(user_dest_path, "wb") as f_u:
                    f_u.write(content_bytes)
                pw_log(f"Cópia salva em {user_dest_path}")
            except Exception:
                pass
            pw_log(f"Arquivo salvo com sucesso em: {dest_path}")
        except Exception as e_save:
            return False, f"Erro ao salvar arquivo em disco: {e_save}"

        # 8. Atualiza scene_plan.json
        new_status = scene_plan_svc.STATUS_ANIMADA if is_video_result else scene_plan_svc.STATUS_MIDIA_IMPORTADA
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
                    if st in (scene_plan_svc.STATUS_MIDIA_IMPORTADA, scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR, scene_plan_svc.STATUS_PENDENTE):
                        cenas_a_processar.append(c)
                else:
                    if st in (scene_plan_svc.STATUS_PENDENTE, scene_plan_svc.STATUS_ENVIADA, scene_plan_svc.STATUS_PROMPT_PRONTO, scene_plan_svc.STATUS_ERRO):
                        cenas_a_processar.append(c)

            pw_log(f"Processando {len(cenas_a_processar)} cena(s) via Playwright CDP (projeto={projeto_id}).")

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
                    self.cena_ativa = {
                        "scene_id": cid,
                        "status": "ERRO",
                        "mensagem": f"Cena {cid} falhou: {res_msg}",
                        "ts": time.time()
                    }

                time.sleep(1.5)

        except Exception as e_queue:
            pw_log(f"Erro geral no worker CDP: {e_queue}", level="error")
        finally:
            self.is_running_queue = False
            self.cena_ativa = {}
            pw_log(f"Fila finalizada para o projeto '{projeto_id}'.")


# ---------------------------------------------------------------------------
# Singleton do Worker Thread CDP
# ---------------------------------------------------------------------------

_GLOBAL_WORKER_THREAD: Optional[PlaywrightWorkerThread] = None
_GLOBAL_LOCK = threading.Lock()

def _get_worker_thread() -> PlaywrightWorkerThread:
    global _GLOBAL_WORKER_THREAD
    with _GLOBAL_LOCK:
        if _GLOBAL_WORKER_THREAD is None or not _GLOBAL_WORKER_THREAD.is_alive():
            _GLOBAL_WORKER_THREAD = PlaywrightWorkerThread()
            _GLOBAL_WORKER_THREAD.start()
        return _GLOBAL_WORKER_THREAD


# ---------------------------------------------------------------------------
# Interface Pública para o Flask / app_web.py
# ---------------------------------------------------------------------------

class FlowSessionManager:
    @staticmethod
    def is_active() -> bool:
        t = _get_worker_thread()
        ev = threading.Event()
        box = {}
        t.cmd_queue.put({"type": "CHECK_ACTIVE", "reply_event": ev, "result_box": box})
        ev.wait(timeout=4)
        return bool(box.get("active", False))

    @staticmethod
    def start_session() -> Tuple[bool, str]:
        t = _get_worker_thread()
        ev = threading.Event()
        box = {}
        t.cmd_queue.put({"type": "START_SESSION", "reply_event": ev, "result_box": box})
        ev.wait(timeout=30)
        return box.get("ok", False), box.get("msg", "Timeout ao iniciar sessão.")

    @staticmethod
    def close_session():
        t = _get_worker_thread()
        ev = threading.Event()
        box = {}
        t.cmd_queue.put({"type": "CLOSE_SESSION", "reply_event": ev, "result_box": box})
        ev.wait(timeout=10)


class FlowQueueWorker:
    @staticmethod
    def is_running() -> bool:
        t = _get_worker_thread()
        return t.is_running_queue

    @staticmethod
    def get_cena_ativa() -> Dict[str, Any]:
        t = _get_worker_thread()
        return t.cena_ativa

    @staticmethod
    def stop_queue():
        t = _get_worker_thread()
        t.stop_requested.set()
        pw_log("Sinal de parada enviado para o worker CDP.")

    @staticmethod
    def start_worker(projeto_id: str, scene_ids: Optional[List[int]] = None, modo: str = "imagem") -> bool:
        t = _get_worker_thread()
        if t.is_running_queue:
            pw_log("Worker CDP já está em execução.")
            return True

        ev = threading.Event()
        box = {}
        t.cmd_queue.put({
            "type": "RUN_QUEUE",
            "projeto_id": projeto_id,
            "scene_ids": scene_ids,
            "modo": modo,
            "reply_event": ev,
            "result_box": box
        })
        ev.wait(timeout=10)
        return box.get("ok", False)
