"""
tests/test_parallel_orchestrator.py — Testes do Orquestrador Paralelo Multi-Conta
=================================================================================
Valida a divisão matemática e o respeito aos ciclos narrativos pelo DivisorBlocos,
a consolidação de telemetria, integridade dos blocos e os endpoints Flask da API v2.
"""

import sys
import json
import unittest
from pathlib import Path
from flask import Flask

# Adiciona raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.parallel_flow_orchestrator import (
    BlocoCena,
    DivisorBlocos,
    WorkerInstancia,
    OrchestradorParalelo,
    obter_orquestrador,
    registrar_orquestrador,
    inicializar_worker_seguro,
)
from services.api_v2 import api_v2_bp


class TestDivisorBlocos(unittest.TestCase):

    def setUp(self):
        # Cria listas sintéticas de cenas com id e scene_index
        self.cenas_223 = [{"id": i, "scene_index": i, "tipo_cena": ["avatar_intro", "broll_imagem", "broll_video"][(i - 1) % 3]} for i in range(1, 224)]
        self.cenas_9 = [{"id": i, "scene_index": i} for i in range(1, 10)]

    def test_divisao_223_cenas_3_contas(self):
        """223 cenas em 3 contas deve gerar [75, 75, 73] preservando múltiplos de 3 nos blocos iniciais."""
        blocos = DivisorBlocos.dividir_plano(self.cenas_223, 3)
        self.assertEqual(len(blocos), 3)

        tamanhos = [b.total_cenas for b in blocos]
        self.assertEqual(tamanhos, [75, 75, 73])
        self.assertEqual(sum(tamanhos), 223)

        # Verifica múltiplos de 3 nos blocos não-finais
        self.assertEqual(blocos[0].total_cenas % 3, 0)
        self.assertEqual(blocos[1].total_cenas % 3, 0)

        # Continuidade de índices
        self.assertEqual(blocos[0].inicio_idx, 0)
        self.assertEqual(blocos[0].fim_idx, 74)
        self.assertEqual(blocos[1].inicio_idx, 75)
        self.assertEqual(blocos[1].fim_idx, 149)
        self.assertEqual(blocos[2].inicio_idx, 150)
        self.assertEqual(blocos[2].fim_idx, 222)

    def test_divisao_223_cenas_5_contas(self):
        """223 cenas em 5 contas deve gerar [45, 45, 45, 45, 43] com 4 blocos múltiplos de 3."""
        blocos = DivisorBlocos.dividir_plano(self.cenas_223, 5)
        self.assertEqual(len(blocos), 5)

        tamanhos = [b.total_cenas for b in blocos]
        self.assertEqual(tamanhos, [45, 45, 45, 45, 43])
        self.assertEqual(sum(tamanhos), 223)

        for i in range(4):
            self.assertEqual(blocos[i].total_cenas % 3, 0, f"Bloco {i} não é múltiplo de 3")

    def test_divisao_cenas_exatas(self):
        """9 cenas em 3 contas deve gerar exatamente [3, 3, 3]."""
        blocos = DivisorBlocos.dividir_plano(self.cenas_9, 3)
        self.assertEqual(len(blocos), 3)
        tamanhos = [b.total_cenas for b in blocos]
        self.assertEqual(tamanhos, [3, 3, 3])

    def test_divisao_uma_conta(self):
        """1 conta deve conter 100% das cenas."""
        blocos = DivisorBlocos.dividir_plano(self.cenas_223, 1)
        self.assertEqual(len(blocos), 1)
        self.assertEqual(blocos[0].total_cenas, 223)
        self.assertEqual(blocos[0].inicio_idx, 0)
        self.assertEqual(blocos[0].fim_idx, 222)

    def test_divisao_cenas_vazias_ou_zero_contas(self):
        """Lista vazia ou contas <= 0 deve retornar lista vazia."""
        self.assertEqual(DivisorBlocos.dividir_plano([], 3), [])
        self.assertEqual(DivisorBlocos.dividir_plano(self.cenas_9, 0), [])
        self.assertEqual(DivisorBlocos.dividir_plano(self.cenas_9, -1), [])

    def test_sem_duplicacao_ou_omissao_de_cenas(self):
        """Garante que todas as cenas originais estejam presentes sem sobreposição nem omissão."""
        blocos = DivisorBlocos.dividir_plano(self.cenas_223, 4)
        todos_ids = []
        for b in blocos:
            todos_ids.extend(b.scene_ids)

        self.assertEqual(len(todos_ids), 223)
        self.assertEqual(len(set(todos_ids)), 223, "Há IDs duplicados entre blocos")
        esperados = [c["id"] for c in self.cenas_223]
        self.assertEqual(todos_ids, esperados)


