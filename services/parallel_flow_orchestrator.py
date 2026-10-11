"""
services/parallel_flow_orchestrator.py — Orquestrador Paralelo Multi-Conta
==========================================================================
Distribuição de produção de vídeo do Google Flow entre múltiplos navegadores Chrome CDP,
isolando portas CDP (9222..9226) e diretórios de perfil (lira_profiles/conta_XX)
com sincronização atômica em lira_scene_plan.json.
"""

import os
import sys
import json
import time
import math
import socket
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple

from config import PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
from services.playwright_flow import (
    PlaywrightCDPWorker,
    ensure_chrome_cdp,
    pw_log,
    _cdp_port_open,
    open_flow_tab,
    carregar_projeto_flow_url,
    FLOW_URL,
)


@dataclass
class BlocoCena:
    """Representa uma partição lógica de cenas atribuída a um worker específico."""
    bloco_id: int
    scene_ids: List[int]
    total_cenas: int
    inicio_idx: int
    fim_idx: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DivisorBlocos:
    """Divide um plano de cenas entre N contas respeitando os ciclos narrativos (múltiplos de 3)."""

    @staticmethod
    def dividir_plano(cenas: List[Dict[str, Any]], num_contas: int) -> List[BlocoCena]:
        """
        Calcula o tamanho de bloco ideal baseado em ceil(len(cenas) / num_contas)
        e ajusta para múltiplos de 3 para não quebrar a tríade Avatar(1) -> Imagem(2) -> Vídeo(3).

        Exemplo:
          223 cenas + 3 contas -> [75, 75, 73]
          223 cenas + 5 contas -> [45, 45, 45, 45, 43]
        """
        if not cenas or num_contas <= 0:
            return []

        total = len(cenas)
        num_contas = min(num_contas, total)

        if num_contas == 1:
            sids = [int(c.get("id") or c.get("scene_index") or (i + 1)) for i, c in enumerate(cenas)]
            return [BlocoCena(
                bloco_id=0,
                scene_ids=sids,
                total_cenas=len(sids),
                inicio_idx=0,
                fim_idx=len(sids) - 1
            )]

        tam_ideal = math.ceil(total / num_contas)
        # Ajusta para o próximo múltiplo de 3 para garantir ciclos completos
        if tam_ideal % 3 != 0:
            tam_bloco = ((tam_ideal + 2) // 3) * 3
        else:
            tam_bloco = tam_ideal

        blocos: List[BlocoCena] = []
        idx_atual = 0

        for i in range(num_contas):
            if i == num_contas - 1:
                # O último bloco recebe o restante das cenas
                cenas_bloco = cenas[idx_atual:]
            else:
                prox_idx = min(idx_atual + tam_bloco, total)
                cenas_bloco = cenas[idx_atual:prox_idx]

            if not cenas_bloco:
                break

            sids = [int(c.get("id") or c.get("scene_index") or (idx_atual + j + 1)) for j, c in enumerate(cenas_bloco)]
            blocos.append(BlocoCena(
                bloco_id=i,
                scene_ids=sids,
                total_cenas=len(sids),
                inicio_idx=idx_atual,
                fim_idx=idx_atual + len(sids) - 1
            ))
            idx_atual += len(cenas_bloco)
            if idx_atual >= total:
                break

        return blocos

    @staticmethod
    def calcular_cenas_restantes(
        projeto_id: str,
        lira_scene_plan: Optional[Dict[str, Any]] = None,
        modo: str = "imagem"
    ) -> List[int]:
        """
        Analisa o lira_scene_plan.json e identifica cenas pendentes de produção.
        No modo imagem:
          - status == 'BAIXADA' ou image_status == 'READY' ou arquivo > 500 bytes.
        No modo animação:
          - video_status == 'READY' E arquivo de vídeo (.mp4/.webm/.mov) > 5000 bytes.
        Retorna a lista ordenada de IDs inteiros das cenas pendentes/não feitas.
        """
        if lira_scene_plan is None:
            lira_scene_plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not lira_scene_plan:
            return []

        cenas = lira_scene_plan.get("cenas", [])
        pendentes_ids: List[int] = []

        for c in cenas:
            cid = int(c.get("id") or c.get("scene_index") or 0)
            status = str(c.get("status") or "").upper()
            img_status = str(c.get("image_status") or "").upper()
            vid_status = str(c.get("video_status") or "").upper()

            if modo == "animacao":
                tem_arquivo = False
                arq_midia = str(c.get("arquivo_video") or c.get("arquivo_midia") or c.get("download_path") or "").strip()
                if arq_midia and Path(arq_midia).exists() and Path(arq_midia).is_file():
                    try:
                        p_arq = Path(arq_midia)
                        if p_arq.suffix.lower() in (".mp4", ".webm", ".mov") and p_arq.stat().st_size > 5000:
                            tem_arquivo = True
                    except Exception:
                        pass

                if not tem_arquivo:
                    try:
                        t_ini = float(c.get("tempo_inicio") or c.get("start") or 0.0)
                        arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, t_ini)
                        if (
                            arq_disco
                            and arq_disco.exists()
                            and arq_disco.is_file()
                            and arq_disco.suffix.lower() in (".mp4", ".webm", ".mov")
                            and arq_disco.stat().st_size > 5000
                        ):
                            tem_arquivo = True
                    except Exception:
                        pass

                if vid_status == "READY" and tem_arquivo:
                    continue
                if not tem_arquivo:
                    pendentes_ids.append(cid)
            else:
                # 1. Se já está marcada como baixada / pronta, considera completa
                if status == "BAIXADA" or status == str(scene_plan_svc.STATUS_BAIXADA).upper() or img_status == "READY":
                    continue

                # 2. Se não está baixada, verifica se já existe arquivo de mídia válido em disco
                tem_arquivo = False
                arq_midia = str(c.get("arquivo_midia") or c.get("download_path") or "").strip()
                if arq_midia and Path(arq_midia).exists() and Path(arq_midia).is_file():
                    try:
                        if Path(arq_midia).stat().st_size > 500:
                            tem_arquivo = True
                    except Exception:
                        pass

                if not tem_arquivo:
                    try:
                        t_ini = float(c.get("tempo_inicio") or c.get("start") or 0.0)
                        arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, t_ini)
                        if arq_disco and arq_disco.exists() and arq_disco.is_file() and arq_disco.stat().st_size > 500:
                            tem_arquivo = True
                    except Exception:
                        pass

                if not tem_arquivo:
                    pendentes_ids.append(cid)

        return pendentes_ids


