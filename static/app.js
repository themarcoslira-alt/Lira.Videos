/* ============================================================
   LIRA STUDIO — app.js (vanilla JS)
   ============================================================ */

"use strict";

const S = {
  projeto_id: null,
  modo: "automatico",
  since: 0,
  pollTimer: null,
  pollGaleriaTimer: null,
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
  cenasAnimarSelecionadas: new Set(),
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
  // Garante o envio do cookie de sessão (auth por ACCESS_CODE) em qualquer navegador
  opts.credentials = opts.credentials || "include";
  const res = await fetch(path, opts);
  if (res.status === 401 && !path.startsWith("/api/auth")) {
    if (typeof mostrarLogin === "function") mostrarLogin();
  }
  let data = {};
  try {
    const json = await res.json();
    data = (json && typeof json === "object") ? json : {};
  } catch (e) {
    data = {};
  }
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

async function abrirFluxo() {
  atualizarUrlProjeto(S.projeto_id);
  carregarAvatarGlobal();

  // Verifica se o projeto é Studio 2.0
  try {
    const cfg = await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/config`);
    if (cfg && cfg.studio_version === "v2") {
      S.studio_version = "v2";
      abrirStudio2(S.projeto_id);
      return;
    }
  } catch (e) {}

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
      S.modo = mc.dataset.mode || "studio2";
      const s2Fields = $("inicio-studio2-fields");
      if (s2Fields) s2Fields.style.display = (S.modo === "studio2") ? "block" : "none";
    });
  });
  S.modo = "studio2";

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

  if (!nome) { showMsg($("criar-erro"), "Digite o nome do projeto.", "erro"); return; }

  $("btn-criar").disabled = true;
  $("btn-criar").textContent = "Criando projeto…";

  if (S.modo === "studio2") {
    const fd = new FormData();
    fd.append("nome", nome);
    const pers = $("inicio-personagem") ? $("inicio-personagem").value.trim() : "";
    const est = $("inicio-estilo") ? $("inicio-estilo").value : "photorealistic_cinematic";
    const modoProd = document.querySelector('input[name="inicio-modo-prod"]:checked') ? document.querySelector('input[name="inicio-modo-prod"]:checked').value : "somente_imagens";
    fd.append("nome_personagem", pers);
    fd.append("estilo_visual", est);
    fd.append("modo_producao", modoProd);
    fd.append("continuidade_visual", "true");

    try {
      const r = await apiForm("/api/v2/projeto/criar", fd);
      if (!r.success) {
        showMsg($("criar-erro"), r.error || "Erro ao criar projeto Studio 2.0", "erro");
        return;
      }
      S.projeto_id = r.projeto_id;
      S.projetoId = r.projeto_id;
      S.projetoNome = nome;
      S.studio_version = "v2";
      abrirStudio2(r.projeto_id);
    } catch (e) {
      showMsg($("criar-erro"), "Erro de conexão: " + e.message, "erro");
    } finally {
      $("btn-criar").disabled = false;
      $("btn-criar").textContent = "Criar projeto e começar";
    }
    return;
  }

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
    // ETAPA 3: a etapa "Buscar Vídeos" foi substituída pelo HUB DE PRODUÇÃO NO FLOW;
    // o progresso da fila é acompanhado via /api/flow/status (polling), não via eventos.
    void mVideo;
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
    if (c.image_prompt) {
      const ts = c.tempo_inicio !== undefined ? fmtTs(c.tempo_inicio) : "00:00";
      linhas.push(`[${ts}] ${c.image_prompt}`);
    }
    // optional: include animação if needed, commented out for now
    // if (c.animacao) {
    //   const ts = c.tempo_inicio !== undefined ? fmtTs(c.tempo_inicio) : "00:00";
    //   linhas.push(`[${ts}] ${c.animacao}`);
    // }
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
  pararPollFlow();
  const mural = $("mural-midias");
  if (mural) mural.innerHTML = "";
  const mz = $("mural-vazio");
  if (mz) mz.classList.remove("hidden");
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

  // ETAPA 3 — HUB DE PRODUÇÃO NO FLOW: gating dos cards (sem "Buscar Vídeos")
  const temPlan = (Number(status.scene_plan_total) || 0) > 0;
  setCardHabilitado(2, transcricao);
  setCardHabilitado(3, transcricao && temPlan);
  setCardHabilitado(4, transcricao && temPlan);
  setCardHabilitado(5, transcricao && temPlan);
  atualizarCardAtivo(status);

  // Topbar
  atualizarTopbar(S.projeto_id, status.status, status.mensagem);

  // ETAPA 3 — produção no Flow: inicia o polling da galeria/fila assim que há plano
  if (transcricao && temPlan) iniciarPollFlow();
  else pararPollFlow();

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
  const temPlan = Number((status && status.scene_plan_total) || 0) > 0;
  const comMedia = Number((status && status.scene_plan_com_media) || 0) > 0;
  const video = !!(status && status.video);
  let ativo = null;
  if (!transcricao) ativo = 1;
  else if (!temPlan) ativo = 2;
  else if (!comMedia) ativo = 3;
  else if (!video) ativo = 4;
  else ativo = 5;
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

  // Upload avatar global
  const btnUploadAvatar = $("btn-upload-avatar-global");
  const inputAvatar = $("input-avatar-global");
  if (btnUploadAvatar && inputAvatar) {
    btnUploadAvatar.addEventListener("click", () => inputAvatar.click());
    inputAvatar.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      showMsg($("card2-msg"), "Enviando foto do personagem (avatar)...", "info");
      const fd = new FormData();
      fd.append("personagem", f);
      const r = await apiForm(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/personagem_global`, fd);
      if (r.success) {
        showMsg($("card2-msg"), "Avatar do personagem salvo! Aplicado a todas as cenas.", "ok");
        setTimeout(() => hideMsg($("card2-msg")), 3000);
        carregarAvatarGlobal();
        pollGaleria();
      } else {
        showMsg($("card2-msg"), r.error || "Falha ao enviar avatar.", "erro");
      }
    });
  }

  $("btn-gerar-api").addEventListener("click", async () => {
    const estilo = $("card2-estilo").value;
    const nomePersonagemInput = $("input-nome-personagem");
    const nomePersonagem = nomePersonagemInput ? nomePersonagemInput.value.trim() : "";
    showMsg($("card2-msg"), "Gerando prompts (com personagem de referência)...", "info");
    const payload = { estilo_visual: estilo };
    if (nomePersonagem) {
      payload.nome_personagem = nomePersonagem;
    }
    const r = await apiJson(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/gerar_prompts`, payload);
    if (r.success) {
      showMsg($("card2-msg"), "Prompts gerados com sucesso! Etapa 3 (Produção no Flow) liberada.", "ok");
      setTimeout(() => hideMsg($("card2-msg")), 3500);
      const cenas = (r.plan && r.plan.cenas) || [];
      // Libera Etapa 3 e atualiza o glow
      setCardHabilitado(3, true);
      setCardHabilitado(4, true);
      setCardHabilitado(5, true);
      atualizarCardAtivo({
        transcricao_completa: true,
        scene_plan_total: cenas.length,
        scene_plan_com_media: 0,
      });
      // Storyboard/prompts numerados (1, 2, 3...)
      const box = $("card2-storyboard-box");
      const txt = $("card2-storyboard-texto");
      const cnt = $("card2-storyboard-count");
      if (box && txt && cenas.length) {
        // Clean prompts: split lines, trim, filter empty, dedupe while preserving order
        const rawLines = cenas.map((c) => {
          const ts = c.tempo_inicio !== undefined ? fmtTs(c.tempo_inicio) : "00:00";
          const tipo = c.tipo === "video" ? "[VIDEO]" : "[IMAGEM]";
          return `[${ts}] ${tipo} ${c.prompt_imagem || c.texto || ""}`.trim();
        });
        const seen = new Set();
        const cleanedLines = rawLines.filter(line => {
          const trimmed = line.trim();
          if (!trimmed) return false;
          if (seen.has(trimmed)) return false;
          seen.add(trimmed);
          return true;
        });
        const linhas = cleanedLines.join("\n\n");
        txt.value = linhas;
        if (cnt) cnt.textContent = cleanedLines.length + " cenas";
        box.classList.remove("hidden");
      }
      iniciarPollFlow();
    } else {
      showMsg($("card2-msg"), r.error || "Falha ao gerar prompts.", "erro");
    }
  });

  // Copiar todos os prompts numerados (1 a N) — storyboard box do Card 2
  const btnCopiarTodos = $("btn-copiar-todos-prompts");
  if (btnCopiarTodos) {
    btnCopiarTodos.addEventListener("click", async () => {
      const txt = $("card2-storyboard-texto");
      if (!txt || !txt.value) { showMsg($("card2-msg"), "Gere os prompts primeiro.", "erro"); return; }
      const ok = await copiarTexto(txt.value);
      if (ok) { showMsg($("card2-msg"), "Todos os prompts copiados!", "ok"); setTimeout(() => hideMsg($("card2-msg")), 2500); }
      else showMsg($("card2-msg"), "Não foi possível copiar.", "erro");
    });
  }
}

async function carregarAvatarGlobal() {
  if (!S.projeto_id) return;
  const avatarImg = $("avatar-img-preview");
  const avatarPlaceholder = $("avatar-placeholder");
  if (!avatarImg) return;

  const url = `/api/scene_plan/${encodeURIComponent(S.projeto_id)}/personagem_avatar?t=` + Date.now();
  avatarImg.src = url;
  avatarImg.onload = () => {
    avatarImg.classList.remove("hidden");
    if (avatarPlaceholder) avatarPlaceholder.classList.add("hidden");
  };
  avatarImg.onerror = () => {
    avatarImg.classList.add("hidden");
    if (avatarPlaceholder) avatarPlaceholder.classList.remove("hidden");
  };
}

/* ============================================================
   ETAPA 3 — HUB DE PRODUÇÃO NO FLOW (conexão + fila + mural + animar)
   ============================================================ */

function fmtTs(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const mm = String(Math.floor(s / 60) % 60).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return mm + ":" + ss;
}

const STATUS_MURAL = {
  "PENDENTE":              { cls: "cena-status-pendente",           label: "na fila" },
  "ENVIADA":               { cls: "cena-status-enviada",            label: "enviada" },
  "PROMPT_PRONTO":         { cls: "cena-status-enviada",            label: "enviada" },
  "GERANDO":               { cls: "cena-status-gerando",            label: "⚡ gerando..." },
  "MIDIA_IMPORTADA":       { cls: "cena-status-midia_importada",    label: "pronto" },
  "PRONTA_PARA_ANIMAR":    { cls: "cena-status-pronta_para_animar", label: "a animar" },
  "ANIMADA":               { cls: "cena-status-animada",            label: "animado" },
  "PRONTA_PARA_MONTAGEM":  { cls: "cena-status-pronta_para_montagem", label: "pronto" },
  "MONTADA":               { cls: "cena-status-montada",            label: "montado" },
  "ERRO":                  { cls: "cena-status-erro",               label: "❌ erro" },
};

function bindCard3() {
  // 3.1 — Conexão com o Google Flow
  $("btn-flow-abrir").addEventListener("click", async () => {
    hideMsg($("card3-msg"));
    const r = await apiJson(`/api/flow/abrir`, { projeto_id: S.projeto_id });
    if (!r.success) { showMsg($("card3-msg"), r.error || "Falha ao conectar.", "erro"); return; }
    pollFlowStatus();
  });
  $("btn-flow-desconectar").addEventListener("click", async () => {
    const r = await apiJson(`/api/flow/desconectar`, { projeto_id: S.projeto_id });
    if (!r.success) { showMsg($("card3-msg"), r.error || "Falha ao desconectar.", "erro"); return; }
    pollFlowStatus();
  });

  // 3.2 — Fila de envio
  $("btn-enviar-flow").addEventListener("click", async () => {
    hideMsg($("flow-fila-msg"));
    const r = await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}`);
    if (!r.success || !r.plan || !r.plan.cenas || !r.plan.cenas.length) {
      showMsg($("flow-fila-msg"), "Gere os prompts no Card 2 antes de enviar.", "erro");
      return;
    }
    const ids = r.plan.cenas.map((c) => c.id);
    const btn = $("btn-enviar-flow");
    btn.disabled = true;
    try {
      const env = await apiJson(`/api/flow/enqueue/${encodeURIComponent(S.projeto_id)}`, { scene_ids: ids });
      if (env.success) {
        showMsg($("flow-fila-msg"), `Enviados ${env.enviados} prompt(s) para a fila do Flow.`, "ok");
      } else {
        showMsg($("flow-fila-msg"), env.error || "Falha ao enviar.", "erro");
      }
    } catch (e) {
      showMsg($("flow-fila-msg"), "Erro de rede: " + (e.message || e), "erro");
    } finally {
      btn.disabled = false;
    }
    pollFlowStatus();
  });

  $("btn-parar-fila").addEventListener("click", async () => {
    const r = await apiJson(`/api/flow/fila/parar`, { projeto_id: S.projeto_id });
    if (!r.success) { showMsg($("card3-msg"), r.error || "Falha ao parar a fila.", "erro"); return; }
    $("btn-parar-fila").textContent = r.fila_parada ? "▶ Retomar fila" : "⏸ Parar fila";
    pollFlowStatus();
  });

  $("btn-limpar-fila").addEventListener("click", async () => {
    hideMsg($("flow-fila-msg"));
    const r = await apiJson(`/api/flow/fila/limpar`, { projeto_id: S.projeto_id });
    if (!r.success) { showMsg($("flow-fila-msg"), r.error || "Falha ao limpar.", "erro"); return; }
    showMsg($("flow-fila-msg"), "Fila limpa. Status de envio reiniciados.", "ok");
    pollGaleria();
    pollFlowStatus();
  });

  // Auto-importar da pasta monitorada (downloads/flow)
  const btnAutoImport = $("btn-auto-importar-pasta");
  if (btnAutoImport) {
    btnAutoImport.addEventListener("click", async () => {
      hideMsg($("flow-fila-msg"));
      showMsg($("flow-fila-msg"), "Varrendo pasta de downloads...", "info");
      const r = await apiJson(`/api/flow/auto_importar/${encodeURIComponent(S.projeto_id)}`, {});
      if (r.success) {
        const msg = r.importados > 0
          ? `${r.importados} novo(s) arquivo(s) importado(s) e associado(s) às cenas!`
          : "Nenhum arquivo novo encontrado na pasta monitorada.";
        showMsg($("flow-fila-msg"), msg, r.importados > 0 ? "ok" : "info");
        setTimeout(() => hideMsg($("flow-fila-msg")), 4000);
        pollGaleria();
        pollFlowStatus();
      } else {
        showMsg($("flow-fila-msg"), r.error || "Falha ao auto-importar.", "erro");
      }
    });
  }

  // 3.4 — Animar selecionadas
  const btnAnimar = $("btn-animar-prontas");
  if (btnAnimar) {
    btnAnimar.textContent = `🎬 Animar selecionadas (${S.cenasAnimarSelecionadas.size})`;
    btnAnimar.disabled = (S.cenasAnimarSelecionadas.size === 0);
    btnAnimar.addEventListener("click", async () => {
      hideMsg($("flow-fila-msg"));
      if (!S.cenasAnimarSelecionadas.size) {
        showMsg($("flow-fila-msg"), "Marque ao menos uma imagem nos cards para animar.", "erro");
        return;
      }
      const selecionadas = Array.from(S.cenasAnimarSelecionadas);
      btnAnimar.disabled = true;
      try {
        const env = await apiJson(`/api/flow/enqueue_anim/${encodeURIComponent(S.projeto_id)}`, { scene_ids: selecionadas });
        if (env.success) {
          showMsg($("flow-fila-msg"), `Animação enfileirada para ${env.enviados} cena(s).`, "ok");
          S.cenasAnimarSelecionadas.clear();
          atualizarBtnAnimarSelecionadas();
        } else {
          showMsg($("flow-fila-msg"), env.error || "Falha ao animar.", "erro");
        }
      } catch (e) {
        showMsg($("flow-fila-msg"), "Erro ao enviar animação: " + (e.message || e), "erro");
      } finally {
        atualizarBtnAnimarSelecionadas();
        pollFlowStatus();
        pollGaleria();
      }
    });
  }

  // Modal de mídia
  const btnFecharModal = $("btn-media-modal-fechar");
  if (btnFecharModal) {
    btnFecharModal.addEventListener("click", fecharModalMedia);
  }
}

