const CHAR_DELAY_MS = 50;

// ─────────────────────────────────────────────
// Update banner
// ─────────────────────────────────────────────
const LATEST_VERSION = "1.5.3"; // ← bump this each release

const promptsEl        = document.getElementById("prompts");
const waitMinEl        = document.getElementById("waitMin");
const waitMaxEl        = document.getElementById("waitMax");
const statusEl         = document.getElementById("status");
const appEl            = document.getElementById("app");
const versionTagEl     = document.getElementById("versionTag");
const txtUploadEl      = document.getElementById("txtUpload");
const runBtn           = document.getElementById("run");
const continueBtn      = document.getElementById("continueBtn");
const retryBtn         = document.getElementById("retryBtn");
const videoBtn         = document.getElementById("videoBtn");
const animField        = document.getElementById("animField");
const animPromptsEl    = document.getElementById("animPrompts");
const stopBtn          = document.getElementById("stop");
const connBar          = document.getElementById("connBar");
const connDot          = document.getElementById("connDot");
const connMsg          = document.getElementById("connMsg");
const connLink         = document.getElementById("connLink");
const agentBar         = document.getElementById("agentBar");
const agentMsg         = document.getElementById("agentMsg");
const agentBadge       = document.getElementById("agentBadge");
const helpDialog       = document.getElementById("helpDialog");
const helpOpen         = document.getElementById("helpOpen");
const helpClose        = document.getElementById("helpClose");
const helpBodyEl       = document.getElementById("helpBody");
const listSection      = document.getElementById("listSection");
const promptListEl     = document.getElementById("promptList");
const listCountEl      = document.getElementById("listCount");
const downloadFolderEl = document.getElementById("downloadFolder");
const serialToggleEl   = document.getElementById("serialToggle");
const langSelectEl     = document.getElementById("langSelect");
const openDlSettings   = document.getElementById("openDlSettings");

// ─────────────────────────────────────────────
// Version from manifest
const { version } = chrome.runtime.getManifest();
if (versionTagEl) versionTagEl.textContent = `v${version}`;

// Show update banner if installed version is behind LATEST_VERSION
// Only shown once — dismissed state saved in storage per version
(function checkUpdateBanner() {
  const updateBar     = document.getElementById("updateBar");
  const updateDismiss = document.getElementById("updateDismiss");
  if (!updateBar) return;

  function versionIsOlder(installed, latest) {
    const a = installed.split(".").map(Number);
    const b = latest.split(".").map(Number);
    for (let i = 0; i < 3; i++) {
      if ((a[i] || 0) < (b[i] || 0)) return true;
      if ((a[i] || 0) > (b[i] || 0)) return false;
    }
    return false;
  }

  if (!versionIsOlder(version, LATEST_VERSION)) return; // already up to date

  chrome.storage.local.get("eltonUpdateDismissed", (r) => {
    if (r.eltonUpdateDismissed === LATEST_VERSION) return; // already dismissed for this version
    updateBar.style.display = "";
  });

  updateDismiss.addEventListener("click", () => {
    updateBar.style.display = "none";
    chrome.storage.local.set({ eltonUpdateDismissed: LATEST_VERSION });
  });
})();

// ─────────────────────────────────────────────
// TXT file upload
// ─────────────────────────────────────────────
if (txtUploadEl) {
  txtUploadEl.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result || "";
      promptsEl.value = text.trim();
      rebuildList();
      persist();
    };
    reader.readAsText(file);
    txtUploadEl.value = ""; // reset so same file can be re-uploaded
  });
}

// ─────────────────────────────────────────────
// i18n strings
// ─────────────────────────────────────────────
const LANGS = {
  pt: {
    promptQueueLabel:  "Fila de prompts",
    promptQueueHint:   "Um prompt por linha · a lista atualiza enquanto você digita",
    queueTitle:        "Fila",
    promptCount:       (n) => `${n} prompt${n === 1 ? "" : "s"}`,
    delayFrom:         "Atraso aleatório de",
    delayTo:           "a",
    delaySec:          "seg",
    downloadFolderLabel: "Pasta de download",
    autoDownload:      "Salvar auto",
    checkPage:         "Verificar página",
    runQueue:          "Iniciar geração",
    stop:              "Parar",
    statusPending:     "Pendente",
    statusGenerating:  "Gerando…",
    statusDone:        "Pronto ✓",
    statusFailed:      "Erro ✗",
    statusStopped:     "Ignorado",
    helpTitle:         "Como começar",
    helpClose:         "Entendido",
    helpDelayTitle:    "Atraso aleatório",
    helpDelayBody:     "Após cada geração, ELTON FLOW aguarda um tempo aleatório dentro do intervalo configurado antes de iniciar o próximo prompt.",
    helpBody: `
      <p class="lede">① Acesse seu projeto do Google Flow:<br/>
        <a class="path-link" href="https://labs.google/fx/tools/flow/project/" target="_blank">
          https://labs.google/fx/tools/flow/project/
        </a>
      </p>
      <p class="lede">② Digite seus prompts abaixo — um por linha. A fila atualiza em tempo real.</p>
      <p class="lede">③ Clique em <strong>Iniciar geração</strong>. ELTON FLOW gera cada imagem e faz o download automaticamente.</p>`,
    placeholder: "Uma bicicleta vermelha numa rua chuvosa\nUma raposa em aquarela na floresta\nBeco neon ao entardecer, cinematográfico",
    msgStarting:    (n)   => `Iniciando ${n} prompt(s)…`,
    msgSubmitting:  (i,n) => `Enviando prompt ${i} de ${n}…`,
    msgWaiting:     (i)   => `Prompt ${i} enviado — aguardando geração…`,
    msgDownloading: (i)   => `Prompt ${i} pronto ✓ — baixando imagem…`,
    msgDone:        (i)   => `Próximo em ${i}s…`,
    msgAllDone:     (n)   => `✓ ${n} prompt(s) gerados.`,
    msgPartDone:    (c,n) => `Pronto: ${c} de ${n} gerados.`,
    msgStopped:     "Parado.",
    msgChecking:    "Verificando…",
    msgConnected:   (h)   => `Conectado: ${h}`,
    msgNoTab:       "Nenhuma aba ativa.",
    msgNotReady:    "Script não pronto. Abra uma aba do projeto Flow e atualize.",
    msgTimeout:     (i)   => `Prompt ${i} expirou.`,
    msgError:       (i,e) => `Erro no prompt ${i}: ${e}`,
    msgAddPrompt:   "Adicione pelo menos um prompt.",
    msgOpenProject: "Abra uma página de projeto Flow primeiro (URL deve conter /project/).",
  },
  en: {
    promptQueueLabel:  "Prompt queue",
    promptQueueHint:   "One prompt per line · list updates as you type",
    queueTitle:        "Queue",
    promptCount:       (n) => `${n} prompt${n === 1 ? "" : "s"}`,
    delayFrom:         "Random delay from",
    delayTo:           "To",
    delaySec:          "sec",
    downloadFolderLabel: "Download folder",
    autoDownload:      "Auto-save",
    checkPage:         "Check page",
    runQueue:          "Start Generation",
    stop:              "Stop",
    statusPending:     "Pending",
    statusGenerating:  "Generating…",
    statusDone:        "Done ✓",
    statusFailed:      "Failed ✗",
    statusStopped:     "Skipped",
    helpTitle:         "Getting Started",
    helpClose:         "Got it",
    helpDelayTitle:    "Random delay",
    helpDelayBody:     "After each generation, ELTON FLOW waits a random number of seconds within your set range before starting the next prompt.",
    helpBody: `
      <p class="lede">① Go to your Google Flow project:<br/>
        <a class="path-link" href="https://labs.google/fx/tools/flow/project/" target="_blank">
          https://labs.google/fx/tools/flow/project/
        </a>
      </p>
      <p class="lede">② Enter your prompts below — one per line. Your queue updates as you type.</p>
      <p class="lede">③ Click <strong>Start Generation</strong>. ELTON FLOW generates each image one at a time and downloads it automatically.</p>`,
    placeholder: "A red bicycle on a rainy street\nA watercolor fox in a forest\nNeon alley at dusk, cinematic",
    msgStarting:    (n)   => `Starting ${n} prompt(s)…`,
    msgSubmitting:  (i,n) => `Submitting prompt ${i} of ${n}…`,
    msgWaiting:     (i)   => `Prompt ${i} submitted — waiting for generation…`,
    msgDownloading: (i)   => `Prompt ${i} done ✓ — downloading image…`,
    msgDone:        (i)   => `Next prompt in ${i}s…`,
    msgAllDone:     (n)   => `✓ All ${n} prompt(s) generated.`,
    msgPartDone:    (c,n) => `Done: ${c} of ${n} generated.`,
    msgStopped:     "Stopped.",
    msgChecking:    "Checking…",
    msgConnected:   (h)   => `Connected: ${h}`,
    msgNoTab:       "No active tab.",
    msgNotReady:    "Content script not ready. Open a Flow project tab and refresh.",
    msgTimeout:     (i)   => `Prompt ${i} timed out.`,
    msgError:       (i,e) => `Error on prompt ${i}: ${e}`,
    msgAddPrompt:   "Add at least one prompt.",
    msgOpenProject: "Please open a Flow project page first (URL must contain /project/).",
  },
  es: {
    promptQueueLabel:  "Cola de prompts",
    promptQueueHint:   "Un prompt por línea · la lista se actualiza al escribir",
    queueTitle:        "Cola",
    promptCount:       (n) => `${n} prompt${n === 1 ? "" : "s"}`,
    delayFrom:         "Demora aleatoria de",
    delayTo:           "a",
    delaySec:          "seg",
    downloadFolderLabel: "Carpeta de descarga",
    autoDownload:      "Guardado auto",
    checkPage:         "Verificar página",
    runQueue:          "Ejecutar cola",
    stop:              "Detener",
    statusPending:     "Pendiente",
    statusGenerating:  "Generando…",
    statusDone:        "Listo ✓",
    statusFailed:      "Error ✗",
    statusStopped:     "Omitido",
    helpTitle:         "Cómo empezar",
    helpClose:         "Entendido",
    helpDelayTitle:    "Demora aleatoria",
    helpDelayBody:     "Tras cada generación, ELTON FLOW espera un tiempo aleatorio dentro del rango configurado antes de iniciar el siguiente prompt.",
    helpBody: `
      <p class="lede">① Ve a tu proyecto de Google Flow:<br/>
        <a class="path-link" href="https://labs.google/fx/tools/flow/project/" target="_blank">
          https://labs.google/fx/tools/flow/project/
        </a>
      </p>
      <p class="lede">② Escribe tus prompts abajo — uno por línea. La cola se actualiza al instante.</p>
      <p class="lede">③ Haz clic en <strong>Ejecutar cola</strong>. ELTON FLOW genera cada imagen una a una y la descarga automáticamente.</p>`,
    placeholder: "Una bicicleta roja en una calle lluviosa\nUn zorro en acuarela en el bosque\nCallejón neón al atardecer",
    msgStarting:    (n)   => `Iniciando ${n} prompt(s)…`,
    msgSubmitting:  (i,n) => `Enviando prompt ${i} de ${n}…`,
    msgWaiting:     (i)   => `Prompt ${i} enviado — esperando generación…`,
    msgDownloading: (i)   => `Prompt ${i} listo ✓ — descargando…`,
    msgDone:        (i)   => `Siguiente en ${i}s…`,
    msgAllDone:     (n)   => `✓ ${n} prompt(s) generados.`,
    msgPartDone:    (c,n) => `Hecho: ${c} de ${n}.`,
    msgStopped:     "Detenido.",
    msgChecking:    "Verificando…",
    msgConnected:   (h)   => `Conectado: ${h}`,
    msgNoTab:       "No hay pestaña activa.",
    msgNotReady:    "Script no listo. Abre la página del proyecto Flow y recarga.",
    msgTimeout:     (i)   => `Prompt ${i} superó el tiempo límite.`,
    msgError:       (i,e) => `Error en prompt ${i}: ${e}`,
    msgAddPrompt:   "Añade al menos un prompt.",
    msgOpenProject: "Abre primero una página de proyecto Flow (URL debe contener /project/).",
  },
};

