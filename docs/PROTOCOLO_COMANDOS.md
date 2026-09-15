# Protocolo de Comandos — Lira Studio (`C:\Lira Videos`)

> **Objetivo:** documentar, por ferramenta, *como formular comandos* que o sistema entende —
> pronto para um agente (Claude / Antigravity / Cline) usar na formulação de comandos futuros.
> **Base:** código real do repositório (não suposições). Cada afirmação tem caminho de arquivo
> e/ou evidência de execução (ver §9 — Validações executadas).
> **Data da auditoria:** 15/09/2026 · **Ambiente:** Windows 11 (10.0.22631), Python 3.11.9 (`.venv`), ffmpeg 9.0.

---

## 0. Mapa geral — quais são as "ferramentas" e por onde se comanda cada uma

| # | Ferramenta / Superfície | Ponto de entrada | Transporte | Quem consome hoje |
|---|--------------------------|------------------|------------|-------------------|
| 1 | **HTTP API v1** | `app_web.py` (Flask) | HTTP `127.0.0.1:5000` | Frontend web (`static/app.js`), scripts, agentes |
| 2 | **HTTP API v2** (Studio 2.0) | `services/api_v2.py` (Blueprint em `/api/v2`) | HTTP `127.0.0.1:5000/api/v2/...` | Frontend Studio 2.0, agentes |
| 3 | **SSE de progresso** | `GET /api/v2/producao/<id>/stream` | Server-Sent Events | Frontend (fila do Google Flow) |
| 4 | **CLI** | `main.py` | `python main.py --flags` | Humano / automação de shell |
| 5 | **API Python** | `services/pipeline_service.py` (`PipelineService`) | `import` | `main.py`, `mcp_server/`, GUI, testes |
| 6 | **MCP Server** | `mcp_server/server.py` | stdio, JSON por linha (**não é JSON-RPC MCP**) | **ninguém** hoje (não registrado) |
| 7 | **Google Flow (automação)** | `services/playwright_flow.py` | Playwright CDP porta `9222` | Acionado pela API v1/v2 |
| 8 | **CapCut (draft)** | `capcut_draft_imagens.py` + `capcut_draft.py` (raiz) | Geração de `draft_content.json` | API v1/v2 |
| 9 | **`FLOW/` (legado)** | `FLOW/capcut_draft*.py` | Import indireto (sys.path) | **ninguém** (código órfão) |

**Regra de ouro de escolha:** para *operação de produção* (criar projeto, produzir, montar)
use **HTTP API v1/v2**. Para *automação local dentro do repo* use **CLI** ou **API Python**.
O **MCP** hoje não está conectado a nenhum cliente (§2.6) e fala um dialeto próprio.

---

## 1. Antigravity — como recebe e processa comandos

### 1.1 Instalação e configuração efetiva nesta máquina (verificado)

| Item | Valor real |
|------|-----------|
| Binário | `C:\Users\Administrator\AppData\Local\Programs\Antigravity IDE` |
| Dados do agente | `C:\Users\Administrator\.gemini\antigravity\` (e `.gemini\antigravity-ide\`) |
| Config global | `C:\Users\Administrator\.gemini\config\config.json` |
| MCP global | `C:\Users\Administrator\.gemini\config\mcp_config.json` → **`{"mcpServers": {}}` (vazio)** |
| Workspace registrado | `…\.gemini\config\projects\3a8f38a1-….json` → `file:///c%3A/Lira%20Videos`, branch `main` |
| Política do workspace | `fileAccessPolicy: AGENT_SETTING_POLICY_ALLOW`, `sandboxMode: false`, `autoExecutionPolicy: CASCADE_COMMANDS_AUTO_EXECUTION_EAGER` |
| Execução automática | comandos são executados **sem pedir confirmação** (política EAGER) |
| Permissões globais (`allow`) | `write_file(C:\ultracut3)`, `read_file(C:\ultracut3\FLOW)`, `command(pip)`, `command(python)`, `command(pytest)` |

> ⚠️ **Achado importante:** as permissões globais apontam para o caminho **legado `C:\ultracut3`**
> (inclusive `read_file(C:\ultracut3\FLOW)` — a origem histórica da pasta `FLOW/`).
> O workspace real hoje é `C:\Lira Videos`. Como o workspace tem `fileAccessPolicy: ALLOW` e
> `sandboxMode: false`, os comandos funcionam — mas gravações fora do workspace podem cair em
> "Ask mode". Ao formular comandos para o Antigravity: **sempre use caminhos absolutos** e
> **declare o workspace** (`C:\Lira Videos`).

### 1.2 Como o agente recebe instruções (cadeia real)

1. **Chat/prompt do operador** (linguagem natural) → o agente planeja e emite *tool calls*:
   `write_file`, `read_file`, `command(python|pip|pytest)`, browser, MCP (se houver).
2. **Regras do Antigravity** (Customizations → Rules):
   - Globais: `~/.gemini/GEMINI.md` → **não existe** nesta máquina.
   - Workspace: `.agents/rules/` (legado `.agent/rules`) → **não existe** no repo.
   - Conclusão: hoje o agente **não recebe regras automáticas do projeto** por esse canal.
3. **`.clinerules` (raiz, 105 KB)** → é o canal de regras do **Cline**, não do Antigravity.
   Serve, na prática, como a "constituição" do projeto e é o melhor conteúdo para *promover*
   para `.agents/rules/` (ver §1.4).
4. **`README.md`** → só descreve o Ciclo Narrativo v0.3.5 (não é protocolo de comandos).
5. **MCP** → `mcp_config.json` global vazio e **sem `.agents/mcp_config.json`** no workspace ⇒
   o agente **não** enxerga as 9 ferramentas de `mcp_server/` (§2.6).

### 1.3 Formato de configuração MCP do Antigravity (docs oficiais)

- **Global:** `~/.gemini/config/mcp_config.json`  ·  **Workspace:** `.agents/mcp_config.json`
- **Servidor local (stdio):** `command` + `args` (+ `env` opcional)
- **Servidor remoto:** `serverUrl` (+ `headers`, `authProviderType`, `oauth.clientId/clientSecret`)
- **Permissões de MCP:** `mcp(servidor/ferramenta)`, `mcp(servidor/*)`, `mcp(*)`.
  Por padrão, ferramentas MCP rodam em **modo Ask** (exigem aprovação).

### 1.4 Como formular comandos para o Antigravity (checklist)

1. **Declare o alvo explícito** (caminho absoluto, endpoint, ou comando de shell).
2. **Prefira comando não-interativo** e com `timeout` (terminais novos não recebem input).
3. **Cite o canal**: "via `POST /api/v2/...`", "via `python -m ...`", "via `pytest tests/...`".
4. **Registre o MCP** (opcional, recomendado) em `C:\Lira Videos\.agents\mcp_config.json`:
   ```json
   {
     "mcpServers": {
       "ultracut3": {
         "command": "C:\\Lira Videos\\.venv\\Scripts\\python.exe",
         "args": ["-m", "mcp_server.server"],
         "env": { "PYTHONPATH": "C:\\Lira Videos" }
       }
     }
   }
   ```
   > O par `-m mcp_server.server` + `PYTHONPATH` é **obrigatório**: a forma "ingênua"
   > `python mcp_server\server.py` **falha** com `ModuleNotFoundError: No module named 'mcp_server'`
   > (comprovado, §9.1). Além disso o protocolo do servidor **não é o MCP JSON-RPC** (§2.6).
5. **Promova as regras**: sintetizar o essencial de `.clinerules` para
   `.agents/rules/ultracut3.md` (encoder `h264_amf`, nunca personagem genérico,
   storyboard sem fallback silencioso, caminhos absolutos).
