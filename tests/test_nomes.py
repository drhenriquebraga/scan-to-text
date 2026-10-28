"""Nomes de pasta e de arquivo.

Os casos marcados como porte vêm de `PastaPacienteTest.kt` do photoid-rt
(`app/src/test/java/com/radioterapia/ai/util/PastaPacienteTest.kt`,
Apache-2.0). Os nomes foram trocados por nomes inventados. O photoid-rt põe o
prontuário depois do nome; aqui vai a data do primeiro processamento, e os
casos de casamento com prontuário continuam valendo porque `casa_pasta` é
porte de `pastaCasaPaciente`. O caso da pasta legada sem apóstrofo foi
invertido: a chave solta saiu do porte (ver `casa_pasta`). Os testes de `pastasDoPaciente`,
`nomeDaPasta` e `prontuarioDaPasta` não entram: essas funções tratam da
reirradiação do photoid-rt e não existem aqui.
"""
import unicodedata
from datetime import date
from pathlib import Path

import pytest
import yaml

from scan_to_text.escrever import nomes
from scan_to_text.pacote import RAIZ_PACKS, Pacote
from scan_to_text.tipos import DataExame

DIA = date(2026, 9, 27)


@pytest.fixture(scope="module")
def pacote() -> Pacote:
    # Só o textos.yaml: os outros YAML do pacote não interessam a estes testes.
    arquivo = RAIZ_PACKS / "pt-BR" / "textos.yaml"
    return Pacote("pt-BR", {"textos": yaml.safe_load(arquivo.read_text(encoding="utf-8-sig"))})


# ---------- porte de PastaPacienteTest.kt: nome da pasta ----------

def test_pasta_leva_nome_e_data():
    assert nomes.pasta_paciente("Maria Exemplo", DIA) == "MARIA EXEMPLO - 2026-09-27"


def test_nome_sem_complemento_fica_so_normalizado():
    assert nomes.normalizar("Maria Exemplo") == "MARIA EXEMPLO"


def test_acento_sai_do_nome_da_pasta():
    assert (nomes.pasta_paciente("João Exemplo Conceição", DIA)
            == "JOAO EXEMPLO CONCEICAO - 2026-09-27")


def test_apostrofo_e_hifen_sobrevivem_no_nome_da_pasta():
    assert (nomes.pasta_paciente("Maria D'Arc Exemplo-Teste", DIA)
            == "MARIA D'ARC EXEMPLO-TESTE - 2026-09-27")


# ---------- porte de PastaPacienteTest.kt: casamento pasta x paciente ----------

def test_casa_a_pasta_do_proprio_paciente():
    assert nomes.casa_pasta("MARIA EXEMPLO - 123", "Maria Exemplo")
    assert nomes.casa_pasta("MARIA EXEMPLO - 2026-09-27", "Maria Exemplo")


def test_casa_pasta_antiga_com_separador_underscore():
    assert nomes.casa_pasta("MARIA EXEMPLO_123", "Maria Exemplo")


def test_pasta_sem_apostrofo_e_outra_chave():
    # Divergência do porte: o photoid-rt aceitava a chave só com A-Z e 0-9 para
    # achar pasta antiga gravada sem apóstrofo. Aqui não há pasta antiga, e a
    # chave tem de ser idêntica.
    assert not nomes.casa_pasta("MARIA DARC - 5", "Maria D'Arc")
    assert not nomes.casa_pasta(nomes.pasta_paciente("Maria Darc", DIA), "Maria D'Arc")


def test_nome_que_e_prefixo_de_outro_nao_casa():
    assert not nomes.casa_pasta("ANA MARIA - 999", "Ana")
    assert not nomes.casa_pasta("ANA MARIA - 2026-09-27", "Ana")


def test_homonimas_nao_compartilham_pasta():
    assert (nomes.pasta_paciente("Maria Exemplo", date(2026, 9, 27))
            != nomes.pasta_paciente("Maria Exemplo", date(2026, 9, 28)))
    mesma_data = nomes.pasta_paciente("Maria Exemplo", DIA)
    assert nomes.com_sufixo(mesma_data, {mesma_data}) == "MARIA EXEMPLO - 2026-09-27 (2)"