// ─────────────────────────────────────────────
// Language / i18n
// ─────────────────────────────────────────────
let currentLang = "pt";

function t(key, ...args) {
  const s = LANGS[currentLang]?.[key] ?? LANGS.pt[key];
  return typeof s === "function" ? s(...args) : (s ?? key);
}

function applyLanguage() {
  const L = LANGS[currentLang] || LANGS.pt;

  // Static data-i18n elements
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    const val = L[key];
    if (val && typeof val === "string") el.textContent = val;
  });

  // Help body (HTML)
  if (helpBodyEl) helpBodyEl.innerHTML = L.helpBody || LANGS.pt.helpBody;

  // Rebuild list count label
  rebuildList();
}

langSelectEl.addEventListener("change", () => {
  currentLang = langSelectEl.value;
  applyLanguage();
  chrome.storage.local.set({ eltonLang: currentLang });
});

// ─────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function randomWait(minMs, maxMs) {
  return minMs + Math.floor(Math.random() * Math.max(1, maxMs - minMs + 1));
}

function setStatus(text) { statusEl.textContent = text; }

let flowTabId     = null; // locked-in tab ID during generation
let resumeFromIdx = null; // index to resume from after a stop/failure

function setInputsDisabled(disabled) {
  promptsEl.disabled        = disabled;
  serialToggleEl.disabled   = disabled;
  downloadFolderEl.disabled = disabled;
  openDlSettings.disabled   = disabled;
  waitMinEl.disabled        = disabled;
  waitMaxEl.disabled        = disabled;
  txtUploadEl.disabled      = disabled;
  if (animPromptsEl) animPromptsEl.disabled = disabled;
  const opacity = disabled ? "0.45" : "";
  [promptsEl, serialToggleEl, downloadFolderEl, openDlSettings, waitMinEl, waitMaxEl, txtUploadEl, animPromptsEl]
    .filter(Boolean)
    .forEach(el => el.style.opacity = opacity);
  // Also fade the label button visually
  const uploadLabel = document.querySelector(".btn-txt-upload");
  if (uploadLabel) uploadLabel.style.opacity = opacity;
}

// Keep-alive do PRÓPRIO painel: quando a janela é minimizada o painel também
// congela, e é ele que conduz a fila. Um áudio inaudível mantém o painel vivo.
// O clique do usuário no botão é o "gesto" que autoriza o áudio.
let panelAudio = null;
function panelKeepAlive(on) {
  try {
    if (on) {
      if (panelAudio) { panelAudio.ctx.resume?.(); return; }
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0.0001;
      osc.frequency.value = 30;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      panelAudio = { ctx, osc };
      ctx.resume?.();
    } else if (panelAudio) {
      try { panelAudio.osc.stop(); } catch {}
      try { panelAudio.ctx.close(); } catch {}
      panelAudio = null;
    }
  } catch { /* ignore */ }
}

// Liga/desliga o estado visual de "rodando" E os keep-alives (painel + aba do
// Flow) + o anti-pausa. Isso mantém a geração e o download rodando mesmo com a
// janela minimizada ou em outra aba. Fire-and-forget, sem retries.
function setRunningUI(on) {
  appEl.classList.toggle("is-running", on);
  panelKeepAlive(on);
  sendToFlowTab({ type: "FLOW_KEEPALIVE", on }, { retries: 0 }).catch(() => {});
}

function showContinue(fromIndex) {
  resumeFromIdx = fromIndex;
  continueBtn.style.display = "";
  continueBtn.textContent   = `▶ Continue from #${fromIndex + 1}`;
  runBtn.disabled = true; // keep Start Generation disabled
}

