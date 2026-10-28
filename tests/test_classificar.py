"""Categoria do documento. Nomes inventados."""
import re
from pathlib import Path

import pytest
import yaml

from scan_to_text.entender.classificar import classificar, e_titulo_de_documento
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, Linha, Pagina, Tabela

PACK = Path(__file__).resolve().parents[1] / "app" / "packs" / "pt-BR"
PACOTE = Pacote("pt-BR", {"categorias": yaml.safe_load(
    (PACK / "categorias.yaml").read_text(encoding="utf-8-sig"))})


def _doc(titulo: str = "", linhas=(), titulos=(), secoes=(), instituicao=None,
         paginas_extras=()) -> Documento:
    """`titulo` é o que a segmentação achou; `titulos` e `secoes` são linhas da 1ª página."""
    caixa = (0.05, 0.1, 0.95, 0.12)
    blocos = ([Linha(t, caixa, tipo="titulo") for t in titulos]
              + [Linha(t, caixa, tipo="secao") for t in secoes]
              + [Linha(t, caixa) if isinstance(t, str) else t for t in linhas])
    paginas = (Pagina("cd" * 32, 0, tuple(blocos)),) + tuple(
        Pagina("cd" * 32, i + 1, tuple(Linha(t, caixa) for t in extra))
        for i, extra in enumerate(paginas_extras))
    return Documento(id="cdcdcdcdcdcd-01", paginas=paginas, titulo=titulo,
                     instituicao=instituicao)


def _categoria(*args, **kwargs) -> int:
    return classificar(_doc(*args, **kwargs), PACOTE)[0]


@pytest.mark.parametrize("titulo, categoria", [
    # 1: relatórios, encaminhamentos e tratamentos anteriores
    ("RELATÓRIO MÉDICO", 1),
    ("Relatório", 1),
    ("ENCAMINHAMENTO", 1),
    ("CARTA DE ENCAMINHAMENTO", 1),
    ("ENCAMINHAMENTO PARA TOMOGRAFIA", 1),
    ("PARECER", 1),
    ("EVOLUÇÃO MÉDICA", 1),
    ("RESUMO DE RADIOTERAPIA", 1),
    ("RELATÓRIO DE QUIMIOTERAPIA", 1),
    ("DESCRIÇÃO CIRÚRGICA", 1),
    ("DESCRIÇÃO CIRÚRGICA - BIÓPSIA DE MAMA", 1),
    ("SUMÁRIO DE ALTA", 1),
    ("RESUMO DE ALTA HOSPITALAR", 1),
    # 2: imagem e oncológicos, com PET, endoscopia e colonoscopia
    ("PET-CT ONCOLÓGICO", 2),
    ("PET/CT COM FDG-18F", 2),
    ("PET-PSMA", 2),
    ("PET PSMA COM GÁLIO-68", 2),
    ("ENDOSCOPIA DIGESTIVA ALTA", 2),
    ("COLONOSCOPIA", 2),
    ("MAMOGRAFIA BILATERAL", 2),
    ("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX", 2),
    ("TC DE ABDOME", 2),
    ("RESSONÂNCIA MAGNÉTICA DA PELVE", 2),
    ("RM DE CRÂNIO", 2),
    ("ULTRASSONOGRAFIA DE ABDOME TOTAL", 2),
    ("ECOGRAFIA MAMÁRIA", 2),
    ("DOPPLER DE CARÓTIDAS", 2),
    ("CINTILOGRAFIA ÓSSEA", 2),
    ("DENSITOMETRIA ÓSSEA", 2),
    ("RX DE TÓRAX", 2),
    ("RELATÓRIO DE ULTRASSONOGRAFIA", 2),
    # 3: laboratório
    ("HEMOGRAMA COMPLETO", 3),
    ("Hemograma", 3),
    ("PSA TOTAL E LIVRE", 3),
    ("CEA", 3),
    ("CA 19-9", 3),
    ("CA-125", 3),
    ("CA 15-3", 3),
    ("UREIA E CREATININA", 3),
    ("COAGULOGRAMA", 3),
    ("SOROLOGIA PARA HEPATITE B", 3),
    ("URINA TIPO 1", 3),
    ("TSH E T4 LIVRE", 3),
    ("MARCADORES TUMORAIS", 3),
    # 4: anatomopatológico, IHQ e genética
    ("EXAME ANATOMOPATOLÓGICO", 4),
    ("ANATOMOPATOLOGICO", 4),
    ("LAUDO HISTOPATOLÓGICO", 4),
    ("IMUNO-HISTOQUÍMICA", 4),
    ("IMUNOHISTOQUIMICA", 4),
    ("LAUDO DE IHQ", 4),
    ("PAINEL NGS DE TUMORES SÓLIDOS", 4),
    ("PESQUISA DE MUTAÇÕES BRCA1 E BRCA2", 4),
    ("BRCA", 4),
    ("HER2 POR FISH", 4),
    ("CITOLOGIA ONCÓTICA", 4),
    ("BIÓPSIA DE PRÓSTATA", 4),
    ("TESTE MOLECULAR EGFR", 4),
    # 5: outros
    ("ECOCARDIOGRAMA", 5),
    ("ECOCARDIOGRAMA TRANSTORÁCICO", 5),
    ("Ecocardiograma com Doppler", 5),
    ("ECODOPPLERCARDIOGRAMA", 5),
    ("ELETROCARDIOGRAMA", 5),
    ("ESPIROMETRIA", 5),
    ("ELETRONEUROMIOGRAFIA", 5),
    ("RECEITUÁRIO", 5),
    ("ATESTADO MÉDICO", 5),
    ("TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO", 5),
])
def test_categoria_pelo_titulo(titulo, categoria):
    assert _categoria(titulo) == categoria, titulo


