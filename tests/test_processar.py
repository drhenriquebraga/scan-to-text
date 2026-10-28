"""Ponta a ponta sem OCR: um leitor falso devolve páginas montadas à mão.

Nomes e instituições são inventados. O que importa aqui é a árvore no destino:
paciente certo, nada de um paciente na pasta do outro, origem intocada, nada se
perde.
"""
import errno
import functools
import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from scan_to_text import pacote as pacotes
from scan_to_text import processar as modulo_processar
from scan_to_text import registro
from scan_to_text.es import ES
from scan_to_text.escrever import arvore, atomico
from scan_to_text.ler.pdf import Ilegivel
from scan_to_text.processar import Contexto, processar
from scan_to_text.tipos import Linha, Pagina, Tabela
from scan_to_text.travas import Trava

HOJE = date(2026, 10, 25)


def _linha(texto, y, camada="corpo", tipo="texto", confianca=0.99):
    return Linha(texto, (0.06, y, 0.9, y + 0.02), camada=camada, tipo=tipo, confianca=confianca)


def _hemograma(nome="FULANA DE TESTE SINTÉTICA", hb="12,4", coleta="02/04/2026",
               nascimento="01/05/1960"):
    return (
        _linha("LABORATÓRIO EXEMPLO ANÁLISES CLÍNICAS", 0.03, "cabecalho"),
        _linha("Rua Fictícia, 100 - Centro - Tel (11) 5555-0000", 0.06, "cabecalho"),
        _linha(f"Paciente: {nome}", 0.10),
        _linha(f"Data de nascimento: {nascimento}", 0.115),
        _linha("Médico solicitante: Dr. Beltrano Exemplar CRM 00000", 0.13),
        _linha(f"Data da coleta: {coleta}", 0.145),
        _linha("HEMOGRAMA COMPLETO", 0.18, tipo="secao"),
        Tabela((("Exame", "Resultado", "Unidade", "Valores de referência"),
                ("Hemoglobina", hb, "g/dL", "12,0 a 16,0"),
                ("Hematócrito", "37,1", "%", "36,0 a 46,0")),
               ((1.0,) * 4, (0.99, 0.98, 0.97, 0.99), (0.99,) * 4), (0.06, 0.22, 0.94, 0.30), 1),
        _linha("Página 1 de 1", 0.95, "rodape"),
    )


def _tomografia(nome="CICRANO DE EXEMPLO TESTE"):
    return (
        _linha("CLÍNICA DE IMAGEM EXEMPLO", 0.03, "cabecalho"),
        _linha(f"Paciente: {nome}", 0.10),
        _linha("Data do exame: 10/03/2026", 0.13),
        _linha("TOMOGRAFIA COMPUTADORIZADA DO TÓRAX", 0.18, tipo="secao"),
        _linha("Técnica: aquisição helicoidal sem contraste.", 0.24),
        _linha("Conclusão: pulmões sem alterações.", 0.28),
    )


class LeitorFalso:
    def __init__(self):
        self.por_sha: dict[str, list[tuple]] = {}

    def registrar(self, arquivo: Path, paginas: list[tuple]) -> None:
        self.por_sha[hashlib.sha256(arquivo.read_bytes()).hexdigest()] = paginas

    def ler_arquivo(self, caminho: Path, sha256: str) -> list[Pagina]:
        if sha256 not in self.por_sha:
            raise Ilegivel("corrompido")
        return [Pagina(sha256, i, blocos or (), em_branco=blocos is None)
                for i, blocos in enumerate(self.por_sha[sha256])]


def _pdf(caminho: Path, paginas: int, cor: str = "white") -> Path:
    # Sem data: o mesmo cenário montado duas vezes tem os mesmos sha256.
    imagens = [Image.new("RGB", (200 + i, 280), cor) for i in range(paginas)]
    imagens[0].save(caminho, save_all=True, append_images=imagens[1:], resolution=72,
                    creationDate=None, modDate=None)
    return caminho


@functools.cache
def _pacote():
    return pacotes.carregar("pt-BR")


def _montar(raiz: Path):
    origem, destino = raiz / "scanner", raiz / "destino"
    origem.mkdir(parents=True)
    destino.mkdir()
    leitor = LeitorFalso()
    ctx = Contexto(pacote=_pacote(), leitor=leitor, es=ES([origem]),
                   destinos=[destino], estacao="estacao-teste", pid=4242, hoje=HOJE,
                   pasta_temp=raiz / "temp")
    return origem, destino, leitor, ctx


@pytest.fixture
def ambiente(tmp_path):
    return _montar(tmp_path)


def _arquivos(raiz: Path) -> dict[str, bytes]:
    return {p.relative_to(raiz).as_posix(): p.read_bytes() for p in raiz.rglob("*") if p.is_file()}


def _pastas_de_paciente(destino: Path) -> list[Path]:
    return sorted(p for p in destino.iterdir() if p.is_dir() and not p.name.startswith("_"))


def _texto_md(pasta: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in pasta.rglob("*.md"))


def test_laudo_de_um_paciente_vira_a_arvore_da_spec(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    resumo = processar(arquivo, ctx)
    assert resumo["situacao"] == "feito" and resumo["documentos"] == 1
    [pasta] = _pastas_de_paciente(destino)
    assert pasta.name == "FULANA DE TESTE SINTETICA - 2026-10-25"
    mds = list((pasta / "3 LABORATORIO").glob("*.md"))
    assert [m.name for m in mds] == ["2026-04-02 - HEMOGRAMA COMPLETO.md"]
    texto = mds[0].read_text(encoding="utf-8")
    assert "Hemoglobina" in texto and "12,4" in texto
    assert "12,0 a 16,0" not in texto            # valor de referência sai em 3 LABORATORIO
    assert "scan001" not in texto and "2026-10-25" not in texto
    assert (pasta / "3 LABORATORIO" / "2026-04-02 - HEMOGRAMA COMPLETO.pdf").is_file()
    assert (pasta / "ORIGINAIS" / "scan001.pdf").read_bytes() == arquivo.read_bytes()
    assert "HEMOGRAMA COMPLETO" in (pasta / "COMPLETO.md").read_text(encoding="utf-8")
    manifesto = json.loads((pasta / "_scan-to-text" / "manifesto.json").read_text(encoding="utf-8"))
    assert manifesto["paciente"]["exibido"] == "FULANA DE TESTE SINTÉTICA"
    assert manifesto["documentos"][0]["origens"][0]["caminho"] == "scan001.pdf"
    assert manifesto["originais"] == ["scan001.pdf"]


def test_arquivo_com_dois_pacientes_nao_mistura_nada(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "mix.pdf", 2)
    leitor.registrar(arquivo, [_hemograma(), _tomografia()])
    processar(arquivo, ctx)
    pastas = {p.name.split(" - ")[0]: p for p in _pastas_de_paciente(destino)}
    assert set(pastas) == {"FULANA DE TESTE SINTETICA", "CICRANO DE EXEMPLO TESTE"}
    fulana, cicrano = pastas["FULANA DE TESTE SINTETICA"], pastas["CICRANO DE EXEMPLO TESTE"]
    assert "CICRANO" not in _texto_md(fulana) and "TOMOGRAFIA" not in _texto_md(fulana)
    assert "FULANA" not in _texto_md(cicrano) and "HEMOGRAMA" not in _texto_md(cicrano)
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "mix.pdf").is_file()
    assert not (fulana / "ORIGINAIS" / "mix.pdf").exists()
    assert (fulana / "ORIGINAIS" / "mix (paginas deste paciente).pdf").is_file()
    assert (cicrano / "ORIGINAIS" / "mix (paginas deste paciente).pdf").is_file()


