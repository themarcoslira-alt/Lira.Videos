/**
 * selectors.js — ELTON FLOW / Lira Studio
 *
 * Único ponto de manutenção dos seletores do Flow.
 *
 * Estratégia em CAMADAS (por ordem de confiança):
 *   Camada 1 — atributo estável: aria-label, role, data-testid
 *   Camada 2 — texto visível do elemento (conteúdo textContent)
 *   Camada 3 — posição CSS / classe (mais frágil, só último recurso)
 *
 * Cada grupo é um array de arrays: [[...camada1], [...camada2], [...camada3]]
 * com rótulos descritivos para log.
 */

globalThis.FLOW_BATCH_DEFAULT_SELECTORS = {

  promptInput: {
    layers: [
      // Camada 1 — atributos estáveis
      [
        'div[role="textbox"][data-slate-editor="true"][contenteditable="true"]',
        'div[contenteditable="true"][data-slate-editor="true"]',
        'div[contenteditable="true"][aria-multiline="true"][role="textbox"]',
        '[data-testid*="prompt" i]',
      ],
      // Camada 2 — tipo de elemento semântico
      ['textarea'],
    ],
    labels: ['aria/role/data-slate', 'textarea-fallback'],
  },

  submitButton: {
    layers: [
      // Camada 1 — aria-label estável
      ['button[aria-label="Create"]', 'button[aria-label*="Create" i]'],
      // Camada 2 — tipo submit
      ['button[type="submit"]'],
      // Camada 3 — conteúdo de ícone / texto (descoberta no content.js)
    ],
    labels: ['aria-label', 'type-submit', 'icon-discovery'],
    // Camada 3 tratada pelo findCreateButtonByArrowIcon/findCreateButtonByHiddenLabel no content.js
  },

  uploadButton: {
    layers: [
      // Camada 1 — aria-label estável
      ['button[aria-label="Add image"]', 'button[aria-label*="Add image" i]', 'button[aria-label*="Upload" i]'],
      // Camada 2 — ícone Material (descoberta no content.js via textContent)
    ],
    labels: ['aria-label', 'icon-discovery'],
  },

  dismissOverlays: {
    layers: [
      ['button[aria-label="Close"]', 'button[aria-label="Dismiss"]'],
    ],
    labels: ['aria-label'],
  },

};

/**
 * findWithLayers(group, label) — tenta cada camada do grupo de seletores,
 * retorna o primeiro elemento encontrado e loga qual camada foi usada.
 *
 * @param {object} group  — objeto com {layers, labels} de FLOW_BATCH_DEFAULT_SELECTORS
 * @param {string} label  — nome do grupo, para identificar o log ([ZAPIFLOW][SELECTOR])
 * @param {function} isOk — função de validação adicional (ex: isVisible). Padrão: sempre ok.
 * @returns {Element|null}
 */
globalThis.findWithLayers = function findWithLayers(group, label, isOk) {
  if (!group || !Array.isArray(group.layers)) return null;
  const ok = typeof isOk === 'function' ? isOk : () => true;

  for (let li = 0; li < group.layers.length; li++) {
    const layerLabel = (group.labels && group.labels[li]) || `camada ${li + 1}`;
    const selectors  = group.layers[li] || [];
    for (const sel of selectors) {
      let el = null;
      try { el = document.querySelector(sel); } catch { continue; }
      if (el && ok(el)) {
        const msg = li === 0
          ? `[ZAPIFLOW][SELECTOR] ${label}: camada 1 (${layerLabel})`
          : `[ZAPIFLOW][SELECTOR] ${label}: FALLBACK camada ${li + 1} (${layerLabel}) — sel: ${sel}`;
        console.log(msg);
        // Notifica o sidepanel para repassar ao Lira (console/log da UI)
        try {
          chrome.runtime.sendMessage({
            type:  'LIRA_SELECTOR_LOG',
            label,
            layer: li + 1,
            layerLabel,
            selector: sel,
            fallback: li > 0,
          });
        } catch { /* ignore — extensão pode estar desconectada */ }
        return el;
      }
    }
  }

  console.warn(`[ZAPIFLOW][SELECTOR] ${label}: FALHOU — todas as camadas esgotadas`);
  try {
    chrome.runtime.sendMessage({
      type:    'LIRA_SELECTOR_LOG',
      label,
      layer:   -1,
      failed:  true,
      fallback: true,
    });
  } catch { /* ignore */ }
  return null;
};
