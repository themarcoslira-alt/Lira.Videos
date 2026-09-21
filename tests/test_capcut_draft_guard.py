# -*- coding: utf-8 -*-
"""
Testes automatizados para a salvaguarda (Guard Assertion) em capcut_draft_imagens.py.

Garante:
1. draft_dir apontando para PROJETOS_DIR levanta RuntimeError antes de qualquer deleção.
2. draft_dir apontando para subpasta de PROJETOS_DIR levanta RuntimeError e não deleta arquivos.
3. draft_dir contendo arquivos-canário (lira_scene_plan.json, cenas.json, meta.json, word_timestamps.json)
   fora de PROJETOS_DIR levanta RuntimeError e preserva todos os arquivos intactos.
4. Rascunhos legítimos continuam sendo criados normalmente sem falso-positivo.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
import pytest

from config import PROJETOS_DIR
import capcut_draft_imagens as mod


class TestCapCutDraftGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="guard_test_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_guard_bloqueia_draft_dir_igual_projetos_dir(self):
        """Impede que draft_dir seja exatamente PROJETOS_DIR."""
        destino = PROJETOS_DIR.parent
        nome_projeto = PROJETOS_DIR.name

        with pytest.raises(RuntimeError) as exc_info:
            mod.criar_draft_imagens(
                nome_projeto,
                [],
                "",
                str(destino),
                nome_projeto=nome_projeto,
            )
        self.assertIn("SEGURANÇA", str(exc_info.value))
        self.assertIn("não pode ser a pasta de um projeto", str(exc_info.value))

    def test_guard_bloqueia_draft_dir_dentro_de_projetos_dir(self):
        """Impede que draft_dir seja uma pasta de projeto dentro de PROJETOS_DIR e preserva arquivos."""
        nome_proj = "teste_seguranca_guard_temp"
        pasta_proj = PROJETOS_DIR / nome_proj
        pasta_proj.mkdir(parents=True, exist_ok=True)
        arquivo_vital = pasta_proj / "arquivo_sensivel.txt"
        arquivo_vital.write_text("conteúdo vital que nunca pode ser apagado", encoding="utf-8")

        try:
            with pytest.raises(RuntimeError) as exc_info:
                mod.criar_draft_imagens(
                    nome_proj,
                    [],
                    "",
                    str(PROJETOS_DIR),
                    nome_projeto=nome_proj,
                )
            self.assertIn("SEGURANÇA", str(exc_info.value))
            # Garante que o arquivo sensível não foi tocado/apagado
            self.assertTrue(arquivo_vital.exists(), "O arquivo vital foi indevidamente apagado!")
            self.assertEqual(arquivo_vital.read_text(encoding="utf-8"), "conteúdo vital que nunca pode ser apagado")
        finally:
            if pasta_proj.exists():
                shutil.rmtree(pasta_proj, ignore_errors=True)

    def test_guard_bloqueia_canarios_fora_de_projetos_dir(self):
        """Impede deleção se draft_dir contiver arquivos-canário mesmo fora de PROJETOS_DIR."""
        draft_falso = self._tmp / "draft_com_canarios"
        draft_falso.mkdir(parents=True, exist_ok=True)

        canarios = [
            draft_falso / "lira_scene_plan.json",
            draft_falso / "cenas.json",
            draft_falso / "meta.json",
            draft_falso / "word_timestamps.json",
        ]
        for c in canarios:
            c.write_text('{"vital": true}', encoding="utf-8")

        with pytest.raises(RuntimeError) as exc_info:
            mod.criar_draft_imagens(
                draft_falso.name,
                [],
                "",
                str(self._tmp),
                nome_projeto=draft_falso.name,
            )

        self.assertIn("SEGURANÇA", str(exc_info.value))
        self.assertIn("arquivos vitais de projeto do Lira Studio", str(exc_info.value))
        for c in canarios:
            self.assertTrue(c.exists(), f"Canário {c.name} foi deletado indevidamente!")

    def test_draft_legitimo_sem_falso_positivo(self):
        """Garante que pastas legítimas fora de PROJETOS_DIR continuam funcionando normalmente."""
        destino_valido = self._tmp / "drafts_capcut"
        destino_valido.mkdir(parents=True, exist_ok=True)

        res = mod.criar_draft_imagens(
            "projeto_valido",
            [],
            "",
            str(destino_valido),
            nome_projeto="projeto_valido",
        )
        self.assertTrue(res.get("success"), f"Falha ao gerar draft legítimo: {res}")
        self.assertTrue(Path(res["draft_dir"]).exists())


if __name__ == "__main__":
    unittest.main()
