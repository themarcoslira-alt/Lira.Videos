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
  // TAREFA 8: sub-aba ativa da aba 5 MONTAGEM (persiste entre trocas de projeto/aba)
  montagemSubAba: "legendas",
};

const ETAPAS = [
  { step: 0, id: "transcrever", label: "Transcrever" },
  { step: 1, id: "cenas", label: "Cenas" },
  { step: 2, id: "storyboard", label: "Storyboard" },
  { step: 3, id: "midias", label: "Mídias" },
  { step: 4, id: "render", label: "Render" },
];

const $ = (id) => document.getElementById(id);

/* ---------- Toast global (feedback de ações) ----------
   showToast() é chamado em ~40 pontos do app (Ken Burns, B-Roll, trilha
   sonora, exportação CapCut, etc.). A função havia desaparecido do bundle
   e cada chamada levantava "ReferenceError: showToast is not defined",
   derrubando o feedback do usuário. Utilitário global (classic script →
   window.showToast) com container criado sob demanda. */
function showToast(mensagem, tipo) {
  try {
    const msg = (mensagem == null ? "" : String(mensagem)).trim();
    if (!msg) return;
    let cont = document.getElementById("toast-container");
    if (!cont) {
      cont = document.createElement("div");
      cont.id = "toast-container";
      document.body.appendChild(cont);
    }
    const nivel = tipo === "ok" || tipo === "err"
      ? tipo
      : (/^(\u274C|\u2716|\u26A0|erro|falha)/i.test(msg) ? "err"
        : (/^(\u2705|\u2713|\u2714|\u{1F525}|\u{1F3AC}|\u2728|\u{1F5D1}|\u{1F50D}|\u{1F3B5}|\u23F3)/u.test(msg) ? "ok" : ""));
    const el = document.createElement("div");
    el.className = "toast" + (nivel ? " toast-" + nivel : "");
    el.textContent = msg;
    cont.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 250);
    }, nivel === "err" ? 6000 : 3800);
  } catch (e) {
    console.log("[toast]", mensagem);
  }
}

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

async function carregarProjetosRecentesHome() {
  const listEl = $("inicio-projetos-list");
  if (!listEl) return;
  try {
    let r = await api("/api/v2/projetos");
    if (!r || !r.success) {
      r = await api("/api/projetos");
    }
    const lista = (r && r.projetos) || [];
    // Prioriza projetos reais (não-temporários)
    const reais = lista.filter(p => !p.id.startsWith("test_") && !p.id.startsWith("zzz_"));
    const exibiveis = reais.length ? reais : lista;

    if ($("inicio-projetos-count")) $("inicio-projetos-count").textContent = exibiveis.length;
    if (!exibiveis.length) {
      listEl.innerHTML = '<div class="scenes-empty" style="padding:10px">Nenhum projeto existente ainda. Crie o primeiro acima!</div>';
      return;
    }
    listEl.innerHTML = exibiveis.slice(0, 10).map(p => {
      const criado = p.criado_em ? String(p.criado_em).replace("T", " ").slice(0, 16) : "";
      const statsCenas = p.total_cenas ? `${p.cenas_prontas || 0}/${p.total_cenas} cenas` : '';
      return `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;gap:10px;flex-wrap:wrap">
          <div>
            <div style="display:flex;align-items:center;gap:6px">
              <b style="font-size:13.5px;color:#f8fafc">${esc(p.nome || p.id)}</b>
              ${statsCenas ? `<span class="badge badge-ok" style="font-size:10px">${statsCenas}</span>` : ''}
              ${p.studio_version === 'v2' ? '<span class="badge badge-proc" style="font-size:10px">Studio 2.0</span>' : ''}
            </div>
            <span class="muted" style="font-size:11px">${esc(p.id)} ${criado ? `• ${esc(criado)}` : ''}</span>
          </div>
          <div class="btn-row" style="margin:0;gap:6px">
            <button class="btn btn-sm btn-primary" type="button" onclick="abrirProjetoExistente('${esc(p.id)}', '${esc(p.modo)}')">📂 Abrir</button>
            <button class="btn btn-sm btn-accent" style="background:#7c5cfc;color:#fff;font-weight:600" type="button" onclick="abrirProjetoComAba('${esc(p.id)}', '${esc(p.modo)}', 'producao')">🎬 Produção</button>
          </div>
        </div>
      `;
    }).join("");
  } catch (e) {
    listEl.innerHTML = `<div class="scenes-empty">Erro ao carregar projetos: ${esc(e.message)}</div>`;
  }
}

async function abrirProjetoComAba(pid, modo, aba) {
  await abrirProjetoExistente(pid, modo);
  trocarAbaStudio2(aba);
}
window.abrirProjetoComAba = abrirProjetoComAba;

function abrirHome() {
  pararTodosPollings();
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
  if (typeof atualizarContadorSidebarCenas === "function") atualizarContadorSidebarCenas();
  if ($("scenes-grid")) $("scenes-grid").innerHTML = '<div class="scenes-empty">As cenas aparecerão aqui conforme o pipeline avança.</div>';
  if ($("log-area")) $("log-area").innerHTML = "";
  carregarProjetosRecentesHome();
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
    if (typeof ativarTabProjeto === "function") ativarTabProjeto("cenas", false);
    if (typeof atualizarContadorSidebarCenas === "function") atualizarContadorSidebarCenas();
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
  pararTodosPollings();
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
        '<div class="btn-row" style="margin-top:2px;gap:6px">' +
          '<button class="btn btn-sm btn-primary" type="button" data-acao="abrir">Abrir</button>' +
          '<button class="btn btn-sm btn-accent" style="background:#7c5cfc;color:#fff;font-weight:600" type="button" data-acao="producao">🎬 Produção</button>' +
          '<button class="btn btn-ghost btn-sm" type="button" data-acao="excluir" title="Excluir projeto">🗑</button>' +
        '</div>' +
      '</div>';
    card.querySelector('[data-acao="abrir"]').addEventListener("click", () => abrirProjetoExistente(proj.id, proj.modo));
    card.querySelector('[data-acao="producao"]').addEventListener("click", () => abrirProjetoComAba(proj.id, proj.modo, "producao"));
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
  pararTodosPollings();
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
window.abrirProjetoExistente = abrirProjetoExistente;

/* ---------- Tela inicial: modo + criar (AJUSTE 2: sem upload de áudio) ---------- */
function bindHome() {
  // AJUSTE 2: sem upload de áudio na criação — o áudio é anexado dentro do fluxo.

  // CORREÇÃO 3 — Criação sempre em Studio 2.0 (modo único, hardcoded).
  S.modo = "studio2";

  $("btn-criar").addEventListener("click", criarProjeto);
  const inpNome = $("nome-projeto");
  if (inpNome) {
    inpNome.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        criarProjeto();
      }
    });
  }

  // Nav links
  document.querySelectorAll(".nav-link").forEach((nl) => {
    nl.addEventListener("click", () => {
      setNavAtivo(nl.dataset.nav);
      if (nl.dataset.nav === "config") {
        abrirConfig();
      } else if (nl.dataset.nav === "projetos") {
        abrirListaProjetos();
      } else if (nl.dataset.nav === "dashboard") {
        if (S.projeto_id) {
          abrirProjetoExistente(S.projeto_id, S.modo);
        } else {
          abrirHome();
        }
      }
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

  // CORREÇÃO 3 — Criação sempre Studio 2.0 (modo único, hardcoded) com defaults canônicos.
  const fd = new FormData();
  fd.append("nome", nome);
  fd.append("estilo_visual", "photorealistic_cinematic");
  fd.append("modo_producao", "somente_imagens");
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
    $("btn-criar").textContent = "✨ Criar projeto e começar";
  }
}

/* ---------- Configurações ---------- */
async function abrirConfig() {
  $("config-modal").classList.remove("hidden");
  const cfg = await api("/api/config");
  $("cfg-pasta-midia").value = cfg.pasta_midia_padrao || "";
  $("cfg-pasta-destino").value = cfg.pasta_destino || "";
  // AJUSTE 1: placeholders com as chaves mascaradas (•••• + últimos 4).
  if ($("cfg-deepseek-key")) {
    $("cfg-deepseek-key").value = "";
    $("cfg-deepseek-key").placeholder = (cfg.has_deepseek_key ? cfg.deepseek_key_mascarada + " — " : "") + "deixe em branco para manter";
  }
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
      deepseek_api_key: $("cfg-deepseek-key") ? $("cfg-deepseek-key").value.trim() : "",
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

// ANTIGRAVITY Passo 2: parada CENTRALIZADA de TODOS os timers/pollings do SPA.
// Antes, pararPolling() limpava apenas S.pollTimer — S2_POLL_TIMER,
// pollGaleriaTimer e termPollingInterval continuavam vivos ao navegar para a
// Home ou abrir outro projeto, consumindo rede e alterando o DOM da tela errada.
function pararTodosPollings() {
  pararPolling();
  if (S2_POLL_TIMER) { clearInterval(S2_POLL_TIMER); S2_POLL_TIMER = null; }
  // PHASE 2 (ERRO 6): encerra o stream SSE para não deixar conexão viva
  // enquanto o operador navega para outra tela/projeto.
  if (S2_SSE) { try { S2_SSE.close(); } catch (e) {} S2_SSE = null; }
  S2_SSE_SIG = "";
  S2_SSE_REFRESH_TS = 0;
  if (S2_SSE_TRAILING) { clearTimeout(S2_SSE_TRAILING); S2_SSE_TRAILING = null; }
  if (S.pollGaleriaTimer) { clearInterval(S.pollGaleriaTimer); S.pollGaleriaTimer = null; }
  if (typeof termPollingInterval !== "undefined" && termPollingInterval) {
    clearInterval(termPollingInterval);
    termPollingInterval = null;
  }
  if (typeof _pollTranscricaoTimer !== "undefined" && _pollTranscricaoTimer) {
    clearInterval(_pollTranscricaoTimer);
    _pollTranscricaoTimer = null;
  }
  // Para também o polling de thumbnails das cenas v1
  Object.keys(S.cenaThumbs || {}).forEach((k) => {
    if (S.cenaThumbs[k]) clearInterval(S.cenaThumbs[k]);
  });
  S.cenaThumbs = {};
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

/* ---------- Menus de contexto / dropdowns (Frente 3) ---------- */
function fecharDropdowns() {
  document.querySelectorAll(".dropdown-menu, .step-menu").forEach((m) => {
    m.classList.add("hidden");
    if (m.tagName === "STEP-MENU" || !m.classList.contains("dropdown-menu")) {
      // .step-menu é removido do DOM quando "Fechar" é acionado
      if (!m.classList.contains("keep")) m.remove();
    }
  });
  const btn = $("btn-s2-acoes");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function itemMenu(label, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = b.closest(".step-menu, .dropdown-menu");
    if (menu) {
      menu.classList.add("hidden");
      menu.remove();
    }
    if (fn) fn();
  });
  return b;
}

function posicionarMenu(anchor, menu) {
  menu.classList.remove("hidden");
  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + "px";
  menu.style.left = Math.max(8, Math.min(rect.left - menu.offsetWidth + rect.width,
                                         window.innerWidth - menu.offsetWidth - 8)) + "px";
}

// Clicar fora fecha dropdowns abertos
document.addEventListener("click", (e) => {
  if (e.target.closest(".dropdown") || e.target.closest(".step-acoes")) return;
  fecharDropdowns();
});

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

    // Frente 3: ações por etapa num único botão "⋯" com menu contextual
    // (antes: pares de mini-botões ↺/▶ de 1 caractere por etapa).
    const acoes = document.createElement("div");
    acoes.className = "step-acoes";
    if (st === "done" || st === "active" || st === "error") {
      const kebab = document.createElement("button");
      kebab.type = "button";
      kebab.className = "step-btn kebab";
      kebab.title = "Ações desta etapa";
      kebab.setAttribute("aria-haspopup", "true");
      kebab.textContent = "⋯";
      kebab.addEventListener("click", (e) => {
        e.stopPropagation();
        const menu = document.createElement("div");
        menu.className = "step-menu hidden";
        if (st === "done") {
          menu.appendChild(itemMenu("↺ Reprocessar esta etapa", () => reprocessarEtapa(ep.id)));
        }
        if (st === "active" || st === "error") {
          menu.appendChild(itemMenu("▶ Avançar manualmente", () => avancarEtapa(ep.id)));
        }
        menu.appendChild(itemMenu("✕ Fechar", null));
        posicionarMenu(kebab, menu);
      });
      acoes.appendChild(kebab);
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

/* ============================================================
   TOKYOX — SCENE GRID (mockup tokyox-redesign.html:111-137 / :294-314)
   ------------------------------------------------------------
   Alimentado por S.cenas — a MESMA fonte que ja populava o card antigo e os
   contadores (renderScenesStats / atualizarContadorSidebarCenas). Nenhuma cena
   de exemplo do mockup e hardcodada.
   Badges b-pending/b-active/b-ok/b-error (mockup:117-120) mapeados 1:1 dos 3
   estados REAIS de S.cenas.status ("proc" | "ok" | "err" — app.js:782-792).
   ============================================================ */

/** Classe + rotulo do badge a partir do estado REAL da cena. */
function _badgeCena(cena) {
  if (cena.temMidia || cena.status === "ok") return ["b-ok", "Ok"];
  if (cena.status === "err") return ["b-error", "Erro"];
  if (cena.status === "proc") return ["b-active", "Buscando"];
  return ["b-pending", "Aguardando"];
}

/** Regra de "midia pronta" JA usada nesta mesma funcao (app.js:840) — reaproveitada
    pelo botao "Animacao" (mockup:311 / spec secao 4). Nao inventa criterio novo. */
function _podeAnimarCena(cena) {
  return Boolean(cena.temMidia || cena.status === "ok");
}

/** Reescreve o badge. innerHTML apenas com o dot LITERAL do mockup:121/331. */
function _setBadgeCena(badgeEl, cls, texto) {
  if (!badgeEl) return;
  badgeEl.className = "badge scene-badge " + cls;
  badgeEl.innerHTML = (cls === "b-active"
    ? '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="6"/></svg>'
    : "") + '<span class="badge-txt"></span>';
  badgeEl.querySelector(".badge-txt").textContent = texto;
}

/** Fecha os menus "..." abertos (clique fora / troca de card). */
function _fecharMenusCenaCard() {
  document.querySelectorAll("#scenes-grid .scene-menu").forEach((m) => {
    m.classList.add("hidden");
    const b = m.parentElement && m.parentElement.querySelector('[data-acao-cena="mais"]');
    if (b) b.setAttribute("aria-expanded", "false");
  });
}
document.addEventListener("click", _fecharMenusCenaCard);

/** Liga os 3 botoes do .scene-foot + as 3 copias do menu "..." (ITEM 6/7).
    Chamado UMA vez por card (na criacao), como o card antigo ja fazia. */
function _ligarAcoesCardCena(card, cena) {
  const btnEditar = card.querySelector('[data-acao-cena="editar"]');
  if (btnEditar) {
    btnEditar.onclick = (e) => {
      e.stopPropagation();
      // abrirModalMedia() le cena.id; os cards do grid guardam o numero em `idx`
      // (app.js:782/894). Mesmo padrao ja existente em app.js:2524 ({ id: scene_id }).
      abrirModalMedia({ id: cena.idx, arquivo_midia: cena.arquivo_midia, texto: cena.texto });
    };
  }

  const btnAnimar = card.querySelector('[data-acao-cena="animar"]');
  if (btnAnimar) {
    btnAnimar.onclick = (e) => {
      e.stopPropagation();
      // .disabled nao dispara nada (spec secao 4: "sem handler de clique").
      if (!_podeAnimarCena(cena)) return;
      gerarCenaIndividualFlow(cena.idx, "video");
    };
  }

  const btnMais = card.querySelector('[data-acao-cena="mais"]');
  const menu = card.querySelector(".scene-menu");
  if (btnMais && menu) {
    btnMais.onclick = (e) => {
      e.stopPropagation();
      const abrir = menu.classList.contains("hidden");
      _fecharMenusCenaCard();
      menu.classList.toggle("hidden", !abrir);
      btnMais.setAttribute("aria-expanded", abrir ? "true" : "false");
    };
  }

  // ITEM 6/7 preservado 1:1 — as 3 copias (nome / prompt / animacao) agora vivem
  // dentro do menu "...", com os MESMOS data-copiar e a mesma logica de antes.
  card.querySelectorAll("[data-copiar]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const chave = btn.dataset.copiar;
      const dados = {
        nome: cena.nome || ("Cena " + cena.idx),
        prompt: cena.image_prompt || "",
        animacao: cena.animacao || "",
      };
      const alvo = $("auto-erro") || $("card2-msg");
      _fecharMenusCenaCard();
      if (!dados[chave]) { showMsg(alvo, "Sem dados para copiar.", "erro"); return; }
      const ok = await copiarTexto(dados[chave]);
      if (ok) { showMsg(alvo, "Copiado: " + chave, "ok"); setTimeout(() => hideMsg(alvo), 1500); }
      else showMsg(alvo, "Não foi possível copiar.", "erro");
    };
  });
}

function renderCena(cena) {
  const grid = $("scenes-grid");
  if (!grid) return;
  let card = grid.querySelector(`[data-cena="${cena.idx}"]`);
  if (!card) {
    // Mockup:294-314 clonado de <template id="tpl-scene-card"> (index.html).
    // Fallback minimo caso a pagina venha de cache antigo (sem o template).
    const tpl = $("tpl-scene-card");
    if (tpl && tpl.content && tpl.content.firstElementChild) {
      card = tpl.content.firstElementChild.cloneNode(true);
    } else {
      card = document.createElement("div");
      card.className = "scene-card";
      card.innerHTML =
        '<div class="scene-head"><span class="scene-num"></span>' +
          '<span class="badge b-pending scene-badge"></span></div>' +
        '<div class="scene-thumb thumb"><span class="thumb-fallback">🎬</span>' +
          '<img class="scene-img" alt="" loading="lazy"></div>' +
        '<div class="scene-meta"><span class="scene-status-text"></span>' +
          '<span class="scene-query"></span></div>' +
        '<div class="scene-foot">' +
          '<button class="icon-btn" type="button" data-acao-cena="editar">Editar</button>' +
          '<button class="icon-btn" type="button" data-acao-cena="animar">Animação</button>' +
          '<button class="icon-btn more" type="button" data-acao-cena="mais">⋯</button>' +
          '<div class="scene-menu hidden"></div></div>';
    }
    card.dataset.cena = cena.idx;
    const img = card.querySelector(".scene-img");
    if (img) img.alt = "cena " + cena.idx;
    grid.appendChild(card);
    const empty = grid.querySelector(".scenes-empty");
    if (empty) empty.remove();
    _ligarAcoesCardCena(card, cena);
    // ITEM 5: busca a thumbnail assim que a mídia estiver em disco (polling 2s).
    // O 3º argumento informa que a cena DECLARA mídia — sem isso, um 404 persistente
    // (arquivo deletado) manteria o polling rodando para sempre (ITEM 10).
    iniciarThumbCena(cena.idx, card.querySelector(".scene-img"), Boolean(cena.arquivo_midia));
  }

  // ---------- SCENE GRID: preenchimento do card (mockup:302-314) ----------
  const badgeInfo = _badgeCena(cena);
  _setBadgeCena(card.querySelector(".scene-badge"), badgeInfo[0], badgeInfo[1]);

  // .scene-num = "Cena N" (mockup:303). O nome real da cena (antes exibido no
  // .scene-idx) segue no title e continua COPAVEL pelo menu "..." (data-copiar="nome").
  const elNum = card.querySelector(".scene-num");
  if (elNum) {
    elNum.textContent = "Cena " + cena.idx;
    elNum.title = cena.nome || ("Cena " + cena.idx);
  }

  // .scene-status-text = transcrição real da cena; sem ela, a frase de status
  // (mesmas frases do mockup: "Aguardando transcrição da cena" / "Buscando mídia").
  const elStatus = card.querySelector(".scene-status-text");
  if (elStatus) {
    const tipoTxt = cena.tipo === "video" ? "vídeo" : (cena.tipo ? String(cena.tipo) : "");
    let frase;
    if (badgeInfo[0] === "b-ok") frase = cena.texto || ("Mídia pronta" + (tipoTxt ? " (" + tipoTxt + ")" : ""));
    else if (badgeInfo[0] === "b-error") frase = cena.texto || "Falha ao buscar mídia";
    else if (badgeInfo[0] === "b-active") frase = cena.texto || ("Buscando mídia" + (cena.origem ? " — " + cena.origem : ""));
    else frase = cena.texto || "Aguardando transcrição da cena";
    elStatus.textContent = frase;
    elStatus.title = cena.texto || frase;
  }

  // .scene-query = "cena N de M · tipo · duração · query ..." (mockup:307).
  // O tipo (antes no .scene-meta) e a duração (antes no .scene-dur) nao se perdem.
  const elQuery = card.querySelector(".scene-query");
  if (elQuery) {
    const partes = [cena.total ? `cena ${cena.idx} de ${cena.total}` : `cena ${cena.idx}`];
    if (cena.tipo) partes.push(cena.tipo === "video" ? "vídeo" : String(cena.tipo));
    if (cena.duracao) partes.push(fmtDur(cena.duracao));
    partes.push(cena.query ? `query "${cena.query}"` : "query —");
    elQuery.textContent = partes.join(" · ");
  }

  // .scene-foot — botão "Animação" só habilita com mídia pronta (mockup:311).
  const btnAnimar = card.querySelector('[data-acao-cena="animar"]');
  if (btnAnimar) {
    const podeAnimar = _podeAnimarCena(cena);
    btnAnimar.classList.toggle("disabled", !podeAnimar);
    btnAnimar.classList.toggle("ready", podeAnimar);
    btnAnimar.setAttribute("aria-disabled", podeAnimar ? "false" : "true");
    btnAnimar.title = podeAnimar
      ? "Animar esta cena no Flow"
      : "Disponível quando a cena estiver com mídia pronta";
  }

  $("scene-count").textContent = S.cenas.size;
  if (typeof atualizarContadorSidebarCenas === "function") atualizarContadorSidebarCenas();
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

/* ============================================================
   Banner de confirmação TokyoX (mockup: .confirm-banner)
   ------------------------------------------------------------------
   Componente REUTILIZÁVEL para qualquer etapa que gere artefato baixável.
   Inline e NÃO bloqueante: o pipeline nunca espera por ele. Reusa os
   handlers de download JÁ existentes (baixarTranscricao / baixarPromptsTxt)
   e a rota genérica de arquivos para o storyboard.json (raiz do projeto).
   Ids usados (ADICIONADOS nesta sessão, nenhum existente renomeado):
   #banner-confirmacao, #banner-confirmacao-texto,
   #banner-confirmacao-baixar, #banner-confirmacao-continuar.
   ============================================================ */
const BANNER_CONFIRMACAO = {
  transcricao: {
    texto: () => "Transcrição concluída. Baixar o .txt antes de seguir para o pipeline?",
    baixar: () => baixarTranscricao("txt"),
  },
  prompts: {
    texto: () => {
      const n = S.cenas.size || 0;
      return "Prompts gerados" + (n ? ` — ${n} cenas prontas` : "") +
        ". Baixar o .txt antes de enviar ao Google Flow?";
    },
    baixar: () => baixarPromptsTxt(),
  },
  storyboard: {
    texto: () => {
      const n = S.cenaTotal || S.cenas.size || 0;
      return "Storyboard concluído" + (n ? ` — ${n} cenas planejadas` : "") +
        ". Baixar o .json antes de seguir?";
    },
    baixar: () => {
      if (!S.projeto_id) { showToast("❌ Nenhum projeto ativo selecionado."); return; }
      window.open(`/api/v2/arquivos/${encodeURIComponent(S.projeto_id)}/download/raiz/storyboard.json`, "_blank");
    },
  },
};

let _bannerConfirmacaoTipo = null;

function mostrarBannerConfirmacao(tipo) {
  const cfg = BANNER_CONFIRMACAO[tipo];
  const box = $("banner-confirmacao");
  const alvo = $("banner-confirmacao-texto");
  if (!cfg || !box || !alvo) return;
  const texto = cfg.texto();
  if (_bannerConfirmacaoTipo === tipo && alvo.textContent === texto && !box.classList.contains("hidden")) return;
  alvo.textContent = texto;
  _bannerConfirmacaoTipo = tipo;
  box.classList.remove("hidden");
}

function esconderBannerConfirmacao() {
  const box = $("banner-confirmacao");
  if (box) box.classList.add("hidden");
  _bannerConfirmacaoTipo = null;
}

(function _initBannerConfirmacao() {
  function ligar() {
    const btnBaixar = $("banner-confirmacao-baixar");
    const btnContinuar = $("banner-confirmacao-continuar");
    if (btnBaixar) {
      btnBaixar.addEventListener("click", () => {
        const cfg = BANNER_CONFIRMACAO[_bannerConfirmacaoTipo];
        if (cfg) cfg.baixar();
      });
    }
    if (btnContinuar) btnContinuar.addEventListener("click", () => esconderBannerConfirmacao());
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ligar);
  else ligar();
})();

window.mostrarBannerConfirmacao = mostrarBannerConfirmacao;
window.esconderBannerConfirmacao = esconderBannerConfirmacao;

/* ============================================================
   Sidebar secundária "Projeto" TokyoX (mockup: tokyox-redesign.html:218-226)
   ------------------------------------------------------------
   Tabs simples (.nav-item): Roteiro / Cenas / Mídias / CapCut.
   Default: "Cenas" ativa ao carregar projeto.
   Contador: sincronizado com S.cenaTotal / S.cenas.size.
   ============================================================ */
let _tabProjetoAtiva = "cenas";

function atualizarContadorSidebarCenas() {
  const el = $("nav-proj-cenas-count");
  if (!el) return;
  const count = S.cenaTotal || (S.cenas ? S.cenas.size : 0);
  el.textContent = count > 0 ? String(count) : "0";
}

function ativarTabProjeto(tab, focar = true) {
  _tabProjetoAtiva = tab;
  const tabs = ["roteiro", "cenas", "midias", "capcut"];
  tabs.forEach((t) => {
    const btn = $("nav-proj-" + t);
    if (btn) btn.classList.toggle("active", t === tab);
  });

  if (!focar) return;

  // Alterna/foca a seção correspondente no painel central
  if (tab === "cenas") {
    const grid = $("scenes-grid");
    if (grid) grid.scrollIntoView({ behavior: "smooth", block: "start" });
    if (S.studio_version === "v2" && typeof trocarAbaStudio2 === "function") {
      trocarAbaStudio2("prompts");
    }
  } else if (tab === "roteiro") {
    const audioPanel = $("auto-audio-panel");
    const transcricaoAcoes = $("transcricao-acoes");
    if (audioPanel && !audioPanel.classList.contains("hidden")) {
      audioPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (transcricaoAcoes) {
      transcricaoAcoes.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (S.studio_version === "v2" && typeof trocarAbaStudio2 === "function") {
      trocarAbaStudio2("prompts");
    }
  } else if (tab === "midias") {
    const btnImp = $("btn-importar-imagens");
    if (btnImp) btnImp.scrollIntoView({ behavior: "smooth", block: "center" });
    if (S.studio_version === "v2" && typeof trocarAbaStudio2 === "function") {
      trocarAbaStudio2("arquivos");
    }
  } else if (tab === "capcut") {
    const btnExp = $("btn-exportar-capcut");
    if (btnExp) btnExp.scrollIntoView({ behavior: "smooth", block: "center" });
    if (S.studio_version === "v2" && typeof trocarAbaStudio2 === "function") {
      trocarAbaStudio2("exportacao");
    }
  }
}

(function _initSidebarProjeto() {
  function ligar() {
    const tabs = ["roteiro", "cenas", "midias", "capcut"];
    tabs.forEach((tab) => {
      const btn = $("nav-proj-" + tab);
      if (btn) {
        btn.addEventListener("click", () => ativarTabProjeto(tab, true));
      }
    });
    atualizarContadorSidebarCenas();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ligar);
  } else {
    ligar();
  }
})();

window.ativarTabProjeto = ativarTabProjeto;
window.atualizarContadorSidebarCenas = atualizarContadorSidebarCenas;

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

  // Banner de confirmação TokyoX — mostra o artefato MAIS RECENTE já pronto
  // (storyboard > prompts > transcrição). Inline e NÃO bloqueante: não altera
  // o fluxo do pipeline nem o comportamento de nenhum botão existente.
  if (e.storyboard) {
    mostrarBannerConfirmacao("storyboard");
  } else if (e.gerar_cenas) {
    mostrarBannerConfirmacao("prompts");
  } else if (e.transcrever || status.transcricao_completa) {
    mostrarBannerConfirmacao("transcricao");
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
function iniciarThumbCena(sceneId, imgEl, declarouMidia = false) {
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
      } else if (res.status === 404 && declarouMidia) {
        // ITEM 10 — a cena DECLARA mídia (arquivo_midia preenchido) mas o arquivo
        // não existe mais em disco: o polling de 2s nunca vai resolver. Para o
        // polling (variável real: `timer` / S.cenaThumbs[sceneId]) e avisa a UI —
        // mesmo motivo que o backend devolve em /api/cena_media: "arquivo_deletado".
        clearInterval(timer);
        delete S.cenaThumbs[sceneId];
        _marcarMidiaDeletadaCena(imgEl, sceneId);
      }
    } catch (e) { /* rede — tenta de novo no próximo tick */ }
  }, 2000);
  S.cenaThumbs[sceneId] = timer;
}

/**
 * ITEM 10 — sinaliza no card que o arquivo da cena foi deletado do disco.
 * UI best-effort: qualquer falha aqui não pode afetar o polling.
 */
function _marcarMidiaDeletadaCena(imgEl, sceneId) {
  try {
    const wrap = imgEl && imgEl.parentElement;
    if (!wrap) return;
    const fallback = wrap.querySelector(".thumb-fallback");
    if (fallback) {
      fallback.textContent = "🗑";
      fallback.style.display = "flex";
    }
    if (imgEl) imgEl.classList.remove("visible");
    // O badge saiu da thumb e passou para a .scene-head (mockup:303): procura
    // primeiro dentro da thumb (cards antigos) e depois no card inteiro.
    const badge = wrap.querySelector(".scene-badge")
      || (wrap.parentElement && wrap.parentElement.querySelector(".scene-badge"));
    if (badge) {
      badge.textContent = "arquivo deletado";
      badge.title = "Arquivo deletado — aguardando reconstrução";
    }
  } catch (e) { /* UI é best-effort */ }
}

/**
 * ITEM 7 — lê o `reason` do 404 de /api/cena_media.
 * Necessário porque a verificação é feita com HEAD (sem corpo de resposta):
 * um GET leve só é disparado quando o HEAD falha — o arquivo não existe, então
 * a resposta é apenas o JSON de erro do backend.
 */
async function _motivo404CenaMedia(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r || r.status !== 404) return "";
    const data = await r.json().catch(() => ({}));
    return String((data && data.reason) || "");
  } catch (e) {
    return "";
  }
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

/* ---------- Contas Google Flow (aba 1 / card 3) ---------- */
async function carregarContasFlow() {
  try {
    const r = await api("/api/flow/contas");
    const contas = (r && r.contas) || [];
    renderizarContasFlow(contas, r && r.ativa_id);
    // PASSO 3 — estado global de créditos: banner fixo + bloqueo visual dos
    // botões "Produção" da lista lateral quando NINGUNA cuenta Flow tem créditos.
    _aplicarEstadoCreditosGlobal(r && r.alguna_conta_com_creditos === false);
  } catch (e) {
    console.warn("Erro ao carregar contas do Flow:", e);
  }
}

// PASSO 3 — banner fixo no topo do dashboard + bloqueio visual dos botões
// "Produção" da lista lateral (clase .disabled + pointer-events:none) enquanto
// não há NINGUNA conta Google Flow com créditos. Idempotente: se já há créditos,
// garante que o banner NÃO aparece e os botões ficam habilitados.
function _aplicarEstadoCreditosGlobal(bloqueado) {
  const idEstilo = "css-bloqueo-produccion-creditos";
  const idBanner = "banner-flow-sem-creditos";
  const selBotoes = "#inicio-projetos-list button[onclick*='producao'], " +
                    "#projetos-grid button[data-acao='producao']";

  let style = document.getElementById(idEstilo);
  if (bloqueado) {
    // CSS por selector cobre também re-renders posteriores da lista lateral
    if (!style) {
      style = document.createElement("style");
      style.id = idEstilo;
      style.textContent = selBotoes +
        "{pointer-events:none!important;opacity:.45!important;cursor:not-allowed!important}";
      document.head.appendChild(style);
    }
    // Clase disabled ademais do CSS (cubre elementos já renderizados)
    document.querySelectorAll(selBotoes).forEach((b) => b.classList.add("disabled"));

    // Banner fixo no topo do dashboard
    let banner = document.getElementById(idBanner);
    const home = document.getElementById("tela-inicio");
    if (!banner && home) {
      banner = document.createElement("div");
      banner.id = idBanner;
      banner.style.cssText = "position:sticky;top:0;z-index:60;display:flex;align-items:center;" +
        "justify-content:center;gap:8px;padding:10px 14px;background:#d32f2f;color:#fff;" +
        "font-weight:600;font-size:13px;border-radius:0 0 8px 8px;margin-bottom:12px;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.3)";
      banner.textContent = "⚠️ Todas as contas Google Flow estão sem créditos. Produção bloqueada.";
      home.prepend(banner);
    }
  } else {
    if (style) style.remove();
    document.querySelectorAll(selBotoes).forEach((b) => b.classList.remove("disabled"));
    const banner = document.getElementById(idBanner);
    if (banner) banner.remove();
  }
}

