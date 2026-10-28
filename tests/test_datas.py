"""Data do exame. Nomes e datas inventados."""
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from scan_to_text.entender.datas import data_do_exame, datas_no_texto
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import DataExame, Documento, Linha, Pagina, Tabela

PACK = Path(__file__).resolve().parents[1] / "app" / "packs" / "pt-BR"
PACOTE = Pacote("pt-BR", {"datas": yaml.safe_load(
    (PACK / "datas.yaml").read_text(encoding="utf-8-sig"))})
HOJE = date(2026, 9, 28)


def _linha(texto: str, camada: str = "corpo", tipo: str = "texto", grupo: int = -1) -> Linha:
    return Linha(texto, (0.05, 0.5, 0.95, 0.52), camada=camada, tipo=tipo, grupo=grupo)


def _tabela(*linhas: tuple[str, ...]) -> Tabela:
    return Tabela(celulas=tuple(linhas),
                  confiancas=tuple(tuple(1.0 for _ in linha) for linha in linhas),
                  caixa=(0.05, 0.3, 0.95, 0.6), linhas_de_cabecalho=1)


def _doc(*paginas) -> Documento:
    """Cada argumento é a lista de blocos de uma página; texto solto vira Linha do corpo."""
    return Documento(id="abababababab-01", paginas=tuple(
        Pagina("ab" * 32, i, tuple(_linha(b) if isinstance(b, str) else b for b in blocos))
        for i, blocos in enumerate(paginas)))


def _data(*paginas, nascimento=None, hoje=HOJE) -> DataExame:
    return data_do_exame(_doc(*paginas), PACOTE, hoje, nascimento)


# ---------------------------------------------------------------- dia e mês

FORMATOS = (
    lambda d: f"{d.day:02d}/{d.month:02d}/{d.year}",
    lambda d: f"{d.day}/{d.month}/{d.year}",
    lambda d: f"{d.day:02d}.{d.month:02d}.{d.year}",
    lambda d: f"{d.day:02d}-{d.month:02d}-{d.year}",
    lambda d: f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}",
)


def _ambiguas() -> list[date]:
    """Dia e mês os dois até 12 e diferentes: a troca daria outra data válida."""
    return [date(ano, mes, dia) for ano in (2019, 2023, 2025, 2026)
            for dia in range(1, 13) for mes in range(1, 13)
            if dia != mes and date(ano, mes, dia) <= HOJE]


def test_duzentas_datas_ambiguas_nunca_trocam_dia_e_mes():
    casos = _ambiguas()
    assert len(casos) >= 200
    for i, esperado in enumerate(casos):
        escrito = FORMATOS[i % len(FORMATOS)](esperado)
        achadas = datas_no_texto(f"Data da coleta: {escrito}", PACOTE)
        assert [(valor, precisao) for valor, precisao, _, _ in achadas] == [(esperado, "dia")], escrito
        assert _data([f"Data da coleta: {escrito}"]).valor == esperado, escrito


def test_duas_datas_na_mesma_linha_seguem_a_mesma_ordem():
    achadas = datas_no_texto("Data da coleta: 03/02/2026  Liberado em: 05/02/2026", PACOTE)
    assert [valor for valor, _, _, _ in achadas] == [date(2026, 2, 3), date(2026, 2, 5)]


def test_coleta_com_hora_colada_vence_a_liberacao():
    assert _data(["Coleta: 06/01/202513:03", "Liberado em: 11/01/2025"]) == \
        DataExame(date(2025, 1, 6), "dia", "realizacao", "Coleta: 06/01/2025")


@pytest.mark.parametrize("escrito, data_literal", [
    ("06/01/202500:00", "06/01/2025"), ("06/01/202523:59", "06/01/2025"),
    ("06/01/202513:03:59", "06/01/2025"), ("6/1/202513:03", "6/1/2025"),
    ("06.01.202513:03", "06.01.2025"), ("06-01-202513:03", "06-01-2025"),
])
def test_data_com_hora_colada_preserva_valor_e_intervalo_original(escrito, data_literal):
    texto = "Coleta: " + escrito
    [achada] = datas_no_texto(texto, PACOTE)
    valor, precisao, inicio, fim = achada
    assert (valor, precisao) == (date(2025, 1, 6), "dia")
    assert texto[inicio:fim] == data_literal
    assert _data([texto]).valor == date(2025, 1, 6)


