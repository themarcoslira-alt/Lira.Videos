"""
Teste de Validação da Fase 2D:
- Verifica que o ParallelFlowOrchestrator não confunde PNG com MP4 em modo 'animacao'.
- Testa DivisorBlocos.calcular_cenas_restantes em modo imagem vs animacao.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.parallel_flow_orchestrator import DivisorBlocos, OrchestradorParalelo


def test_phase_2d():
    print("=== Teste 2D: Validação do Parallel Flow Orchestrator (PNG vs MP4) ===")

    projeto_id = "test_proj_2d"
    mock_plan = {
        "cenas": [
            {
                "id": 1,
                "texto": "Cena com PNG existente em disco",
                "status": "BAIXADA",
                "image_status": "READY",
                "video_status": "PENDING",
                "arquivo_midia": "midias/01_[00-00-00-05].png"
            },
            {
                "id": 2,
                "texto": "Cena com MP4 existente em disco",
                "status": "BAIXADA",
                "image_status": "READY",
                "video_status": "READY",
                "arquivo_video": "midias/02_[00-05-00-10].mp4"
            },
            {
                "id": 3,
                "texto": "Cena sem nenhuma mídia",
                "status": "PENDENTE",
                "image_status": "PENDING",
                "video_status": "PENDING"
            }
        ]
    }

    # Mock do disco: cena 1 tem png de 50KB, cena 2 tem mp4 de 500KB
    def mock_resolver(p_id, cid, t_ini):
        if cid == 1:
            p = MagicMock(spec=Path)
            p.exists.return_value = True
            p.is_file.return_value = True
            p.suffix = ".png"
            p.stat.return_value.st_size = 50000
            return p
        elif cid == 2:
            p = MagicMock(spec=Path)
            p.exists.return_value = True
            p.is_file.return_value = True
            p.suffix = ".mp4"
            p.stat.return_value.st_size = 500000
            return p
        return None

    with patch("services.scene_plan_service.carregar_scene_plan", return_value=mock_plan), \
         patch("services.scene_plan_service.resolver_arquivo_cena", side_effect=mock_resolver):

        # 1. Modo Imagem:
        # Cenas 1 e 2 devem ser consideradas completas (ambas têm mídias válidas).
        # Apenas Cena 3 deve constar como pendente.
        pendentes_img = DivisorBlocos.calcular_cenas_restantes(projeto_id, mock_plan, modo="imagem")
        print(f"Modo Imagem - Pendentes: {pendentes_img}")
        assert pendentes_img == [3], f"No modo imagem, apenas cena 3 deveria estar pendente, obteve: {pendentes_img}"
        print("✅ Modo Imagem: filtra corretamente cenas restantes.")

        # 2. Modo Animação:
        # Cena 1 tem PNG (NÃO É VÍDEO!) -> DEVE constar como PENDENTE!
        # Cena 2 tem MP4 (video_status=READY) -> Completa.
        # Cena 3 não tem nada -> PENDENTE.
        # Pendentes em modo animacao devem ser: [1, 3]!
        pendentes_anim = DivisorBlocos.calcular_cenas_restantes(projeto_id, mock_plan, modo="animacao")
        print(f"Modo Animação - Pendentes: {pendentes_anim}")
        assert pendentes_anim == [1, 3], f"No modo animação, cenas 1 e 3 devem estar pendentes (Cena 1 tem apenas PNG), obteve: {pendentes_anim}"
        assert 1 in pendentes_anim, "Cena 1 (com apenas PNG) NUNCA pode ser considerada completa em modo animação!"
        print("✅ Modo Animação: não considera PNG como vídeo e inclui cena 1 nas pendentes!")

    print("\n=========================================")
    print("TODOS OS TESTES DA FASE 2D FORAM APROVADOS!")
    print("=========================================")


if __name__ == "__main__":
    test_phase_2d()