def test_arquivo_ilegivel_vai_para_desconhecido_com_leia_me(ambiente):
    origem, destino, _leitor, ctx = ambiente
    arquivo = origem / "ruim.pdf"
    arquivo.write_bytes(b"%PDF-1.7 quebrado")
    processar(arquivo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert pasta.name == "DESCONHECIDO - 2026-10-25 - RUIM"
    assert "arquivo corrompido" in (pasta / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert (pasta / "ORIGINAIS" / "ruim.pdf").read_bytes() == arquivo.read_bytes()


def test_mesmo_arquivo_de_novo_nao_e_refeito(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    processar(arquivo, ctx)
    antes = _arquivos(destino)
    assert processar(arquivo, ctx)["situacao"] == "ja_feito"
    assert _arquivos(destino) == antes


def test_origem_fica_intocada(ambiente):
    origem, _destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 2)
    leitor.registrar(arquivo, [_hemograma(), _tomografia()])
    antes = _arquivos(origem)
    processar(arquivo, ctx)
    assert _arquivos(origem) == antes


def test_segundo_exame_do_mesmo_paciente_entra_na_mesma_pasta(ambiente):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma()])
    processar(primeiro, ctx)
    segundo = _pdf(origem / "scan002.pdf", 1, cor="ivory")
    leitor.registrar(segundo, [_hemograma(hb="11,9", coleta="02/06/2026")])
    processar(segundo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert len(list((pasta / "3 LABORATORIO").glob("*.md"))) == 2
    completo = (pasta / "COMPLETO.md").read_text(encoding="utf-8")
    assert "12,4" in completo and "11,9" in completo
    assert "pendentes" not in _manifesto(pasta)


def test_pagina_sem_nome_sozinha_vai_para_desconhecido_com_md(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan003.pdf", 1)
    leitor.registrar(arquivo, [(_linha("ANOTAÇÃO", 0.1, tipo="secao"),
                                _linha("Retorno em 30 dias com exames.", 0.2))])
    processar(arquivo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert pasta.name.startswith("DESCONHECIDO - 2026-10-25")
    assert "Retorno em 30 dias" in _texto_md(pasta)


def test_copia_xerocada_vira_uma_transcricao_so(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "xerox.pdf", 2)
    leitor.registrar(arquivo, [_hemograma(), _hemograma()])
    processar(arquivo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert len(list((pasta / "3 LABORATORIO").glob("*.md"))) == 1
    manifesto = json.loads((pasta / "_scan-to-text" / "manifesto.json").read_text(encoding="utf-8"))
    [entrada] = manifesto["documentos"]
    assert entrada["duplicatas"] and entrada["duplicatas"][0]["caminho"] == "xerox.pdf"


def _manifesto(pasta: Path) -> dict:
    return json.loads((pasta / "_scan-to-text" / "manifesto.json").read_text(encoding="utf-8"))


def test_pagina_em_branco_fica_registrada_no_manifesto(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 2)
    leitor.registrar(arquivo, [_hemograma(), None])
    processar(arquivo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    [entrada] = _manifesto(pasta)["documentos"]
    assert entrada["paginas_em_branco"] == [1]
    assert entrada["origens"][0]["paginas"] == [0]


def test_em_branco_de_arquivo_so_com_repetidos_nao_se_perde(ambiente):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma()])
    processar(primeiro, ctx)
    segundo = _pdf(origem / "scan002.pdf", 2, cor="ivory")
    leitor.registrar(segundo, [_hemograma(), None])
    processar(segundo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    brancos = _manifesto(pasta)["paginas_em_branco"]
    assert [(b["caminho"], b["paginas"]) for b in brancos] == [("scan002.pdf", [1])]


def test_arquivo_so_com_paginas_em_branco_vai_para_desconhecido(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "verso.pdf", 2)
    leitor.registrar(arquivo, [None, None])
    assert processar(arquivo, ctx)["situacao"] == "feito"
    [pasta] = _pastas_de_paciente(destino)
    assert pasta.name.startswith("DESCONHECIDO - 2026-10-25")
    assert "em branco" in (pasta / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert (pasta / "ORIGINAIS" / "verso.pdf").read_bytes() == arquivo.read_bytes()
    assert [b["paginas"] for b in _manifesto(pasta)["paginas_em_branco"]] == [[0, 1]]


def test_nenhum_parcial_sobra(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "mix.pdf", 2)
    leitor.registrar(arquivo, [_hemograma(), _tomografia()])
    processar(arquivo, ctx)
    assert not [p for p in destino.rglob("*") if ".parcial" in p.name]
    assert not list((destino / "_scan-to-text" / "travas").glob("*")) if (destino / "_scan-to-text" / "travas").exists() else True


def test_trava_perdida_durante_a_leitura_nao_grava_nem_marca_feito(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    ler = leitor.ler_arquivo

    def ler_e_perder_a_trava(caminho, sha256):
        [trava] = (destino / "_scan-to-text" / "travas").glob("*.lock")
        dado = json.loads(trava.read_text(encoding="utf-8"))
        trava.write_text(json.dumps(dict(dado, estacao="OUTRA", token="0" * 32)), encoding="utf-8")
        return ler(caminho, sha256)

    leitor.ler_arquivo = ler_e_perder_a_trava
    resumo = processar(arquivo, ctx)
    assert resumo["situacao"] == "trava_perdida"
    assert _pastas_de_paciente(destino) == []
    from scan_to_text import registro
    assert not registro.ja_feito(destino, resumo["sha256"])


def test_arquivo_que_derrubou_a_leitura_vai_sem_leitura_para_desconhecido(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "pesado.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    assert processar(arquivo, ctx, desistir=True)["situacao"] == "feito"
    [pasta] = _pastas_de_paciente(destino)
    assert pasta.name.startswith("DESCONHECIDO - 2026-10-25")
    assert "não foi possível ler" in (pasta / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert (pasta / "ORIGINAIS" / "pesado.pdf").read_bytes() == arquivo.read_bytes()


def test_trabalhando_json_existe_so_enquanto_grava(ambiente, monkeypatch):
    from scan_to_text.escrever import markdown
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    vistos = []
    original = markdown.md_documento

    def espiar(doc, *args, **kw):
        vistos.extend(destino.rglob("trabalhando.json"))
        return original(doc, *args, **kw)

    monkeypatch.setattr(markdown, "md_documento", espiar)
    processar(arquivo, ctx)
    [sinal] = vistos
    assert sinal.parent.name == "_scan-to-text" and sinal.parent.parent.name.startswith("FULANA")
    assert not list(destino.rglob("trabalhando.json"))


def test_resumo_conta_documentos_sem_paciente(ambiente):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan003.pdf", 1)
    leitor.registrar(arquivo, [(_linha("ANOTAÇÃO", 0.1, tipo="secao"),
                                _linha("Retorno em 30 dias com exames.", 0.2))])
    resumo = processar(arquivo, ctx)
    assert resumo["desconhecidos"] == 1 and resumo["documentos"] == 1


# --- arquivo com mais de um paciente: decidido no arquivo de origem inteiro ------

def _zip(caminho: Path, membros: dict[str, Path]) -> Path:
    with zipfile.ZipFile(caminho, "w") as arquivo:
        for nome, fonte in membros.items():
            arquivo.writestr(zipfile.ZipInfo(nome, date_time=(2026, 1, 1, 0, 0, 0)),
                             fonte.read_bytes())
    return caminho


def _ruim(caminho: Path) -> Path:
    caminho.write_bytes(b"%PDF-1.7 quebrado")
    return caminho


def _montagem(origem: Path) -> Path:
    pasta = origem.parent / "montar"
    pasta.mkdir(exist_ok=True)
    return pasta


def test_zip_com_um_pdf_de_cada_paciente_nao_leva_o_zip_a_nenhum_dos_dois(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_tomografia()])
    lote = _zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b})
    processar(lote, ctx)
    pastas = {p.name.split(" - ")[0]: p for p in _pastas_de_paciente(destino)}
    assert set(pastas) == {"FULANA DE TESTE SINTETICA", "CICRANO DE EXEMPLO TESTE"}
    mistos = destino / "_ARQUIVOS COM MAIS DE UM PACIENTE"
    assert (mistos / "lote.zip").read_bytes() == lote.read_bytes()
    for nome, proprio in (("FULANA DE TESTE SINTETICA", a), ("CICRANO DE EXEMPLO TESTE", b)):
        originais = list((pastas[nome] / "ORIGINAIS").iterdir())
        assert [p.read_bytes() for p in originais] == [proprio.read_bytes()]
        assert _manifesto(pastas[nome])["originais"] == [originais[0].name]


def test_zip_com_paciente_e_item_ilegivel_separa_os_dois(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, ruim = _pdf(montar / "a.pdf", 1), _ruim(montar / "ruim.pdf")
    leitor.registrar(a, [_hemograma()])
    lote = _zip(origem / "lote.zip", {"a.pdf": a, "ruim.pdf": ruim})
    processar(lote, ctx)
    pastas = {p.name.split(" - ")[0]: p for p in _pastas_de_paciente(destino)}
    assert set(pastas) == {"FULANA DE TESTE SINTETICA", "DESCONHECIDO"}
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()
    fulana = pastas["FULANA DE TESTE SINTETICA"]
    assert [p.read_bytes() for p in (fulana / "ORIGINAIS").iterdir()] == [a.read_bytes()]
    desconhecido = pastas["DESCONHECIDO"]
    assert [p.read_bytes() for p in (desconhecido / "ORIGINAIS").iterdir()] == [ruim.read_bytes()]
    assert (desconhecido / "LEIA-ME.txt").is_file()


def test_zip_de_um_paciente_so_vai_inteiro_para_originais(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(hb="11,9", coleta="02/06/2026")])
    lote = _zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b})
    processar(lote, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert [p.name for p in (pasta / "ORIGINAIS").iterdir()] == ["lote.zip"]
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


def test_zip_com_homonimos_de_nascimentos_diferentes_e_misto(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="15/08/1975")])
    lote = _zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b})
    processar(lote, ctx)
    pastas = _pastas_de_paciente(destino)
    assert len(pastas) == 2
    for pasta in pastas:
        assert [p.suffix for p in (pasta / "ORIGINAIS").iterdir()] == [".pdf"]
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def _sem_nascimento(hb: str, coleta: str):
    paginas = list(_hemograma(hb=hb, coleta=coleta))
    paginas[3] = _linha("Convênio: PARTICULAR", 0.115)
    return tuple(paginas)


def _zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro: bool) -> Path:
    montar = _montagem(origem)
    com, sem = _pdf(montar / "com.pdf", 1), _pdf(montar / "sem.pdf", 1, cor="ivory")
    leitor.registrar(com, [_hemograma()])
    leitor.registrar(sem, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    membros = {"a.pdf": sem, "b.pdf": com} if sem_nascimento_primeiro else {"a.pdf": com,
                                                                            "b.pdf": sem}
    return _zip(origem / "lote.zip", membros)


@pytest.mark.parametrize("sem_nascimento_primeiro", [True, False], ids=["sem_antes", "sem_depois"])
def test_zip_com_item_sem_nascimento_fica_com_o_nascimento_lido_no_outro_item(
        ambiente, sem_nascimento_primeiro):
    origem, destino, leitor, ctx = ambiente
    processar(_zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro), ctx)
    [de_1960] = _pastas_de_paciente(destino)
    assert _manifesto(de_1960)["paciente"]["nascimento"] == "1960-05-01"
    assert "12,4" in _texto_md(de_1960) and "10,1" in _texto_md(de_1960)
    assert [p.name for p in (de_1960 / "ORIGINAIS").iterdir()] == ["lote.zip"]
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


@pytest.mark.parametrize("sem_nascimento_primeiro", [True, False], ids=["sem_antes", "sem_depois"])
@pytest.mark.parametrize("no_destino", ["1975", "1960_e_1975"])
def test_zip_com_item_sem_nascimento_e_homonima_no_destino_nao_herda_o_nascimento(
        ambiente, sem_nascimento_primeiro, no_destino):
    origem, destino, leitor, ctx = ambiente
    de_60, de_75 = _homonimas_no_destino(origem, leitor)
    for arquivo in ([de_75] if no_destino == "1975" else [de_60, de_75]):
        processar(arquivo, ctx)
    de_1975 = next(p for p in _pastas_de_paciente(destino) if "8,8" in _texto_md(p))
    intocada = _arquivos(de_1975)
    processar(_zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro), ctx)
    assert _arquivos(de_1975) == intocada
    [com_o_sem] = [p for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    assert _manifesto(com_o_sem)["paciente"]["nascimento"] is None
    item_sem = "A.pdf" if sem_nascimento_primeiro else "B.pdf"
    assert [p.name for p in (com_o_sem / "ORIGINAIS").iterdir()] == [item_sem]
    assert "12,4" not in _texto_md(com_o_sem)
    [de_1960] = [p for p in _pastas_de_paciente(destino) if "12,4" in _texto_md(p)]
    assert _manifesto(de_1960)["paciente"]["nascimento"] == "1960-05-01"
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def test_zip_de_um_paciente_lido_com_e_sem_acento_nao_e_misto(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(nome="FULANA DE TESTE SINTETICA", hb="11,9",
                                    coleta="02/06/2026")])
    processar(_zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b}), ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert [p.name for p in (pasta / "ORIGINAIS").iterdir()] == ["lote.zip"]
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


