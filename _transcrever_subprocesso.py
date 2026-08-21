"""
_transcrever_subprocesso.py — Subprocesso isolado de transcricao (faster-whisper)
Executado via -c inline pelo transcriber.py (evita segfault do ctranslate2)
Recebe 3 args: arquivo_video, project_name, output_path
"""
import sys, json, os, re
from pathlib import Path


def _coletar_palavras(seg):
    """Extrai [{w, s, e}] do atributo `.words` de um segmento faster-whisper.

    BLOCO 3 — alinhamento por palavra (aditivo). Retorna lista vazia quando o
    segmento não possui word timestamps.
    """
    palavras = []
    for w in (getattr(seg, "words", None) or []):
        wpal = (getattr(w, "word", "") or "").strip()
        if not wpal:
            continue
        palavras.append({
            "w": wpal,
            "s": round(float(getattr(w, "start", 0)), 3),
            "e": round(float(getattr(w, "end", 0)), 3),
        })
    return palavras


# ---------------------------------------------------------------------------
# BLOCO 6 — Segmentação consistente (VAD + pós-processamento)
#
# Parâmetros de VAD escolhidos:
#   - vad_filter=True: ativa a detecção de voz (antes estava desligado, o que
#     fazia o Whisper fundir várias frases num bloco só de 13-14s).
#   - min_silence_duration_ms=300: pausa >= 300ms entre falas já gera um novo
#     segmento (frases curtas e estáveis).
#   - speech_pad_ms=150: margem curta de respiro ao redor de cada trecho de fala.
#   - threshold=0.5: limiar padrão do VAD.
# Pós-processamento de segurança (_quebrar_segmentos_longos):
#   - segmento > TETO_DURACAO_SEGMENTO (8s) OU com > MAX_SENTENCAS_SEGMENTO (2)
#     sentenças completas é quebrado usando os word_timestamps (Bloco 3),
#     localizando o fim de cada sentença pela pontuação.
# ---------------------------------------------------------------------------
VAD_FILTER = True
VAD_PARAMETERS = {
    "threshold": 0.5,
    "min_silence_duration_ms": 300,
    "speech_pad_ms": 150,
}
TETO_DURACAO_SEGMENTO = 8.0
MAX_SENTENCAS_SEGMENTO = 2
_SENT_END = re.compile(r"[.!?…]+[\"')]*(?:\s+|$)")


def _fmt_mmss(sec):
    sec = max(0.0, float(sec or 0))
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def _tempos_finais_sentencas(texto, fins, palavras, inicio, fim):
    """Tempo de fim de cada sentença (usando word timestamps; fallback proporcional)."""
    tempos = []
    if not palavras:
        total = max(0.0, fim - inicio)
        for i in range(len(fins)):
            tempos.append(inicio + total * (i + 1) / max(1, len(fins)))
        return tempos
    offsets = []
    offset = 0
    texto_low = texto.lower()
    for p in palavras:
        w = (p.get("w") or "").strip()
        if not w:
            continue
        idx = texto_low.find(w.lower(), offset)
        if idx >= 0:
            offset = idx + len(w)
            offsets.append((offset, float(p.get("e", fim))))
    for fi in fins:
        melhor = None
        for of, e in offsets:
            if of <= fi + 2:
                melhor = e
            else:
                break
        if melhor is None:
            melhor = inicio + (fim - inicio) * (len(tempos) + 1) / (len(fins) + 1)
        tempos.append(melhor)
    return tempos


