"""Identidade do paciente.

Os casos de chave, validação e rótulo de campo são portados dos testes do
photoid-rt (Apache-2.0): `StorageLocalTest.kt`,
`PastaPacienteTest.kt` e `EtiquetaParserTest.kt`. Só nomes inventados.
"""
from datetime import date
from pathlib import Path

import pytest
import yaml

from scan_to_text import pacote as pacote_mod
from scan_to_text.entender import identidade as idt
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Linha, Pagina, Tabela

ROTULOS = Path(__file__).resolve().parents[1] / "app" / "packs" / "pt-BR" / "rotulos.yaml"


@pytest.fixture(scope="module")
def pt() -> Pacote:
    # Só o arquivo deste módulo: os outros YAML do pacote ficam fora deste teste.
    return Pacote("pt-BR", {"rotulos": yaml.safe_load(ROTULOS.read_text(encoding="utf-8-sig"))})


def _pagina(*textos, indice=0, conf=1.0, camada="corpo", y0=0.05, passo=0.03) -> Pagina:
    linhas = tuple(Linha(t, (0.05, y0 + i * passo, 0.95, y0 + i * passo + 0.02),
                         camada=camada, confianca=conf)
                   for i, t in enumerate(textos))
    return Pagina(arquivo_sha256="ab" * 32, indice=indice, blocos=linhas)


def _nomes(paginas, pt, **kw) -> list[str]:
    return [c.chave for c in idt.candidatos(paginas, pt, **kw)]


# ---------- chave (porte de StorageLocal.removerAcentosMaiusculas e chaveNome) ----------

def test_mapa_de_acentos_e_o_da_rotina_vba():
    com = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑáàâãäéèêëíìîïóòôõöúùûüçñ"
    sem = "AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn"
    assert idt.chave(com) == sem.upper()


def test_chave_tira_acento_e_maiusculiza():
    assert idt.chave("João Conceição") == "JOAO CONCEICAO"
    assert idt.chave("Ângela Muñoz") == "ANGELA MUNOZ"


def test_chave_tira_caracteres_ilegais():
    for bruto in ("MARIA/SILVA", "JOSE:SANTOS", "ANA*PAULA", 'A<B>C|D?E"F'):
        assert not any(c in idt.chave(bruto) for c in '/:*<>|?"')


def test_chave_sem_ponto_ou_espaco_no_fim():
    r = idt.chave("PACIENTE JR. ")
    assert not r.endswith(".") and not r.endswith(" ")


def test_chave_normaliza_espacos_e_sublinhados():
    assert idt.chave("MARIA_DA_SILVA") == idt.chave("Maria  da   Silva") == "MARIA DA SILVA"


def test_chave_preserva_apostrofo_e_hifen():
    assert idt.chave("Maria D'Arc Silva-Souza") == "MARIA D'ARC SILVA-SOUZA"


def test_chave_nfc_e_sem_caractere_de_formatacao():
    assert idt.chave("José Exemplo") == "JOSE EXEMPLO"
    assert idt.chave("MA​RIA EXEM‮PLO") == "MARIA EXEMPLO"


def test_chave_tolera_vazio():
    assert idt.chave("") == ""


# ---------- validar (porte de SuspectNameValidator) ----------

@pytest.mark.parametrize("nome, motivo", [
    ("FULANA DE TESTE", "ok"),
    ("Maria D'Arc Silva-Souza", "ok"),
    ("ANA", "curto"),
    ("A.B.", "curto"),
    ("MARIA", "uma_palavra"),
    ("ANA M", "sobrenome_curto"),
    ("JOSE S", "sobrenome_curto"),
    ("S. JOSE", "sobrenome_curto"),
    ("M. J. S.", "so_iniciais"),
    ("FULANA 2 TESTE", "digito"),
    ("MAR1A EXEMPLO", "digito"),
    ("FULANA DE TESTE " * 6, "longo"),
    ("FULANA ### TESTE %%%", "poucas_letras"),
])
def test_validar(nome, motivo):
    assert idt.validar(nome) == (motivo == "ok", motivo)


