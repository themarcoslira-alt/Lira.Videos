# -*- coding: utf-8 -*-
"""
tests/conftest.py — ISOLAMENTO GLOBAL DA SUÍTE (ANTIGRAVITY)
============================================================
Incidente (23/09 19:32:59): a suíte rodou na árvore REAL e escreveu estado de produção:
`config/flow_accounts.json` ganhou `video_creditos_esgotados=true` na conta conta_03
(um MagicMock tratado como sinal de cota no `_tratar_indicador_limite`), `logs/lira.log`
recebeu eventos "teste@lira.com / Projeto CB" e testes criaram personagens falsos em
`Biblioteca/Personagens/` e `projetos/`.

Este conftest garante que NENHUM teste toque config, identidades, projetos, Biblioteca,
output, assets_cache, tmp de contexto CDP ou logs reais:

1. Fixture autouse (sessão) redireciona TODOS os caminhos de estado para um diretório
   temporário, incluindo os caminhos expostos como constantes nomeadas
   (`playwright_flow.FLOW_ACCOUNTS_PATH`/`CONTEXTO_CDP_PATH`, `event_logger.VAR_LOG_FILE`,
   `FlowAccountManager.CONFIG_PATH`, `REGISTRO_CONTAS_FLOW_PATH`) e TODOS os módulos que
   já importaram `PROJETOS_DIR`/`LOGS_DIR`/etc. do `config`.
2. Guarda de segurança: SHA256+tamanho dos arquivos REAIS monitorados são medidos no
   `pytest_configure` e revalidados no `pytest_sessionfinish`; qualquer alteração FALHA a
   sessão com o nome do arquivo alterado.
3. `flow_accounts.json` de EXEMPLO (2 contas) é semeado no temporário para os testes que
   precisam ler contas.

Sem rede e sem Chrome real: o conftest só redireciona caminhos.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------- arquivos monitorados
ARQUIVOS_REAIS_MONITORADOS = [
    RAIZ / "config" / "flow_accounts.json",
    RAIZ / "logs" / "lira.log",
    RAIZ / "logs" / "events.jsonl",
    RAIZ / "identidades" / "contas_flow.json",
    RAIZ / "tmp" / "lira_context_cdp.json",
    RAIZ / "Biblioteca" / "Personagens" / "Marcos" / "character.json",
    RAIZ / "Biblioteca" / "Personagens" / "Marcos" / "reference.png",
    RAIZ / "projetos" / "projeto_teste" / "identidade.json",
    RAIZ / "projetos" / "proj_test" / "flow_meta.json",
]

CONTAS_EXEMPLO = {
    "contas": [
        {
            "id": 1, "nome": "conta_teste_1", "email": "teste1@exemplo.invalid",
            "profile_dir": "lira_profiles/conta_teste_1",
            "creditos_esgotados": False, "ativa": True, "creditos_disponiveis": 50,
            "ultima_renovacao": "2026-01-01", "video_creditos_esgotados": False,
        },
        {
            "id": 2, "nome": "conta_teste_2", "email": "teste2@exemplo.invalid",
            "profile_dir": "lira_profiles/conta_teste_2",
            "creditos_esgotados": False, "ativa": False, "creditos_disponiveis": 50,
            "ultima_renovacao": "2026-01-01", "video_creditos_esgotados": False,
        },
    ],
    "ultimo_reset_data": "2026-01-01",
}


def _assinatura_arquivo(caminho: Path):
    """(tamanho, sha256) do arquivo real — None quando não existe."""
    try:
        if not caminho.exists():
            return None
        _h = hashlib.sha256()
        with open(caminho, "rb") as _f:
            for _bloco in iter(lambda: _f.read(1024 * 1024), b""):
                _h.update(_bloco)
        return (caminho.stat().st_size, _h.hexdigest())
    except Exception as _e:  # pragma: no cover
        return ("erro", str(_e))


# Diretórios REAIS monitorados (assina TODOS os arquivos recursivamente) — o vazamento de
# 19:32:50 sobrescreveu `Biblioteca\Personagens\Marcos\{character.json,reference.png}` via
# tests/test_criar_meu_avatar.py (que usava MagicMock e gravava na Biblioteca real). Assinar
# a pasta inteira também pega arquivos NOVOS (personagem criada por teste).
DIRETORIOS_REAIS_MONITORADOS = [
    RAIZ / "Biblioteca" / "Personagens",
]


def _assinatura_diretorio(caminho: Path):
    """{arquivo_relativo: (tamanho, sha256)} de um diretório real — {} quando ausente."""
    try:
        if not caminho.exists():
            return None
        _mapa = {}
        for _f in sorted(caminho.rglob("*")):
            if _f.is_file():
                _mapa[_f.relative_to(caminho).as_posix()] = _assinatura_arquivo(_f)
        return _mapa
    except Exception as _e:  # pragma: no cover
        return ("erro", str(_e))


_HASHES_ANTES = {}
_HASHES_DIR_ANTES = {}


def pytest_configure(config):
    """Snapshot dos arquivos/diretórios REAIS antes de qualquer teste."""
    for _caminho in ARQUIVOS_REAIS_MONITORADOS:
        _HASHES_ANTES[str(_caminho)] = _assinatura_arquivo(_caminho)
    for _dir in DIRETORIOS_REAIS_MONITORADOS:
        _HASHES_DIR_ANTES[str(_dir)] = _assinatura_diretorio(_dir)


# --------------------------------------------------------------- isolamento (fixture)
# SEMENTES DE LEITURA: fixtures REAIS copiadas para o diretório temporário. Sem isso, testes
# que leem fixtures de produção (ex.: `test_avatar_decision` lê a Biblioteca global do
# personagem Marcos / o projeto `Historia`) passariam a falhar por "arquivo ausente" — e não
# por bug. São cópias: qualquer escrita do teste vai para o TEMP, nunca para o real.
SEMENTES_DE_LEITURA = (
    ("Biblioteca/Personagens/Marcos", Path("Biblioteca") / "Personagens" / "Marcos"),
    ("projetos/Historia", Path("projetos") / "Historia"),
)


def _semear_fixtures_reais(raiz: Path) -> None:
    """Copia fixtures reais de LEITURA para a árvore temporária (silencioso se ausentes)."""
    import shutil as _shutil
    for _origem_rel, _destino_rel in SEMENTES_DE_LEITURA:
        _origem = RAIZ / _origem_rel
        _destino = raiz / _destino_rel
        if not _origem.exists() or _destino.exists():
            continue
        try:
            _destino.parent.mkdir(parents=True, exist_ok=True)
            if _origem.is_dir():
                _shutil.copytree(_origem, _destino)
            else:
                _shutil.copy2(_origem, _destino)
        except Exception:
            pass


def _criar_arvore_temporaria(raiz: Path) -> Path:
    """Cria a árvore temporária com um `flow_accounts.json` de exemplo."""
    for _sub in ("config", "logs", "identidades", "projetos", "Biblioteca", "output",
                 "assets_cache", "tmp", "var_log"):
        (raiz / _sub).mkdir(parents=True, exist_ok=True)
    (raiz / "config" / "flow_accounts.json").write_text(
        json.dumps(CONTAS_EXEMPLO, ensure_ascii=False, indent=2), encoding="utf-8")
    _semear_fixtures_reais(raiz)
    return raiz


def _patcher_atributo(alvo, nome: str, valor):
    """Patch tolerante de atributo (None quando o alvo não tem o atributo)."""
    import unittest.mock as _mock
    if alvo is None or not hasattr(alvo, nome):
        return None
    return _mock.patch.object(alvo, nome, valor)


def _patchers_de_diretorios(raiz: Path):
    """Redireciona PROJETOS_DIR/LOGS_DIR/... no `config` e em todo módulo que já os importou.

    O patch em `config` cobre módulos importados DEPOIS (eles fazem
    `from config import PROJETOS_DIR` e leem o valor já redirecionado); o scan de
    `sys.modules` cobre os módulos já importados (que guardam a própria referência).
    """
    import unittest.mock as _mock
    import config as _config
    _mapa = {
        "PROJETOS_DIR": ("projetos", _config.PROJETOS_DIR),
        "BIBLIOTECA_DIR": ("Biblioteca", _config.BIBLIOTECA_DIR),
        "OUTPUT_DIR": ("output", _config.OUTPUT_DIR),
        "ASSETS_CACHE_DIR": ("assets_cache", _config.ASSETS_CACHE_DIR),
        "LOGS_DIR": ("logs", _config.LOGS_DIR),
    }
    _patchers = []
    for _nome, (_sub, _real) in _mapa.items():
        _novo = raiz / _sub
        _patchers.append(_mock.patch.object(_config, _nome, _novo))
        for _mod in list(sys.modules.values()):
            if _mod is _config:
                continue
            try:
                if getattr(_mod, _nome, None) == _real:
                    _patchers.append(_mock.patch.object(_mod, _nome, _novo))
            except Exception:
                continue
    return _patchers


def _patchers_de_config_estado(raiz: Path):
    """Contas Flow, registro de personagens, logs de evento e helper do app_web."""
    import unittest.mock as _mock
    _patchers = []
    _contas = raiz / "config" / "flow_accounts.json"

    # FlowAccountManager grava config/flow_accounts.json (classe, atributo CONFIG_PATH)
    try:
        from services.flow_account_manager import FlowAccountManager as _FAM
        _patchers.append(_mock.patch.object(_FAM, "CONFIG_PATH", _contas))
    except Exception:
        pass

    # playwright_flow: caminhos expostos como constantes nomeadas
    try:
        import services.playwright_flow as _pf
        for _nome, _valor in (
            ("FLOW_ACCOUNTS_PATH", _contas),
            ("CONTEXTO_CDP_PATH", raiz / "tmp" / "lira_context_cdp.json"),
            ("REGISTRO_CONTAS_FLOW_PATH", raiz / "identidades" / "contas_flow.json"),
            ("REFERENCE_PNG_IDENTIDADES_FALLBACK", str(raiz / "identidades" / "reference.png")),
        ):
            _p = _patcher_atributo(_pf, _nome, _valor)
            if _p:
                _patchers.append(_p)
    except Exception:
        pass

    # event_logger: events.jsonl, lira.log e o espelho C:/var/log/lira.log
    try:
        import services.event_logger as _ev
        for _nome, _valor in (
            ("EVENTS_FILE", raiz / "logs" / "events.jsonl"),
            ("LIRA_LOG_FILE", raiz / "logs" / "lira.log"),
            ("VAR_LOG_FILE", raiz / "var_log" / "lira.log"),
        ):
            _p = _patcher_atributo(_ev, _nome, _valor)
            if _p:
                _patchers.append(_p)
    except Exception:
        pass

    # app_web: helper que devolve o caminho do arquivo de contas (sem tocar produção)
    try:
        import app_web as _aw
        if hasattr(_aw, "_flow_accounts_path"):
            _patchers.append(_mock.patch.object(_aw, "_flow_accounts_path", lambda: _contas))
    except Exception:
        pass

    # api_v2: contexto CDP (tmp/lira_context_cdp.json) usado nas rotas de contexto
    try:
        import services.api_v2 as _api_v2
        _p = _patcher_atributo(_api_v2, "CONTEXTO_CDP_PATH",
                               raiz / "tmp" / "lira_context_cdp.json")
        if _p:
            _patchers.append(_p)
    except Exception:
        pass

    return _patchers


@pytest.fixture(scope="session", autouse=True)
def _isolamento_de_estado(tmp_path_factory):
    """Redireciona TODO estado gravável para um diretório temporário da sessão."""
    _raiz = _criar_arvore_temporaria(tmp_path_factory.mktemp("estado_isolado"))
    _patchers = []
    try:
        # 1) diretórios (em config + módulos já importados) — start imediato para que
        #    os imports seguintes já leiam o valor redirecionado.
        _de_diretorios = _patchers_de_diretorios(_raiz)
        for _p in _de_diretorios:
            _p.start()
        _patchers += _de_diretorios
        # 2) caminhos específicos de estado (contas, registro, logs, app_web)
        _outros = _patchers_de_config_estado(_raiz)
        for _p in _outros:
            _p.start()
        _patchers += _outros
        yield _raiz
    finally:
        for _p in reversed(_patchers):
            try:
                _p.stop()
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _reset_contas_exemplo(_isolamento_de_estado):
    """Reescreve o `flow_accounts.json` de EXEMPLO antes de CADA teste.

    Os testes de rotação MUTAM o arquivo (marcam conta esgotada/ativam a próxima). Sem
    reset por teste, o 2º teste enxergaria o estado deixado pelo 1º (sem conta elegível)
    e falharia por interferência, não por bug — foi exatamente o que aconteceu com
    `test_rotacao_conta_reset`.
    """
    _contas = _isolamento_de_estado / "config" / "flow_accounts.json"
    try:
        _contas.parent.mkdir(parents=True, exist_ok=True)
        _contas.write_text(json.dumps(CONTAS_EXEMPLO, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    except Exception:
        pass
    yield


def pytest_sessionfinish(session, exitstatus):
    """Falha a sessão se algum arquivo REAL monitorado mudou durante os testes."""
    _alterados = []
    for _caminho in ARQUIVOS_REAIS_MONITORADOS:
        _antes = _HASHES_ANTES.get(str(_caminho))
        _depois = _assinatura_arquivo(_caminho)
        if _antes != _depois:
            _alterados.append(f"  - {_caminho}\n      antes={_antes}\n      depois={_depois}")
    for _dir in DIRETORIOS_REAIS_MONITORADOS:
        _antes_dir = _HASHES_DIR_ANTES.get(str(_dir))
        _depois_dir = _assinatura_diretorio(_dir)
        if _antes_dir == _depois_dir:
            continue
        _antes_dir = _antes_dir or {}
        _depois_dir = _depois_dir or {}
        _novos = sorted(set(_depois_dir) - set(_antes_dir))
        _removidos = sorted(set(_antes_dir) - set(_depois_dir))
        _mudados = sorted(k for k in set(_antes_dir) & set(_depois_dir)
                          if _antes_dir[k] != _depois_dir[k])
        _detalhe = []
        if _novos:
            _detalhe.append(f"arquivos NOVOS: {_novos}")
        if _removidos:
            _detalhe.append(f"arquivos REMOVIDOS: {_removidos}")
        if _mudados:
            _detalhe.append(f"arquivos ALTERADOS: {_mudados}")
        _alterados.append(f"  - {_dir} (diretório)\n      " + "\n      ".join(_detalhe))
    if _alterados:
        print("\n" + "=" * 78)
        print("!! ISOLAMENTO QUEBRADO: ARQUIVO(S) REAL(IS) ALTERADO(S) DURANTE A SUÍTE !!")
        print("=" * 78)
        for _linha in _alterados:
            print(_linha)
        print("Arquivo real NUNCA deve mudar em testes. Verifique tests/conftest.py e o")
        print("teste que rodou (ATENÇÃO: produção real/app rodando também altera estes")
        print("arquivos — confira se havia app/Flow ativo durante a suíte).")
        print("=" * 78)
        session.exitstatus = 1

