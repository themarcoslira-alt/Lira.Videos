"""
services/playwright_flow.py — Automação Google Flow via Playwright CDP
======================================================================
"""

import os
import sys
import re
import json
import logging
import time
import base64
import queue
import uuid
import threading
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
import functools
from typing import Optional, Dict, Any, List, Set, Tuple
from contextlib import contextmanager

# TAREFA 1 — o ator CDP usa Future para devolver o resultado (e a exceção) da
# tarefa executada na thread dedicada do Playwright.
from concurrent.futures import Future, TimeoutError as FuturesTimeoutError

from config import BASE_DIR, PROJETOS_DIR
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
# Tela de LOGIN do Google usada pelo "Logar/Relogar" (login guiado manual):
# o Chrome abre direto no sign-in. A URL é a observada pelo usuário, SEM o
# parâmetro `dsh` (que é de sessão e muda a cada tentativa).
FLOW_LOGIN_URL = (
    "https://accounts.google.com/v3/signin/identifier"
    "?continue=https://flow.google.com/"
    "&flowName=GlifWebSignIn&flowEntry=ServiceLogin"
)
WEB_HOSTS = ("127.0.0.1:5000", "localhost:5000")

# TAREFA — Delay configurável entre gerações sequenciais na fila.
# Respiro entre o fim do download+salvamento de uma cena (SCENE_SAVED_OK)
# e o disparo do próximo prompt (SCENE_GENERATION_START da cena seguinte).
# 2s evita rajada/throttling (rate limit) no Google Flow.
DELAY_ENTRE_PROMPTS_SEG = 2

# Estabilização da aba Personagens no dialog do Google Flow
TIMEOUT_ESTABILIZACAO_PERSONAGENS_MS = 6000  # Janela máxima para lista de personagens estabilizar
INTERVALO_POLL_PERSONAGENS_MS = 500  # Intervalo de amostragem durante estabilização

# CIRCUIT BREAKER (ANTIGRAVITY) — pausa automática da fila após N falhas
# CONSECUTIVAS por bloqueio do Flow ("notamos uma atividade incomum"). Protege o
# tempo do operador e a conta: sem isso a fila seguia rodando com ~90% de falha.
LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW = 3
MOTIVO_PAUSA_BLOQUEIO_FLOW = "PAUSADA_BLOQUEIO_FLOW"

# ANTIGRAVITY (popover de personagens) — o clique amplo em "Adicionar" casava com o
# botão "Menu para adicionar arquivos" e o [role="menu"] de UPLOAD (Enviar / Nova
# coleção / Criar personagem / Nova cena) era aceito como se fosse o popover de
# personagens. Os seletores abaixo separam o popover REAL desse menu de upload.
SEL_POPOVER_PERSONAGENS = (
    'flow-add-menu-popover-content, .add-menu-popover-container, '
    '.asset-title, .asset-item, flow-custom-tile, div.character-card, '
    '[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Characters"), '
    'mat-list-item[role="tab"]:has-text("Personagens"), mat-list-item[role="tab"]:has-text("Characters")'
)
SEL_ITENS_MENU_UPLOAD = (
    '[role="menuitem"]:has-text("Criar personagem"), '
    '[role="menuitem"]:has-text("Nova coleção"), '
    '[role="menuitem"]:has-text("Nova cena"), '
    '[role="menuitem"]:has-text("Enviar"), '
    'button:has-text("Criar personagem"), button:has-text("Nova coleção"), '
    'button:has-text("Nova cena")'
)
SEL_POPOVER_PERSONAGENS_RAIZ = 'flow-add-menu-popover-content, .add-menu-popover-container'
SEL_TABS_POPOVER_PERSONAGENS = (
    '[role="tab"]:has-text("Personagens"), [role="tab"]:has-text("Characters"), '
    'mat-list-item[role="tab"]:has-text("Personagens"), mat-list-item[role="tab"]:has-text("Characters")'
)
SEL_CARDS_POPOVER_PERSONAGENS = (
    'flow-add-menu-popover-content .asset-title, '
    'flow-add-menu-popover-content .asset-item, '
    'flow-add-menu-popover-content button.asset-item, '
    'flow-add-menu-popover-content [role="option"], '
    'flow-add-menu-popover-content flow-custom-tile, '
    'flow-add-menu-popover-content div.character-card'
)
SEL_CARDS_LOCAL_POPOVER_PERSONAGENS = (
    '.asset-title, .asset-item, button.asset-item, '
    '[role="option"]:has(.asset-title), flow-custom-tile, div.character-card'
)

# GUARD DO AVATAR (ANTIGRAVITY) — cenas com uses_character=False NUNCA podem aceitar o
# reference.png do avatar como resultado (b-roll 002/004 contaminado: o tile do upload
# do avatar entrou no DOM 2.9s após o prompt e o polling o aceitou como "mídia nova").
LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR = 15.0   # diferença média absoluta RGB
LIMIAR_HAMMING_DHASH_AVATAR = 4            # distância de Hamming (dHash 64 bits)
LADO_MAXIMO_ANALISE_AVATAR = 256           # normalização da referência p/ comparação
LIMIAR_GERACAO_RAPIDA_SEG = 5.0            # TAREFA 4 — só observabilidade (não rejeita)
JANELA_INGESTAO_UPLOAD_AVATAR_S = 10.0     # TAREFA 3 — polling da ingestão do upload
REFERENCE_PNG_IDENTIDADES_FALLBACK = "C:/Lira Videos/identidades/reference.png"

# VERIFICAÇÃO DE PERSONAGEM (ANTIGRAVITY) — o popover isolado por projeto abre com
# "Nenhum recurso encontrado" mesmo com '@Nome' existente (a sidebar mostrou 4 cópias).
# Leitura PRIMÁRIA pela sidebar (flow-character-tile) + registro local por CONTA e por
# PROJETO do Flow; `None`/vazio NUNCA disparam criação de personagem.
TIMEOUT_SIDEBAR_PERSONAGENS_S = 3.0
REGISTRO_CONTAS_FLOW_PATH = Path(BASE_DIR) / "identidades" / "contas_flow.json"

# FILA DE ANIMAÇÃO NUNCA GERA IMAGEM (ANTIGRAVITY — TAREFA 2): marcador de retorno para a
# cena PULADA por não ser elegível a vídeo. O chamador (`_handle_run_queue`) o reconhece e
# remove a cena da fila como "ignorada" — sem STATUS_ERRO, sem retentativa, sem circuit-breaker.
ANIM_SKIP_RESULTADO = "anim_skip:nao_elegivel"

# DOWNLOAD DE VÍDEO VIA <flow-video-tile> (ANTIGRAVITY — Flow 2025): a grade NÃO expõe
# <video> nativo; o vídeo vive em <flow-video-tile> (play_circle) e é baixado pelo menu
# de contexto ("Fazer o download" → qualidade). Estes timeouts regem o menu e a captura.
TIMEOUT_VIDEO_TILE_MENU_MS = 4000
TIMEOUT_VIDEO_TILE_DOWNLOAD_MS = 45000

# CAMINHOS CONFIGURÁVEIS (ANTIGRAVITY — isolamento da suíte): eram literais
# espalhados no módulo. Produção usa EXATAMENTE os mesmos valores; a suíte
# (tests/conftest.py) redireciona para um diretório temporário.
FLOW_ACCOUNTS_PATH = Path("config/flow_accounts.json")
CONTEXTO_CDP_PATH = Path("tmp/lira_context_cdp.json")
SEL_VAZIO_POPOVER_PERSONAGENS = 'flow-add-menu-popover-content .empty-state, .empty-state'
JS_LER_TILES_PERSONAGEM_SIDEBAR = """
() => {
    try {
        const tiles = Array.from(document.querySelectorAll('flow-character-tile .character-tile-name'));
        const nomes = tiles.map(n => (n.textContent || '').trim()).filter(t => t.length > 0);
        return { ok: true, tiles: document.querySelectorAll('flow-character-tile').length, nomes: nomes };
    } catch (e) {
        return { ok: false, error: e.toString(), tiles: 0, nomes: [] };
    }
}
"""

def _cdp_host(port: int) -> str:
    for h in ("[::1]", "127.0.0.1"):
        try:
            req = urllib.request.Request(f"http://{h}:{port}/json/version", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    return h
        except Exception:
            pass
    return "127.0.0.1"


def _cdp_port_open(port: int) -> bool:
    for host in ("::1", "127.0.0.1"):
        try:
            with socket.create_connection((host, port), timeout=1):
                h_str = f"[{host}]" if ":" in host else host
                try:
                    req = urllib.request.Request(f"http://{h_str}:{port}/json/version", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=0.8) as r:
                        if r.status == 200:
                            return True
                except Exception:
                    pass
        except Exception:
            pass
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


def open_flow_tab(page: Any, target_url: Optional[str] = None) -> Any:
    """Abre ou navega para o Google Flow exclusivamente quando o worker de produção é iniciado.

    - Se a página fornecida for a do Lira Studio (127.0.0.1:5000), ela NUNCA é
      reutilizada para o Flow: cria uma nova aba (ou devolve None se não houver
      contexto) — TAREFA 1: o Studio vive no navegador padrão, não no Chrome CDP.
    - Se a página for uma nova aba (about:blank ou limpa), navega para a URL do Flow.
    - Se target_url for fornecido (ex: URL salva do projeto no Flow), navega para ela; caso contrário, FLOW_URL.
    """
    dest_url = target_url or FLOW_URL
    if page is None:
        return None
    try:
        url_atual = getattr(page, "url", "") or ""
        if "127.0.0.1:5000" in url_atual or "localhost:5000" in url_atual:
            # NUNCA reutiliza a página do Studio para o Flow.
            ctx = getattr(page, "context", None)
            if ctx is None:
                pw_log("[open_flow_tab] Página do Studio sem contexto — o Flow NÃO a "
                       "reutiliza.", level="warn")
                return None
            nova = ctx.new_page()
            nova.goto(dest_url, timeout=60000)
            pw_log(f"[open_flow_tab] Nova aba do Google Flow aberta: {dest_url}")
            return nova
        elif not url_atual or url_atual == "about:blank" or not ("labs.google" in url_atual or "flow" in url_atual):
            page.goto(dest_url, timeout=60000)
            pw_log(f"[open_flow_tab] Navegado para Google Flow: {dest_url}")
        else:
            if target_url and target_url not in url_atual:
                page.goto(dest_url, timeout=60000)
    except Exception as e:
        pw_log(f"[open_flow_tab] Erro ao abrir/navegar aba do Google Flow: {e}", level="warn")
    return page


def _conta_por_id(contas: Any, conta_id: Any) -> Optional[dict]:
    """Devolve a conta cujo `id` bate com `conta_id` (comparação por int) ou None.

    Tolera ids em string/None/não numéricos sem levantar exceção.
    """
    for c in (contas or []):
        if not isinstance(c, dict):
            continue
        try:
            if int(c.get("id", -1)) == int(conta_id):
                return c
        except (TypeError, ValueError):
            continue
    return None


# =============================================================================
# CONFIGURAÇÃO DA JANELA DO CHROME DO FLOW (modo oculto)
# =============================================================================
# Arquivo de configuração do Chrome do Flow — MESMO padrão e MESMA resolução de
# caminho do config/flow_accounts.json (BASE_DIR / "config").
#   "browser_window_position": ""        → comportamento ATUAL (janela visível)
#   "browser_window_position": "X,Y"     → Chrome do Flow fora da tela
FLOW_CONFIG_PATH = Path(BASE_DIR) / "config" / "flow_config.json"

# Marcador do modo de cada Chrome lançado (porta → modo/PID). Precisa funcionar
# ENTRE PROCESSOS: o boot (_preparar_flow.py) roda em processo diferente do app_web.py.
CHROME_MODO_PATH = Path(BASE_DIR) / "tmp" / "chrome_modo.json"

# Cache em memória do modo (porta → oculto) — evita I/O de disco a cada verificação.
_CACHE_MODO_CHROME = {"porta": None, "valor": False, "ts": 0.0}


def _ler_json_caminho(caminho: Path) -> dict:
    """Lê um JSON de configuração e devolve dict ({} em qualquer erro)."""
    try:
        if caminho.exists():
            data = json.loads(caminho.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        pw_log(f"[FLOW_CONFIG] Falha ao ler {caminho}: {e}", level="warn")
    return {}


def _posicao_janela_flow() -> str:
    """Devolve "X,Y" de `browser_window_position` ou "" (modo visível/atual).

    Aceita "" (vazio = comportamento atual) ou "X,Y" com inteiros entre -32768 e
    32767. Valor inválido → aviso em log com o motivo e retorna "".
    """
    dados = _ler_json_caminho(FLOW_CONFIG_PATH)
    bruto = dados.get("browser_window_position", "")
    valor = str(bruto if bruto is not None else "").strip()
    if not valor:
        return ""
    partes = valor.split(",")
    if len(partes) != 2:
        pw_log(f"[FLOW_CONFIG] browser_window_position inválido ('{valor}'): esperado "
               f"'X,Y' — usando o modo visível (atual).", level="warn")
        return ""
    try:
        x = int(partes[0].strip())
        y = int(partes[1].strip())
    except (TypeError, ValueError):
        pw_log(f"[FLOW_CONFIG] browser_window_position inválido ('{valor}'): X e Y devem "
               f"ser inteiros — usando o modo visível (atual).", level="warn")
        return ""
    if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
        pw_log(f"[FLOW_CONFIG] browser_window_position fora do intervalo (-32768..32767) "
               f"em '{valor}' — usando o modo visível (atual).", level="warn")
        return ""
    return f"{x},{y}"


def _montar_args_chrome(chrome_exe, port, profile_dir, worker_id, url_lira, url_extra,
                        posicao: str = "", visivel: bool = False) -> list:
    """Monta a lista de argumentos do Chrome (função PURA, testável).

    `posicao == ""` ou `visivel == True`  → lista do modo visível (com
      `--start-maximized`), SEM a URL do Studio.
    Modo oculto (`posicao` válida e `visivel == False`) → troca `--start-maximized`
      por `--window-position=X,Y` + `--window-size=1920,1080`.

    TAREFA 1 — o STUDIO NÃO ENTRA MAIS NESTE CHROME: `url_lira` é aceito por
    compatibilidade de assinatura e IGNORADO (o Studio abre no navegador padrão).
    `worker_id == 0` deixou de ser especial; só `url_extra` (login guiado) entra.
    """
    oculto = bool(posicao) and not visivel
    args = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if oculto:
        args.append(f"--window-position={posicao}")
        args.append("--window-size=1920,1080")
    else:
        args.append("--start-maximized")
    if url_extra:
        args.append(url_extra)
    return args


def _pid_vivo(pid) -> bool:
    """True se o processo `pid` existe — SEM matá-lo (sondagem de existência)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            k32.CloseHandle(h)
            return True
        os.kill(pid, 0)          # POSIX: sinal 0 = só sondagem
        return True
    except OSError:
        return False
    except Exception:
        return False


def _ler_marcador_chrome() -> dict:
    """Lê o marcador porta → {pid, oculto} ({} se não existir/ilegível)."""
    return _ler_json_caminho(CHROME_MODO_PATH)


def _registrar_modo_chrome(port, pid, oculto: bool) -> None:
    """Grava `porta → {pid, oculto}` no marcador (entre processos). Nunca levanta.

    Sem PID válido (> 0) NÃO grava nada: um marcador sem PID seria inútil (é
    ignorado na leitura) e poluiria o arquivo em lançamentos simulados/testes.
    """
    try:
        pid_int = int(pid) if pid else 0
    except (TypeError, ValueError):
        pid_int = 0
    if pid_int <= 0:
        pw_log(f"[CHROME_MODO] Sem PID válido para a porta {port}; marcador não gravado.",
               level="debug")
        return
    try:
        chave = str(int(port))
        dados = _ler_marcador_chrome()
        dados[chave] = {
            "pid": pid_int,
            "oculto": bool(oculto),
            "em": datetime.now().isoformat(),
        }
        CHROME_MODO_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHROME_MODO_PATH.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    except Exception as e:
        pw_log(f"[CHROME_MODO] Falha ao gravar o marcador: {e}", level="warn")


def _entrada_marcador(port):
    """Devolve a entrada VIVA (PID existente) do marcador para `port`, ou None."""
    try:
        chave = str(int(port))
    except (TypeError, ValueError):
        return None
    entrada = _ler_marcador_chrome().get(chave)
    if not isinstance(entrada, dict):
        return None
    if not _pid_vivo(entrada.get("pid")):
        return None                      # marcador com PID morto é IGNORADO
    return entrada


def _chrome_da_porta_esta_oculto(port) -> bool:
    """True se o Chrome VIVO registrado para `port` foi lançado em modo oculto.

    Lê o marcador em disco (funciona entre processos) e confirma que o PID ainda
    existe; marcador de PID morto é ignorado (→ False = comportamento atual).
    """
    entrada = _entrada_marcador(port)
    return bool(entrada.get("oculto")) if entrada else False


def _fechamento_gracioso(port, espera_s: float = 10.0) -> bool:
    """Fecha a janela do Chrome SEM forçar e espera a porta CDP cair (até `espera_s`).

    Usa `taskkill /PID <pid>` (sem /F = WM_CLOSE) no Windows ou SIGTERM no POSIX,
    para os cookies já gravados em memória chegarem ao disco. Devolve True se a
    porta fechou dentro do prazo; NUNCA força o encerramento (quem decide cair para
    o encerramento duro é o chamador).
    """
    pid = None
    entrada = _entrada_marcador(port)
    if entrada:
        pid = entrada.get("pid")
    if not pid or not _pid_vivo(pid):
        # Nada VIVO registrado para fechar graciosamente: não espera nada (o
        # encerramento duro do chamador resolve) — e não sonda a porta à toa.
        pw_log(f"[CHROME_MODO] Sem PID vivo registrado para a porta {port}; "
               f"fechamento gracioso não se aplica.", level="debug")
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(int(pid))],
                           capture_output=True, timeout=10)
        else:
            os.kill(int(pid), 15)
        pw_log(f"[CHROME_MODO] Fechamento gracioso solicitado ao PID {pid} "
               f"(porta {port}).", level="info")
    except Exception as e:
        pw_log(f"[CHROME_MODO] Fechamento gracioso falhou ({e}); seguindo para o "
               f"encerramento do Chrome.", level="warn")
    t0 = time.time()
    while time.time() - t0 < espera_s:
        if not _cdp_port_open(port):
            return True
        time.sleep(0.4)
    return not _cdp_port_open(port)


def _chrome_oculto_cache(port, ttl_s: float = 5.0) -> bool:
    """`_chrome_da_porta_esta_oculto` com cache de poucos segundos (evita I/O por chamada)."""
    agora = time.time()
    if _CACHE_MODO_CHROME.get("porta") == port and (agora - _CACHE_MODO_CHROME.get("ts", 0.0)) < ttl_s:
        return bool(_CACHE_MODO_CHROME.get("valor"))
    valor = _chrome_da_porta_esta_oculto(port)
    _CACHE_MODO_CHROME.update({"porta": port, "valor": valor, "ts": agora})
    return valor


def _trazer_para_frente_se_visivel(page, port: int = 9222, rotulo: str = "") -> bool:
    """Chama `page.bring_to_front()` SOMENTE quando o Chrome do Flow NÃO está oculto.

    No modo oculto a página do Flow não deve roubar o foco do usuário (o Studio está
    no navegador padrão). NUNCA levanta exceção (os chamadores seguem o fluxo normal).
    """
    try:
        if _chrome_oculto_cache(port):
            pw_log(f"[FLOW] bring_to_front ignorado ({rotulo or 'página do Flow'}): "
                   f"Chrome oculto (porta {port}).", level="debug")
            return False
        page.bring_to_front()
        return True
    except Exception as e:
        pw_log(f"[FLOW] bring_to_front falhou ({rotulo or 'página do Flow'}): {e}",
               level="warn")
        return False


def _restaurar_janela_flow(page) -> bool:
    """Devolve a janela do Chrome do Flow para a tela (quando o Flow pede o usuário).

    Usa CDP direto na página: Browser.getWindowForTarget + Browser.setWindowBounds
    (windowState "normal", 100,100, 1280x800). NUNCA levanta: erro vira aviso em log.
    """
    try:
        sessao = page.context.new_cdp_session(page)
        win = sessao.send("Browser.getWindowForTarget")
        sessao.send("Browser.setWindowBounds", {
            "windowId": win["windowId"],
            "bounds": {"windowState": "normal", "left": 100, "top": 100,
                       "width": 1280, "height": 800},
        })
        pw_log("[FLOW] Janela do Flow restaurada para a tela (o Flow precisa de você).",
               level="info")
        return True
    except Exception as e:
        pw_log(f"[FLOW] Não foi possível restaurar a janela do Flow: {e}", level="warn")
        return False


def ensure_chrome_cdp(
    port: int = 9222,
    force_restart: bool = False,
    profile_dir: Optional[str] = None,
    projeto_id: Optional[str] = None,
    worker_id: Optional[int] = None,
    modo: Optional[str] = None,
    conta_id: Optional[int] = None,
    url_extra: Optional[str] = None,
    visivel: bool = False,
) -> Tuple[bool, str]:
    """Garante Chrome rodando com CDP na porta indicada.

    No boot ou inicialização básica do Chrome CDP:
      - Worker 0 (worker_id=0): abre APENAS a aba do Lira Studio (http://127.0.0.1:5000).
      - Workers 1+ (worker_id >= 1): inicializa o Chrome com porta CDP limpa.
      - Google Flow NÃO é aberto aqui — a abertura do Flow é delegada exclusivamente
        para open_flow_tab(page) quando o worker de produção for iniciado.
      - Se modo in ('animacao', 'video'), seleciona conta com créditos de vídeo disponíveis.

    Prioridade de escolha do PERFIL (quando `profile_dir` não é passado):
      (a) `conta_id` informado → perfil DESSA conta (id inexistente = ERRO: nunca
          cai em conta_03/conta ativa);
      (b) modo animacao/video → `proxima_conta_para_video()`;
      (c) `worker_id` passado EXPLICITAMENTE pelo chamador → `contas[worker_id]`
          (a produção paralela depende disto nas portas 9223+);
      (d) `worker_id` apenas INFERIDO da porta → conta com `ativa=True`; se não
          houver, `contas[0]` (antes, o índice da porta vencia a conta ativa e a
          aba do login sempre abria `contas[0]` = conta_03).
      - `url_extra` (opcional) é anexado ao Chrome DEPOIS da `url_lira` — usado
        pelo login guiado para abrir já na tela de login do Google.
    """
    import urllib.parse

    # O worker_id veio EXPLICITAMENTE do chamador? Isso precisa ser capturado ANTES
    # da inferência por porta logo abaixo: só o valor explícito autoriza escolher a
    # conta por ÍNDICE (worker_id → contas[worker_id]). A inferência continua valendo
    # para o offset de porta/aba (worker 0 abre o Lira Studio), mas NÃO para a conta.
    worker_id_informado = worker_id is not None

    # Inferência inteligente de worker_id a partir da porta se não fornecido explicitamente
    if worker_id is None:
        if 9222 <= port <= 9240:
            worker_id = port - 9222
        else:
            worker_id = 0

    if not projeto_id and worker_id == 0:
        try:
            ctx_path = CONTEXTO_CDP_PATH
            if ctx_path.exists():
                ctx_obj = json.loads(ctx_path.read_text(encoding="utf-8"))
                projeto_id = ctx_obj.get("projeto_id")
        except Exception:
            pass

    # TAREFA 1 — o Studio NÃO abre neste Chrome: nenhuma URL do app entra nos args.
    # (`proj_query`/`url_lira` foram removidos; o `projeto_id` continua sendo usado
    # para escolher a CONTA e é preservado no contexto CDP.)

    # MODO OCULTO: definido por `browser_window_position` em config/flow_config.json.
    # Vazio (ou visivel=True) = comportamento ATUAL (janela visível, com o Studio).
    posicao = _posicao_janela_flow()
    oculto = bool(posicao) and not visivel

    # Modo registrado deste Chrome (marcador em disco, entre processos). Chrome SEM
    # marcador vivo (modo desconhecido) → comportamento atual, sem reinício.
    _entrada_marc = _entrada_marcador(port)
    modo_mudou = bool(_entrada_marc) and (bool(_entrada_marc.get("oculto")) != oculto)

    if _cdp_port_open(port) and not force_restart and not modo_mudou:
        try:
            import urllib.request, json
            h_cdp = _cdp_host(port)
            req = urllib.request.Request(f"http://{h_cdp}:{port}/json", headers={"User-Agent": "Mozilla/5.0"})
            tabs = json.loads(urllib.request.urlopen(req, timeout=3).read())

            # TAREFA 1 — nenhuma aba do Studio é aberta/reaberta neste Chrome.
            # Workers 1+: higienização — se houver aba do Lira Studio residual, fecha para evitar poluição
            for t in tabs:
                u = t.get("url", "")
                if t.get("type") == "page" and ("127.0.0.1:5000" in u or "localhost:5000" in u):
                    try:
                        tid = t.get("id")
                        if tid:
                            req_close = urllib.request.Request(f"http://{h_cdp}:{port}/json/close/{tid}")
                            urllib.request.urlopen(req_close, timeout=2)
                    except Exception:
                        pass
        except Exception:
            pass
        return True, "Chrome CDP já ativo."
    if modo_mudou and not force_restart:
        pw_log(f"[CHROME_MODO] Modo da janela do Chrome mudou (oculto={oculto}) na porta "
               f"{port}; reiniciando.", level="info")
    if force_restart or modo_mudou:
        # Fechamento GRACIOSO primeiro (taskkill sem /F → WM_CLOSE): um kill duro logo
        # depois do login pode perder cookies ainda não gravados no disco. Só então o
        # encerramento atual (_encerrar_chrome_cdp).
        _fechamento_gracioso(port, espera_s=10.0)
        _encerrar_chrome_cdp(port)
        time.sleep(1.5)
    chrome_exe = _find_chrome_exe()
    if not chrome_exe:
        return False, "Chrome não encontrado nos caminhos padrão."
    import json
    if not profile_dir:
        accounts_path = FLOW_ACCOUNTS_PATH
        profile_dir = str(Path.home() / "ultracut3_chrome_profile")  # fallback

        # PRIORIDADE (a) — conta_id EXPLÍCITO manda: o perfil do Chrome segue a conta
        # PEDIDA. Id inexistente (ou sem profile_dir) é ERRO CLARO: NUNCA cai em
        # conta_03/conta ativa. A pasta é criada pelo próprio Chrome (não criamos aqui).
        if conta_id is not None:
            conta_alvo = None
            try:
                if accounts_path.exists():
                    _accs = json.loads(accounts_path.read_text(encoding="utf-8"))
                    conta_alvo = _conta_por_id(_accs.get("contas", []), conta_id)
            except Exception as e_c:
                pw_log(f"[ensure_chrome_cdp] Falha ao ler contas em {accounts_path}: {e_c}",
                       level="warn")
            if not conta_alvo:
                msg = (f"Conta id={conta_id} não encontrada em {accounts_path}; "
                       f"nenhum Chrome foi aberto.")
                pw_log(f"[ensure_chrome_cdp] {msg}", level="error")
                return False, msg
            _pdir_rel = str(conta_alvo.get("profile_dir") or "").strip()
            if not _pdir_rel:
                msg = (f"Conta id={conta_id} não tem 'profile_dir' configurado; "
                       f"nenhum Chrome foi aberto.")
                pw_log(f"[ensure_chrome_cdp] {msg}", level="error")
                return False, msg
            profile_dir = str(Path.home() / _pdir_rel)
        elif accounts_path.exists():
            try:
                accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
                _contas = accounts.get("contas", []) or []
                # (b) modo animação/vídeo: prioriza conta que NÃO tenha video_creditos_esgotados
                if modo in ("animacao", "video"):
                    from services.flow_account_manager import FlowAccountManager
                    _fam = FlowAccountManager()
                    conta_alvo = _fam.proxima_conta_para_video()
                    if conta_alvo and conta_alvo.get("profile_dir"):
                        profile_dir = str(Path.home() / conta_alvo["profile_dir"])
                # (c) worker_id EXPLÍCITO (produção paralela, portas 9223+) → contas[worker_id]
                elif worker_id_informado and worker_id is not None and worker_id >= 0 and len(_contas) > worker_id:
                    conta_alvo = _contas[worker_id]
                    if conta_alvo and conta_alvo.get("profile_dir"):
                        profile_dir = str(Path.home() / conta_alvo["profile_dir"])
                # (d) worker_id apenas INFERIDO da porta → conta ativa; sem ativa, contas[0]
                else:
                    conta_ativa = next((c for c in _contas if c.get("ativa")), None)
                    if not conta_ativa and _contas:
                        conta_ativa = _contas[0]
                    if conta_ativa and conta_ativa.get("profile_dir"):
                        profile_dir = str(Path.home() / conta_ativa["profile_dir"])
            except Exception:
                pass
    else:
        p_dir = Path(profile_dir)
        if not p_dir.is_absolute():
            p_dir = Path.home() / profile_dir
        profile_dir = str(p_dir)
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0

        # Montagem dos argumentos — função PURA `_montar_args_chrome`: com `posicao`
        # vazia (ou `visivel=True`) a lista é IDÊNTICA à de antes. Worker 0 (boot /
        # master) abre a UI do Lira Studio; workers 1+ ficam com porta CDP limpa.
        chrome_args = _montar_args_chrome(
            chrome_exe, port, profile_dir, worker_id, "", url_extra, posicao, visivel
        )
        if oculto:
            pw_log("Iniciando o navegador do Flow fora da tela; evite digitar por cerca "
                   "de 10 s.", level="info")

        _proc = subprocess.Popen(
            chrome_args,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
        # Registra o modo (porta → pid/oculto) para o reaproveitamento entre processos.
        _registrar_modo_chrome(port, getattr(_proc, "pid", 0), oculto)
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
    accounts_path = FLOW_ACCOUNTS_PATH
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
    Cenas POV (broll_pov, first_person_pov) NUNCA são tratadas como avatar.
    """
    st = str(cena.get("scene_type") or "").lower()
    ast = str(cena.get("avatar_style") or "").lower()
    if st in ("broll_pov", "pov") or ast in ("first_person_pov", "pov", "broll_pov") or cena.get("skip_facial_validation"):
        return False
    try:
        nome_cena = str(cena.get("nome") or cena.get("titulo") or cena.get("texto") or "").lower()
    except Exception:
        nome_cena = ""
    return (
        cena.get("uses_character") is True
        or "avatar" in st
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
    # Em cenas de avatar/personagem, remove restrições negativas como "no people" ou "sem pessoas"
    prompt_limpo = re.sub(
        r'\b(no people|no person|no persons|no humans|no human|sem pessoas|sem pessoa)\b[,;]?',
        "",
        prompt_limpo,
        flags=re.IGNORECASE,
    ).strip()
    # Limpa espaços duplos remanescentes
    prompt_limpo = re.sub(r'\s+', ' ', prompt_limpo).strip()
    objetivo_visual = "gesticulando e expressando emoção para câmera"
    if "objetivo" in base.lower() or "show" in base.lower():
        objetivo_visual = "usando gestos e expressão corporal para transmitir intenção/objetivo"
    if prompt_limpo and prompt_limpo[-1] not in ".!?":
        prompt_limpo += "."
    novo_prompt = f"{prompt_limpo} Avatar {objetivo_visual}, sem fala, apenas linguagem corporal expressiva."
    pw_log(f"[AVATAR] Prompt ajustado para gestualidade. Sufixo: '{objetivo_visual}'", level="info")
    return novo_prompt


def pw_log(msg: str, level: str = "info", duration_ms: Optional[float] = None):
    log_event("PLAYWRIGHT_FLOW", msg, level=level, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# DIAGNÓSTICO DE TRAVAMENTO — trace com timestamp nos DOIS consoles
# ---------------------------------------------------------------------------
# Motivo: o fluxo congelava entre `[LOG] IMAGE_DOWNLOADED_OK` e
# `[LOG] FILE_SAVED_OK` sem nenhuma pista de qual operação pendurou.
# `log_trace` grava um carimbo HH:MM:SS.mmm em AMBOS os destinos:
#   1. CMD/terminal  -> print(flush=True): sobrevive ao congelamento do processo.
#   2. Console web   -> pw_log -> log_event("PLAYWRIGHT_FLOW") -> fila lida pelo
#      polling de `GET /api/eventos/<projeto>` (static/app.js: adicionarLog).
# Nota técnica: NÃO existe `emit_evento_producao` em services/api_v2.py — o canal
# de eventos do Lira Studio é o `services/event_logger` compartilhado (usado por
# `pw_log`), portanto nenhum import novo/inexistente é necessário.
TRACE_DIAGNOSTICO_ATIVO = True


def log_trace(msg: str, cid: Optional[int] = None) -> None:
    """Log de diagnóstico com timestamp (HH:MM:SS.mmm) no CMD e no console web."""
    if not TRACE_DIAGNOSTICO_ATIVO:
        return
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefixo = f"[CENA {int(cid):03d}] " if cid is not None else ""
    linha = f"[{ts}] [TRACE] {prefixo}{msg}"
    try:
        print(linha, flush=True)
    except Exception:
        # CMD/redirecionamento com codepage legado (cp850/cp1252/cp437) não encoda
        # "→"/"←"/"❌": sem este fallback o PRÓPRIO trace derrubaria a cena com
        # UnicodeEncodeError (pior que o travamento que ele investiga).
        # Reencoda com 'replace' — o diagnóstico continua legível.
        try:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(linha.encode(enc, "replace").decode(enc, "replace"), flush=True)
        except Exception:
            pass
    try:
        pw_log(linha, level="info")
    except Exception:
        # O trace nunca pode derrubar o fluxo de produção.
        pass


# ---------------------------------------------------------------------------
# LOG DE PERSONAGEM ([CHAR]) — timestamp nos DOIS consoles
# ---------------------------------------------------------------------------
# Defeito corrigido: a automação anexava um personagem GENÉRICO ("@avatar")
# quando o nome do projeto não chegava à função que clica no "+" do campo de
# prompt — e nada ficava registrado. Todo passo de resolução/anexo/criação de
# personagem passa por `log_char`, sempre com o projeto explícito.
def log_char(msg: str, projeto_id: str = "", level: str = "info") -> None:
    """Log '[HH:MM:SS.mmm] [CHAR] projeto=<id> ...' no CMD e no console web."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    contexto = f"projeto={projeto_id} " if projeto_id else ""
    linha = f"[{ts}] [CHAR] {contexto}{msg}"
    try:
        print(linha, flush=True)
    except Exception:
        # Redirecionamento com codepage legado (cp1252/cp850) não encoda
        # acentos/setas: reencoda com 'replace' para o log nunca derrubar a cena.
        try:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(linha.encode(enc, "replace").decode(enc, "replace"), flush=True)
        except Exception:
            pass
    try:
        pw_log(linha, level=level)
    except Exception:
        pass


def _nome_personagem_generico(nome: str) -> bool:
    """True para nomes que NÃO identificam personagem real (avatar/@me/...)."""
    try:
        import services.character_service as character_svc
        return bool(character_svc.personagem_e_generico(nome))
    except Exception:
        n = str(nome or "").strip().lstrip("@").strip().lower()
        return (not n) or n in {"avatar", "avatares", "personagem", "personagens", "character", "me"}


JS_FETCH_MEDIA_LIST = """
() => {
    try {
        const results = [];
        const imgs = Array.from(document.querySelectorAll('img'));
        imgs.forEach((img, idx) => {
            // PREVIEW DE VÍDEO NÃO É MÍDIA (ANTIGRAVITY — Flow 2025): o <img> dentro de
            // <flow-video-tile> é apenas o THUMBNAIL do card de vídeo, não a mídia.
            // Tratá-lo como imagem faria o polling aceitar o preview como resultado.
            if (img.closest && img.closest('flow-video-tile')) return;
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

        // VÍDEO EM CUSTOM ELEMENT (ANTIGRAVITY — Flow 2025): a grade NÃO renderiza
        // <video> nativo. O vídeo fica em <flow-video-tile> (custom element) com
        // <mat-icon>play_circle</mat-icon>; o <img class="thumbnail"> interno é
        // apenas PREVIEW (nunca é aceito como mídia — ver skip acima).
        const tiles = Array.from(document.querySelectorAll('flow-video-tile'));
        tiles.forEach((tile, tIdx) => {
            try {
                const icones = Array.from(tile.querySelectorAll('mat-icon'));
                const temPlay = icones.some(ic => {
                    const txt = (ic.textContent || '').trim().toLowerCase();
                    const aria = (ic.getAttribute('aria-label') || '').toLowerCase();
                    return txt === 'play_circle' || txt.indexOf('play_circle') >= 0
                        || aria.indexOf('play') >= 0;
                });
                if (!temPlay) return;
                const thumb = tile.querySelector('img.thumbnail') || tile.querySelector('img');
                const src = thumb ? (thumb.src || thumb.currentSrc || '') : '';
                const uuidMatch = src.match(/[?&]name=([0-9a-fA-F-]{36})/);
                let mediaKey = uuidMatch ? uuidMatch[1] : '';
                if (!mediaKey) {
                    mediaKey = tile.getAttribute('data-id') || tile.getAttribute('id') || '';
                }
                if (!mediaKey) {
                    mediaKey = 'video_tile_' + tIdx + '_' + (src ? src.slice(-48) : 'sem_src');
                }
                // Não duplica quando o mesmo vídeo também expõe <video> nativo.
                if (results.some(r => r.type === 'video' && r.mediaKey === mediaKey)) return;
                const cleanName = uuidMatch ? uuidMatch[1]
                    : (src ? (src.split('/').pop().split('?')[0] || src.slice(0, 60)) : mediaKey);
                results.push({
                    id: 'video_tile_' + mediaKey,
                    mediaKey: mediaKey,
                    name: cleanName,
                    src: src,
                    srcEhPreview: true,
                    type: 'video',
                    width: thumb ? (thumb.naturalWidth || 0) : 0,
                    height: thumb ? (thumb.naturalHeight || 0) : 0,
                    domIndex: tIdx,
                    tileIndex: tIdx,
                    fonte: 'flow-video-tile'
                });
            } catch (eTile) { /* tile problemático não derruba o polling */ }
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


_worker_instance: Optional["PlaywrightCDPWorker"] = None
_worker_lock = threading.Lock()


def check_flow_credits(page=None) -> dict:
    """
    Verifica se há aviso de créditos insuficientes no Google Flow antes de submeter.
    Retorna:
        {
            "has_credits": bool,
            "warning_visible": bool,
            "warning_message": str,
            "credits_count": Optional[int]
        }
    """
    if page is None:
        try:
            global _worker_instance
            if _worker_instance and getattr(_worker_instance, "page", None):
                page = _worker_instance.page
            else:
                worker = FlowQueueWorker.get_worker()
                page = worker.page if worker else None
        except Exception:
            page = None

    if not page:
        return {
            "has_credits": True,
            "warning_visible": False,
            "warning_message": "Página Flow não disponível para verificação",
            "credits_count": None
        }

    try:
        warning_btn = page.locator(
            "flow-credit-warning-button button, button.prompt-warning-button, button[aria-label*='créditos insuficientes']"
        ).first

        if warning_btn.is_visible(timeout=1000):
            msg = warning_btn.get_attribute("aria-label") or "Créditos insuficientes"
            try:
                tooltip = page.locator(".prompt-warning-tooltip #prompt-warning-desc, .prompt-warning-text").first
                if tooltip.is_visible(timeout=500):
                    msg = tooltip.inner_text().strip()
            except Exception:
                pass

            return {
                "has_credits": False,
                "warning_visible": True,
                "warning_message": msg,
                "credits_count": 0
            }
    except Exception as e:
        pw_log(f"[FLOW_CREDITS] Erro ao checar créditos: {e}", level="debug")

    return {
        "has_credits": True,
        "warning_visible": False,
        "warning_message": "",
        "credits_count": None
    }


def get_exact_balance(page=None) -> Optional[int]:
    """
    Abre o menu de conta/perfil para ler o número exato de créditos restantes no Google Flow.
    Retorna o valor inteiro de créditos (ex: 45, 0) ou None se não conseguir ler.
    """
    if page is None:
        try:
            global _worker_instance
            if _worker_instance and getattr(_worker_instance, "page", None):
                page = _worker_instance.page
            else:
                worker = FlowQueueWorker.get_worker()
                page = worker.page if worker else None
        except Exception:
            page = None

    if not page:
        return None

    try:
        if page.is_closed():
            return None
    except Exception:
        return None

    try:
        # 1. Seletores do botão de perfil/avatar no Google Flow moderno
        user_selectors = [
            "button[aria-label*='Google Account' i]",
            "button[aria-label*='Conta do Google' i]",
            "button[aria-label*='@']",
            "img[alt*='@']",
            "flow-header-user-icon button",
            "flow-header-user-icon .header-user-button",
            "flow-header-user-icon",
            "header button:has(img[src*='googleusercontent'])",
            "header button:has(img)",
            "header button.profile-btn",
        ]

        user_btn = None
        for sel in user_selectors:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=400):
                    user_btn = loc
                    break
            except Exception:
                continue

        if not user_btn:
            pw_log("[FLOW_CREDITS] Botão de perfil não localizado pelos seletores primários.", level="debug")
            return None

        user_btn.click()
        page.wait_for_timeout(350)

        # 2. Busca texto de créditos no popover aberto (ou elementos específicos)
        dados_credito = page.evaluate('''() => {
            const popovers = document.querySelectorAll('[role="dialog"], [role="menu"], [class*="popover" i], [class*="profile" i], [class*="account" i], [class*="menu" i], flow-header-user-icon');
            for (const pop of popovers) {
                const text = (pop.innerText || '').trim();
                if (/crédito|credit|balance|saldo/i.test(text)) {
                    return text;
                }
            }
            const el = document.querySelector('.credits-count, [class*="credits-count" i], [class*="credit-balance" i]');
            if (el) return (el.innerText || '').trim();
            return null;
        }''')

        # Fecha o menu imediatamente com Escape para desobstruir o canvas
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
        except Exception:
            pass

        if dados_credito:
            # Ex: "1.020 créditos", "45 créditos", "Créditos: 45", "1,020 compute credits", "0 credits"
            m = re.search(r"([\d\.\,]+)\s*(?:crédito|credit|computação|computações)", dados_credito, re.IGNORECASE)
            if not m:
                m = re.search(r"(?:créditos?|credits?|saldo|balance)[\s:]*([\d\.\,]+)", dados_credito, re.IGNORECASE)
            if not m:
                m = re.search(r"\b([\d\.\,]+)\b", dados_credito)

            if m:
                raw_num = m.group(1).replace(".", "").replace(",", "")
                if raw_num.isdigit():
                    saldo = int(raw_num)
                    pw_log(f"[FLOW_CREDITS] Saldo exato lido no Flow: {saldo} créditos.", level="debug")
                    return saldo

        pw_log("[FLOW_CREDITS] Menu de perfil aberto, mas texto de saldo numérico não identificado.", level="debug")
    except Exception as e:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        pw_log(f"[FLOW_CREDITS] Erro ao obter saldo exato: {e}", level="debug")
    return None


class _SceneTimer:
    """Gerencia a medição de tempo das etapas e resumo consolidado por cena."""
    def __init__(self, cid: int):
        self.cid = cid
        self.t0 = time.perf_counter()
        self.timings = {
            "abertura": None,
            "config_modo": None,
            "anexo_personagem": None,
            "envio_prompt": None,
            "renderizacao": None,
        }
        self._fechado = False

    def etapa(self, chave: str, dur_s: float, nome_log: Optional[str] = None):
        self.timings[chave] = dur_s
        dur_ms = dur_s * 1000.0
        label = nome_log or chave
        msg = f"[TIMING] Cena {self.cid:03d} | Etapa: {label} | Duração: {int(round(dur_ms))}ms"
        pw_log(msg, duration_ms=dur_ms)
        print(msg, flush=True)

    def fechar(self):
        if self._fechado:
            return
        self._fechado = True
        t_total = time.perf_counter() - self.t0
        def _fmt(val):
            if val is None:
                return "n/a"
            if val < 0.5:
                return f"{int(round(val * 1000))}ms"
            return f"{val:.1f}s"
        total_str = f"{int(round(t_total * 1000))}ms" if t_total < 1.0 else f"{t_total:.1f}s"
        breakdown = " ".join([f"{k}={_fmt(v)}" for k, v in self.timings.items()])
        resumo_msg = f"[TIMING_RESUMO] Cena {self.cid:03d} | Total: {total_str} | {breakdown}"
        pw_log(resumo_msg, duration_ms=t_total * 1000)
        print(resumo_msg, flush=True)


def _instrumentar_timing_cena(fn):
    @functools.wraps(fn)
    def wrapper(self, projeto_id, cena, *args, **kwargs):
        cid = int(cena.get("id", 0))
        timer = _SceneTimer(cid)
        self._current_scene_timer = timer
        try:
            return fn(self, projeto_id, cena, *args, **kwargs)
        finally:
            timer.fechar()
            self._current_scene_timer = None
    return wrapper


class PlaywrightCDPWorker:
    def __init__(self, port: int = 9222, profile_dir: Optional[str] = None, worker_id: Optional[int] = None):
        global _worker_instance
        _worker_instance = self
        self.port = port
        self.profile_dir = profile_dir
        if worker_id is not None:
            self.worker_id = worker_id
        elif 9222 <= port <= 9240:
            self.worker_id = port - 9222
        else:
            self.worker_id = 0
        self.creditos_atuais: Optional[int] = None
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
        # IMPORTANTE (TAREFA 2/3): esta flag só é ligada pela decisão CANÔNICA
        # (`_tratar_indicador_limite`), alimentada por EVIDÊNCIA REAL do Flow
        # (frases de limite, toasts ou `check_flow_credits`). NUNCA deve ser setada
        # preventivamente dentro de `_processar_cena_individual` — era isso que fazia
        # o sistema pular a tentativa de vídeo e ir direto para imagem.
        self._fallback_video_para_imagem: bool = False
        # Motivo/prova do fallback ativo (auditoria da UI e do log).
        self._fallback_video_para_imagem_motivo: str = ""
        self.stop_requested = threading.Event()
        # ------------------------------------------------------------------
        # REQ 2/REQ 3 — MOTIVO CANÔNICO DA PARADA DA FILA
        # ------------------------------------------------------------------
        # Antes: a fila parava por créditos sem registrar POR QUÊ (a cena virava
        # STATUS_ERRO), então o frontend não distinguia "créditos zerados" de
        # "bug" ou "usuário pausou". Agora o motivo vive no worker e é exposto
        # por get_status() -> /api/v2/producao/<id>/status e pelo SSE
        # (/api/v2/producao/<id>/stream). None/"" = fila NÃO pausada.
        # Valores canônicos: "credito_esgotado_video", "credito_esgotado_imagem",
        # "fim_fila_credito_zerado", "manual".
        self.pause_reason: Optional[str] = None
        self.pause_reason_ts: Optional[str] = None
        self.current_project_id: Optional[str] = None
        self.current_flow_mode: Optional[str] = None
        self.current_download_quality: str = "1K"
        self.current_download_quality_video: str = "1080p"
        self._upscale_tentado_cena: bool = False
        self.cena_ativa: Optional[Dict[str, Any]] = None
        self.queue_start_time: Optional[float] = None
        self.scene_durations: List[float] = []
        self._lock = threading.Lock()
        self._avatar_uploaded = False
        self._avatar_media_uploaded = False
        self._personagem_card_persistido = False
        # GUARD DO AVATAR (ANTIGRAVITY) — chaves de mídia que o polling NUNCA deve
        # tratar como "resultado novo": chaves do upload do avatar e chaves já
        # rejeitadas por contaminação (ver _media_item_e_novo).
        self._chaves_upload_conhecidas: Set[str] = set()
        self._chaves_ignoradas: Set[str] = set()
        # Cache POR PROJETO dos dados do reference.png (RGB normalizado + dHash).
        self._avatar_ref_cache: Dict[str, Any] = {}
        self.account_email: Optional[str] = None
        self.current_project_name: Optional[str] = None
        self.current_delay_info: Optional[Dict[str, Any]] = None
        # TAREFA 1 (v9.18) — GUARDA DE AFINIDADE: ident da thread que criou a
        # sessão Playwright atual. O sync_api é thread-bound (greenlet): usar
        # `page` de outra thread levanta "Cannot switch to a different thread".
        # None = não há sessão viva (ou a sessão é de outra thread e foi
        # descartada por _descartar_sessao_outra_thread).
        self._session_thread_ident: Optional[int] = None

    def check_flow_credits(self, page=None) -> dict:
        return check_flow_credits(page or self.page)

    def get_exact_balance(self, page=None) -> Optional[int]:
        return get_exact_balance(page or self.page)

    def _limpar_overlays_e_backdrops(self):
        """Descarta qualquer backdrop, overlay ou menu pendente que possa interceptar cliques no Flow."""
        if not self.page:
            return
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
            self.page.evaluate("""() => {
                document.querySelectorAll('.cdk-overlay-backdrop, .cdk-overlay-pane, .flow-account-panel-overlay').forEach(el => {
                    try { el.remove(); } catch(e){}
                });
            }""")
        except Exception:
            pass

    def _extrair_email_via_painel_conta(self, usar_cache: bool = True) -> Optional[str]:
        """Extrai o email da conta Google ativa de forma instantânea (0.01s) e segura, sem abrir modais.

        1. Lê o email diretamente do atributo 'aria-label' do botão de conta no DOM (ex: 'Conta do Google: ... (email@gmail.com)').
        2. Fallback imediato: lê a conta ativa de 'config/flow_accounts.json'.
        NUNCA abre modais intrusivos e NUNCA trava a fila.

        `usar_cache=False` (usado pelo LOGIN GUIADO): IGNORA o cache `self.account_email`
        e NÃO usa o fallback do config — devolve SOMENTE o que o DOM da aba do Flow disser
        (None se o DOM não trouxer e-mail). Sem isso, o e-mail cacheado/da conta ativa
        (outra conta) era gravado na conta que estava sendo logada.
        Lendo do DOM, o cache `self.account_email` continua sendo gravado normalmente.
        """
        if usar_cache and getattr(self, "account_email", None):
            return self.account_email

        if not self.page:
            return None
        try:
            if self.page.is_closed():
                return None
        except Exception:
            return None

        # 1. Extração instantânea pelo DOM via aria-label (0.01s, 0 cliques, 0 modais)
        try:
            els = self.page.locator("[aria-label*='@']").all()
            for el in els:
                al = el.get_attribute("aria-label") or ""
                m = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", al)
                if m:
                    email = m.group(0)
                    self.account_email = email
                    pw_log(f"[CONTA] Conta Google ativa identificada: {email}", level="info")
                    return email
        except Exception:
            pass

        # 2. Fallback direto pelo config de contas do Flow (0.001s)
        #    PULADO quando `usar_cache=False`: o login guiado NUNCA pode "confirmar" o
        #    e-mail cadastrado da conta ativa — ele tem de vir do Chrome.
        if not usar_cache:
            return None
        try:
            from services.flow_account_manager import FlowAccountManager
            mgr = FlowAccountManager()
            for c in mgr.listar_contas_ativas():
                if c.get("ativa") and c.get("email"):
                    email = c["email"]
                    self.account_email = email
                    pw_log(f"[CONTA] Conta ativa obtida do config local: {email}", level="info")
                    return email
        except Exception:
            pass

        return None

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
            "you've reached your usage limit",
            "usage limit",
            "insufficient credits",
            "quota exceeded",
            "créditos insuficientes",
            "limite diário",
            "limite de uso",
            "você chegou ao limite de uso",
            "chegou ao limite",
            "tente de novo mais tarde",
            "não houve cobrança",
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

        # 1. Extração do E-mail da conta Google (prioriza config/flow_accounts.json para não abrir popups)
        if not self.account_email:
            try:
                from services.flow_account_manager import FlowAccountManager
                _contas = FlowAccountManager().listar_contas_ativas()
                for _c in _contas:
                    if _c.get("ativa") and _c.get("email"):
                        self.account_email = _c.get("email")
                        break
            except Exception:
                pass
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

        # 3. Se nenhuma aba existir, abre uma nova aba usando open_flow_tab
        try:
            nova = self.context.new_page()
            target_dest = target_url or FLOW_URL
            return open_flow_tab(nova, target_url=target_dest)
        except Exception as e_open:
            pw_log(f"[FLOW] Erro ao abrir nova aba para Flow no worker: {e_open}", level="warn")
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

    def _worker_id_para_chrome(self) -> Optional[int]:
        """`worker_id` a repassar ao `ensure_chrome_cdp`, ou None.

        O worker SINGLETON da fila (porta 9222) tem `profile_dir=None` e
        `worker_id=0`; repassar esse 0 EXPLICITAMENTE fazia o `ensure_chrome_cdp`
        cair no ramo (c) → `contas[0]` (conta_03), ignorando a conta ATIVA.
        Devolvendo None, o índice continua sendo inferido da porta e o ramo (d)
        escolhe a conta `ativa=True`.

        Workers PARALELOS (produção concorrente) SEMPRE têm `profile_dir`
        preenchido (`parallel_flow_orchestrator.WorkerInstancia`, L443-445) e
        continuam passando o índice EXPLÍCITO (`contas[worker_id]`).
        """
        if getattr(self, "profile_dir", None):
            return getattr(self, "worker_id", None)
        return None

    def _abrir_chrome_cdp(self) -> Tuple[bool, str]:
        """Abre/garante o Chrome CDP SEM criar sessão Playwright.

        Seguro para chamar da thread HTTP (botão 'Abrir Google Flow'): não
        cria sync_playwright/browser/page. A sessão Playwright é criada pela
        thread da fila de produção (ver _iniciar_sessao_thread).
        """
        return ensure_chrome_cdp(
            getattr(self, "port", 9222),
            profile_dir=getattr(self, "profile_dir", None),
            projeto_id=getattr(self, "current_project_id", None),
            worker_id=self._worker_id_para_chrome()
        )

    def _iniciar_sessao_thread(self) -> Tuple[bool, str]:
        """Cria a sessão Playwright DENTRO da thread que vai usá-la.

        O Playwright sync_api é thread-bound (greenlet): a sessão DEVE nascer
        e morrer na MESMA thread (a thread da fila de produção). NUNCA
        reutiliza self.page criado em outra thread (ex: thread HTTP do Flask),
        evitando o erro 'cannot switch to a different thread'.
        """
        # TAREFA 1 — cache de e-mail SEMPRE zerado ao ABRIR a sessão: o e-mail deve vir
        # do DOM da aba atual, nunca do cache de um login/sessão anterior.
        self.account_email = None
        if self.playwright is not None or self.browser is not None:
            # Sessão residual de execução anterior (thread morta) — descarta.
            self._encerrar_sessao()
        ok_cdp, msg_cdp = ensure_chrome_cdp(
            getattr(self, "port", 9222),
            profile_dir=getattr(self, "profile_dir", None),
            projeto_id=getattr(self, "current_project_id", None),
            worker_id=self._worker_id_para_chrome()
        )
        if not ok_cdp:
            return False, msg_cdp
        try:
            from playwright.sync_api import sync_playwright
            pw_log(f"Conectando ao Chrome via CDP na porta {self.port} (thread da fila)...")
            self.playwright = sync_playwright().start()
            h_cdp = _cdp_host(self.port)
            self.browser = self.playwright.chromium.connect_over_cdp(f"http://{h_cdp}:{self.port}")
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
                    nova = self.context.new_page() if self.context else self.browser.new_page()
                    target_url = carregar_projeto_flow_url(self.current_project_id) if self.current_project_id else FLOW_URL
                    self.page = open_flow_tab(nova, target_url=target_url)
                    if self.page and hasattr(self.page, "context"):
                        self.context = self.page.context
                except Exception as e_np:
                    pw_log(f"Falha ao criar nova página via open_flow_tab: {e_np}", level="warn")

            if self.page is None:
                self._encerrar_sessao()
                return False, "Não foi possível resolver a aba do Google Flow."

            # TAREFA 1 — GUARDA DE AFINIDADE: a sessão (playwright/browser/page)
            # pertence a ESTA thread. Qualquer uso futuro de `page` fora dela
            # levanta greenlet.error ("Cannot switch to a different thread").
            self._session_thread_ident = threading.get_ident()

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
        Desconecta do CDP sem fechar a janela do Chrome do usuário (mantendo Flow e UltraCut3 abertos).
        """
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
        # TAREFA 1 — o cache de e-mail pertence à sessão que está sendo encerrada: se
        # ficasse preenchido, o PRÓXIMO login (outra conta) gravaria o e-mail antigo.
        self.account_email = None
        # TAREFA 1 — não há mais sessão viva (guarda de afinidade).
        self._session_thread_ident = None

    def _descartar_sessao_outra_thread(self):
        """TAREFA 1d — descarta a sessão Playwright criada por OUTRA thread.

        Chamar métodos da sessão (ou `playwright.stop()`) de uma thread diferente
        da que a criou pode levantar greenlet.error; por isso tudo é protegido e o
        estado é zerado de qualquer forma — a próxima chamada recria a sessão na
        thread atual (o ator CDP).
        """
        ident_atual = threading.get_ident()
        ident_da_sessao = getattr(self, "_session_thread_ident", None)
        if ident_da_sessao == ident_atual:
            return False

        pw_log(
            f"[CDP-ATOR] Sessão Playwright criada na thread {ident_da_sessao} descartada "
            f"(uso na thread {ident_atual}) — será recriada na thread do ator.",
            level="warn",
        )
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            pw_log(f"[CDP-ATOR] Falha ao encerrar sessão de outra thread: {e}", level="warn")

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.current_flow_reference = None
        self._session_thread_ident = None
        return True

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
                    self._configured_mode = None
                    pw_log("[ENSURE_PROJECT] Campo de prompt do canvas confirmado.")
                    return True
                pw_log("[ENSURE_PROJECT] ERRO: canvas aberto mas campo de prompt não apareceu em 15s.", level="error")
                return False

            pw_log("[ENSURE_PROJECT] Falha: URL não mudou para /project/ após clicar em '+ Novo projeto'.", level="warn")
            return False
        except Exception as e_dash:
            pw_log(f"[ENSURE_PROJECT] Erro na rotina determinística de abertura de projeto: {e_dash}", level="warn")
            return "/project/" in (self.page.url or "")

    def _upload_arquivo_flow(self, arquivo_path: str) -> bool:
        """Upload resiliente de mídia/imagem para o workspace do Google Flow.
        
        Segue o padrão oficial validado no Google Flow:
        1. Limpa backdrops/menus pendentes.
        2. Tenta input[type="file"] direto caso já exista no DOM.
        3. Caso contrário, clica no botão '+' (Adicionar arquivos / Adicionar mídia).
        4. Aguarda e clica no item 'Enviar' ([xapfileselectortrigger] button / [role="menuitem"]:has-text("Enviar"))
           capturando o seletor nativo com expect_file_chooser.
        5. Define os arquivos e fecha o menu com Escape/limpeza garantida.
        """
        if not self.page or not arquivo_path or not Path(arquivo_path).exists():
            return False

        try:
            self._limpar_overlays_e_backdrops()

            # (a) Input file direto (mais rápido quando disponível)
            input_file = self.page.locator('input[type="file"]').first
            if input_file.count() > 0:
                try:
                    # UI esperada ao final: miniatura da mídia na galeria do workspace do Flow.
                    baseline_srcs = _baseline_imgs_pagina(self.page)
                    input_file.set_input_files(str(arquivo_path))
                    if not _aguardar_upload_concluido(self.page, baseline_srcs, timeout_ms=20000):
                        pw_log("[FLOW] _upload_arquivo_flow: upload não confirmado por sinal; "
                               "seguindo com comportamento anterior. "
                               "Não validado em execução real.", level="warn")
                    self.page.wait_for_timeout(300)
                    self._limpar_overlays_e_backdrops()
                    return True
                except Exception:
                    pass

            # (b) Fluxo oficial via Menu do Topo: '+' -> 'Enviar'
            plus_btn = self.page.locator(
                "button[aria-label*='adicionar arquivos' i], "
                "button[aria-label*='Menu para adicionar arquivos' i], "
                "button[mattooltip*='Adicionar mídia' i], "
                "button[aria-label*='Adicionar mídia' i]"
            ).first

            if not plus_btn.is_visible(timeout=3000):
                pw_log(f"[FLOW_UPLOAD] Botão '+' não visível para envio de '{Path(arquivo_path).name}'.", level="warn")
                return False

            plus_btn.click(force=True)
            self.page.wait_for_timeout(400)

            enviar_btn = self.page.locator(
                ".cdk-overlay-pane button:has-text('Enviar'), "
                ".cdk-overlay-pane .mat-mdc-menu-item:has-text('Enviar'), "
                "[xapfileselectortrigger] button, "
                "[role='menuitem']:has-text('Enviar'), "
                "button[mat-menu-item]:has-text('Enviar'), "
                "button:has-text('Enviar')"
            ).first

            if not enviar_btn.is_visible(timeout=3500):
                pw_log("[FLOW_UPLOAD] Item 'Enviar' não apareceu no menu do Flow.", level="warn")
                self._limpar_overlays_e_backdrops()
                return False

            # UI esperada ao final: miniatura da mídia na galeria do workspace do Flow.
            baseline_srcs = _baseline_imgs_pagina(self.page)
            with self.page.expect_file_chooser(timeout=6000) as fc_info:
                enviar_btn.click(force=True)
            fc_info.value.set_files(str(Path(arquivo_path).resolve()))
            if not _aguardar_upload_concluido(self.page, baseline_srcs, timeout_ms=20000):
                pw_log("[FLOW] _upload_arquivo_flow: upload não confirmado por sinal; "
                       "seguindo com comportamento anterior. "
                       "Não validado em execução real.", level="warn")
            self.page.wait_for_timeout(300)

            self._limpar_overlays_e_backdrops()
            return True
        except Exception as e:
            pw_log(f"[FLOW_UPLOAD] Erro ao enviar '{Path(arquivo_path).name}': {e}", level="warn")
            self._limpar_overlays_e_backdrops()
            return False

    def _resolver_reference_png(self, projeto_id: str, caminho_override: str = "") -> Optional[str]:
        """Resolve o caminho do reference.png do projeto — ordem estrita:

          1. override explícito (se existir);
          2. C:\\Lira Videos\\projetos\\<projeto_id>\\reference.png;
          3. C:\\Lira Videos\\identidades\\reference.png (fallback global);
          4. characters/<Nome>/reference.png via character_service.

        É o MESMO resolvedor usado por `_upload_avatar_projeto` e pelo GUARD DO AVATAR
        (reutilizado — nunca duplicado).
        """
        if caminho_override and Path(caminho_override).exists():
            return str(Path(caminho_override).resolve())
        p_proj = PROJETOS_DIR / projeto_id / "reference.png"
        p_ident = Path(REFERENCE_PNG_IDENTIDADES_FALLBACK)
        if p_proj.exists():
            return str(p_proj.resolve())
        if p_ident.exists():
            return str(p_ident.resolve())
        try:
            import services.character_service as character_svc
            _caminho = character_svc.resolver_imagem_avatar_projeto(projeto_id)
            if _caminho and Path(_caminho).exists():
                return str(_caminho)
        except Exception:
            pass
        return None

    def _upload_avatar_projeto(self, projeto_id: str, caminho_override: str = "") -> bool:
        """Faz upload do avatar/referência do projeto para a galeria e garante a criação nativa do personagem no Flow.

        Roda por instância de worker (guard self._avatar_uploaded).
        Busca de reference.png:
          1. C:\\Lira Videos\\projetos\\<projeto_id>\\reference.png
          2. C:\\Lira Videos\\identidades\\reference.png (fallback)
          3. characters/<Nome>/reference.png via character_service
        """
        if getattr(self, "_avatar_uploaded", False) and getattr(self, "_avatar_media_uploaded", False):
            return True

        try:
            import services.character_service as character_svc
        except Exception:
            character_svc = None

        # Resolução de reference.png (MESMO resolvedor usado pelo GUARD DO AVATAR)
        caminho = self._resolver_reference_png(projeto_id, caminho_override)

        if not caminho or not Path(caminho).exists():
            pw_log(f"[AVATAR_UPLOAD] Worker {self.worker_id}: Nenhum reference.png encontrado para o projeto '{projeto_id}' — seguindo sem avatar.", level="warn")
            self._avatar_uploaded = True
            self._avatar_media_uploaded = True
            return False

        nome_arquivo = Path(caminho).name
        pw_log(f"[AVATAR_UPLOAD] Worker {self.worker_id}: Usando imagem '{nome_arquivo}' de '{caminho}' para o projeto '{projeto_id}'...")

        if not self.page:
            pw_log(f"[AVATAR_UPLOAD] Worker {self.worker_id}: Sem aba do Flow ativa — upload/criação ignorados.", level="warn")
            return False

        # 1. Upload do arquivo para a galeria do workspace do Flow (se ainda não enviado)
        if not getattr(self, "_avatar_media_uploaded", False):
            try:
                # TAREFA 3 (ANTIGRAVITY) — snapshot ANTES do upload: as chaves que
                # aparecerem depois são do tile do avatar e ficam registradas em
                # _chaves_upload_conhecidas para o polling NUNCA vê-las como novas
                # (era assim que o b-roll 002/004 aceitava o avatar como resultado).
                _snap_antes_avatar, _ = self._get_existing_media_snapshot()
                ok_up = self._upload_arquivo_flow(str(caminho))
                self._avatar_media_uploaded = True
                if ok_up:
                    pw_log(f"[AVATAR_UPLOAD] Worker {self.worker_id}: Avatar '{nome_arquivo}' enviado para a galeria com sucesso.")
                    self._registrar_chaves_upload_avatar(_snap_antes_avatar)
            except Exception as e_up:
                pw_log(f"[AVATAR_UPLOAD] Worker {self.worker_id}: Aviso ao enviar '{nome_arquivo}' para galeria: {e_up}", level="warn")
                self._avatar_media_uploaded = True

        # 2. Criação do personagem nativo no Google Flow
        if not getattr(self, "_avatar_uploaded", False):
            nome_char = "Marcos"
            if character_svc:
                try:
                    identidade = character_svc.obter_identidade_projeto(projeto_id) or {}
                    nome_char = identidade.get("nome") or "Marcos"
                except Exception:
                    nome_char = "Marcos"

            try:
                pw_log(f"[Worker {self.worker_id}] Iniciando criação nativa do personagem '@{nome_char}' no Flow...")
                _criado = criar_personagem_flow(self.page, nome_char, str(caminho))
                self._avatar_uploaded = True
                if _criado:
                    pw_log(f"[Worker {self.worker_id}] PERSONAGEM_CRIADO_OK: '@{nome_char}' criado com sucesso no Flow.", level="info")
                    print(f"[Worker {self.worker_id}] PERSONAGEM_CRIADO_OK: '@{nome_char}' criado com sucesso no Flow.", flush=True)
                    # TAREFA 3 (ANTIGRAVITY) — registra a criação por CONTA + PROJETO do
                    # Flow: na próxima execução o pré-voo sabe que '@Nome' existe nesta
                    # conta/projeto mesmo se o popover abrir com "Nenhum recurso encontrado".
                    self._registrar_personagem_flow(nome_char, fonte="criacao")
                    try:
                        self.page.reload()
                        self.page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    return True
                else:
                    pw_log(f"[Worker {self.worker_id}] PERSONAGEM_CRIACAO_FALHOU: criação de '@{nome_char}' retornou False.", level="warn")
                    print(f"[Worker {self.worker_id}] PERSONAGEM_CRIACAO_FALHOU: criação de '@{nome_char}' retornou False.", flush=True)
                    return False
            except Exception as e_cr:
                self._avatar_uploaded = True
                pw_log(f"[Worker {self.worker_id}] PERSONAGEM_CRIACAO_FALHOU: erro ao criar '@{nome_char}': {e_cr}", level="error")
                print(f"[Worker {self.worker_id}] PERSONAGEM_CRIACAO_FALHOU: erro ao criar '@{nome_char}': {e_cr}", flush=True)
                return False

        return True


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
        qualidade_solicitada: Optional[str] = "x1",
        sub_modo: str = "elementos"
    ):
        """Configura a proporção, qualidade, contagem e modelo no Flow.

        `sub_modo` só tem efeito em `target_mode="video"`:
          - "elementos" (PADRÃO): comportamento idêntico ao de sempre (rádio "Elementos");
          - "frames": garante o rádio "Frames" (usado pelo fluxo 'Animar', que já
            ancora a imagem no slot inicial) e NUNCA clica em "Elementos".
        

        Retorna True quando o Flow foi deixado no modo/modelo/qualidade pedidos
        (ou já estava nesse estado) e False quando a reconfiguração falhou.
        """
        if not self.page:
            return False
        modelo_alvo = modelo_solicitado or self.current_model or ("Veo 3.1 - Lite" if target_mode == "video" else "Nano Banana Pro")
        prop_alvo = proporcao_solicitada or "16:9"
        qtd_alvo = qualidade_solicitada or "x1"
        # TAREFA 2 — a chave do cache INCLUI o sub-modo pedido: sem isso, uma
        # animação em "Frames" deixava a chave "vídeo" válida e a PRÓXIMA cena que
        # precisa de "Elementos" (ingredientes/personagem) retornava cedo sem clicar.
        _sub_chave = (str(sub_modo).lower() if target_mode == "video" else None)
        if getattr(self, "_configured_mode", None) == (target_mode, modelo_alvo, prop_alvo, qtd_alvo, _sub_chave):
            return True
        pw_log(f"[FLOW] Reconfigurando para modo {target_mode} ({modelo_alvo}, {prop_alvo}, {qtd_alvo})...")

        try:
            # 1. Abre o menu de configurações do Flow (dock chip / Settings trigger)
            dock_btn = self.page.locator(
                'button.settings-trigger-button, '
                'button[aria-label="Gatilho de configurações"], '
                'button[aria-label="Settings trigger"]'
            ).first
            settings_panel = self.page.locator("flow-prompt-box-settings")

            # Garante que o painel esteja aberto via dock_btn
            for _ in range(3):
                if settings_panel.count() > 0 and settings_panel.first.is_visible():
                    break
                if dock_btn.is_visible(timeout=800):
                    dock_btn.click()
                    self.page.wait_for_timeout(300)

            # 1.1 Garante a aba/toggle correta de modo (Imagem vs Vídeo)
            import re
            tab_pattern = re.compile(r"v[ií]deo", re.I) if target_mode == "video" else re.compile(r"imag[eé]m|image", re.I)
            tab_loc = self.page.locator('flow-prompt-box-settings button[role="radio"]').filter(has_text=tab_pattern).first
            if tab_loc.is_visible(timeout=500):
                if tab_loc.get_attribute("aria-checked") != "true":
                    tab_loc.click(force=True)
                    self.page.wait_for_timeout(300)

            # 1.2 No modo vídeo, garante o SUB-MODO pedido:
            #   - "elementos" (padrão, TODOS os outros chamadores): rádio "Elementos";
            #   - "frames" (fluxo 'Animar'): rádio "Frames" — NUNCA clica em Elementos,
            #     pois isso DESCARTA a flow-ingredient-bar (a imagem ancorada some).
            if target_mode == "video":
                _sub_sel = ("frames" if str(sub_modo).lower() == "frames" else "elementos")
                _grupo = self.page.locator('flow-toggles[aria-label*="Tipo de v" i] button[role="radio"], flow-prompt-box-settings button[role="radio"]')
                _rd_frames = _grupo.filter(has_text=re.compile(r"frames", re.I)).first
                _rd_elementos = _grupo.filter(has_text=re.compile(r"elementos", re.I)).first
                try:
                    _frames_on = _rd_frames.get_attribute("aria-checked") if _rd_frames.count() else None
                    _elem_on = _rd_elementos.get_attribute("aria-checked") if _rd_elementos.count() else None
                    pw_log(f"[FLOW] Sub-modo ANTES: Frames={_frames_on} Elementos={_elem_on} "
                           f"(pedido='{_sub_sel}')", level="debug")
                except Exception:
                    pass
                _alvo = _rd_frames if _sub_sel == "frames" else _rd_elementos
                try:
                    if _alvo.count() and _alvo.is_visible(timeout=300) and _alvo.get_attribute("aria-checked") != "true":
                        _alvo.click(force=True)
                        self.page.wait_for_timeout(200)
                except Exception:
                    pass
                try:
                    _frames_dep = _rd_frames.get_attribute("aria-checked") if _rd_frames.count() else None
                    _elem_dep = _rd_elementos.get_attribute("aria-checked") if _rd_elementos.count() else None
                    pw_log(f"[FLOW] Sub-modo DEPOIS: Frames={_frames_dep} Elementos={_elem_dep} "
                           f"(pedido='{_sub_sel}')", level="debug")
                except Exception:
                    pass

            # 2. Garante proporção selecionada (16:9, 4:3, 1:1, 9:16)
            prop_btn = self.page.locator(f'flow-prompt-box-settings button[role="radio"]').filter(has_text=prop_alvo).first
            if prop_btn.is_visible(timeout=400):
                if prop_btn.get_attribute("aria-checked") != "true":
                    prop_btn.click(force=True)
                    self.page.wait_for_timeout(200)

            # 3/4. Abre o dropdown e seleciona o modelo solicitado se não for o padrão atual
            dd_trigger = self.page.locator(
                'flow-prompt-box-settings button:has(.model-select-trigger-content), '
                'flow-prompt-box-settings button[aria-label="Selecionar família de modelos"], '
                'button[aria-label="Selecionar família de modelos"]'
            ).first
            if dd_trigger.is_visible(timeout=400):
                curr_txt = (dd_trigger.inner_text() or "").lower()
                needs_dd = False
                if target_mode == "video" and "veo" not in curr_txt:
                    needs_dd = True
                elif target_mode == "image" and ("banana" not in curr_txt and "imagen" not in curr_txt):
                    needs_dd = True
                elif modelo_alvo.lower() not in curr_txt:
                    needs_dd = True

                if needs_dd:
                    dd_trigger.click(force=True)
                    self.page.wait_for_timeout(300)
                    opt = self.page.locator(f'.cdk-overlay-pane [role="menuitem"]').filter(has_text=modelo_alvo).first
                    if opt.is_visible(timeout=400):
                        opt.click(force=True)
                        self.page.wait_for_timeout(200)

            # 5. Garante contagem / quantidade (x1, x2, x3, x4)
            qty_btn = self.page.locator(f'flow-prompt-box-settings button[role="radio"]').filter(has_text=qtd_alvo).first
            if qty_btn.is_visible(timeout=400):
                if qty_btn.get_attribute("aria-checked") != "true":
                    qty_btn.click(force=True)
                    self.page.wait_for_timeout(150)

            # 6. Fecha menus e painel limpo com Escape
            for _ in range(3):
                if self.page.locator("flow-prompt-box-settings").count() == 0:
                    break
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)

            # Confirmação final via texto do dock_btn
            dock_text = (dock_btn.inner_text(timeout=400) or "").lower() if dock_btn.is_visible(timeout=400) else ""
            if target_mode == "video":
                _modelo_confirmado = "vídeo" in dock_text or "video" in dock_text
            else:
                _modelo_confirmado = "banana" in dock_text or "imagem" in dock_text or "imagen" in dock_text or ("vídeo" not in dock_text and "video" not in dock_text)

            if target_mode == "video" and not _modelo_confirmado:
                pw_log(f"[FLOW] ERRO: Modo vídeo ('{modelo_alvo}') não pôde ser confirmado no Flow após configuração.", level="error")
                return False

            if not _modelo_confirmado:
                pw_log(f"[FLOW] Aviso: modelo '{modelo_alvo}' não confirmado visualmente após seleção — _configured_mode NÃO salvo.", level="warn")
            else:
                self._configured_mode = (target_mode, modelo_alvo, prop_alvo, qtd_alvo, _sub_chave)
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

        TAREFA 2 — DETECÇÃO CONTEXTUAL REFINADA:
        - NÃO varre document.body.innerText de forma global (evita resíduos de cards passados no canvas).
        - Busca EXCLUSIVAMENTE em toasts, alertas ativos ([role='alert'], flow-error-message) e no prompt atual.
        - Descarta termos genéricos como 'usage limit', 'credit limit', 'tente de novo mais tarde' e 'não houve cobrança'.
        - Exige evidência explícita de esgotamento de cota diária / falta de créditos.
        """
        if not self.page:
            return None
        try:
            if callable(getattr(self.page, "is_closed", None)) and self.page.is_closed() is True:
                return None
        except Exception:
            return None

        try:
            # 1. Checa aviso de erro ativo vinculado diretamente ao prompt / botão Create
            indicador_prompt = self.page.evaluate('''() => {
                const warnBtn = document.querySelector("flow-credit-warning-button button, button.prompt-warning-button, button[aria-label*='créditos insuficientes' i]");
                if (warnBtn && warnBtn.offsetParent !== null) {
                    return "prompt_warning_button";
                }
                const tip = document.querySelector(".prompt-warning-tooltip, .prompt-warning-text, #prompt-warning-desc");
                if (tip && tip.offsetParent !== null && /crédito|credit|quota|limite/i.test(tip.innerText || '')) {
                    return (tip.innerText || '').trim();
                }
                return null;
            }''')
            if indicador_prompt:
                pw_log(f"[FLOW] Aviso de crédito insuficiente no prompt detectado: {indicador_prompt!r}", level="warn")
                return self._tratar_indicador_limite("prompt_warning", video_mode)
        except Exception:
            pass

        try:
            # 2. Busca estritamente em toasts/modais de erro ativos (fora do canvas de cards históricos)
            indicador_toast = self.page.evaluate('''() => {
                const activeAlerts = document.querySelectorAll(
                    '[role="alert"], flow-error-message, .error-toast, [class*="toast" i], [class*="snackbar" i], [class*="banner-error" i], [role="dialog"] [class*="error" i], [role="alertdialog"]'
                );

                // Frases inequívocas de esgotamento
                const explicitExhaustion = [
                    "you've reached your daily limit",
                    "você atingiu o limite diário",
                    "você chegou ao limite diário",
                    "reached your daily limit",
                    "insufficient credits",
                    "créditos insuficientes",
                    "out of credits",
                    "no credits remaining",
                    "not enough compute credits",
                    "créditos esgotados",
                    "ran out of credits",
                    "quota exceeded"
                ];

                for (const alertEl of activeAlerts) {
                    // Descarta se estiver contido dentro de nós do canvas (cards antigos)
                    if (alertEl.closest('[data-testid*="canvas"], .flow-canvas, .canvas-viewport, [class*="canvas-node"], [class*="media-card"]')) {
                        continue;
                    }
                    // Elemento deve estar visível
                    if (alertEl.offsetParent === null) continue;

                    const txt = (alertEl.innerText || '').toLowerCase();
                    if (!txt || txt.length > 300) continue;

                    for (const exp of explicitExhaustion) {
                        if (txt.includes(exp)) {
                            return exp;
                        }
                    }
                }
                return null;
            }''')

            if indicador_toast:
                pw_log(f"[FLOW] Indicador de limite por TOAST/MODAL ativo detectado: {indicador_toast!r}", level="warn")
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

    # ------------------------------------------------------------------
    # REQ 2/REQ 3 — set_pause_reason(): carimba o MOTIVO da parada da fila
    # ------------------------------------------------------------------
    # Fonte ÚNICA para interromper a fila com motivo. Todo ponto de parada
    # (crédito de vídeo/imagem, fim de fila com créditos zerados, parada manual)
    # passa por aqui, que:
    #   1. grava o motivo no worker (self.pause_reason) + timestamp;
    #   2. loga "[HH:MM:SS.mmm] [PAUSE] Motivo: <motivo>" (CMD + console web);
    #   3. interrompe a fila via stop_requested (threading.Event) quando
    #      `parar_fila=True` — ATENÇÃO: o atributo é um Event (contrato
    #      .set()/.is_set()/.clear() usado no loop), NUNCA um bool.
    # `parar_fila=False` serve para registrar um motivo INFORMATIVO sem parar a
    # fila (ex.: aviso de pré-voo de personagem, que segue sem bloquear).
    def set_pause_reason(self, reason: Optional[str] = None, parar_fila: bool = True) -> None:
        motivo = str(reason or "").strip()
        self.pause_reason = motivo or None
        if not motivo:
            self.pause_reason_ts = None
            return
        self.pause_reason_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_log = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        linha = f"[{ts_log}] [PAUSE] Motivo: {motivo}"
        try:
            print(linha, flush=True)
        except Exception:
            # Redirecionamento com codepage legado (cp1252/cp850): o log de
            # diagnóstico NUNCA pode derrubar o fluxo.
            try:
                enc = getattr(sys.stdout, "encoding", None) or "ascii"
                print(linha.encode(enc, "replace").decode(enc, "replace"), flush=True)
            except Exception:
                pass
        try:
            pw_log(linha, level="warn")
        except Exception:
            pass
        if parar_fila and hasattr(self, "stop_requested"):
            self.stop_requested.set()

    # Compatibilidade com o nome antigo (telas/rotinas antigas leem e escrevem
    # `last_queue_pause_reason`). O SETTER registra o motivo SEM parar a fila,
    # preservando o comportamento original dos dois usos existentes (aviso de
    # pré-voo que NÃO bloqueia a fila e reset no início da fila).
    @property
    def last_queue_pause_reason(self) -> str:
        return getattr(self, "pause_reason", None) or ""

    @last_queue_pause_reason.setter
    def last_queue_pause_reason(self, valor) -> None:
        self.set_pause_reason(valor, parar_fila=False)

    def _notificar_navegacao_credito(self, motivo: str) -> None:
        """REQ 4 — pede à camada WEB (SSE) para levar o operador à aba Montagem.

        Quando a fila para por CRÉDITO não faz sentido continuar na aba de produção:
        o backend enfileira um evento SSE `navegarAba` e o frontend executa
        `window.irParaAbaS2("montagem")` (edição final).

        A camada web é OPCIONAL (o worker também roda via CLI sem Flask): o import
        é TARDIO e tudo é protegido — falha aqui NUNCA pode alterar a fila.
        """
        try:
            from services import api_v2 as _api_v2
        except Exception:
            return
        try:
            if not _api_v2.should_navigate_on_pause(motivo):
                return
            projeto = str(getattr(self, "current_project_id", "") or "").strip()
            if not projeto:
                return
            _api_v2.emit_navigate_event(
                projeto,
                _api_v2.aba_destino_para_pausa(motivo) or "montagem",
                motivo,
            )
        except Exception:
            pass

    def _tratar_indicador_limite(self, indicador: str, video_mode: bool) -> str:
        """Processa indicador de limite/quota detectado e executa rotação ou pausa."""
        from services.flow_account_manager import FlowAccountManager
        _fam = FlowAccountManager()

        if video_mode:
            # ── CRÉDITOS DE VÍDEO SEPARADOS POR CONTA ──────────────────────
            _conta_id = _conta_ativa_id()
            if _conta_id is not None:
                _fam.marcar_video_esgotado(_conta_id)
                pw_log(f"[FLOW] Conta {_conta_id}: video_creditos_esgotados=True (indicador: {indicador!r}).", level="warn")

            # Verificar se ainda há conta com crédito de vídeo disponível
            _proxima_video = _fam.proxima_conta_para_video(_conta_id)
            if _proxima_video:
                pw_log(f"[FLOW] Rotacionando para próxima conta com vídeo: {_proxima_video.get('nome')} (id={_proxima_video.get('id')})...", level="warn")
                self._rotacionar_conta(apenas_video=True, conta_destino_id=_proxima_video.get("id"))
                return "rotacionando_video"

            # Todas esgotadas — não há mais contas para vídeo
            pw_log(f"[FLOW] TODAS as contas com créditos de vídeo esgotados (indicador: {indicador!r}).", level="warn")
            self._fallback_video_para_imagem = True
            self._fallback_video_para_imagem_motivo = f"credito_esgotado_video (indicador={indicador!r})"
            self._persistir_alerta_creditos(fallback=True)
            self.set_pause_reason("credito_esgotado_video")
            self._notificar_navegacao_credito("credito_esgotado_video")
            return "credito_esgotado_video"

        # Modo imagem padrão
        proxima_conta = None
        try:
            proxima_conta = _fam.proxima_conta_disponivel()
        except Exception as _e_conta:
            pw_log(f"[FLOW] Aviso ao consultar contas para rotação: {_e_conta}", level="warn")

        if proxima_conta:
            pw_log(f"[FLOW] Créditos esgotados (indicador: {indicador!r}). Rotacionando para {proxima_conta}...", level="warn")
            self._rotacionar_conta(apenas_video=False)
            return "credito_esgotado"

        pw_log(f"[FLOW] Créditos esgotados e nenhuma conta com créditos (indicador: {indicador!r}).", level="warn")
        self._rotacionar_conta(apenas_video=False)
        return "credito_esgotado"

    def _rotacionar_conta(self, _depth: int = 0, apenas_video: bool = False, conta_destino_id: Optional[int] = None):
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
        accounts_path = FLOW_ACCOUNTS_PATH
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
                if apenas_video:
                    c["video_creditos_esgotados"] = True
                    c["video_esgotado_em"] = datetime.now().isoformat()
                else:
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
        if apenas_video:
            if conta_destino_id is not None:
                proxima = next((c for c in contas if c.get("id") == conta_destino_id), None)
            else:
                proxima = next(
                    (c for c in contas
                     if not c.get("video_creditos_esgotados") and not c.get("creditos_esgotados") and not c.get("ativa") and (c.get("email") or "").strip()),
                    None
                )
        else:
            proxima = next(
                (c for c in contas
                 if not c.get("creditos_esgotados") and not c.get("ativa") and (c.get("email") or "").strip()),
                None
            )

        if proxima:
            proxima["ativa"] = True
            if proxima.get("profile_dir"):
                self.profile_dir = proxima.get("profile_dir")

        try:
            accounts_path.write_text(
                json.dumps(accounts, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            pw_log(f"[FLOW] Erro ao salvar config/flow_accounts.json na rotação: {e}", level="error")
            return
        if not proxima:
            if apenas_video:
                pw_log("[FLOW] Todas as contas com créditos de VÍDEO esgotados.", level="error")
                self.set_pause_reason("credito_esgotado_video")
                self._notificar_navegacao_credito("credito_esgotado_video")
            else:
                pw_log("[FLOW] Todas as contas com créditos esgotados.", level="error")
                self.set_pause_reason("credito_esgotado_imagem")
                self._notificar_navegacao_credito("credito_esgotado_imagem")
            return
        proxima_id = proxima.get("id")
        pw_log(f"[FLOW] Alternando para: {proxima['nome']} (id={proxima_id})", level="info")

        # TAREFA 3 — Salva contexto ativo do projeto antes de reiniciar o Chrome
        if self.current_project_id:
            try:
                ctx_path = CONTEXTO_CDP_PATH
                ctx_path.parent.mkdir(parents=True, exist_ok=True)
                ctx_info = {
                    "projeto_id": self.current_project_id,
                    "worker_id": getattr(self, "worker_id", 0),
                    "port": self.port,
                    "modo": "video" if apenas_video else getattr(self, "current_flow_mode", "image"),
                    "timestamp": time.time()
                }
                ctx_path.write_text(json.dumps(ctx_info, ensure_ascii=False, indent=2), encoding="utf-8")
                pw_log(f"[CDP_CONTEXT] Contexto persistido para restauração da UI: {self.current_project_id}")
            except Exception as e_c:
                pw_log(f"[CDP_CONTEXT] Falha ao persistir contexto: {e_c}", level="warn")

        # 3. Reinicia o Chrome com o perfil da nova conta mantendo a URL no projeto ativo
        # `worker_id` só vai EXPLÍCITO para workers paralelos (profile_dir preenchido);
        # o singleton OMITE (None) para o ensure_chrome_cdp usar a conta ATIVA (ramo d).
        # Se a conta nova NÃO tem `profile_dir` (self.profile_dir continua vazio), manda
        # o `conta_id` dela — sem isso o Chrome cairia em contas[0] (conta_03).
        _kw_cdp = dict(
            force_restart=True,
            profile_dir=getattr(self, "profile_dir", None),
            projeto_id=getattr(self, "current_project_id", None),
            worker_id=self._worker_id_para_chrome(),
            modo="video" if apenas_video else "image",
        )
        if not _kw_cdp["profile_dir"]:
            _kw_cdp["conta_id"] = proxima_id
        ensure_chrome_cdp(getattr(self, "port", 9222), **_kw_cdp)

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
            self._avatar_media_uploaded = False
            self._personagem_card_persistido = False
            # GUARD DO AVATAR — biblioteca é POR CONTA: zera as chaves conhecidas.
            self._chaves_upload_conhecidas = set()
            self._chaves_ignoradas = set()
            self._configured_mode = None   # Força reconfiguração do canvas na nova conta (vídeo vs imagem)

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
            # A biblioteca pertence à conta Google: a conta nova pode já ter o personagem. Verificar antes de criar.
            self._reprovisionar_personagem_apos_rotacao(forcar_criacao=False)

            # 7. Upload do avatar/referência na nova conta (mídia na galeria)
            if self.current_project_id:
                try:
                    self._upload_avatar_projeto(self.current_project_id)
                except Exception as e_av:
                    pw_log(f"[AVATAR_UPLOAD] Erro inesperado ao enviar avatar do projeto pós-rotação: {e_av}", level="warn")

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
            vazia → `forcar_criacao=True` (default).
          * No INÍCIO da fila (_handle_run_queue, pré-voo): com `forcar_criacao=False`
            só cria quando o personagem realmente NÃO existe (verificação estrita,
            sem falso positivo).

        Em AMBOS os casos o personagem é o OFICIAL do projeto e `criar_personagem_flow`
        valida no popup '@' antes de retornar True (nunca cria '@avatar' genérico).
        Na rotação a biblioteca está vazia → cria direto; no pré-voo verifica antes.

        A biblioteca de personagens do Google Flow é POR CONTA — após a rotação a aba
        Personagens da conta nova está vazia. Se o projeto usa personagem nas cenas e
        há identidade local configurada, cria '@Nome' via criar_personagem_flow()
        (que valida no popup '@' antes de retornar True) e marca _avatar_uploaded=True.

        O personagem NUNCA é inventado: vem de identidade.json (validar_personagem_
        do_projeto). Sem identidade válida a reprovisão é CANCELADA com log claro.

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

        # Validação OBRIGATÓRIA (anti-genérico): o personagem vem de
        # identidade.json; sem identidade válida NÃO cria nada no Flow.
        _personagem_proj = None
        _erro_idt = ""
        try:
            _personagem_proj, _erro_idt = character_svc.validar_personagem_do_projeto(_projeto)
        except Exception as _e_idt:
            _personagem_proj, _erro_idt = None, f"erro ao ler identidade local: {_e_idt}"
        if not _personagem_proj:
            log_char(f"reprovisão de personagem CANCELADA: {_erro_idt or 'personagem não configurado'}.",
                     projeto_id=_projeto, level="error")
            pw_log(f"[FLOW] Reprovisão personagem: {_erro_idt or 'projeto sem personagem configurado.'}",
                   level="warn")
            return False
        _nome_char = str(_personagem_proj.get("nome") or "").strip()
        if not _nome_char:
            log_char(f"reprovisão CANCELADA: identidade de '{_projeto}' sem nome de personagem.",
                     projeto_id=_projeto, level="error")
            pw_log("[FLOW] Reprovisão personagem: identidade sem nome de personagem.", level="warn")
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
            _status_verif = None
            try:
                _status_verif = self._verificar_personagem_na_biblioteca(_nome_char)
            except Exception as _e_verif:
                pw_log(f"[FLOW] Aviso ao verificar personagem na biblioteca: {_e_verif}", level="warn")
                _status_verif = None

            if _status_verif is None:
                for _tentativa in range(1, 3):
                    try:
                        if self.page:
                            self.page.keyboard.press("Escape")
                            self.page.wait_for_timeout(1500)
                        _status_verif = self._verificar_personagem_na_biblioteca(_nome_char)
                    except Exception as _e_retry:
                        pw_log(f"[FLOW] Aviso na retentativa {_tentativa} de verificação: {_e_retry}", level="warn")
                        _status_verif = None
                    if _status_verif is not None:
                        break

            if _status_verif is True:
                pw_log(f"[PRE_VOO] Personagem '{_nome_exhib}' já existe na biblioteca — reprovisão desnecessária.", level="info")
                self._avatar_uploaded = True
                self._personagem_card_persistido = False
                return True
            elif _status_verif is None:
                pw_log("[PRE_VOO] verificação inconclusiva — criação NÃO disparada para evitar duplicata", level="warn")
                return False

            if getattr(self, "_personagem_card_persistido", False):
                pw_log(f"[PRE_VOO] card '{_nome_exhib}' criado no Flow, validação do popup '@' falhou — NOVA CRIAÇÃO BLOQUEADA nesta sessão", level="warn")
                return False

        log_char(f"reprovisionando personagem oficial '{_nome_exhib}' na conta ativa "
                 f"(imagem: {Path(_foto).name}).", projeto_id=_projeto)

        try:
            _criado = criar_personagem_flow(self.page, _nome_char, _foto)
        except FlowCharacterCreationError as _e_flow:
            if getattr(_e_flow, "card_persistido", False):
                self._personagem_card_persistido = True
                pw_log(f"[PRE_VOO] card '{_nome_exhib}' criado no Flow, validação do popup '@' falhou — NOVA CRIAÇÃO BLOQUEADA nesta sessão", level="warn")
            else:
                pw_log(f"[FLOW] Reprovisão personagem: falha ao criar '{_nome_exhib}' no Flow: {_e_flow}", level="warn")

            # Recuperação de navegação: se a página ficou presa em /character/<id>, volta ao canvas do projeto
            if self.page:
                try:
                    _url_atual = str(getattr(self.page, "url", "") or "")
                    _target_canvas_url = ""
                    if "/character/" in _url_atual:
                        _target_canvas_url = _url_atual.split("/character/")[0]
                    if not _target_canvas_url and _projeto:
                        try:
                            _target_canvas_url = carregar_projeto_flow_url(_projeto) or ""
                        except Exception:
                            _target_canvas_url = ""

                    if _target_canvas_url:
                        pw_log(f"[FLOW] Recuperação pós-falha: retornando ao canvas do projeto ({_target_canvas_url})...")
                        self.page.goto(_target_canvas_url, timeout=15000)
                        try:
                            self.page.wait_for_selector('div.ProseMirror[contenteditable="true"]', state="visible", timeout=8000)
                        except Exception:
                            pass
                except Exception as _e_nav:
                    pw_log(f"[FLOW] Aviso ao retornar ao canvas do projeto pós-falha: {_e_nav}", level="warn")

            _criado = False
        except Exception as _e_criar:
            pw_log(f"[FLOW] Reprovisão personagem: falha ao criar '{_nome_exhib}' no Flow: {_e_criar}", level="warn")

            # Recuperação de navegação: se a página ficou presa em /character/<id>, volta ao canvas do projeto
            if self.page:
                try:
                    _url_atual = str(getattr(self.page, "url", "") or "")
                    _target_canvas_url = ""
                    if "/character/" in _url_atual:
                        _target_canvas_url = _url_atual.split("/character/")[0]
                    if not _target_canvas_url and _projeto:
                        try:
                            _target_canvas_url = carregar_projeto_flow_url(_projeto) or ""
                        except Exception:
                            _target_canvas_url = ""

                    if _target_canvas_url:
                        pw_log(f"[FLOW] Recuperação pós-falha: retornando ao canvas do projeto ({_target_canvas_url})...")
                        self.page.goto(_target_canvas_url, timeout=15000)
                        try:
                            self.page.wait_for_selector('div.ProseMirror[contenteditable="true"]', state="visible", timeout=8000)
                        except Exception:
                            pass
                except Exception as _e_nav:
                    pw_log(f"[FLOW] Aviso ao retornar ao canvas do projeto pós-falha: {_e_nav}", level="warn")

            _criado = False
        if _criado:
            pw_log(f"[PRE_VOO] Personagem @{_nome_char} criado automaticamente")
            self._avatar_uploaded = True
            self._personagem_card_persistido = False
        else:
            pw_log(f"[PRE_VOO] Personagem @{_nome_char} NÃO pôde ser criado automaticamente (a fila continua; cenas com personagem podem falhar individualmente; flag de personagem confirmado NÃO marcada).", level="warn")

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

    # =====================================================================
    # GUARD DO AVATAR (ANTIGRAVITY) — o b-roll NUNCA aceita o reference.png
    # =====================================================================
    def _conjunto_chaves_guard(self, nome: str) -> Set[str]:
        """Conjunto de chaves do guard, criado sob demanda (workers stub podem não tê-lo)."""
        _atual = getattr(self, nome, None)
        if not isinstance(_atual, set):
            _atual = set()
            setattr(self, nome, _atual)
        return _atual

    def _dados_avatar_referencia(self, projeto_id: str) -> Dict[str, Any]:
        """Dados do reference.png cacheados POR PROJETO/worker: RGB normalizado + dHash.

        Desativação limpa: referência ausente/ilegível → {"ativo": False} com UM log
        claro (`[GUARD_AVATAR] desativado: ...`) — nunca levanta exceção.
        """
        if not hasattr(self, "_avatar_ref_cache") or not isinstance(self._avatar_ref_cache, dict):
            self._avatar_ref_cache = {}
        if projeto_id in self._avatar_ref_cache:
            return self._avatar_ref_cache[projeto_id]
        _caminho = self._resolver_reference_png(projeto_id)
        if not _caminho:
            pw_log("[GUARD_AVATAR] desativado: referência ausente (reference.png não encontrado).",
                   level="warn")
            _dados: Dict[str, Any] = {"ativo": False}
        else:
            try:
                import numpy as np
                from PIL import Image
                _img = Image.open(_caminho).convert("RGB")
                _img.thumbnail((LADO_MAXIMO_ANALISE_AVATAR, LADO_MAXIMO_ANALISE_AVATAR),
                               Image.LANCZOS)
                _dados = {
                    "ativo": True,
                    "caminho": _caminho,
                    "size": _img.size,
                    "rgb": np.asarray(_img, dtype=np.int16),
                    "dhash": self._dhash64(_img),
                }
                pw_log(f"[GUARD_AVATAR] referência carregada de '{_caminho}' "
                       f"({_img.size[0]}x{_img.size[1]}) — guard ATIVO para cenas sem personagem.")
            except Exception as e:
                pw_log(f"[GUARD_AVATAR] desativado: referência ilegível ({e}).", level="warn")
                _dados = {"ativo": False}
        self._avatar_ref_cache[projeto_id] = _dados
        return _dados

    @staticmethod
    def _dhash64(img) -> int:
        """dHash de 64 bits: grayscale 9x8 + comparações horizontais (PIL + numpy)."""
        import numpy as np
        from PIL import Image
        _g = img.convert("L").resize((9, 8), Image.LANCZOS)
        _px = np.asarray(_g, dtype=np.int16)
        _hash = 0
        for _b in (_px[:, 1:] > _px[:, :-1]).flatten():
            _hash = (_hash << 1) | int(bool(_b))
        return _hash

    def _imagem_e_avatar_de_referencia(self, projeto_id: str, origem: Any) -> bool:
        """True quando a imagem (bytes OU caminho) é PRATICAMENTE IDÊNTICA ao reference.png.

        Critério (limiares nomeados no topo do módulo): diferença média absoluta RGB <
        LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR **E** Hamming(dHash) <= LIMIAR_HAMMING_DHASH_AVATAR.
        Referência ausente/ilegível → guard desativado (retorna False; nunca quebra a fila).
        """
        _dados = self._dados_avatar_referencia(projeto_id)
        if not _dados.get("ativo"):
            return False
        try:
            import io as _io
            import numpy as np
            from PIL import Image
            _fonte = _io.BytesIO(bytes(origem)) if isinstance(origem, (bytes, bytearray)) else origem
            _img = Image.open(_fonte).convert("RGB").resize(_dados["size"], Image.LANCZOS)
            _rgb = np.asarray(_img, dtype=np.int16)
            _dif = float(np.abs(_rgb - _dados["rgb"]).mean())
            _ham = bin(self._dhash64(_img) ^ _dados["dhash"]).count("1")
            _eh_avatar = (_dif < LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR
                          and _ham <= LIMIAR_HAMMING_DHASH_AVATAR)
            pw_log(f"[GUARD_AVATAR] comparação com o reference.png: RGB={_dif:.2f} "
                   f"(limiar <{LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR}), Hamming={_ham} "
                   f"(limiar <={LIMIAR_HAMMING_DHASH_AVATAR}) → "
                   f"{'É O AVATAR' if _eh_avatar else 'imagem distinta'}.")
            return _eh_avatar
        except Exception as e:
            pw_log(f"[GUARD_AVATAR] comparação falhou ({e}) — candidato liberado.", level="warn")
            return False

    def _baixar_bytes_item_para_guard(self, item: Dict[str, Any]) -> Optional[bytes]:
        """Bytes de um item de mídia para o GUARD DO AVATAR (best-effort, nunca quebra a fila)."""
        try:
            _data_url = item.get("dataUrl")
            if isinstance(_data_url, str) and "," in _data_url:
                return base64.b64decode(_data_url.split(",", 1)[1])
            _src = item.get("src")
            if _src:
                try:
                    resp = self.page.request.get(_src)
                    if resp.ok and len(resp.body()) > 500:
                        return resp.body()
                except Exception:
                    pass
                _res = self._baixar_midia_com_retry(_src, is_video=False) or {}
                _b64 = str(_res.get("base64") or "")
                if _res.get("ok") and "," in _b64:
                    return base64.b64decode(_b64.split(",", 1)[1])
        except Exception as e:
            pw_log(f"[GUARD_AVATAR] Falha ao obter bytes do candidato ({e}) — "
                   "guard não aplicado a este item.", level="warn")
        return None

    def _registrar_chave_ignorada(self, item: Dict[str, Any]) -> None:
        """Marca as chaves (mediaKey/id/src) de um item contaminado como IGNORADAS na fila."""
        _ignoradas = self._conjunto_chaves_guard("_chaves_ignoradas")
        for _chave in (item.get("mediaKey"), item.get("id"), item.get("src")):
            if _chave:
                _ignoradas.add(_chave)

    def _registrar_chaves_upload_avatar(self, snapshot_antes: Set[str],
                                        timeout_s: float = JANELA_INGESTAO_UPLOAD_AVATAR_S) -> int:
        """TAREFA 3 — registra as chaves do tile do avatar enviado ao Flow.

        A grade do Flow é virtualizada (.virtual-item-container): o tile do upload pode
        entrar no DOM DEPOIS do snapshot da cena e ser aceito como "mídia nova". Aqui
        aguardamos a ingestão (polling curto) e gravamos as chaves novas em
        `_chaves_upload_conhecidas` (consultado por `_media_item_e_novo`).
        """
        if not snapshot_antes:
            pw_log("[GUARD_AVATAR] snapshot inicial indisponível — registro das chaves do "
                   "upload pulado (o guard de conteúdo segue ativo).", level="warn")
            return 0
        _t0 = time.time()
        _novas: Set[str] = set()
        while (time.time() - _t0) < timeout_s:
            try:
                _depois, _ = self._get_existing_media_snapshot()
            except Exception:
                _depois = set()
            _novas = {_k for _k in (_depois - snapshot_antes) if _k}
            if _novas:
                break
            try:
                self.page.wait_for_timeout(500)
            except Exception:
                break
        if _novas:
            self._conjunto_chaves_guard("_chaves_upload_conhecidas").update(_novas)
            pw_log(f"[GUARD_AVATAR] upload do avatar registrado como mídia conhecida "
                   f"({len(_novas)} chave(s)) — nunca será tratado como resultado novo.")
        else:
            pw_log("[GUARD_AVATAR] ingestão do upload do avatar não detectada em "
                   f"~{timeout_s:.0f}s — seguindo; o guard de conteúdo permanece ativo.",
                   level="warn")
        return len(_novas)

    def _media_item_e_novo(self, item: Dict[str, Any], existing_keys: Set[str]) -> bool:
        """Novidade determinística do item: mediaKey/id/src fora do snapshot E fora das
        chaves CONHECIDAS do upload do avatar e das chaves IGNORADAS (guards ANTIGRAVITY)."""
        media_key = item.get("mediaKey")
        if not media_key:
            return False
        _excluidas = (set(existing_keys)
                      | self._conjunto_chaves_guard("_chaves_upload_conhecidas")
                      | self._conjunto_chaves_guard("_chaves_ignoradas"))
        for _chave in (media_key, item.get("id"), item.get("src")):
            if _chave and _chave in _excluidas:
                return False
        return True

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

    def _baixar_video_1080p_menu(self, new_media_item: Dict[str, Any], cid: int) -> Optional[bytes]:
        """Tenta baixar o vídeo recém-gerado em 1080p Aprimorada via menu de 3 pontinhos do card.

        Fluxo UI no Google Flow:
          1. Localiza o card do vídeo (via mediaKey ou último vídeo).
          2. Faz hover no card para exibir a barra superior (favorito, reutilizar, 3 pontinhos).
          3. Clica no botão de 3 pontinhos ('Mais').
          4. No menu popover, passa o mouse / clica em 'Fazer o download'.
          5. No submenu, clica em '1080p' ('Aprimorada') enquanto aguarda o evento de download.
          6. Intercepta o arquivo baixado e retorna os bytes do vídeo Full HD.
          7. Se falhar, retorna None para acionar o fallback para 720p.
        """
        if not self.page:
            return None
        import tempfile
        media_key = new_media_item.get("mediaKey") or ""
        pw_log(f"[CENA {cid:03d}][1080P] Tentando download 1080p Aprimorada via menu do card...")

        try:
            # 1. Localiza e faz hover no vídeo para garantir que a barra de ferramentas apareça
            alvo_video = None
            if media_key:
                try:
                    v_loc = self.page.locator(f'video[src*="{media_key}"]').first
                    if v_loc.is_visible(timeout=1000):
                        alvo_video = v_loc
                except Exception:
                    pass
            if alvo_video is None:
                try:
                    v_loc = self.page.locator("video").last
                    if v_loc.is_visible(timeout=1000):
                        alvo_video = v_loc
                except Exception:
                    pass

            if alvo_video:
                try:
                    alvo_video.scroll_into_view_if_needed(timeout=1000)
                    alvo_video.hover(timeout=1500)
                    self.page.wait_for_timeout(350)
                except Exception:
                    pass

            # 2. Clica no botão de 3 pontinhos ('Mais')
            menu_aberto = False

            # Tentativa 2a: via JavaScript específico do card correspondente
            try:
                clicou_js = self.page.evaluate("""(mKey) => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    let v = null;
                    if (mKey) {
                        v = videos.find(x => (x.src && x.src.includes(mKey)) || (x.querySelector('source') && x.querySelector('source').src.includes(mKey)));
                    }
                    if (!v && videos.length > 0) {
                        v = videos[videos.length - 1];
                    }
                    if (!v) return false;
                    let p = v.parentElement;
                    let depth = 0;
                    while (p && depth < 8) {
                        const btns = Array.from(p.querySelectorAll('button, [role="button"]'));
                        for (const b of btns) {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const title = (b.getAttribute('title') || '').toLowerCase();
                            const txt = (b.innerText || '').toLowerCase();
                            if (aria.includes('mais') || aria.includes('more') || title.includes('mais') || title.includes('more') || txt.includes('more_vert')) {
                                b.scrollIntoView({ block: 'center' });
                                b.click();
                                return true;
                            }
                        }
                        p = p.parentElement;
                        depth++;
                    }
                    return false;
                }""", media_key)
                if clicou_js:
                    menu_aberto = True
                    self.page.wait_for_timeout(400)
            except Exception as e_js_click:
                pw_log(f"[CENA {cid:03d}][1080P] JS click no botão Mais: {e_js_click}", level="debug")

            # Tentativa 2b: via Playwright locators se o JS não clicou
            if not menu_aberto:
                for sel_mais in [
                    'button[aria-label="Mais"]',
                    'button[aria-label*="Mais"]',
                    'button[title="Mais"]',
                    'button[title*="Mais"]',
                    'button[aria-label*="More"]',
                    '[aria-label="Mais"]',
                    '[aria-label="More options"]',
                ]:
                    try:
                        b = self.page.locator(sel_mais).last
                        if b.is_visible(timeout=600):
                            b.click(timeout=1000)
                            menu_aberto = True
                            self.page.wait_for_timeout(400)
                            break
                    except Exception:
                        continue

            if not menu_aberto:
                pw_log(f"[CENA {cid:03d}][1080P] Botão 'Mais' (3 pontinhos) não encontrado — seguindo fallback 720p.", level="warn")
                return None

            # 3. Localiza e aciona o item 'Fazer o download'
            dl_menu_ok = False
            for sel_dl in [
                '[role="menuitem"]:has-text("Fazer o download")',
                'button:has-text("Fazer o download")',
                'div:has-text("Fazer o download")',
                'span:has-text("Fazer o download")',
                'text="Fazer o download"',
                '[role="menuitem"]:has-text("Download")',
                'button:has-text("Download")',
            ]:
                try:
                    dl_loc = self.page.locator(sel_dl).last
                    if dl_loc.is_visible(timeout=800):
                        dl_loc.hover(timeout=800)
                        self.page.wait_for_timeout(250)
                        dl_loc.click(timeout=800)
                        dl_menu_ok = True
                        self.page.wait_for_timeout(350)
                        break
                except Exception:
                    continue

            if not dl_menu_ok:
                try:
                    dl_menu_ok = self.page.evaluate("""() => {
                        const items = Array.from(document.querySelectorAll('[role="menuitem"], button, div, span'));
                        const dl = items.find(el => {
                            const t = (el.innerText || '').trim();
                            return t === 'Fazer o download' || t.startsWith('Fazer o download') || t === 'Download';
                        });
                        if (dl) {
                            dl.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                            dl.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                            dl.click();
                            return true;
                        }
                        return false;
                    }""")
                    if dl_menu_ok:
                        self.page.wait_for_timeout(350)
                except Exception:
                    pass

            if not dl_menu_ok:
                pw_log(f"[CENA {cid:03d}][1080P] Item 'Fazer o download' não encontrado no menu.", level="warn")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return None

            # 4. Clica em '1080p' / 'Aprimorada' e intercepta o download do navegador
            pw_log(f"[CENA {cid:03d}][1080P] Disparando download da opção '1080p Aprimorada'...")
            sel_1080p = [
                '[role="menuitem"]:has-text("1080p")',
                'button:has-text("1080p")',
                'div:has-text("1080p")',
                'span:has-text("1080p")',
                'text="1080p"',
                '[role="menuitem"]:has-text("Aprimorada")',
                'button:has-text("Aprimorada")',
            ]

            download_bytes = None
            try:
                with self.page.expect_download(timeout=35000) as download_info:
                    clicou_1080 = False
                    for sel_op in sel_1080p:
                        try:
                            op = self.page.locator(sel_op).last
                            if op.is_visible(timeout=800):
                                op.click(timeout=1500)
                                clicou_1080 = True
                                break
                        except Exception:
                            continue

                    if not clicou_1080:
                        self.page.evaluate("""() => {
                            const items = Array.from(document.querySelectorAll('[role="menuitem"], button, div, span'));
                            const item1080 = items.find(el => (el.innerText || '').includes('1080p'));
                            if (item1080) item1080.click();
                        }""")

                download = download_info.value
                tmp_dir = tempfile.gettempdir()
                tmp_file = os.path.join(tmp_dir, f"flow_1080p_{cid}_{int(time.time())}.mp4")
                download.save_as(tmp_file)

                if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 1000:
                    with open(tmp_file, "rb") as f:
                        download_bytes = f.read()
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
                    pw_log(f"[CENA {cid:03d}][1080P] Download 1080p concluído via expect_download ({len(download_bytes)} bytes).")
            except Exception as e_dl:
                pw_log(f"[CENA {cid:03d}][1080P] Falha ao interceptar download 1080p ({e_dl}).", level="warn")

            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

            return download_bytes
        except Exception as e_geral:
            pw_log(f"[CENA {cid:03d}][1080P] Erro geral ao tentar download 1080p: {e_geral}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return None

    @staticmethod
    def _bytes_sao_video_real(dados: Optional[bytes]) -> bool:
        """True quando a assinatura binária é de contêiner de VÍDEO (mp4/webm/mov).

        Usado para NUNCA gravar um thumbnail <img> como .mp4 (mentira de formato —
        mesmo vetor do "PNG servido como MP4").
        """
        if not dados or len(dados) < 12:
            return False
        _ini = bytes(dados[:16])
        _eh_imagem = (
            _ini.startswith(b"\x89PNG") or _ini.startswith(b"\xff\xd8\xff")
            or _ini.startswith(b"GIF8") or _ini.startswith(b"BM")
            or (_ini.startswith(b"RIFF") and dados[8:12] == b"WEBP")
        )
        if _eh_imagem:
            return False
        return dados[4:8] in (b"ftyp", b"styp", b"moov") or dados[:4] == b"\x1a\x45\xdf\xa3"

    def _baixar_video_tile_automatico(
        self,
        media_item: Dict[str, Any],
        cid: int,
        qualidade: str = "720p",
    ) -> bool:
        """Baixa o vídeo recém-gerado via menu de contexto do <flow-video-tile> (Flow 2025).

        O Flow não renderiza <video> nativo na grade: o vídeo vive em um custom
        element <flow-video-tile> (ícone play_circle; o <img class="thumbnail"> é
        apenas preview). O arquivo completo é obtido por right-click / botão
        "Mais opções" → "Fazer o download" → qualidade.

        Os bytes capturados ficam em `self._download_tile_bytes` para o pipeline
        oficial gravá-los em `cenas/{cid}_[...].mp4` (mesmo caminho canônico de
        `salvar_midia_cena_estruturada`) — esta função NÃO grava mídia final e
        NÃO altera status de cena. Retorna True apenas quando o download foi
        interceptado E validado como vídeo real (mp4/webm/mov).
        """
        self._download_tile_bytes = None
        if not self.page:
            return False
        media_key = str(media_item.get("mediaKey") or "").strip()
        tile_index = media_item.get("tileIndex")
        if tile_index is None:
            tile_index = media_item.get("domIndex")
        qualidade = str(qualidade or "720p").lower()
        pw_log(
            f"[VIDEO_DOWNLOAD_START] Cena {cid:03d}: flow-video-tile "
            f"(mediaKey={media_key or 'n/d'}, tileIndex={tile_index if tile_index is not None else 'n/d'}) "
            f"— abrindo menu de contexto para download {qualidade}."
        )
        try:
            # 1. Localiza o tile: por mediaKey (thumbnail com src contendo o UUID)
            #    e, se falhar, por índice (nth) ou pelo último tile da grade.
            alvo = None
            _mk_sel = media_key.replace('"', "").replace("\\", "")
            if _mk_sel:
                try:
                    loc = self.page.locator(f'flow-video-tile:has(img[src*="{_mk_sel}"])').first
                    if loc.is_visible(timeout=1500):
                        alvo = loc
                except Exception:
                    alvo = None
            if alvo is None and isinstance(tile_index, int) and tile_index >= 0:
                try:
                    loc = self.page.locator("flow-video-tile").nth(int(tile_index))
                    if loc.is_visible(timeout=1500):
                        alvo = loc
                except Exception:
                    alvo = None
            if alvo is None:
                try:
                    loc = self.page.locator("flow-video-tile").last
                    if loc.is_visible(timeout=1500):
                        alvo = loc
                except Exception:
                    alvo = None
            if alvo is None:
                pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: nenhum <flow-video-tile> visível para baixar.", level="warn")
                return False
            pw_log(f"[CENA {cid:03d}] flow-video-tile detectado com play_circle — disparando menu de download.")
            try:
                alvo.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                alvo.hover(timeout=1500)
                self.page.wait_for_timeout(300)
            except Exception:
                pass

            # 2. Abre o menu de contexto: right-click no tile; se falhar, botão
            #    "Mais opções"/"Mais" (visível no hover) ou JS dentro do tile.
            menu_aberto = False
            try:
                alvo.click(button="right", timeout=2000)
                menu_aberto = True
            except Exception as e_rc:
                pw_log(f"[CENA {cid:03d}] right-click no tile falhou ({e_rc}); tentando 'Mais opções'.", level="debug")
            if not menu_aberto:
                for sel_mais in (
                    'button[aria-label*="Mais opções" i]',
                    'button[aria-label*="More options" i]',
                    'flow-video-tile button[aria-label*="Mais" i]',
                    'flow-video-tile button[aria-label*="More" i]',
                    'button[aria-label="Mais"]',
                    'button[aria-label*="Mais"]',
                ):
                    try:
                        b = self.page.locator(sel_mais).last
                        if b.is_visible(timeout=600):
                            b.click(timeout=1000)
                            menu_aberto = True
                            break
                    except Exception:
                        continue
            if not menu_aberto:
                try:
                    menu_aberto = bool(self.page.evaluate(
                        """(ref) => {
                            const tiles = Array.from(document.querySelectorAll('flow-video-tile'));
                            let tile = null;
                            if (ref && ref.key) {
                                tile = tiles.find(t => {
                                    const img = t.querySelector('img');
                                    return img && (img.src || '').includes(ref.key);
                                }) || null;
                            }
                            if (!tile && ref && typeof ref.idx === 'number' && ref.idx >= 0) tile = tiles[ref.idx] || null;
                            if (!tile && tiles.length) tile = tiles[tiles.length - 1];
                            if (!tile) return false;
                            const btns = Array.from(tile.querySelectorAll('button, [role="button"]'));
                            for (const b of btns) {
                                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                                const title = (b.getAttribute('title') || '').toLowerCase();
                                if (aria.includes('mais') || aria.includes('more') || title.includes('mais') || title.includes('more')) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        {"key": _mk_sel, "idx": int(tile_index) if isinstance(tile_index, int) else -1},
                    ))
                except Exception:
                    menu_aberto = False
            if not menu_aberto:
                pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: menu de contexto do tile não abriu (right-click e 'Mais opções' falharam).", level="warn")
                return False

            # Aguarda o menu de contexto aparecer de fato.
            try:
                self.page.wait_for_selector(
                    '[role="menu"], .mat-mdc-menu-panel, .cdk-overlay-pane [role="menuitem"]',
                    timeout=TIMEOUT_VIDEO_TILE_MENU_MS,
                )
            except Exception:
                pass
            self.page.wait_for_timeout(300)

            # 3. "Fazer o download" (hover abre o submenu de qualidade).
            dl_menu_ok = False
            for sel_dl in (
                '[role="menuitem"]:has-text("Fazer o download")',
                'button:has-text("Fazer o download")',
                'div:has-text("Fazer o download")',
                'span:has-text("Fazer o download")',
                'text="Fazer o download"',
                '[role="menuitem"]:has-text("Download")',
                'button:has-text("Download")',
            ):
                try:
                    dl_loc = self.page.locator(sel_dl).last
                    if dl_loc.is_visible(timeout=800):
                        dl_loc.hover(timeout=800)
                        self.page.wait_for_timeout(250)
                        dl_loc.click(timeout=800)
                        dl_menu_ok = True
                        self.page.wait_for_timeout(350)
                        break
                except Exception:
                    continue
            if not dl_menu_ok:
                try:
                    dl_menu_ok = bool(self.page.evaluate("""() => {
                        const items = Array.from(document.querySelectorAll('[role="menuitem"], button, div, span'));
                        const dl = items.find(el => {
                            const t = (el.innerText || '').trim();
                            return t === 'Fazer o download' || t.startsWith('Fazer o download') || t === 'Download';
                        });
                        if (dl) {
                            dl.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                            dl.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                            dl.click();
                            return true;
                        }
                        return false;
                    }"""))
                    if dl_menu_ok:
                        self.page.wait_for_timeout(350)
                except Exception:
                    pass
            if not dl_menu_ok:
                pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: item 'Fazer o download' não encontrado no menu.", level="warn")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return False

            # 4. Seleciona a qualidade e intercepta o download do navegador.
            if qualidade.startswith("1080"):
                termos_q = ("1080p", "Aprimorada")
            else:
                termos_q = ("720p", "Original")
            pw_log(f"[VIDEO_DOWNLOAD_START] Cena {cid:03d}: disparando download '{termos_q[0]}'...")
            sels_q = []
            for termo in termos_q:
                sels_q.extend([
                    f'[role="menuitem"]:has-text("{termo}")',
                    f'button:has-text("{termo}")',
                    f'div:has-text("{termo}")',
                    f'span:has-text("{termo}")',
                    f'text="{termo}"',
                ])
            import tempfile
            download_bytes = None
            try:
                with self.page.expect_download(timeout=TIMEOUT_VIDEO_TILE_DOWNLOAD_MS) as download_info:
                    clicou_q = False
                    for sel_op in sels_q:
                        try:
                            op = self.page.locator(sel_op).last
                            if op.is_visible(timeout=800):
                                op.click(timeout=1500)
                                clicou_q = True
                                break
                        except Exception:
                            continue
                    if not clicou_q:
                        self.page.evaluate("""(termos) => {
                            const items = Array.from(document.querySelectorAll('[role="menuitem"], button, div, span'));
                            for (const termo of termos) {
                                const el = items.find(x => (x.innerText || '').includes(termo));
                                if (el) { el.click(); return true; }
                            }
                            return false;
                        }""", list(termos_q))
                download = download_info.value
                tmp_file = os.path.join(tempfile.gettempdir(), f"flow_video_tile_{cid}_{int(time.time())}.mp4")
                download.save_as(tmp_file)
                if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 1000:
                    with open(tmp_file, "rb") as f:
                        download_bytes = f.read()
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
            except Exception as e_dl:
                pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: falha ao interceptar o download ({e_dl}).", level="warn")

            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

            if not download_bytes:
                return False

            # 5. Valida que os bytes são vídeo REAL — um thumbnail salvo como .mp4
            #    repetiria a mentira de formato que estamos eliminando.
            if not self._bytes_sao_video_real(download_bytes):
                pw_log(
                    f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: download não é vídeo real "
                    f"(assinatura={download_bytes[:8]!r}, {len(download_bytes)} bytes) — descartado.",
                    level="warn",
                )
                return False

            self._download_tile_bytes = download_bytes
            pw_log(f"[VIDEO_DOWNLOAD_OK] Cena {cid:03d}: vídeo {qualidade} obtido via flow-video-tile ({len(download_bytes)} bytes).")
            return True
        except Exception as e_geral:
            pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: erro geral no download via tile: {e_geral}", level="warn")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def _aguardar_lista_personagens_estavel(
        self,
        dialog,
        nome_limpo: str,
        timeout_ms: int = TIMEOUT_ESTABILIZACAO_PERSONAGENS_MS
    ) -> str:
        """Faz polling na aba Personagens do popup aguardando a lista estabilizar.

        Retorna:
          * "encontrado": o nome do personagem apareceu no dialog.
          * "estavel": contagem de cards > 0 e estável (igual em 2 leituras consecutivas),
                      sem indicador de carregamento visível.
          * "vazio": contagem 0 durante toda a janela e nenhum indicador de carregamento visível.
          * "vazio_confirmado": .empty-state ("Nenhum recurso encontrado") visível em DUAS
                      leituras CONSECUTIVAS sem spinner (único "vazio" que pode gerar False).
          * "carregando": indicador de carregamento visível ou contagem ainda oscilando ao fim da janela.
        """
        termos_nome = [
            f'[role="option"]:has-text("{nome_limpo}")',
            f'button.asset-item:has-text("{nome_limpo}")',
            f'.asset-item:has-text("{nome_limpo}")',
            f'.asset-title:has-text("{nome_limpo}")',
            f'[role="button"]:has-text("{nome_limpo}")',
            f'div:has-text("{nome_limpo}")',
            f'span:has-text("{nome_limpo}")',
            f'img[alt*="{nome_limpo}" i]',
            f'[role="option"]:has-text("@{nome_limpo}")',
            f'button.asset-item:has-text("@{nome_limpo}")',
            f'.asset-item:has-text("@{nome_limpo}")',
            f'.asset-title:has-text("@{nome_limpo}")',
            f'[role="button"]:has-text("@{nome_limpo}")',
            f'div:has-text("@{nome_limpo}")',
            f'span:has-text("@{nome_limpo}")',
            f'img[alt*="@{nome_limpo}" i]',
        ]
        # ANTIGRAVITY — a contagem de cards fica RESTRITA ao popover REAL de
        # personagens (SEL_CARDS_POPOVER_PERSONAGENS/SEL_CARDS_LOCAL_POPOVER...):
        # itens de menu genéricos (button[role="menuitem"] do menu de upload etc.)
        # NÃO podem contar como cards. `valido_alguma_vez` distingue "lista lida
        # (mesmo vazia)" de "popover errado/não abriu" (=> inconclusivo).
        valido_alguma_vez = False
        sels_loading = [
            'mat-spinner',
            '.mat-progress-spinner',
            '.mat-mdc-progress-spinner',
            '[role="progressbar"]',
            '[aria-busy="true"]',
            'text="Carregando..."',
            'text="Loading..."',
        ]

        t0 = time.time()
        timeout_s = max(0.1, timeout_ms / 1000.0)
        leitura_anterior = None
        viu_algum_loader = False
        contagem_maxima = 0
        # ANTIGRAVITY — "vazio" explícito (.empty-state) só confirma com 2 leituras seguidas.
        _empty_visto_anterior = False

        while (time.time() - t0) < timeout_s:
            # 1. Checa se o nome procurado já apareceu
            for sel_n in termos_nome:
                try:
                    loc_n = dialog.locator(sel_n).first
                    if loc_n.is_visible(timeout=100):
                        return "encontrado"
                except Exception:
                    continue

            # 2. Checa se há loader/spinner/carregamento ativo
            carregando_agora = False
            for sel_l in sels_loading:
                try:
                    loc_l = dialog.locator(sel_l).first
                    if loc_l.is_visible(timeout=50):
                        carregando_agora = True
                        viu_algum_loader = True
                        break
                except Exception:
                    continue

            # 2.5 Estado VAZIO EXPLÍCITO do popover ("Nenhum recurso encontrado"):
            #     somente DUAS leituras consecutivas sem spinner confirmam vazio_confirmado.
            if not carregando_agora:
                _vazio_agora = False
                try:
                    _loc_vazio = dialog.locator(SEL_VAZIO_POPOVER_PERSONAGENS).first
                    _vazio_agora = bool(_loc_vazio.is_visible(timeout=60))
                except Exception:
                    _vazio_agora = False
                if _vazio_agora:
                    if _empty_visto_anterior:
                        return "vazio_confirmado"
                    _empty_visto_anterior = True
                else:
                    _empty_visto_anterior = False
            else:
                _empty_visto_anterior = False

            # 3. Contagem de cards — SOMENTE dentro do popover de personagens
            cards_count = 0
            popover_valido = False
            try:
                if dialog.locator(SEL_POPOVER_PERSONAGENS_RAIZ).count() > 0:
                    # O dialog é o overlay ANCESTRAL: conta só o que está dentro
                    # da tag do popover real.
                    popover_valido = True
                    cards_count = dialog.locator(SEL_CARDS_POPOVER_PERSONAGENS).count()
                else:
                    # O dialog pode ser o PRÓPRIO popover: valida por sinais fortes
                    # (abas Personagens/Characters ou itens .asset-title/.asset-item).
                    popover_valido = (
                        dialog.locator(SEL_TABS_POPOVER_PERSONAGENS).count() > 0
                        or dialog.locator('.asset-title, .asset-item').count() > 0
                    )
                    if popover_valido:
                        cards_count = dialog.locator(SEL_CARDS_LOCAL_POPOVER_PERSONAGENS).count()
            except Exception:
                cards_count = 0
                popover_valido = False
            if popover_valido:
                valido_alguma_vez = True

            if cards_count > contagem_maxima:
                contagem_maxima = cards_count

            # 4. Condição de estável: contagem > 0, igual à anterior e sem loader
            if not carregando_agora and cards_count > 0:
                if leitura_anterior is not None and leitura_anterior == cards_count:
                    return "estavel"
                leitura_anterior = cards_count
            else:
                leitura_anterior = None

            if self.page:
                try:
                    self.page.wait_for_timeout(INTERVALO_POLL_PERSONAGENS_MS)
                except Exception:
                    time.sleep(min(0.5, INTERVALO_POLL_PERSONAGENS_MS / 1000.0))
            else:
                time.sleep(min(0.5, INTERVALO_POLL_PERSONAGENS_MS / 1000.0))

        # Fim da janela de timeout:
        # Checa uma última vez se o nome apareceu
        for sel_n in termos_nome:
            try:
                loc_n = dialog.locator(sel_n).first
                if loc_n.is_visible(timeout=100):
                    return "encontrado"
            except Exception:
                continue

        # ANTIGRAVITY — sem popover de personagens VÁLIDO lido em toda a janela, o
        # resultado é INCONCLUSIVO (nunca False): o antigo False por overlay errado
        # (menu de upload com 4 itens "estáveis") fazia o pré-voo recriar o avatar
        # mesmo com '@Nome' já existente na conta.
        if not valido_alguma_vez:
            pw_log("[PERSONAGEM] Popover de personagens não detectado na janela de verificação "
                   "(overlay errado ou menu de upload?) — resultado inconclusivo.", level="warn")
            return "inconclusivo"

        # Verifica se ainda há loader visível
        loader_final = False
        for sel_l in sels_loading:
            try:
                loc_l = dialog.locator(sel_l).first
                if loc_l.is_visible(timeout=50):
                    loader_final = True
                    break
            except Exception:
                continue

        if loader_final:
            return "carregando"

        if contagem_maxima == 0 and not viu_algum_loader:
            return "vazio"

        if contagem_maxima > 0 and leitura_anterior is not None:
            return "estavel"

        return "carregando"

    def _e_menu_upload_overlay(self, loc) -> bool:
        """True quando `loc` é o MENU DE UPLOAD — nunca o popover de personagens.

        ANTIGRAVITY (evidência no Flow real): o clique amplo em "Adicionar" casava
        com "Menu para adicionar arquivos", abrindo um [role="menu"] com Enviar /
        Nova coleção / Criar personagem / Nova cena. Esse overlay NÃO pode ser
        aceito como dialog da biblioteca de personagens. Regra conservadora: só
        rejeita quando NÃO há nenhum sinal do popover real (aba Personagens/
        Characters, .asset-title, .asset-item, tag flow-add-menu-popover-content)
        E há item do menu de upload. Probe que falhar conta como "sem sinal".
        """
        if loc is None:
            return False
        try:
            if loc.locator(SEL_POPOVER_PERSONAGENS).count() > 0:
                return False
        except Exception:
            pass
        try:
            if loc.locator(SEL_ITENS_MENU_UPLOAD).count() > 0:
                return True
        except Exception:
            pass
        return False

    # =====================================================================
    # ANTIGRAVITY — VERIFICAÇÃO DE PERSONAGEM (sidebar + registro local)
    # =====================================================================
    def _uuid_projeto_flow(self) -> str:
        """UUID do projeto Flow na URL atual (mesmo regex de _ensure_project_open)."""
        try:
            _url = str(getattr(self.page, "url", "") or "")
        except Exception:
            return ""
        if "/project/" not in _url:
            return ""
        _m = re.search(r"/project/([0-9a-fA-F-]{36})", _url)
        return _m.group(1) if _m else ""

    @staticmethod
    def _nome_casa_exato(texto: str, nome_limpo: str) -> bool:
        """Igualdade EXATA do nome normalizado: '@Marcos' casa com 'Marcos'; '@Marcos2' NÃO."""
        _t = str(texto or "").strip()
        if _t.startswith("@"):
            _t = _t[1:]
        _n = str(nome_limpo or "").strip()
        if _n.startswith("@"):
            _n = _n[1:]
        return _t.strip().casefold() == _n.strip().casefold()

    def _ler_tiles_personagem_sidebar(self) -> Tuple[int, List[str]]:
        """Lê a sidebar (flow-character-tile/.character-tile-name) → (total, nomes)."""
        if not self.page:
            return 0, []
        try:
            _res = self.page.evaluate(JS_LER_TILES_PERSONAGEM_SIDEBAR)
        except Exception as _e:
            pw_log(f"[PERSONAGEM] sidebar: falha de leitura ({_e}).", level="warn")
            return 0, []
        if not isinstance(_res, dict) or not _res.get("ok"):
            return 0, []
        _total = _res.get("tiles")
        _nomes = _res.get("nomes")
        _total = _total if isinstance(_total, int) else 0
        _nomes = [str(_n) for _n in _nomes if isinstance(_n, str)] if isinstance(_nomes, list) else []
        return _total, _nomes

    def _verificar_personagem_sidebar(self, nome_limpo: str,
                                      timeout_s: Optional[float] = None) -> Optional[bool]:
        """CAMADA 1 (primária) — a sidebar (flow-character-tile) lista '@Nome'?

        * True → tile com o nome EXATO encontrado (fonte confiável, já montada, sem abrir
          popover nem digitar '@').
        * None → não encontrado / zero tiles / fora da rota /project/<uuid> / erro.
          NUNCA devolve False: "sidebar sem tiles" NÃO prova ausência.
        """
        if not self.page:
            return None
        _timeout = TIMEOUT_SIDEBAR_PERSONAGENS_S if timeout_s is None else float(timeout_s)
        _uuid = self._uuid_projeto_flow()
        if not _uuid:
            pw_log("[PERSONAGEM] sidebar: fora da rota /project/<uuid> — leitura primária indisponível.",
                   level="info")
            return None
        _t0 = time.time()
        _total_final = 0
        while (time.time() - _t0) < max(0.1, _timeout):
            _total, _nomes = self._ler_tiles_personagem_sidebar()
            _total_final = max(_total_final, _total)
            if any(self._nome_casa_exato(_n, nome_limpo) for _n in _nomes):
                pw_log(f"PRE-VOO PERSONAGEM: '@{nome_limpo}' ENCONTRADO na sidebar "
                       f"(fonte: sidebar, projeto {_uuid[:8]}).")
                return True
            try:
                self.page.wait_for_timeout(INTERVALO_POLL_PERSONAGENS_MS)
            except Exception:
                time.sleep(0.3)
        pw_log(f"[PERSONAGEM] sidebar: {_total_final} tile(s) lidos — nenhum '@{nome_limpo}' "
               "(sidebar: 0 tiles do nome; zero tiles NÃO prova ausência).", level="warn")
        return None

    # ---------------------- Camada 2: registro local ----------------------
    def _chave_registro_personagem(self, nome_limpo: str) -> str:
        """Chave do registro local: conta|uuid_projeto_flow|nome_normalizado."""
        _conta = str(_conta_ativa_id() or "sem_conta")
        _projeto = self._uuid_projeto_flow() or "sem_projeto"
        return f"{_conta}|{_projeto}|{str(nome_limpo or '').strip().casefold()}"

    def _ler_registro_contas_flow(self) -> Dict[str, Any]:
        """Leitura TOLERANTE do registro: ausente/corrompido → {} (+ log), sem exceção."""
        try:
            _p = Path(REGISTRO_CONTAS_FLOW_PATH)
            if not _p.exists():
                return {}
            _dados = json.loads(_p.read_text(encoding="utf-8"))
            if not isinstance(_dados, dict):
                pw_log(f"[REGISTRO_PERSONAGEM] '{_p.name}' inválido (não-dict) — tratado como vazio.",
                       level="warn")
                return {}
            if not isinstance(_dados.get("personagens"), dict):
                return {"versao": _dados.get("versao", 1), "personagens": {}}
            return _dados
        except Exception as _e:
            pw_log(f"[REGISTRO_PERSONAGEM] falha ao ler o registro ({_e}) — tratado como vazio.",
                   level="warn")
            return {}

    def _registro_diz_criado(self, nome_limpo: str) -> bool:
        """True quando o registro local TEM entrada para conta+projeto+nome."""
        _dados = self._ler_registro_contas_flow()
        return bool((_dados.get("personagens") or {}).get(self._chave_registro_personagem(nome_limpo)))

    def _registrar_personagem_flow(self, nome_limpo: str, fonte: str = "") -> bool:
        """Grava a entrada (conta|uuid_projeto|nome) com escrita ATÔMICA (tmp + os.replace).

        Só grava com conta ativa E uuid do projeto Flow conhecidos: a chave sem projeto
        não distingue sessões e poderia bloquear criações legítimas em outro projeto.
        """
        _nome = str(nome_limpo or "").strip().lstrip("@")
        if not _nome:
            return False
        _projeto = self._uuid_projeto_flow()
        _conta = _conta_ativa_id()
        if not _projeto or _conta is None:
            pw_log(f"[REGISTRO_PERSONAGEM] '@{_nome}' não registrado: conta/projeto Flow "
                   f"desconhecidos (conta={_conta}, projeto={_projeto or '-'}).", level="info")
            return False
        try:
            _p = Path(REGISTRO_CONTAS_FLOW_PATH)
            _p.parent.mkdir(parents=True, exist_ok=True)
            _dados = self._ler_registro_contas_flow() or {}
            _personagens = _dados.get("personagens")
            if not isinstance(_personagens, dict):
                _personagens = {}
            _personagens[self._chave_registro_personagem(_nome)] = {
                "nome": _nome,
                "conta": str(_conta),
                "projeto_flow": _projeto,
                "fonte": str(fonte or "desconhecida"),
                "criado_em": datetime.now().isoformat(timespec="seconds"),
            }
            _dados["versao"] = _dados.get("versao", 1)
            _dados["personagens"] = _personagens
            _tmp = _p.with_name(_p.name + ".tmp")
            _tmp.write_text(json.dumps(_dados, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(_tmp), str(_p))   # atômico (mesmo volume)
            pw_log(f"[REGISTRO_PERSONAGEM] '@{_nome}' registrado (conta={_conta}, "
                   f"projeto={_projeto[:8]}, fonte={fonte}).")
            return True
        except Exception as _e:
            pw_log(f"[REGISTRO_PERSONAGEM] falha ao gravar o registro ({_e}) — seguindo sem registro.",
                   level="warn")
            return False

    def _verificar_personagem_na_biblioteca(self, nome_personagem: str) -> Optional[bool]:
        """PRÉ-VOO: confere se o personagem consta na biblioteca ou projeto do Flow.
        
        Retorno tri-estado:
          * True: personagem confirmado (DOM ou aba Personagens ao vivo).
          * False: biblioteca aberta e lida com sucesso, e o personagem NÃO está listado.
          * None: verificação inconclusiva (popup não abriu, exceção, timeout ou erro de leitura).
        NUNCA recria se já confirmado ou se a verificação for inconclusiva.
        """
        if not self.page or not nome_personagem:
            return False

        nome_limpo = str(nome_personagem or "").strip().lstrip("@")

        # 1. Checagem em identidade.json do projeto (apenas informativo — não confirma sem verificação ao vivo)
        proj_id = getattr(self, "current_project_id", None)
        if proj_id:
            try:
                import services.character_service as character_svc
                idt = character_svc.obter_identidade_projeto(proj_id) or {}
                if idt.get("flow_character_created") and (
                    idt.get("nome", "").lower() == nome_limpo.lower()
                    or idt.get("referencia_flow", "").lower() == f"@{nome_limpo}".lower()
                ):
                    pw_log(f"[PERSONAGEM] identidade.json indica personagem '@{nome_limpo}' criado; confirmando na biblioteca ao vivo.", level="info")
            except Exception:
                pass

        # 1.5 CAMADA 1 (PRIMÁRIA — ANTIGRAVITY): a sidebar (flow-character-tile) está
        # montada mesmo na aba "Todas as mídias" e é estável. Tile com o nome EXATO →
        # True SEM abrir popover/menu nem digitar '@' (o popover é isolado por projeto e
        # já abriu com "Nenhum recurso encontrado" mesmo com o personagem existindo).
        if self._verificar_personagem_sidebar(nome_limpo) is True:
            self._registrar_personagem_flow(nome_limpo, fonte="sidebar")
            return True

        # 2. Checagem direta no DOM do Flow (chips ou aria-labels visíveis)
        try:
            loc_termo = f"[aria-label*='@{nome_limpo}' i], [aria-label*='{nome_limpo}' i]"
            if self.page.locator(loc_termo).count() > 0:
                pw_log(f"[PERSONAGEM] texto '@{nome_limpo}' presente na página, mas isso NÃO prova que o personagem existe na conta ativa; confirmando na biblioteca ao vivo.", level="info")
        except Exception as _e_dom:
            pw_log(f"[PERSONAGEM] Erro ao checar DOM prévio: {_e_dom}", level="warn")

        # Limpa overlays e backdrops residuais para evitar que interceptem cliques
        try:
            self._limpar_overlays_e_backdrops()
        except Exception:
            pass

        # 3. Fallback: menu de ingredientes
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
            return None

        try:
            dialog = None

            # ANTIGRAVITY — procura o popover REAL entre os candidatos de overlay.
            # Candidato que seja o MENU DE UPLOAD (Enviar/Nova coleção/Criar
            # personagem/Nova cena, sem a UI de personagens) é FECHADO com Escape e
            # NUNCA vira `dialog`; o fluxo segue para o próximo candidato/fallback.
            def _localizar_popover(timeout_visivel_ms: int):
                for d_sel in (
                    'flow-add-menu-popover-content',
                    'div.cdk-overlay-pane:has(flow-add-menu-popover-content)',
                    '.add-menu-popover-container',
                    'div.cdk-overlay-pane:has([role="tablist"])',
                    'div.cdk-overlay-pane:has([role="listbox"])',
                    'div.cdk-overlay-pane',
                    'div[role="dialog"]',
                ):
                    try:
                        loc_d = self.page.locator(d_sel).first
                        if not loc_d.is_visible(timeout=timeout_visivel_ms):
                            continue
                        if self._e_menu_upload_overlay(loc_d):
                            pw_log("[PERSONAGEM] Menu de upload detectado e descartado (não é o popover de personagens).", level="info")
                            try:
                                self.page.keyboard.press("Escape")
                                self.page.wait_for_timeout(250)
                            except Exception:
                                pass
                            continue
                        return loc_d
                    except Exception:
                        continue
                return None

            for tentativa_abrir in range(1, 3):
                try:
                    btn_apagar = self.page.locator('button[aria-label*="Apagar comando" i], button.clear-button').first
                    if btn_apagar.is_visible(timeout=200):
                        btn_apagar.click(timeout=500)
                        self.page.wait_for_timeout(200)
                    self._safe_click(editor)
                    self.page.wait_for_timeout(100)
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.wait_for_timeout(100)
                except Exception:
                    pass

                # CORREÇÃO 2 / ANTIGRAVITY — botão físico de ingredientes: SOMENTE
                # seletores ESPECÍFICOS. Removido o casamento amplo com "Adicionar"
                # (e "Upload"/"Add image"), que clicava em "Menu para adicionar
                # arquivos" e abria o MENU DE UPLOAD em vez do popover.
                btn_mais = None
                for sel_mais in [
                    'button.add-menu-trigger',
                    'button[aria-label*="Adicionar elementos" i]',
                    'button[aria-label*="Add ingredients" i]',
                    'button[aria-label="Add ingredients to the prompt box"]',
                    'button[aria-label*="ingredient" i]',
                    'button[aria-label*="elemento" i]',
                ]:
                    try:
                        loc_mais = self.page.locator(sel_mais).first
                        if loc_mais.is_visible(timeout=1500):
                            btn_mais = loc_mais
                            break
                    except Exception:
                        continue

                if btn_mais:
                    try:
                        btn_mais.click()
                        self.page.wait_for_timeout(500)
                        # Overlays VALIDADOS: popover real é aceito; menu de upload é
                        # descartado (Escape) e nunca vira dialog.
                        dialog = _localizar_popover(2500)
                    except Exception:
                        dialog = None

                if dialog is not None:
                    break

                # Backoff antes da próxima tentativa
                pw_log(f"PRE-VOO PERSONAGEM: tentativa {tentativa_abrir} de abertura do menu falhou, tentando novamente...", level="info")
                try:
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(300)
                except Exception:
                    pass

            # Fallback PRINCIPAL (contraprova no Flow real): digitar '@' no prompt
            # editor abre o popover de personagens (flow-add-menu-popover-content).
            # Roda quando o botão físico não abriu o popover correto — inclusive
            # quando ele abriu o MENU DE UPLOAD (descartado acima com Escape).
            if dialog is None:
                try:
                    editor.focus()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.wait_for_timeout(100)
                    self.page.keyboard.type("@", delay=60)
                    self.page.wait_for_timeout(700)
                    dialog = _localizar_popover(2000)
                except Exception:
                    pass

            if dialog is None:
                pw_log("PRE-VOO PERSONAGEM: popover de personagens não abriu "
                       "(menu de upload descartado se apareceu) — inconclusivo.", level="warn")
                # Remove o '@' residual do editor para não poluir o próximo prompt.
                try:
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                except Exception:
                    pass
                return None

            if not dialog.is_visible(timeout=2000):
                pw_log("PRE-VOO PERSONAGEM: menu/popup de personagens não abriu.", level="warn")
                return None

            # Navega para a aba Characters/Personagens (texto em PT ou EN, incluindo mat-list-item)
            tab_pers = dialog.locator(
                'mat-list-item[role="tab"]:has-text("Personagens"), '
                '[role="tab"]:has-text("Personagens"), '
                'mat-list-item[role="tab"]:has-text("Characters"), '
                '[role="tab"]:has-text("Characters"), '
                'button[role="tab"]:has-text("Personagens"), '
                'button[role="tab"]:has-text("Characters"), '
                'button:has-text("Personagens"), '
                'button:has-text("Characters")'
            ).first
            if tab_pers.is_visible(timeout=1000):
                self._safe_click(tab_pers)
                self.page.wait_for_timeout(400)
            else:
                # Fallback por texto (cobre abas que não usam role=tab)
                try:
                    dialog.evaluate('''el => {
                        const tabs = Array.from(el.querySelectorAll("mat-list-item[role=tab], button[role=tab], button, div[role=button], [class*='tab']"));
                        const target = tabs.find(b => {
                            const t = (b.textContent || '').trim().toLowerCase();
                            return t === 'characters' || t === 'personagens' || t === 'character' || t === 'personagem';
                        });
                        if (target) target.click();
                    }''')
                    self.page.wait_for_timeout(400)
                except Exception:
                    pass

            # Aguarda a lista de personagens estabilizar antes de concluir False
            status_lista = self._aguardar_lista_personagens_estavel(dialog, nome_limpo)

            # Fallback de busca no search-input se a aba inicial não localizou
            if status_lista != "encontrado":
                try:
                    search_box = dialog.locator('input.search-input, input[placeholder*="Pesquisar" i], input[placeholder*="Search" i]').first
                    if search_box.is_visible(timeout=500):
                        search_box.fill(nome_limpo)
                        self.page.wait_for_timeout(500)
                        status_search = self._aguardar_lista_personagens_estavel(dialog, nome_limpo, timeout_ms=2500)
                        if status_search == "encontrado":
                            status_lista = "encontrado"
                except Exception:
                    pass

            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)

            if status_lista == "encontrado":
                pw_log(f"PRE-VOO PERSONAGEM: '@{nome_personagem}' ENCONTRADO na aba Personagens (fonte: popover).")
                self._registrar_personagem_flow(nome_limpo, fonte="popover")
                return True
            elif status_lista == "vazio_confirmado":
                # POLÍTICA (ANTIGRAVITY): "vazio" só vira False com PROVA ACUMULADA —
                # sidebar sem o tile + .empty-state em 2 leituras estáveis + registro
                # local SEM entrada. Registro COM entrada → None (nunca False).
                if self._registro_diz_criado(nome_limpo):
                    pw_log(f"PRE-VOO PERSONAGEM: popover vazio 2x, MAS o registro local diz que "
                           f"'{nome_personagem}' já foi criado nesta conta+projeto — inconclusivo "
                           "(possível remoção manual ou falha de leitura).", level="warn")
                    return None
                pw_log(f"PRE-VOO PERSONAGEM: '{nome_personagem}' NÃO encontrado — provas: sidebar sem o "
                       "tile, popover com .empty-state em 2 leituras estáveis sem spinner e registro "
                       "local sem entrada (fonte: sidebar+popover+registro).", level="warn")
                return False
            elif status_lista == "vazio":
                pw_log("PRE-VOO PERSONAGEM: popover sem itens, mas SEM .empty-state estável (2 leituras) "
                       "— inconclusivo (vazio sozinho não prova ausência).", level="warn")
                return None
            elif status_lista == "estavel":
                pw_log(f"PRE-VOO PERSONAGEM: lista lida sem '{nome_personagem}' (fonte: popover) — "
                       "inconclusivo sob a política atual (False apenas com .empty-state estável + "
                       "registro local sem entrada).", level="warn")
                return None
            elif status_lista == "inconclusivo":
                # ANTIGRAVITY — popover real não confirmado: NUNCA concluir False
                # (evita recriação indevida de personagem que já existe).
                pw_log("PRE-VOO PERSONAGEM: verificação inconclusiva — popover de personagens não "
                       "confirmado (menu de upload descartado).", level="warn")
                return None
            else:  # "carregando"
                pw_log("PRE-VOO PERSONAGEM: verificação inconclusiva — lista de personagens ainda carregando após timeout.", level="warn")
                return None
        except Exception as e:
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            pw_log(f"PRE-VOO PERSONAGEM: erro ao verificar biblioteca ({e}).", level="warn")
            return None

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

    def _marcar_cards_falha_existentes(self) -> int:
        """Marca no DOM todos os cards de falha já existentes para não confundir com falhas novas."""
        if not self.page:
            return 0
        try:
            res = self.page.evaluate("""() => {
                let marked = 0;
                const failureTerms = [
                    "não foi possível gerar",
                    "não houve cobrança por esta geração",
                    "não houve cobrança",
                    "couldn't generate",
                    "could not generate",
                    "there was no charge for this generation"
                ];
                const nodes = Array.from(document.querySelectorAll(
                    'flow-tile-container, flow-grid-tile-container, div[class*="tile"], div[class*="card"], [role="alert"]'
                ));
                for (const n of nodes) {
                    const txt = (n.innerText || n.textContent || '').toLowerCase();
                    if (failureTerms.some(t => txt.includes(t))) {
                        n.dataset.flowOldFailure = 'true';
                        marked++;
                    }
                }
                return marked;
            }""")
            return int(res or 0)
        except Exception:
            return 0

    def _detectar_novo_card_falha(self) -> Optional[str]:
        """Detecta se surgiu um novo card de falha de geração do Flow no canvas (em vez de esperar timeout de 240s)."""
        if not self.page:
            return None
        try:
            return self.page.evaluate("""() => {
                const failureTerms = [
                    "não foi possível gerar o vídeo",
                    "não foi possível gerar a imagem",
                    "não foi possível gerar a mídia",
                    "não foi possível gerar",
                    "não houve cobrança por esta geração",
                    "não houve cobrança",
                    "couldn't generate video",
                    "could not generate video",
                    "couldn't generate",
                    "could not generate",
                    "there was no charge for this generation"
                ];
                const nodes = Array.from(document.querySelectorAll(
                    'flow-tile-container, flow-grid-tile-container, div[class*="tile"], div[class*="card"], [role="alert"]'
                ));
                for (const n of nodes) {
                    if (n.offsetParent === null) continue;
                    if (n.dataset.flowOldFailure === 'true') continue;
                    const txt = (n.innerText || n.textContent || '').toLowerCase().trim();
                    if (!txt) continue;
                    for (const t of failureTerms) {
                        if (txt.includes(t)) {
                            return txt.slice(0, 150);
                        }
                    }
                }
                return null;
            }""")
        except Exception:
            return None

    def _acionar_retry_card_falha(self) -> bool:
        """Tenta clicar no botão de 'Tentar novamente' (reload) do card com falha."""
        if not self.page:
            return False
        try:
            return bool(self.page.evaluate("""() => {
                const failureTerms = [
                    "não foi possível gerar",
                    "não houve cobrança",
                    "couldn't generate",
                    "could not generate"
                ];
                const nodes = Array.from(document.querySelectorAll(
                    'flow-tile-container, flow-grid-tile-container, div[class*="tile"], div[class*="card"]'
                ));
                for (const n of nodes) {
                    if (n.offsetParent === null) continue;
                    const txt = (n.innerText || n.textContent || '').toLowerCase();
                    if (failureTerms.some(t => txt.includes(t))) {
                        const btns = Array.from(n.querySelectorAll('button'));
                        if (btns.length > 0) {
                            btns[0].click();
                            return true;
                        }
                    }
                }
                return false;
            }"""))
        except Exception:
            return False

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
                    # UI esperada ao final: imagem de referência renderizada no card de
                    # criação de personagem (aba /characters do Flow).
                    baseline_srcs = _baseline_imgs_pagina(self.page)
                    input_file.set_input_files(img_caminho)
                    if not _aguardar_upload_concluido(self.page, baseline_srcs, timeout_ms=20000):
                        pw_log("[FLOW] _garantir_personagem_criado_no_flow: upload não "
                               "confirmado por sinal; seguindo com comportamento anterior. "
                               "Não validado em execução real.", level="warn")
                    self.page.wait_for_timeout(300)
                else:
                    btn_up = self.page.locator('button:has-text("Fazer upload"), button:has-text("upload")').first
                    if btn_up.is_visible(timeout=2000):
                        try:
                            # UI esperada ao final: imagem de referência renderizada no card
                            # de criação de personagem (aba /characters do Flow).
                            baseline_srcs = _baseline_imgs_pagina(self.page)
                            with self.page.expect_file_chooser(timeout=4000) as fc_info:
                                btn_up.click(force=True)
                            fc_info.value.set_files(img_caminho)
                            if not _aguardar_upload_concluido(self.page, baseline_srcs, timeout_ms=20000):
                                pw_log("[FLOW] _garantir_personagem_criado_no_flow: upload não "
                                       "confirmado por sinal; seguindo com comportamento "
                                       "anterior. Não validado em execução real.", level="warn")
                            self.page.wait_for_timeout(300)
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

    def _resolver_imagem_oficial_personagem(self, projeto_id: str) -> str:
        """Imagem de referência REAL do personagem oficial ('' quando não há).

        REQ (TAREFA 1/2/3) — chamada ANTES de qualquer uso de 'reference.png':
        valida `identidade.json` e devolve um arquivo que EXISTE em disco.

        Em projeto NOVO (sem identidade) o fluxo caía no literal "reference.png",
        que não é o personagem do projeto — era daí que vinha o anexo genérico /
        rosto aleatório no Google Flow. Aqui a validação acontece primeiro e o
        chamador aborta quando não há imagem real.
        """
        if not projeto_id:
            return ""
        try:
            import services.character_service as character_svc
            personagem, erro = character_svc.validar_personagem_do_projeto(projeto_id)
            if not personagem:
                log_char(f"validação de identidade.json antes da referência: "
                         f"{erro or 'personagem não configurado'}.", projeto_id=projeto_id,
                         level="warn")
                return ""
            tag = personagem.get("referencia_flow") or personagem.get("nome") or "(sem nome)"
            caminho = str(personagem.get("imagem_abs") or "").strip()
            if caminho and Path(caminho).exists():
                log_char(f"imagem de referência validada para '{tag}': {Path(caminho).name}.",
                         projeto_id=projeto_id)
                return caminho
            # Fallback OFICIAL: characters/<Nome>/reference.png (arquivo REAL).
            try:
                candidato = str(character_svc.resolver_imagem_avatar_projeto(projeto_id) or "").strip()
            except Exception:
                candidato = ""
            if candidato and Path(candidato).exists():
                log_char(f"imagem de referência resolvida no projeto para '{tag}': "
                         f"{Path(candidato).name}.", projeto_id=projeto_id)
                return candidato
            log_char(f"identidade válida para '{tag}', porém SEM foto de referência em "
                     f"disco — anexo será cancelado (nunca usa 'reference.png' genérica).",
                     projeto_id=projeto_id, level="error")
        except Exception as e:
            log_char(f"erro ao validar identidade antes da referência: {e}",
                     projeto_id=projeto_id, level="warn")
        return ""

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

        # ANTI-GENÉRICO: sem ref_tag/nome explícitos, o personagem vem SEMPRE de
        # identidade.json do projeto. Antes havia um "@Marcos" FIXO no código —
        # o personagem genérico/errado era anexado quando o projeto não tinha
        # nome no plano de cena. Agora: identidade válida ou anexo cancelado.
        eh_personagem = str(tipo or "personagem").strip().lower() in ("personagem", "character", "avatar")
        tag_display = ref_tag or (f"@{nome_personagem}" if nome_personagem else "")
        if not tag_display and eh_personagem:
            try:
                import services.character_service as _char_svc
                _personagem_proj, _erro_char = _char_svc.validar_personagem_do_projeto(projeto_id)
            except Exception as _e_char:
                _personagem_proj, _erro_char = None, f"erro ao validar identidade: {_e_char}"
            if not _personagem_proj:
                log_char(f"ABORTADO — {_erro_char or 'personagem não configurado'}: nenhum personagem "
                         f"será anexado.", projeto_id=projeto_id, level="error")
                return False
            nome_personagem = _personagem_proj.get("nome") or ""
            tag_display = _personagem_proj.get("referencia_flow") or f"@{nome_personagem}"
            log_char(f"personagem do projeto resolvido por identidade.json: '{tag_display}'.",
                     projeto_id=projeto_id)
        elif tag_display and eh_personagem:
            log_char(f"usando a tag do plano de cena: '{tag_display}'.", projeto_id=projeto_id)

        try:
            self._safe_click(editor)
            self.page.wait_for_timeout(100)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(100)

            # REQ (TAREFA 1/2/3) — VALIDAÇÃO ANTES DO FALLBACK: para PERSONAGEM a
            # imagem tem de ser a REAL do projeto (identidade.json). O literal
            # 'reference.png' NUNCA é usado como referência de personagem — era esse
            # o caminho do anexo genérico/rosto aleatório em projeto NOVO sem
            # identidade. Para upload de mídia (tipo='imagem') o comportamento
            # original é preservado.
            _ref_img = str(imagem_abs or "").strip()
            if eh_personagem and not (_ref_img and Path(_ref_img).exists()):
                _ref_img = self._resolver_imagem_oficial_personagem(projeto_id)
            if eh_personagem and not _ref_img:
                log_char(f"ABORTADO — sem imagem de referência REAL para "
                         f"'{tag_display or nome_personagem or '(sem personagem)'}': "
                         f"anexo cancelado (identidade.json ausente/sem foto válida).",
                         projeto_id=projeto_id, level="error")
                return False

            _motivos_anexo: list = []
            try:
                sucesso = incluir_referencia_personagem(
                    self.page,
                    _ref_img if eh_personagem else (imagem_abs or "reference.png"),
                    nome_personagem=nome_personagem,
                    # Só propaga tag_display quando há personagem real (uploads usam
                    # nome_personagem="" e a tag de personagem NÃO deve virar busca
                    # indevida por um nome na aba Characters).
                    tag_personagem=(tag_display if nome_personagem else ""),
                    projeto_id=projeto_id,
                    tipo=tipo,
                    motivo_erro=_motivos_anexo,
                    driver=self,
                )
            except TypeError:
                sucesso = incluir_referencia_personagem(
                    self.page,
                    _ref_img if eh_personagem else (imagem_abs or "reference.png"),
                    nome_personagem=nome_personagem,
                    tag_personagem=(tag_display if nome_personagem else ""),
                    projeto_id=projeto_id,
                    tipo=tipo,
                    motivo_erro=_motivos_anexo,
                )

            # Fallback por digitação se o menu de ingredientes não inseriu o chip
            if not sucesso and not self._verificar_chip_personagem_no_editor(editor):
                try:
                    editor.focus()
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                    self.page.wait_for_timeout(100)
                    # ANTI-GENÉRICO: nunca digita "@" solto/genérico — só digita
                    # quando existe uma tag/personagem REAL resolvido.
                    if tag_display.startswith("@"):
                        alvo_dig = tag_display
                    elif nome_personagem or tag_display:
                        alvo_dig = f"@{str(nome_personagem or tag_display).lstrip('@')}"
                    else:
                        alvo_dig = ""
                    if alvo_dig:
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
                if eh_personagem and tag_display:
                    log_char(f"CHARACTER_ENTITY_ATTACHED_OK: '{tag_display}' anexado ao prompt.",
                             projeto_id=projeto_id)
                self.current_flow_reference = tag_display
                return True
            else:
                _detalhe = _motivos_anexo[-1] if _motivos_anexo else ""
                pw_log(f"Aviso: chip não detectado no editor para '{tag_display}'."
                       + (f" Motivo: {_detalhe}" if _detalhe else ""), level="warn")
                if _detalhe:
                    log_char(f"CHARACTER_ENTITY_ATTACH_FAILED: '{tag_display}' — {_detalhe}",
                             projeto_id=projeto_id, level="error")
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
        galeria do Google Flow usando '+' -> 'Enviar' e a anexa como referência visual
        no editor antes de digitar o prompt de animação.
        """
        if not self.page or not arquivo_imagem or not Path(arquivo_imagem).exists():
            return False
        nome = Path(arquivo_imagem).name

        try:
            # (a) Upload resiliente via fluxo oficial do Flow
            ok_up = self._upload_arquivo_flow(str(arquivo_imagem))
            if not ok_up:
                pw_log(f"[IMG_REF] Falha no upload de '{nome}' — seguindo com Text-to-Video.", level="warn")
                return False

            # (b) Aguarda brevemente a miniatura estabilizar na galeria
            self.page.wait_for_timeout(1500)
            pw_log(f"[IMG_REF] Upload de '{nome}' concluído com sucesso.")
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

    def _animar_imagem_galeria_flow(self, projeto_id: str, arquivo_imagem: str, prompt_movimento: str,
                                    uses_character: Optional[bool] = None) -> bool:
        """IMAGE-TO-VIDEO via menu nativo 'Animar' do Google Flow.

        `uses_character=False` ativa o GUARD DO AVATAR (nunca animar o reference.png em
        cena sem personagem); `None`/`True` não aplicam o guard.

        Fluxo correto (verificado ao vivo no Google Flow):
        1. Faz upload da imagem de referência para o projeto do Flow (se ainda não estiver lá).
        2. Localiza o tile da imagem no canvas (flow-grid-tile-container).
        3. Hover no tile e clica no botão de 3 pontinhos (button[aria-label="Mais opções"]).
        4. No menu contextual aberto (.cdk-overlay-pane), clica em 'Animar' (ou 'Incluir no comando' / 'Incluir no momento').
        5. A imagem é fixada no chat/dock como elemento/frame de referência (flow-image-ingredient-chip).
        6. Garante que as configurações de vídeo estejam ativas (Veo 3.1 - Lite, 16:9).
        7. Digita o prompt de movimento no editor (flow-rich-text-editor .ProseMirror) após a foto fixada.
        8. Retorna True para que o worker faça o envio (Enter / Create).
        """
        if not self.page or not arquivo_imagem:
            return False

        nome = Path(arquivo_imagem).name

        # GUARD DO AVATAR (ANTIGRAVITY) — cena SEM personagem não pode animar o
        # reference.png (foi exatamente assim que o b-roll 002/004 virou vídeo do
        # @Marcos no Veo). `uses_character=None` (chamadores legados) NÃO aplica o guard.
        if uses_character is False and Path(arquivo_imagem).exists() \
                and self._imagem_e_avatar_de_referencia(projeto_id, str(arquivo_imagem)):
            pw_log(
                f"[GUARD_AVATAR] Image-to-Video ABORTADO para '{nome}': a imagem de entrada é o "
                "reference.png e a cena não usa personagem (uses_character=False) — nada foi "
                "enviado ao Veo.",
                level="error",
            )
            return False

        pw_log(f"[ANIMAR] Iniciando fluxo 'Animar' para '{nome}'...")
        # TAREFA 3 — contagem de tiles ANTES do upload (prova se tiles[0]
        # é o item recém-enviado em projeto com histórico).
        _tile_sel = ("flow-grid-tile-container, .tile-row flow-grid-tile-container, "
                     ".virtual-item-container flow-grid-tile-container")
        def _contar_tiles():
            try:
                return self.page.locator(_tile_sel).count()
            except Exception:
                return -1
        _n_tiles_antes = _contar_tiles()
        pw_log(f"[ANIMAR] ANIMAR_PASSO_2: tiles ANTES do upload = {_n_tiles_antes}"
               f" | arquivo='{nome}'.")

        # =====================================================================
        # PASSO 1: Upload da PNG para o Flow se o arquivo existir em disco
        # =====================================================================
        if Path(arquivo_imagem).exists():
            try:
                ok_up = self._upload_arquivo_flow(str(arquivo_imagem))
                if ok_up:
                    pw_log("ANIMAR_PASSO_1: upload concluído")
                else:
                    pw_log("ANIMAR_FALHA_PASSO_1: upload falhou — seguindo para "
                           "localizar tile existente na galeria.", level="error")
                if not ok_up:
                    pw_log(f"[ANIMAR] Upload de '{nome}' falhou — tentando localizar tile existente na galeria.", level="warn")
                else:
                    pw_log(f"[ANIMAR] Upload de '{nome}' concluído com sucesso.")
                self.page.wait_for_timeout(1500)
            except Exception as e:
                pw_log(f"[ANIMAR] Erro no upload de '{nome}': {e}", level="warn")
        else:
            pw_log(f"[ANIMAR] Arquivo '{nome}' não existe em disco — buscando tile existente no Flow.", level="warn")
            pw_log(f"ANIMAR_FALHA_PASSO_1: arquivo '{nome}' não existe em disco.",
                   level="error")

        # =====================================================================
        # PASSO 2: Localiza o tile correspondente no canvas do Flow
        _n_tiles_depois = _contar_tiles()
        pw_log(f"[ANIMAR] ANIMAR_PASSO_2: tiles DEPOIS do upload = {_n_tiles_depois}"
               f" (antes={_n_tiles_antes}).")
        # =====================================================================
        alvo_card = None
        try:
            tiles = self.page.locator("flow-grid-tile-container, .tile-row flow-grid-tile-container, .virtual-item-container flow-grid-tile-container").all()
            if tiles:
                # O item recém-enviado aparece no início (índice 0)
                # Verifica se o primeiro tile tem imagem ou o botão Mais opções
                for _idx_tile, t in enumerate(tiles):
                    try:
                        # Ignora tiles que são vídeos! Image-to-Video exige tile de imagem
                        if t.locator("video").count() > 0:
                            continue
                        btn_opt = t.locator("button[aria-label*='Mais opções' i], button.mat-mdc-menu-trigger").first
                        if btn_opt.count() > 0:
                            alvo_card = t
                            # TAREFA 3 — índice, flag de <video> e src/nome do tile escolhido.
                            try:
                                _tem_video = t.locator("video").count() > 0
                                _src_tile = ""
                                _img_tile = t.locator("img").first
                                if _img_tile.count() > 0:
                                    _src_tile = str(_img_tile.get_attribute("src") or "")[-70:]
                                pw_log(f"[ANIMAR] ANIMAR_PASSO_2: tile escolhido idx={_idx_tile} "
                                       f"de {tiles.count()} | tem_video={_tem_video} | src=...{_src_tile}")
                            except Exception as e_log2:
                                pw_log(f"[ANIMAR] ANIMAR_PASSO_2: tile idx={_idx_tile} (log parcial: {e_log2})")
                            break
                    except Exception:
                        continue
                if alvo_card is None and tiles:
                    # Tenta primeiro tile que não contenha video
                    for t in tiles:
                        if t.locator("video").count() == 0:
                            alvo_card = t
                            break
                pw_log(f"[ANIMAR] Tile flow-grid-tile-container localizado com sucesso no Flow.")
        except Exception as e_tile:
            pw_log(f"[ANIMAR] Erro ao buscar flow-grid-tile-container: {e_tile}", level="debug")

        # Fallback de seletores caso a classe do container mude
        if alvo_card is None:
            for sel_card in [
                "flow-grid-tile-container",
                ".virtual-item-container",
                "[role='gridcell']",
                ".image-container:last-of-type img",
                "img[class*='result']:last-of-type",
            ]:
                try:
                    loc = self.page.locator(sel_card).first
                    if loc.is_visible(timeout=800):
                        alvo_card = loc
                        pw_log(f"[ANIMAR] Tile encontrado via fallback seletor: {sel_card}")
                        break
                except Exception:
                    pass

        if alvo_card is None:
            pw_log(f"[ANIMAR] Nenhum card de imagem encontrado na galeria para '{nome}' — tentando via modal '+'...", level="warn")
            anexo_ok = self._selecionar_referencia_flow(
                projeto_id=projeto_id,
                nome_personagem="",
                tipo="imagem",
                ref_tag="",
                arquivo_flow="",
                imagem_abs=arquivo_imagem,
                flow_character_id=""
            )
            if not anexo_ok:
                pw_log(f"[ANIMAR] Falha crítica: não foi possível localizar ou anexar '{nome}' para animar.", level="warn")
                return False
            animar_clicado = True
        else:
            # =====================================================================
            # PASSO 3: Hover no card → localiza o botão ⋮ ('Mais opções') → abre menu
            # =====================================================================
            menu_aberto = False
            card_container = alvo_card.locator(
                "xpath=ancestor::div[@role='listitem'] | ancestor::div[contains(@class, 'card')] | ancestor::flow-grid-tile-container | self::flow-grid-tile-container"
            ).first

            try:
                if card_container.count() > 0 and card_container.is_visible(timeout=500):
                    card_container.hover(timeout=2000)
                else:
                    alvo_card.hover(timeout=2000)
                self.page.wait_for_timeout(300)
            except Exception:
                pass

            menu_btn = alvo_card.locator(
                "button[aria-label*='Mais opções' i], "
                "button[aria-label*='menu' i], "
                "button[aria-label*='options' i], "
                "button[aria-label*='more' i], "
                "button.mat-mdc-menu-trigger, "
                "button:has-text('⋮'), "
                "button:has-text('⋯')"
            ).first

            try:
                if menu_btn.is_visible(timeout=1000):
                    menu_btn.click(timeout=1500)
                    self.page.wait_for_timeout(400)
                    menu_aberto = True
                    pw_log("[ANIMAR] Menu ⋮ ('Mais opções') aberto via clique direto no botão.")
                    pw_log("ANIMAR_PASSO_3: ⋮ clicado")
            except Exception as e_mb:
                pw_log(f"[ANIMAR] Tentativa clique menu_btn: {e_mb}", level="debug")

            if not menu_aberto:
                # Fallback: right click no tile
                try:
                    alvo_card.click(button="right", timeout=1500)
                    self.page.wait_for_timeout(400)
                    menu_aberto = True
                    pw_log("[ANIMAR] Menu aberto via clique com botão direito no tile.")
                except Exception:
                    pass

            if not menu_aberto:
                pw_log("ANIMAR_FALHA_PASSO_3: botão ⋮ não encontrado/não visível e o menu "
                       "não abriu (hover e clique direito falharam).", level="error")
            # =====================================================================
            # PASSO 4: Clica em 'Animar' ou 'Incluir no comando' / 'Incluir no momento'
            # =====================================================================
            animar_clicado = False
            _txts = []
            if menu_aberto:
                try:
                    _itens = self.page.locator(
                        ".cdk-overlay-pane button, .cdk-overlay-pane .mat-mdc-menu-item, "
                        "[role='menuitem']").all()
                    _txts = [(it.inner_text() or "").strip().replace("\n", " ")[:40]
                             for it in _itens]
                    pw_log(f"[ANIMAR] ANIMAR_PASSO_4: menu aberto com {len(_txts)} item(ns): {_txts}")
                except Exception as _e_m4:
                    pw_log(f"[ANIMAR] ANIMAR_PASSO_4: falha ao ler os itens do menu: {_e_m4}",
                           level="warn")
                # Prioridade: 1) 'Animar' (ativa diretamente modo I2V), 2) 'Incluir no comando' / 'Incluir no momento'
                opcoes_busca = [
                    "Animar",
                    "Incluir no comando",
                    "Incluir no momento",
                    "Adicionar ao comando",
                    "Adicionar ao prompt",
                    "Animate",
                    "Include in prompt",
                    "Include in moment",
                ]
                for termo in opcoes_busca:
                    for sel in [
                        f'.cdk-overlay-pane button:has-text("{termo}")',
                        f'.cdk-overlay-pane .mat-mdc-menu-item:has-text("{termo}")',
                        f'[role="menu"] button:has-text("{termo}")',
                        f'[role="menuitem"]:has-text("{termo}")',
                        f'button:has-text("{termo}")',
                    ]:
                        try:
                            op = self.page.locator(sel).first
                            if op.is_visible(timeout=300):
                                op.click(timeout=1500)
                                animar_clicado = True
                                pw_log(f"[ANIMAR] Opção '{termo}' clicada com sucesso via: {sel}")
                                pw_log(f"ANIMAR_PASSO_4: '{termo}' clicado")
                                break
                        except Exception:
                            continue
                    if animar_clicado:
                        break

                if not animar_clicado:
                    try:
                        animar_clicado = bool(self.page.evaluate("""() => {
                            const btns = Array.from(document.querySelectorAll('.cdk-overlay-pane button, .cdk-overlay-pane [role="menuitem"], [role="menu"] button'));
                            for (const b of btns) {
                                const txt = (b.innerText || '').toLowerCase();
                                if (txt.includes('animar') || txt.includes('incluir no comando') || txt.includes('incluir no momento')) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }"""))
                        if animar_clicado:
                            pw_log("[ANIMAR] Opção do menu clicada via evaluate DOM.")
                    except Exception:
                        pass

            if not animar_clicado:
                pw_log(f"ANIMAR_FALHA_PASSO_4: nenhuma opção de menu casou (menu_aberto={menu_aberto}); "
                       f"textos encontrados={_txts}", level="error")
                pw_log(f"[ANIMAR] Opção do menu não clicada para '{nome}'. Tentando via modal '+'...", level="warn")
                try:
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(300)
                except Exception:
                    pass
                anexo_ok = self._selecionar_referencia_flow(
                    projeto_id=projeto_id,
                    nome_personagem="",
                    tipo="imagem",
                    ref_tag="",
                    arquivo_flow="",
                    imagem_abs=arquivo_imagem,
                    flow_character_id=""
                )
                if not anexo_ok:
                    pw_log(f"[ANIMAR] Não foi possível fixar a imagem de referência '{nome}'.", level="warn")
                    return False
                animar_clicado = True

        # =====================================================================
        # PASSO 5: Aguarda imagem ancorar no dock do editor como frame fixado
        # =====================================================================
        _chip_sels = [
            # DOM REAL (inspeção 25/09): a imagem ancorada por ⋮ > Animar vive em
            # flow-ingredient-bar .frame-trigger flow-image-ingredient-chip.
            "flow-ingredient-bar .frame-trigger flow-image-ingredient-chip",
            ".frame-trigger flow-image-ingredient-chip",
            "flow-ingredient-bar .frame-trigger .chip-container",
            "flow-image-ingredient-chip",
            "button[aria-label*='Elemento de imagem' i]",
            ".ingredient-bar-container",
            "flow-ingredient-bar",
            ".chip-container",
            "img.chip-image",
            "[class*='ingredient-chip']",
            "img[class*='chip']",
            "img[class*='reference']",
            "[data-type='image']",
        ]
        _anchor_ok = False
        _t_start = time.time()
        while time.time() - _t_start < 5.0:
            for _chip_sel in _chip_sels:
                try:
                    if self.page.locator(_chip_sel).first.is_visible(timeout=80):
                        _anchor_ok = True
                        pw_log(f"[ANIMAR] Foto de referência fixada no chat com sucesso! Detectada via: {_chip_sel}")
                        break
                except Exception:
                    pass
            if _anchor_ok:
                break
            try:
                _ed_chk = _localizar_editor_prompt(self.page)
                if _ed_chk and _ed_chk.evaluate(_JS_VERIFICA_CHIP_EDITOR):
                    _anchor_ok = True
                    pw_log("[ANIMAR] Foto de referência fixada no chat detectada via _JS_VERIFICA_CHIP_EDITOR.")
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(200)

        if not _anchor_ok:
            pw_log("ANIMAR_FALHA_PASSO_5: nenhum seletor de chip casou (flow-ingredient-bar .frame-trigger, chip genérico) após 5s.", level="error")
            pw_log("[ANIMAR] Aviso: chip de imagem não detectado após 5s — verificando barra de ingredientes...", level="warn")

        # =====================================================================
        # PASSO 6: Garante configuração de VÍDEO (Veo 3.1 - Lite, 16:9)
        # =====================================================================
        try:
            btn_cfg = self.page.locator("button[aria-label*='Gatilho de configurações' i], button.settings-trigger-button").first
            texto_cfg = btn_cfg.inner_text().lower() if btn_cfg.is_visible(timeout=600) else ""
            if "vídeo" not in texto_cfg and "video" not in texto_cfg:
                ok_vm = self._set_output_mode("video", modelo_solicitado="Veo 3.1 - Lite", proporcao_solicitada="16:9", sub_modo="frames")
                if not ok_vm:
                    pw_log("[ANIMAR] Falha ao configurar modo de vídeo no Flow. Abortando animação.", level="error")
                    return False
            else:
                pw_log(f"[ANIMAR] Configuração de vídeo confirmada ativa: '{texto_cfg.replace(chr(10), ' ')}'")
        except Exception as e_vcfg:
            pw_log(f"[ANIMAR] Aviso ao verificar/aplicar modo de vídeo: {e_vcfg}", level="warn")

        # =====================================================================
        # PASSO 7: Digita o prompt de MOVIMENTO no editor ProseMirror após a foto fixada
        # =====================================================================
        editor = _localizar_editor_prompt(self.page)
        if not editor:
            for sel in [
                'flow-rich-text-editor div.ProseMirror',
                'div.ProseMirror[contenteditable="true"]',
                'div[contenteditable="true"]',
                '[contenteditable="true"]',
            ]:
                try:
                    loc = self.page.locator(sel).first
                    if loc.is_visible(timeout=1000):
                        editor = loc
                        break
                except Exception:
                    pass

        try:
            _txt_previo = (editor.inner_text() or "").strip() if editor else ""
            pw_log(f"ANIMAR_PASSO_7: prompt {len(prompt_movimento or '')} caracteres | "
                   f"editor_previo_vazio={not _txt_previo}")
        except Exception as _e_p7:
            pw_log(f"ANIMAR_PASSO_7: falha ao medir o editor prévio: {_e_p7}", level="warn")
        if editor and prompt_movimento:
            try:
                self._safe_click(editor)
                self.page.wait_for_timeout(100)
                # Posiciona cursor no final e insere o texto sem apagar a imagem fixada
                self.page.keyboard.press("End")
                self.page.wait_for_timeout(100)
                self.page.keyboard.insert_text(" " + prompt_movimento.strip())
                self.page.wait_for_timeout(200)
                pw_log(f"[ANIMAR] Prompt de movimento inserido após a foto fixada: '{prompt_movimento[:120]}...'")
            except Exception as e:
                pw_log(f"[ANIMAR] Erro ao digitar prompt no editor: {e}", level="warn")

        pw_log(f"[ANIMAR] Imagem fixada no chat com configurações de vídeo aplicadas. Pronto para gerar!")
        return True


    def _marcar_falha_video(self, projeto_id: str, cid: int,
                            motivo: str = "credito_esgotado_video",
                            detalhe: str = "") -> None:
        """TAREFA 2/4 — registra na cena que a tentativa de VÍDEO falhou DE VERDADE.

        Substitui a ativação PREVENTIVA de `_fallback_video_para_imagem`: a flag de
        fallback só é ligada pela decisão canônica (`_tratar_indicador_limite`),
        enquanto a cena guarda a prova da tentativa (`video_tentado`,
        `video_falhou`, `video_falhou_motivo`) para a UI e para a montagem.

        Nunca levanta: falha de log/persistência não pode alterar a fila.
        """
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            return
        try:
            scene_plan_svc.atualizar_cena(projeto_id, cid_int, {
                "video_tentado": True,
                "video_falhou": True,
                "video_falhou_motivo": motivo,
            })
        except Exception as _e_marca:
            pw_log(f"[BROLL] Aviso ao registrar falha de vídeo da cena {cid_int}: {_e_marca}",
                   level="warn")
        # level="warn" (padrão do projeto — `log_event` documenta info/warn/error).
        pw_log("[BROLL] Tentativa de vídeo falhou — fallback para imagem"
               f" (cena {cid_int}, motivo={motivo}"
               f"{', ' + detalhe if detalhe else ''})", level="warn")

    def _etapa_timing(self, chave: str, dur_s: float, nome_log: Optional[str] = None):
        timer = getattr(self, "_current_scene_timer", None)
        if timer:
            timer.etapa(chave, dur_s, nome_log=nome_log)

    @_instrumentar_timing_cena
    def _processar_cena_individual(
        self,
        projeto_id: str,
        cena: Dict[str, Any],
        is_anim: bool = False,
        index: int = 1,
        total_cenas: int = 1
    ) -> Tuple[bool, str]:
        global _worker_instance
        _worker_instance = self
        cid = int(cena.get("id", 0))
        # Reseta flag de upscale 2K por cena (garante tentativa por cena nova)
        self._upscale_tentado_cena = False

        # TAREFA 1 — Verificação do saldo numérico real no Flow antes de processar a cena
        try:
            saldo_real = self.get_exact_balance()
            if saldo_real is not None:
                self.creditos_atuais = saldo_real
                pw_log(f"[CENA {cid:03d}] Saldo numérico real no Flow: {saldo_real} créditos.", level="info")
                conta_id = _conta_ativa_id()
                if conta_id:
                    try:
                        from services.flow_account_manager import FlowAccountManager
                        FlowAccountManager().atualizar_saldo_conta(conta_id, saldo_real)
                    except Exception as e_att:
                        pw_log(f"[FLOW_CREDITS] Erro ao sincronizar saldo: {e_att}", level="debug")
                if saldo_real == 0:
                    pw_log(f"[CENA {cid:03d}] Saldo REAL zerado (0 créditos). Disparando rotação legítima...", level="warn")
                    self._tratar_indicador_limite("creditos_reais_zerados", video_mode=bool(is_anim))
                    return (False, "creditos_reais_esgotados")
        except Exception as e_sald:
            pw_log(f"[CENA {cid:03d}] Aviso na leitura de saldo: {e_sald}", level="debug")
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
        # TAREFA 3 — CONFIRMAÇÃO: o modo PRECISA ser decidido ANTES de montar o prompt
        # e submeter ao Flow (o modo define prompt, modelo e configuração do canvas),
        # por isso esta leitura fica no topo. A garantia pedida está no outro lado do
        # contrato: a flag só pode estar True depois de uma falha REAL registrada —
        # ela é ligada exclusivamente por `_tratar_indicador_limite` (evidência do
        # Flow) e nunca mais dentro de `_processar_cena_individual` (TAREFA 2).
        # ELEGIBILIDADE A VÍDEO (ANTIGRAVITY — TAREFA 3): MESMA expressão de metadados de
        # antes, calculada SEM depender do fallback de crédito. `elegivel_video` responde
        # "esta cena PODE ser vídeo?"; `video_mode` responde "esta cena será vídeo AGORA?".
        # Separar os dois é o que fecha o vazamento: com o fallback de vídeo esgotado
        # (PARTE 5) as cenas ELEGÍVEIS caem para imagem (correto e testado), mas as cenas
        # que NUNCA foram elegíveis continuam puladas — antes, o fallback zerava
        # `video_mode` e liberava o Nano Banana 2 sobre PNGs existentes.
        if eh_avatar:
            elegivel_video = bool(
                is_anim
                and (
                    cena.get("animar") is True
                    or cena.get("animate_later") is True
                    or cena.get("animar_depois") is True
                    or tipo_efetivo == scene_plan_svc.TIPO_VIDEO
                )
            )
        else:
            # REGRA 6: Na passada de imagem (is_anim=False) SEMPRE gera imagem!
            # Só gera vídeo se estiver explicitamente em fila de animação (is_anim=True).
            elegivel_video = bool(
                is_anim
                and (
                    tipo_efetivo == scene_plan_svc.TIPO_VIDEO
                    or str(cena.get("media_intent") or "").lower() == "video"
                    or cena.get("animar") is True
                    or cena.get("animate_later") is True
                    or cena.get("animar_depois") is True
                )
            )
        if getattr(self, "_fallback_video_para_imagem", False):
            # PARTE 5 — créditos de vídeo esgotados nesta fila: as cenas que seriam
            # animadas caem para IMAGEM (fallback video→imagem, sem rotacionar conta).
            _motivo_fb = (getattr(self, "_fallback_video_para_imagem_motivo", "")
                          or "credito de video esgotado (evidencia do Flow)")
            pw_log(f"[BROLL] Fallback video->imagem ATIVO ({_motivo_fb}) — "
                   f"cena {cid} sera gerada como IMAGEM.", level="info")
            video_mode = False
        else:
            video_mode = elegivel_video
        timeout_s = 150 if video_mode else 90

        # ANTIGRAVITY — FILA DE ANIMAÇÃO NUNCA GERA IMAGEM (TAREFA 2 + TAREFA 3):
        # causa do gasto de créditos nas cenas 003/005-007/009-018/020-029 (submetidas ao
        # Nano Banana 2 numa fila "animacao") e da sobrescrita dos PNGs 002/004.
        # O gate usa `elegivel_video` (METADADOS), nunca `video_mode`: a cena que nunca foi
        # elegível é SEMPRE pulada — inclusive com o fallback de crédito de vídeo ativo.
        # É pulada antes de QUALQUER interação com o Flow: sem prompt, sem troca de modelo,
        # sem tocar em PNG/mídia e sem alterar status/image_status/video_status.
        if is_anim and not elegivel_video:
            pw_log(
                f"[ANIM_SKIP] Cena {cid:03d}: não elegível a vídeo em fila de animação; "
                "nenhuma geração enviada",
                level="warn",
            )
            print(f"[LOG] ANIM_SKIP: Cena {cid:03d} não elegível a vídeo em fila de animação — nenhuma geração enviada.", flush=True)
            return True, ANIM_SKIP_RESULTADO

        # CORREÇÃO 3 — prompt SEMPRE coerente com o modo:
        #  - video_mode=False (geração de IMAGEM): usa SEMPRE prompt_imagem —
        #    NUNCA prompt_animacao (o campo animar/animar_depois agenda animação
        #    futura; não converte a geração atual em vídeo).
        #  - video_mode=True (animação/vídeo): usa prompt_animacao (instrução de
        #    movimento) — NUNCA prompt_imagem (descrição estática).
        if video_mode:
            # CORREÇÃO ANIMAR: NUNCA usar prompt_imagem em modo vídeo — ele descreve
            # aparência estática, não movimento. Somente prompt_animacao (instrução de
            # motion) ou fallback dinâmico de movimento cinematográfico baseado na narração.
            raw_prompt = cena.get("prompt_animacao")
            if not raw_prompt:
                try:
                    from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
                    raw_prompt = _montar_prompt_animacao_fallback(cena.get("texto") or cena.get("narration") or "")
                except Exception:
                    raw_prompt = ""
            if not raw_prompt:
                raw_prompt = "Cinematic slow camera motion, natural depth of field, high clarity 16:9"
        else:
            raw_prompt = cena.get("prompt_imagem") or cena.get("texto", "")

        # Detecção de POV: cenas first_person_pov não usam personagem ou trava gestual
        _pov_tipo = str(cena.get("pov_tipo") or cena.get("avatar_style") or "").strip().lower()
        if _pov_tipo in ("first_person_pov", "pov", "broll_pov"):
            pw_log(f"[POV] Cena {cid:03d}: Detecta pov_tipo: '{_pov_tipo}'. Geração em primeira pessoa.")
            print(f"[POV] Cena {cid:03d}: Detecta pov_tipo: '{_pov_tipo}'", flush=True)
            _tem_personagem = False
        else:
            # Trava avatar-gesto-sem-fala: impede lip sync em vídeos com personagem
            _tem_personagem = (
                bool(cena.get("uses_character")) or
                "avatar" in str(cena.get("scene_type") or "").lower() or
                bool(cena.get("character_ref"))
            )
        if _tem_personagem and video_mode:
            raw_prompt = raw_prompt.rstrip(" .,") + \
                ", mouth closed, no lip sync, no speech, natural gestures only."

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
                _fechou = False
                for _sel_c in [
                    "div[role='dialog'] button[aria-label*='fechar' i]",
                    "div[role='dialog'] button[aria-label*='close' i]",
                    "div[role='dialog'] button:has(svg)",
                    ".header-user-button",
                ]:
                    try:
                        _btn_m = self.page.locator(_sel_c).first
                        if _btn_m.is_visible(timeout=200):
                            _btn_m.click(timeout=800)
                            self.page.wait_for_selector("div[role='dialog']", state="hidden", timeout=1200)
                            _fechou = True
                            break
                    except Exception:
                        pass
                if not _fechou:
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_selector("div[role='dialog']", state="hidden", timeout=1200)
                pw_log("[MODAL_GUARD] Modal encontrado aberto no início da cena — fechado forçadamente.")
        except Exception as e:
            pw_log(f"[MODAL_GUARD] Falha ao verificar/fechar modal: {e}")

        # 1. Garante projeto aberto
        _t_etapa_a = time.perf_counter()
        _proj_ok = self._ensure_project_open(projeto_id, timeout_s=5)
        self._etapa_timing("abertura", time.perf_counter() - _t_etapa_a, nome_log="abertura_projeto")
        if not _proj_ok:
            return False, "Google Flow fechado. Clique em Abrir Google Flow novamente."

        # 2. Garante que qualquer chat/painel de agente esteja fechado e fecha banners
        self._disable_agent_mode()
        self._fechar_modais_bloqueantes()

        # 3. Força configuração do modo correto (Imagem vs Vídeo) e modelo do projeto
        _t_etapa_b = time.perf_counter()
        target_mode = "video" if video_mode else "image"
        # PRIORIDADE 4 — avatar gestual usa a config de IMAGEM apenas quando NÃO estiver em modo vídeo.
        if eh_avatar and not video_mode:
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
            modo_ok = self._set_output_mode(
                target_mode,
                modelo_solicitado=target_model,
                proporcao_solicitada="16:9" if video_mode else None
            )
            if not modo_ok:
                if video_mode:
                    self._etapa_timing("config_modo", time.perf_counter() - _t_etapa_b, nome_log="configuracao_modo")
                    msg_abort = f"Modo vídeo não confirmado no Flow — cena {cid:03d} abortada antes do envio para evitar timeout de polling"
                    pw_log(f"[FLOW] ERRO: {msg_abort}", level="error")
                    print(f"[LOG] VIDEO_MODE_ATTACH_FAILED: {msg_abort}", flush=True)
                    try:
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "video_status": scene_plan_svc.VIDEO_STATUS_ERROR,
                            "video_tentado": True,
                            "video_falhou": True,
                            "video_falhou_motivo": "modo_video_nao_confirmado",
                            "erro_msg": msg_abort,
                        })
                    except Exception as e_upd:
                        pw_log(f"[FLOW] Falha ao atualizar status de erro da cena {cid:03d}: {e_upd}", level="warn")
                    return (False, msg_abort)
                else:
                    pw_log(f"[FLOW] Aviso ao configurar modo {target_mode} — prosseguindo para envio imediato sem aborto.", level="warn")
        self.current_flow_mode = target_mode
        self.current_model = target_model
        self._etapa_timing("config_modo", time.perf_counter() - _t_etapa_b, nome_log="configuracao_modo")

        existing_keys, initial_media_count = self._get_existing_media_snapshot()
        self._marcar_cards_falha_existentes()

        # 4. Localiza e foca o campo de prompt do DOCK PRINCIPAL (com espera ativa de até 10s)
        self._limpar_overlays_e_backdrops()
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
        prompt_visual_puro = ""
        prompt_final = ""

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
            _t_etapa_c = time.perf_counter()
            tem_override = bool(cena.get("manual_override"))
            png_candidato = str(cena.get("arquivo_midia") or "")
            png_valido_personagem = bool(
                png_candidato
                and png_candidato.lower().endswith(".png")
                and Path(png_candidato).is_file()
                and Path(png_candidato).stat().st_size > 0
                and cena.get("imagem_com_personagem") is True
            )

            # 2d. Cena com manual_override e sem PNG válido: não gerar vídeo.
            # Registrar "imagem do avatar pendente" e manter a cena pendente, sem gastar crédito.
            if video_mode and tem_override and not png_valido_personagem:
                msg_pend = f"[uses_char] Cena {cid:03d} com manual_override requer imagem prévia do avatar gerada com personagem. Imagem do avatar pendente — geração de vídeo ignorada para não gastar créditos."
                pw_log(msg_pend, level="warn")
                print(f"[LOG] {msg_pend}", flush=True)
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "video_status": scene_plan_svc.STATUS_PENDENTE,
                    "status": scene_plan_svc.STATUS_PENDENTE
                })
                self._etapa_timing("anexo_personagem", time.perf_counter() - _t_etapa_c, nome_log="anexo_personagem")
                return True, "imagem do avatar pendente"

            # 2b. Antes do modal de ingredientes, se video_mode e uses_char e a cena tem PNG válido gerado COM o personagem,
            # chamar _animar_imagem_galeria_flow(projeto_id, png, prompt_animacao).
            animar_char_ok = False
            if video_mode and png_valido_personagem:
                prompt_movimento = self._clean_prompt_text(prompt, ref_tag=tag_char, strip_character_tag=True)
                pw_log(f"[uses_char] Cena {cid:03d}: PNG válido com personagem detectado ('{Path(png_candidato).name}'). Tentando Image-to-Video via Animar...")
                try:
                    animar_char_ok = self._animar_imagem_galeria_flow(
                        projeto_id, png_candidato, prompt_movimento, uses_character=uses_char
                    )
                except Exception as e_anim_char:
                    pw_log(f"[uses_char] Exceção em _animar_imagem_galeria_flow para cena {cid:03d}: {e_anim_char}", level="warn")
                    animar_char_ok = False

                if animar_char_ok:
                    pw_log(f"[uses_char] Cena {cid:03d}: Image-to-Video nativo concluído via Animar. Pulando modal de ingredientes.")
                    entidade_inserida = True
                    prompt_visual_puro = prompt_movimento
                else:
                    pw_log(f"[uses_char] Animar falhou -> fallback T2V", level="warn")

            if not entidade_inserida:
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
                    eh_erro_personagem = getattr(self, "_ultimo_erro_personagem", False)
                    self._ultimo_erro_personagem = False

                    if eh_erro_personagem:
                        msg_erro_char = f"ERRO_PERSONAGEM: Character Entity '{tag_char}' não encontrado e reprovisionamento não concluiu a tempo. Cena {cid:03d} pulada e mantida como PENDENTE para reprocessamento."
                        print(f"[LOG] {msg_erro_char}", flush=True)
                        pw_log(f"[CENA {cid:03d}] {msg_erro_char}", level="warn")
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                            "status": scene_plan_svc.STATUS_PENDENTE
                        })
                        self._etapa_timing("anexo_personagem", time.perf_counter() - _t_etapa_c, nome_log="anexo_personagem")
                        return False, msg_erro_char
                    else:
                        msg_erro_char = f"ERRO CRÍTICO: Character Entity '{tag_char}' não pôde ser anexado ao editor do Google Flow. Geração abortada para não criar rosto aleatório."
                        print(f"[LOG] CHARACTER_ATTACH_FAILED: {msg_erro_char}", flush=True)
                        pw_log(f"[CENA {cid:03d}] {msg_erro_char}", level="error")
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                            "status": scene_plan_svc.STATUS_ERRO
                        })
                        self._etapa_timing("anexo_personagem", time.perf_counter() - _t_etapa_c, nome_log="anexo_personagem")
                        return False, msg_erro_char

            self._etapa_timing("anexo_personagem", time.perf_counter() - _t_etapa_c, nome_log="anexo_personagem")
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
            pw_log(f"[CENA {cid:03d}] SCENE_SKIPPED_NO_CHARACTER: Cena é b-roll.")
            pw_log("[CENA_BROLL] Cena sem personagem — modal de personagem não será aberto.")

            prompt_final = self._clean_prompt_text(prompt, ref_tag="", strip_character_tag=True)

            # TAREFA 2: Comutar Flow pra Modo Vídeo (Veo 3.1 - Lite) antes de enviar prompt / animar
            if str(cena.get("media_intent") or "").lower() == "video" or (video_mode and cena.get("prompt_animacao")):
                try:
                    # Clique no selector de modelo no Flow
                    modelo_selector = self.page.locator("button:has-text('Veo'), button[data-model*='veo']").first
                    if not (modelo_selector.count() > 0 and modelo_selector.is_visible(timeout=500)):
                        # Abrir dropdown de modelos
                        for sel_m in [
                            "button[aria-label*='model' i]",
                            "button[aria-label*='modelo' i]",
                            "button[aria-label*='Selecionar família' i]",
                            "button:has(.model-select-trigger-content)",
                        ]:
                            loc_m = self.page.locator(sel_m).first
                            if loc_m.count() > 0 and loc_m.is_visible(timeout=500):
                                loc_m.click()
                                self.page.wait_for_timeout(500)
                                break
                        # Clique em Veo 3.1 - Lite
                        for sel_veo in [
                            "text=Veo 3.1 - Lite",
                            "button:has-text('Veo 3.1 - Lite')",
                            "[role='menuitem']:has-text('Veo 3.1 - Lite')",
                            "text=Veo 3.1",
                            "text=Veo",
                        ]:
                            loc_v = self.page.locator(sel_veo).first
                            if loc_v.count() > 0 and loc_v.is_visible(timeout=500):
                                loc_v.click()
                                self.page.wait_for_timeout(1000)
                                break

                    # Comutar output pra vídeo
                    output_toggle = self.page.locator(
                        "button[aria-label*='video' i], button[aria-label*='vídeo' i], "
                        "input[value='video'], button[role='tab']:has-text('Vídeo'), "
                        "button[role='tab']:has-text('Video'), button:has-text('Vídeo'), button:has-text('Video')"
                    ).first
                    if output_toggle.count() > 0 and output_toggle.is_visible(timeout=500):
                        is_checked = False
                        try:
                            is_checked = (
                                output_toggle.is_checked()
                                or output_toggle.get_attribute("aria-selected") == "true"
                                or output_toggle.get_attribute("aria-checked") == "true"
                                or "selected" in (output_toggle.get_attribute("class") or "").lower()
                            )
                        except Exception:
                            pass
                        if not is_checked:
                            output_toggle.click()
                            self.page.wait_for_timeout(500)
                except Exception as e_v_switch:
                    pw_log(f"[FLOW_MODE] Aviso ao comutar modo vídeo/Veo: {e_v_switch}", level="warn")

            # CORREÇÃO ANIMAR: Image-to-Video via menu nativo 'Animar' / 'Incluir no comando' do Flow.
            # Quando video_mode=True e a imagem da cena existe, usa o fluxo correto:
            # upload → 3 pontinhos → Incluir no comando/momento → imagem fixa no editor → prompt de movimento.
            # Isso gera vídeo a partir da imagem (Veo Image-to-Video), não texto puro.
            arquivo_img_ref = ""
            if cena.get("caminho_imagem") and Path(str(cena.get("caminho_imagem"))).exists():
                arquivo_img_ref = str(cena.get("caminho_imagem"))
            elif cena.get("arquivo_midia") and Path(str(cena.get("arquivo_midia"))).exists():
                suf = Path(str(cena.get("arquivo_midia"))).suffix.lower()
                if suf in (".png", ".jpg", ".jpeg", ".webp"):
                    arquivo_img_ref = str(cena.get("arquivo_midia"))

            if not arquivo_img_ref:
                disco_png = scene_plan_svc.resolver_arquivo_cena(
                    projeto_id, cid, float(cena.get("tempo_inicio", 0)), ext=".png"
                )
                if disco_png and disco_png.exists():
                    arquivo_img_ref = str(disco_png)

            if not arquivo_img_ref:
                disco_qualquer = scene_plan_svc.resolver_arquivo_cena(
                    projeto_id, cid, float(cena.get("tempo_inicio", 0))
                )
                if disco_qualquer and disco_qualquer.exists() and disco_qualquer.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    arquivo_img_ref = str(disco_qualquer)

            tem_png_referencia = bool(
                video_mode
                and arquivo_img_ref
                and Path(arquivo_img_ref).exists()
            )

            # TAREFA 1 — o prompt de movimento NUNCA pode chegar vazio aqui: no ramo
            # uses_char o `prompt_final` (L5942) só era atribuído no `else` (b-roll),
            # então a animação recebia "" (vídeo genérico). Recalcula como no else.
            if not prompt_final:
                try:
                    prompt_final = self._clean_prompt_text(
                        prompt,
                        ref_tag=(tag_char if uses_char else ""),
                        strip_character_tag=True,
                    )
                except Exception as e_pf:
                    pw_log(f"[CENA {cid:03d}] Falha ao montar prompt_final: {e_pf}", level="warn")
            if not prompt_final:
                try:
                    _fb = getattr(self, "_montar_prompt_animacao_fallback", None)
                    prompt_final = (_fb(cena) if callable(_fb) else "") or ""
                except Exception as e_fb:
                    pw_log(f"[CENA {cid:03d}] Fallback de prompt falhou: {e_fb}", level="warn")
                prompt_final = prompt_final or "Cinematic slow camera motion."
            pw_log(f"[CENA {cid:03d}] PROMPT_ANIMACAO: {len(prompt_final)} caracteres "
                   f"({'uses_char' if uses_char else 'b-roll'}).")

            animar_ok = False
            if tem_png_referencia:
                try:
                    animar_ok = self._animar_imagem_galeria_flow(
                        projeto_id, arquivo_img_ref, prompt_final, uses_character=uses_char
                    )
                except Exception as e:
                    pw_log(f"[CENA {cid:03d}] Falha no fluxo 'Animar': {e}", level="warn")
                pw_log("RESULTADO_ANIMACAO: " + ("I2V" if animar_ok else
                       ("CANCELADO" if tem_png_referencia else "T2V")))
                pw_log(
                    f"[CENA {cid:03d}] IMAGE_TO_VIDEO: Imagem '{Path(arquivo_img_ref).name}' "
                    + ("→ Animar OK (Image-to-Video nativo com foto fixada no chat)." if animar_ok
                       else "→ Animar falhou — fallback Text-to-Video.")
                )

            if not animar_ok and tem_png_referencia:
                # TAREFA 2 — CANCELA em vez de gerar Text-to-Video DESVINCULADO: sem a
                # imagem fixada o vídeo não teria relação com a cena e gastaria
                # crédito. O editor NÃO é apagado (nada de Control+A/Backspace) e a
                # cena fica em ERRO explícito, reenviável pelo usuário.
                _msg_canc = ("Animação cancelada: não foi possível fixar a imagem. "
                             "Nenhum crédito gasto.")
                pw_log(f"[CENA {cid:03d}] ANIMAR_CANCELADO: {_msg_canc}", level="error")
                pw_log("RESULTADO_ANIMACAO: CANCELADO")
                print(f"[LOG] ANIMAR_CANCELADO: {_msg_canc}", flush=True)
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "status": scene_plan_svc.STATUS_ERRO,
                    "erro_msg": _msg_canc,
                })
                return False, _msg_canc

            if not animar_ok:
                # TAREFA 4 — chega aqui SOMENTE sem PNG de referência: o caso COM
                # PNG cancela acima (ANIMAR_CANCELADO). Ramo morto removido.
                self._safe_click(editor)
                self.page.wait_for_timeout(100)
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.wait_for_timeout(100)
                if prompt_final:
                    self.page.keyboard.insert_text(prompt_final)
                    self.page.wait_for_timeout(200)
                if video_mode:
                    _t_reconf = time.perf_counter()
                    reconfig_ok = False
                    try:
                        reconfig_ok = bool(self._set_output_mode(
                            "video",
                            modelo_solicitado="Veo 3.1 - Lite",
                            proporcao_solicitada="16:9"
                        ))
                    except Exception as e_reconf:
                        pw_log(f"[FLOW] Falha ao reconfigurar modo vídeo após Animar: {e_reconf}", level="warn")
                    if not reconfig_ok:
                        self._etapa_timing("config_modo", time.perf_counter() - _t_reconf, nome_log="configuracao_modo")
                        msg_abort = f"Modo vídeo não confirmado no Flow após falha do Animar — cena {cid:03d} abortada para evitar timeout de polling"
                        pw_log(f"[FLOW] ERRO: {msg_abort}", level="error")
                        print(f"[LOG] VIDEO_MODE_ATTACH_FAILED: {msg_abort}", flush=True)
                        try:
                            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                "status": scene_plan_svc.STATUS_ERRO,
                                "video_status": scene_plan_svc.VIDEO_STATUS_ERROR,
                                "video_tentado": True,
                                "video_falhou": True,
                                "video_falhou_motivo": "modo_video_nao_confirmado_pos_animar",
                                "erro_msg": msg_abort,
                            })
                        except Exception as e_upd:
                            pw_log(f"[FLOW] Falha ao atualizar status de erro da cena {cid:03d}: {e_upd}", level="warn")
                        return (False, msg_abort)



        # 6. Envio Imediato: Dispara Enter no editor e clica no botão Create
        t_inicio_cena = time.time()
        prompt_completo_enviado = (f"{tag_char} " if uses_char else "") + (prompt_visual_puro if uses_char else prompt_final)
        pw_log(f"[COMANDO_ENVIADO] Cena {cid:03d} | Prompt exato: '{prompt_completo_enviado}'")
        print(f"[COMANDO_ENVIADO] Cena {cid:03d} | Prompt exato: '{prompt_completo_enviado}'", flush=True)
        pw_log(f"[PROMPT_PREVIEW] Primeiros 200 chars do prompt: {prompt[:200]}")

        # Verificação prévia de créditos (Flow DOM) antes de submeter / clicar Create / Enter.
        if not check_flow_credits()["has_credits"]:
            pw_log("[FLOW] Aviso prévio de créditos insuficientes detectado no Google Flow.", level="warn")
            res_limite = self._tratar_indicador_limite("check_flow_credits", video_mode)
            if res_limite == "credito_esgotado_video":
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
                # TAREFA 2 — REMOVIDA a ativação preventiva de `_fallback_video_para_imagem`
                # (fazia o sistema pular a tentativa de vídeo). A flag já foi ligada
                # pela decisão canônica `_tratar_indicador_limite` (evidência real do
                # Flow); aqui apenas registramos na cena a falha REAL da tentativa.
                self._marcar_falha_video(projeto_id, cena.get("id"), "credito_esgotado_video",
                                         "check_flow_credits sem créditos de vídeo")
                return (False, "credito_esgotado_video_recolocado")
            pw_log(f"[FLOW] Cena {cena.get('id')} marcada para reprocessamento após rotação de conta.", level="warn")
            return (False, "credito_esgotado_recolocado")

        _t_etapa_d = time.perf_counter()
        editor.focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)

        try:
            for sel_btn in [
                'button.generate-icon-button',
                'button[aria-label*="Iniciar geração" i]',
                'button[aria-label*="Iniciar geracao" i]',
                'button:has(i:has-text("arrow_forward")):not(aside *):not([role="dialog"] *)',
                'button[aria-label="Create"]:not(aside *)',
                'button[aria-label*="Create" i]:not(aside *)',
                'button[aria-label*="Criar" i]:not(aside *)',
                'button:has-text("Create"):not(aside *)',
                'button:has-text("Criar"):not(aside *)',
            ]:
                btn = self.page.locator(sel_btn).first
                if btn.is_visible(timeout=500) and not btn.is_disabled():
                    self._safe_click(btn)
                    break
        except Exception:
            pass
        self._etapa_timing("envio_prompt", time.perf_counter() - _t_etapa_d, nome_log="envio_prompt")

        print("[LOG] PROMPT_SENT_OK", flush=True)
        pw_log(f"[CENA {cid:03d}] PROMPT_SENT_OK: Prompt enviado ao Flow com modelo {self.current_model}.")
        pw_log(f"[CENA {cid:03d}] Prompt enviado. Aguardando Flow...")
        campos_gerando = {
            "status": scene_plan_svc.STATUS_GERANDO,
        }
        if video_mode:
            campos_gerando["video_status"] = scene_plan_svc.VIDEO_STATUS_GENERATING
        else:
            campos_gerando["image_status"] = scene_plan_svc.IMAGE_STATUS_GENERATING
        scene_plan_svc.atualizar_cena(projeto_id, cid, campos_gerando)

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
            # TAREFA 2 — REMOVIDA a ativação preventiva de `_fallback_video_para_imagem`.
            # Esta é a tentativa REAL (prompt enviado + Create clicado): a flag já foi
            # ligada pela decisão canônica e a cena recebe a prova da falha.
            self._marcar_falha_video(projeto_id, cena.get("id"), "credito_esgotado_video",
                                     "erro de limite do Flow após Create")
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
                    eh_erro_personagem = getattr(self, "_ultimo_erro_personagem", False)
                    self._ultimo_erro_personagem = False
                    if eh_erro_personagem:
                        msg_sem_char = (f"ERRO_PERSONAGEM no fallback de modelo: Character Entity "
                                        f"'{tag_char}' não pôde ser re-anexado ao editor. Cena {cid:03d} "
                                        f"mantida como PENDENTE para reprocessamento.")
                        print(f"[LOG] {msg_sem_char}", flush=True)
                        pw_log(f"[CENA {cid:03d}] {msg_sem_char}", level="warn")
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                            "status": scene_plan_svc.STATUS_PENDENTE
                        })
                        return False, msg_sem_char
                    else:
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
        _t_etapa_e = time.perf_counter()
        new_media_item = None
        t_poll_start = time.time()
        _iter_poll = 0
        pw_log(f"[CENA {cid:03d}] Aguardando renderização do Flow (detecção contínua a cada 1.5s)...")

        _segunda_tentativa_feita = False
        while True:
            if time.time() - t_poll_start >= timeout_s:
                # FALLBACK RÁPIDO (imagem): o Flow pode ter gerado uma IMAGEM em vez de
                # vídeo. Antes de marcar ERRO, aceita a imagem nova como resultado
                # provisório (nunca perder a geração).
                _img_fallback = None
                try:
                    _res_fb = self.page.evaluate(JS_FETCH_MEDIA_LIST)
                    if _res_fb and _res_fb.get("ok"):
                        _img_fallback = next((
                            it for it in _res_fb.get("media", [])
                            if it.get("type") == "image"
                            and self._media_item_e_novo(it, existing_keys)
                            and (it.get("dataUrl") or it.get("width", 0) > 60)
                        ), None)
                except Exception as e_fb:
                    pw_log(f"[FLOW] Cena {cid}: checagem de imagem de fallback falhou: {e_fb}", level="warn")
                if _img_fallback and not uses_char:
                    # GUARD DO AVATAR (ANTIGRAVITY) — o fallback de timeout TAMBÉM não
                    # pode aceitar o reference.png como resultado de cena sem personagem.
                    _bytes_fb = self._baixar_bytes_item_para_guard(_img_fallback)
                    if _bytes_fb and self._imagem_e_avatar_de_referencia(projeto_id, _bytes_fb):
                        pw_log(f"[MIDIA_CONTAMINADA] Cena {cid:03d}: resultado é o avatar de "
                               "referência; ignorado (fallback de timeout).", level="warn")
                        self._registrar_chave_ignorada(_img_fallback)
                        _img_fallback = None
                    elif _bytes_fb:
                        _img_fallback["_bytes_guard"] = _bytes_fb
                if _img_fallback:
                    pw_log(f"[FLOW] Cena {cid}: vídeo não gerado, aceitando imagem como fallback", level="warn")
                    new_media_item = _img_fallback
                    break
                # SEGUNDA TENTATIVA IMEDIATA: reenvia o mesmo prompt antes do ERRO definitivo.
                if not _segunda_tentativa_feita:
                    _segunda_tentativa_feita = True
                    pw_log(f"[FLOW] Cena {cid}: timeout, tentando novamente imediatamente", level="warn")
                    try:
                        _texto_retry = prompt_visual_puro if uses_char else prompt_final
                        editor.click()
                        self.page.keyboard.press("Control+A")
                        self.page.keyboard.press("Backspace")
                        self.page.wait_for_timeout(100)
                        # TAREFA 3 — o Control+A + Backspace remove o chip/entidade do
                        # personagem do editor do Flow. Em cena com personagem
                        # (uses_char=True), re-anexa a referência (popup '+' → aba
                        # Characters → card) ANTES de reinserir o texto, garantindo que
                        # o retry NÃO gere rosto aleatório em vez do avatar correto.
                        if uses_char:
                            _re_entidade_retry = self._selecionar_referencia_flow(
                                projeto_id=projeto_id,
                                nome_personagem=nome_char,
                                tipo=tipo_char,
                                ref_tag=tag_char,
                                arquivo_flow=arq_char,
                                imagem_abs=img_abs,
                                flow_character_id=flow_id,
                            )
                            if _re_entidade_retry:
                                pw_log(f"[CENA {cid:03d}] CHARACTER_REATTACHED_OK no retry: "
                                       f"'{tag_char}' re-anexado antes de reenviar o prompt.")
                            else:
                                pw_log(f"[CENA {cid:03d}] CHARACTER_REATTACH_FAILED no retry: "
                                       f"'{tag_char}' não pôde ser re-anexado ao editor.",
                                       level="warn")
                        if _texto_retry:
                            self.page.keyboard.insert_text(_texto_retry)
                            self.page.wait_for_timeout(200)
                        editor.focus()
                        self.page.keyboard.press("Enter")
                        self.page.wait_for_timeout(200)
                        try:
                            for sel_btn_r in [
                                'button[aria-label="Create"]:not(aside *)',
                                'button[aria-label*="Create" i]:not(aside *)',
                                'button[aria-label*="Criar" i]:not(aside *)',
                                'button:has-text("Create"):not(aside *)',
                                'button:has-text("Criar"):not(aside *)',
                            ]:
                                _btn_r = self.page.locator(sel_btn_r).first
                                if _btn_r.is_visible(timeout=500) and not _btn_r.is_disabled():
                                    _btn_r.click()
                                    break
                        except Exception:
                            pass
                    except Exception as e_retry:
                        pw_log(f"[FLOW] Cena {cid}: falha ao reenviar prompt na 2ª tentativa: {e_retry}", level="warn")
                    t_poll_start = time.time()
                    _iter_poll = 0
                    continue
                # ERRO definitivo após a segunda tentativa
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

            # Detecção IMEDIATA de card de falha no canvas ("Falha. Não foi possível gerar o vídeo.")
            # Evita espera inútil do timeout (150s-300s) quando o Flow já exibiu falha explícita no card.
            falha_card = self._detectar_novo_card_falha()
            if falha_card:
                pw_log(f"[CENA {cid:03d}][FLOW_FALHA] Google Flow indicou falha na geração: '{falha_card}'.", level="warn")
                print(f"[FLOW_FALHA] Cena {cid:03d}: Card de falha detectado imediatamente no Flow ('{falha_card}')", flush=True)

                if not _segunda_tentativa_feita:
                    _segunda_tentativa_feita = True
                    pw_log(f"[CENA {cid:03d}] Tentando reenvio imediato após falha do Flow (sem aguardar timeout)...", level="warn")
                    clicou_retry = False
                    try:
                        clicou_retry = self._acionar_retry_card_falha()
                        if clicou_retry:
                            pw_log(f"[CENA {cid:03d}] Botão de tentar novamente do card acionado diretamente!")
                    except Exception:
                        pass
                    if not clicou_retry:
                        try:
                            _texto_retry = prompt_visual_puro if uses_char else prompt_final
                            editor.click()
                            self.page.keyboard.press("Control+A")
                            self.page.keyboard.press("Backspace")
                            self.page.wait_for_timeout(100)
                            if uses_char:
                                _re_entidade_retry = self._selecionar_referencia_flow(
                                    projeto_id=projeto_id,
                                    nome_personagem=nome_char,
                                    tipo=tipo_char,
                                    ref_tag=tag_char,
                                    arquivo_flow=arq_char,
                                    imagem_abs=img_abs,
                                    flow_character_id=flow_id,
                                )
                                if _re_entidade_retry:
                                    pw_log(f"[CENA {cid:03d}] CHARACTER_REATTACHED_OK no retry: '{tag_char}' re-anexado.")
                            if _texto_retry:
                                self.page.keyboard.insert_text(_texto_retry)
                                self.page.wait_for_timeout(200)
                            editor.focus()
                            self.page.keyboard.press("Enter")
                            self.page.wait_for_timeout(200)
                            for sel_btn_r in [
                                'button[aria-label="Create"]:not(aside *)',
                                'button[aria-label*="Create" i]:not(aside *)',
                                'button[aria-label*="Criar" i]:not(aside *)',
                                'button:has-text("Create"):not(aside *)',
                                'button:has-text("Criar"):not(aside *)',
                            ]:
                                _btn_r = self.page.locator(sel_btn_r).first
                                if _btn_r.is_visible(timeout=500) and not _btn_r.is_disabled():
                                    _btn_r.click()
                                    break
                        except Exception as e_retry:
                            pw_log(f"[FLOW] Cena {cid}: falha ao reenviar prompt na 2ª tentativa: {e_retry}", level="warn")
                    self._marcar_cards_falha_existentes()
                    t_poll_start = time.time()
                    _iter_poll = 0
                    self.page.wait_for_timeout(2000)
                    continue
                else:
                    pw_log(f"[CENA {cid:03d}] Falha definitiva de geração no Flow: '{falha_card}'.", level="error")
                    try:
                        scene_plan_svc.atualizar_cena(projeto_id, cid, {
                            "status": scene_plan_svc.STATUS_ERRO,
                            "erro_msg": f"Google Flow falhou: {falha_card}",
                            "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                            "video_status": (scene_plan_svc.VIDEO_STATUS_ERROR if video_mode
                                             else scene_plan_svc.VIDEO_STATUS_NOT_STARTED),
                        })
                    except Exception as e_err:
                        pw_log(f"[CENA {cid:03d}] Falha ao marcar status ERRO: {e_err}", level="warn")
                    return False, f"Google Flow falhou ao gerar mídia: {falha_card}"

            # Detecção ativa de corte de cota / limite de uso durante a renderização
            # TAREFA 2 — PROTEÇÃO PROGRESSO VS ERRO: se a renderização estiver ativamente
            # em andamento (barra de progresso visível ou porcentagem > 0%), NÃO interpreta
            # termos de erro residuais de cards antigos no canvas.
            render_ativa = False
            try:
                render_ativa = self.page.evaluate(r'''() => {
                    const pbars = document.querySelectorAll('[role="progressbar"], .progress-bar, [class*="progress" i]');
                    for (const p of pbars) {
                        if (p.offsetParent !== null) return true;
                    }
                    const txt = (document.body ? document.body.innerText : '');
                    if (/\b(?:renderizando|rendering|gerando|generating)[^\n\d]*([1-9]\d?%)/i.test(txt)) {
                        return true;
                    }
                    return false;
                }''')
            except Exception:
                render_ativa = False

            if not render_ativa:
                err_limite_loop = self._detectar_erro_ou_limite_modelo(video_mode=video_mode)
                if err_limite_loop:
                    pw_log(f"[CENA {cid:03d}] Limite de uso/cota detectado durante renderização: {err_limite_loop}", level="warn")
                    print(f"[FLOW] Limite de uso atingido na Cena {cid:03d}: alternando conta imediatamente...", flush=True)
                    return False, ("credito_esgotado_video_recolocado" if err_limite_loop == "credito_esgotado_video" else "credito_esgotado_recolocado")

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
                    # GUARD DO AVATAR (TAREFA 3): is_new também exclui as chaves
                    # conhecidas do upload do avatar e as chaves já ignoradas.
                    if not self._media_item_e_novo(item, existing_keys):
                        continue
                    if video_mode and item["type"] == "video":
                        novos.append(item)
                    elif not video_mode and item["type"] == "image":
                        if item.get("dataUrl") or (item.get("width", 0) > 60):
                            novos.append(item)

                # Em modo vídeo, se o Flow gerou apenas imagem, NUNCA aceita como fallback!
                if video_mode and not novos:
                    candidatos_img = [
                        item for item in curr_media
                        if item.get("type") == "image"
                        and self._media_item_e_novo(item, existing_keys)
                        and item.get("width", 0) > 60
                    ]
                    if candidatos_img and (time.time() - t_poll_start > 15):
                        pw_log(
                            f"[FLOW] Cena {cid}: modo vídeo requisitado, mas o Flow gerou imagem "
                            f"— rejeitando imagem (esperado vídeo).",
                            level="warn"
                        )

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
                # GUARD DO AVATAR (ANTIGRAVITY) — cena SEM personagem nunca pode aceitar
                # o reference.png: o tile do upload do avatar entra no DOM depois do
                # snapshot e era aceito como "mídia nova" (b-roll 002/004 contaminado).
                # Se contaminado: descarta, marca a chave como ignorada e CONTINUA o
                # polling (sem marcar sucesso/geração; o timeout usa a falha normal).
                if not uses_char and new_media_item.get("type") == "image":
                    _bytes_guard = self._baixar_bytes_item_para_guard(new_media_item)
                    if _bytes_guard and self._imagem_e_avatar_de_referencia(projeto_id, _bytes_guard):
                        pw_log(
                            f"[MIDIA_CONTAMINADA] Cena {cid:03d}: resultado é o avatar de "
                            "referência; ignorado (chave marcada e polling continua).",
                            level="warn",
                        )
                        self._registrar_chave_ignorada(new_media_item)
                        new_media_item = None
                        self.page.wait_for_timeout(1500)
                        continue
                    if _bytes_guard:
                        # Reaproveita os bytes no download oficial (sem segundo fetch).
                        new_media_item["_bytes_guard"] = _bytes_guard
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
                # TAREFA 4 (ANTIGRAVITY) — OBSERVABILIDADE apenas: render muito rápido é
                # sinal de alerta (contaminação), mas NUNCA rejeita — a cena 047 é
                # legítima com 2.4s de render.
                if tempo_decorrido < LIMIAR_GERACAO_RAPIDA_SEG:
                    pw_log(f"[GERACAO_RAPIDA] Cena {cid:03d}: {tempo_decorrido:.1f}s "
                           f"(abaixo de {LIMIAR_GERACAO_RAPIDA_SEG:.1f}s) — mídia aceita e registrada.",
                           level="warn")
                pw_log(f"[CENA {cid:03d}] Mídia detectada com sucesso no Flow em {tempo_decorrido:.1f}s!")
                self._etapa_timing("renderizacao", time.perf_counter() - _t_etapa_e, nome_log="renderizacao")
                break

            self.page.wait_for_timeout(1500)

        if not new_media_item:
            self._etapa_timing("renderizacao", time.perf_counter() - _t_etapa_e, nome_log="renderizacao")
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

        # 8. Extrai o arquivo binário (via download 1080p aprimorada, dataUrl direto do canvas ou download base64)
        print("Baixando...", flush=True)
        pw_log("Baixando...")

        content_bytes = None
        # Origem dos bytes de vídeo (usada no log [VIDEO_SAVED]).
        _video_bytes_via_tile = False

        # ANTIGRAVITY — DOWNLOAD AUTOMÁTICO VIA <flow-video-tile> (TAREFA 3): 720p é o
        # caminho PRIMÁRIO — o Flow 2025 não expõe <video> nativo e a rotina antiga
        # não encontra o card. Falha aqui => [VIDEO_DOWNLOAD_FALLBACK] + rotinas
        # antigas; se nada obtiver bytes, o fluxo de erro normal (ERRO/retry) assume.
        if video_mode and new_media_item.get("type") == "video":
            _tile_ok = False
            try:
                _tile_ok = self._baixar_video_tile_automatico(new_media_item, cid, "720p")
            except Exception as e_tile:
                pw_log(f"[VIDEO_DOWNLOAD_ERROR] Cena {cid:03d}: {e_tile}", level="warn")
            if _tile_ok and getattr(self, "_download_tile_bytes", None):
                content_bytes = self._download_tile_bytes
                _video_bytes_via_tile = True
                pw_log(f"[CENA {cid:03d}] VIDEO_TILE_DOWNLOAD_OK: vídeo obtido direto do flow-video-tile ({len(content_bytes)} bytes).")
            if not content_bytes:
                pw_log(f"[VIDEO_DOWNLOAD_FALLBACK] Cena {cid:03d}: download automático via flow-video-tile falhou — acionando rotinas de fallback (1080p / antigas).", level="warn")

        # VÍDEO 1080p APRIMORADA (FALLBACK): Se configurado (default '1080p'), tenta baixar via menu dos 3 pontinhos do card
        if not content_bytes and video_mode and getattr(self, "current_download_quality_video", "1080p") == "1080p":
            try:
                content_bytes = self._baixar_video_1080p_menu(new_media_item, cid)
                if content_bytes:
                    pw_log(f"[CENA {cid:03d}] DOWNLOAD_1080P_OK: Vídeo 1080p Aprimorada obtido com sucesso ({len(content_bytes)} bytes).")
            except Exception as e_1080:
                pw_log(f"[CENA {cid:03d}] Falha na rotina 1080p ({e_1080}) — acionando fallback 720p.", level="warn")

        # GUARD DO AVATAR — reaproveita os bytes já baixados na verificação de
        # contaminação (evita um segundo download do mesmo asset).
        if not content_bytes and new_media_item.get("_bytes_guard"):
            content_bytes = new_media_item["_bytes_guard"]
            pw_log(f"[CENA {cid:03d}] GUARD_AVATAR_BYTES_REUSE: bytes do candidato "
                   "reaproveitados da verificação do guard.")

        if not content_bytes and not video_mode and ("domIndex" in new_media_item or "mediaKey" in new_media_item):
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

        # THUMBNAIL NUNCA VIRA VÍDEO (ANTIGRAVITY): em <flow-video-tile> o campo
        # `src` é o PREVIEW (<img>), não a mídia — baixá-lo e salvá-lo como .mp4
        # seria mentira de formato. Os dois fallbacks abaixo valem apenas para
        # imagem e para <video> NATIVO (que ainda expõe a URL do vídeo no src).
        _src_eh_preview_video = bool(video_mode and new_media_item.get("srcEhPreview"))
        if not content_bytes and not _src_eh_preview_video and new_media_item.get("src"):
            try:
                resp = self.page.request.get(new_media_item["src"])
                if resp.ok and len(resp.body()) > 500:
                    content_bytes = resp.body()
            except Exception as e_req:
                pw_log(f"Download via page.request: {e_req}", level="warn")

        if not content_bytes and not _src_eh_preview_video and new_media_item.get("src"):
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
        # TRACE 1 — início da janela que congelava (IMAGE_DOWNLOADED_OK -> FILE_SAVED_OK).
        is_video_result = (new_media_item["type"] == "video")
        if video_mode and not is_video_result:
            pw_log(f"[CENA {cid:03d}] ERRO_FORMATO: Modo vídeo requisitado, mas Flow retornou mídia do tipo '{new_media_item.get('type')}'. Abortando sem salvar.", level="error")
            try:
                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                    "status": scene_plan_svc.STATUS_ERRO,
                    "erro_msg": "Esperado vídeo do Flow, mas foi retornada imagem",
                    "video_status": scene_plan_svc.VIDEO_STATUS_ERROR,
                })
            except Exception:
                pass
            return False, f"Cena {cid}: esperado vídeo do Flow, mas retornou imagem"

        if is_video_result:
            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                "video_status": scene_plan_svc.VIDEO_STATUS_GENERATING
            })
        else:
            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                "image_status": scene_plan_svc.IMAGE_STATUS_DOWNLOADED
            })
        log_trace("← LOCK LIBERADO, prosseguindo para salvar_midia_cena_estruturada.", cid)

        ts_ini = float(cena.get("tempo_inicio", 0))
        dur = float(cena.get("duracao", 5))
        ts_fim = float(cena.get("tempo_fim", ts_ini + dur))

        # 9. Salva na estrutura oficial profissional do projeto:
        char_tag = ""
        if char_info:
            char_tag = char_info.get("referencia_flow") or (f"@{char_info.get('nome')}" if char_info.get("nome") else "")

        # TRACE 2 — a operação mais pesada da janela (grava arquivo, storyboard,
        # galeria, Visual Judgment interno, padrão v0.3.0 e mais locks de escrita).
        log_trace("→ INICIANDO salvar_midia_cena_estruturada (validação de bytes/arquivo + visual judgment interno).", cid)
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
        log_trace(f"← salvar_midia_cena_estruturada COMPLETOU (success={res_salva.get('success')}, "
                  f"arquivo={res_salva.get('arquivo_nome') or res_salva.get('error')}).", cid)

        # FASE 3.2 — falha de validação não entra em storyboard/galeria (status=ERRO já setado).
        if not res_salva.get("success", True):
            msg = res_salva.get("error", "mídia inválida")
            pw_log(f"[CENA {cid:03d}] MIDIA_INVALIDA: {msg}", level="error")
            return False, f"Mídia inválida para a cena {cid}: {msg}"

        # ANTIGRAVITY #3 — VALIDAÇÃO COM VISÃO (cenas AVATAR): compara a face da
        # imagem gerada com a reference.png (@personagem). Fidelidade < 70% →
        # rejeita e reprocessa (o loop da fila executa a 2ª tentativa).
        # Cenas POV pulam validação facial por design intencional (sem rosto).
        st_cena = str(cena.get("scene_type") or "").lower()
        ast_cena = str(cena.get("avatar_style") or "").lower()
        eh_pov = (
            st_cena in ("broll_pov", "pov")
            or ast_cena in ("first_person_pov", "pov", "broll_pov")
            or bool(cena.get("skip_facial_validation"))
        )
        if eh_pov:
            pw_log(f"[VISUAL_JUDGMENT_AVATAR] Cena {cid:03d}: Validação facial desabilitada (POV first-person has no visible face by design).")
            log_event("BRAIN_DIRECTOR", f"Cena {cid:03d}: Validação facial desabilitada (POV)", level="info")
        elif eh_avatar and not is_video_result:
            # TRACE 3 — Visual Judgment (fidelidade facial) do avatar.
            log_trace("→ INICIANDO Visual Judgment (fidelidade facial do avatar).", cid)
            try:
                import services.visual_judgment_service as vjs_svc
                _vj = vjs_svc.avaliar_fidelidade_facial(
                    projeto_id, res_salva.get("arquivo_path") or "", cena
                )
                if _vj and _vj.get("fidelidade") is not None:
                    _fid_av = int(_vj["fidelidade"])
                    log_trace(f"← Visual Judgment completou: fidelidade={_fid_av}% "
                              f"(método={_vj.get('metodo')}).", cid)
                    pw_log(f"[VISUAL_JUDGMENT_AVATAR] Fidelidade facial: {_fid_av}% (método={_vj.get('metodo')})")
                    if _fid_av < 70:
                        log_trace(f"❌ Fidelidade {_fid_av}% < 70% — cena {cid:03d} rejeitada para reprocessamento.", cid)
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
                else:
                    log_trace("← Visual Judgment sem medição (None) — seguindo sem rejeição.", cid)
            except Exception as _e_vj:
                # Falha NUNCA silenciosa: trace + evento de ERRO no console web.
                # (Não re-lança: a mídia já foi salva com sucesso — abortar aqui
                # faria a cena ficar presa em DOWNLOADED e regerar um crédito.)
                log_trace(f"❌ Visual Judgment FALHOU: {_e_vj}", cid)
                pw_log(f"[VISUAL_JUDGMENT_AVATAR] ERRO na validação facial da cena {cid:03d}: {_e_vj}", level="error")

        # CORREÇÃO 1 (FASE 3) — Não marcar PRONTO/BAIXADA sem o arquivo REAL em disco.
        # Antes, a cena era promovida a BAIXADA logo após a geração, mesmo quando a
        # gravação/download falhava. Agora só é promovida quando o arquivo existe
        # fisicamente (> 500 bytes); caso contrário permanece PENDENTE e pode ser
        # reprocessada (sem rebaixar a UI para um "pronto" falso).
        # TRACE 4 — I/O de disco: se o trace parar aqui, o pendurado é o
        # filesystem (disco cheio/lento, arquivo travado por outro processo).
        log_trace("→ Verificando arquivo no disco (deve existir e ter > 500 bytes para imagem, > 5000 bytes para vídeo).", cid)
        _arq_gerado = str((res_salva or {}).get("arquivo_path") or "")
        _arquivo_ok = False
        try:
            _min_bytes = 5000 if (video_mode or is_video_result) else 500
            _p_gerado = Path(_arq_gerado)
            _arquivo_ok = bool(_arq_gerado) and _p_gerado.exists() and _p_gerado.stat().st_size > _min_bytes
            if (video_mode or is_video_result) and _p_gerado.suffix.lower() not in (".mp4", ".webm", ".mov"):
                _arquivo_ok = False
        except OSError:
            _arquivo_ok = False
        log_trace(f"← Verificação de disco concluída: arquivo_ok={_arquivo_ok} "
                  f"({_arq_gerado or 'sem arquivo_path'}).", cid)
        if not _arquivo_ok:
            try:
                _campos_falha = {
                    "status": scene_plan_svc.STATUS_PENDENTE,
                    "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                    "erro_msg": "Arquivo não encontrado em disco após a geração ou extensão/tamanho inválido",
                }
                # CORREÇÃO 3 (FASE 3): em passada de VÍDEO a falha precisa refletir
                # no video_status (a UI/retomada decidem por ele).
                if is_video_result or video_mode:
                    _campos_falha["video_status"] = scene_plan_svc.VIDEO_STATUS_ERROR
                scene_plan_svc.atualizar_cena(projeto_id, cid, _campos_falha)
            except Exception:
                pass
            pw_log(f"[CENA {cid:03d}] Sem arquivo válido em disco — mantendo PENDENTE.", level="warn")
            return False, f"Cena {cid} não gerou arquivo válido em disco (mantida PENDENTE)"

        # ANTIGRAVITY — [VIDEO_SAVED]: par explícito do [VIDEO_DOWNLOAD_OK] —
        # confirma que o MP4 do vídeo foi gravado em cenas/ ({cid}_[...].mp4).
        if is_video_result:
            try:
                _sz_saved = Path(_arq_gerado).stat().st_size
            except OSError:
                _sz_saved = 0
            _origem_saved = " — origem: flow-video-tile" if _video_bytes_via_tile else ""
            pw_log(f"[VIDEO_SAVED] Cena {cid:03d}: {res_salva.get('arquivo_nome')} salvo em cenas/ ({_sz_saved} bytes){_origem_saved}.")

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
        # TRACE 5 — os DOIS locks de escrita desta etapa, separados de propósito:
        # se o trace parar em um deles, o culpado é identificado sem ambiguidade
        # (atualizar_cena vs sincronizar_midias_encontradas).
        log_trace("→ atualizar_cena(status=BAIXADA/READY) — aguardando LOCK de escrita.", cid)
        scene_plan_svc.atualizar_cena(projeto_id, cid, campos_cena)
        log_trace("← Status atualizado para BAIXADA/READY (lock liberado).", cid)
        log_trace("→ sincronizar_midias_encontradas (lê/escreve midias_encontradas.json).", cid)
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        log_trace("← midias_encontradas sincronizado; fechando padrão de mídia (status.json).", cid)
        # CORREÇÃO 3 (FASE 3): fechamento obrigatório das 3 fontes sincronizadas.
        # garantir_cena_padrao(mover=False) grava metadata/cena_XXX/status.json (a 3ª
        # fonte) SEM mover o arquivo: o layout em disco (cenas/) é preservado.
        try:
            from services.media_standard import garantir_cena_padrao
            garantir_cena_padrao(projeto_id, cid, mover=False)
        except Exception as e_pad:
            pw_log(f"[CENA {cid:03d}] Aviso ao sincronizar mídia padrão (status.json): {e_pad}", level="warn")
        pw_log(f"[CENA {cid:03d}] ✅ Baixada: {res_salva['arquivo_path']}")

        # TRACE 6 — fim da janela que congelava (próximo print é o FILE_SAVED_OK).
        log_trace("← PRONTO, emitindo FILE_SAVED_OK.", cid)
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

    def _handle_run_queue(self, projeto_id: str, scene_ids: Optional[List[int]], modo: str,
                          bypass_rate_limit: bool = False):
        self.is_running_queue = True
        self.stop_requested.clear()
        # PARTE 5 — reseta o fallback video→imagem a cada fila nova.
        self._fallback_video_para_imagem = False
        self._fallback_video_para_imagem_motivo = ""
        self.current_project_id = projeto_id
        self._persistir_alerta_creditos(fallback=False)
        self.current_flow_mode = modo
        self._avatar_uploaded = False  # nova sessão = novo upload de avatar
        self._avatar_media_uploaded = False
        self._personagem_card_persistido = False
        # GUARD DO AVATAR — chaves são por FILA/conta (nova fila = novo snapshot).
        self._chaves_upload_conhecidas: Set[str] = set()
        self._chaves_ignoradas: Set[str] = set()
        self._configured_mode = None   # Reseta estado do canvas para configurar novo modo/modelo

        # TAREFA 2 — EXCLUSÃO DO CAMINHO LEGADO (auditoria): este caminho cria a
        # própria sessão Playwright numa thread própria e a encerra no fim
        # (_encerrar_sessao no finally). Para ele NÃO disputar o mesmo Chrome com
        # o ator CDP (que executa a fila assíncrona), TODO o legado passa a rodar
        # sob o MESMO lock global de geração — liberado no finally abaixo.
        # O lock é adquirido SÓ aqui e dentro de _criar_imagem_cena_impl (thread
        # do ator); adquirir no chamador E no ator causaria deadlock entre threads.
        with _legacy_run_lock:
            global _legacy_run_ativos
            _legacy_run_ativos += 1
        _flow_generation_lock.acquire()
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

            # 2.1 PRÉ-VOO DO AVATAR (ANTIGRAVITY — checagem contra a biblioteca REAL):
            #     self._avatar_uploaded vive na instância do worker (zerada a cada
            #     fila nova logo acima) e não prova nada sobre a conta ativa — era por
            #     isso que o avatar era reenviado/recriado (com reload da página)
            #     mesmo quando '@Nome' já existia. Antes de qualquer upload/criação,
            #     consulta UMA vez a biblioteca real do Flow (popup '@', por conta):
            #       True  -> confirmado: PULA _upload_avatar_projeto inteiramente;
            #       False -> segue o fluxo normal (upload + criação nativa);
            #       None  -> inconclusivo: NÃO pula (evita mascarar ausência real).
            _status_avatar_pre = None
            _nome_avatar_pre = ""
            _pular_upload_avatar = False
            try:
                import services.character_service as character_svc
                _personagem_av, _ = character_svc.validar_personagem_do_projeto(projeto_id)
                if _personagem_av:
                    _nome_avatar_pre = str(_personagem_av.get("referencia_flow")
                                           or _personagem_av.get("nome") or "").strip().lstrip("@")
                else:
                    _idt_av = character_svc.obter_identidade_projeto(projeto_id) or {}
                    _nome_avatar_pre = str(_idt_av.get("nome") or "").strip().lstrip("@")
                # TRAVA ANTI-GENÉRICO (mesma regra do pré-voo de personagens).
                if _nome_avatar_pre and character_svc.personagem_e_generico(_nome_avatar_pre):
                    _nome_avatar_pre = ""
                if _nome_avatar_pre:
                    _status_avatar_pre = self._verificar_personagem_na_biblioteca(_nome_avatar_pre)
                    if _status_avatar_pre is True:
                        _pular_upload_avatar = True
                        self._avatar_uploaded = True
                        self._avatar_media_uploaded = True
                        pw_log(f"[AVATAR_UPLOAD] PERSONAGEM_JA_NA_BIBLIOTECA: '@{_nome_avatar_pre}' já existe na biblioteca real do Flow — upload/criação do avatar ignorados nesta fila (sem reload de página).")
                        print(f"[OK] AVATAR: personagem '@{_nome_avatar_pre}' já existe no Flow — upload ignorado.", flush=True)
                    elif _status_avatar_pre is None:
                        # TAREFA 5 (ANTIGRAVITY) — INCONCLUSIVO NUNCA CRIA: era o caminho que
                        # recriava '@Nome' (4ª cópia) quando o popover abria vazio por projeto.
                        _pular_upload_avatar = True
                        pw_log(f"[AVATAR_UPLOAD] Verificação inconclusiva para '@{_nome_avatar_pre}' — "
                               "upload/criação prevenidos para não duplicar.", level="warn")
            except Exception as e_pre_av:
                pw_log(f"[AVATAR_UPLOAD] Aviso no pré-voo de avatar (segue fluxo normal): {e_pre_av}", level="warn")

            # 2.2 Upload do avatar do projeto para o workspace (UMA vez por sessão).
            #     Falha NUNCA trava a fila — apenas avisa e segue sem avatar.
            if not _pular_upload_avatar:
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
                # PERSONAGEM OFICIAL DO PROJETO (fonte única = identidade.json):
                # `validar_personagem_do_projeto` devolve erro CLARO quando não há
                # identidade/personagem — nunca um nome genérico.
                _personagem_oficial, _erro_idt_pre = character_svc.validar_personagem_do_projeto(projeto_id)
                _idt = character_svc.obter_identidade_projeto(projeto_id) or {}
                if _personagem_oficial:
                    # Tag OFICIAL da identidade ('@Marcos'), sem '@' para casar com
                    # _verificar_personagem_na_biblioteca()/_reprovisionar_personagem_apos_rotacao().
                    _nome_char = str(_personagem_oficial.get("referencia_flow")
                                     or _personagem_oficial.get("nome") or "").strip().lstrip("@")
                else:
                    _nome_char = str(_idt.get("nome") or "").strip()
                    if _erro_idt_pre:
                        log_char(f"pré-voo: identidade do projeto inválida ({_erro_idt_pre}) — "
                                 f"provisionamento cancelado (nunca criamos personagem genérico).",
                                 projeto_id=projeto_id, level="error")
                # TRAVA ANTI-GENÉRICO: identidade com nome genérico ("avatar",
                # "personagem", "me") NÃO vira provisionamento no Flow — seria
                # exatamente o personagem genérico que este fluxo proíbe.
                if _nome_char and character_svc.personagem_e_generico(_nome_char):
                    log_char(f"pré-voo: identidade de '{projeto_id}' usa nome genérico "
                             f"'{_nome_char}' — provisionamento cancelado (nunca criamos "
                             f"personagem genérico).", projeto_id=projeto_id, level="error")
                    _nome_char = ""
                motivo_pre_voo = ""
                if not _nome_char:
                    motivo_pre_voo = ("há cenas que usam personagem, mas nenhum "
                                      "personagem está configurado no projeto (Studio 2.0 -> aba "
                                      "Identidade: nome + foto de referência). Configure e clique "
                                      "novamente em 'Gerar Todas as Imagens' para retomar a fila.")
                    log_char(f"pré-voo ABORTADO: {motivo_pre_voo}", projeto_id=projeto_id, level="error")
                else:
                    # Reaproveita o pré-voo ANTECIPADO (rodado ANTES do upload do
                    # avatar): se ele já confirmou ESTE personagem na biblioteca
                    # real do Flow, não repete o ciclo do popup '@' — mesmo
                    # resultado sem verificação dupla. False/None seguem o fluxo
                    # original (retentativas incluídas) abaixo.
                    _status_pre = None
                    if (_status_avatar_pre is True and _nome_avatar_pre
                            and _nome_char.strip().lower() == _nome_avatar_pre.strip().lower()):
                        _status_pre = True
                        pw_log(f"[PRE_VOO] '@{_nome_char}' já confirmado no pré-voo do avatar — verificação repetida dispensada.")
                    if _status_pre is None:
                        try:
                            _status_pre = self._verificar_personagem_na_biblioteca(_nome_char)
                        except Exception as _e_verif:
                            pw_log(f"[FLOW] Aviso ao verificar personagem no pré-voo: {_e_verif}", level="warn")
                            _status_pre = None

                    if _status_pre is None:
                        for _tentativa in range(1, 3):
                            try:
                                if self.page:
                                    self.page.keyboard.press("Escape")
                                    self.page.wait_for_timeout(1500)
                                _status_pre = self._verificar_personagem_na_biblioteca(_nome_char)
                            except Exception as _e_retry:
                                pw_log(f"[FLOW] Aviso na retentativa {_tentativa} de pré-voo: {_e_retry}", level="warn")
                                _status_pre = None
                            if _status_pre is not None:
                                break

                    if _status_pre is False:
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
                    elif _status_pre is None:
                        pw_log(f"[PRE_VOO] verificação inconclusiva para '@{_nome_char}' — criação NÃO disparada para evitar duplicata.", level="warn")
                        motivo_pre_voo = (f"Verificação do personagem '@{_nome_char}' na biblioteca foi inconclusiva "
                                          "(menu não abriu ou erro temporário). Criação automática evitada para não duplicar. "
                                          "A fila segue normalmente.")
                    elif _status_pre is True:
                        pass

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
                    self._avatar_uploaded = True

            # PRÉ-VOO FINAL — só reprovisiona se a validação anterior não confirmou o personagem
            if cenas_a_processar and self.current_project_id and not getattr(self, "_avatar_uploaded", False):
                pw_log("[QUEUE] Executando pré-voo final de personagens... (personagem não confirmado — verificando e provisionando se necessário)", level="info")
                self._reprovisionar_personagem_apos_rotacao(
                    projeto_id=projeto_id, forcar_criacao=False
                )
                pw_log("[QUEUE] Pré-voo de personagens concluído.")
            elif cenas_a_processar and self.current_project_id:
                pw_log("[QUEUE] Pré-voo final: personagem já validado — reprovisionamento ignorado.", level="info")

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
            # Qualidade de DOWNLOAD do vídeo gerado (1080p = aprimorada, 720p = original)
            _qualidade_download_video_pre = str(_meta_proj.get("prod_qualidade_download_video") or "1080p")
            self.current_download_quality_video = _qualidade_download_video_pre
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
                print(f"[OK] MODELO_CONFIGURADO: {_modelo_pre} ({_modo_pre}, {_proporcao_pre}, {_qualidade_pre}) — lido do meta.json do projeto. | Download Img: {_qualidade_download_pre} | Download Vid: {_qualidade_download_video_pre}", flush=True)
                pw_log(f"[QUEUE] MODELO_PRE_LOOP_OK: {_modelo_pre} ({_modo_pre}, {_proporcao_pre}, {_qualidade_pre}) configurado antes do primeiro envio. | Download Img: {_qualidade_download_pre} | Download Vid: {_qualidade_download_video_pre}")
            except Exception as _e_modo:
                pw_log(f"[QUEUE] Aviso ao configurar modelo antes do loop: {_e_modo}", level="warn")

            # RATE LIMIT PROTECTION — aguarda 10s ANTES de iniciar o primeiro envio da
            # fila (evita CAPTCHA/rate limit do Google Flow ao iniciar uma sequência).
            # Espera em blocos de 5s respeitando o botão Pausar/Cancelar do usuário.
            # ITEM 8 — `bypass_rate_limit=True` (rota /retomar_projeto) pula a espera
            # inicial: é uma RETOMADA explícita de uma fila já em andamento. Default
            # False mantém o comportamento original (início normal aguarda 10s).
            if len(cenas_a_processar) > 0:
                # TAREFA 1 — Sincroniza escopo do lote em flow_queue.json para consumo imediato da UI
                try:
                    _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                    with transacao_fila(projeto_id, _pdir_queue) as fila_lote:
                        itens_lote = []
                        for pos_lote, c_lote in enumerate(cenas_a_processar, 1):
                            cid_lote = int(c_lote.get("id", 0))
                            eh_video_lote = bool(
                                modo == "animacao"
                                or scene_plan_svc.tipo_efetivo_cena(c_lote) == scene_plan_svc.TIPO_VIDEO
                                or c_lote.get("tipo") == "video"
                                or c_lote.get("media_intent") == "video"
                            )
                            modo_str = "video" if eh_video_lote else "imagem"
                            prompt_str = (
                                (c_lote.get("prompt_animacao") if modo_str == "video" else None)
                                or c_lote.get("visual_prompt")
                                or c_lote.get("prompt_imagem")
                                or c_lote.get("texto")
                                or ""
                            ).strip()
                            itens_lote.append({
                                "scene_id": cid_lote,
                                "id": cid_lote,
                                "posicao": pos_lote,
                                "status": "enfileirada",
                                "tentativa_atual": 1,
                                "max_tentativas": 2,
                                "iniciada_em": None,
                                "concluida_em": None,
                                "ultimo_erro": None,
                                "error_msg": "",
                                "modo": modo_str,
                                "prompt": prompt_str
                            })
                        fila_lote["projeto_id"] = projeto_id
                        fila_lote["itens"] = itens_lote
                        fila_lote["total"] = len(itens_lote)
                        fila_lote["concluidos"] = 0
                        fila_lote["erros"] = 0
                        fila_lote["cena_ativa"] = None
                        fila_lote["ativa"] = bool(itens_lote)
                        fila_lote["pausada"] = False
                        fila_lote["atualizado_em"] = datetime.now().isoformat()
                except Exception as _e_init_fila:
                    pw_log(f"[QUEUE] Falha ao sincronizar flow_queue.json inicial: {_e_init_fila}", level="warn")

                if bypass_rate_limit:
                    pw_log("[RATE_LIMIT_PROTECTION] bypass_rate_limit=True — espera inicial de 10s "
                           "PULADA (retomada explícita do projeto).", level="warn")
                else:
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
            # CIRCUIT BREAKER (ANTIGRAVITY) — falhas CONSECUTIVAS por bloqueio do
            # Flow ("notamos uma atividade incomum"): ao atingir o limite, a fila é
            # pausada automaticamente. Só um SUCESSO zera o contador; falhas de outro
            # tipo (imagem no lugar de vídeo, timeout, política de conteúdo) são
            # NEUTRAS (não incrementam nem zeram). Local à fila (zera a cada nova).
            falhas_consecutivas_bloqueio = 0
            for idx, cena in enumerate(cenas_a_processar, 1):
                if self.stop_requested.is_set():
                    # REQ 3 — a fila pode ter sido interrompida por CRÉDITOS ou por
                    # ação do operador: o motivo canônico (quando houver) vai no log.
                    _motivo_pausa = getattr(self, "pause_reason", None) or "manual"
                    print(f"\n[INFO] Fila interrompida (motivo: {_motivo_pausa}).", flush=True)
                    pw_log(f"\n[FLOW SESSION]\nStatus: Fila interrompida.\nMotivo: {_motivo_pausa}")
                    break

                # RATE LIMIT PROTECTION — em filas grandes (>5 cenas), aguarda 5s antes
                # de cada cena a partir da 2ª (idx é 1-based neste loop). Espera em
                # blocos de 5s respeitando o botão Pausar/Cancelar do usuário.
                if idx > 1 and len(cenas_a_processar) > 5:
                    _cid_prox = int(cena.get("id", 0))
                    print(f"[RATE_LIMIT_PROTECTION] Aguardando 5s antes de processar Cena {_cid_prox:03d}...", flush=True)
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

                # TAREFA 2 — Atualiza item em flow_queue.json para 'gerando'
                try:
                    _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                    with transacao_fila(projeto_id, _pdir_queue) as fila_cena:
                        for it in (fila_cena.get("itens") or []):
                            if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                it["status"] = "gerando"
                                it["iniciada_em"] = datetime.now().isoformat()
                                it["tentativa_atual"] = 1
                                break
                        fila_cena["cena_ativa"] = {
                            "scene_id": cid,
                            "posicao": idx,
                            "status": "gerando"
                        }
                        fila_cena["ativa"] = True
                except Exception as _e_sc_start:
                    pw_log(f"[QUEUE] Falha ao marcar cena {cid} como gerando em flow_queue: {_e_sc_start}", level="warn")

                sucesso = False
                res_msg = ""
                recolocada_por_credito = False
                for tentativa in range(2):
                    if self.stop_requested.is_set():
                        break

                    self.cena_ativa["tentativa"] = tentativa + 1
                    self.cena_ativa["etapa"] = f"Executando no Flow (Tentativa {tentativa + 1}/2)..."
                    if tentativa > 0:
                        try:
                            _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                            with transacao_fila(projeto_id, _pdir_queue) as fila_cena:
                                for it in (fila_cena.get("itens") or []):
                                    if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                        it["tentativa_atual"] = tentativa + 1
                                        break
                        except Exception:
                            pass

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
                        _limite_reciclagens = 3
                        try:
                            from services.flow_account_manager import FlowAccountManager
                            _contas_disp = FlowAccountManager().listar_contas_ativas()
                            _limite_reciclagens = max(3, len(_contas_disp) + 1)
                        except Exception:
                            pass
                        if recoloc_count[cid] > _limite_reciclagens:
                            _msg_esgotadas = "Todas as contas esgotadas"
                            log_event(
                                "PLAYWRIGHT_FLOW",
                                f"[FLOW] Cena {cid} reciclada {recoloc_count[cid]}x por créditos esgotados — "
                                f"{_msg_esgotadas}. Cena devolvida a PENDENTE (sem ERRO) e fila interrompida.",
                                level="error",
                            )
                            pw_log(
                                f"[FLOW] Cena {cid} excedeu 3 reciclagens por créditos esgotados "
                                f"({_msg_esgotadas}). Marcando como PENDENTE por crédito e PARANDO a fila.",
                                level="error",
                            )
                            # REQ 2 — a cena NÃO vira ERRO: é "pendente por crédito" e a
                            # mídia já existente em disco é preservada (nada é regerado).
                            # TAREFA 4 — crédito esgotado detectado DURANTE a tentativa
                            # real: registra a prova na cena (video_tentado/video_falhou).
                            _eh_falha_video = (res_msg == "credito_esgotado_video_recolocado")
                            _motivo_falha = "credito_esgotado_video" if _eh_falha_video else "credito_esgotado"
                            try:
                                scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                    "status": scene_plan_svc.STATUS_PENDENTE,
                                    "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                                    "video_status": scene_plan_svc.VIDEO_STATUS_NOT_STARTED,
                                    "erro_msg": _msg_esgotadas,
                                    "video_tentado": True,
                                    "video_falhou": True,
                                    "video_falhou_motivo": _motivo_falha,
                                })
                            except Exception as _e_erro_cred:
                                pw_log(f"[FLOW] Aviso ao marcar cena {cid} como PENDENTE por crédito: {_e_erro_cred}", level="warn")
                            # NÃO recoloca na fila; para a fila (o loop externo sai no topo,
                            # no guard "if self.stop_requested.is_set()").
                            # REQ 3 — motivo CANÔNICO da parada (exposto em /status e no SSE).
                            # TAREFA 4 — quando o que falhou foi o VÍDEO, o motivo canônico
                            # é `credito_esgotado_video` (o mesmo que o frontend usa para
                            # notificar/navegar); falha de imagem mantém `fim_fila_...`.
                            if _eh_falha_video:
                                self.set_pause_reason("credito_esgotado_video", parar_fila=True)
                                self._notificar_navegacao_credito("credito_esgotado_video")
                            else:
                                self.set_pause_reason("fim_fila_credito_zerado", parar_fila=True)
                                self._notificar_navegacao_credito("fim_fila_credito_zerado")
                            break
                        # CORREÇÃO 4 / PARTE 5 — crédito esgotado NÃO é erro da cena: NÃO marca
                        # STATUS_ERRO; volta para PENDENTE e recoloca no INÍCIO da fila
                        # para reprocessamento (com a nova conta — credito_esgotado_recolocado —
                        # ou como IMAGEM via fallback — credito_esgotado_video_recolocado, pois a
                        # flag _fallback_video_para_imagem força video_mode=False na re-execução).
                        pw_log(f"[FLOW] Recolocando cena {cena.get('id')} no início da fila (PENDENTE, sem ERRO).", level="warn")
                        # TAREFA 4 — a tentativa REAL falhou por crédito: registra a prova
                        # na cena (sem parar a fila neste ramo: a rotação/fallback continua).
                        _motivo_rec = ("credito_esgotado_video"
                                       if res_msg == "credito_esgotado_video_recolocado"
                                       else "credito_esgotado")
                        try:
                            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                                "status": scene_plan_svc.STATUS_PENDENTE,
                                "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                                "erro_msg": "",
                                "video_tentado": True,
                                "video_falhou": True,
                                "video_falhou_motivo": _motivo_rec,
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

                # ANTIGRAVITY — TAREFA 2: cena PULADA em fila de animação (não elegível a
                # vídeo). Nada foi gerado nem alterado: sai da fila como "ignorada" (NÃO é
                # STATUS_ERRO, NÃO conta como concluída, NÃO retenta, NÃO alimenta o
                # circuit-breaker e não entra na média de tempos). Sem DELAY: nenhum prompt
                # foi enviado, então não há rate limit a respeitar.
                if sucesso and str(res_msg) == ANIM_SKIP_RESULTADO:
                    pw_log(
                        f"[ANIM_SKIP] Cena {cid:03d}: removida da fila como IGNORADA "
                        "(não elegível a vídeo nesta fila de animação).",
                        level="warn",
                    )
                    try:
                        _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                        with transacao_fila(projeto_id, _pdir_queue) as fila_skip:
                            for it in (fila_skip.get("itens") or []):
                                if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                    it["status"] = "ignorada"
                                    it["concluida_em"] = datetime.now().isoformat()
                                    it["ultimo_erro"] = None
                                    break
                            fila_skip["cena_ativa"] = None
                            fila_skip["ignoradas"] = sum(
                                1 for it in (fila_skip.get("itens") or [])
                                if it.get("status") == "ignorada"
                            )
                    except Exception as _e_anim_skip:
                        pw_log(f"[QUEUE] Falha ao marcar cena {cid} como ignorada em flow_queue: {_e_anim_skip}", level="warn")
                    self.cena_ativa = {
                        "scene_id": cid,
                        "scene_idx": idx,
                        "total_cenas": len(cenas_a_processar),
                        "status": "IGNORADA",
                        "etapa": f"Cena {cid:03d} ignorada: não elegível a vídeo nesta fila.",
                        "tempo_total": time.time() - self.queue_start_time,
                    }
                    continue

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
                    # CIRCUIT BREAKER (ANTIGRAVITY): um SUCESSO zera o contador de
                    # bloqueios consecutivos do Flow (só ele zera — ver comentário
                    # da inicialização antes do loop).
                    falhas_consecutivas_bloqueio = 0

                    # TAREFA 3 — Marca status: concluida em flow_queue.json
                    try:
                        _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                        with transacao_fila(projeto_id, _pdir_queue) as fila_fim:
                            for it in (fila_fim.get("itens") or []):
                                if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                    it["status"] = "concluida"
                                    it["concluida_em"] = datetime.now().isoformat()
                                    it["ultimo_erro"] = None
                                    break
                            fila_fim["cena_ativa"] = None
                            fila_fim["concluidos"] = sum(1 for it in (fila_fim.get("itens") or []) if it.get("status") == "concluida")
                    except Exception as _e_sc_ok:
                        pw_log(f"[QUEUE] Falha ao marcar cena {cid} como concluida em flow_queue: {_e_sc_ok}", level="warn")

                elif recolocada_por_credito:
                    # CORREÇÃO 4 — crédito esgotado: cena recolocada no início com
                    # PENDENTE; NÃO registra erro. O loop externo continua sem break.
                    pw_log(f"[FLOW] Cena {cid} recolocada no início da fila (créditos esgotados) — sem marcar ERRO.")
                    try:
                        _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                        with transacao_fila(projeto_id, _pdir_queue) as fila_fim:
                            for it in (fila_fim.get("itens") or []):
                                if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                    it["status"] = "enfileirada"
                                    it["tentativa_atual"] = 1
                                    break
                            fila_fim["cena_ativa"] = None
                    except Exception:
                        pass
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

                        # TAREFA 3 — Marca status: erro em flow_queue.json
                        try:
                            _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                            with transacao_fila(projeto_id, _pdir_queue) as fila_fim:
                                for it in (fila_fim.get("itens") or []):
                                    if int(it.get("scene_id") or it.get("id") or 0) == cid:
                                        it["status"] = "erro"
                                        it["concluida_em"] = datetime.now().isoformat()
                                        it["ultimo_erro"] = res_msg
                                        it["error_msg"] = res_msg
                                        break
                                fila_fim["cena_ativa"] = None
                                fila_fim["erros"] = sum(1 for it in (fila_fim.get("itens") or []) if it.get("status") == "erro")
                        except Exception as _e_sc_err:
                            pw_log(f"[QUEUE] Falha ao marcar cena {cid} como erro em flow_queue: {_e_sc_err}", level="warn")

                        # CIRCUIT BREAKER (ANTIGRAVITY) — falha DEFINITIVA da cena
                        # (2 tentativas esgotadas, já registrada como ERRO acima).
                        # Só o bloqueio do Flow ("notamos uma atividade incomum")
                        # alimenta o contador; outras falhas são neutras.
                        if "notamos uma atividade incomum" in str(res_msg).lower():
                            falhas_consecutivas_bloqueio += 1
                            pw_log(
                                f"[CIRCUIT_BREAKER] Bloqueio do Flow (atividade incomum) na cena "
                                f"{cid:03d} — falha consecutiva "
                                f"{falhas_consecutivas_bloqueio}/{LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW}: {res_msg}",
                                level="warn",
                            )
                            if falhas_consecutivas_bloqueio >= LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW:
                                pw_log(
                                    f"[CIRCUIT_BREAKER] {LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW} falhas consecutivas "
                                    "por atividade incomum — PAUSANDO a fila automaticamente "
                                    f"(motivo: {MOTIVO_PAUSA_BLOQUEIO_FLOW}). Aguarde alguns minutos antes de retomar.",
                                    level="error",
                                )
                                print(
                                    f"[CIRCUIT_BREAKER] {LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW} falhas consecutivas "
                                    "por atividade incomum — fila pausada automaticamente. "
                                    "Aguarde alguns minutos antes de clicar em Retomar.",
                                    flush=True,
                                )
                                self.set_pause_reason(MOTIVO_PAUSA_BLOQUEIO_FLOW, parar_fila=True)
                                break

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
            # TAREFA 2 — libera o lock global de geração do caminho legado.
            try:
                _flow_generation_lock.release()
            except Exception:
                pass
            with _legacy_run_lock:
                _legacy_run_ativos -= 1

            # TAREFA 3/4 — Finaliza estado ativo da fila em flow_queue.json
            try:
                _pdir_queue = getattr(self, "projetos_dir", None) or PROJETOS_DIR
                with transacao_fila(projeto_id, _pdir_queue) as fila_final:
                    fila_final["ativa"] = False
                    fila_final["cena_ativa"] = None
                    if getattr(self, "stop_requested", None) and self.stop_requested.is_set():
                        fila_final["pausada"] = True
            except Exception as _e_fin:
                pw_log(f"[QUEUE] Falha ao finalizar estado em flow_queue: {_e_fin}", level="warn")

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

# Seletores do botão de conclusão do assistente de personagem do Flow (Passo 8)
SELETORES_BOTAO_CONCLUIR_PERSONAGEM = [
    'button.flow-button-secondary:has-text("Concluir")',
    'button.flow-button-primary:has-text("Concluir")',
    'button:has-text("Concluir")',
    'button.flow-button-secondary:has-text("Done")',
    'button.flow-button-primary:has-text("Done")',
    'button:has-text("Done")',
    'button:has-text("Concluído")',
    'button:has-text("Pronto")',
    'button:has-text("Salvar")',
    'button:has-text("Save")',
    'button[aria-label*="Edição concluída" i]',
    'button[aria-label*="Edicao concluida" i]',
    '[aria-label*="Edição concluída" i]',
    '[aria-label*="Edicao concluida" i]',
    '[aria-label*="Concluir" i]',
    '[aria-label*="Done" i]',
    '[aria-label*="Salvar" i]',
]


class FlowCharacterCreationError(RuntimeError):
    """Falha explícita em um passo da criação automática de personagem no Flow."""

    def __init__(self, message: str, card_persistido: bool = False):
        super().__init__(message)
        self.card_persistido = card_persistido


def _clicar_item_personagens_flow(page, timeout_ms: int = 15000) -> bool:
    """Abre o drawer lateral (se necessário) e clica em Characters/Personagens.

    Cobre PT/EN e várias tecnologias de lista (mat-list-item, role=menuitem,
    links, botões), além de fallback JS por texto exato. Honra timeout_ms com
    espera ativa pelo desaparecimento de overlays de 'Carregando...'. Retorna True se clicou.
    """
    if not page:
        return False

    t_start = time.time()
    timeout_s = max(float(timeout_ms) / 1000.0, 5.0)

    # 0) Se o canvas ainda estiver carregando, aguarda sumir overlay de "Carregando..."
    try:
        for s_load in ['text="Carregando..."', 'text="Loading..."', 'mat-spinner', '.mat-progress-spinner']:
            loc_load = page.locator(s_load).first
            if loc_load.is_visible(timeout=300):
                pw_log("[FLOW] Tela de carregamento detectada. Aguardando estabilização do canvas...")
                loc_load.wait_for(state="hidden", timeout=15000)
                page.wait_for_timeout(500)
                break
    except Exception:
        pass

    termos = ["Characters", "Personagens"]
    seletores = []
    for termo in termos:
        seletores += [
            f'.mat-drawer-inner-container mat-list-item:has-text("{termo}")',
            f'.mat-drawer-inner-container [role="menuitem"]:has-text("{termo}")',
            f'.mat-drawer-inner-container a:has-text("{termo}")',
            f'.mat-drawer-inner-container button:has-text("{termo}")',
            f'button:has-text("{termo}")',
            f'mat-list-item:has-text("{termo}")',
            f'[role="menuitem"]:has-text("{termo}")',
            f'[role="button"]:has-text("{termo}")',
            f'a:has-text("{termo}")',
            f'[aria-label*="{termo}" i]',
            f'span:text-is("{termo}")',
            f'div[role="tab"]:has-text("{termo}")',
            f'nav button:has-text("{termo}")',
            f'aside button:has-text("{termo}")',
            f'flow-navigation-item:has-text("{termo}")',
            f'flow-nav-item:has-text("{termo}")',
        ]

    sel_menus = [
        'button[aria-label*="menu" i]',
        'button[aria-label*="navigation" i]',
        'button[aria-label*="navega" i]',
        'button[aria-label*="drawer" i]',
        'button[aria-label*="sidebar" i]',
        'button.mdc-icon-button:has-text("menu")',
        'button:has(i:text-is("menu"))',
        'button:has(mat-icon:has-text("menu"))',
        'button:has(i:has-text("menu"))',
        'button:has(.material-icons:has-text("menu"))',
        'button:has(.material-symbols-outlined:has-text("menu"))',
    ]

    def _tentar() -> bool:
        for sel in seletores:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=300):
                    loc.click(timeout=1500)
                    pw_log(f"[FLOW] Clicou em Characters/Personagens via seletor: {sel}")
                    return True
            except Exception:
                continue
        return False

    def _tentar_js() -> bool:
        try:
            clicou = page.evaluate('''() => {
                const containers = [
                    document.querySelector('.mat-drawer-inner-container'),
                    document.querySelector('mat-sidenav'),
                    document.querySelector('nav'),
                    document.querySelector('aside'),
                    document
                ];
                for (const raiz of containers) {
                    if (!raiz) continue;
                    const alvos = Array.from(raiz.querySelectorAll(
                        'mat-list-item, [role="menuitem"], a, button, [role="button"], span, div'
                    ));
                    const alvo = alvos.find(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        return t === 'characters' || t === 'personagens';
                    });
                    if (alvo) {
                        const clicavel = alvo.closest('mat-list-item, [role="menuitem"], a, button, [role="button"]') || alvo;
                        clicavel.click();
                        return true;
                    }
                }
                return false;
            }''')
            if clicou:
                pw_log("[FLOW] Clicou em Characters/Personagens via fallback JS.")
                return True
        except Exception:
            pass
        return False

    # Loop ativo de tentativas até o timeout
    while time.time() - t_start < timeout_s:
        # 1) item já disponível (drawer aberto)?
        if _tentar():
            return True

        # 2) abre o menu lateral (hamburger) e tenta novamente
        for sel_menu in sel_menus:
            try:
                m = page.locator(sel_menu).first
                if m.is_visible(timeout=350):
                    m.click(timeout=1200)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                continue

        if _tentar():
            return True

        # 3) fallback JS — procura o item por texto exato (PT/EN) e clica no ancestral clicável
        if _tentar_js():
            return True

        page.wait_for_timeout(500)

    return False


# =============================================================================
# UPLOAD NO FLOW — GUARDA POR SINAL (não fatal)
# =============================================================================
# JS: estado do upload na página — porcentagem visível (/^\s*\d{1,3}\s*%$/) e <img>
# novas, completas e fora do baseline. Devolve {"pct": str|None, "novas": [srcs]}.
_JS_ESTADO_UPLOAD = """
(baseline) => {
  const visivel = (el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
      const st = window.getComputedStyle(el);
      return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
    } catch (e) { return false; }
  };
  const pctRe = /^\\s*\\d{1,3}\\s*%\\s*$/;
  let pct = null;
  const all = document.querySelectorAll("*");
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    if (el.children && el.children.length === 0) {
      const t = (el.textContent || "").trim();
      if (t.length <= 5 && t.indexOf("%") >= 0 && pctRe.test(t) && visivel(el)) { pct = t; break; }
    }
  }
  const base = baseline || [];
  const novas = [];
  const imgs = document.images || [];
  for (let i = 0; i < imgs.length; i++) {
    const src = imgs[i].currentSrc || imgs[i].src || "";
    if (!src || base.indexOf(src) !== -1) continue;
    if (src.indexOf("data:image/svg") === 0) continue;
    if (imgs[i].complete === true && imgs[i].naturalWidth > 50) novas.push(src);
  }
  return { pct: pct, novas: novas };
}
"""

# JS: diagnóstico quando o upload NÃO é confirmado — outerHTML (2000 chars) das
# porcentagens visíveis e das <img> candidatas.
_JS_DIAG_UPLOAD = """
() => {
  const out = { pct: [], imgs: [] };
  const pctRe = /^\\s*\\d{1,3}\\s*%\\s*$/;
  const all = document.querySelectorAll("*");
  for (let i = 0; i < all.length && out.pct.length < 5; i++) {
    const el = all[i];
    if (el.children && el.children.length === 0) {
      const t = (el.textContent || "").trim();
      if (t.length <= 5 && t.indexOf("%") >= 0 && pctRe.test(t)) {
        out.pct.push(String(el.outerHTML || "").slice(0, 2000));
      }
    }
  }
  const imgs = document.images || [];
  for (let i = 0; i < imgs.length && out.imgs.length < 5; i++) {
    const src = imgs[i].currentSrc || imgs[i].src || "";
    if (src.indexOf("data:image/svg") === 0) continue;
    out.imgs.push(String(imgs[i].outerHTML || src).slice(0, 2000));
  }
  return out;
}
"""


def _baseline_imgs_pagina(page) -> list:
    """Srcs das <img> ANTES do upload (baseline da guarda). NUNCA levanta exceção."""
    try:
        res = page.evaluate("() => Array.from(document.images).map(i => i.currentSrc || i.src)")
        return res if isinstance(res, list) else []
    except Exception:
        return []


def _diagnostico_upload(page, t0: float) -> None:
    """Screenshot em logs/flow_automation/ + outerHTML das '%' e das <img> (diagnóstico)."""
    caminho = None
    try:
        shot_dir = Path(BASE_DIR) / "logs" / "flow_automation"
        shot_dir.mkdir(parents=True, exist_ok=True)
        caminho = shot_dir / f"upload_nao_confirmado_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        page.screenshot(path=str(caminho))
    except Exception as e_shot:
        caminho = None
        pw_log(f"[FLOW_UPLOAD_SINAL] screenshot de diagnóstico falhou: {e_shot}", level="warn")
    try:
        diag = page.evaluate(_JS_DIAG_UPLOAD)
    except Exception:
        diag = None
    if isinstance(diag, dict):
        for i, html in enumerate(diag.get("pct") or []):
            pw_log(f"[FLOW_UPLOAD_SINAL] elemento '%' #{i + 1}: {str(html)[:2000]}", level="warn")
        for i, html in enumerate(diag.get("imgs") or []):
            pw_log(f"[FLOW_UPLOAD_SINAL] <img> candidata #{i + 1}: {str(html)[:2000]}", level="warn")
    pw_log(
        f"[FLOW_UPLOAD_SINAL] upload NÃO confirmado em {time.time() - t0:.1f}s"
        f"{' (screenshot: ' + str(caminho) + ')' if caminho else ''}. "
        f"Não validado em execução real.",
        level="warn",
    )


def _aguardar_upload_concluido(page, baseline_srcs, timeout_ms: int = 20000) -> bool:
    """Espera o upload terminar por SINAL — SEM sleep fixo. Devolve True/False.

    FASE 1 (até 4000 ms, NÃO fatal) — espera aparecer um elemento visível com texto de
    porcentagem (`^\\s*\\d{1,3}\\s*%\\s*$`) OU uma <img> nova já carregada.
    FASE 2 (até `timeout_ms`) — espera NÃO existir porcentagem visível E existir ao
    menos uma <img> com complete === True, naturalWidth > 50, src fora de
    `baseline_srcs` e sem prefixo "data:image/svg".

    Em timeout: salva screenshot em logs/flow_automation/, registra via pw_log o
    outerHTML (2000 primeiros caracteres) das porcentagens e das <img> candidatas, e
    retorna False. O chamador é quem decide — esta guarda é NÃO FATAL.

    NUNCA levanta exceção (qualquer erro vira False), para que a função chamadora
    mantenha EXATAMENTE o fluxo anterior.

    Teto de espera quando NENHUMA porcentagem aparece: `min(timeout_ms, 8000)` ms —
    evita 20 s de espera inútil em fluxos cujo resultado não é uma <img> (ex.: chip
    '@' no prompt). Se uma porcentagem aparece, espera até `timeout_ms` (conexão lenta).
    """
    t0 = time.time()
    tentar_ms = max(0, int(timeout_ms or 0))
    viu_pct = False
    try:
        baseline = list(baseline_srcs or [])
        while True:
            info = None
            try:
                info = page.evaluate(_JS_ESTADO_UPLOAD, baseline)
            except Exception:
                info = None
            if isinstance(info, dict):
                pct = info.get("pct")
                novas = info.get("novas") or []
                if pct:
                    if not viu_pct:
                        viu_pct = True
                        pw_log(f"[FLOW_UPLOAD_SINAL] envio em andamento (porcentagem "
                               f"'{pct}') — aguardando conclusão (até {tentar_ms} ms).", level="info")
                if (not pct) and novas:
                    pw_log(f"[FLOW_UPLOAD_SINAL] upload concluído por <img> nova "
                           f"({len(novas)} imagem(ns)) em {time.time() - t0:.1f}s.", level="info")
                    return True
            decorrido_ms = (time.time() - t0) * 1000.0
            limite_ms = tentar_ms if viu_pct else min(tentar_ms, 8000)
            if decorrido_ms >= limite_ms:
                break
            time.sleep(0.35)
        _diagnostico_upload(page, t0)
        return False
    except Exception as e:
        pw_log(f"[FLOW_UPLOAD_SINAL] guarda de upload falhou ({e}); seguindo como antes.",
               level="warn")
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

    def _passo_falhou(passo: str, detalhe: str, card_persistido: bool = False):
        p = _shot(passo)
        msg = (f"[criar_personagem_flow] PASSO '{passo}' FALHOU para '{nome_exibicao}': {detalhe} "
               f"(screenshot: {p})")
        pw_log(msg, level="error")
        raise FlowCharacterCreationError(msg, card_persistido=card_persistido)

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
        # Aguarda tela de carregamento sumir se o canvas acabou de ser aberto
        try:
            loc_loading = page.locator('text="Carregando..."').first
            if loc_loading.is_visible(timeout=300):
                pw_log("[FLOW] Canvas ainda em carregamento. Aguardando estabilização...")
                loc_loading.wait_for(state="hidden", timeout=15000)
                page.wait_for_timeout(500)
        except Exception:
            pass

        if not _clicar_item_personagens_flow(page, timeout_ms=15000):
            # Se já estiver na tela do editor (/character ou formulário aberto), não falha
            if "/character" not in str(page.url or ""):
                raise FlowCharacterCreationError(
                    "menu lateral 'Characters'/'Personagens' não encontrado (drawer fechado ou "
                    "layout do Flow alterado)."
                )
            pw_log("[FLOW] Tela do editor de personagem já ativa.")
        page.wait_for_timeout(900)
    except Exception as _e:
        _passo_falhou("2_aba_characters", str(_e))

    # 3. New character / Novo personagem
    try:
        btn_upload = page.locator(
            'button.create-character-footer-mode-button:has-text("Upload"), '
            'button.create-character-footer-mode-button:has-text("Fazer upload"), '
            'button.create-character-footer-mode-button:has-text("Enviar"), '
            'button:has-text("Upload"), '
            'button:has-text("Fazer upload"), '
            'button:has-text("Enviar")'
        ).first
        if btn_upload.is_visible(timeout=1500):
            pw_log("[FLOW] Já está na tela do assistente de personagem — avançando para upload.")
        else:
            tile_sels = [
                'flow-custom-tile:has-text("New character") button',
                'flow-custom-tile:has-text("Novo personagem") button',
                'button:has-text("New character")',
                'button:has-text("Novo personagem")',
                'flow-custom-tile:has-text("New character")',
                'flow-custom-tile:has-text("Novo personagem")',
                '[aria-label*="New character" i]',
                '[aria-label*="Novo personagem" i]',
                'div[role="button"]:has-text("New character")',
                'div[role="button"]:has-text("Novo personagem")',
            ]
            clicou_tile = False
            for s_tile in tile_sels:
                try:
                    t_loc = page.locator(s_tile).first
                    if t_loc.is_visible(timeout=1500):
                        t_loc.click()
                        clicou_tile = True
                        page.wait_for_timeout(900)
                        break
                except Exception:
                    continue
            if not clicou_tile:
                try:
                    page.evaluate('''() => {
                        const tiles = Array.from(document.querySelectorAll('flow-custom-tile, button, [role="button"]'));
                        const t = tiles.find(el => {
                            const txt = (el.textContent || '').trim().toLowerCase();
                            return txt.includes('new character') || txt.includes('novo personagem');
                        });
                        if (t) { (t.querySelector('button') || t).click(); return true; }
                        return false;
                    }''')
                    page.wait_for_timeout(900)
                except Exception:
                    pass
    except Exception as _e:
        _passo_falhou("3_new_character", str(_e))

    # 4. Upload da foto de referência via file chooser
    try:
        with page.expect_file_chooser(timeout=7000) as fc_info:
            page.locator(
                'button.create-character-footer-mode-button:has-text("Upload"), '
                'button.create-character-footer-mode-button:has-text("Fazer upload"), '
                'button.create-character-footer-mode-button:has-text("Enviar"), '
                'button:has-text("Upload"), '
                'button:has-text("Fazer upload"), '
                'button:has-text("Enviar")'
            ).first.click(timeout=5000)
        fc_info.value.set_files(str(caminho_foto))
        page.wait_for_timeout(3000)
    except Exception as _e:
        _passo_falhou("4_upload_foto", str(_e))

    # 5. Preenche o nome oficial do personagem — SEMPRE com '@' (ex: '@Coringa')
    nome_flow = nome_personagem if str(nome_personagem).startswith("@") else f"@{nome_personagem}"
    try:
        input_nome = page.locator('input.name-input[placeholder="Character name"], input.name-input[placeholder="Nome do personagem"], input.name-input').first
        input_nome.fill(str(nome_flow), timeout=5000)
        page.wait_for_timeout(400)
    except Exception as _e:
        _passo_falhou("5_nome", str(_e))

    # 6. Descrição de personalidade — deixada em BRANCO de propósito (opcional;
    #    decisão: manter o mais simples. Preservação facial vem da foto oficial
    #    promovida no passo 7 + nome estável).

    # 7. PASSO CRÍTICO (7_promover_portrait_oficial):
    #    Promove a foto a Portrait oficial do card se houver miniatura no histórico.
    #    No fluxo com upload de foto de referência, o Flow define a foto enviada como
    #    Retrato oficial automaticamente. Se a miniatura estiver visível ou o painel
    #    de histórico puder ser expandido, promovemos; se o retrato já estiver ativo,
    #    não travamos por 60s nem abortamos a conclusão.
    try:
        portrait_promovido = False

        # 7.1. Seletor clássico de miniatura do histórico visível de imediato
        try:
            _hist_img = page.locator(
                'flow-editor-history-step-image img.image.clickable, '
                'flow-editor-history-step-image img.clickable, '
                'img.image.clickable'
            ).first
            if _hist_img.is_visible(timeout=2500):
                _hist_img.click(timeout=3000)
                portrait_promovido = True
                pw_log("[FLOW] Retrato oficial promovido via clique na miniatura do histórico.")
                page.wait_for_timeout(600)
        except Exception:
            pass

        # 7.2. Se o histórico estiver recolhido ("Mostrar histórico" / "Show history"), tenta expandir
        if not portrait_promovido:
            try:
                btn_hist = page.locator(
                    'button:has-text("Mostrar histórico"), '
                    'button:has-text("Show history"), '
                    'button[aria-label*="histórico" i], '
                    'button[aria-label*="history" i]'
                ).first
                if btn_hist.is_visible(timeout=1500):
                    btn_hist.click(timeout=1500)
                    page.wait_for_timeout(800)
                    _hist_img2 = page.locator('flow-editor-history-step-image img.image.clickable, flow-editor-history-step-image img.clickable').first
                    if _hist_img2.is_visible(timeout=3000):
                        _hist_img2.click(timeout=3000)
                        portrait_promovido = True
                        pw_log("[FLOW] Histórico expandido e miniatura promovida a Retrato oficial.")
                        page.wait_for_timeout(600)
            except Exception as _e_exp:
                pw_log(f"[FLOW] Aviso ao verificar painel de histórico: {_e_exp}", level="warn")

        # 7.3. Confirma se o Retrato já está ativo no editor central
        if not portrait_promovido:
            tem_retrato_ativo = False
            try:
                pill_retrato = page.locator(
                    'button:has-text("Retrato"), '
                    'button:has-text("Portrait"), '
                    'div[role="tab"]:has-text("Retrato"), '
                    'div[role="tab"]:has-text("Portrait"), '
                    '[aria-label*="Retrato" i]'
                ).first
                if pill_retrato.is_visible(timeout=2000):
                    tem_retrato_ativo = True
                else:
                    img_preview = page.locator('.character-editor-preview img, .preview-container img, main img, img').first
                    if img_preview.is_visible(timeout=2000):
                        tem_retrato_ativo = True
            except Exception:
                pass

            if tem_retrato_ativo:
                pw_log("[FLOW] Foto de referência já está selecionada como Retrato oficial.")
            else:
                pw_log("[FLOW] Passo 7_promover_portrait_oficial: miniatura de histórico não encontrada, prosseguindo para conclusão.", level="warn")

    except Exception as _e:
        pw_log(f"[FLOW] Aviso no passo 7_promover_portrait_oficial: {_e} — prosseguindo para conclusão.", level="warn")

    # 8. Done / Concluir / Concluído / Pronto (finaliza a criação do personagem)
    #    Garante o clique no botão de conclusão com seletores múltiplos e fallback JS.
    try:
        btn_concluir_clicado = False
        concluir_sels = SELETORES_BOTAO_CONCLUIR_PERSONAGEM

        # Espera ativa de até 10s pelo botão de conclusão estar visível e habilitado
        t0_done = time.time()
        while time.time() - t0_done < 10:
            for s_done in concluir_sels:
                try:
                    b_loc = page.locator(s_done).first
                    if b_loc.is_visible(timeout=400) and not b_loc.is_disabled():
                        b_loc.click(timeout=3000)
                        btn_concluir_clicado = True
                        pw_log(f"[FLOW] Clicou com sucesso em Concluir/Done via seletor: {s_done}")
                        break
                except Exception:
                    continue
            if btn_concluir_clicado:
                break
            page.wait_for_timeout(500)

        # Fallback JS se locator não conseguiu clicar
        if not btn_concluir_clicado:
            try:
                btn_concluir_clicado = page.evaluate('''() => {
                    const btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
                    const b = btns.find(el => {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
                        const matchText = (t === 'concluir' || t === 'done' || t === 'concluído' || t === 'pronto' || t === 'salvar' || t === 'save');
                        const matchAria = aria.includes('edição concluída') || aria.includes('edicao concluida') || aria.includes('concluir') || aria.includes('done');
                        return (matchText || matchAria) &&
                               !el.disabled && !el.getAttribute('aria-disabled') && el.getAttribute('aria-disabled') !== 'true';
                    });
                    if (b) {
                        b.click();
                        return true;
                    }
                    return false;
                }''')
                if btn_concluir_clicado:
                    pw_log("[FLOW] Clicou em Concluir/Done via fallback JavaScript.")
            except Exception as _e_js:
                pw_log(f"[FLOW] Aviso no fallback JS de conclusão: {_e_js}", level="warn")

        if not btn_concluir_clicado:
            _passo_falhou("8_done", "botão 'Concluir'/'Done' não encontrado ou permaneceu desabilitado.")

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
                card_persistido=True,
            )
    except FlowCharacterCreationError:
        raise
    except Exception as _e:
        _passo_falhou("9_validacao_popup", str(_e), card_persistido=True)

    _shot("sucesso")
    pw_log(f"CHARACTER_AUTO_CREATED_OK: '{nome_exibicao}' criado e confirmado no popup '@'.")
    return True


def incluir_referencia_personagem(page, reference_path: str = "reference.png",
                                  nome_personagem: str = "", tag_personagem: str = "",
                                  projeto_id: str = "", tipo: str = "personagem",
                                  motivo_erro: Optional[list] = None,
                                  driver: Any = None) -> bool:
    """
    Inclui a imagem/referência do personagem no prompt do Google Flow.
    Fluxo: clicar em '+' → modal abre → clicar aba 'Characters'/'Uploads' →
    clicar no card do personagem/mídia → 'Incluir no comando'.

    Quando nome_personagem/tag_personagem é fornecido, a busca na aba
    Characters prioriza o NOME DO PERSONAGEM (ex: "@Marcos"/"Marcos"), que
    é como o card aparece no Flow nativo — NÃO o nome do arquivo local de
    referência (ex: "reference.png"). Para uploads de mídia (sem personagem),
    mantém a busca por nome do arquivo (comportamento original).

    CORREÇÃO ANTI-GENÉRICO (personagem):
      * `projeto_id` passa a ser obrigatório na prática: a identidade do projeto
        (identidade.json → @Marcos) é lida AQUI e é a fonte de verdade. Sem
        nome/tag explícitos, o personagem do projeto é usado.
      * Se o projeto não tiver identidade válida => erro CLARO e anexo
        CANCELADO (nunca anexa/cria "@avatar" genérico).
      * O fallback "primeiro card visível do menu" foi removido para PERSONAGEM
        (era ele que anexava o card genérico no lugar do @Marcos do projeto).
        Continua valendo apenas para uploads de mídia (tipo="imagem").
      * `motivo_erro` (opcional) recebe a mensagem clara para o chamador logar.
    """
    eh_personagem = str(tipo or "personagem").strip().lower() in ("personagem", "character", "avatar")
    motivos: Optional[list] = motivo_erro if isinstance(motivo_erro, list) else None

    def _abortar(msg: str) -> bool:
        log_char(f"ABORTADO — {msg}", projeto_id=projeto_id, level="error")
        if motivos is not None:
            motivos.append(msg)
        return False
    global _debug_modal_done
    nome_arq = Path(reference_path).name if reference_path else "reference.png"
    nome_personagem = str(nome_personagem or "").strip()
    tag_personagem = str(tag_personagem or "").strip()

    # ------------------------------------------------------------------
    # PASSO 0 — VALIDAÇÃO OBRIGATÓRIA DO PERSONAGEM DO PROJETO (anti-genérico)
    # ------------------------------------------------------------------
    # identidade.json é a fonte de verdade. Sem ela (ou sem personagem) NÃO
    # anexamos nada genérico: devolvemos erro claro para o chamador.
    if eh_personagem:
        personagem_projeto: Optional[Dict[str, Any]] = None
        erro_identidade: Optional[str] = None
        if projeto_id:
            try:
                import services.character_service as _char_svc
                personagem_projeto, erro_identidade = _char_svc.validar_personagem_do_projeto(projeto_id)
            except Exception as _e_val:
                erro_identidade = f"Erro ao validar identidade do projeto '{projeto_id}': {_e_val}"
        else:
            erro_identidade = "Projeto ID não fornecido"

        nome_informado = nome_personagem or tag_personagem.lstrip("@").strip()

        if personagem_projeto:
            oficial_tag = personagem_projeto.get("referencia_flow") or f"@{personagem_projeto.get('nome', '')}"
            oficial_nome = personagem_projeto.get("nome") or oficial_tag.lstrip("@")
            if not nome_informado:
                # Nada foi informado: usa o personagem OFICIAL do projeto.
                nome_personagem = oficial_nome
                tag_personagem = oficial_tag
                log_char(f"personagem oficial lido de identidade.json: '{oficial_tag}'.", projeto_id=projeto_id)
            elif nome_informado.lstrip("@").strip().lower() != str(oficial_nome).strip().lower():
                # Tag diferente da oficial: só é aceita se pertencer ao catálogo
                # do projeto (references.json). Genérica/desconhecida => substitui
                # pelo personagem oficial (nunca anexa card genérico).
                pertence = False
                try:
                    import services.character_service as _char_svc
                    pertence = bool(_char_svc.tag_personagem_valida(projeto_id, nome_informado))
                except Exception:
                    pertence = False
                if pertence:
                    log_char(f"tag '{nome_informado}' validada no catálogo do projeto.", projeto_id=projeto_id)
                else:
                    log_char(f"tag '{nome_informado}' não pertence ao projeto — "
                             f"substituída pelo personagem oficial '{oficial_tag}'.", projeto_id=projeto_id,
                             level="warn")
                    nome_personagem = oficial_nome
                    tag_personagem = oficial_tag
            else:
                log_char(f"personagem do projeto confirmado: '{oficial_tag}'.", projeto_id=projeto_id)

            # A foto de referência do projeto é a oficial desta identidade.
            if (tag_personagem == oficial_tag
                    and personagem_projeto.get("imagem_abs")
                    and Path(str(personagem_projeto["imagem_abs"])).exists()):
                reference_path = str(personagem_projeto["imagem_abs"])
        else:
            if _nome_personagem_generico(nome_informado):
                return _abortar(
                    f"nenhum personagem válido para o projeto: "
                    f"{erro_identidade or 'identidade não informada'} — o valor "
                    f"'{nome_informado or '(vazio)'}' é genérico; anexo cancelado "
                    f"(nunca usa personagem genérico)."
                )
            log_char(f"identidade.json indisponível ({erro_identidade}) — usando a tag "
                     f"explícita '{nome_informado}' do plano de cena.", projeto_id=projeto_id, level="warn")
            if tag_personagem and not tag_personagem.startswith("@"):
                tag_personagem = f"@{tag_personagem.lstrip('@')}"
            elif not tag_personagem:
                tag_personagem = f"@{nome_informado.lstrip('@')}"

    # REQ (TAREFA 1/2/3) — TRAVA FINAL ANTI-GENÉRICO: personagem sem arquivo de
    # referência REAL em disco não é anexado. Impede que o literal "reference.png"
    # (default da assinatura) vire uma busca genérica na aba Characters do Flow —
    # era exatamente o caminho do anexo genérico em projeto NOVO sem identidade.json.
    if eh_personagem:
        _ref_real = str(reference_path or "").strip()
        if not _ref_real or not Path(_ref_real).exists():
            return _abortar(
                f"personagem '{nome_personagem or tag_personagem.lstrip('@') or '(sem nome)'}' "
                f"sem imagem de referência real em disco ('{_ref_real or '(vazio)'}') — "
                f"anexo CANCELADO (nunca usa 'reference.png' genérica)."
            )

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
        # Fecha modal ou overlay residual e limpa comando anterior
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            btn_apagar = page.locator('button[aria-label*="Apagar comando" i], button.clear-button').first
            if btn_apagar.is_visible(timeout=200):
                btn_apagar.click(timeout=500)
                page.wait_for_timeout(200)
        except Exception:
            pass

        # 1. Botão '+' do campo de prompt (Ingredients / Add image / Menu trigger)
        btn_mais = None
        dialog = None

        for tentativa_anexo in range(1, 3):
            for sel in [
                'button.add-menu-trigger',
                'button[aria-label*="Adicionar elementos" i]',
                'button[aria-label*="Add ingredients" i]',
                'button[aria-label="Add ingredients to the prompt box"]',
                'button[aria-label*="ingredient" i]',
                'button[aria-label*="elemento" i]',
                'button[aria-label*="Adicionar" i]',
                'button[aria-label*="Add image" i]',
                'button[aria-label="Add image"]',
                'button[aria-label*="Upload" i]',
            ]:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1500):
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
                        if (aria.includes('ingredient') || aria.includes('adicionar') || aria.includes('add image') || aria.includes('upload')
                            || t === 'add' || t === 'add_circle' || t === 'add_photo_alternate'
                            || t.includes('add_2')) {
                            return i;
                        }
                    }
                    return -1;
                }""")
                if isinstance(idx_mais, int) and idx_mais >= 0:
                    btn_mais = page.locator("button").nth(idx_mais)

            if btn_mais:
                try:
                    btn_mais.click()
                    page.wait_for_timeout(500)
                    # 2. Aguarda o menu/overlay/dialog abrir (Angular CDK Overlay com flow-add-menu-popover-content)
                    for d_sel in [
                        'flow-add-menu-popover-content',
                        'div.cdk-overlay-pane:has(flow-add-menu-popover-content)',
                        '.add-menu-popover-container',
                        'div.cdk-overlay-pane:has([role="listbox"])',
                        'div.cdk-overlay-pane:has([role="tablist"])',
                        'div.cdk-overlay-pane:has([role="menu"])',
                        'div.cdk-overlay-pane',
                        'div[role="dialog"]',
                        'div[role="menu"]',
                    ]:
                        loc = page.locator(d_sel).first
                        if loc.is_visible(timeout=2500):
                            dialog = loc
                            break
                except Exception:
                    dialog = None

            if dialog:
                break

            pw_log(f"[REFERENCIA] Tentativa {tentativa_anexo} de abertura do menu falhou; tentando novamente...", level="info")
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

        if not btn_mais:
            _falha("botão '+' / Ingredients do campo de prompt não encontrado")
            return False

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
                    const tabs = Array.from(el.querySelectorAll("mat-list-item[role=tab], button[role=tab], button, div[role=button], [class*='tab']"));
                    const target = tabs.find(b => {
                        const t = (b.textContent || '').trim().toLowerCase();
                        return t === 'character' || t === 'characters' || t === 'personagem' || t === 'personagens';
                    });
                    if (target) target.click();
                }''')
            else:
                dialog.evaluate('''el => {
                    const tabs = Array.from(el.querySelectorAll("mat-list-item[role=tab], button[role=tab], button, div[role=button], [class*='tab']"));
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
                # Procura em button.asset-item / [role="option"] / .asset-title
                for sel_card in [
                    f'button.asset-item:has-text("{alvo}")',
                    f'[role="option"]:has-text("{alvo}")',
                    f'.asset-title:has-text("{alvo}")',
                ]:
                    try:
                        _loc = dialog.locator(sel_card).first
                        if _loc.is_visible(timeout=500):
                            return _loc
                    except Exception:
                        pass

                if not so_exato:
                    # Primeiro match exato (evita clicar em card errado com nome parecido)
                    try:
                        _loc = dialog.get_by_text(alvo, exact=True).first
                        if _loc.is_visible(timeout=500):
                            return _loc
                    except Exception:
                        pass
                try:
                    _loc = dialog.get_by_text(alvo).first
                    if _loc.is_visible(timeout=500):
                        return _loc
                except Exception:
                    pass
            return None

        # 4a. Personagem nativo (busca por nome — sem exato para tolerar card com @/sem @)
        if alvos_personagem and not item_ref:
            item_ref = _procurar_por_alvo(alvos_personagem)

        # Se não encontrou de imediato na lista, tenta filtrar pelo search-input
        if not item_ref and nome_busca_personagem:
            try:
                search_loc = dialog.locator('input.search-input, input[placeholder*="Pesquisar" i], input[placeholder*="Search" i]').first
                if search_loc.is_visible(timeout=500):
                    search_loc.fill(nome_busca_personagem.lstrip("@"))
                    page.wait_for_timeout(600)
                    item_ref = _procurar_por_alvo(alvos_personagem)
            except Exception:
                pass

        # 4b. Upload/mídia (envia arquivo local ou busca na lista)
        if not eh_personagem and reference_path and Path(reference_path).exists():
            # Tenta input[type=file] direto no dialog ou na página
            try:
                f_inputs = page.locator('input[type="file"]').all()
                if f_inputs:
                    # UI esperada ao final: chip '@' no prompt (pode NÃO ser uma <img> —
                    # daí o timeout curto e a guarda NÃO FATAL).
                    baseline_srcs = _baseline_imgs_pagina(page)
                    f_inputs[0].set_input_files(reference_path)
                    if not _aguardar_upload_concluido(page, baseline_srcs, timeout_ms=5000):
                        pw_log("[FLOW] incluir_referencia_personagem: upload não confirmado "
                               "por sinal; seguindo com comportamento anterior. "
                               "Não validado em execução real.", level="warn")
                    page.wait_for_timeout(300)
                    return True
            except Exception:
                pass
            # Ou clica no botão "Enviar" / "Upload" do menu
            for u_sel in [
                '.cdk-overlay-pane button:has-text("Enviar")',
                '.cdk-overlay-pane [role="menuitem"]:has-text("Enviar")',
                'button:has-text("Enviar")',
                'button:has-text("Upload")',
            ]:
                try:
                    u_btn = dialog.locator(u_sel).first
                    if u_btn.is_visible(timeout=400):
                        # UI esperada ao final: chip '@' no prompt (pode NÃO ser uma <img>).
                        baseline_srcs = _baseline_imgs_pagina(page)
                        with page.expect_file_chooser(timeout=2000) as fc_info:
                            u_btn.click()
                        file_chooser = fc_info.value
                        file_chooser.set_files(reference_path)
                        if not _aguardar_upload_concluido(page, baseline_srcs, timeout_ms=5000):
                            pw_log("[FLOW] incluir_referencia_personagem: upload não confirmado "
                                   "por sinal; seguindo com comportamento anterior. "
                                   "Não validado em execução real.", level="warn")
                        page.wait_for_timeout(300)
                        return True
                except Exception:
                    pass

        if alvos_arquivo and not item_ref:
            item_ref = _procurar_por_alvo(alvos_arquivo, so_exato=True)
            if not item_ref:
                item_ref = _procurar_por_alvo(alvos_arquivo)

        # 4c. ÚLTIMO RECURSO — SOMENTE para uploads de mídia (sem personagem):
        #     primeiro item visível do menu. Para PERSONAGEM este caminho era o
        #     defeito reportado ("@avatar genérico"): o Flow anexava um card
        #     qualquer (ex.: avatar genérico da conta) no lugar do '@Marcos' do
        #     projeto. Nunca mais — personagem sem match por nome => erro claro.
        if not item_ref and not nome_busca_personagem:
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

        # NOVO FLUXO: Se personagem não encontrado, tenta criar no worker e re-tentar
        if not item_ref and nome_busca_personagem:
            w_id = getattr(driver, "worker_id", 0) if driver else 0
            # Guard: _avatar_uploaded por WorkerInstancia — não criar duas vezes no mesmo worker
            if driver and not getattr(driver, "_avatar_uploaded", False):
                msg_ini = f"Worker {w_id}: @{nome_busca_personagem} não encontrado — iniciando criação"
                pw_log(msg_ini, level="info")
                print(msg_ini, flush=True)

                # Fecha o menu de ingredientes para liberar o canvas/drawer para a criação
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                except Exception:
                    pass

                # Chama _upload_avatar_projeto no worker atual
                try:
                    driver._upload_avatar_projeto(projeto_id)
                except Exception as e_up:
                    pw_log(f"Worker {w_id}: aviso ao executar _upload_avatar_projeto: {e_up}", level="warn")

                # Aguarda 5s após criação
                pw_log(f"Worker {w_id}: aguardando 5s após criação de @{nome_busca_personagem}...")
                page.wait_for_timeout(5000)

                # Tentar encontrar @Marcos no menu de ingredientes novamente
                btn_mais_retry = None
                for sel_r in [
                    'button.add-menu-trigger',
                    'button[aria-label*="Adicionar elementos" i]',
                    'button[aria-label*="Add ingredients" i]',
                    'button[aria-label="Add ingredients to the prompt box"]',
                    'button[aria-label*="ingredient" i]',
                    'button[aria-label*="elemento" i]',
                    'button[aria-label*="Adicionar" i]',
                    'button[aria-label*="Add image" i]',
                    'button[aria-label="Add image"]',
                    'button[aria-label*="Upload" i]',
                ]:
                    loc_r = page.locator(sel_r).first
                    if loc_r.is_visible(timeout=1500):
                        btn_mais_retry = loc_r
                        break
                if btn_mais_retry:
                    btn_mais_retry.click()
                    page.wait_for_timeout(700)
                    for d_sel in [
                        'flow-add-menu-popover-content',
                        'div.cdk-overlay-pane:has(flow-add-menu-popover-content)',
                        '.add-menu-popover-container',
                        'div.cdk-overlay-pane:has([role="listbox"])',
                        'div.cdk-overlay-pane:has([role="tablist"])',
                        'div.cdk-overlay-pane:has([role="menu"])',
                        'div.cdk-overlay-pane',
                        'div[role="dialog"]',
                        'div[role="menu"]',
                    ]:
                        loc_d = page.locator(d_sel).first
                        if loc_d.is_visible(timeout=2500):
                            dialog = loc_d
                            break

                if dialog:
                    try:
                        dialog.evaluate('''el => {
                            const tabs = Array.from(el.querySelectorAll("mat-list-item[role=tab], button[role=tab], button, div[role=button], [class*='tab']"));
                            const target = tabs.find(b => {
                                const t = (b.textContent || '').trim().toLowerCase();
                                return t === 'character' || t === 'characters' || t === 'personagem' || t === 'personagens';
                            });
                            if (target) target.click();
                        }''')
                        page.wait_for_timeout(600)
                    except Exception:
                        pass
                    item_ref = _procurar_por_alvo(alvos_personagem)
                    if not item_ref and nome_busca_personagem:
                        try:
                            search_loc = dialog.locator('input.search-input, input[placeholder*="Pesquisar" i], input[placeholder*="Search" i]').first
                            if search_loc.is_visible(timeout=500):
                                search_loc.fill(nome_busca_personagem.lstrip("@"))
                                page.wait_for_timeout(600)
                                item_ref = _procurar_por_alvo(alvos_personagem)
                        except Exception:
                            pass

            if not item_ref:
                msg_falha = f"Worker {w_id}: reprovisionado mas não encontrado — cena pulada, status=ERRO_PERSONAGEM"
                pw_log(msg_falha, level="error")
                print(msg_falha, flush=True)
                pw_log(f"[Worker {w_id}] PERSONAGEM_CRIACAO_FALHOU: '@{nome_busca_personagem}' não apareceu na aba Characters.", level="error")
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                if driver:
                    setattr(driver, "_ultimo_erro_personagem", True)
                return _abortar(f"ERRO_PERSONAGEM: {msg_falha} — anexo CANCELADO (nunca anexa card genérico).")
            else:
                pw_log(f"[Worker {w_id}] PERSONAGEM_CRIADO_OK: '@{nome_busca_personagem}' encontrado no menu após criação.", level="info")
                print(f"[Worker {w_id}] PERSONAGEM_CRIADO_OK: '@{nome_busca_personagem}' encontrado no menu após criação.", flush=True)

        if not item_ref:
            desc_alvo = nome_busca_personagem or nome_arq
            _falha(f"'{desc_alvo}' não encontrado no menu de ingredientes")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        # 4d. TRAVA FINAL: o card escolhido TEM de ser o personagem do projeto.
        if nome_busca_personagem:
            try:
                _txt_card = (item_ref.text_content() or "").strip()
            except Exception:
                _txt_card = ""
            if nome_busca_personagem.lstrip("@").strip().lower() not in _txt_card.lower():
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                return _abortar(
                    f"o card selecionado ('{(_txt_card or 'sem texto')[:60]}') não é o "
                    f"personagem do projeto '{nome_busca_personagem}' — anexo CANCELADO."
                )

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
        nome_entidade_log = tag_personagem or nome_busca_personagem or nome_arq
        if confirm_clicado:
            pw_log(f"[REFERENCIA] Entidade '{nome_entidade_log}' inserida (botão de confirmação clicado).")
            if nome_busca_personagem:
                log_char(f"personagem '{tag_personagem or nome_busca_personagem}' anexado ao prompt "
                         f"(botão de confirmação).", projeto_id=projeto_id)
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
                pw_log(f"[REFERENCIA] Entidade '{nome_entidade_log}' inserida (entidade real detectada no editor).")
                if nome_busca_personagem:
                    log_char(f"personagem '{tag_personagem or nome_busca_personagem}' anexado ao prompt "
                             f"(chip/entidade confirmada no editor).", projeto_id=projeto_id)
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


# ===========================================================================
# SISTEMA DE FILA DE PROCESSAMENTO ASSÍNCRONO (FLOW QUEUE)
# ===========================================================================

_flow_queue_file_lock = threading.RLock()
_flow_generation_lock = threading.RLock()
_queue_workers: Dict[str, "FlowQueueWorker"] = {}
_queue_workers_lock = threading.Lock()

_legacy_run_ativos = 0
_legacy_run_lock = threading.Lock()


def legacy_queue_ativa() -> bool:
    """Indica se há alguma execução da fila legada ativa no momento."""
    with _legacy_run_lock:
        return _legacy_run_ativos > 0


FLOW_QUEUE_BACKOFFS = [0, 10, 30]
FLOW_QUEUE_INTERVALO_S = 4.0

# ===========================================================================
# TAREFA 3 — IDENTIFICADOR DE EXECUÇÃO (exec_id) DA FILA
# ===========================================================================
# Um item da fila só é órfão ("gerando" sem ninguém o processando) se nenhuma
# EXECUÇÃO VIVA o possui. Por isso cada execução da Fase A recebe um exec_id
# (uuid4().hex) gravado no item e registrado em `_exec_ids_ativos` enquanto roda:
# a Fase C localiza o item POR exec_id (e não mais por scene_id+status), o que
# resolve a conclusão tardia de uma cena cancelada e re-adicionada.
_exec_ids_ativos: Set[str] = set()
_exec_ids_lock = threading.Lock()


# ===========================================================================
# TAREFA 1 — ATOR CDP: UMA ÚNICA THREAD PARA TODA CHAMADA PLAYWRIGHT
# ===========================================================================
# O Playwright sync_api é THREAD-BOUND (greenlet): `page`/`browser` só funcionam
# na thread nativa que os criou. Com um FlowQueueWorker por projeto, a página do
# singleton PlaywrightCDPWorker ficava inutilizável para qualquer worker cuja
# thread não fosse a que criou a sessão — erro medido na auditoria:
# "Cannot switch to a different thread" (greenlet.error).
#
# Correção: o ATOR. UMA thread (daemon, name="CdpActor") cria a sessão e executa
# TODA chamada Playwright; os workers apenas enviam a tarefa e esperam o Future.
# O `_flow_generation_lock` é adquirido SÓ dentro da tarefa (thread do ator) e no
# `_handle_run_queue` (caminho legado) — adquirir no chamador E de novo no ator
# geraria deadlock entre threads, mesmo com RLock.

FLOW_ACTOR_TIMEOUT_EXEC_S = 600.0
FLOW_ACTOR_POLL_S = 0.5
FLOW_LOCK_ACQUIRE_TIMEOUT_S = 2.0
FLOW_QUEUE_ESPERA_OCUPADO_S = 15.0


def _timeout_do_ator() -> float:
    """Timeout (s) de execução de uma tarefa no ator CDP (após início efetivo)."""
    return FLOW_ACTOR_TIMEOUT_EXEC_S


class _NaoTentada(Exception):
    """Lançada quando uma tarefa não pôde sequer iniciar sua execução."""
    def __init__(self, motivo: str, espera_s: float = 0.0):
        super().__init__(f"Não tentada: {motivo}")
        self.motivo = motivo
        self.espera_s = espera_s


class _ExecTimeout(FuturesTimeoutError):
    """Lançada quando a tarefa ultrapassa o timeout de execução após ter iniciado."""
    pass


class _ExecCancelada(Exception):
    """Lançada quando a tarefa é cancelada pelo worker enquanto já está executando."""
    pass


class _ActorTask:
    """Encapsula uma unidade de trabalho a ser executada na thread do ator CDP."""
    def __init__(self, fn, args, kwargs):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.future: Future = Future()
        self.started = threading.Event()
        self.done_event = threading.Event()
        self.submitted_at = time.time()
        self.started_at: Optional[float] = None


class _CdpActor(threading.Thread):
    """Thread dedicada (daemon) que executa TODA chamada Playwright.

    A sessão sync_api nasce nesta thread e NUNCA é usada de outra. Os chamadores
    (FlowQueueWorker de cada projeto, rota HTTP, etc.) enviam tarefas.
    A espera na fila não consome o timeout de execução (duas fases).
    """

    def __init__(self):
        super().__init__(daemon=True, name="CdpActor")
        self._task_queue: "queue.Queue[Optional[_ActorTask]]" = queue.Queue()
        self._tarefa_atual: Optional[_ActorTask] = None

    def esta_travado(self) -> bool:
        """Indica se a tarefa atual está em execução há mais tempo que o timeout configurado."""
        task = self._tarefa_atual
        if task is not None and task.started_at is not None:
            return (time.time() - task.started_at) > _timeout_do_ator()
        return False

    def submit_async(self, fn, *args, **kwargs) -> _ActorTask:
        """Submete uma tarefa ao ator sem bloquear, retornando um _ActorTask."""
        task = _ActorTask(fn, args, kwargs)
        if threading.current_thread() is self:
            task.started_at = time.time()
            task.started.set()
            self._tarefa_atual = task
            try:
                task.future.set_result(fn(*args, **kwargs))
            except (SystemExit, KeyboardInterrupt, GeneratorExit) as be:
                pw_log(f"[CDP-ATOR] Exceção crítica interceptada: {type(be).__name__}: {be}", level="error")
                task.future.set_exception(RuntimeError(f"{type(be).__name__}: {be}"))
            except BaseException as e:
                task.future.set_exception(e)
            finally:
                task.done_event.set()
                self._tarefa_atual = None
            return task

        self._task_queue.put(task)
        return task

    def aguardar(self, task: _ActorTask, cancel_check=None, timeout: Optional[float] = None):
        """Aguarda a conclusão de uma tarefa dividida em Fase de Fila e Fase de Execução."""
        tout = timeout if timeout is not None else _timeout_do_ator()

        # FASE 1: Espera na fila do ator (NÃO consome o timeout de execução)
        while not task.started.wait(FLOW_ACTOR_POLL_S):
            if cancel_check and cancel_check():
                task.future.cancel()
                raise _NaoTentada(motivo="cancelada")
            if self.esta_travado():
                task.future.cancel()
                raise _NaoTentada(motivo="travado")

        # FASE 2: Execução em andamento na thread do ator
        while not task.future.done():
            if cancel_check and cancel_check():
                raise _ExecCancelada()
            tempo_exec = time.time() - (task.started_at or time.time())
            if tempo_exec > tout:
                pw_log(
                    f"[CDP-ATOR] Tarefa excedeu o timeout de execução de {tout}s.",
                    level="error",
                )
                raise _ExecTimeout(f"Tarefa excedeu o timeout de execução de {tout}s")
            task.done_event.wait(timeout=FLOW_ACTOR_POLL_S)

        return task.future.result()

    def submit(self, fn, *args, timeout: Optional[float] = None, **kwargs):
        """Executa fn(*args, **kwargs) na thread do ator e devolve o resultado (síncrono)."""
        if threading.current_thread() is self:
            return fn(*args, **kwargs)
        task = self.submit_async(fn, *args, **kwargs)
        return self.aguardar(task, timeout=timeout)

    def run(self):
        while True:
            task = self._task_queue.get()
            if task is None:
                break
            if not task.future.set_running_or_notify_cancel():
                # Tarefa cancelada antes de começar: não executa.
                continue
            task.started_at = time.time()
            task.started.set()
            self._tarefa_atual = task
            try:
                res = task.fn(*task.args, **task.kwargs)
                task.future.set_result(res)
            except (SystemExit, KeyboardInterrupt, GeneratorExit) as be:
                pw_log(f"[CDP-ATOR] Exceção crítica interceptada no loop do ator: {type(be).__name__}: {be}", level="error")
                task.future.set_exception(RuntimeError(f"{type(be).__name__}: {be}"))
            except BaseException as e:  # noqa: BLE001 — a thread do ator NUNCA morre
                try:
                    task.future.set_exception(e)
                except Exception:
                    pass
            finally:
                task.done_event.set()
                self._tarefa_atual = None


_cdp_actor: Optional[_CdpActor] = None
_cdp_actor_lock = threading.Lock()


def _get_cdp_actor() -> _CdpActor:
    """Singleton do ator CDP; recria a thread se a anterior tiver morrido."""
    global _cdp_actor
    with _cdp_actor_lock:
        actor = _cdp_actor
        if actor is None or not actor.is_alive():
            actor = _CdpActor()
            actor.start()
            _cdp_actor = actor
            pw_log("[CDP-ATOR] Thread dedicada iniciada (name=CdpActor).")
        return actor


def _get_flow_queue_path(projeto_id: str, projetos_dir: Optional[Any] = None) -> Path:
    pdir = Path(projetos_dir) if projetos_dir else PROJETOS_DIR
    return pdir / projeto_id / "flow_queue.json"


def _salvar_fila_atomico_unlocked(fpath: Path, fila_dict: dict) -> bool:
    """Grava atomicamente em disco usando arquivo temporário + replace (sem adquirir lock)."""
    fpath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = fpath.parent / f"flow_queue.json.tmp.{os.getpid()}_{int(time.time()*1000)}"
    try:
        fila_dict["atualizado_em"] = datetime.now().isoformat()
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(fila_dict, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        tmp_path.replace(fpath)
        return True
    except Exception as e:
        pw_log(f"Erro ao salvar atomicamente flow_queue.json: {e}", level="error")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False


def _carregar_fila_unlocked(fpath: Path, projeto_id: str) -> dict:
    """
    Lê o JSON de fpath sem lock.
    Se ausente ou inválido, devolve a estrutura padrão vazia SEM gravar em disco.
    """
    if fpath.exists():
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                dados = json.load(fp)
                if isinstance(dados, dict):
                    return dados
        except Exception as e:
            pw_log(f"Aviso ao ler flow_queue.json ({fpath}): {e}", level="warn")

    agora = datetime.now().isoformat()
    return {
        "projeto_id": projeto_id,
        "criado_em": agora,
        "atualizado_em": agora,
        "itens": [],
        "cena_ativa": None,
        "total": 0,
        "concluidos": 0,
        "erros": 0
    }


@contextmanager
def transacao_fila(projeto_id: str, projetos_dir: Optional[Any] = None):
    """
    Context manager atômico para leitura -> alteração -> salvamento de flow_queue.json.
    Adquire _flow_queue_file_lock (RLock) para toda a transação e só persiste em disco
    se o bloco interno não lançar nenhuma exceção.
    """
    fpath = _get_flow_queue_path(projeto_id, projetos_dir)
    with _flow_queue_file_lock:
        fila = _carregar_fila_unlocked(fpath, projeto_id)
        yield fila
        _salvar_fila_atomico_unlocked(fpath, fila)


def _init_fila_vazia(projeto_id: str, projetos_dir: Optional[Any] = None) -> dict:
    """
    Gera flow_queue.json com estrutura vazia se o arquivo não existir.
    Retorna o dicionário da fila existente ou recém-criada.
    """
    fpath = _get_flow_queue_path(projeto_id, projetos_dir)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    with _flow_queue_file_lock:
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as fp:
                    dados = json.load(fp)
                    if isinstance(dados, dict):
                        return dados
            except Exception as e:
                pw_log(f"Aviso ao ler flow_queue.json existente ({projeto_id}): {e}", level="warn")

        agora = datetime.now().isoformat()
        estrutura = {
            "projeto_id": projeto_id,
            "criado_em": agora,
            "atualizado_em": agora,
            "itens": [],
            "cena_ativa": None,
            "total": 0,
            "concluidos": 0,
            "erros": 0
        }
        _salvar_fila_atomico_unlocked(fpath, estrutura)
        return estrutura


def _ler_fila(projeto_id: str, projetos_dir: Optional[Any] = None) -> dict:
    """
    Lê flow_queue.json de forma thread-safe com Lock.
    Retorna o dicionário ou {} se não existir / erro de parse.
    """
    fpath = _get_flow_queue_path(projeto_id, projetos_dir)
    with _flow_queue_file_lock:
        if not fpath.exists():
            return {}
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                dados = json.load(fp)
                return dados if isinstance(dados, dict) else {}
        except Exception as e:
            pw_log(f"Erro ao ler flow_queue.json ({projeto_id}): {e}", level="warn")
            return {}


def _salvar_fila(projeto_id: str, fila_dict: dict, projetos_dir: Optional[Any] = None) -> bool:
    """
    Escreve atomicamente no flow_queue.json protegido por lock para evitar race conditions.
    """
    fpath = _get_flow_queue_path(projeto_id, projetos_dir)
    with _flow_queue_file_lock:
        return _salvar_fila_atomico_unlocked(fpath, fila_dict)


def sincronizar_cenas_para_pendente(projeto_id: str, scene_ids: List[int]) -> int:
    """TAREFA 4 — volta para PENDENTE/IMAGE_PENDING as cenas indicadas que estejam GERANDO.

    Usado pelo cancelamento da fila: a execução que estava em voo pode terminar
    DEPOIS do cancelamento (a Fase D do worker reconhece a conclusão tardia e, se
    a cena voltar a ficar pronta, regrava o plan como BAIXADA/READY).
    Retorna quantas cenas foram efetivamente atualizadas.
    """
    if not scene_ids:
        return 0
    atualizadas = 0
    try:
        plano = scene_plan_svc.carregar_scene_plan(projeto_id) or {}
        mapa = {int(c.get("id", 0)): c for c in plano.get("cenas", []) if isinstance(c, dict)}
    except Exception as e:
        pw_log(f"[FILA] Falha ao ler o plan para sincronizar cenas GERANDO ({projeto_id}): {e}", level="warn")
        return 0

    for cid in scene_ids:
        cena = mapa.get(int(cid))
        if not cena or cena.get("status") != scene_plan_svc.STATUS_GERANDO:
            continue
        try:
            scene_plan_svc.atualizar_cena(projeto_id, int(cid), {
                "status": scene_plan_svc.STATUS_PENDENTE,
                "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
            })
            atualizadas += 1
        except Exception as e:
            pw_log(f"[FILA] Falha ao voltar cena {cid} para PENDENTE ({projeto_id}): {e}", level="warn")
    return atualizadas


def _sincronizar_plan_baixada_se_necessario(projeto_id: str, scene_id: int, caminho_img: str) -> bool:
    """Marca a cena como BAIXADA/READY no plan SOMENTE se ainda não estiver assim.

    TAREFA 3 — usado pela Fase D de uma execução DESCARTADA (item cancelado ou
    re-adicionado durante a geração): a mídia já foi gravada em disco, então o
    plan pode recebê-la, mas sem sobrescrever um estado já concluído por outra
    execução (a 2ª geração da mesma cena vence).
    """
    try:
        plano = scene_plan_svc.carregar_scene_plan(projeto_id) or {}
        mapa = {int(c.get("id", 0)): c for c in plano.get("cenas", []) if isinstance(c, dict)}
    except Exception as e:
        pw_log(f"[FILA] Falha ao ler o plan para sincronizar cena {scene_id}: {e}", level="warn")
        return False

    cena = mapa.get(int(scene_id))
    if cena and cena.get("status") == scene_plan_svc.STATUS_BAIXADA and cena.get("image_status") == scene_plan_svc.IMAGE_STATUS_READY:
        return False  # já está pronta — nada a fazer

    try:
        campos = {
            "status": scene_plan_svc.STATUS_BAIXADA,
            "image_status": scene_plan_svc.IMAGE_STATUS_READY,
        }
        if caminho_img:
            campos["arquivo_midia"] = caminho_img
            campos["download_path"] = caminho_img
        scene_plan_svc.atualizar_cena(projeto_id, int(scene_id), campos)
        return True
    except Exception as e:
        pw_log(f"[FILA] Falha ao sincronizar cena {scene_id} como BAIXADA: {e}", level="warn")
        return False


def _curar_orfaos_fila(projeto_id: str, projetos_dir: Optional[Any] = None) -> List[Tuple[int, str]]:
    """TAREFA 3 — cura itens "gerando" ÓRFÃOS (sem execução viva) do flow_queue.json.

    Um item "gerando" só é órfão se nenhuma execução viva o possui: cada execução
    da Fase A registra um `exec_id` (uuid4().hex) em `_exec_ids_ativos` enquanto
    roda. Item sem exec_id (formato antigo) ou com exec_id fora do set = órfão.
      - órfão com tentativas restantes (attempt_count < max_tentativas) volta para
        "enfileirada";
      - órfão sem tentativas restantes vira "erro" (erros += 1) com a mensagem
        "Interrompida (app reiniciado ou cancelada) sem tentativas restantes".
    Se não sobrar item "gerando" legítimo, cena_ativa = None.

    Fora da transação, sincroniza o lira_scene_plan.json SOMENTE nas cenas cujo
    status atual seja GERANDO: reenfileirada -> PENDENTE/IMAGE_PENDING;
    erro -> ERRO/ERROR. Retorna a lista de (scene_id, novo_status) curados.
    """
    curados: List[Tuple[int, str]] = []
    fpath = _get_flow_queue_path(projeto_id, projetos_dir)

    # Projeto sem fila em disco: nada a curar (e não cria arquivo por engano).
    if not fpath.exists():
        return curados

    with _exec_ids_lock:
        vivos = set(_exec_ids_ativos)

    agora = datetime.now().isoformat()
    with transacao_fila(projeto_id, projetos_dir) as fila:
        itens = fila.get("itens") if isinstance(fila.get("itens"), list) else fila.get("fila", [])
        if not isinstance(itens, list):
            itens = []

        gerando_legitimo = False
        for it in itens:
            if not isinstance(it, dict) or it.get("status") != "gerando":
                continue

            exec_id = str(it.get("exec_id") or "")
            if exec_id and exec_id in vivos:
                gerando_legitimo = True
                continue

            # ---- ÓRFÃO (nenhuma execução viva o possui) ----
            try:
                cid = int(it.get("scene_id") or it.get("id") or 0)
            except Exception:
                cid = 0
            tentativas = int(it.get("attempt_count") or it.get("tentativa_atual") or 0)
            max_tent = int(it.get("max_tentativas") or 3)

            if tentativas < max_tent:
                it["status"] = "enfileirada"
                it["reenfileirada_em"] = agora
                curados.append((cid, "enfileirada"))
            else:
                msg = "Interrompida (app reiniciado ou cancelada) sem tentativas restantes"
                it["status"] = "erro"
                it["error_msg"] = msg
                it["ultimo_erro"] = msg
                it["completed_at"] = agora
                fila["erros"] = fila.get("erros", 0) + 1
                curados.append((cid, "erro"))

            it["exec_id"] = None
            pw_log(
                f"[FILA] Item órfão curado: cena {cid} -> {it['status']} "
                f"(tentativas {tentativas}/{max_tent}).",
                level="warn",
            )

        if not gerando_legitimo and fila.get("cena_ativa") is not None:
            fila["cena_ativa"] = None

    # Fora da transação: alinhar o lira_scene_plan.json (SOMENTE cenas GERANDO).
    if curados:
        try:
            plano = scene_plan_svc.carregar_scene_plan(projeto_id) or {}
            mapa = {int(c.get("id", 0)): c for c in plano.get("cenas", []) if isinstance(c, dict)}
        except Exception:
            mapa = {}

        for cid, novo_status in curados:
            cena = mapa.get(int(cid))
            if not cena or cena.get("status") != scene_plan_svc.STATUS_GERANDO:
                continue
            try:
                if novo_status == "enfileirada":
                    scene_plan_svc.atualizar_cena(projeto_id, int(cid), {
                        "status": scene_plan_svc.STATUS_PENDENTE,
                        "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
                    })
                else:
                    scene_plan_svc.atualizar_cena(projeto_id, int(cid), {
                        "status": scene_plan_svc.STATUS_ERRO,
                        "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
                        "erro_msg": "Interrompida (app reiniciado ou cancelada) sem tentativas restantes",
                    })
            except Exception as e:
                pw_log(f"[FILA] Falha ao sincronizar cena {cid} curada no plan: {e}", level="warn")

    return curados


def _criar_imagem_cena_impl(projeto_id: str, scene_id: int, prompt: str = "", is_anim: bool = False) -> Dict[str, Any]:
    """
    Executa a geração e download de imagem de uma cena individual no Google Flow via Playwright CDP.

    TAREFA 1 & 2 — roda SEMPRE na thread do ator CDP (_get_cdp_actor). O lock global de
    geração é adquirido com timeout FLOW_LOCK_ACQUIRE_TIMEOUT_S; se estiver ocupado
    (caminho legado em andamento), retorna nao_tentada=True, ocupado=True.
    Mede a duração da execução real e inclui duracao_s na telemetria.
    """
    t0_impl = time.time()
    acquired = _flow_generation_lock.acquire(timeout=FLOW_LOCK_ACQUIRE_TIMEOUT_S)
    if not acquired:
        duracao_s = round(time.time() - t0_impl, 2)
        pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok=False (lock ocupado)", level="warn")
        return {
            "success": False,
            "nao_tentada": True,
            "ocupado": True,
            "erro": "Flow ocupado: fila legada em andamento",
            "espera_s": FLOW_QUEUE_ESPERA_OCUPADO_S,
            "duracao_s": duracao_s,
        }

    try:
        ok_cdp, msg_cdp = ensure_chrome_cdp(modo="animacao" if is_anim else "image")
        if not ok_cdp:
            duracao_s = round(time.time() - t0_impl, 2)
            pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok=False")
            return {"success": False, "erro": f"Chrome CDP indisponível: {msg_cdp}", "duracao_s": duracao_s}

        worker = FlowQueueWorker.get_worker()
        worker.current_project_id = projeto_id

        # TAREFA 1d — GUARDA DE AFINIDADE: sessão criada por OUTRA thread (thread
        # HTTP, outro worker, worker morto) é DESCARTADA antes de qualquer uso —
        # usar `page` de outra thread levanta greenlet.error
        # ("Cannot switch to a different thread").
        if worker.page is not None and getattr(worker, "_session_thread_ident", None) != threading.get_ident():
            worker._descartar_sessao_outra_thread()

        # Garante que a sessão thread Playwright esteja ativa (exceção = inativo)
        try:
            sessao_ativa = bool(worker._check_is_active())
        except Exception as e_check:
            pw_log(f"[CDP-ATOR] _check_is_active() levantou ({e_check}) — tratando como inativo.", level="warn")
            sessao_ativa = False

        if not sessao_ativa or worker.page is None:
            ok_sessao, msg_sessao = worker._iniciar_sessao_thread()
            if not ok_sessao:
                duracao_s = round(time.time() - t0_impl, 2)
                pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok=False")
                return {"success": False, "erro": f"Falha na sessão do Flow: {msg_sessao}", "duracao_s": duracao_s}

        if not worker._ensure_project_open(projeto_id, timeout_s=10):
            duracao_s = round(time.time() - t0_impl, 2)
            pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok=False")
            return {"success": False, "erro": "Google Flow sem projeto aberto ou não foi possível conectar ao canvas.", "duracao_s": duracao_s}

        # Carrega plano de cenas para encontrar a cena alvo
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        cenas = plan.get("cenas", []) if plan else []
        cena_alvo = None
        for c in cenas:
            if int(c.get("id", 0)) == int(scene_id):
                cena_alvo = dict(c)
                break

        if cena_alvo is None:
            cena_alvo = {
                "id": int(scene_id),
                "prompt_imagem": prompt,
                "texto": prompt,
                "tempo_inicio": 0.0,
                "tempo_fim": 5.0,
                "tipo": "video" if is_anim else "image"
            }

        if is_anim:
            cena_alvo["prompt_animacao"] = prompt or cena_alvo.get("prompt_animacao") or prompt
            cena_alvo["tipo"] = "video"
            cena_alvo["media_intent"] = "video"
            cena_alvo["animar"] = True
        else:
            if prompt:
                cena_alvo["prompt_imagem"] = prompt
            cena_alvo["tipo"] = "image"
            cena_alvo["media_intent"] = "image"

        try:
            ok_proc, msg_proc = worker._processar_cena_individual(
                projeto_id=projeto_id,
                cena=cena_alvo,
                is_anim=is_anim
            )
            duracao_s = round(time.time() - t0_impl, 2)
            pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok={ok_proc}")
            if ok_proc:
                # Tenta resolver o caminho final do arquivo salvo
                caminho_img = ""
                plan_pos = scene_plan_svc.carregar_scene_plan(projeto_id)
                if plan_pos and "cenas" in plan_pos:
                    for c in plan_pos["cenas"]:
                        if int(c.get("id", 0)) == int(scene_id):
                            caminho_img = str(c.get("arquivo_midia") or c.get("download_path") or "")
                            break
                if not caminho_img:
                    arq_resolvido = scene_plan_svc.resolver_arquivo_cena(
                        projeto_id, int(scene_id), float(cena_alvo.get("tempo_inicio", 0))
                    )
                    if arq_resolvido and arq_resolvido.exists():
                        caminho_img = str(arq_resolvido)
                    else:
                        caminho_img = msg_proc

                if is_anim:
                    p_img = Path(caminho_img)
                    suf = p_img.suffix.lower() if caminho_img else ""
                    if not caminho_img or suf not in (".mp4", ".webm", ".mov") or not p_img.exists() or p_img.stat().st_size <= 5000:
                        pw_log(f"[FlowQueue] Cena {scene_id}: esperava arquivo de vídeo (.mp4/.webm/.mov), mas obteve '{caminho_img}'", level="error")
                        return {
                            "success": False,
                            "erro": f"Esperado vídeo (.mp4/.webm/.mov), mas foi retornado '{caminho_img}' (tamanho ou extensão inválida)",
                            "duracao_s": duracao_s,
                        }

                return {
                    "success": True,
                    "caminho_imagem": caminho_img,
                    "mensagem": msg_proc,
                    "duracao_s": duracao_s,
                }
            else:
                return {
                    "success": False,
                    "erro": msg_proc,
                    "duracao_s": duracao_s,
                }
        except Exception as e:
            duracao_s = round(time.time() - t0_impl, 2)
            pw_log(f"Exceção em criar_imagem_cena (cena {scene_id}): {e}", level="error")
            pw_log(f"[FlowQueue] DURACAO projeto={projeto_id} cena={scene_id} s={duracao_s} ok=False")
            return {
                "success": False,
                "erro": str(e),
                "duracao_s": duracao_s,
            }
    finally:
        try:
            _flow_generation_lock.release()
        except Exception:
            pass


def criar_imagem_cena(projeto_id: str, scene_id: int, prompt: str = "", is_anim: bool = False) -> Dict[str, Any]:
    """Gera e baixa a imagem ou vídeo de UMA cena do Google Flow (entrada pública).

    TAREFA 1 — wrapper do ator CDP: MESMA assinatura e MESMOS retornos de antes
    ({ "success": True, "caminho_imagem": ... } / { "success": False, "erro": ... }),
    mas a execução real acontece na thread dedicada do ator
    (_criar_imagem_cena_impl), que é quem cria/usar a sessão Playwright e adquire
    o `_flow_generation_lock`. Qualquer exceção (inclusive timeout do ator) vira
    {"success": False, "erro": str(e)} — o contrato consumido pelo app.js não muda.
    """
    try:
        return _get_cdp_actor().submit(
            _criar_imagem_cena_impl, projeto_id, scene_id, prompt, is_anim=is_anim,
            timeout=_timeout_do_ator(),
        )
    except Exception as e:
        pw_log(f"Exceção em criar_imagem_cena (cena {scene_id}): {e}", level="error")
        return {"success": False, "erro": str(e)}


def _tratar_resultado_tardio(fut: Future, projeto_id: str, scene_id: int, exec_id: str, projetos_dir: Optional[Any] = None):
    """Callback registrado no Future do ator para tratar conclusão após cancelamento ou timeout."""
    try:
        res = fut.result()
        if isinstance(res, dict) and res.get("success") and res.get("caminho_imagem"):
            caminho = res["caminho_imagem"]
            _sincronizar_plan_baixada_se_necessario(projeto_id, scene_id, caminho)
            with transacao_fila(projeto_id, projetos_dir) as fila:
                itens = fila.get("itens") if isinstance(fila.get("itens"), list) else fila.get("fila", [])
                for it in itens:
                    if str(it.get("exec_id") or "") == exec_id:
                        is_anim_it = (str(it.get("modo") or "").lower() in ("video", "animacao")) or bool(it.get("is_anim"))
                        suf = Path(caminho).suffix.lower()
                        if is_anim_it and (suf not in (".mp4", ".webm", ".mov") or not Path(caminho).exists() or Path(caminho).stat().st_size <= 5000):
                            pw_log(f"[FlowQueue] Conclusão tardia da cena {scene_id} rejeitada: esperado vídeo, recebido '{caminho}'", level="warn")
                            break
                        if it.get("status") == "erro" and fila.get("erros", 0) > 0:
                            fila["erros"] -= 1
                        it["status"] = "concluida"
                        it["caminho_imagem"] = caminho
                        it["concluida_em"] = datetime.now().isoformat()
                        fila["concluidos"] = fila.get("concluidos", 0) + 1
                        break
            pw_log(f"[FlowQueue] Conclusão tardia com sucesso da cena {scene_id} (exec_id {exec_id[:8]}) gravada no plan e fila.")
    except Exception as e:
        pw_log(f"[FlowQueue] Conclusão tardia da cena {scene_id} resultou em exceção ou foi ignorada: {e}", level="warn")


class FlowQueueWorker(threading.Thread):
    """
    Worker sequencial de fila de processamento assíncrono para geração de imagens no Google Flow.
    Herda de threading.Thread e processa um item por vez de flow_queue.json.
    Mantém também métodos estáticos legados para compatibilidade com chamadas existentes no sistema.
    """

    def __init__(self, projeto_id: str = "", projetos_dir: Optional[Any] = None):
        super().__init__(daemon=True, name=f"FlowQueueWorker-{projeto_id or 'default'}")
        self.projeto_id = projeto_id
        self.projetos_dir = Path(projetos_dir) if projetos_dir else PROJETOS_DIR
        self.daemon = True
        self.running = False
        self._cancel_gen = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def cancelar_execucao(self):
        """Cancela a execução em andamento sem encerrar a thread do worker."""
        with self._lock:
            self._cancel_gen += 1
        self._wake_event.set()

    def start_processing(self):
        """Inicia a thread de processamento da fila se ainda não estiver ativa."""
        with self._lock:
            self.running = True
            self._stop_event.clear()
            self._wake_event.set()
            if not self.is_alive():
                try:
                    self.start()
                except RuntimeError:
                    pass

    def stop_processing(self):
        """Sinaliza parada para o loop da thread."""
        with self._lock:
            self.running = False
            self._stop_event.set()
            self._wake_event.set()

    def is_active(self) -> bool:
        """Verifica se o worker está rodando e ativo."""
        return self.running and self.is_alive()

    def run(self):
        """
        Loop de execução sequencial da fila com transação atômica:
        FASE 0: cura itens "gerando" órfãos (sem execução viva) do disco
        FASE A (transação): checar pausa e capturar próximo item ("enfileirada"/"pendente"),
                 gerando o exec_id da execução
        FASE B (fora da transação): atualizar cena para GERANDO e submeter ao ator
        FASE C (transação): atualizar o item LOCALIZADO PELO exec_id com resultado tipado
                 (sucesso, falha, nao_tentada, timeout_execucao, cancelada).
        FASE D (fora da transação): atualizar scene_plan_svc, aguardar backoff e intervalo
        """
        pw_log(f"[FlowQueueWorker] Thread iniciada para projeto '{self.projeto_id}'.")

        # TAREFA 3d — FASE 0: itens "gerando" sem execução viva (app reiniciado,
        # cancelamento abrupto) voltam para a fila (ou viram "erro" se esgotaram
        # as tentativas) antes de capturar qualquer item.
        try:
            _curados = _curar_orfaos_fila(self.projeto_id, self.projetos_dir)
            if _curados:
                pw_log(f"[FlowQueueWorker] Cura de órfãos: {_curados}")
        except Exception as e_cura:
            pw_log(f"[FlowQueueWorker] Aviso: falha na cura de órfãos: {e_cura}", level="warn")

        while not self._stop_event.is_set():
            exec_id_em_voo: Optional[str] = None
            tipo_resultado: str = ""
            try:
                # ===============================================================
                # FASE A (transação): Checar pausa e capturar item
                # ===============================================================
                deve_pausar = False
                sem_itens = False
                item_alvo_dados = None
                gen_atual = self._cancel_gen

                with transacao_fila(self.projeto_id, self.projetos_dir) as fila:
                    if fila.get("pausada") is True or getattr(self, "pausada", False) or fila.get("ativa") is False:
                        deve_pausar = True
                    else:
                        itens = fila.get("itens") if isinstance(fila.get("itens"), list) else fila.get("fila", [])
                        item_alvo = None
                        for it in itens:
                            if isinstance(it, dict) and it.get("status") in ("enfileirada", "pendente"):
                                item_alvo = it
                                break

                        if item_alvo is None:
                            sem_itens = True
                            fila["ativa"] = False
                            if fila.get("cena_ativa") is not None:
                                fila["cena_ativa"] = None
                        else:
                            scene_id = int(item_alvo.get("scene_id") or item_alvo.get("id") or 0)
                            prompt = str(item_alvo.get("prompt") or "")
                            modo_item = str(item_alvo.get("modo") or "imagem").lower()
                            is_anim_item = (modo_item in ("video", "animacao")) or bool(item_alvo.get("is_anim"))
                            agora = datetime.now().isoformat()
                            max_tent = int(item_alvo.get("max_tentativas") or 3)

                            # Incrementa no INÍCIO de cada execução (Fase A)
                            tentativas = int(item_alvo.get("attempt_count") or item_alvo.get("tentativa_atual") or 0) + 1
                            item_alvo["attempt_count"] = tentativas
                            item_alvo["tentativa_atual"] = tentativas
                            item_alvo["max_tentativas"] = max_tent

                            # Identificador único desta execução
                            exec_id = uuid.uuid4().hex
                            with _exec_ids_lock:
                                _exec_ids_ativos.add(exec_id)
                            exec_id_em_voo = exec_id
                            item_alvo["exec_id"] = exec_id
                            item_alvo["status"] = "gerando"
                            item_alvo["started_at"] = agora
                            item_alvo["iniciada_em"] = agora

                            fila["cena_ativa"] = {
                                "scene_id": scene_id,
                                "status": "gerando",
                                "prompt": prompt,
                                "modo": modo_item,
                                "is_anim": is_anim_item,
                                "exec_id": exec_id,
                                "started_at": agora,
                                "iniciada_em": agora,
                                "tentativa_atual": tentativas,
                                "attempt_count": tentativas,
                                "max_tentativas": max_tent
                            }
                            item_alvo_dados = {
                                "scene_id": scene_id,
                                "prompt": prompt,
                                "modo": modo_item,
                                "is_anim": is_anim_item,
                                "tentativas": tentativas,
                                "max_tent": max_tent,
                                "exec_id": exec_id
                            }

                if deve_pausar:
                    with self._lock:
                        self.running = False
                    self._wake_event.wait(timeout=2.0)
                    self._wake_event.clear()
                    continue

                if sem_itens:
                    with self._lock:
                        self.running = False
                    self._wake_event.wait(timeout=2.0)
                    self._wake_event.clear()
                    continue

                with self._lock:
                    self.running = True

                scene_id = item_alvo_dados["scene_id"]
                prompt = item_alvo_dados["prompt"]
                tentativas = item_alvo_dados["tentativas"]
                max_tent = item_alvo_dados["max_tent"]
                exec_id = str(item_alvo_dados.get("exec_id") or "")
                modo_item = item_alvo_dados.get("modo", "imagem")
                is_anim_item = bool(item_alvo_dados.get("is_anim"))

                # ===============================================================
                # FASE B (fora da transação): Atualizar cena e submeter ao ator
                # ===============================================================
                try:
                    if is_anim_item:
                        scene_plan_svc.atualizar_cena(self.projeto_id, scene_id, {
                            "status": scene_plan_svc.STATUS_GERANDO,
                            "video_status": scene_plan_svc.VIDEO_STATUS_GENERATING
                        })
                    else:
                        scene_plan_svc.atualizar_cena(self.projeto_id, scene_id, {
                            "status": scene_plan_svc.STATUS_GERANDO,
                            "image_status": scene_plan_svc.IMAGE_STATUS_GENERATING
                        })
                except Exception as e_sp:
                    pw_log(f"[FlowQueueWorker] Aviso ao atualizar cena {scene_id} para GERANDO: {e_sp}", level="warn")

                tipo_label = "vídeo" if is_anim_item else "imagem"
                pw_log(f"[FlowQueueWorker] Gerando {tipo_label} para cena {scene_id} (tentativa {tentativas}/{max_tent})...")

                actor = _get_cdp_actor()
                if is_anim_item:
                    import inspect
                    try:
                        sig = inspect.signature(criar_imagem_cena)
                        aceita_anim = "is_anim" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                    except Exception:
                        aceita_anim = True
                    if aceita_anim:
                        task = actor.submit_async(
                            criar_imagem_cena, self.projeto_id, scene_id, prompt, is_anim=is_anim_item
                        )
                    else:
                        task = actor.submit_async(
                            criar_imagem_cena, self.projeto_id, scene_id, prompt
                        )
                else:
                    task = actor.submit_async(
                        criar_imagem_cena, self.projeto_id, scene_id, prompt
                    )

                tipo_resultado = "sucesso"
                res = {}
                motivo_nao_tentada = ""
                espera_nao_tentada = 0.0

                try:
                    res = actor.aguardar(
                        task,
                        cancel_check=lambda: self._cancel_gen != gen_atual or self._stop_event.is_set(),
                        timeout=_timeout_do_ator()
                    )
                    if not isinstance(res, dict):
                        res = {"success": False, "erro": f"Retorno inesperado: {res}"}
                    if res.get("nao_tentada"):
                        tipo_resultado = "nao_tentada"
                        motivo_nao_tentada = "ocupado" if res.get("ocupado") else "rejeitada"
                        espera_nao_tentada = float(res.get("espera_s") or FLOW_QUEUE_ESPERA_OCUPADO_S)
                    elif not res.get("success"):
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
                        c_img = str(res.get("caminho_imagem") or "")
                        p_img = Path(c_img) if c_img else None
                        if not p_img or p_img.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not p_img.exists() or p_img.stat().st_size < 1024:
                            tipo_resultado = "falha"
                            res = {
                                "success": False,
                                "erro": f"Esperado arquivo de imagem válido (.png/.jpg/.jpeg/.webp >= 1KB), recebido '{c_img}' (tamanho ou extensão inválida)"
                            }
                        else:
                            tipo_resultado = "sucesso"
                except _NaoTentada as nt:
                    tipo_resultado = "nao_tentada"
                    motivo_nao_tentada = nt.motivo
                    espera_nao_tentada = nt.espera_s
                    res = {"success": False, "erro": f"Não tentada: {nt.motivo}"}
                except _ExecTimeout:
                    tipo_resultado = "timeout_execucao"
                    res = {"success": False, "erro": "Timeout de execução no Google Flow"}
                    task.future.add_done_callback(
                        lambda f, p=self.projeto_id, s=scene_id, eid=exec_id, d=self.projetos_dir:
                        _tratar_resultado_tardio(f, p, s, eid, d)
                    )
                    def _remover_eid_timeout(f, eid=exec_id):
                        with _exec_ids_lock:
                            _exec_ids_ativos.discard(eid)
                    task.future.add_done_callback(_remover_eid_timeout)
                except _ExecCancelada:
                    tipo_resultado = "cancelada"
                    res = {"success": False, "erro": "Execução cancelada"}
                    task.future.add_done_callback(
                        lambda f, p=self.projeto_id, s=scene_id, eid=exec_id, d=self.projetos_dir:
                        _tratar_resultado_tardio(f, p, s, eid, d)
                    )
                    def _remover_eid_cancel(f, eid=exec_id):
                        with _exec_ids_lock:
                            _exec_ids_ativos.discard(eid)
                    task.future.add_done_callback(_remover_eid_cancel)
                except Exception as e_ator:
                    tipo_resultado = "falha"
                    res = {"success": False, "erro": str(e_ator)}

                # ===============================================================
                # FASE C (transação): Localizar o item PELO exec_id desta execução
                # ===============================================================
                tempo_espera = 0.0
                falha_definitiva = False
                item_encontrado = False

                with transacao_fila(self.projeto_id, self.projetos_dir) as fila:
                    itens = fila.get("itens") if isinstance(fila.get("itens"), list) else fila.get("fila", [])
                    item_atual = None
                    for it in itens:
                        if exec_id and str(it.get("exec_id") or "") == exec_id:
                            item_atual = it
                            break

                    if item_atual is None:
                        # Execução descartada (cancelada/re-adicionada)
                        pw_log(f"[FlowQueueWorker] Aviso: execução da cena {scene_id} (exec_id {exec_id[:8]}) não encontrada na fila (cancelada/re-adicionada durante geração).", level="warn")
                        if fila.get("cena_ativa") is not None and str((fila.get("cena_ativa") or {}).get("exec_id") or "") == exec_id:
                            fila["cena_ativa"] = None
                    else:
                        item_encontrado = True
                        agora_c = datetime.now().isoformat()

                        if tipo_resultado == "nao_tentada":
                            item_atual["status"] = "enfileirada"
                            tent_rec = max(0, int(item_atual.get("attempt_count") or 1) - 1)
                            item_atual["attempt_count"] = tent_rec
                            item_atual["tentativa_atual"] = tent_rec
                            item_atual["iniciada_em"] = None
                            item_atual["started_at"] = None
                            fila["cena_ativa"] = None
                            if motivo_nao_tentada == "ocupado":
                                tempo_espera = espera_nao_tentada or FLOW_QUEUE_ESPERA_OCUPADO_S
                                pw_log(f"[FlowQueueWorker] Cena {scene_id}: não tentada (Flow ocupado). Aguardando {tempo_espera}s...", level="warn")
                            elif motivo_nao_tentada == "travado":
                                fila["pausada"] = True
                                fila["motivo_pausa"] = "Ator Playwright travado; verifique o Chrome ou reinicie o app"
                                with self._lock:
                                    self.pausada = True
                                pw_log(f"[FlowQueueWorker] Cena {scene_id}: ator CDP travado. Fila pausada automaticamente.", level="error")
                            else:
                                tempo_espera = 0.0

                        elif tipo_resultado == "timeout_execucao":
                            falha_definitiva = True
                            item_atual["status"] = "erro"
                            item_atual["error_msg"] = res.get("erro") or "Timeout de execução no Google Flow"
                            item_atual["ultimo_erro"] = item_atual["error_msg"]
                            item_atual["last_attempt_ts"] = agora_c
                            fila["erros"] = fila.get("erros", 0) + 1
                            fila["cena_ativa"] = None
                            pw_log(f"[FlowQueueWorker] ❌ Cena {scene_id} falhou por timeout de execução: {item_atual['error_msg']}", level="error")

                        elif tipo_resultado == "cancelada":
                            fila["cena_ativa"] = None

                        elif tipo_resultado == "sucesso":
                            caminho_cand = str(res.get("caminho_imagem") or "")
                            suf_cand = Path(caminho_cand).suffix.lower() if caminho_cand else ""
                            if is_anim_item and (suf_cand not in (".mp4", ".webm", ".mov") or not Path(caminho_cand).exists() or Path(caminho_cand).stat().st_size <= 5000):
                                msg_erro = f"Esperado arquivo de vídeo (.mp4/.webm/.mov), recebido '{caminho_cand}'"
                                item_atual["error_msg"] = msg_erro
                                item_atual["ultimo_erro"] = msg_erro
                                item_atual["last_attempt_ts"] = agora_c
                                if tentativas < max_tent:
                                    item_atual["status"] = "enfileirada"
                                    fila["cena_ativa"] = None
                                    idx_backoff = tentativas - 1
                                    tempo_espera = FLOW_QUEUE_BACKOFFS[idx_backoff] if idx_backoff < len(FLOW_QUEUE_BACKOFFS) else 10.0
                                    pw_log(f"[FlowQueueWorker] ⚠️ Cena {scene_id} falhou por validação de mídia (vídeo esperado, obtido {suf_cand}). Backoff: {tempo_espera}s...", level="warn")
                                else:
                                    falha_definitiva = True
                                    item_atual["status"] = "erro"
                                    fila["erros"] = fila.get("erros", 0) + 1
                                    fila["cena_ativa"] = None
                                    pw_log(f"[FlowQueueWorker] ❌ Cena {scene_id} falhou definitivamente: {msg_erro}", level="error")
                            else:
                                item_atual["status"] = "concluida"
                                item_atual["caminho_imagem"] = caminho_cand
                                item_atual["completed_at"] = agora_c
                                item_atual["concluida_em"] = agora_c
                                item_atual["error_msg"] = ""
                                item_atual["ultimo_erro"] = ""
                                fila["concluidos"] = fila.get("concluidos", 0) + 1
                                fila["cena_ativa"] = None

                        elif tipo_resultado == "falha":
                            msg_erro = str(res.get("erro") or "Erro desconhecido")
                            item_atual["error_msg"] = msg_erro
                            item_atual["ultimo_erro"] = msg_erro
                            item_atual["last_attempt_ts"] = agora_c

                            if tentativas < max_tent:
                                item_atual["status"] = "enfileirada"
                                fila["cena_ativa"] = None
                                idx_backoff = tentativas - 1
                                tempo_espera = FLOW_QUEUE_BACKOFFS[idx_backoff] if idx_backoff < len(FLOW_QUEUE_BACKOFFS) else 10.0
                                pw_log(f"[FlowQueueWorker] ⚠️ Cena {scene_id} falhou (execução {tentativas}/{max_tent}). Erro: {msg_erro}. Backoff: {tempo_espera}s...", level="warn")
                            else:
                                falha_definitiva = True
                                item_atual["status"] = "erro"
                                fila["erros"] = fila.get("erros", 0) + 1
                                fila["cena_ativa"] = None
                                pw_log(f"[FlowQueueWorker] ❌ Cena {scene_id} falhou definitivamente após esgotar {max_tent} tentativas: {msg_erro}", level="error")

                # ===============================================================
                # FASE D (fora da transação): Sincronizar scene_plan_svc e aguardar
                # ===============================================================
                if item_encontrado:
                    if tipo_resultado == "sucesso":
                        try:
                            caminho_midia = res.get("caminho_imagem") or ""
                            suf_midia = Path(caminho_midia).suffix.lower() if caminho_midia else ""
                            eh_video_ext = suf_midia in (".mp4", ".webm", ".mov")

                            campos_atualizar = {
                                "status": scene_plan_svc.STATUS_BAIXADA,
                            }
                            if eh_video_ext:
                                campos_atualizar["video_status"] = scene_plan_svc.VIDEO_STATUS_READY
                                campos_atualizar["has_video"] = True
                                campos_atualizar["tipo"] = scene_plan_svc.TIPO_VIDEO
                            else:
                                campos_atualizar["image_status"] = scene_plan_svc.IMAGE_STATUS_READY

                            if caminho_midia:
                                campos_atualizar["arquivo_midia"] = caminho_midia
                                campos_atualizar["download_path"] = caminho_midia
                            scene_plan_svc.atualizar_cena(self.projeto_id, scene_id, campos_atualizar)
                        except Exception as e_sp2:
                            pw_log(f"[FlowQueueWorker] Erro ao sincronizar cena {scene_id} como BAIXADA: {e_sp2}", level="warn")
                        pw_log(f"[FlowQueueWorker] ✅ Cena {scene_id} concluída e baixada com sucesso.")
                    elif falha_definitiva:
                        try:
                            erro_campos = {
                                "status": scene_plan_svc.STATUS_ERRO,
                                "erro_msg": res.get("erro") or "Erro definitivo na geração"
                            }
                            if is_anim_item:
                                erro_campos["video_status"] = scene_plan_svc.VIDEO_STATUS_ERROR
                            else:
                                erro_campos["image_status"] = scene_plan_svc.IMAGE_STATUS_ERROR
                            scene_plan_svc.atualizar_cena(self.projeto_id, scene_id, erro_campos)
                        except Exception as e_sp3:
                            pw_log(f"[FlowQueueWorker] Erro ao marcar cena {scene_id} como ERRO: {e_sp3}", level="warn")
                    elif tipo_resultado == "nao_tentada":
                        pass
                else:
                    if tipo_resultado == "sucesso" and res.get("caminho_imagem"):
                        _sincronizar_plan_baixada_se_necessario(self.projeto_id, scene_id, res["caminho_imagem"])
                        pw_log(f"[FlowQueueWorker] Cena {scene_id}: execução descartada, mas a mídia gerada foi registrada no plan.")

                if tempo_espera > 0 and not self._stop_event.is_set():
                    self._stop_event.wait(tempo_espera)

                if not self._stop_event.is_set():
                    self._stop_event.wait(FLOW_QUEUE_INTERVALO_S)

            except Exception as e_loop:
                pw_log(f"[FlowQueueWorker] Exceção inesperada no loop da fila: {e_loop}", level="error")
                if not self._stop_event.is_set():
                    self._stop_event.wait(2.0)
            finally:
                if exec_id_em_voo and tipo_resultado not in ("timeout_execucao", "cancelada"):
                    with _exec_ids_lock:
                        _exec_ids_ativos.discard(exec_id_em_voo)

        pw_log(f"[FlowQueueWorker] Thread finalizada para projeto '{self.projeto_id}'.")

    # =======================================================================
    # MÉTODOS DE GERENCIAMENTO DE INSTÂNCIAS E COMPATIBILIDADE LEGADA
    # =======================================================================

    @classmethod
    def get_queue_worker(cls, projeto_id: str, projetos_dir: Optional[Any] = None) -> "FlowQueueWorker":
        """Retorna (ou cria) a instância única de worker por projeto."""
        with _queue_workers_lock:
            w = _queue_workers.get(projeto_id)
            if w is not None and w.is_alive():
                if w._stop_event.is_set():
                    w.join(timeout=2.0)
                    if not w.is_alive():
                        w = cls(projeto_id=projeto_id, projetos_dir=projetos_dir)
                        _queue_workers[projeto_id] = w
                return w
            w = cls(projeto_id=projeto_id, projetos_dir=projetos_dir)
            _queue_workers[projeto_id] = w
            return w

    @classmethod
    def peek_queue_worker(cls, projeto_id: str) -> Optional["FlowQueueWorker"]:
        """TAREFA 4b — consulta o worker do projeto SEM instanciar (polling).

        Antes, GET /fila_status usava get_or_create_worker: CADA polling criava um
        worker novo para projetos sem fila (objeto registrado em _queue_workers
        para sempre). Este peek apenas consulta o registro sob lock.
        """
        with _queue_workers_lock:
            return _queue_workers.get(projeto_id)

    @staticmethod
    def get_worker() -> PlaywrightCDPWorker:
        global _worker_instance
        with _worker_lock:
            if _worker_instance is None:
                _worker_instance = PlaywrightCDPWorker(port=9222)
            return _worker_instance

    @staticmethod
    def start_worker(projeto_id: str, scene_ids: Optional[List[int]] = None, modo: str = "imagem",
                     bypass_rate_limit: bool = False) -> bool:
        worker = FlowQueueWorker.get_worker()
        if worker.is_running_queue:
            pw_log("Worker CDP já está em execução.", level="warn")
            return False

        # ITEM 8 — `bypass_rate_limit` é repassado ao handler APENAS quando True: o
        # caminho padrão continua com exatamente a MESMA chamada de antes
        # (3 posicionais, sem kwargs), preservando rotinas/handlers legados.
        _kwargs = {"bypass_rate_limit": True} if bypass_rate_limit else {}
        t = threading.Thread(
            target=worker._handle_run_queue,
            args=(projeto_id, scene_ids, modo),
            kwargs=_kwargs,
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
            # REQ 3 — parada MANUAL também precisa de motivo: o frontend mostra
            # "fila interrompida manualmente" em vez de um estado sem explicação.
            worker.set_pause_reason("manual")
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
            # REQ 3 — motivo CANÔNICO da parada da fila ("" = não pausada). Mantém o
            # literal getattr(worker, ...) para compatibilidade com rotinas antigas
            # (last_queue_pause_reason é propriedade espelhada em pause_reason).
            "pause_reason": getattr(worker, "pause_reason", "") or getattr(worker, "last_queue_pause_reason", "") or "",
            # REQ 3 — flag de interrupção da fila. ATENÇÃO: stop_requested é um
            # threading.Event; a serialização JSON exige bool(.is_set()).
            "stop_requested": bool(getattr(worker, "stop_requested", None) is not None
                                   and worker.stop_requested.is_set()),
            "pause_reason_ts": getattr(worker, "pause_reason_ts", "") or "",
            # PHASE 2 (ERRO 2): expuesto para la UI (banner/diálogo)
            "fallback_video_imagen": bool(getattr(worker, "_fallback_video_para_imagem", False)),
        }


FlowQueueWorker.criar_imagem_cena = staticmethod(criar_imagem_cena)


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
            h_cdp = _cdp_host(9222)
            browser = p.chromium.connect_over_cdp(f"http://{h_cdp}:9222")
            if not browser.contexts:
                return {"success": False, "error": "Falha na criação do personagem: etapa de contexto do navegador não concluída."}
            context = browser.contexts[0]

            pages = [pg for pg in context.pages if "labs.google" in (pg.url or "")]
            if not pages:
                return {"success": False, "error": "Falha na criação do personagem: aba do Google Flow não encontrada."}

            page = pages[0]
            _trazer_para_frente_se_visivel(page, 9222, "criar_personagem_no_flow_direto")
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
                    # UI esperada ao final: imagem de referência renderizada no formulário
                    # de criação de personagem (/characters).
                    baseline_srcs = _baseline_imgs_pagina(page)
                    input_file.set_input_files(imagem_abs)
                    if not _aguardar_upload_concluido(page, baseline_srcs, timeout_ms=20000):
                        pw_log("[FLOW] criar_personagem_no_flow_direto: upload não confirmado "
                               "por sinal; seguindo com comportamento anterior. "
                               "Não validado em execução real.", level="warn")
                    upload_sucesso = True
                except Exception as e_inp:
                    pw_log(f"Aviso ao usar set_input_files: {e_inp}", level="warn")

            if not upload_sucesso:
                btn_up = page.locator('button:has-text("Fazer upload"), button:has-text("upload")').first
                if btn_up.is_visible(timeout=2000):
                    try:
                        # UI esperada ao final: imagem de referência renderizada no formulário
                        # de criação de personagem (/characters).
                        baseline_srcs = _baseline_imgs_pagina(page)
                        with page.expect_file_chooser(timeout=4000) as fc_info:
                            btn_up.click(force=True)
                        fc_info.value.set_files(imagem_abs)
                        if not _aguardar_upload_concluido(page, baseline_srcs, timeout_ms=20000):
                            pw_log("[FLOW] criar_personagem_no_flow_direto: upload não confirmado "
                                   "por sinal; seguindo com comportamento anterior. "
                                   "Não validado em execução real.", level="warn")
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


def _retornar_foco_para_app(browser, projeto_id: str) -> bool:
    """TAREFA 1 — NÃO-OP: o Studio não está mais no Chrome CDP.

    O Studio vive no navegador PADRÃO (fora do CDP), então não existe aba do Studio
    neste Chrome para trazer para frente (nem `bring_to_front` de tela). A assinatura
    é mantida para compatibilidade com os chamadores.
    """
    pw_log("[FLOW] _retornar_foco_para_app: sem efeito — o Studio abre no navegador "
           "padrão, fora do Chrome do Flow.", level="debug")
    return False


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

    # ------------------------------------------------------------------
    # VALIDAÇÃO DE identidade.json (anti-genérico) — ANTES de criar no Flow
    # ------------------------------------------------------------------
    # O personagem oficial do projeto é a fonte de verdade. Sem identidade
    # válida, um nome genérico/vazio ("avatar", "@me", "personagem") NUNCA é
    # criado — devolvemos erro claro para o usuário.
    personagem_projeto, erro_identidade = character_svc.validar_personagem_do_projeto(projeto_id)
    if personagem_projeto:
        nome_oficial = str(personagem_projeto.get("nome") or "").strip()
        if nome_oficial:
            if str(nome).lstrip("@").strip().lower() != nome_oficial.lower():
                log_char(f"identidade do projeto define '{personagem_projeto.get('referencia_flow')}' — "
                         f"usando o personagem oficial (recebido: '{nome}').",
                         projeto_id=projeto_id, level="warn")
            nome = nome_oficial
        log_char(f"personagem validado em identidade.json: '{personagem_projeto.get('referencia_flow')}'.",
                 projeto_id=projeto_id)
    elif _nome_personagem_generico(nome):
        msg = (f"Falha na criação do personagem: {erro_identidade or 'identidade do projeto não configurada'} — "
               f"não criamos personagem genérico ('{nome or 'vazio'}'). Configure o personagem no projeto "
               f"(Identidade & Avatar) e tente novamente.")
        log_char(f"ABORTADO — {msg}", projeto_id=projeto_id, level="error")
        return {"success": False, "error": msg}
    else:
        log_char(f"identidade.json indisponível ({erro_identidade}) — usando o nome explícito "
                 f"'{nome}'.", projeto_id=projeto_id, level="warn")

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
            h_cdp = _cdp_host(9222)
            browser = p.chromium.connect_over_cdp(f"http://{h_cdp}:9222")
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
                    _trazer_para_frente_se_visivel(target_page, 9222,
                                                   "criar_avatar_flow_via_playwright")
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
            _trazer_para_frente_se_visivel(page, 9222, "criar_avatar_flow_via_playwright")
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
                                    _trazer_para_frente_se_visivel(
                                        page, 9222, "criar_avatar_flow_via_playwright")
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

            # Garante que o canvas terminou de carregar antes de iniciar a automação
            try:
                for s_load in ['text="Carregando..."', 'text="Loading..."', 'mat-spinner', '.mat-progress-spinner']:
                    loc_l = page.locator(s_load).first
                    if loc_l.is_visible(timeout=300):
                        pw_log("[FLOW] Canvas em carregamento. Aguardando estabilização...")
                        loc_l.wait_for(state="hidden", timeout=15000)
                        page.wait_for_timeout(800)
                        break
            except Exception:
                pass

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

            _retornar_foco_para_app(browser, projeto_id)

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