def test_dois_nomes_raros_curtos_passam():
    # Só primeiro nome comum com sobrenome de 2 letras é suspeito.
    assert idt.validar("WANG LI") == (True, "ok")


# ---------- compativeis ----------

@pytest.mark.parametrize("a, b", [
    ("MARIA J. DA SILVA", "MARIA JOSE DA SILVA"),
    ("MARIA JOSE SILVA", "Maria José da Silva"),
    ("SILVA, Maria da", "MARIA DA SILVA"),
    ("MARIA DA SILVA", "MARIA JOSE DA SILVA"),
    ("joão conceição", "JOAO CONCEICAO"),
    ("FULANA DE SOUZA", "FULANA DE SOUSA"),
    ("MARIA D'ARC", "MARIA DARC"),
    # O OCR cola palavras vizinhas: só o espaço muda.
    ("ODETE LUCIAVALVERDINO", "ODETE LUCIA VALVERDINO"),
    ("DORIVALRAFAEL NOGUEIRAO", "DORIVAL RAFAEL NOGUEIRAO"),
    ("MARIA DASILVA", "MARIA DA SILVA"),
    ("JOSE DOSSANTOS", "JOSE DOS SANTOS"),
])
def test_compativeis(a, b):
    assert idt.compativeis(a, b)
    assert idt.compativeis(b, a)


@pytest.mark.parametrize("a, b", [
    ("ANA", "ANA MARIA"),
    ("MARIA SILVA SANTOS", "MARIA SILVA"),
    ("JOAO SILVA", "MARIA SILVA"),
    ("MARIA DA SILVEIRA", "MARIA DA SILVA"),
    ("MARIA JOSE DA SILVA", "MARIA JOANA DA SILVA"),
    ("JOAO DA SILVA", "JOAO DA SILVA FILHO"),
    ("M. DA SILVA", "MARIA DA SILVA"),
    ("MARIA DA S.", "MARIA DA SILVA"),
    ("JOHN DOE", "FULANA DE TESTE"),
    ("ODETE LUCIAVALVERDINO", "ODETE LUCIA VALVERDINA"),
    ("DORIVALRAFAEL NOGUEIRAO", "DORIVAL RAFAEL"),
    # Sem o espaço, a partícula ou a inicial vira o fim do nome anterior: são
    # outras pessoas, e não a mesma com o espaço colado pelo OCR.
    ("GIL DA SILVA", "GILDA SILVA"),
    ("NEI DE SOUZA", "NEIDE SOUZA"),
    ("ELI DA COSTA", "ELIDA COSTA"),
    ("DANIEL A. SILVA", "DANIELA SILVA"),
    ("RAFAEL A COSTA", "RAFAELA COSTA"),
    ("LUIS A. LIMA", "LUISA LIMA"),
    ("ADRIAN O SOUZA", "ADRIANO SOUZA"),
    ("MARIA DA SILVA", "MARIAD ASILVA"),
    ("MARIA DE SA", "MARIA DESA"),
])
def test_incompativeis(a, b):
    assert not idt.compativeis(a, b)
    assert not idt.compativeis(b, a)


def test_tolerancia_de_ocr_no_ultimo_sobrenome():
    assert idt.compativeis("FULANA DE SOUZA", "FULANA DE SOUSA", tolerancia_ocr=1)
    assert not idt.compativeis("FULANA DE SOUZA", "FULANA DE SOUSA", tolerancia_ocr=0)
    # Sobrenome curto não recebe tolerância: LI e LU são outras pessoas.
    assert not idt.compativeis("WANG LI", "WANG LU")


# ---------- forma_exibida ----------

def test_forma_exibida_e_a_mais_completa():
    assert idt.forma_exibida(["MARIA J DA SILVA", "MARIA JOSE DA SILVA"]) == "MARIA JOSE DA SILVA"
    assert idt.forma_exibida(["MARIA DA SILVA", "MARIA J. DA SILVA"]) == "MARIA J. DA SILVA"