@pytest.mark.parametrize("titulo, categoria", [
    # endoscopia com biópsia é o laudo do endoscopista; o do patologista vem nomeado
    ("COLONOSCOPIA COM BIÓPSIA", 2),
    ("ENDOSCOPIA DIGESTIVA ALTA COM BIÓPSIA", 2),
    ("EDA COM BIÓPSIA", 2),
    ("ANATOMOPATOLÓGICO - BIÓPSIA DE CÓLON POR COLONOSCOPIA", 4),
    ("HISTOPATOLÓGICO DE BIÓPSIA GÁSTRICA (ENDOSCOPIA)", 4),
    # o tratamento citado no título de um exame não faz dele resumo de tratamento
    ("TOMOGRAFIA PARA PLANEJAMENTO DE RADIOTERAPIA", 2),
    ("PET-CT PÓS-QUIMIOTERAPIA", 2),
    ("RESSONÂNCIA MAGNÉTICA PÓS-RADIOTERAPIA", 2),
    ("MAMOGRAFIA PÓS-QUIMIOTERAPIA", 2),
    ("PD-L1 PARA IMUNOTERAPIA", 4),
    ("HEMOGRAMA PRÉ-QUIMIOTERAPIA", 3),
    ("RESUMO DE TRATAMENTO RADIOTERÁPICO", 1),
    ("RESUMO DA RADIOTERAPIA", 1),
    ("QUIMIOTERAPIA - CICLO 3", 1),
    # "Patologia Clínica" é o laboratório de análises clínicas
    ("PATOLOGIA CLÍNICA - HEMOGRAMA COMPLETO", 3),
    ("HEMOGRAMA - PATOLOGIA CLÍNICA", 3),
    ("PATOLOGIA CIRÚRGICA", 4),
])
def test_exame_que_cita_tratamento_biopsia_ou_patologia(titulo, categoria):
    assert _categoria(titulo) == categoria, titulo


def test_linha_de_patologia_clinica_nao_faz_o_hemograma_virar_anatomopatologico():
    doc = _doc("", titulos=["SERVIÇO DE PATOLOGIA CLÍNICA"], secoes=["HEMOGRAMA COMPLETO"],
               instituicao="LABORATÓRIO EXEMPLO")
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio")


@pytest.mark.parametrize("titulo", [
    "PEDIDO DE EXAME - TOMOGRAFIA DE TÓRAX",
    "Pedido de exames: hemograma e PSA",
    "SOLICITAÇÃO DE EXAMES - RESSONÂNCIA MAGNÉTICA",
    "SOLICITACAO DE PET-CT",
    "REQUISIÇÃO DE EXAME ANATOMOPATOLÓGICO",
    "GUIA DE SP/SADT - MAMOGRAFIA BILATERAL",
    "GUIA TISS - COLONOSCOPIA",
    "GUIA DE SOLICITAÇÃO DE INTERNAÇÃO",
    "SOLICITAÇÃO DE RADIOTERAPIA",
    "AUTORIZAÇÃO DE PROCEDIMENTO - PET-CT",
])
def test_pedido_solicitacao_e_guia_vao_para_5_mesmo_com_nome_de_exame(titulo):
    categoria, motivo = classificar(_doc(titulo), PACOTE)
    assert categoria == 5, titulo
    assert motivo in ("regra:pedido", "regra:guia", "regra:autorizacao"), motivo


def test_guia_reconhecida_pelo_corpo_mesmo_com_titulo_de_exame():
    doc = _doc("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX", linhas=[
        "GUIA DE SERVIÇO PROFISSIONAL / SERVIÇO AUXILIAR DE DIAGNÓSTICO E TERAPIA - SP/SADT",
        "Registro ANS: 000000",
        "Indicação clínica: estadiamento"])
    assert classificar(doc, PACOTE) == (5, "regra:guia")


