# -*- coding: utf-8 -*-
"""
tests/test_efeitos_transicoes_estabilidade.py
=============================================
Suíte de testes de estabilidade, integridade e regressão para o ecossistema
de Efeitos e Transições do Lira Studio & CapCut Desktop.
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

# Garante import do diretório raiz
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.capcut_library_service import capcut_library, TRANSICOES_ALIASES
from services.scene_plan_service import (
    TRANSICIONES_TIPOS,
    TRANSICION_DURACION_MIN_MS,
    TRANSICION_DURACION_MAX_MS,
    aplicar_transicion_cena,
    aplicar_transicoes_em_lote,
)
import capcut_draft_imagens as draft_img
from services.video_builder import _gerar_comando_kenburns


def _criar_png_minimo(pasta: Path, nome="img.png", w=64, h=64) -> Path:
    """Cria um PNG RGB válido em disco."""
    def chunk(tipo: bytes, dados: bytes) -> bytes:
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    dst = pasta / nome
    dst.write_bytes(png)
    return dst


class TestCatalogoEResolucaoTransicoes(unittest.TestCase):
    """Testa o carregamento do catálogo e resolução exata de transições e aliases."""

    def test_catalogo_carrega_transicoes_obrigatorias(self):
        catalogo = capcut_library.carregar_catalogo(forcar_reload=True)
        self.assertGreaterEqual(len(catalogo), 5, "Catálogo deve conter ao menos 5 transições catalogadas")
        ids_presentes = {t["id"] for t in catalogo}
        obrigatorios = {"bordas_difusas", "sobrepor", "combinar", "circulo", "retalhos_do_caos", "barra_de_luz", "espelho"}
        for item_id in obrigatorios:
            self.assertIn(item_id, ids_presentes, f"Transição obrigatória ausente no catálogo: {item_id}")

    def test_resolucao_transicoes_diretas(self):
        casos = [
            ("bordas_difusas", "7670460216856628500", "Bordas difusas"),
            ("sobrepor", "6917578154089386498", "Sobrepor"),
            ("combinar", "6724845717472416269", "Combinar"),
            ("circulo", "6725767129519362573", "Círculo"),
            ("retalhos_do_caos", "7665625788557069588", "Retalhos do caos"),
            ("barra_de_luz", "7678682228766870805", "Barra de luz"),
            ("espelho", "6848792278710882824", "Espelho"),
        ]
        for tid, expected_eid, expected_name in casos:
            res = capcut_library.resolver_material_transicao(tid, duracao_ms=600)
            self.assertIsNotNone(res, f"Falha ao resolver transição direta: {tid}")
            self.assertEqual(res["type"], "transition")
            self.assertEqual(res["effect_id"], expected_eid)
            self.assertEqual(res["name"], expected_name)
            self.assertEqual(res["duration"], 600_000, "Duração deve ser 600.000 us (600ms)")
            self.assertTrue(len(res["id"]) >= 32, "ID do material deve ser UUID válido")

    def test_resolucao_aliases_ui(self):
        """Garante que opções da UI (dissolve, fade_out, fade_in, etc.) resolvem para materiais válidos."""
        aliases_test = [
            ("dissolve", "6724845717472416269"),      # resolve para Combinar
            ("crossfade", "6724845717472416269"),     # resolve para Combinar
            ("fade_out", "6917578154089386498"),      # resolve para Sobrepor
            ("fade_in", "7678682228766870805"),       # resolve para Barra de luz
            ("mirror", "6848792278710882824"),        # resolve para Espelho
            ("flip", "6848792278710882824"),          # resolve para Espelho
            ("glitch", "7665625788557069588"),        # resolve para Retalhos do caos
        ]
        for alias, expected_eid in aliases_test:
            res = capcut_library.resolver_material_transicao(alias, duracao_ms=500)
            self.assertIsNotNone(res, f"Alias '{alias}' não pôde ser resolvido")
            self.assertEqual(res["effect_id"], expected_eid, f"Alias '{alias}' mapeou para effect_id inesperado")

    def test_corte_seco_retorna_none(self):
        for seco in ["none", "None", "nenhuma", "corte_seco", ""]:
            res = capcut_library.resolver_material_transicao(seco)
            self.assertIsNone(res, f"'{seco}' deveria retornar None (corte seco)")

    def test_transicoes_disponiveis_web(self):
        itens = capcut_library.obter_transicoes_disponiveis()
        self.assertGreater(len(itens), 1)
        self.assertEqual(itens[0]["id"], "none")
        ids = [i["id"] for i in itens]
        self.assertIn("barra_de_luz", ids)
        self.assertIn("espelho", ids)
        self.assertIn("bordas_difusas", ids)


class TestKeyframesKenBurns(unittest.TestCase):
    """Testa geração de keyframes de zoom e pan expandidos para CapCut 9.x."""

    def test_presets_zoom_e_pan(self):
        dur_us = 5_000_000  # 5s
        presets = [
            ("zoom_in", 2, "KFTypeScaleX", 1.0, 1.15),
            ("zoom_out", 2, "KFTypeScaleX", 1.15, 1.0),
            ("zoom_in_slow", 2, "KFTypeScaleX", 1.0, 1.08),
            ("zoom_out_slow", 2, "KFTypeScaleX", 1.08, 1.0),
        ]
        for mp, count_props, check_prop, val_de, val_para in presets:
            kfs = draft_img._gerar_keyframes_zoom(dur_us, ativo=True, motion_preset=mp)
            self.assertEqual(len(kfs), count_props, f"Preset {mp} deve ter {count_props} propriedades de escala")
            prop = next((p for p in kfs if p["property_type"] == check_prop), None)
            self.assertIsNotNone(prop, f"Propriedade {check_prop} não encontrada em {mp}")
            kfl = prop["keyframe_list"]
            self.assertEqual(kfl[0]["time_offset"], 0)
            self.assertEqual(kfl[0]["values"][0], val_de)
            self.assertEqual(kfl[1]["time_offset"], dur_us)
            self.assertEqual(kfl[1]["values"][0], val_para)

    def test_presets_pan_vertical(self):
        dur_us = 4_000_000
        kfs_up = draft_img._gerar_keyframes_zoom(dur_us, ativo=True, motion_preset="pan_up")
        self.assertEqual(len(kfs_up), 3, "pan_up deve conter ScaleX, ScaleY e PositionY")
        posY_up = next((p for p in kfs_up if p["property_type"] == "KFTypePositionY"), None)
        self.assertIsNotNone(posY_up)
        self.assertEqual(posY_up["keyframe_list"][0]["values"][0], 0.0)
        self.assertEqual(posY_up["keyframe_list"][1]["values"][0], 0.06)

        kfs_down = draft_img._gerar_keyframes_zoom(dur_us, ativo=True, motion_preset="pan_down")
        posY_down = next((p for p in kfs_down if p["property_type"] == "KFTypePositionY"), None)
        self.assertIsNotNone(posY_down)
        self.assertEqual(posY_down["keyframe_list"][1]["values"][0], -0.06)

    def test_estatico_retorna_vazio(self):
        kfs = draft_img._gerar_keyframes_zoom(3_000_000, ativo=True, motion_preset="estatico")
        self.assertEqual(kfs, [])


class TestVideoBuilderFFmpeg(unittest.TestCase):
    """Testa geração de comandos FFmpeg com novos presets de Ken Burns."""

    def test_comandos_ffmpeg_ken_burns(self):
        modos = ["zoom_in", "zoom_out", "zoom_in_slow", "zoom_out_slow", "pan_up", "pan_down", "pan_right", "pan_left"]
        for modo in modos:
            cmd = _gerar_comando_kenburns(
                foto_path="test.png",
                output_path="test.mp4",
                duracao=4.0,
                indice_cena=1,
                preset=modo,
            )
            cmd_str = " ".join(cmd)
            self.assertIn("-vf", cmd)
            self.assertIn("zoompan=", cmd_str)
            self.assertIn("-loop", cmd)


class TestExportCapCutComTransicoes(unittest.TestCase):
    """Testa a exportação real de um draft nativo 9.x com transições e validação estrutural."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="lira_trans_test_"))
        self.drafts_dir = self._tmp / "drafts"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_export_draft_com_varias_transicoes(self):
        img1 = _criar_png_minimo(self._tmp, "01.png")
        img2 = _criar_png_minimo(self._tmp, "02.png")
        img3 = _criar_png_minimo(self._tmp, "03.png")

        cenas = [
            {
                "start": 0.0,
                "duracao": 4.0,
                "arquivo": str(img1),
                "media_type": "photo",
                "transicao_saida": {"tipo": "barra_de_luz", "duracao_ms": 500},
                "ken_burns_ativo": True,
                "motion_preset": "zoom_in_slow",
            },
            {
                "start": 4.0,
                "duracao": 4.0,
                "arquivo": str(img2),
                "media_type": "photo",
                "transicao_saida": {"tipo": "espelho", "duracao_ms": 500},
                "ken_burns_ativo": True,
                "motion_preset": "pan_up",
            },
            {
                "start": 8.0,
                "duracao": 4.0,
                "arquivo": str(img3),
                "media_type": "photo",
                # Última cena: não deve ter transição aplicada no draft final
                "transicao_saida": {"tipo": "bordas_difusas", "duracao_ms": 500},
                "ken_burns_ativo": False,
                "motion_preset": "estatico",
            },
        ]

        res = draft_img.criar_draft_imagens(
            project_name="projeto_trans_test",
            lista_cenas=cenas,
            arquivo_audio="",
            destino_drafts=str(self.drafts_dir),
            nome_projeto="projeto_trans_test",
        )
        self.assertTrue(res.get("success"), f"Export falhou: {res}")
        draft_path = Path(res["draft_dir"])
        dc_path = draft_path / "draft_content.json"
        self.assertTrue(dc_path.exists())

        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        transitions = dc["materials"].get("transitions", [])
        self.assertEqual(len(transitions), 2, "Devem existir exatamente 2 transições (cenas 1 e 2; última não tem)")

        t_names = [t["name"] for t in transitions]
        self.assertIn("Barra de luz", t_names)
        self.assertIn("Espelho", t_names)

        # Validação estrutural do draft pelo validador oficial
        from services.capcut_validator import validar_draft_content
        val = validar_draft_content("projeto_trans_test", draft_path=draft_path)
        self.assertTrue(val.get("ok"), f"Validação do draft falhou: {val}")


if __name__ == "__main__":
    unittest.main()
