# RELATÓRIO DE AUDITORIA ARQUITETURAL — ULTRACUT3

**Data**: 23/07/2026
**Objetivo**: Mapear o fluxo real do pipeline antes de implementar a chamada batch única ao Claude e o pool compartilhado de queries.

---

## 1. FLUXO ATUAL REAL DO PIPELINE

```
GUI (gui.py) → PipelineService (pipeline_service.py)
  1. transcricao()     → services/transcriber.py
  2. gerar_cenas()     → services/scene_builder.py
  3. gerar_storyboard() → services/broll_director.py
  4. gerar_queries()   → services/query_generator.py (NÃO CONSUMIDO)
  5. buscar_midias()   → services/media_search.py → media_fetcher.py
  6. renderizar()      → services/video_builder.py → services/video_encoder.py
```

### Observações importantes:
- `query_generator.py` gera queries mas **ninguém as consome** — o `media_search.py` usa as keywords do `storyboard.json` diretamente
- O fluxo de 5 etapas é executado em sequência, sem pipeline de dados compartilhado entre etapas (cada etapa lê/grava em arquivos JSON no diretório do projeto)

---

## 2. ESTRUTURA ATUAL DA TRANSCRIÇÃO

**Arquivo**: `services/transcriber.py`

### Execução do Whisper
- **Linha 30**: `segments, info = model.transcribe(arquivo_video, language="pt")`
- Idioma **hardcoded** como `"pt"` (português)
- Modelo carregado via `WHISPER_MODEL_SIZE` e `WHISPER_DEVICE` da config

### Formato de saída
- **Arquivo salvo**: `{projeto}/roteiro_transcricao.txt` — formato **TXT** (extensão `.txt`)
- **Formato de cada linha**: `[MM:SS] texto do segmento`
  - Exemplo: `[00:12] it's called the dandelion...`
- **Dict retornado** (linhas 57-65):
  ```python
  {
      "success": True,
      "project": project_name,
      "arquivo": "caminho/roteiro_transcricao.txt",
      "texto": "texto completo concatenado (join por espaço)",
      "language": "pt",
      "duration": 150.36,
      "segments": 25
  }
  ```

### Timestamps disponíveis
- **Apenas por segmento** (não por palavra, frase ou cena)
- Cada segmento do faster-whisper tem `seg.start` (float, segundos) — convertido para `MM:SS`
- **Após o loop de segmentos, os timestamps individuais são DESCARTADOS** — só o texto completo e a contagem de segmentos são retornados

### O que NÃO existe:
- ❌ Arquivo JSON/SRT/VTT — só TXT
- ❌ Timestamps por palavra
- ❌ `start` e `end` por segmento preservados (só o `MM:SS` no TXT)
- ❌ `info.duration` é retornado no dict mas não é salvo em arquivo próprio

---

## 3. ESTRUTURA ATUAL DAS CENAS

**Arquivo**: `services/scene_builder.py`

### Criação das cenas
- **Função**: `gerar_cenas(project_name)` — linha 10
- **Lógica**: 
  1. Lê `roteiro_transcricao.txt` linha por linha
  2. Extrai timestamp `[MM:SS]` e texto via regex (linha 40)
  3. Acumula texto enquanto ≤ 200 caracteres (linha 46)
  4. Quando ultrapassa 200 chars, finaliza cena e inicia nova
  5. Se resultar em apenas 1 cena, tenta dividir por pontuação `[.!?]+` (linha 67)

### Estrutura de cada cena (salva em `cenas.json`)
```python
{
    "id": 1,              # int, sequencial 1-based
    "texto": "texto da cena",
    "timestamps": ["00:00", "00:05"],   # list[str], timestamps MM:SS  (ORDEM DE APARIÇÃO, não start/end)
    "topic": ""           # string, sempre vazio (NUNCA populado)
}
```