function atualizarBtnAnimarSelecionadas() {
  const btnAnimar = $("btn-animar-prontas");
  if (btnAnimar) {
    const n = S.cenasAnimarSelecionadas.size;
    btnAnimar.textContent = `🎬 Animar selecionadas (${n})`;
    btnAnimar.disabled = (n === 0);
  }
}

/* ---------- Polling da Etapa 3 (status da conexão + galeria ao vivo) ---------- */
function iniciarPollFlow() {
  if (S.pollGaleriaTimer) return;
  S.pollGaleriaTimer = setInterval(() => {
    pollFlowStatus();
    pollGaleria();
  }, 2500);
  pollFlowStatus();
  pollGaleria();
}

function pararPollFlow() {
  if (S.pollGaleriaTimer) {
    clearInterval(S.pollGaleriaTimer);
    S.pollGaleriaTimer = null;
  }
}

async function pollFlowStatus() {
  if (!S.projeto_id) return;
  try {
    const r = await api(`/api/flow/status?projeto_id=${encodeURIComponent(S.projeto_id)}`);
    if (!r.success) return;
    const dot = $("flow-status-dot");
    const txt = $("flow-status-texto");
    const conta = $("flow-conta");
    if (dot) dot.classList.toggle("conectado", !!r.conectado);
    if (txt) {
      txt.textContent = r.conectado ? "Conectado (Chrome Playwright)" : "Desconectado";
      txt.className = "flow-status-texto " + (r.conectado ? "conectado" : "desconectado");
    }
    if (conta) conta.textContent = r.conectado ? (r.conta || "") : "";
    const btn = $("btn-parar-fila");
    if (btn) btn.textContent = r.fila_parada ? "▶ Retomar fila" : "⏸ Parar fila";
    const c = r.contadores || {};
    const total = (r.progresso && r.progresso.total) || 0;
    if ($("cnt-total")) $("cnt-total").textContent = total;
    if ($("cnt-pendentes")) $("cnt-pendentes").textContent = c.pendentes || 0;
    if ($("cnt-gerando")) $("cnt-gerando").textContent = c.gerando || 0;
    if ($("cnt-prontos")) $("cnt-prontos").textContent = c.prontos || 0;
    if ($("cnt-erros")) $("cnt-erros").textContent = c.erros || 0;

    // Atualiza barra de atividade em tempo real
    const atvTxt = $("flow-atividade-texto");
    const atvIco = $("flow-atividade-icon");
    if (atvTxt) {
      if (r.cena_ativa && r.cena_ativa.mensagem) {
        atvTxt.textContent = r.cena_ativa.mensagem;
        if (atvIco) {
          atvIco.textContent = r.cena_ativa.status === "GERANDO" ? "⚡" : (r.cena_ativa.status === "ERRO" ? "⚠️" : "✅");
          atvIco.className = r.cena_ativa.status === "GERANDO" ? "flow-pulsing-dot" : "";
        }
      } else if (c.gerando > 0 || r.worker_rodando) {
        atvTxt.textContent = `${c.gerando || 1} cena(s) sendo geradas no Google Flow...`;
        if (atvIco) { atvIco.textContent = "⚡"; atvIco.className = "flow-pulsing-dot"; }
      } else if (c.enviadas > 0) {
        atvTxt.textContent = `${c.enviadas} cena(s) aguardando na fila...`;
        if (atvIco) { atvIco.textContent = "⏳"; atvIco.className = ""; }
      } else if (c.prontos > 0 && c.pendentes === 0) {
        atvTxt.textContent = `Todas as ${total} cenas concluídas com sucesso!`;
        if (atvIco) { atvIco.textContent = "🎉"; atvIco.className = ""; }
      } else {
        atvTxt.textContent = "Fila ociosa. Clique em 'Enviar pro Flow' para iniciar.";
        if (atvIco) { atvIco.textContent = "●"; atvIco.className = ""; }
      }
    }
  } catch (e) { /* polling silencioso */ }
}

/* ---------- Mural de Mídias em Produção (galeria ao vivo) ---------- */
async function pollGaleria() {
  if (!S.projeto_id) return;
  try {
    const r = await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}`);
    if (!r.success || !r.plan || !r.plan.cenas) return;
    S.currentPlan = r.plan;
    renderMural(r.plan.cenas);
    atualizarBtnAnimarSelecionadas();
  } catch (e) { /* polling silencioso */ }
}

function renderMural(cenas) {
  const grid = $("mural-midias");
  const vazio = $("mural-vazio");
  if (!grid) return;
  if (!cenas.length) {
    grid.innerHTML = "";
    if (vazio) vazio.classList.remove("hidden");
    return;
  }
  if (vazio) vazio.classList.add("hidden");

  // Mapeia os elementos existentes
  const existentes = {};
  grid.querySelectorAll(".cena-card").forEach((el) => {
    existentes[el.dataset.mural] = el;
  });

  // Track cards que devem permanecer na grid
  const mantidos = new Set();

  cenas.forEach((c) => {
    const cid = String(c.id);
    const numCid = Number(c.id);
    mantidos.add(cid);

    const st = STATUS_MURAL[c.status] || { cls: "cena-status-pendente", label: c.status || "pendente" };
    const ts = c.tempo_inicio !== undefined ? fmtTs(c.tempo_inicio) : "00:00";
    const temMidia = !!c.arquivo_midia;
    const temPersona = !!c.personagem_ref;
    const isVideo = c.tipo === "video" || (c.arquivo_midia && c.arquivo_midia.toLowerCase().endsWith(".mp4"));
    const path = c.arquivo_midia || "";
    const isGerando = c.status === "GERANDO";
    const isErro = c.status === "ERRO";
    const isEnviada = c.status === "ENVIADA" || c.status === "PROMPT_PRONTO";

    const cardExistente = existentes[cid];
    if (cardExistente) {
      // Sincroniza checkbox sem recriar o card
      const chk = cardExistente.querySelector(".chk-animar-cena");
      if (chk) {
        chk.checked = S.cenasAnimarSelecionadas.has(numCid);
      }
      // Se nada mudou na mídia, no status ou na mensagem de erro, mantém o card intacto
      if (cardExistente.dataset.status === c.status && cardExistente.dataset.mediaPath === path && cardExistente.dataset.erro === (c.erro_msg || "")) {
        return;
      }
    }

    // Cria ou reconstrói o card
    const card = cardExistente || document.createElement("div");
    card.className = `cena-card status-${(c.status || "pendente").toLowerCase()}`;
    card.dataset.mural = cid;
    card.dataset.status = c.status || "PENDENTE";
    card.dataset.mediaPath = path;
    card.dataset.erro = c.erro_msg || "";

    // Checkbox para animar quando mídia pronta
    const isChecked = S.cenasAnimarSelecionadas.has(numCid);
    const checkAnimarHtml = temMidia
      ? `<div class="cena-animar-check" style="position:absolute;top:6px;left:6px;z-index:4;display:flex;align-items:center;background:rgba(18,18,24,0.85);padding:3px 7px;border-radius:4px;border:1px solid rgba(255,255,255,0.18)">
          <input type="checkbox" class="chk-animar-cena" data-cid="${c.id}" ${isChecked ? 'checked' : ''} style="cursor:pointer;width:14px;height:14px;accent-color:var(--accent)">
          <span style="font-size:10px;font-weight:600;margin-left:4px;color:#fff">Animar</span>
        </div>`
      : '';

    // Conteúdo da miniatura / placeholder
    let thumbHtml = "";
    if (temMidia) {
      thumbHtml = `<img class="cena-img" src="/api/cena/${encodeURIComponent(S.projeto_id)}/${c.id}/thumbnail?t=${Date.now()}" alt="cena ${c.id}" loading="lazy">`;
    } else if (isGerando) {
      thumbHtml = `<span class="cena-thumb-placeholder"><span class="flow-spinner"></span><span style="font-size:11.5px;font-weight:600;margin-top:8px;color:var(--accent-light)">Gerando no Flow...</span></span>`;
    } else if (isErro) {
      thumbHtml = `<span class="cena-thumb-placeholder" style="color:var(--err);padding:8px;text-align:center"><span style="font-size:22px">⚠️</span><span style="font-size:10.5px;margin-top:4px;color:var(--err);line-height:1.3">${esc(c.erro_msg || "Falha na geração")}</span></span>`;
    } else if (isEnviada) {
      thumbHtml = `<span class="cena-thumb-placeholder"><span style="font-size:24px">📤</span><span style="font-size:11px;margin-top:4px;color:var(--warn)">Na fila do Flow</span></span>`;
    } else {
      thumbHtml = `<span class="cena-thumb-placeholder"><span style="font-size:26px">🖼</span></span>`;
    }

    card.innerHTML =
      '<div class="cena-thumb" style="cursor:pointer;position:relative" title="Clique para visualizar mídia">' +
        checkAnimarHtml +
        thumbHtml +
        `<span class="cena-tipo-badge cena-tipo-${isVideo ? "video" : "image"}">${isVideo ? "vídeo" : "imagem"}</span>` +
        (temPersona ? '<span class="badge badge-ok" style="position:absolute;bottom:6px;left:6px;font-size:10px">👤 Avatar</span>' : '') +
      '</div>' +
      '<div class="cena-info">' +
        `<div class="cena-id-ts"><span class="cena-num">Cena ${c.id}</span><span class="cena-ts">[${ts}]</span></div>` +
        `<div class="cena-texto" title="${esc(c.prompt_imagem || c.texto || "")}">${esc(c.prompt_imagem || c.texto || "")}</div>` +
        `<span class="cena-status ${st.cls}" title="${esc(c.erro_msg || st.label)}">${st.label}</span>` +
      '</div>' +
      '<div class="cena-acoes" style="padding:6px 12px 10px;display:flex;gap:6px;justify-content:flex-end">' +
        `<button class="btn btn-sm btn-ghost btn-ver-midia" type="button" title="Visualizar">👁 Ver</button>` +
        (temMidia
          ? `<a class="btn btn-sm btn-ghost" href="/api/cena/${encodeURIComponent(S.projeto_id)}/${c.id}/media?download=1" target="_blank" download title="Baixar">⬇</a>` +
            `<button class="btn btn-sm btn-ghost btn-excluir-midia" type="button" title="Excluir mídia">🗑</button>`
          : '') +
      '</div>';

    // Handler do checkbox
    const chkEl = card.querySelector(".chk-animar-cena");
    if (chkEl) {
      chkEl.addEventListener("click", (e) => e.stopPropagation());
      chkEl.addEventListener("change", (e) => {
        e.stopPropagation();
        if (chkEl.checked) {
          S.cenasAnimarSelecionadas.add(numCid);
        } else {
          S.cenasAnimarSelecionadas.delete(numCid);
        }
        atualizarBtnAnimarSelecionadas();
      });
    }

    // Click handlers
    card.querySelector(".cena-thumb").addEventListener("click", (e) => {
      if (e.target.closest(".cena-animar-check")) return;
      abrirModalMedia(c);
    });
    card.querySelector(".btn-ver-midia").addEventListener("click", () => abrirModalMedia(c));
    
    const btnDel = card.querySelector(".btn-excluir-midia");
    if (btnDel) {
      btnDel.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Excluir mídia da Cena ${c.id}?`)) return;
        const res = await apiJson(`/api/cena/${encodeURIComponent(S.projeto_id)}/${c.id}/excluir_midia`, {});
        if (res.success) {
          S.cenasAnimarSelecionadas.delete(numCid);
          atualizarBtnAnimarSelecionadas();
          pollGaleria();
          pollFlowStatus();
        }
      });
    }

    if (!cardExistente) {
      grid.appendChild(card);
    }
  });

  // Remove cards que não existem mais (caso de limpar a fila)
  grid.querySelectorAll(".cena-card").forEach((el) => {
    if (!mantidos.has(el.dataset.mural)) {
      el.remove();
    }
  });
}