def test_pasta_casa_com_prontuario_alfanumerico():
    assert nomes.casa_pasta("MARIA EXEMPLO - RT2024", "Maria Exemplo")
    assert nomes.casa_pasta("MARIA EXEMPLO - RT-2024-001", "Maria Exemplo")
    assert nomes.casa_pasta("MARIA EXEMPLO - A1234", "Maria Exemplo")


def test_pasta_com_prontuario_numerico_continua_casando():
    assert nomes.casa_pasta("MARIA EXEMPLO - 123456", "Maria Exemplo")
    assert nomes.casa_pasta("MARIA EXEMPLO_123456", "Maria Exemplo")
    assert nomes.casa_pasta("MARIA EXEMPLO", "Maria Exemplo")


def test_nome_mais_longo_continua_sendo_outro_paciente():
    assert not nomes.casa_pasta("MARIA EXEMPLO SANTOS - 99", "Maria Exemplo")
    assert not nomes.casa_pasta("MARIA EXEMPLO SANTOS", "Maria Exemplo")
    assert not nomes.casa_pasta("JOAO EXEMPLO - RT2024", "Maria Exemplo")


# ---------- casamento com as pastas deste app ----------

def test_casa_a_pasta_gerada_por_pasta_paciente():
    pasta = nomes.pasta_paciente("Fulana de Teste", DIA)
    assert nomes.casa_pasta(pasta, "FULANA DE TESTE")
    assert nomes.casa_pasta(pasta + " (2)", "Fulana de Teste")


def test_casa_ignora_formatacao_invisivel_no_nome():
    pasta = nomes.pasta_paciente("Fulana de Teste", DIA)
    assert nomes.casa_pasta(pasta, "Fulana\u200b de Teste")


def test_nome_vazio_nunca_casa():
    assert not nomes.casa_pasta("", "")
    assert not nomes.casa_pasta(" - 123", "")
    assert not nomes.casa_pasta("MARIA EXEMPLO - 123", "   ")


@pytest.mark.parametrize("da_pasta, do_paciente", [
    ("王 SILVA", "李 SILVA"),
    ("ИВАН PETROV", "ОЛЕГ PETROV"),
    ("محمد SILVA", "علي SILVA"),
    ("ZOFIA WĄS", "ZOFIA WĘS"),
    ("ÝLVA EXEMPLO", "ŸLVA EXEMPLO"),
    ("NGUYỄN THỊ HẠNH", "NGUYỄN THỊ HẰNH"),
    ("ANA PAULA EXEMPLO", "ANAPAULA EXEMPLO"),
    ("ANAPAULA EXEMPLO", "ANA PAULA EXEMPLO"),
    ("MARIA EXEMPLO-TESTE", "MARIA EXEMPLOTESTE"),
])
def test_nomes_de_chave_diferente_nunca_casam(da_pasta, do_paciente):
    # Mesmo esqueleto ASCII, chave diferente: outra pessoa.
    assert nomes.casa_pasta(nomes.pasta_paciente(da_pasta, DIA), da_pasta)
    assert not nomes.casa_pasta(nomes.pasta_paciente(da_pasta, DIA), do_paciente)
    assert not nomes.casa_pasta(nomes.pasta_paciente(da_pasta, DIA) + " (2)", do_paciente)
    assert not nomes.casa_pasta(da_pasta, do_paciente)


def test_pasta_desconhecido_nao_casa_com_paciente(pacote):
    pasta = nomes.pasta_desconhecido(DIA, "scan007.pdf", pacote)
    assert not nomes.casa_pasta(pasta, "Fulana de Teste")


# ---------- porte exato de removerAcentosMaiusculas ----------

