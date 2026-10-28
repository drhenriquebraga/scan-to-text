import os
import subprocess
from pathlib import Path

import pytest

from scan_to_text.es import ES, OrigemIntocavel
from scan_to_text.escrever import atomico
from scan_to_text.escrever.atomico import copiar, gravar, limpar_parciais, nome_parcial

ESTACAO, PID = "RECEPCAO-1", 4242


@pytest.fixture
def pastas(tmp_path):
    origem, destino = tmp_path / "origem", tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()
    return origem, destino, ES([origem])


@pytest.fixture
def esperas(monkeypatch):
    feitas = []
    monkeypatch.setattr(atomico, "_esperar", feitas.append)
    return feitas


def _parciais(raiz: Path) -> list[Path]:
    return sorted(raiz.rglob("*.parcial"))


# --- nome_parcial: porte de radai_sets.nome_parcial --------------------------

def test_parcial_leva_estacao_e_pid_ao_lado_do_alvo(tmp_path):
    alvo = tmp_path / "2026-04-02 - HEMOGRAMA.md"
    parcial = nome_parcial(alvo, ESTACAO, PID)
    assert parcial.parent == alvo.parent
    assert parcial.name == f"{alvo.name}.{ESTACAO}.{PID}.parcial"
    assert parcial != alvo.with_name(alvo.name + ".parcial")


def test_parcial_so_com_caractere_seguro(tmp_path):
    alvo = tmp_path / "a.md"
    parcial = nome_parcial(alvo, "SALA 2 · RÁDIO/CT", PID)
    meio = parcial.name[len(alvo.name) + 1:-len(f".{PID}.parcial")]
    assert meio and all(c.isascii() and (c.isalnum() or c in "-_") for c in meio)
    assert nome_parcial(alvo, "", PID).name == f"a.md.estacao.{PID}.parcial"


def test_corte_do_nome_da_estacao_nao_colide(tmp_path):
    alvo = tmp_path / "a.md"
    longa = "ESTACAO-DA-RECEPCAO-PRINCIPAL"
    a = nome_parcial(alvo, longa + "-A", PID)
    b = nome_parcial(alvo, longa + "-B", PID)
    assert a != b
    assert len(a.name) == len(b.name) == len(f"a.md.{'x' * 28}.{PID}.parcial")


def test_pids_diferentes_rascunhos_diferentes(tmp_path):
    alvo = tmp_path / "a.md"
    assert nome_parcial(alvo, ESTACAO, 1) != nome_parcial(alvo, ESTACAO, 2)


# --- gravar -----------------------------------------------------------------

def test_gravar_cria_pastas_e_nao_deixa_rascunho(pastas):
    _, destino, es = pastas
    alvo = destino / "FULANA DE TESTE - 2026-09-28" / "3 LABORATORIO" / "2026 - PSA.md"
    gravar(alvo, "# PSA\n".encode(), es, ESTACAO, PID)
    assert alvo.read_bytes() == "# PSA\n".encode()
    assert _parciais(destino) == []


def test_gravar_substitui_conteudo_anterior(pastas):
    _, destino, es = pastas
    alvo = destino / "COMPLETO.md"
    gravar(alvo, b"um", es, ESTACAO, PID)
    gravar(alvo, b"dois", es, ESTACAO, PID)
    assert alvo.read_bytes() == b"dois"


def test_gravar_recusa_destino_dentro_da_origem(pastas):
    origem, _, es = pastas
    with pytest.raises(OrigemIntocavel):
        gravar(origem / "PACIENTE" / "x.md", b"x", es, ESTACAO, PID)
    assert list(origem.iterdir()) == []


def test_permission_error_tenta_de_novo_com_espera_crescente(pastas, esperas, monkeypatch):
    _, destino, es = pastas
    alvo = destino / "x.md"
    substituir, falhas = os.replace, [2]

    def antivirus(a, b):
        if falhas[0]:
            falhas[0] -= 1
            raise PermissionError(13, "em_uso")
        substituir(a, b)

    monkeypatch.setattr(atomico.os, "replace", antivirus)
    gravar(alvo, b"conteudo", es, ESTACAO, PID)
    assert alvo.read_bytes() == b"conteudo"
    assert len(esperas) == 2 and esperas[0] < esperas[1]
    assert _parciais(destino) == []


