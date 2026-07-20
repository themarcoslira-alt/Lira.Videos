"""
gui.py — Interface gráfica ULTRACUT3 (ttkbootstrap, 7 abas)
Com: painel de progresso, modo automático, campo de áudio, tema visual.
"""
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import json
import os
import threading
from pathlib import Path

# Tenta importar ttkbootstrap; fallback para ttk puro se não estiver instalado
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    TEMA = "litera"
    TEM_TTB = True
except ImportError:
    from tkinter import ttk
    TEMA = None
    TEM_TTB = False

from services.pipeline_service import PipelineService
from services.library import listar_biblioteca, remover_media
from config import PROJETOS_DIR, OUTPUT_DIR


STEP_NAMES = ["Transcrição", "Cenas", "Storyboard", "Mídias", "Render"]
STEP_ICONS = {"pendente": " ○ ", "andamento": " ⟳ ", "concluido": " ✓ ", "erro": " ✗ "}


class ProgressPanel:
    """Painel fixo de progresso das 5 etapas do pipeline."""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent, padding=5)
        self.frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.labels = []
        for i, nome in enumerate(STEP_NAMES):
            if i > 0:
                sep = ttk.Label(self.frame, text="  →  ", font=("", 10))
                sep.pack(side=tk.LEFT)
            lb = ttk.Label(self.frame, text=f"{STEP_ICONS['pendente']}{nome}",
                           font=("Segoe UI", 10), foreground="gray")
            lb.pack(side=tk.LEFT)
            self.labels.append(lb)

    def atualizar(self, step_index: int, status: str):
        """Atualiza ícone de uma etapa."""
        if 0 <= step_index < len(self.labels):
            icon = STEP_ICONS.get(status, STEP_ICONS["pendente"])
            cor = {"pendente": "gray", "andamento": "#0d6efd",
                   "concluido": "#198754", "erro": "#dc3545"}.get(status, "gray")
            self.labels[step_index].config(
                text=f"{icon}{STEP_NAMES[step_index]}",
                foreground=cor
            )
            self.frame.update_idletasks()

    def resetar(self):
        """Reseta todas as etapas para pendente."""
        for i in range(len(self.labels)):
            self.atualizar(i, "pendente")