function hideContinue() {
  resumeFromIdx = null;
  continueBtn.style.display = "none";
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

// Acha a aba do Flow MESMO que ela não seja a aba ativa: primeiro tenta a
// ativa; senão procura em todas as janelas (a host permission de
// labs.google/fx/* nos deixa ver a URL dessas abas). Assim trocar de aba
// não "desconecta" — o painel continua falando com a aba do Flow ao fundo.
async function getFlowTab() {
  const active = await getActiveTab();
  if (active?.url && FLOW_BASE_RE.test(active.url)) return active;
  try {
    const tabs = await chrome.tabs.query({ url: "https://labs.google/fx/*" });
    return tabs.find(t => FLOW_PROJECT_RE.test(t.url || "")) || tabs[0] || null;
  } catch {
    return null;
  }
}

async function sendToFlowTab(message, { retries = 5, retryDelayMs = 3000 } = {}) {
  // During generation use the saved tab ID so switching tabs doesn't break it
  const tabId = flowTabId || (await getFlowTab())?.id;
  if (!tabId) { setStatus(t("msgNoTab")); return null; }

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await chrome.tabs.sendMessage(tabId, message);
    } catch {
      if (attempt === retries) {
        setStatus(t("msgNotReady"));
        return null;
      }
      // Content script likely reloading — wait and retry
      setStatus(`Page reloading… retrying in ${Math.round(retryDelayMs / 1000)}s (${attempt + 1}/${retries})`);
      await sleep(retryDelayMs);
    }
  }
}

// ─────────────────────────────────────────────
// Help dialog
// ─────────────────────────────────────────────
if (helpOpen  && helpDialog?.showModal) helpOpen.addEventListener("click",  () => helpDialog.showModal());
if (helpClose && helpDialog?.close)     helpClose.addEventListener("click", () => helpDialog.close());

// ─────────────────────────────────────────────
// Auto-download settings link
// ─────────────────────────────────────────────
openDlSettings?.addEventListener("click", () => {
  chrome.tabs.create({ url: "chrome://settings/downloads" });
});

// ─────────────────────────────────────────────
// html escape
// ─────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ─────────────────────────────────────────────
// Prompt list
// ─────────────────────────────────────────────
let promptStatuses = [];
let isRunning = false;
// Imagens geradas com sucesso, indexadas pelo índice do prompt:
// generatedMedia[i] = { baseName, imageUrl, promptText }. Alimenta a fase de vídeo.
let generatedMedia = [];

// Lira Studio SSE Bridge Globals
let bridgeFilaParada = false;
let bridgeQueue = [];
let bridgeSeenNames = new Set();
let bridgeSeedDone = false;

function statusLabel(s) {
  return t("status" + s.charAt(0).toUpperCase() + s.slice(1));
}

function parsePrompts() {
  const raw = (promptsEl.value || "").trim();
  if (!raw) return [];
  // Se o texto contiver blocos separados por linhas em branco (duplo Enter)
  if (/\r?\n\s*\r?\n/.test(raw)) {
    return raw.split(/\r?\n\s*\r?\n/).map(b => b.trim()).filter(b => b.length > 0);
  }
  return raw.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
}

function rebuildList() {
  const lines = parsePrompts();
  if (!lines.length) { listSection.style.display = "none"; return; }

  const prev = promptStatuses;
  promptStatuses = lines.map((text, i) => ({
    text,
    status: (prev[i] && prev[i].text === text) ? prev[i].status : "pending",
  }));

  renderList();
  listSection.style.display = "";
  const n = lines.length;
  listCountEl.textContent = t("promptCount", n);
}

function renderList() {
  promptListEl.innerHTML = "";
  promptStatuses.forEach((item, i) => {
    const row = document.createElement("div");
    row.className = "prompt-item" + statusClass(item.status);
    row.id = `pr-${i}`;
    row.innerHTML =
      `<span class="prompt-num">${i + 1}</span>` +
      `<span class="prompt-text" title="${escHtml(item.text)}">${escHtml(item.text)}</span>` +
      `<span class="prompt-status s-${item.status}">${statusLabel(item.status)}</span>`;
    promptListEl.appendChild(row);
  });
}

function statusClass(s) {
  return s === "generating" ? " is-running"
       : s === "done"       ? " is-done"
       : s === "failed"     ? " is-failed"
       : s === "stopped"    ? " is-stopped" : "";
}

function updateStatus(index, status) {
  if (index < 0 || index >= promptStatuses.length) return;
  promptStatuses[index].status = status;
  const row = document.getElementById(`pr-${index}`);
  if (!row) return;
  row.className = "prompt-item" + statusClass(status);
  const badge = row.querySelector(".prompt-status");
  if (badge) { badge.className = `prompt-status s-${status}`; badge.textContent = statusLabel(status); }
  if (status === "generating") row.scrollIntoView({ block: "nearest" });
}

promptsEl.addEventListener("input", () => {
  if (!isRunning) {
    rebuildList();
    hideContinue();        // clear resume state when prompts change
    hideRetry();           // índices de erro ficam inválidos se os prompts mudam
    hideVideoButton();     // as imagens guardadas não batem mais com os prompts
    runBtn.disabled = !connBar.classList.contains("is-connected") || agentModeOn;
  }
});

// ─────────────────────────────────────────────
// Countdown
// ─────────────────────────────────────────────
async function countdown(seconds) {
  for (let s = seconds; s > 0 && isRunning; s--) {
    setStatus(t("msgDone", s));
    await sleep(1000);
  }
}

// ─────────────────────────────────────────────
// Download
// ─────────────────────────────────────────────
function safePromptName(text) {
  return text
    .slice(0, 30)
    .replace(/:/g, "-")            // ALL colons → dash (: not allowed in filenames)
    .replace(/\s+/g, "_")
    .replace(/[^\w_\[\]\-]/g, "")  // keep [ ] - along with word chars
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, 25);
}

// Nome-base do arquivo (sem pasta, sem extensão) — igual pra imagem e vídeo,
// pra o vídeo sair com o MESMO nome da imagem (o montador casa pelo [MM-SS]).
function computeBaseName(serialNum, promptText) {
  const name      = safePromptName(promptText);
  const serial    = String(serialNum).padStart(2, "0");
  const useSerial = serialToggleEl.checked;
  return useSerial ? `${serial}_${name}` : name;
}

function downloadFolder() {
  const val = (downloadFolderEl.value || "elton-img").trim();
  if (val.endsWith("/") || val.endsWith("\\")) {
    return val.slice(0, -1);
  }
  return val;
}

// Baixa a mídia PELO content script (contexto same-origin da aba do Flow) e
// deixa o service worker só salvar o binário. Necessário porque o Flow passou
// a exigir Referer/Sec-Fetch same-origin no endpoint de mídia — baixar direto
// pelo service worker retornava HTML de erro (arquivo virava .htm).
async function baixarMidiaViaAba(url, filename) {
  if (!url.startsWith("http")) url = "https://labs.google" + url;
  const tabId = flowTabId || (await getFlowTab())?.id;
  if (!tabId) { console.warn("[ELTON FLOW] Sem aba do Flow pra baixar a mídia"); return false; }
  try {
    const res = await chrome.runtime.sendMessage({ type: "ELTON_DOWNLOAD_MEDIA", url, filename, tabId });
    if (!res?.ok) console.warn("[ELTON FLOW] Download falhou:", res?.error || "sem resposta");
    return !!res?.ok;
  } catch (e) { console.warn("[ELTON FLOW] Download falhou:", e); return false; }
}

async function downloadGeneratedImages(newUrls, serialNum, promptText) {
  const folder   = downloadFolder();
  const baseName = computeBaseName(serialNum, promptText);

  for (let j = 0; j < newUrls.length; j++) {
    const suffix   = newUrls.length > 1 ? `_${j + 1}` : "";
    const filename = `${folder}/${baseName}${suffix}.jpg`;
    await baixarMidiaViaAba(newUrls[j], filename);
  }
}

// Baixa o vídeo gerado com o MESMO nome-base da imagem, em <pasta>/videos/.
async function downloadGeneratedVideo(url, baseName) {
  const filename = `${downloadFolder()}/videos/${baseName}.mp4`;
  return baixarMidiaViaAba(url, filename);
}

