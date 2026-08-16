/* ============================================================
   ULTRACUT3 WEB v1.0 — app.js (vanilla JS, sem framework)
   Dashboard dark — pipeline em tempo real + cenas + log
   ============================================================ */

"use strict";

const S = {
  projeto_id: null,
  modo: "automatico",
  since: 0,
  pollTimer: null,
  mediaType: "photo",
  destinoPadrao: "",
  pastaMidiaPadrao: "",
  pastaCapcut: "",
  videoPronto: false,
  arquivoAudio: null,
  audioFile: null,
  _transcricaoCard2Carregada: false,
  // cenas: Map<scene_id, {idx,total,pct,status,texto,query}>
  cenas: new Map(),
  cenaTotal: 0,
  // thumbnails: Map<scene_id, setInterval> para polling de /api/cena/.../thumbnail
  cenaThumbs: {},
  // etapas: 0 transcrever, 1 cenas, 2 storyboard, 3 midias, 4 render
  etapasStatus: { 0: "wait", 1: "wait", 2: "wait", 3: "wait", 4: "wait" },
  etapasMsg: { 0: "", 1: "", 2: "", 3: "", 4: "" },
  etapaAtual: -1,
};

const ETAPAS = [
  { step: 0, id: "transcrever", label: "Transcrever" },
  { step: 1, id: "cenas", label: "Cenas" },
  { step: 2, id: "storyboard", label: "Storyboard" },
  { step: 3, id: "midias", label: "Mídias" },
  { step: 4, id: "render", label: "Render" },
];

const $ = (id) => document.getElementById(id);

