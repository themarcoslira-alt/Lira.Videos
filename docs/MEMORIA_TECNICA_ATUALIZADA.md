# Lira Studio (Lira Videos) — Memória Técnica Consolidada
**Data de Atualização:** 10/09/2026  
**Ambiente:** Windows (`C:\Lira Videos`), Python 3.10+, Playwright CDP (porta 9222), Flask (`app_web.py` + `services/api_v2.py`)  
**Repositório Git:** Último commit base: `5d7a089` (`feat(lira): cena ativa em destaque, avatar nunca video, failover multi-conta e refatoracoes web/S2`)

---

## 1. Resumo das Últimas 5 Mudanças Implementadas no Código

### 1️⃣ Failover de Contas do Google Flow (Multi-Conta)
- **Arquivo:** `services/playwright_flow.py` (linhas 1215–1345 e 2990–3055)
- **O que mudou:**
  - **C1:** `_detectar_erro_ou_limite_modelo()` — lista `frases_credito` expandida com 13 variações em PT/EN. Ao detectar no modo imagem, chama `_rotacionar_conta()` e retorna `"credito_esgotado"`.
  - **C2:** Em `_processar_cena_individual()`, delay inicial pós-envio subiu para **4000ms**, com loop de **até 3 tentativas com 2000ms** entre elas.
  - **C3:** `_rotacionar_conta()` — atualiza `config/flow_accounts.json` (`creditos_esgotados: true` na atual, ativa a próxima), reinicia o Chrome CDP (`ensure_chrome_cdp(force_restart=True)`), reconecta a sessão Playwright (`_iniciar_sessao_thread()`), navega à URL salva do projeto e aguarda networkidle.
  - **C4:** No loop da fila (`executar_fila_playwright`), o retorno `credito_esgotado_recolocado` **não marca erro** (`STATUS_ERRO`). A cena é recolocada no topo da fila (`cenas_a_processar.insert(0, cena)`) com status `PENDENTE` e a fila continua com a nova conta.

### 2️⃣ Wait Fixo no Anexo de Referência Local
- **Arquivo:** `services/playwright_flow.py`
- **O que mudou:**
  - Em `_anexar_referencia_imagem_local()`, após o upload e polling na galeria do Flow (inclusive no timeout de 15s), inserido `self.page.wait_for_timeout(2000)` antes de `_selecionar_referencia_flow()`, evitando cliques antes do elemento estar interativo.

### 3️⃣ Bloqueio de Abas Inesperadas (`flowmusic.app` e similares)
- **Arquivo:** `services/playwright_flow.py`
- **O que mudou:**
  - **C1:** Novo helper `_eh_aba_flow_valida(url)` aceita estritamente `flow.google.com` e `labs.google`, rejeitando `flowmusic.app`, `accounts.google`, `127.0.0.1`, `localhost`.
  - **C2:** Polling da geração verifica a cada 5 iterações se a URL da aba é válida; se foi redirecionada, restaura via `carregar_projeto_flow_url()`.
  - **C3:** Abas com `flowmusic` são fechadas automaticamente (`p.close()`).

### 4️⃣ Visibilidade da Cena Ativa na UI
- **Arquivos:** `static/app.js`, `static/style.css`, `static/index.html`
- **O que mudou:**
  - **C1:** Barra de atividade (`pollFlowStatus`) aceita `etapa` sem exigir `mensagem` e exibe ícone de pulso ⚡.
  - **C2:** Destaque dinâmico com classe CSS `.cena-ativa-gerando` aplicado no card da cena em tempo real.
  - **C3:** Live Terminal HUD exibe `⚡ ${ca.etapa}` e tempo decorrido/médio.
  - **C4:** CSS `.cena-ativa-gerando` com borda roxa, glow e animação de pulso.
  - **C5:** O HUD expande automaticamente durante a geração ativa.