// ─────────────────────────────────────────────
// Motor API (v1.4) — espera a próxima mídia consultando o backend do Flow
// via content script (FLOW_API_LIST_MEDIA), sem depender do DOM. Funciona
// com a aba oculta, janela minimizada, qualquer zoom e sem rolar a tela.
// O prompt exato devolvido pela API casa cada mídia com o prompt certo.
// O painel conduz o relógio (ele fica ativo mesmo com a aba do Flow em
// segundo plano); o content script só executa a consulta ao receber a
// mensagem — handlers de mensagem não sofrem o throttling de timers.
// ─────────────────────────────────────────────
async function waitForNewMediaApi({ kind, seenNames, promptText, beforeFailCount, timeoutMs, isBridge = false }) {
  const deadline = Date.now() + timeoutMs;
  const want = String(promptText || "").trim();
  const activeCheck = () => isBridge ? !bridgeFilaParada : isRunning;

  while (activeCheck() && Date.now() < deadline) {
    const res = await sendToFlowTab({ type: "FLOW_API_LIST_MEDIA" }, { retries: 0 });
    if (res?.ok) {
      const fresh = (res.media || []).filter(m => m.kind === kind && !seenNames.has(m.name));
      if (fresh.length > 0) {
        fresh.sort((a, b) => String(a.createTime).localeCompare(String(b.createTime)));
        // Preferir a mídia gerada exatamente por ESTE prompt; senão a mais
        // antiga das novas. Todas viram "vistas" para não vazar ao próximo.
        const pick = fresh.find(m => m.prompt.trim() === want) || fresh[0];
        fresh.forEach(m => seenNames.add(m.name));
        return { done: true, newUrls: [pick.url] };
      }
    }
    // Card de erro ("Retry") visível no DOM — melhor esforço; se a aba
    // estiver oculta o timeout cobre o caso.
    const tiles = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT", mediaKind: kind }, { retries: 0 });
    if (tiles && (tiles.failCount ?? 0) > beforeFailCount) return { failed: true };
    await sleep(4000);
  }
  return activeCheck() ? { timeout: true } : { stopped: true };
}

// ─────────────────────────────────────────────
// Auto connection check
// ─────────────────────────────────────────────
const FLOW_BASE    = "https://labs.google/fx/tools/flow";
const FLOW_PROJECT = "https://labs.google/fx/tools/flow/project/";
// Regex to match localized URLs like /fx/fr/tools/flow/... or /fx/zh-TW/tools/flow/... or /fx/tools/flow/...
const FLOW_PROJECT_RE = /labs\.google\/fx(?:\/[a-z]{2,}(?:-[a-zA-Z]{2,})?)?\/tools\/flow\/project\//;
const FLOW_BASE_RE    = /labs\.google\/fx(?:\/[a-z]{2,}(?:-[a-zA-Z]{2,})?)?\/tools\/flow/;

let agentModeOn = false;

async function checkAgentMode() {
  const res = await sendToFlowTab({ type: "FLOW_GET_AGENT_MODE" });
  if (!res?.found) { agentBar.style.display = "none"; return; }

  agentModeOn = res.isOn;
  agentBar.style.display = "";

  if (res.isOn) {
    agentBar.className = "agent-bar is-on";
    agentMsg.textContent = "Turn off Agent mode to start generation";
    agentBadge.textContent = "ON";
    agentBadge.className = "agent-badge is-on";
    if (!isRunning) runBtn.disabled = true;
  } else {
    agentBar.className = "agent-bar is-off";
    agentMsg.textContent = "Agent mode";
    agentBadge.textContent = "OFF";
    agentBadge.className = "agent-badge is-off";
    if (!isRunning) runBtn.disabled = false;
  }
}

function setConnected(inBackground) {
  connBar.className = "conn-bar is-connected";
  connMsg.textContent = inBackground
    ? "Conectado ao Flow (aba em segundo plano)"
    : "Connected to Google Flow project";
  connLink.style.display = "none";
  if (!isRunning) runBtn.disabled = false;
  checkAgentMode();
}

function setOpenProject() {
  connBar.className = "conn-bar is-warn";
  connMsg.textContent = "Open a project to continue";
  connLink.style.display = "none";
  agentBar.style.display = "none";
  if (!isRunning) runBtn.disabled = true;
}

function setGoToFlow() {
  connBar.className = "conn-bar is-off";
  connMsg.textContent = "Not on Google Flow";
  connLink.style.display = "";
  connLink.textContent = "Go to Google Flow →";
  connLink.href = FLOW_BASE;
  agentBar.style.display = "none";
  if (!isRunning) runBtn.disabled = true;
}

async function checkConnection() {
  // Durante a geração a fila usa o flowTabId travado — trocar de aba NÃO
  // desconecta. Não deixa a UI assustar o usuário com "Not on Google Flow".
  if (isRunning) return;
  // Considera a aba do Flow mesmo em segundo plano: estar no Gmail/YouTube
  // com o projeto Flow aberto em outra aba continua "conectado".
  const tab = await getFlowTab();
  const url = tab?.url || "";
  if (FLOW_PROJECT_RE.test(url)) {
    const active = await getActiveTab();
    setConnected(tab.id !== active?.id);
  } else if (FLOW_BASE_RE.test(url)) {
    // On labs.google/fx/.../tools/flow but no project open
    setOpenProject();
  } else {
    // No Flow tab anywhere
    setGoToFlow();
  }
}

// Check on load
checkConnection();

// Poll agent mode every 3 seconds when connected
setInterval(() => { if (connBar.classList.contains("is-connected")) checkAgentMode(); }, 3000);


// Re-check whenever the active tab URL changes
chrome.tabs.onActivated.addListener(() => checkConnection());
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url || changeInfo.status === "complete") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]?.id === tabId) checkConnection();
    });
  }
});

