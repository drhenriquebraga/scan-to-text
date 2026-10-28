import json
import os
import subprocess
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pytest

from scan_to_text import registro
from scan_to_text.es import ES, OrigemIntocavel
from scan_to_text.registro import (ManifestoInvalido, achar_pasta, gravar_manifesto, ja_feito,
                                   ler_manifesto, marcar_feito)

ESTACAO, PID = "RECEPCAO", 7
SHA = "ab" * 32
PARTICULAS = {"DA", "DE", "DO", "DOS", "DAS", "E"}


def _chave(nome: str) -> str:
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", nome)
                         if unicodedata.category(c) != "Mn")
    return " ".join(sem_acento.upper().split())


def _compativeis(a: str, b: str) -> bool:
    """Substituto simples da regra de identidade, só para o teste."""
    pa = [p.rstrip(".") for p in _chave(a).split() if p not in PARTICULAS]
    pb = [p.rstrip(".") for p in _chave(b).split() if p not in PARTICULAS]
    if len(pa) != len(pb) or pa[0] != pb[0] or pa[-1] != pb[-1]:
        return False
    return all(x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
               for x, y in zip(pa, pb))


def _manifesto(nome: str | None = "FULANA DE TESTE", nascimento: str | None = None,
               variantes: list[str] | None = None, pasta: str = "") -> dict:
    return {
        "pasta": pasta or f"{nome} - 2026-09-28",
        "primeiro_processamento": "2026-09-28T10:00:00",
        "ultima_atualizacao": "2026-09-28T10:05:00",
        "paciente": None if nome is None else {
            "exibido": nome, "variantes": variantes or [nome], "nascimento": nascimento,
            "confianca": 0.95},
        "documentos": [{
            "id": "abababababab-01", "categoria": 3,
            "data": {"valor": "2026-04-02", "precisao": "dia", "papel": "coleta"},
            "titulo": "HEMOGRAMA", "instituicao": "LABORATORIO EXEMPLO",
            "md": "3 LABORATORIO/2026-04-02 - HEMOGRAMA.md",
            "conferencia": "3 LABORATORIO/2026-04-02 - HEMOGRAMA.pdf",
            "origens": [{"caminho": "scan001.pdf", "sha256": SHA, "paginas": [1, 2]}],
            "duplicatas": [], "paginas_em_branco": [3], "paginas_leitura_incerta": [],
        }],
        "originais": ["ORIGINAIS/scan001.pdf"],
        "versoes": {"app": "0.0.1", "pacote": "pt-BR", "docling": "2.130.0",
                    "ocr": {"rapidocr": "3.9.2"}},
    }


@pytest.fixture
def ambiente(tmp_path):
    origem, destino = tmp_path / "origem", tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()
    return origem, destino, ES([origem])


def _pasta_com_manifesto(destino: Path, nome_pasta: str, es: ES, **kw) -> Path:
    pasta = destino / nome_pasta
    gravar_manifesto(pasta, _manifesto(pasta=nome_pasta, **kw), es, ESTACAO, PID)
    return pasta


def _achar(destino: Path, nome: str, nascimento: date | None = None) -> Path | None:
    return achar_pasta(destino, nome, nascimento, _compativeis, _chave)


# --- manifesto ----------------------------------------------------------------

def test_manifesto_atomico_e_relido_igual(ambiente):
    _, destino, es = ambiente
    pasta = destino / "FULANA DE TESTE - 2026-09-28"
    original = _manifesto()
    gravar_manifesto(pasta, original, es, ESTACAO, PID)
    arquivo = pasta / "_scan-to-text" / "manifesto.json"
    assert arquivo.is_file()
    assert sorted(p.name for p in arquivo.parent.iterdir()) == ["manifesto.json"]
    relido = ler_manifesto(pasta)
    assert relido == {**original, "versao_esquema": 1}
    assert original.get("versao_esquema") is None