class Ultracut3GUI:
    """Interface gráfica principal do ULTRACUT3."""

    def __init__(self, root):
        self.root = root
        self.root.title("ULTRACUT3 - Pipeline de Vídeo")
        self.root.geometry("1200x750")
        self.root.minsize(900, 650)

        self.pipeline = PipelineService()
        self.arquivo_audio = None
        self.pipeline_thread = None
        self.modo_automatico = tk.BooleanVar(value=False)

        self._build_menu()

        # Painel de progresso no topo
        self.progress_panel = ProgressPanel(self.root)

        self._build_notebook()
        self._status_bar()

        self._atualizar_lista_projetos()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Arquivo", menu=file_menu)
        file_menu.add_command(label="Novo Projeto", command=self._novo_projeto_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Sair", command=self.root.quit)
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Ajuda", menu=help_menu)
        help_menu.add_command(label="Sobre", command=self._sobre)

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.tab_projeto = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_projeto, text="Projeto")
        self._build_tab_projeto()

        self.tab_transcricao = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_transcricao, text="Transcrição")
        self._build_tab_transcricao()

        self.tab_cenas = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_cenas, text="Cenas")
        self._build_tab_cenas()

        self.tab_storyboard = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_storyboard, text="Storyboard")
        self._build_tab_storyboard()

        self.tab_midias = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_midias, text="Mídias")
        self._build_tab_midias()

        self.tab_render = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_render, text="Render")
        self._build_tab_render()

        self.tab_biblioteca = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_biblioteca, text="Biblioteca")
        self._build_tab_biblioteca()

    def _status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.status_label = ttk.Label(self.status_frame, text="Pronto", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress_bar = ttk.Progressbar(self.status_frame, mode='indeterminate', length=150)
        self.progress_bar.pack(side=tk.RIGHT, padx=5)

    # =============== ABA 1: PROJETO ===============
    def _build_tab_projeto(self):
        frame = self.tab_projeto

        ttk.Label(frame, text="Novo Projeto", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Nome do projeto:").grid(row=1, column=0, sticky=tk.W)
        self.entry_novo_projeto = ttk.Entry(frame, width=45)
        self.entry_novo_projeto.grid(row=2, column=0, sticky=tk.W, pady=3)
        ttk.Button(frame, text="Criar Projeto", command=self._criar_projeto, bootstyle="success").grid(row=2, column=1, padx=12)

        ttk.Label(frame, text="Projetos Existentes", font=("Segoe UI", 13, "bold")).grid(row=3, column=0, sticky=tk.W, pady=(18, 5))

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=4, column=0, columnspan=2, sticky=tk.NSEW)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.lista_projetos = tk.Listbox(list_frame, height=6, yscrollcommand=scrollbar.set)
        self.lista_projetos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.lista_projetos.yview)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Selecionar Projeto", command=self._selecionar_projeto, bootstyle="info-outline").pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_lista_projetos).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Deletar Projeto", command=self._deletar_projeto, bootstyle="danger-outline").pack(side=tk.LEFT, padx=4)

        self.projeto_info = ttk.Label(frame, text="", foreground="gray")
        self.projeto_info.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=5)

        # Modo Automático
        sep = ttk.Separator(frame, orient="horizontal")
        sep.grid(row=7, column=0, columnspan=2, sticky=tk.EW, pady=12)

        ttk.Label(frame, text="Pipeline", font=("Segoe UI", 13, "bold")).grid(row=8, column=0, sticky=tk.W, pady=(0, 5))
        auto_frame = ttk.Frame(frame)
        auto_frame.grid(row=9, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Checkbutton(auto_frame, text="Modo Automático (executa 5 etapas em sequência)",
                        variable=self.modo_automatico, bootstyle="success-round-toggle").pack(side=tk.LEFT)
        ttk.Label(auto_frame, text="  Desligado: clique etapa por etapa | Ligado: pipeline completo automático",
                  font=("", 9), foreground="gray").pack(side=tk.LEFT)

        grid_frame = ttk.Frame(frame)
        grid_frame.grid(row=10, column=0, columnspan=2, sticky=tk.NSEW)
        frame.grid_rowconfigure(10, weight=1)
        frame.grid_columnconfigure(0, weight=1)

    # =============== ABA 2: TRANSCRIÇÃO ===============
    def _build_tab_transcricao(self):
        frame = self.tab_transcricao

        ttk.Label(frame, text="Etapa 1: Transcrição", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Selecione o arquivo de áudio para transcrição (ou vídeo com extração automática de áudio):").pack(anchor=tk.W)

        file_frame = ttk.Frame(frame)
        file_frame.pack(fill=tk.X, pady=8)

        self.entry_audio = ttk.Entry(file_frame, width=65)
        self.entry_audio.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_frame, text="Selecionar Áudio/Vídeo", command=self._selecionar_audio,
                   bootstyle="info-outline").pack(side=tk.RIGHT, padx=6)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=6)
        ttk.Button(btn_frame, text="Iniciar Transcrição", command=self._iniciar_transcricao,
                   bootstyle="success").pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Resultado da Transcrição:").pack(anchor=tk.W, pady=(8, 3))
        self.texto_transcricao = scrolledtext.ScrolledText(frame, height=10, wrap=tk.WORD,
                                                           font=("Consolas", 10))
        self.texto_transcricao.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 3: CENAS ===============
    def _build_tab_cenas(self):
        frame = self.tab_cenas

        ttk.Label(frame, text="Etapa 2: Geração de Cenas", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Gerar Cenas", command=self._gerar_cenas,
                   bootstyle="success").pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Cenas Geradas:").pack(anchor=tk.W, pady=(8, 3))
        self.lista_cenas = tk.Listbox(frame, height=14, font=("Consolas", 10))
        self.lista_cenas.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 4: STORYBOARD ===============
    def _build_tab_storyboard(self):
        frame = self.tab_storyboard

        ttk.Label(frame, text="Etapa 3: Storyboard e B-roll", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        self.usar_claude_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Usar Claude (IA)", variable=self.usar_claude_var).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Gerar Storyboard", command=self._gerar_storyboard,
                   bootstyle="success").pack(side=tk.LEFT, padx=10)

        ttk.Label(frame, text="Storyboard:").pack(anchor=tk.W, pady=(8, 3))
        self.texto_storyboard = scrolledtext.ScrolledText(frame, height=13, wrap=tk.WORD,
                                                          font=("Consolas", 10))
        self.texto_storyboard.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 5: MÍDIAS ===============
    def _build_tab_midias(self):
        frame = self.tab_midias

        ttk.Label(frame, text="Etapa 4: Busca de Mídias", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Busca automática em Pexels, Pixabay e Unsplash (2 passadas: GREEN > YELLOW)").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Buscar Mídias", command=self._buscar_midias,
                   bootstyle="success").pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Resultados da Busca:").pack(anchor=tk.W, pady=(8, 3))
        self.texto_midias = scrolledtext.ScrolledText(frame, height=13, wrap=tk.WORD,
                                                      font=("Consolas", 10))
        self.texto_midias.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 6: RENDER ===============
    def _build_tab_render(self):
        frame = self.tab_render

        ttk.Label(frame, text="Etapa 5: Renderização", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Renderiza vídeo final com encoder h264_amf (AMD GPU)").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Renderizar Vídeo", command=self._renderizar,
                   bootstyle="success").pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Log de Renderização:").pack(anchor=tk.W, pady=(8, 3))
        self.texto_render = scrolledtext.ScrolledText(frame, height=13, wrap=tk.WORD,
                                                      font=("Consolas", 10))
        self.texto_render.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 7: BIBLIOTECA ===============
    def _build_tab_biblioteca(self):
        frame = self.tab_biblioteca

        ttk.Label(frame, text="Biblioteca de Mídia", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(frame, text="Mídias salvas localmente, organizadas por categoria.").pack(anchor=tk.W)
        ttk.Label(frame, text="Usada como fallback após esgotar 2 passadas de busca nova (máx 2 reusos por projeto).").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_biblioteca,
                   bootstyle="info-outline").pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Remover Selecionada", command=self._remover_biblioteca,
                   bootstyle="danger-outline").pack(side=tk.LEFT, padx=4)

        ttk.Label(frame, text="Mídias na Biblioteca:").pack(anchor=tk.W, pady=(8, 3))
        self.lista_biblioteca = tk.Listbox(frame, height=13, font=("Consolas", 10))
        self.lista_biblioteca.pack(fill=tk.BOTH, expand=True)
        self.lista_biblioteca.bind('<Double-Button-1>', lambda e: self._detalhes_biblioteca())

    # =============== MÉTODOS ===============
    def _atualizar_lista_projetos(self):
        self.lista_projetos.delete(0, tk.END)
        for p in self.pipeline.listar_projetos():
            self.lista_projetos.insert(tk.END, p.get("name", "Desconhecido"))

    def _criar_projeto(self):
        nome = self.entry_novo_projeto.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Digite um nome para o projeto")
            return
        result = self.pipeline.criar_projeto(nome)
        if result.get("success"):
            messagebox.showinfo("Sucesso", f"Projeto '{nome}' criado!")
            self.entry_novo_projeto.delete(0, tk.END)
            self._atualizar_lista_projetos()
        else:
            messagebox.showerror("Erro", result.get("error", "Erro desconhecido"))

    def _selecionar_projeto(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto na lista")
            return
        nome = self.lista_projetos.get(sel[0])
        self.pipeline.project_name = nome
        self.projeto_info.config(text=f"Projeto atual: {nome}")
        self.status_label.config(text=f"Projeto selecionado: {nome}")

    def _deletar_projeto(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto para deletar")
            return
        nome = self.lista_projetos.get(sel[0])
        if messagebox.askyesno("Confirmar", f"Deletar projeto '{nome}'?"):
            from mcp_server.project_tools import deletar_projeto
            result = deletar_projeto(nome)
            if result.get("success"):
                messagebox.showinfo("Sucesso", f"Projeto '{nome}' deletado")
                self._atualizar_lista_projetos()

    def _novo_projeto_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Novo Projeto")
        dialog.geometry("350x160")
        dialog.transient(self.root)

        ttk.Label(dialog, text="Nome do projeto:", font=("Segoe UI", 11)).pack(pady=(15, 5))
        entry = ttk.Entry(dialog, width=35)
        entry.pack(pady=5, padx=20)
        entry.focus()

        def criar():
            nome = entry.get().strip()
            if nome:
                result = self.pipeline.criar_projeto(nome)
                if result.get("success"):
                    self._atualizar_lista_projetos()
                    dialog.destroy()
                else:
                    messagebox.showerror("Erro", result.get("error"), parent=dialog)

        ttk.Button(dialog, text="Criar", command=criar, bootstyle="success").pack(pady=12)

    def _selecionar_audio(self):
        """Seleciona arquivo de áudio (ou vídeo com extração automática)."""
        arquivo = filedialog.askopenfilename(
            title="Selecionar Áudio ou Vídeo",
            filetypes=[
                ("Arquivos de áudio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                ("Arquivos de vídeo", "*.mp4 *.avi *.mov *.mkv *.webm"),
                ("Todos os formatos", "*.*")
            ]
        )
        if arquivo:
            self.entry_audio.delete(0, tk.END)
            self.entry_audio.insert(0, arquivo)
            self.arquivo_audio = arquivo
            ext = Path(arquivo).suffix.lower()
            if ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
                self.status_label.config(text=f"Áudio selecionado: {Path(arquivo).name}")
            else:
                self.status_label.config(text=f"Vídeo selecionado (áudio será extraído): {Path(arquivo).name}")

    def _extrair_audio_se_video(self, arquivo: str) -> str:
        """Se for vídeo, extrai áudio com ffmpeg. Se for áudio, retorna o próprio."""
        ext = Path(arquivo).suffix.lower()
        if ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
            return arquivo

        # Extrai áudio do vídeo
        import subprocess
        project_dir = PROJETOS_DIR / self.pipeline.project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        saida = str(project_dir / "audio_extraido.wav")

        cmd = ["ffmpeg", "-y", "-i", arquivo, "-vn",
               "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", saida]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and Path(saida).exists():
            return saida
        return arquivo

    def _iniciar_transcricao(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        arquivo = self.entry_audio.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de áudio ou vídeo")
            return

        self.progress_panel.resetar()
        self.progress_panel.atualizar(0, "andamento")
        self.status_label.config(text="Transcrevendo...")
        self.progress_bar.start()

        def task():
            # Extrai áudio se for vídeo
            audio_path = self._extrair_audio_se_video(arquivo)
            result = self.pipeline.transcrever(audio_path)
            self.root.after(0, lambda: self._transcricao_concluida(result))

        threading.Thread(target=task, daemon=True).start()

    def _transcricao_concluida(self, result):
        self.progress_bar.stop()
        self.texto_transcricao.delete(1.0, tk.END)
        if result.get("success"):
            self.texto_transcricao.insert(tk.END, result.get("texto", ""))
            self.progress_panel.atualizar(0, "concluido")
            self.status_label.config(text="Transcrição concluída")
        else:
            self.progress_panel.atualizar(0, "erro")
            self.texto_transcricao.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na transcrição")

    def _gerar_cenas(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        self.progress_panel.atualizar(1, "andamento")
        self.status_label.config(text="Gerando cenas...")
        self.progress_bar.start()

        def task():
            result = self.pipeline.gerar_cenas()
            self.root.after(0, lambda: self._cenas_concluidas(result))

        threading.Thread(target=task, daemon=True).start()

    def _cenas_concluidas(self, result):
        self.progress_bar.stop()
        self.lista_cenas.delete(0, tk.END)
        if result.get("success"):
            for cena in result.get("cenas", []):
                texto_resumo = cena.get("texto", "")[:80] + "..."
                self.lista_cenas.insert(tk.END, f"Cena {cena['id']}: {texto_resumo}")
            self.progress_panel.atualizar(1, "concluido")
            self.status_label.config(text=f"{result.get('cenas_count', 0)} cenas geradas")
        else:
            self.progress_panel.atualizar(1, "erro")
            self.lista_cenas.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro ao gerar cenas")

    def _gerar_storyboard(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        self.progress_panel.atualizar(2, "andamento")
        self.status_label.config(text="Gerando storyboard...")
        self.progress_bar.start()

        def task():
            result = self.pipeline.gerar_storyboard(self.usar_claude_var.get())
            self.root.after(0, lambda: self._storyboard_concluido(result))

        threading.Thread(target=task, daemon=True).start()

    def _storyboard_concluido(self, result):
        self.progress_bar.stop()
        self.texto_storyboard.delete(1.0, tk.END)
        if result.get("success"):
            for cena in result.get("storyboard", []):
                self.texto_storyboard.insert(tk.END,
                    f"Cena {cena['id']}: type={cena['scene_type']}, "
                    f"media={cena['media_preference']}, keywords={cena['keywords']}\n")
            self.progress_panel.atualizar(2, "concluido")
            self.status_label.config(text=f"Storyboard gerado (camada: {result.get('camada', 'local')})")
        else:
            self.progress_panel.atualizar(2, "erro")
            self.texto_storyboard.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro no storyboard")

    def _buscar_midias(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        self.progress_panel.atualizar(3, "andamento")
        self.status_label.config(text="Buscando mídias...")
        self.progress_bar.start()

        def task():
            self.pipeline.gerar_queries()
            result = self.pipeline.buscar_midias()
            self.root.after(0, lambda: self._midias_concluidas(result))

        threading.Thread(target=task, daemon=True).start()

    def _midias_concluidas(self, result):
        self.progress_bar.stop()
        self.texto_midias.delete(1.0, tk.END)
        if result.get("success"):
            self.texto_midias.insert(tk.END,
                f"Total: {result.get('total_scenes', 0)} cenas\n"
                f"Green: {result.get('green', 0)}  |  "
                f"Yellow: {result.get('yellow', 0)}  |  "
                f"Reused: {result.get('reused', 0)}  |  "
                f"Needs Media: {result.get('needs_media', 0)}\n\n")
            for r in result.get("resultados", []):
                if r.get("success"):
                    reused = " [REUSED]" if r.get("reused") else ""
                    self.texto_midias.insert(tk.END,
                        f"Cena {r.get('scene_id', '?')}: {r.get('quality', '?')} "
                        f"- {r.get('source', '?')} - {r.get('passada', '?')}{reused}\n")
                else:
                    needs = " [NEEDS MEDIA]" if r.get("needs_media") else " [FALHOU]"
                    self.texto_midias.insert(tk.END, f"Cena {r.get('scene_id', '?')}: {needs}\n")
            self.progress_panel.atualizar(3, "concluido")
            self.status_label.config(text="Busca de mídias concluída")
        else:
            self.progress_panel.atualizar(3, "erro")
            self.texto_midias.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na busca de mídias")

    def _renderizar(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        self.progress_panel.atualizar(4, "andamento")
        self.status_label.config(text="Renderizando...")
        self.progress_bar.start()

        def task():
            result = self.pipeline.renderizar()
            self.root.after(0, lambda: self._render_concluido(result))

        threading.Thread(target=task, daemon=True).start()

    def _render_concluido(self, result):
        self.progress_bar.stop()
        self.texto_render.delete(1.0, tk.END)
        if result.get("success"):
            self.texto_render.insert(tk.END,
                f"Renderização concluída!\n"
                f"Arquivo: {result.get('arquivo', 'N/A')}\n"
                f"Tamanho: {result.get('tamanho', 0)} bytes\n")
            self.progress_panel.atualizar(4, "concluido")
            self.status_label.config(text="Renderização concluída")
            messagebox.showinfo("Sucesso", "Vídeo renderizado com sucesso!")
        else:
            self.progress_panel.atualizar(4, "erro")
            self.texto_render.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na renderização")

    def _pipeline_completo(self):
        """Executa pipeline completo (modo automático ou manual)."""
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        arquivo = self.entry_audio.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de áudio ou vídeo")
            return

        if not self.modo_automatico.get():
            # Modo manual: executa a pipeline completa numa thread
            self._executar_sequencia_manual(arquivo)
        else:
            # Modo automático: encadeia etapa por etapa
            self._executar_sequencia_automatica()

    def _executar_sequencia_manual(self, arquivo: str):
        """Modo manual: executa pipeline completo de uma vez (comportamento original)."""
        self.progress_panel.resetar()
        self.status_label.config(text="Executando pipeline completo...")
        self.progress_bar.start()

        def task():
            # Extrai áudio se for vídeo
            audio_path = self._extrair_audio_se_video(arquivo)
            result = self.pipeline.executar_pipeline_completo(audio_path)
            self.root.after(0, lambda: self._pipeline_concluido(result))

        self.pipeline_thread = threading.Thread(target=task, daemon=True)
        self.pipeline_thread.start()

    def _executar_sequencia_automatica(self):
        """Modo automático: encadeia as 5 etapas, cada uma dispara a próxima."""
        self.progress_panel.resetar()
        self.progress_bar.start()

        def task():
            arquivo = self.entry_audio.get().strip()
            audio_path = self._extrair_audio_se_video(arquivo)

            # Etapa 1: Transcrição
            self.root.after(0, lambda: self.progress_panel.atualizar(0, "andamento"))
            self.root.after(0, lambda: self.status_label.config(text="[1/5] Transcrevendo..."))
            from services.transcriber import transcrever
            r1 = transcrever(self.pipeline.project_name, audio_path)
            if not r1.get("success"):
                self.root.after(0, lambda: self.progress_panel.atualizar(0, "erro"))
                self.root.after(0, lambda: self.progress_bar.stop())
                self.root.after(0, lambda: messagebox.showerror("Erro",
                    f"Etapa 1 (Transcrição) falhou:\n{r1.get('error', 'Erro desconhecido')}"))
                return
            self.root.after(0, lambda: self.progress_panel.atualizar(0, "concluido"))
            self.root.after(0, lambda: self.texto_transcricao.insert(tk.END, r1.get("texto", "")))

            # Etapa 2: Cenas
            self.root.after(0, lambda: self.progress_panel.atualizar(1, "andamento"))
            self.root.after(0, lambda: self.status_label.config(text="[2/5] Gerando cenas..."))
            from services.scene_builder import gerar_cenas
            r2 = gerar_cenas(self.pipeline.project_name)
            if not r2.get("success"):
                self.root.after(0, lambda: self.progress_panel.atualizar(1, "erro"))
                self.root.after(0, lambda: self.progress_bar.stop())
                self.root.after(0, lambda: messagebox.showerror("Erro",
                    f"Etapa 2 (Cenas) falhou:\n{r2.get('error', 'Erro desconhecido')}"))
                return
            self.root.after(0, lambda: self.progress_panel.atualizar(1, "concluido"))
            self.root.after(0, lambda: self._exibir_cenas(r2.get("cenas", [])))

            # Etapa 3: Storyboard
            self.root.after(0, lambda: self.progress_panel.atualizar(2, "andamento"))
            self.root.after(0, lambda: self.status_label.config(text="[3/5] Gerando storyboard..."))
            from services.broll_director import gerar_storyboard
            r3 = gerar_storyboard(self.pipeline.project_name, self.usar_claude_var.get())
            if not r3.get("success"):
                self.root.after(0, lambda: self.progress_panel.atualizar(2, "erro"))
                self.root.after(0, lambda: self.progress_bar.stop())
                self.root.after(0, lambda: messagebox.showerror("Erro",
                    f"Etapa 3 (Storyboard) falhou:\n{r3.get('error', 'Erro desconhecido')}"))
                return
            self.root.after(0, lambda: self.progress_panel.atualizar(2, "concluido"))

            # Queries (intermediário)
            from services.query_generator import gerar_queries
            gerar_queries(self.pipeline.project_name)

            # Etapa 4: Mídias
            self.root.after(0, lambda: self.progress_panel.atualizar(3, "andamento"))
            self.root.after(0, lambda: self.status_label.config(text="[4/5] Buscando mídias..."))
            from services.media_search import buscar_midias_projeto
            r4 = buscar_midias_projeto(self.pipeline.project_name)
            if not r4.get("success"):
                self.root.after(0, lambda: self.progress_panel.atualizar(3, "erro"))
                self.root.after(0, lambda: self.progress_bar.stop())
                self.root.after(0, lambda: messagebox.showerror("Erro",
                    f"Etapa 4 (Mídias) falhou:\n{r4.get('error', 'Erro desconhecido')}"))
                return
            self.root.after(0, lambda: self.progress_panel.atualizar(3, "concluido"))

            # Etapa 5: Render
            self.root.after(0, lambda: self.progress_panel.atualizar(4, "andamento"))
            self.root.after(0, lambda: self.status_label.config(text="[5/5] Renderizando..."))
            from services.video_builder import construir_video
            from services.video_encoder import renderizar_video
            build = construir_video(self.pipeline.project_name)
            if build.get("success") and build.get("arquivos_video") and build.get("arquivo_audio"):
                r5 = renderizar_video(build["arquivos_video"], build["arquivo_audio"],
                                      self.pipeline.project_name)
                if r5.get("success"):
                    self.root.after(0, lambda: self.progress_panel.atualizar(4, "concluido"))
                    self.root.after(0, lambda: self.texto_render.insert(tk.END,
                        f"Renderização concluída!\nArquivo: {r5.get('arquivo', 'N/A')}\n"))
                    self.root.after(0, lambda: self.progress_bar.stop())
                    self.root.after(0, lambda: self.status_label.config(text="Pipeline concluído com sucesso!"))
                    self.root.after(0, lambda: messagebox.showinfo("Sucesso",
                        "Pipeline automático concluído com sucesso!"))
                    return
            self.root.after(0, lambda: self.progress_panel.atualizar(4, "erro"))
            self.root.after(0, lambda: self.progress_bar.stop())
            self.root.after(0, lambda: messagebox.showerror("Erro",
                "Etapa 5 (Render) falhou: verifique se há arquivos de mídia e áudio"))

        threading.Thread(target=task, daemon=True).start()

    def _exibir_cenas(self, cenas: list):
        """Exibe cenas na aba de Cenas."""
        self.lista_cenas.delete(0, tk.END)
        for cena in cenas:
            texto = cena.get("texto", "")[:80] + "..."
            self.lista_cenas.insert(tk.END, f"Cena {cena['id']}: {texto}")

    def _pipeline_concluido(self, result):
        self.progress_bar.stop()
        if result.get("success"):
            self.progress_panel.atualizar(4, "concluido")
            self.status_label.config(text="Pipeline concluído com sucesso!")
            messagebox.showinfo("Sucesso", "Pipeline completo executado!")
        else:
            self.status_label.config(text="Pipeline concluído com erros")
            messagebox.showwarning("Aviso", "Pipeline concluído, mas algumas etapas podem ter falhado.")

    def _atualizar_biblioteca(self):
        self.lista_biblioteca.delete(0, tk.END)
        data = listar_biblioteca()
        for entry in data.get("entries", []):
            texto = (f"[{entry.get('media_type', '?')}] {entry.get('source', '?')} "
                     f"#{entry.get('id', '?')} - Qualidade: {entry.get('quality', '?')} "
                     f"- Categoria: {entry.get('scene_type', '?')}")
            self.lista_biblioteca.insert(tk.END, texto)
        self.status_label.config(text=f"Biblioteca: {data.get('total', 0)} mídias")

    def _remover_biblioteca(self):
        sel = self.lista_biblioteca.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione uma mídia para remover")
            return
        texto = self.lista_biblioteca.get(sel[0])
        try:
            parts = texto.split(" #")
            source_part = parts[0].split("] ")[1] if "]" in parts[0] else ""
            source = source_part.strip()
            id_part = parts[1].split(" ")[0] if len(parts) > 1 else ""
        except Exception:
            messagebox.showerror("Erro", "Não foi possível identificar a mídia")
            return
        if messagebox.askyesno("Confirmar", "Remover esta mídia da biblioteca?"):
            if remover_media(id_part, source):
                self._atualizar_biblioteca()
                messagebox.showinfo("Sucesso", "Mídia removida")

    def _detalhes_biblioteca(self):
        sel = self.lista_biblioteca.curselection()
        if not sel:
            return
        messagebox.showinfo("Detalhes", self.lista_biblioteca.get(sel[0]))

    def _sobre(self):
        tema_str = f"ttkbootstrap ({TEMA})" if TEM_TTB else "tkinter padrão"
        messagebox.showinfo("Sobre",
            "ULTRACUT3\n"
            "Pipeline automático de vídeo\n"
            f"GUI: {tema_str}\n"
            "Encoder: h264_amf (AMD GPU)\n"
            "Faster-Whisper + ffmpeg\n"
            "Fontes: Pexels, Pixabay, Unsplash\n"
            "Versão: 3.1 (GUI melhorada)")


def main():
    if TEM_TTB:
        root = ttk.Window(themename=TEMA)
    else:
        root = tk.Tk()
    app = Ultracut3GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()