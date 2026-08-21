chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((e) => {
    console.error("[ELTON FLOW] setPanelBehavior falhou:", e);
  });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((e) => {
    console.error("[ELTON FLOW] setPanelBehavior falhou:", e);
  });
});

chrome.action.onClicked.addListener((tab) => {
  if (tab?.windowId) {
    chrome.sidePanel.open({ windowId: tab.windowId }).catch((e) => {
      console.error("[ELTON FLOW] Falha ao abrir o painel:", e);
    });
  }
});

/* ── Forçar o nome do arquivo no download das imagens ─────────────────
 * O src da imagem gerada no Flow é um endpoint de redirect
 * (…/media.getMediaUrlRedirect?name=UUID) cujo Content-Disposition impõe
 * um nome UUID e ATROPELA o filename passado em downloads.download().
 * onDeterminingFilename é a palavra final do Chrome sobre o nome — sempre
 * vence o Content-Disposition. Guardamos o nome desejado por URL e o
 * aplicamos aqui. Sem isso, o arquivo sai como 941b6f11-….jpg e perde o
 * timestamp usado pra sincronizar com o áudio.
 * ---------------------------------------------------------------- */
// Nossos downloads são sequenciais (o painel aguarda cada um). Guardamos os
// nomes esperados em FILA e forçamos cada um aqui. Isso é obrigatório com
// data: URL: o Chrome não deriva um nome sozinho (sairia "download.jpg") e o
// timestamp [MM-SS] se perderia. A fila não depende de casar a URL (data URLs
// gigantes não casam de forma confiável).
const pendingDownloadNames = []; // FIFO de filenames desejados

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (pendingDownloadNames.length === 0) return false; // não é nosso
  const want = pendingDownloadNames.shift();
  suggest({ filename: want, conflictAction: "uniquify" });
  return true;
});

/* ── MAIN world React fiber submit ───────────────────────────────────
 * chrome.scripting.executeScript with world:"MAIN" bypasses the page's
 * CSP and runs in the same JS context as React / Slate.
 *
 * The content script marks the target button with a data attribute,
 * then asks us to find and invoke the real submission handler from
 * the React fiber tree.
 * ---------------------------------------------------------------- */

/**
 * Injected into the page's MAIN world via chrome.scripting.executeScript.
 * Must be fully self-contained — no closures over service-worker scope.
 */
function reactFiberSubmit(token, markerAttr) {
  var el = document.querySelector("[" + markerAttr + '="' + token + '"]');
  if (!el) return { ok: false, reason: "element not found in MAIN world" };

  // Find React fiber key
  var fiberKey = Object.keys(el).find(function (k) {
    return (
      k.startsWith("__reactFiber$") ||
      k.startsWith("__reactInternalInstance$")
    );
  });
  if (!fiberKey) return { ok: false, reason: "no React fiber on element" };

  // Walk the fiber tree and collect ALL handlers we might call
  var fiber = el[fiberKey];
  var depth = 0;
  var onSubmit = null;
  var onSubmitDepth = -1;
  var onClick = null;
  var onClickDepth = -1;

  while (fiber && depth < 30) {
    var props = fiber.memoizedProps;
    if (props) {
      // Prefer onSubmit — this is the real submission handler
      if (!onSubmit && typeof props.onSubmit === "function") {
        onSubmit = props.onSubmit;
        onSubmitDepth = depth;
      }
      // Also collect onClick as fallback
      if (!onClick && typeof props.onClick === "function") {
        onClick = props.onClick;
        onClickDepth = depth;
      }
    }
    fiber = fiber.return;
    depth++;
  }

  // Also check __reactProps$ for direct handlers
  var propsKey = Object.keys(el).find(function (k) {
    return k.startsWith("__reactProps$");
  });
  if (propsKey) {
    var directProps = el[propsKey];
    if (!onClick && typeof directProps.onClick === "function") {
      onClick = directProps.onClick;
      onClickDepth = -1; // direct props
    }
  }

  // Strategy 1: Call onSubmit directly (bypasses all click/isTrusted checks)
  // The argument is the isTrusted flag — onClick passes event.nativeEvent.isTrusted
  // to onSubmit, which uses it in a boolean guard: (!isLoading || arg).
  if (onSubmit) {
    try {
      onSubmit(true);
      return { ok: true, method: "onSubmit", depth: onSubmitDepth };
    } catch (e) {
      // If onSubmit() with no args fails, it might need an event — continue to fallbacks
    }
  }

  // Strategy 2: Call onClick with isTrusted: true in the fake event
  if (onClick) {
    try {
      var rect = el.getBoundingClientRect();
      onClick({
        type: "click",
        target: el,
        currentTarget: el,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
        button: 0,
        isTrusted: true,
        preventDefault: function () {},
        stopPropagation: function () {},
        isPropagationStopped: function () { return false; },
        isDefaultPrevented: function () { return false; },
        nativeEvent: { type: "click", isTrusted: true },
      });
      return { ok: true, method: "onClick", depth: onClickDepth };
    } catch (e) {
      return {
        ok: false,
        reason: "onClick threw: " + e.message,
        hadOnSubmit: !!onSubmit,
      };
    }
  }

  return {
    ok: false,
    reason: "neither onSubmit nor onClick found in fiber tree",
  };
}