// ─────────────────────────────────────────────
// Run queue
// ─────────────────────────────────────────────
async function startQueue(startIndex = 0, onlyIndices = null) {
  const lines = parsePrompts();
  if (!lines.length) { setStatus(t("msgAddPrompt")); return; }

  let waitMinMs = Math.round(Number(waitMinEl.value) * 1000) || 0;
  let waitMaxMs = Math.round(Number(waitMaxEl.value) * 1000) || 0;
  if (waitMaxMs < waitMinMs) [waitMinMs, waitMaxMs] = [waitMaxMs, waitMinMs];

  // Trava o ID da aba do Flow (mesmo que ela esteja em segundo plano)
  const flowTab = await getFlowTab();
  if (!flowTab?.id) { setStatus(t("msgOpenProject")); return; }
  flowTabId = flowTab.id;

  // Lista de índices a processar:
  //  - modo normal: range contíguo de startIndex até o fim
  //  - modo "regerar erros": só os índices passados em onlyIndices
  const indices = onlyIndices
    ? onlyIndices.filter(j => j >= 0 && j < lines.length)
    : Array.from({ length: lines.length - startIndex }, (_, k) => startIndex + k);

  // On fresh start reset all statuses; on continue/retry keep existing,
  // só os índices que vamos processar voltam para "pending".
  if (!onlyIndices && startIndex === 0) {
    promptStatuses = lines.map(text => ({ text, status: "pending" }));
    generatedMedia = []; // fila nova de imagens → zera o que alimenta os vídeos
  } else {
    for (const j of indices) {
      if (promptStatuses[j]) promptStatuses[j].status = "pending";
    }
  }
  renderList();
  listSection.style.display = "";
  hideRetry();

  isRunning = true;
  runBtn.disabled = true;
  hideContinue();
  setInputsDisabled(true);
  setRunningUI(true);
  let completed = 0;
  setStatus(t("msgStarting", lines.length));

  // Conjunto GLOBAL de mídias já vistas/baixadas. Começa com o que já existe
  // no projeto, para o primeiro prompt não capturar nada antigo. Cada prompt
  // adiciona o que gerou — uma mídia nunca é atribuída a dois prompts.
  // Motor preferido: API do Flow (v1.4). Se ela falhar (mudança no Google),
  // cai para o motor antigo via DOM.
  const seenSrcs  = new Set(); // motor DOM (fallback)
  const seenNames = new Set(); // motor API
  let engine = "dom";
  const apiSeed = await sendToFlowTab({ type: "FLOW_API_LIST_MEDIA" });
  if (apiSeed?.ok) {
    engine = "api";
    (apiSeed.media || []).forEach(m => seenNames.add(m.name));
  } else {
    console.warn("[ELTON FLOW] Motor API indisponível, usando DOM:", apiSeed?.error);
    const initialTiles = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT" });
    (initialTiles?.srcs || []).forEach(s => seenSrcs.add(s));
  }

  for (let pos = 0; pos < indices.length; pos++) {
    const i = indices[pos];
    if (!isRunning) {
      for (let q = pos; q < indices.length; q++) updateStatus(indices[q], "stopped");
      showContinue(i); // offer to resume from this point
      break;
    }

    updateStatus(i, "generating");
    let promptDone = false;

    for (let attempt = 0; attempt <= 1; attempt++) {
      if (!isRunning) break;

      if (attempt === 1) {
        // Countdown before retry
        for (let s = 3; s > 0 && isRunning; s--) {
          setStatus(`Prompt ${i + 1} failed — retrying in ${s}s…`);
          await sleep(1000);
        }
        if (!isRunning) break;
        updateStatus(i, "generating");
        setStatus(`Prompt ${i + 1} — retrying…`);
      } else {
        setStatus(t("msgSubmitting", i + 1, lines.length));
      }

      const countRes        = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT" });
      const beforeFailCount = countRes?.failCount  ?? 0;

      const submitRes = await sendToFlowTab({
        type: "FLOW_BATCH_RUN",
        prompts: [lines[i]],
        waitMinMs: 0, waitMaxMs: 0,
        charDelayMs: CHAR_DELAY_MS, refImage: null,
      });

      if (!isRunning) break;

      if (!submitRes || submitRes.error) {
        if (attempt < 1) continue; // retry
        updateStatus(i, "failed");
        setStatus(t("msgError", i + 1, submitRes?.error || "no response"));
        for (let q = pos + 1; q < indices.length; q++) updateStatus(indices[q], "stopped");
        showContinue(i); // offer to retry from this failed prompt
        isRunning = false;
        break;
      }

      setStatus(t("msgWaiting", i + 1));
      // Passa o conjunto GLOBAL de vistas — a espera só considera imagens que
      // NENHUM prompt anterior já capturou.
      const genRes = engine === "api"
        ? await waitForNewMediaApi({
            kind: "image",
            seenNames,
            promptText: lines[i],
            beforeFailCount,
            timeoutMs: 180000,
          })
        : await sendToFlowTab({
            type: "FLOW_WAIT_GENERATION",
            seenSrcs: [...seenSrcs],
            beforeFailCount,
            timeoutMs: 180000,
          });

      if (!isRunning) break;

      if (genRes?.failed || genRes?.timeout) {
        if (attempt < 1) continue; // will show countdown at top of loop
        // Both attempts failed — mark failed, move to next prompt
        updateStatus(i, "failed");
        const why = genRes?.reason ? ` — Flow: "${genRes.reason}"`
                  : genRes?.timeout ? " (tempo esgotado)" : "";
        setStatus(`Prompt ${i + 1} falhou${why}. Seguindo para o próximo.`);
        promptDone = true; // continue queue
        break;
      }

      // Sucesso. genRes.newUrls = todas as imagens novas estáveis deste prompt.
      // Marca TODAS como vistas (pra nenhuma vazar para o próximo prompt) e
      // baixa só a PRIMEIRA — uma imagem por prompt, sempre a deste prompt.
      const newUrls = genRes?.newUrls || [];
      newUrls.forEach(u => seenSrcs.add(u));
      if (newUrls.length > 0) {
        setStatus(t("msgDownloading", i + 1));
        await downloadGeneratedImages([newUrls[0]], i + 1, lines[i]);
        // Guarda pra fase de vídeo: mesmo índice → mesmo nome-base. O retry
        // sobrescreve a mesma posição, então nunca duplica.
        generatedMedia[i] = {
          baseName: computeBaseName(i + 1, lines[i]),
          imageUrl: newUrls[0],
          promptText: lines[i],
        };
      }

      updateStatus(i, "done");
      completed++;
      promptDone = true;
      break; // no retry needed
    }

    // promptDone = true means either success or skip-after-fail — both continue queue
    // promptDone = false only if isRunning was set to false (submit error path)

    if (pos < indices.length - 1 && isRunning) {
      const pause = randomWait(waitMinMs, waitMaxMs);
      await countdown(Math.round(pause / 1000));
    }
  }

  isRunning = false;
  flowTabId = null;
  setInputsDisabled(false);
  setRunningUI(false);

  const failedCount  = promptStatuses.filter(p => p.status === "failed").length;
  const stoppedCount = promptStatuses.filter(p => p.status === "stopped").length;

  if (stoppedCount > 0) {
    // Fila pausada pelo usuário — oferece Continuar, não faz auto-retry
    if (failedCount > 0 || stoppedCount > 0) showRetry(failedCount + stoppedCount);
    const firstStopped = promptStatuses.findIndex(p => p.status === "stopped");
    showContinue(firstStopped);
    runBtn.disabled = true;
    setStatus(`Parado — ${completed} prontas, ${stoppedCount} puladas.`);
    return;
  }

  hideContinue();
  runBtn.disabled = false;

  if (failedCount === 0) {
    hideRetry();
    setStatus(t("msgAllDone", completed));
    maybeShowVideoButton();
    return;
  }

  // Tem erros — auto-retry UMA vez se esta não for já uma passagem de retry
  const failedIndices = promptStatuses
    .map((p, i) => (p.status === "failed" ? i : -1))
    .filter(i => i >= 0);

  if (!onlyIndices) {
    // Primeira passagem: faz contagem regressiva e reprocessa os erros automaticamente
    showRetry(failedCount);
    const WAIT_S = 5;
    let skipCountdown = false;

    // Clique no botão durante a contagem: pula os segundos restantes e inicia já
    const onRetryClick = () => { skipCountdown = true; };
    autoRetryCountdownActive = true;
    if (retryBtn) retryBtn.addEventListener("click", onRetryClick, { once: true });

    for (let s = WAIT_S; s > 0; s--) {
      if (skipCountdown) break;
      setStatus(`${failedCount} com erro — regerando automaticamente em ${s}s… (ou clique no botão)`);
      await sleep(1000);
    }

    if (retryBtn) retryBtn.removeEventListener("click", onRetryClick);
    autoRetryCountdownActive = false;

    hideRetry();
    await startQueue(0, failedIndices);
  } else {
    // Segunda passagem (já é um retry): se ainda tem erros, mostra botão e para
    showRetry(failedCount);
    setStatus(`Pronto — ${completed} geradas, ${failedCount} ainda com erro. Clique "Regerar com erro".`);
    maybeShowVideoButton(); // as que deram certo já podem virar vídeo
  }
}

runBtn.addEventListener("click", () => startQueue(0));

// ─────────────────────────────────────────────
// Regerar com erro — reprocessa só os prompts "failed" ou "stopped"
// ─────────────────────────────────────────────
function showRetry(n) {
  if (!retryBtn) return;
  retryBtn.style.display = "";
  retryBtn.textContent = `↻ Regerar com erro (${n})`;
}
function hideRetry() {
  if (retryBtn) retryBtn.style.display = "none";
}
// Durante o auto-retry countdown, o clique no botão apenas cancela a contagem
// (skipCountdown=true) sem disparar um segundo startQueue.
let autoRetryCountdownActive = false;

if (retryBtn) {
  retryBtn.addEventListener("click", () => {
    if (autoRetryCountdownActive) return; // a contagem já vai iniciar o startQueue
    const idx = promptStatuses
      .map((p, i) => ((p.status === "failed" || p.status === "stopped") ? i : -1))
      .filter(i => i >= 0);
    if (idx.length) startQueue(0, idx);
  });
}

// ─────────────────────────────────────────────
// Fase de VÍDEO — anima cada imagem gerada (mesmo nome, em /videos)
// ─────────────────────────────────────────────
function videoItems() {
  return generatedMedia
    .map((m, i) => (m && m.imageUrl ? { ...m, srcIndex: i } : null))
    .filter(Boolean);
}

// Linhas da caixa de animação, SEM filtrar vazias (preserva o casamento por
// índice: linha N → N-ésima imagem da fila). Cada linha já vem trimada.
function animLines() {
  return (animPromptsEl?.value || "").split("\n").map(s => s.trim());
}

// Prompt de animação efetivo para a posição `pos` (índice em videoItems()).
// Se a linha estiver vazia, reusa o prompt que gerou a imagem.
function animPromptFor(pos, item) {
  const a = (animLines()[pos] || "").trim();
  return a || item.promptText;
}

