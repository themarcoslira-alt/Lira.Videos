# -*- coding: utf-8 -*-
"""Diagnóstico de travamento entre IMAGE_DOWNLOADED_OK e FILE_SAVED_OK.

Cobre a instrumentação adicionada em:
  - services/playwright_flow.py    -> `log_trace` (timestamp no CMD + console web)
  - services/scene_plan_service.py -> `trace_plan`, `dump_threads_stacks` e
    `_adquirir_lock_escrita` (contenção do lock de escrita agora é ANUNCIADA em
    vez de esperar para sempre em silêncio).

Nenhuma regra de negócio muda: o lock continua sendo adquirido antes de toda
escrita do lira_scene_plan.json (removê-lo concatenaria conteúdo no JSON).
"""
import contextlib
import io
import json
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJETOS_DIR
import services.playwright_flow as pwf
import services.scene_plan_service as sps

PROJ = "_t_trace_diag"
PROJ_MIDIA = "_t_trace_midia"


def _png_valido_minimo(lado: int = 96) -> bytes:
    """PNG RGB real (>= 1KB, decodificável pelo Pillow) sem arquivos externos."""
    import random
    from PIL import Image
    rnd = random.Random(7)
    img = Image.new("RGB", (lado, lado))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                 for _ in range(lado * lado)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    dados = buf.getvalue()
    assert len(dados) >= 1024, "PNG de teste precisa passar do mínimo de 1KB"
    return dados


def _criar_projeto(nome: str) -> Path:
    pdir = Path(PROJETOS_DIR) / nome
    shutil.rmtree(pdir, ignore_errors=True)
    (pdir / "cenas").mkdir(parents=True, exist_ok=True)
    plano = {
        "projeto": nome,
        "cenas": [{
            "id": 1,
            "scene_index": 1,
            "tempo_inicio": 0.0,
            "tempo_fim": 3.0,
            "start": 0.0,
            "end": 3.0,
            "duracao": 3.0,
            "narration": "Balcony plant close up",
            "texto": "Balcony plant close up",
            "prompt_imagem": "balcony plant close up, natural light, 16:9 framing",
            "tipo": "image",
            "status": "PENDENTE",
        }],
    }
    (pdir / "lira_scene_plan.json").write_text(
        json.dumps(plano, ensure_ascii=False, indent=2), encoding="utf-8")
    return pdir


class TestLogTrace(unittest.TestCase):
    """`playwright_flow.log_trace` — timestamp em CMD + console web."""

    def test_imprime_timestamp_com_cena_e_envia_para_console_web(self):
        enviados = []
        original = pwf.pw_log
        pwf.pw_log = lambda msg, level="info": enviados.append((msg, level))
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pwf.log_trace("IMAGE_DOWNLOADED_OK: iniciando pós-processamento", cid=98)
        finally:
            pwf.pw_log = original

        saida = buf.getvalue()
        self.assertIn("[TRACE]", saida)
        self.assertIn("[CENA 098] IMAGE_DOWNLOADED_OK: iniciando pós-processamento", saida)
        # Carimbo HH:MM:SS.mmm
        self.assertRegex(saida, r"\[\d{2}:\d{2}:\d{2}\.\d{3}\]")
        # Segunda saída: fila de eventos do console web (polling /api/eventos)
        self.assertEqual(len(enviados), 1)
        self.assertIn("[TRACE]", enviados[0][0])

    def test_falha_no_pw_log_nao_derruba_o_fluxo(self):
        original = pwf.pw_log

        def _explode(msg, level="info"):
            raise RuntimeError("fila de eventos indisponível")

        pwf.pw_log = _explode
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pwf.log_trace("trace resiliente", cid=1)  # não pode levantar
        finally:
            pwf.pw_log = original
        self.assertIn("trace resiliente", buf.getvalue())

    def test_unicode_nao_derruba_o_fluxo_com_codepage_legado(self):
        """`print("→")` levanta UnicodeEncodeError quando o stdout é redirecionado
        (pipe/arquivo com cp1252). O trace precisa SOBREVIVER a isso: se ele caísse,
        a cena morreria por erro de encoding em vez de apenas travar.
        """
        legado = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
        original_stdout, original_pw = sys.stdout, pwf.pw_log
        pwf.pw_log = lambda *a, **k: None
        try:
            # sanity: sem o fallback, escrever a seta quebraria
            with self.assertRaises(UnicodeEncodeError):
                legado.write("→")
                legado.flush()

            sys.stdout = legado
            pwf.log_trace("→ AGUARDANDO LOCK DE ESCRITA ❌ — teste", cid=98)
            sys.stdout.flush()
            escrito = legado.buffer.getvalue().decode("cp1252")
        finally:
            sys.stdout, pwf.pw_log = original_stdout, original_pw
            legado.detach()

        # Fallback 'replace': o texto legível do diagnóstico é preservado
        self.assertIn("[TRACE]", escrito)
        self.assertIn("[CENA 098]", escrito)
        self.assertIn("AGUARDANDO LOCK DE ESCRITA", escrito)


