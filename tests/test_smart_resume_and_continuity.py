import json
import shutil
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Piso de importação — causa raiz da dessincronização do mock de erro
# ---------------------------------------------------------------------------
# O pytest importa TODOS os módulos de teste no MESMO processo. Vários testes
# legados desta pasta (test_animation_director.py, test_character_identity_lock.py,
# test_visual_director.py, ...) ainda inserem no sys.path o caminho ABSOLUTO da
# árvore ANTIGA do repositório (C:\ultracut3) e só então importam `services.*`.
# Como o `services` do primeiro módulo coletado ficava cacheado em sys.modules,
# o restante da suíte passava a usar a implementação ANTIGA — inclusive o
# `C:\ultracut3\projetos\...\lira_scene_plan.json`. Era exatamente isso que
# dessincronizava a contagem/IDs de cenas deste teste em relação à cena do mock
# de erro (erro na cena 3).
#
# Este bloco garante que ESTE teste sempre importe `app_web`/`services` da raiz
# ATUAL do repositório. A guarda é LOCAL (não altera o sys.path dos demais
# testes), mas descarta do sys.modules os módulos do projeto já carregados da
# árvore antiga; com isso, testes legados que ainda apontam para C:\ultracut3
# passam a enxergar a config atual (a correção definitiva é atualizar esses
# caminhos legados — fora do escopo deste teste).
ROOT = Path(__file__).resolve().parent.parent


def _importar_projeto_da_raiz_atual() -> None:
    """Prende a raiz ATUAL no topo do sys.path e descarta módulos do projeto que
    já tenham sido carregados de outra árvore (ex.: C:\\ultracut3)."""
    raiz = str(ROOT)
    while raiz in sys.path:
        sys.path.remove(raiz)
    sys.path.insert(0, raiz)

    def _de_outra_arvore(modulo) -> bool:
        origem = getattr(modulo, "__file__", None)
        if not origem:
            return False
        try:
            return not Path(origem).resolve().is_relative_to(ROOT)
        except Exception:
            return False

    descartar = []
    for nome, modulo in list(sys.modules.items()):
        if not (nome in ("app_web", "config", "config_local")
                or nome == "services"
                or nome.startswith("services.")):
            continue
        if _de_outra_arvore(modulo):
            descartar.append(nome)
    for nome in descartar:
        sys.modules.pop(nome, None)


_importar_projeto_da_raiz_atual()

from app_web import app, PROJETOS_DIR  # noqa: E402
import services.scene_plan_service as scene_plan_svc  # noqa: E402


# ---------------------------------------------------------------------------
# Cenário simulado — fonte única de verdade deste teste
# ---------------------------------------------------------------------------
TOTAL_CENAS = 5                              # cenas 1..5 no lira_scene_plan.json
CENAS_PRONTAS = (1, 2)                       # arquivos simulados no disco
CENA_ERRO_ESPERADA = max(CENAS_PRONTAS) + 1  # cena 3 = 1ª pendente do cenário