function maybeShowVideoButton() {
  if (!videoBtn) return;
  const items = videoItems();
  const n = items.length;
  if (n > 0) {
    videoBtn.style.display = "";
    videoBtn.textContent = `🎬 Gerar vídeos das imagens (${n})`;
    if (animField) {
      animField.style.display = "";
      // Pré-preenche com os prompts das imagens (uma linha por imagem, na
      // ordem da fila) só se o usuário ainda não digitou nada — assim o
      // padrão é "reusar", e cada linha pode ser reescrita como movimento.
      if (animPromptsEl && !animPromptsEl.value.trim()) {
        animPromptsEl.value = items.map(it => it.promptText).join("\n");
      }
    }
  } else {
    videoBtn.style.display = "none";
    if (animField) animField.style.display = "none";
  }
}
function hideVideoButton() {
  if (videoBtn) videoBtn.style.display = "none";
  if (animField) animField.style.display = "none";
}

if (videoBtn) videoBtn.addEventListener("click", () => startVideoQueue());

async function startVideoQueue(onlyPositions = null) {
  const items = videoItems();
  if (!items.length) { setStatus("Nenhuma imagem gerada para virar vídeo."); return; }

  // Trava a aba do Flow (mesmo que ela esteja em segundo plano)
  const flowTab = await getFlowTab();
  if (!flowTab?.id) { setStatus(t("msgOpenProject")); return; }
  flowTabId = flowTab.id;

  // Confirma que o Flow está no modo VÍDEO (senão geraria imagem de novo)
  const modeRes = await sendToFlowTab({ type: "FLOW_GET_OUTPUT_MODE" });
  if (modeRes?.mode === "image") {
    setStatus("⚠ No Flow, mude a saída de Imagem para VÍDEO e clique de novo.");
    return;
  }

  // Lista de posições a processar (todas, ou só as que falharam num retry)
  const positions = onlyPositions
    ? onlyPositions.filter(j => j >= 0 && j < items.length)
    : items.map((_, k) => k);

  // Estado visual: reusa a lista de prompts mostrando cada vídeo
  if (!onlyPositions) {
    promptStatuses = items.map((it, k) => ({ text: "🎬 " + animPromptFor(k, it), status: "pending" }));
  } else {
    for (const j of positions) if (promptStatuses[j]) promptStatuses[j].status = "pending";
  }
  renderList();
  listSection.style.display = "";
  hideVideoButton(); hideRetry(); hideContinue();

  let waitMinMs = Math.round(Number(waitMinEl.value) * 1000) || 0;
  let waitMaxMs = Math.round(Number(waitMaxEl.value) * 1000) || 0;
  if (waitMaxMs < waitMinMs) [waitMinMs, waitMaxMs] = [waitMaxMs, waitMinMs];

  isRunning = true;
  runBtn.disabled = true;
  setInputsDisabled(true);
  setRunningUI(true);
  let completed = 0;
  setStatus(`Iniciando ${positions.length} vídeo(s)…`);

  // Conjunto global de vídeos já vistos/baixados (evita atribuir o vídeo de um
  // prompt anterior ao atual — mesma proteção do embaralhamento das imagens).
  // Motor preferido: API do Flow (v1.4); DOM como fallback.
  const seenVideos = new Set(); // motor DOM (fallback)
  const seenNames  = new Set(); // motor API (todas as mídias do projeto)
  let engine = "dom";
  const apiSeed = await sendToFlowTab({ type: "FLOW_API_LIST_MEDIA" });
  if (apiSeed?.ok) {
    engine = "api";
    (apiSeed.media || []).forEach(m => seenNames.add(m.name));
  } else {
    console.warn("[ELTON FLOW] Motor API indisponível, usando DOM:", apiSeed?.error);
    const initial = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT", mediaKind: "video" });
    (initial?.srcs || []).forEach(s => seenVideos.add(s));
  }

  for (let p = 0; p < positions.length; p++) {
    const pos = positions[p];
    const item = items[pos];
    if (!isRunning) { for (let q = p; q < positions.length; q++) updateStatus(positions[q], "stopped"); break; }

    updateStatus(pos, "generating");
    let done = false;

    for (let attempt = 0; attempt <= 1; attempt++) {
      if (!isRunning) break;
      if (attempt === 1) {
        for (let s = 3; s > 0 && isRunning; s--) { setStatus(`Vídeo ${pos + 1} falhou — tentando de novo em ${s}s…`); await sleep(1000); }
        if (!isRunning) break;
        updateStatus(pos, "generating");
      }
      setStatus(`Enviando vídeo ${pos + 1} de ${positions.length}…`);

      const countRes = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT", mediaKind: "video" });
      const beforeFailCount = countRes?.failCount ?? 0;

      // Manda a imagem (por URL) + o prompt de animação desta posição (a linha
      // correspondente da caixa 🎬, ou o prompt da imagem se a linha for vazia).
      const submitRes = await sendToFlowTab({
        type: "FLOW_BATCH_RUN",
        prompts: [animPromptFor(pos, item)],
        refImageUrl: item.imageUrl,
        videoMode: true,
        waitMinMs: 0, waitMaxMs: 0,
        charDelayMs: CHAR_DELAY_MS,
      });
      if (!isRunning) break;
      if (!submitRes || submitRes.error) {
        if (attempt < 1) continue;
        updateStatus(pos, "failed");
        setStatus(`Erro no vídeo ${pos + 1}: ${submitRes?.error || "sem resposta"}`);
        done = true; break;
      }

      setStatus(`Vídeo ${pos + 1} enviado — aguardando geração… (pode levar minutos)`);
      const genRes = engine === "api"
        ? await waitForNewMediaApi({
            kind: "video",
            seenNames,
            promptText: animPromptFor(pos, item),
            beforeFailCount,
            timeoutMs: 300000,
          })
        : await sendToFlowTab({
            type: "FLOW_WAIT_GENERATION",
            mediaKind: "video",
            seenSrcs: [...seenVideos],
            beforeFailCount,
          });
      if (!isRunning) break;

      if (genRes?.failed || genRes?.timeout) {
        if (attempt < 1) continue;
        updateStatus(pos, "failed");
        const why = genRes?.reason ? ` — Flow: "${genRes.reason}"` : genRes?.timeout ? " (tempo esgotado)" : "";
        setStatus(`Vídeo ${pos + 1} falhou${why}. Seguindo.`);
        done = true; break;
      }

      const newUrls = genRes?.newUrls || [];
      newUrls.forEach(u => seenVideos.add(u));
      if (newUrls.length > 0) {
        setStatus(`Vídeo ${pos + 1} pronto ✓ — baixando…`);
        await downloadGeneratedVideo(newUrls[0], item.baseName);
      }
      updateStatus(pos, "done");
      completed++; done = true; break;
    }

    if (p < positions.length - 1 && isRunning) {
      const pause = randomWait(waitMinMs, waitMaxMs);
      await countdown(Math.round(pause / 1000));
    }
  }

  isRunning = false;
  flowTabId = null;
  setInputsDisabled(false);
  setRunningUI(false);
  runBtn.disabled = false;

  const failed = promptStatuses.filter(p => p.status === "failed").length;
  if (failed === 0) { setStatus(`✓ ${completed} vídeo(s) gerados em /videos.`); return; }

  // Auto-retry dos vídeos que falharam (uma passagem automática)
  const failedPositions = promptStatuses.map((s, i) => (s.status === "failed" ? i : -1)).filter(i => i >= 0);
  if (!onlyPositions) {
    setStatus(`${failed} vídeo(s) com erro — regerando automaticamente em 5s…`);
    for (let s = 5; s > 0; s--) { setStatus(`${failed} vídeo(s) com erro — regerando em ${s}s…`); await sleep(1000); }
    await startVideoQueue(failedPositions);
  } else {
    setStatus(`Pronto — ${completed} vídeo(s) ok, ${failed} ainda com erro.`);
    maybeShowVideoButton();
  }
}

// ─────────────────────────────────────────────
// Continue
// ─────────────────────────────────────────────
continueBtn.addEventListener("click", () => {
  if (resumeFromIdx !== null) startQueue(resumeFromIdx);
});

// ─────────────────────────────────────────────
// Stop
// ─────────────────────────────────────────────
stopBtn.addEventListener("click", async () => {
  isRunning = false;
  flowTabId = null;
  await sendToFlowTab({ type: "FLOW_BATCH_STOP" });
  setStatus(t("msgStopped"));
  setInputsDisabled(false);
  setRunningUI(false);
  // runBtn stays disabled — Continue button is shown by the loop
});

