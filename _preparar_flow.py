r"""
_preparar_flow.py — Prepara o Google Flow para automacao via CDP.

Reutiliza services/playwright_flow.ensure_chrome_cdp() (NAO duplica logica):
  - verifica / abre o Chrome dedicado com:
      --remote-debugging-port=9222
      --user-data-dir=%USERPROFILE%\ultracut3_chrome_profile
  - abre https://labs.google/fx/tools/flow
  - aguarda a porta CDP responder (ate 20s)

Imprime [OK]/[ERRO] no console e retorna exit code 0/1.

Uso (chamado por iniciar_web.bat):
    C:\ultracut3\.venv\Scripts\python.exe _preparar_flow.py
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CDP_PORT = 9222
FLOW_URL = "https://labs.google/fx/tools/flow"


def main() -> int:
    try:
        from services.playwright_flow import ensure_chrome_cdp
    except Exception as e:
        print(f"[ERRO] Nao foi possivel importar playwright_flow: {e}")
        return 1

    try:
        ok, msg = ensure_chrome_cdp(CDP_PORT)
    except Exception as e:
        print(f"[ERRO] Falha ao preparar Chrome/CDP: {e}")
        return 1

    if ok:
        print(f"[OK] Chrome CDP disponivel (porta {CDP_PORT}): {msg}")
        print(f"[OK] Google Flow preparado: {FLOW_URL}")
        return 0

    print(f"[ERRO] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