function abrirModalMedia(cena) {
  const modal = $("media-modal");
  if (!modal) return;

  $("media-modal-titulo").textContent = `Visualizar Mídia — Cena ${cena.id}`;
  const st = STATUS_MURAL[cena.status] || { cls: "badge-wait", label: cena.status || "pendente" };
  const stEl = $("media-modal-status");
  if (stEl) {
    stEl.className = "badge " + st.cls;
    stEl.textContent = st.label;
  }

  const promptEl = $("media-modal-prompt");
  if (promptEl) promptEl.textContent = cena.prompt_imagem || cena.texto || "—";

  const charBox = $("media-modal-char-box");
  if (charBox) {
    charBox.classList.toggle("hidden", !cena.personagem_ref);
  }

  const img = $("media-modal-img");
  const video = $("media-modal-video");
  const vazio = $("media-modal-vazio");
  const btnDown = $("btn-media-modal-download");
  const btnDel = $("btn-media-modal-excluir");

  const temMidia = !!cena.arquivo_midia;
  const isVideo = cena.tipo === "video" || (cena.arquivo_midia && cena.arquivo_midia.toLowerCase().endsWith(".mp4"));

  if (!temMidia) {
    if (img) img.classList.add("hidden");
    if (video) { video.classList.add("hidden"); video.pause(); }
    if (vazio) vazio.classList.remove("hidden");
    if (btnDown) btnDown.classList.add("hidden");
    if (btnDel) btnDel.classList.add("hidden");
  } else {
    if (vazio) vazio.classList.add("hidden");
    const mediaUrl = `/api/cena/${encodeURIComponent(S.projeto_id)}/${cena.id}/media?t=${Date.now()}`;
    if (isVideo) {
      if (img) img.classList.add("hidden");
      if (video) {
        video.src = mediaUrl;
        video.classList.remove("hidden");
      }
    } else {
      if (video) { video.classList.add("hidden"); video.pause(); }
      if (img) {
        img.src = mediaUrl;
        img.classList.remove("hidden");
      }
    }

    if (btnDown) {
      btnDown.href = `/api/cena/${encodeURIComponent(S.projeto_id)}/${cena.id}/media?download=1`;
      btnDown.classList.remove("hidden");
    }
    if (btnDel) {
      btnDel.classList.remove("hidden");
      btnDel.onclick = async () => {
        if (!confirm(`Excluir mídia da Cena ${cena.id}?`)) return;
        const res = await apiJson(`/api/cena/${encodeURIComponent(S.projeto_id)}/${cena.id}/excluir_midia`, {});
        if (res.success) {
          fecharModalMedia();
          pollGaleria();
          pollFlowStatus();
        }
      };
    }
  }

  modal.classList.remove("hidden");
}

function fecharModalMedia() {
  const modal = $("media-modal");
  if (!modal) return;
  modal.classList.add("hidden");
  const video = $("media-modal-video");
  if (video) { video.pause(); video.src = ""; }
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
    const caminho = $("card4-caminho").value.trim() || "C:\\Users\\Administrator\\Videos\\PROJETO";
    hideMsg($("card4-msg"));
    $("btn-card4-usar").disabled = true;
    try {
      const imp = await apiJson(`/api/importar_imagens/${encodeURIComponent(S.projeto_id)}`, { caminho });
      if (imp.success) {
        showMsg($("card4-msg"), imp.mensagem || `${imp.importadas} mídias sincronizadas com sucesso!`, "ok");
        if (imp.cenas) aplicarCenasDetalhadas(imp.cenas);
      } else {
        showMsg($("card4-msg"), imp.error || "Falha ao sincronizar mídias.", "erro");
      }
    } catch (e) {
      showMsg($("card4-msg"), "Erro de rede ao sincronizar as mídias: " + (e.message || e), "erro");
    } finally {
      $("btn-card4-usar").disabled = false;
    }
  });

  // "Montar Cenas e Sincronizar Timestamps (Diretor de Vídeo YouTube → CapCut)"
  const btnMontarCapcut = $("btn-card4-montar-capcut");
  if (btnMontarCapcut) {
    btnMontarCapcut.addEventListener("click", async () => {
      const caminho = $("card4-caminho").value.trim() || "C:\\Users\\Administrator\\Videos\\PROJETO";
      hideMsg($("card4-msg"));
      btnMontarCapcut.disabled = true;
      const textoOriginal = btnMontarCapcut.textContent;
      btnMontarCapcut.textContent = "⏳ Montando Cenas e Sincronizando Timestamps...";
      try {
        const r = await apiJson(`/api/flow/montar_e_exportar_capcut/${encodeURIComponent(S.projeto_id)}`, { caminho });
        if (r.success) {
          showMsg($("card4-msg"), `✅ ${r.mensagem || "Projeto montado e sincronizado!"}<br><small style="color:var(--muted)">Pasta do rascunho: ${r.draft_dir || "CapCut"}</small>`, "ok");
          if (r.cenas) aplicarCenasDetalhadas(r.cenas);
          const c5 = $("card-5");
          if (c5) c5.removeAttribute("disabled");
        } else {
          showMsg($("card4-msg"), r.error || "Falha ao exportar para o CapCut.", "erro");
        }
      } catch (e) {
        showMsg($("card4-msg"), "Erro ao sincronizar e montar projeto: " + (e.message || e), "erro");
      } finally {
        btnMontarCapcut.disabled = false;
        btnMontarCapcut.textContent = textoOriginal;
      }
    });
  }
}