function renderizarContasFlow(contas, ativa_id) {
  const lista = $("flow-contas-lista");
  if (!lista) return;
  // PASSO 4 — contas com créditos (creditos_esgotados=false) primeiro, esgotadas por último
  const contasOrdenadas = (contas || []).slice().sort((a, b) => {
    return (a.creditos_esgotados ? 1 : 0) - (b.creditos_esgotados ? 1 : 0);
  });
  lista.innerHTML = contasOrdenadas.map(c => `
    <div style="display:flex;align-items:center;gap:10px;
      padding:10px 14px;border-radius:8px;
      background:${c.id === ativa_id ? '#1e3a2f' : '#1a1a1a'};
      border:1px solid ${c.id === ativa_id ? '#2ecc71' : '#333'}">
      <div style="flex:1">
        <div style="font-weight:600;font-size:13px">
          ${c.nome}
          ${c.id === ativa_id
            ? '<span style="color:#2ecc71;font-size:11px"> ● ATIVA</span>'
            : ''}
          ${c.creditos_esgotados
            ? '<span style="color:#e74c3c;font-size:11px"> ⚠ Créditos esgotados</span>'
            : ''}
        </div>
        <div style="font-size:11px;color:#888;margin-top:2px">
          ${c.email ? c.email : '<i style="color:#555">Não logada</i>'}
        </div>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-xs btn-ghost"
          onclick="ativarContaFlow(${c.id})"
          ${c.id === ativa_id ? 'disabled style="opacity:0.5"' : ''}>
          ${c.id === ativa_id ? '✓ Ativa' : 'Selecionar'}
        </button>
        <button class="btn btn-xs btn-secondary"
          onclick="loginGuiadoContaFlow(${c.id})">
          🔑 ${c.email ? 'Relogar' : 'Logar'}
        </button>
        <button class="btn btn-xs btn-danger"
          onclick="removerContaFlow(${c.id})"
          ${c.id === ativa_id ? 'disabled style="opacity:0.5"' : ''}>
          🗑
        </button>
      </div>
    </div>
  `).join('');
}

async function ativarContaFlow(id) {
  await apiJson('/api/flow/contas/ativar', { conta_id: id });
  carregarContasFlow();
}

async function loginGuiadoContaFlow(id) {
  alert('O Chrome vai abrir. Faça login e aguarde...');
  const r = await apiJson('/api/flow/contas/login_guiado',
    { conta_id: id, projeto_id: S.projeto_id });
  if (!r) { carregarContasFlow(); return; }
  if (r.creditos_ok === false) {
    alert('⚠️ ' + (r.error || 'Conta logada sem créditos'));
    carregarContasFlow();
    return;
  }
  if (r.creditos_ok === true && r.retomar_fila === true) {
    alert('✅ Login salvo: ' + r.email + '\nConta pronta — retomando produção...');
    // Retoma a fila automaticamente (mismo endpoint del botón "Retomar fila")
    await apiJson('/api/flow/fila/parar', { projeto_id: S.projeto_id });
  } else if (r.email) {
    alert('✅ Login salvo: ' + r.email);
  } else {
    alert('⚠️ Email não capturado. Tente novamente.');
  }
  carregarContasFlow();
}

async function removerContaFlow(id) {
  if (!confirm('Remover esta conta?')) return;
  await apiJson('/api/flow/contas/remover', { conta_id: id });
  carregarContasFlow();
}

function bindCard3() {
  // Contas do Flow (múltiplas contas / rotação por créditos)
  carregarContasFlow();

  // 3.1 — Conexão com o Google Flow
  $("btn-flow-abrir").addEventListener("click", async () => {
    hideMsg($("card3-msg"));
    // A conta ativa já foi persistida via ativarContaFlow (cards de conta)
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
    const prog = r.progresso || {};
    const total = prog.total || (r.contadores && r.contadores.total) || 0;
    const prontos = prog.prontas !== undefined ? prog.prontas : (c.prontos || 0);
    const pendentes = Math.max(0, total - prontos);
    if ($("cnt-total")) $("cnt-total").textContent = total;
    if ($("cnt-pendentes")) $("cnt-pendentes").textContent = pendentes;
    if ($("cnt-gerando")) $("cnt-gerando").textContent = c.gerando || 0;
    if ($("cnt-prontos")) $("cnt-prontos").textContent = prontos;
    if ($("cnt-erros")) $("cnt-erros").textContent = c.erros || 0;

    // Atualiza barra de atividade em tempo real
    const atvTxt = $("flow-atividade-texto");
    const atvIco = $("flow-atividade-icon");
    if (atvTxt) {
      if (r.cena_ativa && (r.cena_ativa.etapa || r.cena_ativa.mensagem)) {
        const _sceneId = r.cena_ativa.scene_id ? `Cena #${String(r.cena_ativa.scene_id).padStart(3,'0')} — ` : '';
        atvTxt.textContent = _sceneId + (r.cena_ativa.etapa || r.cena_ativa.mensagem || 'Gerando...');
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

    // CORREÇÃO 2 — registra cena ativa e destaca o card correspondente (Produção/Mural)
    if (r.cena_ativa && r.cena_ativa.scene_id != null) {
      _ULTIMA_CENA_ATIVA_SCENE_ID = r.cena_ativa.scene_id;
    } else {
      _ULTIMA_CENA_ATIVA_SCENE_ID = null;
    }
    _aplicarDestaqueCenaAtiva();
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

  // BLOCO 3C (aprovado): badge conforme disponibilidade real da mídia.
  // - arquivo_midia vazio → âmbar "Aguardando geração"
  // - mídia carregada com sucesso → verde "Mídia pronta"
  // - HTTP 404 na tentativa de carregar → âmbar "Aguardando geração"
  // - HTTP 404 com reason="arquivo_deletado" → "Arquivo deletado — aguardando reconstrução"
  //   (ITEM 7/10: o arquivo existia e foi apagado do disco — a UI esconde a mídia em
  //   vez de tentar exibir um erro genérico).
  const stEl = $("media-modal-status");
  const temMidiaDeclarado = !!cena.arquivo_midia;
  let midiaDeletada = false;

  async function definirBadgeDisponibilidade() {
    if (!stEl) return;
    const ehVideoArquivo = String(cena.arquivo_midia || "").toLowerCase().endsWith(".mp4");
    const setBadge = (texto, cls = "badge-warn") => {
      stEl.textContent = texto;
      stEl.className = `badge ${cls}`;
    };
    if (!temMidiaDeclarado) {
      setBadge("Aguardando geração");
      return;
    }
    // HEAD request — só verifica existência/resposta sem baixar a mídia inteira
    const mediaUrl = `/api/cena_media/${encodeURIComponent(S.projeto_id)}/${cena.id}`;
    try {
      const resp = await fetch(mediaUrl, { method: "HEAD" });
      if (resp.ok) {
        setBadge(ehVideoArquivo ? "VÍDEO PRONTO" : "Mídia pronta", "badge-ok");
      } else if (resp.status === 404) {
        // ITEM 7/10 — o HEAD não traz corpo: busca o `reason` no JSON do 404 (GET
        // leve; o arquivo não existe, então a resposta é só o JSON de erro).
        const motivo = await _motivo404CenaMedia(mediaUrl);
        if (motivo === "arquivo_deletado") {
          midiaDeletada = true;
          setBadge("Arquivo deletado — aguardando reconstrução");
          return;
        }
        setBadge("Aguardando geração");
      } else {
        setBadge("ERRO");
      }
    } catch (e) {
      setBadge("OFFLINE");
    }
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

  // A verificação de disponibilidade roda AQUI (depois dos elementos estarem
  // resolvidos): se o arquivo foi DELETADO do disco, esconde a mídia e deixa o
  // estado vazio visível — sem tentar exibir um arquivo inexistente.
  definirBadgeDisponibilidade().then(() => {
    if (!midiaDeletada) return;
    if (img) img.classList.add("hidden");
    if (video) {
      video.classList.add("hidden");
      try { video.pause(); } catch (e) {}
    }
    if (vazio) vazio.classList.remove("hidden");
    if (btnDown) btnDown.classList.add("hidden");
    if (btnDel) btnDel.classList.add("hidden");
  });

  if (!temMidia) {
    if (img) img.classList.add("hidden");
    if (video) { video.classList.add("hidden"); video.pause(); }
    if (vazio) vazio.classList.remove("hidden");
    if (btnDown) btnDown.classList.add("hidden");
    if (btnDel) btnDel.classList.add("hidden");
  } else {
    if (vazio) vazio.classList.add("hidden");
    const mediaUrl = `/api/cena_media/${encodeURIComponent(S.projeto_id)}/${cena.id}`;
    if (isVideo) {
      if (img) img.classList.add("hidden");
      if (video) {
        // Remove erro residual de abertura anterior antes de recarregar
        const errEl = $("media-modal-video-erro");
        if (errEl) errEl.remove();
        video.classList.remove("hidden");
        video.src = mediaUrl;
        video.load();  // força recarga do elemento / limpa buffer
        video.play().catch(e => console.warn("Autoplay bloqueado:", e)); // tenta iniciar streaming
        video.onerror = () => {
          console.error("Erro ao carregar vídeo:", video.error);
          try { video.classList.add("hidden"); } catch (_e) {}
          const container = $("media-modal-container");
          if (container && !$("media-modal-video-erro")) {
            const p = document.createElement("p");
            p.id = "media-modal-video-erro";
            p.style.color = "red";
            p.style.padding = "8px";
            p.textContent = "Erro ao carregar vídeo. Tente novamente.";
            container.appendChild(p);
          }
        };
      }
    } else {
      if (video) { video.classList.add("hidden"); video.pause(); }
      if (img) {
        img.src = mediaUrl;
        img.classList.remove("hidden");
      }
    }

    if (btnDown) {
      btnDown.href = `/api/cena_media/${encodeURIComponent(S.projeto_id)}/${cena.id}?download=1`;
      btnDown.classList.remove("hidden");
    }
    if (btnDel) {
      btnDel.classList.remove("hidden");
      btnDel.onclick = async () => {
        if (!confirm(`Excluir mídia da Cena ${cena.id}?`)) return;
        const res = await apiJson(`/api/cena/${encodeURIComponent(S.projeto_id)}/${cena.id}/excluir_midia`, {});
        if (res.success) {
          fecharModalMedia();
          if (typeof carregarStudio2Dados === "function") carregarStudio2Dados(S.projeto_id);
          pollGaleria();
          pollFlowStatus();
        }
      };
    }
  }

  modal.classList.remove("hidden");
}

async function abrirMediaModalCena(scene_id) {
  if (!S.projeto_id) return;
  try {
    const r = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/status`);
    if (r && r.cenas) {
      const cena = r.cenas.find(c => Number(c.scene_index || c.id) === Number(scene_id));
      if (cena) {
        abrirModalMedia(cena);
        return;
      }
    }
  } catch (e) {}
  abrirModalMedia({ id: scene_id });
}
window.abrirMediaModalCena = abrirMediaModalCena;

function fecharModalMedia() {
  const modal = $("media-modal");
  if (!modal) return;
  modal.classList.add("hidden");
  const video = $("media-modal-video");
  if (video) { video.pause(); video.src = ""; }
  const errEl = $("media-modal-video-erro");
  if (errEl) errEl.remove();
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

  if ($("btn-exportar-capcut")) {
    $("btn-exportar-capcut").addEventListener("click", () => exportarCapCut(""));
  }
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

  // Carrega lista de projetos na tela inicial
  carregarProjetosRecentesHome();

  // ANTIGRAVITY Passo 2: fonte ÚNICA de inicialização — todos os módulos do SPA
  // sobem aqui no MESMO DOMContentLoaded. Antes havia 3 listeners (este init,
  // window.addEventListener e document.addEventListener) que registravam callbacks
  // DUPLICADOS em botões do Studio 2.0, disparando requisições em dobro.
  initStudio2();

  // Boot unificado de contas Flow (antes: listener DOMContentLoaded separado en
  // _bootFlowContas): carrega la lista y programa el refresco cada 5 min para
  // detectar el reset diário de créditos.
  carregarContasSimples();
  if (_flowContasTimer) clearInterval(_flowContasTimer);
  _flowContasTimer = setInterval(carregarContasSimples, 5 * 60 * 1000);

  initCharacterIntelligenceUI();
  initLiveTerminalHUD();

  // Suporta abrir direto pela URL /projeto/<id>.
  // A tela inicial (Dashboard) SEMPRE abre primeiro — o último projeto ativo
  // NÃO é reaberto automaticamente; fica disponível na lista de projetos recentes.
  const m = location.pathname.match(/^\/projeto\/([^/]+)/);
  if (m && m[1]) {
    const pid = decodeURIComponent(m[1]);
    if (pid) {
      abrirProjetoDaUrl(pid);
    }
  }
}
document.addEventListener("DOMContentLoaded", init);


/* ============================================================
   ULTRACUT3 STUDIO 2.0 — CONTROLADOR CLIENT-SIDE (EXPERIÊNCIA REFINADA)
   ============================================================ */

let S2_POLL_TIMER = null;
// PHASE 2 (ERRO 6): stream SSE da fila de produção. Substitui o polling fixo de
// 3s por eventos do backend (<1s) e mantém um polling de segurança espaçado
// (30s) apenas como rede de proteção caso o EventSource caia.
// S2_SSE_SIG guarda a assinatura do último evento aplicado — evita re-render
// redundante quando o backend reenvia o mesmo estado (ex.: eventuais pings).
let S2_SSE = null;
let S2_SSE_SIG = "";
let S2_SSE_REFRESH_TS = 0;
const S2_SSE_REFRESH_MIN_MS = 400; // throttle do re-render disparado pelo SSE
let S2_SSE_TRAILING = null;        // garante que o último evento do throttle é aplicado
let S2_ALERTA_CREDITOS_FECHADO = false; // PHASE 2 (ERRO 2): banner fechado pelo operador
// REQ 3 — último motivo de parada já avisado ao operador. Evita repetir o mesmo
// toast a cada polling/evento SSE; quando o motivo muda (crédito → manual, etc.)
// um novo aviso é mostrado.
let S2_ULTIMO_PAUSE_REASON = "";
// REQ 4 — última navegação automática já executada ("projeto:motivo:aba"). Evita
// navegar duas vezes pelo mesmo motivo (o SSE reconecta e reenvia o evento).
let S2_ULTIMA_NAVEGACAO = "";
let _pollTranscricaoTimer = null; // ANTIGRAVITY: polling de transcrição (global, parado por pararTodosPollings)
let S2_ACTIVE_TAB = "studio";

function initStudio2() {

  // Contas Google Flow (card na aba Estúdio)
  if ($("btn-add-conta-flow")) {
    $("btn-add-conta-flow").addEventListener("click", async () => {
      const nome = prompt("Nome da nova conta (ex: Conta 4):");
      if (!nome) return;
      try {
        await apiJson('/api/flow/contas/adicionar', { nome });
        carregarContasFlow();
      } catch (e) {
        alert("Erro ao adicionar conta: " + e.message);
      }
    });
  }
  carregarContasFlow();

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
  const panelDev = $("s2-dev-panel");
  if ($("btn-fechar-modo-dev") && panelDev) {
    $("btn-fechar-modo-dev").addEventListener("click", () => {
      panelDev.classList.add("hidden");
    });
  }

  // Frente 3: Dropdown "⚡ Ações ▾" consolida modo-dev, modo-clássico,
  // exportar CapCut, importar imagens e baixar prompts (antes: 5 botões soltos).
  const btnAcoes = $("btn-s2-acoes");
  const menuAcoes = $("s2-acoes-menu");
  if (btnAcoes && menuAcoes) {
    btnAcoes.addEventListener("click", (e) => {
      e.stopPropagation();
      const aberto = !menuAcoes.classList.contains("hidden");
      fecharDropdowns();
      if (!aberto) {
        menuAcoes.classList.remove("hidden");
        btnAcoes.setAttribute("aria-expanded", "true");
      }
    });
    menuAcoes.addEventListener("click", (e) => {
      const item = e.target.closest("[data-acao]");
      if (!item) return;
      const acao = item.dataset.acao;
      fecharDropdowns();
      if (acao === "modo-dev" && panelDev) {
        panelDev.classList.toggle("hidden");
      } else if (acao === "modo-classico") {
        mostrarTela("tela-manual");
        $("manual-projeto-nome").textContent = S.projetoNome || S.projeto_id || "";
        iniciarManual();
      } else if (acao === "exportar-capcut") {
        // Fase 2: mesma função/rota v2 do botão oficial da Aba 6
        // (POST /api/v2/montagem/<id>/exportar_capcut — duração corrigida).
        exportarCapCutDireto();
      } else if (acao === "importar-imagens") {
        importarImagens(); // usa o modal existente
      } else if (acao === "baixar-prompts") {
        // Fase 2: aponta para a rota v2 real (storyboard_prompts.txt do servidor)
        // em vez do .txt client a partir de S.cenas (vazio no Studio2).
        if (!S.projeto_id) {
          showToast("❌ Nenhum projeto ativo selecionado.");
          return;
        }
        window.open(`/api/v2/arquivos/${encodeURIComponent(S.projeto_id)}/download/prompts/storyboard_prompts.txt`, "_blank");
      }
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
        atualizarBadgeAudioS2("erro");
      } finally {
        // ANTIGRAVITY Passo 2: garante que o botão NUNCA fique eternamente
        // desabilitado, mesmo em falha inesperada (o polling também reativa).
        $("btn-s2-transcrever").disabled = false;
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
          const painel = $("s2-painel-transcricao");
          if (painel) painel.style.display = "block";
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
  if ($("btn-s2-gerar-cenas-srt")) {
    $("btn-s2-gerar-cenas-srt").addEventListener("click", async () => {
      const btn = $("btn-s2-gerar-cenas-srt");
      btn.disabled = true;
      btn.textContent = "⏳ Planejando Cenas (70% Imagens / 30% B-Roll)...";
      try {
        const r = await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/gerar_scene_plan`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true })
        });
        if (r && r.success) {
          btn.textContent = "✅ Cenas Geradas com Sucesso!";
          await carregarStudio2Dados(S.projeto_id);
          const tabPrompts = document.querySelector('[data-s2-tab="prompts"]');
          if (tabPrompts) tabPrompts.click();
        } else {
          alert("Aviso: " + (r.error || "Não foi possível gerar cenas automaticamente"));
          btn.textContent = "✨ Planejar e Gerar Cenas do Roteiro (70% Imagens / 30% B-Roll)";
        }
      } catch (err) {
        alert("Erro ao gerar cenas: " + err.message);
        btn.textContent = "✨ Planejar e Gerar Cenas do Roteiro (70% Imagens / 30% B-Roll)";
      } finally {
        btn.disabled = false;
      }
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
  document.querySelectorAll('input[name="s2-modo-producao"]').forEach((r) => r.addEventListener("change", async () => {
    await salvarConfigS2();
    if (S.projeto_id) {
      try {
        await api(`/api/v2/storyboard/${encodeURIComponent(S.projeto_id)}/gerar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        });
        await carregarStudio2Dados(S.projeto_id);
      } catch (e) {
        console.warn("Erro ao atualizar modo de produção:", e);
      }
    }
  }));

  // Abas de Filtro do Plano de Edição com Reprocessamento Real no Backend
  document.querySelectorAll(".s2-plano-tab").forEach(tab => {
    tab.addEventListener("click", async () => {
      if (!S.projeto_id) return;
      const filtro = tab.dataset.filter || "all";
      const mapaModo = {
        "all": "imagem_video_texto",
        "img_vid": "imagem_video",
        "only_img": "somente_imagens"
      };
      const novoModo = mapaModo[filtro] || "imagem_video";

      // Sincroniza estado visual das abas e radio
      document.querySelectorAll(".s2-plano-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      _PLANO_EDICAO_FILTER = filtro;

      const rRadio = document.querySelector(`input[name="s2-modo-producao"][value="${novoModo}"]`);
      if (rRadio) rRadio.checked = true;

      // Feedback visual de carregamento
      const btnRefazer = $("btn-s2-refazer-plano");
      if (btnRefazer) {
        btnRefazer.innerHTML = "↻ recalculando...";
        btnRefazer.disabled = true;
      }

      try {
        // 1. Salva o novo modo no meta.json
        await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ modo_producao: novoModo })
        });

        // 2. Reprocessa o planejamento completo sob a nova restrição
        const r = await api(`/api/v2/storyboard/${encodeURIComponent(S.projeto_id)}/gerar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        });

        // 3. Recarrega os dados completos da tela
        if (r && r.success) {
          await carregarStudio2Dados(S.projeto_id);
        }
      } catch (e) {
        console.error("Erro ao reprocessar plano pelas abas:", e);
      } finally {
        if (btnRefazer) {
          btnRefazer.innerHTML = "↻ refazer";
          btnRefazer.disabled = false;
        }
      }
    });
  });

  // Botão ↻ refazer do Plano de Edição
  if ($("btn-s2-refazer-plano")) {
    $("btn-s2-refazer-plano").addEventListener("click", async () => {
      if (!S.projeto_id) return;
      const btn = $("btn-s2-refazer-plano");
      const origHtml = btn.innerHTML;
      btn.innerHTML = "↻ recalculando...";
      btn.disabled = true;
      try {
        await salvarConfigS2();
        const r = await api(`/api/v2/storyboard/${encodeURIComponent(S.projeto_id)}/gerar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        });
        if (r && r.success) {
          await carregarStudio2Dados(S.projeto_id);
        }
      } catch (e) {
        console.error("Erro ao refazer plano de edição:", e);
      } finally {
        btn.innerHTML = origHtml;
        btn.disabled = false;
      }
    });
  }

  // 4b. Gerar Prompts base (determinístico — prompt_engine com locks e continuidade)
  if ($("btn-s2-gerar-base")) {
    $("btn-s2-gerar-base").addEventListener("click", async () => {
      const btn = $("btn-s2-gerar-base");
      if (btn) { btn.disabled = true; btn.textContent = "⚡ Gerando..."; }
      try {
        const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
        const r = await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ estilo_visual: estilo })
        });
        if (r && r.success) {
          await carregarStudio2Dados(S.projeto_id);
          await carregarPromptsGridS2(S.projeto_id);
          // Exibir botão "Ir para Flow" após sucesso (mesmo padrão do btn-s2-gerar-tudo)
          const btnIrFlow = $("btn-s2-ir-flow");
          if (btnIrFlow) {
            btnIrFlow.style.display = "inline-block";
            btnIrFlow.disabled = false;
          }
          alert(`✓ Prompts base gerados! ${r.total || ""} cenas prontas.`);
        } else {
          alert("Aviso: " + ((r && r.error) || "Não foi possível gerar os prompts base."));
        }
      } catch (e) {
        alert("Erro ao gerar prompts base: " + e.message);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = "⚡ Rápido (Local)"; }
      }
    });
  }

  // 4c. Gerar Prompts (DeepSeek — imagem + animação SEQUENCIAIS em 1 chamada)
  if ($("btn-s2-gerar-tudo")) {
    $("btn-s2-gerar-tudo").addEventListener("click", async () => {
      const btn = $("btn-s2-gerar-tudo");
      const prog = $("s2-prompts-progress");
      const progTitle = $("s2-prompts-progress-title");
      const progDesc = $("s2-prompts-progress-desc");
      if (btn) { btn.disabled = true; btn.textContent = "🚀 Gerando prompts..."; }
      if (prog) prog.style.display = "block";
      if (progTitle) progTitle.textContent = "DeepSeek Prompt Director em execução...";
      if (progDesc) progDesc.textContent = "Lendo a transcrição de cada cena e gerando prompt_imagem + prompt_animacao como sequência...";

      try {
        const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
        const eeatEnabled = ($("checkbox-eeat")?.checked || false);
        const r = await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/gerar_prompts_ia`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            estilo_visual: estilo,
            eeat_enabled: eeatEnabled
          })
        });
        if (!r || !r.success) {
          alert("Aviso: " + ((r && r.error) || "Não foi possível iniciar a geração de prompts."));
          if (prog) prog.style.display = "none";
          if (btn) { btn.disabled = false; btn.textContent = "🚀 Avançado (DeepSeek)"; }
          return;
        }
        // Polling do job assíncrono
        const pid = S.projeto_id;
        let concluido = false;
        for (let tent = 0; tent < 600 && !concluido; tent++) {
          await new Promise(res => setTimeout(res, 1000));
          const st = await api(`/api/v2/projeto/${encodeURIComponent(pid)}/prompt_ia_status`);
          if (st && st.status === "concluido") {
            concluido = true;
          } else if (st && st.status === "erro") {
            alert("Erro ao gerar prompts: " + (st.erro || "erro desconhecido"));
            break;
          } else if (st && st.progresso !== undefined) {
            let descricao = `Gerando prompts... ${st.progresso}/100 cenas`;
            // Custo DeepSeek estimado, exibido em tempo real durante o polling
            const custo = Number(st.custo_estimado_usd) || 0;
            const totalCenas = Number(st.total_cenas) || 0;
            const custoPorCena = totalCenas > 0 ? (custo / totalCenas).toFixed(4) : "0.0000";
            descricao += ` | 💰 Custo: $${custo.toFixed(2)} (~$${custoPorCena}/cena)`;
            if (progDesc) progDesc.textContent = descricao;
            if (progTitle) progTitle.textContent = st.etapa || "Gerando prompts...";
          }
        }
        if (concluido) {
          if (prog) prog.style.display = "none";
          await carregarStudio2Dados(pid);
          await carregarPromptsGridS2(pid);
          // Mostra e habilita "⚡ Enviar para o Flow →" (sem disparo automático)
          const btnIrFlow = $("btn-s2-ir-flow");
          if (btnIrFlow) { btnIrFlow.style.display = "inline-block"; btnIrFlow.disabled = false; }
          alert("✓ Prompts gerados! Imagem + animação sequenciais. Avance para a Produção Flow quando quiser.");
        }
      } catch (e) {
        alert("Erro ao gerar prompts: " + e.message);
      } finally {
        if (btn && btn.style.display !== "none") {
          btn.disabled = false;
          btn.textContent = "🚀 Avançado (DeepSeek)";
        }
      }
    });
  }

  if ($("btn-s2-ir-flow")) {
    $("btn-s2-ir-flow").addEventListener("click", () => {
      trocarAbaStudio2("producao");
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

  // Produção: Iniciar Fila ("⚡ Enviar Prompts para o Flow" / "▶ Retomar Projeto (N restantes)")
  const iniciarFilaHandler = async () => {
    const btnFila = $("btn-s2-iniciar-fila");
    const rotuloOriginal = btnFila ? btnFila.textContent : "";
    try {
      // REQ (demora em "gerar restantes"): o clique encadeia confirmação + POST
      // pesado (scan das cenas + Chrome CDP). Desabilita o botão e mostra o estado
      // durante o await — cliques repetidos refaziam todo o trabalho de I/O e eram
      // percebidos como "travamento" da interface.
      if (btnFila) {
        btnFila.disabled = true;
        btnFila.textContent = "⏳ Preparando fila...";
      }
      // PHASE 2 (ERRO 2): confirmação explícita ANTES de consumir créditos.
      // Mostra cenas pendentes, créditos disponíveis (soma das contas) e o
      // estado do fallback video→imagem — evita consumo silencioso.
      const confirmado = await confirmarInicioFilaS2(S.projeto_id);
      if (!confirmado) {
        console.log("[FILA] Início cancelado pelo operador.");
        return;
      }
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
    } finally {
      if (btnFila) {
        btnFila.disabled = false;
        // Só restaura o rótulo quando ele ainda está no estado de espera — se
        // `carregarStudio2Dados` já atualizou (ex.: "▶ Retomar Projeto (N restantes)"),
        // esse valor é preservado.
        if (String(btnFila.textContent || "").indexOf("Preparando fila") !== -1) {
          btnFila.textContent = rotuloOriginal || "⚡ Enviar Prompts para o Flow";
        }
      }
    }
  };

  // Handler — Animar B-Roll em lote
  if ($("btn-s2-animar-broll")) {
    $("btn-s2-animar-broll").addEventListener("click", async () => {
      const btn = $("btn-s2-animar-broll");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ Iniciando Animação...";
      }
      try {
        // Reclassificar cenas animáveis antes de filtrar (aligned estado real dos prompts)
        try {
          await apiJson(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/reclassificar_animacoes`, {});
          await new Promise(r => setTimeout(r, 600)); // Aguardar processamento backend
        } catch (e) {
          console.warn("Reclassificação de animações falhou (opcional):", e);
        }

        const prod = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/status`);
        const brollIds = (prod.cenas || [])
          .filter(c => {
            const ehBrollAnimado = (
              c.animar === true ||
              c.animate_later === true ||
              c.animar_depois === true ||
              c.tipo === "video" ||
              c.media_intent === "video"
            );
            // CORREÇÃO 1: exclui cenas avatar — avatar sempre gera imagem, nunca vídeo
            const ehAvatar = c.uses_character === true ||
              c.scene_type === "avatar_talking" ||
              c.scene_type === "avatar_action" ||
              c.narrative_role === "avatar" ||
              c.visual_role === "avatar";
            const vidStatus = (c.video_status || "").toUpperCase();
            const naoFinalizado = vidStatus !== "DONE" && vidStatus !== "READY";
            return ehBrollAnimado && naoFinalizado && !ehAvatar;
          })
          .map(c => Number(c.scene_index || c.scene_id || c.id));

        if (brollIds.length === 0) {
          alert("Nenhum B-Roll pendente para animar (todas as cenas de B-Roll já foram animadas ou estão prontas).");
          return;
        }

        const r = await apiJson(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/iniciar_fila`,
          { scene_ids: brollIds, modo: "animacao" });

        if (r && r.success) {
          if (typeof showToast === "function") {
            showToast(`🚀 Fila de Animação B-Roll iniciada! ${r.enfileiradas || brollIds.length} cenas em fila.`, "success");
          }
          if (!termExpanded) toggleTerminalExpanded();
          pollLiveTerminalHUD();
          await carregarStudio2Dados(S.projeto_id);
        } else if (r && r.already_running) {
          if (typeof showToast === "function") {
            showToast("⚡ A produção já está em andamento no Google Flow.", "info");
          }
          if (!termExpanded) toggleTerminalExpanded();
          pollLiveTerminalHUD();
        } else {
          alert("Erro ao animar B-Roll: " + ((r && (r.error || r.message)) || "resposta inesperada"));
        }
      } catch (e) {
        alert("Erro ao animar B-Roll: " + e.message);
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "🎬 Animar B-Roll";
        }
      }
    });
  }

  if ($("btn-s2-iniciar-fila")) {
    $("btn-s2-iniciar-fila").addEventListener("click", iniciarFilaHandler);
  }
  if ($("btn-s2-produzir-pendentes")) {
    $("btn-s2-produzir-pendentes").addEventListener("click", iniciarFilaHandler);
  }
  // Ir para Roteiro & Prompts (Aba 1 -> Aba 2)
  if ($("btn-s2-ir-producao")) {
    $("btn-s2-ir-producao").addEventListener("click", () => {
      trocarAbaStudio2("prompts");
    });
  }

  // Arquivos & Montagem: Download de Todas as Imagens (ZIP)
  const baixarZipImagensHandler = () => {
    if (!S.projeto_id) {
      alert("Nenhum projeto ativo.");
      return;
    }
    window.open(`/projeto/${encodeURIComponent(S.projeto_id)}/imagens_zip`, "_blank");
  };

  if ($("btn-s2-baixar-zip-arquivos")) {
    $("btn-s2-baixar-zip-arquivos").addEventListener("click", baixarZipImagensHandler);
  }
  if ($("btn-s2-baixar-zip-montagem")) {
    $("btn-s2-baixar-zip-montagem").addEventListener("click", baixarZipImagensHandler);
  }

  // Arquivos: Filtros da Galeria
  document.querySelectorAll("#s2-arquivo-filtros .s2-plano-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#s2-arquivo-filtros .s2-plano-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      _ARQUIVO_FILTRO = tab.dataset.filtroArq || "todas";
      aplicarFiltroGaleriaArquivo();
    });
  });

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
    $("btn-s2-recarregar-arquivos").addEventListener("click", async () => {
      if (!S.projeto_id) return;
      try {
        await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/indexar_midias`, { method: "POST" });
      } catch (e) {}
      await carregarStudio2Dados(S.projeto_id);
    });
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
        $("btn-s2-exportar-capcut").textContent = "✂ Exportar para CapCut";
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

  // Log de navegação entre abas no console (persistido via backend, isolado por projeto)
  if (tabName && S.projeto_id) {
    const nomeAba = (tabName.charAt(0).toUpperCase() + tabName.slice(1));
    apiJson("/api/v2/log", { projeto_id: S.projeto_id, message: `[NAVEGAÇÃO] Aba ${nomeAba} aberta`, status: "concluido" }).catch(() => {});
  }

  if (S.projeto_id) {
    if (tabName === "prompts") {
      carregarPromptsGridS2(S.projeto_id);
    } else if (tabName === "producao") {
      atualizarStatusProducaoS2(S.projeto_id);
    } else if (tabName === "arquivos") {
      renderGaleriaArquivosS2(S.projeto_id);
      atualizarArquivosS2(S.projeto_id);
    } else if (tabName === "montagem") {
      if (typeof carregarTransicoesCapCut === "function") carregarTransicoesCapCut();
      // TAREFA 8: reaplica a sub-aba persistida (sobrevive a troca de projeto/aba).
      if (typeof aplicarSubAbaMontagemPersistida === "function") aplicarSubAbaMontagemPersistida();
      atualizarMontagemS2(S.projeto_id);
    } else if (tabName === "exportacao") {
      atualizarExportacaoS2(S.projeto_id);
    }
  }
}
window.irParaAbaS2 = trocarAbaStudio2;

/* ============================================================
   REQ 5 — NOTIFICAÇÃO VISUAL DE PAUSA DA FILA (toast com AÇÃO)
   ============================================================
   Quando a fila é interrompida (créditos zerados / fim de fila / pausa manual) o
   operador precisa de feedback CLARO e de uma ação em 1 clique. O toast simples de
   showToast() só aceita texto, então este cartão — no MESMO #toast-container —
   traz título, corpo, contagem de B-roll pendente e botões (ação + fechar),
   com auto-fechamento. Mensagens derivadas do `pause_reason` canônico do backend.
   Segurança: TODO texto entra por textContent (nunca innerHTML) — nada de HTML
   dinâmico vindo do backend.
*/
const PAUSA_MENSAGENS = {
  credito_esgotado_video: {
    titulo: "⚠️ Créditos de Vídeo Zerados",
    corpo: "Os créditos de VÍDEO acabaram em todas as contas Flow. As cenas B-roll ficaram pendentes de animação.",
    acao: "Ir para Montagem",
    destino: "montagem",
    cor: "warning",
    auto: 8000,
  },
  credito_esgotado_imagem: {
    titulo: "⚠️ Créditos de Imagem Zerados",
    corpo: "Os créditos acabaram em todas as contas Flow. Nenhuma nova imagem pode ser gerada agora.",
    acao: "Ir para Montagem",
    destino: "montagem",
    cor: "warning",
    auto: 8000,
  },
  fim_fila_credito_zerado: {
    titulo: "✅ Fila Encerrada",
    corpo: "Todos os créditos foram usados. As cenas geradas estão prontas para a montagem.",
    acao: "Editar Vídeo",
    destino: "montagem",
    cor: "success",
    auto: 8000,
  },
  manual: {
    titulo: "⏸️ Fila Pausada",
    corpo: "A geração foi pausada manualmente. Você pode retomar quando quiser.",
    acao: "Retomar na aba Produção",
    destino: "producao",
    cor: "info",
    auto: 10000,
  },
};
let S2_ULTIMO_TOAST_PAUSA = ""; // dedup: nunca repete o mesmo aviso de pausa

/** Cenas B-roll (vídeo) ainda pendentes — mesma regra do botão "Animar B-Roll". */
function _contarBrollPendenteS2(prod) {
  try {
    return ((prod && prod.cenas) || []).filter((c) => {
      const ehVideo = c.tipo === "video" || c.media_intent === "video"
        || c.animate_later === true || c.animar_depois === true || c.animar === true;
      if (!ehVideo) return false;
      const ehAvatar = c.uses_character === true
        || c.scene_type === "avatar_talking" || c.scene_type === "avatar_action"
        || c.narrative_role === "avatar" || c.visual_role === "avatar";
      if (ehAvatar) return false;
      const vidStatus = String(c.video_status || "").toUpperCase();
      const temVideo = /\.(mp4|mov|webm)$/i.test(String(c.arquivo_midia || ""));
      return !temVideo && vidStatus !== "DONE" && vidStatus !== "READY";
    }).length;
  } catch (e) {
    return 0;
  }
}

/**
 * Mostra o aviso de pausa da fila (REQ 5).
 * @param {string} pauseReason motivo canônico (pause_reason do /status).
 * @param {object} opcoes      { pendentesBroll: number }
 * @returns {HTMLElement|null} elemento criado (ou null quando não há aviso).
 */
function showPauseNotification(pauseReason, opcoes) {
  try {
    const motivo = String(pauseReason || "").trim();
    const cfg = PAUSA_MENSAGENS[motivo];
    if (!cfg) return null;                              // motivo não notificável
    if (S2_ULTIMO_TOAST_PAUSA === motivo) return null;   // dedup por motivo
    S2_ULTIMO_TOAST_PAUSA = motivo;

    const pendentes = Number((opcoes || {}).pendentesBroll || 0);
    let corpo = cfg.corpo;
    if (pendentes > 0 && motivo !== "manual") {
      corpo += ` ${pendentes} cena(s) B-roll pendente(s) de vídeo.`;
    }

    let cont = document.getElementById("toast-container");
    if (!cont) {
      cont = document.createElement("div");
      cont.id = "toast-container";
      document.body.appendChild(cont);
    }

    const el = document.createElement("div");
    el.className = `toast toast-pausa toast-pausa-${cfg.cor}`;
    el.setAttribute("role", "alert");

    const elTitulo = document.createElement("div");
    elTitulo.className = "toast-pausa-titulo";
    elTitulo.textContent = cfg.titulo;

    const elCorpo = document.createElement("div");
    elCorpo.className = "toast-pausa-corpo";
    elCorpo.textContent = corpo;

    const elAcoes = document.createElement("div");
    elAcoes.className = "toast-pausa-acoes";

    const btnAcao = document.createElement("button");
    btnAcao.type = "button";
    btnAcao.className = "toast-pausa-btn";
    btnAcao.textContent = cfg.acao;
    btnAcao.addEventListener("click", () => {
      console.log(`[NAV] [PAUSA] Ação '${cfg.acao}' (motivo: ${motivo}) → aba ${cfg.destino}`);
      fechar();
      if (typeof irParaAbaS2 === "function") irParaAbaS2(cfg.destino);
    });

    const btnFechar = document.createElement("button");
    btnFechar.type = "button";
    btnFechar.className = "toast-pausa-fechar";
    btnFechar.setAttribute("aria-label", "Fechar aviso de pausa");
    btnFechar.textContent = "✕";
    btnFechar.addEventListener("click", () => fechar());

    elAcoes.appendChild(btnAcao);
    elAcoes.appendChild(btnFechar);
    el.appendChild(elTitulo);
    el.appendChild(elCorpo);
    el.appendChild(elAcoes);
    cont.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));

    const timer = setTimeout(() => fechar(), cfg.auto || 8000);

    function fechar() {
      try { clearTimeout(timer); } catch (e) {}
      el.classList.remove("show");
      setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 250);
    }
    return el;
  } catch (e) {
    console.log("[toast-pausa]", pauseReason, e);
    return null;
  }
}
window.showPauseNotification = showPauseNotification;

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
  pararTodosPollings();
  S.projeto_id = projeto_id;
  S.projetoId = projeto_id;
  S.studio_version = "v2";
  atualizarUrlProjeto(projeto_id);

  // ANTIGRAVITY Passo 2: limpa caches de renderização do DOM ao trocar de projeto
  // — os IDs numéricos das cenas (1,2,3...) coincidem entre projetos e o algoritmo
  // in-place assumiria cards já renderizados, exibindo dados do projeto anterior.
  if (typeof _S2_STORY_RENDER_CACHE !== "undefined") _S2_STORY_RENDER_CACHE.clear();
  if (typeof _S2_PROD_RENDER_CACHE !== "undefined") _S2_PROD_RENDER_CACHE.clear();

  mostrarTela("tela-studio2");
  setNavAtivo("projetos");
  $("s2-projeto-nome").textContent = S.projetoNome || projeto_id;
  atualizarTopbar(projeto_id, "andamento", "Studio 2.0");

  carregarPreviewAvatarS2(projeto_id);
  await carregarDadosPersonagemS2(projeto_id);
  await carregarStudio2Dados(projeto_id);

  // PHASE 2 (ERRO 6): SSE em tempo real (<1s) substitui o polling fixo de 3s.
  // O polling de 30s permanece apenas como rede de segurança caso o
  // EventSource caia (o navegador reconecta sozinho em ~3s).
  if (S2_POLL_TIMER) clearInterval(S2_POLL_TIMER);
  S2_POLL_TIMER = setInterval(() => {
    if (S.projeto_id === projeto_id && $("tela-studio2").classList.contains("ativa")) {
      atualizarStatusProducaoS2(projeto_id);
    }
  }, 30000);
  iniciarSSEProducao(projeto_id);
}

