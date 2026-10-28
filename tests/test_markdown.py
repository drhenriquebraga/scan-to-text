"""Markdown do documento e do COMPLETO.md.

Os golden ficam aqui como listas de linhas, com os dois espaços de quebra
escritos de propósito: editor que apara espaço no fim da linha estragaria um
arquivo de golden sem ninguém ver. Um arquivo `COMPLETO.md` no repositório
seria recusado pelo gancho de paciente.
"""
import dataclasses
from datetime import date

import pytest
import yaml

from scan_to_text import pacote as pacote_mod
from scan_to_text.escrever import markdown
from scan_to_text.pacote import RAIZ_PACKS, Pacote
from scan_to_text.tipos import DataExame, Documento, Linha, Pagina, Tabela

SHA = "3f" * 32
CAIXA = (0.1, 0.1, 0.9, 0.2)


@pytest.fixture(scope="module")
def pacote() -> Pacote:
    # Só o textos.yaml: os outros YAML do pacote não interessam a estes testes.
    arquivo = RAIZ_PACKS / "pt-BR" / "textos.yaml"
    return Pacote("pt-BR", {"textos": yaml.safe_load(arquivo.read_text(encoding="utf-8-sig"))})


def _linha(texto, grupo=-1, confianca=1.0, camada="corpo", tipo="texto"):
    return Linha(texto, CAIXA, camada=camada, tipo=tipo, confianca=confianca, grupo=grupo)


def _doc(n, *, titulo="", paciente="FULANA DE TESTE", instituicao=None, data=DataExame(),
         categoria=5, cabecalho=(), corpo=(), paginas=None, repetido_de=None):
    if paginas is None:
        paginas = (Pagina(SHA, n, tuple(corpo)),)
    return Documento(id=f"{SHA[:12]}-{n:02d}", paginas=paginas, titulo=titulo,
                     instituicao=instituicao, paciente=paciente, categoria=categoria,
                     data=data, cabecalho_original=tuple(cabecalho), corpo=tuple(corpo),
                     repetido_de=repetido_de)


def _dia(a, m, d):
    return DataExame(date(a, m, d), "dia", "realizacao", None)


def _mamografia():
    return _doc(
        1, titulo="MAMOGRAFIA BILATERAL", instituicao="Clínica de Imagem Beta",
        data=_dia(2026, 4, 2), categoria=2,
        cabecalho=("Clínica de Imagem Beta", "Convênio: Plano Exemplo",
                   "Médico solicitante: Dr. Sicrano Exemplo", "Convênio: Plano Exemplo"),
        corpo=(_linha("MAMOGRAFIA BILATERAL", grupo=0, tipo="titulo"),
               _linha("Mamas com padrão fibroglandular", grupo=1),
               _linha("heterogêneo, nota_1 e *misto*.", grupo=1),
               _linha("BI-RADS: 2", grupo=2)),
    )


def _hemograma(n=5, confianca_hb=0.62):
    tabela = Tabela(
        celulas=(("Exame", "Resultado", "Unidade"),
                 ("Hemoglobina", "124", "g/dL"),
                 ("Leucócitos", "6.500", "/mm3")),
        confiancas=((1.0, 1.0, 1.0), (0.99, confianca_hb, 0.97), (0.95, 0.91, 0.99)),
        caixa=(0.1, 0.3, 0.9, 0.5), linhas_de_cabecalho=1)
    return _doc(n, titulo="HEMOGRAMA", instituicao="Laboratório Alfa",
                data=_dia(2026, 5, 10), categoria=3, corpo=(tabela,))


AVISO = "## DADOS NUMÉRICOS ISOLADOS NÃO SÃO CONFIÁVEIS"


# ---------- golden do documento individual ----------

