# -*- coding: utf-8 -*-
"""
tests/test_fallback_video_nao_preventivo.py
===========================================
Correção: `_fallback_video_para_imagem` era setado PREVENTIVAMENTE dentro de
`_processar_cena_individual`, fazendo o sistema pular a tentativa de vídeo e ir
direto para imagem sem testar o Flow.

Regras garantidas por esta suíte:
1. A flag só é ligada em UM ponto — a decisão canônica `_tratar_indicador_limite`,
   alimentada por evidência REAL do Flow (frases de limite, toasts ou
   `check_flow_credits`).
2. `_processar_cena_individual` NÃO liga a flag: ela apenas REGISTRA a falha real
   via `_marcar_falha_video` (video_tentado/video_falhou/video_falhou_motivo).
3. O guard de fallback (leitura da flag) permanece ANTES da tentativa por
   necessidade do pipeline, mas a flag só pode estar True após uma falha real.
4. Crédito esgotado detectado DURANTE a tentativa real marca a cena e usa o motivo
   canônico `credito_esgotado_video` (+ parar_fila).
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.character_service as char_svc
import services.playwright_flow as pf
import services.scene_plan_service as sps

SRC = Path("services/playwright_flow.py").read_text(encoding="utf-8")
TMP_ROOT = Path(tempfile.mkdtemp(prefix="fb_video_"))
LOG_ESPERADO = "[BROLL] Tentativa de vídeo falhou — fallback para imagem"


def _worker_sem_init():
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.page = None
    w._fallback_video_para_imagem = False
    w._fallback_video_para_imagem_motivo = ""
    return w


def _corpo_metodo(nome: str) -> str:
    """Trecho-fonte do método `nome` (do `def` até o próximo `def` de mesma indentação)."""
    ini = SRC.index(f"    def {nome}(")
    fim = SRC.index("\n    def ", ini + 10)
    return SRC[ini:fim]


class TestUmUnicoPontoLigaOFFallback(unittest.TestCase):

    def test_01_apenas_a_decisao_canonica_liga_a_flag(self):
        ocorrencias = [l for l in SRC.splitlines()
                       if "self._fallback_video_para_imagem = True" in l]
        self.assertEqual(len(ocorrencias), 1,
                         "a flag só pode ser ligada em UM ponto (decisão canônica)")
        ini = SRC.index("    def _tratar_indicador_limite(")
        fim = SRC.index("\n    def ", ini + 10)
        self.assertTrue(ini < SRC.index(ocorrencias[0]) < fim,
                        "a atribuição deve estar em _tratar_indicador_limite")

    def test_02_processar_cena_nao_liga_a_flag(self):
        trecho = _corpo_metodo("_processar_cena_individual")
        self.assertNotIn("self._fallback_video_para_imagem = True", trecho)

    def test_03_processar_cena_registra_a_falha_real_duas_vezes(self):
        trecho = _corpo_metodo("_processar_cena_individual")
        self.assertEqual(trecho.count("self._marcar_falha_video("), 2,
                         "pré-check (check_flow_credits) e pós-Create devem registrar a falha")

    def test_04_helper_registra_campos_e_log(self):
        trecho = _corpo_metodo("_marcar_falha_video")
        self.assertIn('"video_tentado": True', trecho)
        self.assertIn('"video_falhou": True', trecho)
        self.assertIn('"video_falhou_motivo": motivo', trecho)
        self.assertIn(LOG_ESPERADO, trecho)

    def test_05_guard_le_a_flag_com_motivo_auditavel(self):
        self.assertIn('getattr(self, "_fallback_video_para_imagem", False)', SRC)
        self.assertIn("_fallback_video_para_imagem_motivo", SRC)
        self.assertIn('self._fallback_video_para_imagem_motivo = ""', SRC)

    def test_06_decisao_canonica_grava_o_motivo(self):
        ini = SRC.index("self._fallback_video_para_imagem = True")
        self.assertIn('_fallback_video_para_imagem_motivo = f"credito_esgotado_video',
                      SRC[ini:ini + 500])


class TestMarcaFalhaRuntime(unittest.TestCase):
    """Runtime: a cena REALMENTE guarda a prova da tentativa (whitelist incluída)."""

    def setUp(self):
        self.projeto = "t_fb_video"
        self.pdir = TMP_ROOT / self.projeto
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        (self.pdir / sps.SCENE_PLAN_FILE).write_text(json.dumps({
            "cenas": [{"id": 1, "status": "PENDENTE", "tempo_inicio": 0.0}]
        }), encoding="utf-8")
        for modulo in (sps, char_svc):
            p = patch.object(modulo, "PROJETOS_DIR", TMP_ROOT)
            p.start()
            self.addCleanup(p.stop)

    def _cena1(self):
        plan = json.loads((self.pdir / sps.SCENE_PLAN_FILE).read_text(encoding="utf-8"))
        return plan["cenas"][0]

    def test_01_marcar_falha_video_persiste_os_tres_campos(self):
        w = _worker_sem_init()
        with patch.object(pf, "pw_log") as m_log:
            w._marcar_falha_video(self.projeto, 1, "credito_esgotado_video", "teste")
        cena = self._cena1()
        self.assertTrue(cena.get("video_tentado"))
        self.assertTrue(cena.get("video_falhou"))
        self.assertEqual(cena.get("video_falhou_motivo"), "credito_esgotado_video")
        mensagens = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertIn(LOG_ESPERADO, mensagens)

    def test_02_campos_estao_na_whitelist_de_edicao(self):
        r = sps.atualizar_cena(self.projeto, 1, {"video_tentado": True})
        self.assertTrue(r.get("success"))
        self.assertTrue(self._cena1().get("video_tentado"))

    def test_03_motivo_default_quando_omitido(self):
        w = _worker_sem_init()
        w._marcar_falha_video(self.projeto, 1)
        self.assertEqual(self._cena1().get("video_falhou_motivo"), "credito_esgotado_video")

    def test_04_cena_inexistente_nao_quebra(self):
        w = _worker_sem_init()
        w._marcar_falha_video(self.projeto, "abc")   # id inválido: nunca levanta
        w._marcar_falha_video(self.projeto, 999)     # cena inexistente: nunca levanta


class TestTarefa4DuranteTentativaReal(unittest.TestCase):

    def _trecho(self):
        ini = SRC.index('if not ok and res_msg in ("credito_esgotado_recolocado"')
        fim = SRC.index("if ok:", ini)
        return SRC[ini:fim]

    def test_01_marca_e_usa_motivo_canonico(self):
        trecho = self._trecho()
        self.assertIn('"video_tentado": True', trecho)
        self.assertIn('"video_falhou": True', trecho)
        self.assertIn('"video_falhou_motivo": _motivo_falha', trecho)
        self.assertIn('"video_falhou_motivo": _motivo_rec', trecho)
        self.assertIn('self.set_pause_reason("credito_esgotado_video", parar_fila=True)', trecho)

    def test_02_motivo_escolhido_pelo_tipo_da_falha(self):
        trecho = self._trecho()
        self.assertIn('_eh_falha_video = (res_msg == "credito_esgotado_video_recolocado")', trecho)
        self.assertIn('self.set_pause_reason("fim_fila_credito_zerado", parar_fila=True)', trecho)


if __name__ == "__main__":
    unittest.main()
