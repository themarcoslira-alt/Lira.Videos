"""
gui.py — Interface gráfica ULTRACUT3 (ttkbootstrap, 9 abas)
Com: fluxo automático padrão, progresso em tempo real, logs no projeto.
"""
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import json, os, threading, time
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
from services.event_logger import ler_eventos, listar_categorias
from config import PROJETOS_DIR, OUTPUT_DIR

STEP_NAMES = ["Transcrição", "Cenas", "Storyboard", "Mídias", "Render"]
STEP_ICONS = {"pendente": " ○ ", "andamento": " ⟳ ", "concluido": " ✓ ", "erro": " ✗ "}


class ProgressPanel:
    """Painel de progresso das 5 etapas + indicadores em tempo real."""

    def __init__(self, parent, on_iniciar_pipeline=None):
        self.parent = parent
        self.on_iniciar_pipeline = on_iniciar_pipeline
        self.frame = ttk.Frame(parent, padding=8)
        self.frame.pack(fill=tk.X, padx=10, pady=5)

        # Linha das 5 etapas
        self.labels = []
        etapa_frame = ttk.Frame(self.frame)
        etapa_frame.pack(fill=tk.X)
        for i, nome in enumerate(STEP_NAMES):
            if i > 0:
                sep = ttk.Label(etapa_frame, text="  →  ", font=("", 10))
                sep.pack(side=tk.LEFT)
            lb = ttk.Label(etapa_frame, text=f"{STEP_ICONS['pendente']}{nome}",
                           font=("Segoe UI", 10), foreground="gray")
            lb.pack(side=tk.LEFT)
            self.labels.append(lb)

        # Status atual
        self.status_atual = ttk.Label(self.frame, text="Pronto — selecione um projeto e áudio",
                                      font=("Segoe UI", 10), foreground="gray")
        self.status_atual.pack(anchor=tk.W, pady=(5, 0))

        # Última ação
        self.ultima_acao = ttk.Label(self.frame, text="", font=("Segoe UI", 9), foreground="gray")
        self.ultima_acao.pack(anchor=tk.W)

        # Barra de progresso geral (0-100%)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self.frame, variable=self.progress_var,
                                             maximum=100, length=400, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=3)

        self.tempo_inicio = None
        self._pipeline_passos = 0
        self._pipeline_total = 5

    def atualizar_etapa(self, step_index: int, status: str, mensagem: str = ""):
        if 0 <= step_index < len(self.labels):
            icon = STEP_ICONS.get(status, STEP_ICONS["pendente"])
            cor = {"pendente": "gray", "andamento": "#0d6efd",
                   "concluido": "#198754", "erro": "#dc3545"}.get(status, "gray")
            self.labels[step_index].config(text=f"{icon}{STEP_NAMES[step_index]}", foreground=cor)
            self.frame.update_idletasks()

        if status == "andamento":
            self.status_atual.config(text=f"{mensagem}", foreground="#0d6efd")
        elif status == "concluido":
            self.ultima_acao.config(text=f"✓ {mensagem}", foreground="#198754")
        elif status == "erro":
            self.ultima_acao.config(text=f"✗ {mensagem}", foreground="#dc3545")

        # Atualiza barra de progresso
        passos_feitos = sum(1 for i in range(len(self.labels))
                           if self.labels[i].cget("foreground") == "#198754")
        passo_andamento = sum(1 for i in range(len(self.labels))
                             if self.labels[i].cget("foreground") == "#0d6efd")
        total = passos_feitos + (passo_andamento * 0.3)
        pct = min(100, (total / self._pipeline_total) * 100)
        self.progress_var.set(pct)

    def resetar(self):
        for i in range(len(self.labels)):
            self.atualizar_etapa(i, "pendente", "")
        self.status_atual.config(text="Pronto", foreground="gray")
        self.ultima_acao.config(text="", foreground="gray")
        self.progress_var.set(0)
        self.tempo_inicio = None


