r"""
_preparar_flow.py — Prepara o Chrome via CDP no boot com a aba do Lira Studio.

Abertura limpa no boot (redução de poluição visual e separação de ciclos):
  - No boot / Worker 0: abre APENAS a aba do Lira Studio (http://127.0.0.1:5000).
  - Google Flow NÃO é aberto no boot — sua aba é aberta exclusivamente quando o
    worker de produção for iniciado (via open_flow_tab).
  - Workers 1+ (portas 9223..9226): inicializados com portas CDP dedicadas para a produção.

Reutiliza services/playwright_flow.ensure_chrome_cdp() (NÃO duplica lógica):
  - Verifica / abre o Chrome dedicado com a porta CDP e pasta de perfil da conta.
  - Abre apenas a UI do Lira Studio no Worker 0.
  - Aguarda a porta CDP responder (até 20s).

Uso via CLI:
    C:\Lira Videos\.venv\Scripts\python.exe _preparar_flow.py [worker_id]
    C:\Lira Videos\.venv\Scripts\python.exe _preparar_flow.py --worker-id 1 --port 9223
"""
import sys
import argparse
from pathlib import Path
from typing import Optional, Tuple

# Ao rodar da raiz do projeto, __file__ == <raiz>/_preparar_flow.py.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

FLOW_URL = "https://labs.google/fx/pt/tools/flow"


def _abrir_studio_no_navegador_padrao() -> bool:
    """Abre o Studio UMA vez no NAVEGADOR PADRÃO. Nunca levanta exceção.

    TAREFA 2 — o boot não sobe mais o Chrome do Flow; o Studio vive no navegador
    padrão do usuário (não há mais dependência do modo oculto para isso).
    """
    try:
        import webbrowser
        webbrowser.open("http://127.0.0.1:5000/")
        print("[OK] Studio aberto no navegador padrão.", flush=True)
        return True
    except Exception as e:
        print(f"[AVISO] Falha ao abrir o Studio no navegador padrão: {e}", flush=True)
        return False


def preparar_flow(
    worker_id: int = 0,
    port: Optional[int] = None,
    profile_dir: Optional[str] = None,
    projeto_id: Optional[str] = None,
    force_restart: bool = False
) -> Tuple[bool, str]:
    """Prepara o Chrome CDP para o worker especificado.

    - No boot (worker_id == 0): abre APENAS a aba do Lira Studio (http://127.0.0.1:5000).
    - O Google Flow NÃO é aberto aqui — é aberto exclusivamente quando o worker de produção for iniciado.
    """
    try:
        from services.playwright_flow import ensure_chrome_cdp
    except Exception as e:
        return False, f"Não foi possível importar playwright_flow: {e}"

    cdp_port = port if port is not None else (9222 + worker_id)

    try:
        # TAREFA 2 — O BOOT NÃO SOBE MAIS O CHROME DO FLOW. O Chrome CDP nasce sob
        # demanda (geração/login chamam ensure_chrome_cdp). Aqui só garantimos o
        # Studio no NAVEGADOR PADRÃO — uma única vez, no worker 0.
        if worker_id == 0:
            abriu = _abrir_studio_no_navegador_padrao()
            return True, ("Studio aberto no navegador padrão; Chrome do Flow sob demanda."
                          if abriu else
                          "Chrome do Flow sob demanda (abra http://127.0.0.1:5000).")
        return True, "Nada a preparar no boot (Chrome do Flow sob demanda)."
    except Exception as e:
        return False, f"Falha no preparo de boot do worker {worker_id}: {e}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepara Google Flow via CDP com suporte a múltiplos workers")
    parser.add_argument("worker_id_pos", nargs="?", type=int, default=None, help="ID do worker posicional (0=projeto+flow, 1+=flow só)")
    parser.add_argument("--worker-id", type=int, default=None, dest="worker_id", help="ID do worker (0=projeto+flow, 1+=flow só)")
    parser.add_argument("--port", type=int, default=None, help="Porta CDP (padrão: 9222 + worker_id)")
    parser.add_argument("--projeto-id", type=str, default=None, help="ID do projeto no Lira Studio (Worker 0)")
    parser.add_argument("--profile-dir", type=str, default=None, help="Diretório de perfil do Chrome")
    parser.add_argument("--force-restart", action="store_true", help="Força encerramento e reinício do Chrome")

    args = parser.parse_args(argv)

    worker_id = 0
    if args.worker_id is not None:
        worker_id = args.worker_id
    elif args.worker_id_pos is not None:
        worker_id = args.worker_id_pos

    cdp_port = args.port if args.port is not None else (9222 + worker_id)

    ok, msg = preparar_flow(
        worker_id=worker_id,
        port=cdp_port,
        profile_dir=args.profile_dir,
        projeto_id=args.projeto_id,
        force_restart=args.force_restart
    )

    if ok:
        abas_desc = "aba Lira Studio (Flow sob demanda na produção)" if worker_id == 0 else "porta CDP limpa (Flow sob demanda)"
        print(f"[OK] Chrome CDP Worker {worker_id} disponível (porta {cdp_port}): {abas_desc} ({msg})")
        return 0

    print(f"[ERRO] Worker {worker_id} na porta {cdp_port}: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())