"""
tests/test_avatar_resolver_e_prompt_deteccao.py
================================================
Cobre as duas correções do motor oficial de geração (Playwright CDP):

1. `resolver_imagem_avatar_projeto` — busca DINÂMICA do avatar do projeto
   (identidade.json -> characters/*/reference.png -> references/*/reference.png),
   eliminando o caminho hardcoded de outra máquina.
2. `construir_prompt_diretor` — integra `detectar_personagens_cena` ao prompt
   final da cena (character_ref/personagem_ref + personagem_ref_imagem), para
   o Playwright saber qual personagem está presente e qual reference.png anexar.
"""

import sys
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJETOS_DIR
from services.character_service import salvar_identidade_projeto, resolver_imagem_avatar_projeto
from services.prompt_builder_service import construir_prompt_diretor

DUMMY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"


class TestResolverImagemAvatarProjeto(unittest.TestCase):
    """Busca dinâmica do avatar do projeto (sem caminho fixo)."""

    def setUp(self):
        self.proj = "test_avatar_resolver_proj"
        self.pdir = PROJETOS_DIR / self.proj
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.pdir, ignore_errors=True)

    def _salvar_identidade_com_imagem(self):
        img = self.pdir / "origem.png"
        img.write_bytes(DUMMY_PNG)
        # patch evita sincronizar na Biblioteca global real (evita efeito colateral)
        with patch("services.character_service.salvar_personagem_biblioteca_global"):
            salvar_identidade_projeto(
                projeto_id=self.proj,
                tipo="personagem",
                nome="Marcos",
                referencia_flow="@Marcos",
                arquivo_origem=str(img),
            )

    def test_resolve_imagem_da_identidade(self):
        """identidade.json -> imagem_abs (characters/Marcos/reference.png)."""
        self._salvar_identidade_com_imagem()
        r = resolver_imagem_avatar_projeto(self.proj)
        self.assertTrue(r, "deve resolver um caminho")
        self.assertTrue(Path(r).exists())
        self.assertEqual(Path(r).name, "reference.png")
        self.assertIn(str(self.pdir), r)

    def test_resolve_characters_glob_sem_identidade(self):
        """Fallback characters/<Nome>/reference.png sem identidade.json."""
        pasta = self.pdir / "characters" / "Ana"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "reference.png").write_bytes(DUMMY_PNG)
        r = resolver_imagem_avatar_projeto(self.proj)
        self.assertEqual(Path(r), pasta / "reference.png")

    def test_resolve_references_glob(self):
        """Fallback references/<alias>/reference.png."""
        pasta = self.pdir / "references" / "xaviera"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "reference.png").write_bytes(DUMMY_PNG)
        r = resolver_imagem_avatar_projeto(self.proj)
        self.assertEqual(Path(r), pasta / "reference.png")

    def test_sem_avatar_retorna_vazio(self):
        """Projeto sem nenhum avatar/referência -> \"\" (o chamador erra claro)."""
        self.assertEqual(resolver_imagem_avatar_projeto(self.proj), "")
        self.assertEqual(resolver_imagem_avatar_projeto(""), "")


class TestPromptBuilderIntegraDetecao(unittest.TestCase):
    """Prompt final da cena conhece o personagem presente e o reference.png."""

    def setUp(self):
        self.proj = "test_prompt_deteccao_proj"
        self.pdir = PROJETOS_DIR / self.proj
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        img = self.pdir / "origem.png"
        img.write_bytes(DUMMY_PNG)
        with patch("services.character_service.salvar_personagem_biblioteca_global"):
            salvar_identidade_projeto(
                projeto_id=self.proj,
                tipo="personagem",
                nome="Marcos",
                referencia_flow="@Marcos",
                arquivo_origem=str(img),
            )

    def tearDown(self):
        shutil.rmtree(self.pdir, ignore_errors=True)

    def test_cena_cita_personagem_marca_referencia(self):
        """Cena que cita o personagem -> prompt com @Nome + reference.png marcado."""
        cena = {"id": 1, "texto": "Marcos mostra a rosa no jardim"}
        res = construir_prompt_diretor(self.proj, cena, contexto_visual={"world": "garden"})
        self.assertIn("@Marcos", res["prompt_imagem"])
        self.assertEqual(cena.get("character_ref"), "@Marcos")
        self.assertEqual(cena.get("personagem_ref"), "@Marcos")
        img = cena.get("personagem_ref_imagem") or ""
        self.assertTrue(img, "personagem_ref_imagem deve apontar o reference.png do personagem")
        self.assertTrue(Path(img).exists())
        self.assertEqual(Path(img).name, "reference.png")
        self.assertEqual(cena.get("personagem_detectado", {}).get("nome"), "Marcos")

    def test_cena_sem_personagem_nao_inventa(self):
        """B-roll puro (sem nome citado) -> prompt sem @ e sem referência inventada."""
        cena = {"id": 2, "texto": "Close na casca de banana no solo"}
        res = construir_prompt_diretor(self.proj, cena, contexto_visual={"world": "garden"})
        self.assertNotIn("@", res["prompt_imagem"])
        self.assertFalse(cena.get("personagem_ref_imagem"))
        self.assertFalse(cena.get("personagem_ref"))


if __name__ == "__main__":
    unittest.main()