@pytest.mark.parametrize("escrito", [
    "06/01/202512345", "06/01/202525:99", "06/01/202524:00", "06/01/202513:60",
    "06/01/202513:030", "06/01/202513:03:99", "06/01/202513:03:300",
    "06/01/202513:03:", "06/01/202513:03x", "06/01/202513:03.5",
    "06/01/202513:03,5", "06/01/202513:03/123", "06/01/202513:03-123",
    "31/02/202513:03", "04/15/202513:03", "00/01/202513:03",
])
def test_hora_invalida_ou_numero_colado_nao_libera_prefixo_de_data(escrito):
    assert datas_no_texto("Coleta: " + escrito, PACOTE) == []
    assert _data(["Coleta: " + escrito]) == DataExame()


@pytest.mark.parametrize("rotulo", ["Nascimento", "Impresso em", "Retornar para coleta em"])
def test_data_com_hora_colada_respeita_exclusoes(rotulo):
    assert _data([f"{rotulo}: 06/01/202513:03"]) == DataExame()
    assert _data([f"{rotulo}: 06/01/202513:03", "Liberado em: 11/01/2025"]).valor == \
        date(2025, 1, 11)


def test_data_com_hora_colada_respeita_plausibilidade():
    assert _data(["Coleta: 02/10/202613:03"]) == DataExame()
    assert _data(["Coleta: 06/01/189913:03"]) == DataExame()
    assert _data(["Coleta: 06/01/202513:03"], nascimento=date(2025, 1, 6)) == DataExame()


def test_mes_depois_do_dia_nunca_e_reinterpretado():
    # 04/15/aaaa só seria data lida como mês/dia; o padrão pt-BR não inverte
    assert datas_no_texto("Data do exame: 04/15/2026", PACOTE) == []
    assert _data(["Data do exame: 04/15/2026"]) == DataExame()


@pytest.mark.parametrize("escrito, valor, precisao", [
    ("02/04/2026", date(2026, 4, 2), "dia"),
    ("2/4/2026", date(2026, 4, 2), "dia"),
    ("02.04.2026", date(2026, 4, 2), "dia"),
    ("02-04-2026", date(2026, 4, 2), "dia"),
    ("02/04/26", date(2026, 4, 2), "dia"),
    ("15/08/98", date(1998, 8, 15), "dia"),
    ("2026-04-02", date(2026, 4, 2), "dia"),
    ("5 de março de 2026", date(2026, 3, 5), "dia"),
    ("05 de Março de 2026", date(2026, 3, 5), "dia"),
    ("5 DE MARCO DE 2026", date(2026, 3, 5), "dia"),
    ("1º de abril de 2026", date(2026, 4, 1), "dia"),
    ("05/MAR/2026", date(2026, 3, 5), "dia"),
    ("12 set 2025", date(2025, 9, 12), "dia"),
    ("mar/2026", date(2026, 3, 1), "mes"),
    ("Março de 2026", date(2026, 3, 1), "mes"),
    ("fev. 2026", date(2026, 2, 1), "mes"),
    ("03/2026", date(2026, 3, 1), "mes"),
    ("2025", date(2025, 1, 1), "ano"),
])
def test_formatos(escrito, valor, precisao):
    texto = f"Realizado em {escrito}."
    achadas = datas_no_texto(texto, PACOTE)
    assert [(v, p) for v, p, _, _ in achadas] == [(valor, precisao)]
    _, _, inicio, fim = achadas[0]
    assert texto[inicio:fim] == escrito