async function atualizarStatusStudio2(projeto_id) {
  return await carregarStudio2Dados(projeto_id);
}
window.atualizarStatusStudio2 = atualizarStatusStudio2;

/* ---------- MODELO por Tipo de Mídia (dropdowns separados, sem radio) ----------
   IMAGEM → Nano Banana 2, Nano Banana Pro, Imagen 4, Imagen 4 Ultra
   VÍDEO  → Veo 3.1 - Lite, Veo 3.1 - Quality
   Default: Nano Banana 2 (IMAGEM) / Veo 3.1 - Lite (VÍDEO) */
const MODELOS_MODELO_IMAGEM = ["Nano Banana 2", "Nano Banana Pro", "Imagen 4", "Imagen 4 Ultra"];
const MODELOS_MODELO_VIDEO = ["Veo 3.1 - Lite", "Veo 3.1 - Quality"];
const MODELO_PADRAO_IMAGEM = "Nano Banana 2";
const MODELO_PADRAO_VIDEO = "Veo 3.1 - Lite";

function _popularSelectModelos(selId, modelos, padrao, valorPreferido) {
  const sel = $(selId);
  if (!sel) return;
  const valorAtual = valorPreferido || sel.value || "";
  sel.innerHTML = "";
  modelos.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    sel.appendChild(opt);
  });
  sel.value = modelos.includes(valorAtual) ? valorAtual : padrao;
}

function filterModeloByTipo() {
  // Preenche SEPARADAMENTE os dropdowns de imagem e de vídeo (sem radio de tipo de saída).
  _popularSelectModelos("s2-prod-modelo-imagem", MODELOS_MODELO_IMAGEM, MODELO_PADRAO_IMAGEM);
  _popularSelectModelos("s2-prod-modelo-video", MODELOS_MODELO_VIDEO, MODELO_PADRAO_VIDEO);
  // Retrocompat: se o HTML antigo ainda tiver #s2-prod-modelo único, mantém imagem.
  if ($("s2-prod-modelo") && $("s2-prod-modelo-imagem")) {
    $("s2-prod-modelo").innerHTML = $("s2-prod-modelo-imagem").innerHTML;
    $("s2-prod-modelo").value = $("s2-prod-modelo-imagem").value;
  }
}
window.filterModeloByTipo = filterModeloByTipo;
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

      // Carregar configurações de produção salvas (modelos/qualidades separados por mídia)
      filterModeloByTipo(); // popula os dropdowns separados IMAGEM e VÍDEO
      // IMAGEM: prod_modelo_imagem → fallback prod_modelo (compatibilidade meta antigo)
      const modeloImgSalvo = m.prod_modelo_imagem || m.prod_modelo || "";
      const selModeloImg = $("s2-prod-modelo-imagem");
      if (selModeloImg && modeloImgSalvo && Array.from(selModeloImg.options).some(o => o.value === modeloImgSalvo)) {
        selModeloImg.value = modeloImgSalvo;
      }
      // VÍDEO: prod_modelo_video (default Veo 3.1 - Lite já preenchido)
      const selModeloVid = $("s2-prod-modelo-video");
      if (selModeloVid && m.prod_modelo_video && Array.from(selModeloVid.options).some(o => o.value === m.prod_modelo_video)) {
        selModeloVid.value = m.prod_modelo_video;
      }
      // Qualidade por cena da IMAGEM: prod_qualidade_imagem → fallback prod_qualidade
      const qImgSalvo = m.prod_qualidade_imagem || m.prod_qualidade || "";
      const selQImg = $("s2-prod-qualidade-imagem");
      if (selQImg && qImgSalvo && Array.from(selQImg.options).some(o => o.value === qImgSalvo)) {
        selQImg.value = qImgSalvo;
      }
      // Qualidade por cena do VÍDEO (x1/x2)
      const selQVid = $("s2-prod-qualidade-video");
      if (selQVid && m.prod_qualidade_video && Array.from(selQVid.options).some(o => o.value === m.prod_qualidade_video)) {
        selQVid.value = m.prod_qualidade_video;
      }
      if ($("s2-prod-qualidade-download") && m.prod_qualidade_download) $("s2-prod-qualidade-download").value = m.prod_qualidade_download;
      if ($("s2-prod-proporcao") && m.prod_proporcao) $("s2-prod-proporcao").value = m.prod_proporcao;
      if ($("s2-prod-config-badge") && (m.prod_modelo || m.prod_modelo_imagem || m.prod_modelo_video || m.prod_qualidade || m.prod_qualidade_imagem || m.prod_qualidade_video)) {
        $("s2-prod-config-badge").textContent = "✓ Configurado";
        $("s2-prod-config-badge").className = "badge badge-ok";
      }

      // Carregar configurações de provedores de IA salvos
      if ($("s2-select-provedor-storyboard") && m.provedor_storyboard) $("s2-select-provedor-storyboard").value = m.provedor_storyboard;
      if ($("s2-select-provedor-prompts") && m.provedor_prompts) $("s2-select-provedor-prompts").value = m.provedor_prompts;
      if ($("s2-ai-providers-badge") && m.provedor_storyboard) {
        $("s2-ai-providers-badge").textContent = "✓ Customizado";
        $("s2-ai-providers-badge").className = "badge badge-ok";
      }

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
    await carregarPromptsGridS2(projeto_id);

    // 4. Carrega Arquivos
    await atualizarArquivosS2(projeto_id);

    // 5. Carrega Montagem
    await atualizarMontagemS2(projeto_id);
  } catch (e) {
    console.error("Erro ao carregar dados Studio 2.0:", e);
  }
}

async function carregarPromptsGridS2(projeto_id) {
  const box = $("s2-prompts-grid");
  if (!box) return;
  try {
    const res = await api(`/api/v2/producao/${encodeURIComponent(projeto_id)}/status`);
    const cenas = (res && res.cenas) ? res.cenas : [];
    if ($("s2-badge-prompts-count")) {
      $("s2-badge-prompts-count").textContent = `${cenas.length} cenas`;
    }
    if ($("s2-prompts-status-badge")) {
      if (cenas.length > 0) {
        $("s2-prompts-status-badge").className = "badge badge-ok";
        $("s2-prompts-status-badge").textContent = `✓ ${cenas.length} Cenas Prontas`;
      } else {
        $("s2-prompts-status-badge").className = "badge badge-wait";
        $("s2-prompts-status-badge").textContent = "Aguardando geração";
      }
    }
    renderPromptsGridS2(cenas);
  } catch (e) {
    box.innerHTML = `<div class="scenes-empty">Erro ao carregar prompts: ${esc(e.message)}</div>`;
  }
}

function renderPromptsGridS2(cenas) {
  const box = $("s2-prompts-grid");
  if (!box) return;
  if (!cenas || !cenas.length) {
    box.innerHTML = '<div class="scenes-empty">Nenhum prompt gerado ainda. Configure o áudio e o avatar no <b>Estúdio</b> e clique em <b>"Gerar Prompts com DeepSeek"</b>.</div>';
    return;
  }

  const sorted = [...cenas].sort((a, b) => Number(a.scene_index || a.id) - Number(b.scene_index || b.id));

  box.innerHTML = sorted.map(c => {
    const cid = c.scene_index || c.id;
    const tIni = parseFloat(c.tempo_inicio !== undefined ? c.tempo_inicio : (c.start || 0));
    const tFim = parseFloat(c.tempo_fim !== undefined ? c.tempo_fim : (c.end || (tIni + 5.0)));
    const ts = `[${fmtTs(tIni)} - ${fmtTs(tFim)}]`;
    const narration = c.narration || c.texto || "Sem narração";
    const promptImg = c.prompt_imagem || c.prompt || "";
    const usesChar = Boolean(c.uses_character || (c.character_ref && c.character_ref !== ""));
    const charTag = c.character_ref || "@Personagem";

    const badgeChar = usesChar 
      ? `<span class="badge badge-ok mono" style="font-size:11px">👤 ${esc(charTag)} Presente</span>`
      : `<span class="badge badge-muted" style="font-size:11px">🖼 B-Roll Cinematográfico</span>`;

    return `
      <div class="s2-card" style="padding:14px;background:var(--surface-2);border-radius:10px;display:flex;flex-direction:column;gap:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div style="display:flex;align-items:center;gap:10px">
            <span class="badge badge-primary" style="font-weight:700">Cena ${String(cid).padStart(3, '0')}</span>
            <span class="mono text-muted" style="font-size:12px">${ts}</span>
          </div>
          ${badgeChar}
        </div>

        <div style="background:var(--surface-3);border-radius:6px;padding:8px 12px;font-size:13px;color:var(--text)">
          <span style="font-size:11px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:2px">🎙 FALA / NARRAÇÃO:</span>
          "${esc(narration)}"
        </div>

        <div class="field" style="margin:0">
          <label style="font-size:11.5px;font-weight:700;color:var(--accent-light);display:flex;justify-content:space-between">
            <span>✨ PROMPT VISUAL (GOOGLE FLOW):</span>
            <span class="mono text-muted" style="font-size:11px">16:9 Cinematográfico</span>
          </label>
          <textarea id="s2-prompt-txt-${cid}" class="textarea mono" rows="3" style="font-size:12.5px;background:#09090d">${esc(promptImg)}</textarea>
        </div>

        <div style="display:flex;justify-content:flex-end;gap:8px">
          <button class="btn btn-xs btn-primary" onclick="salvarPromptIndividualCenaS2(${cid})">💾 Salvar Prompt</button>
        </div>
      </div>
    `;
  }).join("");
}

