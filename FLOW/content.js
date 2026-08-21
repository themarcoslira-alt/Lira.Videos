(function () {
  const LOG_PREFIX = "[ZAPIFLOW]";

  function log(...args) {
    console.log(LOG_PREFIX, ...args);
  }

  function warn(...args) {
    console.warn(LOG_PREFIX, ...args);
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function randomIntInclusive(min, max) {
    const a = Math.ceil(min);
    const b = Math.floor(max);
    return a + Math.floor(Math.random() * (b - a + 1));
  }

  function isVisible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return (
      r.width > 0 &&
      r.height > 0 &&
      style.visibility !== "hidden" &&
      style.display !== "none"
    );
  }

  /** Plain text roughly visible in a Slate contenteditable (drops ZWSP / FEFF). */
  function editorPlainText(el) {
    return (el.innerText || "")
      .replace(/[\u200b\ufeff]/g, "")
      .replace(/\n+/g, " ")
      .trim();
  }

  function base64ToFile(dataUrl, filename = "reference.png") {
    const arr = dataUrl.split(",");
    const mime = arr[0].match(/:(.*?);/)[1];
    const bstr = atob(arr[1]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new File([u8arr], filename, { type: mime });
  }

  async function pasteImageIntoEditor(el, file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    const ev = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      composed: true,
      clipboardData: dt
    });
    if (!ev.clipboardData) {
      try {
        Object.defineProperty(ev, "clipboardData", {
          value: dt,
          enumerable: true,
          configurable: true,
        });
      } catch {
        /* ignore */
      }
    }
    el.dispatchEvent(ev);
  }

  function editorSeemsToContain(el, text) {
    const t = text.trim();
    if (!t) return false;
    const raw = editorPlainText(el);
    if (raw.includes(t)) return true;
    const head = t.slice(0, Math.min(48, t.length));
    return raw.includes(head);
  }

  /**
   * Prefer the editable with Flow's main placeholder; else the lowest visible Slate box (prompt bar).
   */
  function findFlowPromptEditor() {
    const candidates = [];
    for (const el of document.querySelectorAll(
      '[data-slate-editor="true"][contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
    )) {
      if (!isVisible(el)) continue;
      // Editor na barra inferior (prompt bar)
      const r = el.getBoundingClientRect();
      if (r.width > 50 && r.height > 20) {
        candidates.push(el);
      }
    }
    if (!candidates.length) {
      for (const el of document.querySelectorAll('textarea')) {
        if (isVisible(el)) candidates.push(el);
      }
    }
    if (!candidates.length) return null;

    // Retorna o editor mais próximo do rodapé da tela (a barra de prompts)
    return candidates.sort(
      (a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom
    )[0];
  }

  function firstMatch(selectors) {
    for (const sel of selectors || []) {
      try {
        const el = document.querySelector(sel);
        if (el) return el;
      } catch {
        /* invalid selector */
      }
    }
    return null;
  }

  /**
   * Focus like a user: scroll, then click inside the box so React/Slate activates the field.
   */
  async function focusEditorLikeUser(el) {
    el.scrollIntoView({ block: "center", inline: "nearest" });
    await sleep(40);
    const r = el.getBoundingClientRect();
    const cx = Math.min(Math.max(r.left + r.width / 2, r.left + 8), r.right - 8);
    const cy = Math.min(Math.max(r.top + r.height / 2, r.top + 8), r.bottom - 8);
    const common = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: cx,
      clientY: cy,
      button: 0,
      buttons: 1,
    };
    el.dispatchEvent(new PointerEvent("pointerdown", { ...common, pointerId: 1, pointerType: "mouse" }));
    el.dispatchEvent(new MouseEvent("mousedown", common));
    el.dispatchEvent(new PointerEvent("pointerup", { ...common, pointerId: 1, pointerType: "mouse" }));
    el.dispatchEvent(new MouseEvent("mouseup", common));
    el.dispatchEvent(new MouseEvent("click", common));
    el.focus();
    await sleep(50);
  }

  async function selectAllInEditor(el) {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    await sleep(30);
  }

  async function clearEditorContent(el) {
    await selectAllInEditor(el);
    try {
      document.execCommand("delete", false, null);
    } catch {
      el.dispatchEvent(
        new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          inputType: "deleteContentBackward",
        })
      );
    }
    await sleep(40);
  }

  function dispatchSyntheticPaste(el, text) {
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    const ev = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      composed: true,
    });
    try {
      Object.defineProperty(ev, "clipboardData", {
        value: dt,
        enumerable: true,
        configurable: true,
      });
    } catch {
      /* ignore */
    }
    return el.dispatchEvent(ev);
  }

  async function typeInsertTextEvents(el, text, charDelayMs) {
    el.focus();
    const endSel = window.getSelection();
    const endRange = document.createRange();
    endRange.selectNodeContents(el);
    endRange.collapse(false);
    endSel.removeAllRanges();
    endSel.addRange(endRange);
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      el.dispatchEvent(
        new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          composed: true,
          inputType: "insertText",
          data: char,
        })
      );
      el.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          composed: true,
          inputType: "insertText",
          data: char,
        })
      );
      if (charDelayMs > 0) await sleep(charDelayMs);
    }
  }

  /**
   * Slate/React ignores raw DOM writes — drive the same path as typing/pasting.
   */
  async function clickPlusButton() {
    const selGroup = globalThis.FLOW_BATCH_DEFAULT_SELECTORS.uploadButton;
    // Camada 1-2: aria-label / busca via findWithLayers
    let btn = globalThis.findWithLayers
      ? globalThis.findWithLayers(selGroup, "uploadButton", isVisible)
      : null;

    // Camada 3 (descoberta por ícone Material — fallback interno)
    if (!btn || !isVisible(btn)) {
      btn = Array.from(document.querySelectorAll('button')).find(b =>
        isVisible(b) && (b.textContent || '').includes('add_2')
      );
      if (btn) log("[SELECTOR] uploadButton: FALLBACK camada 3 (ícone add_2 textContent)");
    }
    if (!btn || !isVisible(btn)) {
      for (const b of document.querySelectorAll('button')) {
        if (!isVisible(b)) continue;
        const text = b.textContent.trim().toLowerCase();
        if (text === 'add' || text === 'add_circle' || text === 'add_photo_alternate') {
          btn = b;
          log("[SELECTOR] uploadButton: FALLBACK camada 3 (textContent:", text, ")");
          break;
        }
      }
    }
    if (btn) {
      log("Found '+' button, clicking it.");
      btn.click();
      return true;
    }
    warn("[SELECTOR] uploadButton: FALHOU — nenhuma camada encontrou o botão");
    return false;
  }


  async function clickExistingReference() {
    const matches = Array.from(document.querySelectorAll('div, span, p')).filter(el => {
      return isVisible(el) && el.textContent.trim() === 'reference.png';
    });
    
    if (matches.length > 0) {
      const target = matches[matches.length - 1];
      const clickable = target.closest('button, [role="menuitem"], li, [role="button"]') || target;
      clickable.click();
      return true;
    }
    return false;
  }

  async function injectTextIntoFlowPrompt(el, text, charDelayMs, refImageFile = null, isFirstPrompt = false) {
    if (!text && !refImageFile) return false;

    await focusEditorLikeUser(el);
    await clearEditorContent(el);
    await focusEditorLikeUser(el);

    if (refImageFile) {
      let attachedFromMenu = false;
      
      if (!isFirstPrompt) {
        log("Attempting to attach from existing assets...");
        if (await clickPlusButton()) {
        let found = false;
        for (let i = 0; i < 10; i++) {
          await sleep(500);
          if (await clickExistingReference()) {
            found = true;
            break;
          }
        }
        
        if (found) {
          log("Attached existing reference.png from menu!");
          attachedFromMenu = true;
          await sleep(1500); // Wait for pill to render in prompt
        } else {
          log("Not found in menu. Closing menu and falling back to paste...");
          await focusEditorLikeUser(el); // clicks editor to close menu
          await sleep(500);
        }
      }
      }

      if (!attachedFromMenu) {
        const selectors = globalThis.FLOW_BATCH_DEFAULT_SELECTORS;
        const htmlBefore = el.innerHTML;
        const imgsBefore = document.querySelectorAll('img').length;
        
        await pasteImageIntoEditor(el, refImageFile);
        log("Waiting for image to appear in DOM...");
        
        let changed = false;
        for(let i=0; i<60; i++) {
          if (el.innerHTML !== htmlBefore || document.querySelectorAll('img').length > imgsBefore) {
            changed = true;
            break;
          }
          await sleep(500);
        }

        if (changed) {
          log("Image detected in DOM, waiting for upload to settle...");
          for(let i=0; i<60; i++) {
             let visibleSpinner = false;
             for (const s of document.querySelectorAll('[role="progressbar"]')) {
               if (isVisible(s)) visibleSpinner = true;
             }
             const submitBtn = firstMatch(selectors.submitButton || []) || findCreateButtonByArrowIcon();
             const isSubmitDisabled = submitBtn && (submitBtn.disabled || submitBtn.getAttribute('aria-disabled') === 'true');
             
             if (!visibleSpinner && !isSubmitDisabled && submitBtn) {
                log("Upload appears complete (Submit button enabled).");
                break;
             }
             if (!visibleSpinner && i > 16) { 
                log("No spinner found after 8s, assuming upload complete.");
                break;
             }
             await sleep(500);
          }
          await sleep(2500); // Final safety buffer
        } else {
          // BUG FIX (Lira): não prosseguir sem a imagem de referência.
          // Antes: continuava silenciosamente e submetia o prompt sem imagem
          // (causa raiz do bug de inconsistência de personagem).
          warn("Image paste not detected in DOM after 30s — abortando para não gerar sem referência.");
          chrome.runtime.sendMessage({
            type: 'LIRA_SELECTOR_LOG',
            label: 'imagePaste',
            layer: -1,
            failed: true,
            fallback: true,
            detail: 'paste not detected in DOM after 30s',
          }).catch(() => {});
          return false;
        }
      } // End if (!attachedFromMenu)

      await focusEditorLikeUser(el);
    }

    if (!text) return true;

    // 1. Try synthetic paste
    dispatchSyntheticPaste(el, text);
    await sleep(200);
    if (editorSeemsToContain(el, text)) {
      log("Filled prompt via synthetic paste.");
      return true;
    }

    // 2. Try beforeinput
    log("Text not found, trying beforeinput...");
    const before = new InputEvent("beforeinput", {
      bubbles: true, cancelable: true, composed: true,
      inputType: "insertText", data: text,
    });
    el.dispatchEvent(before);
    el.dispatchEvent(new InputEvent("input", {
      bubbles: true, composed: true,
      inputType: "insertText", data: text,
    }));
    await sleep(200);
    if (editorSeemsToContain(el, text)) {
      log("Filled prompt via insertText beforeinput.");
      return true;
    }

    // 3. Try execCommand insertText
    log("Trying execCommand insertText per character...");
    for (let j = 0; j < text.length; j++) {
      try { document.execCommand("insertText", false, text[j]); } catch {}
      if (charDelayMs > 0) await sleep(charDelayMs);
    }
    await sleep(200);
    if (editorSeemsToContain(el, text)) {
      log("Filled prompt via execCommand insertText.");
      return true;
    }

    // 4. Try InputEvents
    log("Trying per-character InputEvent insertText...");
    await typeInsertTextEvents(el, text, charDelayMs);
    await sleep(200);
    if (editorSeemsToContain(el, text)) {
      log("Filled prompt via synthetic InputEvents.");
      return true;
    }

    // 5. Try CDP Debugger insertText (trusted OS-level native event)
    try {
      await focusEditorLikeUser(el);
      const dbgRes = await chrome.runtime.sendMessage({ type: "DEBUGGER_INSERT_TEXT", text });
      if (dbgRes?.ok) {
        await sleep(250);
        if (editorSeemsToContain(el, text)) {
          log("Filled prompt via CDP Debugger Input.insertText (trusted native text).");
          return true;
        }
      }
    } catch {}

    log("Prompt verification failed; editor text:", editorPlainText(el).slice(0, 120));
    return false;
  }

  function findCreateButtonByArrowIcon() {
    const candidates = [];
    for (const btn of document.querySelectorAll("button")) {
      if (!isVisible(btn)) continue;
      const text = (btn.textContent || "").trim();
      const r = btn.getBoundingClientRect();
      // O botão de envio fica sempre na barra inferior de comandos
      if (r.bottom < window.innerHeight - 250) continue;

      const icon = btn.querySelector("i, span, svg");
      const iconText = (icon?.textContent || "").trim();
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      if (
        text === "arrow_forward" ||
        iconText === "arrow_forward" ||
        aria.includes("create") ||
        aria.includes("generate") ||
        aria.includes("submit") ||
        btn.querySelector("svg")
      ) {
        candidates.push(btn);
      }
    }
    if (!candidates.length) return null;
    // O botão de envio fica posicionado no extremo direito da barra de comandos
    return candidates.sort(
      (a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right
    )[0];
  }

  /**
   * Find the Create button by its visually-hidden <span> label.
   * Google Flow wraps "Create" in a clip-rect hidden span inside the button.
   */
  function findCreateButtonByHiddenLabel() {
    for (const btn of document.querySelectorAll("button")) {
      if (!isVisible(btn)) continue;
      const spans = btn.querySelectorAll("span");
      for (const span of spans) {
        if (span.textContent?.trim().toLowerCase() === "create") {
          return btn;
        }
      }
    }
    return null;
  }

  /**
   * Consolidated submit button discovery with layered selectors + diagnostic logging.
   */
  function findSubmitButton(selectors) {
    // Camada 1-2: aria-label e type=submit via findWithLayers
    const selGroup = selectors.submitButton;
    if (globalThis.findWithLayers && selGroup && selGroup.layers) {
      const btn = globalThis.findWithLayers(selGroup, "submitButton", isVisible);
      if (btn) return btn;
    } else {
      // Fallback: firstMatch legado se selectors ainda for array
      const arr = Array.isArray(selectors.submitButton) ? selectors.submitButton : [];
      const btn = firstMatch(arr);
      if (btn && isVisible(btn)) {
        log("[SELECTOR] submitButton: legado (array)");
        return btn;
      }
    }

    // Camada 3a — ícone arrow_forward
    const byArrow = findCreateButtonByArrowIcon();
    if (byArrow) {
      log("[SELECTOR] submitButton: FALLBACK camada 3a (arrow_forward icon)");
      return byArrow;
    }

    // Camada 3b — span oculto "Create"
    const byLabel = findCreateButtonByHiddenLabel();
    if (byLabel) {
      log("[SELECTOR] submitButton: FALLBACK camada 3b (hidden 'Create' span)");
      return byLabel;
    }

    // Camada 3c — textContent scan
    for (const b of document.querySelectorAll("button")) {
      if (!isVisible(b)) continue;
      if (b.textContent?.toLowerCase().includes("create")) {
        log("[SELECTOR] submitButton: FALLBACK camada 3c (textContent scan)");
        return b;
      }
    }

    warn("[SELECTOR] submitButton: FALHOU — todas as camadas esgotadas");
    return null;
  }


  /* ── MAIN world React fiber click ──────────────────────────────────
   * Marks the target element, then asks the background service worker
   * to inject a function via chrome.scripting.executeScript in the
   * MAIN world. This bypasses both CSP and isTrusted checks — the
   * injected code calls React's onClick handler directly.
   * ---------------------------------------------------------------- */

  const FQ_MARKER_ATTR = "data-fq-click-target";
  let fqClickCounter = 0;

  async function clickViaReactFiber(el) {
    const token = String(++fqClickCounter);
    el.setAttribute(FQ_MARKER_ATTR, token);
    try {
      const result = await chrome.runtime.sendMessage({
        type: "REACT_FIBER_CLICK",
        token,
        markerAttr: FQ_MARKER_ATTR,
      });
      return result || { ok: false, reason: "no response from background" };
    } catch (e) {
      return { ok: false, reason: String(e?.message || e) };
    } finally {
      el.removeAttribute(FQ_MARKER_ATTR);
    }
  }

  /**
   * Click a button with full pointer/mouse event sequence (synthetic fallback).
   */
  function clickButtonSynthetic(btn) {
    const r = btn.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const common = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: cx,
      clientY: cy,
      button: 0,
      buttons: 1,
    };
    btn.dispatchEvent(new PointerEvent("pointerdown", { ...common, pointerId: 1, pointerType: "mouse" }));
    btn.dispatchEvent(new MouseEvent("mousedown", common));
    btn.dispatchEvent(new PointerEvent("pointerup", { ...common, pointerId: 1, pointerType: "mouse" }));
    btn.dispatchEvent(new MouseEvent("mouseup", common));
    btn.dispatchEvent(new MouseEvent("click", { ...common, buttons: 0 }));
    btn.click();
  }

  /**
   * Try all click strategies: CDP Debugger Click (trusted OS native event) → React fiber onSubmit/onClick → synthetic events
   */
  async function clickSubmitButton(btn) {
    // Attempt 1: CDP Debugger native click (trusted OS-level event, triggers native isTrusted)
    try {
      const r = btn.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const dbgRes = await chrome.runtime.sendMessage({
        type: "DEBUGGER_CLICK",
        x: cx,
        y: cy,
      });
      if (dbgRes?.ok) {
        log("Submit clicked via CDP Debugger (trusted native isTrusted event)!");
        return true;
      }
    } catch {}

    // Attempt 2: React fiber direct call (bypasses isTrusted entirely)
    const fiberResult = await clickViaReactFiber(btn);
    if (fiberResult.ok) {
      log(`Submit via React fiber: ${fiberResult.method} at depth ${fiberResult.depth}`);
      return true;
    }
    warn("React fiber submit failed:", JSON.stringify(fiberResult));

    // Attempt 3: Full synthetic pointer/mouse event sequence
    warn("Trying synthetic click events (fallback)...");
    clickButtonSynthetic(btn);
    return true;
  }

  async function submitViaEnter(el) {
    // Attempt 1: CDP Debugger native Enter key
    try {
      const dbgRes = await chrome.runtime.sendMessage({ type: "DEBUGGER_ENTER" });
      if (dbgRes?.ok) {
        log("Submitted via CDP Debugger native Enter key!");
        return;
      }
    } catch {}

    // Attempt 2: Synthetic KeyboardEvent sequence
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(
        new KeyboardEvent(type, {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          cancelable: true,
        })
      );
    }
  }

  async function dismissOptionalOverlays(selectorsConfig) {
    const list = selectorsConfig.dismissOverlays || [];
    for (let i = 0; i < 3; i++) {
      const btn = firstMatch(list);
      if (btn && isVisible(btn)) {
        btn.click();
        await sleep(400);
      } else break;
    }
  }

  // ── Keep-alive ────────────────────────────────────────────────────
  // O Chrome "congela" os timers de uma aba em segundo plano (e descarta abas
  // ociosas). Um oscilador de áudio INAUDÍVEL marca a aba como "tocando som",
  // o que impede o congelamento agressivo — assim a fila continua mesmo se você
  // trocar de aba ou minimizar. Não restaura o rAF de aba oculta, mas mantém os
  // timers + o MutationObserver vivos pra capturar e baixar as mídias.
  let keepAlive = null;
  function setKeepAlive(on) {
    try {
      if (on) {
        // Instala o anti-pausa (MAIN world) pra a página não se pausar ao
        // minimizar/trocar de aba. Idempotente do outro lado.
        chrome.runtime.sendMessage({ type: "INSTALL_AWAKE_SHIM" }).catch(() => {});
        if (keepAlive) { keepAlive.ctx.resume?.(); return; }
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        const ctx = new Ctx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        gain.gain.value = 0.0001;   // praticamente silêncio
        osc.frequency.value = 30;   // sub-grave inaudível
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        keepAlive = { ctx, osc };
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
      } else if (keepAlive) {
        try { keepAlive.osc.stop(); } catch {}
        try { keepAlive.ctx.close(); } catch {}
        keepAlive = null;
      }
    } catch {
      /* ignore */
    }
  }

  let runToken = 0;

  async function runQueue(payload) {
    const token = ++runToken;
    let prompts = (payload.prompts || []).map((p) => String(p).trim()).filter(Boolean);
    const waitMinMs = Math.max(0, payload.waitMinMs ?? 10_000);
    const waitMaxMs = Math.max(waitMinMs, payload.waitMaxMs ?? 30_000);
    const preferEnter = payload.preferEnter === true;
    const charDelayMs = Math.max(0, payload.charDelayMs ?? 50);
    const refImageBase64 = payload.refImage;
    // No modo vídeo o painel manda a URL da imagem gerada; buscamos aqui
    // (contexto logado do labs.google) e colamos como frame de referência.
    const refImageUrl = payload.refImageUrl;
    const videoMode = payload.videoMode === true;
    let refImageFile = null;
    if (refImageBase64) {
      refImageFile = base64ToFile(refImageBase64);
      log(`Converted reference image (size: ${refImageFile.size} bytes)`);
    } else if (refImageUrl) {
      try {
        refImageFile = await fetchImageAsFile(refImageUrl);
        log(`Fetched reference image from URL (size: ${refImageFile.size} bytes)`);
      } catch (e) {
        return { error: "Não consegui baixar a imagem para gerar o vídeo: " + String(e?.message || e),
                 completed: 0, failedPromptIndex: 0 };
      }
    }

    /** Only bundled defaults — never merge message-supplied selectors (reduces attack surface). */
    const selectors = globalThis.FLOW_BATCH_DEFAULT_SELECTORS;

    for (let i = 0; i < prompts.length; i++) {
      if (token !== runToken) {
        log("Stopped by user.");
        return { stopped: true, completed: i };
      }

      const text = prompts[i];

      await dismissOptionalOverlays(selectors);

      let input = findFlowPromptEditor();
      if (!input) {
        const selGroup = selectors.promptInput;
        if (globalThis.findWithLayers && selGroup && selGroup.layers) {
          input = globalThis.findWithLayers(selGroup, "promptInput", isVisible);
          if (input) log("[SELECTOR] promptInput: findWithLayers encontrou o editor");
        } else {
          // Fallback legado
          input = firstMatch(Array.isArray(selectors.promptInput) ? selectors.promptInput : []);
        }
      }
      if (!input || !isVisible(input)) {
        const msg =
          "Could not find prompt field. Open a Flow project tab (…/flow/project/…) and try again.";
        log(msg);
        return { error: msg, completed: i, failedPromptIndex: i };
      }

      // No modo vídeo cada imagem é nova → sempre colamos direto (não tentamos
      // reaproveitar do menu de assets). No modo imagem mantém o comportamento.
      const isFirstPrompt = videoMode ? true : (i === 0);
      const ok = await injectTextIntoFlowPrompt(input, text, charDelayMs, refImageFile, isFirstPrompt);
      if (!ok) {
        return {
          error:
            "Could not set prompt text in a way Flow accepts. Try entering one prompt manually once, then retry the queue.",
          completed: i,
          failedPromptIndex: i,
        };
      }

      await sleep(200);

      let submitted = false;
      if (!preferEnter) {
        const submit = findSubmitButton(selectors);
        if (submit) {
          try {
            await clickSubmitButton(submit);
            submitted = true;
            log(`Submitted prompt ${i + 1}/${prompts.length}.`);
          } catch (e) {
            warn(`Click failed for prompt ${i + 1}:`, e?.message || e);
          }
        } else {
          warn(`No submit button found for prompt ${i + 1}, will try Enter.`);
        }
      }
      if (!submitted) {
        submitViaEnter(input);
        log(`Sent Enter to submit (prompt ${i + 1}/${prompts.length})`);
      }

      if (i < prompts.length - 1) {
        const pause = randomIntInclusive(waitMinMs, waitMaxMs);
        log(`Waiting ${Math.round(pause / 1000)}s before next prompt…`);
        await sleep(pause);
      }
    }

    return { done: true, completed: prompts.length };
  }

  // Count failure cards by the "Retry" span inside a button.
  // O texto é localizado pelo Flow (PT/ES/FR/DE...), então cobrimos as variações.
  const RETRY_LABELS = /^(retry|tentar novamente|reintentar|réessayer|wiederholen|riprova)$/i;
  function countFailCards() {
    var n = 0;
    document.querySelectorAll("button span").forEach(function(span) {
      if (span.children.length === 0 && RETRY_LABELS.test(span.textContent.trim())) n++;
    });
    return n;
  }

  // Imagens geradas pelo Flow. O atributo alt é LOCALIZADO ("Generated image" em
  // inglês, "Imagem gerada" em PT, "Imagen generada" em ES...), por isso casamos
  // as variações conhecidas E, como rede de segurança independente de idioma,
  // qualquer <img> servida pelo próprio Flow (host labs.google) com tamanho de
  // imagem real — o avatar do usuário vem de googleusercontent e fica de fora.
  function isLabsMediaSrc(src) {
    if (!src || !/^https?:/.test(src)) return false;
    try { return new URL(src).host.includes("labs.google"); } catch { return false; }
  }

  // alt localizado da MINIATURA do vídeo ("Miniatura do vídeo" PT, "Video
  // thumbnail" EN, etc.) — é o <img> visível que representa um vídeo pronto.
  const VIDEO_THUMB_ALT = /miniatura do v[ií]deo|video thumbnail|miniatura del v[ií]deo|miniature de la vid[eé]o|videovorschau|miniatura( del)? video/i;

  const GENERATED_ALT = /generated image|imagem gerada|imagen generada|image g[eé]n[eé]r[eé]e|generiertes bild|immagine generata/i;
  function getGeneratedImgs() {
    return Array.from(document.querySelectorAll("img")).filter(function (img) {
      const alt = img.getAttribute("alt") || "";
      // Nunca tratar a miniatura de um VÍDEO como imagem gerada.
      if (VIDEO_THUMB_ALT.test(alt)) return false;
      if (GENERATED_ALT.test(alt)) return true;
      const src = img.src || "";
      if (!isLabsMediaSrc(src)) return false;
      const r = img.getBoundingClientRect();
      return r.width >= 200 && r.height >= 150;
    });
  }

  // Vídeos gerados pelo Flow. O vídeo pronto vira um <video src="...media
  // .getMediaUrlRedirect?name=UUID"> — MAS o Flow o deixa OCULTO (0×0) até o
  // mouse passar por cima. Por isso NÃO filtramos por tamanho: detectamos o
  // <video> mesmo invisível, e também o <img alt="Miniatura do vídeo"> (que
  // fica visível) como rede de segurança. Os dois apontam pro mesmo asset
  // (?name=…); a deduplicação por name acontece na espera.
  function getGeneratedVideos() {
    const out = [];
    for (const v of document.querySelectorAll("video")) {
      if (isLabsMediaSrc(v.src || v.currentSrc || "")) out.push(v);
    }
    for (const img of document.querySelectorAll("img")) {
      if (VIDEO_THUMB_ALT.test(img.getAttribute("alt") || "") && isLabsMediaSrc(img.src || "")) {
        out.push(img);
      }
    }
    return out;
  }

  // Devolve a lista de mídias (img OU video) conforme o tipo pedido.
  function getGeneratedMedia(kind) {
    return kind === "video" ? getGeneratedVideos() : getGeneratedImgs();
  }

  // Busca uma imagem por URL (no contexto logado do labs.google) e devolve um
  // File pronto pra colar no editor — usado pra reinserir a imagem no modo vídeo.
  async function fetchImageAsFile(url, filename = "reference.png") {
    const resp = await fetch(url);
    const blob = await resp.blob();
    const ext = (blob.type.split("/")[1] || "png").replace("jpeg", "jpg");
    const name = filename.replace(/\.[^.]+$/, "") + "." + ext;
    return new File([blob], name, { type: blob.type || "image/png" });
  }

  /* ── Motor via API (v1.4) ─────────────────────────────────────────
   * Em vez de raspar o DOM — que o Flow VIRTUALIZA (desmonta tiles fora da
   * tela) e PAUSA em aba oculta — perguntamos ao próprio backend do Flow a
   * lista de mídias do projeto: GET /fx/api/trpc/flow.projectInitialData
   * (same-origin, autenticado pelo cookie da sessão). Cada mídia traz o
   * name (uuid), o tipo e o PROMPT EXATO que a gerou; o download é
   * name → /fx/api/trpc/media.getMediaUrlRedirect?name=…
   * Não depende de nada renderizado: funciona minimizado, em outra aba,
   * com qualquer zoom e sem rolar a tela.
   * ---------------------------------------------------------------- */

  const MEDIA_REDIRECT_URL =
    "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=";

  function getProjectIdFromUrl() {
    const m = location.pathname.match(/\/project\/([0-9a-f-]{36})/i);
    return m ? m[1] : null;
  }

  async function fetchProjectMediaList() {
    const projectId = getProjectIdFromUrl();
    if (!projectId) throw new Error("URL sem /project/<id> — abra um projeto do Flow");
    const input = encodeURIComponent(JSON.stringify({ json: { projectId } }));
    const resp = await fetch("/fx/api/trpc/flow.projectInitialData?input=" + input, {
      credentials: "same-origin",
    });
    if (!resp.ok) throw new Error("projectInitialData HTTP " + resp.status);
    const data = await resp.json();
    const pc = data?.result?.data?.json?.projectContents || {};
    const media = Array.isArray(pc.media) ? pc.media : [];
    return media.map((m) => {
      const req = m.mediaMetadata?.requestData || {};
      // A mídia de imagem vem com o bloco `image`; vídeo é o resto (o Flow
      // só guarda imagem/vídeo em projeto). Checagem dupla pelo requestData.
      const isImage = !!m.image || !!req.imageGenerationRequestData;
      const prompt =
        m.image?.generatedImage?.prompt ||
        m.video?.generatedVideo?.prompt ||
        (Array.isArray(req.promptInputs) && req.promptInputs[0]?.textInput) ||
        "";
      return {
        name: m.name,
        kind: isImage ? "image" : "video",
        prompt: String(prompt),
        createTime: m.mediaMetadata?.createTime || "",
        workflowId: m.workflowId || "",
        url: MEDIA_REDIRECT_URL + m.name,
      };
    });
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id) {
      return;
    }
    if (message?.type === "FLOW_API_LIST_MEDIA") {
      fetchProjectMediaList()
        .then((media) => sendResponse({ ok: true, media }))
        .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));
      return true;
    }
    if (message?.type === "FLOW_BATCH_PING") {
      sendResponse({ ok: true, href: location.href });
      return;
    }
    if (message?.type === "FLOW_BATCH_RUN") {
      runQueue(message)
        .then((result) => sendResponse(result))
        .catch((e) => sendResponse({ error: String(e?.message || e) }));
      return true;
    }
    if (message?.type === "FLOW_BATCH_STOP") {
      runToken++;
      setKeepAlive(false);
      sendResponse({ ok: true });
    }

    if (message?.type === "FLOW_KEEPALIVE") {
      setKeepAlive(message.on !== false);
      sendResponse({ ok: true, state: !!keepAlive });
      return;
    }

    if (message?.type === "FLOW_GET_AGENT_MODE") {
      const agentBtn = Array.from(document.querySelectorAll("button[aria-pressed]"))
        .find(b => /agent/i.test(b.textContent));
      const isOn = agentBtn?.getAttribute("aria-pressed") === "true";
      const found = !!agentBtn;
      sendResponse({ found, isOn });
      return;
    }

    if (message?.type === "FLOW_DISABLE_AGENT_MODE") {
      (async function () {
        const getAgentBtn = () =>
          Array.from(document.querySelectorAll("button[aria-pressed]"))
            .find(b => /agent/i.test(b.textContent));

        const btn = getAgentBtn();

        // Already off or not found
        if (!btn || btn.getAttribute("aria-pressed") !== "true") {
          sendResponse({ ok: true, skipped: true });
          return;
        }

        // Click in main world via background scripting (bypasses isolated world)
        const clickRes = await chrome.runtime.sendMessage({ type: "MAIN_WORLD_AGENT_CLICK" });
        log("Agent click result:", JSON.stringify(clickRes));

        // Wait up to 3s for aria-pressed to flip to false
        for (let i = 0; i < 15; i++) {
          await sleep(200);
          const b = getAgentBtn();
          if (!b || b.getAttribute("aria-pressed") === "false") {
            sendResponse({ ok: true });
            return;
          }
        }

        sendResponse({ ok: false, error: "Agent mode still on after click" });
      })();
      return true;
    }

    if (message?.type === "FLOW_GET_TILE_COUNT") {
      const kind = message.mediaKind === "video" ? "video" : "image";
      const realSrcs = getGeneratedMedia(kind)
        .map(el => el.src || el.currentSrc || "")
        .filter(src => src && src.startsWith('http'));
      sendResponse({
        count: realSrcs.length,
        srcs: realSrcs,
        failCount: countFailCards(),
      });
      return;
    }

    // Detecta se o Flow está no modo Vídeo (o botão de saída no rodapé mostra
    // "Vídeo · 16:9 · 1x" no modo vídeo, ou "Imagem…" no modo imagem). Usado pra
    // avisar o usuário antes de iniciar a fila de vídeos.
    if (message?.type === "FLOW_GET_OUTPUT_MODE") {
      let mode = "unknown";
      const btns = Array.from(document.querySelectorAll("button")).filter(b => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.bottom > window.innerHeight - 160;
      });
      for (const b of btns) {
        const txt = (b.textContent || "").trim().toLowerCase();
        if (/^v[ií]deo/.test(txt)) { mode = "video"; break; }
        if (/^imagem|^image/.test(txt)) { mode = "image"; break; }
      }
      sendResponse({ mode });
      return;
    }

    if (message?.type === "FLOW_WAIT_GENERATION") {
      // `seen` = TODAS as imagens já baixadas em prompts anteriores (conjunto
      // global acumulado pelo painel). Isso impede que uma imagem de um prompt
      // anterior — que só terminou de carregar agora — seja atribuída a ESTE
      // prompt (a causa do embaralhamento). `beforeSrcs` mantido p/ retrocompat.
      const seen            = new Set(message.seenSrcs || message.beforeSrcs || []);
      const beforeFailCount = message.beforeFailCount ?? 0;
      // Vídeo demora mais que imagem — timeout maior por padrão no modo vídeo.
      const mediaKind       = message.mediaKind === "video" ? "video" : "image";
      const timeout         = message.timeoutMs ?? (mediaKind === "video" ? 300000 : 180000);
      const SETTLE_MS       = 2500; // espera estabilizar antes de capturar

      // src real de uma mídia (img usa .src; video usa .src||.currentSrc)
      function mediaSrc(el) { return el.src || el.currentSrc || ""; }
      // identidade do asset = ?name=… (um vídeo aparece como <video> E como
      // <img> thumbnail com o MESMO name — precisamos contar como UM só)
      function mediaName(src) { try { return new URL(src).searchParams.get("name") || src; } catch { return src; } }

      // names já baixados em prompts anteriores (derivados das srcs vistas)
      const seenNames = new Set(); seen.forEach(s => seenNames.add(mediaName(s)));

      // Só mídias HTTP novas, em ordem do DOM, deduplicadas por name
      function getNewImageUrls() {
        const dedup = new Set();
        const urls = [];
        for (const el of getGeneratedMedia(mediaKind)) {
          const src = mediaSrc(el);
          if (!src || !src.startsWith("http")) continue;
          const name = mediaName(src);
          if (seenNames.has(name) || dedup.has(name)) continue;
          dedup.add(name);
          urls.push(src);
        }
        return urls;
      }

      // Containers roláveis da página (janela + áreas internas com scroll).
      function getScrollers() {
        const out = [document.scrollingElement || document.documentElement];
        document.querySelectorAll('main, [role="main"], [class*="scroll" i]').forEach(el => {
          if (el.scrollHeight > el.clientHeight + 10) out.push(el);
        });
        return out;
      }

      // Traz o tile da mídia pro centro e dispara hover — o Flow deixa o <video>
      // 0×0 até interagir; isso força ele a montar e expor o src real (mp4), em
      // vez de cairmos na miniatura (poster) que não é o vídeo.
      function nudgeTile(el) {
        try {
          el.scrollIntoView({ block: "center" });
          const tile = el.closest('[role="listitem"], li, article, div') || el;
          tile.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
          tile.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
          tile.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));
        } catch { /* ignore */ }
      }

      // Sweep COMPLETO: percorre cada container do fundo ao topo em passos, com
      // pequenas pausas, FORÇANDO a lista virtualizada do Flow a montar TODOS os
      // tiles — não importa se a geração nova aparece no topo ou embaixo. Isto
      // resolve o caso em que o vídeo gerado ficava fora da tela e só baixava
      // depois de o usuário rolar a página até o topo na mão.
      async function sweepScroll() {
        for (const sc of getScrollers()) {
          const max = sc.scrollHeight - sc.clientHeight;
          if (max <= 0) continue;
          const STEPS = 5;
          for (let s = STEPS; s >= 0; s--) {           // fundo → topo
            sc.scrollTop = Math.round((max * s) / STEPS);
            sc.dispatchEvent(new Event("scroll", { bubbles: true }));
            await sleep(90);
          }
        }
        // Garante que cada mídia exponha seu src (em especial o <video> mais novo).
        for (const el of getGeneratedMedia(mediaKind)) nudgeTile(el);
        await sleep(90);
        // O item MAIS NOVO nasce no TOPO. Quando a lista passa de ~10, o Flow
        // VIRTUALIZA (desmonta o que está fora da tela). O nudge acima deixava o
        // scroll embaixo, então o vídeo novo — no topo — ficava fora do DOM e
        // NAO era detectado (travava depois do ~10o). Aqui a gente VOLTA AO TOPO,
        // monta os primeiros tiles e expoe o src deles antes de capturar.
        for (const sc of getScrollers()) {
          sc.scrollTop = 0;
          sc.dispatchEvent(new Event("scroll", { bubbles: true }));
        }
        await sleep(160);
        for (const el of getGeneratedMedia(mediaKind).slice(0, 5)) nudgeTile(el);
        for (const sc of getScrollers()) sc.scrollTop = 0;   // fica no topo p/ o considerSettle
        await sleep(140);
      }

      // Versão rápida/síncrona só pro pré-check inicial.
      function triggerLazyLoad() {
        for (const sc of getScrollers()) {
          sc.scrollTop = sc.scrollHeight;
          sc.scrollTop = 0;
        }
        const media = getGeneratedMedia(mediaKind);
        if (media.length) media[media.length - 1].scrollIntoView({ block: "nearest" });
      }

      function hasNewFailure() {
        return countFailCards() > beforeFailCount;
      }

      // Texto do card de erro do Flow — pra mostrar o MOTIVO real ao usuário
      // (ex.: limite/cota atingido) em vez de um "falhou" genérico.
      function getFailReason() {
        let reason = "";
        document.querySelectorAll("button span").forEach(function (span) {
          if (span.children.length === 0 && span.textContent.trim() === "Retry") {
            let node = span.closest("div");
            for (let up = 0; up < 4 && node && node.parentElement; up++) node = node.parentElement;
            const txt = (node ? node.innerText : "")
              .replace(/\s+/g, " ").replace(/\bRetry\b/g, "").trim();
            if (txt) reason = txt.slice(0, 160);
          }
        });
        return reason;
      }

      // Só considera FALHA se NÃO apareceu imagem nova junto. O Flow às vezes
      // mostra um "Retry" por um instante enquanto a imagem ainda carrega; se a
      // imagem veio, isso é SUCESSO, não erro — evita parar a fila à toa.
      function handleFailure() {
        if (getNewImageUrls().length > 0) return false;
        finish({ failed: true, reason: getFailReason() });
        return true;
      }

      let resolved = false;
      let polling = true;
      let settleTimer = null;
      let lastSeenCount = -1;

      function finish(result) {
        if (resolved) return;
        resolved = true;
        polling = false;
        clearTimeout(timer);
        clearTimeout(settleTimer);
        observer.disconnect();
        sendResponse(result);
      }

      // Quando aparece imagem nova, NÃO retorna na hora: espera SETTLE_MS sem o
      // número de novas mudar — assim, se o Flow gerar mais de uma, capturamos
      // o conjunto completo de uma vez e o painel baixa só a primeira (a deste
      // prompt), marcando todas como vistas. Resolve o embaralhamento.
      function considerSettle() {
        const urls = getNewImageUrls();
        if (urls.length === 0) return;
        if (urls.length !== lastSeenCount) {
          // ainda crescendo — reinicia a contagem de estabilização
          lastSeenCount = urls.length;
          clearTimeout(settleTimer);
          settleTimer = setTimeout(() => {
            const finalUrls = getNewImageUrls();
            if (finalUrls.length > 0) finish({ done: true, newUrls: finalUrls });
          }, SETTLE_MS);
        }
      }

      triggerLazyLoad();
      if (hasNewFailure() && getNewImageUrls().length === 0) {
        sendResponse({ failed: true, reason: getFailReason() });
        return;
      }
      considerSettle();

      const timer = setTimeout(() => finish({ timeout: true }), timeout);

      // Loop de varredura auto-agendado (em vez de setInterval fixo): faz um
      // sweep completo, checa falha/sucesso e repete até resolver. O await
      // garante que um sweep termine antes do próximo começar.
      (async function pollLoop() {
        while (polling && !resolved) {
          await sweepScroll();
          if (resolved) break;
          if (hasNewFailure() && handleFailure()) return;
          considerSettle();
          await sleep(700);
        }
      })();

      // O MutationObserver pega mudanças de DOM na hora e NÃO sofre throttle de
      // timer, então a captura é rápida mesmo com a aba em segundo plano.
      const observer = new MutationObserver(function() {
        if (hasNewFailure() && handleFailure()) return;
        considerSettle();
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["alt", "src"],
      });

      return true;
    }
  });

  log("Content script ready on", location.href);
})();