def test_manifesto_deterministico_byte_a_byte(ambiente):
    _, destino, es = ambiente
    a, b = destino / "A", destino / "B"
    gravar_manifesto(a, _manifesto(), es, ESTACAO, PID)
    invertido = dict(reversed(list(_manifesto().items())))
    gravar_manifesto(b, invertido, es, "OUTRA", 99)
    bytes_a = (a / "_scan-to-text" / "manifesto.json").read_bytes()
    assert bytes_a == (b / "_scan-to-text" / "manifesto.json").read_bytes()
    assert "FULANA DE TESTE".encode() in bytes_a


def test_manifesto_converte_datas_para_iso(ambiente):
    _, destino, es = ambiente
    m = _manifesto()
    m["paciente"]["nascimento"] = date(1980, 1, 31)
    m["ultima_atualizacao"] = datetime(2026, 9, 28, 10, 5)
    gravar_manifesto(destino / "P", m, es, ESTACAO, PID)
    relido = ler_manifesto(destino / "P")
    assert relido["paciente"]["nascimento"] == "1980-01-31"
    assert relido["ultima_atualizacao"] == "2026-09-28T10:05:00"


def test_manifesto_incompleto_e_recusado_sem_gravar(ambiente):
    _, destino, es = ambiente
    m = _manifesto()
    del m["originais"]
    del m["documentos"][0]["origens"][0]["sha256"]
    del m["paciente"]["nascimento"]
    with pytest.raises(ManifestoInvalido) as erro:
        gravar_manifesto(destino / "P", m, es, ESTACAO, PID)
    assert set(erro.value.faltando) == {"originais", "documentos[0].origens[0].sha256",
                                        "paciente.nascimento"}
    assert not (destino / "P").exists()


def test_manifesto_sem_paciente_e_valido(ambiente):
    _, destino, es = ambiente
    gravar_manifesto(destino / "D", _manifesto(nome=None, pasta="D"), es, ESTACAO, PID)
    assert ler_manifesto(destino / "D")["paciente"] is None


def test_manifesto_na_origem_e_recusado(ambiente):
    origem, _, es = ambiente
    with pytest.raises(OrigemIntocavel):
        gravar_manifesto(origem / "P", _manifesto(), es, ESTACAO, PID)
    assert list(origem.iterdir()) == []


def test_ler_manifesto_ausente_corrompido_e_com_bom(tmp_path):
    assert ler_manifesto(tmp_path / "nada") is None
    arquivo = tmp_path / "P" / "_scan-to-text" / "manifesto.json"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(b'{"pasta": ')
    with pytest.raises(ManifestoInvalido):
        ler_manifesto(tmp_path / "P")
    arquivo.write_bytes(b"[1, 2]")
    with pytest.raises(ManifestoInvalido):
        ler_manifesto(tmp_path / "P")
    arquivo.write_bytes('{"pasta": "Ç"}'.encode("utf-8-sig"))
    assert ler_manifesto(tmp_path / "P") == {"pasta": "Ç"}


# --- achar_pasta --------------------------------------------------------------

def test_chave_identica_reaproveita(ambiente):
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                                 nome="MARIA DA SILVA")
    assert _achar(destino, "Maria da  Silva") == pasta
    assert _achar(destino, "MARIA DA SILVA", date(1970, 1, 1)) == pasta


def test_chave_identica_com_nascimento_registrado_e_documento_sem_nascimento(ambiente):
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                                 nome="MARIA DA SILVA", nascimento="1980-01-31")
    assert _achar(destino, "MARIA DA SILVA") == pasta
    assert _achar(destino, "MARIA DA SILVA", date(1980, 1, 31)) == pasta


def test_homonimo_com_nascimento_diferente_nao_reaproveita(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                         nome="MARIA DA SILVA", nascimento="1980-01-31")
    assert _achar(destino, "MARIA DA SILVA", date(1991, 5, 5)) is None