def test_permission_error_sem_fim_levanta_e_nao_deixa_rascunho(pastas, esperas, monkeypatch):
    _, destino, es = pastas
    alvo = destino / "x.md"
    alvo.write_bytes(b"anterior")

    def preso(_a, _b):
        raise PermissionError(13, "em_uso")

    monkeypatch.setattr(atomico.os, "replace", preso)
    with pytest.raises(PermissionError):
        gravar(alvo, b"novo", es, ESTACAO, PID, tentativas=3)
    assert len(esperas) == 2
    assert alvo.read_bytes() == b"anterior"
    assert _parciais(destino) == []


def test_falha_no_meio_da_escrita_nao_deixa_rascunho(pastas, monkeypatch):
    _, destino, es = pastas
    alvo = destino / "x.md"

    def disco_cheio(_descritor):
        raise OSError(28, "sem_espaco")

    monkeypatch.setattr(atomico.os, "fsync", disco_cheio)
    with pytest.raises(OSError):
        gravar(alvo, b"x" * 1000, es, ESTACAO, PID)
    assert not alvo.exists()
    assert _parciais(destino) == []


def test_conteudo_igual_nao_regrava(pastas, monkeypatch):
    # Corrida perdida entre estações regrava bytes idênticos; com o arquivo
    # aberto num visualizador, substituir falharia sem necessidade.
    _, destino, es = pastas
    alvo = destino / "x.md"
    gravar(alvo, b"igual", es, ESTACAO, PID)

    def preso(_a, _b):
        raise PermissionError(13, "em_uso")

    monkeypatch.setattr(atomico.os, "replace", preso)
    gravar(alvo, b"igual", es, ESTACAO, PID)
    assert alvo.read_bytes() == b"igual"


def test_operacoes_usam_caminho_estendido(pastas, monkeypatch):
    # A estação da clínica costuma ter LongPathsEnabled=0; aqui pode estar
    # ligado, então o teste confere o prefixo em vez de confiar no sistema.
    assert atomico._longo(Path(r"C:\a\b.md")) == "\\\\?\\C:\\a\\b.md"
    assert atomico._longo(Path(r"\\srv\scans\b.md")) == "\\\\?\\UNC\\srv\\scans\\b.md"
    assert atomico._longo("\\\\?\\C:\\a") == "\\\\?\\C:\\a"
    _, destino, es = pastas
    vistos, substituir = [], os.replace

    def registra(a, b):
        vistos.extend([a, b])
        substituir(a, b)

    monkeypatch.setattr(atomico.os, "replace", registra)
    gravar(destino / "x.md", b"x", es, ESTACAO, PID)
    assert vistos and all(v.startswith("\\\\?\\") for v in vistos)


def test_caminho_longo_alem_de_260(pastas):
    _, destino, es = pastas
    pasta = destino
    while len(str(pasta)) < 200:
        pasta = pasta / ("P" * 20)
    alvo = pasta / ("D" * (240 - len(str(pasta)) - 4) + ".md")
    assert len(str(alvo)) == 240
    assert len(str(nome_parcial(alvo, ESTACAO, PID))) > 260
    gravar(alvo, b"longo", es, ESTACAO, PID)
    assert alvo.read_bytes() == b"longo"
    assert _parciais(destino) == []


# --- copiar -------------------------------------------------------------------

def test_copiar_intacto_sem_rascunho(pastas, tmp_path):
    _, destino, es = pastas
    fonte = tmp_path / "temp" / ("ab" * 32 + ".pdf")
    fonte.parent.mkdir()
    fonte.write_bytes(b"%PDF-1.7 " * 100_000)
    alvo = destino / "FULANA DE TESTE - 2026-09-28" / "ORIGINAIS" / "scan001.pdf"
    copiar(fonte, alvo, es, ESTACAO, PID)
    assert alvo.read_bytes() == fonte.read_bytes()
    assert _parciais(destino) == []