def test_mapa_de_acentos_identico_ao_do_photoid():
    com_acentos = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑáàâãäéèêëíìîïóòôõöúùûüçñ"
    sem_acentos = "AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn"
    assert nomes.normalizar(com_acentos) == sem_acentos.upper()


def test_caracteres_ilegais_e_controles_viram_espaco():
    assert (nomes.normalizar('a\\b/c:d*e?f"g<h>i|j\x00k\x1fl')
            == "A B C D E F G H I J K L")


def test_espacos_repetidos_colapsam_e_pontas_saem():
    assert nomes.normalizar("  fulana \t de\n\n teste  ") == "FULANA DE TESTE"


def test_ponto_e_espaco_do_fim_saem():
    assert nomes.normalizar("Fulana de Teste. ") == "FULANA DE TESTE"
    assert nomes.normalizar("Fulana de Teste . . .") == "FULANA DE TESTE"


def test_letra_latina_fora_do_mapa_so_vira_maiuscula():
    assert nomes.normalizar("ýlva øster") == "ÝLVA ØSTER"


def test_escrita_nao_latina_fica_na_escrita_original():
    assert nomes.normalizar("王小明") == "王小明"
    assert nomes.normalizar("محمد  علي") == "محمد علي"


# ---------- regras de Windows ----------

def test_caracteres_de_formatacao_saem():
    assert nomes.normalizar("Fulana\u202e de\u200b Teste\u2066") == "FULANA DE TESTE"
    assert nomes.normalizar("Fu\u200dla\u00adna de Tes\ufeffte") == "FULANA DE TESTE"
    assert not any(unicodedata.category(c) == "Cf"
                   for c in nomes.normalizar("\u200e\u200fA\u2060B C\u061cD"))


def test_forma_decomposta_vira_a_mesma_pasta():
    decomposto = "Joa\u0303o Exemplo"
    assert nomes.normalizar(decomposto) == nomes.normalizar("João Exemplo") == "JOAO EXEMPLO"
    angstrom = nomes.normalizar("A\u030angela Exemplo")
    assert angstrom == nomes.normalizar("\u00c5ngela Exemplo")
    assert unicodedata.is_normalized("NFC", angstrom)


def test_espaco_unicode_colapsa():
    assert nomes.normalizar("Fulana\u00a0de\u3000\u2003Teste") == "FULANA DE TESTE"


@pytest.mark.parametrize("entrada, esperado", [
    ("con", "CON_"), ("PRN", "PRN_"), ("aux", "AUX_"), ("Nul", "NUL_"),
    ("COM1", "COM1_"), ("com9", "COM9_"), ("LPT1", "LPT1_"), ("lpt9", "LPT9_"),
    ("COM0", "COM0_"), ("LPT0", "LPT0_"),
    ("COM\u00b9", "COM\u00b9_"), ("com\u00b2", "COM\u00b2_"), ("LPT\u00b3", "LPT\u00b3_"),
    ("con.", "CON_"), ("NUL.txt", "NUL_.TXT"), ("aux .log", "AUX_ .LOG"),
])
def test_nome_reservado_ganha_sublinhado(entrada, esperado):
    assert nomes.normalizar(entrada) == esperado


@pytest.mark.parametrize("entrada", ["CONSOLE", "COM10", "LPT", "ICON", "CON X", "NULO", "AUXILIO"])
def test_nome_parecido_com_reservado_fica(entrada):
    assert nomes.normalizar(entrada) == entrada


def test_limite_de_80_em_fronteira_de_palavra():
    original = " ".join(["FULANA"] + ["TESTE"] * 20)
    cortado = nomes.normalizar(original)
    assert len(cortado) <= 80
    assert original.startswith(cortado)
    assert original[len(cortado)] == " "
    assert cortado.endswith("TESTE")
    assert len(cortado) > 80 - len(" TESTE")


def test_palavra_unica_longa_corta_em_80():
    assert nomes.normalizar("A" * 100) == "A" * 80