6. **Antes de "concluir"**: rodar `python -m py_compile <arquivo>`, `node --check static/app.js`,
   `pytest tests/...` (§8).

---

## 2. MCP Server (`mcp_server/`) — ferramentas, parâmetros, protocolo

### 2.1 Arquitetura

| Arquivo | Papel |
|---------|-------|
| `mcp_server/__init__.py` | Docstring — "9 ferramentas: project_tools (3), pipeline_tools (3), log_tools (2), system_tools (1)" |
| `mcp_server/app.py` | Registro central `FERRAMENTAS` + `executar_ferramenta(nome, params)` |
| `mcp_server/server.py` | Loop **stdio**: lê JSON por linha (stdin), responde JSON por linha (stdout) |
| `mcp_server/project_tools.py` | `criar_projeto`, `listar_projetos`, `deletar_projeto` |
| `mcp_server/pipeline_tools.py` | `executar_pipeline`, `step_status`, `cancelar_pipeline` |
| `mcp_server/log_tools.py` | `log_event`, `session_report` |
| `mcp_server/system_tools.py` | `system_info` |

### 2.2 Protocolo de fio (wire protocol)

```
stdin  (1 JSON por linha)  →  {"tool": "<nome>", "params": {...}}
stdout (1 JSON por linha)  →  {"result": <retorno da ferramenta>}
                              {"tools": [ ... ]}          # quando tool == "list_tools"
                              {"error": "<texto>"}        # JSON inválido na linha
```

- **Pseudo-ferramenta** `list_tools` → `{"tools":[{name, description, parameters}]}`.
- **Ferramenta inexistente** → `{"result": {"error": "Ferramenta '<x>' não encontrada"}}`.
- **Parâmetro obrigatório ausente** → `{"result": {"error": "Parâmetro obrigatório '<p>' não fornecido"}}`.
- **Exceção na ferramenta** → `{"result": {"error": "<str(e)>"}}` (o servidor **não** morre).
- **JSON inválido** → `{"error": "JSON inválido: <detalhe>"}`.
- Saída com `ensure_ascii=False` (acentos preservados); `flush=True` em cada resposta.

### 2.3 As 9 ferramentas (contrato exato, de `mcp_server/app.py`)

| Ferramenta | Parâmetros (tipo / obrigatório / default) | O que faz | Retorno |
|------------|-------------------------------------------|-----------|---------|
| `criar_projeto` | `nome` (string / **sim**) | `PipelineService().criar_projeto(nome)` | `{"success": true, "project": "<nome_sanitizado>"}` / `{"success": false, "error": "Projeto 'x' já existe"}` |
| `listar_projetos` | — | Lê `meta.json` de cada pasta em `projetos/` | `list[dict]` (o **meta.json inteiro**, inclusive `steps[].details.texto`) |
| `deletar_projeto` | `nome` (string / **sim**) | `shutil.rmtree(projetos/<nome>)` | `{"success": true, "project": ...}` / `{"success": false, "error": ...}` |
| `executar_pipeline` | `project_name` (string / **sim**), `arquivo_video` (string / **sim**) | `PipelineService.executar_pipeline_completo(arquivo_video)` — **bloqueia** até o fim das 5 etapas | `{"success": bool, "project": ..., "results": {etapa: {...}}}` |
| `step_status` | `project_name` (string / **sim**), `step` (string / **sim**) | `meta["steps"][step]` | `{"status": ..., "details": {...}}` ou `{"error": "Etapa 'x' não encontrada"}` |
| `cancelar_pipeline` | — | `PipelineService.cancelar()` | `{"success": true, "message": "Pipeline cancelado"}` |
| `log_event` | `level` (**sim**), `category` (**sim**), `event` (**sim**), `message` (**sim**), `details` (object / opcional) | Anexa linha em `logs/events.jsonl` | `{"success": true}` |
| `session_report` | `minutes` (integer / opcional / **default 120**) | Relatório markdown da sessão (erros, última etapa, cliques de UI) | `{"success": true, "report": "<markdown>", "event_count": N, "error_count": N}` |
| `system_info` | — | `platform`, `ffmpeg -version`, `ffmpeg -encoders`, `-init_hw_device amf` | `{os, os_version, python_version, machine, processor, ffmpeg, h264_amf_available, h264_nvenc_available, libx264_available, amf_supported}` |

**Valores válidos de `step`** (etapas gravadas em `meta.json` → `steps`): `transcrever`,
`gerar_cenas`, `storyboard_broll`, `buscar_midias`, `renderizar` (`PIPELINE_STEPS`, `config.py:203`).

**Categorias de `log_event`** (documentadas no módulo): `UI_CLICK`, `UI_ERROR`, `PIPELINE_STEP`,
`PIPELINE_ERROR`, `RENDER_PROGRESS`, `MEDIA_FETCH`, `SYSTEM`. **Níveis:** `INFO`, `WARN`, `ERROR`
(o módulo aplica `.upper()`).

### 2.4 Como executar (comandos validados)

```powershell
# A) A partir da raiz do projeto (recomendado)
cd 'C:\Lira Videos'
'{"tool":"list_tools"}' | & .\.venv\Scripts\python.exe -m mcp_server.server

# B) De QUALQUER diretório (é assim que um cliente MCP lança o processo)
$env:PYTHONPATH='C:\Lira Videos'
'{"tool":"system_info"}' | & 'C:\Lira Videos\.venv\Scripts\python.exe' -m mcp_server.server

# C) NÃO USE (falha): python mcp_server\server.py  →  ModuleNotFoundError: 'mcp_server'
```

### 2.5 Templates de comando prontos (copiar/colar)

```jsonc
{"tool": "list_tools"}
{"tool": "criar_projeto", "params": {"nome": "Meu Video"}}
{"tool": "listar_projetos"}
{"tool": "step_status", "params": {"project_name": "Teste 02", "step": "transcrever"}}
{"tool": "executar_pipeline", "params": {"project_name": "Meu Video", "arquivo_video": "C:\\audios\\narracao.mp3"}}
{"tool": "cancelar_pipeline"}
{"tool": "log_event", "params": {"level": "info", "category": "SYSTEM", "event": "INICIO", "message": "iniciando lote", "details": {"lote": 3}}}
{"tool": "session_report", "params": {"minutes": 60}}
{"tool": "system_info"}
{"tool": "deletar_projeto", "params": {"nome": "Meu Video"}}
```

### 2.6 Limitações reais (leia antes de usar)

1. **Não é MCP de verdade.** Não há `initialize`, `tools/list` nem `tools/call` (JSON-RPC 2.0);
   é um protocolo próprio `{"tool","params"}` por linha. Um cliente MCP padrão (Antigravity,
   Claude Desktop, Cline) **não** conecta sem adaptador.
2. **Não está registrado em nenhum cliente:** `~/.gemini/config/mcp_config.json` = `{"mcpServers": {}}`
   e não existe `.agents/mcp_config.json` no workspace.
3. **`listar_projetos` pode devolver centenas de KB** — cada `meta.json` carrega
   `steps.details.texto` com a transcrição inteira (no projeto de 218 cenas isso passa de 40 KB
   *por projeto*). Evite essa ferramenta em projetos grandes; prefira ler `projetos/<id>/meta.json`
   ou usar `step_status`.
4. **`executar_pipeline` é síncrono/bloqueante** (transcrição + cenas + storyboard + render).
   Para operação assíncrona use HTTP (§3), que roda em threads.
5. **`PipelineService` é instanciado no import** de `project_tools`/`pipeline_tools`
   (`_pipeline = PipelineService()`), e `config.py` cria as pastas no import.
