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
from datetime import datetime
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

# TAREFA — Delay configurável entre gerações sequenciais na fila.
# Respiro entre o fim do download+salvamento de uma cena (SCENE_SAVED_OK)
# e o disparo do próximo prompt (SCENE_GENERATION_START da cena seguinte).
# 2s evita rajada/throttling (rate limit) no Google Flow.
DELAY_ENTRE_PROMPTS_SEG = 2

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

def _encerrar_chrome_cdp(port: int = 9222) -> None:
    """Encerra APENAS o Chrome iniciado com CDP na porta indicada.

    Não fecha o Chrome comum do usuário: filtra processos chrome.exe cuja
    linha de comando contenha '--remote-debugging-port=<port>'.
    """
    try:
        import subprocess as _sp
        if sys.platform == "win32":
            _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 f"Where-Object {{ $_.CommandLine -like '*remote-debugging-port={port}*' }} | "
                 f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
                capture_output=True, timeout=20)
        else:
            _sp.run(["pkill", "-f", f"remote-debugging-port={port}"], capture_output=True)
    except Exception:
        pass


def ensure_chrome_cdp(port: int = 9222, force_restart: bool = False) -> Tuple[bool, str]:
    """Garante Chrome rodando com CDP na porta indicada, abrindo-o se preciso com Flow e UltraCut3 na mesma janela.

    Se force_restart=True, encerra a instância CDP atual (somente a que roda com
    --remote-debugging-port=<port>) e abre uma nova usando o perfil da conta
    ativa em config/flow_accounts.json (rotação de contas por créditos).
    """
    if _cdp_port_open(port) and not force_restart:
        try:
            import urllib.request, json
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json", headers={"User-Agent": "Mozilla/5.0"})
            tabs = json.loads(urllib.request.urlopen(req, timeout=3).read())
            has_web = any(("127.0.0.1:5000" in t.get("url", "") or "localhost:5000" in t.get("url", "")) for t in tabs if t.get("type") == "page")
            if not has_web:
                req_new_web = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?http://127.0.0.1:5000", method="PUT")
                urllib.request.urlopen(req_new_web, timeout=3)
            has_flow = any(("labs.google" in t.get("url", "") or "flow" in t.get("url", "")) for t in tabs if t.get("type") == "page")
            if not has_flow:
                req_new = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?https://labs.google/fx/pt/tools/flow", method="PUT")
                urllib.request.urlopen(req_new, timeout=3)
        except Exception:
            pass
        return True, "Chrome CDP já ativo."
    if force_restart:
        _encerrar_chrome_cdp(port)
        time.sleep(1.5)
    chrome_exe = _find_chrome_exe()
    if not chrome_exe:
        return False, "Chrome não encontrado nos caminhos padrão."
    import json
    accounts_path = Path("config/flow_accounts.json")
    profile_dir = str(Path.home() / "ultracut3_chrome_profile")  # fallback
    if accounts_path.exists():
        accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
        conta_ativa = next(
            (c for c in accounts["contas"] if c.get("ativa")), None
        )
        if conta_ativa:
            profile_dir = str(Path.home() / conta_ativa["profile_dir"])
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        subprocess.Popen(
            [
                chrome_exe,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                "http://127.0.0.1:5000",
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


def _flow_meta_data(projeto_id: str) -> dict:
    """Lê o flow_meta.json do projeto (nunca lança exceção; sempre dict)."""
    p = _flow_meta_path(projeto_id)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _conta_ativa_id() -> Optional[Any]:
    """Retorna o `id` da conta ativa em config/flow_accounts.json (ou None)."""
    accounts_path = Path("config/flow_accounts.json")
    if not accounts_path.exists():
        return None
    try:
        accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
        for c in accounts.get("contas", []):
            if c.get("ativa"):
                return c.get("id")
    except Exception:
        return None
    return None


def salvar_projeto_flow_url(projeto_id: str, url: str, conta_id: Optional[Any] = None):
    """Salva a URL do projeto Google Flow no flow_meta.json.

    RETROCOMPATÍVEL: `flow_project_url` continua sendo gravado (callers sem
    `conta_id` seguem funcionando). A URL também é indexada em
    `urls_por_conta{str(conta_id): url}` — usando o `conta_id` informado ou, se
    ausente, o id da conta ativa — o que garante **1 projeto por conta** na
    rotação de contas por créditos.
    """
    p = _flow_meta_path(projeto_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _flow_meta_data(projeto_id)
    data["flow_project_url"] = url
    if conta_id is None:
        conta_id = _conta_ativa_id()
    if conta_id is not None:
        data.setdefault("urls_por_conta", {})[str(conta_id)] = url
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    pw_log(f"URL do projeto Flow salva para {projeto_id} (conta={conta_id or '-'}): {url}")


def carregar_projeto_flow_url(projeto_id: str, conta_id: Optional[Any] = None) -> Optional[str]:
    """Retorna a URL do projeto Flow salva.

    - `conta_id=None` → URL legada `flow_project_url` (compatibilidade retroativa).
    - `conta_id` dado → APENAS a URL específica daquela conta
      (`urls_por_conta[str(conta_id)]`), sem cair para a URL de outra conta —
      retorna None se a conta ainda não tem projeto próprio. Usado na rotação
      para NUNCA navegar à URL da conta antiga.
    """
    data = _flow_meta_data(projeto_id)
    if conta_id is not None:
        return (data.get("urls_por_conta", {}) or {}).get(str(conta_id))
    return data.get("flow_project_url")


def _detectar_cena_avatar(cena: dict) -> bool:
    """PRIORIDADE 4 — True se a cena é de avatar/personagem humano.

    Critérios: uses_character=True, scene_type contendo 'avatar', ou nome/título/
    texto da cena contendo 'avatar'. Avatar SEMPRE gera IMAGEM (modo gestual).
    """
    try:
        nome_cena = str(cena.get("nome") or cena.get("titulo") or cena.get("texto") or "").lower()
    except Exception:
        nome_cena = ""
    return (
        cena.get("uses_character") is True
        or "avatar" in str(cena.get("scene_type") or "").lower()
        or "avatar" in nome_cena
    )


def _ajustar_prompt_avatar_gestual(prompt_base: str, nome_cena: str = "") -> str:
    """PRIORIDADE 4 — Avatar GESTUAL (sem fala, apenas gestos).

    1. Remove construções de fala/narração do prompt (@fala/@speaks/@says,
       "speaks to camera", "says to camera", "narration", ...).
    2. Adiciona sufixo de linguagem corporal expressiva ("sem fala, apenas gestos").
    Retorna o prompt ajustado ("" se não havia prompt base).
    """
    base = str(prompt_base or "").strip()
    if not base:
        return ""
    prompt_limpo = re.sub(
        r'(@fala|@speaks|@says|speaks to camera|says to camera|narration)[:\s]*[^.!?]*[.!?]?',
        "",
        base,
        flags=re.IGNORECASE,
    ).strip()
    objetivo_visual = "gesticulando e expressando emoção para câmera"
    if "objetivo" in base.lower() or "show" in base.lower():
        objetivo_visual = "usando gestos e expressão corporal para transmitir intenção/objetivo"
    if prompt_limpo and prompt_limpo[-1] not in ".!?":
        prompt_limpo += "."
    novo_prompt = f"{prompt_limpo} Avatar {objetivo_visual}, sem fala, apenas linguagem corporal expressiva."
    pw_log(f"[AVATAR] Prompt ajustado para gestualidade. Sufixo: '{objetivo_visual}'", level="info")
    return novo_prompt


def pw_log(msg: str, level: str = "info"):
    log_event("PLAYWRIGHT_FLOW", msg, level=level)


JS_FETCH_MEDIA_LIST = """
() => {
    try {
        const results = [];
        const imgs = Array.from(document.querySelectorAll('img'));
        imgs.forEach((img, idx) => {
            const src = img.src || img.currentSrc || '';
            if (!src || src.includes('gstatic.com') || src.includes('googleusercontent.com') || src.includes('avatar') || src.includes('icon') || src.includes('google_logo') || src.includes('profile')) {
                return;
            }
            const r = img.getBoundingClientRect();
            if ((r.width > 60 && r.height > 60) || (img.naturalWidth > 60 && img.naturalHeight > 60)) {
                // IDENTIFICADOR ESTÁVEL (v0.4.0): UUID do src (atributo imutável do
                // elemento), NUNCA posição/índice do DOM. UUID presente em toda mídia
                // do Flow via /fx/api/trpc/media.getMediaUrlRedirect?name=<uuid>.
                const uuidMatch = src.match(/[?&]name=([0-9a-fA-F-]{36})/);
                const mediaKey = uuidMatch ? uuidMatch[1] : src;
                const cleanName = uuidMatch ? uuidMatch[1]
                    : (src.split('/').pop().split('?')[0] || src.slice(0, 60));
                const uniqueId = 'media_' + mediaKey + '_' + (img.naturalWidth || 0);

                results.push({
                    id: uniqueId,
                    mediaKey: mediaKey,
                    name: cleanName,
                    src: src,
                    type: 'image',
                    width: img.naturalWidth || r.width,
                    height: img.naturalHeight || r.height,
                    domIndex: idx
                });
            }
        });

        const videos = Array.from(document.querySelectorAll('video'));
        videos.forEach((v, idx) => {
            const src = v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
            if (src && !src.startsWith('data:')) {
                const uuidMatch = src.match(/[?&]name=([0-9a-fA-F-]{36})/);
                const mediaKey = uuidMatch ? uuidMatch[1] : src;
                const cleanName = uuidMatch ? uuidMatch[1]
                    : (src.split('/').pop().split('?')[0] || src.slice(0, 60));
                results.push({
                    id: 'media_' + mediaKey,
                    mediaKey: mediaKey,
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

JS_EXTRACT_CANVAS_DATA_URL = """
(mediaKeyOrIndex) => {
    try {
        let img = null;
        const imgs = Array.from(document.querySelectorAll('img'));
        if (typeof mediaKeyOrIndex === 'string' && mediaKeyOrIndex) {
            // Localiza por UUID/src (estável) — NUNCA por posição quando possível.
            img = imgs.find(i => (i.src || i.currentSrc || '').includes(mediaKeyOrIndex)) || null;
        }
        if (!img && typeof mediaKeyOrIndex === 'number' && mediaKeyOrIndex >= 0) {
            img = imgs[mediaKeyOrIndex] || null;
        }
        if (!img || !img.complete || img.naturalWidth <= 0) return { ok: false, error: 'img not complete' };
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth || 1376;
        canvas.height = img.naturalHeight || 768;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const dataUrl = canvas.toDataURL('image/png');
        if (dataUrl && dataUrl.startsWith('data:image/')) {
            return { ok: true, dataUrl: dataUrl };
        }
        return { ok: false, error: 'invalid dataUrl' };
    } catch(e) {
        return { ok: false, error: e.toString() };
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
    const alertEls = Array.from(document.querySelectorAll('div[role="alert"], div[role="status"], div[class*="error" i], div[class*="toast" i], div[class*="snackbar" i]'));
    const frases = [
        'violate our policies',
        'violates our policies',
        'violates content policy',
        'against our policies',
        'this prompt might violate',
        'cannot generate this image',
        'unable to generate image'
    ];

    for (const el of alertEls) {
        const txt = (el.innerText || el.textContent || '').toLowerCase();
        for (const f of frases) {
            if (txt.includes(f)) {
                return { recusado: true, trecho: txt.substring(0, 120) };
            }
        }
    }
    return { recusado: false };
}
"""

# Detecção de chip/entidade REAL no editor do Flow (ProseMirror/Slate).
# Regra (Item 2 — P2): texto puro digitado como "@Nome" NUNCA é considerado
# entidade — exige marcador estrutural (contenteditable=false, <img>,
# data-entity/data-ingredient ou classe chip/pill/badge/mention/ingredient).
_JS_VERIFICA_CHIP_EDITOR = r"""
el => {
    // 1. Elementos não editáveis (void nodes / inline chips no ProseMirror e Slate)
    const nonEdit = el.querySelectorAll('[contenteditable="false"]');
    if (nonEdit.length > 0) return true;
    // 2. Imagens de avatar/personagem/ingrediente dentro do editor
    const imgs = el.querySelectorAll('img');
    if (imgs.length > 0) return true;
    // 3. Classes ou atributos de chip/pill/entity/ingredient/mention
    const chips = el.querySelectorAll('[class*="chip"], [class*="pill"], [class*="badge"], [data-entity], [data-ingredient], [class*="ingredient"], [class*="mention"]');
    if (chips.length > 0) return true;
    // 4. Nós estruturados do editor com marcador REAL de entidade
    const nodes = el.querySelectorAll('[data-slate-node="element"], .ProseMirror-widget, span');
    for (const n of nodes) {
        const cls = (n.className || '');
        if (n.querySelector('img') || n.getAttribute('contenteditable') === 'false'
            || n.hasAttribute('data-entity') || n.hasAttribute('data-ingredient')
            || /(^|[\s_])(chip|pill|badge|mention|ingredient|entity)([\s_]|$)/i.test(cls)) {
            return true;
        }
    }
    // 5. Texto plano "@Nome" digitado (menção NÃO convertida em chip) NÃO é evidência.
    return false;
}
"""

_EDITOR_PROMPT_SELECTORS = [
    'div.ProseMirror[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
    'div.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
    'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
    'div[role="textbox"][contenteditable="true"]:not(aside *)',
    '[contenteditable="true"]:not(aside *)',
]


def _localizar_editor_prompt(page):
    """Localiza o editor de prompt principal do Flow (fora de overlays/aside).

    Usada por incluir_referencia_personagem para a verificação DOM pós-clique
    (evidência real de inserção de entidade). None se não houver editor visível.
    """
    if not page:
        return None
    for sel in _EDITOR_PROMPT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                return loc
        except Exception:
            pass
    return None


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
        # PARTE 5 — quando True, a fila/worker deve tratar créditos esgotados no modo VÍDEO
        # caindo para IMAGEM (sem rotacionar conta). Resetado a cada fila nova.
        self._fallback_video_para_imagem: bool = False
        self.stop_requested = threading.Event()
        self.current_project_id: Optional[str] = None
        self.current_flow_mode: Optional[str] = None
        self.current_download_quality: str = "1K"
        self._upscale_tentado_cena: bool = False
        self.cena_ativa: Optional[Dict[str, Any]] = None
        self.queue_start_time: Optional[float] = None
        self.scene_durations: List[float] = []
        self._lock = threading.Lock()
        self._avatar_uploaded = False
        self.account_email: Optional[str] = None
        self.current_project_name: Optional[str] = None
        self.current_delay_info: Optional[Dict[str, Any]] = None

    def _extrair_email_via_painel_conta(self) -> Optional[str]:
        """Clica no avatar de perfil, lê o painel de conta e extrai o email.

        O email da conta Google NÃO existe no DOM da página principal do Flow —
        ele só aparece como texto visível no painel de conta ([role="dialog"])
        que abre ao clicar no avatar (canto superior direito).

        Seletores do avatar (testados no Flow real, v1.6):
          1. img[alt="Imagem do perfil do usuário"]   (alt padrão do Google)
          2. img[src*="googleusercontent"]            (avatar real: lh3.googleusercontent.com)
          3. button:has(img[src*="googleusercontent"])

        Após a leitura, fecha o painel com Escape. NUNCA lança exceção —
        qualquer falha retorna None e a fila segue normalmente.
        """
        if not self.page:
            return None
        try:
            if self.page.is_closed():
                return None
        except Exception:
            return None

        email = None
        try:
            avatar = None
            for sel in (
                'img[alt="Imagem do perfil do usuário"]',
                'img[src*="googleusercontent"]',
                'button:has(img[src*="googleusercontent"])',
            ):
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=1500):
                        avatar = loc
                        break
                except Exception:
                    continue
            if avatar is None:
                pw_log("[METADATA] Avatar de perfil não encontrado — email não extraído.", level="debug")
                return None

            avatar.click(timeout=5000)

            # Aguarda o painel de conta abrir (dialog); fallback: delay fixo 1.8s
            try:
                self.page.wait_for_selector('[role="dialog"]', state="visible", timeout=5000)
            except Exception:
                self.page.wait_for_timeout(1800)

            email_js = """
            () => {
                const emailRegex = /[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+/;
                const painel = document.querySelector('[role="dialog"]');
                if (!painel) return null;
                const txt = (painel.innerText || painel.textContent || '');
                if (txt.length > 8000) return null;
                const m = txt.match(emailRegex);
                return m ? m[0] : null;
            }
            """
            email = self.page.evaluate(email_js)
            if email:
                pw_log(f"[METADATA] Email da conta extraído via painel de conta: {email}")
            else:
                pw_log("[METADATA] Painel de conta aberto mas email não encontrado no texto.", level="debug")
        except Exception as e:
            pw_log(f"[METADATA] Falha ao extrair email via painel de conta: {e}", level="debug")
            email = None
        finally:
            # Fecha o painel para não deixar a UI do Flow bloqueada.
            # O dialog de conta e Radix UI (animacao de saida) — o Escape fecha,
            # mas e preciso aguardar o state="hidden" (o is_visible durante a
            # animacao ainda reporta o dialog aberto).
            try:
                self.page.keyboard.press("Escape")
                try:
                    self.page.wait_for_selector('[role="dialog"]', state="hidden", timeout=3000)
                except Exception:
                    pass
                # Fallback: se ainda houver dialog visivel, tenta botao fechar
                try:
                    _visiveis = 0
                    _n_dlg = self.page.locator('[role="dialog"]').count()
                    for _i in range(_n_dlg):
                        try:
                            if self.page.locator('[role="dialog"]').nth(_i).is_visible(timeout=300):
                                _visiveis += 1
                        except Exception:
                            continue
                    if _visiveis:
                        _btns = self.page.locator('[role="dialog"] button')
                        for _b in range(_btns.count()):
                            try:
                                _el = _btns.nth(_b)
                                _txt = (_el.inner_text() or "").strip().lower()
                                _aria = (_el.get_attribute("aria-label") or "").lower()
                                if "close" in _txt or "fechar" in _txt or "fechar" in _aria or "close" in _aria:
                                    _el.click(timeout=2000)
                                    self.page.wait_for_selector('[role="dialog"]', state="hidden", timeout=2500)
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass
            except Exception:
                pass
        return email

    def _verificar_sessao_google(self) -> bool:
        """Verifica se há sessão Google ativa via cookies CDP.
        Mais rápido e confiável que clicar no avatar.
        Retorna True se cookies de sessão existem, False caso contrário.
        """
        try:
            if not self.page or self.page.is_closed():
                return False
            cookies = self.page.context.cookies(["https://accounts.google.com"])
            return any(c["name"] in ("SAPISID", "SID", "SSID") for c in cookies)
        except Exception as e:
            pw_log(f"[FLOW] Erro ao verificar sessão Google: {e}", level="warn")
            return False

    def _verificar_creditos_disponiveis(self) -> bool:
        """Verifica na página atual se a conta ainda tem créditos de geração.

        LECTURA não destrutiva: NÃO chama _detectar_erro_ou_limite_modelo nem
        _tratar_indicador_limite (que ROTAM/fallback a conta como efeito colateral).
        Varre document.body.innerText contra as frases/indicadores de crédito
        conhecidos do Google Flow. Retorna True se NÃO há indicio de esgotamento,
        False se há. Ante erro de leitura/página indisponível → True (não bloquear
        uma conta por um fallo de lectura).
        """
        if not self.page:
            return True
        try:
            texto = self.page.evaluate("() => (document.body ? document.body.innerText : '') || ''")
            lower = (texto or "").lower()
        except Exception:
            return True
        frases_sem_creditos = [
            "you've reached your daily limit",
            "insufficient credits",
            "quota exceeded",
            "créditos insuficientes",
            "limite diário",
            "out of credits",
            "no credits remaining",
            "not enough compute credits",
            "créditos esgotados",
            "upgrade to continue",
            "upgrade your plan",
            "ran out of credits",
            "credit limit",
            "daily limit",
            "rate limit",
            "model unavailable",
            "modelo indisponível",
            "indisponível no momento",
            "temporarily unavailable",
        ]
        return not any(f in lower for f in frases_sem_creditos)

    def _extrair_metadados_sessao(self, projeto_id: str = "") -> Tuple[Optional[str], Optional[str]]:
        """Extrai o email da conta Google conectada e o nome do projeto no Flow.

        Roda UMA vez por sessão (antes da fila de geração). O email é obtido
        clicando no avatar e lendo o painel de conta (ver
        _extrair_email_via_painel_conta) e fica cacheado em self.account_email.
        Nome do projeto vem do título da aba (fallback: projeto_id).
        """
        proj_name = None
        if not self.page:
            return self.account_email, self.current_project_name or projeto_id

        try:
            if self.page.is_closed():
                return self.account_email, self.current_project_name or projeto_id
        except Exception:
            return self.account_email, self.current_project_name or projeto_id

        # 1. Extração do E-mail da conta Google (via painel de conta, 1x por sessão)
        if not self.account_email:
            self.account_email = self._extrair_email_via_painel_conta()

        # 2. Extração do Nome do Projeto
        try:
            title = (self.page.title() or "").strip()
            if title and "flow" in title.lower() and (" - " in title or " | " in title):
                clean_title = re.split(r" [-|] ", title)[0].strip()
                if clean_title and clean_title.lower() not in ("google flow", "flow"):
                    proj_name = clean_title
            if not proj_name:
                proj_name = projeto_id or "Projeto Flow"
        except Exception:
            proj_name = projeto_id or "Projeto Flow"

        if proj_name:
            self.current_project_name = proj_name

        return self.account_email, self.current_project_name

    @staticmethod
    def _eh_aba_flow_valida(url: str) -> bool:
        """True apenas para abas do Google Flow (labs.google/flow.google.com).

        Rejeita domínios inválidos que casam genericamente com "flow" na URL,
        como flowmusic.app e similares (abas inesperadas abertas pelo usuário).
        """
        if not url:
            return False
        dominios_aceitos = ["flow.google.com", "labs.google"]
        dominios_rejeitados = [
            "flowmusic.app", "flowmusic", "accounts.google",
            "google.com/signin", "127.0.0.1", "localhost",
        ]
        url_lower = (url or "").lower()
        if any(d in url_lower for d in dominios_rejeitados):
            return False
        return any(d in url_lower for d in dominios_aceitos)

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
            # CORREÇÃO 3: fecha abas indesejadas (flowmusic.app e similares)
            if "flowmusic" in url.lower():
                try:
                    p.close()
                except Exception:
                    pass
                continue
            if target_url and target_url in url and self._eh_aba_flow_valida(url):
                return p
            if "/project/" in url and self._eh_aba_flow_valida(url):
                return p

        # 2. Se tem aba do Flow aberta, reaproveita e navega direto para o projeto
        for p in self.context.pages:
            url = (p.url or "")
            if "flowmusic" in url.lower():
                try:
                    p.close()
                except Exception:
                    pass
                continue
            if self._eh_aba_flow_valida(url):
                if target_url and target_url not in url:
                    try:
                        p.goto(target_url, timeout=60000)
                    except Exception:
                        pass
                return p

        # 3. Se nenhuma aba existir, abre direto na URL do projeto via CDP HTTP PUT
        dest_url = target_url or FLOW_URL
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?{dest_url}", method="PUT")
            urllib.request.urlopen(req, timeout=5)
            time.sleep(1.5)
            for p in self.context.pages:
                url = (p.url or "")
                if "flowmusic" in url.lower():
                    try:
                        p.close()
                    except Exception:
                        pass
                    continue
                if self._eh_aba_flow_valida(url):
                    return p
        except Exception:
            pass

        try:
            nova = self.context.new_page()
            nova.goto(dest_url, timeout=60000)
            return nova
        except Exception:
            return None

    def _garantir_aba_flow(self) -> bool:
        """Garante que self.page aponte para a aba existente do Google Flow.
        NUNCA cria novas abas nem navega abas do ULTRACUT3.
        """
        if not self.context:
            return False
        try:
            if self.page and not self.page.is_closed() and "/project/" in (self.page.url or ""):
                if self._eh_aba_flow_valida(self.page.url or ""):
                    return True
        except Exception:
            self.page = None

        # CORREÇÃO 3: fecha abas indesejadas (flowmusic.app e similares) encontradas
        # no contexto antes de procurar a aba correta do Flow.
        for p in self.context.pages:
            try:
                if not p.is_closed() and "flowmusic" in (p.url or "").lower():
                    try:
                        p.close()
                    except Exception:
                        pass
            except Exception:
                pass
        for p in self.context.pages:
            try:
                if not p.is_closed() and ("/project/" in (p.url or "") and self._eh_aba_flow_valida(p.url or "")):
                    self.page = p
                    return True
            except Exception:
                pass
        for p in self.context.pages:
            try:
                if not p.is_closed() and self._eh_aba_flow_valida(p.url or ""):
                    self.page = p
                    return True
            except Exception:
                pass
        return False

    def _garantir_aba_flow_aberta(self) -> bool:
        """CORREÇÃO 2 (FASE 3) — Garante a aba do Flow aberta, REABRINDO-a se
        tiver sido fechada durante a fila de produção.

        Difere de _garantir_aba_flow() (que apenas PROCURA abas existentes e
        devolve False quando nenhuma está aberta): aqui, se não houver aba
        válida, delegamos a _resolver_aba_flow(), que reabre via CDP HTTP PUT
        (/json/new) ou context.new_page() e já navega para a URL do projeto.
        Isso evita que uma cena falhe com "Google Flow fechado" quando a aba
        foi simplesmente derrubada (crash/acidente do operador), permitindo a
        recuperação automática e o reprocessamento.
        """
        if self._garantir_aba_flow():
            return True
        pw_log("[FLOW] Aba do Flow não encontrada — reabrindo automaticamente...", level="warn")
        try:
            nova = self._resolver_aba_flow()
            if nova is not None:
                try:
                    if not nova.is_closed():
                        self.page = nova
                        pw_log("[FLOW] Aba do Flow reaberta com sucesso.", level="info")
                        return True
                except Exception:
                    pass
            else:
                pw_log("[FLOW] _resolver_aba_flow() não retornou aba válida.", level="warn")
        except Exception as e_reabrir:
            pw_log(f"[FLOW] Erro ao reabrir a aba do Flow: {e_reabrir}", level="error")
        # Última tentativa: revalida abas existentes (pode ter havido corrida)
        return self._garantir_aba_flow()

    def _check_is_active(self) -> bool:
        if not self.page:
            return False
        try:
            if self.page.is_closed():
                self.page = None
                return False
            url = self.page.url or ""
            return bool(url and url != "about:blank" and self._eh_aba_flow_valida(url))
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
            self.context = contexts[0] if contexts else None
            self.page = None

            # Procura aba existente em todos os contextos
            for c in (contexts or []):
                for p in c.pages:
                    try:
                        u = p.url or ""
                        # CORREÇÃO 3: fecha abas indesejadas (flowmusic.app e similares)
                        if "flowmusic" in u.lower():
                            try:
                                p.close()
                            except Exception:
                                pass
                            continue
                        if "/project/" in u and self._eh_aba_flow_valida(u):
                            self.page = p
                            self.context = c
                            break
                        elif self._eh_aba_flow_valida(u) and not self.page:
                            self.page = p
                            self.context = c
                    except Exception:
                        pass
                if self.page and "/project/" in (self.page.url or ""):
                    break

            if self.page is None:
                self.page = self._resolver_aba_flow()

            if self.page is None:
                try:
                    self.page = self.browser.new_page()
                    self.context = self.page.context
                    target_url = carregar_projeto_flow_url(self.current_project_id) if self.current_project_id else FLOW_URL
                    self.page.goto(target_url or FLOW_URL, timeout=60000)
                except Exception as e_np:
                    pw_log(f"Falha ao criar nova página via browser.new_page: {e_np}", level="warn")

            if self.page is None:
                self._encerrar_sessao()
                return False, "Não foi possível resolver a aba do Google Flow."

            try:
                self.page.on("dialog", lambda d: d.dismiss())
            except Exception:
                pass

            self._fechar_modais_bloqueantes()

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

    def _ensure_project_open(self, projeto_id: str, timeout_s: int = 5,
                             conta_id: Optional[Any] = None) -> bool:
        """Garante que a aba Flow esteja no projeto.

        Estratégia PRINCIPAL: ler o project_id DIRETO da URL da aba Chrome aberta
        (labs.google/fx/tools/flow/project/<UUID>). Se a aba já estiver num projeto
        válido, usa imediatamente — sem depender de flow_meta.json nem da galeria
        (a galeria do Flow não expõe nomes nos cards, então busca por nome falha).

        conta_id (ROTAÇÃO DE CONTAS): quando informado, a URL salva é resolvida de
        forma ESTRITA para essa conta (urls_por_conta[conta_id]) e o projeto
        criado/recuperado é salvo indexado para essa conta (1 projeto por conta).
        Sem conta_id mantém o comportamento histórico (flow_project_url legado).
        """
        if not self._garantir_aba_flow():
            return False
        url = self.page.url or ""

        # 1. Extrai o UUID do projeto direto da URL aberta (estratégia principal)
        m_proj = re.search(r"/project/([0-9a-fA-F-]{36})", url)
        if m_proj:
            uuid_aba = m_proj.group(1)
            pw_log(f"[FLOW] URL detectada: labs.google/fx/tools/flow/project/{uuid_aba}")
            # Valida que a página carregou conteúdo real (SPA de GUID inexistente
            # fica preso em "Carregando…", body minúsculo).
            try:
                body_txt = self.page.inner_text("body", timeout=3000).lower()
                pagina_ok = (
                    len(body_txt.strip()) >= 60
                    and "algo deu errado" not in body_txt
                    and "something went wrong" not in body_txt
                )
            except Exception:
                pagina_ok = False
            if pagina_ok:
                pw_log(f"[FLOW] Projeto {uuid_aba} validado e disponível — usando")
                if not getattr(self, "_project_url_saved", False):
                    salvar_projeto_flow_url(projeto_id, url, conta_id=conta_id)
                    self._project_url_saved = True
                return True
            pw_log(f"[FLOW] URL em /project/{uuid_aba} mas página sem conteúdo/erro — recuperando...", level="warn")
        else:
            pw_log("[FLOW] Nenhuma aba de projeto Flow detectada — criando novo")

        if conta_id is not None:
            saved_url = carregar_projeto_flow_url(projeto_id, conta_id=conta_id)
        else:
            saved_url = carregar_projeto_flow_url(projeto_id)
        if saved_url and saved_url != url:
            pw_log(f"Abrindo canvas do projeto salvo: {saved_url}")
            try:
                self.page.goto(saved_url, timeout=30000)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                # Detectar página de erro do Flow
                try:
                    body_text = self.page.inner_text("body", timeout=3000).lower()
                    # Página de GUID inexistente NÃO mostra "algo deu errado": o SPA
                    # carrega o shell normal e fica preso em "Carregando…" (body minúsculo).
                    # Projeto real carregado tem texto substancial (toolbar, menu, etc).
                    pagina_vazia = len(body_text.strip()) < 60
                    tem_erro = (
                        "algo deu errado" in body_text or
                        "something went wrong" in body_text or
                        pagina_vazia or
                        "/project/" not in (self.page.url or "")
                    )
                    if tem_erro:
                        pw_log("[FLOW] Projeto expirado detectado. Navegando para home...", level="warn")
                        self.page.goto("https://labs.google/fx/pt/tools/flow", timeout=30000)
                        try:
                            self.page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        pw_log("[FLOW] Nenhuma aba de projeto Flow detectada — criando novo", level="warn")
                        novo = self.page.locator(
                            'button:has-text("Novo projeto"), '
                            'a:has-text("Novo projeto"), '
                            'button:has-text("New project"), '
                            'button:has(i:has-text("add"))'
                        ).first
                        if novo.is_visible(timeout=10000):
                            novo.click()
                        try:
                            self.page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        nova_url = self.page.url or ""
                        pw_log(f"[FLOW] Novo projeto criado: {nova_url}")
                        salvar_projeto_flow_url(projeto_id, nova_url, conta_id=conta_id)
                        self._project_url_saved = True
                        if "/project/" in nova_url:
                            return True
                    else:
                        if "/project/" in (self.page.url or ""):
                            self._project_url_saved = True
                            return True
                except Exception as e:
                    pw_log(f"[FLOW] Erro ao detectar/recuperar página: {e}", level="error")
            except Exception as e:
                pw_log(f"Falha ao abrir projeto salvo: {e}", level="warn")

        # Se estiver na galeria/dashboard do Flow (labs.google/fx/pt/tools/flow):
        # ROTINA DETERMINÍSTICA — navega para a raiz do Flow, clica em "+ Novo projeto"
        # e aguarda a URL mudar para /project/<id> (substitui o clique em projetos existentes).
        try:
            self._fechar_modais_bloqueantes()
            pw_log("[ENSURE_PROJECT] Navegando para a galeria do Google Flow...")
            self.page.goto("https://labs.google/fx/pt/tools/flow", timeout=30000)
            self.page.wait_for_timeout(1500)

            btn_novo = self.page.locator(
                'button:has-text("Novo projeto"), '
                'button:has-text("+ Novo projeto"), '
                'button:has-text("Criar projeto"), '
                'button:has-text("New project"), '
                'button:has(i:has-text("add"))'
            ).first
            if btn_novo.is_visible(timeout=max(timeout_s, 10) * 1000):
                pw_log("[ENSURE_PROJECT] Clicando em '+ Novo projeto' para abrir o canvas...")
                btn_novo.click()
            else:
                pw_log("[ENSURE_PROJECT] Botão '+ Novo projeto' não encontrado na galeria do Flow.", level="warn")
                return "/project/" in (self.page.url or "")

            # Aguarda a URL mudar para /project/<id>
            url_final = ""
            t0_proj = time.time()
            while time.time() - t0_proj < max(timeout_s, 15):
                url_now = self.page.url or ""
                if "/project/" in url_now:
                    url_final = url_now
                    break
                self.page.wait_for_timeout(500)

            if url_final:
                salvar_projeto_flow_url(projeto_id, url_final, conta_id=conta_id)
                self._project_url_saved = True
                pw_log(f"[ENSURE_PROJECT] Canvas do projeto aberto e salvo: {url_final}")
                # CORREÇÃO 2 — Aguarda o campo de prompt aparecer antes de retornar
                _prompt_sels = [
                    'div.ProseMirror[contenteditable="true"]',
                    '[contenteditable="true"]',
                    '.ProseMirror',
                ]
                _prompt_ok = False
                _t0_prompt = time.time()
                while time.time() - _t0_prompt < 15:
                    for _sel in _prompt_sels:
                        try:
                            if self.page.locator(_sel).first.is_visible(timeout=500):
                                _prompt_ok = True
                                break
                        except Exception:
                            pass
                    if _prompt_ok:
                        break
                    self.page.wait_for_timeout(500)
                if _prompt_ok:
                    pw_log("[ENSURE_PROJECT] Campo de prompt do canvas confirmado.")
                    return True
                pw_log("[ENSURE_PROJECT] ERRO: canvas aberto mas campo de prompt não apareceu em 15s.", level="error")
                return False

            pw_log("[ENSURE_PROJECT] Falha: URL não mudou para /project/ após clicar em '+ Novo projeto'.", level="warn")
            return False
        except Exception as e_dash:
            pw_log(f"[ENSURE_PROJECT] Erro na rotina determinística de abertura de projeto: {e_dash}", level="warn")
            return "/project/" in (self.page.url or "")

    def _upload_avatar_projeto(self, projeto_id: str) -> bool:
        """Faz upload do avatar/referência do projeto para a galeria do workspace do Flow.

        Roda UMA única vez por sessão (flag self._avatar_uploaded).
        Se o projeto não tiver avatar local, apenas loga aviso e segue SEM travar a fila.
        """
        if getattr(self, "_avatar_uploaded", False):
            return True
        try:
            import services.character_service as character_svc
        except Exception:
            return False

        caminho = character_svc.resolver_imagem_avatar_projeto(projeto_id)
        if not caminho or not Path(caminho).exists():
            pw_log(f"[AVATAR_UPLOAD] Nenhum avatar local para o projeto '{projeto_id}' — seguindo sem avatar.")
            self._avatar_uploaded = True
            return False

        arquivo = Path(caminho)
        nome = arquivo.name
        pw_log(f"[AVATAR_UPLOAD] Enviando avatar '{nome}' para o workspace do projeto '{projeto_id}'...")

        if not self.page:
            pw_log("[AVATAR_UPLOAD] Sem aba do Flow ativa — upload do avatar ignorado.", level="warn")
            return False

        # Verifica se reference.png já existe na galeria antes de fazer upload
        if self.page:
            try:
                res_chk = self.page.evaluate(JS_FETCH_MEDIA_LIST)
                if res_chk and res_chk.get("ok"):
                    lista_galeria = res_chk.get("media", [])
                    arquivos_existentes = [item.get("name", "") or str(item.get("id", "")) for item in lista_galeria]
                    if any("reference.png" in str(n).lower() or nome.lower() in str(n).lower() for n in arquivos_existentes):
                        pw_log("[AVATAR_UPLOAD] reference.png já existe na galeria — pulando upload.")
                        print(f"[AVATAR_UPLOAD] '{nome}' já existe na galeria, pulando upload.", flush=True)
                        self._avatar_uploaded = True
                        return True
            except Exception as _e_chk:
                pw_log(f"[AVATAR_UPLOAD] Aviso ao verificar galeria: {_e_chk}", level="warn")

        try:
            # (a) Envia a mídia: input[type="file"] direto (camada 1) OU botão de envio
            #     'Enviar mídia'/'Adicionar mídia'/'Fazer upload'/'Add image'/'Upload' (camada 2).
            input_file = self.page.locator('input[type="file"]').first
            if input_file.count() > 0:
                input_file.set_input_files(caminho)
                self.page.wait_for_timeout(2500)
            else:
                btn_env = self.page.locator(
                    'button:has-text("Enviar mídia"), '
                    'button:has-text("Adicionar mídia"), '
                    'button:has-text("Fazer upload"), '
                    'button[aria-label*="Add image" i], '
                    'button[aria-label*="Upload" i], '
                    'button:has(i:has-text("add"))'
                ).first
                if btn_env.is_visible(timeout=3000):
                    with self.page.expect_file_chooser(timeout=4000) as fc_info:
                        btn_env.click(force=True)
                    fc_info.value.set_files(caminho)
                    self.page.wait_for_timeout(2500)
                else:
                    pw_log("[AVATAR_UPLOAD] Botão de envio de mídia não encontrado no canvas.", level="warn")
                    self._avatar_uploaded = True
                    return False

            # (c) Aguarda a imagem aparecer na galeria do workspace (polling via JS_FETCH_MEDIA_LIST)
            apareceu = False
            t0_av = time.time()
            while time.time() - t0_av < 15:
                try:
                    res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
                    if res and res.get("ok"):
                        nomes = [str(m.get("name") or m.get("id") or "") for m in res.get("media", [])]
                        if nomes and any(nome.lower() in n.lower() for n in nomes):
                            apareceu = True
                            break
                except Exception:
                    pass
                self.page.wait_for_timeout(1000)

            # (d) Log de sucesso com o nome do arquivo
            self._avatar_uploaded = True
            if apareceu:
                pw_log(f"[AVATAR_UPLOAD] Avatar '{nome}' enviado e visível na galeria do workspace.")
            else:
                pw_log(f"[AVATAR_UPLOAD] Avatar '{nome}' enviado (galeria ainda não confirmada no polling).")
            return True
        except Exception as e:
            pw_log(f"[AVATAR_UPLOAD] Falha ao enviar avatar '{nome}': {e}", level="warn")
            self._avatar_uploaded = True  # não repete na mesma sessão
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

    def _set_output_mode(
        self,
        target_mode: str = "image",
        modelo_solicitado: Optional[str] = None,
        proporcao_solicitada: Optional[str] = "16:9",
        qualidade_solicitada: Optional[str] = "x1"
    ):
        """Configura a proporção, qualidade, contagem e modelo no Flow.

        Retorna True quando o Flow foi deixado no modo/modelo/qualidade pedidos
        (ou já estava nesse estado) e False quando a reconfiguração falhou.
        """
        if not self.page:
            return False
        modelo_alvo = modelo_solicitado or self.current_model or ("Veo 3.1 - Lite" if target_mode == "video" else "Nano Banana Pro")
        prop_alvo = proporcao_solicitada or "16:9"
        qtd_alvo = qualidade_solicitada or "x1"
        if getattr(self, "_configured_mode", None) == (target_mode, modelo_alvo, prop_alvo, qtd_alvo):
            return True
        pw_log(f"[FLOW] Reconfigurando para modo {target_mode} ({modelo_alvo}, {prop_alvo}, {qtd_alvo})...")

        try:
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
                'button:has-text("Imagen")',
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

            # 3. Garante proporção selecionada (16:9, 4:3, 1:1, 9:16)
            tab_prop = self.page.locator(f'button[role="tab"]:has-text("{prop_alvo}")').first
            if tab_prop.is_visible(timeout=600) and tab_prop.get_attribute("aria-selected") != "true":
                tab_prop.click()
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
                if target_mode == "video" or "veo" in modelo_alvo.lower():
                    opcoes.extend([
                        'div[role="menuitem"]:has-text("Veo 3.1 - Lite")',
                        'div[role="menuitem"]:has-text("Veo 3.1 - Quality")',
                        'div[role="menuitem"]:has-text("Veo 3.1")',
                        'div[role="menuitem"]:has-text("Veo 3")',
                        'div[role="menuitem"]:has-text("Veo")',
                        '[role="option"]:has-text("Veo 3.1 - Lite")',
                        '[role="option"]:has-text("Veo 3.1")',
                        '[role="option"]:has-text("Veo 3")',
                        '[role="option"]:has-text("Veo")',
                    ])
                elif "imagen 4 ultra" in modelo_alvo.lower():
                    opcoes.extend([
                        'div[role="menuitem"]:has-text("Imagen 4 Ultra")',
                        '[role="option"]:has-text("Imagen 4 Ultra")',
                        '[role="option"]:has-text("Ultra")'
                    ])
                elif "imagen 4" in modelo_alvo.lower() or "imagen" in modelo_alvo.lower():
                    opcoes.extend([
                        'div[role="menuitem"]:has-text("Imagen 4")',
                        '[role="option"]:has-text("Imagen 4")'
                    ])
                elif "2" in modelo_alvo:
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

            # 5. Garante contagem / quantidade (x1, x2, x3, x4)
            btn_qtd = self.page.locator(f'button[role="tab"]:has-text("{qtd_alvo}"), button:has-text("{qtd_alvo}")').first
            if btn_qtd.is_visible(timeout=500) and btn_qtd.get_attribute("aria-selected") != "true":
                btn_qtd.click()
                self.page.wait_for_timeout(200)

            # 6. Fecha o menu com Escape e devolve foco
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(100)
            self._configured_mode = (target_mode, modelo_alvo, prop_alvo, qtd_alvo)
            return True
        except Exception as e:
            pw_log(f"Aviso em _set_output_mode ({target_mode}): {e}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def _detectar_erro_ou_limite_modelo(self, video_mode: bool = False) -> Optional[str]:
        """Detecta se o modelo atual atingiu limite diário ou quota ou está indisponível.

        video_mode=True → chamado durante geração de VÍDEO (animação). Quando os
        créditos de vídeo esgotam, NÃO rotaciona a conta: ativa o fallback video→imagem
        (self._fallback_video_para_imagem) e retorna "credito_esgotado_video".
        video_mode=False → créditos esgotados no modo imagem: rotaciona a conta e
        retorna "credito_esgotado".
        """
        if not self.page:
            return None
        try:
            # Créditos esgotados na conta ativa -> rotação automática para a próxima conta
            texto_pagina = self.page.evaluate("() => (document.body ? document.body.innerText : '') || ''")
            texto_lower = (texto_pagina or "").lower()
            # CORREÇÃO 1 — todas as variações conhecidas de crédito/quota do Google Flow.
            # Ao detectar QUALQUER uma delas:
            #   - modo VÍDEO  → ativa fallback para IMAGEM, sem rotacionar conta
            #   - modo IMAGEM → rotaciona a conta e retorna SEMPRE "credito_esgotado".
            frases_credito = [
                "you've reached your daily limit",
                "insufficient credits",
                "quota exceeded",
                "créditos insuficientes",
                "limite diário",
                "out of credits",
                "no credits remaining",
                "not enough compute credits",
                "créditos esgotados",
                "upgrade to continue",
                "upgrade your plan",
                "ran out of credits",
                "credit limit",
            ]
            if any(f in texto_lower for f in frases_credito):
                # CORREÇÃO BURACO 1 — la lógica de flag/rotación es ÚNICA para
                # AMBAS detecciones (frases_credito y selectores JS/toasts).
                return self._tratar_indicador_limite("frases_credito", video_mode)
        except Exception:
            pass
        try:
            # BURACO 1 (CORREÇÃO) — la alerta del Flow puede aparecer SÓLO en un selector
            # JS (toast/banner/[role=alert]) sin llegar a document.body.innerText. Antes,
            # este bloque retornaba el string CRUDO del indicador (ej: "limite de geração")
            # y el caller lo descartaba en silencio — _fallback_video_para_imagem NUNCA se
            # setaba. Ahora se captura el indicador y se procesa por la MISMA vía que
            # frases_credito (helper _tratar_indicador_limite).
            indicador_toast = self.page.evaluate('''() => {
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
            if indicador_toast:
                pw_log(f"[FLOW] Indicador de limite por TOAST/SELETOR JS detectado: {indicador_toast!r}", level="warn")
                return self._tratar_indicador_limite(indicador_toast, video_mode)
            return None
        except Exception:
            return None

    def _persistir_alerta_creditos(self, fallback: bool):
        """Salva/limpia el estado de alerta de créditos en meta.json del proyecto.

        PHASE 2 (ERRO 2): cuando se activa el fallback video→imagen se persiste
        {alerta_credito: {fallback_video, timestamp, mensaje}} para que la API
        de status y la UI (banner) lo muestren. Al iniciar una fila nueva se
        limpia el campo para no dejar el aviso stale.
        """
        pid = getattr(self, "current_project_id", None) or None
        if not pid:
            return
        try:
            meta_path = PROJETOS_DIR / pid / "meta.json"
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8")) or {}
            else:
                meta = {}
            alerta = meta.get("alerta_credito") or {}
            alerta["fallback_video"] = bool(fallback)
            if fallback:
                alerta["todas_esgotadas"] = True
                alerta["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                alerta["mensaje"] = "Todos los créditos de vídeo se agotaron. Continuando con modo imagen."
            else:
                alerta["todas_esgotadas"] = False
                alerta.pop("timestamp", None)
                alerta.pop("mensaje", None)
            meta["alerta_credito"] = alerta
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as _e_alerta:
            pw_log(f"[FLOW] Aviso al persisitir alerta de créditos en meta.json: {_e_alerta}", level="warn")

    def _tratar_indicador_limite(self, indicador: str, video_mode: bool) -> str:
        """Procesa un indicador de límite/quota detectado (frases_credito O toast/seletor JS)
        y devuelve SIEMPRE un código canónico.

        BURACO 1 (CORRECCIÓN PARTE 5) — fuente ÚNICA de la lógica de fallback:
          - PARTE 6: ANTES de cualquier fallback, intenta ROTAR a otra cuenta con
            créditos (auto-rotación inteligente). Si no hay otra cuenta disponible:
          - video_mode=True  → setea _fallback_video_para_imagem y retorna
            "credito_esgotado_video".
          - video_mode=False → rota a la próxima cuenta y retorna "credito_esgotado".

        Garantiza que AMBAS detecciones (texto de página y toasts) seteen la flag antes
        de retornar, para que el caller (líneas ~2388-2392) SIEMPRE reciba un código
        reconocido por los `if err_limite == ...`.
        """
        # PARTE 6 — AUTO-ROTAÇÃO INTELIGENTE: consulta o gerenciador de contas
        # (reset diário + próxima conta com créditos) antes de cair no fallback.
        proxima_conta = None
        try:
            from services.flow_account_manager import FlowAccountManager
            proxima_conta = FlowAccountManager().proxima_conta_disponivel()
        except Exception as _e_conta:
            pw_log(f"[FLOW] Aviso ao consultar contas para rotação: {_e_conta}", level="warn")

        if proxima_conta:
            pw_log(f"[FLOW] Créditos esgotados (indicador: {indicador!r}). Rotacionando para {proxima_conta}...", level="warn")
            self._rotacionar_conta()
            return "credito_esgotado"

        if video_mode:
            pw_log(f"[FLOW] Créditos de VÍDEO esgotados e nenhuma conta com créditos (indicador: {indicador!r}). Ativando fallback video→imagem...", level="warn")
            self._fallback_video_para_imagem = True
            self._persistir_alerta_creditos(fallback=True)
            return "credito_esgotado_video"
        pw_log(f"[FLOW] Créditos esgotados e nenhuma conta com créditos (indicador: {indicador!r}).", level="warn")
        self._rotacionar_conta()
        return "credito_esgotado"

    def _rotacionar_conta(self, _depth: int = 0):
        """Marca a conta atual como esgotada e ativa a próxima conta disponível.

        RESET TOTAL (1 projeto por conta):
          1. Persiste em config/flow_accounts.json (conta atual esgotada, ativa a
             próxima) e migra a URL legada do projeto para a conta que a criou.
          2. Reinicia o Chrome CDP com o perfil da nova conta (force_restart).
          3. Reconecta a sessão Playwright (encerra a anterior e reconecta) e limpa
             o estado cacheado da conta ANTERIOR (email, nome, avatar, project saved).
          4. NUNCA navega à URL da conta antiga: resolve a URL específica da NOVA
             conta (flow_meta.json -> urls_por_conta[conta_id]). Se a conta nova não
             tem projeto próprio, CRIA um projeto novo na galeria (1 projeto por conta).
          5. REPROVISIONA o personagem: se as cenas usam personagem, recria '@Nome'
             via criar_personagem_flow() e marca _avatar_uploaded=True.
          6. VERIFICAÇÃO DE SESSÃO (CORREÇÃO 4): checa a sessão Google da conta nova
             pelos cookies CDP (_verificar_sessao_google). Sem sessão, a conta é
             marcada como creditos_esgotados=True e a rotação continua na PRÓXIMA
             conta (recursão limitada por MAX_PROFUNDIDADE_ROTACAO); na profundidade
             máxima, a cena atual é marcada STATUS_ERRO e a fila é PARADA.

        Usa pw_log (o wrapper de log_event) porque a classe PlaywrightCDPWorker não
        possui atributo self.logger.
        """
        import json
        MAX_PROFUNDIDADE_ROTACAO = 3  # CORREÇÃO 4 — limite de profundidade da recursão
        accounts_path = Path("config/flow_accounts.json")
        if not accounts_path.exists():
            pw_log("[FLOW] config/flow_accounts.json não encontrado — sem rotação.", level="warn")
            return
        try:
            accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
        except Exception as e:
            pw_log(f"[FLOW] Erro ao ler config/flow_accounts.json na rotação: {e}", level="error")
            return
        contas = accounts.get("contas", [])
        if not contas:
            return

        # 1. Marca a conta atual como esgotada (guarda o id p/ migrar a URL legada)
        conta_anterior_id = None
        for c in contas:
            if c.get("ativa"):
                conta_anterior_id = c.get("id")
                c["creditos_esgotados"] = True
                c["esgotado_em"] = datetime.now().isoformat()
                c["ativa"] = False
                break

        # 1.1 Migração: indexa a URL legada à conta que a criou (1 projeto por conta)
        if conta_anterior_id is not None and self.current_project_id:
            try:
                _meta_proj = _flow_meta_data(self.current_project_id)
                _urls_proj = _meta_proj.setdefault("urls_por_conta", {}) or {}
                _url_legada = _meta_proj.get("flow_project_url")
                if _url_legada and str(conta_anterior_id) not in _urls_proj:
                    _urls_proj[str(conta_anterior_id)] = _url_legada
                    _flow_meta_path(self.current_project_id).write_text(
                        json.dumps(_meta_proj, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
            except Exception as _e_mig:
                pw_log(f"[FLOW] Aviso ao indexar URL legada por conta na rotação: {_e_mig}", level="warn")

        # 2. Ativa a próxima conta disponível
        proxima = next(
            (c for c in contas
             if not c.get("creditos_esgotados") and not c.get("ativa")),
            None
        )
        if proxima:
            proxima["ativa"] = True
        # CORREÇÃO 1 — o write_text foi MOVIDO para ANTES do "if not proxima: return".
        # A marcação creditos_esgotados=True / ativa=False do passo 1 existe apenas em
        # memória; sem gravar aqui, o caminho "todas as contas esgotadas" descartava a
        # marcação no return e o config/flow_accounts.json permanecia com a conta já
        # esgotada marcada como ativa e com créditos — a rotação se repetia inutilmente
        # a cada ciclo (e o disco nunca registrava o esgotamento).
        try:
            accounts_path.write_text(
                json.dumps(accounts, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            pw_log(f"[FLOW] Erro ao salvar config/flow_accounts.json na rotação: {e}", level="error")
            return
        if not proxima:
            pw_log("[FLOW] Todas as contas com créditos esgotados.", level="error")
            return
        proxima_id = proxima.get("id")
        pw_log(f"[FLOW] Alternando para: {proxima['nome']} (id={proxima_id})", level="info")

        # 3. Reinicia o Chrome com o perfil da nova conta
        ensure_chrome_cdp(self.port, force_restart=True)

        try:
            # 4. Encerra a sessão da conta ANTERIOR e reconecta à nova instância
            self._encerrar_sessao()
            ok_recon, msg_recon = self._iniciar_sessao_thread()
            if not ok_recon:
                pw_log(f"[FLOW] Reconexão pós-rotação falhou: {msg_recon}", level="error")
                return

            # 4.1 Limpa o estado cacheado da conta ANTERIOR (RESET TOTAL)
            self.account_email = None
            self.current_project_name = None
            self._project_url_saved = False
            self._avatar_uploaded = False  # a conta nova não tem o avatar da conta velha

            # 5. NUNCA navega à URL da conta antiga — resolve o projeto da NOVA conta:
            #    urls_por_conta[conta_id] (se existir) OU cria um projeto NOVO na conta.
            if self.current_project_id:
                _url_conta_nova = carregar_projeto_flow_url(
                    self.current_project_id, conta_id=proxima_id
                )
                if _url_conta_nova and self.page:
                    try:
                        self.page.goto(_url_conta_nova, timeout=45000)
                        try:
                            self.page.wait_for_load_state("networkidle", timeout=20000)
                        except Exception:
                            pass
                        self._project_url_saved = True
                        pw_log(f"[FLOW] Projeto da conta {proxima_id} reutilizado: {_url_conta_nova}")
                    except Exception as e_nav:
                        pw_log(f"[FLOW] Aviso ao navegar ao projeto da conta {proxima_id}: {e_nav}", level="warn")
                elif self.page:
                    # Conta nova SEM projeto próprio: cria um projeto novo na galeria
                    pw_log(f"[FLOW] Conta {proxima_id} sem projeto próprio — criando projeto novo na conta...", level="info")
                    if not self._ensure_project_open(self.current_project_id, timeout_s=12,
                                                     conta_id=proxima_id):
                        pw_log("[FLOW] Falha ao criar projeto novo na conta recém-ativada (a fila continua).", level="warn")
            if self.page:
                self.page.wait_for_timeout(3000)

            # CORREÇÃO 4 — VERIFICAÇÃO DE SESSÃO PÓS-ROTAÇÃO (cookies CDP).
            # O perfil da conta recém-ativada pode NÃO ter sessão Google válida
            # (login nunca feito ou expirado): nesse caso o Flow abre deslogado e a
            # fila trava cena após cena. Verificamos a sessão pelos cookies de
            # accounts.google.com (mais rápido/confiável que clicar no avatar). Sem
            # sessão: marca a conta atual como esgotada, persiste em disco e tenta a
            # PRÓXIMA conta (recursão limitada por MAX_PROFUNDIDADE_ROTACAO — nunca
            # loop infinito). Na profundidade máxima: cena atual vira STATUS_ERRO e a
            # fila é PARADA (stop_requested).
            if not self._verificar_sessao_google():
                pw_log("Conta rotacionada sem sessão Google ativa — pulando", level="error")
                proxima["creditos_esgotados"] = True
                proxima["esgotado_em"] = datetime.now().isoformat()
                try:
                    accounts_path.write_text(
                        json.dumps(accounts, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                except Exception as _e_sessao:
                    pw_log(f"[FLOW] Falha ao marcar conta {proxima_id} como esgotada: {_e_sessao}",
                           level="warn")
                if _depth >= MAX_PROFUNDIDADE_ROTACAO:
                    # Profundidade máxima atingida: marca a cena atual como ERRO e PARA a fila.
                    _proj_rot = self.current_project_id
                    _cid_rot = int((self.cena_ativa or {}).get("scene_id") or 0)
                    if _proj_rot and _cid_rot:
                        try:
                            scene_plan_svc.atualizar_cena(_proj_rot, _cid_rot, {
                                "status": scene_plan_svc.STATUS_ERRO,
                                "erro_msg": "Conta rotacionada sem sessão Google ativa",
                            })
                        except Exception as _e_erro_rot:
                            pw_log(f"[FLOW] Aviso ao marcar cena {_cid_rot} como ERRO após "
                                   f"esgotar rotações: {_e_erro_rot}", level="warn")
                    pw_log(f"[FLOW] Profundidade máxima de rotação ({MAX_PROFUNDIDADE_ROTACAO}) "
                           f"atingida sem sessão Google válida — parando a fila.", level="error")
                    self.stop_requested.set()
                    return
                self._rotacionar_conta(_depth + 1)
                return

            # 6. REPROVISIONA o personagem na conta nova (se as cenas usam personagem)
            self._reprovisionar_personagem_apos_rotacao()

            pw_log("[FLOW] Reconexão completa após rotação de conta (browser/context/page reestabelecidos).")
        except Exception as e_rec:
            pw_log(f"[FLOW] Erro ao reconectar após rotação de conta: {e_rec}", level="error")
            return

    def _reprovisionar_personagem_apos_rotacao(self,
                                               projeto_id: Optional[str] = None,
                                               forcar_criacao: bool = True) -> bool:
        """Garante/provisiona o personagem do projeto na conta ativa do Google Flow.

        ANTIGRAVITY #2 — usado em DOIS momentos:
          * Na ROTAÇÃO de contas (_rotacionar_conta): a biblioteca da conta nova está
            vazia → `forcar_criacao=True` (default) cria '@Nome' SEMPRE.
          * No INÍCIO da fila (_handle_run_queue, pré-voo): com `forcar_criacao=False`
            só cria quando o personagem realmente NÃO existe (verificação estrita,
            sem falso positivo).

        A biblioteca de personagens do Google Flow é POR CONTA — após a rotação a aba
        Personagens da conta nova está vazia. Se o projeto usa personagem nas cenas e
        há identidade local configurada, cria '@Nome' via criar_personagem_flow()
        (que valida no popup '@' antes de retornar True) e marca _avatar_uploaded=True.

        Falha NUNCA bloqueia a fila: loga aviso e segue (cenas que precisarem do
        personagem e não conseguirem anexá-lo são marcadas erro individualmente,
        comportamento já existente).
        """
        _projeto = projeto_id or self.current_project_id
        if not _projeto or not self.page:
            return False
        try:
            _plan = scene_plan_svc.carregar_scene_plan(_projeto)
            _cenas = (_plan or {}).get("cenas", []) if isinstance(_plan, dict) else []
        except Exception:
            _cenas = []
        if not self._cenas_usam_personagem(_cenas):
            return False
        try:
            import services.character_service as character_svc
        except Exception as _e_imp:
            pw_log(f"[FLOW] Reprovisão personagem: módulo character_service indisponível: {_e_imp}", level="warn")
            return False

        _nome_char = ""
        try:
            _idt = character_svc.obter_identidade_projeto(_projeto) or {}
            _nome_char = str(_idt.get("nome") or "").strip()
        except Exception as _e_idt:
            pw_log(f"[FLOW] Reprovisão personagem: erro ao ler identidade local: {_e_idt}", level="warn")
        if not _nome_char:
            pw_log("[FLOW] Reprovisão personagem: projeto sem personagem configurado.", level="warn")
            return False

        # CORREÇÃO 3: nome pode já chegar com '@' — un único '@' en logs.
        _nome_exhib = f"@{str(_nome_char or '').lstrip('@')}"

        _foto = ""
        try:
            _foto = character_svc.resolver_imagem_avatar_projeto(_projeto) or ""
        except Exception:
            _foto = ""
        if not _foto or not Path(_foto).exists():
            pw_log(f"[FLOW] Reprovisão personagem: foto de referência local não encontrada para '{_nome_exhib}'.", level="warn")
            return False

        if not forcar_criacao:
            # Início de fila: só cria se o personagem realmente não existir (verificação
            # estrita — _verificar_personagem_na_biblioteca sem falso positivo).
            try:
                if self._verificar_personagem_na_biblioteca(_nome_char):
                    pw_log(f"[PRE_VOO] Personagem '{_nome_exhib}' já existe na biblioteca — reprovisão desnecessária.")
                    self._avatar_uploaded = True
                    return True
            except Exception:
                pass

        try:
            _criado = criar_personagem_flow(self.page, _nome_char, _foto)
        except Exception as _e_criar:
            pw_log(f"[FLOW] Reprovisão personagem: falha ao criar '{_nome_exhib}' no Flow: {_e_criar}", level="warn")
            _criado = False
        if _criado:
            pw_log(f"[PRE_VOO] Personagem @{_nome_char} criado automaticamente")
        else:
            pw_log(f"[PRE_VOO] Personagem @{_nome_char} NÃO pôde ser criado automaticamente (a fila continua; cenas com personagem podem falhar individualmente).", level="warn")

        # Requisito: após o provisionamento o avatar da conta ativa já é o do personagem
        self._avatar_uploaded = True
        return bool(_criado)

    def _fechar_modais_bloqueantes(self):
        """Fecha modais intrusivos, banners de novidades e termos do Google que possam bloquear o editor."""
        if not self.page:
            return
        try:
            self.page.evaluate('''() => {
                const dismissBtns = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]'));
                const termos = ['got it', 'entendi', 'dismiss', 'fechar', 'close', 'não agora', 'not now', 'ok', 'accept', 'aceitar', 'pular', 'skip'];
                for (const b of dismissBtns) {
                    const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (termos.includes(txt)) {
                        const isInsideEditor = b.closest('[data-slate-editor="true"]') || b.closest('div[role="dialog"]');
                        if (!isInsideEditor && b.offsetWidth > 0 && b.offsetHeight > 0) {
                            b.click();
                        }
                    }
                }
            }''')
        except Exception:
            pass

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
        # 3. Remove prefixos técnicos (Prompt:, Visual:, Image:, Cena X:, Cinematic visual depicting)
        prompt = re.sub(r'^(?:Prompt(?:\s*Visual)?|Visual|Image|Cena\s*\d+)\s*:\s*', '', prompt, flags=re.IGNORECASE)
        prompt = re.sub(r'^(?:Cinematic\s+visual\s+depicting\s+)+', '', prompt, flags=re.IGNORECASE)
        # 4. Remove menções textuais redundantes do @Nome APENAS se o chip nativo foi inserido (strip_character_tag=True)
        if strip_character_tag:
            if ref_tag:
                ref_clean = ref_tag.lstrip("@")
                prompt = re.sub(r'^@?' + re.escape(ref_tag) + r'\s*', '', prompt, flags=re.IGNORECASE)
                prompt = re.sub(r'^@?' + re.escape(ref_clean) + r'\s*', '', prompt, flags=re.IGNORECASE)
            prompt = re.sub(r'^@[\w\.\-]+\s*', '', prompt).strip()
            # Sanitiza pontuações residuais no início (ex: ", walking in garden")
            prompt = re.sub(r'^[,\s\.\-:]+', '', prompt).strip()
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
                    # v0.4.0: usa mediaKey (UUID estável) como chave PRIMÁRIA.
                    if item.get("mediaKey"):
                        keys.add(item["mediaKey"])
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

    def _tentar_upscale_2k(self) -> bool:
        """Tenta aplicar '2K Upscaled' na imagem recém-gerada no Google Flow.

        O menu de download do Flow oferece '1K Original size' / '2K Upscaled' /
        '4K'. Se a config do projeto define prod_qualidade_download='2K', esta
        rotina localiza o card/imagem ativa e clica na opção de upscale.
        Retorna True se a interação foi realizada; False se os seletores não
        foram encontrados (neste caso o download segue em 1K com aviso).
        """
        if not self.page:
            return False
        try:
            # 1. Localiza o card da imagem mais recente (galeria de resultados do Flow)
            alvo_card = None
            for sel_card in [
                ".image-container:last-of-type img",
                ".generated-image:last-of-type",
                "img[class*='result']:last-of-type",
                "img:not([class*='avatar']):last-of-type",
            ]:
                try:
                    loc = self.page.locator(sel_card).first
                    if loc.is_visible(timeout=800):
                        alvo_card = loc
                        break
                except Exception:
                    pass
            if alvo_card is None:
                pw_log("[UPSCALE_2K] Card da imagem gerada não encontrado — mantendo 1K.", level="warn")
                return False

            # 2. Clica no card para abrir o menu de download/opções
            try:
                alvo_card.click(timeout=2000)
                self.page.wait_for_timeout(800)
            except Exception:
                try:
                    alvo_card.click(force=True, timeout=2000)
                    self.page.wait_for_timeout(800)
                except Exception:
                    pw_log("[UPSCALE_2K] Não foi possível clicar no card da imagem.", level="warn")
                    return False

            # 3. Procura e clica em '2K Upscaled' / '2K' (menu pode estar em overlay/dialog)
            upscale_clicado = False
            for sel_op in [
                'button:has-text("2K Upscaled")',
                'button:has-text("2K")',
                'div[role="menuitem"]:has-text("2K Upscaled")',
                '[role="option"]:has-text("2K Upscaled")',
                '[role="option"]:has-text("2K")',
                'flow-menu-item:has-text("2K")',
            ]:
                try:
                    op = self.page.locator(sel_op).first
                    if op.is_visible(timeout=600):
                        op.click(timeout=1500)
                        upscale_clicado = True
                        break
                except Exception:
                    continue

            # 4. Fecha menu/overlay se ainda estiver aberto
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

            if upscale_clicado:
                pw_log("[UPSCALE_2K] Opção '2K Upscaled' clicada — aguardando nova renderização...")
                self.page.wait_for_timeout(2500)
                return True
            pw_log("[UPSCALE_2K] Opção '2K Upscaled' não encontrada no menu — mantendo 1K.", level="warn")
            return False
        except Exception as e:
            pw_log(f"[UPSCALE_2K] Erro ao tentar upscale: {e}", level="warn")
            return False

    def _verificar_personagem_na_biblioteca(self, nome_personagem: str) -> bool:
        """PRÉ-VOO (uma única vez por fila): confere se o personagem consta na
        aba 'Personagens' REAL do Google Flow, abrindo o popup '@'.

        Mecânica reaproveitada de _garantir_personagem_criado_no_flow (parte 1),
        porém SOMENTE VERIFICAÇÃO: NÃO cria recurso, NÃO anexa chip, NÃO envia
        prompt e NÃO grava identidade.json — cadastro é manual do usuário.
        Retorna True se encontrou; False caso contrário (popup ausente, aba
        vazia, campo indisponível ou erro de automação).
        """
        if not self.page or not nome_personagem:
            return False

        # CORREÇÃO 1 — seletor do editor atualizado para ProseMirror (mesmo padrão de
        # _selecionar_referencia_flow). Fallback: se ProseMirror não encontrar, tenta o
        # seletor legado data-slate antes de retornar False.
        editor = None
        for sel_editor in [
            'div.ProseMirror[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div.ProseMirror[contenteditable="true"]',
            'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[role="textbox"][contenteditable="true"]:not(aside *)',
            '[contenteditable="true"]:not(aside *)',
        ]:
            loc_ed = self.page.locator(sel_editor).first
            try:
                if loc_ed.is_visible(timeout=800):
                    editor = loc_ed
                    break
            except Exception:
                continue
        if not editor:
            pw_log(f"PRE-VOO PERSONAGEM: campo de prompt não encontrado para checar '@{nome_personagem}'.", level="warn")
            return False

        try:
            self._safe_click(editor)
            self.page.wait_for_timeout(150)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(100)

            # CORREÇÃO 2 — abre o menu de personagens pelo BOTÃO FÍSICO de adicionar
            # ingredientes (mesmo método de _selecionar_referencia_flow / incluir_referencia_personagem),
            # mais confiável que digitar '@' no ProseMirror. Depois navega para a aba Characters.
            btn_mais = None
            for sel_mais in [
                'button[aria-label="Add ingredients to the prompt box"]',
                'button[aria-label*="Add ingredients" i]',
                'button.add-menu-trigger',
                'button[aria-label*="ingredient" i]',
                'button[aria-label="Add image"]',
                'button[aria-label*="Add image" i]',
            ]:
                try:
                    loc_mais = self.page.locator(sel_mais).first
                    if loc_mais.is_visible(timeout=800):
                        btn_mais = loc_mais
                        break
                except Exception:
                    continue

            dialog = None
            if btn_mais:
                try:
                    btn_mais.click()
                    self.page.wait_for_timeout(700)
                    # Aguarda o menu/overlay/dialog abrir (Angular CDK Overlay, popover ou dialog)
                    for d_sel in [
                        'div.cdk-overlay-pane:has([role="menu"])',
                        'div.cdk-overlay-pane:has(.flow-add-menu-popover-content)',
                        '.flow-add-menu-popover-content',
                        'div.cdk-overlay-pane',
                        'div[role="dialog"]',
                        'div[role="menu"]',
                    ]:
                        try:
                            loc_d = self.page.locator(d_sel).first
                            if loc_d.is_visible(timeout=1200):
                                dialog = loc_d
                                break
                        except Exception:
                            continue
                except Exception:
                    dialog = None

            # Fallback legado: se o botão físico não abriu o menu, digita '@'
            if dialog is None:
                try:
                    editor.focus()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.wait_for_timeout(100)
                    self.page.keyboard.type("@", delay=60)
                    self.page.wait_for_timeout(700)
                except Exception:
                    pass

            # Localiza o dialog do menu (se ainda não localizado via botão)
            if dialog is None:
                dialog = self.page.locator('div[role="dialog"], div.cdk-overlay-pane').first

            if not dialog.is_visible(timeout=2000):
                pw_log("PRE-VOO PERSONAGEM: menu/popup de personagens não abriu.", level="warn")
                return False

            # Navega para a aba Characters/Personagens (texto em PT ou EN)
            tab_pers = dialog.locator('button[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Characters"), button:has-text("Personagens"), button:has-text("Characters")').first
            if tab_pers.is_visible(timeout=1000):
                self._safe_click(tab_pers)
                self.page.wait_for_timeout(400)
            else:
                # Fallback por texto (cobre abas que não usam role=tab)
                try:
                    dialog.evaluate('''el => {
                        const tabs = Array.from(el.querySelectorAll("button[role=tab], button, div[role=button], [class*='tab']"));
                        const target = tabs.find(b => {
                            const t = (b.textContent || '').trim().toLowerCase();
                            return t === 'characters' || t === 'personagens' || t === 'character' || t === 'personagem';
                        });
                        if (target) target.click();
                    }''')
                    self.page.wait_for_timeout(400)
                except Exception:
                    pass

            # Verifica se o personagem especifico ou qualquer card de personagem existe na aba
            loc_termos = [
                f'[role="option"]:has-text("{nome_personagem}")',
                f'[role="button"]:has-text("{nome_personagem}")',
                f'div:has-text("{nome_personagem}")',
                f'span:has-text("{nome_personagem}")',
                f'img[alt*="{nome_personagem}" i]',
            ]
            encontrado = False
            nome_limpo = str(nome_personagem or "").strip().lstrip("@")
            # ANTIGRAVITY #1 — PRÉ-VOO SEM FALSO POSITIVO: só considera ENCONTRADO
            # quando o NOME específico do personagem aparece na aba Characters.
            # Removidos: o fallback "qualquer card" e o "encontrado = True" forçado
            # (que aprovavam '@Marcos' mesmo quando ele não existia na biblioteca).
            termos_busca = list(loc_termos)
            if nome_limpo and nome_limpo != nome_personagem:
                termos_busca += [
                    f'[role="option"]:has-text("{nome_limpo}")',
                    f'[role="button"]:has-text("{nome_limpo}")',
                    f'div:has-text("{nome_limpo}")',
                    f'span:has-text("{nome_limpo}")',
                ]
            for sel in termos_busca:
                try:
                    if dialog.locator(sel).first.is_visible(timeout=400):
                        encontrado = True
                        break
                except Exception:
                    continue

            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)

            if encontrado:
                pw_log(f"PRE-VOO PERSONAGEM: '@{nome_personagem}' ENCONTRADO na aba Personagens.")
            else:
                pw_log(f"PRE-VOO PERSONAGEM: '@{nome_personagem}' NÃO encontrado na aba Personagens.", level="warn")
            return encontrado
        except Exception as e:
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            pw_log(f"PRE-VOO PERSONAGEM: erro ao verificar biblioteca ({e}).", level="warn")
            return False

    @staticmethod
    def _cenas_usam_personagem(cenas: List[dict]) -> bool:
        """True se alguma cena da fila depende de personagem humano.

        Critérios determinísticos (sem tocar na UI): uses_character explícito,
        character_ref preenchido, ou scene_type humano. Espelha as prioridades
        de character_service.obter_personagem_cena.
        """
        tipos_h = {"avatar_talking", "avatar_action", "hybrid", "cta"}
        for c in (cenas or []):
            if not isinstance(c, dict):
                continue
            if c.get("uses_character") is True:
                return True
            if str(c.get("character_ref") or "").strip():
                return True
            if str(c.get("scene_type") or "").strip().lower() in tipos_h:
                return True
        return False

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
                    tab_pers = dialog.locator('button[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Characters")').first
                    if tab_pers.is_visible(timeout=1000):
                        self._safe_click(tab_pers)
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

            # 2. Se não encontrou, resolve o caminho da imagem de referência de forma
            #    DINÂMICA (projetos/<id>/identidade.json -> characters/*/reference.png ->
            #    references/*/reference.png). NUNCA usa caminho fixo de outra máquina.
            img_caminho = imagem_abs if (imagem_abs and Path(imagem_abs).exists()) else ""
            if not img_caminho:
                img_caminho = character_svc.resolver_imagem_avatar_projeto(projeto_id)
            if not img_caminho:
                pw_log(
                    f"Nenhum avatar/referência encontrado para o projeto '{projeto_id}' "
                    f"(procurou em identidade.json, characters/*/reference.png e references/*/reference.png). "
                    f"Criação do personagem no Flow ABORTADA — sem caminho fixo de outra máquina.",
                    level="error",
                )
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

    def _safe_click(self, locator, timeout_ms: int = 1000) -> bool:
        """Executa clique resiliente: scroll -> click -> force click -> JS scroll/click -> dispatch."""
        if not locator:
            return False
        try:
            locator.scroll_into_view_if_needed(timeout=timeout_ms)
        except Exception:
            pass

        try:
            locator.click(timeout=timeout_ms)
            return True
        except Exception:
            pass

        try:
            locator.click(force=True, timeout=timeout_ms)
            return True
        except Exception:
            pass

        try:
            locator.evaluate('el => { el.scrollIntoView({block: "center", inline: "center"}); el.click(); }')
            return True
        except Exception:
            pass

        try:
            locator.dispatch_event('click')
            return True
        except Exception:
            pass

        return False

    def _verificar_chip_personagem_no_editor(self, editor) -> bool:
        """Verifica no DOM do editor (ProseMirror/Slate) se há uma Character Entity / Chip / referência visual anexada.

        Exige marcador ESTRUTURAL real (contenteditable=false, <img>,
        data-entity/data-ingredient ou classe chip/pill/badge/mention/ingredient).
        Texto puro digitado como "@Nome" (menção não convertida em chip) NÃO é
        considerado entidade — evita falso positivo na trava anti-rosto-aleatório.
        """
        if not editor:
            return False
        try:
            return bool(editor.evaluate(_JS_VERIFICA_CHIP_EDITOR))
        except Exception:
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
        Localiza e vincula a referência visual do personagem via menu de ingredientes ou @ do Google Flow.
        Suporta o editor atual (ProseMirror) e editores legados.
        """
        if not self.page:
            return False

        editor = None
        for sel in [
            'div.ProseMirror[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div.ProseMirror[contenteditable="true"]',
            'div[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[role="textbox"][contenteditable="true"]:not(aside *)',
            '[contenteditable="true"]:not(aside *)',
        ]:
            loc = self.page.locator(sel).first
            if loc.is_visible(timeout=1000):
                editor = loc
                break

        if not editor:
            return False

        tag_display = ref_tag or (f"@{nome_personagem}" if nome_personagem else "@Marcos")

        try:
            self._safe_click(editor)
            self.page.wait_for_timeout(100)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(100)

            sucesso = incluir_referencia_personagem(
                self.page,
                imagem_abs or "reference.png",
                nome_personagem=nome_personagem,
                # Só propaga tag_display quando há personagem real (uploads usam
                # nome_personagem="" e o default "@Marcos" do tag_display NÃO deve
                # virar busca indevida por "Marcos" na aba Characters).
                tag_personagem=(tag_display if nome_personagem else ""),
            )

            # Fallback por digitação se o menu de ingredientes não inseriu o chip
            if not sucesso and not self._verificar_chip_personagem_no_editor(editor):
                try:
                    editor.focus()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.wait_for_timeout(100)
                    alvo_dig = tag_display if tag_display.startswith("@") else f"@{nome_personagem or tag_display}"
                    self.page.keyboard.type(alvo_dig, delay=60)
                    self.page.wait_for_timeout(500)
                    # Tenta confirmar menção com Enter
                    self.page.keyboard.press("Enter")
                    self.page.wait_for_timeout(300)
                except Exception:
                    pass

            # Garante que qualquer overlay ou dialog esteja fechado antes de prosseguir
            try:
                for ov_sel in ["div.cdk-overlay-pane", "div[role='dialog']", "div[role='menu']"]:
                    if self.page.locator(ov_sel).first.is_visible(timeout=300):
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(200)
                        break
            except Exception:
                pass

            if sucesso or self._verificar_chip_personagem_no_editor(editor):
                print(f"[OK] Referência visual '{tag_display}' anexada com sucesso!", flush=True)
                pw_log(f"CHARACTER_ENTITY_ATTACHED_OK: Referência visual '{tag_display}' anexada ao comando.")
                self.current_flow_reference = tag_display
                return True
            else:
                pw_log(f"Aviso: chip não detectado no editor para '{tag_display}'.", level="warn")
                return False
        except Exception as e:
            pw_log(f"Aviso ao selecionar referência no Flow: {e}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def _anexar_referencia_imagem_local(self, projeto_id: str, arquivo_imagem: str) -> bool:
        """IMAGE-TO-VIDEO (B-Roll puro): envia a PNG já gerada da cena para a
        galeria do Google Flow e a anexa como referência visual no editor
        (upload → '+ Uploads → Incluir no comando'), antes de digitar o prompt
        de animação. Retorna True se a referência foi anexada com sucesso.

        Fallback natural: se o upload/anexo falhar, quem chama segue com
        Text-to-Video (apenas o prompt de movimento).
        """
        if not self.page or not arquivo_imagem or not Path(arquivo_imagem).exists():
            return False
        nome = Path(arquivo_imagem).name

        try:
            # (a) Upload da PNG local para a galeria do workspace (mesmo padrão do avatar)
            input_file = self.page.locator('input[type="file"]').first
            if input_file.count() > 0:
                input_file.set_input_files(arquivo_imagem)
                self.page.wait_for_timeout(2500)
            else:
                btn_env = self.page.locator(
                    'button:has-text("Enviar mídia"), '
                    'button:has-text("Adicionar mídia"), '
                    'button:has-text("Fazer upload"), '
                    'button[aria-label*="Add image" i], '
                    'button[aria-label*="Upload" i], '
                    'button:has(i:has-text("add"))'
                ).first
                if btn_env.is_visible(timeout=3000):
                    with self.page.expect_file_chooser(timeout=4000) as fc_info:
                        btn_env.click(force=True)
                    fc_info.value.set_files(arquivo_imagem)
                    self.page.wait_for_timeout(2500)
                else:
                    pw_log(f"[IMG_REF] Botão de envio de mídia não encontrado para '{nome}'.", level="warn")
                    return False

            # (b) Aguarda o arquivo aparecer na galeria (polling JS_FETCH_MEDIA_LIST)
            apareceu = False
            t0_ref = time.time()
            while time.time() - t0_ref < 15:
                try:
                    res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
                    if res and res.get("ok"):
                        nomes = [str(m.get("name") or m.get("id") or "") for m in res.get("media", [])]
                        if nomes and any(nome.lower() in n.lower() for n in nomes):
                            apareceu = True
                            break
                except Exception:
                    pass
                self.page.wait_for_timeout(1000)
            # Aguarda 2000ms fixos após confirmar que a imagem apareceu na galeria
            # (e também antes de prosseguir quando o polling expira sem confirmação),
            # para a miniatura/indexação estabilizarem antes de _selecionar_referencia_flow().
            self.page.wait_for_timeout(2000)
            pw_log(f"[IMG_REF] Upload '{nome}' concluído (visível na galeria={apareceu}).")
        except Exception as e:
            pw_log(f"[IMG_REF] Falha no upload de '{nome}': {e}", level="warn")
            return False

        # (c) Anexa a imagem ao comando (abre '+' → Uploads → Incluir no comando)
        return self._selecionar_referencia_flow(
            projeto_id=projeto_id,
            nome_personagem="",
            tipo="imagem",
            ref_tag="",
            arquivo_flow="",
            imagem_abs=arquivo_imagem,
            flow_character_id=""
        )


    def _processar_cena_individual(
        self,
        projeto_id: str,
        cena: Dict[str, Any],
        is_anim: bool = False,
        index: int = 1,
        total_cenas: int = 1
    ) -> Tuple[bool, str]:
        cid = int(cena.get("id", 0))
        # Reseta flag de upscale 2K por cena (garante tentativa por cena nova)
        self._upscale_tentado_cena = False
        # FASE 3.2 — fonte única de tipo de mídia: scene_plan.tipo decide se é vídeo.
        # animar_depois/animate_later apenas AGENDAM animação futura; não alteram tipo.
        # Na passada de imagem (is_anim=False) SEMPRE gera imagem (REGRA 6 mantida).
        tipo_efetivo = scene_plan_svc.tipo_efetivo_cena(cena)
        # PRIORIDADE 4 — AVATAR GESTUAL: avatar SEMPRE gera IMAGEM com linguagem
        # corporal expressiva (sem fala). A flag is_anim do worker (fila de animação)
        # NÃO deve sobrescrever a decisão quando a cena é explicitamente avatar.
        nome_cena = str(cena.get("nome") or cena.get("titulo") or cena.get("texto") or f"cena {cid}")
        eh_avatar = _detectar_cena_avatar(cena)
        if eh_avatar:
            pw_log(f"[AVATAR] Cena '{nome_cena[:80]}' é AVATAR. Forçando modo gestual.", level="info")
        if getattr(self, "_fallback_video_para_imagem", False):
            # PARTE 5 — créditos de vídeo esgotados nesta fila: TODAS as cenas que seriam
            # animadas caem para IMAGEM (fallback video→imagem, sem rotacionar conta).
            video_mode = False
        elif eh_avatar:
            video_mode = False  # Avatar sempre gera imagem, nunca vídeo (gestual)
        else:
            video_mode = bool(is_anim or (tipo_efetivo == scene_plan_svc.TIPO_VIDEO))
        timeout_s = 300

        # CORREÇÃO 3 — prompt SEMPRE coerente com o modo:
        #  - video_mode=False (geração de IMAGEM): usa SEMPRE prompt_imagem —
        #    NUNCA prompt_animacao (o campo animar/animar_depois agenda animação
        #    futura; não converte a geração atual em vídeo).
        #  - video_mode=True (animação/vídeo): usa prompt_animacao, com fallback
        #    para prompt_imagem caso a cena não tenha animação pronta.
        if video_mode:
            raw_prompt = cena.get("prompt_animacao") or cena.get("prompt_imagem") or cena.get("texto", "")
            if not raw_prompt:
                raw_prompt = "Cinematic slow camera motion, natural depth of field, high clarity 16:9"
        else:
            raw_prompt = cena.get("prompt_imagem") or cena.get("texto", "")
        prompt = self._clean_prompt_text(raw_prompt)
        # PRIORIDADE 4 — avatar gestual: remove narração/fala do prompt e adiciona o
        # sufixo de linguagem corporal expressiva ("sem fala, apenas gestos").
        if eh_avatar and prompt:
            prompt = _ajustar_prompt_avatar_gestual(prompt, nome_cena)

        # 0. Garante que a aba Flow esteja aberta (CORREÇÃO 2 — FASE 3: reabre
        # automaticamente se a aba foi derrubada durante a fila, em vez de
        # simplesmente falhar a cena com "Google Flow fechado").
        if not self._garantir_aba_flow_aberta():
            return False, "Google Flow fechado. Clique em Abrir Google Flow novamente."

        # MODAL_GUARD: Garante que modal nunca fica aberto no início de uma cena
        try:
            if self.page and self.page.locator("div[role='dialog']").first.is_visible(timeout=500):
                self.page.keyboard.press("Escape")
                self.page.wait_for_selector("div[role='dialog']", state="hidden", timeout=2000)
                pw_log("[MODAL_GUARD] Modal encontrado aberto no início da cena — fechado forçadamente.")
        except Exception as e:
            pw_log(f"[MODAL_GUARD] Falha ao verificar/fechar modal: {e}")

        # 1. Garante projeto aberto
        if not self._ensure_project_open(projeto_id, timeout_s=5):
            return False, "Google Flow fechado. Clique em Abrir Google Flow novamente."

        # 2. Garante que qualquer chat/painel de agente esteja fechado e fecha banners
        self._disable_agent_mode()
        self._fechar_modais_bloqueantes()

        # 3. Força configuração do modo correto (Imagem vs Vídeo) e modelo do projeto
        target_mode = "video" if video_mode else "image"
        # PRIORIDADE 4 — avatar gestual usa SEMPRE a config de IMAGEM do projeto
        # (modelo/qualidade/proporcao), mesmo quando a fila é de animação/vídeo.
        if eh_avatar:
            _cfg_av = getattr(self, "_cfg_imagem_projeto", None) or {}
            target_model = (_cfg_av.get("modelo") or _cfg_av.get("prod_modelo_imagem")
                            or "Nano Banana 2")
            _prop_av = _cfg_av.get("proporcao") or _cfg_av.get("prod_proporcao") or "16:9"
            _qtd_av = _cfg_av.get("qualidade") or _cfg_av.get("prod_qualidade_imagem") or "x1"
            self._set_output_mode("image", modelo_solicitado=target_model,
                                  proporcao_solicitada=_prop_av,
                                  qualidade_solicitada=_qtd_av)
        else:
            # CORREÇÃO 3 — quando video_mode=False (imagem), o modelo NÃO pode herdar
            # self.current_model se ele for "Veo 3.1 - Lite" (modelo de vídeo). Fallback
            # para "Nano Banana 2" (modelo de imagem) nesse caso.
            target_model = "Veo 3.1 - Lite" if video_mode else (
                self.current_model if self.current_model and "veo" not in self.current_model.lower()
                else "Nano Banana 2"
            )
            self._set_output_mode(target_mode, modelo_solicitado=target_model)
        self.current_flow_mode = target_mode
        self.current_model = target_model

        existing_keys, initial_media_count = self._get_existing_media_snapshot()

        # 4. Localiza e foca o campo de prompt do DOCK PRINCIPAL (com espera ativa de até 10s)
        editor = None
        selectors_editor = [
            'div.ProseMirror[contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div.ProseMirror[contenteditable="true"]',
            'div[contenteditable="true"].ProseMirror',
            'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
            'div[role="textbox"][contenteditable="true"]:not(aside *)',
            '[contenteditable="true"]:not(aside *)',
        ]
        t0_ed = time.time()
        while time.time() - t0_ed < 10:
            for sel in selectors_editor:
                try:
                    loc = self.page.locator(sel).first
                    if loc.is_visible(timeout=600):
                        editor = loc
                        break
                except Exception:
                    pass
            if editor:
                break
            self.page.wait_for_timeout(500)

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
        flow_id = ""

        if char_info:
            nome_char = char_info.get("nome", "")
            tipo_char = char_info.get("tipo", "personagem")
            tag_char = char_info.get("character_ref") or char_info.get("referencia_flow", f"@{nome_char}" if nome_char else "")
            arq_char = char_info.get("arquivo_flow", "")
            img_abs = char_info.get("imagem_abs", "")
            flow_id = char_info.get("flow_character_id", "")

            # Detecção por cena (integrada no prompt builder): prioriza o personagem REAL
            # citado na cena e a imagem de referência (reference.png) detectada para anexar.
            det_tag = cena.get("personagem_ref") or ""
            det_img = cena.get("personagem_ref_imagem") or ""

            # CORREÇÃO 1 — personagem_ref pode conter um CAMINHO DE ARQUIVO
            # (ex: "C:\Lira Videos\projetos\Batman\personagem_global.png") em vez de um
            # alias @Nome. O alias deve vir APENAS de campos com @NomePersonagem, nunca
            # de caminhos de arquivo. Se det_tag for caminho/extensão de imagem, ignora.
            det_tag = str(det_tag or "").strip()
            if det_tag and ("\\" in det_tag or "/" in det_tag
                            or det_tag.lower().endswith((".png", ".jpg", ".jpeg"))):
                pw_log(f"[CENA {cid:03d}] personagem_ref é caminho de arquivo (não alias @Nome) — ignorado: {det_tag}")
                tag_char = ""
                det_tag = ""
            if det_tag:
                tag_char = det_tag
                nome_char = det_tag.lstrip("@")
            if det_img:
                p_img = Path(det_img)
                if not p_img.is_absolute():
                    p_img = PROJETOS_DIR / projeto_id / det_img
                if p_img.exists():
                    img_abs = str(p_img)
            if not img_abs or not Path(img_abs).exists():
                img_abs = character_svc.resolver_imagem_avatar_projeto(projeto_id)

        if uses_char:
            print(f"[LOG] ATTACHING_CHARACTER_ENTITY: Anexando entidade oficial '{tag_char}' no Flow...", flush=True)
            pw_log(f"[CENA {cid:03d}] ATTACHING_CHARACTER_ENTITY: Tentando anexar chip de '{tag_char}' no Flow.")

            entidade_inserida = self._selecionar_referencia_flow(
                projeto_id=projeto_id,
                nome_personagem=nome_char,
                tipo=tipo_char,
                ref_tag=tag_char,
                arquivo_flow=arq_char,
                imagem_abs=img_abs,
                flow_character_id=flow_id
            )

            # PARTE 6 — VALIDAÇÃO ANTES DE GERAR (TRAVA ANTI-ROSTO-ALEATÓRIO):
            if not entidade_inserida:
                msg_erro_char = f"ERRO CRÍTICO: Character Entity '{tag_char}' não pôde ser anexado ao editor do Google Flow. Geração abortada para não criar rosto aleatório."
                print(f"[LOG] CHARACTER_ATTACH_FAILED: {msg_erro_char}", flush=True)
                pw_log(f"[CENA {cid:03d}] {msg_erro_char}", level="error")
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                    "status": scene_plan_svc.STATUS_ERRO
                })
                return False, msg_erro_char

            # PARTE 5 — INSERÇÃO DO PROMPT: Envia SOMENTE a descrição visual (remove @Nome para não duplicar com o chip)
            prompt_visual_puro = self._clean_prompt_text(prompt, ref_tag=tag_char, strip_character_tag=True)
            pw_log(f"[CENA {cid:03d}] CHARACTER_ENTITY_CONFIRMED: Entidade '{tag_char}' presente no editor. Enviando prompt visual complementar.")

            if prompt_visual_puro:
                editor.focus()
                self.page.keyboard.insert_text(" " + prompt_visual_puro)
                self.page.wait_for_timeout(200)

        else:
            # PARTE 7 — CENAS SEM PERSONAGEM (B-Roll puro):
            print(f"[LOG] SCENE_SKIPPED_NO_CHARACTER: Cena é b-roll ou sem sujeito humano. Personagem não anexado.", flush=True)
            pw_log(f"[CENA {cid:03d}] SCENE_SKIPPED_NO_CHARACTER: Cena é b-roll. Usando PNG como referência (se houver) para Image-to-Video.")
            pw_log("[CENA_BROLL] Cena sem personagem — modal de personagem não será aberto.")

            prompt_final = self._clean_prompt_text(prompt, ref_tag="", strip_character_tag=True)

            # IMAGE-TO-VIDEO: ao animar (video_mode) com a PNG da cena já gerada,
            # anexa a imagem como referência visual e digita o prompt de movimento
            # como complemento — o vídeo animado vira continuidade da imagem original.
            arquivo_midia_cena = str(cena.get("arquivo_midia") or "")
            tem_png_referencia = bool(
                video_mode
                and arquivo_midia_cena
                and arquivo_midia_cena.lower().endswith(".png")
                and Path(arquivo_midia_cena).exists()
            )
            ref_anexada = False
            if tem_png_referencia:
                try:
                    ref_anexada = self._anexar_referencia_imagem_local(projeto_id, arquivo_midia_cena)
                except Exception as e:
                    pw_log(f"[CENA {cid:03d}] Falha ao anexar PNG de referência: {e}", level="warn")
                pw_log(
                    f"[CENA {cid:03d}] IMAGE_TO_VIDEO: PNG '{Path(arquivo_midia_cena).name}' "
                    + ("anexada como referência." if ref_anexada else "não anexada — seguindo Text-to-Video.")
                )

            if ref_anexada:
                # Editor já foi limpo e recebeu a imagem pela rotina de anexo —
                # basta adicionar a instrução de movimento ao lado da referência.
                editor.focus()
                if prompt_final:
                    self.page.keyboard.insert_text(" " + prompt_final)
                    self.page.wait_for_timeout(200)
            else:
                # Fallback Text-to-Video (sem imagem de referência disponível)
                editor.click()
                self.page.wait_for_timeout(100)
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.wait_for_timeout(100)
                if prompt_final:
                    self.page.keyboard.insert_text(prompt_final)
                    self.page.wait_for_timeout(200)

        # 6. Envio Imediato: Dispara Enter no editor e clica no botão Create
        t_inicio_cena = time.time()
        prompt_completo_enviado = (f"{tag_char} " if uses_char else "") + (prompt_visual_puro if uses_char else prompt_final)
        pw_log(f"[COMANDO_ENVIADO] Cena {cid:03d} | Prompt exato: '{prompt_completo_enviado}'")
        print(f"[COMANDO_ENVIADO] Cena {cid:03d} | Prompt exato: '{prompt_completo_enviado}'", flush=True)
        pw_log(f"[PROMPT_PREVIEW] Primeiros 200 chars do prompt: {prompt[:200]}")
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
        pw_log(f"[CENA {cid:03d}] Prompt enviado. Aguardando Flow...")
        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "image_status": scene_plan_svc.IMAGE_STATUS_GENERATING,
            "status": scene_plan_svc.STATUS_GERANDO
        })

        # Checa se houve erro imediato ou limite de modelo para disparar fallback automático
        # CORREÇÃO 2 — espera 4s após o envio e faz até 3 tentativas de detecção com
        # 2s entre elas, parando assim que detectar algo (ou esgotar as tentativas).
        self.page.wait_for_timeout(4000)
        err_limite = None
        for _tent_detect in range(3):
            err_limite = self._detectar_erro_ou_limite_modelo(video_mode=video_mode)
            if err_limite:
                break
            self.page.wait_for_timeout(2000)
        # BURACO 2 (defensivo) — a função _detectar_erro_ou_limite_modelo DEVE retornar
        # apenas códigos canónicos ("credito_esgotado"/"credito_esgotado_video") ou None.
        # Se uma regressão futura devolver um indicador cru (ex: "limite de geração"),
        # NUNCA perder a señal en silencio: normaliza ao código canónico e aplica a
        # misma lógica de fallback/rotación antes de los `if err_limite == ...`.
        if err_limite and err_limite not in ("credito_esgotado", "credito_esgotado_video"):
            pw_log(f"[FLOW] Indicador de limite NO canónico normalizado: {err_limite!r}. Aplicando lógica de créditos esgotados.", level="warn")
            # PARTE 6 — reutiliza a fonte única (_tratar_indicador_limite), que agora
            # tenta a rotação de conta ANTES do fallback video→imagem.
            err_limite = self._tratar_indicador_limite(err_limite, video_mode)
        if err_limite == "credito_esgotado_video":
            # PARTE 5 — créditos de VÍDEO esgotados: NÃO rotaciona conta. Reconverte a
            # geração atual para IMAGEM (fallback video→imagem) e reprocessa a cena
            # como imagem. A flag _fallback_video_para_imagem fica True p/ o resto da fila.
            pw_log("[FLOW] Crédito vídeo esgotado. Reconfigurando para IMAGEM...", level="warn")
            video_mode = False
            self.current_flow_mode = "image"
            _cfg_img_fb = getattr(self, "_cfg_imagem_projeto", None) or {}
            _modelo_img_fb = _cfg_img_fb.get("modelo") or "Nano Banana 2"
            _prop_img_fb = _cfg_img_fb.get("proporcao") or "16:9"
            _qtd_img_fb = _cfg_img_fb.get("qualidade") or "x1"
            self.current_model = _modelo_img_fb
            reconf_ok = False
            try:
                self._configured_mode = None  # força reconfiguração
                reconf_ok = bool(self._set_output_mode(
                    "image",
                    modelo_solicitado=_modelo_img_fb,
                    proporcao_solicitada=_prop_img_fb,
                    qualidade_solicitada=_qtd_img_fb,
                ))
            except Exception as _e_fb:
                pw_log(f"[FLOW] Aviso ao reconverter para imagem no fallback: {_e_fb}", level="warn")
            if reconf_ok:
                pw_log("[FLOW] Reconfiguração OK. Recolocando cena como IMAGEM.", level="info")
                pw_log(f"[FLOW] FALLBACK_VIDEO_IMAGEM_OK: reconvertido para IMAGEM com {_modelo_img_fb} ({_prop_img_fb}, {_qtd_img_fb}).")
            else:
                pw_log("[FLOW] Reconfiguração FALHOU. Tentaremos na próxima iteração.", level="error")
            # Recoloca com fallback ativado (a próxima execução já roda como imagem)
            self._fallback_video_para_imagem = True
            return (False, "credito_esgotado_video_recolocado")
        if err_limite == "credito_esgotado":
            pw_log(f"[FLOW] Cena {cena.get('id')} marcada para reprocessamento após rotação de conta.", level="warn")
            return (False, "credito_esgotado_recolocado")
        elif err_limite and not self.is_fallback_active and "Pro" in self.current_model:
            print("\n[AVISO] Nano Banana Pro indisponível", flush=True)
            print("Fallback ativado", flush=True)
            print("Modelo: Nano Banana 2", flush=True)
            print("[OK] Produção continuada", flush=True)
            pw_log(f"[AVISO] Nano Banana Pro indisponível ({err_limite}) - Fallback ativado para Nano Banana 2")
            self.current_model = "Nano Banana 2"
            self.is_fallback_active = True
            self._set_output_mode(target_mode, modelo_solicitado="Nano Banana 2")

            # REGRA FALLBACK DE MODELO com personagem: para cenas com personagem
            # (uses_char=True), o chip/entidade do Flow NÃO sobrevive ao envio
            # anterior — o editor é limpo pelo Flow após a submissão. Simplesmente
            # reenviar o texto "@Nome" NÃO cria a referência visual (descoberto na
            # criação de personagem: é necessário abrir o seletor, localizar o
            # personagem na aba Characters e clicar para anexar). Por isso, re-anexa
            # via clique ANTES de reenviar o prompt visual puro.
            editor.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(100)

            if uses_char:
                # Re-anexa o personagem nativo no editor (popup '+' → aba
                # Characters → card do personagem) — mesma função do fluxo principal.
                _re_entidade = self._selecionar_referencia_flow(
                    projeto_id=projeto_id,
                    nome_personagem=nome_char,
                    tipo=tipo_char,
                    ref_tag=tag_char,
                    arquivo_flow=arq_char,
                    imagem_abs=img_abs,
                    flow_character_id=flow_id,
                )
                if not _re_entidade:
                    msg_sem_char = (f"ERRO CRÍTICO no fallback de modelo: Character Entity "
                                    f"'{tag_char}' não pôde ser re-anexado ao editor após troca "
                                    f"para {self.current_model}. Geração abortada para não criar "
                                    f"rosto aleatório.")
                    print(f"[LOG] CHARACTER_REATTACH_FAILED: {msg_sem_char}", flush=True)
                    pw_log(f"[CENA {cid:03d}] {msg_sem_char}", level="error")
                    scene_plan_svc.atualizar_cena(projeto_id, cid, {
                        "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                        "status": scene_plan_svc.STATUS_ERRO
                    })
                    return False, msg_sem_char
                pw_log(f"[CENA {cid:03d}] CHARACTER_REATTACHED_OK no fallback: '{tag_char}' re-anexado para {self.current_model}.")
                if prompt_visual_puro:
                    editor.focus()
                    self.page.keyboard.insert_text(" " + prompt_visual_puro)
                    self.page.wait_for_timeout(200)
            else:
                # Cena b-roll / sem personagem: reenvia o prompt visual final limpo
                # (prompt_final já teve timestamps/@Nome residuais removidos).
                self.page.keyboard.insert_text(prompt_final)

            self.page.wait_for_timeout(200)
            editor.focus()
            self.page.keyboard.press("Enter")

        # 7. Polling ultra-rápido a cada 1.5s para captura imediata da nova mídia
        new_media_item = None
        t_poll_start = time.time()
        _iter_poll = 0
        pw_log(f"[CENA {cid:03d}] Aguardando renderização do Flow (detecção contínua a cada 1.5s)...")

        while time.time() - t_poll_start < timeout_s:
            if self.stop_requested.is_set():
                return False, "Operação cancelada pelo usuário."

            # CORREÇÃO 2 — a cada 5 iterações do polling, confere se a aba ainda está
            # na URL correta do projeto (labs.google/flow.google.com). Se o usuário
            # navegou para outro domínio (ex: flowmusic.app), restaura a URL salva.
            _iter_poll += 1
            if _iter_poll % 5 == 0:
                try:
                    _url_atual_poll = self.page.url or ""
                    if not self._eh_aba_flow_valida(_url_atual_poll):
                        pw_log("[FLOW] Aba desviou da URL correta do projeto — tentando restaurar...", level="warn")
                        url_salva = carregar_projeto_flow_url(projeto_id)
                        if url_salva:
                            try:
                                self.page.goto(url_salva, timeout=30000)
                                self.page.wait_for_timeout(2000)
                            except Exception as e_rest:
                                pw_log(f"[FLOW] Falha ao restaurar URL do projeto: {e_rest}", level="warn")
                except Exception:
                    pass

            # Progresso incremental (BLOCO 2 aprovado) — a cada iteração do polling
            tempo_loop_decorrido = time.time() - t_poll_start
            pct = min(99, int(tempo_loop_decorrido / timeout_s * 100))
            log_event(
                "PLAYWRIGHT_FLOW",
                f"[CENA {cid:03d}] Renderizando no Flow: {pct}% ({tempo_loop_decorrido:.1f}s)",
                level="info",
            )
            if self.cena_ativa is not None:
                self.cena_ativa["progresso_pct"] = pct

            recusa = self._checar_recusa_politica()
            if recusa:
                msg_recusa = f"BLOQUEADO_POLITICA: {recusa}"
                pw_log(f"[CENA {cid:03d}] {msg_recusa}", level="error")
                print(f"[POLITICA] Falha de política detectada na Cena {cid:03d}: {recusa}", flush=True)
                try:
                    p_log_dir = PROJETOS_DIR / projeto_id
                    p_log_dir.mkdir(parents=True, exist_ok=True)
                    log_file = p_log_dir / "erros_politica.log"
                    ts_now = datetime.now().isoformat(sep=" ", timespec="seconds")
                    entry = f"[{ts_now}] CENA {cid:03d} | MOTIVO: {recusa}\nPROMPT ENVIADO: {prompt_completo_enviado}\n{'-'*70}\n"
                    with open(log_file, "a", encoding="utf-8") as _f_pol:
                        _f_pol.write(entry)
                except Exception as _e_plog:
                    pw_log(f"[POLITICA_LOG] Erro ao gravar log de política: {_e_plog}", level="warn")
                return False, msg_recusa

            try:
                curr_res = self.page.evaluate(JS_FETCH_MEDIA_LIST)
            except Exception as e_poll:
                pw_log(f"Aviso de polling no Flow: {e_poll}", level="warn")
                if not self._garantir_aba_flow_aberta():
                    return False, f"Aba do Flow inacessível: {e_poll}"
                self.page.wait_for_timeout(1000)
                continue

            if curr_res and curr_res.get("ok"):
                curr_media = curr_res.get("media", [])
                # v0.4.0: novidade determinada pelo mediaKey (UUID estável da URL
                # do asset no Flow), nunca por posição/índice do DOM.
                # CORREÇÃO 2 (FASE 3): coleta TODOS os candidatos novos e só aceita
                # EXATAMENTE 1. Com 2+ mídias novas é impossível atribuir qual asset
                # pertence a este job -> ERRO, nada é salvo (antes: "último da
                # galeria", que atribuía a mídia pelo relógio).
                novos = []
                for item in curr_media:
                    media_key = item.get("mediaKey")
                    is_new = (
                        bool(media_key)
                        and media_key not in existing_keys
                        and item.get("id") not in existing_keys
                        and item.get("src") not in existing_keys
                    )
                    if not is_new:
                        continue
                    if video_mode and item["type"] == "video":
                        novos.append(item)
                    elif not video_mode and item["type"] == "image":
                        if item.get("dataUrl") or (item.get("width", 0) > 60):
                            novos.append(item)

                if len(novos) > 1:
                    pw_log(
                        f"[CENA {cid:03d}] AMBIGUO: {len(novos)} mídias novas no Flow — "
                        f"impossível atribuir com certeza qual asset é deste job. "
                        f"Marcando ERRO; nada foi salvo.",
                        level="error",
                    )
                    try:
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "erro_msg": f"{len(novos)} mídias novas no Flow (ambíguo)",
                            "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                            "video_status": (scene_plan_svc.VIDEO_STATUS_ERROR if video_mode
                                             else scene_plan_svc.VIDEO_STATUS_NOT_STARTED),
                        })
                    except Exception as e_amb:
                        pw_log(f"[CENA {cid:03d}] Falha ao marcar ERRO (ambíguo): {e_amb}", level="warn")
                    return False, (f"Ambíguo: {len(novos)} mídias novas no Flow para a "
                                   f"cena {cid} — asset não atribuível com certeza")
                if len(novos) == 1:
                    new_media_item = novos[0]

            if new_media_item:
                # QUALIDADE DE DOWNLOAD 2K: se configurado, tenta aplicar upscale
                # no card da imagem antes do download. Se funcionou, o Flow gera
                # uma NOVA versão 2K (outro mediaKey) — continua o polling até ela
                # aparecer; se não funcionou, segue com 1K (aviso em log).
                if (
                    not video_mode
                    and getattr(self, "current_download_quality", "1K") == "2K"
                    and not getattr(self, "_upscale_tentado_cena", False)
                ):
                    self._upscale_tentado_cena = True
                    ups_ok = self._tentar_upscale_2k()
                    if ups_ok:
                        # Adiciona o item 1K aos já conhecidos (evita re-detecção)
                        _mk = new_media_item.get("mediaKey")
                        _id = new_media_item.get("id")
                        _src = new_media_item.get("src")
                        if _mk:
                            existing_keys.add(_mk)
                        if _id:
                            existing_keys.add(_id)
                        if _src:
                            existing_keys.add(_src)
                        new_media_item = None
                        pw_log(f"[CENA {cid:03d}] UPSCALE_2K_OK: upscale aplicado — aguardando versão 2K...")
                        self.page.wait_for_timeout(1500)
                        continue  # volta ao polling para detectar a versão 2K
                    pw_log(f"[CENA {cid:03d}] UPSCALE_2K_SKIP: mantendo download em 1K (menu 2K não acessível).", level="warn")

                tempo_decorrido = time.time() - t_poll_start
                pw_log(f"[CENA {cid:03d}] Mídia detectada com sucesso no Flow em {tempo_decorrido:.1f}s!")
                break

            self.page.wait_for_timeout(1500)

        if not new_media_item:
            # CORREÇÃO 3 (FASE 3): timeout não pode deixar a cena "presa" em GERADA.
            try:
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "status": scene_plan_svc.STATUS_ERRO,
                    "erro_msg": f"Timeout ({timeout_s}s) aguardando nova mídia no Flow",
                    "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                    "video_status": (scene_plan_svc.VIDEO_STATUS_ERROR if video_mode
                                     else scene_plan_svc.VIDEO_STATUS_NOT_STARTED),
                })
            except Exception as e_to:
                pw_log(f"[CENA {cid:03d}] Falha ao marcar timeout: {e_to}", level="warn")
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
        if not video_mode and ("domIndex" in new_media_item or "mediaKey" in new_media_item):
            try:
                # Extração por UUID (estável) com fallback de posição DOM
                res_canvas = self.page.evaluate(
                    JS_EXTRACT_CANVAS_DATA_URL,
                    new_media_item.get("mediaKey") or new_media_item["domIndex"]
                )
                if res_canvas and res_canvas.get("ok") and res_canvas.get("dataUrl"):
                    raw_b64 = res_canvas["dataUrl"].split(",")[1]
                    content_bytes = base64.b64decode(raw_b64)
            except Exception as e_dec:
                pw_log(f"Falha ao extrair canvas dataUrl: {e_dec}", level="warn")

        if not content_bytes and new_media_item.get("dataUrl"):
            try:
                raw_b64 = new_media_item["dataUrl"].split(",")[1]
                content_bytes = base64.b64decode(raw_b64)
            except Exception as e_dec:
                pw_log(f"Falha ao decodificar canvas dataUrl: {e_dec}", level="warn")

        if not content_bytes and new_media_item.get("src"):
            try:
                resp = self.page.request.get(new_media_item["src"])
                if resp.ok and len(resp.body()) > 500:
                    content_bytes = resp.body()
            except Exception as e_req:
                pw_log(f"Download via page.request: {e_req}", level="warn")

        if not content_bytes and new_media_item.get("src"):
            res_blob = self._baixar_midia_com_retry(new_media_item["src"], video_mode)
            if res_blob.get("ok") and res_blob.get("base64"):
                try:
                    raw_b64 = res_blob["base64"].split(",")[1]
                    content_bytes = base64.b64decode(raw_b64)
                except Exception as e_dec:
                    pass

        if not content_bytes:
            # CORREÇÃO 3 (FASE 3): download vazio também marca ERRO (não só GERADA).
            try:
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "status": scene_plan_svc.STATUS_ERRO,
                    "erro_msg": "Erro ao baixar mídia: dados binários vazios",
                    "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                    "video_status": (scene_plan_svc.VIDEO_STATUS_ERROR if video_mode
                                     else scene_plan_svc.VIDEO_STATUS_NOT_STARTED),
                })
            except Exception as e_bin:
                pw_log(f"[CENA {cid:03d}] Falha ao marcar download vazio: {e_bin}", level="warn")
            return False, f"Erro ao baixar mídia: dados binários vazios"

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

        # ANTIGRAVITY #3 — VALIDAÇÃO COM VISÃO (cenas AVATAR): compara a face da
        # imagem gerada com a reference.png (@personagem). Fidelidade < 70% →
        # rejeita e reprocessa (o loop da fila executa a 2ª tentativa).
        if eh_avatar and not is_video_result:
            try:
                import services.visual_judgment_service as vjs_svc
                _vj = vjs_svc.avaliar_fidelidade_facial(
                    projeto_id, res_salva.get("arquivo_path") or "", cena
                )
                if _vj and _vj.get("fidelidade") is not None:
                    _fid_av = int(_vj["fidelidade"])
                    pw_log(f"[VISUAL_JUDGMENT_AVATAR] Fidelidade facial: {_fid_av}% (método={_vj.get('metodo')})")
                    if _fid_av < 70:
                        pw_log(f"[VISUAL_JUDGMENT_AVATAR] Fidelidade facial {_fid_av}% < 70% — rejeitando cena {cid:03d} para reprocessamento.", level="warn")
                        try:
                            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                "status": scene_plan_svc.STATUS_PENDENTE,
                                "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                                "erro_msg": "",
                            })
                        except Exception:
                            pass
                        return False, f"Fidelidade facial do avatar {_fid_av}% < 70% — reprocessando cena {cid}"
            except Exception as _e_vj:
                pw_log(f"[VISUAL_JUDGMENT_AVATAR] Aviso na validação facial da cena {cid:03d}: {_e_vj}", level="warn")

        # CORREÇÃO 1 (FASE 3) — Não marcar PRONTO/BAIXADA sem o arquivo REAL em disco.
        # Antes, a cena era promovida a BAIXADA logo após a geração, mesmo quando a
        # gravação/download falhava. Agora só é promovida quando o arquivo existe
        # fisicamente (> 500 bytes); caso contrário permanece PENDENTE e pode ser
        # reprocessada (sem rebaixar a UI para um "pronto" falso).
        _arq_gerado = str((res_salva or {}).get("arquivo_path") or "")
        _arquivo_ok = False
        try:
            _arquivo_ok = bool(_arq_gerado) and Path(_arq_gerado).exists() and Path(_arq_gerado).stat().st_size > 500
        except OSError:
            _arquivo_ok = False
        if not _arquivo_ok:
            try:
                _campos_falha = {
                    "status": scene_plan_svc.STATUS_PENDENTE,
                    "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                    "erro_msg": "Arquivo não encontrado em disco após a geração",
                }
                # CORREÇÃO 3 (FASE 3): em passada de VÍDEO a falha precisa refletir
                # no video_status (a UI/retomada decidem por ele).
                if is_video_result:
                    _campos_falha["video_status"] = scene_plan_svc.VIDEO_STATUS_ERROR
                scene_plan_svc.atualizar_cena(projeto_id, cid, _campos_falha)
            except Exception:
                pass
            pw_log(f"[CENA {cid:03d}] Sem arquivo válido em disco — mantendo PENDENTE.", level="warn")
            return False, f"Cena {cid} não gerou arquivo válido em disco (mantida PENDENTE)"

        # 10. Atualiza o status definitivo para READY / BAIXADA no scene_plan.json
        campos_cena = {
            "arquivo_midia": res_salva["arquivo_path"],
            "download_path": res_salva["arquivo_path"],
            "filename": res_salva["arquivo_nome"],
            "image_status": scene_plan_svc.IMAGE_STATUS_READY if not is_video_result else scene_plan_svc.IMAGE_STATUS_DOWNLOADED,
            "video_status": scene_plan_svc.VIDEO_STATUS_READY if is_video_result else scene_plan_svc.VIDEO_STATUS_NOT_STARTED,
            "status": scene_plan_svc.STATUS_BAIXADA,
            "erro_msg": "",
            "uses_character": uses_char,
            "character_ref": char_tag if uses_char else "",
        }
        # Promove a cena de "image" para "video" SOMENTE quando o arquivo salvo é
        # realmente um vídeo — timeline, badges, export CapCut e render decidem por
        # tipo/media_intent. (Passadas de imagem NÃO rebaixam uma cena já animada.)
        if is_video_result:
            campos_cena["tipo"] = scene_plan_svc.TIPO_VIDEO
            campos_cena["media_intent"] = "video"
        scene_plan_svc.atualizar_cena(projeto_id, cid, campos_cena)
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        # CORREÇÃO 3 (FASE 3): fechamento obrigatório das 3 fontes sincronizadas.
        # garantir_cena_padrao(mover=False) grava metadata/cena_XXX/status.json (a 3ª
        # fonte) SEM mover o arquivo: o layout em disco (cenas/) é preservado.
        try:
            from services.media_standard import garantir_cena_padrao
            garantir_cena_padrao(projeto_id, cid, mover=False)
        except Exception as e_pad:
            pw_log(f"[CENA {cid:03d}] Aviso ao sincronizar mídia padrão (status.json): {e_pad}", level="warn")
        pw_log(f"[CENA {cid:03d}] ✅ Baixada: {res_salva['arquivo_path']}")

        print("[LOG] FILE_SAVED_OK", flush=True)
        print("[LOG] SCENE_SAVED_OK", flush=True)
        print("[LOG] SCENE_LINKED_OK", flush=True)
        print("[LOG] UPDATE_UI", flush=True)
        print(f"[OK] Arquivo salvo: {res_salva['arquivo_nome']}", flush=True)
        print("[OK] Storyboard atualizado", flush=True)
        t_fim_cena = time.time()
        duracao_real_segundos = round(t_fim_cena - t_inicio_cena, 2)
        pw_log(f"[TEMPO_GERACAO] Cena {cid:03d}: concluída em {duracao_real_segundos}s (start -> end).")
        print(f"[TEMPO_GERACAO] Cena {cid:03d}: concluída em {duracao_real_segundos}s (tempo real medido).", flush=True)
        pw_log(f"[CENA {cid:03d}] SCENE_SAVED_OK & UPDATE_UI: Arquivo salvo como '{res_salva['arquivo_nome']}' e sincronizado no Lira Studio.")
        return True, f"Cena {cid} gerada e baixada com sucesso: {res_salva['arquivo_nome']} em {duracao_real_segundos}s"

    def _handle_run_queue(self, projeto_id: str, scene_ids: Optional[List[int]], modo: str):
        self.is_running_queue = True
        self.stop_requested.clear()
        # PARTE 5 — reseta o fallback video→imagem a cada fila nova.
        self._fallback_video_para_imagem = False
        self.current_project_id = projeto_id
        self._persistir_alerta_creditos(fallback=False)
        self.current_flow_mode = modo
        self._avatar_uploaded = False  # nova sessão = novo upload de avatar

        try:
            # A sessão Playwright nasce DENTRO desta thread (obrigatório p/ sync_api).
            ok_sessao, msg_sessao = self._iniciar_sessao_thread()
            if not ok_sessao:
                print(f"[ERRO] Falha ao conectar no Google Flow: {msg_sessao}", flush=True)
                pw_log(f"\n[FLOW SESSION]\nStatus: Erro\nMotivo: {msg_sessao}", level="error")
                return

            # 2. Garante que a fila rode na aba do Google Flow COM PROJETO aberto.
            #    _ensure_project_open navega ao projeto salvo (flow_meta.json) ou
            #    tenta abrir/criar automaticamente clicando em "Novo projeto".
            if not self._ensure_project_open(projeto_id, timeout_s=8):
                msg_erro = ("Google Flow sem projeto aberto. Abra/crie um projeto no "
                            "Google Flow e clique em 'Gerar Todas as Imagens' novamente.")
                print(f"[ERRO] {msg_erro}", flush=True)
                self.last_queue_pause_reason = msg_erro
                pw_log(f"\n[FLOW SESSION]\nStatus: Erro\nMotivo: {msg_erro}", level="error")
                return

            # 2.1 Upload do avatar do projeto para o workspace (UMA vez por sessão).
            #     Falha NUNCA trava a fila — apenas avisa e segue sem avatar.
            try:
                self._upload_avatar_projeto(projeto_id)
            except Exception as e_av:
                pw_log(f"[AVATAR_UPLOAD] Erro inesperado ao enviar avatar do projeto: {e_av}", level="warn")

            # Sincroniza mídias existentes no disco para garantir retomada exata de onde parou
            scene_plan_svc.sincronizar_galeria_projeto(projeto_id)

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

                arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
                tem_arquivo_disco = False
                if modo == "animacao":
                    if arq_disco and arq_disco.suffix.lower() in [".mp4", ".mov", ".webm"]:
                        tem_arquivo_disco = True
                else:
                    tem_arquivo_disco = bool(arq_disco and arq_disco.exists() and arq_disco.stat().st_size > 500)

                # Retomada automática: se já possui arquivo em disco, não repete.
                # ANTIGRAVITY — migração: se o arquivo resolvido NÃO estiver na pasta
                # canônica cenas/, copia fisicamente para cenas/ com o nome padrão de
                # 4 blocos (media_standard) antes de persistir BAIXADA. Não altera o
                # fallback de leitura do resolver_arquivo_cena.
                if tem_arquivo_disco and arq_disco is not None:
                    _arq_final = arq_disco
                    try:
                        import shutil as _shutil
                        _cenas_dir = PROJETOS_DIR / projeto_id / "cenas"
                        _orig = Path(str(arq_disco))
                        if not _cenas_dir.exists() or _orig.resolve().parent != _cenas_dir.resolve():
                            _cenas_dir.mkdir(parents=True, exist_ok=True)
                            _ext = _orig.suffix.lower() or (".mp4" if modo == "animacao" else ".png")
                            _t0 = float(c.get("tempo_inicio") or 0)
                            _t1 = float(c.get("tempo_fim") or (_t0 + float(c.get("duracao") or 0)))
                            _novo_nome = ""
                            try:
                                from services import media_standard as _ms
                                _novo_nome = _ms.nome_arquivo_seguro(_ms.nome_padrao_cena(cid, _t0, _t1, _ext))
                            except Exception:
                                _novo_nome = scene_plan_svc._nome_cena_timecode(projeto_id, cid, _t0, _t1, _ext)
                            if _novo_nome:
                                _destino = _cenas_dir / _novo_nome
                                if _destino.resolve() != _orig.resolve():
                                    if not _destino.exists():
                                        _shutil.copy2(str(_orig), str(_destino))
                                    _arq_final = _destino
                                    pw_log(f"[RETOMADA] Cena {cid}: mídia migrada para pasta canônica cenas/ -> {_novo_nome}")
                    except Exception as _e_mig:
                        pw_log(f"[RETOMADA] aviso ao migrar mídia da cena {cid}: {_e_mig}", level="warn")
                    if st != scene_plan_svc.STATUS_BAIXADA or _arq_final != arq_disco:
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_BAIXADA,
                            "image_status": scene_plan_svc.IMAGE_STATUS_READY,
                            "arquivo_midia": str(_arq_final),
                            "filename": Path(str(_arq_final)).name,
                        })
                    continue

                # REGRA 6: Nunca gerar vídeo na primeira passada. Se modo for vídeo, marca para animar depois
                if modo in ["video", "imagem_video"]:
                    scene_plan_svc.atualizar_cena(projeto_id, cid, {"animar_depois": True, "animate_later": True})
                    print(f"[LOG] ANIMATE_LATER_FLAGGED: Cena {cid} marcada para animação posterior", flush=True)
                    pw_log(f"[CENA {cid:03d}] ANIMATE_LATER_FLAGGED: Cena marcada para animação posterior.")

                if modo == "animacao":
                    e_video = bool(
                        (scene_plan_svc.tipo_efetivo_cena(c) == scene_plan_svc.TIPO_VIDEO)
                        or c.get("animate_later")
                        or c.get("animar_depois")
                        or c.get("animar")
                        or (target_ids is not None and cid in target_ids)
                    )
                    if e_video:
                        cenas_a_processar.append(c)
                else:
                    cenas_a_processar.append(c)

            print("[OK] ULTRACUT3 iniciado", flush=True)
            print("[OK] Google Flow conectado", flush=True)
            print(f"[OK] Projeto carregado: {projeto_id}", flush=True)
            print(f"[INFO] Cenas a produzir: {len(cenas_a_processar)} de {total_cenas_projeto}\n", flush=True)

            # -------------------------------------------------------------
            # PRE-VOO DO PERSONAGEM (roda UMA única vez por fila):
            #   • Reseta a flag _flow_character_library_empty (não vaza entre filas).
            #   • Se alguma cena usa personagem, verifica UMA vez se '@Nome'
            #     consta na aba Personagens REAL do Flow (popup '@').
            #   • Cadastro é MANUAL e ÚNICO (~30s pelo usuário no navegador);
            #     durante as cenas basta '@Nome' no TEXTO do prompt.
            #   • Não cadastrado => PAUSA a fila antes da primeira cena (não
            #     aborta projeto, não pula cenas). Retomada = clicar novamente
            #     no botão de produção (start_worker refaz a fila; cenas
            #     BAIXADAS com arquivo em disco são puladas automaticamente).
            # -------------------------------------------------------------
            self._flow_character_library_empty = False
            self.last_queue_pause_reason = ""

            if self._cenas_usam_personagem(cenas_a_processar):
                import services.character_service as character_svc
                _idt = character_svc.obter_identidade_projeto(projeto_id) or {}
                _nome_char = str(_idt.get("nome") or "").strip()
                motivo_pre_voo = ""
                if not _nome_char:
                    motivo_pre_voo = ("há cenas que usam personagem, mas nenhum "
                                      "personagem está configurado no projeto (Studio 2.0 -> aba "
                                      "Identidade: nome + foto de referência). Configure e clique "
                                      "novamente em 'Gerar Todas as Imagens' para retomar a fila.")
                elif not self._verificar_personagem_na_biblioteca(_nome_char):
                    # ANTIGRAVITY #1/#2 — PRÉ-VOO AUTOCORRETIVO: o personagem NÃO existe
                    # de verdade (verificação estrita, sem falso positivo). Reprovisiona
                    # ANTES de processar a fila — a MESMA função da rotação de contas
                    # agora também roda no início da fila (cria + garante upload).
                    pw_log(f"[PRE_VOO] Personagem '@{_nome_char}' não encontrado na biblioteca. Criando automaticamente...", level="warn")
                    _ok_pre = self._reprovisionar_personagem_apos_rotacao(
                        projeto_id=projeto_id, forcar_criacao=True
                    )
                    if _ok_pre:
                        # Garantido: criar_personagem_flow valida no popup '@' (passo 9)
                        # antes de retornar True; _avatar_uploaded já marcado pelo método.
                        print(f"[OK] PRE-VOO PERSONAGEM: '@{_nome_char}' criado automaticamente no Flow.", flush=True)
                    else:
                        motivo_pre_voo = (f"'@{_nome_char}' NÃO consta na aba Personagens "
                                          "do Google Flow e a criação automática falhou. Crie o personagem "
                                          f"no Flow com a foto de referência e nome '@{_nome_char}', e clique "
                                          "novamente em Gerar para retomar.")

                if motivo_pre_voo:
                    # CORREÇÃO 3 — NÃO bloqueia a fila: registra aviso e segue enviando
                    # os prompts automaticamente cena por cena (sem intervenção manual).
                    # Cenas que precisarem de personagem e não conseguirem anexar são
                    # marcadas como erro individualmente (comportamento existente).
                    self.last_queue_pause_reason = motivo_pre_voo
                    print(f"\n[AVISO PRE-VOO] PERSONAGEM NÃO ENCONTRADO NO PRÉ-VOO (geração continua): {motivo_pre_voo}", flush=True)
                    pw_log(motivo_pre_voo, level="warn")
                else:
                    print(f"[OK] PRE-VOO PERSONAGEM: '@{_nome_char}' validado na biblioteca do Flow.", flush=True)
                    pw_log(f"PRE_VOO_PERSONAGEM_OK: '@{_nome_char}' pronto para anexação de Character Entity.")

            # PRÉ-VOO FINAL (PROBLEMA 2) — garantia ANTES do 1º processamento, mesmo
            # quando NÃO houve rotação de conta: reprovisiona se algum personagem usado
            # nas cenas ainda faltar (forcar_criacao=False → verifica e só cria se ausente;
            # evita recriar '@Marcos' duplicado quando ele já existe).
            if cenas_a_processar and self.current_project_id:
                pw_log("[QUEUE] Executando pré-voo final de personagens...", level="info")
                self._reprovisionar_personagem_apos_rotacao(
                    projeto_id=projeto_id, forcar_criacao=False
                )
                pw_log("[QUEUE] Pré-voo de personagens concluído.")

            url_aba = (self.page.url if self.page else "") or ""
            email_conta, nome_proj = self._extrair_metadados_sessao(projeto_id)
            pw_log(f"[CONEXÃO] Conta ativa: {email_conta or 'Não identificada'} | Projeto: {nome_proj or projeto_id}")
            pw_log(f"\n[FLOW SESSION]\nStatus: Produção Ativa\nAba: Google Flow\nURL: {url_aba}\nTotal de Cenas: {len(cenas_a_processar)}")

            self.queue_start_time = time.time()
            self.scene_durations = []

            # Lê configurações de produção do meta.json do projeto (salvas pelo painel de config)
            _meta_proj = {}
            try:
                _meta_file = PROJETOS_DIR / projeto_id / "meta.json"
                if _meta_file.exists():
                    import json as _json
                    _meta_proj = _json.loads(_meta_file.read_text(encoding="utf-8"))
            except Exception as _e_meta:
                pw_log(f"[QUEUE] Aviso ao ler meta.json do projeto: {_e_meta}", level="warn")

            # PARTE 4 — Configura modelo/qualidade ANTES do loop a partir do meta do projeto.
            # Imagem e Vídeo têm configurações SEPARADAS (prod_modelo_imagem/_video e
            # prod_qualidade_imagem/_video). Se não houver config salva, usa defaults seguros.
            _modo_pre = "video" if modo == "animacao" else "image"
            _proporcao_pre = _meta_proj.get("prod_proporcao") or "16:9"
            if _modo_pre == "video":
                # Fila de animação (b-roll) → modelo/quantidade de VÍDEO
                _modelo_pre = _meta_proj.get("prod_modelo_video") or "Veo 3.1 - Lite"
                _qualidade_pre = _meta_proj.get("prod_qualidade_video") or "x1"
            else:
                # Fila de imagem → modelo/quantidade de IMAGEM
                # (fallback para prod_modelo/prod_qualidade legados, se presentes no meta)
                _modelo_pre = _meta_proj.get("prod_modelo_imagem") or _meta_proj.get("prod_modelo") or "Nano Banana 2"
                _qualidade_pre = _meta_proj.get("prod_qualidade_imagem") or _meta_proj.get("prod_qualidade") or "x1"
            # Qualidade de DOWNLOAD da imagem gerada (1K = original, 2K = upscaled)
            _qualidade_download_pre = str(_meta_proj.get("prod_qualidade_download") or "1K")
            self.current_download_quality = _qualidade_download_pre
            self.current_model = _modelo_pre
            # PARTE 4 — guarda a config de IMAGEM do projeto (usada no fallback video→imagem
            # quando os créditos de vídeo esgotam: reconverte o Flow para imagem).
            self._cfg_imagem_projeto = {
                "modelo": _meta_proj.get("prod_modelo_imagem") or _meta_proj.get("prod_modelo") or "Nano Banana 2",
                "qualidade": _meta_proj.get("prod_qualidade_imagem") or _meta_proj.get("prod_qualidade") or "x1",
                "proporcao": _proporcao_pre,
            }
            try:
                self._configured_mode = None  # força reconfiguração
                self._set_output_mode(
                    _modo_pre,
                    modelo_solicitado=_modelo_pre,
                    proporcao_solicitada=_proporcao_pre,
                    qualidade_solicitada=_qualidade_pre
                )
                print(f"[OK] MODELO_CONFIGURADO: {_modelo_pre} ({_modo_pre}, {_proporcao_pre}, {_qualidade_pre}) — lido do meta.json do projeto. | Download: {_qualidade_download_pre}", flush=True)
                pw_log(f"[QUEUE] MODELO_PRE_LOOP_OK: {_modelo_pre} ({_modo_pre}, {_proporcao_pre}, {_qualidade_pre}) configurado antes do primeiro envio. | Download: {_qualidade_download_pre}")
            except Exception as _e_modo:
                pw_log(f"[QUEUE] Aviso ao configurar modelo antes do loop: {_e_modo}", level="warn")

            # RATE LIMIT PROTECTION — aguarda 10s ANTES de iniciar o primeiro envio da
            # fila (evita CAPTCHA/rate limit do Google Flow ao iniciar uma sequência).
            # Espera em blocos de 5s respeitando o botão Pausar/Cancelar do usuário.
            if len(cenas_a_processar) > 0:
                pw_log("[RATE_LIMIT_PROTECTION] Iniciando fila. Aguardando 10s para evitar CAPTCHA/rate limit...", level="info")
                _t0_rate = time.time()
                while (time.time() - _t0_rate) < 10 and not self.stop_requested.is_set():
                    time.sleep(min(5, 10 - (time.time() - _t0_rate)))
                if self.stop_requested.is_set():
                    pw_log("[RATE_LIMIT_PROTECTION] Espera inicial interrompida pelo usuário.", level="warn")

            # CORREÇÃO 4 — Confirmação de sequencialidade:
            # O loop abaixo é um 'for' Python simples, sem threading, asyncio ou
            # futures internos. Cada cena é processada até o fim (ou erro) antes
            # de avançar para a próxima. Nenhuma cena é disparada em paralelo.
            # CORREÇÃO 2 — contador de reciclagens por cena (anti loop infinito quando
            # todas as contas estão sem créditos e a cena é devolvida ao início da fila).
            recoloc_count: Dict[int, int] = {}
            for idx, cena in enumerate(cenas_a_processar, 1):
                if self.stop_requested.is_set():
                    print("\n[INFO] Fila pausada pelo usuário.", flush=True)
                    pw_log("\n[FLOW SESSION]\nStatus: Fila pausada pelo usuário.")
                    break

                # RATE LIMIT PROTECTION — em filas grandes (>5 cenas), aguarda 5s antes
                # de cada cena a partir da 2ª (idx é 1-based neste loop). Espera em
                # blocos de 5s respeitando o botão Pausar/Cancelar do usuário.
                if idx > 1 and len(cenas_a_processar) > 5:
                    pw_log(f"[RATE_LIMIT_PROTECTION] Cena {idx}/{len(cenas_a_processar)}. Aguardando 5s antes de processar...", level="info")
                    _t0_rate = time.time()
                    while (time.time() - _t0_rate) < 5 and not self.stop_requested.is_set():
                        time.sleep(min(5, 5 - (time.time() - _t0_rate)))
                    if self.stop_requested.is_set():
                        pw_log("[RATE_LIMIT_PROTECTION] Espera entre cenas interrompida pelo usuário.", level="warn")

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
                # idx é 1-based (enumerate(..., 1)) -> exibe posição real na fila
                pw_log(f"[CENA {cid:03d}] Iniciando ({idx}/{len(cenas_a_processar)})...")

                sucesso = False
                res_msg = ""
                recolocada_por_credito = False
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
                    if not ok and res_msg in ("credito_esgotado_recolocado", "credito_esgotado_video_recolocado"):
                        # CORREÇÃO 2 — limite de reciclagens por cena: sem este teto, quando
                        # não há NENHUMA conta com créditos a cena é devolvida ao início da
                        # fila indefinidamente (loop infinito — não há timeout global).
                        recoloc_count[cid] = recoloc_count.get(cid, 0) + 1
                        if recoloc_count[cid] > 3:
                            _msg_esgotadas = "Todas as contas esgotadas"
                            log_event(
                                "PLAYWRIGHT_FLOW",
                                f"[FLOW] Cena {cid} reciclada {recoloc_count[cid]}x por créditos esgotados — "
                                f"{_msg_esgotadas}. Marcada como ERRO e fila interrompida.",
                                level="error",
                            )
                            pw_log(
                                f"[FLOW] Cena {cid} excedeu 3 reciclagens por créditos esgotados "
                                f"({_msg_esgotadas}). Marcando ERRO e PARANDO a fila.",
                                level="error",
                            )
                            try:
                                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                    "status": scene_plan_svc.STATUS_ERRO,
                                    "erro_msg": _msg_esgotadas,
                                })
                            except Exception as _e_erro_cred:
                                pw_log(f"[FLOW] Aviso ao marcar cena {cid} como ERRO: {_e_erro_cred}", level="warn")
                            # NÃO recoloca na fila; para a fila (o loop externo sai no topo,
                            # no guard "if self.stop_requested.is_set()").
                            self.stop_requested.set()
                            break
                        # CORREÇÃO 4 / PARTE 5 — crédito esgotado NÃO é erro da cena: NÃO marca
                        # STATUS_ERRO; volta para PENDENTE e recoloca no INÍCIO da fila
                        # para reprocessamento (com a nova conta — credito_esgotado_recolocado —
                        # ou como IMAGEM via fallback — credito_esgotado_video_recolocado, pois a
                        # flag _fallback_video_para_imagem força video_mode=False na re-execução).
                        pw_log(f"[FLOW] Recolocando cena {cena.get('id')} no início da fila (PENDENTE, sem ERRO).", level="warn")
                        try:
                            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                "status": scene_plan_svc.STATUS_PENDENTE,
                                "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                                "erro_msg": "",
                            })
                        except Exception as _e_recoloca:
                            pw_log(f"[FLOW] Aviso ao marcar cena {cid} como PENDENTE: {_e_recoloca}", level="warn")
                        cenas_a_processar.insert(0, cena)
                        recolocada_por_credito = True
                        break  # sai do loop de tentativas; bloco abaixo NÃO marca ERRO
                    if ok:
                        sucesso = True
                        break
                    else:
                        print(f"[AVISO] Tentativa {tentativa + 1} falhou para cena {cid}: {res_msg}", flush=True)
                        pw_log(f"[CENA {cid:03d}] ⚠️ Tentativa {tentativa + 1} falhou: {res_msg}", level="warn")
                        self.cena_ativa["etapa"] = f"Tentativa {tentativa + 1} falhou. Tentando reconectar aba do Flow..."
                        self._garantir_aba_flow_aberta()
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
                elif recolocada_por_credito:
                    # CORREÇÃO 4 — crédito esgotado: cena recolocada no início com
                    # PENDENTE; NÃO registra erro. O loop externo continua sem break.
                    pw_log(f"[FLOW] Cena {cid} recolocada no início da fila (créditos esgotados) — sem marcar ERRO.")
                    # CORREÇÃO 3 — respiro mínimo no caminho de reciclagem: este `continue`
                    # pula o DELAY_ENTRE_PROMPTS_SEG do fim do laço, o que tornava o laço
                    # "hot" enquanto tenta rotacionar a conta. 5s antes de reprocessar.
                    time.sleep(5)
                    continue
                else:
                    if not self.stop_requested.is_set():
                        now_ts = datetime.now().isoformat(sep=" ", timespec="seconds")
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                            "video_status": scene_plan_svc.VIDEO_STATUS_ERROR if modo == "animacao" else scene_plan_svc.VIDEO_STATUS_NOT_STARTED,
                            "erro_msg": res_msg,
                            "erro_ts": now_ts
                        })
                        pw_log(f"[CENA {cid:03d}] ERRO ({now_ts}): Não foi possível gerar após 2 tentativas ({res_msg}). Registrado no log.", level="error")
                        print(f"[AVISO CENA {cid:03d}] Falha registrada: {res_msg}. Continuando a fila para a próxima cena...", flush=True)

                # Respiro configurável após confirmação de download/salvamento antes da próxima cena:
                if idx < len(cenas_a_processar):
                    next_cena = cenas_a_processar[idx]
                    next_cid = int(next_cena.get("id", 0))
                    pw_log(f"[DELAY] Aguardando {DELAY_ENTRE_PROMPTS_SEG}s para iniciar Cena {next_cid:03d}...")
                    print(f"[DELAY] Aguardando {DELAY_ENTRE_PROMPTS_SEG}s antes do próximo prompt (rate limit)...", flush=True)
                    self.current_delay_info = {
                        "next_scene_id": next_cid,
                        "delay_total": DELAY_ENTRE_PROMPTS_SEG,
                        "inicio_ts": time.time()
                    }
                    if self.cena_ativa is not None:
                        self.cena_ativa["etapa"] = f"Aguardando {DELAY_ENTRE_PROMPTS_SEG}s para iniciar Cena {next_cid:03d}..."
                        self.cena_ativa["status"] = "DELAY"
                    time.sleep(DELAY_ENTRE_PROMPTS_SEG)
                    self.current_delay_info = None

            if not self.stop_requested.is_set():
                total_t = time.time() - (self.queue_start_time or time.time())
                print(f"\n[LOG] ALL_IMAGES_COMPLETE_OK (Tempo total: {total_t:.1f}s)", flush=True)
                print("[OK] Produção de cenas concluída!", flush=True)
                pw_log(f"[FLOW SESSION] ALL_IMAGES_COMPLETE_OK: Todas as cenas foram produzidas com sucesso em {total_t:.1f}s.")
                pw_log(f"✅ Fila concluída: {len(cenas_a_processar)} cenas em {total_t:.1f}s")

        except Exception as e:
            print(f"[ERRO] Erro na execução da fila: {e}", flush=True)
            pw_log(f"Erro inesperado na execução da fila Playwright CDP: {e}", level="error")
        finally:
            # Encerra a sessão na MESMA thread que a criou (obrigatório p/ sync_api)
            self._encerrar_sessao()
            self.is_running_queue = False
            self.cena_ativa = None
            self.current_delay_info = None
            pw_log("\n[FLOW SESSION]\nStatus: Fila de produção finalizada.")

    def reconectar_projeto_salvo(self, projeto_id: str, timeout_s: int = 45) -> Tuple[bool, str]:
        """Botão 'Reconectar ao Flow' — reconexão detectando a aba do Flow JÁ ABERTA.

        Conecta ao Chrome via CDP (porta 9222) e reutiliza a aba do Google Flow
        que já estiver aberta (labs.google/flow). Se a aba estiver no canvas de
        um projeto (/project/), a URL é salva em flow_meta.json para uso futuro.
        NÃO exige flow_meta.json, NÃO processa a fila, NÃO envia prompts e NÃO
        cria projeto.

        A sessão Playwright nasce numa thread daemon própria (o sync_api é
        thread-bound); ao final ela é encerrada — isso desconecta APENAS o
        client CDP, a aba permanece aberta no canvas do projeto.
        """
        # 1. Fila ocupada -> recusa informativa (evita disputa de thread/sessão)
        if self.is_running_queue:
            return False, ("Fila de produção em execução — aguarde o término ou "
                           "pare a fila antes de reconectar.")

        # 2. Sem exigência de URL salva: o _trabalho abaixo conecta via CDP e
        #    detecta a aba do Flow já aberta (_garantir_aba_flow -> _ensure_project_open);
        #    a URL do projeto é salva em flow_meta.json quando encontrada.

        resultado = {"ok": False, "msg": ""}

        def _trabalho():
            try:
                ok_cdp, msg_cdp = self._abrir_chrome_cdp()
                if not ok_cdp:
                    resultado["msg"] = f"Chrome/CDP indisponível: {msg_cdp}"
                    return
                self.current_project_id = projeto_id
                ok_sessao, msg_sessao = self._iniciar_sessao_thread()
                if not ok_sessao:
                    resultado["msg"] = f"Falha ao criar sessão Playwright: {msg_sessao}"
                    return
                if not self._garantir_aba_flow():
                    resultado["msg"] = ("Nenhuma aba do Google Flow encontrada no Chrome. "
                                        "Abra o Flow (labs.google/fx/pt/tools/flow) e "
                                        "tente reconectar novamente.")
                    return
                if not self._ensure_project_open(projeto_id, timeout_s=10):
                    url_atual = (self.page.url if self.page else "") or ""
                    resultado["msg"] = ("A aba não chegou ao canvas do projeto "
                                        f"(URL atual: {url_atual or 'indefinida'}). "
                                        "Verifique se o projeto ainda existe no Flow.")
                    return
                url_final = (self.page.url if self.page else "") or ""
                resultado["ok"] = True
                resultado["msg"] = f"Reconectado ao projeto Flow ({url_final})"
                pw_log(f"RECONNECT_OK | projeto={projeto_id} | URL={url_final}")
            except Exception as e:  # noqa: BLE001
                resultado["msg"] = f"Erro inesperado na reconexão: {e}"
                pw_log(f"RECONNECT_FAIL | projeto={projeto_id} | {e}", level="error")
            finally:
                try:
                    # Encerra na MESMA thread que a criou (obrigatório p/ sync_api)
                    self._encerrar_sessao()
                except Exception:
                    pass

        t = threading.Thread(target=_trabalho, daemon=True,
                             name=f"FlowReconnect-{projeto_id}")
        t.start()
        t.join(timeout=max(5, int(timeout_s)))
        if t.is_alive():
            return False, (f"Reconexão excedeu {timeout_s}s aguardando o Chrome. "
                           "Tente novamente.")
        return bool(resultado["ok"]), resultado["msg"]


