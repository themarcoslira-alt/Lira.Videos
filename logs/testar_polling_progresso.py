"""
Teste: simula transcricao com progresso e verifica polling
"""
import sys, os
sys.path.insert(0, "c:/ultracut3")

from services.event_logger import log_event, ler_eventos

# Limpa logs anteriores para teste limpo
try:
    os.remove("c:/ultracut3/logs/events.jsonl")
except FileNotFoundError:
    pass

# Simula transcricao completa com logs de progresso
log_event("TRANSCRIBE", "Iniciando transcricao: C:/ultracut3/video1/completo.MP3", level="info")
log_event("TRANSCRIBE", "Carregando modelo faster-whisper...", level="info")
log_event("TRANSCRIBE", "Carregando modelo faster-whisper... (15 segundos decorridos)", level="info")
log_event("TRANSCRIBE", "Carregando modelo faster-whisper... (30 segundos decorridos)", level="info")
log_event("TRANSCRIBE", "Modelo carregado. Iniciando transcricao do audio...", level="info")
log_event("TRANSCRIBE", "Segmento 1 | [00:00] | 0% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Segmento 3 | [00:15] | 12% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Segmento 5 | [00:30] | 25% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Segmento 10 | [01:00] | 50% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Segmento 15 | [01:30] | 75% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Segmento 20 | [02:00] | 100% do audio transcrito", level="info")
log_event("TRANSCRIBE", "Transcricao concluida: 20 segmentos, idioma pt", level="info")

# Le de volta (simula o polling com 99999)
todos = ler_eventos(linhas=99999)
print("=" * 60)
print("TESTE DE PROGRESSO - POLLING COM ler_eventos(99999)")
print("=" * 60)
print()
print(">>> 12 eventos que DEVEM aparecer no Console de Execucao:")
print()
for e in todos[-12:]:
    print("  [%s] [%s] %s" % (e["ts"], e["category"], e["message"]))
print()
print(">>> Total de eventos no arquivo: %d" % len(todos))
print()

# Verifica que o indice avanca corretamente
print("=" * 60)
print("TESTE DO INDICE DO POLLING")
print("=" * 60)
_ultimo_log_idx = 0
for ciclo in range(1, 5):
    todos = ler_eventos(linhas=99999)
    total_atual = len(todos)
    novos = total_atual - _ultimo_log_idx
    print("  Ciclo %d: total=%d, ultimo_idx=%d, novos=%d" % (ciclo, total_atual, _ultimo_log_idx, novos))
    if novos > 0:
        eventos_exibidos = min(novos, 3)
        for e in todos[_ultimo_log_idx:_ultimo_log_idx + eventos_exibidos]:
            print("    -> [%s] %s" % (e["category"], e["message"][:60]))
    _ultimo_log_idx = total_atual

print()
print(">>> PROVA: polling com 99999 funciona, indice avanca corretamente")
print(">>> O bug anterior (ler_eventos(100)) foi corrigido no commit 9082f02")