6. **`buscar_midias` é no-op** (§5.3): `executar_pipeline` "conclui" a etapa de mídias sem buscar nada.

### 2.7 Como tornar o MCP realmente consumível (caminho recomendado)

Como o dialeto atual não é JSON-RPC, há duas rotas:

- **Rota A (menor esforço, agente ↔ agente):** manter `mcp_server/` como *biblioteca de comandos*
  e usar os templates de §2.5 via `echo`/`python -m` em comandos de shell (funciona hoje).
- **Rota B (MCP oficial):** envolver `mcp_server.app.FERRAMENTAS` num servidor JSON-RPC usando o
  SDK oficial `mcp` (`tools/list` → `FERRAMENTAS`; `tools/call` → `executar_ferramenta`),
  expondo as 9 ferramentas sem reescrever a lógica. Só então o `mcp_config.json` do Antigravity
  passa a listá-las de fato.

---

## 3. HTTP API — a superfície de comando principal (Flask)

### 3.1 Servidor, convenções e autenticação

| Item | Valor real |
|------|-----------|
| App | `app_web.py` — `Flask(__name__, static_folder="static")` |
| Bind | `app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)` → **somente local** |
| Blueprint v2 | `app.register_blueprint(api_v2_bp, url_prefix="/api/v2")` (`app_web.py:1414`) |
| Upload máximo | `MAX_CONTENT_LENGTH = 1 GB` |
| Autenticação | **NENHUMA** — `require_auth` é um decorator *no-op* (`app_web.py:1417`) e `/api/auth/status` responde `{"autenticado": true}` sempre. Nunca exponha a porta 5000. |
| Inicialização | `iniciar_web.bat` (matar zumbi na 5000 → subir Flask → `_preparar_flow.py` → abrir Chrome CDP) |
| Convenção de resposta | JSON com `success: bool`; erros vêm com `error: str` e HTTP 400/409/500 |
| Progresso | `GET /api/eventos/<id>?since=N` (polling de `logs/events.jsonl`) e SSE em `/api/v2/producao/<id>/stream` |
| Chaves de API | `web_keys.json` (gitignored) — Claude, DeepSeek, Pexels, Pixabay, Unsplash; **sempre mascaradas** no retorno |
| Pastas padrão | `web_config.json` → `pasta_midia_padrao`, `pasta_destino`, `pasta_capcut` |

### 3.2 Receitas de comando (sequências prontas)

**R1 — Fluxo completo via API v2 (modo Studio 2.0 / Google Flow):**

```bash
# 1) criar projeto (JSON; áudio pode vir depois)
POST /api/v2/projeto/criar          {"nome":"Meu Video","modo_producao":"imagem_video","nome_personagem":"Marcos","estilo_visual":"photorealistic_cinematic"}
# 2) anexar áudio (multipart, campo "audio") → transcreve
POST /api/v2/transcricao/Meu Video/upload_audio
# 3) (opcional) SRT manual em vez de Whisper
POST /api/v2/transcricao/Meu Video/usar_srt   {"srt_texto":"[00:00] texto..."}
# 4) gerar plano de cenas (lira_scene_plan.json)
POST /api/v2/storyboard/Meu Video/gerar
# 5) gerar prompts (imagem + animação, com locks de personagem/estilo)
POST /api/v2/prompts/Meu Video/gerar        {"estilo_visual":"photorealistic_cinematic","nome_personagem":"Marcos"}
# 6) produzir no Google Flow (imagens)
POST /api/v2/producao/Meu Video/iniciar_fila  {"modo":"imagem"}      # ou {"modo":"animacao"} / {"scene_ids":[3,7]}
# 7) acompanhar: GET /api/v2/producao/Meu Video/status  |  SSE /stream
# 8) montagem
POST /api/v2/montagem/Meu Video/sincronizar
POST /api/v2/montagem/Meu Video/renderizar_mp4       # vídeo .mp4 final (ffmpeg)
POST /api/v2/montagem/Meu Video/exportar_capcut      # draft nativo do CapCut Desktop
```

**R2 — Fluxo legado (v1) completo:** `POST /api/criar_projeto` → `POST /api/upload_audio/<id>`
→ `POST /api/storyboard_api/<id>` → `POST /api/montar_video/<id>` → `POST /api/salvar_video/<id>`.

**R3 — Retomada:** `POST /api/v2/producao/<id>/retomar_projeto` (sem espera de rate-limit) ou
`/retomar` (com a espera de 10s). `POST /api/v2/producao/<id>/retentar_erros` reenfileira só ERRO.

**R4 — Diagnóstico:** `GET /api/v2/producao/<id>/status` (contagens, `pause_reason`,
`creditos_restantes_total`, `fallback_video_ativo`) · `GET /api/v2/console/logs_globais` ·
`GET /api/v2/metrics/<id>` · `GET /api/v2/versions/<id>`.

### 3.3 API v1 (`app_web.py`) — endpoints de comando

| Método | Rota | Parâmetros | Efeito |
|--------|------|-----------|--------|
| POST | `/api/criar_projeto` | multipart: `nome` (**obrig.**), `modo` (`automatico`\|`manual`, default `automatico`), `audio` (opcional) | Cria projeto; **não** transcreve; estado `aguardando_audio`; HTTP 201 |
| POST | `/api/upload_audio/<id>` | multipart `audio` (**obrig.**) | Salva áudio, inicia transcrição (thread) e, se `modo_execucao=automatico`, o fluxo completo |
| POST | `/api/srt_manual` | JSON `{projeto_id, srt}` | Substitui a transcrição por SRT colado (aceita `[MM:SS] texto`, `MM:SS texto` e SRT padrão) |
| GET | `/api/transcricao/<id>` | — | Texto + segmentos processados |
| GET | `/api/transcricao/<id>/download?formato=txt\|srt` | `formato` | Baixa transcrição |
| POST | `/api/gerar_prompt/<id>` | — | Gera prompt manual (MASTER STYLE + transcrição) |
| POST | `/api/storyboard_api/<id>` | — | Storyboard via Claude (timeout 30s; **sem** fallback local silencioso) |
| GET | `/api/status/<id>` | — | Estado do fluxo (etapas, mídia, `arquivo_audio`, `buscar_videos_status`, `pause_reason`) |
| POST | `/api/selecionar_midia/<id>` | JSON `{caminho, media_type?}` | Valida pasta de mídia local (modo local-timestamp) |
| POST | `/api/importar_imagens/<id>` | JSON `{caminho}` | Importa imagens geradas (número no nome ou `[MM-SS]`) |
| POST | `/api/buscar_videos/<id>` · `/api/pular_buscar_videos/<id>` | — | Busca estrita de vídeos (Card 3) / pula a etapa |
| POST | `/api/montar_video/<id>` | — | Render do vídeo final |
| POST | `/api/exportar_capcut/<id>` · `/api/flow/montar_e_exportar_capcut/<id>` | — | Gera draft CapCut |
| POST | `/api/salvar_video/<id>` · `/api/abrir_pasta/<id>` | — | Copia para `pasta_destino` / abre a pasta no Explorer |
| POST | `/api/reprocessar/<id>` · `/api/avancar/<id>` | JSON `{etapa}` | Reexecuta uma etapa / força como concluída |
| POST | `/api/deletar_projeto/<id>` | — | Remove a pasta do projeto |
| GET/POST | `/api/config` | JSON (`pasta_midia_padrao`, `pasta_destino`, `pasta_capcut`, `*_api_key`) | Lê/grava config global (chaves mascaradas) |
| GET | `/api/eventos/<id>?since=N` | `since` | Polling de eventos do projeto (`{eventos, since}`) |
| POST | `/api/flow/abrir` · `/api/flow/reconectar` · `/api/flow/desconectar` | JSON `{projeto_id}` | Ciclo de vida da sessão Playwright CDP |
| GET | `/api/flow/status` · `/api/flow/contas` | — | Estado do worker / contas Google Flow |
| POST | `/api/flow/contas/ativar` · `/adicionar` · `/remover` · `/login_guiado` | JSON (conta/email) | Gestão de contas do Flow |
| POST | `/api/flow/enqueue/<id>` · `/api/flow/enqueue_anim/<id>` | JSON | Enfileira produção (imagem / animação) |
| POST | `/api/flow/fila/parar` · `/api/flow/fila/limpar` | JSON | Para/limpa a fila (parada manual grava `pause_reason="manual"`) |
| GET | `/api/scene_plan/<id>` · `/api/scene_plan/<id>/progresso` | — | Plano de cenas / progresso |
| PATCH | `/api/scene_plan/<id>/<scene_id>` | JSON (campos editáveis) | Edita uma cena |
| POST | `/api/v2/validar_capcut/<id>` · `GET` | — | Validação pré-CapCut (3 fontes sincronizadas) |