def test_zip_com_variante_do_nome_que_cai_na_mesma_pasta_nao_e_misto(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(nome="FULANA TESTE SINTÉTICA", hb="11,9",
                                    coleta="02/06/2026")])
    processar(_zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b}), ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert "12,4" in _texto_md(pasta) and "11,9" in _texto_md(pasta)
    assert [p.name for p in (pasta / "ORIGINAIS").iterdir()] == ["lote.zip"]
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


def test_zip_com_homonimos_e_item_sem_nascimento_nao_poe_o_sem_nascimento_com_nenhuma(ambiente):
    origem, destino, leitor, ctx = ambiente
    montar = _montagem(origem)
    sem = _pdf(montar / "sem.pdf", 1, cor="beige")
    de_60, de_75 = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(sem, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    leitor.registrar(de_60, [_hemograma()])
    leitor.registrar(de_75, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="15/08/1975")])
    lote = _zip(origem / "lote.zip", {"a.pdf": sem, "b.pdf": de_60, "c.pdf": de_75})
    processar(lote, ctx)
    por_pasta = {p.name: _texto_md(p) for p in _pastas_de_paciente(destino)}
    com_o_sem = [nome for nome, texto in por_pasta.items() if "10,1" in texto]
    assert len(com_o_sem) == 1 and com_o_sem[0].startswith("DESCONHECIDO")
    assert not any("12,4" in t and "9,1" in t for t in por_pasta.values())
    assert sum(nome.startswith("FULANA") for nome in por_pasta) == 2
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx):
    antiga = _pdf(origem / "receita.pdf", 1, cor="gray")
    leitor.registrar(antiga, [_sem_nascimento(hb="7,7", coleta="01/01/2026")])
    processar(antiga, ctx)
    [existente] = _pastas_de_paciente(destino)
    return existente, _arquivos(existente)


def _uma_por_pasta(destino: Path, existente: Path, hbs: dict[str, str]) -> None:
    """Cada hemoglobina numa pasta nova só dela, com o nascimento do documento."""
    novas = [p for p in _pastas_de_paciente(destino) if p.name.startswith("FULANA")
             and p != existente]
    assert len(novas) == len(hbs)
    for pasta in novas:
        [hb] = [hb for hb in hbs if hb in _texto_md(pasta)]
        assert _manifesto(pasta)["paciente"]["nascimento"] == hbs[hb]


