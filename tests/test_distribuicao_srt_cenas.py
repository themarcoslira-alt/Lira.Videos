# -*- coding: utf-8 -*-
"""Distribuição automática de tipos de cena (RETENÇÃO | CORPO | GANCHO) por SRT.

Cobre services/scene_plan_service.py:
  - calculate_retencao_gancho()
  - detect_fala_em_intervalo()
  - aplicação dentro do gerar_scene_plan() (abertura/fecho em vídeo e
    alternância do corpo conforme há fala no intervalo da cena).
"""
import contextlib
import io
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJETOS_DIR
from services.scene_plan_service import (
    calculate_retencao_gancho,
    detect_fala_em_intervalo,
    gerar_scene_plan,
)

PROJ = "_t_srt_dist"
PROJ_SEM_SRT = "_t_srt_dist_sem_srt"


def _fmt_ts(seg: float) -> str:
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = int(seg % 60)
    ms = int(round((seg - int(seg)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_texto(cues):
    """cues: [(start_seg, end_seg, texto)] -> SRT padrão."""
    blocos = [
        f"{i}\n{_fmt_ts(a)} --> {_fmt_ts(b)}\n{tx}"
        for i, (a, b, tx) in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocos) + "\n"


def _cenas_json(n_cenas, duracao=3.0, texto="Scene speech line"):
    """cenas.json sintético em sequência (sem silêncio entre cenas)."""
    cenas = []
    for i in range(n_cenas):
        ini = round(i * duracao, 3)
        fim = round(ini + duracao, 3)
        cenas.append({
            "id": i + 1,
            "texto": texto,
            "timestamps": [f"00:{int(ini):02d}"],
            "topic": "",
            "start_time": ini,
            "end_time": fim,
            "duration": duracao,
            "previous_scene_id": i,
            "next_scene_id": i + 2,
            "previous_context": "",
            "next_context": "",
            "transcript_lines": [],
        })
    return cenas


def _criar_projeto(nome, cenas, srt_texto=None):
    pdir = Path(PROJETOS_DIR) / nome
    shutil.rmtree(pdir, ignore_errors=True)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "cenas.json").write_text(json.dumps(cenas, ensure_ascii=False), encoding="utf-8")
    (pdir / "meta.json").write_text(json.dumps({
        "name": nome, "display_name": nome, "modo_producao": "imagem_video",
        "modo_execucao": "manual", "estilo_visual": "photorealistic_cinematic",
    }, ensure_ascii=False), encoding="utf-8")
    if srt_texto is not None:
        (pdir / "srt").mkdir(parents=True, exist_ok=True)
        (pdir / "srt" / "roteiro_transcricao.srt").write_text(srt_texto, encoding="utf-8")
    return pdir


class TestCalculateRetencaoGancho(unittest.TestCase):
    """Divisão em blocos: retenção (abertura muda) | corpo | gancho (últimos 10s)."""

    def _cenas(self, duracoes):
        cenas, t = [], 0.0
        for d in duracoes:
            cenas.append({"start": round(t, 3), "end": round(t + d, 3), "duracao": d})
            t += d
        return cenas

    def test_blocos_retencao_corpo_gancho(self):
        # 1ª fala em 10s -> retenção = cenas que terminam até 7s; total 18s -> gancho a partir de 8s.
        srt = [{"start": 10.0, "end": 12.0, "text": "fala", "srt_tag": None}]
        cenas = self._cenas([3, 3, 3, 3, 3, 3])
        r = calculate_retencao_gancho(srt, cenas, 18.0)
        self.assertEqual(r["retencao_indices"], [0, 1])
        self.assertEqual(r["gancho_indices"], [3, 4, 5])
        self.assertEqual(r["corpo_indices"], [2])

    def test_sem_fala_retorna_corpo_completo(self):
        srt = [{"start": 0.0, "end": 3.0, "text": "   ", "srt_tag": None}]
        cenas = self._cenas([3, 3, 3])
        r = calculate_retencao_gancho(srt, cenas, 9.0)
        self.assertEqual(r["retencao_indices"], [])
        self.assertEqual(r["gancho_indices"], [])
        # nunca devolve dict sem corpo_indices (evita KeyError em quem chama)
        self.assertEqual(r["corpo_indices"], [0, 1, 2])

    def test_srt_vazio(self):
        r = calculate_retencao_gancho([], self._cenas([3, 3]), 6.0)
        self.assertEqual(r["corpo_indices"], [0, 1])

    def test_aceita_formato_cenas_json(self):
        """Cenas no formato cenas.json (start_time/end_time) também funcionam."""
        srt = [{"start": 10.0, "end": 12.0, "text": "fala", "srt_tag": None}]
        cenas = [{"start_time": 0.0, "end_time": 3.0},
                 {"start_time": 3.0, "end_time": 6.0},
                 {"start_time": 6.0, "end_time": 9.0}]
        r = calculate_retencao_gancho(srt, cenas, 9.0)
        self.assertEqual(r["retencao_indices"], [0, 1])


class TestDetectFalaEmIntervalo(unittest.TestCase):
    """Fala vs silêncio dentro do intervalo de uma cena."""

    SRT = [
        {"start": 0.0, "end": 2.0, "text": "primeira fala", "srt_tag": None},
        {"start": 5.0, "end": 6.0, "text": "   ", "srt_tag": None},
        {"start": 10.0, "end": 12.0, "text": "", "srt_tag": None},
    ]

    def test_detecta_sobreposicao(self):
        self.assertTrue(detect_fala_em_intervalo(self.SRT, 1.0, 4.0))
        self.assertTrue(detect_fala_em_intervalo(self.SRT, -1.0, 0.5))

    def test_silencio_retorna_false(self):
        self.assertFalse(detect_fala_em_intervalo(self.SRT, 3.0, 4.5))
        self.assertFalse(detect_fala_em_intervalo(self.SRT, 6.5, 9.5))

    def test_ignora_blocos_sem_texto(self):
        # 5-6s e 10-12s existem mas sem texto: não contam como fala.
        self.assertFalse(detect_fala_em_intervalo(self.SRT, 5.0, 6.0))
        self.assertFalse(detect_fala_em_intervalo(self.SRT, 10.0, 12.0))


class TestDistribuicaoNoPlan(unittest.TestCase):
    """Integração real: gerar_scene_plan() aplica a distribuição por SRT."""

    def tearDown(self):
        for nome in (PROJ, PROJ_SEM_SRT):
            shutil.rmtree(Path(PROJETOS_DIR) / nome, ignore_errors=True)

    def _gerar(self, nome):
        # force=False: sem chamada de API (fallback determinístico no passo 4.5).
        with contextlib.redirect_stdout(io.StringIO()):
            return gerar_scene_plan(nome, force=False)

    def test_abertura_fecho_e_alternancia_do_corpo(self):
        # 7 cenas de 3s (total 21s). Fala só em 10-12s e 20-21s:
        #   retenção (1ª fala em 10s -> cenas que terminam até 7s): cenas 1-2
        #   corpo: cena 3 (silêncio, 6-9s) e cena 4 (com fala, 9-12s)
        #   gancho (últimos 10s -> start >= 11s): cenas 5-7
        srt = _srt_texto([(10.0, 12.0, "Speech in the middle"),
                          (20.0, 21.0, "Speech at the very end")])
        _criar_projeto(PROJ, _cenas_json(7), srt)

        res = self._gerar(PROJ)
        self.assertTrue(res["success"], res)

        plano = Path(PROJETOS_DIR) / PROJ / "lira_scene_plan.json"
        cenas = json.loads(plano.read_text(encoding="utf-8"))["cenas"]
        por_id = {int(c["id"]): c for c in cenas}

        # Cena 1 (índice 0) = retenção -> vídeo + HOOK
        self.assertEqual(cenas[0]["tipo"], "video")
        self.assertEqual(cenas[0]["narrative_role"], "HOOK")
        self.assertTrue(cenas[0]["animar"])
        self.assertTrue(cenas[0]["animate_later"])

        # Cena 3 = corpo em SILÊNCIO -> imagem estática + BROLL
        self.assertEqual(por_id[3]["tipo"], "image")
        self.assertEqual(por_id[3]["media_intent"], "image")
        self.assertFalse(por_id[3]["animar"])
        self.assertFalse(por_id[3]["animate_later"])
        self.assertEqual(por_id[3]["narrative_role"], "BROLL")
        self.assertFalse(por_id[3]["avatar_required"])
        self.assertEqual(por_id[3]["prompt_animacao"], "")

        # Cena 4 = corpo COM fala (10-12s) -> vídeo + AVATAR
        self.assertEqual(por_id[4]["tipo"], "video")
        self.assertEqual(por_id[4]["media_intent"], "video")
        self.assertTrue(por_id[4]["animar"])
        self.assertEqual(por_id[4]["narrative_role"], "AVATAR")

        # Últimas cenas = gancho -> vídeo + CTA
        for cid in (5, 6, 7):
            self.assertEqual(por_id[cid]["tipo"], "video", cid)
            self.assertEqual(por_id[cid]["narrative_role"], "CTA", cid)
        self.assertEqual(cenas[-1]["tipo"], "video")

    def test_projeto_sem_srt_nao_quebra(self):
        _criar_projeto(PROJ_SEM_SRT, _cenas_json(4), srt_texto=None)
        res = self._gerar(PROJ_SEM_SRT)
        self.assertTrue(res["success"], res)
        self.assertEqual(res["total"], 4)


if __name__ == "__main__":
    unittest.main()