def test_posicoes_apontam_para_o_texto_original():
    texto = "Coleta: 02/04/2026; laudo em 5 de março de 2026 e revisão em mar/2026."
    achadas = datas_no_texto(texto, PACOTE)
    assert [texto[i:f] for _, _, i, f in achadas] == [
        "02/04/2026", "5 de março de 2026", "mar/2026"]


@pytest.mark.parametrize("texto", [
    "há 3 dias", "Paciente refere dor há 2 meses", "ontem", "hoje pela manhã",
    "Hemoglobina 12,4 g/dL", "Leucócitos 6.800 /mm3", "Plaquetas 250.000",
    "tomar 1/2 comprimido de 12/12 horas", "FAN 1/1280", "31/02/2026", "CEP 01310-100",
])
def test_nao_e_data(texto):
    assert datas_no_texto(texto, PACOTE) == []


# ------------------------------------------------------------ data do exame

def test_sem_data_devolve_data_exame_vazia():
    assert _data(["Hemoglobina 12,4 g/dL", "Paciente refere dor há 3 dias."]) == DataExame()
    assert data_do_exame(Documento(id="abababababab-01", paginas=()), PACOTE, HOJE) == DataExame()


def test_data_sem_rotulo_nao_vira_data_do_exame():
    assert _data(["Tomografia de 10/01/2025 sem alterações."]) == DataExame()
    assert _data(["02/04/2026"]) == DataExame()


def test_rotulo_longe_da_data_nao_vale():
    # entre o rótulo e a data só cabem pontuação e conectores
    assert _data(["Data da internação: 01/03/2026"]) == DataExame()
    assert _data(["Coleta enviada ao laboratório de apoio, resultado de 10/01/2025"]) == DataExame()
    assert _data(["Atendimento 123456 02/04/2026"]) == DataExame()
    assert _data(["Realizado no dia 02/04/2026"]).valor == date(2026, 4, 2)
    assert _data(["Data da coleta ........ 02/04/2026"]).valor == date(2026, 4, 2)


def test_rotulo_papel_precisao_e_trecho():
    assert _data(["Paciente: FULANA DE TESTE", "Data da coleta: 02/04/2026 às 08:30"]) == \
        DataExame(date(2026, 4, 2), "dia", "realizacao", "Data da coleta: 02/04/2026")


@pytest.mark.parametrize("rotulo", [
    "Data de nascimento", "Nascimento", "Nasc.", "DN", "D.N.", "Dt. Nasc.",
    "Impresso em", "Data de impressão", "Gerado em",
    "Validade", "Válido até", "Vencimento",
    "Retorno", "Data do retorno", "Agendado para", "Próxima consulta",
    "Próximo atendimento", "Data do próximo atendimento", "Próxima coleta",
    "Data da próxima coleta", "Data do próximo exame", "Retornar para coleta em",
    "DUM", "D.U.M.", "Data da última menstruação",
    "Exame anterior", "Comparação com exame de", "Comparado a estudo de",
    "Data do exame anterior", "Coleta anterior",
])
def test_data_excluida_nunca_vira_data_do_exame(rotulo):
    assert _data([f"{rotulo}: 10/01/2025"]) == DataExame()
    assert _data(["Data do exame: 02/04/2026", f"{rotulo}: 10/01/2025"]).valor == date(2026, 4, 2)
    assert _data([f"{rotulo}: 10/01/2025", _linha("Data do exame: 02/04/2026", "cabecalho")]
                 ).valor == date(2026, 4, 2)


def test_nascimento_ao_lado_do_exame_na_mesma_linha():
    linha = "Paciente: FULANA DE TESTE   DN: 01/01/1960   Data da coleta: 02/04/2026"
    assert _data([linha]).valor == date(2026, 4, 2)
    linha = "Data da coleta: 02/04/2026   Data de nascimento: 01/01/1960 (66 anos)"
    assert _data([linha]).valor == date(2026, 4, 2)