def test_homonimo_escolhe_a_pasta_do_mesmo_nascimento(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                         nome="MARIA DA SILVA", nascimento="1980-01-31")
    segunda = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                                   nome="MARIA DA SILVA", nascimento="1991-05-05")
    assert _achar(destino, "MARIA DA SILVA", date(1991, 5, 5)) == segunda


def test_dois_homonimos_e_documento_sem_nascimento_e_duvida(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                         nome="MARIA DA SILVA", nascimento="1980-01-31")
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                         nome="MARIA DA SILVA", nascimento="1991-05-05")
    assert _achar(destino, "MARIA DA SILVA") is None


def test_na_duvida_entre_homonimos_volta_a_pasta_que_a_mesma_origem_abriu(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                         nome="MARIA DA SILVA", nascimento="1980-01-31")
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                         nome="MARIA DA SILVA", nascimento="1991-05-05")
    marca = {"caminho": "scan002.pdf", "sha256": SHA}
    aberta = destino / "MARIA DA SILVA - 2026-09-01 (3)"
    gravar_manifesto(aberta, {**_manifesto(nome="MARIA DA SILVA", pasta=aberta.name),
                              "origem": marca}, es, ESTACAO, PID)
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave,
                       origem=marca) == aberta
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave,
                       origem={**marca, "caminho": "scan003.pdf"}) is None
    assert _achar(destino, "MARIA DA SILVA") is None
    assert achar_pasta(destino, "MARIA DA SILVA", date(1991, 5, 5), _compativeis, _chave,
                       origem=marca) == destino / "MARIA DA SILVA - 2026-09-01 (2)"


def test_pasta_da_mesma_origem_que_ganhou_nascimento_nao_e_reaberta_na_duvida(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                         nome="MARIA DA SILVA", nascimento="1980-01-31")
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                         nome="MARIA DA SILVA", nascimento="1991-05-05")
    marca = {"caminho": "scan002.pdf", "sha256": SHA}
    tomada = destino / "MARIA DA SILVA - 2026-09-01 (3)"
    gravar_manifesto(tomada, {**_manifesto(nome="MARIA DA SILVA", nascimento="2001-02-20",
                                           pasta=tomada.name), "origem": marca},
                     es, ESTACAO, PID)
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave,
                       origem=marca) is None


def test_com_homonimo_registrado_a_pasta_sem_nascimento_nao_recebe_outro_nascimento(ambiente):
    _, destino, es = ambiente
    de_1980 = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                                   nome="MARIA DA SILVA", nascimento="1980-01-31")
    sem = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                               nome="MARIA DA SILVA")
    assert _achar(destino, "MARIA DA SILVA", date(2001, 2, 20)) is None
    assert _achar(destino, "MARIA DA SILVA", date(1980, 1, 31)) == de_1980
    os.rename(de_1980, destino / "_fora")
    assert _achar(destino, "MARIA DA SILVA", date(2001, 2, 20)) == sem


def test_nome_que_o_arquivo_mostra_com_dois_nascimentos_nao_adota_pasta_sem_nascimento(ambiente):
    _, destino, es = ambiente
    sem = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                               nome="MARIA DA SILVA")
    dois = frozenset({date(1980, 1, 31), date(1991, 5, 5)})

    def achar(nascimento, no_arquivo):
        return achar_pasta(destino, "MARIA DA SILVA", nascimento, _compativeis, _chave,
                           no_arquivo=no_arquivo)

    assert achar(date(1980, 1, 31), dois) is None
    assert achar(date(1980, 1, 31), frozenset({date(1980, 1, 31)})) == sem
    de_1980 = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es,
                                   nome="MARIA DA SILVA", nascimento="1980-01-31")
    assert achar(date(1980, 1, 31), dois) == de_1980
    assert achar(date(1991, 5, 5), dois) is None