class Ultracut3GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ULTRACUT3 - Pipeline de Vídeo")
        self.root.geometry("1200x750")
        self.root.minsize(900, 650)

        self.pipeline = PipelineService()
        self.arquivo_audio = None
        self.pipeline_thread = None
        self.modo_automatico = tk.BooleanVar(value=True)  # ATIVADO por padrão

        # Callback de progresso
        self.pipeline.set_progress_callback(self._on_pipeline_progress)

        self._build_menu()

        # Painel de progresso no topo
        self.progress_panel = ProgressPanel(self.root)

        self._build_notebook()
        self._status_bar()
        self._atualizar_lista_projetos()

    def _on_pipeline_progress(self, step_index: int, status: str, message: str):
        """Callback chamado pelo pipeline para atualizar progresso."""
        self.root.after(0, lambda: self.progress_panel.atualizar_etapa(step_index, status, message))

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

        # Aba Progresso (tempo real)
        self.tab_progresso = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_progresso, text="Progresso")
        self._build_tab_progresso()

        # Aba Logs do Projeto
        self.tab_logs_projeto = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(self.tab_logs_projeto, text="Logs do Projeto")
        self._build_tab_logs_projeto()

    def _status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.status_label = ttk.Label(self.status_frame, text="Pronto", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.spinner = ttk.Progressbar(self.status_frame, mode='indeterminate', length=150)
        self.spinner.pack(side=tk.RIGHT, padx=5)

    # ===== ABA PROJETO =====
    def _build_tab_projeto(self):
        frame = self.tab_projeto

        ttk.Label(frame, text="Novo Projeto", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(frame, text="Nome do projeto:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.entry_novo_projeto = ttk.Entry(frame, width=45)
        self.entry_novo_projeto.grid(row=2, column=0, sticky=tk.W, pady=3)

        ttk.Label(frame, text="Arquivo de áudio/vídeo:").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        audio_frame = ttk.Frame(frame)
        audio_frame.grid(row=4, column=0, sticky=tk.W)
        self.entry_audio = ttk.Entry(audio_frame, width=60)
        self.entry_audio.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(audio_frame, text="Selecionar Áudio/Vídeo", command=self._selecionar_audio,
                   bootstyle="info-outline").pack(side=tk.RIGHT, padx=6)

        ttk.Button(frame, text="Criar Projeto", command=self._criar_projeto_com_audio,
                   bootstyle="success").grid(row=5, column=0, pady=10, sticky=tk.W)

        ttk.Separator(frame, orient="horizontal").grid(row=6, column=0, columnspan=2, sticky=tk.EW, pady=8)

        ttk.Label(frame, text="Projetos Existentes", font=("Segoe UI", 13, "bold")).grid(row=7, column=0, sticky=tk.W)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=8, column=0, columnspan=2, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.lista_projetos = tk.Listbox(list_frame, height=6, yscrollcommand=scrollbar.set)
        self.lista_projetos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.lista_projetos.yview)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Selecionar Projeto", command=self._selecionar_projeto).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_lista_projetos).pack(side=tk.LEFT, padx=4)

        self.projeto_info = ttk.Label(frame, text="", foreground="gray")
        self.projeto_info.grid(row=10, column=0, columnspan=2, sticky=tk.W)

        ttk.Separator(frame, orient="horizontal").grid(row=11, column=0, columnspan=2, sticky=tk.EW, pady=8)

        ttk.Label(frame, text="Pipeline", font=("Segoe UI", 13, "bold")).grid(row=12, column=0, sticky=tk.W)
        auto_frame = ttk.Frame(frame)
        auto_frame.grid(row=13, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Checkbutton(auto_frame, text="Modo Automático (executa 5 etapas em sequência)",
                        variable=self.modo_automatico, bootstyle="success-round-toggle").pack(side=tk.LEFT)
        ttk.Label(auto_frame, text="  ATIVADO por padrão — 1 clique inicia tudo",
                  font=("", 9), foreground="gray").pack(side=tk.LEFT)

        btn_pipeline = ttk.Frame(frame)
        btn_pipeline.grid(row=14, column=0, columnspan=2, pady=5)
        ttk.Button(btn_pipeline, text="▶ Iniciar Pipeline Completo", command=self._pipeline_completo,
                   bootstyle="success", width=35).pack(side=tk.LEFT, padx=5)

        frame.grid_rowconfigure(15, weight=1)
        frame.grid_columnconfigure(0, weight=1)

    # ===== ABA TRANSCRIÇÃO =====
    def _build_tab_transcricao(self):
        frame = self.tab_transcricao
        ttk.Label(frame, text="Transcrição", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="Resultado da transcrição será exibido aqui.").pack(anchor=tk.W, pady=5)
        self.texto_transcricao = scrolledtext.ScrolledText(frame, height=14, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_transcricao.pack(fill=tk.BOTH, expand=True)

    # ===== ABA CENAS =====
    def _build_tab_cenas(self):
        frame = self.tab_cenas
        ttk.Label(frame, text="Cenas Geradas", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        self.lista_cenas = tk.Listbox(frame, height=16, font=("Consolas", 10))
        self.lista_cenas.pack(fill=tk.BOTH, expand=True)

    # ===== ABA STORYBOARD =====
    def _build_tab_storyboard(self):
        frame = self.tab_storyboard
        ttk.Label(frame, text="Storyboard e B-roll", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        self.texto_storyboard = scrolledtext.ScrolledText(frame, height=16, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_storyboard.pack(fill=tk.BOTH, expand=True)

    # ===== ABA MÍDIAS =====
    def _build_tab_midias(self):
        frame = self.tab_midias
        ttk.Label(frame, text="Mídias Encontradas", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        self.texto_midias = scrolledtext.ScrolledText(frame, height=16, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_midias.pack(fill=tk.BOTH, expand=True)

    # ===== ABA RENDER =====
    def _build_tab_render(self):
        frame = self.tab_render
        ttk.Label(frame, text="Renderização", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        self.texto_render = scrolledtext.ScrolledText(frame, height=16, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_render.pack(fill=tk.BOTH, expand=True)

    # ===== ABA BIBLIOTECA =====
    def _build_tab_biblioteca(self):
        frame = self.tab_biblioteca
        ttk.Label(frame, text="Biblioteca de Mídia", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="Mídias salvas localmente (fallback após 2 passadas de busca)").pack(anchor=tk.W)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_biblioteca).pack(side=tk.LEFT, padx=4)
        self.lista_biblioteca = tk.Listbox(frame, height=14, font=("Consolas", 10))
        self.lista_biblioteca.pack(fill=tk.BOTH, expand=True)

    # ===== ABA PROGRESSO (tempo real) =====
    def _build_tab_progresso(self):
        frame = self.tab_progresso
        ttk.Label(frame, text="Acompanhamento em Tempo Real", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)

        self.prog_status = ttk.Label(frame, text="Aguardando início do pipeline...",
                                     font=("Segoe UI", 11), foreground="gray")
        self.prog_status.pack(anchor=tk.W, pady=10)

        self.prog_ultima = ttk.Label(frame, text="", font=("Segoe UI", 10), foreground="gray")
        self.prog_ultima.pack(anchor=tk.W)

        self.prog_proxima = ttk.Label(frame, text="", font=("Segoe UI", 10), foreground="gray")
        self.prog_proxima.pack(anchor=tk.W)

        self.prog_tempo = ttk.Label(frame, text="", font=("Segoe UI", 10), foreground="gray")
        self.prog_tempo.pack(anchor=tk.W)

        ttk.Label(frame, text="Últimas ações do log:", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(15, 5))
        self.prog_log = scrolledtext.ScrolledText(frame, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.prog_log.pack(fill=tk.BOTH, expand=True)

    # ===== ABA LOGS DO PROJETO =====
    def _build_tab_logs_projeto(self):
        frame = self.tab_logs_projeto
        ttk.Label(frame, text="Logs Salvos do Projeto", font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="Registros salvos em projetos/<nome>/logs.json").pack(anchor=tk.W, pady=5)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Atualizar Logs", command=self._atualizar_logs_projeto).pack(side=tk.LEFT, padx=4)
        self.texto_logs_projeto = scrolledtext.ScrolledText(frame, height=16, wrap=tk.WORD, font=("Consolas", 10))
        self.texto_logs_projeto.pack(fill=tk.BOTH, expand=True)

    # ===== MÉTODOS =====
    def _selecionar_audio(self):
        arquivo = filedialog.askopenfilename(
            title="Selecionar Áudio ou Vídeo",
            filetypes=[("Áudio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                       ("Vídeo", "*.mp4 *.avi *.mov *.mkv"), ("Todos", "*.*")]
        )
        if arquivo:
            self.entry_audio.delete(0, tk.END)
            self.entry_audio.insert(0, arquivo)
            self.arquivo_audio = arquivo
            self.status_label.config(text=f"Arquivo: {Path(arquivo).name}")

    def _criar_projeto_com_audio(self):
        nome = self.entry_novo_projeto.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Digite um nome para o projeto")
            return
        arquivo = self.entry_audio.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de áudio/vídeo")
            return

        result = self.pipeline.criar_projeto(nome, arquivo)
        if result.get("success"):
            # Projeto já fica selecionado automaticamente
            self.projeto_info.config(text=f"Projeto atual: {nome} (com áudio)")
            self.status_label.config(text=f"Projeto '{nome}' criado e selecionado")
            self.entry_novo_projeto.delete(0, tk.END)
            self._atualizar_lista_projetos()
            messagebox.showinfo("Sucesso", f"Projeto '{nome}' criado com áudio!")

            # Se modo automático, dispara pipeline imediatamente
            if self.modo_automatico.get():
                self.root.after(500, lambda: self._pipeline_completo())
        else:
            messagebox.showerror("Erro", result.get("error", "Erro"))

    def _selecionar_projeto(self):
        sel = self.lista_projetos.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um projeto na lista")
            return
        nome = self.lista_projetos.get(sel[0])
        self.pipeline.project_name = nome
        meta = self.pipeline._carregar_meta()
        audio = meta.get("arquivo_audio", "")
        self.projeto_info.config(text=f"Projeto atual: {nome}")
        self.status_label.config(text=f"Projeto selecionado: {nome}")
        if audio and Path(audio).exists():
            self.entry_audio.delete(0, tk.END)
            self.entry_audio.insert(0, audio)
            self.arquivo_audio = audio

    def _atualizar_lista_projetos(self):
        self.lista_projetos.delete(0, tk.END)
        for p in self.pipeline.listar_projetos():
            nome = p.get("name", "Desconhecido")
            audio = p.get("arquivo_audio", "")
            label = nome
            if audio:
                label += " [áudio]"
            self.lista_projetos.insert(tk.END, label)

    def _extrair_audio_se_video(self, arquivo: str) -> str:
        ext = Path(arquivo).suffix.lower()
        if ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
            return arquivo
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

    def _pipeline_completo(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione ou crie um projeto primeiro")
            return
        arquivo = self.entry_audio.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de áudio/vídeo")
            return

        self.progress_panel.resetar()
        self.progress_panel.status_atual.config(text="Iniciando pipeline automático...", foreground="#0d6efd")
        self.spinner.start()

        def task():
            try:
                audio_path = self._extrair_audio_se_video(arquivo)
                result = self.pipeline.executar_pipeline_completo(audio_path)

                self.root.after(0, lambda: self._pipeline_finalizado(result))
            except Exception as e:
                self.root.after(0, lambda: self._pipeline_erro(str(e)))

        self.pipeline_thread = threading.Thread(target=task, daemon=True)
        self.pipeline_thread.start()

    def _pipeline_finalizado(self, result):
        self.spinner.stop()
        if result.get("success"):
            self.progress_panel.atualizar_etapa(4, "concluido", "Pipeline completo!")
            self.status_label.config(text="Pipeline concluído com sucesso!")
            messagebox.showinfo("Sucesso", "Pipeline completo!")
        else:
            self.status_label.config(text="Pipeline com erros")
            messagebox.showwarning("Aviso", "Pipeline concluído com alguns erros. Verifique os logs.")

    def _pipeline_erro(self, erro):
        self.spinner.stop()
        self.status_label.config(text=f"Erro no pipeline: {erro}")
        messagebox.showerror("Erro", f"Pipeline falhou:\n{erro}")

    def _atualizar_biblioteca(self):
        self.lista_biblioteca.delete(0, tk.END)
        data = listar_biblioteca()
        for entry in data.get("entries", []):
            texto = (f"[{entry.get('media_type', '?')}] {entry.get('source', '?')} "
                     f"#{entry.get('id', '?')} - {entry.get('quality', '?')} "
                     f"- {entry.get('scene_type', '?')}")
            self.lista_biblioteca.insert(tk.END, texto)
        self.status_label.config(text=f"Biblioteca: {data.get('total', 0)} mídias")

    def _atualizar_logs_projeto(self):
        self.texto_logs_projeto.delete(1.0, tk.END)
        logs = self.pipeline.get_logs_projeto()
        for log in logs[-50:]:
            ts = log.get("ts", "")
            etapa = log.get("etapa", "?")
            status = log.get("status", "?")
            detalhes = log.get("detalhes", {})
            msg = detalhes.get("texto", log.get("message", "")) if isinstance(detalhes, dict) else ""
            self.texto_logs_projeto.insert(tk.END,
                f"[{ts}] [{etapa}] [{status}] {msg}\n")

    def _novo_projeto_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Novo Projeto")
        dialog.geometry("400x220")
        dialog.transient(self.root)
        ttk.Label(dialog, text="Nome do projeto:", font=("Segoe UI", 11)).pack(pady=(15, 5))
        entry = ttk.Entry(dialog, width=40)
        entry.pack(pady=5)
        entry.focus()
        ttk.Label(dialog, text="Arquivo de áudio:").pack()
        audio_entry = ttk.Entry(dialog, width=40)
        audio_entry.pack(pady=5)

        def sel_audio():
            f = filedialog.askopenfilename(title="Selecionar Áudio",
                filetypes=[("Áudio", "*.mp3 *.wav *.m4a *.aac"), ("Vídeo", "*.mp4"), ("Todos", "*.*")])
            if f:
                audio_entry.delete(0, tk.END)
                audio_entry.insert(0, f)

        ttk.Button(dialog, text="Selecionar", command=sel_audio).pack()

        def criar():
            nome = entry.get().strip()
            audio = audio_entry.get().strip()
            if nome and audio:
                result = self.pipeline.criar_projeto(nome, audio)
                if result.get("success"):
                    self.entry_novo_projeto.delete(0, tk.END)
                    self.entry_audio.delete(0, tk.END)
                    self.entry_audio.insert(0, audio)
                    self.arquivo_audio = audio
                    self.projeto_info.config(text=f"Projeto atual: {nome}")
                    self._atualizar_lista_projetos()
                    dialog.destroy()
                else:
                    messagebox.showerror("Erro", result.get("error"), parent=dialog)

        ttk.Button(dialog, text="Criar e Selecionar", command=criar, bootstyle="success").pack(pady=12)

    def _sobre(self):
        tema_str = f"ttkbootstrap ({TEMA})" if TEM_TTB else "tkinter padrão"
        messagebox.showinfo("Sobre",
            "ULTRACUT3 v3.5\nPipeline automático de vídeo\n"
            f"GUI: {tema_str}\nEncoder: h264_amf (AMD GPU)\n"
            "Faster-Whisper + ffmpeg\n"
            "Fontes: Pexels, Pixabay, Unsplash")


def main():
    if TEM_TTB:
        root = ttk.Window(themename=TEMA)
    else:
        root = tk.Tk()
    app = Ultracut3GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()