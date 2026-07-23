"""
gui.py — Interface gráfica ULTRACUT3 (ttkbootstrap, 9 abas)
Fluxo: Criar Projeto -> Transcricao auto -> Escolha modo -> Pipeline
"""
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import json, os, threading, time, shutil
from pathlib import Path
from datetime import datetime

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    TEMA = "litera"; TEM_TTB = True
except ImportError:
    from tkinter import ttk
    TEMA = None; TEM_TTB = False

from services.pipeline_service import PipelineService
from services.library import listar_biblioteca, remover_media
from config import PROJETOS_DIR, OUTPUT_DIR

STEP_NAMES = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"]
STEP_ICONS = {"pendente": " o ", "andamento": " >> ", "concluido": " v ", "erro": " x "}
MODO_AUTO = None  # "auto" ou "manual", definido apos transcricao


class Ultracut3GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ULTRACUT3 - Pipeline de Video")
        self.root.geometry("1200x750")
        self.root.minsize(900, 650)

        self.pipeline = PipelineService()
        self.arquivo_audio = None
        self.pipeline_thread = None
        self.etapas_concluidas = [False] * 5
        self._on_pipeline_end = None

        self.pipeline.set_progress_callback(self._on_progress)

        self._build_menu()
        self._build_notebook()
        self._status_bar()
        self._atualizar_lista_projetos()
        self._modo_escolhido = None  # "auto" ou "manual"

    def _on_progress(self, step, status, msg):
        self.root.after(0, lambda: self._atualizar_etapa_ui(step, status, msg))

    def _atualizar_etapa_ui(self, step, status, msg):
        # Atualiza labels de progresso
        cor = {"concluido": "#198754", "andamento": "#0d6efd", "erro": "#dc3545", "pendente": "gray"}.get(status, "gray")
        icon = STEP_ICONS.get(status, " o ")
        nome = STEP_NAMES[step]

        # Atualiza status na aba Progresso
        if hasattr(self, 'prog_status'):
            if status == "andamento":
                self.prog_status.config(text=">> %s: %s" % (nome, msg[:80]), foreground=cor)
            elif status == "concluido":
                self.prog_status.config(text="v %s: %s" % (nome, msg[:80]), foreground=cor)
                self.etapas_concluidas[step] = True
            elif status == "erro":
                self.prog_status.config(text="x %s: %s" % (nome, msg[:80]), foreground=cor)

        # Atualiza log na aba Progresso
        if hasattr(self, 'prog_log'):
            self.prog_log.insert(tk.END, "[%s] [%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), nome, msg[:80]))
            self.prog_log.see(tk.END)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        fm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Arquivo", menu=fm)
        fm.add_command(label="Novo Projeto", command=self._novo_projeto_rapido)
        fm.add_separator()
        fm.add_command(label="Sair", command=self.root.quit)

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Aba 1: Projeto (criar + lista + botoes)
        self.tab_projeto = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_projeto, text="Projeto")
        self._build_tab_projeto()

        # Aba 2: Progresso em tempo real
        self.tab_progresso = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_progresso, text="Progresso")
        self._build_tab_progresso()

        # Aba 3: Resultados (transcricao, cenas, storyboard, midias, render)
        self.tab_resultados = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_resultados, text="Resultados")
        self._build_tab_resultados()

        # Aba 4: Biblioteca
        self.tab_biblioteca = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_biblioteca, text="Biblioteca")
        self._build_tab_biblioteca()

    def _status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.status_label = ttk.Label(self.status_frame, text="Pronto", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.spinner = ttk.Progressbar(self.status_frame, mode='indeterminate', length=150)
        self.spinner.pack(side=tk.RIGHT, padx=5)

    # ==================== ABA PROJETO ====================
    def _build_tab_projeto(self):
        frame = self.tab_projeto

        # Criar Projeto
        ttk.Label(frame, text="CRIAR PROJETO", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)

        ttk.Label(frame, text="Nome do Projeto:", font=("Segoe UI", 10)).pack(anchor=tk.W, pady=(10, 2))
        self.entry_nome = ttk.Entry(frame, width=50)
        self.entry_nome.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(frame, text="Selecionar Audio:", font=("Segoe UI", 10)).pack(anchor=tk.W)
        audio_frame = ttk.Frame(frame)
        audio_frame.pack(fill=tk.X, pady=5)
        self.entry_audio = ttk.Entry(audio_frame, width=50)
        self.entry_audio.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(audio_frame, text="Escolher arquivo...", command=self._selecionar_audio,
                   bootstyle="info-outline").pack(side=tk.RIGHT, padx=5)
        ttk.Button(audio_frame, text="Limpar", command=lambda: (self.entry_audio.delete(0, tk.END), setattr(self, 'arquivo_audio', None)),
                   bootstyle="secondary").pack(side=tk.RIGHT, padx=5)

        self.status_criar = ttk.Label(frame, text="", font=("Segoe UI", 9), foreground="gray")
        self.status_criar.pack(anchor=tk.W, pady=5)

        self.btn_criar = ttk.Button(frame, text="CRIAR PROJETO", command=self._criar_projeto_fluxo,
                                     bootstyle="success", width=30)
        self.btn_criar.pack(pady=10)

        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=10)

        # Projetos Existentes
        ttk.Label(frame, text="PROJETOS EXISTENTES", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)

        # Cabecalho
        cab = ttk.Frame(frame)
        cab.pack(fill=tk.X, pady=5)
        ttk.Label(cab, text="Nome", font=("Segoe UI", 9, "bold"), width=35).pack(side=tk.LEFT)
        ttk.Label(cab, text="Status", font=("Segoe UI", 9, "bold"), width=20).pack(side=tk.LEFT)
        ttk.Label(cab, text="Acao", font=("Segoe UI", 9, "bold"), width=10).pack(side=tk.LEFT)

        # Lista de projetos
        lista_container = ttk.Frame(frame)
        lista_container.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(lista_container)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.lista_projetos = tk.Listbox(lista_container, height=8, yscrollcommand=scroll.set,
                                          font=("Consolas", 10))
        self.lista_projetos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.lista_projetos.yview)

        # Botoes da lista
        btn_lista = ttk.Frame(frame)
        btn_lista.pack(fill=tk.X, pady=8)

        # Botao de deletar com confirmacao
        ttk.Button(btn_lista, text="Selecionar Projeto", command=self._selecionar_projeto_lista).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_lista, text="[x] DEL", command=self._deletar_projeto_confirmado,
                   bootstyle="danger-outline").pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_lista, text="Atualizar Lista", command=self._atualizar_lista_projetos).pack(side=tk.LEFT, padx=4)

        self.projeto_info = ttk.Label(frame, text="", foreground="gray")
        self.projeto_info.pack(anchor=tk.W, pady=5)

    # ==================== ABA PROGRESSO ====================
    def _build_tab_progresso(self):
        frame = self.tab_progresso
        ttk.Label(frame, text="ACOMPANHAMENTO EM TEMPO REAL", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)

        self.prog_projeto = ttk.Label(frame, text="", font=("Segoe UI", 11))
        self.prog_projeto.pack(anchor=tk.W, pady=5)

        self.prog_status = ttk.Label(frame, text="Aguardando inicio do pipeline...",
                                     font=("Segoe UI", 12), foreground="gray")
        self.prog_status.pack(anchor=tk.W, pady=10)

        # Barra de progresso
        self.prog_var = tk.DoubleVar(value=0)
        self.prog_bar = ttk.Progressbar(frame, variable=self.prog_var, maximum=100, length=500, mode='determinate')
        self.prog_bar.pack(fill=tk.X, pady=5)

        # Etapas
        self.prog_etapas = ttk.Frame(frame)
        self.prog_etapas.pack(fill=tk.X, pady=10)
        self._etapa_labels = []
        for i, nome in enumerate(STEP_NAMES):
            lb = ttk.Label(self.prog_etapas, text="o %s" % nome, font=("Segoe UI", 10), foreground="gray")
            lb.pack(side=tk.LEFT, padx=8)
            self._etapa_labels.append(lb)

        # Temporizador
        self.prog_tempo = ttk.Label(frame, text="Tempo decorrido: 00:00", font=("Segoe UI", 10), foreground="gray")
        self.prog_tempo.pack(anchor=tk.W, pady=5)
        self._tempo_inicio = None
        self._tempo_timer = None

        # Log
        ttk.Label(frame, text="Ultimas acoes:", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(15, 5))
        self.prog_log = scrolledtext.ScrolledText(frame, height=10, wrap=tk.WORD, font=("Consolas", 9))
        self.prog_log.pack(fill=tk.BOTH, expand=True)

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

        # Notebook interno para cada etapa
        self.sub_notebook = ttk.Notebook(frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)

        # Transcricao
        tab_trans = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_trans, text="Transcricao")
        self.texto_trans = scrolledtext.ScrolledText(tab_trans, height=15, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_trans.pack(fill=tk.BOTH, expand=True)

        # Cenas
        tab_cenas = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_cenas, text="Cenas")
        self.lista_cenas = tk.Listbox(tab_cenas, height=15, font=("Consolas", 10))
        self.lista_cenas.pack(fill=tk.BOTH, expand=True)

        # Storyboard
        tab_story = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_story, text="Storyboard")
        self.texto_story = scrolledtext.ScrolledText(tab_story, height=15, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_story.pack(fill=tk.BOTH, expand=True)

        # Midias
        tab_midias = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_midias, text="Midias")
        self.texto_midias = scrolledtext.ScrolledText(tab_midias, height=15, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_midias.pack(fill=tk.BOTH, expand=True)

        # Render
        tab_render = ttk.Frame(self.sub_notebook, padding=10)
        self.sub_notebook.add(tab_render, text="Render")
        self.texto_render = scrolledtext.ScrolledText(tab_render, height=15, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_render.pack(fill=tk.BOTH, expand=True)

    # ==================== ABA BIBLIOTECA ====================
    def _build_tab_biblioteca(self):
        frame = self.tab_biblioteca
        ttk.Label(frame, text="Biblioteca de Midia", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)
        self.lista_bib = tk.Listbox(frame, height=16, font=("Consolas", 10))
        self.lista_bib.pack(fill=tk.BOTH, expand=True)
        ttk.Button(frame, text="Atualizar", command=self._atualizar_biblioteca).pack(pady=5)

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
        """Cria projeto, ja seleciona, dispara transcricao automaticamente."""
        nome = self.entry_nome.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Digite um nome para o projeto")
            return
        if not self.arquivo_audio:
            messagebox.showwarning("Aviso", "Selecione um arquivo de audio")
            return

        # Cria projeto
        r = self.pipeline.criar_projeto(nome, self.arquivo_audio)
        if not r.get("success"):
            messagebox.showerror("Erro", r.get("error", "Erro ao criar"))
            return

        # Projeto selecionado automaticamente
        self.projeto_info.config(text="Projeto atual: %s" % nome)
        self.status_label.config(text="Projeto '%s' criado e selecionado" % nome)
        self._atualizar_lista_projetos()

        # Muda para aba Progresso
        self.notebook.select(self.tab_progresso)
        self.prog_projeto.config(text="Projeto: %s  |  Audio: %s" % (nome, Path(self.arquivo_audio).name))
        self.prog_status.config(text=">> Transcrevendo audio...", foreground="#0d6efd")
        self._iniciar_temporizador()
        self._etapa_labels[0].config(text=">> Transcricao", foreground="#0d6efd")

        # Dispara transcricao em thread
        self.spinner.start()

        def task():
            audio_path = self._extrair_audio_se_video(self.arquivo_audio)

            # Etapa 1: Transcricao
            self.pipeline._notify(0, "andamento", "Transcrevendo audio...")
            r1 = self.pipeline.transcrever(audio_path)
            if not r1.get("success"):
                self.root.after(0, lambda: self._erro_etapa(0, r1.get("error", "Erro na transcricao")))
                self.spinner.stop()
                return

            self.root.after(0, lambda: self._transcricao_concluida(r1))

        threading.Thread(target=task, daemon=True).start()

    def _transcricao_concluida(self, result):
        """Apos transcricao, mostra escolha de modo."""
        self._etapa_labels[0].config(text="v Transcricao", foreground="#198754")
        self.texto_trans.delete(1.0, tk.END)
        self.texto_trans.insert(tk.END, result.get("texto", ""))
        self.spinner.stop()

        # Dialogo de escolha de modo
        self._mostrar_escolha_modo()

    def _mostrar_escolha_modo(self):
        """Dialogo para escolher modo automatico ou manual."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Escolher Modo de Execucao")
        dialog.geometry("550x380")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text="CONFIGURACAO DO PIPELINE", font=("Segoe UI", 13, "bold")).pack(pady=(15, 5))
        ttk.Label(dialog, text="Transcricao: v Concluida", font=("Segoe UI", 10), foreground="#198754").pack()

        frame_opcoes = ttk.Frame(dialog, padding=10)
        frame_opcoes.pack(fill=tk.BOTH, expand=True, pady=10)

        modo_var = tk.StringVar(value="auto")

        # Opcao Automatico
        auto_frame = ttk.Frame(frame_opcoes, relief="solid", borderwidth=1, padding=10)
        auto_frame.pack(fill=tk.X, pady=5)
        ttk.Radiobutton(auto_frame, text="MODO AUTOMATICO", variable=modo_var, value="auto",
                         bootstyle="success-toolbutton").pack(anchor=tk.W)
        ttk.Label(auto_frame, text="Proximas etapas (Cenas, Storyboard, Midias, Render)\n"
                   "executarao AUTOMATICAMENTE sem interrupcao.",
                   font=("Segoe UI", 9), foreground="gray").pack(anchor=tk.W, padx=20)

        # Opcao Manual
        manual_frame = ttk.Frame(frame_opcoes, relief="solid", borderwidth=1, padding=10)
        manual_frame.pack(fill=tk.X, pady=5)
        ttk.Radiobutton(manual_frame, text="MODO MANUAL", variable=modo_var, value="manual",
                         bootstyle="info-toolbutton").pack(anchor=tk.W)
        ttk.Label(manual_frame, text="Voce vera BOTOES individuais para cada etapa.\n"
                   "Clique em cada um quando estiver pronto.",
                   font=("Segoe UI", 9), foreground="gray").pack(anchor=tk.W, padx=20)

        ttk.Label(dialog, text="ATENCAO: Esta escolha nao pode ser alterada depois!\n"
                  "(se precisar mudar, delete e recrie o projeto)",
                  font=("Segoe UI", 9), foreground="#dc3545").pack(pady=5)

        def continuar():
            modo = modo_var.get()
            dialog.destroy()
            if modo == "auto":
                self._executar_modo_automatico()
            else:
                self._executar_modo_manual()

        ttk.Button(dialog, text="OK, CONTINUAR", command=continuar, bootstyle="success", width=25).pack(pady=10)

    def _executar_modo_automatico(self):
        """Executa pipeline completo em sequencia."""
        self.prog_status.config(text=">> Modo Automatico ativado. Executando...", foreground="#0d6efd")

        def task():
            # Etapa 2: Cenas
            self.pipeline._notify(1, "andamento", "Gerando cenas...")
            self.root.after(0, lambda: self._etapa_labels[1].config(text=">> Cenas", foreground="#0d6efd"))
            r2 = self.pipeline.gerar_cenas()
            if r2.get("success"):
                self.root.after(0, lambda: self._cenas_concluidas(r2))
            else:
                self.root.after(0, lambda: self._erro_etapa(1, r2.get("error", "")))
                return

            # Etapa 3: Storyboard
            self.pipeline._notify(2, "andamento", "Gerando storyboard...")
            self.root.after(0, lambda: self._etapa_labels[2].config(text=">> Storyboard", foreground="#0d6efd"))
            r3 = self.pipeline.gerar_storyboard(usar_claude=False)
            if r3.get("success"):
                self.root.after(0, lambda: self._storyboard_concluido(r3))
            else:
                self.root.after(0, lambda: self._erro_etapa(2, r3.get("error", "")))
                return

            # Queries
            self.pipeline.gerar_queries()

            # Etapa 4: Midias
            self.pipeline._notify(3, "andamento", "Buscando midias (Pexels, Pixabay, Unsplash)...")
            self.root.after(0, lambda: self._etapa_labels[3].config(text=">> Midias", foreground="#0d6efd"))
            r4 = self.pipeline.buscar_midias()

            # Etapa 5: Render
            self.pipeline._notify(4, "andamento", "Renderizando video final...")
            self.root.after(0, lambda: self._etapa_labels[4].config(text=">> Render", foreground="#0d6efd"))
            r5 = self.pipeline.renderizar()
            if r5.get("success"):
                self.root.after(0, lambda: self._render_concluido(r5))
            else:
                self.root.after(0, lambda: self._render_concluido(r5))

            self.spinner.stop()

        threading.Thread(target=task, daemon=True).start()

    def _executar_modo_manual(self):
        """Mostra botoes individuais na aba Projeto."""
        self.prog_status.config(text="Modo Manual ativado. Clique nos botoes na aba Projeto.", foreground="#198754")

        # Adiciona botoes na aba Projeto
        frame = self.tab_projeto
        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=5)
        ttk.Label(frame, text="PROXIMAS ETAPAS (execute na ordem desejada):",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        self.btn_manual_frame = ttk.Frame(frame)
        self.btn_manual_frame.pack(fill=tk.X, pady=8)

        self.btn_cenas = ttk.Button(self.btn_manual_frame, text="GERAR CENAS",
                                     command=self._manual_cenas, bootstyle="success")
        self.btn_cenas.pack(side=tk.LEFT, padx=5)

        self.btn_story = ttk.Button(self.btn_manual_frame, text="GERAR STORYBOARD",
                                     command=self._manual_storyboard, bootstyle="info")
        self.btn_story.pack(side=tk.LEFT, padx=5)
        self.btn_story.config(state="disabled")

        self.btn_midias = ttk.Button(self.btn_manual_frame, text="BUSCAR MIDIAS",
                                      command=self._manual_midias, bootstyle="warning")
        self.btn_midias.pack(side=tk.LEFT, padx=5)
        self.btn_midias.config(state="disabled")

        self.btn_render = ttk.Button(self.btn_manual_frame, text="RENDERIZAR VIDEO",
                                      command=self._manual_render, bootstyle="danger")
        self.btn_render.pack(side=tk.LEFT, padx=5)
        self.btn_render.config(state="disabled")

        # Muda para aba Projeto
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
        self.lista_cenas.delete(0, tk.END)
        if result.get("success"):
            for cena in result.get("cenas", []):
                self.lista_cenas.insert(tk.END, "Cena %d: %s..." % (cena['id'], cena.get('texto','')[:60]))
        self.prog_log.insert(tk.END, "[Cenas] %d cenas geradas\n" % result.get('cenas_count', 0))
        self.prog_log.see(tk.END)

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

    def _deletar_projeto_confirmado(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto para deletar")
            return

        # Pega o nome diretamente da lista de projetos do pipeline
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

        # Dialogo de confirmacao
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirmar Exclusao")
        dialog.geometry("420x200")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text="CONFIRMAR EXCLUSAO", font=("Segoe UI", 13, "bold")).pack(pady=(15, 8))
        ttk.Label(dialog, text='Tem certeza que deseja deletar:', font=("Segoe UI", 10)).pack()
        ttk.Label(dialog, text='"{}"'.format(nome), font=("Segoe UI", 11, "bold"),
                  foreground="#dc3545").pack(pady=5)
        ttk.Label(dialog, text="Esta acao eh IRREVERSIVEL!", font=("Segoe UI", 10),
                  foreground="#dc3545").pack()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=18)
        ttk.Button(btn_frame, text="NAO, CANCELAR", command=dialog.destroy,
                   bootstyle="secondary", width=15).pack(side=tk.LEFT, padx=8)

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
                   bootstyle="danger", width=15).pack(side=tk.LEFT, padx=8)

    def _novo_projeto_rapido(self):
        """Dialogo rapido para criar projeto."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Novo Projeto")
        dialog.geometry("450x200")
        dialog.transient(self.root)
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text="Nome do projeto:", font=("Segoe UI", 11)).pack(pady=(15, 5))
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

        ttk.Button(dialog, text="Criar e Iniciar", command=criar, bootstyle="success").pack(pady=12)

    def _atualizar_biblioteca(self):
        self.lista_bib.delete(0, tk.END)
        data = listar_biblioteca()
        for entry in data.get("entries", []):
            texto = "[%s] %s - %s" % (entry.get('media_type','?'), entry.get('source','?'), entry.get('quality','?'))
            self.lista_bib.insert(tk.END, texto)


def main():
    if TEM_TTB:
        root = ttk.Window(themename=TEMA)
    else:
        root = tk.Tk()
    app = Ultracut3GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()