async function salvarPromptIndividualCenaS2(cid) {
  const txtEl = $(`s2-prompt-txt-${cid}`);
  if (!txtEl) return;
  const novoPrompt = txtEl.value.trim();
  try {
    const res = await api(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/cena/${cid}/prompt`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: novoPrompt, prompt_imagem: novoPrompt })
    });
    if (res && res.success) {
      alert(`✓ Prompt da Cena ${cid} salvo com sucesso!`);
      await carregarStudio2Dados(S.projeto_id);
    } else {
      alert("Erro ao salvar prompt: " + ((res && res.error) || ""));
    }
  } catch (e) {
    alert("Erro na conexão: " + e.message);
  }
}
window.salvarPromptIndividualCenaS2 = salvarPromptIndividualCenaS2;
window.carregarPromptsGridS2 = carregarPromptsGridS2;

/* ============================================================
   PHASE 2 — CRÉDITOS FLOW: confirmação + banner (ERRO 2)
   ============================================================ */

/**
 * Mostra/oculta o banner de créditos conforme o estado real do backend.
 * Auto-oculta quando o fallback normaliza (créditos voltaram).
 */
function atualizarBannerCreditosS2(fallbackAtivo, creditosRestantes) {
  const el = $("s2-alerta-creditos");
  if (!el) return;
  const ativo = !!fallbackAtivo;
  if (!ativo) {
    el.classList.add("hidden");
    S2_ALERTA_CREDITOS_FECHADO = false;
    return;
  }
  // Só reescreve o texto quando ele muda (evita repaint a cada poll)
  const texto =
    "<strong>⚠️ Atenção aos Créditos:</strong> " +
    "o fallback vídeo→imagem está ATIVO — cenas de vídeo estão sendo geradas como imagem estática." +
    (creditosRestantes !== undefined && creditosRestantes !== null
      ? ` Créditos restantes nas contas Flow: <b>${creditosRestantes}</b>.`
      : "");
  const span = el.querySelector("span");
  if (span && span.innerHTML !== texto) span.innerHTML = texto;
  if (!S2_ALERTA_CREDITOS_FECHADO) el.classList.remove("hidden");
}

function fecharAvisoCreditosS2() {
  const el = $("s2-alerta-creditos");
  if (el) el.classList.add("hidden");
  S2_ALERTA_CREDITOS_FECHADO = true;
}
window.fecharAvisoCreditosS2 = fecharAvisoCreditosS2;

/**
 * Confirmação explícita ANTES de consumir créditos (PHASE 2, ERRO 2).
 * Devolve true quando o operador autoriza. Se o status não puder ser lido,
 * não bloqueia a produção: cai em um confirm() simples.
 */
async function confirmarInicioFilaS2(projeto_id) {
  let prod = null;
  try {
    prod = await api(`/api/v2/producao/${encodeURIComponent(projeto_id)}/status`);
  } catch (e) {
    console.warn("[FILA] Não foi possível ler o status antes de iniciar:", e);
  }
  if (!prod || !prod.success) {
    return window.confirm(
      "Não foi possível ler o status atual do projeto.\n\nDeseja iniciar a fila mesmo assim?"
    );
  }

  const rInfo = prod.resume_info || {};
  const pendentes = (rInfo.pendentes_count !== undefined ? rInfo.pendentes_count : 0) || 0;
  const creditos = prod.creditos_restantes_total;
  const contas = prod.contas_ativas;
  const fallback = !!prod.fallback_video_ativo;

  let msg = `Esta fila tem ${pendentes} cena(s) pendente(s).\n`;
  if (creditos !== undefined && creditos !== null) {
    msg += `Créditos disponíveis nas contas Flow: ${creditos}` +
      (contas !== undefined && contas !== null ? ` (${contas} conta(s))` : "") + ".\n";
  }
  if (creditos !== undefined && creditos !== null && pendentes > 0 && creditos < pendentes) {
    msg += "\n⚠️ AVISO: créditos insuficientes para todas as cenas. O sistema rotaciona entre " +
      "contas automaticamente e pausa quando todas esgotarem.\n";
  }
  if (fallback) {
    msg += "\n🔴 CRÍTICO: o fallback vídeo→imagem está ATIVO. " +
      "Cenas de vídeo serão geradas como imagem estática.\n";
  }
  msg += "\nDeseja prosseguir?";
  return window.confirm(msg);
}
window.confirmarInicioFilaS2 = confirmarInicioFilaS2;

/* ============================================================
   PHASE 2 — SSE: progresso da fila em tempo real (ERRO 6)
   Substitui o polling fixo de 3s por eventos do backend (<1s).
   O payload do SSE é um "sinal" (stat do scene plan + estado do
   Flow); a UI continua lendo /status como fonte da verdade, mas
   apenas quando o estado realmente muda (assinatura + throttle).
   ============================================================ */
function iniciarSSEProducao(projeto_id) {
  if (!projeto_id || typeof EventSource === "undefined") {
    console.warn("[SSE] EventSource indisponível; mantendo polling de segurança.");
    return null;
  }
  if (S2_SSE) { try { S2_SSE.close(); } catch (e) {} S2_SSE = null; }
  if (S2_SSE_TRAILING) { clearTimeout(S2_SSE_TRAILING); S2_SSE_TRAILING = null; }
  S2_SSE_SIG = "";
  S2_SSE_REFRESH_TS = 0;
  // REQ 4 — stream novo: libera a dedup da navegação automática (aba Montagem).
  S2_ULTIMA_NAVEGACAO = "";

  let es;
  try {
    es = new EventSource(`/api/v2/producao/${encodeURIComponent(projeto_id)}/stream`);
  } catch (e) {
    console.warn("[SSE] Falha ao abrir o stream:", e);
    return null;
  }

  const aplicar = () => {
    if (S2_SSE_TRAILING) { clearTimeout(S2_SSE_TRAILING); S2_SSE_TRAILING = null; }
    const espera = Math.max(0, S2_SSE_REFRESH_MIN_MS - (Date.now() - S2_SSE_REFRESH_TS));
    S2_SSE_TRAILING = setTimeout(() => {
      S2_SSE_TRAILING = null;
      S2_SSE_REFRESH_TS = Date.now();
      if (S.projeto_id === projeto_id && $("tela-studio2") && $("tela-studio2").classList.contains("ativa")) {
        atualizarStatusProducaoS2(projeto_id);
      }
    }, espera);
  };

  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    if (data.tipo === "ping" || data.tipo === "error") return;

    // REQ 4 — NAVEGAÇÃO AUTOMÁTICA: quando a fila pausa por CRÉDITO o backend
    // emite um evento DEDICADO "navegarAba" (canal separado do progresso) e o
    // frontend abre a aba pedida (ex.: 5. MONTAGEM) para o operador seguir para a
    // edição final. `irParaAbaS2` já existe (alias de trocarAbaStudio2).
    if (data.tipo === "navegarAba") {
      const abaNav = String(data.aba || "montagem");
      const chaveNav = `${S.projeto_id}:${data.motivo || ""}:${abaNav}`;
      console.log(`[${data.timestamp || ""}] [NAV] Navegando para: ${abaNav} (motivo: ${data.motivo || ""})`);
      if (chaveNav === S2_ULTIMA_NAVEGACAO) return; // dedup (reconexão do SSE)
      S2_ULTIMA_NAVEGACAO = chaveNav;
      if (typeof irParaAbaS2 === "function") irParaAbaS2(abaNav);
      return;
    }

    // Assinatura: só dispara re-render quando o estado do plano/Flow muda.
    const sig = JSON.stringify([data.total, data.por_status, data.flow]);
    if (sig === S2_SSE_SIG) return;
    S2_SSE_SIG = sig;
    aplicar();
  };

  // O EventSource reconecta sozinho; o polling de 30s cobre a janela de retry.
  es.onerror = () => console.warn("[SSE] Conexão caiu; reconexão automática em andamento.");
  es.onopen = () => console.log(`[SSE] Conectado ao stream de produção de '${projeto_id}'.`);

  S2_SSE = es;
  return es;
}
window.iniciarSSEProducao = iniciarSSEProducao;

async function atualizarStatusProducaoS2(projeto_id) {
  try {
    const prod = await api(`/api/v2/producao/${encodeURIComponent(projeto_id)}/status`);
    if (!prod || !prod.success) return;

    // Atualiza contadores com dados 100% reais sincronizados
    const p = prod.progresso || {};
    const porSt = p.por_status || {};
    const numProntos = p.prontas || 0;
    const numPendentes = Math.max(0, (p.total || 0) - numProntos);
    const numGerando = (porSt.GERANDO || 0) + (porSt.ENVIADA || 0);
    const numErros = porSt.ERRO || 0;

    if ($("s2-cnt-total")) $("s2-cnt-total").textContent = p.total || 0;
    if ($("s2-cnt-pendentes")) $("s2-cnt-pendentes").textContent = numPendentes;
    if ($("s2-cnt-gerando")) $("s2-cnt-gerando").textContent = numGerando;
    if ($("s2-cnt-prontos")) $("s2-cnt-prontos").textContent = numProntos;
    if ($("s2-cnt-erros")) $("s2-cnt-erros").textContent = numErros;
    if ($("s2-badge-prod-count")) $("s2-badge-prod-count").textContent = `${numProntos}/${p.total || 0}`;
    if ($("s2-total-cenas-label")) $("s2-total-cenas-label").textContent = `${p.total || 0} cenas`;

    // Contagem granular por tipo de mídia na aba Produção (70% IMAGEM / 30% B-ROLL)
    let cntAv = 0, cntBr = 0, cntImg = 0, cntVid = 0;
    (prod.cenas || []).forEach(c => {
      const isVideoMedia = Boolean(
        c.tipo === "video"
        || c.media_intent === "video"
        || c.video_status === "READY"
        || (c.arquivo_midia && String(c.arquivo_midia).match(/\.(mp4|mov|webm)$/i))
      );
      const isAv = Boolean(
        c.uses_character === true
        || (c.character_ref && c.character_ref !== "" && c.character_ref !== "none")
        || c.scene_type === "avatar_talking"
        || c.scene_type === "avatar_action"
        || c.narrative_role === "avatar"
        || c.visual_role === "avatar"
      );
      const isAnim = Boolean(c.animate_later || c.animar_depois || c.animar);

      if (isAv) {
        cntAv++;
      } else if (isVideoMedia || isAnim) {
        cntBr++;
        if (isVideoMedia) cntVid++;
      } else {
        cntImg++;
      }
    });
    if ($("s2-cnt-midia-avatar")) $("s2-cnt-midia-avatar").textContent = cntAv;
    if ($("s2-cnt-midia-broll")) $("s2-cnt-midia-broll").textContent = cntBr;
    if ($("s2-cnt-midia-img")) $("s2-cnt-midia-img").textContent = cntImg;
    if ($("s2-cnt-midia-anim")) $("s2-cnt-midia-anim").textContent = cntBr;
    if ($("s2-cnt-midia-vid")) $("s2-cnt-midia-vid").textContent = cntVid;


    // Flow Status
    const fDot = $("s2-flow-dot");
    const fTxt = $("s2-flow-status-text");
    if (fDot && fTxt) {
      const conectado = prod.flow && prod.flow.conectado;
      fDot.className = "flow-status-dot " + (conectado ? "online" : "");
      fTxt.className = "badge " + (conectado ? "badge-ok" : "badge-wait");
      fTxt.textContent = conectado ? "Flow Conectado" : "Desconectado";
    }

    // PHASE 2 (ERRO 2): banner vermelho quando o fallback vídeo→imagem está
    // ativo (créditos de vídeo esgotados em todas as contas Flow). Auto-oculta
    // quando o backend normaliza o estado (créditos renovados/rotacionados).
    atualizarBannerCreditosS2(prod.fallback_video_ativo, prod.creditos_restantes_total);

    // REQ 3/REQ 5 — MOTIVO REAL da parada da fila + NOTIFICAÇÃO VISUAL. Antes o
    // frontend só sabia QUE a fila parou (nada de "por quê"); agora /status devolve
    // pause_reason canônico (credito_esgotado_video | credito_esgotado_imagem |
    // fim_fila_credito_zerado | manual) e o aviso (título + corpo + ação) é exibido
    // apenas na TRANSIÇÃO do motivo.
    const motivoPausa = String(prod.pause_reason || "");
    const pausaComMotivo = Boolean(motivoPausa) && motivoPausa !== "nao_pausado";
    if (pausaComMotivo && motivoPausa !== S2_ULTIMO_PAUSE_REASON) {
      S2_ULTIMO_PAUSE_REASON = motivoPausa;
      const legendaMotivo = {
        credito_esgotado_video: "Créditos de VÍDEO esgotados em todas as contas Flow — a fila foi interrompida e as mídias já geradas foram preservadas.",
        credito_esgotado_imagem: "Créditos esgotados em todas as contas Flow — a fila foi interrompida.",
        fim_fila_credito_zerado: "Fila encerrada: créditos zerados em todas as contas Flow.",
        manual: "Fila interrompida manualmente pelo operador.",
      }[motivoPausa] || `Fila interrompida: ${motivoPausa}`;
      if (typeof showPauseNotification === "function") {
        // REQ 5 — cartão com título, corpo, contagem de B-roll pendente e botões.
        showPauseNotification(motivoPausa, { pendentesBroll: _contarBrollPendenteS2(prod) });
      } else if (typeof showToast === "function") {
        showToast(`⏸ ${legendaMotivo}`, "err"); // fallback (função ausente)
      }
    } else if (!pausaComMotivo) {
      // Fila voltou a rodar / motivo normalizado: libera o próximo aviso, a próxima
      // navegação automática (REQ 4) e o próximo toast (REQ 5).
      S2_ULTIMO_PAUSE_REASON = "";
      S2_ULTIMA_NAVEGACAO = "";
      S2_ULTIMO_TOAST_PAUSA = "";
    }

    // Retomada Inteligente — botão único no cabeçalho (#btn-s2-iniciar-fila)
    // CORREÇÃO 6: o botão deve aparecer e estar ATIVO sempre que houver cenas
    // pendentes (pendentes_count > 0), mesmo que o projeto já tenha sido 100%
    // processado antes (ex.: após remover uma mídia da galeria). Não existe mais
    // nenhuma condição que o esconda quando o projeto está completo.
    const rInfo = prod.resume_info || {};
    const btnIniciarFila = $("btn-s2-iniciar-fila");
    const pendentesTotal = (rInfo.pendentes_count !== undefined
      ? rInfo.pendentes_count
      : Math.max(0, (p.total || 0) - (p.prontas !== undefined ? p.prontas : numProntos))) || 0;

    if (btnIniciarFila) {
      btnIniciarFila.style.display = "inline-block";
      btnIniciarFila.disabled = false;
      if (pendentesTotal > 0 && rInfo.prontas_count > 0) {
        btnIniciarFila.textContent = `▶ Retomar Projeto (${pendentesTotal} restantes)`;
      } else {
        btnIniciarFila.textContent = `⚡ Enviar Prompts para o Flow`;
      }
    }

    // Botão "🎬 Animar B-Roll" — visível se houver imagens prontas para animar
    const btnAnimarBroll = $("btn-s2-animar-broll");
    if (btnAnimarBroll) {
      const prontosTotal = (p.prontas !== undefined ? p.prontas : (prod.resume_info && prod.resume_info.prontas_count)) || 0;
      const totalCenas = (p.total !== undefined ? p.total : (prod.resume_info && prod.resume_info.total)) || 0;
      btnAnimarBroll.style.display = (totalCenas > 0 && prontosTotal > 0) ? "inline-block" : "none";
    }

    // Renderiza Cenas do Storyboard
    renderStoryboardS2(prod.cenas || []);

    // Renderiza Plano de Edição Visual na aba Studio
    renderizarPlanoEdicao(prod.cenas || []);

    // Renderiza Cenas da Produção
    renderProducaoGridS2(prod.cenas || [], projeto_id);

    if ($("s2-dev-plan-json")) $("s2-dev-plan-json").value = JSON.stringify(prod.cenas || [], null, 2);
  } catch (e) {}
}

let _PLANO_EDICAO_CURRENT_CENAS = [];
let _PLANO_EDICAO_FILTER = "all"; // "all" (Imagem+Vídeo+Texto), "img_vid" (Imagem+Vídeo), "only_img" (Só Imagens)

function renderizarPlanoEdicao(cenas) {
  const painel = $("s2-painel-plano-edicao");
  const listaEl = $("s2-plano-edicao-lista");
  const countersEl = $("s2-plano-edicao-counters");
  if (!painel || !listaEl || !countersEl) return;

  if (!cenas || !cenas.length) {
    painel.style.display = "none";
    listaEl.innerHTML = "";
    countersEl.innerHTML = "";
    _PLANO_EDICAO_CURRENT_CENAS = [];
    return;
  }

  _PLANO_EDICAO_CURRENT_CENAS = cenas;

  // Ordena cenas pelo ID crescente
  const sorted = [...cenas].sort((a, b) => Number(a.scene_index || a.id || 0) - Number(b.scene_index || b.id || 0));

  let cntImagens = 0;
  let cntImagemAnimar = 0;
  let cntVideos = 0;
  let cntTextos = 0;

  // Contagem global sobre todas as cenas
  sorted.forEach(c => {
    const isText = (c.tipo === "text" || c.scene_type === "text" || c.media_intent === "text");
    const isVideo = (!isText && (c.tipo === "video" || c.media_intent === "video"));
    const isImageAnim = (!isText && !isVideo && (c.animate_later === true || c.animar_depois === true || c.animar === true));

    if (isText) {
      cntTextos++;
    } else if (isVideo) {
      cntVideos++;
    } else if (isImageAnim) {
      cntImagemAnimar++;
    } else {
      cntImagens++;
    }
  });

  // Filtra linhas exibidas de acordo com a aba selecionada
  const filtered = sorted.filter(c => {
    const isText = (c.tipo === "text" || c.scene_type === "text" || c.media_intent === "text");
    const isVideo = (!isText && (c.tipo === "video" || c.media_intent === "video"));

    if (_PLANO_EDICAO_FILTER === "only_img") {
      return !isText && !isVideo; // Mostra apenas IMAGEM e IMAGEM+ANIMAR
    } else if (_PLANO_EDICAO_FILTER === "img_vid") {
      return !isText; // Mostra IMAGEM, IMAGEM+ANIMAR e VÍDEO (oculta TEXTO)
    }
    return true; // "all": mostra Imagem + Vídeo + Texto
  });

  const linhasHtml = filtered.map(c => {
    const cid = c.scene_index || c.id;
    const tIni = parseFloat(c.tempo_inicio !== undefined ? c.tempo_inicio : (c.start || 0));
    const tsStr = fmtTs(tIni);
    
    // Extrai texto da narração e trunca suavemente
    let textoNarracao = (c.narration || c.texto || c.text || c.fala || "").trim();
    if (!textoNarracao) {
      textoNarracao = `Cena ${cid} (${fmtTs(tIni)} - ${fmtTs(c.tempo_fim || c.end || tIni + 5)})`;
    }

    // Determina a tag de acordo com as categorias
    const isText = (c.tipo === "text" || c.scene_type === "text" || c.media_intent === "text");
    const isVideo = (!isText && (c.tipo === "video" || c.media_intent === "video"));
    const isAvatar = !isText && Boolean(
      c.uses_character === true
      || (c.character_ref && c.character_ref !== "" && c.character_ref !== "none")
      || c.scene_type === "avatar_talking"
      || c.scene_type === "avatar_action"
      || c.narrative_role === "avatar"
      || c.visual_role === "avatar"
    );
    const isImageAnim = (!isText && !isVideo && (c.animate_later === true || c.animar_depois === true || c.animar === true));

    let tagLabel = "B-ROLL";
    let tagClass = "tag-broll";

    if (isText) {
      tagLabel = "TEXTO";
      tagClass = "tag-texto";
    } else if (isAvatar) {
      tagLabel = "AVATAR";
      tagClass = "tag-avatar";
    } else if (isVideo || isImageAnim) {
      tagLabel = "B-ROLL";
      tagClass = "tag-broll";
    } else {
      tagLabel = "IMAGEM";
      tagClass = "tag-imagem";
    }

    return `
      <div class="s2-plano-edicao-item" onclick="selecionarCenaPlanoEdicao(${cid})" title="Clique para focar na Cena ${cid} no Storyboard">
        <span class="mono" style="color:var(--text-muted);font-weight:700;min-width:55px">[${tsStr}]</span>
        <span class="s2-plano-tag ${tagClass}">${tagLabel}</span>
        <span style="flex:1;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4">${esc(textoNarracao)}</span>
      </div>
    `;
  }).join("");

  // Atualiza estado visual das abas
  document.querySelectorAll(".s2-plano-tab").forEach(tab => {
    if (tab.dataset.filter === _PLANO_EDICAO_FILTER) {
      tab.classList.add("active");
    } else {
      tab.classList.remove("active");
    }
  });

  // Atualiza contadores
  let badgesHtml = `
    <span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;border:1px solid rgba(79,142,247,0.35);font-size:11px"><b>${cntImagens}</b> imagens</span>
    <span class="badge" style="background:rgba(124,92,252,0.2);color:#a78bfa;border:1px solid rgba(124,92,252,0.45);font-size:11px"><b>${cntImagemAnimar}</b> imagem+animar</span>
    <span class="badge" style="background:rgba(34,199,122,0.15);color:#22c77a;border:1px solid rgba(34,199,122,0.35);font-size:11px"><b>${cntVideos}</b> vídeos</span>
  `;
  if (cntTextos > 0) {
    badgesHtml += `
      <span class="badge" style="background:rgba(244,63,94,0.15);color:#f43f5e;border:1px solid rgba(244,63,94,0.35);font-size:11px"><b>${cntTextos}</b> textos</span>
    `;
  }
  countersEl.innerHTML = badgesHtml;

  listaEl.innerHTML = linhasHtml || '<div style="color:var(--text-muted);font-size:12px;padding:12px;text-align:center">Nenhuma cena corresponde ao filtro selecionado.</div>';
  painel.style.display = "block";
}

function selecionarCenaPlanoEdicao(cid) {
  const card = $(`s2-story-card-${cid}`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("scene-card-highlight");
    // Força reflow para reiniciar animação CSS
    void card.offsetWidth;
    card.classList.add("scene-card-highlight");
    setTimeout(() => {
      card.classList.remove("scene-card-highlight");
    }, 2500);
  }
}
window.renderizarPlanoEdicao = renderizarPlanoEdicao;
window.selecionarCenaPlanoEdicao = selecionarCenaPlanoEdicao;


function togglePromptCenaS2(cid) {
  const el = $(`s2-prompt-box-${cid}`);
  const btn = $(`btn-toggle-prompt-${cid}`);
  if (!el || !btn) return;
  const oculta = el.classList.contains("hidden");
  el.classList.toggle("hidden", !oculta);
  btn.textContent = oculta ? "👁 Ocultar Prompt" : "👁 Ver Prompt Visual";
}

// Cache de estado para evitar re-renderização DOM desnecessária e requisições repetidas
const _S2_STORY_RENDER_CACHE = new Map();
const _S2_PROD_RENDER_CACHE = new Map();

// CORREÇÃO 2 — id da cena ativa (cena_ativa.scene_id) conhecida pelo último poll;
// usada para destacar o card correspondente na aba Produção com .cena-ativa-gerando.
let _ULTIMA_CENA_ATIVA_SCENE_ID = null;

function _aplicarDestaqueCenaAtiva() {
  const ativa = _ULTIMA_CENA_ATIVA_SCENE_ID;
  const _cidBate = (cid, sid) => {
    if (ativa === null || cid === null) return false;
    return String(cid) === String(ativa) || (sid !== null && String(sid) === String(ativa));
  };
  document.querySelectorAll("#s2-producao-grid .s2-prod-card").forEach((card) => {
    const cid = card.getAttribute("data-cid");
    const sid = card.getAttribute("data-scene-id");
    card.classList.toggle("cena-ativa-gerando", _cidBate(cid, sid));
  });
  document.querySelectorAll("#mural-midias .cena-card").forEach((card) => {
    const cid = card.getAttribute("data-mural");
    card.classList.toggle("cena-ativa-gerando", _cidBate(cid, null));
  });
}

function _getSceneStateKey(c) {
  const cid = c.scene_index || c.id;
  const imgStatus = c.image_status || (c.arquivo_midia ? "READY" : (c.status === "GERANDO" ? "GENERATING" : "PENDING"));
  const vidStatus = c.video_status || "NOT_STARTED";
  const st = c.status || "";
  const temMidia = Boolean(imgStatus === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
  const arq = c.filename || (c.arquivo_midia ? c.arquivo_midia.split(/[\\/]/).pop() : "");
  const tipo = c.tipo || "image";
  const anim = Boolean(c.animate_later || c.animar_depois || c.animar);
  return `${cid}|${imgStatus}|${vidStatus}|${st}|${temMidia}|${arq}|${tipo}|${anim}|${c.erro_msg || ''}`;
}

function _buildStoryCardHtml(c, S_proj) {
  const cid = c.scene_index || c.id;
  const ts = c.timestamp_saida || c.timestamp || `${fmtTs(c.tempo_inicio || c.start)} - ${fmtTs(c.tempo_fim || c.end)}`;
  const imgStatus = c.image_status || (c.arquivo_midia ? "READY" : (c.status === "GERANDO" ? "GENERATING" : "PENDING"));
  const vidStatus = c.video_status || "NOT_STARTED";
  const filename = c.filename || `${String(cid).padStart(3, '0')}.png`;
  const prompt = c.visual_prompt || c.prompt_imagem || c.narration || c.texto || "Sem prompt gerado";
  const charTag = (c.uses_character && c.character_ref) ? `<span class="badge badge-ok" style="font-size:11px">👤 ${esc(c.character_ref)}</span>` : "";

  const temMidia = Boolean(imgStatus === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));

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

  // URL estável para permitir cache nativo do navegador sem forçar repetições com timestamp
  const mediaUrl = `/api/cena_media/${encodeURIComponent(S_proj)}/${cid}`;

  const thumb = temMidia
    ? `<div style="margin:12px 0;width:100%;height:180px;border-radius:8px;overflow:hidden;background:#0d0d12;display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid var(--border)" onclick="abrirMediaModalCena(${cid})" title="Clique para expandir em tela cheia">
         <img src="${mediaUrl}" alt="Cena ${cid}" style="width:100%;height:100%;object-fit:cover;transition:transform .2s ease" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'" loading="lazy" />
       </div>`
    : `<div style="margin:12px 0;height:150px;border:1px dashed var(--border-strong);border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--text-muted);font-size:13px;background:rgba(255,255,255,0.02);gap:8px">
         <span style="font-size:26px">${imgStatus === 'GENERATING' ? '⚡' : '⏳'}</span>
         <i>${imgStatus === 'GENERATING' ? 'Gerando imagem no Google Flow...' : 'Aguardando na fila de produção...'}</i>
       </div>`;

  return `
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
  `;
}

function renderStoryboardS2(cenas) {
  const box = $("s2-storyboard-grid");
  if (!box) return;
  if (!cenas.length) {
    box.innerHTML = '<div class="scenes-empty">Nenhum prompt planejado ainda. Carregue o áudio/SRT para gerar o planejamento.</div>';
    _S2_STORY_RENDER_CACHE.clear();
    return;
  }

  // Ordena sempre as cenas crescentemente pelo ID numérico
  const sorted = [...cenas].sort((a, b) => Number(a.scene_index || a.id) - Number(b.scene_index || b.id));

  // Se a quantidade de cards mudou ou o box estiver vazio, inicializa os containers
  if (box.children.length !== sorted.length || box.querySelector(".scenes-empty")) {
    box.innerHTML = sorted.map(c => {
      const cid = c.scene_index || c.id;
      const temMidia = Boolean(c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
      return `<div id="s2-story-card-${cid}" class="s2-scene-card" style="border: 1px solid ${temMidia ? 'rgba(34,199,122,0.35)' : 'var(--border)'}">${_buildStoryCardHtml(c, S.projeto_id)}</div>`;
    }).join("");

    sorted.forEach(c => {
      _S2_STORY_RENDER_CACHE.set(c.scene_index || c.id, _getSceneStateKey(c));
    });
    return;
  }

  // Atualização seletiva in-place: apenas nós cujo estado mudou são atualizados
  sorted.forEach(c => {
    const cid = c.scene_index || c.id;
    const key = _getSceneStateKey(c);
    const prevKey = _S2_STORY_RENDER_CACHE.get(cid);

    if (prevKey !== key) {
      const cardEl = $(`s2-story-card-${cid}`);
      if (cardEl) {
        const temMidia = Boolean(c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
        cardEl.style.borderColor = temMidia ? 'rgba(34,199,122,0.35)' : 'var(--border)';
        cardEl.innerHTML = _buildStoryCardHtml(c, S.projeto_id);
      }
      _S2_STORY_RENDER_CACHE.set(cid, key);
    }
  });
}

function _buildProdCardHtml(c, S_proj) {
  const cid = c.scene_index || c.id;
  const tIni = parseFloat(c.tempo_inicio !== undefined ? c.tempo_inicio : (c.start || 0));
  const tFim = parseFloat(c.tempo_fim !== undefined ? c.tempo_fim : (c.end || tIni + 5.0));
  const ts = `[${fmtTs(tIni)}]`;
  const imgStatus = c.image_status || (c.arquivo_midia ? "READY" : (c.status === "GERANDO" ? "GENERATING" : "PENDING"));
  const vidStatus = c.video_status || "NOT_STARTED";
  const filename = c.filename || `${String(cid).padStart(3, '0')}.png`;
  const temMidia = Boolean(imgStatus === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
  
  // URL da imagem via servidor com fallback
  // ANTIGRAVITY Passo 3: cache buster ?t= — impede o navegador de manter a
  // imagem antiga em cache após regeneração no Flow.
  const imgUrl = `/projeto/${encodeURIComponent(S_proj)}/cenas/${String(cid).padStart(3, '0')}.png?t=${Date.now()}`;

  // Tipo de cena: definido automaticamente pelo sistema (SEM selector manual)
  // Detecta vídeo por múltiplas fontes (tipo, media_intent, video_status, extensão arquivo)
  const isVideo = Boolean(
    c.tipo === "video"
    || c.media_intent === "video"
    || c.video_status === "READY"
    || (c.arquivo_midia && String(c.arquivo_midia).match(/\.(mp4|mov|webm)$/i))
  );

  // Detecta se a cena é de Avatar (apresentador) ou B-Roll
  const isAvatar = Boolean(
    c.uses_character === true
    || (c.character_ref && c.character_ref !== "" && c.character_ref !== "none")
    || c.scene_type === "avatar_talking"
    || c.scene_type === "avatar_action"
    || c.narrative_role === "avatar"
    || c.visual_role === "avatar"
  );

  // Detecta intenção de animar (ainda é imagem, mas será animada)
  const isAnim = Boolean(
    c.animate_later === true
    || c.animar_depois === true
    || c.animar === true
  );

  let tagLabel, tagClass;
  if (isAvatar) {
    tagLabel = isVideo ? "🎬 AVATAR" : "👤 AVATAR";
    tagClass = "tag-avatar";
  } else if (isVideo || isAnim) {
    tagLabel = "🎬 B-ROLL";
    tagClass = "tag-broll";
  } else {
    tagLabel = "🖼 IMAGEM";
    tagClass = "tag-imagem";
  }
  const tagHtml = `<span class="s2-plano-tag ${tagClass}" style="font-size:10px;padding:2px 7px;min-width:auto;letter-spacing:0.3px">${tagLabel}</span>`;

  // Texto da narração: truncado em 80 chars, hover/title com texto completo
  let textoNarracao = (c.narration || c.texto || c.text || c.fala || "").trim();
  const narracaoFull = textoNarracao || `Cena ${cid} (${fmtTs(tIni)} - ${fmtTs(tFim)})`;
  let narracaoTrunc = narracaoFull;
  if (narracaoTrunc.length > 80) {
    narracaoTrunc = narracaoTrunc.slice(0, 77) + "...";
  }

  // Thumbnail da imagem:
  const thumbHtml = temMidia
    ? `<img src="${imgUrl}" alt="Cena ${cid}" style="width:100%;height:100%;object-fit:cover" onerror="this.src='/static/placeholder_cena.png'" loading="lazy" />`
    : `<img src="/static/placeholder_cena.png" alt="Cena ${cid} Pendente" style="width:100%;height:100%;object-fit:cover;opacity:0.8" />`;

  // Prompt visual DO FLOW: SEMPRE VISÍVEL, NUNCA ESCONDIDO
  const promptRaw = (c.prompt_imagem || c.visual_prompt || "").trim();
  const promptTexto = promptRaw 
    ? promptRaw 
    : "⚠️ Prompt não gerado ainda. Clique em 'Gerar Storyboard & Prompts' primeiro.";

  // Status
  let statusHtml = '<span class="badge badge-wait">PENDENTE</span>';
  let erroHtml = '';
  if (imgStatus === "GENERATING" || c.status === "GERANDO") {
    statusHtml = '<span class="badge badge-proc"><span class="flow-pulsing-dot" style="font-size:8px">●</span> GERANDO</span>';
  } else if (temMidia) {
    statusHtml = `<span class="badge badge-ok">PRONTA</span>`;
  } else if (c.status === "ERRO") {
    statusHtml = `<span class="badge badge-err" title="${esc(c.erro_msg || 'Erro na geração')}">ERRO</span>`;
    erroHtml = `<div style="font-size:10px;color:#f87171;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.25);border-radius:4px;padding:3px 6px;margin:3px 0;line-height:1.2;word-break:break-word"><b>Falha:</b> ${esc(c.erro_msg || 'Falha na geração')}</div>`;
  }

  // Botão "Gerar no Flow" — SEMPRE presente
  const btnGerarHtml = (c.status === "ERRO")
    ? `<button class="btn btn-sm btn-accent" style="flex:1;background:#ef4444;color:#fff" onclick="gerarCenaIndividualFlow(${cid})" title="Re-tentar cena">🔁 Re-tentar</button>`
    : `<button class="btn btn-sm btn-primary" style="flex:1" onclick="gerarCenaIndividualFlow(${cid})" title="Gerar Imagem com Google Flow">⚡ Gerar no Flow</button>`;

  return `
    <div class="s2-prod-card-thumb" onclick="abrirMediaModalCena(${cid})" style="cursor:pointer" title="Clique para visualizar em tela cheia">
      ${thumbHtml}
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
      <div style="display:flex;align-items:center;gap:6px">
        <b style="font-size:13.5px">Cena ${String(cid).padStart(3, '0')}</b>
        <span class="mono text-muted" style="font-size:12px">${ts}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        ${tagHtml}
        ${statusHtml}
      </div>
    </div>
    ${erroHtml}
    <div class="s2-prod-card-narracao" title="${esc(narracaoFull)}" style="font-size:12px;color:var(--text);line-height:1.4;background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:6px;border:1px solid var(--border);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
      💬 ${esc(narracaoTrunc)}
    </div>
    <div class="s2-prod-card-prompt mono" style="font-size:11px;color:var(--text-muted);background:rgba(0,0,0,0.3);padding:8px 10px;border-radius:6px;border:1px solid var(--border);line-height:1.4;max-height:85px;overflow-y:auto">
      ${esc(promptTexto)}
    </div>
    <div class="btn-row" style="margin-top:auto;gap:6px">
      ${btnGerarHtml}
      ${(c.animate_later || c.animar_depois) && (c.image_status === "READY" || c.status === "BAIXADA")
        ? `<button class="btn btn-sm" style="background:#7c5cfc;color:#fff;border:none;cursor:pointer"
             onclick="gerarCenaIndividualFlow(${cid}, 'video')" title="Animar esta cena no Flow">
             🎬 Animar
           </button>`
        : ""
      }
      <button class="btn btn-sm btn-ghost" onclick="abrirMediaModalCena(${cid})" title="Visualizar em fullscreen">👁 Ver</button>
    </div>
  `;
}

// CORREÇÃO 3 (FASE 3) — uma cena PENDENTE nunca deve ser removida do grid de
// produção. Apenas status terminais explícitos (DESCARTADA/REMOVIDA/CANCELADA)
// saem da lista; PENDENTE, GERANDO, ERRO, BAIXADA e ENVIADA permanecem visíveis
// (ERRO segue visível para o operador reprocessar a cena).
function _deveRenderizarCenaProd(c) {
  const st = String((c && c.status) || "").toUpperCase();
  if (st === "DESCARTADA" || st === "REMOVIDA" || st === "CANCELADA") return false;
  return true;
}

function renderProducaoGridS2(cenas, S_proj) {
  const box = $("s2-producao-grid");
  if (!box) return;
  if (!cenas.length) {
    box.innerHTML = '<div class="scenes-empty">Nenhuma cena na fila de produção. Configure o projeto na Aba 1, gere os prompts na Aba 2 e clique em <b>\'Enviar Prompts para o Flow\'</b>.</div>';
    _S2_PROD_RENDER_CACHE.clear();
    return;
  }

  // Ordena sempre as cenas crescentemente pelo ID numérico
  // CORREÇÃO 3 — mantém PENDENTE/GERANDO/ERRO visíveis (só descarta terminais)
  const sorted = [...cenas]
    .filter(_deveRenderizarCenaProd)
    .sort((a, b) => Number(a.scene_index || a.id) - Number(b.scene_index || b.id));

  // CORREÇÃO 3 — poda cards órfãos (cena removida do plano ou descartada),
  // evitando "fantasmas" antigos no grid após remoções/exclusões.
  const _idsValidos = new Set(sorted.map(c => String(c.scene_index || c.id)));
  Array.from(box.children).forEach(el => {
    const _cid = el.getAttribute && el.getAttribute("data-cid");
    if (_cid && !_idsValidos.has(String(_cid))) {
      el.remove();
      _S2_PROD_RENDER_CACHE.delete(Number(_cid));
    }
  });

  // Se a quantidade mudou ou estiver vazio, inicializa os cards
  if (box.children.length !== sorted.length || box.querySelector(".scenes-empty")) {
    box.innerHTML = sorted.map(c => {
      const cid = c.scene_index || c.id;
      return `<div id="s2-prod-card-${cid}" class="s2-prod-card" data-cid="${cid}" data-scene-id="${c.id}">${_buildProdCardHtml(c, S_proj || S.projeto_id)}</div>`;
    }).join("");

    sorted.forEach(c => {
      _S2_PROD_RENDER_CACHE.set(c.scene_index || c.id, _getSceneStateKey(c));
    });
    // CORREÇÃO 2 — aplica destaque já no primeiro render
    _aplicarDestaqueCenaAtiva();
    return;
  }

  // Atualização in-place inteligente: toca apenas cards que realmente mudaram
  sorted.forEach(c => {
    const cid = c.scene_index || c.id;
    const key = _getSceneStateKey(c);
    const prevKey = _S2_PROD_RENDER_CACHE.get(cid);

    if (prevKey !== key) {
      const cardEl = $(`s2-prod-card-${cid}`);
      if (cardEl) {
        cardEl.innerHTML = _buildProdCardHtml(c, S_proj || S.projeto_id);
      }
      _S2_PROD_RENDER_CACHE.set(cid, key);
    }
  });

  // CORREÇÃO 2 — reaplica o destaque da cena ativa após qualquer re-render
  _aplicarDestaqueCenaAtiva();
}

// ---------------------------------------------------------------------------
// ABA 3: GALERIA DE ARQUIVOS (VER TODAS AS IMAGENS SEM PRECISAR BAIXAR)
// ---------------------------------------------------------------------------
let _ARQUIVO_FILTRO = "todas";
let _ARQUIVO_CENAS_CACHE = [];

async function renderGaleriaArquivosS2(projeto_id) {
  const grid = $("s2-arquivo-grid");
  const contador = $("s2-arquivo-contador");
  if (!grid) return;

  try {
    const prod = await api(`/api/v2/producao/${encodeURIComponent(projeto_id)}/status`);
    const cenas = (prod && prod.cenas) ? prod.cenas : [];
    _ARQUIVO_CENAS_CACHE = cenas;

    const total = cenas.length;
    let baixadas = 0;
    cenas.forEach(c => {
      const temMidia = Boolean(c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
      if (temMidia) baixadas++;
    });

    if (contador) {
      contador.textContent = `${baixadas}/${total} imagens prontas`;
    }
    if ($("s2-badge-arq-count")) {
      $("s2-badge-arq-count").textContent = `${baixadas}/${total}`;
    }

    aplicarFiltroGaleriaArquivo();
  } catch (e) {
    grid.innerHTML = `<div class="scenes-empty">Erro ao carregar galeria: ${esc(e.message)}</div>`;
  }
}

function aplicarFiltroGaleriaArquivo() {
  const grid = $("s2-arquivo-grid");
  if (!grid) return;

  const cenas = _ARQUIVO_CENAS_CACHE || [];
  if (!cenas.length) {
    grid.innerHTML = '<div class="scenes-empty" style="grid-column:1/-1">Nenhuma cena gerada ainda. Crie o storyboard na aba Estúdio/Produção.</div>';
    return;
  }

  const sorted = [...cenas].sort((a, b) => Number(a.scene_index || a.id) - Number(b.scene_index || b.id));

  const filtradas = sorted.filter(c => {
    const temMidia = Boolean(c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
    const isErro = (c.status === "ERRO" || c.image_status === "ERROR");
    if (_ARQUIVO_FILTRO === "baixadas") return temMidia;
    if (_ARQUIVO_FILTRO === "pendentes") return !temMidia && !isErro;
    if (_ARQUIVO_FILTRO === "erros") return isErro;
    return true; // "todas"
  });

  if (!filtradas.length) {
    grid.innerHTML = '<div class="scenes-empty" style="grid-column:1/-1">Nenhuma imagem corresponde ao filtro selecionado.</div>';
    return;
  }

  grid.innerHTML = filtradas.map(c => {
    const cid = c.scene_index || c.id;
    const tIni = parseFloat(c.tempo_inicio !== undefined ? c.tempo_inicio : (c.start || 0));
    const ts = `[${fmtTs(tIni)}]`;
    const temVideo = Boolean(c.video_status === "READY" || (c.arquivo_midia && (c.arquivo_midia.endsWith(".mp4") || c.arquivo_midia.endsWith(".webm"))));
    const temMidia = Boolean(temVideo || c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
    const isErro = (c.status === "ERRO" || c.image_status === "ERROR");
    const filename = c.filename || (temVideo ? `${String(cid).padStart(3, '0')}.mp4` : `${String(cid).padStart(3, '0')}.png`);
    const mediaUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${String(cid).padStart(3, '0')}.${temVideo ? 'mp4' : 'png'}?t=${Date.now()}`;

    let statusBadge = '<span class="badge badge-wait">PENDENTE</span>';
    if (temVideo) {
      statusBadge = '<span class="badge badge-ok" style="background:#7c3aed;color:#fff;font-weight:700">🎬 VÍDEO</span>';
    } else if (temMidia) {
      statusBadge = '<span class="badge badge-ok">BAIXADA</span>';
    } else if (isErro) {
      statusBadge = '<span class="badge badge-err">ERRO</span>';
    }

    const thumbHtml = temVideo
      ? `<video src="${mediaUrl}" style="width:100%;height:100%;object-fit:cover" muted playsinline loop onmouseover="this.play()" onmouseout="this.pause()"></video>`
      : (temMidia
          ? `<img src="${mediaUrl}" alt="Cena ${cid}" style="width:100%;height:100%;object-fit:cover" onerror="this.src='/static/placeholder_cena.png'" loading="lazy" />`
          : `<img src="/static/placeholder_cena.png" alt="Pendente" style="width:100%;height:100%;object-fit:cover;opacity:0.8" />`);

    return `
      <div class="s2-card" style="padding:12px;display:flex;flex-direction:column;gap:10px;background:var(--surface-2);border-radius:10px">
        <div style="width:100%;aspect-ratio:16/9;background:#08080c;border-radius:6px;overflow:hidden;cursor:pointer;position:relative" onclick="abrirMediaModalCena(${cid})" title="Clique para expandir">
          ${thumbHtml}
          ${temVideo ? '<span style="position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,0.7);padding:2px 6px;border-radius:4px;font-size:10px;color:#fff">▶ VÍDEO</span>' : ''}
          ${temMidia ? `<button type="button" onclick="event.stopPropagation();removerMidiaCenaGaleria(${cid})" title="Remover imagem da cena ${cid}" style="position:absolute;top:6px;right:6px;width:26px;height:26px;border-radius:50%;border:none;background:rgba(239,68,68,0.9);color:#fff;font-size:14px;line-height:1;font-weight:700;cursor:pointer;z-index:5;display:flex;align-items:center;justify-content:center">✕</button>` : ''}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div style="display:flex;align-items:center;gap:6px">
            <b class="mono" style="font-size:12.5px">${esc(filename)}</b>
            <span class="mono text-muted" style="font-size:11.5px">${ts}</span>
          </div>
          ${statusBadge}
        </div>
        <div class="btn-row" style="margin-top:auto;gap:6px">
          <button class="btn btn-xs btn-primary" style="flex:1" onclick="abrirMediaModalCena(${cid})">👁 Ver</button>
          <button class="btn btn-xs btn-ghost" style="flex:1" onclick="gerarCenaIndividualFlow(${cid})" title="Regerar no Google Flow">⚡ Regerar no Flow</button>
        </div>
      </div>
    `;
  }).join("");
}

async function removerMidiaCenaGaleria(cena_id) {
  if (!S.projeto_id) return;
  const confirmar = confirm(`Remover imagem da cena ${cena_id}?`);
  if (!confirmar) return;
  try {
    const r = await api(`/api/v2/projetos/${encodeURIComponent(S.projeto_id)}/cenas/${cena_id}/midia`, { method: "DELETE" });
    if (r && r.ok) {
      // Atualiza o cache local para a cena (removida -> PENDENTE)
      (_ARQUIVO_CENAS_CACHE || []).forEach(c => {
        if (Number(c.scene_index || c.id) === Number(cena_id)) {
          c.arquivo_midia = "";
          c.filename = "";
          c.image_status = "PENDING";
          c.status = "PENDENTE";
        }
      });
      aplicarFiltroGaleriaArquivo();
      // Atualiza contadores da Galeria e da aba Produção
      await renderGaleriaArquivosS2(S.projeto_id);
      await atualizarStatusProducaoS2(S.projeto_id);
      showToast(`🗑 Mídia da cena ${cena_id} removida.`);
    } else {
      alert("Erro ao remover mídia: " + ((r && r.error) || "Falha desconhecida"));
    }
  } catch (e) {
    alert("Erro de conexão ao remover mídia: " + e.message);
  }
}
window.removerMidiaCenaGaleria = removerMidiaCenaGaleria;

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

    // Atualiza contadores de pastas (mídias unificadas em cenas/)
    for (const pasta of ["audio", "metadata", "prompts", "export", "cenas"]) {
      const el = $(`s2-cnt-folder-${pasta}`);
      if (el) el.textContent = `${(est[pasta] || []).length} arquivos`;
    }
    // Card "Cenas" mostra separado por tipo (🖼 PNG vs 🎬 MP4)
    const cenasList = est.cenas || [];
    const pngCount = cenasList.filter(f => /\.(png|jpe?g|webp)$/i.test(f.nome || "")).length;
    const mp4Count = cenasList.filter(f => /\.(mp4|mov|mkv|webm)$/i.test(f.nome || "")).length;
    if ($("s2-cnt-folder-cenas-png")) $("s2-cnt-folder-cenas-png").textContent = `🖼 ${pngCount}`;
    if ($("s2-cnt-folder-cenas-mp4")) $("s2-cnt-folder-cenas-mp4").textContent = `🎬 ${mp4Count}`;
  } catch (e) {}
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// ABA 5: EDITOR DE MONTAGEM NLE, TIMELINE MULTITRACK & ABA 6: EXPORTAÇÃO
// ---------------------------------------------------------------------------
let _montagemCenas = [];
let _montagemCenaAtivaIdx = 0;
let _montagemPlayerInited = false; // compat — não é mais usado como trava
let _montagemAudioEl = null;       // elemento <audio> atualmente vinculado
let _montagemPlayerBound = false;  // atalho de teclado global já vinculado
let _montagemRAF = 0;              // id do loop requestAnimationFrame do playhead
let _playheadArrastando = false;   // true enquanto o usuário arrasta a bolinha (scrub)
let _montagemTimelineZoom = 1.0;
let _montagemTotalDuracao = 0;
let _montagemWaveform = null;        // Float32Array de amplitudes normalizadas (0-1)
let _montagemWaveformFps = 100;      // frames de amplitude por segundo (1 frame = 10ms)
let _montagemWaveformSrc = "";       // URL do áudio do qual o waveform foi extraído

// --- Lira Studio 2.0 Aba 5: Legendas & Storyboard State ---
let _capcutSubtitlesPresets = [];
let _estiloLegendaAtivo = "amarelo_capcut";
let _storyboardDragSrcIdx = null;

// ── Transições (CapCut Desktop Nativo & Legadas) ─────────────────────────────
const _TRANS_INFO = {
  none:              { tipo: "none",              rot: "Corte Seco (Sem)",icono: "✕", color: "#64748b" },
  bordas_difusas:    { tipo: "bordas_difusas",    rot: "Bordas Difusas",  icono: "🌊", color: "#38bdf8", capcut: true },
  barra_de_luz:      { tipo: "barra_de_luz",      rot: "Barra de Luz",    icono: "⚡", color: "#facc15", capcut: true },
  sobrepor:          { tipo: "sobrepor",          rot: "Sobrepor",        icono: "🔀", color: "#818cf8", capcut: true },
  combinar:          { tipo: "combinar",          rot: "Combinar",        icono: "✦",  color: "#a78bfa", capcut: true },
  circulo:           { tipo: "circulo",           rot: "Círculo",         icono: "⭕", color: "#f472b6", capcut: true },
  retalhos_do_caos:  { tipo: "retalhos_do_caos",  rot: "Retalhos do Caos",icono: "🌪️", color: "#fb923c", capcut: true },
  espelho:           { tipo: "espelho",           rot: "Espelho / Flip",  icono: "🪞", color: "#2dd4bf", capcut: true },
  fade_out:          { tipo: "fade_out",          rot: "Fade Out (Preto)",icono: "◑",  color: "#4f8ef7" },
  fade_in:           { tipo: "fade_in",           rot: "Fade In (Luz)",   icono: "◐",  color: "#4f8ef7" },
  dissolve:          { tipo: "dissolve",          rot: "Dissolve",        icono: "✦",  color: "#34d399" },
  slow_in:           { tipo: "slow_in",           rot: "Slow In",         icono: "⤵",  color: "#f59e0b" },
  slow_out:          { tipo: "slow_out",          rot: "Slow Out",        icono: "⤴",  color: "#f59e0b" }
};
let _TRANS_MENU_IDX = null;
let _TRANS_SEL = {};

function _montagemPxPerSec() {
  return 28 * _montagemTimelineZoom;
}

function ajustarZoomTimeline(delta) {
  _montagemTimelineZoom = Math.max(0.5, Math.min(3.0, _montagemTimelineZoom + delta));
  if ($("s2-timeline-zoom")) $("s2-timeline-zoom").value = _montagemTimelineZoom.toFixed(1);
  renderMontagemTimeline(_montagemCenas);
}

function definirZoomTimeline(val) {
  _montagemTimelineZoom = Math.max(0.5, Math.min(3.0, parseFloat(val) || 1.0));
  renderMontagemTimeline(_montagemCenas);
}