class TestSmartResumeAndContinuity(unittest.TestCase):
    def setUp(self):
        self.proj_id = "_test_smart_resume_proj"
        self.pdir = PROJETOS_DIR / self.proj_id
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        (self.pdir / "imagens").mkdir(parents=True, exist_ok=True)
        (self.pdir / "cenas").mkdir(parents=True, exist_ok=True)

        meta = {
            "id": self.proj_id,
            "nome": "Smart Resume Test",
            "modo_execucao": "automatico"
        }
        (self.pdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        # Cria as cenas do cenário simulado (IDs 1..TOTAL_CENAS)
        cenas = [
            {"id": i, "texto": f"Scene {i}", "tempo_inicio": (i - 1) * 4,
             "tempo_fim": i * 4, "status": "PENDENTE"}
            for i in range(1, TOTAL_CENAS + 1)
        ]
        scene_plan_svc.salvar_scene_plan(self.proj_id, {"cenas": cenas})
        self.client = app.test_client()

        # Guarda-corpo do cenário: o lira_scene_plan.json REALMENTE gravado é a
        # fonte de verdade do teste. Se ele divergir do cenário simulado (IDs,
        # contagem ou árvore/projeto errados), falhamos AQUI — de forma explícita —
        # em vez de deixar o mock de erro dessincronizado lá na frente.
        self.plan_path = self.pdir / "lira_scene_plan.json"
        self.assertTrue(
            self.plan_path.exists(),
            f"lira_scene_plan.json não foi gravado em {self.plan_path} (árvore/projeto errados?)",
        )
        self.cenas_plan = json.loads(self.plan_path.read_text(encoding="utf-8"))["cenas"]
        self.assertEqual(
            [int(c["id"]) for c in self.cenas_plan],
            list(range(1, TOTAL_CENAS + 1)),
            "lira_scene_plan.json dessincronizado do cenário simulado (IDs/contagem de cenas)",
        )

    def tearDown(self):
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def _simular_cenas_prontas(self):
        """Simula no disco os arquivos das cenas já baixadas do cenário."""
        (self.pdir / "imagens" / f"{CENAS_PRONTAS[0]:03d}.png").write_bytes(b"X" * 1024)
        (self.pdir / "cenas" / f"{CENAS_PRONTAS[1]:03d}.png").write_bytes(b"Y" * 1024)

    def _ids_cenas_no_disco(self):
        """IDs das cenas do plano com arquivo real (>500 bytes) no disco.

        Leitura DIRETA do lira_scene_plan.json + pasta do projeto — não depende
        do serviço, para que qualquer dessincronização apareça como falha clara.
        """
        ids = set()
        for cena in self.cenas_plan:
            cid = int(cena["id"])
            for pasta in ("imagens", "cenas"):
                arquivo = self.pdir / pasta / f"{cid:03d}.png"
                if arquivo.exists() and arquivo.stat().st_size > 500:
                    ids.add(cid)
        return ids

    def _primeira_cena_pendente(self):
        """Primeira cena do cenário sem arquivo no disco (mesma regra do endpoint
        de status: cena pronta = arquivo real presente)."""
        prontas = self._ids_cenas_no_disco()
        pendentes = [int(c["id"]) for c in self.cenas_plan if int(c["id"]) not in prontas]
        return pendentes[0] if pendentes else None

    def test_smart_resume_detecta_arquivos_disco(self):
        # Simula que as cenas 1 e 2 foram baixadas no disco com sucesso
        self._simular_cenas_prontas()

        # Chama status da produção
        res = self.client.get(f"/api/v2/producao/{self.proj_id}/status").get_json()
        self.assertTrue(res["success"])
        
        rinfo = res["resume_info"]
        self.assertEqual(rinfo["total"], TOTAL_CENAS)
        self.assertEqual(rinfo["prontas_count"], len(CENAS_PRONTAS))
        self.assertEqual(rinfo["pendentes_count"], TOTAL_CENAS - len(CENAS_PRONTAS))
        self.assertEqual(rinfo["proxima_cena_id"], CENA_ERRO_ESPERADA)
        self.assertTrue(rinfo["pode_retomar"])
        self.assertFalse(rinfo["concluido"])

    def test_retomar_fila_ignora_prontas(self):
        # Simula as cenas prontas do cenário (1 e 2)
        self._simular_cenas_prontas()

        # Dispara retomada
        res = self.client.post(f"/api/v2/producao/{self.proj_id}/retomar", json={}).get_json()
        self.assertTrue(res["success"])
        # Só as cenas não prontas do cenário entram na fila (cenas 3, 4, 5)
        self.assertEqual(res["enfileiradas"], TOTAL_CENAS - len(CENAS_PRONTAS))
        self.assertEqual(res["proxima_cena_id"], CENA_ERRO_ESPERADA)

    def test_registro_de_erro_e_retentar_erros(self):
        # Cenário simulado: cenas 1 e 2 prontas no disco → a primeira pendente é a
        # CENA 3, que é exatamente a cena que o mock de falha marca com ERRO.
        self._simular_cenas_prontas()
        cena_erro_id = self._primeira_cena_pendente()
        self.assertEqual(
            cena_erro_id, CENA_ERRO_ESPERADA,
            f"mock de erro dessincronizado do cenário: esperava a cena "
            f"{CENA_ERRO_ESPERADA} como 1ª pendente, mas o lira_scene_plan.json/arquivos "
            f"de disco apontam a cena {cena_erro_id}",
        )

        # Marca a cena do cenário com erro e salva motivo e timestamp
        atualizacao = scene_plan_svc.atualizar_cena(self.proj_id, cena_erro_id, {
            "status": "ERRO",
            "erro_msg": "Timeout no Google Flow ao gerar imagem",
            "erro_ts": "2026-08-24 01:50:00"
        })
        self.assertTrue(atualizacao["success"])

        res_status = self.client.get(f"/api/v2/producao/{self.proj_id}/status").get_json()
        self.assertTrue(res_status["success"])
        rinfo = res_status["resume_info"]
        self.assertEqual(rinfo["erros_count"], 1)
        # EXATAMENTE a cena do cenário (sem assertIn "frouxo")
        self.assertEqual(rinfo["cenas_erro_ids"], [cena_erro_id])

        # Persistência real no lira_scene_plan.json (releitura do disco)
        cena_json = next(
            c for c in json.loads(self.plan_path.read_text(encoding="utf-8"))["cenas"]
            if int(c["id"]) == cena_erro_id
        )
        self.assertEqual(cena_json["status"], "ERRO")
        self.assertEqual(cena_json["erro_msg"], "Timeout no Google Flow ao gerar imagem")
        # Obs.: `erro_ts` faz parte do mock de falha, mas NÃO está em
        # scene_plan_svc.CAMPOS_EDITAVEIS — o serviço persiste só status/erro_msg.

        # Dispara re-tentar apenas os erros (a própria cena do cenário)
        res_retry = self.client.post(f"/api/v2/producao/{self.proj_id}/retentar_erros").get_json()
        self.assertTrue(res_retry["success"])
        self.assertEqual(res_retry["enfileiradas"], 1)
        self.assertEqual(res_retry["scene_ids"], [cena_erro_id])


if __name__ == "__main__":
    unittest.main()