def test_forma_exibida_prefere_a_forma_com_mais_palavras():
    for nomes in (["ODETE LUCIAVALVERDINO", "ODETE LUCIA VALVERDINO"],
                  ["DORIVALRAFAEL NOGUEIRAO", "DORIVAL RAFAEL NOGUEIRAO"]):
        separada = nomes[1]
        assert idt.forma_exibida(nomes) == separada
        assert idt.forma_exibida(nomes[::-1]) == separada
        assert idt.forma_exibida([nomes[0], nomes[0], separada]) == separada
    assert idt.forma_exibida(["MARIA DASILVA", "MARIA DA SILVA"]) == "MARIA DA SILVA"
    assert idt.forma_exibida(["MARIA DA SILVA", "MARIA DASILVA"]) == "MARIA DA SILVA"


def test_forma_exibida_particula_a_mais_nao_passa_por_cima_da_frequencia():
    # Mesmo número de palavras inteiras: a forma mais vista vence, e a
    # partícula só desempata.
    assert idt.forma_exibida(["MARIA SILVA", "MARIA SILVA", "MARIA DA SILVA"]) == "MARIA SILVA"
    assert idt.forma_exibida(["MARIA DA SILVA", "MARIA SILVA", "MARIA DA SILVA"]) == "MARIA DA SILVA"
    assert idt.forma_exibida(["MARIA SILVA", "MARIA DA SILVA"]) == "MARIA DA SILVA"
    # A forma descolada ganha mesmo vista menos vezes.
    nomes = ["ODETE LUCIAVALVERDINO"] * 3 + ["ODETE LUCIA VALVERDINO"]
    assert idt.forma_exibida(nomes) == "ODETE LUCIA VALVERDINO"


def test_forma_exibida_particula_colada_mais_vista_nao_vence_a_descolada():
    # "DASILVA" conta como palavra inteira e empata com "DA SILVA" no número
    # de palavras; a frequência não pode pôr a cola do OCR na pasta.
    for colada, separada in (("MARIA DASILVA", "MARIA DA SILVA"), ("JOSE DOSSANTOS", "JOSE DOS SANTOS")):
        assert idt.forma_exibida([colada, colada, separada]) == separada
        assert idt.forma_exibida([separada, colada, colada, colada]) == separada
    # Outras letras não são cola: a frequência continua decidindo.
    assert idt.forma_exibida(["MARIA SILVA", "MARIA SILVA", "MARIA DASILVA"]) == "MARIA SILVA"


def test_forma_exibida_prefere_ordem_natural():
    assert idt.forma_exibida(["SILVA, MARIA", "MARIA SILVA"]) == "MARIA SILVA"


def test_forma_exibida_so_com_sobrenome_primeiro_sai_na_ordem_natural():
    # A pasta de outro dia com "FULANA TESTE" só casa pela chave idêntica.
    assert idt.forma_exibida(["TESTE, Fulana"]) == "Fulana TESTE"
    assert idt.forma_exibida(["SILVA, Maria da"]) == "Maria da SILVA"
    assert idt.chave(idt.forma_exibida(["TESTE, Fulana"])) == idt.chave("FULANA TESTE")


def test_forma_exibida_vazia():
    assert idt.forma_exibida([]) == ""


# ---------- candidatos: rótulo explícito ----------

def test_rotulo_com_dois_pontos(pt):
    cands = idt.candidatos([_pagina("Paciente: FULANA DE TESTE", indice=3, conf=0.93)], pt)
    assert len(cands) == 1
    c = cands[0]
    assert (c.valor_impresso, c.chave, c.rotulo, c.pagina, c.confianca) == (
        "FULANA DE TESTE", "FULANA DE TESTE", "paciente", 3, 0.93)


@pytest.mark.parametrize("linha", [
    "Paciente FULANA DE TESTE",
    "PACIENTE FULANA DE TESTE",
    "Pac. FULANA DE TESTE",
    "Pcte: FULANA DE TESTE",
    "Nome do paciente FULANA DE TESTE",
    "Paciente.........: FULANA DE TESTE",
])
def test_rotulo_sem_dois_pontos_e_variantes(pt, linha):
    assert _nomes([_pagina(linha)], pt) == ["FULANA DE TESTE"]