def _validar_pagina_flow(page) -> bool:
    """Verifica se a página é estritamente do Google Flow (flow.google.com ou labs.google/fx).
    Rejeita explicitamente 127.0.0.1, localhost, about:blank e páginas de erro/403.
    """
    if not page:
        return False
    try:
        if page.is_closed():
            return False
        url = (page.url or "").strip().lower()
        if not url or url == "about:blank":
            return False

        # Rejeita explicitamente URLs locais, chrome interno e abas indesejadas
        dominios_proibidos = ["127.0.0.1", "localhost", "about:blank", "chrome://", "flowmusic"]
        if any(p in url for p in dominios_proibidos):
            return False

        # Aceita APENAS flow.google.com ou labs.google/fx
        if not ("flow.google.com" in url or "labs.google/fx" in url):
            return False

        # Verifica se houve erro 403 / Forbidden / Access Denied no título ou corpo
        title = ""
        try:
            title = (page.title() or "").lower()
        except Exception:
            pass

        for err in ["403", "forbidden", "access denied", "acesso negado", "erro ao carregar", "não é possível acessar"]:
            if err in title:
                return False

        texto = ""
        try:
            texto = (page.evaluate("() => (document.body ? document.body.innerText : '') || ''") or "").lower()
        except Exception:
            pass

        for err in ["403", "forbidden", "access denied", "acesso negado"]:
            if err in texto and len(texto) < 400:
                return False

        # Verifica se não é página totalmente em branco
        if len(texto.strip()) < 10:
            try:
                has_elements = page.evaluate("() => Boolean(document.querySelector('canvas, svg, img, video, main, [role=\"main\"], #root, #app, button'))")
                if not has_elements:
                    return False
            except Exception:
                return False

        return True
    except Exception:
        return False


def _testar_porta_cdp(porta: int, timeout: float = 10.0) -> bool:
    """Testa se a porta CDP está aberta e aceitando conexões."""
    s = None
    try:
        s = socket.create_connection(("127.0.0.1", porta), timeout=timeout)
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False
    finally:
        if s is not None and hasattr(s, "close"):
            try:
                s.close()
            except Exception:
                pass


