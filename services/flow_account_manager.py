"""
services/flow_account_manager.py — Gerenciador de contas Google Flow
====================================================================
Fonte de verdade: config/flow_accounts.json (o MESMO arquivo usado por
services/playwright_flow.py e app_web.py).

Responsabilidades:
- Auto-reset diário dos créditos (renovação à meia-noite local).
- Rotação inteligente: troca automática da conta ativa quando ela esgota.
- Troca manual de conta (UI/API) e listagem de status para o front-end.

NÃO altera o schema existente do JSON: apenas ACRESCENTA os campos
"creditos_disponiveis", "ultima_renovacao" e "ultimo_reset_data".
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


class FlowAccountManager:
    """Gerencia as contas do Google Flow usadas na produção automática."""

    # Raiz do projeto (uma pasta acima de 'services/')
    BASE_DIR = Path(__file__).resolve().parents[1]
    CONFIG_PATH = BASE_DIR / "config" / "flow_accounts.json"
    # Créditos repostos por conta a cada novo dia (placeholder de UI)
    CREDITOS_POR_CONTA = 50

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
                conta["creditos_disponiveis"] = self.CREDITOS_POR_CONTA
                conta["ultima_renovacao"] = hoje
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
                "creditos_disponiveis": c.get("creditos_disponiveis", self.CREDITOS_POR_CONTA),
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
                if not c.get("creditos_esgotados"):
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