// ─────────────────────────────────────────────
// Persist / restore
// ─────────────────────────────────────────────
// On launch — restore only settings (delay, folder, language)
// Prompts always start fresh every session
// Piso seguro de atraso (v1.4): abaixo disso o Flow dispara "atividade
// incomum" e bloqueia. Valores salvos de versões antigas (ex.: 5–15s) são
// elevados uma única vez para o mínimo seguro.
const SAFE_MIN_S = 20;
const SAFE_MAX_S = 40;

chrome.storage.local.get(
  ["flowBatchWaitMin", "flowBatchWaitMax", "eltonLang", "eltonSerial", "flowBatchFolder", "eltonDelayMigrated"],
  (r) => {
    let min = r.flowBatchWaitMin != null ? Number(r.flowBatchWaitMin) : SAFE_MIN_S;
    let max = r.flowBatchWaitMax != null ? Number(r.flowBatchWaitMax) : SAFE_MAX_S;
    // Migração única: sobe intervalos curtos herdados para o piso seguro.
    if (!r.eltonDelayMigrated) {
      if (min < SAFE_MIN_S) min = SAFE_MIN_S;
      if (max < SAFE_MAX_S) max = SAFE_MAX_S;
      chrome.storage.local.set({
        eltonDelayMigrated: true,
        flowBatchWaitMin: min,
        flowBatchWaitMax: max,
      });
    }
    waitMinEl.value = String(min);
    waitMaxEl.value = String(max);
    downloadFolderEl.value = r.flowBatchFolder || "elton-img"; // restore saved folder or default
    if (r.eltonSerial != null) serialToggleEl.checked = r.eltonSerial;
    if (r.eltonLang) {
      currentLang = r.eltonLang;
      langSelectEl.value = currentLang;
    }
    applyLanguage();
  }
);

// Persist only settings, never prompts
function persist() {
  chrome.storage.local.set({
    flowBatchWaitMin: waitMinEl.value,
    flowBatchWaitMax: waitMaxEl.value,
    flowBatchFolder:  downloadFolderEl.value,
    eltonSerial:      serialToggleEl.checked,
  });
}
waitMinEl.addEventListener("change", persist);
waitMaxEl.addEventListener("change", persist);
downloadFolderEl.addEventListener("change", persist);
serialToggleEl.addEventListener("change", persist);

/* ═══════════════════════════════════════════════════════════════════════
   LIRA STUDIO — SSE Bridge
   ─────────────────────────────────────────────────────────────────────
   Conecta ao Lira Studio (Flask em localhost:5000) via EventSource.
   Recebe jobs LIRA_FLOW_JOB, executa via sendToFlowTab/FLOW_BATCH_RUN
   e posta o resultado de volta para /api/flow/job-result.

   Não requer alteração no manifest.json — o side panel (não o service
   worker) pode acessar localhost sem declaração de host permission.
   ═══════════════════════════════════════════════════════════════════════ */