def inicializar_worker_seguro(
    worker_id: int,
    conta: Any,
    porta_cdp: int,
    driver: Optional[PlaywrightCDPWorker] = None,
    worker_instancia: Optional['WorkerInstancia'] = None
) -> bool:
    """
    Inicializa e valida um worker em 4 passos estritos antes de iniciar a produção:
      PASSO 1 — CDP respondendo (timeout 10s, max 3 tentativas com 3s de intervalo)
      PASSO 2 — Janela do Flow no estado correto (navega via open_flow_tab, aguarda domcontentloaded 15s)
      PASSO 3 — Login válido (chama _verificar_sessao_google na página do Flow)
      PASSO 4 — Liberar (status PRONTO ✅)
    """
    if isinstance(conta, dict):
        email = conta.get("email") or f"conta_{worker_id+1}"
    else:
        email = str(conta)

    if worker_instancia:
        worker_instancia._atualizar_telemetria(
            status="VERIFICANDO",
            mensagem=f"Verificando CDP na porta {porta_cdp}..."
        )

    # =========================================================================
    # PASSO 1 — CDP respondendo
    # =========================================================================
    cdp_conectado = False
    for tentativa in range(1, 4):
        if _testar_porta_cdp(porta_cdp, timeout=10.0) or _cdp_port_open(porta_cdp):
            cdp_conectado = True
            break

        # Se não respondeu, tenta garantir Chrome se houver profile_dir configurado
        prof_dir = getattr(driver, "profile_dir", None) if driver else (
            conta.get("profile_dir") if isinstance(conta, dict) else None
        )
        if prof_dir:
            ensure_chrome_cdp(porta_cdp, profile_dir=prof_dir, worker_id=worker_id)
            if _testar_porta_cdp(porta_cdp, timeout=10.0) or _cdp_port_open(porta_cdp):
                cdp_conectado = True
                break

        if tentativa < 3:
            time.sleep(3.0)

    if not cdp_conectado:
        msg = f"Worker {worker_id}: CDP porta {porta_cdp} não respondeu — DESCARTADO"
        pw_log(msg, level="error")
        log_event("ORCHESTRATOR", msg, level="error")
        if worker_instancia:
            worker_instancia._atualizar_telemetria(status="DESCARTADO", mensagem=msg)
        return False

    # Conecta sessão Playwright na thread do worker caso ainda não conectada
    if driver:
        if driver.page is None:
            ok_sessao, msg_sessao = driver._iniciar_sessao_thread()
            if not ok_sessao:
                msg = f"Worker {worker_id}: CDP porta {porta_cdp} não respondeu — DESCARTADO"
                pw_log(f"{msg} ({msg_sessao})", level="error")
                log_event("ORCHESTRATOR", f"{msg} ({msg_sessao})", level="error")
                if worker_instancia:
                    worker_instancia._atualizar_telemetria(status="DESCARTADO", mensagem=msg)
                return False

        # CORREÇÃO B & C: Garante navegação para o Google Flow ANTES do Passo 2 e Passo 3
        if driver.page:
            target_url = None
            if getattr(driver, "current_project_id", None):
                try:
                    target_url = carregar_projeto_flow_url(driver.current_project_id)
                except Exception:
                    pass
            target_url = target_url or FLOW_URL
            try:
                _pagina_flow = open_flow_tab(driver.page, target_url=target_url)
                if _pagina_flow is None:
                    # open_flow_tab pode devolver None (ex.: página do Studio sem contexto).
                    # Mantém `driver.page` como está e segue pelo caminho JÁ existente de
                    # "aba indisponível": o Passo 2 (abaixo) valida a página e, falhando,
                    # o worker é DESCARTADO — sem criar estrutura nova nem AttributeError.
                    pw_log(
                        f"[Worker {worker_id}] Aviso: open_flow_tab não devolveu aba do Flow "
                        f"(None) — seguindo para a validação da página (Passo 2).",
                        level="warn",
                    )
                else:
                    driver.page = _pagina_flow
            except Exception as e_opt:
                pw_log(f"[Worker {worker_id}] Aviso open_flow_tab: {e_opt}", level="warn")

            try:
                if driver.page and not driver.page.is_closed():
                    driver.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception as e_load:
                pw_log(f"[Worker {worker_id}] Aviso ao aguardar domcontentloaded: {e_load}", level="warn")

    # =========================================================================
    # PASSO 2 — Janela do Flow no estado correto
    # =========================================================================
    if worker_instancia:
        worker_instancia._atualizar_telemetria(
            status="VERIFICANDO",
            mensagem="Verificando integridade da aba Google Flow..."
        )

    flow_ok = False
    if driver and driver.page:
        flow_ok = _validar_pagina_flow(driver.page)
        if not flow_ok:
            pw_log(f"[Worker {worker_id}] Flow não validado na 1ª tentativa. Aguardando 5s para recarregar...", level="warn")
            time.sleep(5.0)
            try:
                if driver.page and not driver.page.is_closed():
                    driver.page.reload(timeout=15000)
                    driver.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception as e_reload:
                pw_log(f"[Worker {worker_id}] Falha ao recarregar página: {e_reload}", level="warn")

            flow_ok = _validar_pagina_flow(driver.page)

    if not flow_ok:
        msg = f"Worker {worker_id}: Flow não carregou — DESCARTADO"
        pw_log(msg, level="error")
        log_event("ORCHESTRATOR", msg, level="error")
        if worker_instancia:
            worker_instancia._atualizar_telemetria(status="DESCARTADO", mensagem=msg)
        return False

    # =========================================================================
    # PASSO 3 — Login válido
    # =========================================================================
    if worker_instancia:
        worker_instancia._atualizar_telemetria(
            status="VERIFICANDO",
            mensagem=f"Verificando sessão Google de {email}..."
        )

    sessao_ativa = False
    if driver and hasattr(driver, "_verificar_sessao_google"):
        sessao_ativa = driver._verificar_sessao_google()

    if not sessao_ativa:
        msg = f"Worker {worker_id}: conta {email} sem sessão — DESCARTADO"
        pw_log(msg, level="error")
        log_event("ORCHESTRATOR", msg, level="error")
        if worker_instancia:
            worker_instancia._atualizar_telemetria(status="DESCARTADO", mensagem=msg)
        return False

    # =========================================================================
    # PASSO 4 — Liberar
    # =========================================================================
    msg_pronto = f"Worker {worker_id}: PRONTO ✅ — conta {email}"
    pw_log(msg_pronto, level="info")
    log_event("ORCHESTRATOR", msg_pronto, level="info")
    if worker_instancia:
        worker_instancia._atualizar_telemetria(status="PRONTO", mensagem=msg_pronto)

    return True