// ── Biblioteca de Legendas CapCut ──────────────────────────────────────────
async function carregarPresetsLegendasCapCut() {
  const container = $("capcut-subtitles-preset-list");
  if (!container) return;

  try {
    const res = await api(`/api/v2/capcut/legendas`);
    if (res && res.success && Array.isArray(res.presets) && res.presets.length) {
      _capcutSubtitlesPresets = res.presets;
      container.innerHTML = _capcutSubtitlesPresets.map((p) => {
        const isActive = p.id === _estiloLegendaAtivo;
        const subClass = `sub-preview-${p.id}`;
        const previewTxt = p.id === "amarelo_capcut" ? "CAPCUT" : (p.id === "tiktok_dinamico" ? "TIKTOK" : (p.id === "neon_glow" ? "NEON" : (p.id === "karaoke" ? "KARAOKE" : "POP")));
        return `
          <div class="capcut-sub-card ${isActive ? 'active' : ''}" data-preset="${p.id}" onclick="selecionarEstiloLegendaPreset('${p.id}')">
            <div style="display:flex;flex-direction:column;gap:2px">
              <span style="font-size:11px;font-weight:700;color:var(--text)">${p.name}</span>
              <span style="font-size:9px;color:${p.recommended ? 'var(--accent-light)' : 'var(--text-muted)'}">${p.description || ''}</span>
            </div>
            <div class="capcut-sub-card-preview ${subClass}">${previewTxt}</div>
          </div>
        `;
      }).join("");
    }
  } catch (e) {
    console.warn("Erro ao carregar presets de legendas:", e);
  }
}

async function selecionarEstiloLegendaPreset(presetId) {
  _estiloLegendaAtivo = presetId;

  // Atualiza classes nos cards da biblioteca
  document.querySelectorAll("#capcut-subtitles-preset-list .capcut-sub-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.preset === presetId);
  });

  // Atualiza a cena ativa se houver
  if (_montagemCenas && _montagemCenas[_montagemCenaAtivaIdx]) {
    const c = _montagemCenas[_montagemCenaAtivaIdx];
    c.estilo_legenda = presetId;
    c.caption_style = presetId;
    atualizarPlayerLiveCaption(c);

    const cid = c.id || c.scene_index;
    try {
      await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/cena/${cid}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estilo_legenda: presetId, caption_style: presetId })
      });
    } catch (e) {
      console.warn("Erro ao salvar estilo da cena:", e);
    }
  }
}

async function aplicarEstilosLegendaTodosClipes() {
  const msgEl = $("capcut-legendas-status-msg");
  if (msgEl) msgEl.textContent = "⏳ Aplicando estilo a todas as cenas...";

  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/legendas_lote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estilo_id: _estiloLegendaAtivo, ativar_todas: true })
    });

    if (res && res.success) {
      _montagemCenas.forEach((c) => {
        c.estilo_legenda = _estiloLegendaAtivo;
        c.caption_style = _estiloLegendaAtivo;
        c.legenda_ativa = true;
        c.caption_ativo = true;
      });

      renderMontagemTimeline(_montagemCenas);
      if (_montagemCenas[_montagemCenaAtivaIdx]) {
        atualizarPlayerLiveCaption(_montagemCenas[_montagemCenaAtivaIdx]);
      }

      if (msgEl) {
        msgEl.textContent = "✓ Aplicado a todos os clipes!";
        setTimeout(() => { if (msgEl) msgEl.textContent = ""; }, 3000);
      }
      showToast("✅ Estilo de legenda aplicado a todas as cenas!", "ok");
    } else {
      if (msgEl) msgEl.textContent = `❌ ${(res && res.error) || 'Falha ao aplicar'}`;
    }
  } catch (e) {
    if (msgEl) msgEl.textContent = `❌ Erro de rede: ${e.message}`;
  }
}

function atualizarPlayerLiveCaption(cena) {
  const badgeEl = $("s2-player-caption-badge");
  const badgeStyleEl = $("s2-player-caption-badge-style");
  const captionEl = $("s2-player-caption");
  const captionTextEl = $("s2-player-caption-text");
  if (!captionEl || !captionTextEl) return;

  const ativa = Boolean(cena && cena.legenda_ativa !== false && cena.caption_ativo !== false);
  const texto = (cena ? (cena.texto_transcricao || cena.texto || cena.narration || cena.fala || "") : "").trim();
  const estilo = (cena && (cena.estilo_legenda || cena.caption_style)) || _estiloLegendaAtivo || "amarelo_capcut";

  if (!ativa || !texto) {
    captionEl.classList.add("hidden");
    if (badgeEl) badgeEl.classList.add("hidden");
    return;
  }

  captionTextEl.textContent = texto;
  captionEl.classList.remove("hidden");

  // Remove estilos anteriores e adiciona o atual
  captionEl.className = `nle-caption-overlay sub-preview-${estilo}`;

  // TAREFA 3: overrides por cena (caption_custom) aplicados POR CIMA do preset.
  // A camada CSS `#s2-player-caption[data-custom="1"]` (style.css:5115+) garante
  // a precedencia sobre os `!important` dos presets, sem precisar de style inline.
  _aplicarCaptionCustomNoOverlay(captionEl, _captionCustomDaCena(cena));

  if (badgeEl) {
    badgeEl.classList.remove("hidden");
    if (badgeStyleEl) {
      const presetObj = _capcutSubtitlesPresets.find(p => p.id === estilo);
      badgeStyleEl.textContent = presetObj ? presetObj.name : estilo;
    }
  }
}

// ── Storyboard (Reordenar & Mover Cenas) ───────────────────────────────────
function renderStoryboardCenas(cenas) {
  const container = $("storyboard-cards-container");
  if (!container) return;

  if (!cenas || !cenas.length) {
    container.innerHTML = `<div style="padding:14px;font-size:11px;color:var(--text-muted)">Nenhuma cena no plano.</div>`;
    return;
  }

  container.innerHTML = cenas.map((c, idx) => {
    const cid = c.id || c.scene_index;
    const durSec = parseFloat(c.duracao || 5.0);
    const temMidia = Boolean(c.tem_midia || c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
    const baseMidia = c.arquivo_midia ? c.arquivo_midia.split(/[\\/]/).pop() : `${String(cid).padStart(3, '0')}.png`;
    const imgFile = baseMidia.replace(/\.mp4$/i, '.png');
    const imgUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgFile)}?t=${Date.now()}`;
    const isActive = idx === _montagemCenaAtivaIdx;

    return `
      <div id="storyboard-card-${idx}" class="storyboard-card ${isActive ? 'active' : ''}"
           draggable="true"
           data-idx="${idx}"
           data-cid="${cid}"
           onclick="selecionarCenaMontagem(${idx}, true)">
        <div class="storyboard-thumb-box">
          ${temMidia
            ? `<img src="${imgUrl}" alt="Cena ${cid}" onerror="this.src='/api/v2/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}'" />`
            : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:11px;color:rgba(255,255,255,0.3)">⏳</div>`
          }
          <div class="storyboard-handle" title="Arraste para reordenar">⠿</div>
          <div class="storyboard-dur-tag">${durSec.toFixed(1)}s</div>
        </div>
        <div class="storyboard-card-info">
          <span class="storyboard-card-num">Cena ${String(cid).padStart(2, '0')}</span>
          <div class="storyboard-card-actions" onclick="event.stopPropagation()">
            <button class="storyboard-btn-action" title="Trocar Mídia" onclick="abrirTrocaMidiaCena(${cid})">🔄</button>
            <button class="storyboard-btn-action btn-del" title="Excluir Cena" onclick="excluirCenaStoryboard(${cid}, event)">🗑️</button>
          </div>
        </div>
      </div>
    `;
  }).join("");

  initStoryboardDragAndDrop();
}

function initStoryboardDragAndDrop() {
  const cards = document.querySelectorAll(".storyboard-card");
  cards.forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      _storyboardDragSrcIdx = parseInt(card.dataset.idx, 10);
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.idx);
    });

    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      card.classList.add("drag-over");
    });

    card.addEventListener("dragleave", () => {
      card.classList.remove("drag-over");
    });

    card.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      card.classList.remove("drag-over");
      const targetIdx = parseInt(card.dataset.idx, 10);
      if (_storyboardDragSrcIdx !== null && _storyboardDragSrcIdx !== targetIdx) {
        moverCenaStoryboardPara(_storyboardDragSrcIdx, targetIdx);
      }
    });

    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      document.querySelectorAll(".storyboard-card").forEach(c => c.classList.remove("drag-over"));
      _storyboardDragSrcIdx = null;
    });
  });
}

async function moverCenaStoryboardPara(srcIdx, destIdx) {
  if (srcIdx < 0 || srcIdx >= _montagemCenas.length || destIdx < 0 || destIdx >= _montagemCenas.length) return;

  const novaLista = [..._montagemCenas];
  const [removida] = novaLista.splice(srcIdx, 1);
  novaLista.splice(destIdx, 0, removida);

  const novaOrdemIds = novaLista.map(c => c.id || c.scene_index);

  showToast("⏳ Reordenando cenas na linha do tempo...", "info");
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/reordenar_cenas`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ordem: novaOrdemIds })
    });

    if (res && res.success) {
      showToast("✅ Cenas reordenadas com sucesso!", "ok");
      await atualizarMontagemS2(S.projeto_id);
    } else {
      showToast(`❌ Falha ao reordenar: ${(res && res.error) || 'Erro'}`, "erro");
    }
  } catch (e) {
    showToast(`❌ Erro de rede ao reordenar: ${e.message}`, "erro");
  }
}

async function excluirCenaStoryboard(cid, event) {
  if (event) event.stopPropagation();
  if (!confirm(`Deseja realmente excluir a Cena ${cid}? A linha do tempo será recalculada automaticamente.`)) {
    return;
  }

  showToast(`⏳ Excluindo Cena ${cid}...`, "info");
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/cena/${cid}`, {
      method: "DELETE"
    });

    if (res && res.success) {
      showToast(`✅ Cena ${cid} excluída com sucesso!`, "ok");
      _montagemCenaAtivaIdx = Math.max(0, _montagemCenaAtivaIdx - 1);
      await atualizarMontagemS2(S.projeto_id);
    } else {
      showToast(`❌ Falha ao excluir cena: ${(res && res.error) || 'Erro'}`, "erro");
    }
  } catch (e) {
    showToast(`❌ Erro de rede: ${e.message}`, "erro");
  }
}

function excluirCenaAtivaMontagem() {
  if (!_montagemCenas || !_montagemCenas[_montagemCenaAtivaIdx]) return;
  const c = _montagemCenas[_montagemCenaAtivaIdx];
  const cid = c.id || c.scene_index;
  excluirCenaStoryboard(cid, null);
}

// ── Banco de Cenas IA ──────────────────────────────────────────────────────
function renderBancoCenas(cenas) {
  const grid = $("banco-cenas-grid");
  const countEl = $("banco-cenas-count");
  if (countEl) countEl.textContent = `${cenas.length} cenas`;
  if (!grid) return;

  grid.innerHTML = cenas.map((c, idx) => {
    const cid = c.id || c.scene_index;
    const temMidia = Boolean(c.tem_midia || c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
    const baseMidia = c.arquivo_midia ? c.arquivo_midia.split(/[\\/]/).pop() : `${String(cid).padStart(3, '0')}.png`;
    const imgFile = baseMidia.replace(/\.mp4$/i, '.png');
    const imgUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgFile)}?t=${Date.now()}`;
    const isActive = idx === _montagemCenaAtivaIdx;

    return `
      <div class="banco-cena-thumb ${isActive ? 'active' : ''}" title="Cena ${cid}" onclick="selecionarCenaMontagem(${idx}, true)">
        ${temMidia
          ? `<img src="${imgUrl}" alt="Cena ${cid}" onerror="this.src='/api/v2/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}'" />`
          : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:10px;color:rgba(255,255,255,0.3)">⏳</div>`
        }
        <span class="banco-cena-badge">${cid}</span>
      </div>
    `;
  }).join("");
}

// ── Inspector de Cena Handlers ─────────────────────────────────────────────
function salvarTranscricaoCenaAtiva() {
  if (!_montagemCenas || !_montagemCenas[_montagemCenaAtivaIdx]) return;
  const c = _montagemCenas[_montagemCenaAtivaIdx];
  const cid = c.id || c.scene_index;
  const textEl = $("s2-inspector-caption-text");
  const statusEl = $("s2-inspector-caption-status");
  const novoTexto = textEl ? textEl.value.trim() : "";

  c.texto_transcricao = novoTexto;
  c.texto = novoTexto;
  c.fala = novoTexto;
  c.narration = novoTexto;

  atualizarPlayerLiveCaption(c);

  // Atualiza Trilha CC no DOM
  const subEl = document.querySelector(`#s2-nle-sub-${_montagemCenaAtivaIdx} .sub-clip-text`);
  if (subEl) subEl.textContent = novoTexto || '(Sem fala)';

  if (statusEl) statusEl.textContent = "Salvando...";

  api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/cena/${cid}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texto_transcricao: novoTexto })
  }).then((res) => {
    if (res && res.success) {
      if (statusEl) {
        statusEl.textContent = "✓ Salvo!";
        setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2500);
      }
      showToast("✅ Narração salva com sucesso!", "ok");
    } else {
      if (statusEl) statusEl.textContent = "❌ Falha";
    }
  }).catch((e) => {
    if (statusEl) statusEl.textContent = "❌ Erro";
  });
}

function copiarTextoCenaAtiva() {
  const textEl = $("s2-inspector-caption-text");
  if (textEl && textEl.value) {
    navigator.clipboard.writeText(textEl.value);
    showToast("✅ Narração copiada!", "ok");
  }
}

function copiarPromptCenaAtiva() {
  const pEl = $("s2-player-prompt");
  if (pEl && pEl.innerText) {
    navigator.clipboard.writeText(pEl.innerText);
    showToast("✅ Prompt copiado!", "ok");
  }
}

function removerLegendaCenaAtiva() {
  const toggle = $("s2-inspector-caption-toggle");
  if (toggle) {
    toggle.checked = false;
    toggleCaptionCena(false);
    showToast("🚫 Legenda desativada nesta cena.", "info");
  }
}

/* TAREFA 7 (dedupe): existiam DUAS definicoes de toggleCaptionCena — esta e a de
   ~6900. Como declaracoes de funcao sofrem hoisting e a ULTIMA vence, somente a
   segunda estava de fato em uso. Em vez de descartar esta (que tinha 4 atualizacoes
   de UI a mais: badge, clipe da trilha CC, overlay e os DOIS flags
   legenda_ativa/caption_ativo), o comportamento das duas foi UNIFICADO na
   definicao unica remanescente — nenhuma funcionalidade foi perdida. */

function abrirTrocaMidiaCena(cid) {
  showToast(`💡 Para trocar a imagem ou vídeo da Cena ${cid}, gere uma nova cena na Aba 3 (Produção).`, "info");
}

function abrirTrocaMidiaCenaAtiva() {
  if (!_montagemCenas || !_montagemCenas[_montagemCenaAtivaIdx]) return;
  const cid = _montagemCenas[_montagemCenaAtivaIdx].id || _montagemCenas[_montagemCenaAtivaIdx].scene_index;
  abrirTrocaMidiaCena(cid);
}

// ── Atualização Principal da Aba Montagem ──────────────────────────────────
async function atualizarMontagemS2(projeto_id) {
  orquestrarLayoutMontagem3Paineis();
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(projeto_id)}/sincronizar`);
    if (!res || !res.success) return;

    const prontas = res.cenas_com_midia || 0;
    const total = res.total_cenas || 0;
    const pct = total > 0 ? Math.round((prontas / total) * 100) : 0;

    if ($("s2-montagem-cenas-ok")) $("s2-montagem-cenas-ok").textContent = `${prontas} / ${total}`;
    if ($("montagem-proj-title")) $("montagem-proj-title").textContent = projeto_id || "MONTAGEM";

    const badge = $("s2-montagem-status-badge");
    if (badge) {
      if (res.pode_montar) {
        badge.className = "badge badge-ok";
        badge.textContent = `✓ ${prontas}/${total} Prontas`;
      } else {
        badge.className = "badge badge-wait";
        badge.textContent = `${prontas}/${total} Prontas (${(res.cenas_faltantes || []).length} pendentes)`;
      }
    }

    _montagemCenas = res.cenas || [];

    // Calcula duração total do projeto
    _montagemTotalDuracao = 0;
    _montagemCenas.forEach((c) => {
      const tFim = parseFloat(c.tempo_fim || (parseFloat(c.tempo_inicio || 0) + parseFloat(c.duracao || 5.0)));
      if (tFim > _montagemTotalDuracao) _montagemTotalDuracao = tFim;
    });

    if ($("s2-montagem-duracao-total")) {
      $("s2-montagem-duracao-total").textContent = fmtTs(_montagemTotalDuracao);
    }

    // Configura áudio no player
    const audioEl = $("s2-montagem-audio");
    if (audioEl && res.tem_audio) {
      const audioUrl = `/api/v2/projeto/${encodeURIComponent(projeto_id)}/audio`;
      if (audioEl.src !== window.location.origin + audioUrl) {
        audioEl.src = audioUrl;
      }
      // Extrai o waveform do áudio para a timeline (WebAudio API)
      _extrairWaveformAudio(audioUrl);
    }

    // Inicializa eventos do player
    initMontagemPlayerEvents();

    // Carrega presets de legendas nativas do CapCut
    await carregarPresetsLegendasCapCut();

    // Carrega transições nativas do CapCut Desktop
    await carregarTransicoesCapCut();

    // Renderiza Storyboard (Reordenar Cenas)
    renderStoryboardCenas(_montagemCenas);

    // Renderiza Banco de Cenas IA
    renderBancoCenas(_montagemCenas);

    // Renderiza Timeline NLE Multitrack (com V1 e CC)
    renderMontagemTimeline(_montagemCenas);

    if (_montagemCenas.length) {
      const idxSel = Math.min(_montagemCenaAtivaIdx, _montagemCenas.length - 1);
      selecionarCenaMontagem(idxSel, false);
    }
    atualizarStatusBrollMontagem(projeto_id);
  } catch (e) {
    console.warn("Erro ao atualizar montagem:", e);
  }
}

function renderMontagemTimeline(cenas) {
  const trackVideo = $("s2-nle-track-video");
  const ruler = $("s2-nle-ruler");
  const totalSecs = Math.ceil(_montagemTotalDuracao || (cenas.length * 5.0));
  const pxPerSec = _montagemPxPerSec();
  const totalWidthPx = Math.max(900, Math.round(totalSecs * pxPerSec) + 150);

  const wrapper = $("s2-nle-tracks-wrapper");
  if (wrapper) wrapper.style.width = `${totalWidthPx}px`;
  if (ruler) ruler.style.width = `${totalWidthPx}px`;
  const waveformCanvas = $("s2-nle-waveform-canvas");
  if (waveformCanvas) {
    waveformCanvas.style.width = `${totalWidthPx}px`;
    waveformCanvas.style.height = "64px";
  }

  // 1. Renderiza Clipes Proporcionais na Trilha de Vídeo V1
  trackVideo.innerHTML = cenas.map((c, idx) => {
    const cid = c.id || c.scene_index;
    const temMidia = Boolean(c.tem_midia || c.image_status === "READY" || (c.arquivo_midia && c.status === "BAIXADA"));
    const cls = temMidia ? "ready" : "";
    const isVideo = Boolean(c.tipo === "video" || c.media_intent === "video" || (c.arquivo_midia && c.arquivo_midia.toLowerCase().endsWith(".mp4")));
    const tipoBadge = isVideo ? "🎬" : "🖼";
    const durSec = Math.max(1.0, parseFloat(c.duracao || 5.0));
    const tIni = parseFloat(c.tempo_inicio || 0);
    const clipWidth = Math.round(durSec * pxPerSec);
    const clipLeft = Math.round(tIni * pxPerSec);
    const baseMidia = c.arquivo_midia ? c.arquivo_midia.split(/[\\/]/).pop() : `${String(cid).padStart(3, '0')}.png`;
    const imgFile = baseMidia.replace(/\.mp4$/i, '.png');
    const imgUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgFile)}?t=${Date.now()}`;

    return `
      <div id="s2-nle-clip-${idx}" class="nle-clip ${cls} ${idx === _montagemCenaAtivaIdx ? 'active' : ''}" 
           style="position:absolute;left:${clipLeft}px;width:${clipWidth}px;"
           draggable="true"
           data-scene-idx="${idx}"
           data-start="${tIni}"
           data-dur="${durSec}"
           title="Cena ${cid} · ${durSec.toFixed(1)}s · ${temMidia ? 'Pronta' : 'Pendente'}"
           onclick="selecionarCenaMontagem(${idx}, true)">
        <div class="nle-clip-thumb">
          ${temMidia 
            ? `<img src="${imgUrl}" alt="Cena ${cid}" onerror="this.src='/api/v2/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}'" />` 
            : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:10px;color:rgba(255,255,255,0.3)">⏳</div>`
          }
        </div>
        <div class="nle-clip-info">
          <span>${tipoBadge} ${cid}</span>
          <span>${durSec.toFixed(1)}s</span>
        </div>
      </div>
    `;
  }).join("");

  // 1.5 Marcadores de Transición (P6) entre cenas — ícono + color por tipo
  let markersHtml = "";
  cenas.forEach((c, idx) => {
    if (idx >= cenas.length - 1) return;
    const trSal = c.transicao_saida || { tipo: "fade_out", duracao_ms: 300 };
    const infoSal = _TRANS_INFO[trSal.tipo] || _TRANS_INFO["fade_out"];
    const tFim = parseFloat(c.tempo_fim || (parseFloat(c.tempo_inicio || 0) + parseFloat(c.duracao || 5.0)));
    const xMarker = Math.round(tFim * pxPerSec);
    const titulo = `Transición ${infoSal.rot} · ${trSal.duracao_ms}ms (Cena ${c.id || c.scene_index} → Cena ${(cenas[idx + 1] || {}).id || (idx + 2)})`;
    markersHtml += `\n      <div class="nle-transition-marker tr-${infoSal.tipo}" title="${titulo}" style="left:${xMarker}px" data-scene-idx="${idx}" onclick="abrirMenuTransicion(${idx})">${infoSal.icono}</div>`;
  });
  trackVideo.innerHTML += markersHtml;

  // 1.8 Renderiza Trilha CC (Legendas)
  const trackCC = $("s2-nle-track-cc");
  if (trackCC) {
    trackCC.innerHTML = cenas.map((c, idx) => {
      const cid = c.id || c.scene_index;
      const durSec = Math.max(1.0, parseFloat(c.duracao || 5.0));
      const tIni = parseFloat(c.tempo_inicio || 0);
      const clipWidth = Math.round(durSec * pxPerSec);
      const clipLeft = Math.round(tIni * pxPerSec);
      const ativa = c.legenda_ativa !== false && c.caption_ativo !== false;
      const txt = (c.texto_transcricao || c.texto || c.fala || "").trim();

      return `
        <div id="s2-nle-sub-${idx}" class="nle-sub-clip ${ativa ? 'sub-ativa' : 'sub-inativa'} ${idx === _montagemCenaAtivaIdx ? 'active' : ''}"
             style="position:absolute;left:${clipLeft}px;width:${clipWidth}px;"
             title="Cena ${cid} · ${ativa ? 'Legenda Ativa' : 'Legenda Oculta'}: ${txt.substring(0, 50)}"
             onclick="selecionarCenaMontagem(${idx}, true)">
          <div class="nle-sub-clip-content">
            <span class="sub-clip-status">${ativa ? '💬' : '🚫'}</span>
            <span class="sub-clip-text">${txt ? txt : '<em>(Sem fala)</em>'}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  // 1.9 TAREFA 5 — Trilha M1 (BGM): clipe REAL proporcional, lido de #sel-bgm-trilha
  // (nome/duracao reais) em vez do texto estatico de preencherTrilhaBgm().
  _renderTrilhaBgmM1(pxPerSec);

  // 2. Renderiza Marcadores da Régua de Tempo (Ruler)
  if (ruler) {
    let rulerHtml = "";
    const intervalSec = _montagemTimelineZoom < 0.8 ? 10 : 5;

    for (let sec = 0; sec <= totalSecs; sec += intervalSec) {
      const leftPx = Math.round(sec * pxPerSec);
      rulerHtml += `
        <div class="nle-ruler-tick" style="left:${leftPx}px">
          ${fmtTs(sec)}
        </div>
      `;
    }
    ruler.innerHTML = rulerHtml;

    // Clique na régua para pular tempo (reaproveita o seek compartilhado do playhead)
    ruler.onclick = (e) => {
      _buscarTimelineMontagemPorX(e.clientX);
    };
  }

  // 3. Atualiza Trilha de Áudio A1
  const audioTrack = $("s2-nle-track-audio");
  const audioTrackText = $("s2-nle-audio-track-label");
  if (audioTrack) {
    const videoTrackEl = document.getElementById('s2-nle-track-video');
    const realWidth = videoTrackEl ? videoTrackEl.scrollWidth : (totalWidthPx - 60);
    audioTrack.style.width = `${realWidth}px`;
  }
  if (audioTrackText) {
    audioTrackText.textContent = `Áudio Original Sincronizado (${fmtTs(_montagemTotalDuracao)})`;
  }

  // 4. Redesenha o waveform do áudio (alinhado à mesma escala da timeline)
  _desenharWaveform();

  initTimelineDragAndDrop();
}

/* ============================================================
   TAREFA 5 — Trilha M1 (BGM) com clipe REAL
   Antes, #s2-nle-track-bgm só recebia um texto estático em preencherTrilhaBgm(),
   e apenas uma vez (atrás da guarda de idempotência _layout3paineisAplicado, que
   saía antes do projeto carregar). Agora renderMontagemTimeline desenha um clipe
   com largura proporcional (durSec * pxPerSec), no mesmo padrão da trilha V1,
   lendo os dados REAIS da trilha escolhida em #sel-bgm-trilha.
   ============================================================ */

/** Lê a trilha BGM selecionada no painel "Trilha Sonora & BGM". */
function _dadosTrilhaBgmAtual() {
  const sel = $("sel-bgm-trilha");
  const audio = $("bgm-player-preview");
  const volEl = $("slider-bgm-volume");
  const duckEl = $("chk-bgm-ducking");
  const caminho = sel ? String(sel.value || "") : "";
  let nome = "";
  if (sel && sel.selectedIndex >= 0 && sel.options[sel.selectedIndex]) {
    nome = String(sel.options[sel.selectedIndex].textContent || "")
      .replace(/^\s*🎵\s*/, "")
      .replace(/\s*\([\d.,]+\s*MB\)\s*$/, "")
      .trim();
  }
  if (!nome && caminho) nome = caminho.split(/[\\/]/).pop();
  const dur = (audio && isFinite(audio.duration) && audio.duration > 0) ? audio.duration : 0;
  return {
    arquivo: caminho,
    nome: nome,
    duracao: dur,
    volume: volEl ? Math.round(parseFloat(volEl.value || 0.14) * 100) : 14,
    ducking: duckEl ? !!duckEl.checked : true,
  };
}

/** Desenha o clipe da trilha M1. Roda a cada renderMontagemTimeline (zoom,
 *  reordenação, troca de projeto) e também ao selecionar/ajustar a trilha. */
function _renderTrilhaBgmM1(pxPerSec) {
  const track = $("s2-nle-track-bgm");
  if (!track) return;
  const pps = pxPerSec || _montagemPxPerSec();
  const info = _dadosTrilhaBgmAtual();

  if (!info.arquivo) {
    track.innerHTML = '<div class="nle-bgm-strip">🎵 Nenhuma trilha selecionada — escolha uma música em "Áudio &amp; BGM"</div>';
    return;
  }

  const nomeSeguro = String(info.nome || "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
  const durSec = info.duracao > 0 ? info.duracao : Math.max(1, _montagemTotalDuracao || 0);
  const largura = Math.max(80, Math.round(durSec * pps));
  const meta = `${fmtTs(durSec)} · vol ${info.volume}%${info.ducking ? " · ducking" : ""}`;

  track.innerHTML =
    `<div class="nle-bgm-clip" style="width:${largura}px" title="${nomeSeguro} — ${meta}">` +
      `<span class="nle-bgm-nome">🎵 ${nomeSeguro}</span>` +
      `<span class="nle-bgm-meta">${meta}</span>` +
    `</div>`;
}

/* ============================================================
   TAREFA 6 — Preview REAL de transição (2 frames animados)
   ============================================================ */

/** URL da miniatura de uma cena da timeline (mesma convenção da trilha V1). */
function _thumbUrlCenaMontagem(cena) {
  if (!cena || !S.projeto_id) return "/static/placeholder_cena.png";
  const cid = cena.id || cena.scene_index || 0;
  const base = cena.arquivo_midia ? String(cena.arquivo_midia).split(/[\\/]/).pop() : "";
  const imgFile = base
    ? base.replace(/\.(mp4|mov|webm|jpg|jpeg|png)$/i, ".png")
    : `${String(cid).padStart(3, "0")}.png`;
  return `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgFile)}?t=${Date.now()}`;
}

/** Reinicia as animações CSS do preview (botão "↻" do preview de transição). */
function _replayPreviewTransicion() {
  const box = document.querySelector("#s2-trans-preview .nle-trans-prev");
  if (!box) return;
  box.querySelectorAll("img").forEach((im) => {
    im.style.animation = "none";
    void im.offsetWidth;   // força reflow para a animação recomeçar do zero
    im.style.animation = "";
  });
}

// ── Waveform do Áudio (WebAudio API + Canvas 2D) ────────────────────────────
function _extrairWaveformAudio(url) {
  if (!url || url === _montagemWaveformSrc) return;
  _montagemWaveform = null;
  const canvas = $("s2-nle-waveform-canvas");
  if (canvas) canvas.dataset.carregando = "1";
  try {
    fetch(url)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.arrayBuffer(); })
      .then((buf) => {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) throw new Error("WebAudio indisponível");
        const actx = new Ctx();
        return actx.decodeAudioData(buf).then((ab) => {
          _montagemWaveform = _calcularWaveform(ab);
          _montagemWaveformSrc = url;
          try { if (actx.close) actx.close(); } catch (e) {}
          if (canvas) delete canvas.dataset.carregando;
          _desenharWaveform();
        });
      })
      .catch((e) => {
        _montagemWaveform = null;
        if (canvas) delete canvas.dataset.carregando;
        console.warn("Waveform do áudio indisponível:", e);
        _desenharWaveform();
      });
  } catch (e) {
    console.warn("Waveform do áudio indisponível:", e);
  }
}

function _calcularWaveform(audioBuf) {
  const nCh = audioBuf.numberOfChannels || 1;
  const sr = audioBuf.sampleRate || 44100;
  const len = audioBuf.length;
  const block = sr / _montagemWaveformFps; // amostras por frame de 10ms
  const frames = Math.max(1, Math.ceil(len / block));
  const out = new Float32Array(frames);
  const chans = [];
  for (let c = 0; c < nCh; c++) {
    try { chans.push(audioBuf.getChannelData(c)); } catch (e) { chans.push(null); }
  }
  let maxV = 0.0001;
  for (let f = 0; f < frames; f++) {
    const s = Math.floor(f * block);
    const e = Math.min(len, Math.floor((f + 1) * block));
    let peak = 0;
    for (let i = s; i < e; i++) {
      let v = 0;
      for (let c = 0; c < nCh; c++) {
        const ch = chans[c];
        if (!ch) continue;
        v += ch[i] || 0;
      }
      v = Math.abs(v / nCh);
      if (v > peak) peak = v;
    }
    out[f] = peak;
    if (peak > maxV) maxV = peak;
  }
  // Normaliza 0-1 (piso para silêncios não zerarem completamente)
  for (let f = 0; f < frames; f++) {
    out[f] = Math.max(0.02, Math.min(1, out[f] / maxV));
  }
  return out;
}

function _desenharWaveform() {
  const canvas = $("s2-nle-waveform-canvas");
  if (!canvas) return;
  const width = canvas.clientWidth || parseInt(canvas.style.width, 10) || 900;
  const height = canvas.clientHeight || parseInt(canvas.style.height, 10) || 64;
  const dpr = window.devicePixelRatio || 1;
  const pw = Math.round(width * dpr);
  const ph = Math.round(height * dpr);
  if (canvas.width !== pw || canvas.height !== ph) {
    canvas.width = pw;
    canvas.height = ph;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const pxPerSec = _montagemPxPerSec();
  const audio = $("s2-montagem-audio");
  const cur = (audio && audio.duration) ? audio.currentTime : 0;

  // Grid temporal (a cada 5s ou 10s conforme o zoom — igual à régua)
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.font = "9px monospace";
  ctx.textBaseline = "bottom";
  const intervalSec = _montagemTimelineZoom < 0.8 ? 10 : 5;
  for (let sec = 0; sec <= _montagemTotalDuracao; sec += intervalSec) {
    const x = Math.round(sec * pxPerSec) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
    ctx.fillText(fmtTs(sec), x + 3, height - 2);
  }

  // Transiciones (P6) — sombreado suave del rango de transición de salida de cada cena
  if (_montagemCenas && _montagemCenas.length) {
    for (let i = 0; i < _montagemCenas.length - 1; i++) {
      const cT = _montagemCenas[i];
      const trSal = cT.transicao_saida || { tipo: "fade_out", duracao_ms: 300 };
      const infoT = _TRANS_INFO[trSal.tipo] || _TRANS_INFO["fade_out"];
      const xFin = (parseFloat(cT.tempo_inicio || 0) + parseFloat(cT.duracao || 5.0)) * pxPerSec;
      const mitadPx = Math.max(4, ((trSal.duracao_ms || 300) / 1000) * pxPerSec / 2);
      const x0 = Math.max(0, xFin - mitadPx);
      const x1 = Math.min(width, xFin + mitadPx);
      if (x1 > x0) {
        ctx.fillStyle = infoT.color + "2e";
        ctx.fillRect(x0, 0, x1 - x0, height);
      }
    }
  }

  if (_montagemWaveform && _montagemWaveform.length) {
    const midY = height / 2;
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, "rgba(34,199,122,0.45)");
    grad.addColorStop(0.5, "rgba(34,199,122,0.95)");
    grad.addColorStop(1, "rgba(34,199,122,0.45)");
    ctx.fillStyle = grad;
    const maxH = height - 6;
    for (let x = 0; x < width; x++) {
      const t0 = x / pxPerSec;
      const t1 = (x + 1) / pxPerSec;
      const i0 = Math.max(0, Math.floor(t0 * _montagemWaveformFps));
      const i1 = Math.min(_montagemWaveform.length, Math.ceil(t1 * _montagemWaveformFps) + 1);
      let peak = 0;
      for (let i = i0; i < i1; i++) {
        const v = _montagemWaveform[i];
        if (v > peak) peak = v;
      }
      const h = Math.max(1, Math.round(peak * maxH));
      ctx.fillRect(x, midY - h / 2, 1, h);
    }
  } else if (canvas.dataset.carregando) {
    ctx.fillStyle = "rgba(34,199,122,0.12)";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "rgba(34,199,122,0.6)";
    ctx.font = "10px sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillText("Carregando waveform do áudio...", 8, height / 2);
  } else {
    ctx.fillStyle = "rgba(34,199,122,0.07)";
    ctx.fillRect(0, 0, width, height);
  }

  // Linha de playback (vermelha — sincronizada com o playhead HTML)
  const playX = Math.round(cur * pxPerSec) + 0.5;
  ctx.strokeStyle = "#f43f5e";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(playX, 0);
  ctx.lineTo(playX, height);
  ctx.stroke();

  // Clique no waveform → seek no áudio
  if (!canvas.dataset.boundClick) {
    canvas.dataset.boundClick = "1";
    canvas.addEventListener("click", (e) => {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const audioEl = $("s2-montagem-audio");
      if (audioEl && audioEl.duration) {
        audioEl.currentTime = Math.max(0, Math.min(audioEl.duration, x / _montagemPxPerSec()));
      }
    });
  }
}

