// Offscreen document: converte a data: URL (mídia baixada no contexto do Flow)
// em um blob URL da própria extensão. O service worker baixa esse blob URL
// pelo chrome.downloads — funciona pra vídeo grande, ao contrário de baixar a
// data: URL direto (que estoura o limite de tamanho).
const vivos = []; // segura os blob URLs pra não serem coletados antes de baixar

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type !== "OFFSCREEN_DATAURL_TO_BLOBURL") return;
  (async () => {
    try {
      const blob = await (await fetch(msg.dataUrl)).blob();
      const blobUrl = URL.createObjectURL(blob);
      vivos.push(blobUrl);
      // libera depois de tempo suficiente pra o download terminar
      setTimeout(() => {
        try { URL.revokeObjectURL(blobUrl); } catch {}
        const i = vivos.indexOf(blobUrl); if (i >= 0) vivos.splice(i, 1);
      }, 120000);
      sendResponse({ ok: true, blobUrl });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // resposta assíncrona
});
