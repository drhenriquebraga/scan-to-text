import dataclasses
from datetime import date

import pytest

from scan_to_text import pacote
from scan_to_text.tipos import (DataExame, Documento, Linha, Pagina, Resultado, Tabela,
                                texto)


def _pagina() -> Pagina:
    return Pagina(arquivo_sha256="ab" * 32, indice=0, blocos=(
        Linha("Paciente: FULANO DE TAL", (0.1, 0.05, 0.6, 0.08), camada="cabecalho"),
        Tabela(celulas=(("Exame", "Resultado"), ("Hemoglobina", "12,4")),
               confiancas=((1.0, 1.0), (0.99, 0.62)), caixa=(0.1, 0.3, 0.9, 0.5),
               linhas_de_cabecalho=1),
    ))


def test_tipos_sao_imutaveis():
    p = _pagina()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.indice = 3
    doc = Documento(id="ab-01", paginas=(p,))
    novo = dataclasses.replace(doc, categoria=3, data=DataExame(date(2026, 4, 2), "dia"))
    assert doc.categoria == 5 and novo.categoria == 3


def test_texto_de_linha_e_de_tabela():
    linha, tabela = _pagina().blocos
    assert texto(linha) == "Paciente: FULANO DE TAL"
    assert texto(tabela) == "Exame | Resultado\nHemoglobina | 12,4"


def test_resultado_sem_documento_e_ilegivel():
    r = Resultado(arquivo_sha256="cd" * 32, documentos=(), pacientes=(), multi_paciente=False,
                  ilegivel="senha")
    assert r.ilegivel == "senha"


def test_pacote_le_cada_yaml_como_secao(tmp_path):
    (tmp_path / "xx").mkdir()
    (tmp_path / "xx" / "rotulos.yaml").write_text("nome: [Nome]\n", encoding="utf-8")
    (tmp_path / "xx" / "datas.yaml").write_bytes("meses: [jan]\n".encode("utf-8-sig"))
    p = pacote.carregar("xx", raiz=tmp_path)
    assert p.idioma == "xx"
    assert p["rotulos"] == {"nome": ["Nome"]}
    assert p["datas"] == {"meses": ["jan"]}


def test_pacote_ausente_levanta_erro(tmp_path):
    with pytest.raises(pacote.PacoteAusente):
        pacote.carregar("zz", raiz=tmp_path)