### 3.4 API v2 (`services/api_v2.py`) — endpoints de comando (prefixo `/api/v2`)

**Projeto / configuração**

| Método | Rota | Parâmetros |
|--------|------|-----------|
| POST | `/projeto/criar` | JSON: `nome` (**obrig.**), `modo_producao` (default `imagem_video`), `nome_personagem`, `estilo_visual` (default `photorealistic_cinematic`), `continuidade_visual`; multipart `audio` opcional. Cria 6 pastas padrão e `meta.json` (`studio_version: v2`). HTTP 409 se já existe. |
| GET/POST | `/projeto/<id>/config` | POST aceita `modo_producao`, `nome_personagem`, `estilo_visual`, `continuidade_visual`, `referencia_visual_global`, `prod_modelo[_imagem\|_video]`, `prod_qualidade[_imagem\|_video\|_download]`, `prod_tipo_saida`, `prod_proporcao`, `provedor_storyboard`, `provedor_prompts` |
| GET | `/projetos` · `/projetos/listar` · `/projeto/<id>/scene_plan` | — |
| GET | `/projeto/<id>/cena/<cid>` · PATCH `/cena/<cid>/prompt` | Leitura / edição de prompt |
| POST | `/projeto/<id>/gerar_prompts_ia` · GET `/prompt_ia_status` | Prompts via IA |

**Transcrição / storyboard / prompts**

| Método | Rota | Parâmetros |
|--------|------|-----------|
| POST | `/transcricao/<id>/upload_audio` | multipart `audio`\|`file` |
| POST | `/transcricao/<id>/usar_srt` | JSON `{srt_texto}` (**obrig.**; SRT padrão ou `[MM:SS]`) |
| GET | `/transcricao/<id>/status` | — |
| POST | `/storyboard/<id>/gerar` | Gera/atualiza `lira_scene_plan.json` (`force=True`) |
| POST | `/projeto/<id>/force_narrativa_v3` | Rebalanceia retenção/avatar/B-roll |
| POST | `/prompts/<id>/gerar` | JSON opcional `{estilo_visual, nome_personagem}` |
| GET | `/presets/estilos` · `/deepseek/status` · POST `/deepseek/config` · `/config/testar_provedor` | — |

**Produção (Google Flow)**

| Método | Rota | Parâmetros / retorno |
|--------|------|----------------------|
| GET | `/producao/<id>/status` | `progresso`, `contagem_midia` (imagem/imagem_animar/video/texto), `resume_info` (prontas/erros/gerando/pendentes + `proxima_cena_id`), `cenas`, `flow`, `pause_reason`, `stop_requested`, `creditos_restantes_total`, `fallback_video_ativo` |
| GET | `/producao/<id>/stream` | **SSE** (`progresso_fila`, `navegarAba`, ping ~15s) |
| POST | `/producao/<id>/iniciar_fila` | JSON `{scene_ids?: [int], modo?: "imagem"\|"animacao"\|"animacao_apenas"}`. HTTP 400 sem cenas; 409 se já rodando; retorna `{enfileiradas, proxima_cena_id}` |
| POST | `/producao/<id>/retomar` | Retoma da 1ª pendente/erro (**com** espera de 10s de rate-limit) |
| POST | `/producao/<id>/retomar_projeto` | Alias de `/retomar` **sem** a espera inicial (v9.12) |
| POST | `/producao/<id>/retentar_erros` | Reenfileira apenas cenas `status == ERRO` |
| POST | `/producao/<id>/reclassificar_animacoes` · `/animar_todos_videos` | Animation Director |
| POST | `/producao/<id>/auto_importar` | Importa mídias geradas e pareia com cenas |
| POST | `/producao/<id>/enviar_cena` | JSON `{scene_id}` — envia UMA cena |
| GET | `/producao/<id>/live_console` · `/console/logs_globais` | Telemetria / console |
| GET | `/flow/contas` · POST `/flow/trocar/<email>` | Contas Google Flow |

**Personagem / identidade / memória**

| Método | Rota | Parâmetros |
|--------|------|-----------|
| POST | `/personagem/<id>/criar_flow` | multipart/JSON `nome` (**obrig.**) + `imagem`/`foto`/`personagem`; `estilo_visual` opcional. Assíncrono (<100 ms) → acompanhe `GET /personagem/<id>/criar_flow_status`. **Recusa nome genérico** (`avatar`, `personagem`, `@me`…) com HTTP 400 |
| POST/PATCH/DELETE | `/projeto/<id>/identidade` · `/identidade/<id>/salvar` · `/remover` | Identidade (`tipo`, `nome`, `referencia_flow`, `imagem_abs`…) |
| GET | `/identidade/<id>` · `/identidade/<id>/avatar` · `/personagens/biblioteca` | Leitura |
| POST | `/personagem/<id>/cadastrar` · `/remover` · GET `/ativo` | Personagem ativo |
| GET/POST | `/referencias/<id>` · POST `/adicionar` · PATCH `/renomear` · DELETE `/<alias>` | Referências visuais |
| GET/POST | `/memoria/<id>` | Memória visual acumulada |

**Montagem / CapCut / render**

| Método | Rota | Parâmetros |
|--------|------|-----------|
| POST/GET | `/montagem/<id>/sincronizar` | Sincroniza mídia×cena (3 fontes) e devolve faltantes |
| POST | `/montagem/<id>/renderizar_mp4` | Render final via ffmpeg (usa `meta.arquivo_audio`) |
| GET | `/montagem/<id>/render_status` · `/download_video_final` | Progresso / download |
| POST | `/montagem/<id>/exportar_capcut` | Gera draft nativo (`capcut_draft_imagens.criar_draft_imagens`) |
| GET | `/montagem/<id>/exportar_zip` | Pacote ZIP do projeto |
| POST | `/montagem/<id>/gerar_broll_mp4` · GET `/broll_status` | Imagens → clipes Ken Burns |
| POST | `/montagem/<id>/transicoes_lote` | JSON `{tipo, duracao_ms, lado}` (defaults `bordas_difusas`, `500`, `saida`) |
| POST | `/montagem/<id>/legendas_lote` | JSON `{estilo_id, ativar_todas}` (default `amarelo_capcut`, `true`) |
| POST | `/montagem/<id>/reordenar_cenas` · `/direcionar_movimentos` · `/musica/definir` | Ajustes finos |
| PUT/POST/DELETE | `/montagem/<id>/cena/<scene_id>` · `/remover` | Edita/remove cena |
| GET | `/montagem/<id>/musica/perfil` · `/capcut/transicoes` · `/capcut/legendas` | Catálogos |