def test_copiar_recusa_destino_na_origem(pastas, tmp_path):
    origem, _, es = pastas
    fonte = tmp_path / "a.pdf"
    fonte.write_bytes(b"a")
    with pytest.raises(OrigemIntocavel):
        copiar(fonte, origem / "a.pdf", es, ESTACAO, PID)
    assert list(origem.iterdir()) == []


def test_copiar_com_falha_nao_deixa_rascunho(pastas, tmp_path, esperas, monkeypatch):
    _, destino, es = pastas
    fonte = tmp_path / "a.pdf"
    fonte.write_bytes(b"a" * 100)

    def preso(_a, _b):
        raise PermissionError(13, "em_uso")

    monkeypatch.setattr(atomico.os, "replace", preso)
    with pytest.raises(PermissionError):
        copiar(fonte, destino / "a.pdf", es, ESTACAO, PID)
    assert list(destino.iterdir()) == []


# --- limpar_parciais ------------------------------------------------------------

def test_limpar_parciais_so_os_desta_estacao(tmp_path):
    raiz = tmp_path / "destino"
    fundo = raiz / "FULANA DE TESTE - 2026-09-28" / "_scan-to-text"
    feitos = raiz / "_scan-to-text" / "feitos"
    fundo.mkdir(parents=True)
    feitos.mkdir(parents=True)
    meus = [nome_parcial(fundo / "manifesto.json", ESTACAO, 11),
            nome_parcial(feitos / ("ab" * 32 + ".json"), ESTACAO, 22),
            nome_parcial(raiz / "x.md", ESTACAO, 33)]
    alheios = [nome_parcial(fundo / "manifesto.json", "OUTRA-ESTACAO", 11),
               nome_parcial(raiz / "x.md", ESTACAO + "X", 33),
               raiz / "laudo.parcial",
               raiz / f"x.md.{ESTACAO}.abc.parcial"]
    for arquivo in meus + alheios:
        arquivo.write_bytes(b"r")

    fora = tmp_path / "fora"
    fora.mkdir()
    nome_parcial(fora / "y.md", ESTACAO, 44).write_bytes(b"r")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(raiz / "atalho"), str(fora)],
                   check=True, capture_output=True)

    assert limpar_parciais(raiz, ESTACAO) == 3
    assert not any(p.exists() for p in meus)
    assert all(p.exists() for p in alheios)
    assert nome_parcial(fora / "y.md", ESTACAO, 44).exists()


def test_limpar_parciais_nao_apaga_dentro_de_origem(tmp_path):
    # A exclusão também passa pela E/S segura, mesmo com uma origem mal
    # configurada dentro do destino.
    raiz = tmp_path / "destino"
    origem = raiz / "scanner"
    (origem / "sub").mkdir(parents=True)
    na_origem = [nome_parcial(origem / "scan001.pdf", ESTACAO, 9),
                 nome_parcial(origem / "sub" / "scan002.pdf", ESTACAO, 9)]
    meu = nome_parcial(raiz / "x.md", ESTACAO, 9)
    for arquivo in na_origem + [meu]:
        arquivo.write_bytes(b"r")
    guarda = ES([origem])
    assert limpar_parciais(raiz, ESTACAO, guarda) == 1
    assert not meu.exists()
    assert all(p.exists() for p in na_origem)
    assert limpar_parciais(origem, ESTACAO, guarda) == 0
    assert all(p.exists() for p in na_origem)


def test_limpar_parciais_com_estacao_de_nome_sujo(tmp_path):
    suja = "SALA 2 · RÁDIO/CT"
    nome_parcial(tmp_path / "x.md", suja, 5).write_bytes(b"r")
    assert limpar_parciais(tmp_path, suja) == 1
    assert limpar_parciais(tmp_path / "nao-existe", suja) == 0