### 5️⃣ Configurações Separadas para Imagem e Vídeo + Fallback Vídeo → Imagem (Mais Recente)
- **Arquivos:** `static/index.html`, `static/app.js`, `services/api_v2.py`, `services/playwright_flow.py`
- **O que mudou:**
  - **HTML (`index.html:428-481`):** Bloco de produção dividido em **Sub-card Imagem** (`#s2-prod-modelo-imagem`, `#s2-prod-qualidade-download`, `#s2-prod-qualidade-imagem`, `#s2-prod-proporcao`) e **Sub-card Vídeo** (`#s2-prod-modelo-video`, `#s2-prod-qualidade-video`). Radio "Tipo de Saída" foi removido; botão único de salvar mantido.
  - **JS (`app.js`):** `carregarStudio2Dados()` carrega ambos os blocos; botão de salvar envia `prod_modelo_imagem`, `prod_modelo_video`, `prod_qualidade_imagem`, `prod_qualidade_video` e mantém legados sincronizados.
  - **API (`api_v2.py:278`):** `v2_projeto_config()` atualizado para aceitar e persistir os 4 novos campos em `projetos/<id>/meta.json`.
  - **Worker (`playwright_flow.py`):**
    - Pré-configuração da fila: se for animação, lê configs de vídeo; se for imagem, lê configs de imagem.
    - Guarda `self._cfg_imagem_projeto`.
    - **Fallback de Vídeo:** Ao detectar limite de crédito em modo vídeo (`video_mode=True`), **NÃO troca de conta**. Ativa `self._fallback_video_para_imagem = True`, reconfigura o Flow para `image` (modelo Nano Banana 2), recoloca a cena no topo da fila e força todas as cenas subsequentes para imagem.

---

## 2. Mapa de Arquivos e Funções Críticas

| Componente | Arquivo Principal | Funções Chave | Responsabilidade |
| :--- | :--- | :--- | :--- |
| **Fila & Flow** | `services/playwright_flow.py` | `executar_fila_playwright()`, `_set_output_mode()`, `_detectar_erro_ou_limite_modelo()`, `_rotacionar_conta()`, `_processar_cena_individual()` | Automação Playwright CDP, rotação de contas, reconexão de abas, envio de prompts. |
| **API Backend** | `services/api_v2.py` | `v2_projeto_config()`, `v2_producao_live_console()`, `_get_meta()`, `_save_meta()` | Leitura/escrita de `meta.json`, telemetria do worker em tempo real para o HUD. |
| **Contas Web** | `app_web.py` | `flow_contas()`, `flow_contas_ativar()`, `flow_contas_login_guiado()`, `flow_contas_adicionar()`, `flow_contas_remover()` | CRUD de `config/flow_accounts.json` e login guiado com extração de email do avatar. |
| **Frontend UI** | `static/app.js` | `initStudio2()`, `carregarStudio2Dados()`, `pollLiveTerminalHUD()`, `pollFlowStatus()`, `carregarContasFlow()` | Interface reativa, polling de terminal, renderização de cards e destaques. |
| **Layout UI** | `static/index.html` | `#card-contas-flow` (L551), `#live-terminal-hud` (L1363), `#s2-prod-config` (L428) | Estrutura HTML do Studio 2.0. |

---

## 3. Estado Atual do Repositório e Alertas

1. **Modificações em Working Tree (não commitadas):**
   - `config/flow_accounts.json`
   - `services/api_v2.py`
   - `services/playwright_flow.py`
   - `services/production_metrics_engine.py`
   - `static/app.js`
   - `static/index.html`
   - `services/flow_account_manager.py` **(novo — seção 7.1)**
   - `tests/test_rotacao_conta_reset.py` **(novo — seção 7.1)**
2. **Push Remoto Pendente:**
   - O commit `5d7a089` está gravado no branch local `master`, mas o `git push` falhou com HTTP 403 (token/credencial do GitHub necessita reautenticação se for sincronizar remotamente).
3. **Validação de Sintaxe JS:**
   - Validar com `node --check static/app.js` (passou limpo). Evitar linters com expressões regulares ingênuas que quebram em regex literais com barras escapadas.

---

## 7.1 SISTEMA DE CONTAS GOOGLE FLOW (implementado 10/09/2026)

**Status:** COMPLETO E VALIDADO EM PRODUÇÃO

**Componentes:**
- `services/flow_account_manager.py`: reset diário, rotação inteligente
- UI: botão "🔄 Conta" no header + menu dropdown
- APIs: `GET /api/v2/flow/contas`, `POST /api/v2/flow/trocar/<email>`
- Auto-reset: `creditos_esgotados` reseta diariamente
- Auto-rotação: `daily_limit` → tenta próxima conta **ANTES** de fallback
- Zero 2FA: contas já logadas no Chrome

**Coexiste com sistema anterior (`app_web.py` v1) — sem conflito.**