/* ── Anti-pausa (awake shim) ─────────────────────────────────────────
 * Injetado no MAIN world. Faz a página SEMPRE achar que está visível, pra o
 * Flow não pausar a geração/render quando você minimiza a janela ou troca de
 * aba. Sobrescreve document.hidden/visibilityState e engole os eventos de
 * visibilitychange/blur/pagehide/freeze antes que o app do Flow os receba.
 * É idempotente (só instala uma vez) e some ao recarregar a aba.
 * ---------------------------------------------------------------- */
function awakeShim() {
  if (window.__eltonAwake) return { ok: true, already: true };
  window.__eltonAwake = true;
  try {
    Object.defineProperty(document, "hidden", { configurable: true, get: function () { return false; } });
    Object.defineProperty(document, "visibilityState", { configurable: true, get: function () { return "visible"; } });
    Object.defineProperty(document, "webkitHidden", { configurable: true, get: function () { return false; } });
    Object.defineProperty(document, "webkitVisibilityState", { configurable: true, get: function () { return "visible"; } });
  } catch (e) { /* alguns getters podem ser não-configuráveis */ }
  var swallow = function (e) { e.stopImmediatePropagation(); };
  ["visibilitychange", "webkitvisibilitychange", "blur", "pagehide", "freeze"].forEach(function (t) {
    window.addEventListener(t, swallow, true);
    document.addEventListener(t, swallow, true);
  });
  return { ok: true };
}

/* ── Baixar mídia no MAIN world (contexto same-origin da página) ──────
 * O Flow passou a exigir Referer/Sec-Fetch same-origin no endpoint de mídia.
 * Baixar direto pelo service worker (ou por content script) mandava a origem
 * errada e o Flow devolvia HTML de erro, salvando o arquivo como .htm.
 * Rodando o fetch no MAIN world (mesmo contexto da aba do Flow) a requisição
 * sai como a própria página: cookies + Referer + Sec-Fetch-Site: same-origin.
 * Devolve o binário como data: URL, que o service worker salva sem depender
 * de mais nenhuma requisição de rede.
 * Deve ser auto-contida (roda injetada, sem closures externos).
 * ---------------------------------------------------------------- */
// Roda no MAIN world da aba do Flow: busca a mídia com contexto same-origin
// (cookie + Referer + Sec-Fetch — o Flow exige) e devolve como data: URL.
// COM RETRIES: a mídia aparece em projectInitialData ANTES de estar servível
// pelo CDN — nesse intervalo getMediaUrlRedirect responde HTTP 500. Vídeo
// precisa de mais paciência (visto em produção; porte do _baixar_midia do
// ELTON STUDIO: 12 tentativas p/ vídeo, backoff 4s→12s).
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
        ultimo = "HTTP " + resp.status; // 500 = CDN ainda não serve; retenta
      }
    } catch (e) { ultimo = String(e && e.message || e); }
    if (t < tentativas) {
      await new Promise(r => setTimeout(r, espera));
      espera = Math.min(espera * 1.4, 12000);
    }
  }
  return { ok: false, error: `mídia não servível após ${tentativas} tentativas (${ultimo})` };
}

// ── Offscreen document ───────────────────────────────────────────────
// chrome.downloads.download com data: URL grande (vídeo ~11MB) FALHA. A saída
// robusta é transformar a data URL em blob URL DENTRO de um documento da
// própria extensão (offscreen) e baixar esse blob — sem limite de tamanho e
// acessível pelo chrome.downloads (mesma origem da extensão).
let criandoOffscreen = null;
async function garantirOffscreen() {
  try {
    if (await chrome.offscreen.hasDocument?.()) return;
  } catch {}
  if (criandoOffscreen) return criandoOffscreen;
  criandoOffscreen = chrome.offscreen.createDocument({
    url: "background/offscreen.html",
    reasons: ["BLOBS"],
    justification: "Converter mídia baixada em blob para salvar arquivos grandes (vídeo).",
  }).catch((e) => {
    // "Only a single offscreen document" = já existe, ok. Outro erro: propaga.
    if (!/single offscreen document/i.test(String(e?.message || e))) throw e;
  }).finally(() => { criandoOffscreen = null; });
  return criandoOffscreen;
}