### O que EXISTE:
- ✅ `id` (int)
- ✅ `texto` (str) — texto acumulado da cena
- ✅ `timestamps` (list[str]) — timestamps no formato `MM:SS`, mas representa timestamps INDIVIDUAIS das linhas, não start/end da cena

### O que NÃO existe:
- ❌ `start_time` — não há um campo de início da cena
- ❌ `end_time` — não há um campo de fim da cena
- ❌ `duration` — não calculado nem armazenado
- ❌ `palavras` / `word_timestamps` — não existe
- ❌ `contexto_anterior` — não existe
- ❌ `contexto_posterior` — não existe
- ❌ `previous_scene_id` / `next_scene_id` — não existe
- ❌ `transcript_segment_start` / `end` — não referência ao texto original

### Problema crítico:
O primeiro timestamp da lista é o início aproximado, o último é o fim aproximado, **mas não há garantia** — a cena acumula timestamps de cada linha que a compõe, sem metadados de tempo reais. A duração é estimada por `_extrair_duracao_cena` no `video_builder.py` convertendo primeiro e último timestamp para segundos.

---

## 4. ESTRUTURA ATUAL DO B-ROLL DIRECTOR

**Arquivo**: `services/broll_director.py`

### Função principal
- **`gerar_storyboard(project_name, usar_claude=True)`** — linha 117
- Lê `cenas.json` do diretório do projeto
- Tenta Claude se `usar_claude=True` e `ANTHROPIC_API_KEY` existir
- Fallback para `_gerar_local()` se Claude falhar ou não configurado

### `_gerar_local()` — fallback local (linha 149)
- Extrai keywords do texto via `_extract_keywords_local(texto)`
- Detecta `scene_type` por matching de palavras-chave
- **Não recebe**: timestamps, duração, contexto, informações da cena além do texto
- **Keywords limitadas a 3** (`max_keywords=3`)

### `_gerar_com_claude()` — chamada batch (linha 189)
- **Uma única chamada** para todas as cenas (linha 207: POST para Anthropic API)
- **Payload enviado** (via `_build_batch_prompt`, linha 92):
  - Apenas `Cena {id}: "{texto[:200]}"` — texto truncado em 200 caracteres
  - NÃO inclui: timestamps, duração, contexto, tipo narrativo
- **Resposta do Claude**: array JSON com `{id, keywords[], scene_type, media_preference}`
- **Mapeamento**: por `id` da cena (linha 226: `claude_map = {item["id"]: item for item in claude_data}`)

### O que Claude recebe vs o que poderia receber:
| Atual | Ideal |
|-------|-------|
| texto[:200] | texto completo |
| - | start_time, end_time, duration |
| - | contexto anterior/posterior |
| - | narrative_role, visual_intent |
| - | energia, emoção, ritmo |

---

## 5. ESTRUTURA ATUAL DA INTEGRAÇÃO CLAUDE

**Arquivo**: `services/broll_director.py`

### Chamada atual
- **Endpoint**: `https://api.anthropic.com/v1/messages` (linha 208)
- **Modelo**: `claude-3-sonnet-20241022` (linha 202)
- **Máx tokens**: 4096 (linha 203)
- **Todas as cenas** são enviadas em uma única chamada
- **Resposta parseada**: regex para extrair JSON array (linha 219)

### Prompt atual (linhas 102-113):
```
You are a B-roll director. For each scene below...
Return ONLY a JSON array with objects:
{"id": int, "keywords": [string], "scene_type": string, "media_preference": "video"|"photo"}
```

### Problemas:
1. **Payload muito enxuto** — só texto truncado, sem contexto
2. **Sem fallback** — se falhar, vai para `_gerar_local` que é genérico
3. **Sem instruções de fallback** — Claude não prepara queries alternativas
4. **Sem histórico** — cada chamada é independente

---

## 6. ESTRUTURA ATUAL DO MEDIA FETCHER

**Arquivo**: `services/media_fetcher.py`