def test_pedido_reconhecido_pelo_corpo_mesmo_com_titulo_de_exame():
    doc = _doc("HEMOGRAMA COMPLETO", linhas=[
        "Solicito a realização dos seguintes exames:", "Hemograma completo", "PSA total"])
    assert classificar(doc, PACOTE) == (5, "regra:pedido")
    doc = _doc("RESSONÂNCIA MAGNÉTICA", paginas_extras=[["Pedido de exame emitido pela clínica"]])
    assert classificar(doc, PACOTE) == (5, "regra:pedido")


@pytest.mark.parametrize("titulo, linhas", [
    ("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX",
     ["Solicito:", "Tomografia computadorizada de tórax com contraste",
      "Indicação clínica: estadiamento"]),
    ("HEMOGRAMA", ["Solicito os seguintes exames:", "Hemograma", "PSA total"]),
    ("PSA TOTAL", ["Ao laboratório, solicito PSA total e livre."]),
    ("MAMOGRAFIA BILATERAL", ["Peço a realização de: Mamografia bilateral"]),
    ("PET-CT COM 18F-FDG", ["Solicitamos a realização de PET-CT para estadiamento."]),
    ("BIÓPSIA DE PRÓSTATA", ["Solicito biópsia de próstata guiada por ultrassom."]),
])
def test_pedido_em_formato_de_receituario_vai_para_5(titulo, linhas):
    # sem a palavra "pedido": só o verbo de quem pede
    assert classificar(_doc(titulo, linhas=linhas), PACOTE) == (5, "regra:pedido_receituario")
    assert classificar(_doc("", secoes=[titulo], linhas=linhas), PACOTE) == \
        (5, "regra:pedido_receituario")


def test_encaminhamento_que_solicita_avaliacao_continua_encaminhamento():
    # o verbo de quem pede só tira o documento das categorias de resultado
    doc = _doc("ENCAMINHAMENTO", linhas=["Prezado colega,",
                                         "Solicito avaliação e conduta em radioterapia."])
    assert classificar(doc, PACOTE) == (1, "regra:relatorio")


def test_resultado_com_numero_do_pedido_e_solicitante_continua_resultado():
    doc = _doc("HEMOGRAMA COMPLETO", linhas=[
        "Nº do pedido: 123456", "Médico solicitante: Dr. Beltrano Exemplar CRM 00000",
        "Guia: 987654", "Convênio: Plano Exemplo", "Data da solicitação: 01/04/2026"])
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio")
    doc = _doc("MAMOGRAFIA BILATERAL", linhas=["Pedido: 55555", "Solicitante: Dra. Fulana Teste"])
    assert classificar(doc, PACOTE) == (2, "regra:imagem")
    doc = _doc("PSA TOTAL", linhas=["Exame solicitado por: Dr. Beltrano Exemplar",
                                    "Solicitação nº 4321", "Médica solicitante: Dra. Fulana Teste"])
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio")


@pytest.mark.parametrize("secao", ["DADOS DA SOLICITAÇÃO", "Nº DO PEDIDO", "DADOS DA AUTORIZAÇÃO"])
def test_quadro_de_solicitacao_no_resultado_com_titulo_continua_resultado(secao):
    doc = _doc("HEMOGRAMA COMPLETO", secoes=[secao, "ERITROGRAMA"])
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio")


def test_linha_de_titulo_que_abre_com_pedido_vale_mesmo_com_titulo_de_exame():
    doc = _doc("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX", titulos=["SOLICITAÇÃO DE PET-CT"])
    assert classificar(doc, PACOTE) == (5, "regra:pedido")
    # sem título que decida, o termo vale em qualquer posição da linha
    doc = _doc("", titulos=["TOMOGRAFIA COMPUTADORIZADA DE TÓRAX", "FORMULÁRIO DE SOLICITAÇÃO"])
    assert classificar(doc, PACOTE) == (5, "regra:pedido")


def test_titulo_da_segmentacao_vale_antes_das_outras_linhas_de_titulo():
    assert _categoria("TOMOGRAFIA DE ABDOME", secoes=["HISTÓRICO CLÍNICO", "TÉCNICA"]) == 2
    assert _categoria("RELATÓRIO MÉDICO", secoes=["EXAMES: TOMOGRAFIA E PSA"]) == 1


def test_sem_titulo_usa_as_linhas_de_titulo_da_primeira_pagina():
    assert _categoria("", titulos=["MAMOGRAFIA BILATERAL"]) == 2
    assert _categoria("", secoes=["HEMOGRAMA"]) == 3


def test_linha_da_instituicao_nao_decide_a_categoria():
    instituicao = "LABORATÓRIO EXEMPLO DE PATOLOGIA E ANÁLISES CLÍNICAS"
    doc = _doc(instituicao, titulos=[instituicao], secoes=["HEMOGRAMA COMPLETO"],
               instituicao=instituicao)
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio")