def test_sem_nascimento_e_nascimento_lido_no_arquivo_nao_vai_para_a_homonima(ambiente):
    _, destino, es = ambiente
    de_1991 = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                                   nome="MARIA DA SILVA", nascimento="1991-05-05")
    lido = frozenset({date(1980, 1, 31)})
    assert _achar(destino, "MARIA DA SILVA") == de_1991
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave,
                       no_arquivo=lido) is None
    marca = {"caminho": "lote.zip/b.pdf", "sha256": SHA}
    aberta = destino / "MARIA DA SILVA - 2026-09-01 (2)"
    gravar_manifesto(aberta, {**_manifesto(nome="MARIA DA SILVA", pasta=aberta.name),
                              "origem": marca}, es, ESTACAO, PID)
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave, origem=marca,
                       no_arquivo=lido) == aberta
    assert achar_pasta(destino, "MARIA DA SILVA", None, _compativeis, _chave,
                       no_arquivo=lido) is None


def test_homonimo_registrado_no_destino(ambiente):
    _, destino, es = ambiente

    def tem(nascimento):
        return registro.tem_homonimo(destino, "Maria da Silva", nascimento, _compativeis, _chave)

    assert not tem(date(1980, 1, 31))
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es, nome="MARIA DA SILVA")
    _pasta_com_manifesto(destino, "MARIA J DA SILVA - 2026-09-01", es, nome="MARIA J DA SILVA",
                         nascimento="1991-05-05")
    assert not tem(date(1980, 1, 31))
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01 (2)", es, nome="MARIA DA SILVA",
                         nascimento="1980-01-31")
    assert not tem(date(1980, 1, 31))
    assert tem(date(1991, 5, 5))


def test_paginas_registradas_do_arquivo_com_a_pasta_e_o_nascimento_dela(ambiente):
    _, destino, es = ambiente
    outro = "cd" * 32
    m = _manifesto(nome="MARIA DA SILVA", nascimento="1980-01-31",
                   pasta="MARIA DA SILVA - 2026-09-01")
    m["documentos"][0]["origens"] = [{"caminho": "lote.zip/b.pdf", "sha256": outro,
                                      "paginas": [0]}]
    m["documentos"][0]["duplicatas"] = [{"caminho": "lote.zip/c.pdf", "sha256": SHA,
                                         "paginas": [0, 1]}]
    gravar_manifesto(destino / "MARIA DA SILVA - 2026-09-01", m, es, ESTACAO, PID)
    _pasta_com_manifesto(destino, "JOAO DE TESTE - 2026-09-01", es, nome="JOAO DE TESTE")
    _pasta_com_manifesto(destino, "_ARQUIVOS COM MAIS DE UM PACIENTE", es, nome="JOAO DE TESTE")
    _pasta_com_manifesto(destino, "DESCONHECIDO - 2026-09-01 - SCAN001", es, nome=None)
    estranho = _manifesto(nome="ANA DE TESTE", pasta="ANA DE TESTE - 2026-09-01")
    estranho["documentos"][0]["origens"] = [{"caminho": "x.pdf", "sha256": SHA,
                                             "paginas": [[0]]}, "lixo"]
    estranho["documentos"].append("lixo")
    pasta_estranha = destino / "ANA DE TESTE - 2026-09-01" / registro.PASTA_SISTEMA
    pasta_estranha.mkdir(parents=True)
    (pasta_estranha / registro.MANIFESTO).write_text(json.dumps(estranho), encoding="utf-8")
    (destino / "CORROMPIDO - 2026-09-01" / registro.PASTA_SISTEMA).mkdir(parents=True)
    (destino / "CORROMPIDO - 2026-09-01" / registro.PASTA_SISTEMA / registro.MANIFESTO
     ).write_text("{", encoding="utf-8")

    assert registro.paginas_registradas(destino, SHA) == {
        ("scan001.pdf", (1, 2)): (destino / "JOAO DE TESTE - 2026-09-01", None),
        ("lote.zip/c.pdf", (0, 1)): (destino / "MARIA DA SILVA - 2026-09-01", "1980-01-31"),
    }
    assert registro.paginas_registradas(destino, outro) == {
        ("lote.zip/b.pdf", (0,)): (destino / "MARIA DA SILVA - 2026-09-01", "1980-01-31")}
    assert registro.paginas_registradas(destino / "ausente", SHA) == {}