### APIs consultadas (3, em paralelo)
- **Pexels** (`_fetch_pexels`, linha 84): API de vídeos e fotos
- **Pixabay** (`_fetch_pixabay`, linha 120): API de vídeos e fotos
- **Unsplash** (`_fetch_unsplash`, linha 153): apenas fotos

### Execução paralela
- `ThreadPoolExecutor(max_workers=3)` (media_search.py linha ~188)
- Todas as 3 APIs recebem a **MESMA query**

### Score fixo por fonte:
- Pexels: 0.95
- Pixabay: 0.85 (large) / 0.80 (medium)
- Unsplash: 0.75

### URLs extraídas por fonte (conforme .clinerules):
- Pexels: `src.large` (fotos), `best.link` (vídeos) — **NOTA**: foi alterado para `src.get("original", src.get("large2x", src.get("large", "")))`
- Pixabay: `largeImageURL` (fotos), `videos.large.url` (vídeos)
- Unsplash: `urls.raw` ou `urls.full`

---

## 7. FLUXO ATUAL DAS TRÊS APIs

```
media_search.py:buscar_midias_projeto()
  └─ Para cada cena:
       └─ query = " ".join(keywords)
       └─ media_type = scene["media_preference"]
       └─ buscar_para_cena(scene, query, media_type, used_urls)
            └─ buscar_midias_paralelo(query, media_type, used_urls)
                 └─ ThreadPoolExecutor:
                      ├─ _fetch_pexels(query, media_type)  → score 0.95
                      ├─ _fetch_pixabay(query, media_type) → score 0.85
                      └─ _fetch_unsplash(query)            → score 0.75 (só foto)

       └─ baixar_e_classificar(candidato, scene_id)
            └─ baixa, ffprobe resolução
            └─ GREEN (>=1280x720) ou RED (descartado)
```

### Busca em 2 passadas (media_search.py):
1. **1ª passada**: só GREEN, 6 tentativas, troca vídeo→foto na metade
2. **2ª passada**: GREEN ou YELLOW, 6 tentativas
3. **Fallback Biblioteca**: máx 2 reusos por projeto

---

## 8. FLUXO ATUAL DE FALLBACK

**1º nível**: 2 passadas de busca nova (12 tentativas no total)
- Passada 1: apenas resultados GREEN
- Passada 2: GREEN ou YELLOW

**2º nível**: Biblioteca local
- Verifica arquivos existentes na biblioteca
- Máximo 2 reusos do mesmo arquivo por projeto
- Match por: categoria + keyword em comum (Nível 1) ou palavra específica (Nível 2)

**3º nível**: `needs_media` — cena fica sem mídia (nunca força match fraco)

### Problemas:
- ❌ Fallback não é inteligente — repete a mesma query em vez de variar
- ❌ Sem fallback planejado (Claude não prepara queries alternativas)
- ❌ Biblioteca depende de ter mídias já baixadas

---

## 9. FLUXO ATUAL DE CACHE

**Cache de mídia baixada**:
- `assets_cache/scene_{id}/fonte_id.{jpg|mp4}` — por projeto
- Se o arquivo já existe, não baixa novamente

**Cache de processamento**:
- `{projeto}/_processed/scene_{id}_processed.mp4` — pré-processamento de vídeo
- Se `saida.exists()` retorna o cache (video_builder.py linha 44)

**Não existe cache de**:
- ❌ Resultados de API (Pexels/Pixabay/Unsplash)
- ❌ Queries testadas
- ❌ Resultados de busca por cena (só o midias_encontradas.json final)

---

## 10. PROBLEMAS ENCONTRADOS

### Críticos:
1. **`query_generator.py` não é consumido** — gera queries que ninguém usa
2. **Timestamps individuais dos segmentos são descartados** após a transcrição
3. **Cenas não têm start_time / end_time / duration** — só timestamps MM:SS avulsos
4. **Perda de associação cena→mídia na renderização** — só ordinal (posição na lista)
5. **Sem contexto anterior/posterior** nas cenas
6. **Hardcoded `language="pt"`** na transcrição

