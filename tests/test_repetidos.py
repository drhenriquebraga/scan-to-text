import dataclasses
from datetime import date

import pytest

from scan_to_text.entender.repetidos import mesmo_exame
from scan_to_text.pacote import Pacote, carregar
from scan_to_text.tipos import DataExame, Documento, Linha, Pagina, Tabela

PACOTE = carregar("pt-BR")

CORPO = [
    "Material: sangue total",
    "Metodo: automatizado por citometria de fluxo",
    "Hemoglobina 12,4 g/dL",
    "Hematocrito 38,1 %",
    "Leucocitos 5.400 /mm3",
    "Plaquetas 250.000 /mm3",
    "Conclusao: hemograma dentro dos limites esperados para a idade",
    "Laudo liberado eletronicamente pelo responsavel tecnico",
]


def exame(n, corpo=CORPO, titulo="HEMOGRAMA COMPLETO", dia=date(2026, 4, 2), inst="LABORATORIO ALFA",
          paciente="FULANA DE TESTE", cabecalho=("Paciente: FULANA DE TESTE", "Convenio: Plano Exemplo"),
          tabela=None):
    blocos = tuple(Linha(t, (0.1, 0.2 + 0.05 * i, 0.9, 0.22 + 0.05 * i)) for i, t in enumerate(corpo))
    if tabela:
        blocos += (Tabela(celulas=tabela, confiancas=(), caixa=(0.1, 0.7, 0.9, 0.8)),)
    pagina = Pagina(f"{n:02d}" * 32, 0, blocos)
    return Documento(id=f"doc-{n}", paginas=(pagina,), titulo=titulo, instituicao=inst, paciente=paciente,
                     categoria=3, data=DataExame(dia, "dia", "coleta", "02/04/2026"),
                     cabecalho_original=cabecalho, corpo=blocos)


def test_copia_xerocada_e_repetido():
    assert mesmo_exame(exame(1), exame(2), pacote=PACOTE)


def test_ruido_de_ocr_em_letras_ainda_e_repetido():
    ruido = [t.replace("automatizado", "autornatizado").replace("esperados", "esperad0s") for t in CORPO]
    assert mesmo_exame(exame(1), exame(2, corpo=ruido), pacote=PACOTE)


def test_exame_seriado_com_um_valor_diferente_nao_e_repetido():
    outro = [t.replace("12,4", "12,9") for t in CORPO]
    assert not mesmo_exame(exame(1), exame(2, corpo=outro), pacote=PACOTE)


def test_mesmo_numero_com_outra_unidade_nao_e_repetido():
    outro = [t.replace("12,4 g/dL", "12,4 mg/dL") for t in CORPO]
    assert not mesmo_exame(exame(1), exame(2, corpo=outro), pacote=PACOTE)


def test_valor_numa_tabela_tambem_conta():
    a = exame(1, tabela=(("PSA total", "1,20", "ng/mL"),))
    b = exame(2, tabela=(("PSA total", "1,28", "ng/mL"),))
    assert not mesmo_exame(a, b, pacote=PACOTE)
    assert mesmo_exame(a, exame(3, tabela=(("PSA total", "1,20", "ng/mL"),)), pacote=PACOTE)


def test_titulo_data_ou_instituicao_diferente_nao_e_repetido():
    assert not mesmo_exame(exame(1), exame(2, titulo="HEMOGRAMA"), pacote=PACOTE)
    assert not mesmo_exame(exame(1), exame(2, dia=date(2026, 5, 2)), pacote=PACOTE)
    assert not mesmo_exame(exame(1), exame(2, inst="LABORATORIO BETA"), pacote=PACOTE)


def test_paciente_diferente_nunca_e_repetido():
    assert not mesmo_exame(exame(1), exame(2, paciente="JOHN DOE"), pacote=PACOTE)


def test_texto_bem_diferente_nao_e_repetido():
    outro = CORPO[:4] + ["Observacao: amostra lipemica, repetir a coleta em jejum de doze horas"] + CORPO[4:]
    assert not mesmo_exame(exame(1), exame(2, corpo=outro), pacote=PACOTE)