def test_pasta_e_da_pessoa_pela_regra_de_achar_pasta(ambiente):
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA JOSE DA SILVA - 2026-09-01", es,
                                 nome="MARIA JOSE DA SILVA", nascimento="1980-01-31")

    def e_da(nome, nascimento=None):
        return registro.e_da_pessoa(pasta, nome, nascimento, _compativeis, _chave,
                                    registro.Retrato(destino))

    assert e_da("Maria José da Silva")
    assert e_da("MARIA JOSE DA SILVA", date(1980, 1, 31))
    assert e_da("MARIA J. DA SILVA", date(1980, 1, 31))
    assert not e_da("MARIA J. DA SILVA")
    assert not e_da("MARIA JOSE DA SILVA", date(1991, 5, 5))
    assert not e_da("JOAO DE TESTE")
    assert not e_da("JOAO DE TESTE", date(1980, 1, 31))


def test_retrato_le_cada_manifesto_uma_vez(ambiente, monkeypatch):
    _, destino, es = ambiente
    for i in range(5):
        _pasta_com_manifesto(destino, f"MARIA {i} DA SILVA - 2026-09-01", es,
                             nome=f"MARIA {i} DA SILVA", nascimento="1980-01-31")
    lidas = []
    ler = registro.ler_manifesto
    monkeypatch.setattr(registro, "ler_manifesto", lambda p: (lidas.append(p), ler(p))[1])
    retrato = registro.Retrato(destino, SHA)
    for nome in ("MARIA 1 DA SILVA", "MARIA 9 DA SILVA"):
        achar_pasta(destino, nome, None, _compativeis, _chave, retrato=retrato)
        registro.tem_homonimo(destino, nome, date(1991, 5, 5), _compativeis, _chave,
                              retrato=retrato)
    registro.paginas_registradas(destino, SHA, retrato)
    assert len(lidas) == 5


def test_retrato_guarda_de_cada_manifesto_so_o_que_a_escrita_usa(ambiente):
    _, destino, es = ambiente
    m = _manifesto(nome="MARIA DA SILVA", nascimento="1980-01-31",
                   pasta="MARIA DA SILVA - 2026-09-01")
    outro = {**m["documentos"][0], "id": "cdcdcdcdcdcd-01",
             "origens": [{"caminho": "scan002.pdf", "sha256": "cd" * 32, "paginas": [0]}]}
    m["documentos"].append(outro)
    m.update(origem={"caminho": "scan001.pdf", "sha256": SHA}, pendentes=["cd" * 32])
    pasta = destino / "MARIA DA SILVA - 2026-09-01"
    gravar_manifesto(pasta, m, es, ESTACAO, PID)
    retrato = registro.Retrato(destino, SHA)
    assert retrato.resumo(pasta) == {"paciente": m["paciente"], "origem": m["origem"],
                                     "pendentes": m["pendentes"]}
    assert retrato.registros(pasta) == [{"caminho": "scan001.pdf", "sha256": SHA,
                                         "paginas": [1, 2]}]


def test_variante_compativel_so_com_nascimento_igual(ambiente):
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA JOSE DA SILVA - 2026-09-01", es,
                                 nome="MARIA JOSE DA SILVA", nascimento="1980-01-31")
    assert _achar(destino, "MARIA J. DA SILVA", date(1980, 1, 31)) == pasta
    assert _achar(destino, "MARIA J. DA SILVA") is None
    assert _achar(destino, "MARIA J. DA SILVA", date(1980, 2, 1)) is None


def test_variante_compativel_sem_nascimento_registrado_nao_reaproveita(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA JOSE DA SILVA - 2026-09-01", es,
                         nome="MARIA JOSE DA SILVA")
    assert _achar(destino, "MARIA J. DA SILVA", date(1980, 1, 31)) is None