function abrirMenuTransicion(idx) {
  if (!_montagemCenas || idx < 0 || idx >= _montagemCenas.length) return;
  // REDESIGN F1: o marcador de transição agora apenas SELECIONA a cena e abre a
  // seção de transições já expandida no Inspector fixo (sem popup/overlay/modal).
  selecionarCenaMontagem(idx, false);
  renderTransicoesInspector();
  const body = $("s2-insp-trans-body");
  if (body) body.classList.remove("hidden");
  const st = $("s2-insp-trans-state");
  if (st) st.textContent = "recolher ▴";
  const insp = document.querySelector("#s2-tab-montagem .nle-inspector-panel");
  if (insp && insp.scrollIntoView) {
    insp.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function toggleTransicoesInspector() {
  const body = $("s2-insp-trans-body");
  if (!body) return;
  const aberto = !body.classList.contains("hidden");
  body.classList.toggle("hidden", aberto);
  const st = $("s2-insp-trans-state");
  if (st) st.textContent = aberto ? "expandir ▾" : "recolher ▴";
  if (!aberto) renderTransicoesInspector();
}

function renderTransicoesInspector() {
  const c = _montagemCenas && _montagemCenas[_montagemCenaAtivaIdx];
  if (!c) return;
  const trSal = c.transicao_saida || { tipo: "bordas_difusas", duracao_ms: 500 };
  const dur = parseInt(trSal.duracao_ms || 500, 10);
  const tipo = trSal.tipo || "bordas_difusas";

  _TRANS_MENU_IDX = _montagemCenaAtivaIdx;
  _TRANS_SEL = { tipo: tipo, saida: tipo, duracao_ms: dur };

  const selEl = $("s2-insp-trans-tipo-select");
  if (selEl) selEl.value = tipo;

  const durEl = $("s2-trans-dur");
  if (durEl) durEl.value = dur;

  const lbl = $("s2-trans-dur-lbl");
  if (lbl) lbl.textContent = `${(dur / 1000).toFixed(1)}s (${dur}ms)`;

  _actualizarPreviewTransicion();
}

function selecionarTipoTransicaoCapCut(tipo) {
  _TRANS_SEL.tipo = tipo;
  _TRANS_SEL.saida = tipo;
  const selEl = $("s2-insp-trans-tipo-select");
  if (selEl && selEl.value !== tipo) selEl.value = tipo;
  _actualizarPreviewTransicion();
}

function _actualizarPreviewTransicion() {
  const preview = $("s2-trans-preview");
  const durEl = $("s2-trans-dur");
  if (!preview) return;
  const dur = durEl ? parseInt(durEl.value, 10) : 500;
  const lblEl = $("s2-trans-dur-lbl");
  if (lblEl) lblEl.textContent = `${(dur / 1000).toFixed(1)}s (${dur}ms)`;

  const tipo = _TRANS_SEL.tipo || (_montagemCenas && _montagemCenas[_montagemCenaAtivaIdx]?.transicao_saida?.tipo) || "bordas_difusas";
  const info = _TRANS_INFO[tipo] || { rot: tipo, icono: "✨", color: "#6366f1" };

  // TAREFA 6: preview REAL — 2 frames (cena atual + próxima) com a animação do
  // tipo escolhido (.nle-trans-prev[data-tipo=...], style.css:5165+). Substitui o
  // antigo preview estático (rótulo + barra de gradiente).
  const cAtual = _montagemCenas && _montagemCenas[_montagemCenaAtivaIdx];
  const cProx = _montagemCenas && _montagemCenas[_montagemCenaAtivaIdx + 1];
  const urlA = _thumbUrlCenaMontagem(cAtual);
  const urlB = cProx ? _thumbUrlCenaMontagem(cProx) : "/static/placeholder_cena.png";
  const durSeg = (dur / 1000).toFixed(1);

  preview.innerHTML =
    `<div class="nle-trans-prev" data-tipo="${tipo}" style="--trans-dur:${dur}ms">` +
      `<img class="nle-trans-prev-a" src="${urlA}" alt="Cena atual" onerror="this.src='/static/placeholder_cena.png'">` +
      `<img class="nle-trans-prev-b" src="${urlB}" alt="Próxima cena" onerror="this.src='/static/placeholder_cena.png'">` +
      `<span class="nle-trans-prev-lbl" style="color:${info.color}">${info.icono} ${info.rot}</span>` +
    `</div>` +
    `<button class="btn btn-xs btn-ghost" type="button" title="Repetir animação" onclick="_replayPreviewTransicion()">↻</button>` +
    `<span class="mono" style="font-size:10px;color:var(--text-muted)">${durSeg}s</span>`;
}

async function aplicarTransicaoCapCutTodasCenasPeloInspector() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo selecionado.", "erro");
    return;
  }
  const selEl = $("s2-insp-trans-tipo-select");
  const durEl = $("s2-trans-dur");
  const btn = $("btn-insp-trans-todas");
  const statusEl = $("s2-insp-trans-status");

  const tipo = selEl ? selEl.value : (_TRANS_SEL.tipo || "bordas_difusas");
  const dur = durEl ? parseInt(durEl.value, 10) : 500;
  const info = _TRANS_INFO[tipo] || { rot: tipo, icono: "✨" };

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Aplicando em Todas as Cenas...";
  }
  if (statusEl) statusEl.textContent = "Aplicando transição a todas as cenas...";

  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/transicoes_lote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tipo: tipo, duracao_ms: dur, lado: "saida" })
    });

    if (res && res.success) {
      if (_montagemCenas && _montagemCenas.length) {
        _montagemCenas.forEach((c) => {
          c.transicao_saida = { tipo: tipo, duracao_ms: dur };
        });
      }

      // Sincroniza também os controles da coluna esquerda (Transições Lote)
      const selGlobal = $("sel-capcut-transicao-global");
      const durGlobal = $("slider-capcut-trans-dur");
      const lblGlobal = $("label-capcut-trans-dur");
      if (selGlobal) selGlobal.value = tipo;
      if (durGlobal) durGlobal.value = dur;
      if (lblGlobal) lblGlobal.textContent = `${(dur / 1000).toFixed(1)}s`;

      renderMontagemTimeline(_montagemCenas);
      showToast(`✅ Transição '${info.icono} ${info.rot}' (${(dur/1000).toFixed(1)}s) aplicada a todas as cenas!`, "ok");
      if (statusEl) {
        statusEl.textContent = `✓ Aplicado a todas as cenas (${(dur/1000).toFixed(1)}s)`;
        setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 3500);
      }
    } else {
      showToast(`❌ Falha ao aplicar: ${(res && res.error) || 'Erro'}`, "erro");
      if (statusEl) statusEl.textContent = "Erro ao aplicar.";
    }
  } catch (e) {
    showToast(`❌ Erro de rede: ${e.message}`, "erro");
    if (statusEl) statusEl.textContent = "Erro de conexão.";
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "⚡ Aplicar em Todas as Cenas";
    }
  }
}

async function _guardarMenuTransicion() {
  if (!_montagemCenas || _montagemCenas[_montagemCenaAtivaIdx] == null) return;
  const cena = _montagemCenas[_montagemCenaAtivaIdx];
  const cid = cena.id || cena.scene_index;
  const selEl = $("s2-insp-trans-tipo-select");
  const durEl = $("s2-trans-dur");
  const statusEl = $("s2-insp-trans-status");

  const tipo = selEl ? selEl.value : (_TRANS_SEL.tipo || "bordas_difusas");
  const dur = durEl ? parseInt(durEl.value, 10) : 500;
  const info = _TRANS_INFO[tipo] || { rot: tipo, icono: "✨" };

  if (statusEl) statusEl.textContent = "Salvando nesta cena...";

  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/transicion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_id: cid, lado: "saida", tipo: tipo, duracao_ms: dur })
    });
    if (res && res.success) {
      cena.transicao_saida = { tipo: tipo, duracao_ms: dur };
      renderMontagemTimeline(_montagemCenas);
      showToast(`✅ Transição '${info.icono} ${info.rot}' salva na Cena ${cid}!`, "ok");
      if (statusEl) {
        statusEl.textContent = "✓ Salvo nesta cena!";
        setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2500);
      }
    } else {
      showToast(`❌ Falha ao salvar: ${(res && res.error) || 'Erro'}`, "erro");
    }
  } catch (e) {
    showToast(`❌ Erro: ${e.message}`, "erro");
  }
}

async function _quitarMenuTransicion() {
  if (!_montagemCenas || _montagemCenas[_montagemCenaAtivaIdx] == null) return;
  const cena = _montagemCenas[_montagemCenaAtivaIdx];
  const cid = cena.id || cena.scene_index;
  const statusEl = $("s2-insp-trans-status");

  if (statusEl) statusEl.textContent = "Removendo transição...";

  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/transicion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_id: cid, lado: "saida", tipo: "none", duracao_ms: 0 })
    });
    if (res && res.success) {
      cena.transicao_saida = { tipo: "none", duracao_ms: 0 };
      const selEl = $("s2-insp-trans-tipo-select");
      if (selEl) selEl.value = "none";
      _TRANS_SEL.tipo = "none";
      _actualizarPreviewTransicion();
      renderMontagemTimeline(_montagemCenas);
      showToast(`✂️ Transição removida da Cena ${cid} (Corte Seco).`, "info");
      if (statusEl) {
        statusEl.textContent = "✓ Transição removida!";
        setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2500);
      }
    }
  } catch (e) {
    showToast(`❌ Erro: ${e.message}`, "erro");
  }
}

function _cerrarMenuTransicion() {
  _TRANS_MENU_IDX = null;
  _TRANS_SEL = {};
  const panel = $("s2-nle-transition-menu");
  if (panel) panel.classList.add("hidden");
  const overlay = document.getElementById("s2-nle-transition-overlay");
  if (overlay) overlay.classList.add("hidden");
}

function initTimelineDragAndDrop() {
  const trackVideo = $("s2-nle-track-video");
  if (!trackVideo) return;

  let dragSrcIdx = null;

  trackVideo.querySelectorAll(".nle-clip").forEach(clip => {
    clip.addEventListener("dragstart", (e) => {
      dragSrcIdx = Number(clip.dataset.sceneIdx);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragSrcIdx);
      clip.style.opacity = "0.4";
    });

    clip.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      clip.classList.add("drag-over");
    });

    clip.addEventListener("dragleave", () => {
      clip.classList.remove("drag-over");
    });

    clip.addEventListener("drop", (e) => {
      e.preventDefault();
      clip.classList.remove("drag-over");
      const targetIdx = Number(clip.dataset.sceneIdx);
      if (dragSrcIdx !== null && dragSrcIdx !== targetIdx && _montagemCenas && _montagemCenas.length) {
        const item = _montagemCenas.splice(dragSrcIdx, 1)[0];
        _montagemCenas.splice(targetIdx, 0, item);
        renderMontagemTimeline(_montagemCenas);
        selecionarCenaMontagem(targetIdx, false);
      }
    });

    clip.addEventListener("dragend", () => {
      clip.style.opacity = "1";
      trackVideo.querySelectorAll(".nle-clip").forEach(c => c.classList.remove("drag-over"));
    });
  });
}

// ANTIGRAVITY Passo 1: handlers NOMEADOS do player — permitem removeEventListener
// (removeEventListener exige a MESMA referência de função), tornando a
// inicialização idempotente em trocas de projeto/abas e recargas.
// Bug 1 (Bloco B) — o playhead agora é atualizado por UM loop de requestAnimationFrame
// (lê audio.currentTime a cada frame), NÃO mais preso ao event 'timeupdate' (~4x/s).
// O loop é cancelado ao pausar/terminar/trocar de projeto para não vazar rAF.
function _montagemSincronizarPlayhead() {
  const audio = $("s2-montagem-audio");
  const playhead = $("s2-nle-playhead");
  if (!audio || !audio.duration) return;
  const cur = audio.currentTime;
  const dur = audio.duration;

  if ($("s2-player-timecode")) {
    $("s2-player-timecode").textContent = `${fmtTs(cur)} / ${fmtTs(dur)}`;
  }

  if (playhead) {
    const pxPerSec = _montagemPxPerSec();
    const leftPx = Math.round(cur * pxPerSec);
    playhead.style.transition = 'none';
    playhead.style.left = `${leftPx}px`;

    const container = $("s2-nle-scroll-container");
    if (container && !audio.paused && !_playheadArrastando) {
      const scrollLeft = container.scrollLeft;
      const containerWidth = container.clientWidth;
      if (leftPx > scrollLeft + containerWidth - 100 || leftPx < scrollLeft) {
        container.scrollLeft = Math.max(0, leftPx - 80);
      }
    }
  }

  // Durante o scrub (drag da bolinha) NÃO troca a cena nem rola a timeline:
  // isso seria feito no mouseup, evitando "pulo" do playhead no meio do arrasto.
  if (!_playheadArrastando && _montagemCenas && _montagemCenas.length) {
    let matchIdx = 0;
    for (let i = 0; i < _montagemCenas.length; i++) {
      const tIni = parseFloat(_montagemCenas[i].tempo_inicio || 0);
      const tFim = parseFloat(_montagemCenas[i].tempo_fim ||
                   tIni + parseFloat(_montagemCenas[i].duracao || 5.0));
      if (cur >= tIni && cur < tFim) { matchIdx = i; break; }
      else if (cur >= tIni) { matchIdx = i; }
    }
    if (matchIdx !== _montagemCenaAtivaIdx) {
      selecionarCenaMontagem(matchIdx, false);
    }
  }

  _desenharWaveform();
}

// Atualização LEVE (só timecode + posição da bolinha) durante o scrub da bolinha.
function _montagemAtualizarPosicaoPlayhead() {
  const audio = $("s2-montagem-audio");
  const playhead = $("s2-nle-playhead");
  if (!audio || !audio.duration || !playhead) return;
  const cur = audio.currentTime;
  const dur = audio.duration;
  if ($("s2-player-timecode")) {
    $("s2-player-timecode").textContent = `${fmtTs(cur)} / ${fmtTs(dur)}`;
  }
  const leftPx = Math.round(cur * _montagemPxPerSec());
  playhead.style.transition = 'none';
  playhead.style.left = `${leftPx}px`;
}

// Após um seek: sincronização completa quando parado; leve enquanto arrasta.
function _onMontagemSeeked() {
  if (_playheadArrastando) {
    _montagemAtualizarPosicaoPlayhead();
  } else {
    _montagemSincronizarPlayhead();
  }
}

function _pararRAFPlayheadMontagem() {
  if (_montagemRAF) {
    cancelAnimationFrame(_montagemRAF);
    _montagemRAF = 0;
  }
}

// Callback do loop: sincroniza e re-agenda somente enquanto estiver tocando.
function _onMontagemTimeUpdate() {
  const audio = $("s2-montagem-audio");
  if (!audio) return;
  _montagemSincronizarPlayhead();
  if (!audio.paused && !audio.ended && audio.currentSrc) {
    _montagemRAF = requestAnimationFrame(_onMontagemTimeUpdate);
  } else {
    _montagemRAF = 0;
  }
}

function _iniciarRAFPlayheadMontagem() {
  _pararRAFPlayheadMontagem();
  const audio = $("s2-montagem-audio");
  if (!audio) return;
  _montagemSincronizarPlayhead();
  if (!audio.paused && !audio.ended && audio.currentSrc) {
    _montagemRAF = requestAnimationFrame(_onMontagemTimeUpdate);
  }
}

function _onMontagemEndedMedia() {
  _pararRAFPlayheadMontagem();
  _montagemSincronizarPlayhead();
  _onMontagemEnded();
}

// Seek compartilhado (régua + bolinha do playhead): X do clique -> tempo.
function _buscarTimelineMontagemPorX(clientX) {
  const audio = $("s2-montagem-audio");
  const ruler = $("s2-nle-ruler");
  if (!audio || !ruler) return;
  const rect = ruler.getBoundingClientRect();
  const x = clientX - rect.left;
  const duracao = audio.duration || 0;
  const alvo = Math.max(0, Math.min(duracao, x / _montagemPxPerSec()));
  if (audio.currentTime !== alvo) {
    audio.currentTime = alvo;
  }
  if (_playheadArrastando) {
    _montagemAtualizarPosicaoPlayhead();
  } else {
    _montagemSincronizarPlayhead();
  }
}

// Bug 2 (Bloco B) — bolinha do playhead clicável/arrastável (scrub na timeline).
// Habilita pointer-events SOMENTE na bolinha via JS; o CSS do playhead fica intacto.
function _initPlayheadKnobSeek() {
  const playhead = $("s2-nle-playhead");
  if (!playhead) return;
  const head = playhead.querySelector(".nle-playhead-head") || playhead;
  if (head.dataset.knobSeekBound) return;
  head.dataset.knobSeekBound = "1";

  head.style.pointerEvents = "auto";
  head.style.cursor = "ew-resize";
  head.style.touchAction = "none";

  const pegarX = (e) => {
    if (e.touches && e.touches[0]) return e.touches[0].clientX;
    if (e.changedTouches && e.changedTouches[0]) return e.changedTouches[0].clientX;
    return e.clientX;
  };

  if (window.PointerEvent) {
    head.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { head.setPointerCapture(e.pointerId); } catch (_) {}
      head.dataset.knobDrag = "1";
      _playheadArrastando = true;
      _buscarTimelineMontagemPorX(pegarX(e));
    });
    head.addEventListener("pointermove", (e) => {
      if (head.dataset.knobDrag !== "1") return;
      _buscarTimelineMontagemPorX(pegarX(e));
    });
    const soltarPointer = (e) => {
      if (head.dataset.knobDrag !== "1") return;
      _buscarTimelineMontagemPorX(pegarX(e));
      _playheadArrastando = false;
      head.dataset.knobDrag = "0";
      try { head.releasePointerCapture(e.pointerId); } catch (_) {}
      _montagemSincronizarPlayhead();
    };
    head.addEventListener("pointerup", soltarPointer);
    head.addEventListener("pointercancel", soltarPointer);
    return;
  }

  // Fallback mouse + touch (navegadores sem PointerEvent)
  let arrastando = false;
  const mover = (e) => { e.preventDefault(); _buscarTimelineMontagemPorX(pegarX(e)); };
  const soltar = () => {
    arrastando = false;
    _playheadArrastando = false;
    _montagemSincronizarPlayhead();
    window.removeEventListener("mousemove", mover);
    window.removeEventListener("mouseup", soltar);
    window.removeEventListener("touchmove", mover);
    window.removeEventListener("touchend", soltar);
  };
  head.addEventListener("mousedown", (e) => {
    e.preventDefault();
    arrastando = true;
    _playheadArrastando = true;
    _buscarTimelineMontagemPorX(pegarX(e));
    window.addEventListener("mousemove", mover);
    window.addEventListener("mouseup", soltar);
  });
  head.addEventListener("touchstart", (e) => {
    e.preventDefault();
    arrastando = true;
    _playheadArrastando = true;
    _buscarTimelineMontagemPorX(pegarX(e));
    window.addEventListener("touchmove", mover, { passive: false });
    window.addEventListener("touchend", soltar);
  }, { passive: false });
}

function _onMontagemPlay() {
  const playBtn = $("btn-montagem-play");
  if (playBtn) playBtn.textContent = "⏸ Pause";
}

function _onMontagemPause() {
  const playBtn = $("btn-montagem-play");
  if (playBtn) playBtn.textContent = "▶ Play";
}

function _onMontagemEnded() {
  const playBtn = $("btn-montagem-play");
  if (playBtn) playBtn.textContent = "▶ Play";
}

function _onMontagemKeydown(e) {
  if (e.code === "Space" && S2_ACTIVE_TAB === "montagem" &&
      e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") {
    e.preventDefault();
    toggleMontagemPlayback();
  }
}

// ANTIGRAVITY Passo 1: inicialização IDEMPOTENTE do player (resetAndBindMontagemPlayer).
// A trava global antiga (if (_montagemPlayerInited) return;) impedia RE-vincular os
// listeners ao abrir um segundo projeto ou recarregar dados — o <audio> era recriado/
// realimentado, mas os eventos nunca mais eram reatribuídos e o playhead parava de andar.
function initMontagemPlayerEvents() {
  const audio = $("s2-montagem-audio");

  // Remove listeners antigos (mesma referência de função) se o <audio> mudou.
  if (_montagemAudioEl && _montagemAudioEl !== audio && _montagemPlayerBound) {
    _pararRAFPlayheadMontagem();
    _montagemAudioEl.removeEventListener("play", _iniciarRAFPlayheadMontagem);
    _montagemAudioEl.removeEventListener("pause", _pararRAFPlayheadMontagem);
    _montagemAudioEl.removeEventListener("ended", _onMontagemEndedMedia);
    _montagemAudioEl.removeEventListener("emptied", _pararRAFPlayheadMontagem);
    _montagemAudioEl.removeEventListener("seeked", _onMontagemSeeked);
    _montagemAudioEl.removeEventListener("play", _onMontagemPlay);
    _montagemAudioEl.removeEventListener("pause", _onMontagemPause);
    _montagemAudioEl.removeEventListener("ended", _onMontagemEnded);
    window.removeEventListener("keydown", _onMontagemKeydown);
    _montagemPlayerBound = false;
  }

  if (audio && _montagemAudioEl !== audio) {
    // Bug 1: playhead em rAF (1 loop, cancelado no pause/end) — timeupdate removido.
    audio.addEventListener("play", _iniciarRAFPlayheadMontagem);
    audio.addEventListener("pause", _pararRAFPlayheadMontagem);
    audio.addEventListener("ended", _onMontagemEndedMedia);
    audio.addEventListener("emptied", _pararRAFPlayheadMontagem);
    audio.addEventListener("seeked", _onMontagemSeeked);
    audio.addEventListener("play", _onMontagemPlay);
    audio.addEventListener("pause", _onMontagemPause);
    audio.addEventListener("ended", _onMontagemEnded);
    _montagemAudioEl = audio;
  }

  // Bug 2: bolinha do playhead arrastável (scrub) — liga uma única vez.
  _initPlayheadKnobSeek();

  // Atalho de Teclado: Espaço para Play/Pause (vínculo global ÚNICO)
  if (!_montagemPlayerBound) {
    window.addEventListener("keydown", _onMontagemKeydown);
    _montagemPlayerBound = true;
  }
}

function fmtTsWithDecimals(seconds) {
  if (isNaN(seconds) || seconds < 0) return "00:00.00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 100);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
}