def test_corte_nao_deixa_ponto_nem_espaco_no_fim():
    cortado = nomes.normalizar("ABCD. " * 20)
    assert len(cortado) <= 80
    assert not cortado.endswith((".", " "))


def test_normalizar_e_idempotente():
    for entrada in ("João D'Arc  Exemplo.", "con", "NUL.txt", "A" * 100,
                    " ".join(["FULANA"] + ["TESTE"] * 20), "Fu\u200bla\u0303na", "COM\u00b9"):
        uma = nomes.normalizar(entrada)
        assert nomes.normalizar(uma) == uma, entrada


def test_nome_vazio_nao_vira_pasta():
    with pytest.raises(ValueError):
        nomes.pasta_paciente(" ... ", DIA)


# ---------- caminho completo ----------

def test_cabe_no_caminho_conta_ate_240():
    base = "C:\\DESTINO"
    assert nomes.cabe_no_caminho(base, "X" * (240 - len(base) - 1))
    assert not nomes.cabe_no_caminho(base, "X" * (240 - len(base)))


def test_cabe_no_caminho_conta_os_separadores():
    base = "C:\\DESTINO"
    partes = ("A" * 100, "B" * 100)
    total = len(base) + 1 + 100 + 1 + 100
    assert nomes.cabe_no_caminho(base, *partes, limite=total)
    assert not nomes.cabe_no_caminho(base, *partes, limite=total - 1)


def test_cabe_no_caminho_unc_e_limite_proprio():
    base = "\\\\servidor\\scanner"
    assert nomes.cabe_no_caminho(base, "PASTA", limite=len(base) + 6)
    assert not nomes.cabe_no_caminho(base, "PASTA", limite=len(base) + 5)


def test_cabe_no_caminho_conta_em_unidades_do_windows():
    # Caractere fora do plano básico ocupa duas unidades UTF-16 no caminho.
    base = "C:\\D"
    assert nomes.cabe_no_caminho(base, "\U00020000", limite=len(base) + 3)
    assert not nomes.cabe_no_caminho(base, "\U00020000", limite=len(base) + 2)


# ---------- DESCONHECIDO ----------

def test_pasta_desconhecido(pacote):
    assert (nomes.pasta_desconhecido(DIA, "scan007.pdf", pacote)
            == "DESCONHECIDO - 2026-09-27 - SCAN007")


def test_pasta_desconhecido_normaliza_o_nome_do_arquivo(pacote):
    assert (nomes.pasta_desconhecido(DIA, "relatório  final.PDF", pacote)
            == "DESCONHECIDO - 2026-09-27 - RELATORIO FINAL")
    assert (nomes.pasta_desconhecido(DIA, "C:\\scanner\\lote\\scan.001.pdf", pacote)
            == "DESCONHECIDO - 2026-09-27 - SCAN.001")
    assert (nomes.pasta_desconhecido(DIA, "lote/scan008.jpg", pacote)
            == "DESCONHECIDO - 2026-09-27 - SCAN008")


def test_pasta_desconhecido_sem_nome_utilizavel(pacote):
    assert nomes.pasta_desconhecido(DIA, "???.pdf", pacote) == "DESCONHECIDO - 2026-09-27"


@pytest.mark.parametrize("primeiro, segundo", [
    ("\\\\srv\\a\\scan001.pdf", "\\\\srv\\b\\scan001.jpg"),
    ("lote1/scan001.pdf", "lote2/SCAN001.pdf"),
    ("???.pdf", "***.pdf"),
])
def test_dois_arquivos_do_mesmo_dia_com_o_mesmo_nome_base_nao_dividem_pasta(pacote, primeiro,
                                                                            segundo):
    # Scanner que reinicia a numeração e varredura de subpastas: o nome do
    # arquivo sozinho não separa os dois; quem chama passa as pastas
    # que já existem no destino.
    pasta_um = nomes.pasta_desconhecido(DIA, primeiro, pacote)
    pasta_dois = nomes.pasta_desconhecido(DIA, segundo, pacote, existentes={pasta_um})
    assert pasta_dois.casefold() != pasta_um.casefold()
    assert pasta_dois == pasta_um + " (2)"
    tres = nomes.pasta_desconhecido(DIA, segundo, pacote,
                                    existentes=[pasta_um.lower(), pasta_dois])
    assert tres == pasta_um + " (3)"