def test_golden_md_individual(pacote):
    esperado = "\n".join([
        "# MAMOGRAFIA BILATERAL",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 02/04/2026  ",
        "Instituição: Clínica de Imagem Beta  ",
        "",
        "Convênio: Plano Exemplo  ",
        "Médico solicitante: Dr. Sicrano Exemplo  ",
        "",
        "MAMOGRAFIA BILATERAL  ",
        "",
        "Mamas com padrão fibroglandular  ",
        "heterogêneo, nota_1 e *misto*.  ",
        "",
        "BI-RADS: 2  ",
        "",
        AVISO,
        "",
    ])
    assert markdown.md_documento(_mamografia(), pacote) == esperado


def test_golden_md_laboratorio_com_tabela_e_lista(pacote):
    esperado = "\n".join([
        "# HEMOGRAMA",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 10/05/2026  ",
        "Instituição: Laboratório Alfa  ",
        "",
        "| Exame | Resultado | Unidade |",
        "| --- | --- | --- |",
        "| Hemoglobina | 124 | g/dL |",
        "| Leucócitos | 6.500 | /mm3 |",
        "",
        AVISO,
        "",
        "Leitura incerta nestas linhas:",
        "- Hemoglobina | 124 | g/dL",
        "",
    ])
    assert markdown.md_documento(_hemograma(), pacote) == esperado


# ---------- regras do documento individual ----------

def test_campo_ausente_nao_gera_linha(pacote):
    doc = _doc(2, paciente=None, corpo=(_linha("Anotação livre sem cabeçalho."),))
    md = markdown.md_documento(doc, pacote)
    assert md.splitlines()[0] == "# DOCUMENTO"
    for rotulo in ("Paciente", "Data do exame", "Instituição", "informado"):
        assert rotulo not in md


def test_campos_e_cabecalho_terminam_com_dois_espacos(pacote):
    linhas = markdown.md_documento(_mamografia(), pacote).splitlines()
    campos = [l for l in linhas if l.startswith(("Paciente:", "Data do exame:", "Instituição:",
                                                  "Convênio:", "Médico solicitante:"))]
    assert len(campos) == 5
    assert all(l.endswith("  ") and not l.endswith("   ") for l in campos)


def test_cabecalho_original_entra_uma_vez_sem_repetir_a_instituicao(pacote):
    md = markdown.md_documento(_mamografia(), pacote)
    assert md.count("Convênio: Plano Exemplo") == 1
    assert md.count("Clínica de Imagem Beta") == 1


def test_corpo_sem_escape_de_markdown(pacote):
    corpo = (_linha("CA_15-3 * resultado_final **alto** [ver] #1 <b> `x` 2_3"),)
    md = markdown.md_documento(_doc(3, titulo="nota_1 *x*", corpo=corpo), pacote)
    assert "\\_" not in md and "\\*" not in md and "\\" not in md
    assert "CA_15-3 * resultado_final **alto** [ver] #1 <b> `x` 2_3" in md
    assert md.startswith("# nota_1 *x*\n")


def test_sem_nome_de_arquivo_nem_data_de_hoje(pacote):
    hoje = date.today()
    for doc in (_mamografia(), _hemograma()):
        md = markdown.md_documento(doc, pacote)
        assert SHA not in md and SHA[:12] not in md
        assert ".pdf" not in md.lower() and "scan" not in md.lower()
        assert hoje.isoformat() not in md
        assert hoje.strftime("%d/%m/%Y") not in md


def test_tabela_irregular_e_celula_com_quebra(pacote):
    tabela = Tabela(celulas=(("A", "B"), ("linha\num", "x", "extra"), ("so",)),
                    confiancas=((1.0, 1.0), (1.0, 1.0, 1.0), (1.0,)),
                    caixa=CAIXA, linhas_de_cabecalho=0)
    md = markdown.md_documento(_doc(4, corpo=(tabela,)), pacote)
    assert "\n".join([
        "| A | B |  |",
        "| --- | --- | --- |",
        "| linha um | x | extra |",
        "| so |  |  |",
    ]) in md


