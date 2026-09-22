"""
Teste de Validação da Fase 2C:
- Verifica seleção inteligente de conta para vídeo (ignora Conta 3 que está com vídeo esgotado).
- Testa ensure_chrome_cdp(modo="animacao") escolhendo Conta 4.
- Testa _tratar_indicador_limite em video_mode=True.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.flow_account_manager import FlowAccountManager
import services.playwright_flow as pf


def test_phase_2c():
    print("=== Teste 2C: Validação de Contas e Rotação de Créditos de Vídeo ===")
    fam = FlowAccountManager()

    # 1. Verifica estado atual de flow_accounts.json
    contas = fam._carregar().get("contas", [])
    conta_3 = next((c for c in contas if c.get("id") == 3), None)
    conta_4 = next((c for c in contas if c.get("id") == 4), None)

    assert conta_3 is not None, "Conta 3 deve existir"
    assert conta_4 is not None, "Conta 4 deve existir"
    assert conta_3.get("video_creditos_esgotados") is True, "Conta 3 deve estar com vídeo esgotado"
    assert conta_4.get("video_creditos_esgotados") is False, "Conta 4 deve ter créditos de vídeo"

    # 2. proxima_conta_para_video()
    prox_video = fam.proxima_conta_para_video()
    assert prox_video is not None, "Deve encontrar próxima conta para vídeo"
    assert prox_video.get("id") == 4, f"Deveria retornar Conta 4, mas retornou {prox_video.get('id')}"
    print(f"✅ proxima_conta_para_video() retornou corretamente: Conta {prox_video.get('id')} ({prox_video.get('nome')})")

    # 3. ensure_chrome_cdp escolhendo conta_04 quando modo='animacao'
    with patch("subprocess.Popen") as mock_popen, \
         patch("services.playwright_flow._cdp_port_open", side_effect=[False, True]), \
         patch("services.playwright_flow._find_chrome_exe", return_value="C:\\dummy\\chrome.exe"):

        ok, msg = pf.ensure_chrome_cdp(port=9299, force_restart=False, modo="animacao")
        assert ok is True, f"ensure_chrome_cdp deveria ter sucesso: {msg}"
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        cmd_str = " ".join(cmd)
        print(f"Comando disparado no modo 'animacao': {cmd_str}")
        assert "conta_04" in cmd_str, f"Deveria ter usado conta_04, mas comando foi: {cmd_str}"
        assert "conta_03" not in cmd_str, "NÃO deveria ter usado conta_03 em modo animação!"
        print("✅ ensure_chrome_cdp(modo='animacao') selecionou conta_04 com sucesso!")

    # 4. ensure_chrome_cdp escolhendo conta_03 quando modo='image'
    with patch("subprocess.Popen") as mock_popen, \
         patch("services.playwright_flow._cdp_port_open", side_effect=[False, True]), \
         patch("services.playwright_flow._find_chrome_exe", return_value="C:\\dummy\\chrome.exe"):

        ok, msg = pf.ensure_chrome_cdp(port=9299, force_restart=False, modo="image", worker_id=0)
        assert ok is True
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        cmd_str = " ".join(cmd)
        print(f"Comando disparado no modo 'image': {cmd_str}")
        assert "conta_03" in cmd_str, f"Deveria ter usado conta_03 em modo imagem, mas foi: {cmd_str}"
        print("✅ ensure_chrome_cdp(modo='image') selecionou conta_03 com sucesso!")

    # 5. _tratar_indicador_limite com video_mode=True
    worker = pf.FlowQueueWorker.get_worker()
    with patch.object(worker, "_rotacionar_conta") as mock_rot:
        ret = worker._tratar_indicador_limite("daily quota exceeded", video_mode=True)
        print(f"Retorno _tratar_indicador_limite(video_mode=True): {ret}")
        assert ret == "rotacionando_video", f"Esperado 'rotacionando_video', obteve '{ret}'"
        assert mock_rot.called, "Deveria ter chamado _rotacionar_conta"
        print("✅ _tratar_indicador_limite acionou rotação para vídeo com sucesso!")

    print("\n=========================================")
    print("TODOS OS TESTES DA FASE 2C FORAM APROVADOS!")
    print("=========================================")


if __name__ == "__main__":
    test_phase_2c()