def test_secao_de_comparacao_nao_da_data():
    # o rodapé tem papel mais fraco que a data da seção: se ela valesse, venceria
    comparacao = [_linha("COMPARAÇÃO", tipo="secao"),
                  "Tomografia realizada em 10/01/2025, com nódulo de 8 mm.",
                  _linha("CONCLUSÃO", tipo="secao"),
                  "Redução do nódulo.",
                  _linha("Liberado em 05/04/2026", "rodape")]
    assert _data(comparacao) == \
        DataExame(date(2026, 4, 5), "dia", "liberacao", "Liberado em 05/04/2026")
    assert _data([_linha("Exames anteriores:", tipo="secao"),
                  "Realizado em 10/01/2025."]) == DataExame()
    # a seção termina no título seguinte, e a data depois dele volta a valer
    assert _data(["Comparação: TC de 10/01/2025.",
                  _linha("Conclusão:", tipo="texto"),
                  "Liberado em 05/04/2026"]).valor == date(2026, 4, 5)


@pytest.mark.parametrize("frase", [
    "Em comparação com a TC realizada em 10/01/2025, houve redução.",
    "Comparado com a TC realizada em 10/01/2025.",
    "Comparativamente ao estudo realizado em 10/01/2025, nota-se redução.",
    "Houve redução em relação ao exame anterior, realizado em 10/01/2025.",
])
def test_comparacao_no_meio_da_frase_tira_a_data_que_vem_depois(frase):
    # o rótulo de papel entre o termo de comparação e a data não devolve a data
    assert _data([_linha("Data: 02/04/2026", "cabecalho"), frase]) == \
        DataExame(date(2026, 4, 2), "dia", "generico", "Data: 02/04/2026")
    assert _data([frase, _linha("Liberado em 05/04/2026", "rodape")]).valor == date(2026, 4, 5)
    assert _data([frase]) == DataExame()


def test_comparacao_que_continua_na_linha_seguinte_do_mesmo_paragrafo():
    linhas = [_linha("Data: 02/04/2026", "cabecalho"),
              _linha("Em comparação com a tomografia computadorizada de tórax", grupo=3),
              _linha("realizada em 10/01/2025, houve redução do nódulo.", grupo=3)]
    assert _data(linhas).valor == date(2026, 4, 2)
    # a data consome a exclusão: a linha seguinte do parágrafo volta a valer
    linhas = [_linha("Comparado ao exame de 10/01/2025, houve redução.", grupo=3),
              _linha("Exame realizado em 02/04/2026.", grupo=3)]
    assert _data(linhas).valor == date(2026, 4, 2)
    # outro parágrafo não herda a exclusão
    linhas = [_linha("Sem exames anteriores para comparação.", grupo=3),
              _linha("Exame realizado em 02/04/2026.", grupo=4)]
    assert _data(linhas).valor == date(2026, 4, 2)


@pytest.mark.parametrize("frase", [
    "Próximo atendimento: 15/03/2026",
    "Data do próximo atendimento: 15/03/2026",
    "Retornar para coleta em 15/03/2026",
    "Próxima coleta: 15/03/2026",
])
def test_retorno_com_rotulo_de_realizacao_depois_nao_vira_data(frase):
    assert _data(["São Paulo, 01/03/2026", frase]) == \
        DataExame(date(2026, 3, 1), "dia", "carta", "São Paulo, 01/03/2026")


def test_prioridade_dos_papeis():
    linhas = ["Data: 01/04/2026",
              "São Paulo, 3 de abril de 2026.",
              "Liberado em: 05/04/2026",
              "Recebido em: 04/04/2026",
              "Data da coleta: 02/04/2026"]
    esperados = [(date(2026, 4, 2), "realizacao"), (date(2026, 4, 4), "recebimento"),
                 (date(2026, 4, 5), "liberacao"), (date(2026, 4, 3), "carta"),
                 (date(2026, 4, 1), "generico")]
    for n, (valor, papel) in enumerate(esperados):
        achada = _data(linhas[:len(linhas) - n])
        assert (achada.valor, achada.papel) == (valor, papel)


