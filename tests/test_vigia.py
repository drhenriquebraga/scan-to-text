"""Ping: estabilidade, DAYS_BACK, temporários, ocultos, ordem e origem inacessível."""
import _winapi
import ctypes
import hashlib
import os
import stat
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from scan_to_text.vigia import IGNORAR, Vigia


class Relogio:
    def __init__(self, inicio: float = 500.0):
        self.t = inicio

    def __call__(self) -> float:
        return self.t

    def andar(self, s: float) -> None:
        self.t += s


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.SetFileAttributesW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
_k32.SetFileAttributesW.restype = wintypes.BOOL
_k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _arquivo(pasta: Path, nome: str, conteudo: bytes = b"%PDF-1.4 x", idade_s: float | None = None
             ) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    p = pasta / nome
    p.write_bytes(conteudo)
    if idade_s is not None:
        t = time.time() - idade_s
        os.utime(p, (t, t))
    return p


def _marcar(caminho: Path, atributo: int) -> None:
    assert _k32.SetFileAttributesW(str(caminho), atributo)


def test_ignorar_e_o_esperado():
    assert IGNORAR == ("~$*", "*.tmp", "*.part", "*.crdownload", "Thumbs.db", "desktop.ini")


def test_arquivo_so_entra_depois_da_estabilidade(tmp_path):
    r = Relogio()
    v = Vigia([tmp_path], estabilidade_s=60, days_back=0, relogio=r)
    p = _arquivo(tmp_path, "scan001.pdf", b"pagina 1")
    assert v.varrer() == []
    r.andar(59)
    assert v.varrer() == []
    with open(p, "ab") as f:  # o scanner ainda grava a página seguinte
        f.write(b"pagina 2")
    r.andar(30)
    assert v.varrer() == []
    r.andar(59)
    assert v.varrer() == []
    r.andar(1)
    assert v.varrer() == [p]


def test_data_de_modificacao_nova_reinicia_a_estabilidade(tmp_path):
    r = Relogio()
    v = Vigia([tmp_path], estabilidade_s=60, days_back=0, relogio=r)
    p = _arquivo(tmp_path, "scan001.pdf", idade_s=3600)
    v.varrer()
    r.andar(30)
    os.utime(p, None)  # mesmo tamanho, data nova
    v.varrer()
    r.andar(59)
    assert v.varrer() == []
    r.andar(1)
    assert v.varrer() == [p]


def test_days_back_pela_data_de_modificacao(tmp_path):
    novo = _arquivo(tmp_path, "novo.pdf", idade_s=1 * 86400)
    velho = _arquivo(tmp_path, "velho.pdf", idade_s=10 * 86400)
    assert Vigia([tmp_path], estabilidade_s=0, days_back=7).varrer() == [novo]
    assert Vigia([tmp_path], estabilidade_s=0, days_back=0).varrer() == [velho, novo]
    depois = Vigia([tmp_path], estabilidade_s=0, days_back=7,
                   agora=lambda: time.time() + 20 * 86400)
    assert depois.varrer() == []


def test_temporarios_ocultos_e_de_sistema_sao_ignorados(tmp_path):
    bom = _arquivo(tmp_path, "scan002.pdf")
    fundo = _arquivo(tmp_path / "sub" / "fundo", "scan003.jpg")
    for nome in ("~$laudo.docx", "a.tmp", "B.TMP", "c.part", "d.crdownload", "Thumbs.db",
                 "desktop.ini"):
        _arquivo(tmp_path, nome)
    _arquivo(tmp_path / "sub", "THUMBS.DB")
    _arquivo(tmp_path / "sub", "Desktop.INI")
    _marcar(_arquivo(tmp_path, "oculto.pdf"), stat.FILE_ATTRIBUTE_HIDDEN)
    _marcar(_arquivo(tmp_path, "sistema.pdf"), stat.FILE_ATTRIBUTE_SYSTEM)
    oculta = tmp_path / "oculta"
    _arquivo(oculta, "dentro.pdf")
    _marcar(oculta, stat.FILE_ATTRIBUTE_HIDDEN)
    de_sistema = tmp_path / "de_sistema"
    _arquivo(de_sistema, "dentro.pdf")
    _marcar(de_sistema, stat.FILE_ATTRIBUTE_SYSTEM)
    assert sorted(Vigia([tmp_path], 0, 0).varrer()) == sorted([bom, fundo])


