"""
llm_client.py — Camada de contrato com o LLM (Fase 0).

Responsabilidades: request, response, schema validation, retry, error handling,
logging. O LLM NUNCA escreve em scene_plan.json — ele retorna texto/JSON; o parser
e o validador normalizam e a camada de aplicação persiste via scene_store.
"""

import json
import re
import time
from typing import Callable, Optional

from config import LLM_MAX_RETRIES, LLM_TIMEOUT
from services.event_logger import log_event


class LLMError(Exception):
    """Erro geral de comunicação/contrato com o LLM."""


class LLMInvalidResponse(LLMError):
    """Resposta vazia ou impossível de interpretar."""


class LLMSchemaError(LLMError):
    """Resposta válida como JSON, mas fora do schema esperado."""


def extrair_json(texto) -> dict:
    """
    Extrai o primeiro objeto JSON válido do texto.
    Tolera code fences (```json ... ```) e texto ao redor.
    """
    if not texto:
        raise LLMInvalidResponse("resposta vazia")
    t = str(texto).strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    inicio = t.find("{")
    if inicio == -1:
        raise LLMInvalidResponse("nenhum objeto JSON encontrado na resposta")
    profundidade = 0
    em_string = False
    escape = False
    for i in range(inicio, len(t)):
        ch = t[i]
        if em_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                em_string = False
            continue
        if ch == '"':
            em_string = True
        elif ch == "{":
            profundidade += 1
        elif ch == "}":
            profundidade -= 1
            if profundidade == 0:
                trecho = t[inicio:i + 1]
                try:
                    return json.loads(trecho)
                except ValueError as e:
                    raise LLMInvalidResponse(f"JSON inválido: {e}") from e
    raise LLMInvalidResponse("objeto JSON não fechado")


class LLMClient:
    """Cliente com retry (transporte + schema) e validação opcional."""

    def __init__(self, provider, model: Optional[str] = None,
                 max_retries: Optional[int] = None,
                 timeout: Optional[float] = None,
                 retry_delay: float = 0.5):
        self.provider = provider
        self.model = model or getattr(provider, "model", None) or None
        self.max_retries = max_retries if max_retries is not None else LLM_MAX_RETRIES
        self.timeout = timeout if timeout is not None else LLM_TIMEOUT
        self.retry_delay = retry_delay

    def _chamar(self, messages, temperature, max_tokens) -> str:
        texto = self.provider.generate(messages, model=self.model,
                                       temperature=temperature, max_tokens=max_tokens)
        if not texto or not str(texto).strip():
            raise LLMInvalidResponse("resposta vazia do provider")
        return str(texto).strip()

    def complete(self, system: str, user: str, temperature: float = 0.2,
                 max_tokens: int = 2000) -> str:
        """Retorna o texto bruto (sem parsing)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err = None
        for tentativa in range(1, self.max_retries + 2):
            try:
                return self._chamar(messages, temperature, max_tokens)
            except Exception as e:
                last_err = e
                log_event("LLM", f"tentativa {tentativa}/{self.max_retries + 1} falhou: {type(e).__name__}: {e}", level="warn")
                if tentativa <= self.max_retries and self.retry_delay:
                    time.sleep(self.retry_delay)
        raise LLMError(f"LLM falhou após {self.max_retries + 1} tentativas: {last_err}") from last_err

    def complete_json(self, system: str, user: str,
                      validator: Optional[Callable[[dict], list]] = None,
                      temperature: float = 0.2, max_tokens: int = 2000) -> dict:
        """Retorna dict parseado e validado (retry cobre transporte E schema)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err = None
        for tentativa in range(1, self.max_retries + 2):
            try:
                texto = self._chamar(messages, temperature, max_tokens)
                dados = extrair_json(texto)
                if validator is not None:
                    erros = validator(dados)
                    if erros:
                        raise LLMSchemaError("; ".join(erros))
                return dados
            except Exception as e:
                last_err = e
                log_event("LLM", f"tentativa {tentativa}/{self.max_retries + 1}: {type(e).__name__}: {e}", level="warn")
                if tentativa <= self.max_retries and self.retry_delay:
                    time.sleep(self.retry_delay)
        if isinstance(last_err, (LLMSchemaError, LLMInvalidResponse)):
            raise last_err
        raise LLMError(f"LLM JSON falhou após {self.max_retries + 1} tentativas: {last_err}") from last_err