**Arquivos / métricas / misc**

| Método | Rota | Parâmetros |
|--------|------|-----------|
| GET | `/arquivos/<id>/listar?categoria=<audio\|cenas\|prompts\|export\|metadata\|...>` | Lista categorizada |
| GET | `/arquivos/<id>/download/<categoria>/<nome>` · POST `/abrir_pasta` · `/limpar_temporarios` | Arquivos |
| GET | `/cena_media/<id>/<scene_id>` | Serve a mídia; 404 com `reason: "arquivo_deletado"` quando o arquivo sumiu (v9.12) |
| GET | `/diretor3/<id>` · POST `/autonomous_direct/<id>` | Direção autônoma |
| GET | `/metrics/<id>` · POST `/human_feedback/<id>/<scene_id>` · GET `/versions/<id>` | Métricas/feedback/versões |
| POST | `/projeto/<id>/exportar_capcut` · GET `/baixar_capcut` · POST `/abrir_pasta` | Atalhos de export |

### 3.5 SSE — `GET /api/v2/producao/<id>/stream`

Eventos (`data:` JSON por evento, via `stream_with_context`):

```jsonc
{"tipo":"progresso_fila","total":92,"por_status":{"PENDENTE":10,"BAIXADA":82},
 "flow":{"conectado":true,"fila_parada":false,"stop_requested":false,"pause_reason":""},
 "pause_reason":"", "stop_requested":false}
{"tipo":"navegarAba","aba":"montagem","motivo":"credito_esgotado_video","timestamp":"22:33:42.883"}
{"ping": true}   // keep-alive a cada ~15s (por relógio, não por ticks)
```

`pause_reason` canônico: `""`/`nao_pausado`, `credito_esgotado_video`, `credito_esgotado_imagem`,
`fim_fila_credito_zerado`, `manual`. Os três primeiros disparam `navegarAba → "montagem"`.

---

## 4. CLI (`main.py`) — comandos de terminal

```
python main.py [--listar] [--criar NOME] [--projeto NOME] [--video ARQUIVO] [--etapa ETAPA]
```

| Flag | Efeito | Saída |
|------|--------|-------|
| `--listar` | `PipelineService.listar_projetos()` | JSON (metas) |
| `--criar NOME` | Cria o projeto (`meta.json` com `steps: {}`) | `{"success": true, "project": "nome_sanitizado"}` |
| `--projeto NOME --video ARQ --etapa ETAPA` | Executa a etapa pedida | JSON do resultado |
| `--etapa` aceita | `transcrever` \| `cenas` \| `storyboard` \| `queries` \| `buscar` \| `renderizar` \| `completo` (default) | — |

```powershell
cd 'C:\Lira Videos'; & .\.venv\Scripts\python.exe main.py --listar
& .\.venv\Scripts\python.exe main.py --criar "Meu Video"
& .\.venv\Scripts\python.exe main.py --projeto "Meu Video" --video "C:\audios\narracao.mp3" --etapa completo
```

**Observações:** (a) exige `--video` para qualquer etapa; (b) `--etapa queries` chama
`gerar_queries` (módulo desativado no pipeline — ver §5.3); (c) `--etapa buscar` é **no-op**
(retorna `success: true` sem buscar); (d) `--etapa completo` bloqueia até o render.

---

## 5. API Python (`services/pipeline_service.py`)

### 5.1 `PipelineService` — métodos de comando

| Método | Assinatura | Retorno (chaves principais) |
|--------|-----------|------------------------------|
| `criar_projeto` | `(nome: str, arquivo_audio: str = "")` | `{"success", "project"}` ou `{"success": False, "error": "Projeto 'x' já existe"}` |
| `listar_projetos` | `()` | `list[dict]` (metas; `{"name", "steps": {}}` quando falta `meta.json`) |
| `transcrever` | `(arquivo_video: str)` | resultado do Whisper + fallback SRT de `~/Downloads` |
| `gerar_cenas` | `()` | `scene_builder.gerar_cenas()` → `{"success", "cenas_count"}` |
| `gerar_storyboard` | `(usar_claude: bool = True)` | `{"success", "cenas_count", "camada"}` (`camada = "local"` quando Claude falha) |
| `gerar_queries` | `()` | `query_generator.gerar_queries()` (**passo morto no pipeline**) |
| `buscar_midias` | `()` | **no-op**: `{"success": True, "green": 0, "yellow": 0, "needs_media": 0}` |
| `renderizar` | `()` | `video_encoder.renderizar_video(...)` → `{"success", "arquivo", "tamanho"}` |
| `executar_pipeline_completo` | `(arquivo_audio: str)` | `{"success", "project", "results": {etapa: {...}}}` |
| `get_step_status` | `(step: str)` | `meta["steps"][step]` ou `None` |
| `get_logs_projeto` | `()` | conteúdo de `projetos/<id>/logs.json` |
| `pausar` / `continuar` / `cancelar` | `()` | grava/limpa `projetos/<id>/pipeline_state.json` |
| `set_progress_callback` | `(fn(step_index, status, message))` | — |
| `set_pause_check_callback` | `(fn() -> bool)` | consultado antes de cada item (mídia/render) |

**Etapas/índices** (`config.PIPELINE_STEPS`): `0 transcrever` · `1 gerar_cenas` ·
`2 storyboard_broll` · `3 buscar_midias` · `4 renderizar`.

### 5.2 Exemplo de script de automação (Python)

```python
from services.pipeline_service import PipelineService

p = PipelineService()
p.criar_projeto("Lote 01")
p.project_name = "Lote 01"                      # obrigatório após criar
print(p.transcrever(r"C:\audios\narracao.mp3"))
print(p.gerar_cenas())
print(p.gerar_storyboard(usar_claude=True))
print(p.get_step_status("storyboard_broll"))
print(p.renderizar())                            # exige mídia em disco
```

### 5.3 Passos mortos / no-ops (não perder tempo comandando)

- `buscar_midias()` → sempre `success: True` sem buscar nada (substituído pelo Google Flow).
- `gerar_queries()` → `query_generator` está desativado; `buscar_midias` lia `storyboard.json` direto.
- `search_queries`/Pexels são **legado**: a produção real é o Google Flow (§6.2).

---

## 6. `FLOW/` — o que é de fato (e o que é o "Flow" do pipeline)

### 6.1 Conteúdo real da pasta (verificado)

```
FLOW/
├── capcut_draft.py          (33 KB — gerador de draft; _PLATFORM_INFO v8.7.0 HARDCODED)
├── capcut_draft_imagens.py  (10 KB — API ANTIGA de imagens+áudio)
└── __pycache__/             (bytecode de ambos)
```

| Aspecto | `FLOW/` | Raiz (usado em runtime) |
|---------|---------|--------------------------|
| `capcut_draft.py` | 33 KB; `_PLATFORM_INFO` fixo `app_version 8.7.0`, `device_id 0dd38ac3…`, `hard_disk_id ebd4a8c3…` | 50 KB; `_plataforma_de_draft_nativo()` lê o `platform` de um draft NATIVO; fallback `_FALLBACK_*`; marcador `_ultracut3_gerado.json` |
| `capcut_draft_imagens.criar_draft_imagens` | `(imagens, audio_path, audio_dur_seg, pasta_destino, nome_projeto, largura, altura, fps)` | `(project_name, lista_cenas, arquivo_audio, destino_drafts, nome_projeto=None)` com `lista_cenas=[{start, arquivo, media_type, duracao}]` |
| Importado por | **nenhum** módulo do projeto (grep por `from FLOW`/`import FLOW`/`FLOW.capcut` = 0 ocorrências) | `app_web.py`, `services/api_v2.py` (`from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts`) |