@pytest.mark.parametrize("linha, valor", [
    ("São Paulo, 5 de março de 2026.", date(2026, 3, 5)),
    ("Rio de Janeiro - RJ, 05/03/2026", date(2026, 3, 5)),
    ("Belo Horizonte/MG, 5 de março de 2026", date(2026, 3, 5)),
])
def test_data_da_carta(linha, valor):
    assert _data(["Prezado colega,", "Encaminho a paciente para avaliação.", linha]) == \
        DataExame(valor, "dia", "carta", linha.rstrip("."))


def test_frase_com_virgula_nao_e_data_de_carta():
    assert _data(["Paciente operada, 05/03/2026 teve alta."]) == DataExame()


def test_varias_linhas_de_carta_vence_a_ultima():
    # a lista de exames datados vem antes da linha de cidade e data da carta
    assert _data(["Prezado colega,", "Exames:", "Mamografia, 10/01/2025",
                  "Biópsia de mama, 12/12/2024", "São Paulo, 05/03/2026"]) == \
        DataExame(date(2026, 3, 5), "dia", "carta", "São Paulo, 05/03/2026")
    assert _data(["FULANA DE TESTE, 01/01/1960", "Encaminho a paciente para avaliação.",
                  "São Paulo, 5 de março de 2026."]).valor == date(2026, 3, 5)


def test_linha_com_o_nome_do_paciente_nao_e_data_de_carta():
    # nome e nascimento sem rótulo, e o nascimento não lido: não é a cidade da carta
    doc = replace(_doc(["Fulana de Teste, 01/01/1960", "Encaminho a paciente para avaliação."]),
                  paciente="FULANA DE TESTE")
    assert data_do_exame(doc, PACOTE, HOJE) == DataExame()


def test_ano_solto_nao_vence_data_com_dia():
    # número de atendimento de 4 dígitos lido como ano
    assert _data([_linha("Atendimento: 1987", "cabecalho"),
                  _linha("Liberado em 05/04/2026", "rodape")]) == \
        DataExame(date(2026, 4, 5), "dia", "liberacao", "Liberado em 05/04/2026")
    assert _data(["Data da cirurgia: 2019", "São Paulo, 05/03/2026"]).valor == date(2026, 3, 5)


def test_mesmo_papel_prefere_a_data_mais_precisa_e_depois_a_primeira():
    assert _data(["Data do exame: 03/2026"], ["Data do exame: 02/03/2026"]).valor == date(2026, 3, 2)
    assert _data(["Data da coleta: 02/03/2026"], ["Data da coleta: 09/03/2026"]).valor == \
        date(2026, 3, 2)


def test_precisao_de_mes_e_de_ano():
    assert _data(["Realizado em março de 2026."]) == \
        DataExame(date(2026, 3, 1), "mes", "realizacao", "Realizado em março de 2026")
    assert _data(["Data do exame: 03/2026"]).precisao == "mes"
    assert _data(["Data da cirurgia: 2019"]) == \
        DataExame(date(2019, 1, 1), "ano", "realizacao", "Data da cirurgia: 2019")


def test_plausibilidade():
    # depois do processamento, antes de 1900 e antes (ou no dia) do nascimento
    assert _data(["Data do exame: 02/10/2026"]) == DataExame()
    assert _data(["Data do exame: 10/2026"]) == DataExame()
    assert _data(["Data do exame: 09/2026"]).valor == date(2026, 9, 1)
    assert _data(["Data do exame: 02/04/1899"]) == DataExame()
    nasc = date(1960, 3, 15)
    assert _data(["Data do exame: 02/04/1959"], nascimento=nasc) == DataExame()
    assert _data(["Data do exame: 15/03/1960"], nascimento=nasc) == DataExame()
    assert _data(["Data do exame: 03/1960"], nascimento=nasc) == DataExame()
    assert _data(["Data do exame: 16/03/1960"], nascimento=nasc).valor == date(1960, 3, 16)
    # a implausível não esconde a plausível
    assert _data(["Data do exame: 02/10/2026", "Liberado em 05/04/2026"]).valor == date(2026, 4, 5)