@pytest.mark.parametrize("linha", [
    "Paciente: FULANA DE TESTE Idade: 54",
    "Paciente: FULANA DE TESTE    Convênio: PLANO EXEMPLO",
    "Paciente FULANA DE TESTE Idade 54 anos",
    "Paciente: FULANA DE TESTE Sexo: F",
    "Paciente: FULANA DE TESTE DN: 01/02/1960",
    "Paciente: FULANA DE TESTE, 54 anos",
    "Paciente: FULANA DE TESTE | Prontuário 12345",
    "Atendimento: 998877 Paciente: FULANA DE TESTE",
    # O leitor junta as células da linha com um espaço só: o vão quase nunca
    # separa, e o nome tem de acabar no rótulo.
    "Paciente: FULANA DE TESTE OS: 12345",
    "Paciente: FULANA DE TESTE O.S. 12345",
    "Paciente: FULANA DE TESTE ID: 99",
    "Paciente: FULANA DE TESTE Nº Atendimento 1",
    "Paciente: FULANA DE TESTE Dt. Coleta: 01/02/2026",
    "Paciente: FULANA DE TESTE Pront: 123",
    "Paciente: FULANA DE TESTE Reg.: 123",
    "Paciente: FULANA DE TESTE Ped.: 99",
    "Paciente: FULANA DE TESTE Req. 99",
    "Paciente: FULANA DE TESTE Conv.: PLANO EXEMPLO",
    "Paciente: FULANA DE TESTE D.Nasc.: 01/02/1960",
    "Paciente: FULANA DE TESTE Senha 1234",
    "Paciente: FULANA DE TESTE Hosp. EXEMPLO",
    # rótulo fora da lista, com dois-pontos ou abreviado antes de número
    "Paciente: FULANA DE TESTE Posto: CENTRAL",
    "Paciente: FULANA DE TESTE Cad. 123",
])
def test_valor_vai_ate_o_proximo_rotulo(pt, linha):
    assert _nomes([_pagina(linha)], pt) == ["FULANA DE TESTE"]


def test_ponto_final_encerra_o_nome(pt):
    pagina = _pagina("Encaminho a Sra. FULANA DE TESTE. Ela apresenta dor.")
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_sufixo_abreviado_do_nome_fica_no_nome(pt):
    # Pai e filho homônimos só se separam pelo sufixo: ele nunca sai do nome.
    pagina = _pagina("Paciente: JOAO EXEMPLO DA SILVA JR. 54 anos")
    assert _nomes([pagina], pt) == ["JOAO EXEMPLO DA SILVA JR"]


def test_codigos_de_rotulo(pt):
    pagina = _pagina("Beneficiário: FULANA DE TESTE", "Nome social: ALEX EXEMPLO",
                     "Nome civil: MARIA EXEMPLO", "Cliente: JOANA EXEMPLO")
    assert [(c.chave, c.rotulo) for c in idt.candidatos([pagina], pt)] == [
        ("FULANA DE TESTE", "beneficiario"), ("ALEX EXEMPLO", "nome_social"),
        ("MARIA EXEMPLO", "nome_civil"), ("JOANA EXEMPLO", "cliente")]


def test_todas_as_camadas(pt):
    for camada in ("cabecalho", "corpo", "rodape"):
        assert _nomes([_pagina("Paciente: FULANA DE TESTE", camada=camada)], pt) == [
            "FULANA DE TESTE"]


def test_linha_maiuscula_sem_rotulo_e_ignorada(pt):
    pagina = _pagina("LABORATORIO EXEMPLO", "HEMOGRAMA COMPLETO", "JOHN DOE DA SILVA",
                     "MARIA EXEMPLO")
    assert idt.candidatos([pagina], pt) == []


def test_tratamento_no_meio_da_frase(pt):
    pagina = _pagina("Encaminho a Sra. FULANA DE TESTE, 54 anos, para avaliação.")
    cands = idt.candidatos([pagina], pt)
    assert [(c.chave, c.rotulo) for c in cands] == [("FULANA DE TESTE", "tratamento")]


