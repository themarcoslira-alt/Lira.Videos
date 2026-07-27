"""
gui.py — Interface gráfica ULTRACUT3 (ttkbootstrap, 9 abas)
Fluxo: Criar Projeto -> Transcricao auto -> Escolha modo -> Pipeline
"""
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import json, os, threading, time, shutil, subprocess
from pathlib import Path
from datetime import datetime

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    TEMA = "litera"; TEM_TTB = True
except ImportError:
    from tkinter import ttk
    TEMA = None; TEM_TTB = False

try:
    from PIL import Image, ImageTk
    TEM_PIL = True
except ImportError:
    TEM_PIL = False

try:
    import requests as _requests
    TEM_REQUESTS = True
except ImportError:
    _requests = None
    TEM_REQUESTS = False

from services.pipeline_service import PipelineService, calcular_duracao_total
from services.library import listar_biblioteca, remover_media
from config import PROJETOS_DIR, OUTPUT_DIR

STEP_NAMES = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"]
STEP_ICONS = {"pendente": " o ", "andamento": " >> ", "concluido": " v ", "erro": " x "}
MODO_AUTO = None  # "auto" ou "manual", definido apos transcricao

# Cores de status usadas tanto no painel global quanto nas sub-abas
COR_STATUS = {"concluido": "#22c55e", "andamento": "#3b82f6", "erro": "#ef4444", "pendente": "#6b7280"}

# Dimensoes padrao de thumbnail (biblioteca / cenas / detalhe)
THUMB_GRID = (168, 94)
THUMB_DETALHE = (440, 260)

APP_VERSION = "v1.0"

# Familia de fonte preferida (Segoe UI no Windows, com fallback multiplataforma)
import tkinter.font as tkfont

def _fonte_disponivel():
    try:
        familias = set(tkfont.families())
    except Exception:
        return "Segoe UI"
    for candidata in ("Segoe UI", "Helvetica", "Arial"):
        if candidata in familias:
            return candidata
    return "TkDefaultFont"

FONTE = None  # definida em tempo de execucao (precisa de root ja criado)

def F(tamanho, peso="normal"):
    """Helper para montar tupla de fonte consistente em todo o app."""
    return (FONTE or "Segoe UI", tamanho, peso) if peso != "normal" else (FONTE or "Segoe UI", tamanho)

# Icones unicode usados em botoes, abas e dialogos
ICO = {
    "app": "🎬", "criar": "🚀", "selecionar": "📂", "deletar": "🗑️",
    "progresso": "📊", "config": "⚙️", "atualizar": "🔄", "auto": "⚡",
    "manual": "🎛️", "biblioteca": "🗂️", "cena": "🖼️", "render": "🎞️",
    "midia": "🌐", "storyboard": "📋", "transcricao": "📝", "ok": "✅",
    "erro": "❌", "aviso": "⚠️", "fechar": "✖️", "info": "ℹ️",
}

# Tags de cor para o console de logs estilo terminal (INFO / SUCCESS / ERROR / WARN)
LOG_TAGS = {
    "info": {"foreground": "#93c5fd"},
    "success": {"foreground": "#4ade80"},
    "error": {"foreground": "#f87171"},
    "warn": {"foreground": "#fbbf24"},
    "muted": {"foreground": "#9ca3af"},
    "timestamp": {"foreground": "#6b7280"},
}