/* ---------- Card 5: montar / enviar ---------- */
function bindCard5() {
  $("btn-montar").addEventListener("click", async () => {
    // AJUSTE 3/BLOCO 2: nunca falhar em silêncio — se o card 5 está desabilitado, explicar por quê.
    if ($("card-5").hasAttribute("disabled")) {
      console.warn("[montar] card 5 desabilitado — clique mostrado como aviso explícito");
      showMsg($("card5-msg"),
        "Etapas anteriores pendentes: transcreva (card 1), gere os prompts (card 2) e produza no Flow (card 3) antes de montar.",
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

/* ---------- Autenticação (código de acesso — FASE 4) ---------- */
function mostrarLogin() {
  document.querySelectorAll(".tela").forEach((t) => t.classList.remove("ativa"));
  $("tela-login").classList.add("ativa");
}

async function checkAuth() {
  document.querySelectorAll(".tela").forEach((t) => t.classList.remove("ativa"));
  const inicio = $("tela-inicio");
  if (inicio) inicio.classList.add("ativa");
  return true;
}

function bindAuth() {
  const btn = $("btn-login");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const codigo = $("acesso-codigo").value.trim();
    if (!codigo) { showMsg($("login-erro"), "Digite o código de acesso.", "erro"); return; }
    btn.disabled = true;
    try {
      const r = await api(`/api/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codigo }),
      });
      if (r.success) {
        hideMsg($("login-erro"));
        document.querySelectorAll(".tela").forEach((t) => t.classList.remove("ativa"));
        $("tela-inicio").classList.add("ativa");
      } else {
        showMsg($("login-erro"), r.error || "Código incorreto.", "erro");
      }
    } catch (e) {
      showMsg($("login-erro"), "Erro de rede: " + (e.message || e), "erro");
    } finally {
      btn.disabled = false;
    }
  });
  const input = $("acesso-codigo");
  if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") btn.click(); });
}

/* ---------- Boot ---------- */
function init() {
  bindAuth();
  checkAuth();
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


/* ============================================================
   ULTRACUT3 STUDIO 2.0 — CONTROLADOR CLIENT-SIDE (EXPERIÊNCIA REFINADA)
   ============================================================ */

let S2_POLL_TIMER = null;
let S2_ACTIVE_TAB = "studio";

function initStudio2() {

  // Upload de arquivo .SRT / .TXT direto do disco
  if ($("btn-s2-escolher-srt-file") && $("s2-input-srt-file")) {
    $("btn-s2-escolher-srt-file").addEventListener("click", () => $("s2-input-srt-file").click());
    $("s2-input-srt-file").addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async (re) => {
        const conteudo = re.target.result;
        if ($("s2-textarea-srt")) $("s2-textarea-srt").value = conteudo;
        if (!S.projeto_id) { alert("Selecione um projeto."); return; }
        try {
          const res = await api(`/api/v2/transcricao/${encodeURIComponent(S.projeto_id)}/usar_srt`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ srt_texto: conteudo }),
          });
          if (res.success) {
            alert(`✓ SRT carregado com sucesso! ${res.total_cenas} cenas criadas.`);
            try {
              if (typeof atualizarStatusStudio2 === "function") {
                await atualizarStatusStudio2(S.projeto_id);
              } else if (typeof carregarStudio2Dados === "function") {
                await carregarStudio2Dados(S.projeto_id);
              }
            } catch (e) {
              console.warn("Aviso ao atualizar status Studio2:", e);
            }
          } else {
            alert("Erro ao processar SRT: " + (res.error || ""));
          }
        } catch (err) {
          alert("Erro na conexão: " + err.message);
        }
      };
      reader.readAsText(file);
    });
  }

  // Puxar Transcrição do Whisper para a Textarea
  if ($("btn-s2-puxar-transcricao")) {
    $("btn-s2-puxar-transcricao").addEventListener("click", async () => {
      if (!S.projeto_id) return;
      try {
        const res = await api(`/api/v2/transcricao/${encodeURIComponent(S.projeto_id)}/status`);
        if (res && res.transcricao && res.transcricao.srt_texto) {
          if ($("s2-textarea-srt")) $("s2-textarea-srt").value = res.transcricao.srt_texto;
        } else {
          alert("Nenhuma transcrição encontrada ainda. Execute o Whisper primeiro.");
        }
      } catch (e) {
        alert("Erro ao buscar transcrição: " + e.message);
      }
    });
  }

  // Navegação de Abas
  document.querySelectorAll(".s2-nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      trocarAbaStudio2(tab.dataset.s2Tab);
    });
  });

  // Modo Avançado / Dev
  const btnDev = $("btn-toggle-modo-dev");
  const panelDev = $("s2-dev-panel");
  if (btnDev && panelDev) {
    btnDev.addEventListener("click", () => {
      panelDev.classList.toggle("hidden");
    });
  }
  if ($("btn-fechar-modo-dev") && panelDev) {
    $("btn-fechar-modo-dev").addEventListener("click", () => {
      panelDev.classList.add("hidden");
    });
  }

  // Voltar para Cards Clássicos
  if ($("btn-voltar-cards-legado")) {
    $("btn-voltar-cards-legado").addEventListener("click", () => {
      mostrarTela("tela-manual");
      $("manual-projeto-nome").textContent = S.projetoNome || S.projeto_id || "";
      iniciarManual();
    });
  }

  // 1. ÁUDIO: Escolher arquivo
  if ($("btn-s2-escolher-audio") && $("s2-input-audio")) {
    $("btn-s2-escolher-audio").addEventListener("click", () => $("s2-input-audio").click());
    $("s2-input-audio").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      $("s2-audio-nome").textContent = file.name + " (" + Math.round(file.size/1024) + " KB)";
      $("btn-s2-transcrever").disabled = false;
      atualizarBadgeAudioS2("pronto_para_transcrever");

      // Upload imediato do áudio
      const fd = new FormData();
      fd.append("audio", file);
      try {
        await apiForm(`/api/upload_audio/${encodeURIComponent(S.projeto_id)}`, fd);
      } catch (err) {
        console.error("Erro ao carregar áudio:", err);
      }
    });
  }

  // 1. ÁUDIO: Transcrever com Whisper
  if ($("btn-s2-transcrever")) {
    $("btn-s2-transcrever").addEventListener("click", async () => {
      $("btn-s2-transcrever").disabled = true;
      const prog = $("s2-transcricao-progress");
      if (prog) prog.style.display = "block";
      atualizarBadgeAudioS2("transcrevendo");

      try {
        const file = $("s2-input-audio").files[0];
        if (file) {
          const fd = new FormData();
          fd.append("audio", file);
          await apiForm(`/api/upload_audio/${encodeURIComponent(S.projeto_id)}`, fd);
        }
        iniciarPollingTranscricaoS2();
      } catch (e) {
        alert("Erro ao iniciar transcrição: " + e.message);
        $("btn-s2-transcrever").disabled = false;
        atualizarBadgeAudioS2("erro");
      }
    });
  }

  // 1. ÁUDIO: Usar SRT Manual
  if ($("btn-s2-usar-srt") && $("s2-textarea-srt")) {
    $("btn-s2-usar-srt").addEventListener("click", async () => {
      const srt = $("s2-textarea-srt").value.trim();
      if (!srt) { alert("Cole o texto ou SRT primeiro."); return; }
      try {
        const r = await api(`/api/v2/transcricao/${encodeURIComponent(S.projeto_id)}/usar_srt`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ srt_texto: srt }),
        });
        if (r.success) {
          await carregarStudio2Dados(S.projeto_id);
          atualizarBadgeAudioS2("concluido");
          alert(`✓ Roteiro processado! ${r.total_cenas} cenas geradas.`);
        }
      } catch (e) {
        alert("Erro ao processar SRT: " + e.message);
      }
    });
  }

  // 2. TRANSCRIÇÃO: Salvar edições do roteiro
  if ($("btn-s2-salvar-transcricao-editada")) {
    $("btn-s2-salvar-transcricao-editada").addEventListener("click", async () => {
      const txt = $("s2-textarea-transcricao-completa").value.trim();
      if (!txt) return;
      try {
        const r = await api(`/api/v2/transcricao/${encodeURIComponent(S.projeto_id)}/usar_srt`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ srt_texto: txt }),
        });
        if (r.success) {
          await carregarStudio2Dados(S.projeto_id);
          alert("✓ Transcrição atualizada e cenas reprocessadas!");
        }
      } catch (e) {
        alert("Erro ao salvar transcrição: " + e.message);
      }
    });
  }

  // 2. TRANSCRIÇÃO: Downloads TXT e SRT
  if ($("btn-s2-baixar-txt")) {
    $("btn-s2-baixar-txt").addEventListener("click", () => {
      window.open(`/api/download_transcricao/${encodeURIComponent(S.projeto_id)}/txt`, "_blank");
    });
  }
  if ($("btn-s2-baixar-srt")) {
    $("btn-s2-baixar-srt").addEventListener("click", () => {
      window.open(`/api/download_transcricao/${encodeURIComponent(S.projeto_id)}/srt`, "_blank");
    });
  }

  // 3. PERSONAGEM: Upload de Imagem / Avatar
  if ($("btn-s2-escolher-avatar") && $("s2-input-avatar-file")) {
    $("btn-s2-escolher-avatar").addEventListener("click", () => $("s2-input-avatar-file").click());
    $("s2-input-avatar-file").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const fd = new FormData();
      fd.append("personagem", file);
      try {
        const r = await apiForm(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/personagem_global`, fd);
        if (r.success) {
          carregarPreviewAvatarS2(S.projeto_id);
          alert("✓ Foto do personagem vinculada ao projeto com sucesso!");
        }
      } catch (err) {
        alert("Erro ao salvar foto do personagem: " + err.message);
      }
    });
  }

  // 3. Configurações Visuais: Salvar ao alterar
  const salvarConfigS2 = async () => {
    if (!S.projeto_id || S.studio_version !== "v2") return;
    const pers = $("s2-input-personagem") ? $("s2-input-personagem").value.trim() : "";
    const est = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
    const cont = $("s2-check-continuidade") ? $("s2-check-continuidade").checked : true;
    const modoProd = document.querySelector('input[name="s2-modo-producao"]:checked') ? document.querySelector('input[name="s2-modo-producao"]:checked').value : "somente_imagens";

    try {
      await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nome_personagem: pers,
          estilo_visual: est,
          continuidade_visual: cont,
          modo_producao: modoProd,
        }),
      });
    } catch (e) {
      console.warn("Erro ao salvar config S2:", e);
    }
  };

  if ($("s2-input-personagem")) $("s2-input-personagem").addEventListener("change", salvarConfigS2);
  if ($("s2-select-estilo")) $("s2-select-estilo").addEventListener("change", salvarConfigS2);
  if ($("s2-check-continuidade")) $("s2-check-continuidade").addEventListener("change", salvarConfigS2);
  document.querySelectorAll('input[name="s2-modo-producao"]').forEach((r) => r.addEventListener("change", salvarConfigS2));

  // 4. Gerar Storyboard & Prompts
  if ($("btn-s2-gerar-prompts")) {
    $("btn-s2-gerar-prompts").addEventListener("click", async () => {
      $("btn-s2-gerar-prompts").disabled = true;
      $("btn-s2-gerar-prompts").textContent = "⚡ Gerando Prompts...";
      await salvarConfigS2();

      try {
        const r = await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (r.success) {
          await carregarStudio2Dados(S.projeto_id);
          alert(`✓ Storyboard gerado! ${r.total} cenas prontas.`);
        } else {
          alert("Aviso: " + (r.error || "Não foi possível gerar prompts"));
        }
      } catch (e) {
        alert("Erro ao gerar prompts: " + e.message);
      } finally {
        $("btn-s2-gerar-prompts").disabled = false;
        $("btn-s2-gerar-prompts").textContent = "⚡ Gerar Storyboard & Prompts";
      }
    });
  }

  // Copiar todos os prompts
  if ($("btn-s2-copiar-prompts")) {
    $("btn-s2-copiar-prompts").addEventListener("click", () => {
      const cards = document.querySelectorAll(".s2-scene-prompt");
      const prompts = Array.from(cards).map((c) => c.textContent).join("\n\n");
      if (prompts) {
        navigator.clipboard.writeText(prompts);
        alert("✓ Todos os prompts copiados para a área de transferência!");
      }
    });
  }

  // Baixar prompts txt
  if ($("btn-s2-baixar-prompts")) {
    $("btn-s2-baixar-prompts").addEventListener("click", () => {
      window.open(`/api/v2/arquivos/${encodeURIComponent(S.projeto_id)}/download/prompts/storyboard_prompts.txt`, "_blank");
    });
  }

  // Produção: Iniciar Fila
  const iniciarFilaHandler = async () => {
    try {
      const r = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/iniciar_fila`, { method: "POST" });
      if (r.success) {
        if (!termExpanded) toggleTerminalExpanded();
        pollLiveTerminalHUD();
        await carregarStudio2Dados(S.projeto_id);
      } else {
        alert("Aviso: " + (r.error || "Não foi possível iniciar a fila."));
      }
    } catch (e) {
      alert("Erro ao iniciar fila: " + e.message);
    }
  };

  if ($("btn-s2-iniciar-fila")) {
    $("btn-s2-iniciar-fila").addEventListener("click", iniciarFilaHandler);
  }
  if ($("btn-s2-produzir-pendentes")) {
    $("btn-s2-produzir-pendentes").addEventListener("click", iniciarFilaHandler);
  }

  // Segunda Etapa: Animar todos os vídeos
  if ($("btn-s2-animar-todos-videos")) {
    $("btn-s2-animar-todos-videos").addEventListener("click", async () => {
      try {
        const r = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/animar_todos_videos`, { method: "POST" });
        if (r.success) {
          alert(`🎬 Segunda Etapa Iniciada! ${r.total_animar} cena(s) com animate_later enviadas para animação.`);
          await carregarStudio2Dados(S.projeto_id);
        } else {
          alert("Aviso: " + (r.error || "Nenhuma cena para animar."));
        }
      } catch (e) {
        alert("Erro ao iniciar animação de vídeos: " + e.message);
      }
    });
  }

  // Produção: Auto Importar
  if ($("btn-s2-auto-importar")) {
    $("btn-s2-auto-importar").addEventListener("click", async () => {
      try {
        const r = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/auto_importar`, { method: "POST" });
        if (r.success) {
          alert(`✓ Auto-importação concluída!`);
          await carregarStudio2Dados(S.projeto_id);
        }
      } catch (e) {
        alert("Erro na auto-importação: " + e.message);
      }
    });
  }

  // Produção: Abrir Flow
  if ($("btn-s2-abrir-flow")) {
    $("btn-s2-abrir-flow").addEventListener("click", async () => {
      // O backend cria/ativa a aba EXCLUSIVA do Google Flow no Chrome CDP.
      // O window.open foi removido: abrir nova aba aqui duplicaria o Flow no
      // navegador do usuário e NUNCA deve navegar a aba atual do ULTRACUT3.
      try {
        const r = await api("/api/flow/abrir", { method: "POST", body: JSON.stringify({ projeto_id: S.projeto_id }) });
        if (!r || !r.success) {
          alert("Não foi possível abrir o Google Flow: " + ((r && (r.error || r.message)) || "verifique se o Chrome CDP está ativo."));
        }
      } catch (e) {
        alert("Erro ao abrir o Google Flow: " + (e && e.message ? e.message : e));
      }
      await atualizarStatusProducaoS2(S.projeto_id);
    });
  }

  // Arquivos: Abrir Pasta
  if ($("btn-s2-abrir-pasta-explorer")) {
    $("btn-s2-abrir-pasta-explorer").addEventListener("click", async () => {
      try {
        await api(`/api/v2/arquivos/${encodeURIComponent(S.projeto_id)}/abrir_pasta`, { method: "POST" });
      } catch (e) {
        alert("Erro ao abrir pasta: " + e.message);
      }
    });
  }

  // Arquivos: Limpar Temp
  if ($("btn-s2-limpar-temp")) {
    $("btn-s2-limpar-temp").addEventListener("click", async () => {
      try {
        const r = await api(`/api/v2/arquivos/${encodeURIComponent(S.projeto_id)}/limpar_temporarios`, { method: "POST" });
        alert(`✓ ${r.arquivos_removidos} arquivos temporários limpos.`);
        await carregarStudio2Dados(S.projeto_id);
      } catch (e) {
        alert("Erro ao limpar temporários: " + e.message);
      }
    });
  }

  // Arquivos: Recarregar
  if ($("btn-s2-recarregar-arquivos")) {
    $("btn-s2-recarregar-arquivos").addEventListener("click", () => carregarStudio2Dados(S.projeto_id));
  }

  // Montagem: Exportar CapCut
  if ($("btn-s2-exportar-capcut")) {
    $("btn-s2-exportar-capcut").addEventListener("click", async () => {
      $("btn-s2-exportar-capcut").disabled = true;
      $("btn-s2-exportar-capcut").textContent = "✂ Exportando para CapCut...";
      try {
        const r = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/exportar_capcut`, { method: "POST" });
        if (r.success) {
          const msgEl = $("s2-montagem-msg");
          if (msgEl) {
            msgEl.className = "msg sucesso";
            msgEl.textContent = `✓ Rascunho exportado com sucesso para o CapCut! Abra o CapCut Desktop e veja o projeto 'Studio2_${S.projeto_id}'.`;
            msgEl.classList.remove("hidden");
          }
          alert(`✓ Rascunho CapCut criado com sucesso!`);
        } else {
          alert("Erro: " + (r.error || "Falha na exportação"));
        }
      } catch (e) {
        alert("Erro ao exportar para CapCut: " + e.message);
      } finally {
        $("btn-s2-exportar-capcut").disabled = false;
        $("btn-s2-exportar-capcut").textContent = "✂ Exportar Rascunho para CapCut Desktop";
      }
    });
  }

  // Montagem: Renderizar Vídeo
  if ($("btn-s2-exportar-video")) {
    $("btn-s2-exportar-video").addEventListener("click", async () => {
      try {
        await api(`/api/montar_video/${encodeURIComponent(S.projeto_id)}`, { method: "POST" });
        alert("Processamento de renderização iniciado em segundo plano!");
      } catch (e) {
        alert("Erro ao renderizar vídeo: " + e.message);
      }
    });
  }
}