> **Conclusão:** `FLOW/` é **código legado/órfão** (cópia pré-v9.3 do exportador CapCut).
> O nome da pasta vem do path antigo `C:\ultracut3\FLOW` (ainda citado nas permissões do
> Antigravity, §1.1). **Não use `FLOW/` para gerar drafts**: o `_PLATFORM_INFO` fixo grava um
> `hard_disk_id` errado e o CapCut abre-e-fecha (bug corrigido na v9.4). Se algum dia for
> necessário importá-lo, só via `sys.path.insert(0, r"C:\Lira Videos\FLOW")` — e ciente do risco.

### 6.2 O "FLOW" que realmente importa: automação do Google Flow

`services/playwright_flow.py` (310 KB) controla o **Google Flow** (`https://labs.google/fx/tools/flow`)
via Chrome + CDP na porta `9222`. Etapas, entradas e saídas:

| Etapa | Entrada | Saída/efeito |
|-------|---------|--------------|
| 1. Subir/garantir CDP | `ensure_chrome_cdp(port=9222, force_restart=False)` → `(ok, msg)` | Chrome com `--remote-debugging-port` usando `chrome_profile/` |
| 2. Conectar/abrir projeto Flow | `POST /api/flow/abrir` `{projeto_id}` → `FlowSessionManager.start_session()` | Aba do Flow no projeto; URL salva em `flow_meta.json` |
| 3. Configurar modo/modelo | `_set_output_mode(target_mode="image"\|"video", modelo_solicitado, proporcao_solicitada="16:9", qualidade_solicitada="x1")` | Menus do Flow ajustados (ex.: `Nano Banana Pro` / `Veo 3.1 - Lite`) |
| 4. Enfileirar produção | `FlowQueueWorker.start_worker(projeto_id, scene_ids: list[int]\|None, modo="imagem"\|"animacao", bypass_rate_limit=False)` | Thread `FlowCDP-<projeto>`; cada cena passa por PENDENTE→ENVIANDO→GERANDO→BAIXADA |
| 5. Anexar personagem | `incluir_referencia_personagem(page, reference_path, ...)` (**exige arquivo real**; valida `identidade.json`) | Referência `@Nome` anexada no Flow — **nunca** card genérico |
| 6. Criar avatar | `criar_avatar_flow_via_playwright(projeto_id, nome, imagem_abs)` | Personagem criado no Flow (créditos ~0.15) |
| 7. Salvar mídia | `salvar_midia_cena_estruturada(...)` | Arquivo em `projetos/<id>/cenas/NN_[MM-SS].ext` + `metadata/cena_XXX/{prompt.txt,status.json}` + `midias_encontradas.json` + galeria |
| 8. Estado/pausa | `FlowQueueWorker.get_status()` | `{conectado, rodando_fila, cena_ativa, modo, pause_reason, stop_requested, pause_reason_ts, fallback_video_imagen}` |

**Estados de cena** (`services/scene_plan_service.py:55`): `PENDENTE`, `ENVIANDO`, `GERANDO`,
`GERADA`, `BAIXADA`, `ERRO` (alias legados convergem: `ENVIADA→ENVIANDO`, `PROMPT_PRONTO→PENDENTE`,
`MIDIA_IMPORTADA/ANIMADA/MONTADA/PRONTA_*→BAIXADA`).
**Tipos de cena:** `image` · `video` · `text` (`TIPO_IMAGE/TIPO_VIDEO/TIPO_TEXT`).

**Contas/limites:** `config/flow_accounts.json` + `services/flow_account_manager.py`
(auto-reset diário de `creditos_esgotados`, rotação automática). Endpoints:
`GET /api/v2/flow/contas`, `POST /api/v2/flow/trocar/<email>`, `POST /api/flow/contas/*`.

### 6.3 CapCut — comando de exportação (raiz)

```python
from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts

criar_draft_imagens(
    project_name="Teste 02",
    lista_cenas=[{"start": 0.0, "arquivo": r"...\cenas\01_[00-00-00-02].png",
                  "media_type": "photo", "duracao": 2.0}, ...],
    arquivo_audio=r"...\Teste 02\Teste 02.MP3",
    destino_drafts=detectar_pasta_drafts(),   # ou web_config.json → pasta_capcut
    nome_projeto=None,
)
# → {"success": True, "draft_dir": "...", "nome": "...", "cenas_exportadas": N,
#    "duracao_total": 452.64, "registrado_capcut": True}
```

Via HTTP: `POST /api/v2/montagem/<id>/exportar_capcut` (ou `/api/exportar_capcut/<id>`).
Pasta alvo real: `C:\Users\Administrator\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft`.
`criar_draft_capcut(...)` (raiz) é a variante de timeline única com legendas/punch-ins/reframes.

---

## 7. Estrutura de um projeto exemplo (`projetos/<nome>`)

### 7.1 Árvore real (projeto `Teste 02`, 92 cenas no plano / 120 mídias)

```
projetos/Teste 02/
├── Teste 02.MP3                  # áudio original (raiz, cópia de v1)
├── meta.json                     # estado/estágios do projeto (23 KB)
├── lira_scene_plan.json          # PLANO DE CENAS Studio 2.0 — 92 cenas, 510 KB
├── roteiro_transcricao.json/.txt # transcrição (fonte de verdade temporal)
├── word_timestamps.json          # timestamps por PALAVRA (Whisper)
├── srt/roteiro_transcricao.{json,srt}
├── midias_encontradas.json       # índice cena → arquivo (lista)
├── identidade.json               # personagem oficial (@Marcos)
├── personagem_global.png         # foto de referência do personagem
├── galeria.json                  # galeria de mídias geradas
├── logs.json                     # log por etapa
├── flow_meta.json                # URL/estado do projeto no Google Flow
├── project_versions.json         # versionamento
├── project_visual_context.json   # contexto visual (locks)
├── project_visual_memory.json    # memória visual
├── production_metrics.json       # métricas de produção
├── audio/audio_original.mp3      # áudio canônico v2
├── cenas/                        # MÍDIA FINAL: NN_[MM-SS].png|mp4
├── conteudo/                     # downloads brutos do Flow (001.png, 004.mp4…)
├── metadata/cena_XXX/            # prompt.txt + status.json por cena
├── prompts/prompts.txt, storyboard_prompts.txt
├── prompt_history/scene_XXX.txt  # histórico de prompts por cena
├── characters/<Personagem>/reference.png
├── capcut/ultimo_export.json     # último draft exportado
├── export/                       # entregáveis
├── memory/visual_memory.json
└── .temp/
```

### 7.2 Contratos dos arquivos-chave

**`meta.json`** (chaves reais observadas): `name`, `display_name`, `created`, `modo_execucao`
(`manual`/`automatico`), `modo_producao`, `studio_version` (`v2`), `nome_personagem`,
`estilo_visual`, `continuidade_visual`, `referencia_visual_global`, `arquivo_audio`,
`transcricao_completa`, `steps` (`{etapa: {status, details}}`), `identidade_tipo`,
`personagem_locked`, `referencia_flow`, `provedor_storyboard`, `provedor_prompts`,
`alerta_credito`, `arquivo_flow`, `personagem_global_path` e (v1) `web_event_start_idx`.

**`lira_scene_plan.json`** — topo: `projeto`, `versao`, `narrativa_versao`, `gerado_em`, `total`,
`cenas[]`, `visual_context`. Cada cena tem **~76 campos**; os essenciais para comandar:

| Campo | Tipo | Uso |
|-------|------|-----|
| `id` / `scene_index` | int | Identidade da cena (usar `id` em todos os comandos) |
| `start` / `end` / `tempo_inicio` / `tempo_fim` | float s | Janela temporal (ordene por `tempo_inicio`) |
| `timestamp` | `"MM:SS - MM:SS"` | Exibição |
| `texto` / `narration` | str | Fala da cena |
| `visual_prompt` / `prompt_imagem` | str | Prompt de imagem (Google Flow) |
| `prompt_animacao` | str | Prompt de vídeo/animação |
| `scene_type` | str | `avatar_talking`, `avatar_action`, … |
| `tipo` / `media_intent` | `image`\|`video`\|`text` | O que será gerado |
| `animar` / `animate_later` / `animar_depois` | bool | **devem andar juntos** (o worker lê `animate_later`) |
| `status` | str | Máquina de estados (§6.2) |
| `arquivo_midia` / `download_path` / `filename` | str | Mídia resolvida |
| `character_ref` / `uses_character` | `"@Marcos"` / bool | Referência de personagem (nunca genérica) |
| `narrative_role` | `HOOK`\|`AVATAR`\|`BROLL`\|`CTA`\|`CLOSING` | Papel narrativo (Fases 1/3) |
| `avatar_required`, `broll_query`, `recommended_duration`, `action_verb`, `intensity` | vários | Planejamento visual |
| `video_url`, `broll_url`, `broll_status` | str | B-roll |
| `transicao_entrada` / `transicao_saida` | str | CapCut |
| `timecode_padrao`, `arquivo_nome`, `pasta`, `midia_padrao` | str | Padrão Lira v0.3.0 (3 fontes sincronizadas) |

**`midias_encontradas.json`** — **lista** de dicts:
```json
[{"scene_id": 1, "success": true,
  "arquivo": "C:\\Lira Videos\\projetos\\Teste 02\\cenas\\01_[00-00-00-02].png",
  "quality": "green", "media_type": "photo", "origem_midia": "flow_automation"}]
```

**`identidade.json`** — `tipo`, `nome`, `arquivo_flow`, `referencia_flow` (`"@Marcos"`),
`imagem`/`imagem_abs`, `flow_character_created`, `flow_character_name`, `flow_character_id`,
`status`, `atualizado_em`, `personagens[]`.

**`pipeline_state.json`** (quando pausado) — `step`, `last_completed_idx`, `total`, `project`,
`timestamp`, `paused`.

### 7.3 Nomenclatura canônica de mídia (`services/media_standard.py`)

- Padrão: `{id:02d}_[{MM:SS}-{MM:SS}]{ext}` → ex.: `01_[00:00-00:05].png`.
- Windows não aceita `:` → o físico usa `-`: `01_[00-00-00-05].png` (o timecode com `:` vai para
  `timecode_padrao`). ⚠️ Nos nomes vindos do Flow aparece também a variante pareada
  `01_[00-00-00-02].png` — todas são aceitas pelo parser.
- 3 fontes sempre sincronizadas: `lira_scene_plan.json.arquivo_midia` ↔ `draft_content.json.path`
  ↔ arquivo físico em `<projeto>/cenas/`. Validar com `POST/GET /api/v2/validar_capcut/<id>`.

---

## 8. Regras de ouro ao formular comandos (checklist do agente)

**Ambiente**
1. Use **sempre** `.venv\Scripts\python.exe` (Python 3.11). Nunca outro interpretador.
2. Caminhos absolutos; o projeto é `C:\Lira Videos` (não `C:\ultracut3` — só legado).
3. Encoder: **`h264_amf`** (AMD). **Nunca** `h264_nvenc`; fallback `libx264` via
   `config.resolver_encoder()` — não hardcode encoder em comandos novos.
4. `node --check static/app.js` antes de dar JS como pronto; `python -m py_compile <arquivo>`
   antes de dar Python como pronto; `pytest tests/...` para validar.

**Produção**
5. Um projeto por nome sanitizado (`sanitizar_nome_arquivo`); "já existe" → escolha outro nome
   ou delete explicitamente.
6. Nunca use nome genérico de personagem (`avatar`, `personagem`, `@me`…): HTTP 400 /
   anexo cancelado. A identidade oficial vem de `identidade.json`.
7. Storyboard: **sem fallback local silencioso** no fluxo automático — se o Claude falhar,
   o fluxo para e oferece o caminho manual.
8. Mídias nunca antes do Storyboard concluído (exceto modo local-timestamp).
9. `modo` da fila: `imagem` (default) · `animacao`/`animacao_apenas` (só cenas de vídeo,
   nunca avatar). Use `scene_ids` para lotes específicos.
10. Antes de render/export: `POST /montagem/<id>/sincronizar` (3 fontes) e, se for CapCut,
    `/api/v2/validar_capcut/<id>`.
11. Ordem canônica das cenas = `tempo_inicio`; duração de cena = `tempo_fim - tempo_inicio`
    (a duração da fala **não** deve ser alterada para "esticar" o CapCut).

**Segurança / higiene**
12. Não exponha `web_keys.json` nem a porta 5000 (API sem autenticação, §3.1).
13. Não crie parser/API paralela: reutilize `PipelineService`, HTTP v1/v2 e as funções de
    `services/` (reuso é regra do projeto).
14. Prefira comandos **idempotentes** (o sistema já pula cenas com mídia válida em disco).
15. Registre a ação com `log_event(categoria, mensagem, level, details)` (ou `/api/v2/log`)
    para aparecer no Console de Execução da UI.

---

## 9. Validações executadas nesta auditoria (evidências)

### 9.1 MCP — execução real

```text
# ❌ python mcp_server\server.py
ModuleNotFoundError: No module named 'mcp_server'

# ✅ python -m mcp_server.server   (com {"tool":"list_tools"})
{"tools":[{"name":"criar_projeto","description":"Cria um novo projeto de vídeo",
 "parameters":{"nome":{"type":"string","required":true}}}, ... 9 ferramentas ...]}

# ✅ {"tool":"system_info"}
{"result":{"os":"Windows","os_version":"10.0.22631","python_version":"3.11.9","machine":"AMD64",
 "processor":"AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD",
 "ffmpeg":"ffmpeg version 9.0-full_build-www.gyan.dev ...","h264_amf_available":true,
 "h264_nvenc_available":true,"libx264_available":true,"amf_supported":false}}

# ✅ erros
{"tool":"nao_existe"}                 → {"result":{"error":"Ferramenta 'nao_existe' não encontrada"}}
{"tool":"criar_projeto"}              → {"result":{"error":"Parâmetro obrigatório 'nome' não fornecido"}}
{"tool":"session_report","minutes":5} → {"result":{"success":true,"report":"Nenhum evento encontrado no período.","event_count":0}}

# ✅ execução de OUTRO diretório (C:\) só com PYTHONPATH
$env:PYTHONPATH='C:\Lira Videos'; python -m mcp_server.server  → respondeu normalmente
```

### 9.2 Configuração do Antigravity (lida do disco)