class TestTelemetriaConsolidada(unittest.TestCase):

    def test_consolidacao_metricas(self):
        orch = OrchestradorParalelo(projeto_id="teste_proj", num_contas=2)

        w1 = WorkerInstancia(
            worker_id=0,
            conta_info={"id": 1, "email": "w1@test.com"},
            cdp_port=9222,
            bloco_cenas=[{"id": 1}, {"id": 2}],
            projeto_id="teste_proj"
        )
        w2 = WorkerInstancia(
            worker_id=1,
            conta_info={"id": 2, "email": "w2@test.com"},
            cdp_port=9223,
            bloco_cenas=[{"id": 3}, {"id": 4}],
            projeto_id="teste_proj"
        )

        w1.telemetria["cenas_concluidas"] = 1
        w1.telemetria["status"] = "GERANDO"
        w2.telemetria["cenas_concluidas"] = 2
        w2.telemetria["status"] = "CONCLUIDO"

        orch.workers = [w1, w2]
        orch.is_running = True

        telemetria = orch.obter_telemetria_consolidada()

        self.assertEqual(telemetria["projeto_id"], "teste_proj")
        self.assertEqual(telemetria["num_workers"], 2)
        self.assertEqual(telemetria["total_cenas"], 4)
        self.assertEqual(telemetria["cenas_concluidas"], 3)
        self.assertEqual(telemetria["progresso_pct"], 75)
        self.assertEqual(len(telemetria["workers"]), 2)
        self.assertTrue(telemetria["is_running"])

    def test_registro_global(self):
        orch = OrchestradorParalelo(projeto_id="proj_global_test", num_contas=3)
        registrar_orquestrador("proj_global_test", orch)

        res = obter_orquestrador("proj_global_test")
        self.assertIsNotNone(res)
        self.assertEqual(res.projeto_id, "proj_global_test")