/* ---------- API helpers ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  // Expor status HTTP para diagnóstico (usado no fluxo de criar projeto)
  data.http_status = res.status;
  // Qualquer 2xx é sucesso; preserva o success explícito vindo do backend quando houver
  if (res.ok && data.success === undefined) data.success = true;
  if (!res.ok && data.success === undefined) data.success = false;
  return data;
}
function apiJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
function apiForm(path, formData) {
  return api(path, { method: "POST", body: formData });
}

/* ---------- Helpers ---------- */
function showMsg(el, texto, tipo = "info") {
  el.textContent = texto;
  el.className = "msg " + tipo;
  el.classList.remove("hidden");
}
function hideMsg(el) {
  el.className = "msg hidden";
  el.textContent = "";
}
function fmtBytes(n) {
  if (!n) return "0 KB";
  if (n > 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  if (n > 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + " MB";
  return (n / 1024).toFixed(0) + " KB";
}
function fmtDur(s) {
  s = Math.round(Number(s) || 0);
  const m = Math.floor(s / 60), ss = s % 60;
  return `${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}
function hhmm(ts) {
  const m = String(ts).match(/(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : "";
}
function setBarraProgresso(bar, pct) {
  if (!bar) return;
  const span = bar.querySelector("span");
  if (span) span.style.width = Math.min(100, Math.max(0, pct)) + "%";
}
function setBarraIndeterminada(bar) {
  if (!bar) return;
  const span = bar.querySelector("span");
  if (span) span.style.width = "70%";
}
function esc(html) {
  const d = document.createElement("div");
  d.textContent = String(html == null ? "" : html);
  return d.innerHTML;
}
function setProgressoPct(pct, indet, eta) {
  const wrap = $("progress-wrap");
  const fill = $("progress-fill");
  const label = $("progress-pct");
  const etaEl = $("progress-eta");
  if (fill) fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  if (label) label.textContent = Math.round(pct) + "%";
  if (etaEl) etaEl.textContent = eta || "";
  if (wrap) wrap.classList.toggle("indet", !!indet);
}
function atualizarTopbar(projeto, status, etapa) {
  const nome = $("topbar-nome");
  const badge = $("topbar-status");
  const exibicao = S.projetoNome || projeto;
  if (nome) {
    nome.textContent = exibicao || "Nenhum projeto ativo";
    nome.classList.toggle("dim", !exibicao);
    // ITEM 2: nome do projeto no header vira link de volta ao dashboard
    if (exibicao && S.projeto_id) {
      nome.setAttribute("href", "/projeto/" + encodeURIComponent(S.projeto_id));
      nome.classList.add("link");
    } else {
      nome.removeAttribute("href");
      nome.classList.remove("link");
    }
  }
  if (!badge) return;
  const mapa = {
    andamento: ["badge-proc", etapa || "Processando…"],
    concluido: ["badge-ok", "Concluído"],
    erro: ["badge-err", "Erro"],
    pausado_manual: ["badge-warn", "Ação manual"],
  };
  const [cls, txt] = mapa[status] || ["badge-wait", "—"];
  badge.className = "badge " + cls;
  badge.textContent = txt;
  badge.classList.remove("hidden");
}

/* ---------- Navegação entre telas ---------- */
function mostrarTela(id) {
  document.querySelectorAll(".tela").forEach((t) => t.classList.remove("ativa"));
  const tela = $(id);
  if (tela) tela.classList.add("ativa");
}

function abrirHome() {
  pararPolling();
  S.projeto_id = null;
  S.projetoId = null;
  S.projetoNome = null;
  S.videoPronto = false;
  S.cenas.clear();
  S.cenaTotal = 0;
  S.etapasStatus = { 0: "wait", 1: "wait", 2: "wait", 3: "wait", 4: "wait" };
  S.etapasMsg = { 0: "", 1: "", 2: "", 3: "", 4: "" };
  S.etapaAtual = -1;
  atualizarUrlProjeto(null);
  mostrarTela("tela-inicio");
  atualizarTopbar(null, null, null);
  setNavAtivo("dashboard");
  if ($("scenes-grid")) $("scenes-grid").innerHTML = '<div class="scenes-empty">As cenas aparecerão aqui conforme o pipeline avança.</div>';
  if ($("log-area")) $("log-area").innerHTML = "";
}

function abrirFluxo() {
  atualizarUrlProjeto(S.projeto_id);
  if (S.modo === "automatico") {
    mostrarTela("tela-auto");
    setNavAtivo("dashboard");
    renderEtapas();
    atualizarTopbar(S.projeto_id, "andamento", "Em progresso");
  } else {
    mostrarTela("tela-manual");
    setNavAtivo("projetos");
    $("manual-projeto-nome").textContent = S.projetoNome || S.projeto_id || "";
    atualizarTopbar(S.projeto_id, "andamento", "Em progresso");
    iniciarManual();
  }
  iniciarPolling();
}

function setNavAtivo(qual) {
  document.querySelectorAll(".nav-link").forEach((n) => {
    n.classList.toggle("active", n.dataset.nav === qual);
  });
}

/* ---------- Lista de projetos (sidebar "Projetos") ---------- */
function telaProjetos() {
  let tela = $("tela-projetos");
  if (tela) return tela;
  tela = document.createElement("section");
  tela.id = "tela-projetos";
  tela.className = "tela";
  tela.innerHTML =
    '<div style="width:100%;max-width:960px;margin:0 auto">' +
      '<div class="scenes-header" style="margin-bottom:18px">' +
        '<h3 style="text-transform:none;letter-spacing:0;color:var(--text);font-size:17px">Projetos</h3>' +
        '<span id="projetos-count" class="badge badge-muted">0</span>' +
      '</div>' +
      '<div id="projetos-grid" class="scenes-grid" ' +
        'style="grid-template-columns:repeat(auto-fill,minmax(250px,1fr));align-content:start"></div>' +
    '</div>';
  document.querySelector("#main").appendChild(tela);
  return tela;
}

async function abrirListaProjetos() {
  pararPolling();
  telaProjetos(); // garante que a seção existe antes de exibi-la
  mostrarTela("tela-projetos");
  const grid = $("projetos-grid");
  grid.innerHTML = '<div class="scenes-empty">Carregando projetos…</div>';
  let r;
  try {
    r = await api("/api/projetos");
  } catch (e) {
    grid.innerHTML = '<div class="scenes-empty">Erro ao carregar projetos: ' + esc(e.message) + '</div>';
    return;
  }
  const lista = (r && r.projetos) || [];
  $("projetos-count").textContent = lista.length;
  if (!lista.length) {
    grid.innerHTML = '<div class="scenes-empty">Nenhum projeto ainda. Crie o primeiro na tela inicial.</div>';
    return;
  }
  grid.innerHTML = "";
  const badgeModo = { automatico: "badge-proc", manual: "badge-warn" };
  const badgeStatus = {
    pronto: "badge-ok", transcrito: "badge-ok", cenas: "badge-ok",
    storyboard: "badge-ok", midias: "badge-proc", transcrevendo: "badge-proc",
    criado: "badge-wait",
  };
  lista.forEach((proj) => {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.id = proj.id;
    const criado = proj.criado_em ? String(proj.criado_em).replace("T", " ").slice(0, 16) : "—";
    card.innerHTML =
      '<div class="card-body">' +
        '<b style="font-size:14px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:8px">' + esc(proj.nome) + '</b>' +
        '<div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap">' +
          '<span class="badge ' + (badgeModo[proj.modo] || "badge-muted") + '">' + esc(proj.modo) + '</span>' +
          '<span class="badge ' + (badgeStatus[proj.status] || "badge-muted") + '">' + esc(proj.status) + '</span>' +
        '</div>' +
        '<div class="mono muted" style="font-size:11px;margin-bottom:12px">criado ' + esc(criado) + '</div>' +
        '<div class="btn-row" style="margin-top:2px">' +
          '<button class="btn btn-sm" type="button" data-acao="abrir">Abrir</button>' +
          '<button class="btn btn-ghost btn-sm" type="button" data-acao="excluir" title="Excluir projeto">🗑</button>' +
        '</div>' +
      '</div>';
    card.querySelector('[data-acao="abrir"]').addEventListener("click", () => abrirProjetoExistente(proj.id, proj.modo));
    card.querySelector('[data-acao="excluir"]').addEventListener("click", async () => {
      if (!window.confirm(`Excluir o projeto "${proj.nome}"? Esta ação não pode ser desfeita.`)) return;
      const r = await apiJson(`/api/deletar_projeto/${encodeURIComponent(proj.id)}`, {});
      if (r.success) {
        if (S.projeto_id === proj.id) abrirHome();
        else abrirListaProjetos(); // recarrega a lista
      } else {
        window.alert(r.error || "Falha ao excluir o projeto.");
      }
    });
    grid.appendChild(card);
  });
}

async function abrirProjetoExistente(pid, modo) {
  pararPolling();
  S.projeto_id = pid;
  S.projetoId = pid;
  S.projetoNome = pid; // o topbar/status ajustam a exibição
  S.modo = modo === "manual" ? "manual" : "automatico";
  S.since = 0;
  S.videoPronto = false;
  S.cenas.clear();
  S.cenaTotal = 0;
  S.etapasStatus = { 0: "wait", 1: "wait", 2: "wait", 3: "wait", 4: "wait" };
  S.etapasMsg = { 0: "", 1: "", 2: "", 3: "", 4: "" };
  S.etapaAtual = -1;
  abrirFluxo();
}

/* ---------- Tela inicial: modo + criar (AJUSTE 2: sem upload de áudio) ---------- */
function bindHome() {
  // AJUSTE 2: sem upload de áudio na criação — o áudio é anexado dentro do fluxo.

  // Seleção de modo
  document.querySelectorAll(".mode-card").forEach((mc) => {
    mc.addEventListener("click", () => {
      document.querySelectorAll(".mode-card").forEach((x) => x.classList.remove("active"));
      mc.classList.add("active");
      S.modo = mc.dataset.mode === "manual" ? "manual" : "automatico";
    });
  });

  $("btn-criar").addEventListener("click", criarProjeto);

  // Nav links
  document.querySelectorAll(".nav-link").forEach((nl) => {
    nl.addEventListener("click", () => {
      setNavAtivo(nl.dataset.nav);
      if (nl.dataset.nav === "config") abrirConfig();
      else if (nl.dataset.nav === "projetos") abrirListaProjetos();
      else if (!S.projeto_id) abrirHome();
    });
  });
  $("btn-voltar-home").addEventListener("click", abrirHome);
  $("btn-abrir-config").addEventListener("click", abrirConfig);
}

async function criarProjeto() {
  const nome = $("nome-projeto").value.trim();
  hideMsg($("criar-erro"));

  // AJUSTE 2: a criação pede apenas nome + modo. O áudio é anexado DENTRO do fluxo.
  if (!nome) { showMsg($("criar-erro"), "Digite o nome do projeto.", "erro"); return; }

  $("btn-criar").disabled = true;
  $("btn-criar").textContent = "Criando projeto…";

  const fd = new FormData();
  fd.append("nome", nome);
  fd.append("modo", S.modo);

  try {
    const r = await apiForm("/api/criar_projeto", fd);
    // O backend retorna 201 com {projeto_id, status} (sem campo "success").
    // Aceitamos sucesso quando projeto_id vier presente OU success for verdadeiro.
    if (!r.projeto_id && !r.success) {
      const detalhe = "Falha (" + (r.http_status || "?") + "): " + (r.error || JSON.stringify(r));
      console.error(detalhe, r);
      showMsg($("criar-erro"), detalhe, "erro");
      $("btn-criar").disabled = false;
      $("btn-criar").textContent = "Criar projeto e começar";
      return;
    }
    S.projeto_id = r.projeto_id;
    S.projetoId = r.projeto_id;
    S.projetoNome = nome;
    S.since = 0;
    S.videoPronto = false;
    S.cenas.clear();
    S.cenaTotal = 0;
    S.etapasStatus = { 0: "wait", 1: "wait", 2: "wait", 3: "wait", 4: "wait" };
    S.etapasMsg = { 0: "", 1: "", 2: "", 3: "", 4: "" };
    S.etapaAtual = 0;
    abrirFluxo();
  } catch (e) {
    const detalhe = "Erro de rede: " + e.message;
    console.error(detalhe, e);
    showMsg($("criar-erro"), detalhe, "erro");
  } finally {
    $("btn-criar").disabled = false;
    $("btn-criar").textContent = "Criar projeto e começar";
  }
}

/* ---------- Configurações ---------- */
async function abrirConfig() {
  $("config-modal").classList.remove("hidden");
  const cfg = await api("/api/config");
  $("cfg-pasta-midia").value = cfg.pasta_midia_padrao || "";
  $("cfg-pasta-destino").value = cfg.pasta_destino || "";
  // AJUSTE 1: placeholders com as chaves mascaradas (•••• + últimos 4).
  $("cfg-claude-key").value = "";
  $("cfg-claude-key").placeholder = (cfg.has_claude_key ? cfg.claude_key_mascarada + " — " : "") + "deixe em branco para manter";
  $("cfg-pexels-key").value = "";
  $("cfg-pexels-key").placeholder = (cfg.has_pexels_key ? cfg.pexels_key_mascarada + " — " : "") + "deixe em branco para manter";
  $("cfg-pixabay-key").value = "";
  $("cfg-pixabay-key").placeholder = (cfg.has_pixabay_key ? cfg.pixabay_key_mascarada + " — " : "") + "deixe em branco para manter";
  $("cfg-unsplash-key").value = "";
  $("cfg-unsplash-key").placeholder = (cfg.has_unsplash_key ? cfg.unsplash_key_mascarada + " — " : "") + "deixe em branco para manter";
  hideMsg($("config-msg"));
}
function fecharConfig() {
  $("config-modal").classList.add("hidden");
}
function bindConfig() {
  $("btn-config-fechar").addEventListener("click", fecharConfig);
  $("btn-config-salvar").addEventListener("click", async () => {
    const body = {
      pasta_midia_padrao: $("cfg-pasta-midia").value.trim(),
      pasta_destino: $("cfg-pasta-destino").value.trim(),
    };
    // AJUSTE 1: envia SOMENTE chaves realmente digitadas (vazio = manter atual).
    const chaves = {
      claude_api_key: $("cfg-claude-key").value.trim(),
      pexels_api_key: $("cfg-pexels-key").value.trim(),
      pixabay_api_key: $("cfg-pixabay-key").value.trim(),
      unsplash_api_key: $("cfg-unsplash-key").value.trim(),
    };
    Object.entries(chaves).forEach(([k, v]) => { if (v) body[k] = v; });
    const r = await apiJson("/api/config", body);
    if (r.success) {
      showMsg($("config-msg"), "Configurações salvas (pastas e chaves de API).", "ok");
      setTimeout(fecharConfig, 800);
    } else {
      showMsg($("config-msg"), r.error || "Falha ao salvar.", "erro");
    }
  });
  $("config-modal").addEventListener("click", (e) => {
    if (e.target === $("config-modal")) fecharConfig();
  });
}

/* ---------- Polling (1000ms) ---------- */
function iniciarPolling() {
  pararPolling();
  S.pollTimer = setInterval(pollTudo, 1000);
  pollTudo();
}
function pararPolling() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
}

async function pollTudo() {
  if (!S.projeto_id) return;
  try {
    const [evt, status] = await Promise.all([
      api(`/api/eventos/${encodeURIComponent(S.projeto_id)}?since=${S.since}`),
      api(`/api/status/${encodeURIComponent(S.projeto_id)}`),
    ]);
    if (evt.eventos && evt.eventos.length) {
      S.since = evt.since;
      aplicarEventos(evt.eventos);
    }
    aplicarStatus(status);
    // ITEM 6/7: atualiza os cards com prompt/animação/tipo quando disponível
    if (S.modo === "automatico") {
      api(`/api/cenas/${encodeURIComponent(S.projeto_id)}`).then((r) => {
        if (r.success && r.cenas) aplicarCenasDetalhadas(r.cenas);
      }).catch(() => {});
    }
  } catch (e) { /* rede — ignora */ }
}

/* ---------- Etapas (pipeline) ---------- */
function renderEtapas() {
  const cont = $("pipeline-steps");
  if (!cont) return;
  cont.innerHTML = "";
  ETAPAS.forEach((ep) => {
    const st = S.etapasStatus[ep.step] || "wait";
    const div = document.createElement("div");
    div.className = "step " + (st === "done" ? "done" : st === "active" ? "active" : st === "error" ? "error" : "");
    div.dataset.step = ep.step;

    const ico = document.createElement("span");
    ico.className = "step-icon";
    if (st === "done") {
      ico.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    } else if (st === "active") {
      ico.innerHTML = '<span class="step-spin"></span>';
    } else if (st === "error") {
      ico.textContent = "!";
    }

    const body = document.createElement("div");
    body.className = "step-body";
    const lbl = document.createElement("span");
    lbl.className = "step-label";
    lbl.textContent = ep.label;
    const msg = document.createElement("span");
    msg.className = "step-msg";
    msg.id = "evt-" + ep.id;
    msg.textContent = S.etapasMsg[ep.step] || "Aguardando…";
    body.appendChild(lbl);
    body.appendChild(msg);

    div.appendChild(ico);
    div.appendChild(body);

    // ITEM 3: ações por etapa — ↺ reprocessar (concluída) / ▶ avançar (ativa/travada)
    const acoes = document.createElement("div");
    acoes.className = "step-acoes";
    if (st === "done") {
      const btnRep = document.createElement("button");
      btnRep.type = "button";
      btnRep.className = "step-btn reproc";
      btnRep.title = "Reprocessar esta etapa";
      btnRep.textContent = "↺";
      btnRep.addEventListener("click", (e) => {
        e.stopPropagation();
        reprocessarEtapa(ep.id);
      });
      acoes.appendChild(btnRep);
    } else if (st === "active") {
      const btnAv = document.createElement("button");
      btnAv.type = "button";
      btnAv.className = "step-btn avanc";
      btnAv.title = "Avançar manualmente (forçar como concluída)";
      btnAv.textContent = "▶";
      btnAv.addEventListener("click", (e) => {
        e.stopPropagation();
        avancarEtapa(ep.id);
      });
      acoes.appendChild(btnAv);
    }
    div.appendChild(acoes);

    cont.appendChild(div);
  });
}

const CATEGORIA_PARA_STEP = {
  TRANSCRIBE: 0, CHECKPOINT: 0, SCENES: 1, STORYBOARD: 2, CLAUDE: 2,
  MEDIA_FETCH: 3, RENDER: 4,
};

function setEtapaStatus(step, status) {
  if (step < 0 || step > 4) return;
  if (S.etapasStatus[step] === "done") return; // não regride
  if (status === "concluido") S.etapasStatus[step] = "done";
  else if (status === "erro") S.etapasStatus[step] = "error";
  else if (status === "andamento") {
    S.etapasStatus[step] = "active";
    S.etapaAtual = step;
  }
}

function aplicarEventos(eventos) {
  let pctTranscricao = null;
  let pctRender = null;
  let pctCenas = null;

  for (const evt of eventos) {
    const cat = (evt.category || "").toUpperCase();
    const msg = (evt.message || "").trim();
    if (!msg) continue;

    // Determina etapa do evento
    let step = null;
    if (evt.details && evt.details.step !== undefined && evt.details.step !== null) {
      step = evt.details.step;
    } else {
      step = CATEGORIA_PARA_STEP[cat] !== undefined ? CATEGORIA_PARA_STEP[cat] : null;
    }
    if (step !== null) {
      const status = (evt.details && evt.details.status) || "andamento";
      if (evt.level === "ERROR" || status === "erro") setEtapaStatus(step, "erro");
      else if (status === "concluido") setEtapaStatus(step, "concluido");
      else setEtapaStatus(step, "andamento");

      const el = $("evt-" + (ETAPAS[step] ? ETAPAS[step].id : ""));
      if (el) el.textContent = msg;
      if (step >= 0 && step <= 4) S.etapasMsg[step] = msg;
    }

    // Percentuais
    const mTrans = msg.match(/(\d+(?:\.\d+)?)\s*%\s*do audio transcrito/);
    if (mTrans) pctTranscricao = parseFloat(mTrans[1]);
    const mRender = msg.match(/Renderizando\.\.\.\s*(\d+)%/);
    if (mRender) pctRender = parseInt(mRender[1], 10);
    const mVideo = msg.match(/Busca de vídeos: Cena (\d+)\/(\d+) \((\d+)%\)/);
    if (mVideo && S.modo === "manual") {
      // AJUSTE 3: progresso do Card 3 (Buscar Vídeos)
      const pct = parseInt(mVideo[3], 10);
      $("card3-progress-wrap").classList.remove("hidden");
      setBarraProgresso($("card3-progress"), pct);
      $("card3-progress-msg").textContent = `Buscando vídeos… ${pct}%`;
    }
    // Nota: o regex de cenas do pipeline foi ancorado em "Progresso:" para não
    // colidir com o progresso do Card 3 acima.
    const mCena = msg.match(/Progresso: Cena (\d+)\/(\d+) \((\d+)%\)/);
    if (mCena) {
      pctCenas = parseInt(mCena[3], 10);
      processarEventoCena(parseInt(mCena[1], 10), parseInt(mCena[2], 10), parseInt(mCena[3], 10), msg);
    }

    adicionarLog(evt, msg);
  }

  // Atualiza barra de progresso (prioridade: transcrever -> render -> cenas)
  if (pctTranscricao !== null) {
    setProgressoPct(pctTranscricao, false, "Transcrevendo…");
    if (S.modo === "manual") {
      setBarraProgresso($("card1-progress"), pctTranscricao);
      const mMsg = $("card1-progress-msg");
      if (mMsg) mMsg.textContent = `Transcrevendo… ${Math.round(pctTranscricao)}%`;
    }
  } else if (pctRender !== null) {
    setProgressoPct(pctRender, false, "Renderizando…");
  } else if (pctCenas !== null) {
    setProgressoPct(pctCenas, false, "Buscando mídia…");
  } else if (S.etapasStatus[2] === "active") {
    setProgressoPct(0, true, "Planejando cenas…");
  }
}

function processarEventoCena(idx, total, pct, msg) {
  if (!S.cenas.has(idx)) S.cenas.set(idx, { idx, total, pct, status: "proc", texto: "", query: "" });
  const cena = S.cenas.get(idx);
  cena.total = total;
  cena.pct = pct;
  S.cenaTotal = total;

  if (/GREEN obtida!|JA RESOLVIDA|GREEN aceita|qualidade=green|resultado parcial/.test(msg)) {
    if (cena.status !== "ok") cena.status = "ok";
  } else if (/falhou|needs_media|PENDENTE/.test(msg)) {
    cena.status = "err";
  }

  const mTexto = msg.match(/texto="([^"]*)"/);
  if (mTexto) cena.texto = mTexto[1];
  const mQuery = msg.match(/query="([^"]*)"/);
  if (mQuery) cena.query = mQuery[1];

  renderCena(cena);
  renderScenesStats();
}

function renderCena(cena) {
  const grid = $("scenes-grid");
  if (!grid) return;
  let card = grid.querySelector(`[data-cena="${cena.idx}"]`);
  if (!card) {
    card = document.createElement("div");
    card.className = "scene-card";
    card.dataset.cena = cena.idx;
    card.innerHTML =
      '<div class="scene-thumb">' +
        '<span class="scene-idx"></span>' +
        '<span class="scene-badge"></span>' +
        '<span class="thumb-fallback">🎬</span>' +
        '<img class="scene-img" alt="cena ' + cena.idx + '" loading="lazy">' +
      '</div>' +
      '<div class="scene-body">' +
        '<div class="scene-text"></div>' +
        '<div class="scene-meta"></div>' +
        '<div class="scene-query"></div>' +
        '<div class="scene-dur"></div>' +
        '<div class="scene-copy-btns">' +
          '<button class="btn btn-ghost btn-sm" data-copiar="nome" type="button">Nome</button>' +
          '<button class="btn btn-ghost btn-sm" data-copiar="prompt" type="button">Prompt</button>' +
          '<button class="btn btn-ghost btn-sm" data-copiar="animacao" type="button">Animação</button>' +
        '</div>' +
      '</div>';
    grid.appendChild(card);
    const empty = grid.querySelector(".scenes-empty");
    if (empty) empty.remove();
    // ITEM 5: busca a thumbnail assim que a mídia estiver em disco (polling 2s)
    iniciarThumbCena(cena.idx, card.querySelector(".scene-img"));
  }

  // Badge: status + tipo (vídeo vs image_prompt)
  const tipoTxt = cena.tipo === "video" ? "🎬 vídeo" : "🖼 image_prompt";
  const badge = (cena.temMidia || cena.status === "ok")
    ? ["badge-ok", "✓ " + tipoTxt]
    : (cena.status === "err" ? ["badge-err", "✗ sem mídia"] : ["badge-proc", "· pendente"]);
  card.querySelector(".scene-idx").textContent = cena.nome || ("Cena " + cena.idx);
  card.querySelector(".scene-badge").className = "scene-badge badge " + badge[0];
  card.querySelector(".scene-badge").textContent = badge[1];
  card.querySelector(".scene-text").textContent = cena.texto || "Aguardando transcrição da cena…";
  card.querySelector(".scene-meta").textContent = (cena.origem ? cena.origem + " · " : "") + (cena.tipo || "?");
  card.querySelector(".scene-query").textContent = cena.query ? ("🔎 " + cena.query) : "query: —";
  const dur = cena.duracao ? fmtDur(cena.duracao) : "—";
  card.querySelector(".scene-dur").textContent =
    cena.total ? `cena ${cena.idx} de ${cena.total} · ${dur}` : (dur !== "—" ? "duração " + dur : "—");

  // ITEM 6/7: botões de copiar (nome, prompt de imagem, animação)
  const dados = {
    nome: cena.nome || ("Cena " + cena.idx),
    prompt: cena.image_prompt || "",
    animacao: cena.animacao || "",
  };
  card.querySelectorAll("[data-copiar]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const chave = btn.dataset.copiar;
      const alvo = $("auto-erro") || $("card2-msg");
      if (!dados[chave]) { showMsg(alvo, "Sem dados para copiar.", "erro"); return; }
      const ok = await copiarTexto(dados[chave]);
      if (ok) { showMsg(alvo, "Copiado: " + chave, "ok"); setTimeout(() => hideMsg(alvo), 1500); }
      else showMsg(alvo, "Não foi possível copiar.", "erro");
    };
  });
  $("scene-count").textContent = S.cenas.size;
}

function renderScenesStats() {
  const st = $("scenes-stats");
  if (!st) return;
  let ok = 0, err = 0, proc = 0;
  S.cenas.forEach((c) => { if (c.status === "ok") ok++; else if (c.status === "err") err++; else proc++; });
  const total = S.cenaTotal || S.cenas.size;
  const cobertura = total ? Math.round(((ok + err) / total) * 100) : 0;
  st.innerHTML =
    `<span class="stat"><b>${ok}</b> ok</span>` +
    `<span class="stat"><b>${proc}</b> buscando</span>` +
    `<span class="stat"><b>${err}</b> pendentes</span>` +
    `<span class="stat">cobertura <b>${cobertura}%</b></span>`;
}

/* ITEM 6/7: aplica as cenas detalhadas vindas de /api/cenas */
function aplicarCenasDetalhadas(lista) {
  if (!Array.isArray(lista)) return;
  lista.forEach((cd) => {
    let c = S.cenas.get(cd.id);
    if (!c) {
      c = { idx: cd.id, status: cd.tem_midia ? "ok" : "err", texto: cd.texto, query: cd.search_query };
      S.cenas.set(cd.id, c);
    }
    c.nome = cd.nome;
    c.tipo = cd.tipo_midia;        // "video" | "image_prompt"
    c.temMidia = cd.tem_midia;
    c.arquivo = cd.arquivo;
    c.image_prompt = cd.image_prompt;
    c.animacao = cd.animacao;
    c.origem = cd.origem_midia;
    c.duracao = cd.duracao;
    c.texto = cd.texto || c.texto;
    c.query = cd.search_query || c.query;
    if (cd.tem_midia) c.status = "ok";
    renderCena(c);
  });
  renderScenesStats();
}

/* ITEM 6: importar imagens geradas (Google Flow) */
async function importarImagens() {
  const caminho = $("import-caminho").value.trim();
  hideMsg($("importar-msg"));
  if (!caminho) { showMsg($("importar-msg"), "Cole o caminho da pasta primeiro.", "erro"); return; }
  $("btn-importar-confirmar").disabled = true;
  const r = await apiJson(`/api/importar_imagens/${encodeURIComponent(S.projeto_id)}`, { caminho });
  $("btn-importar-confirmar").disabled = false;
  if (!r.success) { showMsg($("importar-msg"), r.error || "Falha ao importar.", "erro"); return; }
  showMsg($("importar-msg"), r.mensagem || `${r.importadas} imagens importadas`, "ok");
  if (r.cenas) aplicarCenasDetalhadas(r.cenas);
}

/* ITEM 7: exportar para CapCut */
async function exportarCapCut(pasta) {
  hideMsg($("capcut-msg"));
  const body = {};
  if (pasta) body.pasta_capcut = pasta;
  $("btn-capcut-confirmar").disabled = true;
  const r = await apiJson(`/api/exportar_capcut/${encodeURIComponent(S.projeto_id)}`, body);
  $("btn-capcut-confirmar").disabled = false;
  if (r.success) {
    showMsg($("capcut-msg"), r.mensagem, "ok");
    $("capcut-modal-msg").textContent = "Exportação concluída.";
  } else if (r.precisa_caminho) {
    $("capcut-modal-msg").textContent = r.error;
    $("capcut-modal").classList.remove("hidden");
    if (!$("capcut-caminho").value) $("capcut-caminho").value = S.pastaCapcut || "";
  } else {
    showMsg($("capcut-msg"), r.error || "Falha ao exportar.", "erro");
  }
}

/* ITEM 6/7: baixar .txt com nome + prompt + animação de cada cena */
function baixarPromptsTxt() {
  const lista = [...S.cenas.values()].sort((a, b) => a.idx - b.idx);
  const linhas = [];
  lista.forEach((c) => {
    linhas.push(`=== ${c.nome || ("Cena " + c.idx)} ===`);
    linhas.push(`TIPO: ${c.tipo || "?"}`);
    linhas.push("PROMPT:");
    linhas.push(c.image_prompt || "(sem prompt)");
    linhas.push("ANIMAÇÃO:");
    linhas.push(c.animacao || "(sem animação)");
    linhas.push("");
  });
  if (!linhas.length) linhas.push("(nenhuma cena ainda)");
  const blob = new Blob([linhas.join("\n")], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `prompts_${S.projeto_id}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

/* ---------- Log ---------- */
const _ultimoLog = { ts: "", msg: "", count: 0 };
function adicionarLog(evt, msg) {
  const area = $("log-area");
  if (!area) return;
  if (_ultimoLog.msg === msg && _ultimoLog.ts === evt.ts) return;

  const line = document.createElement("div");
  line.className = "log-line";
  if ((evt.level || "").toUpperCase() === "ERROR") line.classList.add("error");
  else if ((evt.level || "").toUpperCase() === "WARN") line.classList.add("warn");
  else if (evt.details && evt.details.status === "concluido") line.classList.add("ok");
  const h = hhmm(evt.ts);
  line.textContent = (h ? `[${h}] ` : "") + msg;
  area.appendChild(line);
  while (area.childElementCount > 250) area.removeChild(area.firstChild);
  area.scrollTop = area.scrollHeight;
}

/* ---------- Aplicação de status ---------- */
function aplicarStatus(status) {
  if (!status) return;

  // Configuração padrão (uma vez)
  if (!S.destinoPadrao) {
    api("/api/config").then((cfg) => {
      S.destinoPadrao = cfg.pasta_destino || "";
      S.pastaMidiaPadrao = cfg.pasta_midia_padrao || "";
      S.pastaCapcut = cfg.pasta_capcut || "";
      if (!$("auto-destino").value) $("auto-destino").value = S.destinoPadrao;
      if (!$("manual-destino").value) $("manual-destino").value = S.destinoPadrao;
      if (S.modo === "manual" && !$("card4-caminho").value) $("card4-caminho").value = S.pastaMidiaPadrao;
    }).catch(() => {});
  }

  if (S.modo === "automatico") aplicarStatusAuto(status);
  else aplicarStatusManual(status);
}

function aplicarStatusAuto(status) {
  if (!status || status.modo_execucao !== "automatico") return;

  // AJUSTE 2: mostra o painel de áudio enquanto o projeto automático não tem áudio
  const panel = $("auto-audio-panel");
  if (panel) {
    if (!status.arquivo_audio) panel.classList.remove("hidden");
    else panel.classList.add("hidden");
  }

  // Etapas concluídas do backend
  const e = status.etapas_concluidas || {};
  if (e.transcrever) setEtapaStatus(0, "concluido");
  if (e.gerar_cenas) setEtapaStatus(1, "concluido");
  if (e.storyboard) setEtapaStatus(2, "concluido");
  if (e.midias) setEtapaStatus(3, "concluido");
  if (e.render) setEtapaStatus(4, "concluido");

  // ITEM 4: botões de download da transcrição quando concluída
  if (e.transcrever || status.transcricao_completa) {
    $("transcricao-acoes").style.display = "flex";
  }

  // Etapa corrente
  const mapaEtapa = {
    transcrever: 0, cenas: 1, storyboard: 2, storyboard_fallback: 2,
    midias: 3, render: 4, montar: 4, pronto: 4,
  };
  if (status.etapa in mapaEtapa) {
    const idx = mapaEtapa[status.etapa];
    if (status.status === "erro") setEtapaStatus(idx, "erro");
    else setEtapaStatus(idx, "andamento");
  }

  // Mensagem da etapa corrente (caso o evento não tenha trazido) — ANTES de renderEtapas
  if (status.mensagem && status.etapa in mapaEtapa) {
    S.etapasMsg[mapaEtapa[status.etapa]] = status.mensagem;
  }
  renderEtapas();

  // Topbar
  atualizarTopbar(S.projeto_id, status.status, status.mensagem);

  // Fallback manual (storyboard via API falhou)
  if (status.etapa === "storyboard_fallback") {
    $("auto-fallback").classList.remove("hidden");
    $("fallback-prompt-area").value = status.prompt_fallback || "";
    $("fallback-prompt-area").classList.remove("hidden");
  }

  // Erro
  if (status.status === "erro") {
    showMsg($("auto-erro"), status.erro || status.mensagem || "Erro desconhecido", "erro");
  }

  // Vídeo pronto
  if (status.etapa === "pronto" && status.video) {
    S.videoPronto = true;
    $("auto-resultado-acoes").classList.remove("hidden");
    $("auto-video-nome").textContent = status.video.nome;
    $("auto-video-meta").textContent =
      `${fmtBytes(status.video.tamanho)}  •  ${fmtDur(status.video.duracao)}`;
    $("btn-renderizar").classList.add("hidden");
    if (!$("auto-destino").value) $("auto-destino").value = S.destinoPadrao;
    setProgressoPct(100, false, "Concluído");
  } else if (!S.videoPronto && (e.storyboard || status.etapa === "midias" || status.etapa === "render")) {
    $("btn-renderizar").classList.remove("hidden");
  }
}

/* ---------- Copiar texto ---------- */
async function copiarTexto(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = texto;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
}

/* ---------- Item 4: baixar transcrição (TXT/SRT) ---------- */
function baixarTranscricao(formato) {
  if (!S.projeto_id) return;
  const url = `/api/transcricao/${encodeURIComponent(S.projeto_id)}/download?formato=${formato}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

/* ---------- Item 3: reprocessar / avançar etapas ---------- */
async function reprocessarEtapa(etapa) {
  if (!S.projeto_id) return;
  showMsg($("auto-erro"), `Reprocessando etapa '${etapa}'…`, "info");
  const r = await apiJson(`/api/reprocessar/${encodeURIComponent(S.projeto_id)}`, { etapa });
  if (!r.success) showMsg($("auto-erro"), r.error || "Falha ao reprocessar.", "erro");
  else setTimeout(() => hideMsg($("auto-erro")), 2000);
}

async function avancarEtapa(etapa) {
  if (!S.projeto_id) return;
  showMsg($("auto-erro"), `Avançando etapa '${etapa}'…`, "info");
  const r = await apiJson(`/api/avancar/${encodeURIComponent(S.projeto_id)}`, { etapa });
  if (!r.success) showMsg($("auto-erro"), r.error || "Falha ao avançar.", "erro");
  else setTimeout(() => hideMsg($("auto-erro")), 2000);
}

/* ---------- Item 5: thumbnail da cena (polling 2s) ---------- */
function iniciarThumbCena(sceneId, imgEl) {
  if (!S.projeto_id || S.cenaThumbs[sceneId]) return;
  const timer = setInterval(async () => {
    try {
      const res = await fetch(
        `/api/cena/${encodeURIComponent(S.projeto_id)}/${sceneId}/thumbnail`,
        { cache: "no-store" }
      );
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        imgEl.src = url;
        imgEl.classList.add("visible");
        const fallback = imgEl.parentElement && imgEl.parentElement.querySelector(".thumb-fallback");
        if (fallback) fallback.style.display = "none";
        clearInterval(timer);
        delete S.cenaThumbs[sceneId];
      }
    } catch (e) { /* rede — tenta de novo no próximo tick */ }
  }, 2000);
  S.cenaThumbs[sceneId] = timer;
}

/* ---------- Item 2: URL /projeto/<id> ---------- */
function atualizarUrlProjeto(pid) {
  if (pid) history.pushState({}, "", "/projeto/" + encodeURIComponent(pid));
  else history.pushState({}, "", "/");
}

async function abrirProjetoDaUrl(pid) {
  try {
    const st = await api(`/api/status/${encodeURIComponent(pid)}`);
    const modo = (st && st.modo_execucao) || "automatico";
    abrirProjetoExistente(pid, modo);
  } catch (e) {
    abrirHome();
  }
}

/* ---------- Auto: fallback + render + salvar/enviar ---------- */
function bindAuto() {
  // AJUSTE 2: painel de áudio do fluxo automático (áudio dentro do fluxo)
  const autoFile = $("auto-audio-file");
  if (autoFile && $("btn-auto-audio-escolher")) {
    $("btn-auto-audio-escolher").addEventListener("click", () => autoFile.click());
    autoFile.addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (!f) return;
      S.autoAudioFile = f;
      $("auto-audio-nome").value = f.name;
      $("btn-auto-audio-enviar").disabled = false;
      hideMsg($("auto-audio-msg"));
    });
    $("btn-auto-audio-enviar").addEventListener("click", async () => {
      if (!S.autoAudioFile) return;
      const btn = $("btn-auto-audio-enviar");
      btn.disabled = true;
      showMsg($("auto-audio-msg"), "Enviando áudio e iniciando pipeline…", "info");
      const fd = new FormData();
      fd.append("audio", S.autoAudioFile);
      try {
        const r = await apiForm(`/api/upload_audio/${encodeURIComponent(S.projeto_id)}`, fd);
        if (!r.success) {
          showMsg($("auto-audio-msg"), r.error || "Falha ao enviar áudio.", "erro");
          btn.disabled = false;
          return;
        }
        $("auto-audio-panel").classList.add("hidden");
      } catch (e) {
        showMsg($("auto-audio-msg"), "Erro de rede: " + (e.message || e), "erro");
        btn.disabled = false;
      }
    });
  }
  $("btn-copiar-prompt").addEventListener("click", async () => {
    const texto = $("fallback-prompt-area").value;
    if (!texto) return;
    const ok = await copiarTexto(texto);
    if (ok) {
      showMsg($("fallback-copiar-ok"), "Prompt copiado para a área de transferência!", "ok");
      setTimeout(() => hideMsg($("fallback-copiar-ok")), 3000);
    }
  });
  $("btn-continuar-manual").addEventListener("click", async () => {
    $("auto-fallback").classList.add("hidden");
    showMsg($("auto-erro"), "Continuando…", "info");
    const r = await apiJson(`/api/continuar_fallback/${encodeURIComponent(S.projeto_id)}`, {});
    if (!r.success) showMsg($("auto-erro"), r.error || "Falha ao continuar.", "erro");
    else setTimeout(() => hideMsg($("auto-erro")), 1500);
  });
  $("btn-renderizar").addEventListener("click", async () => {
    $("btn-renderizar").disabled = true;
    setProgressoPct(0, true, "Renderizando…");
    const r = await apiJson(`/api/montar_video/${encodeURIComponent(S.projeto_id)}`, {});
    if (!r.success) { showMsg($("auto-erro"), r.error || "Falha ao renderizar.", "erro"); $("btn-renderizar").disabled = false; }
  });
  $("btn-auto-salvar").addEventListener("click", () => salvarVideo("auto"));
  $("btn-auto-enviar").addEventListener("click", () => abrirPasta("auto"));
}

/* ---------- Fluxo manual ---------- */
function iniciarManual() {
  const pw = $("card1-progress-wrap");
  if (pw) pw.classList.add("hidden");
  setBarraProgresso($("card1-progress"), 0);
  S.audioFile = null;
  S._transcricaoCard2Carregada = false;
  S._transcricaoRetries = 0;
  const btnTransc = $("btn-transcrever-whisper");
  const nomeArq = $("card1-audio-nome");
  const msg = $("card1-progress-msg");
  // AJUSTE 2: o áudio NÃO vem mais da criação — só pelo seletor do Card 1.
  if (btnTransc) btnTransc.disabled = true;
  if (nomeArq) nomeArq.textContent = "nenhum arquivo selecionado";
  if (msg) msg.textContent = "Aguardando transcrição…";
  $("manual-fallback").classList.add("hidden");
  $("manual-video-info").classList.add("hidden");
  $("card5-progress-wrap").classList.add("hidden");
  $("card3-progress-wrap").classList.add("hidden");
  hideMsg($("card2-msg"));
  hideMsg($("card3-msg"));
  hideMsg($("card4-msg"));
  hideMsg($("card5-msg"));
  setCardHabilitado(2, false);
  setCardHabilitado(3, false);
  setCardHabilitado(4, false);
  setCardHabilitado(5, false);
  atualizarCardAtivo({ transcricao_completa: false, etapas_concluidas: {} });
}

function setCardHabilitado(n, habilitado) {
  const card = $("card-" + n);
  if (!card) return;
  if (habilitado) card.removeAttribute("disabled");
  else card.setAttribute("disabled", "disabled");
}

function nomeArquivoAudio(path) {
  if (!path) return "";
  const partes = String(path).split(/[\\/]/);
  return partes[partes.length - 1] || "";
}

/* AJUSTE 2 — mostra no Card 1 o áudio já associado ao projeto no backend
   (ex.: projeto criado em sessão antiga com áudio). Os bytes só existem quando
   o usuário seleciona o arquivo no próprio Card 1. */
function preencherAudioCard1(status) {
  if (S.audioFile) return; // já há áudio carregado nesta sessão
  const nomeBackend = nomeArquivoAudio(status && status.arquivo_audio);
  const btnTransc = $("btn-transcrever-whisper");
  const nomeArq = $("card1-audio-nome");
  const msg = $("card1-progress-msg");
  if (!nomeBackend) return; // projeto sem áudio associado
  // O projeto tem áudio no backend, mas esta sessão não carregou os bytes:
  // mostra o nome; o usuário pode trocar/recarregar pelo seletor.
  if (btnTransc) btnTransc.disabled = true;
  if (msg) msg.textContent = "Áudio do projeto: " + nomeBackend + ". Use 'Selecionar áudio' para transcrever nesta sessão.";
  if (nomeArq) nomeArq.textContent = nomeBackend;
}

function aplicarStatusManual(status) {
  if (!status || status.modo_execucao !== "manual") return;

  const etapas = status.etapas_concluidas || {};
  const transcricao = !!status.transcricao_completa;

  // AJUSTE 2: preenche o card 1 com o áudio já associado ao projeto no backend.
  preencherAudioCard1(status);

  // AJUSTE 3: gating dos 5 cards
  const bvStatus = status.buscar_videos_status || "idle";
  const bvDone = bvStatus === "concluido" || bvStatus === "pulado"
    || !!status.buscar_videos_pulado;
  setCardHabilitado(2, transcricao);
  setCardHabilitado(3, transcricao);
  setCardHabilitado(4, transcricao && bvDone);
  setCardHabilitado(5, transcricao && bvDone);
  atualizarCardAtivo(status);

  // Topbar
  atualizarTopbar(S.projeto_id, status.status, status.mensagem);

  // Card 3 — BUSCAR VÍDEOS: contagem + progresso + resultado
  const vcEl = $("card3-video-count");
  if (vcEl && (status.video_count !== undefined || status.etapa === "buscar_videos")) {
    const vc = Number(status.video_count) || 0;
    vcEl.textContent = vc > 0
      ? `${vc} cena(s) de vídeo identificadas no storyboard.`
      : "Nenhuma cena de vídeo identificada ainda (abra o storyboard no Card 2 e gere/revise antes de buscar).";
  }
  if (bvStatus === "andamento") {
    $("card3-progress-wrap").classList.remove("hidden");
    setBarraIndeterminada($("card3-progress"));
    $("card3-progress-msg").textContent = status.mensagem || "Buscando vídeos…";
    $("btn-buscar-videos").disabled = true;
    $("btn-pular-videos").disabled = true;
  } else if (bvStatus === "concluido") {
    $("card3-progress-wrap").classList.remove("hidden");
    setBarraProgresso($("card3-progress"), 100);
    $("card3-progress-msg").textContent =
      `Concluído: ${status.videos_ok || 0} vídeo(s), ${status.videos_pendentes || 0} pendente(s).`;
    $("btn-buscar-videos").disabled = false;
    $("btn-pular-videos").disabled = false;
  } else if (bvStatus === "pulado") {
    $("card3-progress-wrap").classList.add("hidden");
    showMsg($("card3-msg"), "Etapa 'Buscar Vídeos' pulada — você pode retomá-la a qualquer momento.", "info");
    $("btn-buscar-videos").disabled = false;
    $("btn-pular-videos").disabled = false;
  } else if (bvStatus === "erro") {
    $("card3-progress-wrap").classList.remove("hidden");
    $("card3-progress-msg").textContent = "Erro: " + (status.erro || status.mensagem || "busca falhou");
    $("btn-buscar-videos").disabled = false;
    $("btn-pular-videos").disabled = false;
  }

  if (transcricao) {
    const pw = $("card1-progress-wrap");
    if (pw) pw.classList.remove("hidden");
    setBarraProgresso($("card1-progress"), 100);
    $("card1-progress-msg").textContent = "Transcrição concluída ✓";
    // ITEM 4: botões de download da transcrição (card 2 manual)
    $("card2-downloads").style.display = "flex";
    const btnTransc = $("btn-transcrever-whisper");
    if (btnTransc) btnTransc.disabled = true;
    // PROBLEMA 1.3: popula o textarea com o MESMO resultado dos downloads
    // (carregarTranscricaoNoCard2) e tenta de novo se ele continuar vazio.
    if (!S._transcricaoCard2Carregada || !$("card2-transcricao").value) {
      S._transcricaoCard2Carregada = true;
      S._transcricaoRetries = (S._transcricaoRetries || 0) + 1;
      if (S._transcricaoRetries <= 3) carregarTranscricaoNoCard2();
    }
  } else if (status.status === "erro" && status.etapa === "transcrever") {
    const pw = $("card1-progress-wrap");
    if (pw) pw.classList.remove("hidden");
    $("card1-progress-msg").textContent = "Erro na transcrição: " + (status.erro || "");
    const btnTransc = $("btn-transcrever-whisper");
    if (btnTransc && S.audioFile) btnTransc.disabled = false;
  }

  if (status.etapa === "storyboard_fallback") {
    $("manual-fallback").classList.remove("hidden");
  }

  if (status.etapa === "montar" || status.etapa === "render" || status.etapa === "midias") {
    $("card5-progress-wrap").classList.remove("hidden");
    $("btn-montar").disabled = true;
    $("card5-progress-msg").textContent = status.mensagem || "Montando…";
    setBarraIndeterminada($("card5-progress"));
  }
  if (status.etapa === "storyboard" && status.status === "andamento") {
    showMsg($("card2-msg"), status.mensagem || "Gerando storyboard via API…", "info");
  }

  if (status.status === "erro" && status.etapa !== "transcrever") {
    showMsg($("card5-msg"), status.erro || status.mensagem || "Erro", "erro");
    $("btn-montar").disabled = false;
  }

  if (status.etapa === "pronto" && status.video) {
    S.videoPronto = true;
    $("btn-montar").disabled = false;
    $("card5-progress-wrap").classList.add("hidden");
    $("manual-video-info").classList.remove("hidden");
    $("manual-video-nome").textContent = status.video.nome;
    $("manual-video-meta").textContent =
      `${fmtBytes(status.video.tamanho)}  •  ${fmtDur(status.video.duracao)}`;
    if (!$("manual-destino").value) $("manual-destino").value = S.destinoPadrao;
  }
}

/* ---------- Card 1: áudio / SRT ---------- */
function bindCard1() {
  // Botão do header e botão do corpo abrem o MESMO input de arquivo (oculto)
  $("btn-card1-selecionar").addEventListener("click", () => $("card1-audio").click());
  $("btn-card1-escolher").addEventListener("click", () => $("card1-audio").click());

  // Seleção de arquivo apenas PREPARA a transcrição (o botão dedicado dispara)
  $("card1-audio").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    S.audioFile = f;
    $("card1-audio-nome").textContent = f.name;
    $("card1-audio-nome").title = f.name;
    setBarraProgresso($("card1-progress"), 0);
    $("card1-progress-msg").textContent = "Áudio selecionado — clique em '▶ Transcrever com WhisperX'.";
    const btnTransc = $("btn-transcrever-whisper");
    if (btnTransc) btnTransc.disabled = false;
  });

  // Transcrição via endpoint EXISTENTE POST /api/upload_audio/<id> (mesmo do fluxo automático)
  $("btn-transcrever-whisper").addEventListener("click", transcreverAudioManual);

  $("btn-usar-srt").addEventListener("click", async () => {
    const srt = $("card1-srt").value.trim();
    if (!srt) return;
    $("card1-progress-msg").textContent = "Carregando SRT…";
    const r = await apiJson("/api/srt_manual", { projeto_id: S.projeto_id, srt });
    if (r.success) {
      setBarraProgresso($("card1-progress"), 100);
      $("card1-progress-msg").textContent = `SRT carregado: ${r.segmentos} segmentos ✓`;
      S._transcricaoCard2Carregada = true;
      carregarTranscricaoNoCard2();
    } else {
      showMsg($("card1-progress-msg"), r.error || "Falha ao usar SRT.", "erro");
    }
  });

  $("card1-srt-arquivo").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      $("card1-srt").value = reader.result;
      $("btn-usar-srt").click();
    };
    reader.readAsText(f);
  });
}

async function carregarTranscricaoNoCard2() {
  if (!S.projeto_id) return;
  try {
    const r = await api(`/api/transcricao/${encodeURIComponent(S.projeto_id)}`);
    const texto = (r && r.texto) || "";
    if (texto) {
      // MESMO conteúdo usado pelos downloads (roteiro_transcricao.json)
      $("card2-transcricao").value = texto;
    } else if (r && r.success === false) {
      showMsg($("card2-msg"), "Não foi possível carregar a transcrição no card 2.", "erro");
    }
  } catch (e) {
    console.error("[transcricao] erro ao carregar card 2:", e);
    showMsg($("card2-msg"), "Erro ao carregar a transcrição: " + (e.message || e), "erro");
  }
}

/* ---------- Transcrição manual via WhisperX (reusa POST /api/upload_audio/<id>) ---------- */
async function transcreverAudioManual() {
  const btn = $("btn-transcrever-whisper");
  if (!S.audioFile) {
    showMsg($("card1-progress-msg"), "Selecione um arquivo de áudio primeiro.", "erro");
    return;
  }
  if (btn) btn.disabled = true;
  const pw = $("card1-progress-wrap");
  if (pw) pw.classList.remove("hidden");
  S._transcricaoCard2Carregada = false;
  S._transcricaoRetries = 0;
  setBarraProgresso($("card1-progress"), 0);
  $("card1-progress-msg").textContent = "Enviando áudio e iniciando transcrição…";
  const fd = new FormData();
  fd.append("audio", S.audioFile);
  try {
    const r = await apiForm(`/api/upload_audio/${encodeURIComponent(S.projeto_id)}`, fd);
    if (!r.success) {
      showMsg($("card1-progress-msg"), r.error || "Erro ao iniciar a transcrição.", "erro");
      if (btn) btn.disabled = false;
      return;
    }
    $("card1-progress-msg").textContent = "Transcrevendo com WhisperX… (acompanhe a barra de progresso)";
  } catch (e) {
    showMsg($("card1-progress-msg"), "Erro de rede ao iniciar a transcrição: " + (e.message || e), "erro");
    if (btn) btn.disabled = false;
  }
}

/* ---------- Card ativo (glow vermelho) — mesma lógica de enable/disable dos cards ---------- */
function atualizarCardAtivo(status) {
  const transcricao = !!(status && status.transcricao_completa);
  const bvStatus = (status && status.buscar_videos_status) || "idle";
  const bvDone = bvStatus === "concluido" || bvStatus === "pulado"
    || !!(status && status.buscar_videos_pulado);
  const midia = !!(status && status.midia_modo);
  const video = !!(status && status.video);
  let ativo = null;
  if (!transcricao) ativo = 1;
  else if (!bvDone) ativo = 3;
  else if (!midia && !video) ativo = 4;
  else if (!video) ativo = 5;
  ["1", "2", "3", "4", "5"].forEach((n) => {
    const card = $("card-" + n);
    if (card) card.classList.toggle("card-active", String(ativo) === n);
  });
}

/* ---------- Card 2: transcrição + prompts ---------- */
function bindCard2() {
  $("btn-copiar-prompt-card2").addEventListener("click", async () => {
    const estilo = $("card2-estilo").value;
    const r = await apiJson(`/api/gerar_prompt/${encodeURIComponent(S.projeto_id)}`, { estilo_visual: estilo });
    if (!r.success) { showMsg($("card2-msg"), r.error || "Falha ao gerar prompt.", "erro"); return; }
    const ok = await copiarTexto(r.prompt);
    if (ok) showMsg($("card2-msg"), "Prompt + SRT copiados para a área de transferência!", "ok");
    else showMsg($("card2-msg"), "Não foi possível copiar automaticamente.", "erro");
    setTimeout(() => hideMsg($("card2-msg")), 3000);
  });

  $("btn-salvar-txt").addEventListener("click", async () => {
    const estilo = $("card2-estilo").value;
    const r = await apiJson(`/api/gerar_prompt/${encodeURIComponent(S.projeto_id)}`, { estilo_visual: estilo });
    if (!r.success) { showMsg($("card2-msg"), r.error || "Falha ao gerar prompt.", "erro"); return; }
    const blob = new Blob([r.prompt], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `prompt_${S.projeto_id}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  });

  $("btn-gerar-api").addEventListener("click", async () => {
    const estilo = $("card2-estilo").value;
    showMsg($("card2-msg"), "Chamando storyboard via API (pago)…", "info");
    const r = await apiJson(`/api/storyboard_api/${encodeURIComponent(S.projeto_id)}`, { estilo_visual: estilo });
    if (!r.success) showMsg($("card2-msg"), r.error || "Falha ao iniciar.", "erro");
  });
}

/* ---------- Card 3: BUSCAR VÍDEOS (AJUSTE 3) ---------- */
function bindCard3() {
  $("btn-buscar-videos").addEventListener("click", async () => {
    hideMsg($("card3-msg"));
    $("btn-buscar-videos").disabled = true;
    $("card3-progress-wrap").classList.remove("hidden");
    setBarraIndeterminada($("card3-progress"));
    $("card3-progress-msg").textContent = "Buscando vídeos… (acompanhe os eventos no console)";
    try {
      const r = await apiJson(`/api/buscar_videos/${encodeURIComponent(S.projeto_id)}`, {});
      if (!r.success) {
        showMsg($("card3-msg"), r.error || "Falha ao iniciar a busca de vídeos.", "erro");
        $("btn-buscar-videos").disabled = false;
        $("card3-progress-wrap").classList.add("hidden");
      }
    } catch (e) {
      showMsg($("card3-msg"), "Erro de rede ao iniciar a busca de vídeos: " + (e.message || e), "erro");
      $("btn-buscar-videos").disabled = false;
      $("card3-progress-wrap").classList.add("hidden");
    }
  });

  // Pular etapa por decisão EXPLÍCITA do usuário (nunca falha em silêncio)
  $("btn-pular-videos").addEventListener("click", async () => {
    hideMsg($("card3-msg"));
    const r = await apiJson(`/api/pular_buscar_videos/${encodeURIComponent(S.projeto_id)}`, {});
    if (!r.success) showMsg($("card3-msg"), r.error || "Falha ao pular a etapa.", "erro");
  });
}

/* ---------- Card 4: IMAGENS (Google Flow) — complementa as cenas-imagem ---------- */
function bindCard4() {
  // Selecionar pasta (webkitdirectory) → preenche o campo de caminho
  $("btn-card4-pasta").addEventListener("click", () => $("card4-pasta-input").click());
  $("card4-pasta-input").addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const primeiro = files[0];
    let caminho = "";
    if (primeiro.path) {
      const partes = String(primeiro.path).split(/[\\/]/);
      caminho = partes.slice(0, -1).join("\\");
    } else if (primeiro.webkitRelativePath) {
      caminho = primeiro.webkitRelativePath.split("/")[0];
    }
    if (caminho) {
      $("card4-caminho").value = caminho;
      showMsg($("card4-msg"), "Pasta selecionada (nome parcial — o navegador não entrega o caminho absoluto). Confira/edite o caminho completo acima e clique em 'Usar caminho'.", "info");
    } else {
      showMsg($("card4-msg"), "Não foi possível obter o caminho da pasta. Cole o caminho absoluto manualmente.", "erro");
    }
  });

  // "Usar caminho" = valida a pasta E importa as imagens para as cenas do tipo IMAGEM
  $("btn-card4-usar").addEventListener("click", async () => {
    const caminho = $("card4-caminho").value.trim();
    hideMsg($("card4-msg"));
    if (!caminho) { showMsg($("card4-msg"), "Cole o caminho da pasta primeiro.", "erro"); return; }
    $("btn-card4-usar").disabled = true;
    try {
      const r = await apiJson(`/api/selecionar_midia/${encodeURIComponent(S.projeto_id)}`, {
        caminho, media_type: "photo",
      });
      if (!r.success) { showMsg($("card4-msg"), r.error || "Falha.", "erro"); return; }
      // AJUSTE 3.3: importa para as cenas do tipo IMAGEM do storyboard
      const imp = await apiJson(`/api/importar_imagens/${encodeURIComponent(S.projeto_id)}`, { caminho });
      if (imp.success) {
        showMsg($("card4-msg"), imp.mensagem || `${imp.importadas} imagens importadas`, "ok");
        if (imp.cenas) aplicarCenasDetalhadas(imp.cenas);
      } else {
        showMsg($("card4-msg"), imp.error || "Falha ao importar imagens.", "erro");
      }
    } catch (e) {
      showMsg($("card4-msg"), "Erro de rede ao importar as imagens: " + (e.message || e), "erro");
    } finally {
      $("btn-card4-usar").disabled = false;
    }
  });
}

