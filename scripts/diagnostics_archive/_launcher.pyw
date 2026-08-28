"""
Launcher da GUI - executa main() e captura erros em _ultracut3_erro.log
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from gui import main
    main()
except Exception:
    with open("_ultracut3_erro.log", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    raise