def test_pasta_desconhecido_sem_colisao_fica_igual(pacote):
    outra = nomes.pasta_desconhecido(DIA, "scan002.pdf", pacote)
    assert (nomes.pasta_desconhecido(DIA, "scan001.pdf", pacote, existentes={outra})
            == "DESCONHECIDO - 2026-09-27 - SCAN001")


def test_pasta_desconhecido_nunca_tem_menor_e_maior(pacote):
    pasta = nomes.pasta_desconhecido(DIA, "<desconhecido>.pdf", pacote)
    assert "<" not in pasta and ">" not in pasta


# ---------- categorias ----------

def test_pastas_das_cinco_categorias(pacote):
    assert [nomes.pasta_categoria(c, pacote) for c in range(1, 6)] == [
        "1 RELATORIOS MEDICOS E ENCAMINHAMENTOS",
        "2 EXAMES DE IMAGEM E ONCOLOGICOS",
        "3 LABORATORIO",
        "4 ANATOMOPATOLOGICO IHQ E GENETICA",
        "5 OUTROS",
    ]


@pytest.mark.parametrize("categoria", [0, 6, -1])
def test_categoria_fora_de_1_a_5_e_erro(pacote, categoria):
    with pytest.raises(ValueError):
        nomes.pasta_categoria(categoria, pacote)


# ---------- nome do documento ----------

def test_nome_documento_por_precisao(pacote):
    titulo = "Mamografia Bilateral"
    assert (nomes.nome_documento(DataExame(date(2026, 4, 2), "dia"), titulo, pacote)
            == "2026-04-02 - MAMOGRAFIA BILATERAL")
    assert (nomes.nome_documento(DataExame(date(2026, 4, 1), "mes"), titulo, pacote)
            == "2026-04 - MAMOGRAFIA BILATERAL")
    assert (nomes.nome_documento(DataExame(date(2026, 1, 1), "ano"), titulo, pacote)
            == "2026 - MAMOGRAFIA BILATERAL")
    assert nomes.nome_documento(DataExame(), titulo, pacote) == "SEM DATA - MAMOGRAFIA BILATERAL"


def test_nome_documento_sem_titulo(pacote):
    data = DataExame(date(2026, 4, 2), "dia")
    assert nomes.nome_documento(data, "", pacote) == "2026-04-02 - DOCUMENTO"
    assert nomes.nome_documento(data, " ?? ", pacote) == "2026-04-02 - DOCUMENTO"


def test_nome_documento_titulo_normalizado_ate_80(pacote):
    titulo = "Ultrassonografia: " + " ".join(["transvaginal"] * 12)
    nome = nomes.nome_documento(DataExame(date(2026, 4, 2), "dia"), titulo, pacote)
    prefixo = "2026-04-02 - "
    assert nome.startswith(prefixo + "ULTRASSONOGRAFIA TRANSVAGINAL")
    assert len(nome) - len(prefixo) <= 80
    assert ":" not in nome and not nome.endswith(" ")


def test_nome_documento_precisao_desconhecida_e_erro(pacote):
    with pytest.raises(ValueError):
        nomes.nome_documento(DataExame(date(2026, 4, 2), "semana"), "X", pacote)


def test_prefixo_sem_data_ordena_depois_das_datas(pacote):
    prefixos = [nomes.prefixo_data(DataExame(date(2026, 4, 2), "dia"), pacote),
                nomes.prefixo_data(DataExame(), pacote)]
    assert sorted(prefixos) == prefixos


# ---------- colisão de nomes ----------