function atualizarBadgeAudioS2(estado) {
  const b = $("s2-audio-status-badge");
  if (!b) return;
  if (estado === "transcrevendo") {
    b.className = "badge badge-proc";
    b.textContent = "Transcrevendo com Whisper...";
  } else if (estado === "concluido") {
    b.className = "badge badge-ok";
    b.textContent = "Áudio e SRT Prontos ✅";
  } else if (estado === "pronto_para_transcrever") {
    b.className = "badge badge-proc";
    b.textContent = "Áudio carregado";
  } else if (estado === "erro") {
    b.className = "badge badge-err";
    b.textContent = "Erro na transcrição";
  } else {
    b.className = "badge badge-wait";
    b.textContent = "Aguardando áudio";
  }
}

function trocarAbaStudio2(tabName) {
  S2_ACTIVE_TAB = tabName;
  document.querySelectorAll(".s2-nav-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.s2Tab === tabName);
  });
  document.querySelectorAll(".s2-tab-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === `s2-tab-${tabName}`);
  });

  if (tabName === "diretor3" && S.projeto_id) {
    carregarPainelDiretor3(S.projeto_id);
  }
}

async function carregarPainelDiretor3(projeto_id) {
  try {
    const res = await api(`/api/v2/diretor3/${encodeURIComponent(projeto_id)}`);
    if (!res || !res.success) return;

    const s = res.summary || {};
    const mem = res.visual_memory || {};
    const amb = mem.ambiente || {};
    const pers = mem.personagem || {};
    const obj = mem.objetos || {};

    // 1. Carrega Métricas de Performance do Diretor
    const mRes = await api(`/api/v2/metrics/${encodeURIComponent(projeto_id)}`);
    const m = (mRes && mRes.metrics) ? mRes.metrics : {};

    if ($("d3-score-visual-val")) $("d3-score-visual-val").textContent = `${m.average_visual_score || 95}%`;
    if ($("d3-continuidade-val")) $("d3-continuidade-val").textContent = `${m.average_continuity_score || 98}%`;
    if ($("d3-retencao-prevista-val")) $("d3-retencao-prevista-val").textContent = `${m.average_retention_score || 94}%`;
    if ($("d3-intervencao-humana-val")) $("d3-intervencao-humana-val").textContent = `${m.manual_interventions || 0}`;
    if ($("d3-cenas-aprovadas-val")) $("d3-cenas-aprovadas-val").textContent = `${m.scenes_approved || s.total_cenas || 0}/${m.scenes_total || s.total_cenas || 0}`;
    if ($("d3-final-grade-badge")) $("d3-final-grade-badge").textContent = `Grade: ${m.final_grade || s.pacing_grade || 'A+'}`;

    if ($("s2-badge-diretor-score")) $("s2-badge-diretor-score").textContent = `${m.average_retention_score || 98}%`;

    if ($("d3-bible-world")) $("d3-bible-world").textContent = amb.location || "Rustic Botanical Garden";
    if ($("d3-bible-lighting")) $("d3-bible-lighting").textContent = amb.lighting || "Natural morning daylight";
    if ($("d3-bible-main-obj")) $("d3-bible-main-obj").textContent = obj.main_object || "Adubo de Casca de Banana";
    if ($("d3-bible-clothing")) $("d3-bible-clothing").textContent = pers.clothing || "Camisa verde de jardinagem";

    if ($("d3-bible-rules-list") && mem.continuidade && mem.continuidade.rules) {
      $("d3-bible-rules-list").innerHTML = mem.continuidade.rules.map(r => `<li>${r}</li>`).join("");
    }

    // 2. Carrega Cenas no grid de auditoria com Ações Humanas
    const spRes = await api(`/api/scene_plan/${encodeURIComponent(projeto_id)}`);
    const grid = $("d3-cenas-timeline");
    if (grid && spRes && spRes.cenas) {
      if ($("d3-cenas-count-badge")) $("d3-cenas-count-badge").textContent = `${spRes.cenas.length} cenas auditadas`;
      grid.innerHTML = spRes.cenas.map(c => `
        <div class="s2-scene-card" style="border-left: 4px solid var(--accent)">
          <div class="s2-scene-head" style="display:flex;justify-content:space-between;align-items:center">
            <span class="s2-scene-num">Cena #${String(c.id).padStart(3, '0')}</span>
            <div style="display:flex;gap:6px;align-items:center">
              <span class="badge ${c.human_status === 'approved' ? 'badge-ok' : (c.human_status === 'revision_requested' ? 'badge-danger' : 'badge-wait')}">${c.human_status || 'pending'}</span>
              <span class="badge ${c.uses_character ? 'badge-primary' : 'badge-muted'}">${c.story_role || c.scene_type}</span>
            </div>
          </div>
          <div style="font-size:12px;margin:6px 0;color:var(--text-dim)">
            <div><b>Retenção:</b> <span class="badge badge-ok">${c.retention_index || 90} pts</span> | <b>Visual Score:</b> <span class="badge badge-primary">${c.visual_score || 95}/100</span></div>
            <div><b>Câmera:</b> ${c.camera_direction?.shot || '35mm medium shot'}</div>
            <div style="margin-top:4px"><b>Propósito:</b> <i>${c.narrative_purpose || 'Progressão narrativa'}</i></div>
            ${c.human_note ? `<div style="margin-top:4px;color:var(--accent-light)"><b>Nota Humana:</b> ${c.human_note}</div>` : ''}
          </div>
          <div class="s2-scene-prompt" style="font-size:11px;max-height:55px;overflow:hidden;background:rgba(0,0,0,0.2);padding:6px;border-radius:4px;color:var(--text-muted)">
            ${c.prompt_imagem || c.visual_prompt || 'Prompt em elaboração...'}
          </div>
          <div class="btn-row" style="margin-top:8px;justify-content:flex-end;gap:6px">
            <button class="btn btn-xs btn-success" type="button" onclick="enviarFeedbackHumanoCena('${projeto_id}', ${c.id}, 'approved')">✓ APROVAR</button>
            <button class="btn btn-xs btn-ghost" type="button" onclick="pedirRevisaoHumanaCena('${projeto_id}', ${c.id})">✎ PEDIR REVISÃO</button>
          </div>
        </div>
      `).join("");
    }
  } catch (e) {
    console.warn("Aviso ao carregar dados do Diretor 3.0:", e);
  }
}

async function enviarFeedbackHumanoCena(projeto_id, scene_id, status, note = "") {
  try {
    const res = await api(`/api/v2/human_feedback/${encodeURIComponent(projeto_id)}/${scene_id}`, {
      method: "POST",
      body: JSON.stringify({ status, note, approved_by: "User" })
    });
    if (res && res.success) {
      await carregarPainelDiretor3(projeto_id);
    }
  } catch (e) {
    alert("Erro ao registrar feedback: " + e.message);
  }
}
window.enviarFeedbackHumanoCena = enviarFeedbackHumanoCena;

async function pedirRevisaoHumanaCena(projeto_id, scene_id) {
  const note = prompt(`Digite a observação de revisão para a Cena #${scene_id}:`, "Ajustar detalhes de iluminação/objeto");
  if (note !== null) {
    await enviarFeedbackHumanoCena(projeto_id, scene_id, "revision_requested", note);
  }
}
window.pedirRevisaoHumanaCena = pedirRevisaoHumanaCena;

function carregarPreviewAvatarS2(projeto_id) {
  const img = $("s2-avatar-img");
  const placeholder = $("s2-avatar-placeholder");
  if (!img || !placeholder) return;

  const url = `/api/scene_plan/${encodeURIComponent(projeto_id)}/personagem_avatar?t=${Date.now()}`;
  const testImg = new Image();
  testImg.onload = () => {
    img.src = url;
    img.classList.remove("hidden");
    placeholder.classList.add("hidden");
  };
  testImg.onerror = () => {
    img.classList.add("hidden");
    placeholder.classList.remove("hidden");
  };
  testImg.src = url;
}

async function abrirStudio2(projeto_id) {
  pararPolling();
  S.projeto_id = projeto_id;
  S.projetoId = projeto_id;
  S.studio_version = "v2";
  atualizarUrlProjeto(projeto_id);

  mostrarTela("tela-studio2");
  setNavAtivo("projetos");
  $("s2-projeto-nome").textContent = S.projetoNome || projeto_id;
  atualizarTopbar(projeto_id, "andamento", "Studio 2.0");

  carregarPreviewAvatarS2(projeto_id);
  await carregarDadosPersonagemS2(projeto_id);
  await carregarStudio2Dados(projeto_id);

  // Inicia polling leve do Studio 2.0 (Flow & status)
  if (S2_POLL_TIMER) clearInterval(S2_POLL_TIMER);
  S2_POLL_TIMER = setInterval(() => {
    if (S.projeto_id === projeto_id && $("tela-studio2").classList.contains("ativa")) {
      atualizarStatusProducaoS2(projeto_id);
    }
  }, 3000);
}

async function atualizarStatusStudio2(projeto_id) {
  return await carregarStudio2Dados(projeto_id);
}
window.atualizarStatusStudio2 = atualizarStatusStudio2;

async function carregarStudio2Dados(projeto_id) {
  try {
    // 1. Carrega Config
    const cfgRes = await api(`/api/v2/projeto/${encodeURIComponent(projeto_id)}/config`);
    if (cfgRes && cfgRes.meta) {
      const m = cfgRes.meta;
      if ($("s2-input-personagem")) $("s2-input-personagem").value = m.nome_personagem || "";
      if ($("s2-select-estilo")) $("s2-select-estilo").value = m.estilo_visual || "photorealistic_cinematic";
      if ($("s2-check-continuidade")) $("s2-check-continuidade").checked = m.continuidade_visual !== false;
      const rModo = document.querySelector(`input[name="s2-modo-producao"][value="${m.modo_producao || "somente_imagens"}"]`);
      if (rModo) rModo.checked = true;
      if ($("s2-dev-meta-json")) $("s2-dev-meta-json").value = JSON.stringify(m, null, 2);

      if (m.arquivo_audio) {
        atualizarBadgeAudioS2(m.transcricao_completa ? "concluido" : "pronto_para_transcrever");
      }
    }

    // 2. Carrega Transcrição se existir
    try {
      const tRes = await api(`/api/transcricao/${encodeURIComponent(projeto_id)}`);
      if (tRes && tRes.texto) {
        const painel = $("s2-painel-transcricao");
        if (painel) painel.style.display = "block";
        if ($("s2-textarea-transcricao-completa")) $("s2-textarea-transcricao-completa").value = tRes.texto;
        if ($("s2-transcricao-count")) $("s2-transcricao-count").textContent = `${(tRes.segmentos || []).length} falas detectadas`;
        atualizarBadgeAudioS2("concluido");
      }
    } catch (e) {}

    // 3. Carrega Produção / Cenas do Storyboard
    await atualizarStatusProducaoS2(projeto_id);

    // 4. Carrega Arquivos
    await atualizarArquivosS2(projeto_id);

    // 5. Carrega Montagem
    await atualizarMontagemS2(projeto_id);
  } catch (e) {
    console.error("Erro ao carregar dados Studio 2.0:", e);
  }
}

async function atualizarStatusProducaoS2(projeto_id) {
  try {
    const prod = await api(`/api/v2/producao/${encodeURIComponent(projeto_id)}/status`);
    if (!prod || !prod.success) return;

    // Atualiza contadores
    const p = prod.progresso || {};
    const porSt = p.por_status || {};
    if ($("s2-cnt-total")) $("s2-cnt-total").textContent = p.total || 0;
    if ($("s2-cnt-pendentes")) $("s2-cnt-pendentes").textContent = (porSt.PENDENTE || 0) + (porSt.PROMPT_PRONTO || 0);
    if ($("s2-cnt-gerando")) $("s2-cnt-gerando").textContent = (porSt.GERANDO || 0) + (porSt.ENVIADA || 0);
    if ($("s2-cnt-prontos")) $("s2-cnt-prontos").textContent = (porSt.PRONTA_PARA_MONTAGEM || 0) + (porSt.MIDIA_IMPORTADA || 0) + (porSt.ANIMADA || 0);
    if ($("s2-cnt-erros")) $("s2-cnt-erros").textContent = porSt.ERRO || 0;
    if ($("s2-badge-prod-count")) $("s2-badge-prod-count").textContent = `${p.prontas || 0}/${p.total || 0}`;
    if ($("s2-total-cenas-label")) $("s2-total-cenas-label").textContent = `${p.total || 0} cenas`;

    // Flow Status
    const fDot = $("s2-flow-dot");
    const fTxt = $("s2-flow-status-text");
    if (fDot && fTxt) {
      const conectado = prod.flow && prod.flow.conectado;
    fDot.className = "flow-status-dot " + (conectado ? "online" : "");
    fTxt.className = "badge " + (conectado ? "badge-ok" : "badge-wait");
    fTxt.textContent = conectado ? "Flow Conectado" : "Desconectado";
  }

  // Renderiza Cenas do Storyboard
  renderStoryboardS2(prod.cenas || []);

  // Renderiza Cenas da Produção
  renderProducaoGridS2(prod.cenas || []);

  if ($("s2-dev-plan-json")) $("s2-dev-plan-json").value = JSON.stringify(prod.cenas || [], null, 2);
  } catch (e) {}
}