@pytest.mark.parametrize("linha", [
    "PACIENTE EM BOM ESTADO GERAL",
    "Paciente refere dor torácica há 3 dias",
    "PACIENTE APRESENTA MELHORA CLINICA",
    "PACIENTE DE 54 ANOS",
    "A paciente FULANA DE TESTE retorna hoje",
    "Nome da mãe: JOANA EXEMPLO",
    "Nome do exame: HEMOGRAMA COMPLETO",
    "Paciente: HOSPITAL EXEMPLO",
    "Paciente: RAIO X DE TORAX",
    "Nome: Assinatura Do Paciente",
    "Cliente: Operadora Exemplo",
])
def test_prosa_e_outros_nomes_nao_viram_candidato(pt, linha):
    assert idt.candidatos([_pagina(linha)], pt) == []


@pytest.mark.parametrize("linha", [
    "Ao Ilmo. Sr. Diretor Clínico do Hospital Exemplo",
    "Prezada Sra. Coordenadora de Enfermagem",
    "Ao Sr. Gerente Administrativo",
    "Prezado Sr. Secretário de Saúde",
])
def test_cargo_depois_do_tratamento_nao_e_nome(pt, linha):
    # Nem candidato nem dúvida: a carta não aponta outra pessoa no arquivo.
    assert idt.leituras([_pagina(linha)], pt) == []


@pytest.mark.parametrize("linha", [
    "Paciente: MARIA",
    "Paciente: FULANA 2 TESTE",
    "Paciente: ANA M",
    "Paciente: F. D. T.",
])
def test_nome_que_falha_na_validacao_nao_vira_candidato(pt, linha):
    assert idt.candidatos([_pagina(linha)], pt) == []


def test_ordem_sobrenome_nome(pt):
    assert [c.valor_impresso for c in idt.candidatos([_pagina("Paciente: TESTE, Fulana de")],
                                                     pt)] == ["TESTE, Fulana"]


# ---------- casos portados do EtiquetaParserTest (photoid-rt) ----------

def test_rotulo_sozinho_quando_o_ocr_perde_os_dois_pontos(pt):
    pagina = _pagina("PACIENTE", "MARIA DA SILVA EXEMPLO", "PRONTUARIO 123456")
    assert _nomes([pagina], pt) == ["MARIA DA SILVA EXEMPLO"]


def test_com_os_dois_pontos_continua_funcionando(pt):
    assert _nomes([_pagina("PACIENTE: MARIA DA SILVA EXEMPLO", "123456")], pt) == [
        "MARIA DA SILVA EXEMPLO"]


def test_rotulo_colado_ao_nome_sem_separador(pt):
    assert _nomes([_pagina("PACIENTE MARIA DA SILVA EXEMPLO", "123456")], pt) == [
        "MARIA DA SILVA EXEMPLO"]


def test_nome_que_contem_palavra_de_rotulo_continua_valendo(pt):
    assert _nomes([_pagina("Paciente: MARIA NOME DA SILVA")], pt) == ["MARIA NOME DA SILVA"]
    assert _nomes([_pagina("Paciente: ANA PAULA NASCIMENTO", "99887")], pt) == [
        "ANA PAULA NASCIMENTO"]


def test_sobrenome_que_e_rotulo_seguido_de_data_fica_em_duvida(pt):
    # NASCIMENTO pode ser o sobrenome ou o rótulo sem dois-pontos: melhor faltar.
    assert idt.candidatos([_pagina("Paciente: ANA PAULA NASCIMENTO 01/02/1960")], pt) == []


def test_rotulo_de_outro_idioma_vem_do_pacote_sem_mexer_em_codigo():
    xx = Pacote("xx", {"rotulos": {
        "paciente": {"especificos": {"paciente": ["Patient", "Patient name"]}},
        "negativos": ["Physician"], "outros_campos": ["Age"],
        "particulas": ["of"], "nomes_comuns": ["JOHN"]}})
    pagina = _pagina("Patient name: JANE ROE Age: 54", "Physician: RICHARD ROE")
    assert [c.chave for c in idt.candidatos([pagina], xx)] == ["JANE ROE"]
    assert idt.candidatos([_pagina("Paciente: FULANA DE TESTE")], xx) == []


# ---------- suíte de armadilhas: médico nunca vira paciente ----------