(function liraBridge() {
  const LIRA_BASE   = "http://localhost:5000";
  const EVENTS_URL  = LIRA_BASE + "/api/flow/events";
  const RESULT_URL  = LIRA_BASE + "/api/flow/job-result";
  const PROG_URL    = LIRA_BASE + "/api/flow/job-progress";
  const SELLOG_URL  = LIRA_BASE + "/api/flow/selector-log";
  const RETRY_MS    = 5000;

  let es           = null;
  let _jobRunning  = false;

  function liraLog(msg) {
    console.log("[LIRA BRIDGE]", msg);
  }

  // Self-contained main-world injector function to fetch media same-origin and convert to base64
  async function fetchMediaAsDataUrl(url, isVideo) {
    const tentativas = isVideo ? 12 : 5;
    let espera = 4000, ultimo = "";
    for (let t = 1; t <= tentativas; t++) {
      try {
        const resp = await fetch(url, { credentials: "same-origin" });
        if (resp.ok) {
          const blob = await resp.blob();
          if (/text\/html/i.test(blob.type)) { ultimo = "Flow retornou HTML"; }
          else if (blob.size === 0) { ultimo = "0 bytes"; }
          else {
            const dataUrl = await new Promise((res, rej) => {
              const fr = new FileReader();
              fr.onload = () => res(fr.result);
              fr.onerror = () => rej(fr.error || new Error("FileReader falhou"));
              fr.readAsDataURL(blob);
            });
            return { ok: true, dataUrl, sizeKB: Math.round(blob.size / 1024), type: blob.type, tentativas: t };
          }
        } else {
          ultimo = "HTTP " + resp.status;
        }
      } catch (e) { ultimo = String(e && e.message || e); }
      if (t < tentativas) {
        await new Promise(r => setTimeout(r, espera));
        espera = Math.min(espera * 1.4, 12000);
      }
    }
    return { ok: false, error: `mídia não servível após ${tentativas} tentativas (${ultimo})` };
  }

  function postResult(payload) {
    fetch(RESULT_URL, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    }).catch(e => liraLog("job-result POST falhou: " + e.message));
  }

  function postProgress(payload) {
    fetch(PROG_URL, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    }).catch(e => liraLog("job-progress POST falhou: " + e.message));
  }

  function updateBridgeUI() {
    try {
      if (promptsEl && bridgeQueue.length > 0 && (!promptsEl.value || promptsEl.value.includes("A red bicycle"))) {
        promptsEl.value = bridgeQueue.map(j => (j.prompts && j.prompts[0]) || "").filter(Boolean).join("\n\n");
        if (typeof rebuildList === "function") rebuildList();
      }
    } catch {}
  }

  function processQueue() {
    if (_jobRunning || bridgeFilaParada) return;
    if (bridgeQueue.length === 0) {
      setStatus("Lira Studio: Fila pronta. Aguardando novas cenas.");
      return;
    }
    const job = bridgeQueue.shift();
    runJob(job).catch(e => {
      liraLog("runJob exceção não capturada: " + e.message);
      _jobRunning = false;
      processQueue();
    });
  }

  async function runJob(job) {
    _jobRunning = true;
    liraLog("iniciando job " + job.jobId + " (cena " + job.sceneId + ")");
    setStatus("Lira Studio: Iniciando Cena " + job.sceneId + " no Google Flow...");

    postProgress({
      projetoId: job.projetoId,
      sceneId:   job.sceneId,
      status:    "GERANDO",
      mensagem:  `Iniciando Cena ${job.sceneId} no Flow...`,
    });

    try {
      const tab = await getFlowTab();
      const tabId = tab?.id;
      if (!tabId) {
        const err = "Sem aba do Google Flow aberta. Abra o Flow no Chrome.";
        liraLog(err);
        setStatus("Lira Studio: " + err);
        postProgress({
          projetoId: job.projetoId,
          sceneId:   job.sceneId,
          status:    "ERRO",
          mensagem:  err,
          error:     err,
        });
        postResult({
          jobId:     job.jobId,
          sceneId:   job.sceneId,
          projetoId: job.projetoId,
          result:    { error: err },
        });
        return;
      }

      if (!FLOW_BASE_RE.test(tab?.url || "")) {
        const err = "Abra o Google Flow (labs.google/fx/tools/flow) no Chrome para iniciar a geração.";
        liraLog(err);
        setStatus("Lira Studio: " + err);
        postProgress({
          projetoId: job.projetoId,
          sceneId:   job.sceneId,
          status:    "ERRO",
          mensagem:  err,
          error:     err,
        });
        postResult({
          jobId:     job.jobId,
          sceneId:   job.sceneId,
          projetoId: job.projetoId,
          result:    { error: err },
        });
        return;
      }

      if (!bridgeSeedDone) {
        const apiSeed = await sendToFlowTab({ type: "FLOW_API_LIST_MEDIA" });
        if (apiSeed?.ok) {
          (apiSeed.media || []).forEach(m => bridgeSeenNames.add(m.name));
          bridgeSeedDone = true;
          liraLog("Fila SSE do Lira: motor API semeado com " + bridgeSeenNames.size + " mídias existentes.");
        }
      }

      const countRes        = await sendToFlowTab({ type: "FLOW_GET_TILE_COUNT" });
      const beforeFailCount = countRes?.failCount  ?? 0;

      const isVideo = job.videoMode === true;
      const mediaKind = isVideo ? "video" : "image";
      const promptText = job.prompts[0];

      liraLog("Submetendo prompt ao Flow: " + promptText);
      setStatus("Lira Studio: Colando prompt da Cena " + job.sceneId + " no Flow...");

      const res = await sendToFlowTab({
        type:         "FLOW_BATCH_RUN",
        prompts:      job.prompts      || [],
        refImage:     job.refImageBase64 || null,
        refImageUrl:  job.refImageUrl    || null,
        videoMode:    job.videoMode      || false,
        waitMinMs:    0,
        waitMaxMs:    0,
        charDelayMs:  CHAR_DELAY_MS,
      });

      if (!res || res.error) {
        const err = res?.error || "Falha ao enviar o prompt para o editor do Flow";
        liraLog("Envio do prompt falhou: " + err);
        setStatus("Lira Studio: Cena " + job.sceneId + " falhou (" + err + ")");
        postProgress({
          projetoId: job.projetoId,
          sceneId:   job.sceneId,
          status:    "ERRO",
          mensagem:  err,
          error:     err,
        });
        postResult({
          jobId:     job.jobId,
          sceneId:   job.sceneId,
          projetoId: job.projetoId,
          result:    { error: err },
        });
        return;
      }

      liraLog("Aguardando geração da mídia...");
      setStatus("Lira Studio: Cena " + job.sceneId + " gerando no Flow (aguardando renderização)...");
      postProgress({
        projetoId: job.projetoId,
        sceneId:   job.sceneId,
        status:    "GERANDO",
        mensagem:  `Cena ${job.sceneId} gerando no Google Flow...`,
      });

      const genRes = await waitForNewMediaApi({
        kind: mediaKind,
        seenNames: bridgeSeenNames,
        promptText: promptText,
        beforeFailCount,
        timeoutMs: isVideo ? 300000 : 180000,
        isBridge: true,
      });

      if (genRes?.failed || genRes?.timeout) {
        const why = genRes?.reason ? `Flow: "${genRes.reason}"`
                  : genRes?.timeout ? "Tempo limite de renderização esgotado" : "Geração cancelada ou falhou";
        liraLog("Geração falhou: " + why);
        setStatus("Lira Studio: Cena " + job.sceneId + " — " + why);
        postProgress({
          projetoId: job.projetoId,
          sceneId:   job.sceneId,
          status:    "ERRO",
          mensagem:  why,
          error:     why,
        });
        postResult({
          jobId:     job.jobId,
          sceneId:   job.sceneId,
          projetoId: job.projetoId,
          result:    { error: why },
        });
        return;
      }

      const mediaUrl = genRes.newUrls[0];
      liraLog("Mídia pronta em: " + mediaUrl + ". Convertendo para Base64...");
      setStatus("Lira Studio: Cena " + job.sceneId + " pronta! Baixando mídia...");
      postProgress({
        projetoId: job.projetoId,
        sceneId:   job.sceneId,
        status:    "GERANDO",
        mensagem:  `Cena ${job.sceneId} pronta! Baixando mídia...`,
      });

      let dataUrlResult;
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId },
          world: "MAIN",
          func: fetchMediaAsDataUrl,
          args: [mediaUrl, isVideo],
        });
        dataUrlResult = results?.[0]?.result;
      } catch (e) {
        liraLog("Erro ao executar script de fetch: " + e.message);
        dataUrlResult = { ok: false, error: e.message };
      }

      if (!dataUrlResult || !dataUrlResult.ok) {
        const err = dataUrlResult?.error || "Erro ao converter mídia gerada";
        liraLog("Falha ao converter mídia para base64: " + err);
        setStatus("Lira Studio: Falha no download da Cena " + job.sceneId);
        postProgress({
          projetoId: job.projetoId,
          sceneId:   job.sceneId,
          status:    "ERRO",
          mensagem:  err,
          error:     err,
        });
        postResult({
          jobId:     job.jobId,
          sceneId:   job.sceneId,
          projetoId: job.projetoId,
          result:    { error: err },
        });
        return;
      }

      liraLog("Conversão concluída (" + dataUrlResult.sizeKB + " KB). Enviando para o Lira Studio...");
      setStatus("Lira Studio: Cena " + job.sceneId + " salva com sucesso (" + dataUrlResult.sizeKB + " KB)!");

      postResult({
        jobId:     job.jobId,
        sceneId:   job.sceneId,
        projetoId: job.projetoId,
        result:    {
          files: [
            {
              dataUrl: dataUrlResult.dataUrl,
              name: job.jobId + (isVideo ? ".mp4" : ".jpg"),
            }
          ],
          videoMode: isVideo,
        },
      });

      liraLog("job " + job.jobId + " concluído com sucesso e enviado para o Lira Studio.");
      chrome.runtime.sendMessage({ type: "LIRA_CLOSE_TAB" }, (resClose) => {
        if (resClose && resClose.closed === false && resClose.tabs > 1) {
          fetch(SELLOG_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ type: "LIRA_SELECTOR_LOG", message: "Fila concluída — pode fechar a aba do Flow (múltiplas abas abertas)" })
          }).catch(() => {});
        }
      });
    } catch (e) {
      liraLog("job " + job.jobId + " erro: " + e.message);
      setStatus("Lira Studio: Erro na Cena " + job.sceneId + ": " + e.message);
      postProgress({
        projetoId: job.projetoId,
        sceneId:   job.sceneId,
        status:    "ERRO",
        mensagem:  e.message,
        error:     e.message,
      });
      postResult({
        jobId:     job.jobId,
        sceneId:   job.sceneId,
        projetoId: job.projetoId,
        result:    { error: String(e?.message || e) },
      });
    } finally {
      _jobRunning = false;
      processQueue();
    }
  }

  function connect() {
    try {
      es = new EventSource(EVENTS_URL);

      es.onopen = () => {
        liraLog("conectado ao Lira Studio em " + EVENTS_URL);
        setStatus("Lira Studio: Conectado ao Lira Studio.");
      };

      es.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (!msg) return;
        if (msg.type === "LIRA_PING") {
          return;
        }
        if (msg.type === "LIRA_FLOW_STOP") {
          bridgeFilaParada = true;
          liraLog("Fila SSE pausada pelo Lira Studio.");
          setStatus("Lira Studio: Fila pausada.");
          return;
        }
        if (msg.type === "LIRA_FLOW_RESUME") {
          bridgeFilaParada = false;
          liraLog("Fila SSE retomada pelo Lira Studio.");
          setStatus("Lira Studio: Fila retomada.");
          processQueue();
          return;
        }
        if (msg.type === "LIRA_FLOW_CLEAR") {
          bridgeQueue.length = 0;
          bridgeFilaParada = false;
          liraLog("Fila SSE limpa pelo Lira Studio.");
          setStatus("Lira Studio: Fila limpa.");
          return;
        }
        if (msg.type === "LIRA_FLOW_JOB") {
          // Evita duplicar job se já estiver na fila
          const exists = bridgeQueue.some(j => j.jobId === msg.jobId || (j.sceneId === msg.sceneId && j.projetoId === msg.projetoId));
          if (!exists) {
            bridgeQueue.push(msg);
            updateBridgeUI();
            setStatus(`Lira Studio: ${bridgeQueue.length} cena(s) na fila de produção.`);
            processQueue();
          }
        }
      };

      es.onerror = () => {
        liraLog("SSE desconectado — reconectando em " + (RETRY_MS / 1000) + "s");
        es.close();
        es = null;
        setTimeout(connect, RETRY_MS);
      };
    } catch (e) {
      liraLog("EventSource falhou: " + e.message + " — tentando em " + (RETRY_MS / 1000) + "s");
      setTimeout(connect, RETRY_MS);
    }
  }

  // Retransmite LIRA_SELECTOR_LOG do content script (via service-worker) para o Lira
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type !== "LIRA_SELECTOR_LOG") return;
    fetch(SELLOG_URL, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(msg),
    }).catch(() => {});
  });

  // Inicia a conexão
  connect();
  liraLog("bridge inicializada — aguardando conexão com " + LIRA_BASE);
})();