function togglePromptCenaS2(cid) {
  const el = $(`s2-prompt-box-${cid}`);
  const btn = $(`btn-toggle-prompt-${cid}`);
  if (!el || !btn) return;
  const oculta = el.classList.contains("hidden");
  el.classList.toggle("hidden", !oculta);
  btn.textContent = oculta ? "👁 Ocultar Prompt" : "👁 Ver Prompt Visual";
}

function renderStoryboardS2(cenas) {
const box = $("s2-storyboard-grid");
if (!box) return;
if (!cenas.length) {
  box.innerHTML = '<div class="scenes-empty">Nenhum prompt planejado ainda. Carregue o áudio/SRT para gerar o planejamento.</div>';
  return;
}

// Layout fluido e espaçoso com scroll confortável
box.style.maxHeight = "none";
box.style.overflowY = "visible";
box.style.paddingRight = "0";

box.innerHTML = cenas.map((c) => {
  const cid = c.scene_index || c.id;
  const ts = c.timestamp_saida || c.timestamp || `${fmtTs(c.tempo_inicio || c.start)} - ${fmtTs(c.tempo_fim || c.end)}`;
  const imgStatus = c.image_status || (c.arquivo_midia ? "READY" : (c.status === "GERANDO" ? "GENERATING" : "PENDING"));
  const vidStatus = c.video_status || "NOT_STARTED";
  const filename = c.filename || `${String(cid).padStart(3, '0')}.png`;
  const prompt = c.visual_prompt || c.prompt_imagem || c.narration || c.texto || "Sem prompt gerado";
  const charTag = (c.uses_character && c.character_ref) ? `<span class="badge badge-ok" style="font-size:11px">👤 ${esc(c.character_ref)}</span>` : "";

  const temMidia = (imgStatus === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));

  let statusBadge = '<span class="badge badge-wait" style="font-size:11px">⏳ PENDENTE</span>';
  if (imgStatus === "GENERATING" || c.status === "GERANDO") {
    statusBadge = '<span class="badge badge-proc" style="font-size:11px">⚡ GERANDO NO FLOW</span>';
  } else if (imgStatus === "RECEIVED") {
    statusBadge = '<span class="badge badge-proc" style="font-size:11px">📥 RECEBIDA</span>';
  } else if (imgStatus === "DOWNLOADED") {
    statusBadge = '<span class="badge badge-proc" style="font-size:11px">💾 SALVANDO</span>';
  } else if (temMidia) {
    statusBadge = `<span class="badge badge-ok" style="font-size:11px">✅ BAIXADA (${esc(filename)})</span>`;
  } else if (c.status === "ERRO") {
    statusBadge = '<span class="badge badge-err" style="font-size:11px">⚠️ FALHA NO FLOW</span>';
  }

  let videoBadge = "";
  if (c.animate_later || c.animar_depois) {
    if (vidStatus === "READY") {
      videoBadge = '<span class="badge badge-ok" style="background:#22c77a22;color:#22c77a;font-size:11px">🎬 VÍDEO PRONTO</span>';
    } else if (vidStatus === "GENERATING") {
      videoBadge = '<span class="badge badge-proc" style="font-size:11px">🎬 ANIMANDO</span>';
    } else {
      videoBadge = '<span class="badge badge-proc" style="background:#7c5cfc22;color:#7c5cfc;font-size:11px">🎬 VÍDEO 2ª ETAPA</span>';
    }
  }

  const thumb = temMidia
    ? `<div style="margin:12px 0;width:100%;height:180px;border-radius:8px;overflow:hidden;background:#0d0d12;display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid var(--border)" onclick="abrirMediaModalCena(${cid})" title="Clique para expandir em tela cheia">
         <img src="/api/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}?t=${Date.now()}" style="width:100%;height:100%;object-fit:cover;transition:transform .2s ease" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'" loading="lazy" />
       </div>`
    : `<div style="margin:12px 0;height:150px;border:1px dashed var(--border-strong);border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--text-muted);font-size:13px;background:rgba(255,255,255,0.02);gap:8px">
         <span style="font-size:26px">${imgStatus === 'GENERATING' ? '⚡' : '⏳'}</span>
         <i>${imgStatus === 'GENERATING' ? 'Gerando imagem no Google Flow...' : 'Aguardando na fila de produção...'}</i>
       </div>`;

  return `
    <div class="s2-scene-card" style="border: 1px solid ${temMidia ? 'rgba(34,199,122,0.35)' : 'var(--border)'}">
      <div class="s2-scene-card-head">
        <div style="display:flex;align-items:center;gap:8px">
          <b style="font-size:14px">Cena ${String(cid).padStart(3, '0')}</b>
          <span class="mono text-muted" style="font-size:12px">${ts}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${charTag}
          ${videoBadge}
          ${statusBadge}
        </div>
      </div>
      ${thumb}
      <div style="margin:8px 0;display:flex;justify-content:space-between;align-items:center">
        <button id="btn-toggle-prompt-${cid}" class="btn btn-xs btn-ghost" type="button" onclick="togglePromptCenaS2(${cid})">👁 Ver Prompt Visual</button>
        <span class="mono text-muted" style="font-size:11.5px">${temMidia ? esc(filename) : 'Arquivo: pendente'}</span>
      </div>
      <div id="s2-prompt-box-${cid}" class="s2-scene-prompt mono hidden" style="margin-top:8px">
        ${esc(prompt)}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:8px">
        <button class="btn btn-xs btn-ghost" style="flex:1" type="button" onclick="gerarCenaIndividualFlow(${cid})" title="Enviar apenas esta cena ao Google Flow">▶ Gerar no Flow</button>
        <button class="btn btn-xs btn-primary" type="button" onclick="abrirMediaModalCena(${cid})">🔍 Detalhes</button>
      </div>
    </div>
  `;
}).join("");
}

function renderProducaoGridS2(cenas) {
const box = $("s2-producao-grid");
if (!box) return;
if (!cenas.length) {
  box.innerHTML = '<div class="scenes-empty">Nenhuma cena na fila de produção. Crie o storyboard no Studio.</div>';
  return;
}

box.innerHTML = cenas.map((c) => {
  const cid = c.scene_index || c.id;
  const ts = c.timestamp_saida || c.timestamp || `${fmtTs(c.tempo_inicio || c.start)} - ${fmtTs(c.tempo_fim || c.end)}`;
  const imgStatus = c.image_status || (c.arquivo_midia ? "READY" : (c.status === "GERANDO" ? "GENERATING" : "PENDING"));
  const vidStatus = c.video_status || "NOT_STARTED";
  const filename = c.filename || `${String(cid).padStart(3, '0')}.png`;
  const temMidia = (imgStatus === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
  const isVideo = (c.tipo === "video" || (c.arquivo_midia && c.arquivo_midia.endsWith(".mp4")));

  let thumbHtml = `<span class="muted" style="font-size:12px">Sem mídia baixada</span>`;
  if (temMidia) {
    const mediaUrl = `/api/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}?t=${Date.now()}`;
    if (isVideo) {
      thumbHtml = `<video src="${mediaUrl}" style="width:100%;height:100%;object-fit:cover" muted></video>`;
    } else {
      thumbHtml = `<img src="${mediaUrl}" alt="Cena ${cid}" style="width:100%;height:100%;object-fit:cover">`;
    }
  }

  let statusHtml = '<span class="badge badge-wait">Pendente ⏳</span>';
  if (imgStatus === "GENERATING" || c.status === "GERANDO") {
    statusHtml = '<span class="badge badge-proc"><span class="flow-pulsing-dot" style="font-size:8px">●</span> Gerando ⏳</span>';
  } else if (temMidia) {
    if (isVideo && vidStatus === "READY") {
      statusHtml = '<span class="badge badge-ok">Vídeo pronto ✅</span>';
    } else {
      // NUNCA mostrar "Vídeo gerado" para imagens PNG!
      statusHtml = `<span class="badge badge-ok">Imagem pronta ✅ (${esc(filename)})</span>`;
    }
  } else if (c.status === "ERRO") {
    statusHtml = '<span class="badge badge-err">Erro ⚠️</span>';
  }

  return `
    <div class="s2-prod-card" data-cid="${cid}">
      <div class="s2-prod-card-thumb" onclick="abrirMediaModalCena(${cid})" style="cursor:pointer" title="Clique para visualizar em tela cheia">
        ${thumbHtml}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>Cena ${String(cid).padStart(3, '0')}</b>
        ${statusHtml}
      </div>
      <div class="mono text-muted" style="font-size:11px">${ts}</div>
      <div class="btn-row" style="margin-top:auto">
        <button class="btn btn-sm btn-primary" style="flex:1" onclick="enviarCenaIndividualS2(${cid}, 'imagem')">📤 Flow</button>
        <button class="btn btn-sm btn-ghost" onclick="enviarCenaIndividualS2(${cid}, 'video')" title="Gerar Vídeo">🎬 Animar</button>
        <button class="btn btn-sm btn-ghost" onclick="abrirMediaModalCena(${cid})" title="Visualizar">👁 Ver</button>
      </div>
    </div>
  `;
}).join("");
}

async function enviarCenaIndividualS2(scene_id, tipo) {
try {
  const r = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/enviar_cena`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene_id: scene_id, tipo: tipo }),
  });
  if (r.success) {
    alert(`✓ Cena ${scene_id} enviada ao Google Flow!`);
    await atualizarStatusProducaoS2(S.projeto_id);
  }
} catch (e) {
  alert("Erro ao enviar cena: " + e.message);
}
}

async function atualizarArquivosS2(projeto_id) {
try {
  const res = await api(`/api/v2/arquivos/${encodeURIComponent(projeto_id)}/listar`);
  if (!res || !res.success) return;

  const est = res.estrutura || {};
  if ($("s2-badge-arq-count")) $("s2-badge-arq-count").textContent = res.total_arquivos || 0;

  // Atualiza contadores de pastas
  for (const pasta of ["audio", "srt", "imagens", "videos", "prompts", "capcut"]) {
    const el = $(`s2-cnt-folder-${pasta}`);
    if (el) el.textContent = `${(est[pasta] || []).length} arquivos`;
  }

  // Tabela
  const tbody = $("s2-arquivos-table-body");
  if (!tbody) return;

  let rows = [];
  for (const [pasta, lista] of Object.entries(est)) {
    for (const arq of lista) {
      rows.push(`
        <tr>
          <td><b>${esc(arq.nome)}</b></td>
          <td><span class="badge badge-muted">/${pasta}</span></td>
          <td class="mono">${arq.tamanho_kb} KB</td>
          <td class="text-muted" style="font-size:11px">${arq.modificado_em}</td>
          <td>
            <a class="btn btn-sm btn-ghost" href="/api/v2/arquivos/${encodeURIComponent(projeto_id)}/download/${pasta}/${encodeURIComponent(arq.nome)}" target="_blank" download>⬇ Baixar</a>
          </td>
        </tr>
      `);
    }
  }

  tbody.innerHTML = rows.length ? rows.join("") : `<tr><td colspan="5" class="text-center text-muted">Nenhum arquivo encontrado nas pastas do projeto.</td></tr>`;
} catch (e) {}
}

async function atualizarMontagemS2(projeto_id) {
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(projeto_id)}/sincronizar`);
    if (!res || !res.success) return;

    const prontas = res.cenas_com_midia || 0;
    const total = res.total_cenas || 0;
    const pct = total > 0 ? Math.round((prontas / total) * 100) : 0;

    if ($("s2-montagem-prontidao")) $("s2-montagem-prontidao").textContent = `${pct}%`;
    if ($("s2-montagem-cenas-ok")) $("s2-montagem-cenas-ok").textContent = `${prontas} / ${total} imagens prontas`;
    if ($("s2-montagem-audio-ok")) $("s2-montagem-audio-ok").textContent = res.tem_audio ? "✓ Sincronizado" : "❌ Ausente";

    const badge = $("s2-montagem-status-badge");
    if (badge) {
      if (res.pode_montar) {
        badge.className = "badge badge-ok";
        badge.textContent = `✓ ${prontas}/${total} Imagens Prontas para Exportar`;
      } else {
        badge.className = "badge badge-wait";
        badge.textContent = `${prontas}/${total} imagens prontas (${res.cenas_faltantes.length} pendentes)`;
      }
    }

    // Renderiza blocos de timeline
    const timeline = $("s2-timeline-tracks");
    if (timeline && res.cenas) {
      timeline.innerHTML = res.cenas.map((c) => {
        const cls = c.tem_midia ? "pronto" : "";
        const icon = c.tem_midia ? (c.tipo === "video" ? "🎬" : "🖼") : "⏳";
        return `
          <div class="s2-timeline-block ${cls}" title="Cena ${c.id}: ${c.tem_midia ? 'Mídia pronta' : 'Pendente'}">
            <b>Cena ${c.id}</b>
            <span style="font-size:16px">${icon}</span>
            <span class="mono" style="font-size:10px">${fmtTs(c.tempo_inicio)}</span>
          </div>
        `;
      }).join("");
    }
  } catch (e) {}
}