class TestTracePlan(unittest.TestCase):
    """`scene_plan_service.trace_plan` — timestamp no CMD + evento TRACE_SCENE_PLAN."""

    def test_emite_evento_com_categoria_trace_scene_plan(self):
        eventos = []
        original = sps.log_event
        sps.log_event = lambda cat, msg, level="info", details=None: eventos.append(
            (cat, msg, level)) or {"success": True}
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sps.trace_plan("CENA 007: gravando bytes em disco.", PROJ, level="warn")
        finally:
            sps.log_event = original

        self.assertIn("[TRACE] [_t_trace_diag] CENA 007: gravando bytes em disco.", buf.getvalue())
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0][0], "TRACE_SCENE_PLAN")
        self.assertEqual(eventos[0][2], "warn")

    def test_unicode_nao_derruba_o_fluxo_com_codepage_legado(self):
        """`trace_plan` com `❌` sobrevive a stdout cp1252 (pipe/arquivo)."""
        legado = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
        eventos = []
        original_stdout, original_log = sys.stdout, sps.log_event
        sps.log_event = lambda cat, msg, level="info", details=None: eventos.append(msg) or {"success": True}
        try:
            with self.assertRaises(UnicodeEncodeError):
                legado.write("❌")
                legado.flush()

            sys.stdout = legado
            sps.trace_plan("CENA 012: ❌ VISUAL JUDGMENT interno FALHOU", PROJ, level="error")
            sys.stdout.flush()
            escrito = legado.buffer.getvalue().decode("cp1252")
        finally:
            sys.stdout, sps.log_event = original_stdout, original_log
            legado.detach()

        self.assertIn("[TRACE] [_t_trace_diag] CENA 012:", escrito)
        self.assertIn("VISUAL JUDGMENT interno FALHOU", escrito)
        self.assertEqual(len(eventos), 1)  # o evento da web continua íntegro (UTF-8)


class TestDumpThreadsStacks(unittest.TestCase):

    def test_snapshot_inclui_as_threads_ativas(self):
        resultado = []

        def _worker():
            time.sleep(0.15)
            resultado.append(sps.dump_threads_stacks())

        t = threading.Thread(target=_worker, name="thread-de-teste")
        t.start()
        t.join()

        self.assertEqual(len(resultado), 1)
        dump = resultado[0]
        self.assertIn("--- thread", dump)
        self.assertIn("_worker", dump)  # o stack da thread viva aparece no dump
        self.assertLessEqual(len(dump), 6000)