@pytest.mark.parametrize("de_1960_primeiro", [True, False], ids=["1960_antes", "1975_antes"])
def test_zip_com_homonimas_nao_adota_a_pasta_sem_nascimento_do_destino(ambiente,
                                                                      de_1960_primeiro):
    origem, destino, leitor, ctx = ambiente
    existente, antes = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    montar = _montagem(origem)
    de_60, de_75 = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(de_60, [_hemograma()])
    leitor.registrar(de_75, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="15/08/1975")])
    membros = {"a.pdf": de_60, "b.pdf": de_75} if de_1960_primeiro else {"a.pdf": de_75,
                                                                        "b.pdf": de_60}
    processar(_zip(origem / "lote.zip", membros), ctx)
    assert _arquivos(existente) == antes
    _uma_por_pasta(destino, existente, {"12,4": "1960-05-01", "9,1": "1975-08-15"})
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def test_zip_com_tres_homonimas_e_item_sem_nascimento_nao_adota_a_pasta_sem_nascimento(
        ambiente):
    origem, destino, leitor, ctx = ambiente
    existente, antes = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    c, s = _pdf(montar / "c.pdf", 1, cor="khaki"), _pdf(montar / "s.pdf", 1, cor="beige")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="15/08/1975")])
    leitor.registrar(c, [_hemograma(hb="6,6", coleta="04/04/2026", nascimento="20/12/1980")])
    leitor.registrar(s, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    processar(_zip(origem / "lote.zip", {"s.pdf": s, "c.pdf": c, "a.pdf": a, "b.pdf": b}), ctx)
    assert _arquivos(existente) == antes
    _uma_por_pasta(destino, existente,
                   {"12,4": "1960-05-01", "9,1": "1975-08-15", "6,6": "1980-12-20"})
    [com_o_sem] = [p.name for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    assert com_o_sem.startswith("DESCONHECIDO")
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def test_zip_com_homonimas_no_mesmo_item_nao_adota_a_pasta_sem_nascimento(ambiente):
    origem, destino, leitor, ctx = ambiente
    existente, antes = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    montar = _montagem(origem)
    so_60, duas = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 2, cor="ivory")
    leitor.registrar(so_60, [_hemograma(hb="11,1", coleta="02/06/2026")])
    leitor.registrar(duas, [_hemograma(), _hemograma(hb="9,1", coleta="05/09/2026",
                                                     nascimento="15/08/1975")])
    processar(_zip(origem / "lote.zip", {"a.pdf": so_60, "b.pdf": duas}), ctx)
    assert _arquivos(existente) == antes
    _uma_por_pasta(destino, existente, {"11,1": "1960-05-01"})


def test_zip_com_item_sem_nascimento_e_item_com_as_duas_homonimas_vai_para_desconhecido(
        ambiente):
    origem, destino, leitor, ctx = ambiente
    homonima = _pdf(origem / "h75.pdf", 1, cor="gray")
    leitor.registrar(homonima, [_hemograma(hb="8,8", coleta="01/02/2026", nascimento="15/08/1975")])
    processar(homonima, ctx)
    [de_1975] = _pastas_de_paciente(destino)
    antes = _arquivos(de_1975)
    montar = _montagem(origem)
    sem, duas = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 2, cor="ivory")
    leitor.registrar(sem, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    leitor.registrar(duas, [_hemograma(), _hemograma(hb="9,1", coleta="05/09/2026",
                                                     nascimento="15/08/1975")])
    processar(_zip(origem / "lote.zip", {"a.pdf": sem, "b.pdf": duas}), ctx)
    assert _arquivos(de_1975) == antes
    com_o_sem = [p.name for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    assert len(com_o_sem) == 1 and com_o_sem[0].startswith("DESCONHECIDO")


def test_zip_com_item_sem_nascimento_e_documento_com_dois_nascimentos_vai_para_desconhecido(
        ambiente):
    origem, destino, leitor, ctx = ambiente
    homonima = _pdf(origem / "h75.pdf", 1, cor="gray")
    leitor.registrar(homonima, [_hemograma(hb="8,8", coleta="01/02/2026", nascimento="15/08/1975")])
    processar(homonima, ctx)
    [de_1975] = _pastas_de_paciente(destino)
    antes = _arquivos(de_1975)
    montar = _montagem(origem)
    sem, conflito = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 2, cor="ivory")
    primeira, segunda = list(_hemograma()), list(_hemograma(hb="9,1", nascimento="15/08/1975"))
    primeira[-1] = _linha("Página 1 de 2", 0.95, "rodape")
    segunda[-1] = _linha("Página 2 de 2", 0.95, "rodape")
    leitor.registrar(sem, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    leitor.registrar(conflito, [tuple(primeira), tuple(segunda)])
    processar(_zip(origem / "lote.zip", {"a.pdf": sem, "b.pdf": conflito}), ctx)
    assert _arquivos(de_1975) == antes
    com_o_sem = [p.name for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    assert len(com_o_sem) == 1 and com_o_sem[0].startswith("DESCONHECIDO")


# --- pasta nova nasce com o manifesto ------------------------------------------

def test_pasta_nova_tem_manifesto_com_nascimento_antes_do_primeiro_md(ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    gravar = atomico.gravar
    vistos = []

    def espiar(alvo, dados, *a, **kw):
        alvo = Path(alvo)
        if alvo.suffix == ".md":
            pasta = alvo.parent if alvo.parent.parent == destino else alvo.parent.parent
            vistos.append((registro.ler_manifesto(pasta) or {}).get("paciente"))
        return gravar(alvo, dados, *a, **kw)

    monkeypatch.setattr(atomico, "gravar", espiar)
    processar(arquivo, ctx)
    assert vistos and all(p and p["nascimento"] == "1960-05-01" for p in vistos)


def test_escrita_interrompida_nao_abre_a_pasta_para_homonimo(ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma()])
    gravar_manifesto = registro.gravar_manifesto

    def falha_no_manifesto_final(pasta, m, *a, **kw):
        if m.get("documentos"):
            raise PermissionError(13, "negado")
        return gravar_manifesto(pasta, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", falha_no_manifesto_final)
    for _ in range(2):
        with pytest.raises(PermissionError):
            processar(primeiro, ctx)
    monkeypatch.undo()
    assert processar(primeiro, ctx, desistir=True)["situacao"] == "feito"
    homonima = _pdf(origem / "scan009.pdf", 1, cor="ivory")
    leitor.registrar(homonima, [_hemograma(hb="9,1", coleta="05/09/2026",
                                           nascimento="15/08/1975")])
    processar(homonima, ctx)
    for pasta in _pastas_de_paciente(destino):
        texto = _texto_md(pasta)
        assert not ("12,4" in texto and "9,1" in texto), pasta.name


def _fora_dos_manifestos(destino: Path) -> list[str]:
    """JSON, MD e conferência de documento que o manifesto da pasta não registra."""
    soltos = []
    for pasta in _pastas_de_paciente(destino):
        entradas = (registro.ler_manifesto(pasta) or {}).get("documentos", [])
        ids = {e["id"] for e in entradas if isinstance(e["id"], str)}
        usados = {e[campo] for e in entradas for campo in ("md", "conferencia")
                  if isinstance(e[campo], str)}
        for arquivo in (pasta / "_scan-to-text" / "documentos").glob("*.json"):
            if arquivo.stem not in ids:
                soltos.append(f"{pasta.name}/{arquivo.name}")
        for arquivo in pasta.rglob("*"):
            relativo = arquivo.relative_to(pasta)
            if (arquivo.is_file() and len(relativo.parts) == 2
                    and relativo.parts[0] not in ("ORIGINAIS", "_scan-to-text")
                    and relativo.as_posix() not in usados):
                soltos.append(f"{pasta.name}/{relativo.as_posix()}")
    return soltos


def _registrados(pasta: Path) -> dict[str, bytes]:
    """Os arquivos que o manifesto da pasta registra, com o conteúdo."""
    m = _manifesto(pasta)
    nomes = [f"_scan-to-text/documentos/{e['id']}.json" for e in m["documentos"]]
    nomes += [e[campo] for e in m["documentos"] for campo in ("md", "conferencia") if e[campo]]
    nomes += [f"ORIGINAIS/{nome}" for nome in m["originais"]]
    return {nome: (pasta / nome).read_bytes() for nome in nomes}


def _falha_na_escrita(monkeypatch, onde: str, caminho: str) -> None:
    """OSError na cópia da conferência, ou no manifesto que registraria `caminho`."""
    if onde == "conferencia":
        copiar = atomico.copiar

        def falha_na_conferencia(fonte, alvo, *a, **kw):
            if Path(alvo).parent.name != "ORIGINAIS" and Path(alvo).suffix == ".pdf":
                raise OSError(errno.EIO, "falha_injetada")
            return copiar(fonte, alvo, *a, **kw)

        monkeypatch.setattr(atomico, "copiar", falha_na_conferencia)
        return
    gravar_manifesto = registro.gravar_manifesto

    def falha_no_manifesto(pasta, m, *a, **kw):
        if any(o["caminho"] == caminho for e in m["documentos"] for o in e["origens"]):
            raise OSError(errno.EIO, "falha_injetada")
        return gravar_manifesto(pasta, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", falha_no_manifesto)


@pytest.mark.parametrize("onde", ["conferencia", "manifesto_final"])
@pytest.mark.parametrize("pasta", ["existente", "nova"])
def test_desistir_depois_de_falhas_na_escrita_nao_deixa_documento_fora_do_manifesto(
        ambiente, monkeypatch, onde, pasta):
    origem, destino, leitor, ctx = ambiente
    entregue = {}
    if pasta == "existente":
        existente, _ = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
        entregue = _registrados(existente)
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    with monkeypatch.context() as m:
        _falha_na_escrita(m, onde, "a.pdf")
        for _ in range(2):
            with pytest.raises(OSError):
                processar(arquivo, ctx)
    assert processar(arquivo, ctx, desistir=True)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    assert not [p.name for p in _pastas_de_paciente(destino) if "12,4" in _texto_md(p)]
    if pasta == "existente":
        assert {k: v for k, v in _arquivos(existente).items() if k in entregue} == entregue
    [desconhecido] = [p for p in _pastas_de_paciente(destino) if p.name.startswith("DESCONHECIDO")]
    assert (desconhecido / "ORIGINAIS" / "a.pdf").read_bytes() == arquivo.read_bytes()


def test_nova_tentativa_que_muda_a_pasta_do_documento_nao_o_deixa_na_pasta_antiga(ambiente,
                                                                                  monkeypatch):
    # O sem nascimento herdou o 1960 e falhou depois do MD; chega a homônima de
    # 1975, e na nova tentativa ele já não herda.
    origem, destino, leitor, ctx = ambiente
    lote = _zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro=False)
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "lote.zip/b.pdf")
        with pytest.raises(OSError):
            processar(lote, ctx)
    de_75 = _pdf(origem / "h75.pdf", 1, cor="gray")
    leitor.registrar(de_75, [_hemograma(hb="8,8", coleta="01/02/2026", nascimento="15/08/1975")])
    processar(de_75, ctx)
    assert processar(lote, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    [com_o_sem] = [p for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    assert _manifesto(com_o_sem)["paciente"]["nascimento"] is None
    assert "12,4" not in _texto_md(com_o_sem) and "8,8" not in _texto_md(com_o_sem)


def _falha_no_feito(monkeypatch) -> None:
    def falha(*_a, **_kw):
        raise OSError(errno.EIO, "falha_injetada")

    monkeypatch.setattr(registro, "marcar_feito", falha)


@pytest.mark.parametrize("formato", ["zip", "pdf"])
def test_nova_tentativa_deixa_o_documento_ja_registrado_na_pasta_que_o_registra(
        ambiente, monkeypatch, formato):
    # Tudo foi registrado e a falha é no fim; a homônima de 1975 chega antes da
    # nova tentativa, que já não entrega o sem nascimento noutra pasta.
    origem, destino, leitor, ctx = ambiente
    de_60, de_75 = _homonimas_no_destino(origem, leitor)
    if formato == "zip":
        alvo = _zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro=False)
    else:
        processar(de_60, ctx)
        alvo = _pdf(origem / "sem.pdf", 1, cor="ivory")
        leitor.registrar(alvo, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    with monkeypatch.context() as m:
        _falha_no_feito(m)
        with pytest.raises(OSError):
            processar(alvo, ctx)
    [de_1960] = _pastas_de_paciente(destino)
    antes = _arquivos(de_1960)
    assert "10,1" in _texto_md(de_1960)
    processar(de_75, ctx)
    assert processar(alvo, ctx)["situacao"] == "feito"
    assert _arquivos(de_1960) == antes
    assert [p for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)] == [de_1960]
    assert len(_pastas_de_paciente(destino)) == 2
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


def _lote_que_falha_antes_da_homonima(raiz: Path, monkeypatch, vez: int):
    """ZIP com FULANA 1960 e um item sem nascimento, destino vazio: falha na gravação
    `vez` (0: não falha), a homônima de 1975 chega, e o ZIP é refeito."""
    origem, destino, leitor, ctx = _montar(raiz)
    lote = _zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro=False)
    _, de_75 = _homonimas_no_destino(origem, leitor)
    with monkeypatch.context() as m:
        falha = _injetar(m, vez)
        if vez:
            with pytest.raises(OSError):
                processar(lote, ctx)
        else:
            assert processar(lote, ctx)["situacao"] == "feito"
    assert processar(de_75, ctx)["situacao"] == "feito"
    processar(lote, ctx)
    return destino, falha.chamadas


def test_homonima_que_chega_entre_as_tentativas_nao_faz_entregar_duas_vezes(tmp_path,
                                                                           monkeypatch):
    monkeypatch.setattr(atomico.os, "fsync", lambda _descritor: None)
    _, passos = _lote_que_falha_antes_da_homonima(tmp_path / "limpo", monkeypatch, 0)
    assert passos > 3
    erradas = []
    for vez in range(1, passos + 1):
        destino, _ = _lote_que_falha_antes_da_homonima(tmp_path / f"falha{vez}", monkeypatch,
                                                        vez)
        pastas_da_pagina: dict[tuple[str, int], list[str]] = {}
        for pasta in _pastas_de_paciente(destino):
            for pagina in _paginas_registradas(_manifesto(pasta)):
                pastas_da_pagina.setdefault(pagina, []).append(pasta.name)
        mds_do_sem = [p for p in destino.rglob("*.md")
                      if p.name != "COMPLETO.md" and "| 10,1 |" in p.read_text(encoding="utf-8")]
        duas = {p: v for p, v in pastas_da_pagina.items() if len(v) > 1}
        if duas or len(mds_do_sem) != 1 or _fora_dos_manifestos(destino):
            erradas.append((vez, duas, len(mds_do_sem), _fora_dos_manifestos(destino)))
    assert erradas == []


def test_nova_tentativa_noutra_pasta_tira_o_documento_da_pasta_que_ela_nao_usa(ambiente,
                                                                             monkeypatch):
    # O sem nascimento caiu na única FULANA do destino e falhou no manifesto;
    # com a homônima de 1975, a nova tentativa abre pasta própria e nem passa
    # pela de 1960.
    origem, destino, leitor, ctx = ambiente
    de_60, de_75 = _homonimas_no_destino(origem, leitor)
    processar(de_60, ctx)
    sem = _pdf(origem / "sem.pdf", 1, cor="ivory")
    leitor.registrar(sem, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "sem.pdf")
        with pytest.raises(OSError):
            processar(sem, ctx)
    processar(de_75, ctx)
    assert processar(sem, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    [de_1960] = [p for p in _pastas_de_paciente(destino) if "13,3" in _texto_md(p)]
    assert "10,1" not in _texto_md(de_1960)
    assert [p.name for p in _pastas_de_paciente(destino) if "pendentes" in _manifesto(p)] == []


def test_nova_tentativa_que_cai_logo_depois_de_tirar_o_documento_refaz_o_completo(
        ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    lote = _zip_com_e_sem_nascimento(origem, leitor, sem_nascimento_primeiro=False)
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "lote.zip/b.pdf")
        with pytest.raises(OSError):
            processar(lote, ctx)
    _, de_75 = _homonimas_no_destino(origem, leitor)
    processar(de_75, ctx)
    copiar = atomico.copiar

    def falha_nos_mistos(fonte, alvo, *a, **kw):
        if Path(alvo).parent.name == "_ARQUIVOS COM MAIS DE UM PACIENTE":
            raise OSError(errno.EIO, "falha_injetada")
        return copiar(fonte, alvo, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(atomico, "copiar", falha_nos_mistos)
        with pytest.raises(OSError):
            processar(lote, ctx)
    [de_1960] = [p for p in _pastas_de_paciente(destino) if "12,4" in _texto_md(p)]
    assert "10,1" not in _texto_md(de_1960)
    assert processar(lote, ctx, desistir=True)["situacao"] == "feito"
    assert "10,1" not in _texto_md(de_1960)
    assert _fora_dos_manifestos(destino) == []


def test_manifesto_estranho_noutra_pasta_nao_impede_a_escrita_nem_a_entrega(ambiente,
                                                                           monkeypatch):
    origem, destino, leitor, ctx = ambiente
    outro = _pdf(origem / "t.pdf", 1, cor="gray")
    leitor.registrar(outro, [_tomografia()])
    processar(outro, ctx)
    [alheia] = _pastas_de_paciente(destino)
    arquivo_manifesto = alheia / "_scan-to-text" / "manifesto.json"
    estranho = json.loads(arquivo_manifesto.read_text(encoding="utf-8"))
    estranho["documentos"][0]["id"] = [estranho["documentos"][0]["id"]]
    estranho["documentos"][0]["md"] = {"md": estranho["documentos"][0]["md"]}
    arquivo_manifesto.write_text(json.dumps(estranho), encoding="utf-8")
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "conferencia", "a.pdf")
        for _ in range(2):
            with pytest.raises(OSError):
                processar(arquivo, ctx)
    assert processar(arquivo, ctx, desistir=True)["situacao"] == "feito"
    assert [s for s in _fora_dos_manifestos(destino) if not s.startswith(alheia.name)] == []
    [desconhecido] = [p for p in _pastas_de_paciente(destino) if p.name.startswith("DESCONHECIDO")]
    assert (desconhecido / "ORIGINAIS" / "a.pdf").read_bytes() == arquivo.read_bytes()


def test_conferencia_presa_nao_deixa_o_documento_meio_apagado(ambiente, monkeypatch):
    # Um visualizador segura a conferência aberta: o MD e o JSON ficam com ela,
    # e o COMPLETO.md volta a ter só o que o manifesto registra.
    origem, destino, leitor, ctx = ambiente
    existente, _ = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "a.pdf")
        for _ in range(2):
            with pytest.raises(OSError):
                processar(arquivo, ctx)
    soltos = sorted(_fora_dos_manifestos(destino))
    assert len(soltos) == 3
    apagar = atomico._apagar

    def presa(caminho):
        if Path(caminho).suffix == ".pdf" and Path(caminho).parent.name != "ORIGINAIS":
            raise PermissionError(errno.EACCES, "aberto_noutro_programa")
        return apagar(caminho)

    with monkeypatch.context() as m:
        m.setattr(atomico, "_apagar", presa)
        assert processar(arquivo, ctx, desistir=True)["situacao"] == "feito"
    assert sorted(_fora_dos_manifestos(destino)) == soltos
    assert "12,4" not in (existente / "COMPLETO.md").read_text(encoding="utf-8")
    # O visualizador fecha: a passada seguinte no destino, de outro arquivo,
    # termina a limpeza que a conferência presa deixou.
    outro = _pdf(origem / "t.pdf", 1, cor="khaki")
    leitor.registrar(outro, [_tomografia()])
    assert processar(outro, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    assert "12,4" not in _texto_md(existente)
    assert "pendentes" not in _manifesto(existente)


@pytest.mark.parametrize("pasta", ["existente", "nova"])
def test_conferencia_presa_do_2o_item_na_mesma_pasta_sai_na_passada_seguinte(ambiente,
                                                                             monkeypatch, pasta):
    # Os dois itens do ZIP vão para a mesma pasta: o registro do 1o não tira a
    # marca enquanto o 2o ainda grava ali.
    origem, destino, leitor, ctx = ambiente
    if pasta == "existente":
        de_60, _ = _homonimas_no_destino(origem, leitor)
        processar(de_60, ctx)
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(hb="5,5", coleta="03/03/2026")])
    lote = _zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b})
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "lote.zip/b.pdf")
        for _ in range(2):
            with pytest.raises(OSError):
                processar(lote, ctx)
    apagar = atomico._apagar

    def presa(caminho):
        if Path(caminho).suffix == ".pdf" and Path(caminho).parent.name != "ORIGINAIS":
            raise PermissionError(errno.EACCES, "aberto_noutro_programa")
        return apagar(caminho)

    with monkeypatch.context() as m:
        m.setattr(atomico, "_apagar", presa)
        assert processar(lote, ctx, desistir=True)["situacao"] == "feito"
    assert len(_fora_dos_manifestos(destino)) == 3
    outro = _pdf(origem / "t.pdf", 1, cor="khaki")
    leitor.registrar(outro, [_tomografia()])
    assert processar(outro, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    assert [p.name for p in _pastas_de_paciente(destino) if "pendentes" in _manifesto(p)] == []


def test_tentativa_que_falha_nao_carimba_o_nascimento_na_pasta_existente(ambiente, monkeypatch):
    # A pasta sem nascimento só ganha o nascimento junto com o documento que o
    # traz; depois do desistir, fica como estava.
    origem, destino, leitor, ctx = ambiente
    existente, entregue = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "conferencia", "a.pdf")
        with pytest.raises(OSError):
            processar(arquivo, ctx)
        assert _manifesto(existente)["paciente"]["nascimento"] is None
        with pytest.raises(OSError):
            processar(arquivo, ctx)
    assert processar(arquivo, ctx, desistir=True)["situacao"] == "feito"
    assert _arquivos(existente) == entregue