def test_linhas_sem_grupo_ficam_em_linhas_separadas(pacote):
    corpo = (_linha("Convênio: Plano Exemplo"), _linha("Pedido: 4512"),
             _linha("Laudo", grupo=3), _linha("   "), _linha("Texto do laudo.", grupo=3))
    md = markdown.md_documento(_doc(5, corpo=corpo), pacote)
    assert "Convênio: Plano Exemplo  \nPedido: 4512  \n\nLaudo  \nTexto do laudo.  \n" in md


def test_quebra_dentro_da_linha_vira_linhas_com_dois_espacos(pacote):
    doc = _doc(14, paciente=None, cabecalho=("Pedido: 1\n\nSetor: B",),
               corpo=(_linha("Linha um\n\n  Linha dois  ", grupo=0),
                      _linha("Valor\n7,5", confianca=0.3, grupo=1)))
    md = markdown.md_documento(doc, pacote)
    assert "\n\nPedido: 1  \nSetor: B  \n\nLinha um  \nLinha dois  \n\nValor  \n7,5  \n\n" in md
    # A lista traz a linha como saiu no corpo, não a Linha juntada.
    assert md.endswith("Leitura incerta nestas linhas:\n- 7,5\n")


def test_linha_com_quebra_lista_cada_linha_com_numero():
    doc = _doc(22, corpo=(_linha("Hb\n12,4\ng/dL 13,0", confianca=0.4),))
    assert markdown.linhas_incertas(doc) == ["12,4", "g/dL 13,0"]


def test_aviso_sempre_presente_mesmo_sem_numero_incerto(pacote):
    for doc in (_mamografia(), _doc(6), _hemograma(confianca_hb=0.99)):
        md = markdown.md_documento(doc, pacote)
        assert md.endswith("\n\n" + AVISO + "\n")
        assert "Leitura incerta" not in md


def test_lista_so_quando_ha_numero_incerto(pacote):
    sem_digito = _doc(7, corpo=(_linha("Texto borrado", confianca=0.1),))
    no_limiar = _doc(8, corpo=(_linha("Glicose 98", confianca=0.80),))
    abaixo = _doc(9, corpo=(_linha("Glicose 98 mg/dL", confianca=0.79),))
    assert "Leitura incerta" not in markdown.md_documento(sem_digito, pacote)
    assert "Leitura incerta" not in markdown.md_documento(no_limiar, pacote)
    md = markdown.md_documento(abaixo, pacote)
    assert md.endswith(AVISO + "\n\nLeitura incerta nestas linhas:\n- Glicose 98 mg/dL\n")


@pytest.mark.parametrize("data, impressa", [
    (DataExame(date(2026, 3, 5), "dia", "carta", "Sao Paulo, 5 de março de 2026"),
     "5 de março de 2026"),
    (DataExame(date(2026, 4, 2), "dia", "realizacao", "Data da coleta: 02.04.2026"),
     "02.04.2026"),
    (DataExame(date(2026, 4, 12), "dia", "realizacao", "Coleta | 12/04/26"), "12/04/26"),
    (DataExame(date(2026, 3, 1), "mes", "realizacao", "Data do exame: mar/2026"), "mar/2026"),
    (DataExame(date(2026, 4, 12), "dia", "liberacao", "Data de liberacao:\n12/04/2026"),
     "12/04/2026"),
])
def test_data_do_exame_sai_como_impressa(data, impressa):
    # Valores copiados do documento, como impressos.
    completo = pacote_mod.carregar("pt-BR")
    md = markdown.md_documento(_doc(23, data=data), completo)
    assert f"\nData do exame: {impressa}  \n" in md


def test_data_sem_trecho_legivel_sai_pelo_formato_do_pacote():
    completo = pacote_mod.carregar("pt-BR")
    sem_trecho = _doc(24, data=_dia(2026, 4, 2))
    outro_valor = _doc(25, data=DataExame(date(2026, 4, 2), "dia", "realizacao",
                                          "Coleta: 03/04/2026"))
    for doc in (sem_trecho, outro_valor):
        assert "\nData do exame: 02/04/2026  \n" in markdown.md_documento(doc, completo)