ARMADILHAS = [
    ["Médico solicitante: Dr. JOHN DOE"],
    ["Solicitante: JOHN DOE"],
    ["Requisitante JOHN DOE"],
    ["Dr. JOHN DOE - CRM 12345"],
    ["Dra. JANE DOE CRM-SP 123456"],
    ["Responsável técnico: JOHN DOE CRBM 1234"],
    ["Assinado eletronicamente por JOHN DOE"],
    ["Assinado digitalmente por: JOHN DOE"],
    ["Laudo liberado por: Dra. JANE DOE"],
    ["Nome do médico: JOHN DOE"],
    ["Nome: Dr. JOHN DOE"],
    ["Nome: JOHN DOE CRM 12345"],
    ["Nome: JOHN DOE", "CRM 12345"],
    ["Responsável técnico", "Nome: JOHN DOE"],
    ["Nome: JOHN DOE", "Responsável técnico"],
    ["Nome: JOHN DOE (responsável técnico)"],
    ["Sr. JOHN DOE (médico responsável)"],
    ["Cliente: JOHN DOE", "Assinado eletronicamente por"],
    ["DADOS DO MÉDICO", "Nome: JOHN DOE", "Conselho: CRM 12345"],
    ["Paciente               Médico solicitante", "JOHN DOE     FULANA DE TESTE"],
    ["Médico solicitante     Paciente", "JOHN DOE     FULANA DE TESTE"],
    ["Paciente: Dr. JOHN DOE"],
    ["DADOS DO MÉDICO", "Nome: JOHN DOE"],
    ["MÉDICO RESPONSÁVEL", "Nome completo: JOHN DOE"],
    # Título de bloco de profissional: a palavra abre o bloco do médico.
    ["Equipe médica", "Nome: JOHN DOE"],
    ["EQUIPE MÉDICA", "Nome: JOHN DOE", "Função: Cirurgião"],
    ["Auditoria médica", "Nome: JOHN DOE"],
    ["Perícia médica", "Nome: JOHN DOE"],
    ["Clínica médica", "Nome: JOHN DOE"],
    ["Orientação médica", "Nome: JOHN DOE"],
]


@pytest.mark.parametrize("linhas", ARMADILHAS)
def test_armadilha_nunca_escolhe_o_medico(pt, linhas):
    assert not [c for c in idt.candidatos([_pagina(*linhas)], pt)
                if "JOHN" in c.chave or "JANE" in c.chave or "DOE" in c.chave]


def test_armadilhas_juntas_com_o_paciente(pt):
    paginas = [_pagina(*linhas, indice=i) for i, linhas in enumerate(ARMADILHAS)]
    paginas.append(_pagina("Paciente: FULANA DE TESTE", indice=len(paginas)))
    assert _nomes(paginas, pt) == ["FULANA DE TESTE"]


@pytest.mark.parametrize("linhas", [
    ["Paciente: FULANA DE TESTE  Solicitante: Dr. JOHN DOE"],
    ["Solicitante: Dr. JOHN DOE  Paciente: FULANA DE TESTE"],
    ["Paciente: FULANA DE TESTE", "Médico solicitante: Dr. JOHN DOE CRM 1234"],
    ["Paciente: FULANA DE TESTE", "Solicitante: CRM-SP 123456"],
    ["Nome: FULANA DE TESTE", "Médico solicitante: Dr. JOHN DOE"],
    ["Nome: FULANA DE TESTE  Médico: Dr. JOHN DOE CRM 1234"],
    ["Paciente: FULANA DE TESTE", "Responsável técnico: Dra. JANE DOE CRBM 1234",
     "Assinado eletronicamente por JANE DOE"],
])
def test_paciente_ao_lado_do_medico(pt, linhas):
    assert _nomes([_pagina(*linhas)], pt) == ["FULANA DE TESTE"]