def test_zip_com_variante_adota_a_pasta_sem_nascimento_uma_vez_so(ambiente):
    # O nascimento que a 1a unidade dá à pasta sem nascimento vale para a
    # escolha da pasta da 2a, antes de ir ao disco.
    origem, destino, leitor, ctx = ambiente
    existente, _ = _receita_sem_nascimento_no_destino(origem, destino, leitor, ctx)
    montar = _montagem(origem)
    a, b = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(a, [_hemograma()])
    leitor.registrar(b, [_hemograma(nome="FULANA TESTE SINTÉTICA", hb="11,1",
                                    coleta="02/06/2026")])
    assert processar(_zip(origem / "lote.zip", {"a.pdf": a, "b.pdf": b}), ctx)["situacao"] == "feito"
    assert _pastas_de_paciente(destino) == [existente]
    assert _manifesto(existente)["paciente"]["nascimento"] == "1960-05-01"
    assert "12,4" in _texto_md(existente) and "11,1" in _texto_md(existente)
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()
    assert "pendentes" not in _manifesto(existente)


@pytest.mark.parametrize("com_nascimento", [False, True], ids=["sem_nascimento", "com_nascimento"])
def test_nova_tentativa_que_le_outro_nome_nao_segue_a_pasta_da_leitura_anterior(
        ambiente, monkeypatch, com_nascimento):
    # A 1a tentativa leu FULANA, registrou tudo e caiu no fim; a nova lê outro
    # nome na mesma página (outra versão do app, OCR que oscila).
    origem, destino, leitor, ctx = ambiente
    de_60, _ = _homonimas_no_destino(origem, leitor)
    processar(de_60, ctx)
    [de_fulana] = _pastas_de_paciente(destino)
    alvo = _pdf(origem / "x.pdf", 1, cor="ivory")
    pagina = (_hemograma(hb="10,1", coleta="03/03/2026") if com_nascimento
              else _sem_nascimento(hb="10,1", coleta="03/03/2026"))
    leitor.registrar(alvo, [pagina])
    with monkeypatch.context() as m:
        _falha_no_feito(m)
        with pytest.raises(OSError):
            processar(alvo, ctx)
    assert "10,1" in _texto_md(de_fulana)
    antes = _arquivos(de_fulana)
    outra_leitura = list(pagina)
    outra_leitura[2] = _linha("Paciente: CICRANO DE EXEMPLO TESTE", 0.10)
    leitor.registrar(alvo, [tuple(outra_leitura)])
    assert processar(alvo, ctx)["situacao"] == "feito"
    assert _arquivos(de_fulana) == antes
    [de_cicrano] = [p for p in _pastas_de_paciente(destino) if p.name.startswith("CICRANO")]
    assert "10,1" in _texto_md(de_cicrano)
    if com_nascimento:
        depois = _pdf(origem / "c60.pdf", 1, cor="khaki")
        leitor.registrar(depois, [_hemograma(nome="CICRANO DE EXEMPLO TESTE", hb="6,6",
                                             coleta="09/09/2026")])
        assert processar(depois, ctx)["situacao"] == "feito"
        assert "6,6" in _texto_md(de_cicrano) and "6,6" not in _texto_md(de_fulana)


