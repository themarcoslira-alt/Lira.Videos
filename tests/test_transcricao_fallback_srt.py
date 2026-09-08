# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

from services.pipeline_service import PipelineService

class TestTranscricaoFallbackSRT(unittest.TestCase):
    def test_tarefa1_language_none(self):
        src = Path('_transcrever_subprocesso.py').read_text(encoding='utf-8')
        self.assertIn('language=None', src)
        self.assertNotIn('language="en"', src)

    def test_tarefa2_extrair_segmentos_srt(self):
        srt_content = '1\n00:00:01,000 --> 00:00:05,000\nPrimeira fala de teste\n\n2\n00:00:06,500 --> 00:00:10,000\nSegunda fala de teste\n'
        segs = PipelineService._extrair_segmentos_srt(srt_content)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]['text'], 'Primeira fala de teste')
        self.assertEqual(segs[0]['start'], 1.0)
        self.assertEqual(segs[0]['end'], 5.0)

    @patch('services.transcriber.transcrever')
    def test_tarefa2_fallback_srt_aplicado(self, mock_tc):
        mock_tc.return_value = {'success': True, 'segments': 0, 'segmentos': []}
        p = PipelineService()
        p.project_name = 'test_fallback_proj'
        with tempfile.TemporaryDirectory() as tmpdir:
            downloads_dir = Path(tmpdir) / 'Downloads'
            downloads_dir.mkdir(parents=True, exist_ok=True)
            srt_fake = downloads_dir / 'test_fallback_proj.srt'
            srt_fake.write_text('1\n00:00:00,000 --> 00:00:03,000\nFala de teste fallback\n', encoding='utf-8')
            with patch('pathlib.Path.home', return_value=Path(tmpdir)), \
                 patch('services.pipeline_service.PROJETOS_DIR', Path(tmpdir)), \
                 patch.object(p, '_salvar_meta'), \
                 patch.object(p, '_carregar_meta', return_value={}), \
                 patch.object(p, '_salvar_log_projeto'), \
                 patch.object(p, '_atualizar_step'), \
                 patch.object(p, '_notify'):
                audio_fake = Path(tmpdir) / 'test.mp3'
                audio_fake.write_bytes(b'fake audio')
                res = p._transcrever_interno(str(audio_fake))
                self.assertIn('sugestao_srt', res)
                self.assertIn('aviso', res)
                self.assertEqual(res['segments'], 1)
                self.assertEqual(res['segmentos'][0]['text'], 'Fala de teste fallback')

if __name__ == '__main__':
    unittest.main()