### Moderados:
7. **Scoring fixo por fonte** — não baseado na qualidade real da query+cena
8. **Payload do Claude muito enxuto** — só texto[:200], sem dados temporais
9. **Sem pool compartilhado de queries** — cada API recebe a mesma query
10. **Fallback não planejado** — repete a mesma query em vez de variar

### Menores:
11. **Topic sempre vazio** nas cenas
12. **`scene_type` detectado por keyword matching frágil**
13. **Keywords limitadas a 3** no fallback local

---

## 11. DADOS QUE FALTAM PARA O CLAUDE

| Dado | Existe? | Onde? |
|------|---------|-------|
| Roteiro completo | ✅ | `roteiro_transcricao.txt` (texto puro) |
| Texto da cena | ✅ | `cenas.json["texto"]` |
| Timestamp início | ⚠️ Parcial | `timestamps[0]` (MM:SS string) |
| Timestamp fim | ⚠️ Parcial | `timestamps[-1]` (MM:SS string) |
| Duração | ❌ | Não calculada no cenas.json |
| Contexto anterior | ❌ | Não existe |
| Contexto posterior | ❌ | Não existe |
| Scene_id | ✅ | `["id"]` |
| Narrative role | ❌ | Não existe |
| Visual intent | ❌ | Não existe |
| Energy/emotion | ❌ | Não existe |
| Segment-level transcript | ❌ | Só o TXT com timestamps por linha |
| Word-level timestamps | ❌ | Whisper não configurado para isso |

---

## 12. CONTRATO DE DADOS RECOMENDADO

### Estrutura ideal para `cena.json` (futuro):
```python
{
    "id": 1,
    "texto": "texto completo da cena",
    "start_time": 12.5,          # segundos
    "end_time": 25.3,            # segundos
    "duration": 12.8,            # segundos
    "timestamps": ["00:12", "00:19"],  # MM:SS (preservado para compatibilidade)
    "topic": "",
    "previous_scene_id": 0,      # 0 se for primeira
    "next_scene_id": 2,          # None se for última
    "transcript_lines": [        # linhas da transcrição que compõem esta cena
        {"timestamp": "00:12", "text": "..."},
        {"timestamp": "00:19", "text": "..."}
    ]
}
```

### Estrutura ideal para o storyboard (futuro):
```python
{
    "id": 1,
    "texto": "texto completo",
    "start_time": 12.5,
    "end_time": 25.3,
    "duration": 12.8,
    "keywords": ["ant", "carrying", "leaf"],
    "scene_type": "explicacao",
    "media_preference": "video",
    "narrative_role": "contexto",        # NOVO
    "visual_intent": "closeup_insect",   # NOVO
    "previous_context": "texto da cena anterior",  # NOVO
    "next_context": "texto da próxima cena",       # NOVO
    "search_strategies": [               # NOVO (vindo do Claude)
        {"query": "...", "fallback": ["...", "..."]}
    ]
}
```

---

## 13. PAYLOAD BATCH RECOMENDADO PARA CLAUDE

### Payload futuro (substituir `_build_batch_prompt`):
```
You are a B-roll director. Analyze the FULL SCRIPT below, then for EACH SCENE,
suggest the ideal B-roll visuals.

FULL SCRIPT:
{roteiro completo}

SCENES (with context):
  Scene 1/9: start=00:00 end=00:12 duration=12s
  Text: "right now, while you're watching..."
  Previous: (none)
  Next: "it's called the dandelion..."

  Scene 2/9: start=00:12 end=00:26 duration=14s
  Text: "it's called the dandelion..."
  Previous: "right now, while you're watching..."
  Next: "cultures around the world..."

For each scene, return a JSON array with:
{
  "id": int,
  "keywords": [3-5 English keywords for stock search],
  "scene_type": "explicacao|exemplo|demonstracao|comparacao|conclusao",
  "media_preference": "video|photo",
  "visual_intent": "closeup|wide|macro|aerial|abstract",
  "narrative_role": "introducao|contexto|demonstracao|transicao|conclusao",
  "search_strategies": [
    {"query": "primary search phrase", "fallback": ["alt1", "alt2", "alt3"]},
    {"query": "secondary approach", "fallback": ["alt1", "alt2"]}
  ],
  "energy": "calm|moderate|dynamic",
  "emotion": "neutral|curious|surprising|educational"
}
```