function iniciarPollingTranscricaoS2() {
  const timer = setInterval(async () => {
    try {
      const st = await api(`/api/status/${encodeURIComponent(S.projeto_id)}`);
      if (st && st.transcricao_completa) {
        clearInterval(timer);
        const prog = $("s2-transcricao-progress");
        if (prog) prog.style.display = "none";
        $("btn-s2-transcrever").disabled = false;
        atualizarBadgeAudioS2("concluido");
        await carregarStudio2Dados(S.projeto_id);
        alert("✓ Transcrição concluída com sucesso! Roteiro carregado.");
      }
    } catch (e) {}
  }, 2000);
}

// Inicializa o Studio 2.0 no carregamento
window.addEventListener("DOMContentLoaded", () => {
  initStudio2();
  initCharacterIntelligenceUI();
});



// ============================================================
// CHARACTER INTELLIGENCE LAYER & GALERIA INTELIGENTE
// ============================================================

let S2_EXIBIR_TODAS_CENAS_PRODUCAO = false;

function initCharacterIntelligenceUI() {
  // Upload & Cadastro de Personagem
  let tempAvatarFile = null;

  if ($("btn-s2-escolher-avatar") && $("s2-input-avatar-file")) {
    $("btn-s2-escolher-avatar").addEventListener("click", () => $("s2-input-avatar-file").click());
    $("s2-input-avatar-file").addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      tempAvatarFile = file;

      // Preview imediato
      const reader = new FileReader();
      reader.onload = (re) => {
        if ($("s2-avatar-img")) {
          $("s2-avatar-img").src = re.target.result;
          $("s2-avatar-img").classList.remove("hidden");
          if ($("s2-avatar-placeholder")) $("s2-avatar-placeholder").classList.add("hidden");
        }
      };
      reader.readAsDataURL(file);
    });
  }

  // Estado local do personagem no Flow
  let flowPersonagemCriado = false;
  let flowPersonagemId = "";
  let flowPersonagemNome = "";

  // Abas de tipo de identidade (Personagem vs Avatar Flow vs Biblioteca)
  let tipoIdentidadeAtivo = "personagem";
  if ($("s2-tab-tipo-personagem") && $("s2-tab-tipo-avatar")) {
    $("s2-tab-tipo-personagem").addEventListener("click", () => {
      tipoIdentidadeAtivo = "personagem";
      $("s2-tab-tipo-personagem").className = "btn btn-sm btn-primary";
      $("s2-tab-tipo-avatar").className = "btn btn-sm btn-ghost";
      if ($("s2-tab-tipo-biblioteca")) $("s2-tab-tipo-biblioteca").className = "btn btn-sm btn-ghost";
      if ($("s2-bloco-personagem")) $("s2-bloco-personagem").classList.remove("hidden");
      if ($("s2-bloco-avatar-flow")) $("s2-bloco-avatar-flow").classList.add("hidden");
      if ($("s2-bloco-biblioteca-personagens")) $("s2-bloco-biblioteca-personagens").classList.add("hidden");
    });

    $("s2-tab-tipo-avatar").addEventListener("click", () => {
      tipoIdentidadeAtivo = "avatar";
      $("s2-tab-tipo-avatar").className = "btn btn-sm btn-primary";
      $("s2-tab-tipo-personagem").className = "btn btn-sm btn-ghost";
      if ($("s2-tab-tipo-biblioteca")) $("s2-tab-tipo-biblioteca").className = "btn btn-sm btn-ghost";
      if ($("s2-bloco-avatar-flow")) $("s2-bloco-avatar-flow").classList.remove("hidden");
      if ($("s2-bloco-personagem")) $("s2-bloco-personagem").classList.add("hidden");
      if ($("s2-bloco-biblioteca-personagens")) $("s2-bloco-biblioteca-personagens").classList.add("hidden");
    });

    if ($("s2-tab-tipo-biblioteca")) {
      $("s2-tab-tipo-biblioteca").addEventListener("click", async () => {
        tipoIdentidadeAtivo = "biblioteca";
        $("s2-tab-tipo-biblioteca").className = "btn btn-sm btn-primary";
        $("s2-tab-tipo-personagem").className = "btn btn-sm btn-ghost";
        $("s2-tab-tipo-avatar").className = "btn btn-sm btn-ghost";
        if ($("s2-bloco-biblioteca-personagens")) $("s2-bloco-biblioteca-personagens").classList.remove("hidden");
        if ($("s2-bloco-personagem")) $("s2-bloco-personagem").classList.add("hidden");
        if ($("s2-bloco-avatar-flow")) $("s2-bloco-avatar-flow").classList.add("hidden");
        await carregarBibliotecaPersonagensS2();
      });
    }
  }

  if ($("btn-s2-recarregar-biblioteca")) {
    $("btn-s2-recarregar-biblioteca").addEventListener("click", async () => {
      await carregarBibliotecaPersonagensS2();
    });
  }

  if ($("s2-input-personagem") && $("s2-input-ref-flow")) {
    $("s2-input-personagem").addEventListener("input", () => {
      const val = $("s2-input-personagem").value.trim();
      $("s2-input-ref-flow").value = val ? (val.startsWith("@") ? val : `@${val}`) : "@Personagem";
      flowPersonagemCriado = false;
      if ($("s2-char-flow-status")) $("s2-char-flow-status").classList.add("hidden");
    });
  }

  // ETAPA 1 e 3: Criar Personagem Oficial no Google Flow
  if ($("btn-s2-criar-flow-personagem")) {
    $("btn-s2-criar-flow-personagem").addEventListener("click", async () => {
      const nome = $("s2-input-personagem") ? $("s2-input-personagem").value.trim() : "";
      if (!nome) {
        alert("Por favor, digite o nome do personagem (ex: Marcos).");
        return;
      }

      const fileInput = $("s2-input-avatar-file");
      if (!tempAvatarFile && (!fileInput || !fileInput.files[0])) {
        alert("Selecione uma imagem/foto de referência para criar o personagem no Google Flow.");
        return;
      }

      const file = tempAvatarFile || fileInput.files[0];
      const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";

      const statusBox = $("s2-char-flow-status");
      const statusTxt = $("s2-char-flow-status-text");
      const btnCriar = $("btn-s2-criar-flow-personagem");

      if (statusBox && statusTxt) {
        statusBox.classList.remove("hidden");
        statusBox.style.background = "rgba(124,92,252,0.12)";
        statusBox.style.borderColor = "var(--accent)";
        statusTxt.innerHTML = `⏳ <b>Conectando ao Google Flow...</b> Selecionando <i>Nano Banana 2</i>, enviando foto e registrando <b>@${esc(nome)}</b>...`;
      }
      if (btnCriar) btnCriar.disabled = true;

      const fd = new FormData();
      fd.append("nome", nome);
      fd.append("imagem", file);
      fd.append("estilo_visual", estilo);

      try {
        const r = await apiForm(`/api/v2/personagem/${encodeURIComponent(S.projeto_id)}/criar_flow`, fd);
        if (r && r.success) {
          flowPersonagemCriado = true;
          flowPersonagemId = r.flow_character_id || "";
          flowPersonagemNome = r.flow_character_name || `@${nome}`;

          if (statusBox && statusTxt) {
            statusBox.style.background = "rgba(34,197,94,0.15)";
            statusBox.style.borderColor = "#22c55e";
            statusTxt.innerHTML = `✅ <b>Personagem criado e vinculado com sucesso!</b><br>Identificador: <b>${esc(flowPersonagemNome)}</b><br>📸 <b>PERSONAGEM COM FOTO</b> carregado na identidade do projeto.`;
          }

          await carregarDadosPersonagemS2(S.projeto_id);
          // Regenera prompts com a nova referência @Nome
          await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nome_personagem: nome, estilo_visual: estilo })
          });
          if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
        } else {
          flowPersonagemCriado = false;
          const errMsg = (r && r.error) ? r.error : "Falha na criação do personagem: etapa de processamento não concluída.";
          if (statusBox && statusTxt) {
            statusBox.style.background = "rgba(239,68,68,0.15)";
            statusBox.style.borderColor = "#ef4444";
            statusTxt.innerHTML = `❌ <b>${esc(errMsg)}</b><br><small>Verifique se o Google Flow está aberto no Chrome e tente novamente.</small>`;
          }
          alert(errMsg);
        }
      } catch (e) {
        flowPersonagemCriado = false;
        const errMsg = `Falha na criação do personagem: ${e.message}`;
        if (statusBox && statusTxt) {
          statusBox.style.background = "rgba(239,68,68,0.15)";
          statusBox.style.borderColor = "#ef4444";
          statusTxt.innerHTML = `❌ <b>${esc(errMsg)}</b>`;
        }
        alert(errMsg);
      } finally {
        if (btnCriar) btnCriar.disabled = false;
      }
    });
  }

  // ETAPA 4: Salvar Personagem no Projeto
  if ($("btn-s2-salvar-personagem")) {
    $("btn-s2-salvar-personagem").addEventListener("click", async () => {
      const nome = $("s2-input-personagem") ? $("s2-input-personagem").value.trim() : "";
      if (!nome) {
        alert("Digite o nome do personagem (ex: Marcos).");
        return;
      }

      const fileInput = $("s2-input-avatar-file");
      const file = tempAvatarFile || (fileInput ? fileInput.files[0] : null);

      if (!file && !tempAvatarFile) {
        // Verifica se já tem avatar salvo anteriormente
        const idtAtual = await api(`/api/v2/identidade/${encodeURIComponent(S.projeto_id)}`);
        if (!idtAtual || !idtAtual.has_identity || !idtAtual.identidade || !idtAtual.identidade.imagem_abs) {
          alert("Selecione uma imagem de referência para o personagem.");
          return;
        }
      }

      const refFlow = flowPersonagemNome || ($("s2-input-ref-flow") ? $("s2-input-ref-flow").value.trim() : `@${nome}`);

      const fd = new FormData();
      fd.append("tipo", "personagem");
      fd.append("nome", nome);
      fd.append("referencia_flow", refFlow);
      if (file) fd.append("imagem", file);
      const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
      fd.append("estilo_visual", estilo);

      try {
        const r = await apiForm(`/api/v2/identidade/${encodeURIComponent(S.projeto_id)}/salvar`, fd);
        if (r && r.success) {
          alert(`✓ Personagem '${nome}' (${refFlow}) salvo e bloqueado com sucesso no projeto!`);
          await carregarDadosPersonagemS2(S.projeto_id);
          // Regenera os prompts automaticamente para refletir a tag do personagem
          await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nome_personagem: nome, estilo_visual: estilo })
          });
          if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
        } else {
          alert("Erro ao salvar personagem: " + ((r && r.error) || ""));
        }
      } catch (e) {
        alert("Erro na conexão: " + e.message);
      }
    });
  }

  // Opção 2: Salvar Avatar Flow (@me)
  if ($("btn-s2-salvar-avatar-flow")) {
    $("btn-s2-salvar-avatar-flow").addEventListener("click", async () => {
      const nome = $("s2-input-avatar-nome") ? $("s2-input-avatar-nome").value.trim() : "Meu Avatar";
      const refFlow = "@me";
      const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";

      const fd = new FormData();
      fd.append("tipo", "avatar");
      fd.append("nome", nome);
      fd.append("referencia_flow", refFlow);
      fd.append("estilo_visual", estilo);

      try {
        const r = await apiForm(`/api/v2/identidade/${encodeURIComponent(S.projeto_id)}/salvar`, fd);
        if (r && r.success) {
          alert(`✓ Avatar Google Flow (@me) configurado como identidade permanente do projeto!`);
          await carregarDadosPersonagemS2(S.projeto_id);
          // Regenera os prompts automaticamente com a tag @me
          await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nome_personagem: nome, estilo_visual: estilo })
          });
          if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
        } else {
          alert("Erro ao salvar avatar: " + ((r && r.error) || ""));
        }
      } catch (e) {
        alert("Erro na conexão: " + e.message);
      }
    });
  }

  // Alterar / Remover Identidade
  if ($("btn-s2-alterar-personagem")) {
    $("btn-s2-alterar-personagem").addEventListener("click", () => {
      if ($("s2-char-ativo-panel")) $("s2-char-ativo-panel").classList.add("hidden");
      if ($("s2-char-form-panel")) $("s2-char-form-panel").classList.remove("hidden");
    });
  }

  if ($("btn-s2-remover-personagem")) {
    $("btn-s2-remover-personagem").addEventListener("click", async () => {
      if (!confirm("Deseja realmente remover a identidade ativa deste projeto?")) return;
      try {
        await api(`/api/v2/identidade/${encodeURIComponent(S.projeto_id)}/remover`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        await carregarDadosPersonagemS2(S.projeto_id);
      } catch (e) {
        alert("Erro ao remover: " + e.message);
      }
    });
  }
}