class WorkerInstancia:
    """Instância isolada de worker para uma conta/porta CDP específica."""

    def __init__(
        self,
        worker_id: int,
        conta_info: Dict[str, Any],
        cdp_port: int,
        bloco_cenas: List[Dict[str, Any]],
        projeto_id: str,
        modo: str = "imagem",
    ):
        self.worker_id = worker_id
        self.conta_info = conta_info or {}
        self.cdp_port = cdp_port
        self.bloco_cenas = bloco_cenas or []
        self.projeto_id = projeto_id
        self.modo = modo

        profile_rel = self.conta_info.get("profile_dir", f"lira_profiles/conta_{self.conta_info.get('id', worker_id+1):02d}")
        self.profile_dir = str(Path.home() / profile_rel)
        self.driver = PlaywrightCDPWorker(port=self.cdp_port, profile_dir=self.profile_dir, worker_id=self.worker_id)
        self.driver.current_project_id = self.projeto_id
        self.thread: Optional[threading.Thread] = None
        self.stop_requested = threading.Event()

        email_conta = self.conta_info.get("email", f"worker_{worker_id}")
        faixa_cenas = f"{bloco_cenas[0].get('id')}-{bloco_cenas[-1].get('id')}" if bloco_cenas else "0-0"

        self.telemetria: Dict[str, Any] = {
            "worker_id": self.worker_id,
            "conta_id": self.conta_info.get("id", self.worker_id + 1),
            "conta_email": email_conta,
            "cdp_port": self.cdp_port,
            "bloco_cenas": faixa_cenas,
            "total_cenas": len(self.bloco_cenas),
            "cenas_concluidas": 0,
            "cena_atual": self.bloco_cenas[0].get("id") if self.bloco_cenas else None,
            "status": "AGUARDANDO",
            "progresso_pct": 0,
            "erros": 0,
            "mensagem": "Aguardando inicialização...",
        }
        self._lock = threading.Lock()

    def rodar(self):
        """Executa o loop de produção do lote atribuído a este worker."""
        log_event("ORCHESTRATOR", f"[Worker {self.worker_id}] Iniciando processamento do bloco {self.telemetria['bloco_cenas']} na porta {self.cdp_port}...")
        self._atualizar_telemetria(status="VERIFICANDO", mensagem=f"Iniciando verificação do worker {self.worker_id}...")

        try:
            # TAREFA 2: Inicialização segura em 4 passos (CDP -> Login -> Flow -> Pronto)
            seguro = inicializar_worker_seguro(
                worker_id=self.worker_id,
                conta=self.conta_info,
                porta_cdp=self.cdp_port,
                driver=self.driver,
                worker_instancia=self
            )
            if not seguro:
                # Worker descartado isoladamente; não afeta os demais workers
                return

            if not self.driver._ensure_project_open(self.projeto_id, timeout_s=10):
                self._atualizar_telemetria(status="ERRO", mensagem="Projeto não encontrado ou não aberto no Google Flow.")
                log_event("ORCHESTRATOR", f"[Worker {self.worker_id}] Projeto {self.projeto_id} não aberto no Flow", level="error")
                return

            try:
                self.driver._upload_avatar_projeto(self.projeto_id)
            except Exception as e_av:
                pw_log(f"[Worker {self.worker_id}] Aviso upload avatar: {e_av}", level="warn")

            total_bloco = len(self.bloco_cenas)
            concluidas = 0

            for idx, cena in enumerate(self.bloco_cenas, 1):
                if self.stop_requested.is_set():
                    self._atualizar_telemetria(status="PAUSADO", mensagem="Produção interrompida pelo operador.")
                    log_event("ORCHESTRATOR", f"[Worker {self.worker_id}] Interrompido por stop_requested.")
                    break

                cid = int(cena.get("id", 0))
                pct = int((concluidas / total_bloco) * 100) if total_bloco else 0
                self._atualizar_telemetria(
                    cena_atual=cid,
                    status="GERANDO",
                    progresso_pct=pct,
                    mensagem=f"Gerando Cena {cid:03d} ({idx}/{total_bloco})..."
                )

                # Verifica se arquivo já existe em disco (retomada inteligente)
                arq_disco = scene_plan_svc.resolver_arquivo_cena(self.projeto_id, cid, float(cena.get("tempo_inicio", 0)))
                if self.modo == "animacao":
                    tem_arquivo = bool(
                        arq_disco
                        and arq_disco.exists()
                        and arq_disco.is_file()
                        and arq_disco.suffix.lower() in (".mp4", ".webm", ".mov")
                        and arq_disco.stat().st_size > 5000
                    )
                else:
                    tem_arquivo = bool(arq_disco and arq_disco.exists() and arq_disco.is_file() and arq_disco.stat().st_size > 500)

                if tem_arquivo:
                    concluidas += 1
                    pct_now = int((concluidas / total_bloco) * 100)
                    self._atualizar_telemetria(cenas_concluidas=concluidas, progresso_pct=pct_now)
                    continue

                # Processa cena individual no Google Flow
                ok, res_msg = self.driver._processar_cena_individual(
                    self.projeto_id,
                    cena,
                    is_anim=(self.modo == "animacao"),
                    index=idx,
                    total_cenas=total_bloco
                )

                if ok:
                    concluidas += 1
                    pct_now = int((concluidas / total_bloco) * 100)
                    self._atualizar_telemetria(
                        cenas_concluidas=concluidas,
                        progresso_pct=pct_now,
                        mensagem=f"Cena {cid:03d} concluída."
                    )
                else:
                    erros_atual = self.telemetria.get("erros", 0) + 1
                    self._atualizar_telemetria(erros=erros_atual, mensagem=f"Falha na Cena {cid:03d}: {res_msg}")

                # Rate-limit suave entre cenas
                time.sleep(2.0)

            if not self.stop_requested.is_set():
                self._atualizar_telemetria(
                    status="CONCLUIDO",
                    progresso_pct=100,
                    mensagem="Bloco concluído com sucesso."
                )
                log_event("ORCHESTRATOR", f"[Worker {self.worker_id}] Bloco concluído com sucesso ({concluidas}/{total_bloco}).")

        except Exception as e:
            self._atualizar_telemetria(status="ERRO", mensagem=f"Exceção no worker: {e}")
            log_event("ORCHESTRATOR", f"[Worker {self.worker_id}] Exceção: {e}", level="error")
        finally:
            try:
                self.driver._encerrar_sessao()
            except Exception:
                pass

    def parar(self):
        """Solicita a interrupção do worker."""
        self.stop_requested.set()
        if hasattr(self.driver, "stop_requested"):
            self.driver.stop_requested.set()
        self._atualizar_telemetria(status="PAUSADO", mensagem="Parada solicitada.")

    def obter_telemetria(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.telemetria)

    def _atualizar_telemetria(self, **campos):
        with self._lock:
            self.telemetria.update(campos)