# ---------- linhas incertas ----------

def test_toda_linha_com_numero_incerto_entra_na_lista():
    tabela = Tabela(celulas=(("Exame", "Resultado"), ("PSA", "4,1"), ("Obs", "baixa"),
                             ("CEA", "2,0")),
                    confiancas=((0.5, 0.5), (0.99, 0.70), (0.2, 0.2), (0.99, 0.99)),
                    caixa=CAIXA, linhas_de_cabecalho=1)
    corpo = (_linha("Plaquetas 250.000", confianca=0.5), _linha("Sem número", confianca=0.1),
             tabela, _linha("Plaquetas 250.000", confianca=0.5), _linha("Ureia 30", confianca=0.9))
    doc = _doc(10, corpo=corpo)
    assert markdown.linhas_incertas(doc) == ["Plaquetas 250.000", "PSA | 4,1"]
    assert markdown.linhas_incertas(doc, limiar=0.95) == ["Plaquetas 250.000", "PSA | 4,1",
                                                          "Ureia 30"]


def test_confianca_de_celula_ausente_conta_como_incerta():
    tabela = Tabela(celulas=(("K", "4,5"),), confiancas=(), caixa=CAIXA)
    assert markdown.linhas_incertas(_doc(11, corpo=(tabela,))) == ["K | 4,5"]


def test_numero_incerto_do_cabecalho_original_entra_na_lista():
    pagina = Pagina(SHA, 0, (
        _linha("Pedido: 88412", camada="cabecalho", confianca=0.40),
        _linha("Convênio: Plano Exemplo", camada="cabecalho", confianca=0.40),
        _linha("Resultado 7,2", confianca=0.99),
    ))
    doc = _doc(12, cabecalho=("Pedido: 88412", "Convênio: Plano Exemplo"),
               corpo=(_linha("Resultado 7,2", confianca=0.99),), paginas=(pagina,))
    assert markdown.linhas_incertas(doc) == ["Pedido: 88412"]


def _pagina_com_rodape(*blocos_do_corpo, rodape="Data de liberacao: 12/04/2026",
                       confianca_rodape=0.40):
    return Pagina(SHA, 0, (*blocos_do_corpo,
                           _linha(rodape, camada="rodape", confianca=confianca_rodape)))


def _liberacao(trecho="Data de liberacao: 12/04/2026"):
    return DataExame(date(2026, 4, 12), "dia", "liberacao", trecho)


def test_data_do_exame_lida_com_confianca_baixa_no_rodape_entra_na_lista(pacote):
    # O rodapé sai na limpeza, mas a data do exame foi lida nele e aparece no
    # cabeçalho de identificação e no nome do arquivo.
    corpo = (_linha("Hemoglobina 12,4 g/dL", confianca=0.99),)
    doc = _doc(15, titulo="HEMOGRAMA", data=_liberacao(), corpo=corpo,
               paginas=(_pagina_com_rodape(*corpo),))
    assert markdown.linhas_incertas(doc) == ["Data de liberacao: 12/04/2026"]
    md = markdown.md_documento(doc, pacote)
    assert "Data do exame: 12/04/2026  \n" in md
    assert md.endswith(AVISO + "\n\nLeitura incerta nestas linhas:\n"
                       "- Data de liberacao: 12/04/2026\n")
    completo = markdown.md_completo("FULANA DE TESTE", [doc], pacote)
    assert completo.endswith("Leitura incerta nestas linhas:\n"
                             "- Data de liberacao: 12/04/2026\n\n---\n")


def test_data_do_exame_lida_com_confianca_alta_nao_entra():
    corpo = (_linha("Hemoglobina 12,4 g/dL", confianca=0.99),)
    doc = _doc(16, data=_liberacao(), corpo=corpo,
               paginas=(_pagina_com_rodape(*corpo, confianca_rodape=0.95),))
    assert markdown.linhas_incertas(doc) == []