_debug_modal_done = False


# ---------------------------------------------------------------------------
# Criação AUTOMÁTICA de personagem no Google Flow (função real, nível de módulo).
#
# Sequência validada por teste manual real (personagem "TesteAutomacao01").
# DIFERE da automação legada (/characters page, _garantir_personagem_criado_no_flow):
# usa o drawer Characters + 'New character' + upload + promoção do retrato (passo 7).
#
# AUTO_CRIAR_PERSONAGEM_FLOW: mantida por compatibilidade/documentação. Desde a
# correção ANTIGRAVITY #1/#2 o pré-voo CRIA automaticamente o personagem sempre
# que ele NÃO existe na biblioteca (verificação estrita, sem falso positivo),
# independentemente desta chave.
# ---------------------------------------------------------------------------
AUTO_CRIAR_PERSONAGEM_FLOW = True


class FlowCharacterCreationError(RuntimeError):
    """Falha explícita em um passo da criação automática de personagem no Flow."""


def _clicar_item_personagens_flow(page, timeout_ms: int = 7000) -> bool:
    """Abre o drawer lateral (se necessário) e clica em Characters/Personagens.

    Cobre PT/EN e várias tecnologias de lista (mat-list-item, role=menuitem,
    links, botões), além de fallback JS por texto exato. Retorna True se clicou.
    """
    termos = ["Characters", "Personagens"]
    seletores = []
    for termo in termos:
        seletores += [
            f'.mat-drawer-inner-container mat-list-item:has-text("{termo}")',
            f'.mat-drawer-inner-container [role="menuitem"]:has-text("{termo}")',
            f'.mat-drawer-inner-container a:has-text("{termo}")',
            f'mat-list-item:has-text("{termo}")',
            f'[role="menuitem"]:has-text("{termo}")',
            f'a:has-text("{termo}")',
            f'button:has-text("{termo}")',
            f'[role="button"]:has-text("{termo}")',
        ]

    def _tentar() -> bool:
        for sel in seletores:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=350):
                    loc.click(timeout=1200)
                    return True
            except Exception:
                continue
        return False

    # 1) item já disponível (drawer aberto)?
    if _tentar():
        return True

    # 2) abre o menu lateral (hamburger) e tenta novamente
    for sel_menu in [
        'button[aria-label*="menu" i]',
        'button[aria-label*="navigation" i]',
        'button[aria-label*="navega" i]',
        'button.mdc-icon-button:has-text("menu")',
        'button:has(i:text-is("menu"))',
    ]:
        try:
            m = page.locator(sel_menu).first
            if m.is_visible(timeout=400):
                m.click(timeout=1200)
                page.wait_for_timeout(500)
                break
        except Exception:
            continue
    if _tentar():
        return True

    # 3) fallback JS — procura o item por texto exato (PT/EN) e clica no ancestral clicável
    try:
        clicou = page.evaluate('''() => {
            const raiz = document.querySelector('.mat-drawer-inner-container') || document;
            const alvos = Array.from(raiz.querySelectorAll(
                'mat-list-item, [role="menuitem"], a, button, [role="button"], span'));
            const alvo = alvos.find(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                return t === 'characters' || t === 'personagens';
            });
            if (alvo) {
                const clicavel = alvo.closest('mat-list-item, [role="menuitem"], a, button, [role="button"]') || alvo;
                clicavel.click();
                return true;
            }
            return false;
        }''')
        if clicou:
            return True
    except Exception:
        pass
    return False