class OrchestradorParalelo:
    """Gerencia o pool de workers paralelos executando blocos do plano de cenas."""

    def __init__(self, projeto_id: str, num_contas: int = None):
        self.projeto_id = projeto_id
        if num_contas is None:
            from config import MAX_CONTAS_SIMULTANEAS
            num_contas = MAX_CONTAS_SIMULTANEAS
        self.num_contas = num_contas
        self.workers: List[WorkerInstancia] = []
        self._lock = threading.Lock()
        self.is_running = False
        self.iniciado_em: Optional[float] = None

    def iniciar_fila_paralela(self, projeto_id: str, num_contas: int, modo: str = "imagem") -> Dict[str, Any]:
        """Inicia os N workers em paralelo fatiando o lira_scene_plan.json."""
        with self._lock:
            if self.is_running:
                return {"success": False, "error": "Orquestrador já está em execução para este projeto."}

            self.projeto_id = projeto_id
            # TAREFA 1 — clamp do número de contas/Chromes simultâneos (padrão 3).
            from config import MAX_CONTAS_SIMULTANEAS
            self.num_contas = max(1, min(num_contas, MAX_CONTAS_SIMULTANEAS))

            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
            if not plan or not plan.get("cenas"):
                return {"success": False, "error": "Plano de cenas não encontrado ou vazio."}

            cenas = plan["cenas"]
            blocos = DivisorBlocos.dividir_plano(cenas, self.num_contas)
            if not blocos:
                return {"success": False, "error": "Não foi possível dividir as cenas em blocos."}

            # Carrega contas disponíveis
            accounts_path = Path("config/flow_accounts.json")
            contas = []
            if accounts_path.exists():
                try:
                    acc_data = json.loads(accounts_path.read_text(encoding="utf-8"))
                    contas = acc_data.get("contas", [])
                except Exception as e:
                    pw_log(f"[ORCHESTRATOR] Erro ao ler flow_accounts.json: {e}", level="error")

            self.workers = []
            PORTA_BASE = 9222

            for i, bloco in enumerate(blocos):
                conta_info = contas[i] if i < len(contas) else {"id": i + 1, "profile_dir": f"lira_profiles/conta_{i+1:02d}"}
                cdp_port = PORTA_BASE + i
                cenas_bloco = cenas[bloco.inicio_idx : bloco.fim_idx + 1]

                worker = WorkerInstancia(
                    worker_id=i,
                    conta_info=conta_info,
                    cdp_port=cdp_port,
                    bloco_cenas=cenas_bloco,
                    projeto_id=projeto_id,
                    modo=modo
                )
                self.workers.append(worker)

            # Inicia threads dos workers
            self.is_running = True
            self.iniciado_em = time.time()

            for worker in self.workers:
                t = threading.Thread(
                    target=worker.rodar,
                    daemon=True,
                    name=f"WorkerParalelo-{worker.worker_id}-{worker.cdp_port}"
                )
                worker.thread = t
                t.start()

            log_event("ORCHESTRATOR", f"{projeto_id}: {len(self.workers)} workers iniciados em paralelo.")

            return {
                "success": True,
                "workers_iniciados": len(self.workers),
                "blocos": [b.to_dict() for b in blocos]
            }

    def redividir_blocos_residuais(self, projeto_id: str, num_workers_novo: int, modo: str = "imagem") -> List[BlocoCena]:
        """
        Calcula as cenas não feitas e as redivide dinamicamente entre N workers.
        Se todas as cenas já estiverem completas, retorna lista vazia.
        """
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return []

        ids_restantes = DivisorBlocos.calcular_cenas_restantes(projeto_id, plan, modo=modo)
        if not ids_restantes:
            return []

        set_ids = set(ids_restantes)
        cenas_restantes = [c for c in plan.get("cenas", []) if int(c.get("id") or c.get("scene_index") or 0) in set_ids]

        return DivisorBlocos.dividir_plano(cenas_restantes, num_workers_novo)

    def relancar_fila_residual(self, projeto_id: str, num_workers_novo: int, modo: str = "imagem") -> Dict[str, Any]:
        """
        Redimensiona os workers, recalcula blocos apenas das cenas restantes e relança a produção em paralelo.
        """
        with self._lock:
            # 1. Pausa workers atuais
            for w in self.workers:
                w.parar()
            self.is_running = False

            time.sleep(0.5)

            # 2. Carrega plano e identifica cenas restantes
            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
            if not plan or not plan.get("cenas"):
                return {"success": False, "error": "Plano de cenas não encontrado.", "cenas_restantes": 0}

            ids_restantes = DivisorBlocos.calcular_cenas_restantes(projeto_id, plan, modo=modo)
            if not ids_restantes:
                return {
                    "success": False,
                    "error": "Projeto já completo",
                    "cenas_restantes": 0,
                    "mensagem": "Todas as cenas do projeto já foram concluídas com sucesso."
                }

            set_ids = set(ids_restantes)
            cenas_restantes = [c for c in plan.get("cenas", []) if int(c.get("id") or c.get("scene_index") or 0) in set_ids]
            cenas_por_id = {int(c.get("id") or c.get("scene_index") or 0): c for c in cenas_restantes}

            num_workers_novo = max(1, min(int(num_workers_novo), 12))
            blocos = DivisorBlocos.dividir_plano(cenas_restantes, num_workers_novo)
            if not blocos:
                return {
                    "success": False,
                    "error": "Não foi possível dividir as cenas residuais.",
                    "cenas_restantes": len(ids_restantes)
                }

            # 3. Carrega contas disponíveis
            accounts_path = Path("config/flow_accounts.json")
            contas = []
            if accounts_path.exists():
                try:
                    acc_data = json.loads(accounts_path.read_text(encoding="utf-8"))
                    contas = acc_data.get("contas", [])
                except Exception as e:
                    pw_log(f"[ORCHESTRATOR] Erro ao ler flow_accounts.json: {e}", level="error")

            # 4. Redimensiona pool de workers
            num_alvo = len(blocos)
            if num_alvo < len(self.workers):
                for w in self.workers[num_alvo:]:
                    w.parar()
                self.workers = self.workers[:num_alvo]

            PORTA_BASE = 9222
            novos_workers: List[WorkerInstancia] = []

            for i, bloco in enumerate(blocos):
                cenas_bloco = [cenas_por_id[sid] for sid in bloco.scene_ids if sid in cenas_por_id]
                conta_info = contas[i] if i < len(contas) else {
                    "id": i + 1,
                    "profile_dir": f"lira_profiles/conta_{i+1:02d}",
                    "email": f"conta_{i+1:02d}@gmail.com"
                }
                cdp_port = PORTA_BASE + i
                faixa_cenas = f"{cenas_bloco[0].get('id')}-{cenas_bloco[-1].get('id')}" if cenas_bloco else "0-0"

                if i < len(self.workers):
                    w = self.workers[i]
                    w.worker_id = i
                    w.cdp_port = cdp_port
                    w.conta_info = conta_info
                    w.bloco_cenas = cenas_bloco
                    w.modo = modo
                    w.projeto_id = projeto_id
                    if hasattr(w.driver, "worker_id"):
                        w.driver.worker_id = i
                    if hasattr(w.driver, "port"):
                        w.driver.port = cdp_port
                    if hasattr(w.driver, "current_project_id"):
                        w.driver.current_project_id = projeto_id
                    w.stop_requested.clear()
                    if hasattr(w.driver, "stop_requested"):
                        w.driver.stop_requested.clear()
                    w._atualizar_telemetria(
                        cdp_port=cdp_port,
                        bloco_cenas=faixa_cenas,
                        total_cenas=len(cenas_bloco),
                        cenas_concluidas=0,
                        cena_atual=cenas_bloco[0].get("id") if cenas_bloco else None,
                        status="AGUARDANDO",
                        progresso_pct=0,
                        erros=0,
                        mensagem="Aguardando inicialização..."
                    )
                    novos_workers.append(w)
                else:
                    novo_w = WorkerInstancia(
                        worker_id=i,
                        conta_info=conta_info,
                        cdp_port=cdp_port,
                        bloco_cenas=cenas_bloco,
                        projeto_id=projeto_id,
                        modo=modo
                    )
                    novos_workers.append(novo_w)

            self.workers = novos_workers
            self.num_contas = len(self.workers)
            self.is_running = True
            self.iniciado_em = time.time()

            # 5. Inicia threads dos workers
            for worker in self.workers:
                t = threading.Thread(
                    target=worker.rodar,
                    daemon=True,
                    name=f"WorkerParaleloResidual-{worker.worker_id}-{worker.cdp_port}"
                )
                worker.thread = t
                t.start()

            log_event("ORCHESTRATOR", f"{projeto_id}: Fila residual relançada com {len(self.workers)} workers para {len(ids_restantes)} cenas.")

            return {
                "success": True,
                "blocos_atualizados": [b.to_dict() for b in blocos],
                "cenas_restantes": len(ids_restantes),
                "workers_novos": len(self.workers),
                "mensagem": f"Fila residual relançada com {len(self.workers)} workers para {len(ids_restantes)} cenas restantes."
            }

    # Alias com cedilha para suporte transparente
    relançar_fila_residual = relancar_fila_residual

    def pausar_tudo(self):
        """Pausa todos os workers ativos."""
        with self._lock:
            for w in self.workers:
                w.parar()
            self.is_running = False
        log_event("ORCHESTRATOR", f"{self.projeto_id}: Todos os workers pausados.")

    def cancelar_tudo(self):
        """Cancela e interrompe todos os workers."""
        self.pausar_tudo()
        log_event("ORCHESTRATOR", f"{self.projeto_id}: Todos os workers cancelados.")

    def obter_telemetria_consolidada(self) -> Dict[str, Any]:
        """Retorna telemetria consolidada de todos os workers para o HUD."""
        with self._lock:
            telemetrias_workers = [w.obter_telemetria() for w in self.workers]
            total_cenas = sum(w.get("total_cenas", 0) for w in telemetrias_workers)
            cenas_concluidas = sum(w.get("cenas_concluidas", 0) for w in telemetrias_workers)
            erros_total = sum(w.get("erros", 0) for w in telemetrias_workers)

            progresso_global = int((cenas_concluidas / total_cenas) * 100) if total_cenas else 0
            todos_concluidos = len(telemetrias_workers) > 0 and all(
                w.get("status") in ("CONCLUIDO", "PAUSADO", "ERRO", "DESCARTADO") for w in telemetrias_workers
            )
            if todos_concluidos and self.is_running:
                self.is_running = False

            return {
                "projeto_id": self.projeto_id,
                "is_running": self.is_running,
                "num_workers": len(self.workers),
                "total_cenas": total_cenas,
                "cenas_concluidas": cenas_concluidas,
                "erros_total": erros_total,
                "progresso_pct": progresso_global,
                "tempo_decorrido": round(time.time() - self.iniciado_em, 1) if self.iniciado_em else 0.0,
                "workers": telemetrias_workers,
            }


# Registro global de orquestradores em memória por projeto_id
_ORQUESTRADORES_PARALELOS: Dict[str, OrchestradorParalelo] = {}
_REGISTRY_LOCK = threading.Lock()


def obter_orquestrador(projeto_id: str) -> Optional[OrchestradorParalelo]:
    with _REGISTRY_LOCK:
        return _ORQUESTRADORES_PARALELOS.get(projeto_id)


def registrar_orquestrador(projeto_id: str, orquestrador: OrchestradorParalelo):
    with _REGISTRY_LOCK:
        _ORQUESTRADORES_PARALELOS[projeto_id] = orquestrador