def test_data_do_exame_com_rotulo_na_linha_de_cima():
    # "Data da coleta:" numa linha e a data abrindo a linha seguinte.
    pagina = Pagina(SHA, 0, (_linha("Data da coleta:", camada="cabecalho", confianca=0.99),
                             _linha("- 12/04/2026", camada="cabecalho", confianca=0.40),
                             _linha("Resultado 7,2", confianca=0.99)))
    doc = _doc(17, data=DataExame(date(2026, 4, 12), "dia", "realizacao",
                                  "Data da coleta: 12/04/2026"),
               corpo=(_linha("Resultado 7,2", confianca=0.99),), paginas=(pagina,))
    assert markdown.linhas_incertas(doc) == ["- 12/04/2026"]


def test_data_do_exame_em_tabela_lista_a_linha_da_tabela():
    tabela = Tabela(celulas=(("Data da coleta", "Material"), ("12/04/2026", "Sangue")),
                    confiancas=((0.99, 0.99), (0.40, 0.99)), caixa=CAIXA)
    lado = Tabela(celulas=(("Coleta", "12/04/2026"),), confiancas=((0.99, 0.40),), caixa=CAIXA)
    acima = _doc(18, data=DataExame(date(2026, 4, 12), "dia", "realizacao",
                                    "Data da coleta 12/04/2026"),
                 paginas=(Pagina(SHA, 0, (tabela,)),))
    ao_lado = _doc(19, data=DataExame(date(2026, 4, 12), "dia", "realizacao",
                                      "Coleta | 12/04/2026"),
                   paginas=(Pagina(SHA, 0, (lado,)),))
    assert markdown.linhas_incertas(acima) == ["12/04/2026 | Sangue"]
    assert markdown.linhas_incertas(ao_lado) == ["Coleta | 12/04/2026"]


def test_linha_vizinha_da_data_nao_vira_leitura_da_data():
    # A data e o rótulo estão inteiros na linha de cima, lida com confiança
    # alta; a linha de baixo, incerta e com número, está no rodapé.
    pagina = Pagina(SHA, 0, (_linha("Data de liberacao: 12/04/2026", camada="rodape"),
                             _linha("Pag 1/1", camada="rodape", confianca=0.30)))
    doc = _doc(20, data=_liberacao(), paginas=(pagina,))
    assert markdown.linhas_incertas(doc) == []


def test_data_do_exame_sem_leitura_achada_conta_como_incerta():
    doc = _doc(21, data=_liberacao(), corpo=(_linha("Texto", confianca=0.99),))
    assert markdown.linhas_incertas(doc) == ["Data de liberacao: 12/04/2026"]


def test_data_do_exame_do_fluxo_real_lida_no_rodape(pacote):
    # Com as etapas de verdade: a data sai do rodapé e o rodapé sai na limpeza.
    from scan_to_text.entender import datas, limpar

    completo = pacote_mod.carregar("pt-BR")
    pagina = Pagina(SHA, 0, (
        Linha("HEMOGRAMA", (0.1, 0.05, 0.9, 0.08), tipo="titulo"),
        Linha("Hemoglobina 12,4 g/dL", (0.1, 0.3, 0.9, 0.32), confianca=0.99),
        Linha("Data de liberacao: 12/04/2026", (0.1, 0.95, 0.9, 0.97), camada="rodape",
              confianca=0.40)))
    doc = Documento(id="x-01", paginas=(pagina,), titulo="HEMOGRAMA",
                    paciente="FULANA DE TESTE", categoria=3)
    doc = dataclasses.replace(doc, data=datas.data_do_exame(doc, completo, date(2026, 10, 1)))
    doc = limpar.limpar(doc, completo)
    assert doc.data.valor == date(2026, 4, 12)
    assert all("liberacao" not in getattr(b, "texto", "") for b in doc.corpo)
    assert markdown.linhas_incertas(doc) == ["Data de liberacao: 12/04/2026"]