def test_com_sufixo():
    assert nomes.com_sufixo("2026-04-02 - HEMOGRAMA", []) == "2026-04-02 - HEMOGRAMA"
    assert (nomes.com_sufixo("2026-04-02 - HEMOGRAMA", {"2026-04-02 - HEMOGRAMA"})
            == "2026-04-02 - HEMOGRAMA (2)")
    assert (nomes.com_sufixo("2026-04-02 - HEMOGRAMA",
                             ["2026-04-02 - HEMOGRAMA", "2026-04-02 - HEMOGRAMA (2)"])
            == "2026-04-02 - HEMOGRAMA (3)")


def test_com_sufixo_ignora_maiusculas_como_o_windows():
    assert nomes.com_sufixo("SEM DATA - DOCUMENTO", {"sem data - documento"}) == "SEM DATA - DOCUMENTO (2)"


# ---------- textos de saída no pacote ----------

def test_textos_do_pacote_pt_br(pacote):
    t = pacote["textos"]
    assert t["pastas"]["desconhecido"] == "DESCONHECIDO"
    assert t["pastas"]["originais"] == "ORIGINAIS"
    assert t["pastas"]["mais_de_um_paciente"] == "_ARQUIVOS COM MAIS DE UM PACIENTE"
    assert t["arquivos"]["completo"] == "COMPLETO.md"
    assert t["arquivos"]["leia_me"] == "LEIA-ME.txt"
    assert t["arquivos"]["paginas_deste_paciente"] == "(paginas deste paciente)"
    assert t["documento"]["sem_data"] == "SEM DATA"
    assert t["documento"]["sem_titulo"] == "DOCUMENTO"
    md = t["markdown"]
    assert (md["paciente"], md["data_exame"], md["instituicao"]) == (
        "Paciente", "Data do exame", "Instituição")
    assert md["aviso"] == "DADOS NUMÉRICOS ISOLADOS NÃO SÃO CONFIÁVEIS"
    assert md["leitura_incerta"] == "Leitura incerta nestas linhas:"
    assert md["indice"] == "Índice"
    assert [md["categorias"][c] for c in range(1, 6)] == [
        "1 RELATÓRIOS MÉDICOS E ENCAMINHAMENTOS",
        "2 EXAMES DE IMAGEM E ONCOLÓGICOS",
        "3 LABORATÓRIO",
        "4 ANATOMOPATOLÓGICO IHQ E GENÉTICA",
        "5 OUTROS",
    ]
    assert t["leia_me"]["senha"] == "protegido por senha"
    assert t["leia_me"]["corrompido"] == "arquivo corrompido"


def _valores_possiveis(expr) -> set[str]:
    import ast

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _valores_possiveis(expr.body) | _valores_possiveis(expr.orelse)
    raise AssertionError(f"motivo de Ilegivel que o teste não sabe ler: {ast.dump(expr)}")


def _codigos_de_ilegivel() -> set[str]:
    """Todo código que o código de leitura passa a `Ilegivel(...)`."""
    import ast

    from scan_to_text import ler

    codigos = set()
    for arquivo in Path(ler.__file__).parent.glob("*.py"):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                    and no.func.id == "Ilegivel"):
                codigos |= {v for arg in no.args for v in _valores_possiveis(arg)}
    return codigos


def test_todo_motivo_de_arquivo_ilegivel_tem_texto_no_leia_me(pacote):
    codigos = _codigos_de_ilegivel()
    assert {"senha", "corrompido", "zip_grande"} <= codigos
    faltam = codigos - set(pacote["textos"]["leia_me"])
    assert not faltam, faltam
    assert all(pacote["textos"]["leia_me"][c].strip() for c in codigos)


def test_nomes_de_pasta_do_pacote_sao_nomes_validos(pacote):
    t = pacote["textos"]
    for nome in [*t["pastas"]["categorias"].values(), t["pastas"]["desconhecido"],
                 t["pastas"]["originais"], t["pastas"]["mais_de_um_paciente"],
                 t["documento"]["sem_data"], t["documento"]["sem_titulo"]]:
        assert nome == nomes.normalizar(nome), nome