### O que muda:
1. Inclui **roteiro completo** para contexto global
2. Inclui **start/end/duration** para ritmo
3. Inclui **contexto anterior/posterior** para coerência narrativa
4. Claude retorna **múltiplas estratégias de busca** com fallbacks embutidos
5. Adiciona `visual_intent`, `energy`, `emotion` para melhor curadoria
6. `search_strategies` com fallbacks planejados (4 níveis)

---

## 14. ESTRATÉGIA RECOMENDADA PARA POOL COMPARTILHADO DE QUERIES

### Estado atual:
```
Cada cena → 1 query → testada em 3 APIs → melhor resultado
```

### Estado futuro:
```
Cada cena → Claude gera N queries (1 primary + 3+ fallbacks)
          ↓
          QUERY POOL (compartilhado, não por API)
          ↓
          Q1 → Pexels       Q1 → Pixabay      Q1 → Unsplash
          Q2 → Pexels       Q2 → Pixabay      Q2 → Unsplash
          Q3 → Pexels       Q3 → Pixabay      Q3 → Unsplash
          Q4 → Pexels       Q4 → Pixabay      Q4 → Unsplash
          ↓
          TODOS os resultados → SCORING UNIFICADO
          ↓
          Melhor resultado por cena
```

### Regras do pool:
- Queries pertencem à **cena**, não à API
- Qualquer query pode ser testada em qualquer API
- Se Q1 funcionar melhor no Pexels, Q2 no Pixabay, OK
- Resultados ordenados por score independente da fonte
- Cache de queries já testadas para evitar repetição

---

## 15. ESTRATÉGIA DE FALLBACK

### Estado atual:
```
query original → 2 passadas de busca → biblioteca → needs_media
```

### Estado futuro (planejado pelo Claude):
```
Cena: "ant carrying leaf"
  ├─ Primary: "ant carrying leaf" → fallbacks mantêm INTENÇÃO VISUAL
  │   ├─ FB1: "ant carrying large leaf"
  │   ├─ FB2: "leafcutter ant carrying leaf"
  │   ├─ FB3: "ant carrying food macro"
  │   └─ FB4: "ants carrying leaves"
  │
  ├─ Secondary Approach: "insect carrying load"
  │   ├─ FB1: "insect transporting food"
  │   └─ FB2: "ant working closeup"
  │
  └─ Tertiary (se tudo falhar): "ant insect nature macro"
```

### Regras de fallback:
1. **Nunca perder a intenção visual** — fallback de "ant carrying leaf" → "garden" é proibido
2. **Fallback planejado antecipadamente** pelo Claude — não em runtime
3. **Sem nova chamada Claude** durante a busca
4. **4 níveis** por estratégia para garantir encontrar algo

---

## 16. ORDEM EXATA DE IMPLEMENTAÇÃO RECOMENDADA

### Fase 1 — Preparação dos dados (antes de alterar Claude)
1. **Transcrição**: 
   - Remover `language="pt"` hardcoded (usar detecção automática)
   - Salvar transcrição em JSON com timestamps preservados (`start`, `end` por segmento)
   - Manter TXT para compatibilidade retroativa

2. **Scene Builder**:
   - Adicionar `start_time` (segundos) e `end_time` (segundos) a cada cena
   - Adicionar `duration` calculado
   - Calcular a partir dos timestamps reais (não aproximação)
   - Vincular cada cena ao trecho original da transcrição (índices)

3. **Estrutura das cenas**:
   - Garantir que `cenas.json` contenha dados temporais precisos
   - Criar campo `transcript_lines` com as linhas originais