def criar_personagem_flow(page, nome_personagem: str, caminho_foto: str) -> bool:
    """Cria um personagem nativo no Google Flow e valida no popup '@'.

    Fluxo (ordem fixa, validada manualmente):
      1. Fecha modal residual (Escape).
      2. Aba "Characters" no drawer lateral.
      3. "New character" (NUNCA "Create my avatar").
      4. Upload da foto de referência via file chooser.
      5. Nome oficial (input "Character name").
      6. Descrição de personalidade: deixada em BRANCO (mais simples de manter;
         a preservação facial é garantida pela foto oficial promovida no passo 7).
      7. PASSO CRÍTICO: clicar na miniatura da foto no histórico para promovê-la
         a Portrait oficial do card. SEM esse clique a foto fica só no histórico
         do rascunho e NÃO vira o retrato do personagem — NÃO REMOVER.
      8. "Done".
      9. Validação: reabre o popup '@' e confirma que '@nome' aparece na aba
         Characters. Se não aparecer => FlowCharacterCreationError (nunca sucesso).

    Cada passo (2-8) com timeout curto; se o seletor não for encontrado, salva
    screenshot em logs/flow_automation/ com nome descritivo, loga o passo que
    falhou e levanta FlowCharacterCreationError (não segue nem simula sucesso).
    """
    from config import BASE_DIR  # import local p/ não criar dependência no topo

    shot_dir = Path(BASE_DIR) / "logs" / "flow_automation"
    shot_dir.mkdir(parents=True, exist_ok=True)
    safe_nome = re.sub(r"[^A-Za-z0-9_-]+", "_", str(nome_personagem or "").strip()) or "personagem"

    def _shot(passo: str):
        p = shot_dir / f"criar_personagem_{safe_nome}_{passo}.png"
        try:
            page.screenshot(path=str(p))
        except Exception:
            pass
        return p

    # CORREÇÃO 3: nome pode já chegar com '@' — evita '@@' duplicado en logs.
    nome_exibicao = f"@{str(nome_personagem or '').lstrip('@')}"

    def _passo_falhou(passo: str, detalhe: str):
        p = _shot(passo)
        msg = (f"[criar_personagem_flow] PASSO '{passo}' FALHOU para '{nome_exibicao}': {detalhe} "
               f"(screenshot: {p})")
        pw_log(msg, level="error")
        raise FlowCharacterCreationError(msg)

    # Pré-condições
    if not page or not str(nome_personagem or "").strip() or not caminho_foto:
        _passo_falhou("pre_condicoes", "page/nome_personagem/caminho_foto ausentes")
    if not Path(caminho_foto).exists():
        _passo_falhou("pre_condicoes", f"foto não existe em '{caminho_foto}'")

    # 1. Fecha qualquer modal residual
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass

    # 2. Aba Characters / Personagens no drawer lateral
    #    (1º confirma que estamos no CANVAS do projeto — na home do Flow não existe
    #     drawer; 2º usa helper com fallback PT/EN para clicar no item.)
    try:
        try:
            _url_antes = str(page.url or "")
        except Exception:
            _url_antes = ""
        if "/project/" not in _url_antes:
            raise FlowCharacterCreationError(
                "o canvas de um projeto não está aberto nesta aba do Flow "
                f"(URL atual: {_url_antes or 'indefinida'}). A aba lateral 'Characters' "
                "só existe DENTRO de um projeto — abra o projeto no Flow e tente novamente."
            )
        if not _clicar_item_personagens_flow(page):
            raise FlowCharacterCreationError(
                "menu lateral 'Characters'/'Personagens' não encontrado (drawer fechado ou "
                "layout do Flow alterado)."
            )
        page.wait_for_timeout(900)
    except Exception as _e:
        _passo_falhou("2_aba_characters", str(_e))

    # 3. New character / Novo personagem
    try:
        btn_upload = page.locator('button.create-character-footer-mode-button:has-text("Upload"), button:has-text("Upload"), button:has-text("Fazer upload")').first
        if btn_upload.is_visible(timeout=1500):
            pw_log("[FLOW] Já está na tela do assistente de personagem — avançando para upload.")
        else:
            tile = page.locator('flow-custom-tile:has-text("New character") button, flow-custom-tile:has-text("Novo personagem") button, button:has-text("New character"), button:has-text("Novo personagem")').first
            if tile.is_visible(timeout=5000):
                tile.click()
                page.wait_for_timeout(900)
    except Exception as _e:
        _passo_falhou("3_new_character", str(_e))

    # 4. Upload da foto de referência via file chooser
    try:
        with page.expect_file_chooser(timeout=7000) as fc_info:
            page.locator('button.create-character-footer-mode-button:has-text("Upload"), button.create-character-footer-mode-button:has-text("Fazer upload")').first.click(timeout=5000)
        fc_info.value.set_files(str(caminho_foto))
        page.wait_for_timeout(2500)
    except Exception as _e:
        _passo_falhou("4_upload_foto", str(_e))

    # 5. Preenche o nome oficial do personagem — SEMPRE com '@' (ex: '@Coringa')
    nome_flow = nome_personagem if str(nome_personagem).startswith("@") else f"@{nome_personagem}"
    try:
        input_nome = page.locator('input.name-input[placeholder="Character name"], input.name-input[placeholder="Nome do personagem"], input.name-input').first
        input_nome.fill(str(nome_flow), timeout=5000)
        page.wait_for_timeout(300)
    except Exception as _e:
        _passo_falhou("5_nome", str(_e))

    # 6. Descrição de personalidade — deixada em BRANCO de propósito (opcional;
    #    decisão: manter o mais simples. Preservação facial vem da foto oficial
    #    promovida no passo 7 + nome estável).

    # 7. PASSO CRÍTICO (descoberto em teste manual real — NÃO REMOVER):
    #    clicar na miniatura da foto no histórico para promovê-la a PORTRAIT
    #    oficial do card. Sem esse clique, a foto fica apenas no histórico do
    #    rascunho e NÃO vira o retrato do personagem (resultado: avatar genérico
    #    / rosto aleatório na geração).
    #    NOTA (teste isolado real): a miniatura só aparece DEPOIS que o Flow
    #    termina de gerar o preview do portrait (pode levar >10s). Por isso
    #    aguardamos até 60s pelo seletor antes de clicar — nunca clicar às cegas.
    try:
        _hist_img = page.locator('flow-editor-history-step-image img.image.clickable').first
        _hist_img.wait_for(state="visible", timeout=60000)
        _hist_img.click(timeout=5000)
        page.wait_for_timeout(900)
    except Exception as _e:
        _passo_falhou("7_promover_portrait_oficial", str(_e))

    # 8. Done / Concluir / Concluído / Pronto (finaliza a criação do personagem)
    #    CORREÇÃO 1: en PT-BR el botón real usa "Concluir" (infinitivo); se lista
    #    explícitamente antes de "Done"/"Concluído"/"Pronto" para no estallar timeout.
    try:
        page.locator(
            'button.flow-button-secondary:has-text("Concluir"), '
            'button.flow-button-secondary:has-text("Done"), '
            'button.flow-button-secondary:has-text("Concluído"), '
            'button.flow-button-secondary:has-text("Pronto"), '
            'button:has-text("Concluir"), '
            'button:has-text("Done")'
        ).first.click(timeout=7000)
        page.wait_for_timeout(2500)
    except Exception as _e:
        _passo_falhou("8_done", str(_e))

    # 9. Validação REAL: confirma que o personagem foi criado com sucesso
    #    (verificando no popup '@' e/ou na lista de personagens do canvas). NUNCA simula sucesso.
    try:
        page.wait_for_timeout(3500)
        _editor = _localizar_editor_prompt(page)
        if _editor is None:
            _editor = page.locator(
                'div.ProseMirror[contenteditable="true"], div[contenteditable="true"]:not(aside *):not([role="dialog"] *)'
            ).first
        _popup_abriu = False
        _dialog = None
        if _editor and _editor.is_visible(timeout=4000):
            try:
                _editor.click()
                page.wait_for_timeout(200)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(100)
                page.keyboard.type("@", delay=60)
                page.wait_for_timeout(1000)
                for sel_dlg in (
                    'div.cdk-overlay-pane:has-text("Search assets")',
                    'div.cdk-overlay-pane:has-text("Characters")',
                    'div.cdk-overlay-pane:has-text("Personagens")',
                    'div[role="dialog"]',
                    '[role="dialog"]',
                    'div.cdk-overlay-pane',
                ):
                    loc_d = page.locator(sel_dlg).first
                    if loc_d.is_visible(timeout=3000):
                        _dialog = loc_d
                        _popup_abriu = True
                        break
            except Exception as _e_open:
                pw_log(f"[FLOW] Aviso ao digitar '@' no editor: {_e_open}", level="warn")
        _encontrado = False
        _nome_com_arroba = nome_personagem if str(nome_personagem).startswith("@") else f"@{nome_personagem}"
        _nome_sem_arroba = str(nome_personagem).lstrip("@")
        _alvos_busca = [_nome_com_arroba, _nome_sem_arroba]

        if _popup_abriu and _dialog:
            pw_log("[FLOW] Popup '@' detectado com sucesso. Verificando presença do personagem...")
            for alvo in _alvos_busca:
                for sel_item in (
                    f'[role="option"]:has-text("{alvo}")',
                    f'[role="button"]:has-text("{alvo}")',
                    f'div:has-text("{alvo}")',
                    f'span:has-text("{alvo}")',
                    f'img[alt*="{alvo}" i]',
                ):
                    if _dialog.locator(sel_item).first.is_visible(timeout=1500):
                        _encontrado = True
                        break
                if _encontrado:
                    break

            if not _encontrado:
                tab_pers = _dialog.locator(
                    'div:has-text("Characters"), div:has-text("Personagens"), button:has-text("Characters"), button:has-text("Personagens")'
                ).first
                if tab_pers.is_visible(timeout=1500):
                    try:
                        tab_pers.click(timeout=1500)
                    except Exception:
                        try:
                            tab_pers.click(force=True, timeout=1500)
                        except Exception:
                            pass
                    page.wait_for_timeout(500)
                    for alvo in _alvos_busca:
                        for sel_item in (
                            f'[role="option"]:has-text("{alvo}")',
                            f'[role="button"]:has-text("{alvo}")',
                            f'div:has-text("{alvo}")',
                            f'span:has-text("{alvo}")',
                            f'img[alt*="{alvo}" i]',
                        ):
                            if _dialog.locator(sel_item).first.is_visible(timeout=1500):
                                _encontrado = True
                                break
                        if _encontrado:
                            break

        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass

        # Fallback de validação no Canvas se o popup não confirmou
        if not _encontrado:
            pw_log(f"[FLOW] Verificando se '{_nome_com_arroba}' consta no canvas/drawer do projeto...")
            for alvo in _alvos_busca:
                for sel_canvas in (
                    f'flow-custom-tile:has-text("{alvo}")',
                    f'div.card:has-text("{alvo}")',
                    f'div[role="button"]:has-text("{alvo}")',
                    f'div:has-text("{alvo}")',
                    f'span:has-text("{alvo}")',
                ):
                    loc_c = page.locator(sel_canvas).first
                    if loc_c.is_visible(timeout=1500):
                        _encontrado = True
                        pw_log(f"[FLOW] Personagem '{alvo}' validado com sucesso no canvas.")
                        break
                if _encontrado:
                    break

        if not _encontrado:
            _passo_falhou(
                "9_validacao_popup",
                f"'{_nome_com_arroba}' NÃO foi confirmado no popup '@' (ou na lista de personagens) após a criação",
            )
    except FlowCharacterCreationError:
        raise
    except Exception as _e:
        _passo_falhou("9_validacao_popup", str(_e))

    _shot("sucesso")
    pw_log(f"CHARACTER_AUTO_CREATED_OK: '{nome_exibicao}' criado e confirmado no popup '@'.")
    return True