class TestLockDeEscrita(unittest.TestCase):
    """Contenção do lock agora é anunciada (antes: espera infinita e silenciosa)."""

    def test_lock_ocupado_avisa_e_conclui_apos_liberacao(self):
        lock = sps._obter_lock_escrita(PROJ)
        liberado = {"ok": False}

        def _solta():
            time.sleep(0.25)
            lock.release()
            liberado["ok"] = True

        lock.acquire()  # simula outra thread escrevendo o scene_plan
        t = threading.Thread(target=_solta)
        t.start()

        avisos = []
        orig_trace, orig_dump = sps.trace_plan, sps.dump_threads_stacks
        sps.trace_plan = lambda msg, projeto="", level="info": avisos.append((msg, level))
        sps.dump_threads_stacks = lambda *a, **k: "--- thread 'stub' ---"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                obtido = sps._adquirir_lock_escrita(PROJ, timeout_s=0.05)
            obtido.release()
        finally:
            sps.trace_plan, sps.dump_threads_stacks = orig_trace, orig_dump
            t.join()

        self.assertTrue(liberado["ok"], "o lock deve ter sido liberado e adquirido")
        self.assertTrue(any("LOCK DE ESCRITA OCUPADO" in m for m, _ in avisos))
        self.assertTrue(any("liberado após" in m for m, _ in avisos))
        self.assertIn("stub", buf.getvalue())  # dump das threads foi impresso no CMD

    def test_salvar_scene_plan_libera_lock_mesmo_com_excecao(self):
        original = sps._salvar_scene_plan_lockado

        def _explode(projeto, plan):
            raise RuntimeError("falha simulada na escrita")

        sps._salvar_scene_plan_lockado = _explode
        try:
            with self.assertRaises(RuntimeError):
                sps.salvar_scene_plan(PROJ, {"cenas": []})
        finally:
            sps._salvar_scene_plan_lockado = original

        lock = sps._obter_lock_escrita(PROJ)
        self.assertTrue(lock.acquire(timeout=0.5), "lock deve estar livre após a exceção")
        lock.release()