```json
// ~/.gemini/config/mcp_config.json
{ "mcpServers": {} }
```
```json
// ~/.gemini/config/config.json → userSettings.globalPermissionGrants.allow
["write_file(C:\\ultracut3)", "read_file(C:\\ultracut3\\FLOW)",
 "command(pip)", "command(python)", "command(pytest)"]
```
```json
// ~/.gemini/config/projects/3a8f38a1-….json
{"name":"Lira.Videos","projectResources":{"resources":[{"gitFolder":
 {"folderUri":"file:///c%3A/Lira%20Videos","defaultBranch":"main"}}]},
 "settings":{"fileAccessPolicy":"AGENT_SETTING_POLICY_ALLOW","sandboxMode":false,
 "autoExecutionPolicy":"CASCADE_COMMANDS_AUTO_EXECUTION_EAGER"}}
```
- Não existem `~/.gemini/GEMINI.md` nem `.agents/rules/` / `.agents/mcp_config.json` no workspace.
- O único arquivo de regras na raiz é `.clinerules` (105 KB, canal do Cline).

### 9.3 Estrutura de projeto (lida de `projetos/Teste 02`)

```text
lira_scene_plan.json → chaves: projeto, versao, narrativa_versao, gerado_em, total, cenas, visual_context
                       cenas = 92;  cena[1] tem 76 chaves (id, tempo_inicio, prompt_imagem, …)
meta.json            → modo_execucao=manual, studio_version=v2, steps.transcrever.status=concluido
midias_encontradas.json → lista; item[0] = {scene_id:1, quality:"green", media_type:"photo",
                                            origem_midia:"flow_automation", arquivo:"…\\cenas\\01_[00-00-00-02].png"}
identidade.json      → referencia_flow="@Marcos", flow_character_created=true
pastas: audio(1) cenas(120) conteudo(339) metadata(95) prompt_history(95) characters(1) srt(2)
```

### 9.4 Código: evidências de leitura

- `FLOW/` **não** é importado por nenhum módulo (0 ocorrências de `from FLOW`/`import FLOW`/`FLOW.capcut`).
- `app_web.py:1414` registra o blueprint v2; `app_web.py:1417` `require_auth` retorna sem validar.
- `services/pipeline_service.py:498` `buscar_midias()` é no-op com `success: True`.
- `config.py:203` `PIPELINE_STEPS`; `config.py:54/65` `VIDEO_ENCODER="h264_amf"` / `ENCODER_FALLBACK="libx264"`.
- `services/api_v2.py` expõe **95 rotas** (decorators `@api_v2_bp.route`); `app_web.py` expõe **75 rotas** (`@app.route`).

---

## 10. Referência rápida de configuração e arquivos

| Arquivo | Conteúdo / papel |
|---------|------------------|
| `config.py` | `BASE_DIR`, `PROJETOS_DIR`, `BIBLIOTECA_DIR`, `OUTPUT_DIR`, `ASSETS_CACHE_DIR`, `LOGS_DIR`; `FFMPEG_PATH`/`FFPROBE_PATH` (auto-detect); `VIDEO_ENCODER="h264_amf"`, `ENCODER_FALLBACK="libx264"`, `resolver_encoder()`; `WHISPER_*` (tiny/cpu/float32/2 threads/1 worker); `ANTHROPIC_MODEL="claude-sonnet-5"`; `PIPELINE_STEPS`; quotas `AVATAR_QUOTA=0.08` / `BROLL_QUOTA=0.92` / `BROLL_VIDEO_RATIO=0.60`; `NARRATIVE_CYCLE_ENABLED`, `CYCLE_TIPOS`, `CYCLE_DURACAO_*`; `FILENAME_PATTERN`, `PASTAS_MIDIA`, `VALID_EFEITOS`; `MAX_BIBLIOTECA_REUSE=2`, `UNSPLASH_RATE_LIMIT=45` |
| `config_local.py` | Chaves de API locais (não commitar) + `LLM_PROVIDER`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_API_KEY`; se ausente, `config.py` cria o template vazio |
| `web_keys.json` | Chaves da UI (`claude`, `deepseek`, `pexels`, `pixabay`, `unsplash`) — **mascaradas** nos endpoints (gitignored) |
| `web_config.json` | `pasta_midia_padrao` = `C:\Lira Videos\downloads`; `pasta_destino` = `C:\Lira Videos\output\entregue`; `pasta_capcut` = `…\CapCut\User Data\Projects\com.lveditor.draft` |
| `config/flow_accounts.json` | Contas Google Flow + `creditos_esgotados` (reset diário) |
| `config/capcut_library.json` / `capcut_subtitles.json` | Catálogos de transições/legendas do CapCut |
| `logs/events.jsonl` | Fila de eventos global consumida por `/api/eventos/<id>` e pelo Console da UI |
| `projetos/brand_profile.json` | Perfil global do canal (apresentador, poses, música, caption_style, `video_mix_ratio`) |
| `.clinerules` | Regras consolidadas do projeto (canal do Cline) + histórico de versões (v4.21 → v9.12) |
| `main.py` · `iniciar.bat` · `iniciar_web.bat` · `instalar.bat` | CLI / launchers (GUI tkinter e web) |

**Estilos visuais** e **modelos de produção** são strings livres validadas em runtime
(`estilo_visual` default `photorealistic_cinematic`; `prod_modelo_imagem`/`prod_modelo_video`,
`prod_qualidade_imagem`/`prod_qualidade_video`, `prod_proporcao`): consulte `GET /api/v2/presets/estilos`
e `GET /api/v2/projeto/<id>/config` antes de enviar valores novos.

---

## 11. Achados, riscos e próximos passos

| # | Achado | Impacto | Ação sugerida |
|---|--------|---------|---------------|
| A1 | `mcp_server/` fala um protocolo próprio, não MCP JSON-RPC | Nenhum cliente MCP consegue conectar | Rota B de §2.7 (SDK `mcp`) |
| A2 | MCP não registrado (`mcp_config.json` vazio; sem `.agents/mcp_config.json`) | As 9 ferramentas são invisíveis ao Antigravity | Registrar (§1.4) |
| A3 | Permissões do Antigravity apontam para `C:\ultracut3` (legado) | Escritas fora do workspace podem pedir aprovação | Atualizar grants para `C:\Lira Videos` |
| A4 | Sem `~/.gemini/GEMINI.md` nem `.agents/rules/` | Agente sem regras automáticas do projeto | Promover `.clinerules` → `.agents/rules/` |
| A5 | API HTTP **sem autenticação** em `127.0.0.1:5000` | Qualquer processo local pode disparar produção/render | Não expor a porta; manter bind local |
| A6 | `listar_projetos` (MCP) devolve `meta.json` inteiro | Consumo alto de contexto/tokens | Filtrar campos ou usar `step_status` |
| A7 | `FLOW/` é código órfão com `platform` hardcoded | Se usado, o CapCut abre-e-fecha | Marcar como legado / eventual remoção |
| A8 | `buscar_midias`/`gerar_queries` são no-ops | Comandos nessas etapas "passam" sem efeito | Saber que a produção real é o Google Flow |
| A9 | Falta de `reason` nos 404s fora de `/cena_media` | Diagnóstico mais difícil no frontend | Padronizar `reason` (a v9.12 fez isso no v2) |

---

### Fontes consultadas

Código: `app_web.py`, `config.py`, `main.py`, `capcut_draft.py`, `capcut_draft_imagens.py`,
`mcp_server/*.py`, `services/pipeline_service.py`, `services/api_v2.py`, `services/event_logger.py`,
`services/scene_plan_service.py`, `services/playwright_flow.py`, `FLOW/*.py`, `web_config.json`, `.clinerules`.
Documentação: `README.md`, `docs/MEMORIA_TECNICA_ATUALIZADA.md`, docs oficiais do Google Antigravity
(MCP e Rules). Dados: `projetos/Teste 02` (lira_scene_plan.json, meta.json, midias_encontradas.json, identidade.json).
Configuração local: `~/.gemini/config/{config.json,mcp_config.json,projects/*.json}`.