def test_hoje_vem_por_parametro():
    assert _data(["Data do exame: 02/04/2026"], hoje=date(2026, 4, 1)) == DataExame()
    assert _data(["Data do exame: 02/04/2026"], hoje=date(2026, 4, 2)).valor == date(2026, 4, 2)


def test_rotulo_na_linha_de_cima():
    assert _data(["Data da coleta:", "02/04/2026"]) == \
        DataExame(date(2026, 4, 2), "dia", "realizacao", "Data da coleta: 02/04/2026")
    assert _data(["Data de nascimento:", "01/01/1960"]) == DataExame()
    # a linha de cima só vale quando é só o rótulo
    assert _data(["Paciente FULANA DE TESTE coleta", "02/04/2026"]) == DataExame()
    # dois rótulos em cima e duas datas embaixo: não dá para saber qual é qual
    assert _data(["Data da coleta:     Data de liberação:", "02/04/2026     05/04/2026"]) == \
        DataExame()


def test_tabela_com_rotulo_no_cabecalho_ou_ao_lado():
    assert _data([_tabela(("Data da coleta", "Data de liberação"),
                          ("02/04/2026", "05/04/2026"))]).valor == date(2026, 4, 2)
    assert _data([_tabela(("Nascimento", "Liberação"),
                          ("01/01/1960", "05/04/2026"))]).valor == date(2026, 4, 5)
    assert _data([_tabela(("Coleta", "02/04/2026"),)]).valor == date(2026, 4, 2)
    assert _data([_tabela(("Exame", "Resultado", "Data da coleta"),
                          ("Hemoglobina", "12,4", "02/04/2026"))]).valor == date(2026, 4, 2)
    assert _data([_tabela(("Exame", "10/01/2026", "02/04/2026"),
                          ("PSA", "4,1", "3,2"))]) == DataExame()


def test_cabecalho_e_rodape_sao_lidos():
    assert _data([_linha("Data da coleta: 02/04/2026", "cabecalho"),
                  "Hemoglobina 12,4 g/dL",
                  _linha("Impresso em 10/04/2026 11:02", "rodape")]).valor == date(2026, 4, 2)
    assert _data(["Hemoglobina 12,4 g/dL",
                  _linha("Impresso em 10/04/2026 11:02", "rodape")]) == DataExame()


# ---------------------------------------------------- o idioma mora no pacote

def test_pacote_bem_formado():
    secao = PACOTE["datas"]
    # YAML lê no, yes, on e off sem aspas como booleano: todo termo tem de ser texto
    listas = [secao["conectores"], secao["secoes_de_comparacao"], secao["fim_de_secao"],
              list(secao["meses"]), *secao["papeis"].values(), *secao["excluidos"].values()]
    for lista in listas:
        assert lista and all(isinstance(termo, str) and termo.strip() for termo in lista), lista
    assert all(isinstance(n, int) and 1 <= n <= 12 for n in secao["meses"].values())
    assert set(secao["prioridade"]) == set(secao["papeis"]) | {"carta"}
    assert all(f["precisao"] in ("dia", "mes", "ano") for f in secao["formatos"])


XX = Pacote("xx", {"datas": {
    "meses": {"zzmar": 3},
    "ano_curto_pivo": 70,
    "formatos": [{"precisao": "dia", "regex": r"(?P<m>\d{2})/(?P<d>\d{2})/(?P<a>\d{4})"}],
    "papeis": {"feito": ["zzdone"]},
    "prioridade": ["feito", "carta"],
    "excluidos": {"nasc": ["zzborn"]},
}})


def test_ordem_e_rotulos_vem_do_pacote():
    doc = _doc(["zzborn 02/01/1960", "zzdone: 04/15/2026"])
    assert data_do_exame(doc, XX, HOJE) == \
        DataExame(date(2026, 4, 15), "dia", "feito", "zzdone: 04/15/2026")