function selecionarCenaMontagem(idx, seekAudio = false) {
  if (!_montagemCenas || idx < 0 || idx >= _montagemCenas.length) return;
  _montagemCenaAtivaIdx = idx;
  const c = _montagemCenas[idx];
  const cid = c.id || c.scene_index;
  const durSec = parseFloat(c.duracao || 5.0);
  const tIni = parseFloat(c.tempo_inicio || 0);
  const tFim = parseFloat(c.tempo_fim || tIni + durSec);

  // Destaca clipe na Timeline NLE
  document.querySelectorAll(".nle-clip").forEach((clip, i) => {
    clip.classList.toggle("active", i === idx);
  });
  const activeClipEl = $(`s2-nle-clip-${idx}`);
  if (activeClipEl && activeClipEl.parentElement) {
    activeClipEl.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }

  // Destaca no Storyboard e Trilha de Legendas CC
  document.querySelectorAll(".storyboard-card").forEach((card, i) => {
    card.classList.toggle("active", i === idx);
  });
  const activeSbCard = $(`storyboard-card-${idx}`);
  if (activeSbCard && activeSbCard.parentElement) {
    activeSbCard.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
  }
  document.querySelectorAll(".banco-cena-thumb").forEach((thumb, i) => {
    thumb.classList.toggle("active", i === idx);
  });
  document.querySelectorAll(".nle-sub-clip").forEach((sub, i) => {
    sub.classList.toggle("active", i === idx);
  });

  // Atualiza Badges do Monitor
  if ($("s2-player-scene-tag")) {
    $("s2-player-scene-tag").textContent = `Cena ${String(cid).padStart(3, '0')} | ${fmtTs(tIni)} - ${fmtTs(tFim)}`;
  }
  if ($("s2-player-type-tag")) {
    $("s2-player-type-tag").textContent = `${c.tipo === 'video' ? '🎬 Vídeo' : '🖼 Imagem'} • ${c.tem_midia ? 'Pronta' : 'Pendente'}`;
  }
  if ($("s2-player-detail-cid")) {
    $("s2-player-detail-cid").textContent = `Cena ${cid}`;
  }
  if ($("s2-player-detail-dur")) {
    $("s2-player-detail-dur").textContent = `${durSec.toFixed(1)}s`;
  }

  // Atualiza Detalhes no Inspector
  if ($("s2-player-prompt")) {
    $("s2-player-prompt").textContent = c.prompt || c.prompt_imagem || "(Sem prompt cadastrado)";
  }
  if ($("s2-player-fala")) {
    $("s2-player-fala").textContent = c.fala || c.transcricao || c.texto || `(Cena ${cid} correspondente ao intervalo ${fmtTs(tIni)} - ${fmtTs(tFim)})`;
  }
  if ($("s2-player-path")) {
    $("s2-player-path").textContent = c.arquivo_midia ? c.arquivo_midia.split(/[\\/]/).pop() : `${String(cid).padStart(3, '0')}.png`;
  }

  // Inspector de Cena 2.0: Thumbnail Preview
  const inspThumb = $("s2-inspector-thumb-img");
  if (inspThumb) {
    const arquivoNome = (c.arquivo_midia || c.filename || "").split(/[\\/]/).pop() || "";
    const ehMp4 = /\.mp4$/i.test(arquivoNome);
    const baseMidia = arquivoNome || `${String(cid).padStart(3, '0')}.png`;
    const imgNome = ehMp4 ? baseMidia.replace(/\.mp4$/i, '.png') : baseMidia;
    inspThumb.src = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgNome)}?t=${Date.now()}`;
    inspThumb.onerror = () => {
      inspThumb.src = `/api/v2/cena_media/${encodeURIComponent(S.projeto_id)}/${cid}`;
    };
  }

  // Inspector de Cena 2.0: Transcrição / Fala Editável
  const txtTranscricao = c.texto_transcricao || c.texto || c.narration || c.fala || "";
  const inspCaption = $("s2-inspector-caption-text");
  if (inspCaption) {
    inspCaption.value = txtTranscricao;
  }
  const countBadge = $("s2-caption-count-badge");
  if (countBadge) {
    const palavras = txtTranscricao ? txtTranscricao.trim().split(/\s+/).filter(Boolean).length : 0;
    countBadge.textContent = `${palavras} pal. · ${txtTranscricao.length} car.`;
  }

  // Live Typing: ao digitar na textarea, reflete instantaneamente no player ao vivo
  if (inspCaption && !inspCaption.dataset.boundLiveTyping) {
    inspCaption.dataset.boundLiveTyping = "1";
    inspCaption.addEventListener("input", () => {
      if (!_montagemCenas || !_montagemCenas[_montagemCenaAtivaIdx]) return;
      const curr = _montagemCenas[_montagemCenaAtivaIdx];
      const val = inspCaption.value;
      curr.texto_transcricao = val;
      curr.texto = val;
      curr.narration = val;
      curr.fala = val;
      atualizarPlayerLiveCaption(curr);
      const subEl = document.querySelector(`#s2-nle-sub-${_montagemCenaAtivaIdx} .sub-clip-text`);
      if (subEl) subEl.textContent = val || '(Sem fala)';
      const cb = $("s2-caption-count-badge");
      if (cb) {
        const pCount = val ? val.trim().split(/\s+/).filter(Boolean).length : 0;
        cb.textContent = `${pCount} pal. · ${val.length} car.`;
      }
    });
  }

  // Ken Burns: reflete o estado salvo da cena no inspector
  const kenBurnsEl = document.getElementById("s2-inspector-ken-burns");
  if (kenBurnsEl) {
    kenBurnsEl.checked = !!c.ken_burns_ativo;
  }

  // Legendas: reflete estado da cena no inspector e badge
  const captionToggle = document.getElementById("s2-inspector-caption-toggle");
  const captionStyles = document.getElementById("s2-inspector-caption-styles");
  const captionBadge = $("s2-caption-status-badge");
  const ativa = c.legenda_ativa !== false && c.caption_ativo !== false;

  if (captionBadge) {
    captionBadge.textContent = ativa ? "ATIVA" : "INATIVA";
    captionBadge.className = ativa ? "badge badge-ok" : "badge badge-muted";
  }

  if (captionToggle) {
    captionToggle.checked = ativa;
    if (captionStyles) {
      captionStyles.style.opacity = ativa ? "1" : "0.4";
      captionStyles.style.pointerEvents = ativa ? "auto" : "none";
    }
  }

  // TAREFA 1/3: reflete no painel custom os overrides ja salvos desta cena.
  _hidratarControlesCaptionCustom(c);

  // Atualiza seleção na biblioteca de legendas CapCut à esquerda
  const estiloAtual = c.estilo_legenda || c.caption_style || _estiloLegendaAtivo;
  document.querySelectorAll("#capcut-subtitles-preset-list .capcut-sub-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.preset === estiloAtual);
  });

  // Atualiza preview de legenda em tempo real no Player 16:9
  atualizarPlayerLiveCaption(c);

  // REDESIGN F1: dropdown de Movimento reflete o motion_preset persistido
  const movEl = document.getElementById("s2-inspector-movimento");
  if (movEl) movEl.value = c.motion_preset || "";

  // REDESIGN F1: transições embutidas no Inspector (mantém estado atualizado)
  renderTransicoesInspector();

  // Atualiza Tela do Monitor 16:9
  const img = $("s2-player-img");
  const vid = $("s2-player-vid");
  const ph = $("s2-player-placeholder");

  if (c.tem_midia) {
    // Fase 1: o nome é derivado do arquivo persistido na cena (arquivo_midia/
    // filename), nunca reconstruído como 001.mp4 — o FFmpeg do B-Roll salva com o
    // stem original da foto (ex: 01_[00-00-00-05].mp4) e o 001.mp4 causava 404.
    const arquivoNome = (c.arquivo_midia || c.filename || "").split(/[\\/]/).pop() || "";
    const ehMp4 = /\.mp4$/i.test(arquivoNome);
    const baseMidia = arquivoNome || `${String(cid).padStart(3, '0')}.png`;
    const imgNome = ehMp4 ? baseMidia.replace(/\.mp4$/i, '.png') : baseMidia;
    const vidNome = ehMp4 ? baseMidia : baseMidia.replace(/\.[^.]+$/, '.mp4');
    const imgUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(imgNome)}`;
    const vidUrl = `/projeto/${encodeURIComponent(S.projeto_id)}/cenas/${encodeURIComponent(vidNome)}`;
    const temVideo = Boolean(
      (c.tipo === "video" || c.media_intent === "video" || baseMidia.endsWith('.mp4')) &&
      (c.video_status === "READY" || c.video_status === "BAIXADA" || baseMidia.endsWith('.mp4'))
    );
    if (temVideo) {
      if (img) img.style.display = "none";
      if (ph) ph.style.display = "none";
      if (vid) {
        vid.src = vidUrl;
        vid.style.display = "block";
        vid.load();
        vid.play().catch(() => {});
      }
    } else {
      if (vid) vid.style.display = "none";
      if (ph) ph.style.display = "none";
      if (img) {
        img.src = imgUrl;
        img.style.display = "block";
      }
    }
  } else {
    if (img) img.style.display = "none";
    if (vid) vid.style.display = "none";
    if (ph) {
      ph.style.display = "flex";
      ph.innerHTML = `<span style="font-size:32px">⏳</span><span style="font-weight:600">Cena ${cid} em geração...</span><span style="font-size:11px;color:var(--text-muted)">Duração: ${durSec.toFixed(1)}s</span>`;
    }
  }

  // Se solicitado (por clique na cena), sincroniza o áudio
  const audio = $("s2-montagem-audio");
  if (seekAudio && audio && audio.duration) {
    audio.currentTime = tIni;
  }
}

async function toggleKenBurnsCena(ativo) {
  // Guarda amigável (Falha 3b): feedback visual se nenhuma cena estiver
  // selecionada no Inspector, em vez de falhar em silêncio.
  //
  // NOTA DO DIAGNÓSTICO: o "erro genérico" relatado pelo operador
  // (ERR_CONTENT_LENGTH_MISMATCH em .../api/v2/projeto/<id>/audio) NÃO
  // foi causado pelo toggle nem por falta de cena selecionada — foi um
  // erro do endpoint de áudio que ocorreu ao mesmo tempo. A cena estava
  // selecionada (Joaquim / idx 1) e o PATCH retornou 200 OK.
  const c = _montagemCenas[_montagemCenaAtivaIdx];
  const chk = document.getElementById("s2-inspector-ken-burns");
  if (!c) {
    if (chk) chk.checked = false;
    showToast("❌ Selecione uma cena na timeline antes de ativar Ken Burns.");
    return;
  }
  const cid = c.id || c.scene_index;
  const anterior = !!c.ken_burns_ativo;
  c.ken_burns_ativo = ativo;
  try {
    await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/${cid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ken_burns_ativo: ativo })
    });
    console.log(`[Ken Burns] Persistido para cena ${cid}: ${ativo}`);
    showToast(ativo
      ? "✅ Ken Burns ativado nesta cena."
      : "✅ Ken Burns desativado nesta cena.");
  } catch (e) {
    console.warn("Erro ao salvar ken_burns_ativo:", e);
    // Reverte estado em memória e o visual do toggle (nada foi persistido)
    c.ken_burns_ativo = anterior;
    if (chk) chk.checked = anterior;
    showToast("❌ Falha ao salvar Ken Burns: " + ((e && e.message) || "erro desconhecido"));
  }
}

/* TAREFA 7: definicao UNICA de toggleCaptionCena (a duplicata em ~5696 foi
   removida). Comportamento = UNIAO das duas versoes anteriores: atualiza os dois
   flags (caption_ativo + legenda_ativa), o badge do Inspector, o clipe da trilha
   CC na timeline, o painel custom e o overlay do player, e persiste via
   PATCH /api/scene_plan/<projeto>/<cid>. */
async function toggleCaptionCena(ativo) {
    if (!_montagemCenas || !_montagemCenas[_montagemCenaAtivaIdx]) return;
    const c = _montagemCenas[_montagemCenaAtivaIdx];
    const cid = c.id || c.scene_index;

    c.caption_ativo = ativo;
    c.legenda_ativa = ativo;

    const badge = $("s2-caption-status-badge");
    if (badge) {
        badge.textContent = ativo ? "ATIVA" : "INATIVA";
        badge.className = ativo ? "badge badge-ok" : "badge badge-muted";
    }

    const captionStyles = document.getElementById("s2-inspector-caption-styles");
    if (captionStyles) {
        captionStyles.style.opacity = ativo ? "1" : "0.4";
        captionStyles.style.pointerEvents = ativo ? "auto" : "none";
    }

    // Clipe da trilha CC na timeline
    const subClip = $(`s2-nle-sub-${_montagemCenaAtivaIdx}`);
    if (subClip) {
        subClip.classList.toggle("sub-ativa", ativo);
        subClip.classList.toggle("sub-inativa", !ativo);
        const st = subClip.querySelector(".sub-clip-status");
        if (st) st.textContent = ativo ? "💬" : "🚫";
    }

    atualizarPlayerLiveCaption(c);

    try {
        await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/${cid}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ caption_ativo: ativo, legenda_ativa: ativo })
        });
    } catch (e) {
        console.warn("Erro ao salvar caption_ativo:", e);
    }
}

/* ============================================================
   TAREFA 1 (ressurreicao) — setCaptionStyle() era ORFA: nenhum
   `.caption-style-btn` existe no DOM (confirmado na auditoria). Ela volta a ser
   funcional como handler dos 4 controles novos do Inspector
   (#s2-cap-font-size / -font-family / -font-color / -position) e continua
   aceitando o id de um preset base (retrocompativel).
   Estado: cena.caption_custom = {font_size, font_family, font_color, position}
   Persistencia: PATCH /api/scene_plan/<projeto>/<cid> (mesma rota de
   toggleCaptionCena), com `caption_custom` na whitelist de atualizar_cena.
   ============================================================ */

const CAP_CUSTOM_CAMPOS = ["font_size", "font_family", "font_color", "position"];

/** Mapa nome-logico -> stack CSS real. "System Font" usa a fonte do projeto. */
const CAP_FONTES_CSS = {
    "System Font": "var(--font-body)",
    "Montserrat": '"Montserrat", Arial, sans-serif',
    "Arial Black": '"Arial Black", Impact, sans-serif',
    "Roboto": '"Roboto", Arial, sans-serif',
};

/** Normaliza cena.caption_custom (tolerante a ausencia/campos vazios). */
function _captionCustomDaCena(cena) {
    const cc = (cena && cena.caption_custom) || {};
    const tam = (cc.font_size !== null && cc.font_size !== undefined && cc.font_size !== "")
        ? parseInt(cc.font_size, 10) : null;
    return {
        font_size: (tam && !isNaN(tam)) ? tam : null,
        font_family: cc.font_family || "",
        font_color: cc.font_color || "",
        position: cc.position || "",
    };
}

/** TAREFA 3: aplica/limpa os overrides no overlay do player (via data-* + vars). */
function _aplicarCaptionCustomNoOverlay(captionEl, cc) {
    if (!captionEl) return;
    const tem = !!(cc && (cc.font_size || cc.font_family || cc.font_color || cc.position));
    if (!tem) {
        captionEl.removeAttribute("data-custom");
        captionEl.removeAttribute("data-pos");
        captionEl.style.removeProperty("--cap-font");
        captionEl.style.removeProperty("--cap-color");
        captionEl.style.removeProperty("--cap-size");
        return;
    }
    captionEl.setAttribute("data-custom", "1");
    if (cc.font_family) {
        captionEl.style.setProperty("--cap-font", CAP_FONTES_CSS[cc.font_family] || CAP_FONTES_CSS["System Font"]);
    }
    if (cc.font_color) captionEl.style.setProperty("--cap-color", cc.font_color);
    if (cc.font_size) captionEl.style.setProperty("--cap-size", cc.font_size + "px");
    if (cc.position) captionEl.setAttribute("data-pos", cc.position);
}

/** Reflete no painel do Inspector os overrides salvos da cena ativa. */
function _hidratarControlesCaptionCustom(cena) {
    const cc = _captionCustomDaCena(cena);
    const elSize = $("s2-cap-font-size");
    if (elSize) elSize.value = String(cc.font_size || 24);
    const elSizeVal = $("s2-cap-font-size-val");
    if (elSizeVal) elSizeVal.textContent = (cc.font_size || 24) + "px";
    const elFam = $("s2-cap-font-family");
    if (elFam) elFam.value = cc.font_family || "System Font";
    const elCor = $("s2-cap-font-color");
    if (elCor) elCor.value = cc.font_color || "#FFE135";
    const elPos = $("s2-cap-position");
    if (elPos) elPos.value = cc.position || "bottom-center";
    const st = $("s2-cap-custom-status");
    if (st) st.textContent = (cc.font_size || cc.font_family || cc.font_color || cc.position)
        ? "• personalizado nesta cena" : "";
}

async function setCaptionStyle(campo, valor) {
    const c = _montagemCenas && _montagemCenas[_montagemCenaAtivaIdx];
    if (!c) return;
    const cid = c.id || c.scene_index;

    // (a) Retrocompativel: `campo` nao e um campo custom -> trata como preset base.
    if (CAP_CUSTOM_CAMPOS.indexOf(campo) < 0) {
        c.caption_style = campo;
        c.estilo_legenda = campo;
        document.querySelectorAll(".caption-style-btn").forEach((btn) => {
            btn.classList.toggle("active", btn.dataset.style === campo);
        });
        document.querySelectorAll("#capcut-subtitles-preset-list .capcut-sub-card").forEach((card) => {
            card.classList.toggle("active", card.dataset.preset === campo);
        });
        atualizarPlayerLiveCaption(c);
        try {
            await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/${cid}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ caption_style: campo })
            });
        } catch (e) {
            console.warn("Erro ao salvar caption_style:", e);
        }
        return;
    }

    // (b) Campo custom (TAREFA 2): grava em cena.caption_custom.
    const cc = Object.assign(
        { font_size: null, font_family: "", font_color: "", position: "" },
        c.caption_custom || {}
    );
    if (campo === "font_size") {
        const n = parseInt(valor, 10);
        cc.font_size = (!isNaN(n) && n > 0) ? n : null;
    } else {
        cc[campo] = String(valor || "");
    }
    c.caption_custom = cc;

    const elSizeVal = $("s2-cap-font-size-val");
    if (elSizeVal) elSizeVal.textContent = (cc.font_size || 24) + "px";

    // TAREFA 3: feedback imediato no overlay do player, antes da rede.
    atualizarPlayerLiveCaption(c);

    const st = $("s2-cap-custom-status");
    if (st) st.textContent = "Salvando…";
    try {
        const res = await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/${cid}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ caption_custom: cc })
        });
        if (st) {
            st.textContent = (res && res.success === false)
                ? ("❌ " + (res.error || "falha ao salvar"))
                : "✓ Salvo nesta cena";
        }
    } catch (e) {
        if (st) st.textContent = "❌ " + (e.message || "erro de rede");
    }
}

function toggleMontagemPlayback() {
  const audio = $("s2-montagem-audio");
  if (!audio) return;
  if (audio.paused) {
    audio.play().catch((e) => console.warn("Erro no play de áudio:", e));
  } else {
    audio.pause();
  }
}

function pularCenaMontagem(delta) {
  if (!_montagemCenas || !_montagemCenas.length) return;
  const novoIdx = Math.max(0, Math.min(_montagemCenas.length - 1, _montagemCenaAtivaIdx + delta));
  selecionarCenaMontagem(novoIdx, true);
}

function seekOffsetMontagem(offsetSec) {
  const audio = $("s2-montagem-audio");
  if (!audio || !audio.duration) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + offsetSec));
}

function copiarPromptCenaAtiva() {
  if (!_montagemCenas || !_montagemCenas.length) return;
  const c = _montagemCenas[_montagemCenaAtivaIdx];
  const prompt = c.prompt || c.prompt_imagem || "";
  if (prompt) {
    navigator.clipboard.writeText(prompt);
    showToast("✅ Prompt copiado para a área de transferência!");
  }
}

function abrirMediaCenaAtiva() {
  const screen = $("s2-player-screen");
  if (!screen) return;
  if (document.fullscreenElement) {
    document.exitFullscreen();
  } else {
    screen.requestFullscreen().catch(err => {
      console.warn("Fullscreen não disponível:", err);
    });
  }
}

// ===========================================================================
// PRODUÇÃO DE B-ROLL & TRILHA SONORA INTELIGENTE (5. PRODUÇÃO / MONTAGEM)
// ===========================================================================

let _brollPollingInterval = null;

async function atualizarStatusBrollMontagem(projeto_id) {
  if (!projeto_id) return;
  const badge = $("broll-media-badge");
  const info = $("broll-contagem-info");

  let totalImagens = 0;
  let totalVideos = 0;
  if (_montagemCenas && _montagemCenas.length) {
    _montagemCenas.forEach(c => {
      const isVid = Boolean(c.tipo === "video" || c.media_intent === "video" || (c.arquivo_midia && c.arquivo_midia.toLowerCase().endsWith(".mp4")));
      if (isVid) totalVideos++;
      else if (c.tem_midia) totalImagens++;
    });
  }

  if (badge) {
    if (totalVideos > 0 && totalVideos >= (_montagemCenas.length || 1)) {
      badge.className = "badge badge-ok";
      badge.textContent = `✓ ${totalVideos} Vídeos MP4`;
    } else if (totalVideos > 0) {
      badge.className = "badge badge-primary";
      badge.textContent = `${totalVideos} Vídeos / ${totalImagens} Fotos`;
    } else {
      badge.className = "badge badge-wait";
      badge.textContent = `${totalImagens} Imagens Estáticas`;
    }
  }

  if (info) {
    const total = (_montagemCenas && _montagemCenas.length) || (totalImagens + totalVideos);
    if (total > 0 && totalVideos >= total) {
      info.textContent = `✓ ${totalVideos} vídeos MP4 gerados e ativos no player`;
    } else if (totalVideos > 0) {
      info.textContent = `${totalVideos} de ${total} convertidos em vídeo MP4`;
    } else {
      info.textContent = `Estado: ${totalImagens} imagens estáticas prontas para conversão em vídeo MP4`;
    }
  }

  // Verifica se já há job de broll rodando
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(projeto_id)}/broll_status`);
    if (res && res.success && res.job && res.job.status === "processando") {
      _mostrarProgressoBroll(res.job);
      _iniciarPollingBroll(projeto_id);
    }
  } catch (e) {}

  // Carrega perfil musical silenciosamente para popular dropdown
  carregarPerfilMusical(projeto_id, false);
}

function _mostrarProgressoBroll(job) {
  const box = $("broll-progress-box");
  const lbl = $("broll-progress-label");
  const pct = $("broll-progress-pct");
  const bar = $("broll-progress-bar");
  const btn = $("btn-gerar-broll-mp4");
  if (box) box.style.display = "block";
  if (btn) btn.disabled = true;

  const prog = Math.min(100, Math.max(0, parseInt(job.progresso || 0, 10)));
  if (lbl) lbl.textContent = job.mensagem || `Convertendo... ${prog}%`;
  if (pct) pct.textContent = `${prog}%`;
  if (bar) bar.style.width = `${prog}%`;
}

function _iniciarPollingBroll(projeto_id) {
  if (_brollPollingInterval) clearInterval(_brollPollingInterval);
  _brollPollingInterval = setInterval(async () => {
    try {
      const res = await api(`/api/v2/montagem/${encodeURIComponent(projeto_id)}/broll_status`);
      if (!res || !res.success || !res.job) return;
      const job = res.job;
      _mostrarProgressoBroll(job);

      if (job.status === "concluido" || job.status === "erro") {
        clearInterval(_brollPollingInterval);
        _brollPollingInterval = null;
        const btn = $("btn-gerar-broll-mp4");
        if (btn) btn.disabled = false;

        if (job.status === "concluido") {
          showToast(`🎬 B-Roll Concluído: ${job.convertidas || 0} cenas convertidas com movimento!`);
          setTimeout(() => {
            const box = $("broll-progress-box");
            if (box) box.style.display = "none";
          }, 3500);
          atualizarMontagemS2(projeto_id);
        } else {
          showToast(`❌ Erro na conversão: ${job.mensagem || 'Falha ao gerar B-Roll'}`);
        }
      }
    } catch (e) {
      console.warn("Erro no polling de broll:", e);
    }
  }, 1500);
}

async function iniciarGeracaoBrollMP4() {
  if (!S.projeto_id) {
    showToast("❌ Selecione um projeto primeiro.");
    return;
  }
  const btn = $("btn-gerar-broll-mp4");
  if (btn) btn.disabled = true;

  try {
    showToast("🎬 Iniciando produção de clipes MP4 B-Roll com efeito Ken Burns...");
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/gerar_broll_mp4`, {
      method: "POST"
    });
    if (res && res.success) {
      _mostrarProgressoBroll({ progresso: 5, mensagem: "Iniciando renderização de movimento nas imagens..." });
      _iniciarPollingBroll(S.projeto_id);
    } else {
      if (btn) btn.disabled = false;
      showToast(`❌ ${res.error || 'Não foi possível iniciar a geração de B-Roll'}`);
    }
  } catch (e) {
    if (btn) btn.disabled = false;
    showToast(`❌ Erro: ${e.message}`);
  }
}

async function carregarTransicoesCapCut() {
  const selGlobal = $("sel-capcut-transicao-global");
  const selInsp = $("s2-insp-trans-tipo-select");
  try {
    const res = await api("/api/v2/capcut/transicoes");
    if (res && res.success && Array.isArray(res.transicoes)) {
      const optsHtml = res.transicoes.map(t => {
        const star = t.recommended ? " ⭐" : "";
        const cat = t.category ? ` (${t.category})` : "";
        return `<option value="${t.id}">${t.name}${star}${cat}</option>`;
      }).join("") + `
        <option value="fade_out">Fade Out (Preto)</option>
        <option value="fade_in">Fade In (Luz)</option>
        <option value="dissolve">Dissolve (Crossfade)</option>
        <option value="none">✕ Sem Transição (Corte Seco)</option>
      `;

      if (selGlobal) {
        const valAtual = selGlobal.value || "bordas_difusas";
        selGlobal.innerHTML = optsHtml;
        if (Array.from(selGlobal.options).some(o => o.value === valAtual)) {
          selGlobal.value = valAtual;
        }
      }
      if (selInsp) {
        const valAtualInsp = selInsp.value || (_TRANS_SEL && _TRANS_SEL.tipo) || "bordas_difusas";
        selInsp.innerHTML = optsHtml;
        if (Array.from(selInsp.options).some(o => o.value === valAtualInsp)) {
          selInsp.value = valAtualInsp;
        }
      }
    }
  } catch (e) {
    console.warn("Aviso ao carregar transições CapCut:", e);
  }
}
window.carregarTransicoesCapCut = carregarTransicoesCapCut;

function atualizarLabelDuracaoCapCut(val) {
  const lbl = $("lbl-capcut-trans-dur");
  if (lbl) {
    const segs = (parseInt(val, 10) / 1000).toFixed(1);
    lbl.textContent = `${segs}s`;
  }
}
window.atualizarLabelDuracaoCapCut = atualizarLabelDuracaoCapCut;

function aoMudarTransicaoCapCutGlobal(val) {
  const msg = $("capcut-trans-status-msg");
  if (msg) msg.textContent = "";
}
window.aoMudarTransicaoCapCutGlobal = aoMudarTransicaoCapCutGlobal;

async function aplicarTransicaoCapCutTodosClipes() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo.");
    return;
  }
  const sel = $("sel-capcut-transicao-global");
  const durEl = $("slider-capcut-trans-dur");
  const msg = $("capcut-trans-status-msg");
  const btn = $("btn-capcut-aplicar-todos");

  const tipo = sel ? sel.value : "bordas_difusas";
  const dur = durEl ? parseInt(durEl.value, 10) : 500;
  const nomeExibicao = sel && sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text.split("(")[0].trim() : tipo;

  if (msg) msg.textContent = "Aplicando...";
  if (btn) btn.disabled = true;

  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/transicoes_lote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tipo: tipo, duracao_ms: dur, lado: "saida" })
    });
    if (res && res.success) {
      if (msg) {
        msg.textContent = `✓ Aplicado (${(dur/1000).toFixed(1)}s)`;
        setTimeout(() => { if (msg) msg.textContent = ""; }, 4000);
      }
      showToast(`✂️ Transição CapCut '${nomeExibicao}' (${(dur/1000).toFixed(1)}s) aplicada a todos os clipes!`);
      atualizarMontagemS2(S.projeto_id);
    } else {
      if (msg) msg.textContent = "Erro ao aplicar";
      showToast(`❌ ${res.error || 'Erro ao aplicar transição'}`);
    }
  } catch (e) {
    if (msg) msg.textContent = "Erro";
    showToast(`❌ Erro: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}
window.aplicarTransicaoCapCutTodosClipes = aplicarTransicaoCapCutTodosClipes;

async function aplicarTransicoesEmLote() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo.");
    return;
  }
  const tipoEl = $("sel-transicao-lote-tipo");
  const durEl = $("sel-transicao-lote-dur");
  const statusEl = $("transicao-lote-status");
  const tipo = tipoEl ? tipoEl.value : "fade_out";
  const dur = durEl ? parseInt(durEl.value, 10) : 300;

  if (statusEl) statusEl.textContent = "Aplicando transições...";
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/transicoes_lote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tipo: tipo, duracao_ms: dur, lado: "saida" })
    });
    if (res && res.success) {
      if (statusEl) {
        statusEl.textContent = `✓ Aplicado (${tipo}, ${dur}ms)`;
        setTimeout(() => { statusEl.textContent = ""; }, 4000);
      }
      showToast(`✨ Transição '${tipo}' aplicada a todas as cenas!`);
      atualizarMontagemS2(S.projeto_id);
    } else {
      if (statusEl) statusEl.textContent = "Erro ao aplicar";
      showToast(`❌ ${res.error || 'Erro ao aplicar transições'}`);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = "Erro";
    showToast(`❌ Erro: ${e.message}`);
  }
}

async function carregarPerfilMusical(projeto_id, notify = true) {
  const pid = projeto_id || S.projeto_id;
  if (!pid) return;

  if (notify) showToast("🔍 Analisando roteiro e nicho para sugestão musical...");
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(pid)}/musica/perfil`);
    if (!res || !res.success || !res.perfil) return;
    const p = res.perfil;

    if ($("bgm-nicho-badge")) $("bgm-nicho-badge").textContent = p.nicho_nome || p.nicho || "Geral";
    if ($("bgm-estilo-val")) $("bgm-estilo-val").textContent = p.estilo || "-";
    if ($("bgm-bpm-val")) $("bgm-bpm-val").textContent = `${p.bpm_sugerido || '90-110'} BPM`;

    const tagsCont = $("bgm-tags-container");
    if (tagsCont && p.tags) {
      tagsCont.innerHTML = p.tags.map(t => `<span class="badge badge-muted" style="font-size:10px">#${t}</span>`).join("");
    }

    // Popular lista de trilhas disponíveis
    const sel = $("sel-bgm-trilha");
    if (sel && p.trilhas_disponiveis) {
      const trilhaAtual = (p.config_atual || {}).arquivo || "";
      let opts = `<option value="">-- Nenhuma música de fundo --</option>`;
      p.trilhas_disponiveis.forEach(t => {
        const selAttr = (trilhaAtual && (trilhaAtual === t.caminho || trilhaAtual.endsWith(t.nome))) ? "selected" : "";
        opts += `<option value="${t.caminho}" ${selAttr}>🎵 ${t.nome} (${(t.tamanho_mb || 0).toFixed(1)}MB)</option>`;
      });
      sel.innerHTML = opts;

      // Se há trilha atual configurada, sincroniza player e controles
      if (p.config_atual) {
        if (p.config_atual.volume != null && $("slider-bgm-volume")) {
          $("slider-bgm-volume").value = p.config_atual.volume;
          atualizarVolumeBgmLabel(p.config_atual.volume);
        }
        if (p.config_atual.ducking != null && $("chk-bgm-ducking")) {
          $("chk-bgm-ducking").checked = Boolean(p.config_atual.ducking);
        }
        if (trilhaAtual) {
          selecionarTrilhaBgm(trilhaAtual);
        }
      }
    }

    if (notify) showToast("✓ Perfil musical e recomendações carregadas!");
  } catch (e) {
    console.warn("Erro ao carregar perfil musical:", e);
  }
}

function selecionarTrilhaBgm(caminho) {
  const audio = $("bgm-player-preview");
  if (!audio) return;
  if (!caminho) {
    audio.style.display = "none";
    audio.pause();
    // TAREFA 5: trilha removida -> M1 volta ao estado "nenhuma trilha selecionada".
    _renderTrilhaBgmM1();
    return;
  }
  audio.src = `/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/audio?file=${encodeURIComponent(caminho)}`;
  audio.style.display = "block";
  audio.volume = parseFloat($("slider-bgm-volume")?.value || 0.14);

  // TAREFA 5: (re)desenha a trilha M1 na timeline imediatamente — sem reload.
  // A duracao real so existe apos o metadata: re-renderiza quando ela chegar.
  if (!audio.dataset.m1Bound) {
    audio.dataset.m1Bound = "1";
    audio.addEventListener("loadedmetadata", () => _renderTrilhaBgmM1());
    audio.addEventListener("durationchange", () => _renderTrilhaBgmM1());
  }
  _renderTrilhaBgmM1();
}

function atualizarVolumeBgmLabel(val) {
  const lbl = $("bgm-vol-label");
  const num = Math.round(parseFloat(val || 0.14) * 100);
  if (lbl) lbl.textContent = `${num}%`;
  const audio = $("bgm-player-preview");
  if (audio) audio.volume = parseFloat(val);
  // REDESIGN F1: espelha o volume na trilha M1 (BGM) da timeline — mantido como
  // fallback do estado "sem trilha" (.nle-bgm-strip).
  const strip = document.querySelector("#s2-nle-track-bgm .nle-bgm-strip");
  if (strip) strip.textContent = `🎵 BGM — volume ${num}% (ducking automático)`;
  // TAREFA 5: com trilha selecionada o clipe M1 mostra nome + volume + ducking.
  _renderTrilhaBgmM1();
}

async function salvarConfigTrilhaSonora() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo.");
    return;
  }
  const sel = $("sel-bgm-trilha");
  const volEl = $("slider-bgm-volume");
  const duckEl = $("chk-bgm-ducking");
  const statusEl = $("bgm-salvar-status");

  const caminho = sel ? sel.value : "";
  const vol = volEl ? parseFloat(volEl.value) : 0.14;
  const duck = duckEl ? duckEl.checked : true;

  if (statusEl) statusEl.textContent = "Salvando...";
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/musica/definir`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        arquivo: caminho,
        volume: vol,
        ducking: duck
      })
    });
    if (res && res.success) {
      if (statusEl) {
        statusEl.textContent = "✓ Trilha salva!";
        setTimeout(() => { statusEl.textContent = ""; }, 4000);
      }
      showToast(caminho ? "🎵 Trilha sonora e ducking vinculados ao projeto!" : "Trilha sonora desvinculada.");
    } else {
      if (statusEl) statusEl.textContent = "Erro ao salvar";
      showToast(`❌ ${res.error || 'Falha ao salvar trilha sonora'}`);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = "Erro";
    showToast(`❌ Erro: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// ABA 6: HANDLERS DE EXPORTAÇÃO (CAPCUT, MP4 RENDER, ZIP)
// ---------------------------------------------------------------------------

async function atualizarExportacaoS2(projeto_id) {
  if (!projeto_id) return;
  try {
    const res = await api(`/api/v2/montagem/${encodeURIComponent(projeto_id)}/sincronizar`);
    if (res && res.success) {
      _montagemCenas = res.cenas || [];
    }
  } catch (e) {}
}

async function exportarCapCutDireto() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo selecionado.", "erro");
    return;
  }
  const btn = $("btn-s2-exportar-capcut");
  const topBtn = $("btn-top-exportar-capcut");
  const statusBox = $("s2-capcut-export-status");
  if (btn) btn.disabled = true;
  if (topBtn) {
    topBtn.disabled = true;
    topBtn.innerHTML = "⏳ Exportando para CapCut...";
  }

  try {
    showToast("⏳ Montando rascunho oficial para CapCut Desktop com legendas e transições...", "info");
    const res = await apiJson(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/exportar_capcut`);
    if (res && res.success) {
      showToast("✅ Projeto exportado para o CapCut Desktop com sucesso!", "ok");
      if (statusBox) {
        statusBox.classList.remove("hidden");
        statusBox.innerHTML = `
          <div style="color:var(--success);font-weight:700;margin-bottom:4px">✓ Rascunho do CapCut Criado com Sucesso!</div>
          <div style="font-size:11px;color:var(--text-muted)">Abra o aplicativo <b>CapCut Desktop</b> no Windows e você verá o projeto <b>${S.projeto_id}</b> pronto na lista de rascunhos com imagens, legendas nativas e áudio sincronizado.</div>
          <div class="mono" style="font-size:10px;margin-top:6px;color:var(--accent-light)">Pasta: ${res.capcut_dir || ''}</div>
        `;
      }
    } else {
      showToast("❌ Erro ao exportar para CapCut: " + (res.error || "Falha desconhecida"), "erro");
    }
  } catch (e) {
    showToast("❌ Erro ao exportar para CapCut: " + e.message, "erro");
  } finally {
    if (btn) btn.disabled = false;
    if (topBtn) {
      topBtn.disabled = false;
      topBtn.innerHTML = "⚡ ENVIAR PARA O CapCut";
    }
  }
}

async function renderizarMp4Projeto() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo selecionado.");
    return;
  }
  const btn = $("btn-s2-exportar-video");
  const statusBox = $("s2-render-mp4-status");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⏳ Renderizando Vídeo via FFmpeg...";
  }

  if (statusBox) {
    statusBox.classList.remove("hidden");
    statusBox.innerHTML = `<div style="color:var(--accent-light);font-size:12px">⏳ Renderizando todas as cenas com o áudio original... Por favor, aguarde alguns segundos.</div>`;
  }

  try {
    const res = await apiJson(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/renderizar_mp4`);
    if (res && res.success) {
      showToast("✅ Vídeo final .MP4 renderizado com sucesso!");
      if (statusBox) {
        const mb = (res.tamanho_bytes / (1024 * 1024)).toFixed(1);
        statusBox.innerHTML = `
          <div style="color:var(--success);font-weight:700;margin-bottom:4px">✓ Vídeo .MP4 1080p Renderizado (${mb} MB)</div>
          <a href="${res.url_download}" class="btn btn-success btn-sm" style="display:inline-block;margin-top:6px;text-decoration:none">
            📥 Baixar Vídeo Final (.MP4)
          </a>
        `;
      }
    } else {
      showToast("❌ Erro ao renderizar: " + (res.error || "Falha"));
      if (statusBox) statusBox.innerHTML = `<div style="color:var(--error)">❌ Falha na renderização: ${res.error || 'Erro'}</div>`;
    }
  } catch (e) {
    showToast("❌ Erro na renderização: " + e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "▶ Renderizar Vídeo Final (.MP4)";
    }
  }
}

function baixarZipProjeto() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo selecionado.");
    return;
  }
  showToast("📦 Gerando pacote ZIP para download...");
  window.location.href = `/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/exportar_zip`;
}


function iniciarPollingTranscricaoS2() {
  // ANTIGRAVITY Passo 2: timer GLOBAL (parado por pararTodosPollings) para não
  // vazar ao trocar de projeto; detecta erro explícito e timeout de segurança
  // para NUNCA deixar o botão de transcrever travado/desabilitado para sempre.
  if (_pollTranscricaoTimer) clearInterval(_pollTranscricaoTimer);
  let erros = 0;
  let ticks = 0;
  const MAX_TICKS = 600; // 600 × 2s = 20 min de espera máxima
  const startTime = Date.now();

  const progEl = $("s2-transcricao-progress");
  const barFill = $("s2-transcricao-bar-fill");
  const timerBadge = $("s2-transcricao-timer-badge");
  const pctEl = $("s2-transcricao-pct");
  const msgEl = $("s2-transcricao-msg");
  const statusLbl = $("s2-transcricao-status-label");

  if (progEl) progEl.style.display = "block";
  if (barFill) barFill.style.width = "5%";
  if (pctEl) pctEl.textContent = "5%";
  if (statusLbl) statusLbl.textContent = "🎙 Transcrevendo áudio com Whisper...";
  if (msgEl) msgEl.textContent = "Detectando falas e pausas na gravação...";

  // Timer local suave de segundos
  const localTimer = setInterval(() => {
    if (!_pollTranscricaoTimer) { clearInterval(localTimer); return; }
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    if (timerBadge) timerBadge.textContent = `⏱ ${m}:${s}s`;

    // Animação progressiva suave
    if (barFill && pctEl) {
      const simulatedPct = Math.min(92, Math.floor(5 + (elapsed * 2.2)));
      barFill.style.width = `${simulatedPct}%`;
      pctEl.textContent = `${simulatedPct}%`;
    }
  }, 1000);

  _pollTranscricaoTimer = setInterval(async () => {
    ticks++;
    try {
      const st = await api(`/api/status/${encodeURIComponent(S.projeto_id)}`);
      if (st && st.transcricao_completa) {
        clearInterval(_pollTranscricaoTimer);
        _pollTranscricaoTimer = null;
        clearInterval(localTimer);

        if (barFill) barFill.style.width = "100%";
        if (pctEl) pctEl.textContent = "100%";
        if (statusLbl) statusLbl.textContent = "✅ Transcrição Concluída!";
        if (msgEl) msgEl.textContent = "Roteiro e timestamps gerados com sucesso.";
        $("btn-s2-transcrever").disabled = false;
        atualizarBadgeAudioS2("concluido");
        await carregarStudio2Dados(S.projeto_id);
        return;
      }
      // Falha EXPLÍCITA na etapa de transcrição
      if (st && st.etapa === "transcrever" && st.status === "erro") {
        throw new Error(st.erro || "Falha na transcrição");
      }
      // Timeout de segurança — nunca deixa o botão travado indefinidamente
      if (ticks >= MAX_TICKS) {
        throw new Error("Tempo limite de transcrição excedido.");
      }
    } catch (e) {
      erros++;
      if (erros >= 3 || ticks >= MAX_TICKS) {
        clearInterval(_pollTranscricaoTimer);
        _pollTranscricaoTimer = null;
        clearInterval(localTimer);
        $("btn-s2-transcrever").disabled = false;
        atualizarBadgeAudioS2("erro");
        if (statusLbl) statusLbl.textContent = "❌ Falha na Transcrição";
        if (msgEl) msgEl.textContent = e.message || "Erro durante o processamento do áudio.";
        console.warn("Polling de transcrição interrompido:", e.message || e);
      }
    }
  }, 2000);
}