def test_estrutura_de_laboratorio_sem_titulo():
    tabela = Tabela(celulas=(("Exame", "Resultado", "Unidade", "Valores de referência"),
                             ("Hemoglobina", "12,4", "g/dL", "12,0 a 16,0")),
                    confiancas=((1.0,) * 4, (1.0,) * 4), caixa=(0.05, 0.3, 0.95, 0.5),
                    linhas_de_cabecalho=1)
    doc = _doc("", linhas=["Material: sangue", tabela])
    assert classificar(doc, PACOTE) == (3, "regra:laboratorio_estrutura")


def test_estrutura_de_anatomopatologico_sem_titulo():
    doc = _doc("", linhas=["Macroscopia: fragmento irregular de 1,2 cm.",
                           "Microscopia: proliferação glandular atípica."])
    assert classificar(doc, PACOTE) == (4, "regra:anatomopatologico_estrutura")


def test_na_duvida_vai_para_5():
    assert classificar(_doc("", linhas=["Paciente com dor há 2 meses."]), PACOTE) == \
        (5, "regra:duvida")
    assert classificar(_doc("LAUDO"), PACOTE) == (5, "regra:duvida")
    assert classificar(Documento(id="cdcdcdcdcdcd-02", paginas=()), PACOTE) == \
        (5, "regra:duvida")


def test_relatorio_que_cita_exames_no_corpo_continua_relatorio():
    doc = _doc("RELATÓRIO MÉDICO", linhas=[
        "Paciente com PSA de 8,5 e biópsia com adenocarcinoma.",
        "Tomografia sem metástases. Valores de referência do laboratório anexos."])
    assert classificar(doc, PACOTE) == (1, "regra:relatorio")


def test_pacote_bem_formado():
    secao = PACOTE["categorias"]
    ids = []
    for grupo in ("exclusoes_titulo", "exclusoes_texto", "regras", "estrutura"):
        for regra in secao[grupo]:
            ids.append((grupo, regra["id"]))
            assert re.fullmatch(r"[a-z_]+", regra["id"]), regra["id"]
            assert regra.get("termos") or regra.get("todos"), regra["id"]
            # YAML lê no, yes, on e off sem aspas como booleano: todo termo tem de ser texto
            for termos in [regra.get("termos") or [], *(regra.get("todos") or [])]:
                assert all(isinstance(t, str) and t.strip() for t in termos), regra["id"]
            if grupo in ("regras", "estrutura"):
                assert regra["categoria"] in (1, 2, 3, 4, 5), regra["id"]
            # `tira_de` só existe nas marcas do corpo, e nunca inclui o próprio 5
            if "tira_de" in regra:
                assert grupo == "exclusoes_texto", regra["id"]
                assert regra["tira_de"] and set(regra["tira_de"]) <= {1, 2, 3, 4}, regra["id"]
    assert len(ids) == len(set(ids))


def test_categorias_vem_do_pacote():
    xx = Pacote("xx", {"categorias": {
        "exclusoes_titulo": [{"id": "zzorder", "termos": ["zzorder"]}],
        "regras": [{"id": "zzlab", "categoria": 3, "termos": ["zzblood count"]}],
    }})
    assert classificar(_doc("ZZBLOOD  COUNT"), xx) == (3, "regra:zzlab")
    assert classificar(_doc("ZZORDER ZZBLOOD COUNT"), xx) == (5, "regra:zzorder")
    assert classificar(_doc("HEMOGRAMA"), xx) == (5, "regra:duvida")


@pytest.mark.parametrize("titulo, esperado", [
    ("TOMOGRAFIA COMPUTADORIZADA", True),
    ("ULTRASSONOGRAFIA", True),
    ("RECEITUÁRIO", True),
    ("RELATÓRIO MÉDICO", True),
    ("SOLICITAÇÃO DE EXAMES", True),
    ("Conduta", False),
    ("MICROSCOPIA", False),
    ("Impressão", False),
    ("Exames complementares", False),
    ("CONCLUSÃO", False),
    ("", False),
])
def test_titulo_de_documento_pelas_regras_do_pacote(titulo, esperado):
    assert e_titulo_de_documento(titulo, PACOTE) is esperado


def test_titulo_de_documento_vem_do_pacote():
    xx = Pacote("xx", {"categorias": {"regras": [{"id": "zzlab", "categoria": 3, "termos": ["zzblood"]}]}})
    assert e_titulo_de_documento("ZZBLOOD COUNT", xx)
    assert not e_titulo_de_documento("TOMOGRAFIA", xx)
    assert not e_titulo_de_documento("TOMOGRAFIA", Pacote("xx", {}))
