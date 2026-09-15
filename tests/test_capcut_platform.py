"""
Testes do 'platform' do draft CapCut — correção do hard_disk_id divergente.

Regra corrigida: device_id/hard_disk_id/mac_address vêm SEMPRE de um draft que o
PRÓPRIO CapCut gravou nesta máquina (ou das constantes nativas do módulo). Nada é
recalculado por hash local (serial do volume C:, MachineGuid, uuid.getnode()) — esses
cálculos foram comprovadamente diferentes do que o CapCut grava e contaminavam o
draft exportado, fazendo o CapCut abrir e fechar.

Cobre também o marcador `_ultracut3_gerado.json` (todo draft gerado pelo ULTRACUT3),
que impede o nosso próprio export de voltar como "fonte de verdade".
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capcut_draft as cc

# Valores NATIVOS desta máquina (drafts que o CapCut gravou).
NATIVO = {
    "app_id": 359289,
    "app_source": "cc",
    "app_version": "9.4.0",
    "os": "windows",
    "os_version": "10.0.22631",
    "device_id": "418ebf6b1973fc7f8c18647b000f0e76",
    "hard_disk_id": "56441e0e433110865693c794cdfc4696",
    "mac_address": "5d51a55eb8359f2f31e449fcee05481c",
}

# IDs que só o cálculo ANTIGO (errado) do ULTRACUT3 produzia.
ID_ERRADO_HARD = "f11aa710551d314b280549e6f8d4eae1"   # md5(serial do volume C:)
ID_ERRADO_DEVICE = "69672681711bc1f4081074332412d9ed"  # md5(MachineGuid)


def _png_teste(pasta: Path, nome="img.png", w=32, h=32) -> Path:
    """PNG RGB preto válido, sem dependências externas."""
    def chunk(tipo: bytes, dados: bytes) -> bytes:
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    destino = pasta / nome
    destino.write_bytes(png)
    return destino


class _BaseDrafts(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="capcut_platform_"))
        self.drafts = self._tmp / "drafts"
        self.drafts.mkdir(parents=True, exist_ok=True)
        self._orig_pasta = cc._pasta_drafts_capcut
        cc._pasta_drafts_capcut = lambda: str(self.drafts)

    def tearDown(self):
        cc._pasta_drafts_capcut = self._orig_pasta
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _draft(self, nome: str, plataforma: dict, mtime: float = None) -> Path:
        """Cria uma pasta de draft falsa com draft_content.json."""
        pasta = self.drafts / nome
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "draft_content.json").write_text(
            json.dumps({"platform": plataforma, "tracks": [], "materials": {}}),
            encoding="utf-8")
        if mtime is not None:
            os.utime(pasta / "draft_content.json", (mtime, mtime))
        return pasta


class TestFonteDeVerdade(_BaseDrafts):
    def test_le_platform_de_draft_nativo(self):
        self._draft("nativo_qualquer", dict(NATIVO), mtime=1000)
        pl = cc._plataforma_de_draft_nativo()
        self.assertIsNotNone(pl)
        self.assertEqual(pl["hard_disk_id"], NATIVO["hard_disk_id"])

    def test_ignora_draft_gerado_pelo_marcador(self):
        self._draft("nativo_antigo", dict(NATIVO), mtime=1000)
        # draft NOSSO, mais recente, com o hard_disk_id errado (md5 do volume C:)
        nosso = self._draft("nosso_mais_novo",
                            dict(NATIVO, hard_disk_id=ID_ERRADO_HARD), mtime=9999)
        cc.marcar_draft_gerado(nosso, "projeto_teste")
        pl = cc._plataforma_de_draft_nativo()
        self.assertEqual(pl["hard_disk_id"], NATIVO["hard_disk_id"])

    def test_ignora_draft_legado_sem_marcador_mas_com_id_autogerado(self):
        self._draft("nativo_antigo", dict(NATIVO), mtime=1000)
        # legado: sem marcador, mas com IDs que só o cálculo do ULTRACUT3 produzia
        self._draft("teste02_legado",
                    dict(NATIVO, device_id=ID_ERRADO_DEVICE), mtime=9999)
        self._draft("joaquim_legado",
                    dict(NATIVO, hard_disk_id=ID_ERRADO_HARD), mtime=9998)
        pl = cc._plataforma_de_draft_nativo()
        self.assertEqual(pl["hard_disk_id"], NATIVO["hard_disk_id"])
        self.assertEqual(pl["device_id"], NATIVO["device_id"])

    def test_sem_draft_nativo_retorna_none(self):
        self._draft("so_nosso", dict(NATIVO, hard_disk_id=ID_ERRADO_HARD), mtime=1000)
        self.assertIsNone(cc._plataforma_de_draft_nativo())

    def test_ids_autogerados_nao_incluem_valores_nativos(self):
        autos = cc.ids_autogerados()
        for valor in NATIVO.values():
            self.assertNotIn(str(valor), autos)


class TestGettersNuncaRecalculam(_BaseDrafts):
    def test_usa_valor_do_draft_nativo(self):
        self._draft("nativo", dict(NATIVO), mtime=1000)
        self.assertEqual(cc.get_hard_disk_id(), NATIVO["hard_disk_id"])
        self.assertEqual(cc.get_device_id(), NATIVO["device_id"])
        self.assertEqual(cc.get_mac_address(), NATIVO["mac_address"])

    def test_sem_nativo_usa_constantes_nativas(self):
        self.assertEqual(cc.get_hard_disk_id(), cc._FALLBACK_HARD_DISK_ID)
        self.assertEqual(cc.get_device_id(), cc._FALLBACK_DEVICE_ID)
        self.assertEqual(cc.get_mac_address(), cc._FALLBACK_MAC_ADDRESS)

    def test_hard_disk_nunca_e_o_md5_do_volume(self):
        md5_volume = cc._md5_serial_volume_c()          # bug antigo
        self._draft("nativo", dict(NATIVO), mtime=1000)
        self._draft("nosso", dict(NATIVO, hard_disk_id=ID_ERRADO_HARD), mtime=9999)
        self.assertNotEqual(cc.get_hard_disk_id(), ID_ERRADO_HARD)
        if md5_volume:
            self.assertNotEqual(cc.get_hard_disk_id(), md5_volume)


class TestMarcadorEExportacao(_BaseDrafts):
    def test_marcar_draft_gerado_cria_marcador(self):
        pasta = self.drafts / "gerado"
        cc.marcar_draft_gerado(pasta, "meu_projeto")
        self.assertTrue((pasta / cc.MARCADOR_DRAFT_GERADO).exists())
        self.assertTrue(cc.draft_foi_gerado_por_nos(pasta))
        dados = json.loads((pasta / cc.MARCADOR_DRAFT_GERADO).read_text(encoding="utf-8"))
        self.assertEqual(dados["gerado_por"], "ULTRACUT3")
        self.assertEqual(dados["projeto"], "meu_projeto")

    def test_exportacao_grava_marcador_e_platform_nativo(self):
        import capcut_draft_imagens as cdi

        img = _png_teste(self._tmp, "cena.png")
        cenas = [{"start": 0.0, "duracao": 3.0, "arquivo": str(img),
                  "media_type": "photo"}]
        res = cdi.criar_draft_imagens("plat_test", cenas, "", str(self.drafts),
                                      nome_projeto="plat_test")
        self.assertTrue(res["success"], res.get("error"))
        draft_dir = Path(res["draft_dir"])
        self.assertTrue((draft_dir / cc.MARCADOR_DRAFT_GERADO).exists(),
                        "export precisa marcar a pasta como gerada pelo ULTRACUT3")
        dc = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(dc["platform"], cc._PLATFORM_INFO)
        self.assertEqual(dc["platform"]["hard_disk_id"], cc.get_hard_disk_id())
        self.assertEqual(dc["last_modified_platform"], dc["platform"])
        # o draft exportado não é fonte de verdade para o próximo export
        self.assertTrue(cc._draft_gerado_pelo_ultracut3(draft_dir, dc["platform"]))


if __name__ == "__main__":
    unittest.main()
