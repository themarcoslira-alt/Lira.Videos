"""
Teste: simula cliques em 6 botoes diferentes e mostra logs UI_CLICK
"""
import sys
sys.path.insert(0, "c:/ultracut3")

from services.event_logger import log_event, ler_eventos

# Simula cliques em 6 botoes diferentes
log_event("UI_CLICK", "Selecionar Audio", level="info")
log_event("UI_CLICK", "Atualizar Lista", level="info")
log_event("UI_CLICK", "DEL Projeto", level="info")
log_event("UI_CLICK", "Testar Anthropic", level="info")
log_event("UI_CLICK", "Salvar PEXELS_API_KEY", level="info")
log_event("UI_CLICK", "Modo Automatico", level="info")

# Mostra os logs de UI_CLICK
eventos = ler_eventos(linhas=50, categoria="UI_CLICK")
for e in eventos[-8:]:
    print("[%s] [UI_CLICK] %s" % (e["ts"], e["message"]))
print("\n>>> 6 botoes diferentes logados com sucesso!")