def test_mais_antigo_primeiro_em_todas_as_origens(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    meio = _arquivo(a, "meio.pdf", idade_s=7200)
    novo = _arquivo(a, "novo.pdf", idade_s=3600)
    velho = _arquivo(b, "velho.pdf", idade_s=10800)
    assert Vigia([a, b], 0, 0).varrer() == [velho, meio, novo]


def test_origem_inacessivel_nao_derruba_e_fica_registrada(tmp_path):
    boa = tmp_path / "boa"
    p = _arquivo(boa, "a.pdf")
    ruim = tmp_path / "nao_existe"
    v = Vigia([ruim, boa], 0, 0)
    assert v.varrer() == [p]
    assert v.inacessiveis == [ruim]
    ruim.mkdir()
    v.varrer()
    assert v.inacessiveis == []


def test_arquivo_que_nao_abre_para_leitura_espera(tmp_path):
    p = _arquivo(tmp_path, "scan.pdf")
    v = Vigia([tmp_path], 0, 0)
    # aberto sem compartilhamento, como o driver do scanner enquanto grava
    h = _k32.CreateFileW(str(p), 0x40000000, 0, None, 3, 0x80, None)
    assert h and h != wintypes.HANDLE(-1).value
    try:
        assert v.varrer() == []
    finally:
        _k32.CloseHandle(h)
    assert v.varrer() == [p]


def test_juncao_dentro_da_origem_nao_vira_laco(tmp_path):
    p = _arquivo(tmp_path, "a.pdf")
    laco = tmp_path / "laco"
    _winapi.CreateJunction(str(tmp_path), str(laco))
    try:
        assert Vigia([tmp_path], 0, 0).varrer() == [p]
    finally:
        os.rmdir(laco)


def test_sha256_lembrado_enquanto_o_arquivo_nao_muda(tmp_path):
    p = _arquivo(tmp_path, "a.pdf")
    v = Vigia([tmp_path], 0, 0)
    assert v.varrer() == [p]
    assert v.sha256_conhecido(p) is None
    v.lembrar(p, "cd" * 32)
    assert v.sha256_conhecido(p) == "cd" * 32
    with open(p, "ab") as f:
        f.write(b"mais")
    assert v.sha256_conhecido(p) is None
    assert v.sha256_conhecido(tmp_path / "outro.pdf") is None


def test_sha256_de_arquivo_que_sumiu_e_esquecido(tmp_path):
    p = _arquivo(tmp_path, "a.pdf", b"conteudo A")
    mtime = p.stat().st_mtime_ns
    v = Vigia([tmp_path], 0, 0)
    v.varrer()
    v.lembrar(p, "cd" * 32)
    p.unlink()
    v.varrer()
    # outro arquivo com o mesmo nome, tamanho e data: a memória já foi embora
    _arquivo(tmp_path, "a.pdf", b"conteudo B")
    os.utime(p, ns=(mtime, mtime))
    assert v.sha256_conhecido(p) is None


def test_varrer_nao_altera_nada_na_origem(tmp_path):
    for i in range(3):
        _arquivo(tmp_path / f"s{i}", f"scan{i}.pdf", bytes([i]) * 100, idade_s=100)

    def retrato():
        return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns,
                       hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in tmp_path.rglob("*") if p.is_file())

    antes = retrato()
    r = Relogio()
    v = Vigia([tmp_path], 60, 0, relogio=r)
    v.varrer()
    r.andar(61)
    assert len(v.varrer()) == 3
    assert retrato() == antes


@pytest.mark.parametrize("nome", ["scan.PDF", "foto.heic", "arquivo.zip", "documento.docx"])
def test_formatos_comuns_nao_sao_confundidos_com_temporarios(tmp_path, nome):
    p = _arquivo(tmp_path, nome)
    assert Vigia([tmp_path], 0, 0).varrer() == [p]