class Ultracut3GUI:
    def __init__(self, root):
        global FONTE
        self.root = root
        self.root.title("ULTRACUT3 - Pipeline de Video")
        self.root.geometry("1280x780")
        self.root.minsize(960, 680)

        FONTE = _fonte_disponivel()
        self.root.rowconfigure(0, weight=0)
        self.root.columnconfigure(0, weight=1)

        self.pipeline = PipelineService()
        self.arquivo_audio = None
        self.pipeline_thread = None
        self.etapas_concluidas = [False] * 5
        self._on_pipeline_end = None
        self._bib_thumb_refs = []
        self._cenas_thumb_refs = []
        self._cenas_data = []
        self._modo_escolhido = None

        self.pipeline.set_progress_callback(self._on_progress)
        self._build_menu()
        self._build_header()
        self._build_progress_global()
        self._build_notebook()
        self._status_bar()
        self._atualizar_lista_projetos()

    def _log_ui_click(self, nome_botao):
        """Loga clique de botao na GUI com categoria UI_CLICK."""
        from services.event_logger import log_event
        log_event("UI_CLICK", nome_botao, level="info")

    def _build_header(self):
        header = ttk.Frame(self.root, padding=(16, 12))
        header.pack(side=tk.TOP, fill=tk.X)
        esquerda = ttk.Frame(header)
        esquerda.pack(side=tk.LEFT, anchor="w")
        titulo_frame = ttk.Frame(esquerda)
        titulo_frame.pack(side=tk.LEFT)
        ttk.Label(titulo_frame, text="%s ULTRACUT3" % ICO["app"],
                  font=(FONTE, 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(titulo_frame, text=APP_VERSION, font=(FONTE, 9, "bold"),
                  foreground="#0ea5e9", padding=(8, 0, 0, 0)).pack(side=tk.LEFT)
        ttk.Label(esquerda, text="Pipeline automatizado de video B-roll",
                  font=(FONTE, 9), foreground="#9ca3af").pack(side=tk.LEFT, padx=(14, 0))
        direita = ttk.Frame(header)
        direita.pack(side=tk.RIGHT, anchor="e")
        self.badge_status = ttk.Label(direita, text="%s Pronto" % ICO["ok"],
                                       font=(FONTE, 9, "bold"), foreground="#22c55e",
                                       background="#14532d", padding=(10, 4))
        self.badge_status.pack(side=tk.RIGHT)
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)

    def _badge(self, texto, tipo="ok"):
        cores = {"ok": ("#22c55e", "#14532d"), "andamento": ("#3b82f6", "#1e3a5f"), "erro": ("#ef4444", "#450a0a")}
        fg, bg = cores.get(tipo, cores["ok"])
        icone = {"ok": ICO["ok"], "andamento": ICO["config"], "erro": ICO["erro"]}.get(tipo, ICO["ok"])
        if hasattr(self, "badge_status"):
            self.badge_status.config(text="%s %s" % (icone, texto), foreground=fg, background=bg)

    def _on_progress(self, step, status, msg):
        self.root.after(0, lambda: self._atualizar_etapa_ui(step, status, msg))

    def _atualizar_etapa_ui(self, step, status, msg):
        cor = COR_STATUS.get(status, "#6b7280")
        icon = STEP_ICONS.get(status, " o ")
        nome = STEP_NAMES[step]
        if status == "andamento":
            self._badge("Executando: %s" % nome, "andamento")
        elif status == "erro":
            self._badge("Erro em %s" % nome, "erro")
        elif status == "concluido" and step == len(STEP_NAMES) - 1:
            self._badge("Pipeline concluido", "ok")
        if hasattr(self, 'prog_status'):
            if status == "andamento":
                self.prog_status.config(text=">> %s: %s" % (nome, msg[:80]), foreground=cor)
            elif status == "concluido":
                self.prog_status.config(text="v %s: %s" % (nome, msg[:80]), foreground=cor)
                self.etapas_concluidas[step] = True
            elif status == "erro":
                self.prog_status.config(text="x %s: %s" % (nome, msg[:80]), foreground=cor)
        if hasattr(self, 'prog_log'):
            tag = {"andamento": "info", "concluido": "success", "erro": "error"}.get(status, "info")
            self._log_terminal(self.prog_log, nome, msg[:100], tag)
        if hasattr(self, '_global_etapa_labels'):
            chip = self._global_etapa_labels[step]
            chip.config(text="%s%s" % (icon, nome), foreground=cor)
            concluidas = sum(1 for c in self.etapas_concluidas if c)
            self.global_progress_var.set(concluidas / len(STEP_NAMES) * 100)
            if status == "andamento":
                self.global_status_label.config(text=msg[:90], foreground=cor)
            elif status == "erro":
                self.global_status_label.config(text="Erro em %s: %s" % (nome, msg[:70]), foreground=cor)

    def _console_key_filter(self, event):
        """Filtro de teclas: bloqueia edicao mas permite Ctrl+C e Ctrl+A."""
        # Permite Ctrl+C (copiar), Ctrl+A (selecionar tudo), Ctrl+Insert (copiar)
        if event.state & 0x0004 and event.keysym in ("c", "C", "a", "A", "Insert"):
            return None
        # Permite setas, PageUp/PageDown, Home, End, Delete (sendo read-only, delete nao faz mal)
        if event.keysym in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            return None
        # Bloqueia qualquer outra tecla (edicao)
        return "break"

    def _log_terminal(self, widget, categoria, msg, tag="info"):
        hora = datetime.now().strftime("%H:%M:%S")
        widget.insert(tk.END, "[%s] " % hora, "timestamp")
        widget.insert(tk.END, "[%s] " % categoria, "muted")
        widget.insert(tk.END, "%s\n" % msg, tag)
        widget.see(tk.END)

    def _build_progress_global(self):
        self.global_frame = ttk.Frame(self.root, padding=(10, 8))
        self.global_frame.pack(side=tk.TOP, fill=tk.X)
        chips_frame = ttk.Frame(self.global_frame)
        chips_frame.pack(anchor="center")
        self._global_etapa_labels = []
        for nome in STEP_NAMES:
            chip = ttk.Label(chips_frame, text="o %s" % nome, font=(FONTE, 9),
                              foreground=COR_STATUS["pendente"], padding=(8, 3))
            chip.pack(side=tk.LEFT, padx=3)
            self._global_etapa_labels.append(chip)
        self.global_progress_var = tk.DoubleVar(value=0)
        self.global_progress_bar = ttk.Progressbar(self.global_frame, variable=self.global_progress_var,
                                                    maximum=100, length=440, mode="determinate")
        self.global_progress_bar.pack(pady=(6, 2))
        self.global_status_label = ttk.Label(self.global_frame, text="Nenhum pipeline em execucao",
                                              font=(FONTE, 9), foreground="#6b7280")
        self.global_status_label.pack(anchor="center")
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)

    def _resetar_progresso_global(self):
        self.etapas_concluidas = [False] * 5
        self.global_progress_var.set(0)
        self.global_status_label.config(text="Iniciando pipeline...", foreground=COR_STATUS["andamento"])
        for i, chip in enumerate(self._global_etapa_labels):
            chip.config(text="o %s" % STEP_NAMES[i], foreground=COR_STATUS["pendente"])

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        fm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Arquivo", menu=fm)
        fm.add_command(label="%s Novo Projeto" % ICO["criar"],
                        command=lambda: self._log_ui_click("Novo Projeto") or self._novo_projeto_rapido())
        fm.add_separator()
        fm.add_command(label="%s Sair" % ICO["fechar"], command=self.root.quit)

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.tab_projeto = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_projeto, text="%s Projeto" % ICO["criar"])
        self._build_tab_projeto()
        self.tab_progresso = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_progresso, text="%s Progresso" % ICO["progresso"])
        self._build_tab_progresso()
        self.tab_resultados = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_resultados, text="%s Resultados" % ICO["storyboard"])
        self._build_tab_resultados()
        self.tab_biblioteca = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_biblioteca, text="%s Biblioteca" % ICO["biblioteca"])
        self._build_tab_biblioteca()
        self.tab_config = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_config, text="%s Configuracoes" % ICO["config"])
        self._build_tab_config()

    def _status_bar(self):
        self.status_frame = ttk.Frame(self.root, padding=(10, 4))
        self.status_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.status_label = ttk.Label(self.status_frame, text="%s Pronto" % ICO["info"], anchor=tk.W,
                                       font=(FONTE, 9), foreground="#9ca3af")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.spinner = ttk.Progressbar(self.status_frame, mode='indeterminate', length=150)
        self.spinner.pack(side=tk.RIGHT, padx=5)

    # ==================== ABA PROJETO ====================
    def _build_tab_projeto(self):
        frame = self.tab_projeto
        card_criar = ttk.Labelframe(frame, text=" %s  Criar Projeto " % ICO["criar"], padding=18)
        card_criar.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(card_criar, text="Nome do Projeto:", font=(FONTE, 10)).pack(anchor=tk.W, pady=(2, 4))
        self.entry_nome = ttk.Entry(card_criar, width=50)
        self.entry_nome.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(card_criar, text="Selecionar Audio:", font=(FONTE, 10)).pack(anchor=tk.W)
        audio_frame = ttk.Frame(card_criar)
        audio_frame.pack(fill=tk.X, pady=5)
        self.entry_audio = ttk.Entry(audio_frame, width=50)
        self.entry_audio.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(audio_frame, text="%s Escolher arquivo..." % ICO["selecionar"],
                   command=lambda: self._log_ui_click("Escolher Arquivo") or self._selecionar_audio(),
                   **({"bootstyle": "info-outline"} if TEM_TTB else {})).pack(side=tk.RIGHT, padx=5)
        ttk.Button(audio_frame, text="Limpar",
                   command=lambda: self._limpar_audio(),
                   **({"bootstyle": "secondary"} if TEM_TTB else {})).pack(side=tk.RIGHT, padx=5)
        self.status_criar = ttk.Label(card_criar, text="", font=(FONTE, 9), foreground="#9ca3af")
        self.status_criar.pack(anchor=tk.W, pady=(8, 4))
        self.btn_criar = ttk.Button(card_criar, text="%s  CRIAR PROJETO" % ICO["criar"],
                                     command=lambda: self._log_ui_click("CRIAR PROJETO") or self._criar_projeto_fluxo(),
                                     **({"bootstyle": "success"} if TEM_TTB else {}), width=30)
        self.btn_criar.pack(pady=(6, 2))

        card_lista = ttk.Labelframe(frame, text=" %s  Projetos Existentes " % ICO["biblioteca"], padding=18)
        card_lista.pack(fill=tk.BOTH, expand=True)
        cab = ttk.Frame(card_lista)
        cab.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cab, text="Nome", font=(FONTE, 9, "bold"), width=35).pack(side=tk.LEFT)
        ttk.Label(cab, text="Status", font=(FONTE, 9, "bold"), width=20).pack(side=tk.LEFT)
        ttk.Label(cab, text="Acao", font=(FONTE, 9, "bold"), width=10).pack(side=tk.LEFT)
        lista_container = ttk.Frame(card_lista)
        lista_container.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(lista_container)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.lista_projetos = tk.Listbox(lista_container, height=8, yscrollcommand=scroll.set,
                                          font=("Consolas", 10))
        self.lista_projetos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.lista_projetos.yview)
        btn_lista = ttk.Frame(card_lista)
        btn_lista.pack(fill=tk.X, pady=(10, 2))
        ttk.Button(btn_lista, text="%s Selecionar Projeto" % ICO["selecionar"],
                   command=lambda: self._log_ui_click("Selecionar Projeto") or self._selecionar_projeto_lista()).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_lista, text="%s DEL" % ICO["deletar"],
                   command=lambda: self._log_ui_click("DEL Projeto") or self._deletar_projeto_confirmado(),
                   **({"bootstyle": "danger-outline"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_lista, text="%s Atualizar Lista" % ICO["atualizar"],
                   command=lambda: self._log_ui_click("Atualizar Lista") or self._atualizar_lista_projetos()).pack(side=tk.LEFT, padx=4)
        self.projeto_info = ttk.Label(card_lista, text="", foreground="#9ca3af", font=(FONTE, 9))
        self.projeto_info.pack(anchor=tk.W, pady=(8, 0))

    def _limpar_audio(self):
        self._log_ui_click("Limpar Audio")
        self.entry_audio.delete(0, tk.END)
        self.arquivo_audio = None

    # ==================== ABA PROGRESSO ====================
    def _build_tab_progresso(self):
        frame = self.tab_progresso
        ttk.Label(frame, text="ACOMPANHAMENTO EM TEMPO REAL", font=(FONTE, 14, "bold")).pack(anchor=tk.W)
        self.prog_projeto = ttk.Label(frame, text="", font=(FONTE, 11))
        self.prog_projeto.pack(anchor=tk.W, pady=5)
        self.prog_status = ttk.Label(frame, text="Aguardando inicio do pipeline...",
                                     font=(FONTE, 12), foreground="gray")
        self.prog_status.pack(anchor=tk.W, pady=10)
        self.prog_var = tk.DoubleVar(value=0)
        self.prog_bar = ttk.Progressbar(frame, variable=self.prog_var, maximum=100, length=500, mode='determinate')
        self.prog_bar.pack(fill=tk.X, pady=5)
        self.prog_etapas = ttk.Frame(frame)
        self.prog_etapas.pack(fill=tk.X, pady=10)
        self._etapa_labels = []
        for i, nome in enumerate(STEP_NAMES):
            lb = ttk.Label(self.prog_etapas, text="o %s" % nome, font=(FONTE, 10), foreground="#6b7280")
            lb.pack(side=tk.LEFT, padx=8)
            self._etapa_labels.append(lb)
        self.prog_tempo = ttk.Label(frame, text="Tempo decorrido: 00:00", font=(FONTE, 10), foreground="#6b7280")
        self.prog_tempo.pack(anchor=tk.W, pady=5)
        self._tempo_inicio = None
        self._tempo_timer = None
        card_metricas = ttk.Labelframe(frame, text=" %s  Metricas do Pipeline " % ICO["info"], padding=10)
        card_metricas.pack(fill=tk.X, pady=(0, 8))
        self.metrics_widgets = {}
        metricas_def = [
            ("Cobertura", "scene_coverage", "---"), ("Midias GREEN", "green_count", "---"),
            ("Total Cenas", "total_cenas", "---"), ("Queries/Cena", "queries_media", "---"),
            ("Status Claude", "claude_status", "---"), ("Status APIs", "api_status", "---"),
        ]
        _metrics_inner = ttk.Frame(card_metricas)
        _metrics_inner.pack(fill=tk.X)
        for i, (rotulo, chave, padrao) in enumerate(metricas_def):
            row_idx, col_idx = divmod(i, 3)
            if col_idx == 0:
                _row_f = ttk.Frame(_metrics_inner)
                _row_f.pack(fill=tk.X, pady=1)
            lb = ttk.Label(_row_f, text="%s: " % rotulo, font=(FONTE, 9), foreground="#9ca3af")
            val = ttk.Label(_row_f, text=padrao, font=(FONTE, 9, "bold"), foreground="#22c55e")
            lb.pack(side=tk.LEFT, padx=(col_idx * 15, 0))
            val.pack(side=tk.LEFT)
            self.metrics_widgets[chave] = val
        card_log = ttk.Labelframe(frame, text=" %s  Console de Execucao " % ICO["config"], padding=12)
        card_log.pack(fill=tk.BOTH, expand=True, pady=(15, 0))
        self.prog_log = scrolledtext.ScrolledText(
            card_log, height=10, wrap=tk.WORD, font=("Consolas", 10),
            background="#0d1117", foreground="#c9d1d9", insertbackground="#c9d1d9",
            borderwidth=0, padx=10, pady=8
        )
        self.prog_log.pack(fill=tk.BOTH, expand=True)
        self.prog_log.bind("<Key>", self._console_key_filter)
        self.prog_log.bind("<Button-3>", lambda e: "break")
        for nome_tag, cfg in LOG_TAGS.items():
            self.prog_log.tag_configure(nome_tag, **cfg)
        self._ultimo_log_idx = 0
        self._iniciar_polling_logs()

    def _iniciar_polling_logs(self):
        def poll():
            try:
                from services.event_logger import ler_eventos
                # Le apenas as ultimas 100 linhas (suficiente para pegar novos eventos)
                # NUNCA le tudo para nao travar a thread principal
                todos = ler_eventos(linhas=100)
                total_atual = len(todos)
                if self._ultimo_log_idx < total_atual:
                    novos = todos[self._ultimo_log_idx:]
                    for evt in novos:
                        categoria = evt.get("category", "")
                        msg = evt.get("message", "")
                        level = evt.get("level", "INFO").lower()
                        tag = {"warn": "warn", "error": "error", "info": "info"}.get(level, "info")
                        if any(x in msg for x in ["GREEN", "concluido", "concluida", "Renderizado", "salvo"]):
                            tag = "success"
                        self._log_terminal(self.prog_log, categoria, msg, tag)
                    self._ultimo_log_idx = total_atual
            except Exception:
                pass
            self.root.after(500, poll)
        # Delay inicial de 2s para nao travar no startup
        self.root.after(2000, poll)

    def _resetar_polling(self):
        from services.event_logger import ler_eventos
        self._ultimo_log_idx = len(ler_eventos(linhas=99999))
        if hasattr(self, 'prog_log'):
            self.prog_log.delete(1.0, tk.END)

    def _iniciar_temporizador(self):
        self._tempo_inicio = time.time()
        def atualizar():
            if self._tempo_inicio:
                dec = time.time() - self._tempo_inicio
                mins = int(dec // 60)
                secs = int(dec % 60)
                self.prog_tempo.config(text="Tempo decorrido: %02d:%02d" % (mins, secs))
                self._tempo_timer = self.root.after(1000, atualizar)
        atualizar()

    def _parar_temporizador(self):
        if self._tempo_timer:
            self.root.after_cancel(self._tempo_timer)
            self._tempo_timer = None

    # ==================== ABA RESULTADOS ====================
    def _build_tab_resultados(self):
        frame = self.tab_resultados
        self.sub_notebook = ttk.Notebook(frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)
        def _console(parent):
            txt = scrolledtext.ScrolledText(parent, height=15, wrap=tk.WORD, font=("Consolas", 10),
                background="#0d1117", foreground="#c9d1d9", insertbackground="#c9d1d9",
                borderwidth=0, padx=10, pady=8)
            txt.pack(fill=tk.BOTH, expand=True)
            return txt
        tab_trans = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_trans, text="%s Transcricao" % ICO["transcricao"])
        self.texto_trans = _console(tab_trans)
        tab_cenas = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_cenas, text="%s Cenas" % ICO["cena"])
        cenas_canvas_frame = ttk.Frame(tab_cenas)
        cenas_canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.cenas_canvas = tk.Canvas(cenas_canvas_frame, highlightthickness=0)
        cenas_scroll = ttk.Scrollbar(cenas_canvas_frame, orient="vertical", command=self.cenas_canvas.yview)
        self.cenas_grid = ttk.Frame(self.cenas_canvas)
        self.cenas_grid.bind("<Configure>", lambda e: self.cenas_canvas.configure(scrollregion=self.cenas_canvas.bbox("all")))
        self.cenas_canvas.create_window((0, 0), window=self.cenas_grid, anchor="nw")
        self.cenas_canvas.configure(yscrollcommand=cenas_scroll.set)
        self.cenas_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cenas_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._habilitar_scroll_mouse(self.cenas_canvas)
        tab_story = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_story, text="%s Storyboard" % ICO["storyboard"])
        self.texto_story = _console(tab_story)
        tab_midias = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_midias, text="%s Midias" % ICO["midia"])
        self.texto_midias = _console(tab_midias)
        tab_render = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_render, text="%s Render" % ICO["render"])
        self.texto_render = _console(tab_render)

    # ==================== ABA BIBLIOTECA ====================
    def _build_tab_biblioteca(self):
        frame = self.tab_biblioteca
        header = ttk.Frame(frame)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="%s Biblioteca de Midia" % ICO["biblioteca"],
                  font=(FONTE, 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="%s Atualizar" % ICO["atualizar"],
                   command=lambda: self._log_ui_click("Biblioteca Atualizar") or self._atualizar_biblioteca(),
                   **({"bootstyle": "info-outline"} if TEM_TTB else {})).pack(side=tk.RIGHT)
        if not TEM_PIL:
            ttk.Label(frame, text="Aviso: Pillow (PIL) nao instalado — thumbnails desativados. "
                                   "Rode: pip install Pillow",
                      font=(FONTE, 9), foreground="#f59e0b").pack(anchor=tk.W, pady=(0, 8))
        canvas_frame = ttk.Frame(frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.bib_canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        bib_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.bib_canvas.yview)
        self.bib_grid = ttk.Frame(self.bib_canvas)
        self.bib_grid.bind("<Configure>", lambda e: self.bib_canvas.configure(scrollregion=self.bib_canvas.bbox("all")))
        self.bib_canvas.create_window((0, 0), window=self.bib_grid, anchor="nw")
        self.bib_canvas.configure(yscrollcommand=bib_scroll.set)
        self.bib_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bib_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._habilitar_scroll_mouse(self.bib_canvas)

    # ==================== ABA CONFIGURACOES ====================
    def _build_tab_config(self):
        frame = self.tab_config
        ttk.Label(frame, text="%s  Configuracao de APIs" % ICO["config"],
                  font=(FONTE, 16, "bold")).pack(anchor=tk.W, pady=(0, 15))

        card_anthropic = ttk.Labelframe(frame, text=" %s Anthropic / Claude " % ICO["auto"], padding=15)
        card_anthropic.pack(fill=tk.X, pady=(0, 10))
        row_anthropic = ttk.Frame(card_anthropic)
        row_anthropic.pack(fill=tk.X)
        self._anthropic_status = ttk.Label(row_anthropic, text="\u25cf Nao configurado", font=(FONTE, 9), foreground="#6b7280")
        self._anthropic_status.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row_anthropic, text="API Key:", font=(FONTE, 9)).pack(side=tk.LEFT)
        self._anthropic_entry = ttk.Entry(row_anthropic, width=50, show="*")
        self._anthropic_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._anthropic_btn_testar = ttk.Button(row_anthropic, text="Testar",
                                                 command=lambda: self._log_ui_click("Testar Anthropic") or self._testar_anthropic(),
                                                 **({"bootstyle": "info"} if TEM_TTB else {}))
        self._anthropic_btn_testar.pack(side=tk.LEFT, padx=5)
        self._anthropic_btn_salvar = ttk.Button(row_anthropic, text="Salvar",
                                                 command=lambda: self._log_ui_click("Salvar Anthropic") or self._salvar_chave("ANTHROPIC_API_KEY", self._anthropic_entry),
                                                 **({"bootstyle": "success"} if TEM_TTB else {}))
        self._anthropic_btn_salvar.pack(side=tk.LEFT, padx=2)
        ttk.Label(card_anthropic, text="Modelo: claude-3-sonnet-20241022", font=(FONTE, 8), foreground="#9ca3af").pack(anchor=tk.W, pady=(4, 0))

        card_pexels = ttk.Labelframe(frame, text=" %s Pexels " % ICO["midia"], padding=15)
        card_pexels.pack(fill=tk.X, pady=(0, 10))
        row_pexels = ttk.Frame(card_pexels)
        row_pexels.pack(fill=tk.X)
        self._pexels_status = ttk.Label(row_pexels, text="\u25cf Nao configurado", font=(FONTE, 9), foreground="#6b7280")
        self._pexels_status.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row_pexels, text="API Key:", font=(FONTE, 9)).pack(side=tk.LEFT)
        self._pexels_entry = ttk.Entry(row_pexels, width=50, show="*")
        self._pexels_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row_pexels, text="Testar",
                   command=lambda: self._log_ui_click("Testar Pexels") or self._testar_pexels(),
                   **({"bootstyle": "info"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_pexels, text="Salvar",
                   command=lambda: self._log_ui_click("Salvar Pexels") or self._salvar_chave("PEXELS_API_KEY", self._pexels_entry),
                   **({"bootstyle": "success"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=2)

        card_pixabay = ttk.Labelframe(frame, text=" %s Pixabay " % ICO["midia"], padding=15)
        card_pixabay.pack(fill=tk.X, pady=(0, 10))
        row_pixabay = ttk.Frame(card_pixabay)
        row_pixabay.pack(fill=tk.X)
        self._pixabay_status = ttk.Label(row_pixabay, text="\u25cf Nao configurado", font=(FONTE, 9), foreground="#6b7280")
        self._pixabay_status.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row_pixabay, text="API Key:", font=(FONTE, 9)).pack(side=tk.LEFT)
        self._pixabay_entry = ttk.Entry(row_pixabay, width=50, show="*")
        self._pixabay_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row_pixabay, text="Testar",
                   command=lambda: self._log_ui_click("Testar Pixabay") or self._testar_pixabay(),
                   **({"bootstyle": "info"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_pixabay, text="Salvar",
                   command=lambda: self._log_ui_click("Salvar Pixabay") or self._salvar_chave("PIXABAY_API_KEY", self._pixabay_entry),
                   **({"bootstyle": "success"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=2)

        card_unsplash = ttk.Labelframe(frame, text=" %s Unsplash " % ICO["midia"], padding=15)
        card_unsplash.pack(fill=tk.X, pady=(0, 10))
        row_unsplash = ttk.Frame(card_unsplash)
        row_unsplash.pack(fill=tk.X)
        self._unsplash_status = ttk.Label(row_unsplash, text="\u25cf Nao configurado", font=(FONTE, 9), foreground="#6b7280")
        self._unsplash_status.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row_unsplash, text="API Key:", font=(FONTE, 9)).pack(side=tk.LEFT)
        self._unsplash_entry = ttk.Entry(row_unsplash, width=50, show="*")
        self._unsplash_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row_unsplash, text="Testar",
                   command=lambda: self._log_ui_click("Testar Unsplash") or self._testar_unsplash(),
                   **({"bootstyle": "info"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_unsplash, text="Salvar",
                   command=lambda: self._log_ui_click("Salvar Unsplash") or self._salvar_chave("UNSPLASH_API_KEY", self._unsplash_entry),
                   **({"bootstyle": "success"} if TEM_TTB else {})).pack(side=tk.LEFT, padx=2)
        self._carregar_chaves_gui()

    def _carregar_chaves_gui(self):
        try:
            import config_local as _local
            for attr, entry in [
                ("ANTHROPIC_API_KEY", "_anthropic_entry"),
                ("PEXELS_API_KEY", "_pexels_entry"),
                ("PIXABAY_API_KEY", "_pixabay_entry"),
                ("UNSPLASH_API_KEY", "_unsplash_entry")
            ]:
                if hasattr(_local, attr) and getattr(_local, attr):
                    getattr(self, entry).delete(0, tk.END)
                    getattr(self, entry).insert(0, getattr(_local, attr))
        except ImportError:
            pass
        self._atualizar_status_apis()

    def _atualizar_status_apis(self):
        from config import ANTHROPIC_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY
        for prefix, var in [("_anthropic", "ANTHROPIC"), ("_pexels", "PEXELS"),
                             ("_pixabay", "PIXABAY"), ("_unsplash", "UNSPLASH")]:
            status_widget = getattr(self, f"{prefix}_status")
            if var == "ANTHROPIC":
                chave = ANTHROPIC_API_KEY
            elif var == "PEXELS":
                chave = PEXELS_API_KEY
            elif var == "PIXABAY":
                chave = PIXABAY_API_KEY
            else:
                chave = UNSPLASH_API_KEY
            if chave:
                status_widget.config(text="\u25cf Configurado", foreground="#22c55e")
            else:
                status_widget.config(text="\u25cf Nao configurado", foreground="#6b7280")
        if hasattr(self, 'metrics_widgets'):
            self.root.after(100, self._atualizar_metricas_apis)

    def _atualizar_metricas_apis(self):
        from config import ANTHROPIC_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY
        if 'api_status' in self.metrics_widgets:
            apis_ok = sum(1 for k in [PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY] if k)
            self.metrics_widgets['api_status'].config(
                text="%d/3 config" % apis_ok, foreground="#22c55e" if apis_ok > 0 else "#6b7280")
        if 'claude_status' in self.metrics_widgets:
            self.metrics_widgets['claude_status'].config(
                text="OK" if ANTHROPIC_API_KEY else "Nao config",
                foreground="#22c55e" if ANTHROPIC_API_KEY else "#f59e0b")

    def _salvar_chave(self, nome_var, entry_widget):
        self._log_ui_click("Salvar %s" % nome_var)
        chave = entry_widget.get().strip()
        if not chave:
            messagebox.showwarning("Aviso", "Digite uma chave antes de salvar")
            return
        from config import BASE_DIR
        cfg_path = BASE_DIR / "config_local.py"
        linhas = []
        encontrado = False
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{nome_var} ="):
                        linhas.append(f'{nome_var} = "{chave}"\n')
                        encontrado = True
                    else:
                        linhas.append(line)
        if not encontrado:
            linhas.append(f'\n{nome_var} = "{chave}"\n')
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.writelines(linhas)
        from config import recarregar_chaves
        recarregar_chaves()
        self._atualizar_status_apis()
        self.status_label.config(text="Chave %s salva!" % nome_var)

    def _testar_api(self, nome, endpoint, headers_func=None):
        if not TEM_REQUESTS:
            from services.event_logger import log_event
            log_event("SYSTEM", f"Teste {nome}: requests nao instalado", level="error")
            return False, "requests ausente"
        from services.event_logger import log_event
        try:
            if headers_func:
                resp = _requests.get(endpoint, headers=headers_func(), timeout=10)
            else:
                resp = _requests.get(endpoint, timeout=10)
            if 200 <= resp.status_code < 300:
                log_event("SYSTEM", f"Teste {nome}: OK (status {resp.status_code})", level="info")
                return True, f"OK ({resp.status_code})"
            else:
                erro_texto = resp.text[:100] if resp.text else "sem resposta"
                log_event("SYSTEM", f"Teste {nome}: ERRO (status {resp.status_code}) - {erro_texto}", level="error")
                return False, f"Erro {resp.status_code}: {erro_texto[:40]}"
        except _requests.exceptions.RequestException as e:
            log_event("SYSTEM", f"Teste {nome}: FALHA - {str(e)[:60]}", level="error")
            return False, str(e)[:40]

    def _testar_anthropic(self):
        from config import ANTHROPIC_API_KEY
        from services.event_logger import log_event
        if not ANTHROPIC_API_KEY:
            messagebox.showwarning("Aviso", "Configure a chave Anthropic primeiro")
            return
        if not TEM_REQUESTS:
            messagebox.showwarning("Aviso", "Biblioteca requests nao instalada. Execute: pip install requests")
            return
        log_event("SYSTEM", "Teste Anthropic: iniciando...", level="info")
        def task():
            self._anthropic_btn_testar.config(state="disabled")
            self._anthropic_status.config(text="\u25cf Testando...", foreground="#3b82f6")
            try:
                log_event("SYSTEM", "Teste Anthropic: enviando POST para api.anthropic.com...", level="info")
                resp = _requests.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 10, "messages": [{"role": "user", "content": "oi"}]},
                    timeout=15)
                log_event("SYSTEM", f"Teste Anthropic: status HTTP {resp.status_code}", level="info")
                if resp.status_code == 200:
                    self._anthropic_status.config(text="\u25cf Conectado", foreground="#22c55e")
                    log_event("SYSTEM", "Teste Anthropic: SUCESSO - API respondendo", level="info")
                    self.root.after(0, lambda: messagebox.showinfo("Sucesso", "Anthropic API respondendo corretamente!"))
                elif resp.status_code == 401:
                    erro = resp.json().get("error", {}).get("message", "Token invalido")
                    self._anthropic_status.config(text="\u25cf %s" % erro[:35], foreground="#ef4444")
                    log_event("SYSTEM", f"Teste Anthropic: FALHA - {erro}", level="error")
                else:
                    erro = resp.json().get("error", {}).get("message", str(resp.status_code))
                    self._anthropic_status.config(text="\u25cf %s" % erro[:35], foreground="#ef4444")
                    log_event("SYSTEM", f"Teste Anthropic: ERRO - {erro}", level="error")
            except Exception as e:
                self._anthropic_status.config(text="\u25cf Falha: %s" % str(e)[:30], foreground="#ef4444")
                log_event("SYSTEM", f"Teste Anthropic: EXCESSAO - {str(e)[:80]}", level="error")
            finally:
                self._anthropic_btn_testar.config(state="normal")
        threading.Thread(target=task, daemon=True).start()

    def _testar_pexels(self):
        from config import PEXELS_API_KEY
        if not PEXELS_API_KEY:
            messagebox.showwarning("Aviso", "Configure a chave Pexels primeiro")
            return
        if not TEM_REQUESTS:
            messagebox.showwarning("Aviso", "Biblioteca requests nao instalada.")
            return
        def task():
            self._pexels_status.config(text="\u25cf Testando...", foreground="#3b82f6")
            ok, msg = self._testar_api("Pexels",
                "https://api.pexels.com/v1/search?query=nature&per_page=1",
                lambda: {"Authorization": PEXELS_API_KEY})
            self._pexels_status.config(text="\u25cf %s" % ("Conectado" if ok else msg), foreground="#22c55e" if ok else "#ef4444")
        threading.Thread(target=task, daemon=True).start()

    def _testar_pixabay(self):
        from config import PIXABAY_API_KEY
        if not PIXABAY_API_KEY:
            messagebox.showwarning("Aviso", "Configure a chave Pixabay primeiro")
            return
        if not TEM_REQUESTS:
            messagebox.showwarning("Aviso", "Biblioteca requests nao instalada.")
            return
        def task():
            self._pixabay_status.config(text="\u25cf Testando...", foreground="#3b82f6")
            ok, msg = self._testar_api("Pixabay",
                f"https://pixabay.com/api/?key={PIXABAY_API_KEY}&q=nature&per_page=3&image_type=photo")
            self._pixabay_status.config(text="\u25cf %s" % ("Conectado" if ok else msg), foreground="#22c55e" if ok else "#ef4444")
        threading.Thread(target=task, daemon=True).start()

    def _testar_unsplash(self):
        from config import UNSPLASH_API_KEY
        if not UNSPLASH_API_KEY:
            messagebox.showwarning("Aviso", "Configure a chave Unsplash primeiro")
            return
        if not TEM_REQUESTS:
            messagebox.showwarning("Aviso", "Biblioteca requests nao instalada.")
            return
        def task():
            self._unsplash_status.config(text="\u25cf Testando...", foreground="#3b82f6")
            ok, msg = self._testar_api("Unsplash",
                f"https://api.unsplash.com/search/photos?query=nature&per_page=1",
                lambda: {"Authorization": f"Client-ID {UNSPLASH_API_KEY}"})
            self._unsplash_status.config(text="\u25cf %s" % ("Conectado" if ok else msg), foreground="#22c55e" if ok else "#ef4444")
        threading.Thread(target=task, daemon=True).start()

    def _habilitar_scroll_mouse(self, canvas):
        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def _obter_poster_video(self, video_path):
        video_path = Path(video_path)
        cache_dir = video_path.parent / "_thumbs"
        try:
            cache_dir.mkdir(exist_ok=True)
        except Exception:
            return None
        poster_path = cache_dir / (video_path.stem + "_poster.jpg")
        if poster_path.exists():
            return poster_path
        try:
            subprocess.run(["ffmpeg", "-y", "-ss", "00:00:00.5", "-i", str(video_path),
                           "-frames:v", "1", "-vf", "scale=320:-1", str(poster_path)],
                          capture_output=True, timeout=15)
        except Exception:
            return None
        return poster_path if poster_path.exists() else None

    def _carregar_thumbnail(self, caminho, tamanho=THUMB_GRID):
        if not TEM_PIL or not caminho:
            return None
        path = Path(caminho)
        if not path.exists():
            return None
        ext = path.suffix.lower()
        try:
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                img = Image.open(path)
                img.thumbnail(tamanho)
                return ImageTk.PhotoImage(img)
            if ext in (".mp4", ".webm", ".mov", ".mkv"):
                poster = self._obter_poster_video(path)
                if poster:
                    img = Image.open(poster)
                    img.thumbnail(tamanho)
                    return ImageTk.PhotoImage(img)
        except Exception:
            return None
        return None

    def _criar_card_midia(self, parent, entry):
        caminho = entry.get("arquivo") or entry.get("path") or entry.get("file") or ""
        card = ttk.Frame(parent, relief="solid", borderwidth=1, padding=6)
        card.grid_propagate(False)
        card.configure(width=186, height=158)
        thumb = self._carregar_thumbnail(caminho)
        if thumb:
            lbl_img = ttk.Label(card, image=thumb)
            lbl_img.image = thumb
            self._bib_thumb_refs.append(thumb)
        else:
            lbl_img = ttk.Label(card, text="sem preview", width=22, anchor="center",
                                 font=(FONTE, 9), foreground="#6b7280")
        lbl_img.pack(pady=(0, 6))
        fonte = str(entry.get("source", "?"))
        qualidade = str(entry.get("quality", "?"))
        largura = entry.get("width", 0)
        altura = entry.get("height", 0)
        cor_q = COR_STATUS["concluido"] if qualidade == "green" else COR_STATUS["erro"]
        ttk.Label(card, text=fonte.capitalize(), font=(FONTE, 9, "bold")).pack(anchor="w")
        ttk.Label(card, text="%sx%s" % (largura, altura), font=(FONTE, 8), foreground="#9ca3af").pack(anchor="w")
        ttk.Label(card, text=qualidade.upper(), font=(FONTE, 8, "bold"), foreground=cor_q).pack(anchor="w")
        return card

    # ==================== METODOS PRINCIPAIS ====================
    def _selecionar_audio(self):
        f = filedialog.askopenfilename(title="Selecionar Audio",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                       ("Video", "*.mp4"), ("Todos", "*.*")])
        if f:
            self.entry_audio.delete(0, tk.END)
            self.entry_audio.insert(0, f)
            self.arquivo_audio = f
            self.status_criar.config(text="Audio selecionado: %s" % Path(f).name, foreground="#198754")

    def _criar_projeto_fluxo(self):
        nome = self.entry_nome.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Digite um nome para o projeto")
            return
        if not self.arquivo_audio:
            messagebox.showwarning("Aviso", "Selecione um arquivo de audio")
            return
        r = self.pipeline.criar_projeto(nome, self.arquivo_audio)
        if not r.get("success"):
            messagebox.showerror("Erro", r.get("error", "Erro ao criar"))
            return
        self.projeto_info.config(text="Projeto atual: %s" % nome)
        self.status_label.config(text="Projeto '%s' criado e selecionado" % nome)
        self._atualizar_lista_projetos()
        self._resetar_progresso_global()
        self.notebook.select(self.tab_progresso)
        self.prog_projeto.config(text="Projeto: %s  |  Audio: %s" % (nome, Path(self.arquivo_audio).name))
        self.prog_status.config(text=">> Transcrevendo audio...", foreground="#0d6efd")
        self._iniciar_temporizador()
        self._etapa_labels[0].config(text=">> Transcricao", foreground="#0d6efd")
        self.spinner.start()
        def task():
            audio_path = self._extrair_audio_se_video(self.arquivo_audio)
            self.pipeline._notify(0, "andamento", "Transcrevendo audio...")
            r1 = self.pipeline.transcrever(audio_path)
            if not r1.get("success"):
                self.root.after(0, lambda: self._erro_etapa(0, r1.get("error", "Erro na transcricao")))
                self.spinner.stop()
                return
            self.root.after(0, lambda: self._transcricao_concluida(r1))
        threading.Thread(target=task, daemon=True).start()

    def _transcricao_concluida(self, result):
        self._etapa_labels[0].config(text="v Transcricao", foreground="#198754")
        self.texto_trans.delete(1.0, tk.END)
        self.texto_trans.insert(tk.END, result.get("texto", ""))
        self.spinner.stop()
        self._mostrar_escolha_modo()

    def _mostrar_escolha_modo(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Escolher Modo de Execucao")
        dialog.geometry("640x420")
        dialog.minsize(560, 400)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="%s CONFIGURACAO DO PIPELINE" % ICO["config"], font=(FONTE, 14, "bold")).pack(pady=(20, 4))
        ttk.Label(dialog, text="%s Transcricao concluida" % ICO["ok"], font=(FONTE, 10), foreground="#22c55e").pack()
        frame_opcoes = ttk.Frame(dialog, padding=(20, 18))
        frame_opcoes.pack(fill=tk.BOTH, expand=True)
        frame_opcoes.columnconfigure(0, weight=1, uniform="cards")
        frame_opcoes.columnconfigure(1, weight=1, uniform="cards")
        frame_opcoes.rowconfigure(0, weight=1)
        modo_var = tk.StringVar(value="auto")
        cards = {}
        def _selecionar(modo):
            modo_var.set(modo)
            for key, card in cards.items():
                if key == modo:
                    card.configure(highlightbackground="#22c55e", highlightthickness=2)
                else:
                    card.configure(highlightbackground="#3a3f4b", highlightthickness=1)
        def _criar_card(parent, col, modo, icone, titulo, descricao, cor):
            card = tk.Frame(parent, bg="#1e222b", highlightthickness=1, highlightbackground="#3a3f4b", bd=0, cursor="hand2")
            card.grid(row=0, column=col, sticky="nsew", padx=10, pady=6)
            conteudo = tk.Frame(card, bg="#1e222b", padx=20, pady=24)
            conteudo.pack(fill=tk.BOTH, expand=True)
            tk.Label(conteudo, text=icone, font=(FONTE, 32), bg="#1e222b").pack(pady=(0, 10))
            tk.Label(conteudo, text=titulo, font=(FONTE, 13, "bold"), fg=cor, bg="#1e222b").pack(pady=(0, 10))
            tk.Label(conteudo, text=descricao, font=(FONTE, 9), fg="#9ca3af", bg="#1e222b", justify="center", wraplength=220).pack()
            for widget in (card, conteudo, *conteudo.winfo_children()):
                widget.bind("<Button-1>", lambda e, m=modo: _selecionar(m))
            cards[modo] = card
            return card
        _criar_card(frame_opcoes, 0, "auto", ICO["auto"], "MODO AUTOMATICO",
                    "Proximas etapas executam automaticamente.", "#22c55e")
        _criar_card(frame_opcoes, 1, "manual", ICO["manual"], "MODO MANUAL",
                    "Botoes individuais para cada etapa.", "#3b82f6")
        _selecionar("auto")
        ttk.Label(dialog, text="%s Esta escolha nao pode ser alterada depois" % ICO["aviso"],
                  font=(FONTE, 9), foreground="#f87171").pack(pady=(4, 8))
        def continuar():
            modo = modo_var.get()
            dialog.destroy()
            if modo == "auto":
                self._log_ui_click("Modo Automatico")
                self._executar_modo_automatico()
            else:
                self._log_ui_click("Modo Manual")
                self._executar_modo_manual()
        ttk.Button(dialog, text="%s  OK, CONTINUAR" % ICO["ok"], command=continuar,
                   **({"bootstyle": "success"} if TEM_TTB else {}), width=25).pack(pady=(4, 16))

    def _executar_modo_automatico(self):
        self.prog_status.config(text=">> Modo Automatico ativado. Executando...", foreground="#0d6efd")
        def task():
            self.pipeline._notify(1, "andamento", "Gerando cenas...")
            self.root.after(0, lambda: self._etapa_labels[1].config(text=">> Cenas", foreground="#0d6efd"))
            self._log_ui_click("Gerar Cenas (auto)")
            r2 = self.pipeline.gerar_cenas()
            if r2.get("success"):
                self.root.after(0, lambda: self._cenas_concluidas(r2))
            else:
                self.root.after(0, lambda: self._erro_etapa(1, r2.get("error", "")))
                return
            self.pipeline._notify(2, "andamento", "Gerando storyboard...")
            self.root.after(0, lambda: self._etapa_labels[2].config(text=">> Storyboard", foreground="#0d6efd"))
            self._log_ui_click("Gerar Storyboard (auto)")
            r3 = self.pipeline.gerar_storyboard(usar_claude=False)
            if r3.get("success"):
                self.root.after(0, lambda: self._storyboard_concluido(r3))
            else:
                self.root.after(0, lambda: self._erro_etapa(2, r3.get("error", "")))
                return
            self.pipeline.gerar_queries()
            self.pipeline._notify(3, "andamento", "Buscando midias...")
            self.root.after(0, lambda: self._etapa_labels[3].config(text=">> Midias", foreground="#0d6efd"))
            self._log_ui_click("Buscar Midias (auto)")
            r4 = self.pipeline.buscar_midias()
            self.root.after(0, self._renderizar_cenas_grid)
            self.root.after(0, self._atualizar_biblioteca)
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            self.root.after(0, lambda: self._etapa_labels[4].config(text=">> Render", foreground="#0d6efd"))
            self._log_ui_click("Renderizar (auto)")
            r5 = self.pipeline.renderizar()
            if r5.get("success"):
                self.root.after(0, lambda: self._render_concluido(r5))
            else:
                self.root.after(0, lambda: self._render_concluido(r5))
            self.spinner.stop()
        threading.Thread(target=task, daemon=True).start()

    def _executar_modo_manual(self):
        self.prog_status.config(text="Modo Manual ativado.", foreground="#198754")
        frame = self.tab_projeto
        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=5)
        ttk.Label(frame, text="PROXIMAS ETAPAS (execute na ordem desejada):", font=(FONTE, 11, "bold")).pack(anchor=tk.W)
        self.btn_manual_frame = ttk.Frame(frame)
        self.btn_manual_frame.pack(fill=tk.X, pady=8)
        self.btn_cenas = ttk.Button(self.btn_manual_frame, text="GERAR CENAS",
                    command=lambda: self._log_ui_click("Gerar Cenas") or self._manual_cenas(),
                    **({"bootstyle": "success"} if TEM_TTB else {}))
        self.btn_cenas.pack(side=tk.LEFT, padx=5)
        self.btn_story = ttk.Button(self.btn_manual_frame, text="GERAR STORYBOARD",
                    command=lambda: self._log_ui_click("Gerar Storyboard") or self._manual_storyboard(),
                    **({"bootstyle": "info"} if TEM_TTB else {}))
        self.btn_story.pack(side=tk.LEFT, padx=5)
        self.btn_story.config(state="disabled")
        self.btn_midias = ttk.Button(self.btn_manual_frame, text="BUSCAR MIDIAS",
                    command=lambda: self._log_ui_click("Buscar Midias") or self._manual_midias(),
                    **({"bootstyle": "warning"} if TEM_TTB else {}))
        self.btn_midias.pack(side=tk.LEFT, padx=5)
        self.btn_midias.config(state="disabled")
        self.btn_render = ttk.Button(self.btn_manual_frame, text="RENDERIZAR VIDEO",
                    command=lambda: self._log_ui_click("Renderizar") or self._manual_render(),
                    **({"bootstyle": "danger"} if TEM_TTB else {}))
        self.btn_render.pack(side=tk.LEFT, padx=5)
        self.btn_render.config(state="disabled")
        self.notebook.select(self.tab_projeto)

    def _manual_cenas(self):
        self.spinner.start()
        def task():
            self.pipeline._notify(1, "andamento", "Gerando cenas...")
            r = self.pipeline.gerar_cenas()
            self.root.after(0, lambda: self._cenas_concluidas(r))
            self.root.after(0, lambda: self.spinner.stop())
            if r.get("success"):
                self.root.after(0, lambda: self.btn_story.config(state="normal"))
        threading.Thread(target=task, daemon=True).start()

    def _manual_storyboard(self):
        self.spinner.start()
        def task():
            self.pipeline._notify(2, "andamento", "Gerando storyboard...")
            r = self.pipeline.gerar_storyboard(usar_claude=False)
            self.root.after(0, lambda: self._storyboard_concluido(r))
            self.root.after(0, lambda: self.spinner.stop())
            if r.get("success"):
                self.pipeline.gerar_queries()
                self.root.after(0, lambda: self.btn_midias.config(state="normal"))
        threading.Thread(target=task, daemon=True).start()

    def _manual_midias(self):
        self.spinner.start()
        def task():
            self.pipeline._notify(3, "andamento", "Buscando midias...")
            r = self.pipeline.buscar_midias()
            self.root.after(0, lambda: self.spinner.stop())
            self.root.after(0, self._renderizar_cenas_grid)
            self.root.after(0, self._atualizar_biblioteca)
            self.root.after(0, lambda: self.btn_render.config(state="normal"))
        threading.Thread(target=task, daemon=True).start()

    def _manual_render(self):
        self.spinner.start()
        def task():
            self.pipeline._notify(4, "andamento", "Renderizando...")
            r = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(r))
            self.root.after(0, lambda: self.spinner.stop())
        threading.Thread(target=task, daemon=True).start()

    def _cenas_concluidas(self, result):
        self._etapa_labels[1].config(text="v Cenas", foreground="#198754")
        self._cenas_data = result.get("cenas", []) if result.get("success") else []
        self._renderizar_cenas_grid()
        self.prog_log.insert(tk.END, "[Cenas] %d cenas geradas\n" % result.get('cenas_count', 0))
        self.prog_log.see(tk.END)

    def _carregar_midias_por_cena(self):
        resultado = {}
        try:
            proj = self.pipeline.project_name
            if not proj:
                return resultado
            arq = PROJETOS_DIR / proj / "midias_encontradas.json"
            if arq.exists():
                dados = json.loads(arq.read_text(encoding="utf-8"))
                for item in dados:
                    sid = item.get("scene_id")
                    if sid is not None:
                        resultado[sid] = item
        except Exception:
            pass
        return resultado

    def _renderizar_cenas_grid(self):
        for w in self.cenas_grid.winfo_children():
            w.destroy()
        self._cenas_thumb_refs = []
        if not self._cenas_data:
            ttk.Label(self.cenas_grid, text="Nenhuma cena gerada ainda.",
                      font=(FONTE, 10), foreground="#6b7280").grid(row=0, column=0, padx=10, pady=10)
            return
        midias_por_cena = self._carregar_midias_por_cena()
        colunas = 4
        for idx, cena in enumerate(self._cenas_data):
            row, col = divmod(idx, colunas)
            midia = midias_por_cena.get(cena.get("id"))
            card = self._criar_card_cena(self.cenas_grid, cena, midia)
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

    def _criar_card_cena(self, parent, cena, midia):
        scene_id = cena.get("id")
        card = ttk.Frame(parent, relief="solid", borderwidth=1, padding=6, cursor="hand2")
        card.grid_propagate(False)
        card.configure(width=196, height=178)
        caminho = (midia or {}).get("arquivo", "")
        thumb = self._carregar_thumbnail(caminho) if caminho else None
        if thumb:
            lbl_img = ttk.Label(card, image=thumb)
            lbl_img.image = thumb
            self._cenas_thumb_refs.append(thumb)
        else:
            texto_estado = "Sem midia" if midia is None else "Aguardando"
            lbl_img = ttk.Label(card, text=texto_estado, width=22, anchor="center",
                                 font=(FONTE, 9), foreground="#6b7280")
        lbl_img.pack(pady=(0, 6))
        ttk.Label(card, text="Cena %s" % scene_id, font=(FONTE, 9, "bold")).pack(anchor="w")
        texto_cena = cena.get("texto", "") or ""
        texto_curto = texto_cena[:38] + ("..." if len(texto_cena) > 38 else "")
        ttk.Label(card, text=texto_curto, font=(FONTE, 8), foreground="#9ca3af", wraplength=176).pack(anchor="w")
        if midia:
            qualidade = str(midia.get("quality", "?"))
            cor_q = COR_STATUS["concluido"] if qualidade == "green" else COR_STATUS["erro"]
            ttk.Label(card, text=qualidade.upper(), font=(FONTE, 8, "bold"), foreground=cor_q).pack(anchor="w")
        for widget in (card, lbl_img):
            widget.bind("<Button-1>", lambda e, c=cena, m=midia: self._abrir_detalhe_cena(c, m))
        return card

    def _abrir_detalhe_cena(self, cena, midia):
        self._log_ui_click("Abrir Detalhe Cena %s" % cena.get("id"))
        dialog = tk.Toplevel(self.root)
        dialog.title("Cena %s" % cena.get("id"))
        dialog.geometry("520x520")
        dialog.transient(self.root)
        ttk.Label(dialog, text="CENA %s" % cena.get("id"), font=(FONTE, 14, "bold")).pack(pady=(15, 5))
        caminho = (midia or {}).get("arquivo", "")
        thumb_grande = self._carregar_thumbnail(caminho, tamanho=THUMB_DETALHE) if caminho else None
        if thumb_grande:
            lbl = ttk.Label(dialog, image=thumb_grande)
            lbl.image = thumb_grande
            lbl.pack(pady=10)
        else:
            ttk.Label(dialog, text="Sem midia associada.", font=(FONTE, 11), foreground="#6b7280").pack(pady=40)
        info_frame = ttk.Frame(dialog, padding=10)
        info_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(info_frame, text="Texto: %s" % cena.get("texto", ""), wraplength=470, font=(FONTE, 10)).pack(anchor="w", pady=2)
        ttk.Label(info_frame, text="Tipo: %s" % cena.get("scene_type", cena.get("type", "?")), font=(FONTE, 10)).pack(anchor="w", pady=2)
        ttk.Label(info_frame, text="Keywords: %s" % cena.get("keywords", []), font=(FONTE, 10), wraplength=470).pack(anchor="w", pady=2)
        if midia:
            ttk.Label(info_frame, text="Fonte: %s" % midia.get("source", "?"), font=(FONTE, 10)).pack(anchor="w", pady=2)
            ttk.Label(info_frame, text="Resolucao: %sx%s" % (midia.get("width", 0), midia.get("height", 0)), font=(FONTE, 10)).pack(anchor="w", pady=2)
            ttk.Label(info_frame, text="Qualidade: %s" % midia.get("quality", "?"), font=(FONTE, 10)).pack(anchor="w", pady=2)
            ttk.Label(info_frame, text="Arquivo: %s" % caminho, font=(FONTE, 8), foreground="#9ca3af", wraplength=470).pack(anchor="w", pady=(8, 2))
        ttk.Button(dialog, text="Fechar", command=dialog.destroy).pack(pady=10)

    def _storyboard_concluido(self, result):
        self._etapa_labels[2].config(text="v Storyboard", foreground="#198754")
        self.texto_story.delete(1.0, tk.END)
        if result.get("success"):
            for c in result.get("storyboard", []):
                self.texto_story.insert(tk.END, "Cena %d: type=%s keywords=%s\n" %
                                        (c['id'], c['scene_type'], c['keywords']))
        self.prog_log.insert(tk.END, "[Storyboard] %d cenas\n" % result.get('cenas_count', 0))
        self.prog_log.see(tk.END)

    def _erro_etapa(self, step, erro):
        self._etapa_labels[step].config(text="x %s" % STEP_NAMES[step], foreground="#dc3545")
        self.prog_status.config(text="x ERRO: %s" % erro[:80], foreground="#dc3545")
        self.prog_log.insert(tk.END, "[ERRO] Etapa %s: %s\n" % (STEP_NAMES[step], erro))
        self.prog_log.see(tk.END)

    def _render_concluido(self, result):
        self._parar_temporizador()
        self._etapa_labels[4].config(text="v Render", foreground="#198754")
        self.texto_render.delete(1.0, tk.END)
        if result.get("success"):
            self.texto_render.insert(tk.END, "Renderizacao concluida!\n")
            self.texto_render.insert(tk.END, "Arquivo: %s\n" % result.get('arquivo', 'N/A'))
            self.texto_render.insert(tk.END, "Tamanho: %d bytes\n" % result.get('tamanho', 0))
            self.prog_status.config(text="v Pipeline concluido com sucesso!", foreground="#198754")
            self.prog_log.insert(tk.END, "[Render] Video salvo em: %s\n" % result.get('arquivo', ''))
            messagebox.showinfo("Sucesso", "Video renderizado com sucesso!")
        else:
            self.prog_status.config(text="x Render falhou: %s" % result.get('error', '')[:60], foreground="#dc3545")
            self.texto_render.insert(tk.END, "Erro: %s\n" % result.get('error', 'Erro desconhecido'))

    def _extrair_audio_se_video(self, arquivo):
        ext = Path(arquivo).suffix.lower()
        if ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
            return arquivo
        import subprocess
        pd = PROJETOS_DIR / self.pipeline.project_name
        pd.mkdir(parents=True, exist_ok=True)
        saida = str(pd / "audio_extraido.wav")
        subprocess.run(["ffmpeg", "-y", "-i", arquivo, "-vn",
                       "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", saida],
                      capture_output=True, text=True, timeout=300)
        if Path(saida).exists():
            return saida
        return arquivo

    def _atualizar_lista_projetos(self):
        self.lista_projetos.delete(0, tk.END)
        for p in self.pipeline.listar_projetos():
            nome = p.get("name", "?")
            steps = p.get("steps", {})
            status = "Criado"
            if steps.get("renderizar", {}).get("status") == "concluido":
                status = "Renderizado"
            elif steps.get("buscar_midias", {}).get("status") == "concluido":
                status = "Midias OK"
            elif steps.get("transcrever", {}).get("status") == "concluido":
                status = "Transcrito"
            self.lista_projetos.insert(tk.END, "%-35s %-20s" % (nome, "[%s]" % status))

    def _selecionar_projeto_lista(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto")
            return
        texto = self.lista_projetos.get(sel[0])
        nome = texto.split("[")[0].strip()
        self.pipeline.project_name = nome
        meta = self.pipeline._carregar_meta()
        audio = meta.get("arquivo_audio", "")
        self.projeto_info.config(text="Projeto atual: %s" % nome)
        if audio and Path(audio).exists():
            self.entry_audio.delete(0, tk.END)
            self.entry_audio.insert(0, audio)
            self.arquivo_audio = audio
        if hasattr(self, 'prog_log'):
            self.prog_log.delete(1.0, tk.END)
        try:
            from services.event_logger import ler_eventos
            self._ultimo_log_idx = len(ler_eventos(linhas=99999))
        except Exception:
            self._ultimo_log_idx = 0
        if hasattr(self, '_frame_retomar') and self._frame_retomar:
            self._frame_retomar.destroy()
        steps = meta.get("steps", {})
        self._frame_retomar = ttk.Labelframe(self.tab_projeto, text=" Retomar Projeto ", padding=12)
        self._frame_retomar.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(self._frame_retomar, text="Projeto: %s" % nome, font=(FONTE, 10, "bold")).pack(anchor=tk.W)
        btn_frame = ttk.Frame(self._frame_retomar)
        btn_frame.pack(fill=tk.X, pady=8)
        render_ok = steps.get("renderizar", {}).get("status") == "concluido"
        midias_ok = steps.get("buscar_midias", {}).get("status") == "concluido"
        storyboard_ok = steps.get("storyboard_broll", {}).get("status") == "concluido"
        cenas_ok = steps.get("gerar_cenas", {}).get("status") == "concluido"
        trans_ok = steps.get("transcrever", {}).get("status") == "concluido"
        if render_ok:
            ttk.Label(btn_frame, text="Pipeline completo!", foreground="#22c55e", font=(FONTE, 10)).pack(side=tk.LEFT, padx=5)
        elif midias_ok:
            ttk.Button(btn_frame, text="RENDERIZAR VIDEO", command=self._retomar_render,
                       **({"bootstyle": "danger"} if TEM_TTB else {}), width=22).pack(side=tk.LEFT, padx=5)
        elif storyboard_ok:
            ttk.Button(btn_frame, text="BUSCAR MIDIAS", command=self._retomar_midias,
                       **({"bootstyle": "warning"} if TEM_TTB else {}), width=22).pack(side=tk.LEFT, padx=5)
        elif cenas_ok:
            ttk.Button(btn_frame, text="GERAR STORYBOARD", command=self._retomar_storyboard,
                       **({"bootstyle": "info"} if TEM_TTB else {}), width=22).pack(side=tk.LEFT, padx=5)
        elif trans_ok:
            ttk.Button(btn_frame, text="GERAR CENAS", command=self._retomar_cenas,
                       **({"bootstyle": "success"} if TEM_TTB else {}), width=22).pack(side=tk.LEFT, padx=5)
        else:
            ttk.Button(btn_frame, text="INICIAR PIPELINE", command=self._retomar_transcricao,
                       **({"bootstyle": "success"} if TEM_TTB else {}), width=22).pack(side=tk.LEFT, padx=5)

    def _retomar_render(self):
        self._log_ui_click("Retomar Render")
        self.notebook.select(self.tab_progresso)
        self._resetar_progresso_global()
        self.spinner.start()
        def task():
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            r = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(r))
            self.spinner.stop()
        threading.Thread(target=task, daemon=True).start()

    def _retomar_midias(self):
        self._log_ui_click("Retomar Midias")
        self.notebook.select(self.tab_progresso)
        self._resetar_progresso_global()
        self.spinner.start()
        def task():
            self.pipeline._notify(3, "andamento", "Buscando midias...")
            r4 = self.pipeline.buscar_midias()
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            r5 = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(r5))
            self.spinner.stop()
        threading.Thread(target=task, daemon=True).start()

    def _retomar_storyboard(self):
        self._log_ui_click("Retomar Storyboard")
        self.notebook.select(self.tab_progresso)
        self._resetar_progresso_global()
        self.spinner.start()
        def task():
            self.pipeline._notify(2, "andamento", "Gerando storyboard...")
            r3 = self.pipeline.gerar_storyboard(usar_claude=False)
            if not r3.get("success"):
                self.spinner.stop()
                return
            self.pipeline.gerar_queries()
            self.pipeline._notify(3, "andamento", "Buscando midias...")
            r4 = self.pipeline.buscar_midias()
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            r5 = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(r5))
            self.spinner.stop()
        threading.Thread(target=task, daemon=True).start()

    def _retomar_cenas(self):
        self._log_ui_click("Retomar Cenas")
        self.notebook.select(self.tab_progresso)
        self._resetar_progresso_global()
        self.spinner.start()
        def task():
            self.pipeline._notify(1, "andamento", "Gerando cenas...")
            r2 = self.pipeline.gerar_cenas()
            if not r2.get("success"):
                self.spinner.stop()
                return
            self.pipeline._notify(2, "andamento", "Gerando storyboard...")
            r3 = self.pipeline.gerar_storyboard(usar_claude=False)
            if not r3.get("success"):
                self.spinner.stop()
                return
            self.pipeline.gerar_queries()
            self.pipeline._notify(3, "andamento", "Buscando midias...")
            r4 = self.pipeline.buscar_midias()
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            r5 = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(r5))
            self.spinner.stop()
        threading.Thread(target=task, daemon=True).start()

    def _retomar_transcricao(self):
        self._log_ui_click("Retomar Transcricao")
        if not self.arquivo_audio:
            messagebox.showwarning("Aviso", "Selecione o arquivo de audio antes de continuar")
            return
        self.notebook.select(self.tab_progresso)
        self._resetar_progresso_global()
        self.spinner.start()
        def task():
            audio_path = self._extrair_audio_se_video(self.arquivo_audio)
            self.pipeline._notify(0, "andamento", "Transcrevendo audio...")
            r1 = self.pipeline.transcrever(audio_path)
            if not r1.get("success"):
                self.root.after(0, lambda: self._erro_etapa(0, r1.get("error", "")))
                self.spinner.stop()
                return
            self.root.after(0, lambda: self._transcricao_concluida(r1))
        threading.Thread(target=task, daemon=True).start()

    def _deletar_projeto_confirmado(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto para deletar")
            return
        idx = sel[0]
        projetos = self.pipeline.listar_projetos()
        if idx < len(projetos):
            nome = projetos[idx].get("name", "")
        else:
            texto = self.lista_projetos.get(idx)
            nome = texto.split("[")[0].strip()
        if not nome:
            messagebox.showerror("Erro", "Nao foi possivel identificar o projeto")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirmar Exclusao")
        dialog.geometry("420x200")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="CONFIRMAR EXCLUSAO", font=(FONTE, 13, "bold")).pack(pady=(15, 8))
        ttk.Label(dialog, text='Tem certeza que deseja deletar:', font=(FONTE, 10)).pack()
        ttk.Label(dialog, text='"{}"'.format(nome), font=(FONTE, 11, "bold"), foreground="#dc3545").pack(pady=5)
        ttk.Label(dialog, text="Esta acao eh IRREVERSIVEL!", font=(FONTE, 10), foreground="#dc3545").pack()
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=18)
        ttk.Button(btn_frame, text="NAO, CANCELAR", command=dialog.destroy,
                   **({"bootstyle": "secondary"} if TEM_TTB else {}), width=15).pack(side=tk.LEFT, padx=8)
        def deletar():
            try:
                caminho = PROJETOS_DIR / nome
                if caminho.exists() and caminho.is_dir():
                    shutil.rmtree(str(caminho))
                dialog.destroy()
                self._atualizar_lista_projetos()
                self.projeto_info.config(text="")
                self.status_label.config(text="Projeto '{}' deletado".format(nome))
                messagebox.showinfo("Sucesso", "Projeto '{}' deletado!".format(nome))
            except Exception as e:
                dialog.destroy()
                messagebox.showerror("Erro", "Nao foi possivel deletar: {}".format(str(e)))
        ttk.Button(btn_frame, text="SIM, DELETAR", command=deletar,
                   **({"bootstyle": "danger"} if TEM_TTB else {}), width=15).pack(side=tk.LEFT, padx=8)

    def _novo_projeto_rapido(self):
        self._log_ui_click("Novo Projeto Rapido")
        dialog = tk.Toplevel(self.root)
        dialog.title("Novo Projeto")
        dialog.geometry("450x200")
        dialog.transient(self.root)
        ttk.Label(dialog, text="Nome do projeto:", font=(FONTE, 11)).pack(pady=(15, 5))
        entry = ttk.Entry(dialog, width=45)
        entry.pack(pady=5)
        entry.focus()
        ttk.Label(dialog, text="Arquivo de audio:").pack()
        audio_entry = ttk.Entry(dialog, width=45)
        audio_entry.pack(pady=5)
        def sel():
            f = filedialog.askopenfilename(title="Selecionar Audio",
                filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac"), ("Todos", "*.*")])
            if f:
                audio_entry.delete(0, tk.END)
                audio_entry.insert(0, f)
        ttk.Button(dialog, text="Selecionar", command=sel).pack()
        def criar():
            nome = entry.get().strip()
            audio = audio_entry.get().strip()
            if nome and audio:
                self.entry_nome.delete(0, tk.END)
                self.entry_nome.insert(0, nome)
                self.entry_audio.delete(0, tk.END)
                self.entry_audio.insert(0, audio)
                self.arquivo_audio = audio
                dialog.destroy()
                self._criar_projeto_fluxo()
        ttk.Button(dialog, text="Criar e Iniciar", command=criar, **({"bootstyle": "success"} if TEM_TTB else {})).pack(pady=12)

    def _atualizar_biblioteca(self):
        for w in self.bib_grid.winfo_children():
            w.destroy()
        self._bib_thumb_refs = []
        data = listar_biblioteca()
        entries = data.get("entries", [])
        if not entries:
            ttk.Label(self.bib_grid, text="Nenhuma midia na biblioteca ainda. "
                                           "Rode uma busca de midias para popular esta lista.",
                      font=(FONTE, 10), foreground="#6b7280").grid(row=0, column=0, padx=10, pady=10)
            return
        colunas = 4
        for idx, entry in enumerate(entries):
            row, col = divmod(idx, colunas)
            card = self._criar_card_midia(self.bib_grid, entry)
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")


def main():
    if TEM_TTB:
        root = ttk.Window(themename=TEMA)
    else:
        root = tk.Tk()
    app = Ultracut3GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()