def test_digito_de_outra_escrita_conta():
    doc = _doc(13, corpo=(_linha("١٢٣", confianca=0.3),))
    assert markdown.linhas_incertas(doc) == ["١٢٣"]


# ---------- COMPLETO.md ----------

def _documentos_do_paciente():
    encaminhamento = _doc(1, titulo="ENCAMINHAMENTO", instituicao="Clínica Gama",
                          data=_dia(2026, 5, 10), categoria=1,
                          corpo=(_linha("Encaminho a paciente para avaliação.", grupo=0),))
    mamografia = _doc(2, titulo="MAMOGRAFIA BILATERAL", instituicao="Clínica de Imagem Beta",
                      data=_dia(2026, 4, 2), categoria=2,
                      corpo=(_linha("BI-RADS: 2", grupo=0),))
    ultrassom = _doc(3, titulo="ULTRASSONOGRAFIA", categoria=2,
                     corpo=(_linha("Exame sem alterações.", grupo=0),))
    tomografia = _doc(4, titulo="TOMOGRAFIA", data=DataExame(date(2026, 3, 1), "mes"),
                      categoria=2, corpo=(_linha("Sem achados.", grupo=0),))
    return [ultrassom, _hemograma(), mamografia, encaminhamento, tomografia]


def test_golden_completo(pacote):
    esperado = "\n".join([
        "# FULANA DE TESTE",
        "",
        AVISO,
        "",
        "## Índice",
        "",
        "**1 RELATÓRIOS MÉDICOS E ENCAMINHAMENTOS**",
        "- 2026-05-10 — ENCAMINHAMENTO",
        "",
        "**2 EXAMES DE IMAGEM E ONCOLÓGICOS**",
        "- 2026-03 — TOMOGRAFIA",
        "- 2026-04-02 — MAMOGRAFIA BILATERAL",
        "- SEM DATA — ULTRASSONOGRAFIA",
        "",
        "**3 LABORATÓRIO**",
        "- 2026-05-10 — HEMOGRAMA",
        "",
        "---",
        "",
        "## 1 RELATÓRIOS MÉDICOS E ENCAMINHAMENTOS",
        "",
        "### ENCAMINHAMENTO — 2026-05-10",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 10/05/2026  ",
        "Instituição: Clínica Gama  ",
        "",
        "Encaminho a paciente para avaliação.  ",
        "",
        "---",
        "",
        "## 2 EXAMES DE IMAGEM E ONCOLÓGICOS",
        "",
        "### TOMOGRAFIA — 2026-03",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 03/2026  ",
        "",
        "Sem achados.  ",
        "",
        "---",
        "",
        "### MAMOGRAFIA BILATERAL — 2026-04-02",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 02/04/2026  ",
        "Instituição: Clínica de Imagem Beta  ",
        "",
        "BI-RADS: 2  ",
        "",
        "---",
        "",
        "### ULTRASSONOGRAFIA — SEM DATA",
        "",
        "Paciente: FULANA DE TESTE  ",
        "",
        "Exame sem alterações.  ",
        "",
        "---",
        "",
        "## 3 LABORATÓRIO",
        "",
        "### HEMOGRAMA — 2026-05-10",
        "",
        "Paciente: FULANA DE TESTE  ",
        "Data do exame: 10/05/2026  ",
        "Instituição: Laboratório Alfa  ",
        "",
        "| Exame | Resultado | Unidade |",
        "| --- | --- | --- |",
        "| Hemoglobina | 124 | g/dL |",
        "| Leucócitos | 6.500 | /mm3 |",
        "",
        "Leitura incerta nestas linhas:",
        "- Hemoglobina | 124 | g/dL",
        "",
        "---",
        "",
    ])
    assert markdown.md_completo("FULANA DE TESTE", _documentos_do_paciente(), pacote) == esperado


def test_completo_nao_depende_da_ordem_de_entrada(pacote):
    docs = _documentos_do_paciente()
    uma = markdown.md_completo("FULANA DE TESTE", docs, pacote)
    assert markdown.md_completo("FULANA DE TESTE", list(reversed(docs)), pacote) == uma
    assert markdown.md_completo("FULANA DE TESTE", docs, pacote) == uma


