import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""GUARD anti-colapso de granularidade (app_web._guard_retranscricao).

Caso real (Tomato Plants): cenas.json com 247 cenas (edição fina) e
word_timestamps.json com 55 segmentos. Re-transcrever colapsaria as 247 cenas
para ~55 e sobrescreveria cenas.json / roteiro_transcricao.json /
word_timestamps.json em silêncio, derrubando a âncora textual de
_ancorar_editorial() (scene_plan_service).

Cobre:
  1. cenas.json fino pré-existente (247x55)      -> BLOQUEIA + aviso;
  2. greenfield (sem cenas.json)                 -> LIBERA;
  3. cenas.json consistente com a transcrição    -> LIBERA (sem falso positivo);
  4. cenas.json abaixo do limiar                 -> LIBERA;
  5. cenas finas sem transcrição nenhuma         -> BLOQUEIA;
  6. _thread_transcrever bloqueado NÃO sobrescreve os artefatos;
  7. rota POST /api/upload_audio devolve 409 + mensagem;
  8. override explícito forcar=true passa do guard.
"""
import io
import json
import shutil
import unittest

import app_web
from config import PROJETOS_DIR

client = app_web.app.test_client()

PREFIXO = "_t_guard_"


def _criar_projeto(nome, n_cenas, n_seg, com_cenas=True, n_plano=None,
                   plano_lista=False):
    """Projeto temporário com cenas.json, word_timestamps.json e (opcional)
    lira_scene_plan.json sintéticos."""
    proj = PROJETOS_DIR / nome
    proj.mkdir(parents=True, exist_ok=True)
    if com_cenas:
        cenas = [{"id": i + 1, "start_time": i * 4.0, "end_time": (i + 1) * 4.0,
                  "texto": f"cena {i + 1}"} for i in range(n_cenas)]
        (proj / "cenas.json").write_text(
            json.dumps(cenas, ensure_ascii=False), encoding="utf-8")
    if n_plano is not None:
        cenas_plano = [{"idx": i + 1, "texto": f"cena plano {i + 1}"}
                       for i in range(n_plano)]
        conteudo = cenas_plano if plano_lista else {
            "projeto": nome, "versao": "2.0", "narrativa_versao": "1",
            "gerado_em": "2026-09-17T00:00:00", "total": n_plano,
            "cenas": cenas_plano, "visual_context": {},
        }
        (proj / "lira_scene_plan.json").write_text(
            json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")
    segs = [{"start": i * 18.0, "end": (i + 1) * 18.0, "text": f"bloco {i + 1}",
             "timestamp": "00:00", "words": [{"w": "x", "s": i * 18.0, "e": i * 18.0 + 1}]}
            for i in range(n_seg)]
    (proj / "word_timestamps.json").write_text(
        json.dumps({"fonte": "teste", "segments": segs}, ensure_ascii=False),
        encoding="utf-8")
    return proj


class TestGuardRetranscricao(unittest.TestCase):
    def tearDown(self):
        for p in PROJETOS_DIR.glob(PREFIXO + "*"):
            shutil.rmtree(p, ignore_errors=True)

    # 1) o caso REAL: Tomato Plants (247 cenas x 55 segmentos) -> bloqueia
    def test_caso_tomato_plantas_bloqueia(self):
        _criar_projeto(PREFIXO + "tomato", 247, 55)
        aviso = app_web._guard_retranscricao(PREFIXO + "tomato")
        self.assertIsNotNone(aviso, "devia bloquear 247 cenas x 55 segmentos")
        self.assertTrue(aviso["bloqueado"])
        self.assertFalse(aviso["success"])
        self.assertEqual(aviso["n_cenas"], 247)
        self.assertEqual(aviso["n_segmentos_atuais"], 55)
        self.assertEqual(aviso["limite_colapso"], 82)   # int(1.5 * 55)
        self.assertEqual(aviso["motivo"], "granularidade_fina_preexistente")
        self.assertIn("247", aviso["mensagem"])
        self.assertIn("cenas.json", aviso["mensagem"])
        self.assertIn("247", aviso["error"])            # a UI lê .error

    # 2) greenfield: sem cenas.json -> segue normal
    def test_greenfield_libera(self):
        _criar_projeto(PREFIXO + "novo", 0, 0, com_cenas=False)
        self.assertIsNone(app_web._guard_retranscricao(PREFIXO + "novo"))

    # 3) consistente (120 cenas <= 1.5 x 100 segmentos) -> sem falso positivo
    def test_consistente_libera(self):
        _criar_projeto(PREFIXO + "ok", 120, 100)
        self.assertIsNone(app_web._guard_retranscricao(PREFIXO + "ok"))

    # 4) cenas.json abaixo do limiar -> nada fino a proteger
    def test_granularidade_baixa_libera(self):
        _criar_projeto(PREFIXO + "baixo", 60, 55)
        self.assertIsNone(app_web._guard_retranscricao(PREFIXO + "baixo"))

    # 5) cenas finas mas SEM transcrição -> gerar_cenas apagaria -> bloqueia
    def test_sem_transcricao_mas_cenas_finas_bloqueia(self):
        proj = PROJETOS_DIR / (PREFIXO + "semsrt")
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cenas.json").write_text(
            json.dumps([{"id": i + 1, "texto": "x"} for i in range(150)]),
            encoding="utf-8")
        aviso = app_web._guard_retranscricao(PREFIXO + "semsrt")
        self.assertIsNotNone(aviso)
        self.assertEqual(aviso["n_segmentos_atuais"], 0)


class TestGuardIntegracao(unittest.TestCase):
    """Garante que o guard está RELIGADO no caminho real (thread + rota)."""

    def tearDown(self):
        for p in PROJETOS_DIR.glob(PREFIXO + "*"):
            shutil.rmtree(p, ignore_errors=True)

    # 6) thread bloqueada: NENHUM artefato é sobrescrito
    def test_thread_bloqueada_nao_sobrescreve(self):
        proj = _criar_projeto(PREFIXO + "thread", 247, 55)
        wt_antes = (proj / "word_timestamps.json").read_bytes()
        cenas_antes = (proj / "cenas.json").read_bytes()
        audio = proj / "audio.mp3"
        audio.write_bytes(b"nao-e-audio-de-verdade")

        app_web._thread_transcrever(PREFIXO + "thread", str(audio))

        self.assertEqual((proj / "word_timestamps.json").read_bytes(), wt_antes,
                         "word_timestamps.json foi sobrescrito apesar do guard")
        self.assertEqual((proj / "cenas.json").read_bytes(), cenas_antes,
                         "cenas.json foi sobrescrito apesar do guard")
        self.assertFalse((proj / "roteiro_transcricao.json").exists(),
                         "roteiro_transcricao.json nao devia ter sido criado")

    # 7) a rota devolve 409 + aviso claro (nada é salvo)
    def test_rota_upload_audio_devolve_409(self):
        proj = _criar_projeto(PREFIXO + "rota", 247, 55)
        cenas_antes = (proj / "cenas.json").read_bytes()
        r = client.post("/api/upload_audio/" + PREFIXO + "rota",
                        data={"audio": (io.BytesIO(b"fake-mp3"), "a.mp3")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 409)
        d = r.get_json()
        self.assertFalse(d["success"])
        self.assertTrue(d["bloqueado"])
        self.assertIn("247", d["error"])
        self.assertIn("cenas.json", d["mensagem"])
        self.assertEqual((proj / "cenas.json").read_bytes(), cenas_antes)
        self.assertFalse((proj / "a.mp3").exists(), "audio nao devia ser salvo")

    # 8) override EXPLÍCITO do usuário (forcar=true) passa do guard
    def test_rota_forcar_passa_do_guard(self):
        _criar_projeto(PREFIXO + "forcar", 247, 55)
        chamadas = []
        original = app_web._iniciar_thread
        app_web._iniciar_thread = lambda *a, **k: chamadas.append(a) or True
        try:
            r = client.post("/api/upload_audio/" + PREFIXO + "forcar?forcar=true",
                            data={"audio": (io.BytesIO(b"fake-mp3"), "a.mp3")},
                            content_type="multipart/form-data")
        finally:
            app_web._iniciar_thread = original
        self.assertNotEqual(r.status_code, 409,
                            "forcar=true devia pular o guard")
        self.assertTrue(chamadas, "a thread de transcricao devia ter sido disparada")

    # 9) o override também é respeitado dentro da própria thread
    def test_thread_com_forcar_pula_o_guard(self):
        proj = _criar_projeto(PREFIXO + "forcar2", 247, 55)
        fake = {"success": False, "error": "pipeline stub"}
        original = app_web._pipeline
        app_web._pipeline = lambda projeto: type("P", (), {
            "transcrever": lambda self, audio: fake})()
        try:
            app_web._thread_transcrever(PREFIXO + "forcar2",
                                        str(proj / "audio.mp3"), True)
        finally:
            app_web._pipeline = original
        st = app_web._web_state(PREFIXO + "forcar2") or {}
        self.assertNotEqual(st.get("status"), "bloqueado",
                            "com forcar=True a thread nao pode bloquear")


class TestGuardPlanoGranularidade(unittest.TestCase):
    """lira_scene_plan.json entra no guard com a MESMA regra de cenas.json."""

    def tearDown(self):
        for p in PROJETOS_DIR.glob(PREFIXO + "*"):
            shutil.rmtree(p, ignore_errors=True)

    def test_contador_plano_envelopado(self):
        proj = _criar_projeto(PREFIXO + "cont1", 0, 0, com_cenas=False, n_plano=247)
        self.assertEqual(app_web._contar_cenas_plano(proj), 247)
        self.assertEqual(app_web._contar_cenas_json(proj), 0)

    def test_contador_plano_lista_pura(self):
        proj = _criar_projeto(PREFIXO + "cont2", 0, 0, com_cenas=False,
                              n_plano=100, plano_lista=True)
        self.assertEqual(app_web._contar_cenas_plano(proj), 100)

    def test_contador_plano_inexistente(self):
        proj = _criar_projeto(PREFIXO + "cont3", 10, 10)
        self.assertEqual(app_web._contar_cenas_plano(proj), 0)

    # NÚCLEO DA TAREFA 4: só o plano indica colapso -> BLOQUEIA
    def test_plano_fino_bloqueia_com_cenas_abaixo_do_limiar(self):
        _criar_projeto(PREFIXO + "so_plano", 50, 55, n_plano=247)
        aviso = app_web._guard_retranscricao(PREFIXO + "so_plano")
        self.assertIsNotNone(aviso, "o plano com 247 entradas devia bloquear")
        self.assertTrue(aviso["bloqueado"])
        self.assertEqual(aviso["n_cenas"], 50)              # abaixo do limiar
        self.assertEqual(aviso["n_cenas_plano"], 247)       # acima do limiar
        self.assertEqual(aviso["artefato_bloqueio"], "lira_scene_plan.json")
        self.assertEqual(aviso["n_artefato_bloqueio"], 247)
        self.assertEqual(aviso["limite_colapso"], 82)       # int(1.5 * 55)
        self.assertIn("lira_scene_plan.json", aviso["mensagem"])

    def test_plano_sem_transcricao_bloqueia(self):
        _criar_projeto(PREFIXO + "plano_sem_seg", 0, 0, com_cenas=False, n_plano=247)
        aviso = app_web._guard_retranscricao(PREFIXO + "plano_sem_seg")
        self.assertIsNotNone(aviso)
        self.assertEqual(aviso["artefato_bloqueio"], "lira_scene_plan.json")
        self.assertEqual(aviso["n_segmentos_atuais"], 0)

    def test_ambos_abaixo_do_limiar_libera(self):
        _criar_projeto(PREFIXO + "baixos", 50, 55, n_plano=80)
        self.assertIsNone(app_web._guard_retranscricao(PREFIXO + "baixos"))

    def test_plano_consistente_libera(self):
        _criar_projeto(PREFIXO + "plano_ok", 50, 100, n_plano=120)
        self.assertIsNone(app_web._guard_retranscricao(PREFIXO + "plano_ok"))

    # forcar=true cobre os DOIS arquivos juntos
    def test_forcar_cobre_os_dois_arquivos(self):
        proj = _criar_projeto(PREFIXO + "forcar2", 247, 55, n_plano=247)
        chamadas = []
        original = app_web._iniciar_thread
        app_web._iniciar_thread = lambda *a, **k: chamadas.append(a) or True
        try:
            r = client.post("/api/upload_audio/" + PREFIXO + "forcar2?forcar=true",
                            data={"audio": (io.BytesIO(b"fake-mp3"), "a.mp3")},
                            content_type="multipart/form-data")
        finally:
            app_web._iniciar_thread = original
        self.assertNotEqual(r.status_code, 409)
        self.assertTrue(chamadas)
        self.assertTrue((proj / "lira_scene_plan.json").is_file())

    def test_thread_forcar_cobre_o_plano(self):
        proj = _criar_projeto(PREFIXO + "forcar3", 50, 55, n_plano=247)
        fake = {"success": False, "error": "pipeline stub"}
        original = app_web._pipeline
        app_web._pipeline = lambda projeto: type("P", (), {
            "transcrever": lambda self, audio: fake})()
        try:
            app_web._thread_transcrever(PREFIXO + "forcar3",
                                        str(proj / "audio.mp3"), True)
        finally:
            app_web._pipeline = original
        st = app_web._web_state(PREFIXO + "forcar3") or {}
        self.assertNotEqual(st.get("status"), "bloqueado")

    def test_projeto_real_tomato_bloqueia_pelos_dois_artefatos(self):
        """Caso real: cenas.json=247 E lira_scene_plan.json=247 (55 segmentos)."""
        nome = ("WHY YOUR TOMATO PLANTS WONT PRODUCE — AND ITS NOT "
                "WHAT YOU THINK")
        if not (PROJETOS_DIR / nome).is_dir():
            self.skipTest("projeto real Tomato Plants ausente")
        aviso = app_web._guard_retranscricao(nome)
        self.assertIsNotNone(aviso)
        self.assertEqual(aviso["n_cenas"], 247)
        self.assertEqual(aviso["n_cenas_plano"], 247)
        self.assertEqual(aviso["n_segmentos_atuais"], 55)
        self.assertEqual(aviso["limite_colapso"], 82)


if __name__ == "__main__":
    unittest.main(verbosity=2)