class TestApiV2Endpoints(unittest.TestCase):

    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(api_v2_bp, url_prefix="/api/v2")
        self.client = self.app.test_client()

    def test_telemetria_sequencial_padrao(self):
        """Sem orquestrador paralelo rodando, telemetria retorna modo sequencial."""
        resp = self.client.get("/api/v2/producao/projeto_inexistente_123/telemetria")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("modo"), "sequencial")

    def test_telemetria_paralela_com_orquestrador(self):
        """Com orquestrador registrado, telemetria retorna modo paralelo e dados consolidados."""
        proj_id = "proj_telemetria_paralelo_test"
        orch = OrchestradorParalelo(projeto_id=proj_id, num_contas=1)
        w = WorkerInstancia(
            worker_id=0,
            conta_info={"id": 1, "email": "test@flow.com"},
            cdp_port=9222,
            bloco_cenas=[{"id": 10}, {"id": 11}],
            projeto_id=proj_id
        )
        orch.workers = [w]
        orch.is_running = True
        registrar_orquestrador(proj_id, orch)

        resp = self.client.get(f"/api/v2/producao/{proj_id}/telemetria")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("modo"), "paralelo")
        self.assertEqual(data.get("total_cenas"), 2)
        self.assertEqual(len(data.get("workers", [])), 1)

    def test_pausar_e_cancelar_endpoints(self):
        """Endpoints de pausar e cancelar respondem com sucesso."""
        proj_id = "proj_pause_test"
        orch = OrchestradorParalelo(projeto_id=proj_id, num_contas=1)
        registrar_orquestrador(proj_id, orch)

        resp_pause = self.client.post(f"/api/v2/producao/{proj_id}/pausar")
        self.assertEqual(resp_pause.status_code, 200)
        self.assertTrue(resp_pause.get_json().get("success"))

        resp_cancel = self.client.post(f"/api/v2/producao/{proj_id}/cancelar")
        self.assertEqual(resp_cancel.status_code, 200)
        self.assertTrue(resp_cancel.get_json().get("success"))

    def test_iniciar_paralelo_bloqueia_conflito(self):
        """Se o orquestrador já estiver em execução, deve retornar status 409 (Conflict)."""
        proj_id = "proj_conflito_test"
        orch = OrchestradorParalelo(projeto_id=proj_id, num_contas=2)
        orch.is_running = True
        registrar_orquestrador(proj_id, orch)

        resp = self.client.post(
            f"/api/v2/producao/{proj_id}/iniciar_paralelo",
            json={"num_contas": 2}
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("já está em execução", data.get("error", ""))

    def test_calcular_cenas_restantes_223_com_130_feitas(self):
        """Validação: 223 cenas com 130 BAIXADA devem resultar em exatamente 93 pendentes."""
        cenas = []
        for i in range(1, 224):
            st = "BAIXADA" if i <= 130 else "PENDENTE"
            cenas.append({"id": i, "scene_index": i, "status": st})
        plan = {"projeto": "proj_test_93", "total": 223, "cenas": cenas}

        restantes = DivisorBlocos.calcular_cenas_restantes("proj_test_93", plan)
        self.assertEqual(len(restantes), 93)
        self.assertEqual(restantes[0], 131)
        self.assertEqual(restantes[-1], 223)

    def test_redivisao_93_cenas_8_workers(self):
        """Validação: 93 cenas divididas entre 8 workers geram blocos com múltiplos de 3."""
        cenas_93 = [{"id": i, "scene_index": i} for i in range(131, 224)]
        self.assertEqual(len(cenas_93), 93)

        blocos = DivisorBlocos.dividir_plano(cenas_93, 8)
        self.assertEqual(len(blocos), 8)

        tamanhos = [b.total_cenas for b in blocos]
        self.assertEqual(sum(tamanhos), 93)
        # 93 / 8 = 11.625 -> ceil = 12. 7 blocos de 12 e 1 bloco de 9 (ou 3 se sobra)
        self.assertEqual(tamanhos[:7], [12, 12, 12, 12, 12, 12, 12])
        self.assertEqual(tamanhos[7], 9)
        for i in range(7):
            self.assertEqual(blocos[i].total_cenas % 3, 0)

    def test_endpoint_relancar_residual_sucesso(self):
        """Endpoint /relancar_residual redivide e responde com blocos e cenas restantes."""
        from unittest.mock import patch
        proj_id = "proj_residual_test"
        cenas = []
        for i in range(1, 224):
            st = "BAIXADA" if i <= 130 else "PENDENTE"
            cenas.append({"id": i, "scene_index": i, "status": st})
        plan = {"projeto": proj_id, "total": 223, "cenas": cenas}

        with patch("services.scene_plan_service.carregar_scene_plan", return_value=plan), \
             patch("threading.Thread.start"):
            resp = self.client.post(
                f"/api/v2/producao/{proj_id}/relancar_residual",
                json={"num_workers_novo": 8, "modo": "imagem"}
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("cenas_restantes"), 93)
        self.assertEqual(data.get("workers_novos"), 8)
        self.assertEqual(len(data.get("blocos_atualizados", [])), 8)

    def test_endpoint_relancar_residual_projeto_completo(self):
        """Endpoint /relancar_residual com todas cenas completas retorna informativo."""
        from unittest.mock import patch
        proj_id = "proj_completo_test"
        cenas = [{"id": i, "scene_index": i, "status": "BAIXADA"} for i in range(1, 51)]
        plan = {"projeto": proj_id, "total": 50, "cenas": cenas}

        with patch("services.scene_plan_service.carregar_scene_plan", return_value=plan):
            resp = self.client.post(
                f"/api/v2/producao/{proj_id}/relancar_residual",
                json={"num_workers_novo": 4}
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("error"), "Projeto já completo")
        self.assertEqual(data.get("cenas_restantes"), 0)


class TestInicializarWorkerSeguro(unittest.TestCase):

    def setUp(self):
        from unittest.mock import MagicMock
        self.mock_driver = MagicMock()
        self.mock_driver.port = 9222
        self.mock_driver.profile_dir = "/tmp/mock_profile"
        self.mock_driver._verificar_sessao_google.return_value = True

        # Mock page
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.url = "https://labs.google/fx/pt/tools/flow"
        self.mock_page.title.return_value = "Google Flow"
        self.mock_page.evaluate.return_value = "Gerador de vídeos AI"
        self.mock_driver.page = self.mock_page

        self.worker = WorkerInstancia(
            worker_id=0,
            conta_info={"id": 1, "email": "teste@flow.com"},
            cdp_port=9222,
            bloco_cenas=[{"id": 1}, {"id": 2}],
            projeto_id="teste_seguro"
        )
        self.worker.driver = self.mock_driver

    def test_passo1_cdp_falha_descarta_worker(self):
        """PASSO 1: Se a porta CDP não responder após tentativas, worker é descartado."""
        from unittest.mock import patch
        with patch("services.parallel_flow_orchestrator._testar_porta_cdp", return_value=False), \
             patch("services.parallel_flow_orchestrator._cdp_port_open", return_value=False), \
             patch("services.parallel_flow_orchestrator.ensure_chrome_cdp", return_value=(False, "Falha")), \
             patch("time.sleep"):
            ok = inicializar_worker_seguro(
                worker_id=0,
                conta={"email": "teste@flow.com"},
                porta_cdp=9222,
                driver=self.mock_driver,
                worker_instancia=self.worker
            )
            self.assertFalse(ok)
            self.assertEqual(self.worker.telemetria["status"], "DESCARTADO")
            self.assertIn("CDP porta 9222 não respondeu — DESCARTADO", self.worker.telemetria["mensagem"])

    def test_passo2_sem_sessao_google_descarta_worker(self):
        """PASSO 2: Se _verificar_sessao_google() retornar False, descarta sem relogin."""
        from unittest.mock import patch
        self.mock_driver._verificar_sessao_google.return_value = False
        with patch("services.parallel_flow_orchestrator._testar_porta_cdp", return_value=True):
            ok = inicializar_worker_seguro(
                worker_id=1,
                conta={"email": "semlogin@gmail.com"},
                porta_cdp=9223,
                driver=self.mock_driver,
                worker_instancia=self.worker
            )
            self.assertFalse(ok)
            self.assertEqual(self.worker.telemetria["status"], "DESCARTADO")
            self.assertIn("conta semlogin@gmail.com sem sessão — DESCARTADO", self.worker.telemetria["mensagem"])

    def test_passo3_flow_invalido_descarta_worker(self):
        """PASSO 3: Se URL não for do Flow ou retornar erro 403, recarrega e descarta."""
        from unittest.mock import patch
        self.mock_page.url = "https://accounts.google.com/signin"
        with patch("services.parallel_flow_orchestrator._testar_porta_cdp", return_value=True), \
             patch("time.sleep"):
            ok = inicializar_worker_seguro(
                worker_id=2,
                conta={"email": "teste@flow.com"},
                porta_cdp=9224,
                driver=self.mock_driver,
                worker_instancia=self.worker
            )
            self.assertFalse(ok)
            self.assertEqual(self.worker.telemetria["status"], "DESCARTADO")
            self.assertIn("Flow não carregou — DESCARTADO", self.worker.telemetria["mensagem"])

    def test_passo4_sucesso_libera_pronto(self):
        """PASSO 4: Com CDP, sessão e Flow válidos, libera como PRONTO ✅."""
        from unittest.mock import patch
        with patch("services.parallel_flow_orchestrator._testar_porta_cdp", return_value=True):
            ok = inicializar_worker_seguro(
                worker_id=0,
                conta={"email": "valido@flow.com"},
                porta_cdp=9222,
                driver=self.mock_driver,
                worker_instancia=self.worker
            )
            self.assertTrue(ok)
            self.assertEqual(self.worker.telemetria["status"], "PRONTO")
            self.assertIn("PRONTO ✅ — conta valido@flow.com", self.worker.telemetria["mensagem"])

    def test_worker_descartado_nao_interfere_em_outros(self):
        """Worker descartado não interrompe os outros workers e telemetria conclui normalmente."""
        orch = OrchestradorParalelo(projeto_id="teste_multi", num_contas=2)
        w_valido = WorkerInstancia(
            worker_id=0,
            conta_info={"id": 1, "email": "w0@test.com"},
            cdp_port=9222,
            bloco_cenas=[{"id": 1}],
            projeto_id="teste_multi"
        )
        w_descartado = WorkerInstancia(
            worker_id=1,
            conta_info={"id": 2, "email": "w1@test.com"},
            cdp_port=9223,
            bloco_cenas=[{"id": 2}],
            projeto_id="teste_multi"
        )

        w_valido.telemetria["status"] = "CONCLUIDO"
        w_valido.telemetria["cenas_concluidas"] = 1
        w_descartado.telemetria["status"] = "DESCARTADO"
        w_descartado.telemetria["mensagem"] = "Worker 1: conta w1@test.com sem sessão — DESCARTADO"

        orch.workers = [w_valido, w_descartado]
        orch.is_running = True

        telemetria = orch.obter_telemetria_consolidada()
        self.assertFalse(orch.is_running, "Orquestrador deve marcar is_running=False quando restantes terminam")
        self.assertEqual(telemetria["workers"][0]["status"], "CONCLUIDO")
        self.assertEqual(telemetria["workers"][1]["status"], "DESCARTADO")


if __name__ == "__main__":
    unittest.main()