// Pede ao offscreen pra virar a data URL em blob URL (curto).
async function dataUrlParaBlobUrl(dataUrl) {
  await garantirOffscreen();
  return await chrome.runtime.sendMessage({ type: "OFFSCREEN_DATAURL_TO_BLOBURL", dataUrl });
}

// Simple main-world click for toggle buttons (no fiber needed)
function mainWorldAgentClick() {
  var btn = Array.from(document.querySelectorAll("button[aria-pressed]"))
    .find(function(b) { return /agent/i.test(b.textContent); });
  if (!btn) return { ok: false, reason: "Agent button not found" };
  if (btn.getAttribute("aria-pressed") !== "true") return { ok: true, skipped: true };
  btn.click();
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "OFFSCREEN_DATAURL_TO_BLOBURL") return; // tratado no offscreen

  if (msg?.type === "ELTON_DOWNLOAD_MEDIA") {
    // 1) busca a mídia no MAIN world (same-origin) → data URL
    // 2) offscreen vira data URL em blob URL da extensão
    // 3) chrome.downloads baixa o blob (sem limite de tamanho); nome via FIFO
    (async () => {
      try {
        const tabId = msg.tabId;
        if (!tabId) { sendResponse({ ok: false, error: "sem tabId da aba do Flow" }); return; }
        const isVideo = /\.mp4($|\?)/i.test(msg.filename || "");
        const results = await chrome.scripting.executeScript({
          target: { tabId }, world: "MAIN", func: fetchMediaAsDataUrl, args: [msg.url, isVideo],
        });
        const r = results?.[0]?.result;
        if (!r?.ok) {
          console.warn("[ELTON FLOW] fetch da mídia FALHOU:", r?.error);
          sendResponse({ ok: false, error: r?.error || "fetch falhou" });
          return;
        }
        const blobRes = await dataUrlParaBlobUrl(r.dataUrl);
        if (!blobRes?.ok) {
          console.warn("[ELTON FLOW] offscreen FALHOU:", blobRes?.error);
          sendResponse({ ok: false, error: blobRes?.error || "offscreen falhou" });
          return;
        }
        pendingDownloadNames.push(msg.filename);
        try {
          const id = await chrome.downloads.download({ url: blobRes.blobUrl, filename: msg.filename, saveAs: false });
          console.log(`[ELTON FLOW] baixado: ${msg.filename} (${r.sizeKB} KB, ${r.type})`);
          sendResponse({ ok: true, id });
        } catch (e2) {
          const i = pendingDownloadNames.indexOf(msg.filename); if (i >= 0) pendingDownloadNames.splice(i, 1);
          console.warn(`[ELTON FLOW] downloads.download FALHOU (${r.sizeKB} KB):`, String(e2?.message || e2));
          sendResponse({ ok: false, error: String(e2?.message || e2) + ` [${r.sizeKB}KB]` });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }

  if (msg?.type === "ELTON_DOWNLOAD") {
    // Fluxo legado (URL http direta). Mantido por retrocompat; o nome é forçado
    // pela fila FIFO no onDeterminingFilename.
    pendingDownloadNames.push(msg.filename);
    chrome.downloads
      .download({ url: msg.url, filename: msg.filename, saveAs: false })
      .then((id) => sendResponse({ ok: true, id }))
      .catch((e) => {
        const i = pendingDownloadNames.indexOf(msg.filename); if (i >= 0) pendingDownloadNames.splice(i, 1);
        sendResponse({ ok: false, error: String(e?.message || e) });
      });
    return true;
  }

  if (msg?.type === "INSTALL_AWAKE_SHIM") {
    const tabId = sender.tab?.id;
    if (!tabId) { sendResponse({ ok: false, reason: "no tab id" }); return; }
    chrome.scripting
      .executeScript({ target: { tabId }, world: "MAIN", func: awakeShim })
      .then(results => sendResponse(results?.[0]?.result || { ok: true }))
      .catch(e => sendResponse({ ok: false, reason: String(e?.message || e) }));
/* ── Chrome DevTools Protocol (CDP) Debugger Automation ───────────────
 * Dispatches trusted OS-level native events (isTrusted: true) via
 * chrome.debugger to guarantee React, Slate, and Material components
 * accept input and clicks.
 * ---------------------------------------------------------------- */

async function attachDebuggerIfNeeded(tabId) {
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (e) {
    if (!/already attached/i.test(e.message || String(e))) {
      throw e;
    }
  }
}

async function debuggerDispatchClick(tabId, x, y) {
  await attachDebuggerIfNeeded(tabId);
  const px = Math.round(x);
  const py = Math.round(y);
  await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: px,
    y: py,
    button: "left",
    clickCount: 1,
  });
  await new Promise(r => setTimeout(r, 60));
  await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: px,
    y: py,
    button: "left",
    clickCount: 1,
  });
}

async function debuggerInsertText(tabId, text) {
  await attachDebuggerIfNeeded(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Input.insertText", {
    text: text,
  });
}