def test_reimpressao_com_outra_hora_de_impressao_e_repetido():
    a = exame(1, corpo=CORPO + ["Impresso em 10/05/2026 10:32"])
    b = exame(2, corpo=CORPO + ["Impresso em 11/05/2026 08:05"])
    assert mesmo_exame(a, b, pacote=PACOTE)


def _com_linha(linha):
    return CORPO[:6] + [linha] + CORPO[6:]


QUALITATIVOS = [
    ("anti-HIV 1/2: Negativo", "anti-HIV 1/2: Positivo"),
    ("VDRL: Nao reagente", "VDRL: Reagente"),
    ("HBsAg: Reagente", "HBsAg: Nao reagente"),
    ("Celulas atipicas: tipicas", "Celulas atipicas: atipicas"),
    ("Grupo sanguineo: A", "Grupo sanguineo: B"),
    ("Proteinas na urina: (+)", "Proteinas na urina: (-)"),
    ("Proteinas na urina: ++", "Proteinas na urina: +++"),
    ("PSA livre < 0,1 ng/mL", "PSA livre > 0,1 ng/mL"),
]


def test_resultado_qualitativo_diferente_nao_e_repetido():
    for antes, depois in QUALITATIVOS:
        assert not mesmo_exame(exame(1, corpo=_com_linha(antes)), exame(2, corpo=_com_linha(depois)), pacote=PACOTE), depois


def test_laudo_retificado_com_a_mesma_data_nao_e_repetido():
    retificado = ["LAUDO RETIFICADO"] + CORPO
    assert not mesmo_exame(exame(1), exame(2, corpo=retificado), pacote=PACOTE)
    assert not mesmo_exame(exame(1), exame(2, corpo=_com_linha("Retificado")), pacote=PACOTE)


def test_titulacao_diferente_nao_e_repetido():
    pares = [("FAN: reagente, titulo 1:40", "FAN: reagente, titulo 1:80"),
             ("VDRL reagente 1:16", "VDRL reagente 1:32"),
             ("FAN: reagente 1/1280", "FAN: reagente 1/2560")]
    for antes, depois in pares:
        assert not mesmo_exame(exame(1, corpo=_com_linha(antes)), exame(2, corpo=_com_linha(depois)), pacote=PACOTE), depois
    assert mesmo_exame(exame(1, corpo=_com_linha(pares[0][0])), exame(2, corpo=_com_linha(pares[0][0])), pacote=PACOTE)


def test_sinal_do_numero_conta():
    a = exame(1, corpo=_com_linha("Excesso de base -1,5 mmol/L"))
    b = exame(2, corpo=_com_linha("Excesso de base +1,5 mmol/L"))
    assert not mesmo_exame(a, b, pacote=PACOTE)


def test_numero_grudado_em_letra_conta():
    a = exame(1, corpo=[t.replace("Hemoglobina 12,4", "Hemoglobina12,4") for t in CORPO])
    b = exame(2, corpo=[t.replace("Hemoglobina 12,4", "Hemoglobina12,9") for t in CORPO])
    assert not mesmo_exame(a, b, pacote=PACOTE)
    assert mesmo_exame(a, exame(3, corpo=[t.replace("Hemoglobina 12,4", "Hemoglobina12,4") for t in CORPO]), pacote=PACOTE)


def test_unidade_em_tabela_conta():
    a = exame(1, tabela=(("PSA total", "1,20", "ng/mL"),))
    b = exame(2, tabela=(("PSA total", "1,20", "mg/dL"),))
    assert not mesmo_exame(a, b, pacote=PACOTE)


def test_limiar_configuravel():
    ruido = [t.replace("automatizado", "autornatizado") for t in CORPO]
    assert not mesmo_exame(exame(1), exame(2, corpo=ruido), limiar=1.0, pacote=PACOTE)