def incluir_referencia_personagem(page, reference_path: str = "reference.png",
                                  nome_personagem: str = "", tag_personagem: str = "") -> bool:
    """
    Inclui a imagem/referência do personagem no prompt do Google Flow.
    Fluxo: clicar em '+' → modal abre → clicar aba 'Characters'/'Uploads' →
    clicar no card do personagem/mídia → 'Incluir no comando'.

    Quando nome_personagem/tag_personagem é fornecido, a busca na aba
    Characters prioriza o NOME DO PERSONAGEM (ex: "@Marcos"/"Marcos"), que
    é como o card aparece no Flow nativo — NÃO o nome do arquivo local de
    referência (ex: "reference.png"). Para uploads de mídia (sem personagem),
    mantém a busca por nome do arquivo (comportamento original).
    """
    global _debug_modal_done
    nome_arq = Path(reference_path).name if reference_path else "reference.png"
    nome_personagem = str(nome_personagem or "").strip()
    tag_personagem = str(tag_personagem or "").strip()
    nome_busca_personagem = nome_personagem or tag_personagem.lstrip("@") or ""
    # Também tenta com '@' (o card no Flow pode exibir "@Nome" ou "Nome")
    alvos_personagem = []
    if nome_busca_personagem:
        base_p = nome_busca_personagem.lstrip("@")
        for variante in (base_p, "@" + base_p):
            alvos_personagem.append(variante)
    # Nomes de arquivo (fallback para uploads tradicionais)
    alvos_arquivo = []
    if reference_path:
        alvos_arquivo.append(nome_arq)
        alvos_arquivo.append(Path(nome_arq).stem)

    def _falha(passo: str, e: Exception = None):
        extra = f": {e}" if e else ""
        pw_log(f"[REFERENCIA] Falha ao incluir '{nome_arq}' — passo '{passo}'{extra}", level="warn")
        print(f"[REFERENCIA] ERRO passo='{passo}'{extra}", flush=True)

    try:
        # 1. Botão '+' do campo de prompt (Ingredients / Add image / Menu trigger)
        btn_mais = None
        for sel in [
            'button[aria-label="Add ingredients to the prompt box"]',
            'button[aria-label*="Add ingredients" i]',
            'button.add-menu-trigger',
            'button[aria-label*="ingredient" i]',
            'button[aria-label="Add image"]',
            'button[aria-label*="Add image" i]',
            'button[aria-label*="Upload" i]',
            'button[aria-label*="adicionar" i]',
        ]:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=800):
                btn_mais = loc
                break
        if not btn_mais:
            idx_mais = page.evaluate("""() => {
                const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                const btns = Array.from(document.querySelectorAll('button'));
                for (let i = 0; i < btns.length; i++) {
                    const b = btns[i];
                    if (!vis(b)) continue;
                    const t = (b.textContent || '').trim().toLowerCase();
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (aria.includes('ingredient') || aria.includes('add image') || aria.includes('upload')
                        || t === 'add' || t === 'add_circle' || t === 'add_photo_alternate'
                        || t.includes('add_2')) {
                        return i;
                    }
                }
                return -1;
            }""")
            if isinstance(idx_mais, int) and idx_mais >= 0:
                btn_mais = page.locator("button").nth(idx_mais)
        if not btn_mais:
            _falha("botão '+' / Ingredients do campo de prompt não encontrado")
            return False

        btn_mais.click()
        page.wait_for_timeout(700)

        # 2. Aguarda o menu/overlay/dialog abrir (Angular CDK Overlay, popover ou dialog)
        dialog = None
        for d_sel in [
            'div.cdk-overlay-pane:has(.flow-add-menu-popover-content)',
            '.flow-add-menu-popover-content',
            'div.cdk-overlay-pane:has([role="menu"])',
            'div.cdk-overlay-pane',
            'div[role="dialog"]',
            'div[role="menu"]',
        ]:
            loc = page.locator(d_sel).first
            if loc.is_visible(timeout=1200):
                dialog = loc
                break

        if not dialog:
            _falha("menu de ingredientes/recursos não abriu após clicar em '+'")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        # PARTE 1 — DIAGNÓSTICO (rodar só na primeira cena com personagem)
        if not _debug_modal_done:
            try:
                pw_log(f"[DEBUG_MODAL] HTML do menu de ingredientes: {dialog.inner_html()[:600]}")
            except Exception as _e_diag:
                pw_log(f"[DEBUG_MODAL] Erro ao capturar diagnóstico: {_e_diag}", level="warn")
            _debug_modal_done = True

        # PARTE 2 — Navegação nas abas de recursos (Character / Media / Uploads).
        # Quando nome_personagem for fornecido, prioriza a aba Characters (o
        # card nativo do Flow lista o personagem pelo NOME, não por arquivo).
        try:
            if nome_busca_personagem:
                dialog.evaluate('''el => {
                    const tabs = Array.from(el.querySelectorAll("button[role=tab], button, div[role=button], [class*='tab']"));
                    const target = tabs.find(b => {
                        const t = (b.textContent || '').trim().toLowerCase();
                        return t === 'character' || t === 'characters' || t === 'personagem' || t === 'personagens';
                    });
                    if (target) target.click();
                }''')
            else:
                dialog.evaluate('''el => {
                    const tabs = Array.from(el.querySelectorAll("button[role=tab], button, div[role=button], [class*='tab']"));
                    const target = tabs.find(b => {
                        const t = (b.textContent || '').trim().toLowerCase();
                        return t === 'character' || t === 'characters' || t === 'personagem' || t === 'personagens' || t === 'media' || t === 'uploads';
                    });
                    if (target) target.click();
                }''')
            page.wait_for_timeout(600)
        except Exception:
            pass

        # 4. Clicar no item da lista — PRIORIDADE 1: nome do personagem (aba
        #    Characters do Flow nativo); PRIORIDADE 2: nome do arquivo (uploads);
        #    PRIORIDADE 3 (último recurso): primeiro card/item visível.
        item_ref = None

        def _procurar_por_alvo(alvos: list, so_exato: bool = False):
            """Tenta localizar um card/item pelo texto. Retorna locator ou None."""
            for alvo in alvos:
                if not alvo:
                    continue
                if not so_exato:
                    # Primeiro match exato (evita clicar em card errado com nome parecido)
                    try:
                        _loc = dialog.get_by_text(alvo, exact=True).first
                        if _loc.is_visible(timeout=800):
                            return _loc
                    except Exception:
                        pass
                try:
                    _loc = dialog.get_by_text(alvo).first
                    if _loc.is_visible(timeout=600):
                        return _loc
                except Exception:
                    pass
            return None

        # 4a. Personagem nativo (busca por nome — sem exato para tolerar card com @/sem @)
        if alvos_personagem and not item_ref:
            item_ref = _procurar_por_alvo(alvos_personagem)

        # 4b. Upload/mídia (busca por nome do arquivo)
        if alvos_arquivo and not item_ref:
            item_ref = _procurar_por_alvo(alvos_arquivo, so_exato=True)
            if not item_ref:
                item_ref = _procurar_por_alvo(alvos_arquivo)

        # 4c. Fallback: qualquer item de opção, card ou linha visível no menu
        if not item_ref:
            for f_sel in [
                '[role="option"]',
                '.flow-add-menu-item',
                'button[role="menuitem"]',
                'li',
                'div[role="button"]',
                '[class*="item"]',
            ]:
                try:
                    _loc = dialog.locator(f_sel).first
                    if _loc.is_visible(timeout=800):
                        item_ref = _loc
                        break
                except Exception:
                    pass

        if not item_ref:
            desc_alvo = nome_busca_personagem or nome_arq
            _falha(f"'{desc_alvo}' não encontrado no menu de ingredientes")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        item_ref.click()
        page.wait_for_timeout(500)

        # 5. Confirmação (se houver botão de confirmação, clica; no Flow moderno,
        #    clicar no card já insere a entidade — sem botão extra)
        confirm_clicado = False
        for confirm_sel in [
            'button:has-text("Add")',
            'button:has-text("Insert")',
            'button:has-text("Select")',
            'button:has-text("Incluir no comando")',
            'button:has-text("Incluir")',
        ]:
            try:
                cbtn = page.locator(confirm_sel).first
                if cbtn.is_visible(timeout=500):
                    cbtn.click(timeout=1000)
                    page.wait_for_timeout(300)
                    confirm_clicado = True
                    break
            except Exception:
                pass

        # Fecha menu se ainda estiver aberto
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(300)

        # 6. Evidência REAL de inserção — só considera sucesso quando a referência
        #    foi de fato anexada ao editor:
        #      (a) um botão de confirmação (Add/Insert/Incluir...) foi clicado, OU
        #      (b) o overlay fechou E o editor contém um marcador de entidade real
        #          (contenteditable=false / <img> / data-entity / chip).
        #    Clicar apenas num card da lista, sem confirmação e sem entidade no
        #    editor, NÃO é evidência — retorna False para o chamador decidir o
        #    fallback (evita a trava anti-rosto-aleatório ser enganada).
        if confirm_clicado:
            pw_log(f"[REFERENCIA] '{nome_arq}' inserida (botão de confirmação clicado).")
            return True

        overlay_aberto = False
        for ov_sel in ["div.cdk-overlay-pane", "div[role='dialog']", "div[role='menu']"]:
            try:
                if page.locator(ov_sel).first.is_visible(timeout=300):
                    overlay_aberto = True
                    break
            except Exception:
                pass

        editor = _localizar_editor_prompt(page)
        if editor and not overlay_aberto:
            try:
                tem_entidade = bool(editor.evaluate(_JS_VERIFICA_CHIP_EDITOR))
            except Exception:
                tem_entidade = False
            if tem_entidade:
                pw_log(f"[REFERENCIA] '{nome_arq}' inserida (entidade real detectada no editor).")
                return True

        pw_log(
            f"[REFERENCIA] '{nome_arq}' clicada no menu, mas SEM evidência de inserção no editor "
            f"— retornando False (chamador decide o fallback).",
            level="warn"
        )
        return False
    except Exception as e:
        _falha("erro inesperado ao incluir referência", e)
        return False
    finally:
        try:
            page.keyboard.press("Escape")
            page.wait_for_selector("div[role='dialog']", state="hidden", timeout=3000)
            pw_log("[REFERENCIA] Modal fechado.")
        except:
            pw_log("[REFERENCIA] Modal pode não ter fechado — continuando.")
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

    stop_queue = stop_worker

    @staticmethod
    def is_running() -> bool:
        return FlowQueueWorker.get_worker().is_running_queue

    @staticmethod
    def get_cena_ativa() -> Dict[str, Any]:
        return FlowQueueWorker.get_worker().cena_ativa or {}

    @staticmethod
    def get_status() -> Dict[str, Any]:
        worker = FlowQueueWorker.get_worker()
        return {
            "conectado": worker._check_is_active(),
            "rodando_fila": worker.is_running_queue,
            "cena_ativa": worker.cena_ativa,
            "modo": worker.current_flow_mode or "desconhecido",
            "pause_reason": getattr(worker, "last_queue_pause_reason", ""),
            # PHASE 2 (ERRO 2): expuesto para la UI (banner/diálogo)
            "fallback_video_imagen": bool(getattr(worker, "_fallback_video_para_imagem", False)),
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

    @staticmethod
    def reconectar(projeto_id: str = "") -> Tuple[bool, str]:
        """Botão 'Reconectar ao Flow' — navega a aba à URL salva do projeto
        (projetos/<id>/flow_meta.json), sem iniciar a fila de produção."""
        worker = FlowQueueWorker.get_worker()
        return worker.reconectar_projeto_salvo(projeto_id)


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
        imagem_abs = character_svc.resolver_imagem_avatar_projeto(projeto_id)
    if not imagem_abs or not Path(imagem_abs).exists():
        return {
            "success": False,
            "error": (
                f"Falha na criação do personagem: nenhuma imagem de avatar/referência encontrada "
                f"para o projeto '{projeto_id}' (procurou em identidade.json, characters/*/reference.png "
                f"e references/*/reference.png). Registre o personagem ou envie uma imagem antes de "
                f"criar no Google Flow."
            ),
        }

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

            # 1. Entra na tela de criação de personagem — navega DIRETO para a URL
            #    oficial de personagens (a criação NÃO fica no canvas do projeto).
            if "/characters" not in page.url and "/character/" not in page.url:
                page.goto("https://labs.google/fx/pt/tools/flow/characters", timeout=30000)
                page.wait_for_timeout(2000)
                btn_criar_p = page.locator(
                    'a[href*="/character/new"], button:has-text("Novo personagem"), '
                    'button:has-text("Criar personagem")'
                ).first
                btn_criar_p.click()
                page.wait_for_timeout(2000)

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


def criar_avatar_flow_via_playwright(projeto_id: str, nome: str, imagem_abs: str) -> Dict[str, Any]:
    """Fluxo OFICIAL de criação de avatar no Google Flow.

    Conecta ao Chrome via CDP e executa `criar_personagem_flow` (a função REAL
    validada passo a passo: drawer Characters → 'New character' → upload da foto
    → promoção do retrato → Done → validação no popup '@').

    DIFERE de `criar_personagem_no_flow_direto` (fluxo legado via /characters):
    usa o fluxo nativo do Flow validado em teste manual.

    Retorna dict no mesmo formato de criar_personagem_no_flow_direto para a
    rota /personagem/<id>/criar_flow não precisar mudar o contrato.
    """
    import services.character_service as character_svc

    if not nome:
        return {"success": False, "error": "Falha na criação do avatar: nome do personagem não informado."}
    if not imagem_abs or not Path(imagem_abs).exists():
        imagem_abs = character_svc.resolver_imagem_avatar_projeto(projeto_id)
    if not imagem_abs or not Path(imagem_abs).exists():
        return {
            "success": False,
            "error": (
                f"Falha na criação do avatar: nenhuma imagem de avatar/referência encontrada "
                f"para o projeto '{projeto_id}'. Selecione uma foto de referência antes de criar."
            ),
        }

    ok_cdp, msg_cdp = ensure_chrome_cdp()
    if not ok_cdp:
        return {"success": False, "error": f"Falha na criação do avatar: etapa de conexão CDP não concluída ({msg_cdp})."}

    nome_limpo = str(nome).lstrip("@").strip()
    nome_flow = f"@{nome_limpo}"
    ref_flow = nome_flow
    flow_char_id = ""
    page = None

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            # Procura a aba do Flow em todos os contextos disponíveis
            target_page = None
            for _ in range(5):  # retry de até ~2.5s caso a aba esteja em transição
                for ctx in (browser.contexts or []):
                    for pg in ctx.pages:
                        try:
                            u = (pg.url or "").lower()
                            t = (pg.title() or "").lower()
                            # Não confunde com a interface local do Lira Studio / UltraCut
                            if "127.0.0.1" in u or "localhost" in u:
                                continue
                            if ("flow.google" in u or "labs.google" in u or "tools/flow" in u or
                                "google flow" in t):
                                target_page = pg
                                break
                        except Exception:
                            pass
                    if target_page:
                        break
                if target_page:
                    break
                time.sleep(0.5)
            # SE aba do Flow não encontrada → ABRIR automaticamente
            if not target_page:
                pw_log("[FLOW] Aba do Google Flow não encontrada no Chrome. Abrindo automaticamente...", level="warn")
                try:
                    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                    target_page = ctx.new_page()
                    target_page.goto("https://flow.google.com/", timeout=30000)
                    try:
                        target_page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    target_page.bring_to_front()
                    target_page.wait_for_timeout(2000)
                except Exception as e_open:
                    pw_log(f"[FLOW] Erro ao abrir nova aba do Flow: {e_open}", level="error")

            # DEPOIS procurar a aba (se target_page ainda for None, varre novamente)
            if not target_page:
                for ctx in (browser.contexts or []):
                    for pg in ctx.pages:
                        try:
                            u = (pg.url or "").lower()
                            t = (pg.title() or "").lower()
                            if "127.0.0.1" in u or "localhost" in u:
                                continue
                            if ("flow.google" in u or "labs.google" in u or "tools/flow" in u or
                                "google flow" in t):
                                target_page = pg
                                break
                        except Exception:
                            pass
                    if target_page:
                        break

            if not target_page:
                return {"success": False, "error": "Falha na criação do avatar: aba do Google Flow não encontrada e não pôde ser aberta no Chrome."}

            page = target_page
            page.bring_to_front()
            page.wait_for_timeout(500)

            # Valida acessibilidade do canvas (/project/) para criar_personagem_flow
            try:
                url_atual = page.url or ""
                if "/project/" not in url_atual:
                    saved_url = carregar_projeto_flow_url(projeto_id)
                    if saved_url and saved_url != url_atual:
                        pw_log(f"[FLOW] Navegando para o projeto salvo: {saved_url}")
                        try:
                            page.goto(saved_url, timeout=30000)
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass

                    if "/project/" not in (page.url or ""):
                        btn_novo = page.locator(
                            'button:has-text("Novo projeto"), '
                            'button:has-text("+ Novo projeto"), '
                            'button:has-text("Criar projeto"), '
                            'button:has-text("New project"), '
                            'button:has(i:has-text("add"))'
                        ).first
                        if btn_novo.is_visible(timeout=6000):
                            pw_log("[FLOW] Clicando em '+ Novo projeto' para abrir o canvas...")
                            btn_novo.click()
                            t0_wait = time.time()
                            while time.time() - t0_wait < 15:
                                if "/project/" in (page.url or ""):
                                    salvar_projeto_flow_url(projeto_id, page.url)
                                    break
                                page.wait_for_timeout(500)

                if "/project/" in (page.url or ""):
                    salvar_projeto_flow_url(projeto_id, page.url)
            except Exception as e_canvas:
                pw_log(f"[FLOW] Aviso na validação do canvas do projeto: {e_canvas}", level="warn")

            # GUARD: cria_personagem_flow exige o CANVAS do projeto aberto (a aba
            # lateral 'Characters' não existe na home do Flow). Se o clique em
            # '+ Novo projeto' abriu o projeto em OUTRA aba, adota essa aba.
            try:
                if "/project/" not in str(page.url or ""):
                    for _ctx in (browser.contexts or []):
                        for _pg in _ctx.pages:
                            try:
                                if "/project/" in (_pg.url or ""):
                                    page = _pg
                                    page.bring_to_front()
                                    break
                            except Exception:
                                continue
                        if "/project/" in str(page.url or ""):
                            break
            except Exception:
                pass
            if "/project/" not in str(page.url or ""):
                return {
                    "success": False,
                    "error": (
                        "Falha na criação do avatar: o canvas de um projeto não está aberto "
                        f"no Google Flow (URL atual: {str(page.url or '') or 'indefinida'}). "
                        "A aba lateral 'Characters' só existe DENTRO de um projeto. Abra o "
                        "projeto no Flow e tente novamente."
                    ),
                }

            # Executa a função REAL validada com o nome garantido com '@' (ex: '@Coringa')
            criado = criar_personagem_flow(page, nome_flow, str(imagem_abs))
            if not criado:
                return {
                    "success": False,
                    "error": (
                        f"Falha na criação do avatar '{nome_flow}' no Google Flow. "
                        "A automação não concluiu todos os passos. Verifique se o Flow "
                        "está com o canvas do projeto aberto e tente novamente."
                    ),
                }
            flow_char_id = f"flow-char-{nome_limpo.lower()}"

            # Sucesso — atualiza a identidade no Lira Studio
            character_svc.salvar_identidade_projeto(
                projeto_id=projeto_id,
                tipo="personagem",
                nome=nome_flow,
                referencia_flow=ref_flow,
                arquivo_origem=str(imagem_abs),
                visual_style="photorealistic_cinematic"
            )
            character_svc.atualizar_status_flow_personagem(
                projeto_id=projeto_id,
                created=True,
                flow_char_name=ref_flow,
                flow_char_id=flow_char_id
            )

            ident_final = character_svc.obter_identidade_projeto(projeto_id)
            print("[LOG] AVATAR_FLOW_CREATED_OK", flush=True)
            pw_log(f"AVATAR_FLOW_CREATED_OK: '{nome_flow}' criado e validado no Google Flow (via criar_personagem_flow).")

            return {
                "success": True,
                "nome": nome_flow,
                "referencia_flow": ref_flow,
                "flow_character_name": ref_flow,
                "flow_character_id": flow_char_id,
                "flow_character_created": True,
                "tipo": "personagem",
                "tipo_display": "PERSONAGEM COM FOTO",
                "imagem_abs": str(imagem_abs),
                "identidade": ident_final,
                "mensagem": f"Avatar '{nome_flow}' ({ref_flow}) criado e vinculado ao projeto com sucesso!"
            }
    except Exception as e:
        pw_log(f"Erro ao criar avatar no Flow via criar_personagem_flow: {e}", level="error")
        return {
            "success": False,
            "error": f"Falha na criação do avatar: etapa de processamento do Flow não concluída ({str(e)})."
        }