# Forma do OCR de verdade: rótulo e valor colados, e "médico" como adjetivo no
# título do documento ou no nome do convênio, na linha vizinha.
@pytest.mark.parametrize("linhas", [
    ["RECEITA MÉDICA", "Sr(a).:FULANA DE TESTE DN: 17/08/1972", "Uso oral"],
    ["RELATÓRIO MÉDICO", "Nome completo:FULANA DE TESTE"],
    ["Cliente:Fulana de Teste Solicitante:Dra. Jane Doe Exemplo",
     "Convênio:Serra Clara Assistência Médica Protocolo: 6272279"],
    ["LAUDO MÉDICO", "Nome: FULANA DE TESTE"],
    ["ENCAMINHAMENTO MÉDICO", "Nome: FULANA DE TESTE"],
    ["RESUMO MÉDICO", "Nome: FULANA DE TESTE"],
    ["HISTÓRICO MÉDICO", "Nome: FULANA DE TESTE"],
    ["SUMÁRIO MÉDICO", "Nome: FULANA DE TESTE"],
    ["PRONTUÁRIO MÉDICO", "Nome: FULANA DE TESTE"],
    ["SOLICITAÇÃO MÉDICA", "Nome: FULANA DE TESTE"],
    ["AVALIAÇÃO MÉDICA", "Nome: FULANA DE TESTE"],
])
def test_medico_como_adjetivo_na_vizinha_nao_recusa_o_paciente(pt, linhas):
    assert _nomes([_pagina(*linhas)], pt) == ["FULANA DE TESTE"]


# ---------- confiança ----------

def test_nome_de_baixa_confianca_nao_vira_candidato(pt):
    pagina = _pagina("Paciente: FULANA DE TESTE", conf=0.62)
    assert idt.candidatos([pagina], pt) == []
    assert _nomes([pagina], pt, limiar_confianca=0.5) == ["FULANA DE TESTE"]


# ---------- tabelas ----------

def _tabela(celulas, confiancas=None) -> Pagina:
    confiancas = confiancas or tuple(tuple(1.0 for _ in linha) for linha in celulas)
    return Pagina(arquivo_sha256="cd" * 32, indice=0, blocos=(
        Tabela(celulas=celulas, confiancas=confiancas, caixa=(0.05, 0.1, 0.95, 0.3)),))


# ---------- rótulo e valor em linhas separadas ----------

def _linhas(*itens) -> Pagina:
    """Linhas com caixa própria: (texto, x0, y0, x1)."""
    return Pagina(arquivo_sha256="ab" * 32, indice=0, blocos=tuple(
        Linha(t, (x0, y0, x1, y0 + 0.02)) for t, x0, y0, x1 in itens))


MESMA_ALTURA = [("Paciente:", 0.05, 0.10, 0.15), ("FULANA DE TESTE", 0.20, 0.10, 0.50),
                ("Médico:", 0.55, 0.10, 0.65), ("Dr. JOHN DOE", 0.66, 0.10, 0.90)]


def test_valor_na_caixa_ao_lado_na_mesma_altura(pt):
    assert _nomes([_linhas(*MESMA_ALTURA)], pt) == ["FULANA DE TESTE"]


def test_valor_ao_lado_vence_o_rotulo_da_linha_de_baixo(pt):
    pagina = _linhas(*MESMA_ALTURA, ("Nome:", 0.05, 0.13, 0.12))
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_nascimento_na_caixa_ao_lado(pt):
    pagina = _linhas(("DN:", 0.05, 0.10, 0.09), ("01/02/1960", 0.20, 0.10, 0.32))
    assert idt.nascimento([pagina], pt) == date(1960, 2, 1)


def test_rotulo_ao_lado_manda_ler_a_linha_de_baixo(pt):
    pagina = _linhas(("Paciente:", 0.05, 0.10, 0.15), ("Idade:", 0.55, 0.10, 0.62),
                     ("FULANA DE TESTE", 0.05, 0.13, 0.35), ("54", 0.55, 0.13, 0.58))
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_outra_coluna_longe_na_mesma_altura_nao_e_o_valor(pt):
    # O valor está embaixo; o que está a meia página de distância é outra coluna.
    pagina = _linhas(("Paciente:", 0.05, 0.10, 0.15), ("JOHN DOE EXEMPLO", 0.60, 0.10, 0.90),
                     ("FULANA DE TESTE", 0.05, 0.13, 0.35))
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_tabela_rotulo_e_valor_lado_a_lado(pt):
    pagina = _tabela((("Paciente", "FULANA DE TESTE", "Idade", "54"),))
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_tabela_valor_na_celula_abaixo(pt):
    pagina = _tabela((("Paciente", "Médico solicitante"), ("FULANA DE TESTE", "JOHN DOE")))
    assert _nomes([pagina], pt) == ["FULANA DE TESTE"]