/* ---------- Card 5: montar / enviar ---------- */
function bindCard5() {
  $("btn-montar").addEventListener("click", async () => {
    // AJUSTE 3/BLOCO 2: nunca falhar em silêncio — se o card 5 está desabilitado, explicar por quê.
    if ($("card-5").hasAttribute("disabled")) {
      console.warn("[montar] card 5 desabilitado — clique mostrado como aviso explícito");
      showMsg($("card5-msg"),
        "Etapas anteriores pendentes: transcreva (card 1), conclua ou pule 'Buscar Vídeos' (card 3) antes de montar.",
        "erro");
      return;
    }
    hideMsg($("card5-msg"));
    $("btn-montar").disabled = true;
    $("card5-progress-wrap").classList.remove("hidden");
    $("card5-progress-msg").textContent = "Montando vídeo…";
    setBarraIndeterminada($("card5-progress"));
    try {
      const r = await apiJson(`/api/montar_video/${encodeURIComponent(S.projeto_id)}`, {});
      if (!r.success) {
        showMsg($("card5-msg"), r.error || "Falha ao iniciar a montagem.", "erro");
        $("btn-montar").disabled = false;
        $("card5-progress-wrap").classList.add("hidden");
      }
    } catch (e) {
      showMsg($("card5-msg"), "Erro de rede ao iniciar a montagem: " + (e.message || e), "erro");
      $("btn-montar").disabled = false;
      $("card5-progress-wrap").classList.add("hidden");
    }
  });

  $("btn-manual-salvar").addEventListener("click", () => salvarVideo("manual"));
  $("btn-manual-enviar").addEventListener("click", () => abrirPasta("manual"));
  $("btn-copiar-prompt-manual").addEventListener("click", async () => {
    const estilo = $("card2-estilo").value;
    const r = await apiJson(`/api/gerar_prompt/${encodeURIComponent(S.projeto_id)}`, { estilo_visual: estilo });
    if (!r.success) return;
    const ok = await copiarTexto(r.prompt);
    if (ok) showMsg($("card2-msg"), "Prompt copiado para a área de transferência!", "ok");
    setTimeout(() => hideMsg($("card2-msg")), 3000);
  });
}