async function debuggerDispatchEnter(tabId) {
  await attachDebuggerIfNeeded(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Input.dispatchKeyEvent", {
    type: "keyDown",
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
    macCharCode: 13,
    unmodifiedText: "\r",
    text: "\r",
    key: "Enter",
    code: "Enter",
  });
  await new Promise(r => setTimeout(r, 60));
  await chrome.debugger.sendCommand({ tabId }, "Input.dispatchKeyEvent", {
    type: "keyUp",
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
    macCharCode: 13,
    unmodifiedText: "\r",
    text: "\r",
    key: "Enter",
    code: "Enter",
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "ELTON_DOWNLOAD_MEDIA") {
    // Media download handler
    return false;
  }

  if (msg?.type === "ELTON_AWAKE_SHIM") {
    const tabId = sender.tab?.id;
    if (!tabId) { sendResponse({ ok: false, reason: "no tab id" }); return; }
    chrome.scripting
      .executeScript({ target: { tabId }, world: "MAIN", func: awakeShim })
      .then(results => sendResponse(results?.[0]?.result || { ok: true }))
      .catch(e => sendResponse({ ok: false, reason: String(e?.message || e) }));
    return true;
  }

  if (msg?.type === "MAIN_WORLD_AGENT_CLICK") {
    const tabId = sender.tab?.id;
    if (!tabId) { sendResponse({ ok: false, reason: "no tab id" }); return; }
    chrome.scripting
      .executeScript({ target: { tabId }, world: "MAIN", func: mainWorldAgentClick })
      .then(results => sendResponse(results?.[0]?.result || { ok: false, reason: "no result" }))
      .catch(e => sendResponse({ ok: false, reason: String(e?.message || e) }));
    return true;
  }

  /* ── CDP Native Debugger Automation ────────────────────────────────── */
  if (msg?.type === "DEBUGGER_CLICK") {
    (async () => {
      try {
        const tabId = msg.tabId || sender.tab?.id;
        if (!tabId) { sendResponse({ ok: false, error: "no tab id" }); return; }
        await debuggerDispatchClick(tabId, msg.x, msg.y);
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }

  if (msg?.type === "DEBUGGER_INSERT_TEXT") {
    (async () => {
      try {
        const tabId = msg.tabId || sender.tab?.id;
        if (!tabId) { sendResponse({ ok: false, error: "no tab id" }); return; }
        await debuggerInsertText(tabId, msg.text);
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }

  if (msg?.type === "DEBUGGER_ENTER") {
    (async () => {
      try {
        const tabId = msg.tabId || sender.tab?.id;
        if (!tabId) { sendResponse({ ok: false, error: "no tab id" }); return; }
        await debuggerDispatchEnter(tabId);
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }

  /* ── Lira: log de seletor ──────────────────────────────────────────── */
  if (msg?.type === "LIRA_SELECTOR_LOG") {
    fetch("http://localhost:5000/api/flow/selector-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg),
    }).catch(() => { /* Lira pode não estar rodando */ });
    sendResponse({ ok: true });
    return;
  }

  /* ── Lira: fechar aba do Flow ──────────────────────────────────────── */
  if (msg?.type === "LIRA_CLOSE_TAB") {
    (async () => {
      try {
        const tabs = await chrome.tabs.query({ url: "https://labs.google/fx/*" });
        if (tabs.length === 1) {
          await chrome.tabs.remove(tabs[0].id);
          sendResponse({ ok: true, closed: true, tabs: 1 });
        } else {
          sendResponse({ ok: true, closed: false, tabs: tabs.length,
                         reason: tabs.length === 0 ? "no-flow-tab" : "multiple-tabs" });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }

  /* ── React Fiber Submit Handler ────────────────────────────────────── */
  if (msg?.type === "REACT_FIBER_CLICK") {
    const tabId = sender.tab?.id;
    if (!tabId) {
      sendResponse({ ok: false, reason: "no tab id" });
      return;
    }

    chrome.scripting
      .executeScript({
        target: { tabId },
        world: "MAIN",
        func: reactFiberSubmit,
        args: [msg.token, msg.markerAttr],
      })
      .then((results) => {
        const val = results?.[0]?.result;
        sendResponse(val || { ok: false, reason: "no result from injection" });
      })
      .catch((e) => {
        sendResponse({ ok: false, reason: String(e?.message || e) });
      });

    return true; // async sendResponse
  }
});
