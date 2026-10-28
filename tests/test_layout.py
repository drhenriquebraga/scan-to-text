"""Leitura com o Docling de verdade: precisa dos modelos e leva segundos por página."""
import sys

import pytest

from scan_to_text import modelos
from scan_to_text.tipos import Linha, Tabela
from sinteticos import laudo_laboratorio, pagina_em_branco

try:
    PASTA = modelos.pasta_modelos()
except modelos.ModelosAusentes:
    PASTA = None

pytestmark = [pytest.mark.modelos,
              pytest.mark.skipif(PASTA is None, reason="modelos ausentes")]

if PASTA is not None:
    # O primeiro import do Docling guarda um quadro de quem o fez: feito dentro
    # do construtor de um Leitor, aquele Leitor e os modelos dele nunca saem
    # da memória.
    import docling.document_converter  # noqa: F401


@pytest.fixture(scope="module")
def leitor():
    from scan_to_text.ler.layout import Leitor
    return Leitor(PASTA)


# Antes do `leitor` do módulo: só um conjunto de modelos na memória por vez.
def test_pipeline_so_nasce_na_leitura_do_formato_e_a_falha_dele_e_formato_indisponivel():
    from scan_to_text.ler.layout import FormatoIndisponivel, Leitor

    novo = Leitor(PASTA)
    assert novo._conversor.initialized_pipelines == {}  # nada pré-carregado na construção
    with pytest.MonkeyPatch.context() as m:
        # O TableFormer importa este módulo só ao montar o pipeline.
        m.setitem(sys.modules, "docling_ibm_models.tableformer.data_management.tf_predictor",
                  None)
        with pytest.raises(FormatoIndisponivel) as erro:
            novo.ler_imagem(laudo_laboratorio(), "a" * 64, 0)
    assert isinstance(erro.value.__cause__, ImportError)
    assert str(erro.value).startswith("IMAGE ")
    pagina = novo.ler_imagem(laudo_laboratorio(), "a" * 64, 0)  # a próxima tentativa lê
    assert any(l.texto.startswith("Paciente") for l in _linhas(pagina))
    assert len(novo._conversor.initialized_pipelines) == 1  # só o da imagem
    novo._conversor.initialized_pipelines.clear()  # o `leitor` do módulo vem depois


def _linhas(pagina):
    return [b for b in pagina.blocos if isinstance(b, Linha)]


def _tabelas(pagina):
    return [b for b in pagina.blocos if isinstance(b, Tabela)]


def test_laudo_vira_linhas_uma_por_linha_impressa(leitor):
    pagina = leitor.ler_imagem(laudo_laboratorio(), "a" * 64, 0)
    textos = [l.texto for l in _linhas(pagina)]
    paciente = [t for t in textos if t.startswith("Paciente")]
    assert paciente and "solicitante" not in paciente[0]
    assert any(t.startswith("Data da coleta") for t in textos)
    for linha in _linhas(pagina):
        x0, y0, x1, y1 = linha.caixa
        assert 0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1
        assert 0 < linha.confianca <= 1


def test_tabela_sai_como_matriz_com_confianca_por_celula(leitor):
    pagina = leitor.ler_imagem(laudo_laboratorio(), "a" * 64, 0)
    tabela = _tabelas(pagina)[0]
    assert tabela.linhas_de_cabecalho == 1
    linha_hb = [l for l in tabela.celulas if l and l[0] == "Hemoglobina"][0]
    assert linha_hb[1] == "12,4"
    assert len(tabela.confiancas) == len(tabela.celulas)
    assert all(len(c) == len(l) for c, l in zip(tabela.confiancas, tabela.celulas))


def test_rodape_vem_na_camada_rodape_e_por_ultimo(leitor):
    pagina = leitor.ler_imagem(laudo_laboratorio(), "a" * 64, 0)
    rodape = [l for l in _linhas(pagina) if l.camada == "rodape"]
    assert rodape and "Página 1 de 1" in rodape[0].texto
    assert pagina.blocos[-1] is rodape[-1]


def test_pagina_de_cabeca_para_baixo_volta_na_ordem(leitor):
    pagina = leitor.ler_imagem(laudo_laboratorio().rotate(180), "a" * 64, 0)
    assert pagina.rotacao == 180
    tabela = _tabelas(pagina)[0]
    assert tabela.celulas[1][0] == "Hemoglobina"


def test_pagina_em_branco(leitor):
    pagina = leitor.ler_imagem(pagina_em_branco(), "a" * 64, 1)
    assert pagina.em_branco and pagina.blocos == () and pagina.indice == 1


def test_pdf_escaneado_passa_pelo_ocr(leitor, tmp_path):
    arquivo = tmp_path / "escaneado.pdf"
    laudo_laboratorio().save(arquivo, resolution=300)
    paginas = leitor.ler_arquivo(arquivo, "b" * 64)
    assert len(paginas) == 1
    assert any(l.texto.startswith("Paciente") for l in _linhas(paginas[0]))


def test_docx_tem_texto_e_tabela_com_confianca_total(leitor, tmp_path):
    import docx

    documento = docx.Document()
    documento.add_heading("RELATÓRIO MÉDICO", level=1)
    documento.add_paragraph("Paciente: FULANA DE TESTE SINTÉTICA")
    tabela = documento.add_table(rows=2, cols=2)
    tabela.cell(0, 0).text, tabela.cell(0, 1).text = "Exame", "Resultado"
    tabela.cell(1, 0).text, tabela.cell(1, 1).text = "PSA", "0,8"
    arquivo = tmp_path / "relatorio.docx"
    documento.save(arquivo)
    paginas = leitor.ler_arquivo(arquivo, "c" * 64)
    linhas = [l for p in paginas for l in _linhas(p)]
    assert any(l.texto == "Paciente: FULANA DE TESTE SINTÉTICA" for l in linhas)
    assert all(l.confianca == 1.0 for l in linhas)
    assert any(t.celulas[1] == ("PSA", "0,8") for p in paginas for t in _tabelas(p))
