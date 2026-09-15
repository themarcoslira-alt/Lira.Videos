# -*- coding: utf-8 -*-
"""
tests/test_io_timeout_e_referencia.py
=====================================
(1) reference.png GENÉRICA em projeto NOVO → validar identidade.json ANTES.
(2) Demora em "gerar restantes" → I/O com PRAZO + no máximo 1 retry + reset de
    status em LOTE (1 leitura + 1 gravação em vez de 2N operações de disco).

Nenhum arquivo real de projeto é tocado: os testes usam PROJETOS_DIR temporário
(ou patcheado) e arquivos descartáveis.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import services.character_service as char_svc
import services.playwright_flow as pf
import services.scene_plan_service as sps

TMP_ROOT = Path(tempfile.mkdtemp(prefix="io_ref_"))


def _worker_sem_init():
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.page = None
    return w


class TestTimeoutELeituraScenePlan(unittest.TestCase):
    """TAREFA 2a — prazo rígido de I/O + teto de tentativas."""

    def setUp(self):
        # `carregar_scene_plan` passa por sincronizar_trava_identidade_cenas, que
        # consulta o character_service (e esse faz mkdir de characters/). Sem patch,
        # os testes criariam pastas em projetos/ reais.
        self._patch = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_01_leitor_tem_prazo_rigido(self):
        p = TMP_ROOT / "grande.json"
        p.write_text("[]", encoding="utf-8")
        # 1ª chamada fixa o limite; 2ª (dentro do loop) salta além do prazo.
        chamadas = {"n": 0}

        def _monotonic_fake():
            chamadas["n"] += 1
            return 0.0 if chamadas["n"] == 1 else 999.0

        with patch.object(sps.time, "monotonic", _monotonic_fake):
            with self.assertRaises(TimeoutError):
                sps._ler_texto_scene_plan(p, timeout_s=5.0)

    def test_02_leitor_normal_le_bytes(self):
        p = TMP_ROOT / "ok.json"
        p.write_text('{"cenas": []}', encoding="utf-8")
        self.assertIn("cenas", sps._ler_texto_scene_plan(p))

    def test_03_teto_de_tentativas_e_2_sem_loop(self):
        projeto = "t_io_proj"
        (TMP_ROOT / projeto).mkdir(parents=True, exist_ok=True)
        (TMP_ROOT / projeto / sps.SCENE_PLAN_FILE).write_text('{"cenas": []}', encoding="utf-8")
        self.assertEqual(sps.SCENE_PLAN_MAX_TENTATIVAS_LEITURA, 2)
        self.assertEqual(sps.SCENE_PLAN_IO_TIMEOUT_SEG, 5.0)
        with patch.object(sps, "PROJETOS_DIR", TMP_ROOT), \
             patch.object(sps, "_ler_texto_scene_plan",
                          side_effect=PermissionError("travado")) as m_read:
            self.assertIsNone(sps.carregar_scene_plan(projeto))
        # leitura + 1 retry (antes: até 3 tentativas SEM prazo)
        self.assertEqual(m_read.call_count, sps.SCENE_PLAN_MAX_TENTATIVAS_LEITURA)

    def test_04_timeout_na_leitura_retorna_none_sem_travar(self):
        projeto = "t_io_timeout"
        (TMP_ROOT / projeto).mkdir(parents=True, exist_ok=True)
        (TMP_ROOT / projeto / sps.SCENE_PLAN_FILE).write_text('{"cenas": []}', encoding="utf-8")
        with patch.object(sps, "PROJETOS_DIR", TMP_ROOT), \
             patch.object(sps, "_ler_texto_scene_plan",
                          side_effect=TimeoutError("prazo")) as m_read:
            self.assertIsNone(sps.carregar_scene_plan(projeto))
        self.assertLessEqual(m_read.call_count, 2)

    def test_05_arquivo_real_carrega(self):
        projeto = "t_io_ok"
        pdir = TMP_ROOT / projeto
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / sps.SCENE_PLAN_FILE).write_text(json.dumps({
            "cenas": [{"id": 1, "status": "PENDENTE", "tempo_inicio": 0.0}]
        }), encoding="utf-8")
        with patch.object(sps, "PROJETOS_DIR", TMP_ROOT):
            plan = sps.carregar_scene_plan(projeto)
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan["cenas"]), 1)


class TestResetStatusEmLote(unittest.TestCase):
    """TAREFA 2b — 1 leitura + 1 gravação para todas as cenas pendentes."""

    def setUp(self):
        self.projeto = "t_reset_proj"
        self.pdir = TMP_ROOT / self.projeto
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        (self.pdir / sps.SCENE_PLAN_FILE).write_text(json.dumps({
            "cenas": [
                {"id": 1, "status": "GERANDO", "tempo_inicio": 0.0},
                {"id": 2, "status": "ENVIANDO", "tempo_inicio": 5.0},
                {"id": 3, "status": "BAIXADA", "tempo_inicio": 10.0},
            ]
        }), encoding="utf-8")
        self._patch = patch.object(sps, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # Evita mkdir em projetos/ reais via character_service (ver teste anterior).
        self._patch_char = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch_char.start()
        self.addCleanup(self._patch_char.stop)

    def _status(self):
        plan = json.loads((self.pdir / sps.SCENE_PLAN_FILE).read_text(encoding="utf-8"))
        return {c["id"]: c["status"] for c in plan["cenas"]}

    def test_01_batch_grava_uma_unica_vez(self):
        with patch.object(sps, "salvar_scene_plan", wraps=sps.salvar_scene_plan) as m_salvar:
            r = sps.resetar_status_cenas(self.projeto, [1, 2], sps.STATUS_PENDENTE)
        self.assertTrue(r["success"])
        self.assertEqual(r["atualizadas"], 2)
        self.assertEqual(m_salvar.call_count, 1, "deve gravar UMA vez (não por cena)")
        self.assertEqual(self._status(), {1: "PENDENTE", 2: "PENDENTE", 3: "BAIXADA"})

    def test_02_sem_mudanca_nao_grava(self):
        sps.resetar_status_cenas(self.projeto, [1, 2], sps.STATUS_PENDENTE)
        mtime_antes = (self.pdir / sps.SCENE_PLAN_FILE).stat().st_mtime_ns
        with patch.object(sps, "salvar_scene_plan") as m_salvar:
            r2 = sps.resetar_status_cenas(self.projeto, [1, 2], sps.STATUS_PENDENTE)
        self.assertEqual(r2["atualizadas"], 0)
        m_salvar.assert_not_called()
        self.assertEqual((self.pdir / sps.SCENE_PLAN_FILE).stat().st_mtime_ns, mtime_antes)

    def test_03_lista_vazia_nao_le_nem_grava(self):
        with patch.object(sps, "carregar_scene_plan") as m_carregar:
            r = sps.resetar_status_cenas(self.projeto, [], sps.STATUS_PENDENTE)
        self.assertTrue(r["success"])
        m_carregar.assert_not_called()

    def test_04_status_invalido_recusado(self):
        r = sps.resetar_status_cenas(self.projeto, [1], "NAO_EXISTE")
        self.assertFalse(r["success"])
        self.assertEqual(self._status()[1], "GERANDO")

    def test_05_iniciar_fila_usa_o_reset_em_lote(self):
        src = Path("services/api_v2.py").read_text(encoding="utf-8")
        self.assertIn("scene_plan_svc.resetar_status_cenas(", src)
        self.assertNotIn(
            'atualizar_status_cena(projeto_id, int(c["id"]), scene_plan_svc.STATUS_PENDENTE)', src
        )


class TestReferenciaAntesDoFallback(unittest.TestCase):
    """TAREFA 1/2/3 — identidade.json validada ANTES de qualquer 'reference.png'."""

    def setUp(self):
        self.projeto = "t_ref_novo"
        self.pdir = TMP_ROOT / self.projeto
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        self._patch = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _identidade(self, com_foto=True, nome="Marcos"):
        ref_dir = self.pdir / "characters" / nome
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref = ref_dir / "reference.png"
        if com_foto:
            ref.write_bytes(b"\x89PNG\r\n\x1a\nfoto-de-teste")
        (self.pdir / "identidade.json").write_text(json.dumps({
            "tipo": "personagem", "nome": nome, "referencia_flow": f"@{nome}",
            "imagem": f"characters/{nome}/reference.png",
            "imagem_abs": str(ref) if com_foto else "",
            "personagens": [{"nome": nome, "referencia_flow": f"@{nome}", "principal": True}],
        }, ensure_ascii=False), encoding="utf-8")
        return ref

    def test_01_helper_sem_identidade_devolve_vazio(self):
        w = _worker_sem_init()
        self.assertEqual(w._resolver_imagem_oficial_personagem(self.projeto), "")

    def test_02_helper_com_identidade_e_foto_devolve_o_arquivo(self):
        ref = self._identidade(com_foto=True)
        w = _worker_sem_init()
        resolvido = w._resolver_imagem_oficial_personagem(self.projeto)
        self.assertTrue(resolvido)
        self.assertEqual(Path(resolvido).resolve(), ref.resolve())

    def test_03_helper_identidade_sem_foto_devolve_vazio(self):
        self._identidade(com_foto=False)
        w = _worker_sem_init()
        self.assertEqual(w._resolver_imagem_oficial_personagem(self.projeto), "")

    def test_04_projeto_novo_aborta_sem_png_generica(self):
        motivos = []
        ok = pf.incluir_referencia_personagem(None, projeto_id=self.projeto,
                                              nome_personagem="Marcos",
                                              tag_personagem="@Marcos",
                                              motivo_erro=motivos)
        self.assertFalse(ok)
        # Sem identidade válida o anexo é CANCELADO sem usar a PNG genérica.
        self.assertIn("reference.png", motivos[-1])
        self.assertIn("CANCELADO", motivos[-1])

    def test_05_identidade_sem_foto_aborta_e_nomeia_reference_png(self):
        self._identidade(com_foto=False)
        motivos = []
        ok = pf.incluir_referencia_personagem(None, projeto_id=self.projeto,
                                              nome_personagem="Marcos",
                                              tag_personagem="@Marcos",
                                              motivo_erro=motivos)
        self.assertFalse(ok)
        self.assertIn("reference.png", motivos[-1])
        self.assertIn("CANCELADO", motivos[-1])

    def test_06_identidade_valida_nao_aborta_por_falta_de_imagem(self):
        self._identidade(com_foto=True)
        motivos = []
        ok = pf.incluir_referencia_personagem(None, projeto_id=self.projeto,
                                              nome_personagem="Marcos",
                                              tag_personagem="@Marcos",
                                              motivo_erro=motivos)
        # page=None impede o anexo de fato, mas o motivo NÃO pode ser "sem imagem".
        self.assertFalse(ok)
        self.assertFalse(any("sem imagem de referência real" in m for m in motivos))

    def test_07_selecionar_referencia_usa_caminho_real(self):
        self._identidade(com_foto=True)
        w = _worker_sem_init()
        w.page = MagicMock()
        capturado = {}

        def _fake(page, reference_path="reference.png", **kw):
            capturado["reference_path"] = reference_path
            return True

        with patch.object(pf, "incluir_referencia_personagem", side_effect=_fake):
            ok = w._selecionar_referencia_flow(projeto_id=self.projeto, tipo="personagem")
        self.assertTrue(ok)
        caminho = Path(capturado["reference_path"])
        self.assertTrue(caminho.is_absolute(), "não pode ser o literal 'reference.png'")
        self.assertTrue(caminho.exists())

    def test_08_selecionar_referencia_aborta_em_projeto_novo(self):
        w = _worker_sem_init()
        w.page = MagicMock()
        with patch.object(pf, "incluir_referencia_personagem") as m_inc:
            ok = w._selecionar_referencia_flow(projeto_id=self.projeto, tipo="personagem")
        self.assertFalse(ok)
        m_inc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
