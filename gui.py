"""
gui.py — Interface gráfica ULTRACUT3 (tkinter, 7 abas)
A Biblioteca de Mídia foi removida e depois restaurada (aba 7) com regras novas.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import json
import os
import threading
from pathlib import Path
from services.pipeline_service import PipelineService
from services.library import listar_biblioteca, remover_media
from config import PROJETOS_DIR, OUTPUT_DIR


class Ultracut3GUI:
    """Interface gráfica principal do ULTRACUT3."""

    def __init__(self, root):
        self.root = root
        self.root.title("ULTRACUT3 - Pipeline de Vídeo")
        self.root.geometry("1200x700")
        self.root.minsize(900, 600)

        self.pipeline = PipelineService()
        self.arquivo_video = None
        self.pipeline_thread = None

        self._build_menu()
        self._build_notebook()
        self._status_bar()

        # Carrega projetos na inicialização
        self._atualizar_lista_projetos()

    def _build_menu(self):
        """Menu superior."""
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
        """Cria abas do notebook."""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Aba 1: Projeto
        self.tab_projeto = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_projeto, text="Projeto")
        self._build_tab_projeto()

        # Aba 2: Transcrição
        self.tab_transcricao = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_transcricao, text="Transcrição")
        self._build_tab_transcricao()

        # Aba 3: Cenas
        self.tab_cenas = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_cenas, text="Cenas")
        self._build_tab_cenas()

        # Aba 4: Storyboard
        self.tab_storyboard = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_storyboard, text="Storyboard")
        self._build_tab_storyboard()

        # Aba 5: Mídias
        self.tab_midias = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_midias, text="Mídias")
        self._build_tab_midias()

        # Aba 6: Render
        self.tab_render = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_render, text="Render")
        self._build_tab_render()

        # Aba 7: Biblioteca de Mídia
        self.tab_biblioteca = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_biblioteca, text="Biblioteca")
        self._build_tab_biblioteca()

    def _status_bar(self):
        """Barra de status inferior."""
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=5, pady=2)

        self.status_label = ttk.Label(self.status_frame, text="Pronto", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_bar = ttk.Progressbar(self.status_frame, mode='indeterminate', length=150)
        self.progress_bar.pack(side=tk.RIGHT, padx=5)

    # =============== ABA 1: PROJETO ===============
    def _build_tab_projeto(self):
        frame = ttk.Frame(self.tab_projeto, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # Novo projeto
        ttk.Label(frame, text="Novo Projeto", font=("", 12, "bold")).grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Label(frame, text="Nome do projeto:").grid(row=1, column=0, sticky=tk.W)
        self.entry_novo_projeto = ttk.Entry(frame, width=40)
        self.entry_novo_projeto.grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Button(frame, text="Criar Projeto", command=self._criar_projeto).grid(row=2, column=1, padx=10)

        # Lista de projetos
        ttk.Label(frame, text="Projetos Existentes", font=("", 12, "bold")).grid(row=3, column=0, sticky=tk.W, pady=(20, 5))

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=4, column=0, columnspan=2, sticky=tk.NSEW)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.lista_projetos = tk.Listbox(list_frame, height=8, yscrollcommand=scrollbar.set)
        self.lista_projetos.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.lista_projetos.yview)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)

        ttk.Button(btn_frame, text="Selecionar Projeto", command=self._selecionar_projeto).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_lista_projetos).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Deletar Projeto", command=self._deletar_projeto).pack(side=tk.LEFT, padx=5)

        # Informações do projeto
        self.projeto_info = ttk.Label(frame, text="", foreground="gray")
        self.projeto_info.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=5)

        grid_frame = ttk.Frame(frame)
        grid_frame.grid(row=7, column=0, columnspan=2, sticky=tk.NSEW)
        frame.grid_rowconfigure(7, weight=1)
        frame.grid_columnconfigure(0, weight=1)

    # =============== ABA 2: TRANSCRIÇÃO ===============
    def _build_tab_transcricao(self):
        frame = ttk.Frame(self.tab_transcricao, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Etapa 1: Transcrição", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Label(frame, text="Selecione o arquivo de vídeo para transcrição:").pack(anchor=tk.W)

        file_frame = ttk.Frame(frame)
        file_frame.pack(fill=tk.X, pady=5)

        self.entry_video = ttk.Entry(file_frame, width=60)
        self.entry_video.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_frame, text="Selecionar Vídeo", command=self._selecionar_video).pack(side=tk.RIGHT, padx=5)

        ttk.Button(frame, text="Iniciar Transcrição", command=self._iniciar_transcricao).pack(anchor=tk.W, pady=10)

        ttk.Label(frame, text="Resultado da Transcrição:").pack(anchor=tk.W)
        self.texto_transcricao = scrolledtext.ScrolledText(frame, height=12, wrap=tk.WORD)
        self.texto_transcricao.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 3: CENAS ===============
    def _build_tab_cenas(self):
        frame = ttk.Frame(self.tab_cenas, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Etapa 2: Geração de Cenas", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Gerar Cenas", command=self._gerar_cenas).pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Cenas Geradas:").pack(anchor=tk.W)
        self.lista_cenas = tk.Listbox(frame, height=15)
        self.lista_cenas.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 4: STORYBOARD ===============
    def _build_tab_storyboard(self):
        frame = ttk.Frame(self.tab_storyboard, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Etapa 3: Storyboard e B-roll", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        self.usar_claude_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Usar Claude (IA)", variable=self.usar_claude_var).pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="Gerar Storyboard", command=self._gerar_storyboard).pack(side=tk.LEFT, padx=10)

        ttk.Label(frame, text="Storyboard:").pack(anchor=tk.W)
        self.texto_storyboard = scrolledtext.ScrolledText(frame, height=15, wrap=tk.WORD)
        self.texto_storyboard.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 5: MÍDIAS ===============
    def _build_tab_midias(self):
        frame = ttk.Frame(self.tab_midias, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Etapa 4: Busca de Mídias", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Label(frame, text="Busca automática em Pexels, Pixabay e Unsplash (2 passadas: GREEN > YELLOW)").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Buscar Mídias", command=self._buscar_midias).pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Resultados da Busca:").pack(anchor=tk.W)
        self.texto_midias = scrolledtext.ScrolledText(frame, height=15, wrap=tk.WORD)
        self.texto_midias.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 6: RENDER ===============
    def _build_tab_render(self):
        frame = ttk.Frame(self.tab_render, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Etapa 5: Renderização", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Label(frame, text="Renderiza vídeo final com encoder h264_amf (AMD GPU)").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Renderizar Vídeo", command=self._renderizar).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Iniciar Pipeline Completo", command=self._pipeline_completo).pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Log de Renderização:").pack(anchor=tk.W)
        self.texto_render = scrolledtext.ScrolledText(frame, height=15, wrap=tk.WORD)
        self.texto_render.pack(fill=tk.BOTH, expand=True)

    # =============== ABA 7: BIBLIOTECA ===============
    def _build_tab_biblioteca(self):
        frame = ttk.Frame(self.tab_biblioteca, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Biblioteca de Mídia", font=("", 12, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Label(frame, text="Mídias salvas localmente, organizadas por categoria.").pack(anchor=tk.W)
        ttk.Label(frame, text="Usada como fallback após esgotar 2 passadas de busca nova (máx 2 reusos por projeto).").pack(anchor=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Atualizar Lista", command=self._atualizar_biblioteca).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Remover Selecionada", command=self._remover_biblioteca).pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="Mídias na Biblioteca:").pack(anchor=tk.W)
        self.lista_biblioteca = tk.Listbox(frame, height=15)
        self.lista_biblioteca.pack(fill=tk.BOTH, expand=True)
        self.lista_biblioteca.bind('<Double-Button-1>', lambda e: self._detalhes_biblioteca())

    # =============== MÉTODOS ===============
    def _atualizar_lista_projetos(self):
        self.lista_projetos.delete(0, tk.END)
        projetos = self.pipeline.listar_projetos()
        for p in projetos:
            nome = p.get("name", "Desconhecido")
            self.lista_projetos.insert(tk.END, nome)

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
        if messagebox.askyesno("Confirmar", f"Deletar projeto '{nome}'? Esta ação não pode ser desfeita."):
            from mcp_server.project_tools import deletar_projeto
            result = deletar_projeto(nome)
            if result.get("success"):
                messagebox.showinfo("Sucesso", f"Projeto '{nome}' deletado")
                self._atualizar_lista_projetos()
            else:
                messagebox.showerror("Erro", result.get("error", "Erro desconhecido"))

    def _novo_projeto_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Novo Projeto")
        dialog.geometry("300x150")
        dialog.transient(self.root)

        ttk.Label(dialog, text="Nome do projeto:").pack(pady=10)
        entry = ttk.Entry(dialog, width=30)
        entry.pack(pady=5)
        entry.focus()

        def criar():
            nome = entry.get().strip()
            if nome:
                result = self.pipeline.criar_projeto(nome)
                if result.get("success"):
                    self._atualizar_lista_projetos()
                    dialog.destroy()
                else:
                    messagebox.showerror("Erro", result.get("error", "Erro"), parent=dialog)

        ttk.Button(dialog, text="Criar", command=criar).pack(pady=10)

    def _selecionar_video(self):
        arquivo = filedialog.askopenfilename(
            title="Selecionar Vídeo",
            filetypes=[("Arquivos de vídeo", "*.mp4 *.avi *.mov *.mkv *.webm"), ("Todos", "*.*")]
        )
        if arquivo:
            self.entry_video.delete(0, tk.END)
            self.entry_video.insert(0, arquivo)
            self.arquivo_video = arquivo

    def _iniciar_transcricao(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        arquivo = self.entry_video.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de vídeo")
            return

        self.status_label.config(text="Transcrevendo...")
        self.progress_bar.start()

        def task():
            result = self.pipeline.transcrever(arquivo)
            self.root.after(0, lambda: self._transcricao_concluida(result))

        threading.Thread(target=task, daemon=True).start()

    def _transcricao_concluida(self, result):
        self.progress_bar.stop()
        self.texto_transcricao.delete(1.0, tk.END)
        if result.get("success"):
            texto = result.get("texto", "")
            self.texto_transcricao.insert(tk.END, texto)
            self.status_label.config(text="Transcrição concluída")
            messagebox.showinfo("Sucesso", f"Transcrição concluída: {result.get('segments', 0)} segmentos")
        else:
            self.texto_transcricao.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na transcrição")
            messagebox.showerror("Erro", result.get("error", "Erro na transcrição"))

    def _gerar_cenas(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return

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
            self.status_label.config(text=f"{result.get('cenas_count', 0)} cenas geradas")
            messagebox.showinfo("Sucesso", f"{result.get('cenas_count', 0)} cenas geradas")
        else:
            self.lista_cenas.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro ao gerar cenas")

    def _gerar_storyboard(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return

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
            camada = result.get("camada", "local")
            cenas = result.get("storyboard", [])
            for cena in cenas:
                self.texto_storyboard.insert(tk.END,
                    f"Cena {cena['id']}: type={cena['scene_type']}, "
                    f"media={cena['media_preference']}, "
                    f"keywords={cena['keywords']}\n")
            self.status_label.config(text=f"Storyboard gerado (camada: {camada})")
        else:
            self.texto_storyboard.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro no storyboard")

    def _buscar_midias(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return

        self.status_label.config(text="Buscando mídias...")
        self.progress_bar.start()

        def task():
            # Primeiro garante que as queries foram geradas
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
                f"Green: {result.get('green', 0)}\n"
                f"Yellow: {result.get('yellow', 0)}\n"
                f"Reused (biblioteca): {result.get('reused', 0)}\n"
                f"Needs Media: {result.get('needs_media', 0)}\n\n")

            for r in result.get("resultados", []):
                if r.get("success"):
                    reused = " [REUSED]" if r.get("reused") else ""
                    self.texto_midias.insert(tk.END,
                        f"Cena {r.get('scene_id', '?')}: {r.get('quality', '?')}"
                        f" - {r.get('source', '?')} - {r.get('passada', '?')}"
                        f"{reused}\n")
                else:
                    needs = " [NEEDS MEDIA]" if r.get("needs_media") else " [FALHOU]"
                    self.texto_midias.insert(tk.END, f"Cena {r.get('scene_id', '?')}: {needs}\n")

            self.status_label.config(text="Busca de mídias concluída")
        else:
            self.texto_midias.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na busca de mídias")

    def _renderizar(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return

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
            self.status_label.config(text="Renderização concluída")
            messagebox.showinfo("Sucesso", "Vídeo renderizado com sucesso!")
        else:
            self.texto_render.insert(tk.END, f"Erro: {result.get('error', 'Erro desconhecido')}")
            self.status_label.config(text="Erro na renderização")

    def _pipeline_completo(self):
        if not self.pipeline.project_name:
            messagebox.showwarning("Aviso", "Selecione um projeto primeiro")
            return
        arquivo = self.entry_video.get().strip()
        if not arquivo:
            messagebox.showwarning("Aviso", "Selecione um arquivo de vídeo")
            return

        self.status_label.config(text="Executando pipeline completo...")
        self.progress_bar.start()

        def task():
            result = self.pipeline.executar_pipeline_completo(arquivo)
            self.root.after(0, lambda: self._pipeline_concluido(result))

        self.pipeline_thread = threading.Thread(target=task, daemon=True)
        self.pipeline_thread.start()

    def _pipeline_concluido(self, result):
        self.progress_bar.stop()
        if result.get("success"):
            self.status_label.config(text="Pipeline concluído com sucesso!")
            messagebox.showinfo("Sucesso", "Pipeline completo executado!")
        else:
            self.status_label.config(text="Pipeline concluído com erros")
            messagebox.showwarning("Aviso", "Pipeline concluído, mas algumas etapas podem ter falhado. Verifique os logs.")

    def _atualizar_biblioteca(self):
        self.lista_biblioteca.delete(0, tk.END)
        data = listar_biblioteca()
        for entry in data.get("entries", []):
            texto = f"[{entry.get('media_type', '?')}] {entry.get('source', '?')} #{entry.get('id', '?')} "
            texto += f"- Qualidade: {entry.get('quality', '?')} - Categoria: {entry.get('scene_type', '?')}"
            self.lista_biblioteca.insert(tk.END, texto)

        self.status_label.config(text=f"Biblioteca: {data.get('total', 0)} mídias")

    def _remover_biblioteca(self):
        sel = self.lista_biblioteca.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione uma mídia para remover")
            return
        texto = self.lista_biblioteca.get(sel[0])
        # Extrai source e id do texto
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
            else:
                messagebox.showerror("Erro", "Não foi possível remover a mídia")

    def _detalhes_biblioteca(self):
        sel = self.lista_biblioteca.curselection()
        if not sel:
            return
        texto = self.lista_biblioteca.get(sel[0])
        messagebox.showinfo("Detalhes", texto)

    def _sobre(self):
        messagebox.showinfo("Sobre",
            "ULTRACUT3\n"
            "Pipeline automático de vídeo\n"
            "Encoder: h264_amf (AMD GPU)\n"
            "Faster-Whisper + moviepy + ffmpeg\n"
            "Fontes: Pexels, Pixabay, Unsplash\n"
            "Versão: Reconstrução 2026")


def main():
    root = tk.Tk()
    app = Ultracut3GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()