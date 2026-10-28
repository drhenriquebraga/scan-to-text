import io
import zipfile

import pytest
from PIL import Image

from scan_to_text.ler import recipientes
from scan_to_text.ler.pdf import Ilegivel


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _zip(conteudo: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, dados in conteudo.items():
            z.writestr(nome, dados)
    return buffer.getvalue()


def test_arquivo_simples_passa_direto(tmp_path):
    arquivo = tmp_path / "scan001.PDF"
    arquivo.write_bytes(b"%PDF-1.7")
    itens, ignorados = recipientes.expandir(arquivo, tmp_path / "trab")
    assert [(i.caminho, i.tipo, i.dentro) for i in itens] == [(arquivo, "pdf", "")]
    assert ignorados == []


def test_zip_aninhado_ate_dois_niveis(tmp_path):
    nivel3 = _zip({"fundo.png": _png()})
    nivel2 = _zip({"meio.pdf": b"%PDF-1.7", "fundo.zip": nivel3})
    arquivo = tmp_path / "exames.zip"
    arquivo.write_bytes(_zip({"laudo.png": _png(), "sub/interno.zip": nivel2, "notas.eml": b"x"}))
    itens, ignorados = recipientes.expandir(arquivo, tmp_path / "trab")
    assert sorted(i.dentro for i in itens) == ["laudo.png", "sub/interno.zip/meio.pdf"]
    assert all(i.caminho.is_file() and (tmp_path / "trab") in i.caminho.parents for i in itens)
    assert sorted(ignorados) == ["notas.eml", "sub/interno.zip/fundo.zip"]


def test_caminho_malicioso_no_zip_nao_escapa(tmp_path):
    arquivo = tmp_path / "ataque.zip"
    arquivo.write_bytes(_zip({"../../fora.png": _png()}))
    itens, _ = recipientes.expandir(arquivo, tmp_path / "trab")
    assert len(itens) == 1 and (tmp_path / "trab") in itens[0].caminho.parents


def test_zip_com_senha_e_ilegivel(tmp_path):
    dados = bytearray(_zip({"a.pdf": b"%PDF-1.7"}))
    # Liga o bit de criptografia no cabeçalho local e no diretório central.
    dados[6] |= 0x01
    central = dados.find(b"PK\x01\x02")
    dados[central + 8] |= 0x01
    arquivo = tmp_path / "senha.zip"
    arquivo.write_bytes(bytes(dados))
    with pytest.raises(Ilegivel) as erro:
        recipientes.expandir(arquivo, tmp_path / "trab")
    assert erro.value.motivo == "senha"


def test_bomba_de_zip_e_recusada(tmp_path, monkeypatch):
    monkeypatch.setattr(recipientes, "LIMITE_BYTES", 1_000_000)
    arquivo = tmp_path / "bomba.zip"
    arquivo.write_bytes(_zip({"grande.pdf": b"0" * 2_000_000}))
    with pytest.raises(Ilegivel) as erro:
        recipientes.expandir(arquivo, tmp_path / "trab")
    assert erro.value.motivo == "zip_grande"


def test_heic_vira_png(tmp_path):
    pi_heif = pytest.importorskip("pi_heif")
    arquivo = tmp_path / "foto.heic"
    try:
        pi_heif.register_heif_opener()
        Image.new("RGB", (64, 64), "white").save(arquivo, format="HEIF")
    except (KeyError, OSError, ValueError):
        pytest.skip("este pi-heif nao grava HEIC")
    itens, _ = recipientes.expandir(arquivo, tmp_path / "trab")
    assert itens[0].tipo == "imagem" and itens[0].caminho.suffix == ".png"
    assert Image.open(itens[0].caminho).size == (64, 64)