/* ---------- Salvar / abrir pasta ---------- */
async function salvarVideo(origem) {
  const caminho = origem === "auto" ? $("auto-destino").value.trim() : $("manual-destino").value.trim();
  const btn = origem === "auto" ? $("btn-auto-salvar") : $("btn-manual-salvar");
  btn.disabled = true;
  const r = await apiJson(`/api/salvar_video/${encodeURIComponent(S.projeto_id)}`, { caminho });
  btn.disabled = false;
  const alvo = origem === "auto" ? $("auto-erro") : $("card4-msg");
  if (r.success) {
    S.destinoPadrao = caminho || S.destinoPadrao;
    showMsg(alvo, `Vídeo salvo em: ${r.destino}`, "ok");
    setTimeout(() => hideMsg(alvo), 5000);
  } else {
    showMsg(alvo, r.error || "Falha ao salvar.", "erro");
  }
}

async function abrirPasta(origem) {
  const alvo = origem === "auto" ? $("auto-erro") : $("card4-msg");
  const r = await apiJson(`/api/abrir_pasta/${encodeURIComponent(S.projeto_id)}`, {});
  if (!r.success) showMsg(alvo, r.error || "Falha ao abrir a pasta.", "erro");
}

/* ---------- ITEM 6/7: toolbar (importar imagens / exportar CapCut / prompts) ---------- */
function bindItens67() {
  $("btn-importar-imagens").addEventListener("click", () => {
    hideMsg($("importar-msg"));
    if (S.pastaMidiaPadrao && !$("import-caminho").value) $("import-caminho").value = S.pastaMidiaPadrao;
    $("import-modal").classList.remove("hidden");
  });
  $("btn-importar-fechar").addEventListener("click", () => $("import-modal").classList.add("hidden"));
  $("btn-importar-confirmar").addEventListener("click", () => importarImagens());
  $("import-modal").addEventListener("click", (e) => {
    if (e.target === $("import-modal")) $("import-modal").classList.add("hidden");
  });

  $("btn-exportar-capcut").addEventListener("click", () => exportarCapCut(""));
  $("btn-capcut-fechar").addEventListener("click", () => $("capcut-modal").classList.add("hidden"));
  $("btn-capcut-confirmar").addEventListener("click", () => exportarCapCut($("capcut-caminho").value.trim()));
  $("capcut-modal").addEventListener("click", (e) => {
    if (e.target === $("capcut-modal")) $("capcut-modal").classList.add("hidden");
  });

  $("btn-baixar-prompts").addEventListener("click", () => baixarPromptsTxt());
}

/* ---------- Boot ---------- */
function init() {
  bindHome();
  bindConfig();
  bindAuto();
  bindCard1();
  bindCard2();
  bindCard3();
  bindCard4();
  bindCard5();
  bindItens67();

  // ITEM 2: clicar no nome do projeto no topbar volta ao dashboard (sem reload)
  const nome = $("topbar-nome");
  if (nome) {
    nome.addEventListener("click", (e) => {
      if (S.projeto_id) {
        e.preventDefault();
        abrirProjetoExistente(S.projeto_id, S.modo);
      }
    });
  }

  // ITEM 2: suporta abrir direto pela URL /projeto/<id>
  const m = location.pathname.match(/^\/projeto\/([^/]+)/);
  if (m && m[1]) {
    const pid = decodeURIComponent(m[1]);
    if (pid) abrirProjetoDaUrl(pid);
  }
}
document.addEventListener("DOMContentLoaded", init);