// A inicialização do Studio 2.0 e da Inteligência de Personagens é feita no ponto
// ÚNICO de entrada (init → document.addEventListener("DOMContentLoaded", init)).
// ANTIGRAVITY Passo 2: removido este listener duplicado — evitava callbacks
// duplicados em botões (btn-s2-escolher-avatar, s2-input-personagem, etc.),
// que disparavam requisições em dobro a cada clique.



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

  // Abas de tipo de identidade (Personagem vs Biblioteca — aba Avatar Flow @me REMOVIDA)
  let tipoIdentidadeAtivo = "personagem";
  if ($("s2-tab-tipo-personagem")) {
    $("s2-tab-tipo-personagem").addEventListener("click", () => {
      tipoIdentidadeAtivo = "personagem";
      $("s2-tab-tipo-personagem").className = "btn btn-sm btn-primary";
      // ABA Avatar Flow @me REMOVIDA — não há mais $("s2-tab-tipo-avatar")
      if ($("s2-tab-tipo-biblioteca")) $("s2-tab-tipo-biblioteca").className = "btn btn-sm btn-ghost";
      if ($("s2-bloco-personagem")) $("s2-bloco-personagem").classList.remove("hidden");
      if ($("s2-bloco-biblioteca-personagens")) $("s2-bloco-biblioteca-personagens").classList.add("hidden");
    });

    // Listener da aba "Avatar Flow @me" REMOVIDO (elemento não existe mais no DOM)
    // $("s2-tab-tipo-avatar").addEventListener("click", () => { ... });

    if ($("s2-tab-tipo-biblioteca")) {
      $("s2-tab-tipo-biblioteca").addEventListener("click", async () => {
        tipoIdentidadeAtivo = "biblioteca";
        $("s2-tab-tipo-biblioteca").className = "btn btn-sm btn-primary";
        $("s2-tab-tipo-personagem").className = "btn btn-sm btn-ghost";
        // ABA Avatar Flow @me REMOVIDA — não há mais $("s2-tab-tipo-avatar")
        if ($("s2-bloco-biblioteca-personagens")) $("s2-bloco-biblioteca-personagens").classList.remove("hidden");
        if ($("s2-bloco-personagem")) $("s2-bloco-personagem").classList.add("hidden");
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
      const raw = $("s2-input-personagem").value.trim();
      const val = raw ? (raw.startsWith("@") ? raw : `@${raw}`) : "";
      // ANTI-GENÉRICO: nunca exibir/preencher uma tag genérica ("@Personagem").
      // Com o campo vazio o backend recusa com erro claro em vez de criar um
      // personagem genérico no Google Flow.
      $("s2-input-ref-flow").value = val;
      flowPersonagemCriado = false;
      if ($("s2-char-flow-status")) $("s2-char-flow-status").classList.add("hidden");
    });
    $("s2-input-personagem").addEventListener("blur", () => {
      const raw = $("s2-input-personagem").value.trim();
      if (raw && !raw.startsWith("@")) {
        $("s2-input-personagem").value = `@${raw}`;
        $("s2-input-ref-flow").value = `@${raw}`;
      }
    });
  }

  // ETAPA 1 e 3: Criar Personagem Oficial no Google Flow (ASSÍNCRONO com polling)
  async function criar_personagem_flow() {
    let nome = $("s2-input-personagem") ? $("s2-input-personagem").value.trim() : "";
    if (!nome) {
      alert("Por favor, digite o nome do personagem (ex: @Marcos).");
      return;
    }
    if (!nome.startsWith("@")) {
      nome = `@${nome}`;
      if ($("s2-input-personagem")) $("s2-input-personagem").value = nome;
      if ($("s2-input-ref-flow")) $("s2-input-ref-flow").value = nome;
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

    const setStatus = (bg, border, html) => {
      if (statusBox && statusTxt) {
        statusBox.classList.remove("hidden");
        statusBox.style.background = bg;
        statusBox.style.borderColor = border;
        statusTxt.innerHTML = html;
      }
    };

    // CORREÇÃO 3: 'nome' ya puede venir con '@' — quitar el literal para no
    // mostrar '@@Marcos' en los mensajes de estado (el envío real sigue con '@').
    const nomeLog = nome.startsWith("@") ? nome.slice(1) : nome;

    setStatus("rgba(124,92,252,0.12)", "var(--accent)",
      `⏳ <b>Iniciando criação do avatar...</b> Enviando foto e registrando <b>@${esc(nomeLog)}</b>...`);
    if (btnCriar) btnCriar.disabled = true;

    const fd = new FormData();
    fd.append("nome", nome);
    fd.append("imagem", file);
    fd.append("estilo_visual", estilo);

    try {
      // 1. POST retorna IMEDIATAMENTE (assíncrono) — sem "Failed to fetch" por timeout
      const r = await apiForm(`/api/v2/personagem/${encodeURIComponent(S.projeto_id)}/criar_flow`, fd);
      if (!r || !r.success) {
        const errMsg = (r && r.error) ? r.error : "Falha ao iniciar criação do avatar.";
        setStatus("rgba(239,68,68,0.15)", "#ef4444", `❌ <b>${esc(errMsg)}</b>`);
        alert(errMsg);
        return;
      }

      // 2. Polling do status a cada 2s (máx 180s = 3min para automação completa)
      setStatus("rgba(124,92,252,0.12)", "var(--accent)",
        `⏳ <b>Conectando ao Google Flow...</b> Aguardando automação criar <b>@${esc(nomeLog)}</b>...`);
      const urlStatus = `/api/v2/personagem/${encodeURIComponent(S.projeto_id)}/criar_flow_status`;
      const maxTentativas = 90; // 90 x 2s = 180s
      for (let tent = 0; tent < maxTentativas; tent++) {
        await new Promise(res => setTimeout(res, 2000));
        let st;
        try {
          st = await api(urlStatus);
        } catch (errPoll) {
          console.warn("[criar_flow] poll error:", errPoll);
          continue; // tenta de novo
        }
        if (st && st.status === "concluido" && st.resultado) {
          flowPersonagemCriado = true;
          flowPersonagemId = st.resultado.flow_character_id || "";
          flowPersonagemNome = st.resultado.flow_character_name || `@${nome}`;
          setStatus("rgba(34,197,94,0.15)", "#22c55e",
            `✅ <b>Avatar criado e vinculado com sucesso!</b><br>Identificador: <b>${esc(flowPersonagemNome)}</b><br>📸 <b>PERSONAGEM COM FOTO</b> carregado na identidade do projeto.`);
          await carregarDadosPersonagemS2(S.projeto_id);
          // Regenera prompts com a nova referência @Nome
          try {
            await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ nome_personagem: nome, estilo_visual: estilo })
            });
          } catch (ePrompt) { console.warn("[criar_flow] prompt regen:", ePrompt); }
          if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
          return;
        }
        if (st && st.status === "erro") {
          flowPersonagemCriado = false;
          const errMsg = st.erro || "Falha na criação do avatar.";
          setStatus("rgba(239,68,68,0.15)", "#ef4444",
            `❌ <b>${esc(errMsg)}</b><br><small>Verifique se o Google Flow está aberto no Chrome e tente novamente.</small>`);
          alert(errMsg);
          return;
        }
        if (st && st.etapa && st.etapa !== "Iniciando criação do avatar no Flow...") {
          // Atualiza a etapa exibida na UI
          const etapaAtual = st.etapa || "";
          setStatus("rgba(124,92,252,0.12)", "var(--accent)",
            `⏳ <b>${esc(etapaAtual)}</b> <small>${Math.min(100, st.progresso || 0)}%</small>`);
        }
      }
      // Timeout do polling
      flowPersonagemCriado = false;
      const errTimeout = "A criação do avatar excedeu 3 minutos. Verifique se o Google Flow está aberto no Chrome e tente novamente.";
      setStatus("rgba(239,68,68,0.15)", "#ef4444", `❌ <b>${esc(errTimeout)}</b>`);
      alert(errTimeout);
    } catch (e) {
      flowPersonagemCriado = false;
      const errMsg = `Falha ao iniciar criação do avatar: ${e.message}`;
      setStatus("rgba(239,68,68,0.15)", "#ef4444", `❌ <b>${esc(errMsg)}</b>`);
      alert(errMsg);
    } finally {
      if (btnCriar) btnCriar.disabled = false;
    }
  }
  window.criar_personagem_flow = criar_personagem_flow;

  if ($("btn-s2-criar-flow-personagem")) {
    $("btn-s2-criar-flow-personagem").addEventListener("click", criar_personagem_flow);
  }

  // ETAPA 4: Salvar Personagem no Projeto
  if ($("btn-s2-salvar-personagem")) {
    $("btn-s2-salvar-personagem").addEventListener("click", async () => {
      let nome = $("s2-input-personagem") ? $("s2-input-personagem").value.trim() : "";
      if (!nome) {
        alert("Digite o nome do personagem (ex: @Marcos).");
        return;
      }
      if (!nome.startsWith("@")) {
        nome = `@${nome}`;
        if ($("s2-input-personagem")) $("s2-input-personagem").value = nome;
        if ($("s2-input-ref-flow")) $("s2-input-ref-flow").value = nome;
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

  // Config de produção: IMAGEM e VÍDEO têm dropdowns SEPARADOS de modelo (sem radio de tipo).
  // Garante que os selects estejam populados caso a aba seja montada antes de carregar projeto.
  if ($("s2-prod-modelo-imagem") || $("s2-prod-modelo-video")) {
    filterModeloByTipo();
  }

  // Salvar configurações de produção
  const btnSalvarProdConfig = $("btn-s2-salvar-prod-config");
  if (btnSalvarProdConfig) {
    btnSalvarProdConfig.addEventListener("click", async () => {
      if (!S.projeto_id) return;
      const modeloImg = $("s2-prod-modelo-imagem") ? $("s2-prod-modelo-imagem").value : "Nano Banana 2";
      const modeloVid = $("s2-prod-modelo-video") ? $("s2-prod-modelo-video").value : "Veo 3.1 - Lite";
      const qualidadeImg = $("s2-prod-qualidade-imagem") ? $("s2-prod-qualidade-imagem").value : "x1";
      const qualidadeVid = $("s2-prod-qualidade-video") ? $("s2-prod-qualidade-video").value : "x1";
      const qualidadeDownload = $("s2-prod-qualidade-download") ? $("s2-prod-qualidade-download").value : "1K";
      const proporcao = $("s2-prod-proporcao") ? $("s2-prod-proporcao").value : "16:9";
      try {
        await apiJson(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/config`, {
          prod_modelo_imagem: modeloImg,
          prod_modelo_video: modeloVid,
          prod_qualidade_imagem: qualidadeImg,
          prod_qualidade_video: qualidadeVid,
          prod_qualidade_download: qualidadeDownload,
          prod_proporcao: proporcao,
          // Compatibilidade: mantém os campos legados sincronizados com a config de imagem
          prod_modelo: modeloImg,
          prod_qualidade: qualidadeImg,
        });
        if ($("s2-prod-config-badge")) {
          $("s2-prod-config-badge").textContent = "✓ Salvo";
          $("s2-prod-config-badge").className = "badge badge-ok";
        }
        showToast("✅ Configurações de produção salvas!");
      } catch (e) {
        showToast("❌ Erro ao salvar configurações: " + e.message);
      }
    });
  }

  // Salvar configurações de provedores de IA
  const btnSalvarProvedores = $("btn-s2-salvar-provedores-config");
  if (btnSalvarProvedores) {
    btnSalvarProvedores.addEventListener("click", async () => {
      if (!S.projeto_id) return;
      const provedorStoryboard = $("s2-select-provedor-storyboard") ? $("s2-select-provedor-storyboard").value : "claude";
      const provedorPrompts = $("s2-select-provedor-prompts") ? $("s2-select-provedor-prompts").value : "deepseek";
      try {
        await apiJson(`/api/v2/projeto/${encodeURIComponent(S.projeto_id)}/config`, {
          provedor_storyboard: provedorStoryboard,
          provedor_prompts: provedorPrompts,
        });
        if ($("s2-ai-providers-badge")) {
          $("s2-ai-providers-badge").textContent = "✓ Salvo";
          $("s2-ai-providers-badge").className = "badge badge-ok";
        }
        showToast("✅ Provedores de IA atualizados!");
      } catch (e) {
        showToast("❌ Erro ao salvar provedores: " + e.message);
      }
    });
  }

  // Função global para testar conexão de APIs
  async function testarConexaoAPI(provedor) {
    const badge = document.getElementById(`status-api-${provedor}`);
    if (!badge) return;
    badge.textContent = "Testando...";
    badge.className = "badge badge-wait";

    try {
      const res = await apiJson("/api/v2/config/testar_provedor", { provedor });
      if (res && res.success) {
        if (res.valida) {
          badge.textContent = `Conectada (${res.latency.toFixed(2)}s)`;
          badge.className = "badge badge-ok";
        } else {
          badge.textContent = "Erro";
          badge.className = "badge badge-err";
          showToast(`❌ Falha no teste do ${provedor}: ${res.mensagem}`);
        }
      } else {
        badge.textContent = "Erro";
        badge.className = "badge badge-err";
        showToast(`❌ Erro: ${res.error || "Resposta inválida"}`);
      }
    } catch (e) {
      badge.textContent = "Erro";
      badge.className = "badge badge-err";
      showToast(`❌ Erro de conexão: ${e.message}`);
    }
  }
  window.testarConexaoAPI = testarConexaoAPI;

  // Opção 2: Salvar Avatar Flow (@me) — REMOVIDO (decisão do usuário: não oferecer avatar via QR do próprio rosto)
  // if ($("btn-s2-salvar-avatar-flow")) {
  //   $("btn-s2-salvar-avatar-flow").addEventListener("click", async () => {
  //     const nome = $("s2-input-avatar-nome") ? $("s2-input-avatar-nome").value.trim() : "Meu Avatar";
  //     const refFlow = "@me";
  //     const estilo = $("s2-select-estilo") ? $("s2-select-estilo").value : "photorealistic_cinematic";
  //
  //     const fd = new FormData();
  //     fd.append("tipo", "avatar");
  //     fd.append("nome", nome);
  //     fd.append("referencia_flow", refFlow);
  //     fd.append("estilo_visual", estilo);
  //
  //     try {
  //       const r = await apiForm(`/api/v2/identidade/${encodeURIComponent(S.projeto_id)}/salvar`, fd);
  //       if (r && r.success) {
  //         alert(`✓ Avatar Google Flow (@me) configurado como identidade permanente do projeto!`);
  //         await carregarDadosPersonagemS2(S.projeto_id);
  //         // Regenera os prompts automaticamente com a tag @me
  //         await api(`/api/v2/prompts/${encodeURIComponent(S.projeto_id)}/gerar`, {
  //           method: "POST",
  //           headers: { "Content-Type": "application/json" },
  //           body: JSON.stringify({ nome_personagem: nome, estilo_visual: estilo })
  //         });
  //         if (typeof carregarStoryboardS2 === "function") await carregarStoryboardS2(S.projeto_id);
  //       } else {
  //         alert("Erro ao salvar avatar: " + ((r && r.error) || ""));
  //       }
  //     } catch (e) {
  //       alert("Erro na conexão: " + e.message);
  //     }
  //   });
  // }

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

async function gerarCenaIndividualFlow(scene_id, modo_forcado) {
  if (!S.projeto_id) return;
  try {
    // Detecta se a cena já tem imagem para decidir modo
    const cena = (_montagemCenas || []).find(c => (c.id || c.scene_index) === scene_id)
              || (S.cenas_producao || []).find(c => (c.id || c.scene_index) === scene_id);
    const temImagem = cena && (cena.image_status === "READY" || cena.tem_midia || cena.status === "BAIXADA");
    const modo = temImagem ? "video" : "imagem";
    const modo_final = modo_forcado || modo;
    const res = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/iniciar_fila`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_ids: [scene_id], modo: modo_final })
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
  try {
    let res;
    if (S.projeto_id) {
      res = await api(`/api/v2/producao/${encodeURIComponent(S.projeto_id)}/live_console`);
    } else {
      res = await api(`/api/v2/console/logs_globais`);
    }
    if (!res || !res.success) return;

    const w = res.worker || {};
    const stats = res.stats || {};
    const ca = w.cena_ativa || {};

    // Durante geração ativa, expande o HUD automaticamente se estiver collapsed
    if (w.is_running) {
      const hud = $("live-terminal-hud");
      if (hud && hud.classList.contains("collapsed") && !hud.classList.contains("expanded")) {
        hud.classList.remove("collapsed");
        hud.classList.add("expanded");
        termExpanded = true;
        const btnTermToggle = $("btn-term-toggle");
        if (btnTermToggle) btnTermToggle.textContent = "▼ Recolher Console";
      }
    }

    if (ca && ca.scene_id != null) {
      _ULTIMA_CENA_ATIVA_SCENE_ID = ca.scene_id;
    } else {
      _ULTIMA_CENA_ATIVA_SCENE_ID = null;
    }
    _aplicarDestaqueCenaAtiva();

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

    // Conta Google Ativa
    const accBadge = $("term-account-badge");
    const accEmail = $("term-account-email");
    if (accBadge && accEmail) {
      if (w.account_email) {
        accEmail.textContent = w.account_email;
        accBadge.style.display = "inline-flex";
      } else {
        accBadge.style.display = "none";
      }
    }

    // Projeto Google Flow Ativo
    const projBadge = $("term-project-badge");
    const projName = $("term-project-name");
    if (projBadge && projName) {
      const pNome = w.project_name || S.projeto_id || "";
      if (pNome) {
        projName.textContent = pNome;
        projBadge.style.display = "inline-flex";
        if (w.project_url) projBadge.title = `Projeto Flow: ${w.project_url}`;
      } else {
        projBadge.style.display = "none";
      }
    }

    const cenaTxt = $("term-cena-ativa");
    if (cenaTxt) {
      if (ca.scene_id) {
        cenaTxt.textContent = `Cena #${String(ca.scene_id).padStart(3, '0')} (${ca.scene_idx || '?'}/${ca.total_cenas || stats.total || '?'})`;
      } else {
        cenaTxt.textContent = w.is_running ? "Produzindo fila..." : (S.projeto_id ? "Fila aguardando" : "Lira Studio Pronto");
      }
    }

    const etapaTxt = $("term-etapa-ativa");
    if (etapaTxt) {
      if (w.current_delay !== null && w.current_delay !== undefined && w.current_delay > 0) {
        etapaTxt.textContent = `⏱ Aguardando delay (${w.current_delay}s)...`;
      } else {
        etapaTxt.textContent = ca.etapa ? `⚡ ${ca.etapa}` : (w.is_running ? '⚡ Processando...' : 'Pronto');
      }
    }

    const timerBadge = $("term-timer-badge");
    if (timerBadge) {
      if (w.is_running) {
        const progPct = ca.progresso_pct;
        const tDec = Math.round(ca.tempo_decorrido || 0);
        const tTot = Math.round(ca.tempo_total || 0);
        const tMed = (ca.tempo_medio || 0).toFixed(1);
        if (progPct !== undefined && progPct !== null) {
          timerBadge.textContent = `⏱ ${progPct}% (${tDec}s) | TOTAL: ${fmtDur(tTot)} | MÉDIA: ~${tMed}s`;
        } else {
          timerBadge.textContent = `⏱ CENA: ${tDec}s | TOTAL: ${fmtDur(tTot)} | MÉDIA: ~${tMed}s`;
        }
      } else if (S.projeto_id) {
        const prontos = stats.baixadas || 0;
        const total = stats.total || 0;
        timerBadge.textContent = `⏱ STATUS: ${prontos}/${total} PRONTAS | FILA PRONTA`;
      } else {
        timerBadge.textContent = "⏱ STATUS: 24/7 ONLINE";
      }
    }

    // Renderiza Logs Coloridos
    const logsEl = $("live-terminal-logs");
    if (logsEl && res.logs && res.logs.length) {
      logsEl.innerHTML = res.logs.map(log => {
        let tagCls = "tag-info";
        const cat = (log.category || "").toUpperCase();
        const msg = log.message || "";

        if (log.level === "ERROR" || msg.includes("ERRO") || msg.includes("Falha")) tagCls = "tag-err";
        else if (log.level === "WARN" || msg.includes("AVISO") || msg.includes("Timeout")) tagCls = "tag-warn";
        else if (cat === "TRANSCRIBE" || cat === "AUDIO") tagCls = "tag-cyan";
        else if (cat === "FLOW_CONTAS" || cat === "CONTA") tagCls = "tag-purple";
        else if (cat === "ROTEIRO" || cat === "SCENE_PLAN") tagCls = "tag-warn";
        else if (msg.includes("OK") || msg.includes("SUCESSO") || msg.includes("READY")) tagCls = "tag-ok";

        return `
          <div class="terminal-log-row">
            <span class="terminal-log-ts">[${esc(log.ts)}]</span>
            <span class="terminal-log-tag ${tagCls}">${esc(cat || 'LOG')}</span>
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

// initLiveTerminalHUD() é inicializado no ponto ÚNICO de entrada (init).
// ANTIGRAVITY Passo 2: removido este DOMContentLoaded duplicado.

/* ============================================================
   CAPCUT EXPORT — aba Montagem (adicionado)
   ============================================================ */
async function exportarParaCapCut() {
  const projetoId = window.projetoAtivo || window.currentProject || window.projeto_id || (typeof S !== "undefined" ? S.projeto_id : "");
  if (!projetoId) {
    alert("Nenhum projeto ativo. Abra um projeto antes de exportar.");
    return;
  }

  const statusEl = document.getElementById("capcut-export-status");
  const abrirDiv = document.getElementById("capcut-abrir-pasta");
  const btn = document.getElementById("btn-exportar-capcut");

  btn.disabled = true;
  statusEl.textContent = "⏳ Exportando...";
  abrirDiv.style.display = "none";

  try {
    const resp = await fetch(`/api/v2/projeto/${projetoId}/exportar_capcut`, {
      method: "POST"
    });
    const data = await resp.json();

    if (data.success) {
      statusEl.textContent = data.msg || `✅ ${data.total_cenas} cenas exportadas`;
      abrirDiv.style.display = "block";
      window._capcut_export_dir = data.export_dir;
    } else {
      statusEl.textContent = `❌ Erro: ${data.error}`;
    }
  } catch (e) {
    statusEl.textContent = `❌ Falha na requisição: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function abrirPastaCapCut() {
  const dir = window._capcut_export_dir;
  if (!dir) return;
  try {
    const resp = await fetch(`/api/v2/abrir_pasta`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dir })
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.success) {
      alert("Não foi possível abrir a pasta no servidor: " + (data.error || `HTTP ${resp.status}`));
    }
  } catch (e) {
    alert("Erro ao abrir pasta: " + e.message);
  }
}


/* ============================================================
   REDESIGN F1 — Aba Montagem (funções de apoio: movimento B-Roll,
   automação em massa e orquestração do layout de 3 painéis)
   ============================================================ */
const _MOTION_LABEL = {
  zoom_in: "Zoom In (ênfase/fala)",
  zoom_out: "Zoom Out (abertura)",
  pan_right: "Pan Direita (revelação)",
  pan_left: "Pan Esquerda (contraste)",
  estatico: "Estáticas"
};

async function salvarMovimentoCena(valor) {
  const c = _montagemCenas && _montagemCenas[_montagemCenaAtivaIdx];
  if (!c) return;
  const cid = c.id || c.scene_index;
  c.motion_preset = valor || "";
  try {
    await api(`/api/scene_plan/${encodeURIComponent(S.projeto_id)}/${cid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ motion_preset: valor || "" })
    });
    showToast(valor ? `✅ Movimento definido: ${_MOTION_LABEL[valor] || valor}` : "✅ Movimento voltou para Auto");
  } catch (e) {
    console.warn("Erro ao salvar motion_preset:", e);
    showToast("❌ Não foi possível salvar o movimento da cena.");
  }
}

function montarResumoMovimentoHtml(resumo, total) {
  const partes = ["zoom_in", "pan_right", "zoom_out", "pan_left", "estatico"]
    .filter(k => (resumo[k] || 0) > 0)
    .map(k => `${resumo[k]} ${_MOTION_LABEL[k] || k}`);
  return `🧭 <b>${total} cenas analisadas:</b> ${partes.join(", ")}.`;
}

async function direcionarMovimentosAutomaticamente() {
  if (!S.projeto_id) {
    showToast("❌ Nenhum projeto ativo selecionado.");
    return;
  }
  const box = $("s2-motion-auto-box");
  const resumoEl = $("s2-motion-auto-resumo");
  const btn = $("btn-mov-auto");
  if (box) box.classList.remove("hidden");
  if (resumoEl) resumoEl.textContent = "Analisando cenas (narrativa, fala, duração e sinais do Animation Director)...";
  if (btn) btn.disabled = true;
  try {
    const res = await apiJson(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/direcionar_movimentos`, { acao: "calcular" });
    if (!res || !res.success) {
      if (resumoEl) resumoEl.textContent = "❌ " + (res && res.error ? res.error : "Falha ao calcular movimentos.");
      return;
    }
    if (resumoEl) resumoEl.innerHTML = montarResumoMovimentoHtml(res.resumo || {}, res.total || 0);
  } catch (e) {
    if (resumoEl) resumoEl.textContent = "❌ Erro: " + (e.message || e);
  } finally {
    if (btn) btn.disabled = false;
  }
}



async function aprovarMovimentosAuto() {
  if (!S.projeto_id) return;
  const box = $("s2-motion-auto-box");
  const resumoEl = $("s2-motion-auto-resumo");
  if (resumoEl) resumoEl.textContent = "Aprovando presets de movimento (gravando motion_preset)...";
  try {
    const res = await apiJson(`/api/v2/montagem/${encodeURIComponent(S.projeto_id)}/direcionar_movimentos`, { acao: "aprovar" });
    if (res && res.success) {
      if (box) box.classList.add("hidden");
      if (resumoEl) resumoEl.textContent = "";
      showToast(`✅ motion_preset gravado em ${res.gravadas || 0} cenas.`);
      await atualizarMontagemS2(S.projeto_id);
      if (_montagemCenas && _montagemCenas.length) selecionarCenaMontagem(_montagemCenaAtivaIdx, false);
    } else if (resumoEl) {
      resumoEl.textContent = "❌ " + (res.error || "Falha ao aprovar movimentos.");
    }
  } catch (e) {
    if (resumoEl) resumoEl.textContent = "❌ Erro: " + (e.message || e);
  }
}

function revisarMovimentosAuto() {
  const box = $("s2-motion-auto-box");
  if (box) box.classList.add("hidden");
  const resumoEl = $("s2-motion-auto-resumo");
  if (resumoEl) resumoEl.textContent = "";
  showToast("✏️ Revisão manual: ajuste o dropdown de Movimento no Inspector, cena a cena.");
}

/* ---------- Layout de 3 painéis (Player | Timeline | Inspector fixo) ---------- */
let _layout3paineisAplicado = false;

/* TAREFA 5: preencherTrilhaBgm() passa a DELEGAR ao renderizador real da trilha
   M1 (_renderTrilhaBgmM1), que agora roda a cada renderMontagemTimeline — e nao
   apenas uma vez atras da guarda _layout3paineisAplicado. A funcao e o nome foram
   preservados (callers em orquestrarLayoutMontagem3Paineis), assim como o texto
   estatico anterior, usado como estado inicial. */
function preencherTrilhaBgm() {
  const bgm = document.getElementById("s2-nle-track-bgm");
  if (!bgm) return;
  // Se a timeline ainda nao foi renderizada (track vazia), garante um estado
  // visivel coerente em vez de deixar a trilha M1 em branco.
  if (!bgm.children.length) {
    const volEl = document.getElementById("slider-bgm-volume");
    const num = volEl ? Math.round(parseFloat(volEl.value || "0.14") * 100) : 14;
    bgm.innerHTML = `<div class="nle-bgm-strip">🎵 BGM — volume ${num}% (ducking automático)</div>`;
  }
  // Fonte de verdade: clipe real, dimensionado por _montagemPxPerSec().
  _renderTrilhaBgmM1();
}

/* ============================================================
   TAREFA 8 — Sub-abas da aba 5 MONTAGEM (CSS-first, aditivo)
   O container #s2-tab-montagem recebe [data-sub] e o CSS (style.css:5246+)
   alterna o display APENAS dos paineis de ferramentas:
     legendas -> #card-legendas-capcut | transicoes -> #card-transicoes-lote
     audio    -> #card-trilha-sonora  | cenas       -> #card-banco-cenas
   Player 16:9, Storyboard, Timeline (V1/CC/A1/M1) e Inspector NUNCA sao
   ocultados: _montagemSincronizarPlayhead usa getBoundingClientRect neles.
   Nenhum no e movido ou renomeado (os 102 ids seguem no DOM).
   ============================================================ */
const MONTAGEM_SUB_ABAS = ["legendas", "transicoes", "audio", "cenas"];

function trocarSubAbaMontagem(sub) {
  const alvo = (MONTAGEM_SUB_ABAS.indexOf(sub) >= 0) ? sub : "legendas";
  S.montagemSubAba = alvo;

  const tab = document.getElementById("s2-tab-montagem");
  if (tab) tab.setAttribute("data-sub", alvo);
  document.querySelectorAll("#s2-tab-montagem .montagem-subtab").forEach((b) => {
    b.classList.toggle("active", b.dataset.montagemTab === alvo);
  });

  // A timeline nunca e ocultada, mas a troca de sub-aba altera a altura da coluna:
  // reposiciona o playhead (best-effort) sem tocar no playback.
  if (typeof _montagemAtualizarPosicaoPlayhead === "function") {
    try { _montagemAtualizarPosicaoPlayhead(); } catch (e) { /* best-effort */ }
  }
}

/** Reaplica a sub-aba persistida (chamada ao entrar na aba Montagem). */
function aplicarSubAbaMontagemPersistida() {
  trocarSubAbaMontagem(S.montagemSubAba || "legendas");
}

function orquestrarLayoutMontagem3Paineis() {
  if (_layout3paineisAplicado) return;
  const tab = document.getElementById("s2-tab-montagem");
  if (!tab) return;

  // Lira Studio 2.0: Layout nativo de 3 colunas (.montagem-v2-grid) preservado
  if (tab.querySelector(".montagem-v2-grid")) {
    _layout3paineisAplicado = true;
    preencherTrilhaBgm();
    return;
  }

  const mon = tab.querySelector(".nle-monitor-panel");
  const insp = tab.querySelector(".nle-inspector-panel");
  const tl = tab.querySelector(".nle-timeline-section");
  if (!mon || !insp || !tl) return;

  const host = insp.parentElement;
  const layout = document.createElement("div");
  layout.className = "s2-montagem-layout";
  const left = document.createElement("div");
  left.className = "s2-montagem-left";
  const right = document.createElement("div");
  right.className = "s2-montagem-right";
  layout.appendChild(left);
  layout.appendChild(right);
  if (host && host.parentElement) host.parentElement.insertBefore(layout, host);

  left.appendChild(mon);   // Player/Preview no topo da coluna esquerda
  left.appendChild(tl);    // Timeline logo abaixo do player
  right.appendChild(insp); // Inspector lateral fixo

  // CORREÇÃO CRÍTICA (Frente B): reposiciona para DENTRO do layout os cards que
  // ficaram soltos fora da nova estrutura (Produção B-Roll/Transições em Massa,
  // Trilha Sonora & BGM e seção CapCut legada) — coluna esquerda, abaixo da timeline.
  const btnBroll = document.getElementById("btn-gerar-broll-mp4");
  const gridBroll = btnBroll ? btnBroll.closest(".s2-grid-2col") : null;
  if (gridBroll && tab.contains(gridBroll)) left.appendChild(gridBroll);
  const capcutSec = document.getElementById("capcut-export-section");
  if (capcutSec && tab.contains(capcutSec)) left.appendChild(capcutSec);

  if (host && host.parentElement && !host.children.length) host.remove();
  _layout3paineisAplicado = true;
  preencherTrilhaBgm();
}

/* ============================================================
   CONTAS GOOGLE FLOW — gerenciador simples (modal no topbar)
   Troca manual de conta + auto-reset diário (créditos renovam às 00:00).
   API v2: GET /api/v2/flow/contas | POST /api/v2/flow/trocar/<email>
   ============================================================ */

let _flowContasTimer = null;

// Busca as contas, atualiza o label do botão e renderiza as opções do modal.
async function carregarContasSimples() {
  const label = document.getElementById("conta-ativa-label");
  const lista = document.getElementById("contas-list-modal");
  try {
    const resp = await fetch("/api/v2/flow/contas", { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const json = await resp.json();
    const contas = Array.isArray(json) ? json : (json.contas || []);

    // Label do botão = parte antes do "@" da conta ativa
    const ativa = contas.find(c => c.ativa);
    if (label) {
      label.textContent = ativa
        ? String(ativa.email || ativa.nome || "N/A").split("@")[0]
        : "N/A";
    }

    // Lista do modal
    if (lista) {
      lista.innerHTML = contas.map(c => {
        const email = c.email || "(sem email)";
        const credito = c.creditos_esgotados
          ? "<span style='color:#e74c3c'>(ESGOTADO)</span>"
          : ("Créditos: " + (c.creditos_disponiveis != null ? c.creditos_disponiveis : "-"));
        const bg = c.ativa ? "#e7f3ff" : "#f9f9f9";
        const borda = c.ativa ? "#007bff" : "#ddd";
        const valor = encodeURIComponent(String(c.email || ""));
        return "<div class='flow-conta-card' data-conta='" + valor + "'" +
          " data-ativa='" + (c.ativa ? "1" : "0") + "' style='" +
          "padding:12px;margin:8px 0;border:2px solid " + borda + ";border-radius:6px;" +
          "cursor:pointer;background:" + bg + ";transition:all .2s;'>" +
          "<strong>" + email + "</strong><br>" +
          "<small style='color:#666;'>" + (c.ativa ? "&#10003; ATIVA | " : "") + credito + "</small>" +
          "</div>";
      }).join("");

      // Delegação de clique (evita quebra de escape do email em onclick inline)
      lista.querySelectorAll(".flow-conta-card").forEach(el => {
        el.addEventListener("click", () => trocarContaSimples(decodeURIComponent(el.dataset.conta || "")));
        el.addEventListener("mouseover", () => { el.style.background = "#f0f0f0"; });
        el.addEventListener("mouseout", () => {
          el.style.background = el.dataset.ativa === "1" ? "#e7f3ff" : "#f9f9f9";
        });
      });
    }
  } catch (err) {
    console.warn("Erro ao carregar contas do Flow:", err);
    if (label) label.textContent = "Erro";
  }
}

function abrirPainelContas() {
  const modal = document.getElementById("modal-contas");
  const overlay = document.getElementById("overlay-contas");
  if (modal) modal.style.display = "block";
  if (overlay) overlay.style.display = "block";
  carregarContasSimples();
}

function fecharPainelContas() {
  const modal = document.getElementById("modal-contas");
  const overlay = document.getElementById("overlay-contas");
  if (modal) modal.style.display = "none";
  if (overlay) overlay.style.display = "none";
}

async function trocarContaSimples(email) {
  if (!email) return;
  try {
    const resp = await fetch("/api/v2/flow/trocar/" + encodeURIComponent(email), {
      method: "POST",
      cache: "no-store",
    });
    const result = await resp.json().catch(() => ({}));
    console.log("Conta trocada para:", result.conta_ativa || email);
    await carregarContasSimples();
    setTimeout(fecharPainelContas, 500);
  } catch (err) {
    console.error("Erro ao trocar conta:", err);
    alert("Erro ao trocar conta. Veja o console.");
  }
}

// Boot de contas Flow movido a init() — el listener DOMContentLoaded separado se
// eliminó para que haya UNA sola inicialización (ver init()).