### Fase 2 — Contrato de dados
4. **Definir estrutura do storyboard expandido**:
   - Adicionar `narrative_role`
   - Adicionar `visual_intent`
   - Adicionar `previous_context` / `next_context`
   - Adicionar `search_strategies` com fallbacks

5. **Atualizar `cenas.json`** para incluir contexto

### Fase 3 — MediaFetcher (preparar para pool)
6. **Criar query pool por cena**:
   - Cada cena pode ter N queries
   - Queries não são mais 1:1 com cena

7. **Implementar teste multi-query multi-API**:
   - Cada query testada em todas as APIs disponíveis
   - Resultados compartilhados

### Fase 4 — B-Roll Director + Claude
8. **Alterar `_build_batch_prompt`**:
   - Incluir roteiro completo
   - Incluir dados temporais precisos
   - Incluir contexto anterior/posterior
   - Pedir search_strategies com fallbacks

9. **Alterar parse da resposta**:
   - Extrair search_strategies, visual_intent, energy, emotion
   - Alimentar media_search com queries do pool

### Fase 5 — Integração final
10. **Conectar query_generator** (ou removê-lo se substituído pelo Claude)
11. **Unificar scoring** (não mais fixo por fonte)
12. **Testar fluxo completo**
13. **Atualizar .clinerules**

---

## RESUMO DO QUE PRECISA MUDAR ANTES DO CLAUDE

### A. O que já está pronto:
- ✅ Pipeline de 5 etapas executando em sequência
- ✅ Transcrição com faster-whisper funcionando
- ✅ Scenes sendo geradas e salvas
- ✅ Storyboard com fallback local
- ✅ 3 APIs consultadas em paralelo
- ✅ Download e classificação de mídia
- ✅ Renderização com FFmpeg
- ✅ Sistema de cache de processamento
- ✅ GUI com polling de logs

### B. O que precisa ser ajustado na transcrição:
- ❌ `language="pt"` hardcoded → usar detecção automática
- ❌ Timestamps de segmento descartados → salvar em JSON
- ❌ TXT-only → adicionar formato JSON com `start`, `end`, `text`

### C. O que precisa ser ajustado no Scene Builder:
- ❌ `start_time` e `end_time` não existem → adicionar
- ❌ `duration` não existe → adicionar
- ❌ `topic` sempre vazio → remover ou popular
- ❌ Sem referência à transcrição original → adicionar `transcript_lines`

### D. O que precisa ser ajustado na estrutura das cenas:
- ❌ Sem contexto → adicionar `previous_context`, `next_context`
- ❌ Timestamps imprecisos (só MM:SS) → usar segundos float
- ⚠️ `texto` está OK (completo)

### E. O que precisa ser ajustado no armazenamento/cache:
- ❌ Cache de queries testadas (evitar repetir chamadas)
- ❌ Cache de resultados de API por query+cena

### F. O que precisa ser ajustado no MediaFetcher:
- ❌ Score fixo por fonte → score dinâmico por relevância real
- ❌ Sem pool de queries → implementar pool compartilhado

### G. O que precisa ser ajustado no sistema de scoring:
- ❌ Score fixo (`pexels=0.95`, `pixabay=0.85`, `unsplash=0.75`)
- ❌ Sem considerar: match semântico, cor, composição, text-overlay suitability

### H. O que pode permanecer intacto:
- ✅ Video Builder (processamento de mídia)
- ✅ Video Encoder (renderização FFmpeg)
- ✅ GUI (interface)
- ✅ Pipeline Service (orquestração)
- ✅ Sistema de cache de arquivos
- ✅ Extração de áudio

### I. O que só deve ser alterado DEPOIS do contrato de dados:
- 🔄 B-Roll Director (`broll_director.py`)
- 🔄 Integração Claude (`_gerar_com_claude`)
- 🔄 Prompt do Claude (`_build_batch_prompt`)
- 🔄 Parse da resposta Claude
- 🔄 Media Search (`media_search.py`) — consumir search_strategies