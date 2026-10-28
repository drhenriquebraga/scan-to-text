import os
import sqlite3
from pathlib import Path

import pytest

from scan_to_text import nuvem


@pytest.fixture
def limpo(tmp_path, monkeypatch):
    """Nada da máquina real entra: sem Drive, sem OneDrive, sem variável."""
    local = tmp_path / "localappdata"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for var in ("RADAI_RAIZES_NUVEM", "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(nuvem, "_onedrive_registro", lambda: [])
    monkeypatch.setattr(nuvem, "_ler_sync_targets", lambda: None)
    nuvem._CACHE.update(db=None, carimbo=None, raizes=())
    return local


def _ld(campo: int, dados: bytes) -> bytes:
    """Campo de tamanho variável de protobuf (tipo 2), tamanho em varint."""
    n, tamanho = len(dados), b""
    while True:
        byte, n = n & 0x7F, n >> 7
        tamanho += bytes([byte | (0x80 if n else 0)])
        if not n:
            break
    return bytes([campo << 3 | 2]) + tamanho + dados


def _sync_targets(unidade: str, *espelhadas: str) -> bytes:
    conta = _ld(1, _ld(1, b"000000000000000000001"))
    corpo = conta + _ld(2, unidade.encode()) + b"".join(_ld(3, e.encode()) for e in espelhadas)
    return _ld(1, corpo)


def _drive(local: Path, *raizes: str) -> Path:
    pasta = local / "Google" / "DriveFS"
    pasta.mkdir(parents=True, exist_ok=True)
    db = pasta / "root_preference_sqlite.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("create table roots (root_id integer primary key, last_seen_absolute_path text)")
    con.executemany("insert into roots (last_seen_absolute_path) values (?)",
                    [(r,) for r in raizes])
    con.commit()
    con.close()
    return db


def test_sem_nada_configurado_nao_e_nuvem(limpo, tmp_path):
    assert nuvem.em_pasta_sincronizada(tmp_path / "pacientes") is False


def test_raiz_espelhada_pelo_google_drive(limpo, tmp_path):
    espelhada = tmp_path / "AI_IMPORT"
    _drive(limpo, str(espelhada))
    assert nuvem.em_pasta_sincronizada(espelhada) is True
    assert nuvem.em_pasta_sincronizada(espelhada / "pacientes" / "hoje") is True


def test_prefixo_de_nome_nao_casa(limpo, tmp_path):
    _drive(limpo, str(tmp_path / "AI_IMPORT"))
    assert nuvem.em_pasta_sincronizada(tmp_path / "AI_IMPORTANTE") is False


def test_maiusculas_e_barras_nao_importam(limpo, tmp_path):
    _drive(limpo, str(tmp_path / "Espelho") + "\\")
    alvo = str(tmp_path / "espelho" / "sub").upper().replace("\\", "/")
    assert nuvem.em_pasta_sincronizada(Path(alvo)) is True


def test_nome_de_pasta_sozinho_nao_decide(limpo, tmp_path):
    for nome in ("Google Drive", "OneDrive", "Dropbox", "Meu Drive", "sync_noturno"):
        assert nuvem.em_pasta_sincronizada(tmp_path / nome / "pacientes") is False, nome


def test_raizes_declaradas_pela_ti(limpo, tmp_path, monkeypatch):
    a, b = tmp_path / "nas_replicado", tmp_path / "backup"
    monkeypatch.setenv("RADAI_RAIZES_NUVEM", f' "{a}" ; {b};; ')
    assert nuvem.em_pasta_sincronizada(a / "x") is True
    assert nuvem.em_pasta_sincronizada(b) is True
    assert nuvem.em_pasta_sincronizada(tmp_path / "outra") is False


def test_onedrive_pela_variavel_do_proprio_cliente(limpo, tmp_path, monkeypatch):
    raiz = tmp_path / "Pessoal"
    monkeypatch.setenv("OneDriveCommercial", str(raiz))
    assert nuvem.em_pasta_sincronizada(raiz / "Documentos") is True


def test_onedrive_pelo_registro(limpo, tmp_path, monkeypatch):
    raiz = tmp_path / "Empresa - Biblioteca"
    monkeypatch.setattr(nuvem, "_onedrive_registro", lambda: [str(raiz)])
    assert nuvem.em_pasta_sincronizada(raiz / "pacientes") is True


def test_unidade_virtual_do_google_drive_pelo_registro(limpo, tmp_path, monkeypatch):
    espelho = tmp_path / "Meu Drive"
    monkeypatch.setattr(nuvem, "_ler_sync_targets", lambda: _sync_targets("Q:", str(espelho)))
    assert nuvem.em_pasta_sincronizada(Path("Q:\\Meu Drive\\pacientes")) is True
    assert nuvem.em_pasta_sincronizada(Path("q:\\")) is True
    assert nuvem.em_pasta_sincronizada(espelho / "x") is True
    assert nuvem.em_pasta_sincronizada(Path("R:\\pacientes")) is False


def test_sync_targets_quebrado_nao_derruba(limpo, tmp_path, monkeypatch):
    for lixo in (b"\x0a\xff\xff", b"\x07", b"", b"\x0a\x05G:"):
        monkeypatch.setattr(nuvem, "_ler_sync_targets", lambda lixo=lixo: lixo)
        assert nuvem.em_pasta_sincronizada(tmp_path / "x") is False


def test_caminhos_do_protobuf():
    blob = _sync_targets("G:", "C:\\Users\\x\\Meu Drive", "\\\\srv\\espelho")
    assert nuvem._caminhos_protobuf(blob) == ["G:", "C:\\Users\\x\\Meu Drive", "\\\\srv\\espelho"]


def test_banco_do_drive_corrompido_nao_derruba(limpo, tmp_path):
    pasta = limpo / "Google" / "DriveFS"
    pasta.mkdir(parents=True)
    (pasta / "root_preference_sqlite.db").write_bytes(b"isto nao e sqlite")
    assert nuvem.em_pasta_sincronizada(tmp_path / "x") is False


def test_banco_novo_e_relido(limpo, tmp_path):
    db = _drive(limpo, str(tmp_path / "primeira"))
    assert nuvem.em_pasta_sincronizada(tmp_path / "segunda") is False
    _drive(limpo, str(tmp_path / "segunda"))
    carimbo = db.stat().st_mtime + 10
    os.utime(db, (carimbo, carimbo))
    assert nuvem.em_pasta_sincronizada(tmp_path / "segunda") is True


def test_juncao_para_dentro_da_nuvem_e_nuvem(limpo, tmp_path):
    espelhada = tmp_path / "espelhada"
    (espelhada / "alvo").mkdir(parents=True)
    _drive(limpo, str(espelhada))
    juncao = tmp_path / "atalho"
    _winapi = pytest.importorskip("_winapi")
    _winapi.CreateJunction(str(espelhada / "alvo"), str(juncao))
    assert nuvem.em_pasta_sincronizada(juncao / "pacientes") is True


def test_registro_real_nao_levanta():
    assert isinstance(nuvem._onedrive_registro(), list)