def test_ana_nunca_casa_com_ana_maria(ambiente):
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "ANA MARIA SOUZA - 2026-09-01", es,
                         nome="ANA MARIA SOUZA", nascimento="1980-01-31")
    assert _achar(destino, "ANA SOUZA", date(1980, 1, 31)) is None


def test_variante_registrada_so_reaproveita_com_nascimento_igual(ambiente):
    # A abreviação registrada entrou na pasta pelo nascimento; sem ele, ela
    # pode ser de outra pessoa (MARIA JULIA DA SILVA).
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA JOSE DA SILVA - 2026-09-01", es,
                                 nome="MARIA JOSE DA SILVA", nascimento="1980-01-31",
                                 variantes=["MARIA J. DA SILVA", "MARIA JOSE DA SILVA"])
    assert _achar(destino, "MARIA J. DA SILVA") is None
    assert _achar(destino, "MARIA J. DA SILVA", date(1991, 5, 5)) is None
    assert _achar(destino, "MARIA J. DA SILVA", date(1980, 1, 31)) == pasta


def test_variante_registrada_nao_e_chave_identica(ambiente):
    # A chave idêntica é a do nome que nomeia a pasta, como no pastaCasaPaciente.
    _, destino, es = ambiente
    _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es, nome="MARIA DA SILVA",
                         variantes=["MARIA DA SILVA", "MARIA DA SILVA SOUZA"])
    assert _achar(destino, "MARIA DA SILVA SOUZA") is None


def test_exibido_do_manifesto_conta_como_chave_identica(ambiente):
    # O nome da pasta é cortado em 80 caracteres; o exibido guarda o inteiro.
    _, destino, es = ambiente
    pasta = _pasta_com_manifesto(destino, "MARIA DA SILVA - 2026-09-01", es,
                                 nome="MARIA DA SILVA")
    m = ler_manifesto(pasta)
    m["paciente"]["exibido"] = "MARIA DA SILVA SOUZA"
    gravar_manifesto(pasta, m, es, ESTACAO, PID)
    assert _achar(destino, "MARIA DA SILVA SOUZA") == pasta


def test_pasta_sem_manifesto_casa_pelo_nome_da_pasta(ambiente):
    _, destino, _ = ambiente
    pasta = destino / "JOHN DOE - 2026-09-01"
    pasta.mkdir()
    assert _achar(destino, "JOHN DOE") == pasta
    assert _achar(destino, "JOHN") is None


def test_pasta_com_area_do_sistema_e_sem_manifesto_e_duvida(ambiente):
    # Escrita interrompida: a pasta não diz de quem é, nem o nascimento.
    _, destino, _ = ambiente
    pasta = destino / "JOHN DOE - 2026-09-01"
    (pasta / "_scan-to-text").mkdir(parents=True)
    (pasta / "3 LABORATORIO").mkdir()
    (pasta / "3 LABORATORIO" / "2026-04-02 - HEMOGRAMA.md").write_text("x", encoding="utf-8")
    assert _achar(destino, "JOHN DOE") is None
    assert _achar(destino, "JOHN DOE", date(1975, 8, 15)) is None


def test_pastas_de_sistema_desconhecido_e_corrompido_ficam_de_fora(ambiente, tmp_path):
    _, destino, es = ambiente
    (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").mkdir()
    (destino / "_scan-to-text").mkdir()
    _pasta_com_manifesto(destino, "DESCONHECIDO - 2026-09-01 - SCAN007", es, nome=None)
    corrompida = destino / "JOHN DOE - 2026-09-01"
    (corrompida / "_scan-to-text").mkdir(parents=True)
    (corrompida / "_scan-to-text" / "manifesto.json").write_bytes(b"{")
    fora = tmp_path / "fora" / "JOHN DOE - 2026-09-02"
    fora.mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(destino / "JOHN DOE - 2026-09-02"),
                    str(fora)], check=True, capture_output=True)
    assert _achar(destino, "JOHN DOE") is None
    assert _achar(destino, "DESCONHECIDO") is None