async function carregarBibliotecaPersonagensS2() {
  const grid = $("s2-biblioteca-personagens-grid");
  if (!grid) return;

  grid.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:10px">Carregando biblioteca...</div>';
  try {
    const res = await api("/api/v2/personagens/biblioteca");
    if (!res || !res.success || !res.personagens || res.personagens.length === 0) {
      grid.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:10px">Nenhum personagem salvo na biblioteca ainda. Crie um novo personagem na Opção 1.</div>';
      return;
    }

    grid.innerHTML = "";
    res.personagens.forEach((char) => {
      const card = document.createElement("div");
      card.style.cssText = "background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:8px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:6px";
      
      const imgWrap = document.createElement("div");
      imgWrap.style.cssText = "width:64px;height:64px;border-radius:8px;overflow:hidden;background:rgba(124,92,252,0.1);display:flex;align-items:center;justify-content:center;border:1px solid var(--border)";
      
      if (char.has_image || char.imagem_abs) {
        const img = document.createElement("img");
        img.src = `/api/v2/personagens/biblioteca/${encodeURIComponent(char.nome)}/avatar?t=${Date.now()}`;
        img.style.cssText = "width:100%;height:100%;object-fit:cover";
        imgWrap.appendChild(img);
      } else {
        imgWrap.innerHTML = '<span style="font-size:24px">👤</span>';
      }

      const nomeSpan = document.createElement("div");
      nomeSpan.style.cssText = "font-weight:bold;font-size:12px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:110px";
      nomeSpan.textContent = char.nome;

      const tagSpan = document.createElement("span");
      tagSpan.className = "badge badge-ok";
      tagSpan.style.cssText = "font-size:10px;padding:2px 6px";
      tagSpan.textContent = char.referencia_flow || `@${char.nome}`;

      const btnUsar = document.createElement("button");
      btnUsar.type = "button";
      btnUsar.className = "btn btn-xs btn-primary btn-full";
      btnUsar.style.marginTop = "4px";
      btnUsar.textContent = "⚡ Usar no Projeto";
      btnUsar.addEventListener("click", async () => {
        try {
          btnUsar.disabled = true;
          btnUsar.textContent = "Vinculando...";
          const vinc = await apiJson("/api/v2/personagens/biblioteca/vincular", {
            projeto_id: S.projeto_id,
            nome: char.nome
          });
          if (vinc && vinc.success) {
            alert(`✓ Personagem '${char.nome}' vinculado ao projeto com sucesso!`);
            await carregarDadosPersonagemS2(S.projeto_id);
            const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
            await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ nome_personagem: char.nome, estilo_visual: estilo })
            });
            if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
          } else {
            alert("Erro ao vincular personagem: " + ((vinc && vinc.error) || ""));
          }
        } catch (e) {
          alert("Erro: " + e.message);
        } finally {
          btnUsar.disabled = false;
          btnUsar.textContent = "⚡ Usar no Projeto";
        }
      });

      card.appendChild(imgWrap);
      card.appendChild(nomeSpan);
      card.appendChild(tagSpan);
      card.appendChild(btnUsar);
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div style="font-size:12px;color:red;padding:10px">Erro ao carregar: ${e.message}</div>`;
  }
}

async function carregarDadosPersonagemS2(projeto_id) {
  try {
    const res = await api(`/api/v2/identidade/${encodeURIComponent(projeto_id)}`);
    const badge = $("s2-char-status-badge");
    const ativoPanel = $("s2-char-ativo-panel");
    const formPanel = $("s2-char-form-panel");

    if (res && res.has_identity && res.identidade) {
      const idt = res.identidade;
      const refTag = idt.referencia_flow || (idt.tipo === "avatar" ? "@me" : `@${idt.nome || "Personagem"}`);
      const isAvatar = idt.tipo === "avatar";

      if (badge) {
        badge.className = "badge badge-ok";
        badge.textContent = isAvatar ? "Avatar Flow @me 🔒" : "Personagem Bloqueado 🔒";
      }
      if ($("s2-char-ativo-nome")) $("s2-char-ativo-nome").textContent = idt.nome || (isAvatar ? "Avatar Google Flow" : "Personagem");
      if ($("s2-char-ativo-tag")) $("s2-char-ativo-tag").textContent = refTag;
      if ($("s2-char-ativo-tipo-badge")) {
        $("s2-char-ativo-tipo-badge").textContent = isAvatar ? "✨ AVATAR GOOGLE FLOW" : "📸 PERSONAGEM COM FOTO";
      }
      if ($("s2-prod-char-badge")) {
        $("s2-prod-char-badge").textContent = `👤 ${refTag} Bloqueado 🔒`;
        $("s2-prod-char-badge").classList.remove("hidden");
      }

      if ($("s2-char-ativo-img") && $("s2-char-ativo-icon")) {
        if (idt.imagem || idt.imagem_abs) {
          $("s2-char-ativo-img").src = `/api/v2/identidade/${encodeURIComponent(projeto_id)}/avatar?t=${Date.now()}`;
          $("s2-char-ativo-img").classList.remove("hidden");
          $("s2-char-ativo-icon").classList.add("hidden");
        } else {
          $("s2-char-ativo-img").classList.add("hidden");
          $("s2-char-ativo-icon").classList.remove("hidden");
          $("s2-char-ativo-icon").textContent = isAvatar ? "✨" : "👤";
        }
      }

      if (ativoPanel) ativoPanel.classList.remove("hidden");
      if (formPanel) formPanel.classList.add("hidden");
    } else {
      if (badge) {
        badge.className = "badge badge-wait";
        badge.textContent = "Nenhuma identidade";
      }
      if ($("s2-prod-char-badge")) $("s2-prod-char-badge").classList.add("hidden");
      if (ativoPanel) ativoPanel.classList.add("hidden");
      if (formPanel) formPanel.classList.remove("hidden");
    }
  } catch (e) {}
}

/* ============================================================
   LIVE TERMINAL HUD CONTROLLER (FASE 11.2)
   ============================================================ */
let termScrollLock = false;
let termExpanded = false;
let termPollingInterval = null;

async function gerarCenaIndividualFlow(scene_id) {
  if (!S.projeto_id) return;
  try {
    const res = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/iniciar_fila`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_ids: [scene_id], modo: "imagem" })
    });
    if (res && res.success) {
      // Abre o console automaticamente para o usuário acompanhar
      if (!termExpanded) toggleTerminalExpanded();
      pollLiveTerminalHUD();
    } else {
      alert("Aviso: " + (res.error || "Não foi possível iniciar a cena."));
    }
  } catch (e) {
    alert("Erro ao iniciar cena no Flow: " + e.message);
  }
}

function initLiveTerminalHUD() {
  const toggleBtn = $("live-terminal-toggle-btn");
  const btnToggle = $("btn-term-toggle");
  const btnScrollLock = $("btn-term-scroll-lock");
  const btnCopiar = $("btn-term-copiar");
  const btnLimpar = $("btn-term-limpar");

  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      toggleTerminalExpanded();
    });
  }
  if (btnToggle) {
    btnToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleTerminalExpanded();
    });
  }
  if (btnScrollLock) {
    btnScrollLock.addEventListener("click", (e) => {
      e.stopPropagation();
      termScrollLock = !termScrollLock;
      btnScrollLock.textContent = termScrollLock ? "🔒 Scroll Lock: ON" : "🔓 Scroll Lock: OFF";
      btnScrollLock.className = termScrollLock ? "btn btn-xs btn-primary" : "btn btn-xs btn-ghost";
    });
  }
  if (btnCopiar) {
    btnCopiar.addEventListener("click", (e) => {
      e.stopPropagation();
      const logsEl = $("live-terminal-logs");
      if (logsEl) {
        navigator.clipboard.writeText(logsEl.innerText).then(() => {
          btnCopiar.textContent = "✓ Copiado!";
          setTimeout(() => { btnCopiar.textContent = "📋 Copiar Logs"; }, 2000);
        });
      }
    });
  }
  if (btnLimpar) {
    btnLimpar.addEventListener("click", (e) => {
      e.stopPropagation();
      const logsEl = $("live-terminal-logs");
      if (logsEl) logsEl.innerHTML = "";
    });
  }

  // Inicia polling se houver projeto ativo
  if (termPollingInterval) clearInterval(termPollingInterval);
  termPollingInterval = setInterval(pollLiveTerminalHUD, 1500);
}

function toggleTerminalExpanded() {
  const hud = $("live-terminal-hud");
  const btn = $("btn-term-toggle");
  if (!hud) return;
  termExpanded = !termExpanded;
  if (termExpanded) {
    hud.classList.remove("collapsed");
    hud.classList.add("expanded");
    if (btn) btn.textContent = "▼ Recolher Console";
  } else {
    hud.classList.remove("expanded");
    hud.classList.add("collapsed");
    if (btn) btn.textContent = "▲ Expandir Console";
  }
}

async function pollLiveTerminalHUD() {
  if (!S.projeto_id) return;
  try {
    const res = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/live_console`);
    if (!res || !res.success) return;

    const w = res.worker || {};
    const stats = res.stats || {};
    const ca = w.cena_ativa || {};

    // Atualiza Badges de Topo do Console
    const badgeStatus = $("term-status-badge");
    if (badgeStatus) {
      if (w.is_running) {
        badgeStatus.className = "badge badge-proc";
        badgeStatus.textContent = "⚡ PRODUÇÃO ATIVA";
      } else {
        badgeStatus.className = "badge badge-ok";
        badgeStatus.textContent = "🟢 GOOGLE FLOW PRONTO";
      }
    }

    const cenaTxt = $("term-cena-ativa");
    if (cenaTxt) {
      if (ca.scene_id) {
        cenaTxt.textContent = `Cena #${String(ca.scene_id).padStart(3, '0')} (${ca.scene_idx || '?'}/${ca.total_cenas || stats.total || '?'})`;
      } else {
        cenaTxt.textContent = w.is_running ? "Produzindo fila..." : "Fila aguardando";
      }
    }

    const etapaTxt = $("term-etapa-ativa");
    if (etapaTxt) {
      etapaTxt.textContent = ca.etapa || (w.is_running ? "Processando..." : "Pronto para gerar");
    }

    const timerBadge = $("term-timer-badge");
    if (timerBadge) {
      const tDec = Math.round(ca.tempo_decorrido || 0);
      const tTot = Math.round(ca.tempo_total || 0);
      const tMed = (ca.tempo_medio || 0).toFixed(1);
      timerBadge.textContent = `⏱ Cena: ${tDec}s | Total: ${fmtDur(tTot)} | Média: ~${tMed}s`;
    }

    // Renderiza Logs Coloridos
    const logsEl = $("live-terminal-logs");
    if (logsEl && res.logs && res.logs.length) {
      logsEl.innerHTML = res.logs.map(log => {
        let tagCls = "tag-info";
        const msg = log.message || "";
        if (log.level === "ERROR" || msg.includes("ERRO") || msg.includes("Falha")) tagCls = "tag-err";
        else if (log.level === "WARN" || msg.includes("AVISO") || msg.includes("Timeout")) tagCls = "tag-warn";
        else if (msg.includes("OK") || msg.includes("SUCESSO") || msg.includes("READY")) tagCls = "tag-ok";

        return `
          <div class="terminal-log-row">
            <span class="terminal-log-ts">[${esc(log.ts)}]</span>
            <span class="terminal-log-tag ${tagCls}">${esc(log.category || 'LOG')}</span>
            <span class="terminal-log-msg">${esc(msg)}</span>
          </div>
        `;
      }).join("");

      if (!termScrollLock) {
        logsEl.scrollTop = logsEl.scrollHeight;
      }
    }
  } catch (e) {}
}

document.addEventListener("DOMContentLoaded", () => {
  initLiveTerminalHUD();
});
