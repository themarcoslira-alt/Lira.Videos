# 🧩 ELTON FLOW (extensão do Chrome)

Extensão que **enfileira prompts para geração de imagens em massa** no Google Flow (`labs.google/fx/tools/flow`). Você cola vários prompts, e ela gera um atrás do outro sozinha.

Companheira do **ELTON VIDEO MAKER**.

Feito por **Elton Machado**.
📺 Canal: **https://www.youtube.com/@eltonmachadoIA**

---

## ✅ O que precisa estar instalado

| Item | Observação |
|------|-----------|
| **Google Chrome 114 ou mais novo** | Também funciona em Edge/Brave (base Chromium). |
| **Conta no Google Flow** | https://labs.google/fx/tools/flow |

Não precisa de Python nem nada extra — é só a extensão.

---

## ▶️ Como instalar (modo desenvolvedor)

1. Baixe esta pasta (botão verde **Code → Download ZIP**) e extraia.
2. No Chrome, abra **`chrome://extensions`**.
3. Ligue o **"Modo do desenvolvedor"** (canto superior direito).
4. Clique em **"Carregar sem compactação"** e selecione a pasta extraída.
5. Pronto — clique no ícone do **ELTON FLOW** e abra o painel lateral.

---

## ▶️ Como usar

1. Abra o **Google Flow** (`labs.google/fx/tools/flow`).
2. Clique no ícone do **ELTON FLOW** pra abrir o painel lateral.
3. Cole seus prompts (um por linha) e mande gerar — a extensão dispara um a um.

---

## 🛠️ Deu erro / não aparece?

- **O painel não abre:** confirme que você está numa página do `labs.google/fx/...`.
- **Botões mudaram de lugar e parou de funcionar:** o Google atualizou o site. Os seletores ficam no arquivo `selectors.js`.
- **Sumiu depois de fechar o Chrome:** extensões "sem compactação" precisam da pasta no lugar — não apague a pasta que você carregou.

---

## 📂 Arquivos principais

| Arquivo | O que é |
|---------|---------|
| `manifest.json` | Configuração da extensão |
| `sidepanel/` | O painel lateral (interface) |
| `content.js` | Age dentro da página do Flow |
| `selectors.js` | Onde ficam os botões do Flow |
| `background/service-worker.js` | Cérebro que organiza a fila |

---

💚 Gostou? **Se inscreve no canal:** https://www.youtube.com/@eltonmachadoIA