class TestTracesNoCaminhoReal(unittest.TestCase):
    """salvar_midia_cena_estruturada (janela que congelava) emite os traces."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(Path(PROJETOS_DIR) / PROJ_MIDIA, ignore_errors=True)

    def test_pontos_criticos_aparecem_no_stdout(self):
        _criar_projeto(PROJ_MIDIA)
        png = _png_valido_minimo()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = sps.salvar_midia_cena_estruturada(
                projeto_id=PROJ_MIDIA,
                cid=1,
                ts_ini=0.0,
                ts_fim=3.0,
                prompt_texto="balcony plant close up, natural light, 16:9 framing",
                midia_bytes=png,
                is_video=False,
                modelo_usado="photorealistic_cinematic",
                personagem_ref="",
            )
        saida = buf.getvalue()

        self.assertTrue(res.get("success"), f"esperado success=True, veio {res}")
        # Cada etapa da janela IMAGE_DOWNLOADED_OK -> FILE_SAVED_OK tem seu trace
        for esperado in (
            "salvar_midia_cena_estruturada iniciou",
            "gravando bytes em",
            "bytes gravados em disco.",
            "atualizando storyboard.json...",
            "storyboard.json atualizado.",
            "atualizando galeria.json...",
            "galeria.json atualizada.",
            "VISUAL JUDGMENT interno",
            "padrão de mídia garantido",
            "salvar_midia_cena_estruturada COMPLETOU.",
        ):
            self.assertIn(esperado, saida, f"trace ausente: {esperado}")
        # O Visual Judgment interno nunca falha em silêncio: concluiu OU falhou
        self.assertTrue(
            ("VISUAL JUDGMENT interno concluiu." in saida)
            or ("VISUAL JUDGMENT interno FALHOU" in saida)
        )


# Rótulos esperados na janela IMAGE_DOWNLOADED_OK -> FILE_SAVED_OK, na ORDEM em
# que o código executa (é essa ordem que revela o culpado quando congela).
JANELA_ESPERADA = (
    "IMAGE_DOWNLOADED_OK: iniciando pós-processamento",
    "→ AGUARDANDO LOCK DE ESCRITA",
    "← LOCK LIBERADO",
    "→ INICIANDO salvar_midia_cena_estruturada",
    "← salvar_midia_cena_estruturada COMPLETOU",
    "→ INICIANDO Visual Judgment (fidelidade facial do avatar).",
    "→ Verificando arquivo no disco",
    "← Verificação de disco concluída",
    "→ atualizar_cena(status=BAIXADA/READY)",
    "← Status atualizado para BAIXADA/READY",
    "→ sincronizar_midias_encontradas",
    "← midias_encontradas sincronizado",
    "← PRONTO, emitindo FILE_SAVED_OK",
)

# Operações que podem pendurar; TODAS precisam de trace ANTES e DEPOIS.
ANCORAS_CRITICAS = (
    'scene_plan_svc.atualizar_cena(',
    'scene_plan_svc.salvar_midia_cena_estruturada(',
    'Path(_arq_gerado).stat().st_size > 500',
    'scene_plan_svc.sincronizar_midias_encontradas(',
)

# O print final FECHA a janela: ele só precisa de trace ANTES (não existe código
# instrumentado depois dele dentro da própria janela).
ANCORA_FINAL = 'print("[LOG] FILE_SAVED_OK"'


class TestCoberturaDaJanelaCritica(unittest.TestCase):
    """A instrumentação cobre CADA operação crítica da janela que congelava.

    Lê os próprios bytes do módulo (não executa Playwright): garante que a
    próxima execução real consiga apontar o culpado sem ambiguidade.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = Path(pwf.__file__).read_text(encoding="utf-8")
        cls.ini = cls.src.index('print("[LOG] IMAGE_DOWNLOADED_OK"')
        cls.fim = cls.src.index('print("[LOG] FILE_SAVED_OK"')
        cls.janela = cls.src[cls.ini:cls.fim]

    def test_janela_instrumentada_tem_conteudo(self):
        self.assertGreater(len(self.janela), 2000)
        # sanity: FILE_SAVED_OK vem DEPOIS de IMAGE_DOWNLOADED_OK no arquivo
        self.assertLess(self.ini, self.fim)

    def test_traces_na_ordem_de_execucao(self):
        pos = -1
        for rotulo in JANELA_ESPERADA:
            i = self.src.find(rotulo, self.ini, self.fim)
            self.assertGreater(i, -1, f"trace ausente na janela: {rotulo}")
            self.assertGreater(i, pos, f"trace fora de ordem: {rotulo}")
            pos = i

    def test_toda_operacao_critica_tem_trace_antes_e_depois(self):
        for ancora in ANCORAS_CRITICAS:
            coberta = False
            busca = self.ini
            while True:
                i = self.src.find(ancora, busca, self.fim)
                if i < 0:
                    break
                antes = self.src.rfind("log_trace(", self.ini, i)
                depois = self.src.find("log_trace(", i, self.fim)
                if antes > -1 and depois > -1:
                    coberta = True
                    break
                busca = i + 1
            self.assertTrue(
                coberta, f"operação crítica sem trace antes/depois: {ancora}")

    def test_emissao_final_tem_trace_imediatamente_antes(self):
        # `fim` aponta para o INÍCIO do print final; o range de busca precisa
        # incluir a própria âncora (por isso + len(ANCORA_FINAL)).
        i = self.src.find(ANCORA_FINAL, self.ini, self.fim + len(ANCORA_FINAL))
        self.assertGreater(i, -1, "print final não encontrado na janela")
        self.assertEqual(i, self.fim)
        antes = self.src.rfind("log_trace(", self.ini, i)
        self.assertGreater(antes, -1, "FILE_SAVED_OK emitido sem trace antes")
        self.assertIn("← PRONTO, emitindo FILE_SAVED_OK",
                      self.src[antes:antes + 200])

    def test_dois_locks_da_etapa_final_sao_distinguiveis(self):
        # atualizar_cena(status=BAIXADA) e sincronizar_midias_encontradas ficam
        # em traces SEPARADOS, senão não dá para saber qual dos dois pendurou.
        i_atualiza = self.src.find("→ atualizar_cena(status=BAIXADA/READY)",
                                   self.ini, self.fim)
        i_sincroniza = self.src.find("→ sincronizar_midias_encontradas",
                                     self.ini, self.fim)
        self.assertTrue(-1 < i_atualiza < i_sincroniza)


if __name__ == "__main__":
    unittest.main()