def test_documento_ainda_nao_limpo_usa_as_paginas():
    a = dataclasses.replace(exame(1), corpo=(), cabecalho_original=())
    b = dataclasses.replace(exame(2), corpo=(), cabecalho_original=())
    assert mesmo_exame(a, b, pacote=PACOTE)
    c = dataclasses.replace(exame(3, corpo=[t.replace("5.400", "5.900") for t in CORPO]),
                            corpo=(), cabecalho_original=())
    assert not mesmo_exame(a, c, pacote=PACOTE)


def numerado(documento, rodapes):
    pagina = documento.paginas[0]
    paginas = tuple(dataclasses.replace(
        pagina, indice=i, blocos=pagina.blocos + (
            Linha(rodape, (0.85, 0.95, 0.95, 0.97), camada="rodape"),))
        for i, rodape in enumerate(rodapes))
    return dataclasses.replace(documento, paginas=paginas)


@pytest.mark.parametrize("rodape", ["Pág. 2/2", "2/2", "Pág. 1/2"])
def test_fragmentos_numerados_iguais_nao_provam_exame_repetido(rodape):
    a = numerado(exame(1, corpo=["Plaquetas 407.000 /mm3"], titulo="PLAQUETAS"), [rodape])
    b = numerado(exame(2, corpo=["Plaquetas 407.000 /mm3"], titulo="PLAQUETAS"), [rodape])
    assert not mesmo_exame(a, b, pacote=PACOTE)


def test_exames_completos_numerados_ainda_sao_repetidos():
    a = numerado(exame(1), ["Pág. 1/2", "Pág. 2/2"])
    b = numerado(exame(2), ["Pág. 1/2", "Pág. 2/2"])
    assert mesmo_exame(a, b, pacote=PACOTE)


def test_exames_completos_com_valor_diferente_nao_sao_repetidos():
    a = numerado(exame(1, corpo=["Hemoglobina 15,9 g/dL", "Plaquetas 407.000 /mm3"]),
                 ["Pág. 1/2", "Pág. 2/2"])
    b = numerado(exame(2, corpo=["Hemoglobina 15,3 g/dL", "Plaquetas 407.000 /mm3"]),
                 ["Pág. 1/2", "Pág. 2/2"])
    assert not mesmo_exame(a, b, pacote=PACOTE)


@pytest.mark.parametrize("rodapes", [
    ["Pág. 1/3", "Pág. 3/3"],
    ["Pág. 1/2", "Pág. 2/3"],
    ["Pág. 1/2", ""],
])
def test_numeracao_com_lacuna_ou_total_contraditorio_nao_prova_copia(rodapes):
    a = numerado(exame(1), rodapes)
    b = numerado(exame(2), rodapes)
    assert not mesmo_exame(a, b, pacote=PACOTE)


@pytest.mark.parametrize("rodape", ["", "Folha 2", "Pág. 1/1"])
def test_numeracao_sem_total_ou_pagina_unica_preserva_copias(rodape):
    a = numerado(exame(1), [rodape])
    b = numerado(exame(2), [rodape])
    assert mesmo_exame(a, b, pacote=PACOTE)


def test_fragmento_de_um_lado_tambem_impede_a_deduplicacao():
    completo = numerado(exame(1), ["Pág. 1/2", "Pág. 2/2"])
    fragmento = numerado(exame(2), ["Pág. 2/2"])
    assert not mesmo_exame(completo, fragmento, pacote=PACOTE)
    assert not mesmo_exame(fragmento, completo, pacote=PACOTE)


def test_numeracao_do_pacote_xx_separa_fragmentos_e_preserva_completos():
    pacote = Pacote("xx", {"paginas": {"numeracao": [
        {"padrao": r"sheet (?P<x>\d+) outof (?P<y>\d+)", "so_margem": True},
    ]}})
    a = numerado(exame(1), ["sheet 2 outof 2"])
    b = numerado(exame(2), ["sheet 2 outof 2"])
    assert not mesmo_exame(a, b, pacote=pacote)
    a = numerado(exame(1), ["sheet 1 outof 2", "sheet 2 outof 2"])
    b = numerado(exame(2), ["sheet 1 outof 2", "sheet 2 outof 2"])
    assert mesmo_exame(a, b, pacote=pacote)