def _quebrar_segmentos_longos(segmentos, teto=None, max_sentencas=None):
    """Pós-processamento BLOCO 6.4.

    Quebra segmentos que ultrapassem o teto de duração OU contenham mais de
    `max_sentencas` sentenças completas, localizando o fim de cada sentença
    pela pontuação e o tempo da última palavra (word timestamps). Sem palavras,
    usa fallback proporcional (documentado).
    """
    teto = teto if teto is not None else TETO_DURACAO_SEGMENTO
    max_sentencas = max_sentencas if max_sentencas is not None else MAX_SENTENCAS_SEGMENTO
    novos = []
    for seg in segmentos:
        texto = (seg.get("text") or "").strip()
        if not texto:
            continue
        inicio = float(seg.get("start", 0))
        fim = float(seg.get("end", inicio + 5))
        dur = fim - inicio
        palavras = seg.get("words") or []
        n_sent = len(_SENT_END.findall(texto)) or (1 if texto else 0)

        if dur <= teto and n_sent <= max_sentencas:
            novos.append(seg)
            continue

        # caso A — sentença única longa (>teto): divide em blocos ~teto
        if n_sent <= 1 and dur > teto:
            n_chunks = int(dur // teto) + 1
            cortes = [inicio + teto * (i + 1) for i in range(n_chunks - 1)]
            if palavras:
                ajustados = []
                for limite in cortes:
                    melhor = None
                    for p in palavras:
                        if float(p.get("s", 0)) < limite:
                            melhor = float(p.get("e", 0))
                        else:
                            break
                    ajustados.append(melhor if melhor is not None else limite)
                cortes = ajustados
            sub = []
            inicio_atual = inicio
            for corte in cortes:
                sub.append({
                    "start": round(inicio_atual, 2), "end": round(corte, 2),
                    "text": texto, "timestamp": _fmt_mmss(inicio_atual),
                    "words": [p for p in palavras if inicio_atual <= float(p.get("s", 0)) <= corte],
                })
                inicio_atual = corte
            sub.append({
                "start": round(inicio_atual, 2), "end": round(fim, 2),
                "text": texto, "timestamp": _fmt_mmss(inicio_atual),
                "words": [p for p in palavras if inicio_atual <= float(p.get("s", 0)) <= fim],
            })
            novos.extend(sub)
            continue

        # caso B — várias sentenças: quebra no fim de cada sentença
        fins = [m.end() for m in _SENT_END.finditer(texto)]
        if not fins:
            novos.append(seg)
            continue
        tempos = _tempos_finais_sentencas(texto, fins, palavras, inicio, fim)
        sub = []
        inicio_atual = inicio
        pos_texto = 0
        for i, fim_off in enumerate(fins):
            pedaco = texto[pos_texto:fim_off].strip()
            pos_texto = fim_off
            if not pedaco:
                continue
            fim_tempo = float(tempos[i])
            if fim_tempo <= inicio_atual:
                fim_tempo = inicio_atual + max(0.5, (fim - inicio) / max(1, len(fins)))
            sub.append({
                "start": round(inicio_atual, 2), "end": round(fim_tempo, 2),
                "text": pedaco, "timestamp": _fmt_mmss(inicio_atual),
                "words": [p for p in palavras if inicio_atual <= float(p.get("s", 0)) <= fim_tempo],
            })
            inicio_atual = fim_tempo
        resto = texto[pos_texto:].strip()
        if resto:
            sub.append({
                "start": round(inicio_atual, 2), "end": round(fim, 2),
                "text": resto, "timestamp": _fmt_mmss(inicio_atual),
                "words": [p for p in palavras if inicio_atual <= float(p.get("s", 0)) <= fim],
            })
        sub = [s for s in sub if s["text"]]
        if not sub:
            novos.append(seg)
            continue
        for s in sub:
            if float(s["end"]) - float(s["start"]) < 0.5:
                s["end"] = round(float(s["start"]) + 0.5, 2)
        novos.extend(sub)
    return novos

def main():
    if len(sys.argv) < 4:
        print(json.dumps({"success": False, "error": "Argumentos insuficientes"}))
        return 1

    arquivo_video = sys.argv[1]
    project_name = sys.argv[2]
    output_path = sys.argv[3]

    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", device="cpu", compute_type="float32", cpu_threads=2, num_workers=1)

    print(f"[SUBPROCESSO] Iniciando transcricao: {arquivo_video}", flush=True)
    print(f"[SUBPROCESSO] Projeto: {project_name}", flush=True)

    segments, info = model.transcribe(
        arquivo_video, beam_size=5, language="en",
        vad_filter=VAD_FILTER, vad_parameters=VAD_PARAMETERS,
        word_timestamps=True,
    )

    total_duration = info.duration if info and info.duration else 0
    print(f"[SUBPROCESSO] Duracao total: {total_duration:.2f}s", flush=True)

    linhas_txt = []
    segmentos = []
    idx = 0

    for seg in segments:
        start = seg.start
        end = seg.end
        text = seg.text.strip()
        if not text:
            continue

        mins_s, secs_s = int(start // 60), int(start % 60)
        timestamp = f"{mins_s:02d}:{secs_s:02d}"

        # BLOCO 3 — timestamps por PALAVRA (faster-whisper word_timestamps=True)
        # Aditivo: preenche "words" por segmento; o TXT/JSON atuais não mudam.
        palavras = _coletar_palavras(seg)

        segmentos.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
            "timestamp": timestamp,
            "words": palavras
        })

        progress = (start / total_duration * 100) if total_duration > 0 else 0
        print(f"[TRANSCREVENDO] {progress:.1f}% do audio transcrito", flush=True)

        idx += 1

    # BLOCO 6.4 — pós-processamento: segmentação curta e estável
    segmentos = _quebrar_segmentos_longos(segmentos)
    linhas_txt = [f"[{s['timestamp']}] {s['text']}" for s in segmentos]
    print(f"[SUBPROCESSO] Segmentacao pos-processada: {len(segmentos)} segmentos "
          f"(teto={TETO_DURACAO_SEGMENTO}s, max_sentencas={MAX_SENTENCAS_SEGMENTO})", flush=True)

    # Salva TXT
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas_txt))
        print(f"[SUBPROCESSO] TXT salvo: {output_path}", flush=True)
    except Exception as e:
        print(f"[SUBPROCESSO] Erro ao salvar TXT: {e}", flush=True)

    # Salva JSON
    try:
        project_dir = Path(output_path).parent
        json_path = project_dir / "roteiro_transcricao.json"
        json_data = {
            "segments": segmentos,
            "segment_count": len(segmentos),
            "duration": round(total_duration, 2),
            "language": info.language if info else "pt"
        }
        with open(str(json_path), "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        print(f"[SUBPROCESSO] JSON salvo: {json_path}", flush=True)
    except Exception as e:
        print(f"[SUBPROCESSO] Erro ao salvar JSON: {e}", flush=True)

    # Salva word_timestamps.json (BLOCO 3 — aditivo, não altera TXT/JSON atuais)
    try:
        project_dir = Path(output_path).parent
        word_path = project_dir / "word_timestamps.json"
        words_data = {
            "fonte": "faster-whisper",
            "language": info.language if info else "pt",
            "duration": round(total_duration, 2),
            "segments": segmentos,
        }
        with open(str(word_path), "w", encoding="utf-8") as f:
            json.dump(words_data, f, indent=2, ensure_ascii=False)
        print(f"[SUBPROCESSO] word_timestamps.json salvo: {word_path}", flush=True)
    except Exception as e:
        print(f"[SUBPROCESSO] Erro ao salvar word_timestamps.json: {e}", flush=True)

    # Resultado final (JSON na ultima linha)
    resultado = {
        "success": True,
        "segments": len(segmentos),
        "texto": "\n".join(linhas_txt),
        "arquivo": output_path
    }
    print(json.dumps(resultado), flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())