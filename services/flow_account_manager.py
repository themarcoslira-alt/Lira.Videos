"""
services/flow_account_manager.py — Gerenciador de contas Google Flow
====================================================================
Fonte de verdade: config/flow_accounts.json (o MESMO arquivo usado por
services/playwright_flow.py e app_web.py).

Responsabilidades:
- Auto-reset diário dos créditos (renovação à meia-noite local).
- Rotação inteligente: troca automática da conta ativa quando ela esgota.
- Troca manual de conta (UI/API) e listagem de status para o front-end.
- TAREFA 1 (orçamento de produção): persistir o SALDO REAL lido do Flow
  (`saldo_lido` + `saldo_lido_em`) e MEDIR o custo real por geração
  (imagem/vídeo) comparando duas leituras consecutivas da MESMA conta.

NÃO altera o schema existente do JSON: apenas ACRESCENTA os campos
"creditos_disponiveis", "ultima_renovacao", "ultimo_reset_data" e, na v1.8,
"saldo_lido"/"saldo_lido_em". O reset diário continua mexendo SÓ em flags.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from services.event_logger import log_event
except Exception:  # pragma: no cover — log é sempre opcional
    def log_event(*_args, **_kwargs):
        pass


# =====================================================================
# TAREFA 1c/1d — CUSTO MEDIDO por geração (imagem/vídeo)
# ---------------------------------------------------------------------
# Fonte: duas leituras consecutivas do saldo da MESMA conta. O que estiver
# entre elas é o custo daquela geração. O par só é aceito quando houver
# EXATAMENTE UMA geração clara no intervalo (0 ou 2+ gerações = ambíguo
# = descartado). Custo negativo também é descartado.
#
# Amostras: logs/flow_custos.jsonl  {ts, conta, tipo, antes, depois, custo}
# Padrões:  config/flow_custos.json {video, imagem, fonte} — criado se faltar
# O planejador troca cada valor pela MEDIANA das últimas 20 amostras do tipo
# quando houver >= 3 amostras (fonte passa a "medido").
# =====================================================================
_FAM_BASE_DIR = Path(__file__).resolve().parents[1]
CUSTOS_CONFIG_PATH = _FAM_BASE_DIR / "config" / "flow_custos.json"
CUSTOS_LEDGER_PATH = _FAM_BASE_DIR / "logs" / "flow_custos.jsonl"
CUSTOS_PADRAO: Dict[str, Any] = {"video": 10, "imagem": 0, "fonte": "padrao"}
CUSTO_AMOSTRAS_MEDIANA = 20
CUSTO_MIN_AMOSTRAS = 3

_custos_lock = threading.RLock()
# conta_id(str) -> {"saldo": int, "ts": str}
_ultimo_saldo_por_conta: Dict[str, Dict[str, Any]] = {}
# conta_id(str) -> [tipo, ...] das gerações desde a última leitura
_geracoes_pendentes: Dict[str, List[str]] = {}

_TIPOS_VIDEO = ("video", "videos", "true", "1")


def _chave_conta(conta_id: Any) -> str:
    """Normaliza o identificador da conta (None -> 'ativa')."""
    if conta_id is None or conta_id == "":
        return "ativa"
    return str(conta_id)


def _normalizar_tipo(tipo: Any) -> str:
    """'video' ou 'imagem' — qualquer coisa diferente de vídeo vira imagem."""
    if tipo is True:
        return "video"
    if str(tipo).strip().lower() in _TIPOS_VIDEO:
        return "video"
    return "imagem"


def ler_custos_config() -> Dict[str, Any]:
    """Lê `config/flow_custos.json` (cria com os padrões quando não existe)."""
    with _custos_lock:
        if CUSTOS_CONFIG_PATH.exists():
            try:
                data = json.loads(CUSTOS_CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    base = dict(CUSTOS_PADRAO)
                    for k in ("video", "imagem", "fonte"):
                        if k in data:
                            base[k] = data[k]
                    return base
            except Exception as e:
                log_event("FLOW_CUSTOS", f"Erro ao ler {CUSTOS_CONFIG_PATH}: {e}", level="error")
        try:
            CUSTOS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CUSTOS_CONFIG_PATH.write_text(
                json.dumps(CUSTOS_PADRAO, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log_event("FLOW_CUSTOS", f"Erro ao criar {CUSTOS_CONFIG_PATH}: {e}", level="error")
        return dict(CUSTOS_PADRAO)


def registrar_geracao(tipo: Any, conta_id: Any = None) -> None:
    """Marca UMA geração ocorrida (`tipo`: 'video'/'imagem') para a conta.

    Pares com 2+ gerações no intervalo são AMBÍGUOS e descartados pelo
    `registrar_leitura_saldo` — por isso guardamos quantas aconteceram.
    """
    chave = _chave_conta(conta_id)
    with _custos_lock:
        lista = _geracoes_pendentes.setdefault(chave, [])
        if len(lista) < 25:
            lista.append(_normalizar_tipo(tipo))



def _mediana(valores: List[int]) -> Optional[int]:
    """Mediana (média dos dois centrais quando par). None para lista vazia."""
    if not valores:
        return None
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return ordenados[meio]
    return int(round((ordenados[meio - 1] + ordenados[meio]) / 2.0))


def _ler_amostras_ledger(tipo: str, limite: int = CUSTO_AMOSTRAS_MEDIANA) -> List[int]:
    """Últimas `limite` amostras de custo do tipo no ledger (ignora inválidas)."""
    if not CUSTOS_LEDGER_PATH.exists():
        return []
    amostras: List[int] = []
    try:
        with CUSTOS_LEDGER_PATH.open("r", encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    obj = json.loads(linha)
                except Exception:
                    continue
                if _normalizar_tipo(obj.get("tipo")) != tipo:
                    continue
                try:
                    custo = int(obj.get("custo"))
                except (TypeError, ValueError):
                    continue
                if custo < 0:
                    continue
                amostras.append(custo)
    except Exception as e:
        log_event("FLOW_CUSTOS", f"Erro ao ler ledger {CUSTOS_LEDGER_PATH}: {e}", level="error")
        return []
    return amostras[-limite:]


def _append_ledger(linha: Dict[str, Any]) -> bool:
    """Acrescenta UMA linha JSON ao ledger (`logs/flow_custos.jsonl`)."""
    try:
        CUSTOS_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CUSTOS_LEDGER_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        log_event("FLOW_CUSTOS", f"Erro ao gravar {CUSTOS_LEDGER_PATH}: {e}", level="error")
        return False


def custos_atuais() -> Dict[str, Any]:
    """Custo por geração com a fonte de cada valor.

    `fonte_video`/`fonte_imagem` só viram "medido" com >= `CUSTO_MIN_AMOSTRAS`
    amostras reais no ledger; caso contrário permanece "padrao" (e a UI
    sinaliza "não medido").
    """
    cfg = ler_custos_config()
    resultado: Dict[str, Any] = {
        "video": int(cfg.get("video", 10) or 0),
        "imagem": int(cfg.get("imagem", 0) or 0),
        "fonte": str(cfg.get("fonte") or "padrao"),
        "fonte_video": "padrao",
        "fonte_imagem": "padrao",
        "amostras": {},
        "medido": {},
    }
    for tipo in ("video", "imagem"):
        amostras = _ler_amostras_ledger(tipo)
        resultado["amostras"][tipo] = len(amostras)
        resultado["medido"][tipo] = False
        if len(amostras) >= CUSTO_MIN_AMOSTRAS:
            med = _mediana(amostras)
            if med is not None:
                resultado[tipo] = med
                resultado["fonte_%s" % tipo] = "medido"
                resultado["medido"][tipo] = True
                resultado["fonte"] = "medido"
    return resultado


def registrar_leitura_saldo(
    conta_id: Any = None,
    saldo: Any = None,
    quando: Optional[str] = None,
    tipo_geracao: Any = None,
) -> Optional[Dict[str, Any]]:
    """Registra uma leitura de saldo e, se o par for claro, grava o custo.

    - `tipo_geracao` (opcional): a ÚNICA geração ocorrida entre a leitura
      anterior e esta (caminho do chamador que já conhece o tipo da cena).
    - Sem `tipo_geracao`, usa as gerações marcadas por `registrar_geracao`.

    Descarta: primeira leitura da conta (sem "antes"), pares ambíguos
    (0 ou 2+ gerações) e custo negativo. Retorna a linha gravada ou None.
    """
    try:
        saldo_int = int(saldo)
    except (TypeError, ValueError):
        return None
    chave = _chave_conta(conta_id)
    ts = quando or datetime.now().isoformat()
    with _custos_lock:
        anterior = _ultimo_saldo_por_conta.get(chave)
        pendentes = list(_geracoes_pendentes.get(chave, []))
        if tipo_geracao is not None:
            pendentes.append(_normalizar_tipo(tipo_geracao))
        _geracoes_pendentes[chave] = []
        _ultimo_saldo_por_conta[chave] = {"saldo": saldo_int, "ts": ts}
        if not anterior:
            return None
        if len(pendentes) != 1:
            return None
        antes = int(anterior.get("saldo", 0))
        custo = antes - saldo_int
        if custo < 0:
            return None
        linha = {
            "ts": ts,
            "conta": chave,
            "tipo": pendentes[0],
            "antes": antes,
            "depois": saldo_int,
            "custo": custo,
        }
        _append_ledger(linha)
        return linha


def limpar_estado_custo() -> None:
    """Zera o estado EM MEMÓRIA do ledger (uso em testes/retomadas)."""
    with _custos_lock:
        _ultimo_saldo_por_conta.clear()
        _geracoes_pendentes.clear()


class FlowAccountManager:
    """Gerencia as contas do Google Flow usadas na produção automática."""

    # Raiz do projeto (uma pasta acima de 'services/')
    BASE_DIR = Path(__file__).resolve().parents[1]
    CONFIG_PATH = BASE_DIR / "config" / "flow_accounts.json"
    # RLock: o worker Playwright roda em thread separada das rotas Flask.
    _lock = threading.RLock()

    # ------------------------------------------------------------- IO básico
    def _carregar(self) -> Dict[str, Any]:
        """Lê o config (nunca lança exceção; sempre devolve um dict válido)."""
        if self.CONFIG_PATH.exists():
            try:
                data = json.loads(self.CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("contas", [])
                    return data
            except Exception as e:
                log_event("FLOW_CONTAS", f"Erro ao ler {self.CONFIG_PATH}: {e}", level="error")
        return {"contas": [], "ultimo_reset_data": None}

    def _salvar(self, config: Dict[str, Any]) -> bool:
        """Persiste o config de forma ATÔMICA (tmp + os.replace). True em sucesso.

        Grava em arquivo temporário no MESMO diretório e só então substitui o
        original. Evita a janela de truncamento em que um `write_text` direto
        deixaria o JSON vazio/parcial para leitores concorrentes (o worker
        Playwright e as rotas Flask leem/escrevem este arquivo em paralelo).
        """
        try:
            self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.CONFIG_PATH.with_name(self.CONFIG_PATH.name + ".tmp")
            tmp_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp_path), str(self.CONFIG_PATH))
            return True
        except Exception as e:
            log_event("FLOW_CONTAS", f"Erro ao salvar {self.CONFIG_PATH}: {e}", level="error")
            return False

    def atualizar_saldo_conta(self, conta_id: Any, saldo: int) -> bool:
        """Atualiza o saldo real lido dinamicamente via get_exact_balance()."""
        with self._lock:
            config = self._carregar()
            alterado = False
            for c in config.get("contas", []):
                if str(c.get("id")) == str(conta_id):
                    c["creditos_disponiveis"] = int(saldo)
                    if saldo == 0:
                        c["creditos_esgotados"] = True
                        c["esgotado_em"] = datetime.now().isoformat()
                    else:
                        c["creditos_esgotados"] = False
                    alterado = True
                    break
            if alterado:
                return self._salvar(config)
            return False

    # ------------------------------------- saldo REAL persistido (TAREFA 1a/1b)
    def gravar_saldo_lido(
        self,
        conta_id: Any = None,
        saldo: Any = None,
        quando: Optional[str] = None,
    ) -> bool:
        """Persiste o SALDO REAL lido do Flow em campos NOVOS por conta.

        Escreve `saldo_lido` (int) e `saldo_lido_em` (ISO-8601) na conta
        informada (ou na conta ATIVA quando `conta_id` é None). Esses campos
        são a fonte do planejador de produção (`orcamento_producao_service`).

        NÃO toca `creditos_disponiveis` nem as flags de esgotado — essas
        continuam sob responsabilidade de `atualizar_saldo_conta` e do reset
        diário (nenhuma lógica existente é alterada).
        """
        if saldo is None:
            return False
        try:
            saldo_int = int(saldo)
        except (TypeError, ValueError):
            return False
        alvo = conta_id if conta_id is not None else self._conta_ativa_id()
        with self._lock:
            config = self._carregar()
            for c in config.get("contas", []):
                if alvo is not None and str(c.get("id")) != str(alvo):
                    continue
                if alvo is None and not c.get("ativa"):
                    continue
                c["saldo_lido"] = saldo_int
                c["saldo_lido_em"] = quando or datetime.now().isoformat()
                return self._salvar(config)
        return False

    def _conta_ativa_id(self) -> Optional[Any]:
        """ID da conta ativa (None quando não há conta ativa)."""
        with self._lock:
            ativa = self._conta_ativa(self._carregar())
        return ativa.get("id") if ativa else None

    def saldos_detalhados(self) -> List[Dict[str, Any]]:
        """Contas COM os campos de saldo real — entrada do planejador."""
        with self._lock:
            config = self._carregar()
        return [
            {
                "id": c.get("id"),
                "nome": c.get("nome") or c.get("email") or f"Conta {c.get('id')}",
                "email": c.get("email", ""),
                "ativa": bool(c.get("ativa", False)),
                "creditos_disponiveis": c.get("creditos_disponiveis"),
                "creditos_esgotados": bool(c.get("creditos_esgotados", False)),
                "video_creditos_esgotados": bool(c.get("video_creditos_esgotados", False)),
                "ultima_renovacao": c.get("ultima_renovacao", "N/A"),
                "saldo_lido": c.get("saldo_lido"),
                "saldo_lido_em": c.get("saldo_lido_em"),
            }
            for c in config.get("contas", [])
        ]

    # --------------------------- custo medido (TAREFA 1c/1d) — delegação
    # O estado (último saldo + gerações pendentes) e o ledger vivem no
    # módulo; estes métodos existem para os chamadores que já têm a
    # instância (playwright_flow.flow_mgr) usarem a mesma porta de entrada.
    def registrar_leitura_saldo(
        self,
        conta_id: Any = None,
        saldo: Any = None,
        quando: Optional[str] = None,
        tipo_geracao: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """Registra leitura de saldo e fecha o custo do par (ver módulo)."""
        return registrar_leitura_saldo(
            conta_id=conta_id, saldo=saldo, quando=quando, tipo_geracao=tipo_geracao
        )

    def registrar_geracao(self, tipo: Any, conta_id: Any = None) -> None:
        """Marca UMA geração (imagem/vídeo) ocorrida na conta."""
        registrar_geracao(tipo, conta_id)

    def custos_atuais(self) -> Dict[str, Any]:
        """Custo por geração (mediana do ledger ou padrão do config)."""
        return custos_atuais()

    @staticmethod
    def _conta_ativa(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return next((c for c in config.get("contas", []) if c.get("ativa")), None)

    @staticmethod
    def _hoje() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # --------------------------------------------------------- reset diário
    def verificar_renovacao_diaria(self) -> bool:
        """True quando o último reset NÃO foi hoje (renovação pendente)."""
        with self._lock:
            config = self._carregar()
        return config.get("ultimo_reset_data") != self._hoje()

    def resetar_creditos_diarios(self) -> bool:
        """Reseta 'creditos_esgotados' quando virou um novo dia.

        Retorna True quando um reset real foi aplicado e persistido.
        """
        with self._lock:
            config = self._carregar()
            hoje = self._hoje()
            if config.get("ultimo_reset_data") == hoje:
                return False  # já renovado hoje
            for conta in config.get("contas", []):
                conta["creditos_esgotados"] = False
                # Não força valor fictício: mantém saldo real ou None até a próxima leitura
                conta["ultima_renovacao"] = hoje
                conta["video_creditos_esgotados"] = False
                conta.pop("video_esgotado_em", None)
            config["ultimo_reset_data"] = hoje
            ok = self._salvar(config)
            if ok:
                log_event(
                    "FLOW_CONTAS",
                    f"Reset diário de créditos aplicado em {hoje} "
                    f"para {len(config.get('contas', []))} conta(s).",
                )
            return ok

    # ------------------------------------------------------- listar / trocar
    def listar_contas_ativas(self) -> List[Dict[str, Any]]:
        """Lista as contas com o status consumido pela UI."""
        with self._lock:
            config = self._carregar()
        return [
            {
                "id": c.get("id"),
                "nome": c.get("nome") or c.get("email") or f"Conta {c.get('id')}",
                "email": c.get("email", ""),
                "ativa": bool(c.get("ativa", False)),
                "creditos_disponiveis": c.get("creditos_disponiveis"),
                "creditos_esgotados": bool(c.get("creditos_esgotados", False)),
                "ultima_renovacao": c.get("ultima_renovacao", "N/A"),
            }
            for c in config.get("contas", [])
        ]

    def trocar_conta(self, email: str) -> Dict[str, Any]:
        """Troca a conta ativa manualmente (aceita email OU nome)."""
        alvo_busca = (email or "").strip().lower()
        with self._lock:
            config = self._carregar()
            contas = config.get("contas", [])
            alvo = next(
                (c for c in contas
                 if (c.get("email") or "").strip().lower() == alvo_busca
                 or (c.get("nome") or "").strip().lower() == alvo_busca),
                None,
            )
            if alvo is None:
                return {"status": "erro", "error": f"Conta '{email}' não encontrada"}
            for c in contas:
                c["ativa"] = (c is alvo)
            self._salvar(config)
            ativa_nome = alvo.get("email") or alvo.get("nome") or ""
            log_event("FLOW_CONTAS", f"Troca manual de conta: ativa -> {ativa_nome}")
            return {"status": "ok", "conta_ativa": ativa_nome, "email": alvo.get("email", "")}

    # -------------------------------------------------------------- rotação
    def proxima_conta_disponivel(self) -> Optional[str]:
        """Email/nome da próxima conta (round-robin) que ainda tem créditos."""
        with self._lock:
            config = self._carregar()
            contas = config.get("contas", [])
            if not contas:
                return None
            ativa = self._conta_ativa(config)
            idx = contas.index(ativa) if ativa in contas else -1
            for salto in range(1, len(contas) + 1):
                c = contas[(idx + salto) % len(contas)]
                if c is ativa:
                    continue
                # CORREÇÃO 2 — conta sem email confirmado NÃO entra na rotação:
                # contas novas nascem com email="" e nunca fizeram login, logo não
                # têm sessão Google no perfil do Chrome (o Flow abriria deslogado).
                if not c.get("creditos_esgotados") and (c.get("email") or "").strip():
                    return c.get("email") or c.get("nome")
        return None

    def rotacao_inteligente(self) -> Optional[str]:
        """Troca a conta ativa quando ela está esgotada.

        Passos: (1) aplica o reset diário; (2) se a conta ativa ainda estiver
        esgotada, ativa a próxima conta com créditos.
        Retorna o email/nome da nova conta ativa, ou None se não houve troca.
        """
        with self._lock:
            self.resetar_creditos_diarios()
            config = self._carregar()
            contas = config.get("contas", [])
            if not contas:
                return None
            ativa = self._conta_ativa(config)

            # Sem conta ativa válida -> ativa a primeira com créditos.
            if ativa is None:
                candidata = next((c for c in contas if not c.get("creditos_esgotados")), None)
                if candidata is None:
                    return None
                for c in contas:
                    c["ativa"] = (c is candidata)
                self._salvar(config)
                return candidata.get("email") or candidata.get("nome")

            if not ativa.get("creditos_esgotados"):
                return None  # conta ativa OK, nada a fazer

            idx = contas.index(ativa)
            for salto in range(1, len(contas) + 1):
                c = contas[(idx + salto) % len(contas)]
                if not c.get("creditos_esgotados"):
                    for o in contas:
                        o["ativa"] = (o is c)
                    self._salvar(config)
                    nova = c.get("email") or c.get("nome") or ""
                    log_event("FLOW_CONTAS", f"Rotação inteligente: conta ativa -> {nova}")
                    return nova
            return None  # nenhuma conta com créditos disponíveis

    # ------------------------------------------------- créditos de VÍDEO (separado)
    def marcar_video_esgotado(self, conta_id: int) -> None:
        """Marca video_creditos_esgotados=True para a conta.

        NÃO altera creditos_esgotados — conta ainda pode gerar imagens.
        """
        with self._lock:
            config = self._carregar()
            for c in config.get("contas", []):
                if c.get("id") == conta_id:
                    c["video_creditos_esgotados"] = True
                    c["video_esgotado_em"] = datetime.now().isoformat()
                    break
            self._salvar(config)
            log_event("FLOW_CONTAS",
                      f"Conta {conta_id}: video_creditos_esgotados=True "
                      f"(conta ainda pode gerar imagens).")

    def proxima_conta_para_video(self, conta_atual_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retorna próxima conta com video_creditos_esgotados=False
        e creditos_esgotados=False e email preenchido.

        Retorna None se todas as contas esgotaram vídeo.
        """
        with self._lock:
            config = self._carregar()
            for c in config.get("contas", []):
                if c.get("id") == conta_atual_id:
                    continue
                if c.get("creditos_esgotados"):
                    continue
                if c.get("video_creditos_esgotados"):
                    continue
                if not (c.get("email") or "").strip():
                    continue
                return c
            return None

    def todas_contas_video_esgotadas(self) -> bool:
        """True se todas as contas com email têm
        video_creditos_esgotados=True ou creditos_esgotados=True."""
        with self._lock:
            config = self._carregar()
            for c in config.get("contas", []):
                if not (c.get("email") or "").strip():
                    continue
                if c.get("creditos_esgotados"):
                    continue
                if not c.get("video_creditos_esgotados"):
                    return False
            return True