def test_nova_tentativa_com_a_variante_fica_na_pasta_que_a_registra_quando_chega_a_homonima_dela(
        ambiente, monkeypatch):
    # A leitura não muda entre as tentativas: o item sem nascimento lido com a
    # variante herdou o 1960 e entrou na pasta da FULANA pela variante; depois
    # chega a homônima da variante, de 1975.
    origem, destino, leitor, ctx = ambiente
    de_60, _ = _homonimas_no_destino(origem, leitor)
    processar(de_60, ctx)
    [de_1960] = _pastas_de_paciente(destino)
    variante = "FULANA TESTE SINTÉTICA"
    montar = _montagem(origem)
    x, y = _pdf(montar / "x.pdf", 1), _pdf(montar / "y.pdf", 1, cor="ivory")
    sem = list(_sem_nascimento(hb="10,1", coleta="03/03/2026"))
    sem[2] = _linha(f"Paciente: {variante}", 0.10)
    leitor.registrar(x, [_hemograma(nome=variante)])
    leitor.registrar(y, [tuple(sem)])
    lote = _zip(origem / "lote.zip", {"x.pdf": x, "y.pdf": y})
    with monkeypatch.context() as m:
        _falha_no_feito(m)
        with pytest.raises(OSError):
            processar(lote, ctx)
    assert "| 10,1 |" in _texto_md(de_1960)
    antes = _arquivos(de_1960)
    de_75 = _pdf(origem / "v75.pdf", 1, cor="khaki")
    leitor.registrar(de_75, [_hemograma(nome=variante, hb="8,8", coleta="01/02/2026",
                                        nascimento="15/08/1975")])
    assert processar(de_75, ctx)["situacao"] == "feito"
    assert processar(lote, ctx)["situacao"] == "feito"
    assert _arquivos(de_1960) == antes
    assert [p for p in _pastas_de_paciente(destino) if "| 10,1 |" in _texto_md(p)] == [de_1960]
    assert len(_pastas_de_paciente(destino)) == 2
    assert not (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE").exists()


def test_item_sem_nascimento_que_nao_foi_entregue_nao_segue_a_pasta_do_item_registrado(
        ambiente, monkeypatch):
    # ZIP com dois itens sem nascimento do mesmo nome e só a FULANA 1960 no
    # destino: a 1a tentativa registra b na pasta dela e cai antes de c. Chega a
    # homônima de 1975: c, que nunca foi entregue, já não sabe de quem é.
    origem, destino, leitor, ctx = ambiente
    de_60, de_75 = _homonimas_no_destino(origem, leitor)
    processar(de_60, ctx)
    [de_1960] = _pastas_de_paciente(destino)
    montar = _montagem(origem)
    b, c = _pdf(montar / "b.pdf", 1, cor="ivory"), _pdf(montar / "c.pdf", 1, cor="khaki")
    leitor.registrar(b, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    leitor.registrar(c, [_sem_nascimento(hb="5,5", coleta="07/07/2026")])
    lote = _zip(origem / "lote.zip", {"b.pdf": b, "c.pdf": c})
    with monkeypatch.context() as m:
        _falha_na_escrita(m, "manifesto_final", "lote.zip/c.pdf")
        with pytest.raises(OSError):
            processar(lote, ctx)
    assert "| 10,1 |" in _texto_md(de_1960)
    processar(de_75, ctx)
    assert processar(lote, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    assert [p for p in _pastas_de_paciente(destino) if "| 10,1 |" in _texto_md(p)] == [de_1960]
    [com_c] = [p for p in _pastas_de_paciente(destino) if "| 5,5 |" in _texto_md(p)]
    assert com_c != de_1960 and _manifesto(com_c)["paciente"]["nascimento"] is None
    assert "8,8" not in _texto_md(com_c)
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def test_escrita_le_cada_manifesto_do_destino_uma_vez(ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    outro = _pdf(origem / "t.pdf", 1, cor="gray")
    leitor.registrar(outro, [_tomografia()])
    processar(outro, ctx)
    [modelo] = _pastas_de_paciente(destino)
    for i in range(20):
        nome = f"PACIENTE {i:02d} DE TESTE"
        copia = destino / f"{nome} - 2026-10-25"
        shutil.copytree(modelo, copia)
        m = _manifesto(copia)
        m.update(pasta=copia.name, paciente={**m["paciente"], "exibido": nome, "variantes": [nome]})
        (copia / "_scan-to-text" / "manifesto.json").write_text(json.dumps(m), encoding="utf-8")
    pastas = len(_pastas_de_paciente(destino))
    lidos, listados = [], []
    ler_manifesto, scandir = registro.ler_manifesto, os.scandir

    def contar_leitura(pasta):
        lidos.append(Path(pasta))
        return ler_manifesto(pasta)

    def contar_listagem(caminho="."):
        if Path(caminho).name == arvore.DOCUMENTOS:
            listados.append(Path(caminho))
        return scandir(caminho)

    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    monkeypatch.setattr(registro, "ler_manifesto", contar_leitura)
    monkeypatch.setattr(os, "scandir", contar_listagem)
    assert processar(arquivo, ctx)["situacao"] == "feito"
    monkeypatch.undo()
    assert len(lidos) <= pastas + 6, len(lidos)
    assert len(listados) <= 2, listados


# --- nova tentativa depois de falha no meio da escrita ----------------------------

class _Falha:
    """Levanta OSError na chamada número `vez` de gravação no destino; 0 só conta."""

    def __init__(self, vez: int):
        self.vez, self.chamadas = vez, 0

    def envolver(self, funcao):
        def chamada(*a, **kw):
            self.chamadas += 1
            if self.chamadas == self.vez:
                raise OSError(errno.EIO, "falha_injetada")
            return funcao(*a, **kw)
        return chamada


def _injetar(monkeypatch, vez: int) -> _Falha:
    falha = _Falha(vez)
    monkeypatch.setattr(atomico, "gravar", falha.envolver(atomico.gravar))
    monkeypatch.setattr(atomico, "copiar", falha.envolver(atomico.copiar))
    monkeypatch.setattr(registro, "gravar", falha.envolver(registro.gravar))
    return falha


def _cenario_misto(origem, leitor):
    # Um paciente, uma página sem nome (DESCONHECIDO) e uma em branco.
    alvo = _pdf(origem / "mix.pdf", 3)
    leitor.registrar(alvo, [_hemograma(), None,
                            (_linha("ANOTAÇÃO", 0.1, tipo="secao"),
                             _linha("Retorno em 30 dias com exames.", 0.2))])
    return [], alvo


def _cenario_ilegivel(origem, leitor):
    return [], _ruim(origem / "ruim.pdf")


def _cenario_zip_misto(origem, leitor):
    # Um paciente e um item ilegível: o ZIP é misto, cada pasta fica com o seu item.
    montar = _montagem(origem)
    a = _pdf(montar / "a.pdf", 1)
    leitor.registrar(a, [_hemograma()])
    return [], _zip(origem / "lote.zip", {"a.pdf": a, "c.pdf": _ruim(montar / "c.pdf")})


def _cenario_repetidos_na_pasta(origem, leitor):
    antes = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(antes, [_hemograma()])
    alvo = _pdf(origem / "scan002.pdf", 4, cor="ivory")
    leitor.registrar(alvo, [_hemograma(), _hemograma(),
                            _tomografia(nome="FULANA DE TESTE SINTÉTICA"), None])
    return [antes], alvo


def _homonimas_no_destino(origem, leitor):
    de_60 = _pdf(origem / "h60.pdf", 1, cor="beige")
    leitor.registrar(de_60, [_hemograma(hb="13,3", coleta="01/01/2026")])
    de_75 = _pdf(origem / "h75.pdf", 1, cor="gray")
    leitor.registrar(de_75, [_hemograma(hb="8,8", coleta="01/02/2026", nascimento="15/08/1975")])
    return [de_60, de_75]


def _cenario_sem_nascimento_entre_homonimas(origem, leitor):
    # O nome casa com as duas pastas e o documento não diz o nascimento: pasta nova.
    alvo = _pdf(origem / "sem.pdf", 1, cor="ivory")
    leitor.registrar(alvo, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    return _homonimas_no_destino(origem, leitor), alvo


def _cenario_zip_homonimas_e_pasta_sem_nascimento(origem, leitor):
    antiga = _pdf(origem / "receita.pdf", 1, cor="gray")
    leitor.registrar(antiga, [_sem_nascimento(hb="7,7", coleta="01/01/2026")])
    montar = _montagem(origem)
    de_60, de_75 = _pdf(montar / "a.pdf", 1), _pdf(montar / "b.pdf", 1, cor="ivory")
    leitor.registrar(de_60, [_hemograma()])
    leitor.registrar(de_75, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="15/08/1975")])
    return [antiga], _zip(origem / "lote.zip", {"a.pdf": de_60, "b.pdf": de_75})


def _cenario_zip_sem_nascimento_entre_homonimas_e_pasta_da_duvida(origem, leitor):
    # Destino com 1960, 1975 e a pasta que o sem.pdf abriu na dúvida; o ZIP traz
    # FULANA 1980 e um item sem nascimento, que não herda o 1980.
    antes, sem = _cenario_sem_nascimento_entre_homonimas(origem, leitor)
    montar = _montagem(origem)
    x, y = _pdf(montar / "x.pdf", 1, cor="beige"), _pdf(montar / "y.pdf", 1, cor="gray")
    leitor.registrar(x, [_hemograma(hb="12,4", coleta="06/06/2026", nascimento="20/02/1980")])
    leitor.registrar(y, [_sem_nascimento(hb="5,5", coleta="07/07/2026")])
    return antes + [sem], _zip(origem / "lote.zip", {"x.pdf": x, "y.pdf": y})


def _cenario_zip_dois_sem_nascimento_entre_homonimas(origem, leitor):
    # Os dois itens do mesmo nome, sem nascimento, vão juntos para a pasta que
    # o ZIP abre na dúvida, e a nova tentativa não os separa.
    antes = _homonimas_no_destino(origem, leitor)
    montar = _montagem(origem)
    b, c = _pdf(montar / "b.pdf", 1, cor="ivory"), _pdf(montar / "c.pdf", 1, cor="khaki")
    leitor.registrar(b, [_sem_nascimento(hb="10,1", coleta="03/03/2026")])
    leitor.registrar(c, [_sem_nascimento(hb="5,5", coleta="07/07/2026")])
    return antes, _zip(origem / "lote.zip", {"b.pdf": b, "c.pdf": c})


CENARIOS = [_cenario_misto, _cenario_ilegivel, _cenario_zip_misto, _cenario_repetidos_na_pasta,
            _cenario_sem_nascimento_entre_homonimas, _cenario_zip_homonimas_e_pasta_sem_nascimento,
            _cenario_zip_sem_nascimento_entre_homonimas_e_pasta_da_duvida,
            _cenario_zip_dois_sem_nascimento_entre_homonimas]


def test_zip_com_item_sem_nascimento_entre_homonimas_abre_pasta_propria(ambiente):
    origem, destino, leitor, ctx = ambiente
    antes, alvo = _cenario_zip_sem_nascimento_entre_homonimas_e_pasta_da_duvida(origem, leitor)
    for arquivo in antes:
        processar(arquivo, ctx)
    [da_duvida] = [p for p in _pastas_de_paciente(destino) if "10,1" in _texto_md(p)]
    intocadas = {p: _arquivos(p) for p in _pastas_de_paciente(destino)}
    processar(alvo, ctx)
    assert {p: _arquivos(p) for p in intocadas} == intocadas
    [com_o_sem] = [p for p in _pastas_de_paciente(destino) if "| 5,5 |" in _texto_md(p)]
    assert com_o_sem not in intocadas and "12,4" not in _texto_md(com_o_sem)
    assert _manifesto(com_o_sem)["paciente"]["nascimento"] is None
    [de_1980] = [p for p in _pastas_de_paciente(destino) if "12,4" in _texto_md(p)]
    assert _manifesto(de_1980)["paciente"]["nascimento"] == "1980-02-20"
    assert da_duvida != com_o_sem
    assert (destino / "_ARQUIVOS COM MAIS DE UM PACIENTE" / "lote.zip").is_file()


def _rodar_cenario(raiz: Path, cenario, monkeypatch, vez: int = 0):
    origem, destino, leitor, ctx = _montar(raiz)
    antes, alvo = cenario(origem, leitor)
    for arquivo in antes:
        processar(arquivo, ctx)
    with monkeypatch.context() as m:
        falha = _injetar(m, vez)
        if vez:
            with pytest.raises(OSError):
                processar(alvo, ctx)
        else:
            assert processar(alvo, ctx)["situacao"] == "feito"
    if vez:
        assert processar(alvo, ctx)["situacao"] == "feito"
    return destino, falha.chamadas


@pytest.mark.parametrize("cenario", CENARIOS, ids=lambda c: c.__name__.removeprefix("_cenario_"))
def test_nova_tentativa_depois_de_falha_em_qualquer_passo_da_a_arvore_limpa(tmp_path, monkeypatch,
                                                                             cenario):
    # Cada passo roda o arquivo duas vezes. O que se mede é a escrita: sem
    # fsync, e com o entendimento (puro) de cada sha256 feito uma vez só.
    monkeypatch.setattr(atomico.os, "fsync", lambda _descritor: None)
    entender, entendidos = modulo_processar.entender, {}

    def entender_uma_vez(paginas, sha256, *a, **kw):
        if sha256 not in entendidos:
            entendidos[sha256] = entender(paginas, sha256, *a, **kw)
        return entendidos[sha256]

    monkeypatch.setattr(modulo_processar, "entender", entender_uma_vez)
    limpo, passos = _rodar_cenario(tmp_path / "limpo", cenario, monkeypatch)
    esperado = _arquivos(limpo)
    assert passos > 3
    diferentes = []
    for vez in range(1, passos + 1):
        # O limite Win32 considera a raiz inteira: cada tentativa tem o mesmo
        # comprimento de `limpo`, para comparar a escrita no mesmo orçamento.
        destino, _ = _rodar_cenario(tmp_path / f"{vez:05d}", cenario, monkeypatch, vez)
        obtido = _arquivos(destino)
        if obtido != esperado:
            diferentes.append((vez, sorted(set(obtido) ^ set(esperado)),
                               sorted(k for k in set(obtido) & set(esperado)
                                      if obtido[k] != esperado[k])))
    assert diferentes == []


def test_nova_tentativa_reaproveita_o_original_que_ja_copiou_com_sufixo(ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma()])
    processar(primeiro, ctx)
    (origem / "outro").mkdir()
    segundo = _pdf(origem / "outro" / "scan001.pdf", 1, cor="ivory")
    leitor.registrar(segundo, [_hemograma(hb="11,9", coleta="02/06/2026")])
    gravar_manifesto = registro.gravar_manifesto
    estado = {"falhou": False}

    def falha_uma_vez_no_manifesto_final(pasta, m, *a, **kw):
        if len(m.get("documentos", [])) == 2 and not estado["falhou"]:
            estado["falhou"] = True
            raise OSError(errno.EIO, "falha_injetada")
        return gravar_manifesto(pasta, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", falha_uma_vez_no_manifesto_final)
    with pytest.raises(OSError):
        processar(segundo, ctx)
    assert processar(segundo, ctx)["situacao"] == "feito"
    [pasta] = _pastas_de_paciente(destino)
    assert sorted(p.name for p in (pasta / "ORIGINAIS").iterdir()) == ["scan001 (2).pdf",
                                                                     "scan001.pdf"]
    assert _manifesto(pasta)["originais"] == ["scan001 (2).pdf", "scan001.pdf"]


def test_nova_tentativa_sem_nascimento_entre_homonimas_volta_a_pasta_que_abriu(ambiente,
                                                                              monkeypatch):
    origem, destino, leitor, ctx = ambiente
    antes, alvo = _cenario_sem_nascimento_entre_homonimas(origem, leitor)
    for arquivo in antes:
        processar(arquivo, ctx)
    gravar_manifesto = registro.gravar_manifesto
    estado = {"falhou": False}

    def falha_uma_vez_no_manifesto_final(pasta, m, *a, **kw):
        if m.get("documentos") and not estado["falhou"]:
            estado["falhou"] = True
            raise OSError(errno.EIO, "falha_injetada")
        return gravar_manifesto(pasta, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", falha_uma_vez_no_manifesto_final)
    with pytest.raises(OSError):
        processar(alvo, ctx)
    assert processar(alvo, ctx)["situacao"] == "feito"
    pastas = _pastas_de_paciente(destino)
    assert len(pastas) == 3
    assert [p.name for p in pastas if "10,1" in _texto_md(p)] == [pastas[2].name]
    assert _manifesto(pastas[2])["paciente"]["nascimento"] is None


def _sem_nascimento_e_terceira_homonima(raiz: Path, monkeypatch, vez: int):
    """Destino com as homônimas de 1960 e 1975; o sem nascimento falha na gravação
    `vez` (0: não falha), a de 1980 chega, e o sem nascimento é refeito."""
    origem, destino, leitor, ctx = _montar(raiz)
    antes, sem = _cenario_sem_nascimento_entre_homonimas(origem, leitor)
    de_80 = _pdf(origem / "h80.pdf", 1, cor="khaki")
    leitor.registrar(de_80, [_hemograma(hb="9,1", coleta="05/09/2026", nascimento="20/02/1980")])
    for arquivo in antes:
        processar(arquivo, ctx)
    with monkeypatch.context() as m:
        falha = _injetar(m, vez)
        if vez:
            with pytest.raises(OSError):
                processar(sem, ctx)
        else:
            assert processar(sem, ctx)["situacao"] == "feito"
    assert processar(de_80, ctx)["situacao"] == "feito"
    processar(sem, ctx)
    return destino, falha.chamadas


def test_outra_homonima_nao_toma_a_pasta_aberta_na_duvida_nem_antes_da_nova_tentativa(
        tmp_path, monkeypatch):
    monkeypatch.setattr(atomico.os, "fsync", lambda _descritor: None)
    _, passos = _sem_nascimento_e_terceira_homonima(tmp_path / "limpo", monkeypatch, 0)
    assert passos > 3
    erradas = []
    for vez in range(0, passos + 1):
        destino, _ = _sem_nascimento_e_terceira_homonima(tmp_path / f"falha{vez}", monkeypatch,
                                                         vez)
        mds = [p for p in destino.rglob("*.md") if p.parent.parent.parent == destino]
        do_sem = [p.parent.parent for p in mds if "| 10,1 |" in p.read_text(encoding="utf-8")]
        de_80 = [p for p in _pastas_de_paciente(destino) if "9,1" in _texto_md(p)]
        certo = (len(do_sem) == 1 and len(de_80) == 1 and do_sem[0] != de_80[0]
                 and _manifesto(do_sem[0])["paciente"]["nascimento"] is None
                 and _manifesto(de_80[0])["paciente"]["nascimento"] == "1980-02-20")
        if not certo:
            erradas.append((vez, [p.name for p in do_sem], [p.name for p in de_80]))
    assert erradas == []


# --- repetidos ---------------------------------------------------------------------

def _paginas_registradas(m: dict) -> set[tuple[str, int]]:
    vistas = set()
    for d in m["documentos"]:
        for o in d["origens"] + d["duplicatas"]:
            vistas.update((o["caminho"], p) for p in o["paginas"])
    for b in m.get("paginas_em_branco", []):
        vistas.update((b["caminho"], p) for p in b["paginas"])
    return vistas


def test_repetido_de_repetido_fica_no_manifesto(ambiente):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma()])
    processar(primeiro, ctx)
    segundo = _pdf(origem / "scan002.pdf", 2, cor="ivory")
    leitor.registrar(segundo, [_hemograma(), _hemograma()])
    processar(segundo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    vistas = _paginas_registradas(_manifesto(pasta))
    assert {("scan002.pdf", 0), ("scan002.pdf", 1)} <= vistas


def test_mesmo_exame_lido_sem_acento_na_mesma_pasta_e_repetido(ambiente):
    origem, destino, leitor, ctx = ambiente
    primeiro = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(primeiro, [_hemograma(nome="FULANA DE TESTE SINTÉTICA")])
    processar(primeiro, ctx)
    segundo = _pdf(origem / "scan002.pdf", 1, cor="ivory")
    leitor.registrar(segundo, [_hemograma(nome="FULANA DE TESTE SINTETICA")])
    processar(segundo, ctx)
    [pasta] = _pastas_de_paciente(destino)
    assert len(list((pasta / "3 LABORATORIO").glob("*.md"))) == 1
    [entrada] = _manifesto(pasta)["documentos"]
    assert [d["caminho"] for d in entrada["duplicatas"]] == ["scan002.pdf"]


# --- duas estações no mesmo destino -------------------------------------------------

def test_duas_estacoes_na_mesma_pasta_nao_perdem_entrada_do_manifesto(tmp_path, monkeypatch):
    origem, destino, leitor, ctx_a = _montar(tmp_path)
    ctx_b = replace(ctx_a, estacao="estacao-b", pid=4343, pasta_temp=tmp_path / "temp-b")
    anterior = _pdf(origem / "scan000.pdf", 1, cor="beige")
    leitor.registrar(anterior, [_hemograma(hb="13,0", coleta="01/01/2026")])
    processar(anterior, ctx_a)
    fa = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(fa, [_hemograma()])
    fb = _pdf(origem / "scan002.pdf", 1, cor="ivory")
    leitor.registrar(fb, [_hemograma(hb="11,9", coleta="02/06/2026")])
    gravar_manifesto = registro.gravar_manifesto

    def lento(pasta, m, *a, **kw):
        time.sleep(0.3)
        return gravar_manifesto(pasta, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", lento)
    erros = []

    def rodar(arquivo, ctx):
        try:
            processar(arquivo, ctx)
        except Exception as erro:  # noqa: BLE001 - o teste confere depois
            erros.append(erro)

    fios = [threading.Thread(target=rodar, args=par) for par in ((fa, ctx_a), (fb, ctx_b))]
    for fio in fios:
        fio.start()
    for fio in fios:
        fio.join()
    assert erros == []
    [pasta] = _pastas_de_paciente(destino)
    origens = sorted(e["origens"][0]["caminho"] for e in _manifesto(pasta)["documentos"])
    assert origens == ["scan000.pdf", "scan001.pdf", "scan002.pdf"]


def _que_faltam(pasta: Path) -> list[str]:
    """Os arquivos que o manifesto da pasta registra e não estão no disco."""
    m = _manifesto(pasta)
    nomes = [f"_scan-to-text/documentos/{e['id']}.json" for e in m["documentos"]]
    nomes += [e[campo] for e in m["documentos"] for campo in ("md", "conferencia") if e[campo]]
    return [nome for nome in nomes if not (pasta / nome).is_file()]


def test_trava_de_escrita_tomada_nao_apaga_o_que_o_dono_ainda_grava(ambiente, monkeypatch):
    # O dono parou além do prazo no meio da escrita e outra estação tomou a
    # trava: ela passa outro arquivo inteiro sem tocar no que ele gravou e
    # ainda não registrou.
    origem, destino, leitor, ctx = ambiente
    antiga = _pdf(origem / "h60.pdf", 1, cor="beige")
    leitor.registrar(antiga, [_hemograma(hb="13,3", coleta="01/01/2026")])
    processar(antiga, ctx)
    [pasta] = _pastas_de_paciente(destino)
    x = _pdf(origem / "a.pdf", 1, cor="ivory")
    leitor.registrar(x, [_hemograma(hb="10,1", coleta="03/03/2026")])
    y = _pdf(origem / "t.pdf", 1, cor="khaki")
    leitor.registrar(y, [_tomografia()])
    ctx_b = replace(ctx, estacao="estacao-b", pid=4343, pasta_temp=ctx.pasta_temp.parent / "temp-b")
    lp = destino / "_scan-to-text" / "travas" / f"{arvore.TRAVA_DE_ESCRITA}.lock"
    gravar_manifesto = registro.gravar_manifesto
    outra = {}

    def tomada_no_registro_de_x(alvo, m, *a, **kw):
        if "y" not in outra and any(o["caminho"] == "a.pdf" for e in m["documentos"]
                                    for o in e["origens"]):
            lp.unlink()
            outra["y"] = processar(y, ctx_b)["situacao"]
        return gravar_manifesto(alvo, m, *a, **kw)

    monkeypatch.setattr(registro, "gravar_manifesto", tomada_no_registro_de_x)
    processar(x, ctx)
    monkeypatch.undo()
    assert outra["y"] == "feito"
    assert "| 10,1 |" in _texto_md(pasta)
    assert _que_faltam(pasta) == []
    assert _fora_dos_manifestos(destino) == []


def test_dono_que_perdeu_a_trava_de_escrita_nao_registra_e_devolve_o_arquivo(ambiente,
                                                                            monkeypatch):
    from scan_to_text.escrever import markdown
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    lp = destino / "_scan-to-text" / "travas" / f"{arvore.TRAVA_DE_ESCRITA}.lock"
    outra = Trava(destino, arvore.TRAVA_DE_ESCRITA, "OUTRA", 9)
    md_documento = markdown.md_documento

    def tomada(doc, *args, **kw):
        if not outra.e_minha():
            lp.unlink()
            assert outra.tomar()
        return md_documento(doc, *args, **kw)

    monkeypatch.setattr(markdown, "md_documento", tomada)
    resumo = processar(arquivo, ctx)
    monkeypatch.undo()
    assert resumo["situacao"] == "destino_ocupado"
    assert not registro.ja_feito(destino, resumo["sha256"])
    assert [p.name for p in _pastas_de_paciente(destino) if _manifesto(p)["documentos"]] == []
    outra.soltar()
    assert processar(arquivo, ctx)["situacao"] == "feito"
    assert _fora_dos_manifestos(destino) == []
    [pasta] = _pastas_de_paciente(destino)
    assert "12,4" in _texto_md(pasta) and _que_faltam(pasta) == []


def test_perda_da_trava_depois_dos_documentos_nao_marca_feito(ambiente, monkeypatch):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "a.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    escrever = arvore.escrever_arquivo
    outra = Trava(destino, arvore.TRAVA_DE_ESCRITA, "OUTRA", 9)

    def perder(*args, **kwargs):
        pastas = escrever(*args, **kwargs)
        (destino / "_scan-to-text" / "travas" / "escrita.lock").unlink()
        assert outra.tomar()
        return pastas

    with monkeypatch.context() as m:
        m.setattr(arvore, "escrever_arquivo", perder)
        resumo = processar(arquivo, ctx)
    assert resumo["situacao"] == "destino_ocupado"
    assert not registro.ja_feito(destino, resumo["sha256"])
    outra.soltar()
    assert processar(arquivo, ctx)["situacao"] == "feito"
    [pasta] = _pastas_de_paciente(destino)
    assert _que_faltam(pasta) == [] and _fora_dos_manifestos(destino) == []


@pytest.mark.parametrize("acao", ["gravar", "copiar", "apagar"])
def test_gravador_que_perdeu_a_trava_nao_altera_os_arquivos(ambiente, acao):
    origem, destino, _, ctx = ambiente
    fonte = origem / "fonte.txt"
    fonte.write_bytes(b"novo")
    alvo = destino / "COMPLETO.md"
    alvo.write_bytes(b"entregue")
    g = arvore.Gravador(ctx.pacote, ctx.es, ctx.estacao, ctx.pid, ctx.hoje,
                        ctx.limiar, ctx.versoes, ctx.pasta_temp)
    outra = Trava(destino, arvore.TRAVA_DE_ESCRITA, "OUTRA", 9)
    with arvore.escrita_exclusiva(destino, g) as exclusivo:
        (destino / "_scan-to-text" / "travas" / "escrita.lock").unlink()
        assert outra.tomar()
        with pytest.raises(arvore.DestinoOcupado):
            if acao == "gravar":
                exclusivo.gravar(alvo, b"novo")
            elif acao == "copiar":
                exclusivo.copiar(fonte, alvo)
            else:
                exclusivo.apagar(alvo)
    outra.soltar()
    assert alvo.read_bytes() == b"entregue"


def test_final_da_escrita_preserva_o_sinal_de_outra_estacao(ambiente):
    _, destino, _, ctx = ambiente
    pasta = destino / "pasta"
    g = arvore.Gravador(ctx.pacote, ctx.es, ctx.estacao, ctx.pid, ctx.hoje,
                        ctx.limiar, ctx.versoes, ctx.pasta_temp)
    sinal = pasta / "_scan-to-text" / "trabalhando.json"
    outra = {"estacao": "OUTRA", "pid": 9}
    with arvore._trabalhando(pasta, g):
        sinal.write_text(json.dumps(outra), encoding="utf-8")
    assert json.loads(sinal.read_text(encoding="utf-8")) == outra


def test_destino_ocupado_por_outra_estacao_devolve_o_arquivo_sem_marcar_feito(ambiente,
                                                                              monkeypatch):
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    monkeypatch.setattr(arvore, "PRAZO_DA_TRAVA_S", 0.3)
    outra = Trava(destino, arvore.TRAVA_DE_ESCRITA, "OUTRA", 9)
    assert outra.tomar()
    resumo = processar(arquivo, ctx)
    assert resumo["situacao"] == "destino_ocupado"
    assert _pastas_de_paciente(destino) == []
    assert not registro.ja_feito(destino, resumo["sha256"])
    outra.soltar()
    assert processar(arquivo, ctx)["situacao"] == "feito"


def _trava_parada(destino: Path, nome: str) -> Path:
    lp = destino / "_scan-to-text" / "travas" / f"{nome}.lock"
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({"estacao": "OUTRA", "pid": 999999, "token": "a" * 32,
                              "contador": 3, "inicio": 0}), encoding="utf-8")
    return lp


def test_trava_de_escrita_parada_vence_em_prazo_curto_mesmo_com_trabalhador_novo(ambiente,
                                                                                 monkeypatch):
    from scan_to_text import travas
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    parada = _trava_parada(destino, arvore.TRAVA_DE_ESCRITA)
    monkeypatch.setattr(arvore, "PARADA_DA_ESCRITA_S", 0.5)
    monkeypatch.setattr(arvore, "PRAZO_DA_TRAVA_S", 0.2)
    assert processar(arquivo, ctx)["situacao"] == "destino_ocupado"
    time.sleep(0.6)
    travas._OBSERVADAS.clear()  # o trabalhador anterior encerrou: a memória dele foi junto
    assert processar(arquivo, ctx)["situacao"] == "feito"
    assert not parada.exists()
    assert len(_pastas_de_paciente(destino)) == 1


def test_trava_de_arquivo_parada_vence_mesmo_com_trabalhador_novo(ambiente, monkeypatch):
    from scan_to_text import travas
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    _trava_parada(destino, sha)
    monkeypatch.setattr(travas, "PARADA_S", 0.5)
    assert processar(arquivo, ctx)["situacao"] == "em_outra_estacao"
    time.sleep(0.6)
    travas._OBSERVADAS.clear()
    assert processar(arquivo, ctx)["situacao"] == "feito"


def test_trava_de_escrita_bate_em_intervalo_curto(ambiente, monkeypatch):
    from scan_to_text import travas
    from scan_to_text.escrever import markdown
    origem, destino, leitor, ctx = ambiente
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    monkeypatch.setattr(arvore, "BATIMENTO_DA_ESCRITA_S", 0.05)
    lp = destino / "_scan-to-text" / "travas" / f"{arvore.TRAVA_DE_ESCRITA}.lock"
    contadores = []
    original = markdown.md_documento

    def devagar(doc, *args, **kw):
        limite = time.monotonic() + 5
        while True:
            assert lp.is_file()
            dado = travas._ler(lp) or {}
            if dado:
                assert dado.get("estacao") == ctx.estacao and dado.get("pid") == ctx.pid
                contador = dado.get("contador")
                if isinstance(contador, int) and contador >= 2:
                    contadores.append(contador)
                    break
            assert time.monotonic() < limite, "dois batimentos não foram persistidos"
            time.sleep(0.005)
        return original(doc, *args, **kw)

    monkeypatch.setattr(markdown, "md_documento", devagar)
    assert processar(arquivo, ctx)["situacao"] == "feito"
    assert contadores and contadores[0] >= 2


# --- PDF gerado na escrita -----------------------------------------------------------

def test_pdf_de_quadros_e_deterministico(tmp_path, monkeypatch):
    imagem = tmp_path / "quadros.tif"
    quadros = [Image.new("RGB", (200 + i, 280), "white") for i in range(3)]
    quadros[0].save(imagem, save_all=True, append_images=quadros[1:])
    gmtime, segundos = time.gmtime, iter(range(0, 10 ** 12, 10 ** 6))
    monkeypatch.setattr(time, "gmtime", lambda *a: gmtime(next(segundos)))
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    arvore._pdf_de_quadros(imagem, [0, 2], a)
    arvore._pdf_de_quadros(imagem, [0, 2], b)
    assert a.read_bytes() == b.read_bytes()