def test_destino_ausente_nao_tem_pasta(tmp_path):
    assert _achar(tmp_path / "nao-existe", "JOHN DOE") is None


# --- feitos ---------------------------------------------------------------------

def test_feito_marcado_e_consultado(ambiente):
    _, destino, es = ambiente
    assert not ja_feito(destino, SHA)
    marcar_feito(destino, SHA, ["JOHN DOE - 2026-09-28"], es, ESTACAO, PID)
    assert ja_feito(destino, SHA)
    assert ja_feito(destino, SHA.upper())
    arquivo = destino / "_scan-to-text" / "feitos" / f"{SHA}.json"
    assert json.loads(arquivo.read_text(encoding="utf-8"))["pastas"] == ["JOHN DOE - 2026-09-28"]
    assert list(arquivo.parent.glob("*.parcial")) == []


def test_feito_de_novo_soma_as_pastas(ambiente):
    _, destino, es = ambiente
    marcar_feito(destino, SHA, ["B - 2026-09-28"], es, ESTACAO, PID)
    marcar_feito(destino, SHA, ["A - 2026-09-28", "B - 2026-09-28"], es, "OUTRA", 8)
    arquivo = destino / "_scan-to-text" / "feitos" / f"{SHA}.json"
    assert json.loads(arquivo.read_text(encoding="utf-8"))["pastas"] == \
        ["A - 2026-09-28", "B - 2026-09-28"]


def test_feito_que_nao_deu_para_ler_nao_e_sobrescrito(ambiente, monkeypatch):
    _, destino, es = ambiente
    marcar_feito(destino, SHA, ["A - 2026-09-28"], es, ESTACAO, PID)
    arquivo = destino / "_scan-to-text" / "feitos" / f"{SHA}.json"
    antes = arquivo.read_bytes()
    ler = Path.read_bytes

    def travado(self):
        if self.name == arquivo.name:
            raise PermissionError(13, "em_uso")
        return ler(self)

    monkeypatch.setattr(Path, "read_bytes", travado)
    with pytest.raises(PermissionError):
        marcar_feito(destino, SHA, ["B - 2026-09-28"], es, ESTACAO, PID)
    monkeypatch.undo()
    assert arquivo.read_bytes() == antes


def test_feito_que_nao_deu_para_consultar_levanta(ambiente, monkeypatch):
    # Destino oscilando não pode passar por "não feito": a pasta que alguém
    # moveu ou apagou seria recriada. O arquivo espera na fila.
    _, destino, es = ambiente
    marcar_feito(destino, SHA, ["A - 2026-09-28"], es, ESTACAO, PID)
    consultar = os.stat

    def sem_acesso(caminho, *args, **kwargs):
        if os.fspath(caminho).endswith(f"{SHA}.json"):
            raise PermissionError(13, "acesso_negado")
        return consultar(caminho, *args, **kwargs)

    monkeypatch.setattr(registro.os, "stat", sem_acesso)
    with pytest.raises(PermissionError):
        ja_feito(destino, SHA)
    monkeypatch.undo()
    assert ja_feito(destino, SHA)
    assert not ja_feito(destino, "cd" * 32)
    assert not ja_feito(destino / "nao-existe", SHA)


def test_sha256_invalido_e_recusado(ambiente):
    _, destino, es = ambiente
    for ruim in ("..\\..\\x", "ab" * 31, "zz" * 32):
        with pytest.raises(ValueError):
            ja_feito(destino, ruim)
        with pytest.raises(ValueError):
            marcar_feito(destino, ruim, ["P"], es, ESTACAO, PID)


def test_feito_na_origem_e_recusado(ambiente):
    origem, _, es = ambiente
    with pytest.raises(OrigemIntocavel):
        marcar_feito(origem, SHA, ["P"], es, ESTACAO, PID)
    assert list(origem.iterdir()) == []


def test_pasta_do_sistema_tem_nome_fixo():
    assert registro.PASTA_SISTEMA == "_scan-to-text"