def test_tabela_rotulo_e_valor_na_mesma_celula(pt):
    assert _nomes([_tabela((("Paciente: FULANA DE TESTE", "Idade: 54"),))], pt) == [
        "FULANA DE TESTE"]


def test_tabela_nome_generico_de_medico(pt):
    pagina = _tabela((("Nome", "JOHN DOE"), ("CRM", "12345")))
    assert idt.candidatos([pagina], pt) == []


def test_tabela_celula_de_baixa_confianca(pt):
    pagina = _tabela((("Paciente", "FULANA DE TESTE"),), ((1.0, 0.4),))
    assert idt.candidatos([pagina], pt) == []


# ---------- data de nascimento ----------

@pytest.mark.parametrize("linha, esperado", [
    ("Data de nascimento: 01/02/1960", date(1960, 2, 1)),
    ("DN 03.04.1960", date(1960, 4, 3)),
    ("Nasc.: 03-04-60", date(1960, 4, 3)),
    ("D.N.: 03/04/25", date(2025, 4, 3)),
    ("Paciente: FULANA DE TESTE  Nascimento: 13/12/1955", date(1955, 12, 13)),
    ("Paciente: FULANA DE TESTE DN: 13/12/1955 Idade: 70", date(1955, 12, 13)),
    ("Dt. nasc. 13/12/1955", date(1955, 12, 13)),
    ("Paciente: FULANA DE TESTE D.Nasc.: 13/12/1955", date(1955, 12, 13)),
])
def test_nascimento(pt, linha, esperado):
    assert idt.nascimento([_pagina(linha)], pt) == esperado


@pytest.mark.parametrize("linha, esperado", [
    ("DN: 15/03/28", date(1928, 3, 15)),
    ("DN: 03/04/25", date(2025, 4, 3)),
    ("DN: 25/10/26", date(2026, 10, 25)),
    ("DN: 26/10/26", date(1926, 10, 26)),
])
def test_nascimento_com_ano_de_dois_digitos_nunca_no_futuro(pt, linha, esperado):
    assert idt.nascimento([_pagina(linha)], pt, hoje=date(2026, 10, 25)) == esperado


@pytest.mark.parametrize("linhas", [
    ["Data do exame: 02/04/2026"],
    ["01/02/1960"],
    ["Data de nascimento: 31/02/1960"],
    ["Data de nascimento: 01/02/1860"],
    ["Paciente: ANA PAULA NASCIMENTO 01/02/1960"],
    ["Data de nascimento:", "Idade: 54"],
])
def test_sem_nascimento(pt, linhas):
    assert idt.nascimento([_pagina(*linhas)], pt) is None


def test_nascimento_na_linha_abaixo(pt):
    assert idt.nascimento([_pagina("Data de nascimento", "01/02/1960")], pt) == date(1960, 2, 1)


def test_nascimento_em_tabela(pt):
    pagina = _tabela((("Paciente", "Data de nascimento"), ("FULANA DE TESTE", "01/02/1960")))
    assert idt.nascimento([pagina], pt) == date(1960, 2, 1)


def test_nascimento_repetido_e_conflitante(pt):
    igual = [_pagina("DN: 01/02/1960", indice=0), _pagina("DN: 01/02/1960", indice=1)]
    assert idt.nascimento(igual, pt) == date(1960, 2, 1)
    diferente = [_pagina("DN: 01/02/1960", indice=0), _pagina("DN: 02/02/1960", indice=1)]
    assert idt.nascimento(diferente, pt) is None


# ---------- pacote real ----------

def test_pacote_pt_br_tem_as_secoes_de_rotulos():
    rotulos = pacote_mod.carregar("pt-BR")["rotulos"]
    for secao in ("paciente", "negativos", "outros_campos", "prefixos_profissionais",
                  "conselhos", "nascimento", "particulas", "nomes_comuns", "nao_nome"):
        assert rotulos[secao], secao
    assert all(isinstance(p, str) for p in rotulos["nao_nome"])