def test_completo_aviso_uma_vez_e_titulos_descem_de_nivel(pacote):
    md = markdown.md_completo("FULANA DE TESTE", _documentos_do_paciente(), pacote)
    assert md.count("DADOS NUMÉRICOS ISOLADOS NÃO SÃO CONFIÁVEIS") == 1
    assert [l for l in md.splitlines() if l.startswith("# ")] == ["# FULANA DE TESTE"]
    assert md.count("\n### ") == 5


def test_completo_transcreve_repetido_uma_vez_so(pacote):
    original = _hemograma(5)
    copia = dataclasses.replace(_hemograma(6), repetido_de=original.id)
    md = markdown.md_completo("FULANA DE TESTE", [original, copia], pacote)
    assert md.count("### HEMOGRAMA") == 1


def test_completo_transcreve_repetido_cujo_original_nao_veio(pacote):
    # Cópia que chegou noutro arquivo ou noutro dia, sem o original na lista:
    # sumir com ela do COMPLETO.md perderia o exame.
    copia = dataclasses.replace(_hemograma(6), repetido_de="a-de-outro-arquivo")
    md = markdown.md_completo("FULANA DE TESTE", [copia], pacote)
    assert md.count("### HEMOGRAMA") == 1
    assert "| Hemoglobina | 124 | g/dL |" in md
    assert "**3 LABORATÓRIO**\n- 2026-05-10 — HEMOGRAMA" in md


def test_completo_sem_nome_de_arquivo_nem_escape(pacote):
    md = markdown.md_completo("FULANA DE TESTE", _documentos_do_paciente(), pacote)
    assert SHA[:12] not in md and "\\_" not in md and "\\*" not in md
    assert date.today().isoformat() not in md


# ---------- idioma vem do pacote ----------

def test_pacote_de_outro_idioma_renderiza_sem_mexer_no_codigo(tmp_path):
    (tmp_path / "xx").mkdir()
    (tmp_path / "xx" / "textos.yaml").write_text("\n".join([
        "pastas:",
        "  categorias: {1: C1, 2: C2, 3: C3, 4: C4, 5: C5}",
        "  desconhecido: UNK",
        "  originais: ORIG",
        "  mais_de_um_paciente: _MIX",
        "arquivos: {completo: ALL.md, leia_me: README.txt, paginas_deste_paciente: (own pages)}",
        "documento: {sem_data: NODATE, sem_titulo: DOC}",
        "markdown:",
        "  categorias: {1: K1, 2: K2, 3: K3, 4: K4, 5: K5}",
        "  paciente: Pt",
        "  data_exame: Dt",
        "  instituicao: Inst",
        "  campo: '{rotulo} = {valor}'",
        "  formato_data: {dia: '%Y.%m.%d', mes: '%Y.%m', ano: '%Y'}",
        "  aviso: NUMBERS MAY BE WRONG",
        "  leitura_incerta: 'Uncertain:'",
        "  indice: Index",
        "  separador: ' / '",
        "leia_me: {senha: password, corrompido: broken}",
        "",
    ]), encoding="utf-8")
    xx = pacote_mod.carregar("xx", raiz=tmp_path)
    md = markdown.md_documento(_hemograma(), xx)
    assert "Pt = FULANA DE TESTE  \nDt = 2026.05.10  \nInst = Laboratório Alfa  " in md
    assert md.endswith("## NUMBERS MAY BE WRONG\n\nUncertain:\n- Hemoglobina | 124 | g/dL\n")
    completo = markdown.md_completo("FULANA DE TESTE", [_hemograma()], xx)
    assert "## Index\n\n**K3**\n- 2026-05-10 / HEMOGRAMA" in completo
    for pt in ("Paciente", "Data do exame", "Índice", "DADOS", "LABORAT"):
        assert pt not in md and pt not in completo
