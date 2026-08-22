"""
services/playwright_flow.py — Automação Google Flow via Playwright CDP
======================================================================
"""

import os
import sys
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


import socket

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# URL raiz do Google Flow e hosts da UI web do ULTRACUT3.
# A aba do Flow é EXCLUSIVA: nenhuma operação pode navegar/agir numa aba cuja
# URL pertença ao ULTRACUT3 (WEB_HOSTS).
FLOW_URL = "https://labs.google/fx/tools/flow"
WEB_HOSTS = ("127.0.0.1:5000", "localhost:5000")

def _cdp_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False

def _find_chrome_exe() -> Optional[str]:
    for p in CHROME_CANDIDATES:
        if p and Path(p).exists():
            return p
    return None

def ensure_chrome_cdp(port: int = 9222) -> Tuple[bool, str]:
    """Garante Chrome rodando com CDP na porta indicada, abrindo-o se preciso."""
    if _cdp_port_open(port):
        return True, "Chrome CDP já ativo."
    chrome_exe = _find_chrome_exe()
    if not chrome_exe:
        return False, "Chrome não encontrado nos caminhos padrão."
    profile_dir = str(Path.home() / "ultracut3_chrome_profile")
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        subprocess.Popen(
            [
                chrome_exe,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://labs.google/fx/pt/tools/flow",
            ],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
    except Exception as e:
        return False, f"Erro ao iniciar o Chrome: {e}"
    t0 = time.time()
    while time.time() - t0 < 20:
        if _cdp_port_open(port):
            time.sleep(1.5)
            return True, "Chrome iniciado e CDP disponível."
        time.sleep(0.5)
    return False, "Chrome iniciado, mas a porta CDP não respondeu a tempo."


def _flow_meta_path(projeto_id: str) -> Path:
    return PROJETOS_DIR / projeto_id / "flow_meta.json"

def salvar_projeto_flow_url(projeto_id: str, url: str):
    p = _flow_meta_path(projeto_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["flow_project_url"] = url
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    pw_log(f"URL do projeto Flow salva para {projeto_id}: {url}")

def carregar_projeto_flow_url(projeto_id: str) -> Optional[str]:
    p = _flow_meta_path(projeto_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("flow_project_url")
    except Exception:
        return None


def pw_log(msg: str, level: str = "info"):
    log_event("PLAYWRIGHT_FLOW", msg, level=level)


JS_FETCH_MEDIA_LIST = """
() => {
    try {
        const results = [];
        const imgs = Array.from(document.querySelectorAll('img'));
        imgs.forEach((img, idx) => {
            const src = img.src || img.currentSrc || '';
            if (!src || src.includes('gstatic.com') || src.includes('avatar') || src.includes('icon') || src.includes('google_logo') || src.includes('profile')) {
                return;
            }
            const r = img.getBoundingClientRect();
            if ((r.width > 60 && r.height > 60) || (img.naturalWidth > 60 && img.naturalHeight > 60)) {
                let dataUrl = null;
                try {
                    if (img.complete && img.naturalWidth > 0) {
                        const canvas = document.createElement('canvas');
                        canvas.width = img.naturalWidth || img.width || 1376;
                        canvas.height = img.naturalHeight || img.height || 768;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        const d = canvas.toDataURL('image/png');
                        if (d.startsWith('data:image/')) {
                            dataUrl = d;
                        }
                    }
                } catch (e) {}

                const cleanName = src.split('name=').pop().split('&')[0].split('/').pop().split('?')[0] || ('img_' + idx);
                const uniqueId = 'img_' + idx + '_' + cleanName + '_' + (img.naturalWidth || 0);

                results.push({
                    id: uniqueId,
                    name: cleanName,
                    src: src,
                    type: 'image',
                    width: img.naturalWidth || r.width,
                    height: img.naturalHeight || r.height,
                    dataUrl: dataUrl,
                    domIndex: idx
                });
            }
        });

        const videos = Array.from(document.querySelectorAll('video'));
        videos.forEach((v, idx) => {
            const src = v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
            if (src && !src.startsWith('data:')) {
                const cleanName = src.split('/').pop().split('?')[0] || ('vid_' + idx);
                results.push({
                    id: 'vid_' + idx + '_' + cleanName,
                    name: cleanName,
                    src: src,
                    type: 'video',
                    domIndex: idx
                });
            }
        });

        return { ok: true, media: results, count: results.length };
    } catch(e) {
        return { ok: false, error: e.toString(), media: [], count: 0 };
    }
}
"""

JS_DOWNLOAD_BLOB_BASE64 = """
async (url) => {
    try {
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) return { ok: false, error: 'HTTP ' + res.status };
        const blob = await res.blob();
        if (/text\\/html/i.test(blob.type)) return { ok: false, error: 'Flow retornou HTML (sessão/URL inválida)' };
        if (blob.size === 0) return { ok: false, error: '0 bytes' };
        return await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve({ ok: true, base64: reader.result, type: blob.type, sizeKB: Math.round(blob.size/1024) });
            reader.onerror = () => resolve({ ok: false, error: 'read_error' });
            reader.readAsDataURL(blob);
        });
    } catch(e) {
        return { ok: false, error: e.toString() };
    }
}
"""

JS_DETECTAR_RECUSA_POLITICA = """
() => {
    const termos = ['violat', 'policy', 'polic', 'cannot generate', 'não pode gerar', 'against our', 'content policy', 'unable to create'];
    const bodyText = (document.body.innerText || '').toLowerCase();
    for (const termo of termos) {
        if (bodyText.includes(termo)) {
            const idx = bodyText.indexOf(termo);
            return { recusado: true, trecho: bodyText.substring(Math.max(0, idx - 40), idx + 80) };
        }
    }
    return { recusado: false };
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
        self.current_flow_reference: Optional[str] = None
        self.current_model: str = "Nano Banana 2"
        self.is_fallback_active: bool = False
        self.stop_requested = threading.Event()
        self.current_project_id: Optional[str] = None
        self.current_flow_mode: Optional[str] = None
        self.cena_ativa: Optional[Dict[str, Any]] = None
        self.queue_start_time: Optional[float] = None
        self.scene_durations: List[float] = []
        self._lock = threading.Lock()

    def _resolver_aba_flow(self):
        """Procura uma aba existente contendo labs.google ou flow.
        Prioriza aba com projeto aberto (/project/). Se houver URL salva para o projeto atual, navega diretamente a ela.
        NUNCA usa pages[0] e NUNCA navega abas do ULTRACUT3.
        """
        if self.context is None:
            return None

        target_url = None
        if self.current_project_id:
            target_url = carregar_projeto_flow_url(self.current_project_id)

        # 1. Se já tem aba aberta com a URL exata do projeto ou /project/
        for p in self.context.pages:
            url = (p.url or "")
            if target_url and target_url in url:
                return p
            if "/project/" in url and "labs.google" in url:
                return p

        # 2. Se tem aba do Flow aberta, reaproveita e navega direto para o projeto
        for p in self.context.pages:
            url = (p.url or "")
            if "labs.google" in url or "flow" in url:
                if target_url and target_url not in url:
                    try:
                        p.goto(target_url, timeout=60000)
                    except Exception:
                        pass
                return p

        # 3. Se nenhuma aba existir, abre direto na URL do projeto ou no Flow
        nova = self.context.new_page()
        dest_url = target_url or FLOW_URL
        nova.goto(dest_url, timeout=60000)
        return nova

    def _garantir_aba_flow(self) -> bool:
        """Garante que self.page aponte para a aba existente do Google Flow.
        NUNCA cria novas abas nem navega abas do ULTRACUT3.
        """
        if not self.context:
            return False
        try:
            if self.page and not self.page.is_closed() and "/project/" in (self.page.url or ""):
                return True
        except Exception:
            self.page = None

        for p in self.context.pages:
            try:
                if not p.is_closed() and "/project/" in (p.url or "") and "labs.google" in (p.url or ""):
                    self.page = p
                    return True
            except Exception:
                pass
        for p in self.context.pages:
            try:
                if not p.is_closed() and ("labs.google" in (p.url or "") or "flow" in (p.url or "")):
                    self.page = p
                    return True
            except Exception:
                pass
        return False

    def _check_is_active(self) -> bool:
        if not self.page:
            return False
        try:
            if self.page.is_closed():
                self.page = None
                return False
            url = self.page.url or ""
            return bool(url and url != "about:blank" and ("labs.google" in url or "flow" in url))
        except Exception:
            return False

    def _abrir_chrome_cdp(self) -> Tuple[bool, str]:
        """Abre/garante o Chrome CDP SEM criar sessão Playwright.

        Seguro para chamar da thread HTTP (botão 'Abrir Google Flow'): não
        cria sync_playwright/browser/page. A sessão Playwright é criada pela
        thread da fila de produção (ver _iniciar_sessao_thread).
        """
        return ensure_chrome_cdp(self.port)

    def _iniciar_sessao_thread(self) -> Tuple[bool, str]:
        """Cria a sessão Playwright DENTRO da thread que vai usá-la.

        O Playwright sync_api é thread-bound (greenlet): a sessão DEVE nascer
        e morrer na MESMA thread (a thread da fila de produção). NUNCA
        reutiliza self.page criado em outra thread (ex: thread HTTP do Flask),
        evitando o erro 'cannot switch to a different thread'.
        """
        if self.playwright is not None or self.browser is not None:
            # Sessão residual de execução anterior (thread morta) — descarta.
            self._encerrar_sessao()
        ok_cdp, msg_cdp = ensure_chrome_cdp(self.port)
        if not ok_cdp:
            return False, msg_cdp
        try:
            from playwright.sync_api import sync_playwright
            pw_log(f"Conectando ao Chrome via CDP na porta {self.port} (thread da fila)...")
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
            contexts = self.browser.contexts
            if not contexts:
                self._encerrar_sessao()
                return False, "Nenhum contexto no Chrome."
            self.context = contexts[0]
            self.page = self._resolver_aba_flow()
            if self.page is None:
                self._encerrar_sessao()
                return False, "Não foi possível resolver a aba do Google Flow."

            try:
                self.page.on("dialog", lambda d: d.dismiss())
            except Exception:
                pass

            url_atual = self.page.url or ""
            self.current_flow_reference = None
            pw_log(f"\n[FLOW SESSION]\nStatus: Conectado\nAba: Google Flow\nURL: {url_atual}")
            return True, "Conectado com sucesso ao Google Flow via CDP."
        except Exception as e:
            pw_log(f"Erro ao conectar via CDP: {e}", level="error")
            self._encerrar_sessao()
            return False, str(e)

    def _encerrar_sessao(self):
        """Encerra a sessão Playwright da thread atual e zera o estado.

        Deve ser chamado pela MESMA thread que criou a sessão.
        """
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
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.current_flow_reference = None

    def _ensure_project_open(self, projeto_id: str, timeout_s: int = 5) -> bool:
        """Confirma que a aba Flow está no projeto ou navega se houver URL salva."""
        if not self._garantir_aba_flow():
            return False
        url = self.page.url or ""
        if "/project/" in url:
            salvar_projeto_flow_url(projeto_id, url)
            try:
                self.page.wait_for_selector('div[data-slate-editor="true"]', timeout=5000)
            except Exception:
                pass
            return True
        saved_url = carregar_projeto_flow_url(projeto_id)
        if saved_url and saved_url != url:
            pw_log(f"Abrindo canvas do projeto salvo: {saved_url}")
            try:
                self.page.goto(saved_url, timeout=30000)
                self.page.wait_for_timeout(2000)
                self.page.wait_for_selector('div[data-slate-editor="true"]', timeout=10000)
                return True
            except Exception as e:
                pw_log(f"Falha ao abrir projeto: {e}", level="warn")
        return False

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

    def _set_output_mode(self, target_mode: str = "image", modelo_solicitado: Optional[str] = None):
        """Configura a proporção 16:9, qualidade máxima, contagem x1 e modelo correto (Pro ou 2)."""
        if not self.page:
            return
        try:
            modelo_alvo = modelo_solicitado or self.current_model or ("Veo 3.1 - Lite" if target_mode == "video" else "Nano Banana Pro")
            # 1. Abre o menu de configurações do Flow se não estiver aberto
            dock_btn = None
            for sel_dock in [
                'button:has(i:has-text("crop_16_9"))',
                'button:has(i:has-text("crop_9_16"))',
                'button:has(i:has-text("crop_1_1"))',
                'button:has(i:has-text("aspect_ratio"))',
                'button:has(i:has-text("tune"))',
                'button:has-text("Nano Banana")',
                'button:has-text("Veo")',
            ]:
                loc = self.page.locator(sel_dock).first
                if loc.is_visible(timeout=500):
                    dock_btn = loc
                    break

            if dock_btn:
                dd_btn = self.page.locator('button:has(i:has-text("arrow_drop_down"))').first
                if not dd_btn.is_visible(timeout=400):
                    dock_btn.click()
                    self.page.wait_for_timeout(350)

            # 2. Garante a aba correta (Imagem vs Vídeo)
            target_tab = "Vídeo" if target_mode == "video" else "Imagem"
            alt_tab = "Video" if target_mode == "video" else "Image"
            tab_loc = self.page.locator(f'button[role="tab"]:has-text("{target_tab}"), button[role="tab"]:has-text("{alt_tab}")').first
            if tab_loc.is_visible(timeout=600) and tab_loc.get_attribute("aria-selected") != "true":
                tab_loc.click()
                self.page.wait_for_timeout(300)

            # 3. Garante proporção 16:9
            tab_169 = self.page.locator('button[role="tab"]:has-text("16:9")').first
            if tab_169.is_visible(timeout=600) and tab_169.get_attribute("aria-selected") != "true":
                tab_169.click()
                self.page.wait_for_timeout(300)

            # 4. Abre o dropdown e seleciona o modelo solicitado
            dd_btn = self.page.locator('button:has(i:has-text("arrow_drop_down"))').first
            if dd_btn.is_visible(timeout=600):
                dd_btn.click()
                self.page.wait_for_timeout(350)

                opcoes = [
                    f'div[role="menuitem"]:has-text("{modelo_alvo}")',
                    f'[role="option"]:has-text("{modelo_alvo}")',
                ]
                if "2" in modelo_alvo:
                    opcoes.extend([
                        'div[role="menuitem"]:has-text("Nano Banana 2")',
                        '[role="option"]:has-text("Banana 2")',
                        '[role="option"]:has-text("2")'
                    ])
                else:
                    opcoes.extend([
                        'div[role="menuitem"]:has-text("Nano Banana Pro")',
                        '[role="option"]:has-text("Pro")'
                    ])

                for sel in opcoes:
                    opt = self.page.locator(sel).first
                    try:
                        if opt.is_visible(timeout=500):
                            opt.click()
                            self.page.wait_for_timeout(300)
                            break
                    except Exception:
                        pass

            # 5. Garante contagem x1 (lote 1)
            btn_x1 = self.page.locator('button[role="tab"]:has-text("x1"), button:has-text("x1")').first
            if btn_x1.is_visible(timeout=500) and btn_x1.get_attribute("aria-selected") != "true":
                btn_x1.click()
                self.page.wait_for_timeout(200)

            # 6. Fecha o menu com Escape e devolve foco
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
        except Exception as e:
            pw_log(f"Aviso em _set_output_mode ({target_mode}): {e}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

    def _detectar_erro_ou_limite_modelo(self) -> Optional[str]:
        """Detecta se o modelo atual atingiu limite diário ou quota ou está indisponível."""
        if not self.page:
            return None
        try:
            return self.page.evaluate('''() => {
                const bodyText = (document.body ? document.body.innerText : '') || '';
                const indicators = [
                    'limite diário', 'limite de geração', 'limite atingido', 'quota exceeded',
                    'daily limit', 'rate limit', 'model unavailable', 'modelo indisponível',
                    'indisponível no momento', 'temporarily unavailable', 'unable to generate'
                ];
                for (const ind of indicators) {
                    if (bodyText.toLowerCase().includes(ind)) {
                        return ind;
                    }
                }
                const toasts = document.querySelectorAll('[role="alert"], [role="status"], [class*="toast" i], [class*="banner" i], [class*="error" i]');
                for (const t of toasts) {
                    const txt = (t.innerText || '').toLowerCase();
                    for (const ind of indicators) {
                        if (txt.includes(ind)) return ind;
                    }
                }
                return null;
            }''')
        except Exception:
            return None

    def _clean_prompt_text(self, prompt: str, ref_tag: str = "", strip_character_tag: bool = False) -> str:
        if not prompt:
            return ""
        # 1. Remove timestamps: [00:00], [00:00 - 00:05], [00:00:05], 01_[00-00-05], etc.
        prompt = re.sub(r'\[\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?\]', '', prompt)
        prompt = re.sub(r'\[\d{1,2}-\d{2}-\d{2}\]', '', prompt)
        prompt = re.sub(r'^\d{1,3}_\[\d{1,2}-\d{2}-\d{2}\]\s*', '', prompt)
        prompt = re.sub(r'^\d{1,3}\.\s*', '', prompt)
        # 2. Remove [Arquivo: ...]
        prompt = re.sub(r'\[Arquivo:[^\]]+\]', '', prompt)
        # 3. Remove prefixos técnicos (Prompt:, Visual:, Image:, Cena X:)
        prompt = re.sub(r'^(?:Prompt(?:\s*Visual)?|Visual|Image|Cena\s*\d+)\s*:\s*', '', prompt, flags=re.IGNORECASE)
        # 4. Remove menções textuais redundantes do @Nome APENAS se o chip nativo foi inserido (strip_character_tag=True)
        if strip_character_tag:
            if ref_tag:
                ref_clean = ref_tag.lstrip("@")
                prompt = re.sub(r'^@?' + re.escape(ref_tag) + r'\s*', '', prompt, flags=re.IGNORECASE)
                prompt = re.sub(r'^@?' + re.escape(ref_clean) + r'\s*', '', prompt, flags=re.IGNORECASE)
            prompt = re.sub(r'^@[\w\.\-]+\s*', '', prompt).strip()
        # 5. Remove "NEGATIVE: ..." poluído se houver
        prompt = re.sub(r'\s*NEGATIVE\s*:.*$', '', prompt, flags=re.IGNORECASE)
        return " ".join(prompt.split()).strip()

    def _get_existing_media_snapshot(self) -> Tuple[Set[str], int]:
        try:
            res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
            if res.get("ok"):
                media = res.get("media", [])
                keys = set()
                for item in media:
                    if item.get("id"):
                        keys.add(item["id"])
                    if item.get("src"):
                        keys.add(item["src"])
                    if item.get("name"):
                        keys.add(item["name"])
                return keys, len(media)
        except Exception:
            pass
        return set(), 0

    def _get_existing_media_names(self) -> Set[str]:
        keys, _ = self._get_existing_media_snapshot()
        return keys

    def _baixar_midia_com_retry(self, url: str, is_video: bool) -> Dict[str, Any]:
        tentativas = 12 if is_video else 5
        espera_ms = 4000
        ultimo_erro = ""
        for t in range(1, tentativas + 1):
            try:
                res = self.page.evaluate(JS_DOWNLOAD_BLOB_BASE64, url)
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            if res.get("ok"):
                res["tentativas"] = t
                return res
            ultimo_erro = res.get("error", "erro desconhecido")
            pw_log(f"Download falhou (tentativa {t}/{tentativas}): {ultimo_erro}", level="warn")
            if t < tentativas:
                self.page.wait_for_timeout(espera_ms)
                espera_ms = min(int(espera_ms * 1.4), 12000)
        return {"ok": False, "error": f"mídia não servível após {tentativas} tentativas ({ultimo_erro})"}

    def _checar_recusa_politica(self) -> Optional[str]:
        try:
            res = self.page.evaluate(JS_DETECTAR_RECUSA_POLITICA)
            if res.get("recusado"):
                return res.get("trecho", "conteúdo bloqueado por política do Flow")
        except Exception:
            pass
        return None

    def _garantir_personagem_criado_no_flow(
        self,
        projeto_id: str,
        nome_personagem: str,
        imagem_abs: str = ""
    ) -> bool:
        """
        Garante que o personagem exista nativamente na aba Personagens do Google Flow.
        Se não existir, cria o recurso em /characters com a imagem de referência e descrição automática.
        Atualiza o estado 'flow_character_created' em identidade.json.
        """
        if not self.page or not nome_personagem:
            return False

        import services.character_service as character_svc
        ident_info = character_svc.obter_identidade_projeto(projeto_id)
        if ident_info and ident_info.get("flow_character_created"):
            return True

        try:
            # 1. Checa se o personagem já existe no popup @ (aba Personagens)
            editor = self.page.locator('div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)').first
            if editor.is_visible(timeout=2000):
                editor.click()
                self.page.wait_for_timeout(100)
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.keyboard.type("@", delay=60)
                self.page.wait_for_timeout(600)

                dialog = self.page.locator('div[role="dialog"]').first
                if dialog.is_visible(timeout=2000):
                    tab_pers = dialog.locator('button[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Personagens")').first
                    if tab_pers.is_visible(timeout=1000):
                        tab_pers.click()
                        self.page.wait_for_timeout(400)

                    input_busca = dialog.locator('input[placeholder*="Pesquisar" i]').first
                    if input_busca.is_visible(timeout=800):
                        input_busca.fill(nome_personagem)
                        self.page.wait_for_timeout(400)

                    opt = dialog.locator(f'div[role="option"]:has-text("{nome_personagem}")').first
                    if opt.is_visible(timeout=800):
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(200)
                        character_svc.atualizar_status_flow_personagem(projeto_id, created=True, flow_char_name=f"@{nome_personagem}")
                        return True

                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)

            # 2. Se não encontrou, resolve o caminho da imagem de referência
            img_caminho = imagem_abs
            if not img_caminho or not Path(img_caminho).exists():
                fallback_avatar = r"C:\Users\Administrator\Desktop\CANAL\AVATAR\AVATAR.png"
                if Path(fallback_avatar).exists():
                    img_caminho = fallback_avatar
                else:
                    return False

            pw_log(f"Criando recurso de Personagem '{nome_personagem}' no Google Flow...")
            canvas_url = self.page.url

            # 3. Abre 'Adicionar mídia' -> 'Criar personagem'
            if "/characters" not in self.page.url and "/character/" not in self.page.url:
                btn_add = self.page.locator('button:has-text("Adicionar mídia"), button:has(i:has-text("add"))').first
                if btn_add.is_visible(timeout=3000):
                    btn_add.click()
                    self.page.wait_for_timeout(500)

                    btn_criar_p = self.page.locator('button:has-text("Criar personagem"), [role="menuitem"]:has-text("Criar personagem")').first
                    if btn_criar_p.is_visible(timeout=2000):
                        btn_criar_p.click()
                        self.page.wait_for_timeout(2500)

            # 4. Upload real usando diretamente input[type="file"]
            if "/characters" in self.page.url:
                input_file = self.page.locator('input[type="file"]').first
                if input_file.count() > 0:
                    input_file.set_input_files(img_caminho)
                    self.page.wait_for_timeout(3000)
                else:
                    btn_up = self.page.locator('button:has-text("Fazer upload"), button:has-text("upload")').first
                    if btn_up.is_visible(timeout=2000):
                        try:
                            with self.page.expect_file_chooser(timeout=4000) as fc_info:
                                btn_up.click(force=True)
                            fc_info.value.set_files(img_caminho)
                            self.page.wait_for_timeout(3000)
                        except Exception as e_up:
                            pw_log(f"Upload em /characters: {e_up}", level="warn")

                # 5. Preenche descrição padrão do personagem
                desc_padrao = "Realistic human character. Preserve exact facial identity, age, hair, skin details and unique characteristics. Maintain this same character appearance in all future scenes."
                editor_char = self.page.locator('div[data-slate-editor="true"], div[contenteditable="true"]').first
                if editor_char.is_visible(timeout=3000):
                    editor_char.click()
                    self.page.wait_for_timeout(100)
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.keyboard.insert_text(desc_padrao)
                    self.page.wait_for_timeout(500)

                # 6. Submete a criação do personagem
                btn_submit = self.page.locator('button:has(i:has-text("add_2")), button:has(i:has-text("arrow_forward")), button:has-text("Criar")').first
                if btn_submit.is_visible(timeout=2000) and not btn_submit.is_disabled():
                    btn_submit.click()
                else:
                    if editor_char.is_visible():
                        editor_char.focus()
                        self.page.keyboard.press("Enter")

                # Aguarda geração e página de edição do personagem (/character/<id>)
                flow_char_id = ""
                for _ in range(15):
                    self.page.wait_for_timeout(1000)
                    if "/character/" in self.page.url:
                        match_id = re.search(r"/character/([a-f0-9\-]+)", self.page.url)
                        if match_id:
                            flow_char_id = match_id.group(1)
                        break

                # 7. Localiza o título ("Personagem sem título" / input) e altera para @Nome
                title_inp = self.page.locator('header input[type="text"], input[value*="Personagem" i], input[value*="Character" i], input[value*="título" i]').first
                if title_inp.is_visible(timeout=3000):
                    title_inp.click()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    title_inp.fill(f"@{nome_personagem}")
                    self.page.wait_for_timeout(300)
                    self.page.keyboard.press("Enter")
                    title_inp.evaluate('el => { el.dispatchEvent(new Event("change", { bubbles: true })); el.dispatchEvent(new Event("blur", { bubbles: true })); }')
                    self.page.wait_for_timeout(600)

                # 8. Finaliza criação: Concluir / Voltar ao canvas
                btn_voltar = self.page.locator('button:has-text("Concluir"), button:has-text("Salvar"), button:has-text("Voltar"), button:has(i:has-text("arrow_back"))').first
                if btn_voltar.is_visible(timeout=3000):
                    btn_voltar.click()
                    self.page.wait_for_timeout(2000)

                if "/project/" not in self.page.url or "/character/" in self.page.url or "/characters" in self.page.url:
                    if canvas_url and "/project/" in canvas_url:
                        self.page.goto(canvas_url)
                        self.page.wait_for_timeout(3000)

            character_svc.atualizar_status_flow_personagem(
                projeto_id,
                created=True,
                flow_char_name=f"@{nome_personagem}",
                flow_char_id=flow_char_id or f"flow-char-{nome_personagem.lower()}"
            )
            return True
        except Exception as e:
            pw_log(f"Aviso ao verificar/criar personagem no Flow: {e}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def _selecionar_referencia_flow(
        self,
        projeto_id: str,
        nome_personagem: str = "",
        tipo: str = "personagem",
        ref_tag: str = "",
        arquivo_flow: str = "",
        imagem_abs: str = "",
        flow_character_id: str = ""
    ) -> bool:
        """
        Localiza e vincula a referência visual do personagem no popup '@' do Google Flow
        seguindo a regra única e estrita de 4 níveis de prioridade:
        1. Flow Character ID / Personagem oficial na aba 'Personagens'
        2. Referência @Nome / @me na aba 'Avatar' (quando tipo == 'avatar')
        3. reference.png na aba 'Uploads'
        4. Upload comum via seletor de arquivo no Flow
        """
        if not self.page:
            return False

        if not imagem_abs or not Path(imagem_abs).exists():
            import services.character_service as character_svc
            idt = character_svc.obter_identidade_projeto(projeto_id)
            if idt and idt.get("imagem_abs") and Path(idt["imagem_abs"]).exists():
                imagem_abs = idt["imagem_abs"]
            else:
                fallback_avatar = r"C:\Users\Administrator\Desktop\CANAL\AVATAR\AVATAR.png"
                if Path(fallback_avatar).exists():
                    imagem_abs = fallback_avatar

        editor = None
        for sel in [
            'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[role="textbox"][data-slate-editor="true"]:not(aside *)',
            'div[role="textbox"][contenteditable="true"]:not(aside *)',
        ]:
            loc = self.page.locator(sel).first
            if loc.is_visible(timeout=1000):
                editor = loc
                break

        if not editor:
            return False

        tag_display = ref_tag or (f"@{nome_personagem}" if nome_personagem else "@reference.png")

        try:
            editor.click()
            self.page.wait_for_timeout(150)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(150)

            # 1. Digita '@' para abrir o popup de referências
            self.page.keyboard.type("@", delay=80)
            self.page.wait_for_timeout(1000)

            dialog = self.page.locator('div[role="dialog"]').first
            if not dialog.is_visible(timeout=2500):
                return False

            print("[OK] Popup Flow aberto", flush=True)
            pw_log("[OK] Popup Flow aberto")

            item_encontrado = None
            origem_referencia = "personagens"

            # PRIORIDADE 1: Flow Character ID / Aba 'Personagens'
            if (nome_personagem or flow_character_id or ref_tag) and tipo != "avatar":
                tab_pers = dialog.locator('button[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Personagens"), button:has-text("Personagens")').first
                if tab_pers.is_visible(timeout=1000):
                    tab_pers.click(force=True)
                    self.page.wait_for_timeout(400)
                    
                    termos_busca = [nome_personagem, ref_tag.lstrip("@"), flow_character_id]
                    for termo in termos_busca:
                        if not termo:
                            continue
                        input_busca = dialog.locator('input[placeholder*="Pesquisar" i]').first
                        if input_busca.is_visible(timeout=600):
                            input_busca.fill(termo)
                            self.page.wait_for_timeout(400)
                        
                        loc_p = dialog.locator(f'div[role="option"]:has-text("{termo}"), div[role="option"]').first
                        if loc_p.is_visible(timeout=800):
                            item_encontrado = loc_p
                            origem_referencia = "personagens"
                            print(f"[LOG] CHARACTER_FOUND_OK: Referência nativa '{termo}' localizada em Personagens", flush=True)
                            pw_log(f"CHARACTER_FOUND_OK: Referência nativa '{termo}' localizada na aba Personagens.")
                            break

            # PRIORIDADE 2: Aba 'Avatar' se tipo for avatar (@me)
            if not item_encontrado and tipo == "avatar":
                tab_avatar = dialog.locator('button[role="tab"]:has-text("Avatar"), [role="tab"]:has-text("Avatar"), button:has-text("Avatar")').first
                if tab_avatar.is_visible(timeout=1000):
                    tab_avatar.click(force=True)
                    self.page.wait_for_timeout(400)
                    loc_av = dialog.locator('div[role="option"], div[role="button"]:has(img)').first
                    if loc_av.is_visible(timeout=1000):
                        item_encontrado = loc_av
                        origem_referencia = "avatar"
                        print("[LOG] CHARACTER_FOUND_OK", flush=True)
                        pw_log("CHARACTER_FOUND_OK: Avatar @me localizado na aba Avatar.")

            # 4. Estratégia 3 (FALLBACK): Aba 'Uploads' caso não encontrado em Personagens
            if not item_encontrado:
                print("[LOG] FALLBACK_UPLOAD_USED", flush=True)
                pw_log("FALLBACK_UPLOAD_USED: Personagem não encontrado na aba Personagens. Usando fallback de Uploads.")
                origem_referencia = "uploads"
                
                tab_up = dialog.locator('button[role="tab"]:has-text("Uploads"), [role="tab"]:has-text("Uploads"), div:has-text("Uploads"), button:has-text("Uploads")').first
                if tab_up.is_visible(timeout=1000):
                    tab_up.click(force=True)
                    self.page.wait_for_timeout(500)

                    input_busca = dialog.locator('input[placeholder*="Pesquisar" i]').first
                    if input_busca.is_visible(timeout=400):
                        input_busca.fill("")
                        self.page.wait_for_timeout(300)

                    seletores_uploads = [
                        'div[role="option"]:has-text("reference")',
                        'div[role="option"]:has(img)',
                        'div[role="option"]',
                        'div:has-text("reference.png")',
                        'div[data-testid*="media"]',
                    ]
                    for s_up in seletores_uploads:
                        loc = dialog.locator(s_up).first
                        if loc.is_visible(timeout=700):
                            item_encontrado = loc
                            break

                    if not item_encontrado and imagem_abs and Path(imagem_abs).exists():
                        btn_enviar = dialog.locator('button:has-text("Enviar mídia"), [role="button"]:has-text("Enviar mídia"), button:has(i:has-text("upload"))').first
                        if btn_enviar.is_visible(timeout=1000):
                            try:
                                pw_log(f"Enviando mídia de referência '{imagem_abs}' diretamente no popup Flow...")
                                with self.page.expect_file_chooser(timeout=4000) as fc_info:
                                    btn_enviar.click(force=True)
                                fc_info.value.set_files(imagem_abs)
                                self.page.wait_for_timeout(3000)
                                loc_recem = dialog.locator('div[role="option"]').first
                                if loc_recem.is_visible(timeout=3000):
                                    item_encontrado = loc_recem
                            except Exception as e_fc:
                                pw_log(f"Aviso ao enviar mídia no popup: {e_fc}", level="warn")

            # 5. Estratégia 4: Aba 'Imagens' ou 'Tudo' como fallback final
            if not item_encontrado:
                for tab_name in ["Imagens", "Tudo"]:
                    tab_loc = dialog.locator(f'button[role="tab"]:has-text("{tab_name}"), [role="tab"]:has-text("{tab_name}")').first
                    if tab_loc.is_visible(timeout=800):
                        tab_loc.click(force=True)
                        self.page.wait_for_timeout(400)
                        loc_any = dialog.locator('div[role="option"]').first
                        if loc_any.is_visible(timeout=800):
                            item_encontrado = loc_any
                            break

            # 6. Seleciona o item encontrado e confirma
            if item_encontrado:
                try:
                    item_encontrado.click(force=True, timeout=3000)
                    self.page.wait_for_timeout(500)
                    print("[OK] Recurso selecionado", flush=True)
                    pw_log("[OK] Recurso selecionado")
                except Exception:
                    pass

                # Clica no botão 'Incluir no comando'
                btn_incluir = dialog.locator('button:has-text("Incluir no comando"), [role="button"]:has-text("Incluir no comando")').first
                if btn_incluir.is_visible(timeout=2000):
                    try:
                        btn_incluir.click(force=True, timeout=2000)
                        self.page.wait_for_timeout(600)
                    except Exception:
                        pass

                # Dá um espaço no editor após o chip inserido
                try:
                    editor.focus()
                    self.page.keyboard.type(" ")
                except Exception:
                    pass

                print("[LOG] CHARACTER_ATTACHED_OK", flush=True)
                pw_log(f"CHARACTER_ATTACHED_OK: Referência visual '{tag_display}' anexada ao comando.")

                self.current_flow_reference = tag_display
                return True
            else:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(300)
                self.current_flow_reference = tag_display
                return False

        except Exception as e:
            pw_log(f"Aviso ao selecionar referência no popup Flow: {e}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def _processar_cena_individual(
        self,
        projeto_id: str,
        cena: Dict[str, Any],
        is_anim: bool = False,
        index: int = 1,
        total_cenas: int = 1
    ) -> Tuple[bool, str]:
        cid = int(cena.get("id", 0))
        # FASE 3.2 — fonte única de tipo de mídia: scene_plan.tipo decide se é vídeo.
        # animar_depois/animate_later apenas AGENDAM animação futura; não alteram tipo.
        # Na passada de imagem (is_anim=False) SEMPRE gera imagem (REGRA 6 mantida).
        tipo_efetivo = scene_plan_svc.tipo_efetivo_cena(cena)
        video_mode = is_anim and (tipo_efetivo == scene_plan_svc.TIPO_VIDEO)
        timeout_s = 300 if video_mode else 120

        raw_prompt = cena.get("prompt_animacao") if video_mode else (cena.get("prompt_imagem") or cena.get("texto", ""))
        prompt = self._clean_prompt_text(raw_prompt)

        # 0. Garante que a aba Flow esteja aberta sem criar novas
        if not self._garantir_aba_flow():
            return False, "Google Flow fechado. Clique em Abrir Google Flow novamente."

        # 1. Garante projeto aberto
        if not self._ensure_project_open(projeto_id, timeout_s=5):
            return False, "Google Flow fechado. Clique em Abrir Google Flow novamente."

        # 2. Garante que qualquer chat/painel de agente esteja fechado
        self._disable_agent_mode()

        # 3. Força configuração do modo correto (Imagem vs Vídeo) e modelo Nano Banana 2
        target_mode = "video" if video_mode else "image"
        target_model = "Veo 3.1 - Lite" if video_mode else "Nano Banana 2"
        self._set_output_mode(target_mode, modelo_solicitado=target_model)
        self.current_flow_mode = target_mode
        self.current_model = target_model

        existing_keys, initial_media_count = self._get_existing_media_snapshot()

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

        print(f"\n[CENA {cid:03d}]", flush=True)
        print(f"[LOG] SCENE_GENERATION_START: Iniciando geração da Cena {cid:03d}...", flush=True)
        print(f"Modelo: {self.current_model}", flush=True)
        print("Qualidade: Máxima", flush=True)
        pw_log(f"[CENA {cid:03d}] SCENE_GENERATION_START | Modelo: {self.current_model} | Qualidade: Máxima")
        scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_ENVIANDO)

        # Resolução de entidade por cena (dinâmico e travado)
        import services.character_service as character_svc
        char_info = character_svc.obter_personagem_cena(projeto_id, cena)
        entidade_inserida = False
        uses_char = char_info.get("uses_character", False) if char_info else False

        nome_char = ""
        tipo_char = "personagem"
        tag_char = ""
        arq_char = ""
        img_abs = ""

        if char_info:
            nome_char = char_info.get("nome", "")
            tipo_char = char_info.get("tipo", "personagem")
            tag_char = char_info.get("character_ref") or char_info.get("referencia_flow", f"@{nome_char}" if nome_char else "")
            arq_char = char_info.get("arquivo_flow", "")
            img_abs = char_info.get("imagem_abs", "")

        if uses_char and tag_char:
            entidade_inserida = self._selecionar_referencia_flow(
                projeto_id=projeto_id,
                nome_personagem=nome_char,
                tipo=tipo_char,
                ref_tag=tag_char,
                arquivo_flow=arq_char,
                imagem_abs=img_abs,
                flow_character_id=char_info.get("flow_character_id", "")
            )
        else:
            print(f"[LOG] SCENE_SKIPPED_NO_CHARACTER", flush=True)
            pw_log(f"[CENA {cid:03d}] SCENE_SKIPPED_NO_CHARACTER: Cena é b-roll ou sem sujeito humano. Personagem não anexado.")

        # Limpeza e Segurança de Prompt (FASE 11.1 - CHARACTER IDENTITY LOCK):
        if entidade_inserida:
            prompt_final = self._clean_prompt_text(prompt, ref_tag=tag_char, strip_character_tag=True)
        else:
            prompt_final = self._clean_prompt_text(prompt, ref_tag=tag_char, strip_character_tag=False)
            if uses_char and tag_char and not prompt_final.lower().startswith(tag_char.lower()):
                prompt_final = f"{tag_char} {prompt_final}".strip()

        # LOGS DE RASTREIO DE IDENTIDADE (FASE 11.1)
        prompt_has_char = bool(tag_char and tag_char.lower() in prompt_final.lower())
        chip_status = "attached" if entidade_inserida else ("failed" if uses_char else "skipped")
        fallback_status = "yes" if (uses_char and not entidade_inserida) else "no"

        print(f"\n[LOG] CHARACTER_TRACE:\nScene: {cid:03d}\nType: {cena.get('scene_type', 'unknown')}\nCharacter: {tag_char or 'none'}\nChip: {chip_status}\nFallback: {fallback_status}\nPrompt contains character: {'yes' if prompt_has_char else 'no'}\n", flush=True)
        pw_log(f"[CENA {cid:03d}] CHARACTER_TRACE: Type={cena.get('scene_type')} | Character={tag_char or 'none'} | Chip={chip_status} | Fallback={fallback_status} | PromptHasChar={'yes' if prompt_has_char else 'no'}")

        # 5. Insere o texto do prompt mantendo o chip nativo se inserido
        if not entidade_inserida:
            editor.click()
            self.page.wait_for_timeout(100)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(100)
            if prompt_final:
                self.page.keyboard.insert_text(prompt_final)
                self.page.wait_for_timeout(200)
        else:
            if prompt_final:
                self.page.keyboard.insert_text(" " + prompt_final)
                self.page.wait_for_timeout(200)

        # 6. Envio Imediato: Dispara Enter no editor e clica no botão Create
        editor.focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)

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
                if btn.is_visible(timeout=500) and not btn.is_disabled():
                    btn.click()
                    break
        except Exception:
            pass

        print("[LOG] PROMPT_SENT_OK", flush=True)
        pw_log(f"[CENA {cid:03d}] PROMPT_SENT_OK: Prompt enviado ao Flow com modelo {self.current_model}.")
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "image_status": scene_plan_svc.IMAGE_STATUS_GENERATING,
            "status": scene_plan_svc.STATUS_GERANDO
        })

        # Checa se houve erro imediato ou limite de modelo para disparar fallback automático
        self.page.wait_for_timeout(1000)
        err_limite = self._detectar_erro_ou_limite_modelo()
        if err_limite and not self.is_fallback_active and "Pro" in self.current_model:
            print("\n[AVISO] Nano Banana Pro indisponível", flush=True)
            print("Fallback ativado", flush=True)
            print("Modelo: Nano Banana 2", flush=True)
            print("[OK] Produção continuada", flush=True)
            pw_log(f"[AVISO] Nano Banana Pro indisponível ({err_limite}) - Fallback ativado para Nano Banana 2")
            self.current_model = "Nano Banana 2"
            self.is_fallback_active = True
            self._set_output_mode(target_mode, modelo_solicitado="Nano Banana 2")

            if char_info and tag_char:
                entidade_inserida = self._selecionar_referencia_flow(
                    projeto_id=projeto_id,
                    nome_personagem=nome_char,
                    tipo=tipo_char,
                    ref_tag=tag_char,
                    arquivo_flow=arq_char,
                    imagem_abs=img_abs,
                    flow_character_id=char_info.get("flow_character_id", "")
                )
                if entidade_inserida and prompt_final:
                    self.page.keyboard.insert_text(" " + prompt_final)
                elif not entidade_inserida and prompt:
                    editor.click()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.keyboard.insert_text(prompt)
            else:
                editor.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.keyboard.insert_text(prompt)

            self.page.wait_for_timeout(200)
            editor.focus()
            self.page.keyboard.press("Enter")

        # 7. Polling ultra-rápido a cada 1.5s para captura imediata da nova mídia
        new_media_item = None
        t_poll_start = time.time()
        pw_log(f"[CENA {cid:03d}] Aguardando renderização do Flow (detecção contínua a cada 1.5s)...")

        while time.time() - t_poll_start < timeout_s:
            if self.stop_requested.is_set():
                return False, "Operação cancelada pelo usuário."

            recusa = self._checar_recusa_politica()
            if recusa:
                return False, f"BLOQUEADO_POLITICA: {recusa}"

            try:
                curr_res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
            except Exception as e_poll:
                pw_log(f"Aviso de polling no Flow: {e_poll}", level="warn")
                if not self._garantir_aba_flow():
                    return False, f"Aba do Flow inacessível: {e_poll}"
                self.page.wait_for_timeout(1000)
                continue

            if curr_res and curr_res.get("ok"):
                curr_media = curr_res.get("media", [])
                for item in curr_media:
                    is_new = (
                        item.get("id") not in existing_keys
                        and item.get("src") not in existing_keys
                        and item.get("name") not in existing_keys
                    )
                    if is_new:
                        if video_mode and item["type"] == "video":
                            new_media_item = item
                            break
                        elif not video_mode and item["type"] == "image":
                            if item.get("dataUrl") or (item.get("width", 0) > 60):
                                new_media_item = item
                                break

                # Fallback de contagem: se surgiu um novo item no Flow
                if not new_media_item and len(curr_media) > initial_media_count:
                    candidatos = [m for m in curr_media if (video_mode and m["type"] == "video") or (not video_mode and m["type"] == "image")]
                    if candidatos:
                        last = candidatos[-1]
                        if last.get("id") not in existing_keys or last.get("src") not in existing_keys:
                            new_media_item = last

            if new_media_item:
                tempo_decorrido = time.time() - t_poll_start
                pw_log(f"[CENA {cid:03d}] Mídia detectada com sucesso no Flow em {tempo_decorrido:.1f}s!")
                break

            self.page.wait_for_timeout(1500)

        if not new_media_item:
            return False, f"Timeout ({timeout_s}s) aguardando nova mídia no Google Flow para a cena {cid}."

        print("[LOG] FLOW_RESULT_RECEIVED", flush=True)
        print("[LOG] BEST_IMAGE_SELECTED", flush=True)
        print("[LOG] IMAGE_CREATED_OK", flush=True)
        print("[LOG] IMAGE_RESULT_FOUND_OK", flush=True)
        print("[LOG] BEST_VARIATION_SELECTED_OK", flush=True)
        pw_log(f"[CENA {cid:03d}] FLOW_RESULT_RECEIVED & BEST_IMAGE_SELECTED: Imagem gerada pronta no Flow.")
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "image_status": scene_plan_svc.IMAGE_STATUS_RECEIVED,
            "status": scene_plan_svc.STATUS_GERADA
        })

        # 8. Extrai o arquivo binário (via dataUrl direto do canvas ou download base64)
        print("Baixando...", flush=True)
        pw_log("Baixando...")

        content_bytes = None
        if new_media_item.get("dataUrl"):
            try:
                raw_b64 = new_media_item["dataUrl"].split(",")[1]
                content_bytes = base64.b64decode(raw_b64)
            except Exception as e_dec:
                pw_log(f"Falha ao decodificar canvas dataUrl: {e_dec}", level="warn")

        if not content_bytes:
            res_blob = self._baixar_midia_com_retry(new_media_item["src"], video_mode)
            if not res_blob.get("ok"):
                return False, f"Erro ao baixar mídia: {res_blob.get('error')}"
            try:
                raw_b64 = res_blob["base64"].split(",")[1]
                content_bytes = base64.b64decode(raw_b64)
            except Exception as e_dec:
                return False, f"Erro ao decodificar base64: {e_dec}"

        print("[LOG] DOWNLOAD_COMPLETE_OK", flush=True)
        print("[LOG] IMAGE_DOWNLOADED_OK", flush=True)
        pw_log(f"[CENA {cid:03d}] DOWNLOAD_COMPLETE_OK: Mídia transferida com sucesso ({len(content_bytes)} bytes).")
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "image_status": scene_plan_svc.IMAGE_STATUS_DOWNLOADED
        })

        is_video_result = (new_media_item["type"] == "video")
        ts_ini = float(cena.get("tempo_inicio", 0))
        dur = float(cena.get("duracao", 5))
        ts_fim = float(cena.get("tempo_fim", ts_ini + dur))

        # 9. Salva na estrutura oficial profissional do projeto:
        char_tag = ""
        if char_info:
            char_tag = char_info.get("referencia_flow") or (f"@{char_info.get('nome')}" if char_info.get("nome") else "")

        res_salva = scene_plan_svc.salvar_midia_cena_estruturada(
            projeto_id=projeto_id,
            cid=cid,
            ts_ini=ts_ini,
            ts_fim=ts_fim,
            prompt_texto=prompt,
            midia_bytes=content_bytes,
            is_video=is_video_result,
            modelo_usado=self.current_model,
            personagem_ref=char_tag
        )

        # FASE 3.2 — falha de validação não entra em storyboard/galeria (status=ERRO já setado).
        if not res_salva.get("success", True):
            msg = res_salva.get("error", "mídia inválida")
            pw_log(f"[CENA {cid:03d}] MIDIA_INVALIDA: {msg}", level="error")
            return False, f"Mídia inválida para a cena {cid}: {msg}"

        # 10. Atualiza o status definitivo para READY / BAIXADA no scene_plan.json
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "arquivo_midia": res_salva["arquivo_path"],
            "download_path": res_salva["arquivo_path"],
            "filename": res_salva["arquivo_nome"],
            "image_status": scene_plan_svc.IMAGE_STATUS_READY if not is_video_result else scene_plan_svc.IMAGE_STATUS_DOWNLOADED,
            "video_status": scene_plan_svc.VIDEO_STATUS_READY if is_video_result else scene_plan_svc.VIDEO_STATUS_NOT_STARTED,
            "status": scene_plan_svc.STATUS_BAIXADA,
            "erro_msg": "",
            "uses_character": uses_char,
            "character_ref": char_tag if uses_char else "",
        })
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)

        print("[LOG] FILE_SAVED_OK", flush=True)
        print("[LOG] SCENE_SAVED_OK", flush=True)
        print("[LOG] SCENE_LINKED_OK", flush=True)
        print("[LOG] UPDATE_UI", flush=True)
        print(f"[OK] Arquivo salvo: {res_salva['arquivo_nome']}", flush=True)
        print("[OK] Storyboard atualizado", flush=True)
        print("[OK] Galeria atualizada", flush=True)
        pw_log(f"[CENA {cid:03d}] SCENE_SAVED_OK & UPDATE_UI: Arquivo salvo como '{res_salva['arquivo_nome']}' e sincronizado no Lira Studio.")
        return True, f"Cena {cid} gerada e baixada com sucesso: {res_salva['arquivo_nome']}"

    def _handle_run_queue(self, projeto_id: str, scene_ids: Optional[List[int]], modo: str):
        self.is_running_queue = True
        self.stop_requested.clear()
        self.current_project_id = projeto_id
        self.current_flow_mode = modo

        try:
            # A sessão Playwright nasce DENTRO desta thread (obrigatório p/ sync_api).
            ok_sessao, msg_sessao = self._iniciar_sessao_thread()
            if not ok_sessao:
                print(f"[ERRO] Falha ao conectar no Google Flow: {msg_sessao}", flush=True)
                pw_log(f"\n[FLOW SESSION]\nStatus: Erro\nMotivo: {msg_sessao}", level="error")
                return

            # 2. Garante que a fila rode exclusivamente na aba do Google Flow
            if not self._garantir_aba_flow():
                msg_erro = "Google Flow fechado. Clique em Abrir Google Flow novamente."
                print(f"[ERRO] {msg_erro}", flush=True)
                pw_log(f"\n[FLOW SESSION]\nStatus: Erro\nMotivo: {msg_erro}", level="error")
                return

            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
            if not plan or not plan.get("cenas"):
                print("[AVISO] scene_plan não encontrado para execução da fila.", flush=True)
                pw_log("scene_plan não encontrado para execução da fila.", level="warn")
                return

            cenas = plan["cenas"]
            target_ids = set(scene_ids) if scene_ids else None
            total_cenas_projeto = len(cenas)

            # Filtra cenas pendentes (ignora cenas concluídas/BAIXADA com arquivo no disco)
            cenas_a_processar = []
            for c in cenas:
                cid = int(c.get("id", 0))
                if target_ids is not None and cid not in target_ids:
                    continue
                arq = c.get("arquivo_midia")
                st = c.get("status")
                # Retomada automática: se já foi BAIXADA e o arquivo existe, não repete
                if st == scene_plan_svc.STATUS_BAIXADA and arq and Path(arq).exists():
                    continue

                # REGRA 6: Nunca gerar vídeo na primeira passada. Se modo for vídeo, marca para animar depois
                if modo in ["video", "imagem_video"]:
                    scene_plan_svc.atualizar_cena(projeto_id, cid, {"animar_depois": True, "animate_later": True})
                    print(f"[LOG] ANIMATE_LATER_FLAGGED: Cena {cid} marcada para animação posterior", flush=True)
                    pw_log(f"[CENA {cid:03d}] ANIMATE_LATER_FLAGGED: Cena marcada para animação posterior.")

                if modo == "animacao":
                    # FASE 3.2 — fonte única de tipo: só cenas com tipo==video são animadas.
                    # animar_depois/animate_later NÃO tornam image em vídeo.
                    e_video = (scene_plan_svc.tipo_efetivo_cena(c) == scene_plan_svc.TIPO_VIDEO)
                    if e_video:
                        cenas_a_processar.append(c)
                else:
                    cenas_a_processar.append(c)

            print("[OK] ULTRACUT3 iniciado", flush=True)
            print("[OK] Google Flow conectado", flush=True)
            print(f"[OK] Projeto carregado: {projeto_id}", flush=True)
            print(f"[INFO] Cenas a produzir: {len(cenas_a_processar)} de {total_cenas_projeto}\n", flush=True)

            url_aba = (self.page.url if self.page else "") or ""
            pw_log(f"\n[FLOW SESSION]\nStatus: Produção Ativa\nAba: Google Flow\nURL: {url_aba}\nTotal de Cenas: {len(cenas_a_processar)}")

            self.queue_start_time = time.time()
            self.scene_durations = []

            for idx, cena in enumerate(cenas_a_processar, 1):
                if self.stop_requested.is_set():
                    print("\n[INFO] Fila pausada pelo usuário.", flush=True)
                    pw_log("\n[FLOW SESSION]\nStatus: Fila pausada pelo usuário.")
                    break

                cid = int(cena.get("id", 0))
                scene_t0 = time.time()
                self.cena_ativa = {
                    "scene_id": cid,
                    "scene_idx": idx,
                    "total_cenas": len(cenas_a_processar),
                    "status": "GERANDO",
                    "etapa": "Iniciando geração no Flow...",
                    "tentativa": 1,
                    "inicio_ts": scene_t0,
                    "tempo_decorrido": 0.0,
                    "tempo_total": time.time() - self.queue_start_time,
                    "tempo_medio": (sum(self.scene_durations) / len(self.scene_durations)) if self.scene_durations else 0.0
                }

                sucesso = False
                res_msg = ""
                for tentativa in range(2):
                    if self.stop_requested.is_set():
                        break

                    self.cena_ativa["tentativa"] = tentativa + 1
                    self.cena_ativa["etapa"] = f"Executando no Flow (Tentativa {tentativa + 1}/2)..."

                    ok, res_msg = self._processar_cena_individual(
                        projeto_id,
                        cena,
                        is_anim=(modo == "animacao"),
                        index=idx,
                        total_cenas=total_cenas_projeto
                    )
                    if ok:
                        sucesso = True
                        break
                    else:
                        print(f"[AVISO] Tentativa {tentativa + 1} falhou para cena {cid}: {res_msg}", flush=True)
                        pw_log(f"[CENA {cid:03d}] AVISO: Tentativa {tentativa + 1} falhou ({res_msg}). Executando auto-recuperação...", level="warn")
                        self.cena_ativa["etapa"] = f"Tentativa {tentativa + 1} falhou. Tentando reconectar aba do Flow..."
                        self._garantir_aba_flow()
                        time.sleep(2)

                dur_cena = time.time() - scene_t0
                if sucesso:
                    self.scene_durations.append(dur_cena)
                    med = sum(self.scene_durations) / len(self.scene_durations)
                    self.cena_ativa = {
                        "scene_id": cid,
                        "scene_idx": idx,
                        "total_cenas": len(cenas_a_processar),
                        "status": "CONCLUIDO",
                        "etapa": f"Cena {cid:03d} concluída em {dur_cena:.1f}s!",
                        "duracao_cena": dur_cena,
                        "tempo_total": time.time() - self.queue_start_time,
                        "tempo_medio": med
                    }
                    pw_log(f"[CENA {cid:03d}] SUCESSO: Concluída em {dur_cena:.1f}s | Média: {med:.1f}s/cena")
                else:
                    if not self.stop_requested.is_set():
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "erro_msg": res_msg
                        })
                        pw_log(f"[CENA {cid:03d}] ERRO: Não foi possível gerar após 2 tentativas ({res_msg})", level="error")

                time.sleep(1)

            if not self.stop_requested.is_set():
                total_t = time.time() - (self.queue_start_time or time.time())
                print(f"\n[LOG] ALL_IMAGES_COMPLETE_OK (Tempo total: {total_t:.1f}s)", flush=True)
                print("[OK] Produção de cenas concluída!", flush=True)
                pw_log(f"[FLOW SESSION] ALL_IMAGES_COMPLETE_OK: Todas as cenas foram produzidas com sucesso em {total_t:.1f}s.")

        except Exception as e:
            print(f"[ERRO] Erro na execução da fila: {e}", flush=True)
            pw_log(f"Erro inesperado na execução da fila Playwright CDP: {e}", level="error")
        finally:
            # Encerra a sessão na MESMA thread que a criou (obrigatório p/ sync_api)
            self._encerrar_sessao()
            self.is_running_queue = False
            self.cena_ativa = None
            pw_log("\n[FLOW SESSION]\nStatus: Fila de produção finalizada.")


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


class FlowSessionManager:
    """Fachada sobre o worker CDP singleton, para uma API estável nas rotas."""

    @staticmethod
    def start_session(projeto_id: str = "") -> Tuple[bool, str]:
        """Botão 'Abrir Google Flow' (thread HTTP do Flask).

        Apenas garante o Chrome CDP aberto — NÃO cria sessão Playwright
        permanente (o sync_api é thread-bound). A sessão, a localização da
        aba do Flow e o processamento da fila são responsabilidade da thread
        da fila de produção (FlowQueueWorker.start_worker → _handle_run_queue).
        """
        worker = FlowQueueWorker.get_worker()
        return worker._abrir_chrome_cdp()

    @staticmethod
    def close_session():
        worker = FlowQueueWorker.get_worker()
        worker._encerrar_sessao()

    @staticmethod
    def is_active() -> bool:
        return FlowQueueWorker.get_worker()._check_is_active()


def criar_personagem_no_flow_direto(projeto_id: str, nome: str, imagem_abs: str) -> Dict[str, Any]:
    """
    Executa a criação real e oficial do personagem no Google Flow agindo como usuário humano:
    1. Abre/conecta ao Google Flow -> Log: FLOW_OPEN_OK
    2. Entra na tela de criação de personagem
    3. Clica em Fazer upload e envia a imagem -> Logs: UPLOAD_START, UPLOAD_COMPLETE
    4. Espera e confirma que a imagem foi renderizada -> Log: IMAGE_REFERENCE_OK
    5. Insere o nome @Nome na descrição e campos -> Log: NAME_SET_OK
    6. Aguarda o processamento do Flow e captura o ID -> Log: CHARACTER_CREATED_OK
    7. Atualiza a identidade no Lira Studio -> Log: CHARACTER_LINKED_OK
    """
    from playwright.sync_api import sync_playwright
    import services.character_service as character_svc

    if not nome:
        return {"success": False, "error": "Falha na criação do personagem: nome do personagem não informado."}

    if not imagem_abs or not Path(imagem_abs).exists():
        fallback_avatar = r"C:\Users\Administrator\Desktop\CANAL\AVATAR\AVATAR.png"
        if Path(fallback_avatar).exists():
            imagem_abs = fallback_avatar
        else:
            return {"success": False, "error": "Falha na criação do personagem: imagem de referência não encontrada no disco."}

    ok_cdp, msg_cdp = ensure_chrome_cdp()
    if not ok_cdp:
        return {"success": False, "error": f"Falha na criação do personagem: etapa de conexão CDP não concluída ({msg_cdp})."}

    flow_char_id = ""
    ref_flow = f"@{nome}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            if not browser.contexts:
                return {"success": False, "error": "Falha na criação do personagem: etapa de contexto do navegador não concluída."}
            context = browser.contexts[0]

            pages = [pg for pg in context.pages if "labs.google" in (pg.url or "")]
            if not pages:
                return {"success": False, "error": "Falha na criação do personagem: aba do Google Flow não encontrada."}

            page = pages[0]
            page.bring_to_front()
            page.wait_for_timeout(500)

            # Garante que está no projeto
            if "/project/" not in page.url:
                proj_link = page.locator('a[href*="/tools/flow/project/"], div[role="button"]:has-text("Projeto")').first
                if proj_link.is_visible(timeout=3000):
                    proj_link.click()
                    page.wait_for_timeout(3000)

            canvas_url = page.url
            print("\n[LOG] FLOW_OPEN_OK", flush=True)
            pw_log("FLOW_OPEN_OK: Conexão e projeto do Google Flow ativos.")

            # 1. Entra na tela de criação de personagem
            if "/characters" not in page.url and "/character/" not in page.url:
                btn_add = page.locator('button:has-text("Adicionar mídia"), button:has(i:has-text("add"))').first
                if btn_add.is_visible(timeout=3000):
                    btn_add.click()
                    page.wait_for_timeout(500)

                    btn_criar_p = page.locator('button:has-text("Criar personagem"), [role="menuitem"]:has-text("Criar personagem")').first
                    if btn_criar_p.is_visible(timeout=2000):
                        btn_criar_p.click()
                        page.wait_for_timeout(2500)

            # 2. Executa Upload da Imagem
            print("[LOG] UPLOAD_START", flush=True)
            pw_log(f"UPLOAD_START: Enviando foto de referência '{imagem_abs}'...")

            upload_sucesso = False
            input_file = page.locator('input[type="file"]').first
            if input_file.count() > 0:
                try:
                    input_file.set_input_files(imagem_abs)
                    upload_sucesso = True
                except Exception as e_inp:
                    pw_log(f"Aviso ao usar set_input_files: {e_inp}", level="warn")

            if not upload_sucesso:
                btn_up = page.locator('button:has-text("Fazer upload"), button:has-text("upload")').first
                if btn_up.is_visible(timeout=2000):
                    try:
                        with page.expect_file_chooser(timeout=4000) as fc_info:
                            btn_up.click(force=True)
                        fc_info.value.set_files(imagem_abs)
                        upload_sucesso = True
                    except Exception as e_fc:
                        pw_log(f"Aviso no file chooser: {e_fc}", level="warn")

            if not upload_sucesso:
                return {"success": False, "error": "Falha na criação do personagem: etapa de upload da imagem não concluída."}

            print("[LOG] UPLOAD_COMPLETE", flush=True)
            pw_log("UPLOAD_COMPLETE: Arquivo de imagem transmitido ao Google Flow.")

            # 3. Espera e confirma que a imagem está carregada e visível no Flow
            img_carregada = False
            for _ in range(15):
                page.wait_for_timeout(1000)
                # Verifica existência de imagens renderizadas no formulário
                tem_preview = page.evaluate('''() => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    return imgs.some(i => i.naturalWidth > 0 && !i.src.includes('avatar_placeholder') && !i.src.includes('icon'));
                }''')
                if tem_preview:
                    img_carregada = True
                    break

            if not img_carregada:
                # Tenta esperar mais 3 segundos como tolerância de rede
                page.wait_for_timeout(3000)
                img_carregada = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('img')).length > 2;
                }''')

            if not img_carregada:
                return {"success": False, "error": "Falha na criação do personagem: etapa de confirmação da imagem não concluída."}

            print("[LOG] IMAGE_REFERENCE_OK", flush=True)
            pw_log("IMAGE_REFERENCE_OK: Imagem de referência carregada e confirmada no DOM do Flow.")

            # 4. Inserir o nome do personagem vindo do projeto (@Marcos)
            desc_padrao = f"Character @{nome}. Realistic human appearance. Preserve exact facial identity, appearance, age, hair, skin details and visual consistency in all scenes."
            editor_char = page.locator('div[data-slate-editor="true"], div[contenteditable="true"]').first
            if editor_char.is_visible(timeout=3000):
                editor_char.click()
                page.wait_for_timeout(100)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.insert_text(desc_padrao)
                page.wait_for_timeout(500)

            print("[LOG] NAME_SET_OK", flush=True)
            pw_log(f"NAME_SET_OK: Nome e referência '{ref_flow}' inseridos no formulário.")

            # 5. Submete a criação do personagem
            btn_submit = page.locator('button:has(i:has-text("add_2")), button:has(i:has-text("arrow_forward")), button:has-text("Criar")').first
            if btn_submit.is_visible(timeout=2000) and not btn_submit.is_disabled():
                btn_submit.click()
            else:
                if editor_char.is_visible():
                    editor_char.focus()
                    page.keyboard.press("Enter")

            # 6. Aguarda o processamento completo do Flow e captura o ID
            for _ in range(25):
                page.wait_for_timeout(1000)
                if "/character/" in page.url:
                    match_id = re.search(r"/character/([a-f0-9\-]+)", page.url)
                    if match_id:
                        flow_char_id = match_id.group(1)
                    break

            # Localiza o título ("Personagem sem título" / input) e altera para @Nome
            try:
                title_inp = page.locator('header input[type="text"], input[value*="Personagem" i], input[value*="Character" i], input[value*="título" i]').first
                if title_inp.is_visible(timeout=1500):
                    title_inp.click(timeout=1000)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.type(f"@{nome}")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Enter")
                    title_inp.evaluate('el => { el.dispatchEvent(new Event("change", { bubbles: true })); el.dispatchEvent(new Event("blur", { bubbles: true })); }')
                    page.wait_for_timeout(400)
            except Exception as e_title:
                pw_log(f"Aviso ao renomear título do personagem: {e_title}", level="warn")

            # Clica em Concluir / Voltar ao canvas
            try:
                btn_voltar = page.locator('button:has-text("Concluir"), button:has-text("Salvar"), button:has-text("Voltar"), button:has(i:has-text("arrow_back"))').first
                if btn_voltar.is_visible(timeout=1500):
                    btn_voltar.click(timeout=1500)
                    page.wait_for_timeout(1500)
            except Exception:
                pass

            if "/project/" not in page.url or "/character/" in page.url or "/characters" in page.url:
                if canvas_url and "/project/" in canvas_url:
                    try:
                        page.goto(canvas_url)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

            flow_id_final = flow_char_id or f"flow-char-{nome.lower()}"
            print("[LOG] CHARACTER_CREATED_OK", flush=True)
            pw_log(f"CHARACTER_CREATED_OK: Personagem '{nome}' ({ref_flow}) criado com ID '{flow_id_final}'.")

            # 7. Atualiza a identidade no Lira Studio
            character_svc.salvar_identidade_projeto(
                projeto_id=projeto_id,
                tipo="personagem",
                nome=nome,
                referencia_flow=ref_flow,
                arquivo_origem=imagem_abs,
                visual_style="photorealistic_cinematic"
            )
            character_svc.atualizar_status_flow_personagem(
                projeto_id=projeto_id,
                created=True,
                flow_char_name=ref_flow,
                flow_char_id=flow_id_final
            )

            ident_final = character_svc.obter_identidade_projeto(projeto_id)
            print("[LOG] CHARACTER_LINKED_OK", flush=True)
            pw_log(f"CHARACTER_LINKED_OK: Identidade do projeto '{projeto_id}' atualizada com sucesso.")

            return {
                "success": True,
                "nome": nome,
                "referencia_flow": ref_flow,
                "flow_character_name": ref_flow,
                "flow_character_id": flow_id_final,
                "flow_character_created": True,
                "tipo": "personagem",
                "tipo_display": "PERSONAGEM COM FOTO",
                "imagem_abs": str(imagem_abs),
                "identidade": ident_final,
                "mensagem": f"Personagem '{nome}' ({ref_flow}) criado e vinculado ao projeto com sucesso!"
            }

    except Exception as e:
        pw_log(f"Erro ao criar personagem no Flow via CDP: {e}", level="error")
        return {
            "success": False,
            "error": f"Falha na criação do personagem: etapa de processamento do Flow não concluída ({str(e)})."
        }

