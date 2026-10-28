"""Atribuição de documentos a pacientes.

A regra que mais importa: documento de outro paciente nunca cai na pasta
errada. Na dúvida, paciente None (DESCONHECIDO), com o MD entregue. Só nomes
inventados.
"""
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from scan_to_text import registro
from scan_to_text.entender import identidade as idt
from scan_to_text.entender.atribuir import atribuir
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, Linha, Pagina

ROTULOS = Path(__file__).resolve().parents[1] / "app" / "packs" / "pt-BR" / "rotulos.yaml"
SHA = "ef" * 32
HOJE = date(2026, 10, 25)


@pytest.fixture(scope="module")
def pt() -> Pacote:
    return Pacote("pt-BR", {"rotulos": yaml.safe_load(ROTULOS.read_text(encoding="utf-8-sig"))})


def _pag(indice, *textos, conf=1.0) -> Pagina:
    linhas = tuple(Linha(t, (0.05, 0.05 + i * 0.03, 0.95, 0.07 + i * 0.03), confianca=conf)
                   for i, t in enumerate(textos))
    return Pagina(arquivo_sha256=SHA, indice=indice, blocos=linhas)


def _doc(n, *paginas) -> Documento:
    return Documento(id=f"{SHA[:12]}-{n:02d}", paginas=tuple(paginas))


def _rodar(docs, pt, limiar=0.80):
    paginas = [p for d in docs for p in d.paginas]
    cands = idt.candidatos(paginas, pt, limiar_confianca=limiar)
    nasc = {p.indice: dn for p in paginas if (dn := idt.nascimento([p], pt, hoje=HOJE)) is not None}
    return atribuir(docs, cands, nasc, pt)


def _pacientes(resultado) -> list[str | None]:
    return [d.paciente for d in resultado.documentos]


# ---------- um paciente ----------

def test_um_paciente_em_dois_documentos(pt):
    docs = [_doc(1, _pag(0, "LABORATORIO EXEMPLO", "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "CLINICA BETA", "Paciente: Fulana de Teste"))]
    r = _rodar(docs, pt)
    assert r.arquivo_sha256 == SHA
    assert _pacientes(r) == ["FULANA DE TESTE", "FULANA DE TESTE"]
    assert r.pacientes == ("FULANA DE TESTE",)
    assert r.multi_paciente is False
    assert all("paciente:rotulo" in d.motivos for d in r.documentos)


def test_documentos_de_entrada_ficam_intactos(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE"))]
    r = _rodar(docs, pt)
    assert docs[0].paciente is None and docs[0].motivos == ()
    assert r.documentos[0].id == docs[0].id and r.documentos[0].paginas == docs[0].paginas


def test_sem_documentos():
    r = atribuir([], [], {}, Pacote("pt-BR", {"rotulos": {}}))
    assert (r.documentos, r.pacientes, r.multi_paciente) == ((), (), False)


# ---------- contaminação: nunca na pasta do primeiro ----------

def test_contaminacao_com_nome_aceito_vai_para_o_outro(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Paciente: JOHN DOE EXEMPLO")),
            _doc(3, _pag(2, "Paciente: FULANA DE TESTE"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["FULANA DE TESTE", "JOHN DOE EXEMPLO", "FULANA DE TESTE"]
    assert r.pacientes == ("FULANA DE TESTE", "JOHN DOE EXEMPLO")
    assert r.multi_paciente is True


@pytest.mark.parametrize("contaminante", [
    ["Paciente: JOHN"],                                          # falha na validação
    ["Paciente: JOHN D0E"],                                      # dígito
    ["Paciente: Dr. JOHN DOE"],                                  # valor de profissional
    ["Paciente               Idade", "JOHN DOE     54"],         # valor ilegível
    ["Paciente               Médico solicitante", "JOHN DOE     RICHARD ROE"],
    ["Paciente: john doe exemplo"],                              # minúsculas
])
def test_contaminacao_sem_nome_aceito_vira_desconhecido(pt, contaminante):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, *contaminante)),
            _doc(3, _pag(2, "Paciente: FULANA DE TESTE"))]
    r = _rodar(docs, pt)
    assert r.documentos[1].paciente is None
    assert r.pacientes == ("FULANA DE TESTE",)


def test_contaminacao_de_baixa_confianca_vira_desconhecido(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Paciente: JOHN DOE EXEMPLO", conf=0.55))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE", None]


def test_limiar_do_chamador_mais_alto_nao_deixa_herdar(pt):
    # O nome de confiança 0,85 não é candidato com limiar 0,90: é dúvida, nunca
    # documento sem nome que herda o paciente do arquivo.
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Paciente: JOHN DOE EXEMPLO", conf=0.85))]
    assert _pacientes(_rodar(docs, pt, limiar=0.90)) == ["FULANA DE TESTE", None]


def test_conflito_dentro_do_documento(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE"), _pag(1, "Paciente: JOHN DOE EXEMPLO"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [None]
    assert "paciente:conflito" in r.documentos[0].motivos
    assert r.pacientes == ()


def test_duvida_incompativel_no_mesmo_documento_e_conflito(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE"), _pag(1, "Paciente: JOHN"))]
    assert _pacientes(_rodar(docs, pt)) == [None]


def test_continuacao_com_nome_de_outro_em_duas_colunas_e_conflito(pt):
    # A página 2 não tem candidato (o nome está embaixo do rótulo, numa linha
    # com outro rótulo), e a segmentação a junta à página 1. O nome lido nela
    # é de outra pessoa: o documento não pode ir para a pasta da primeira.
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "Página 1 de 2"),
                 _pag(1, "Paciente:", "JOHN DOE EXEMPLO     Solicitante: Dr. RICHARD ROE",
                      "Página 2 de 2"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [None]
    assert "paciente:conflito" in r.documentos[0].motivos
    assert r.pacientes == ()


def test_continuacao_com_o_mesmo_nome_em_duas_colunas_fica(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "Página 1 de 2"),
                 _pag(1, "Paciente:", "FULANA DE TESTE     Solicitante: Dr. RICHARD ROE",
                      "Página 2 de 2"))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE"]


def test_duvida_compativel_no_mesmo_documento_nao_atrapalha(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE"),
                 _pag(1, "Paciente: FULANA DE TESTE", conf=0.5))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE"]


# ---------- documento sem nome ----------

def test_anotacao_sem_nome_com_um_paciente_vai_para_ele(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "HEMOGRAMA")),
            _doc(2, _pag(1, "Retorno em 30 dias. Trazer exames."))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["FULANA DE TESTE", "FULANA DE TESTE"]
    assert "paciente:herdado" in r.documentos[1].motivos
    assert r.multi_paciente is False


def test_anotacao_sem_nome_com_dois_pacientes_fica_sem_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Retorno em 30 dias.")),
            _doc(3, _pag(2, "Paciente: JOHN DOE EXEMPLO"))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE", None, "JOHN DOE EXEMPLO"]


def test_arquivo_sem_nenhum_paciente(pt):
    docs = [_doc(1, _pag(0, "HEMOGRAMA COMPLETO")), _doc(2, _pag(1, "Retorno em 30 dias."))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [None, None]
    assert (r.pacientes, r.multi_paciente) == ((), False)


def test_nome_duvidoso_no_arquivo_impede_heranca(pt):
    # Um nome que não deu para aceitar mostra que há outra pessoa no arquivo: a
    # anotação sem nome pode ser dela.
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Paciente: JOHN")),
            _doc(3, _pag(2, "Retorno em 30 dias."))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE", None, None]


def test_documento_com_nome_de_profissional_reservado_nao_herda(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Responsável técnico", "Nome: JOHN DOE EXEMPLO"))]
    assert _pacientes(_rodar(docs, pt))[1] is None


def test_assinatura_do_medico_nao_impede_heranca(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Retorno em 30 dias.", "Dr. JOHN DOE CRM 12345"))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE", "FULANA DE TESTE"]


def test_carta_a_um_cargo_nao_vira_paciente_e_herda(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE")),
            _doc(2, _pag(1, "Ao Sr. Diretor Clínico", "Solicito vaga."))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["FULANA DE TESTE", "FULANA DE TESTE"]
    assert r.pacientes == ("FULANA DE TESTE",)


def test_anotacao_com_outra_data_de_nascimento_nao_herda(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "DN: 01/02/1960")),
            _doc(2, _pag(1, "Retorno em 30 dias.", "DN: 05/06/1980"))]
    assert _pacientes(_rodar(docs, pt)) == ["FULANA DE TESTE", None]


# ---------- variantes, homônimos e ambiguidade ----------

def test_variantes_compativeis_sao_o_mesmo_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: MARIA J. EXEMPLO DA SILVA")),
            _doc(2, _pag(1, "Paciente: MARIA JOSE EXEMPLO DA SILVA")),
            _doc(3, _pag(2, "Paciente: SILVA, Maria Jose Exemplo da"))]
    r = _rodar(docs, pt)
    assert r.pacientes == ("MARIA JOSE EXEMPLO DA SILVA",)
    assert _pacientes(r) == ["MARIA JOSE EXEMPLO DA SILVA"] * 3


def test_rotulo_depois_do_nome_nao_divide_o_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE OS: 12345")),
            _doc(2, _pag(1, "Paciente: FULANA DE TESTE Idade: 54"))]
    r = _rodar(docs, pt)
    assert r.pacientes == ("FULANA DE TESTE",)
    assert r.multi_paciente is False


def test_so_sobrenome_primeiro_vira_ordem_natural(pt):
    r = _rodar([_doc(1, _pag(0, "Paciente: TESTE, Fulana"))], pt)
    assert r.pacientes == ("Fulana TESTE",)


def test_nascimento_com_ano_de_dois_digitos_nao_cria_homonimo(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "DN: 15/03/28")),
            _doc(2, _pag(1, "Paciente: FULANA DE TESTE", "DN: 15/03/1928"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["FULANA DE TESTE", "FULANA DE TESTE"]
    assert [d.nascimento for d in r.documentos] == [date(1928, 3, 15)] * 2


def test_nome_com_palavras_coladas_pelo_ocr_e_o_mesmo_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: ODETE LUCIAVALVERDINO")),
            _doc(2, _pag(1, "Paciente: ODETE LUCIA VALVERDINO")),
            _doc(3, _pag(2, "Retorno em 30 dias."))]
    r = _rodar(docs, pt)
    assert r.pacientes == ("ODETE LUCIA VALVERDINO",)
    assert _pacientes(r) == ["ODETE LUCIA VALVERDINO"] * 3
    assert r.multi_paciente is False


def test_nome_com_palavras_coladas_e_nascimento_diferente_nao_se_junta(pt):
    docs = [_doc(1, _pag(0, "Paciente: DORIVALRAFAEL NOGUEIRAO", "DN: 01/02/1960")),
            _doc(2, _pag(1, "Paciente: DORIVAL RAFAEL NOGUEIRAO", "DN: 05/06/1980"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [None, None]
    assert all("paciente:homonimo" in d.motivos for d in r.documentos)


@pytest.mark.parametrize("a, b", [
    ("GIL DA SILVA", "GILDA SILVA"), ("NEI DE SOUZA", "NEIDE SOUZA"),
    ("ELI DA COSTA", "ELIDA COSTA"), ("DANIEL A. SILVA", "DANIELA SILVA"),
])
def test_particula_ou_inicial_colada_no_nome_anterior_e_outro_paciente(pt, a, b):
    docs = [_doc(1, _pag(0, "LABORATORIO ALFA", f"Paciente: {a}")),
            _doc(2, _pag(1, "CLINICA BETA", f"Paciente: {b}"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [a, b]
    assert r.pacientes == (a, b)
    assert r.multi_paciente is True


def test_entre_arquivos_palavras_coladas_so_se_juntam_com_nascimento_igual(tmp_path):
    # A pasta de outro arquivo só é reaproveitada pela variante compatível com
    # o nascimento confirmado dos dois lados.
    pasta = tmp_path / "ODETE LUCIAVALVERDINO - 2026-10-01"
    (pasta / registro.PASTA_SISTEMA).mkdir(parents=True)
    (pasta / registro.PASTA_SISTEMA / registro.MANIFESTO).write_text(json.dumps({"paciente": {
        "exibido": "ODETE LUCIAVALVERDINO", "variantes": ["ODETE LUCIAVALVERDINO"],
        "nascimento": "1960-02-01", "confianca": 1.0}}), encoding="utf-8")

    def achar(nome, nascimento):
        return registro.achar_pasta(tmp_path, nome, nascimento, idt.compativeis, idt.chave)

    assert achar("ODETE LUCIA VALVERDINO", date(1960, 2, 1)) == pasta
    assert achar("ODETE LUCIA VALVERDINO", date(1975, 3, 4)) is None
    assert achar("ODETE LUCIA VALVERDINO", None) is None


def test_nome_compativel_com_duas_pessoas_e_ambiguo(pt):
    docs = [_doc(1, _pag(0, "Paciente: MARIA JOSE DA SILVA")),
            _doc(2, _pag(1, "Paciente: MARIA JOANA DA SILVA")),
            _doc(3, _pag(2, "Paciente: MARIA J. DA SILVA"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["MARIA JOSE DA SILVA", "MARIA JOANA DA SILVA", None]
    assert "paciente:ambiguo" in r.documentos[2].motivos


def test_homonimos_no_mesmo_arquivo_ficam_sem_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "DN: 01/02/1960")),
            _doc(2, _pag(1, "Paciente: FULANA DE TESTE", "DN: 05/06/1980"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == [None, None]
    assert all("paciente:homonimo" in d.motivos for d in r.documentos)


def test_nascimento_vai_para_todos_os_documentos_do_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "Data de nascimento: 01/02/1960")),
            _doc(2, _pag(1, "Paciente: FULANA DE TESTE")),
            _doc(3, _pag(2, "Retorno em 30 dias."))]
    r = _rodar(docs, pt)
    assert [d.nascimento for d in r.documentos] == [date(1960, 2, 1)] * 3


def test_duas_datas_de_nascimento_no_mesmo_documento(pt):
    docs = [_doc(1, _pag(0, "Paciente: FULANA DE TESTE", "DN: 01/02/1960"),
                 _pag(1, "Paciente: FULANA DE TESTE", "DN: 05/06/1980"))]
    assert _pacientes(_rodar(docs, pt)) == [None]


# ---------- nome civil e nome social ----------

def test_civil_nomeia_e_so_social_vai_para_a_pasta_do_civil(pt):
    docs = [_doc(1, _pag(0, "Paciente: MARIA EXEMPLO DA SILVA", "Nome social: ALEX EXEMPLO")),
            _doc(2, _pag(1, "Nome social: ALEX EXEMPLO"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["MARIA EXEMPLO DA SILVA", "MARIA EXEMPLO DA SILVA"]
    assert r.pacientes == ("MARIA EXEMPLO DA SILVA",)
    assert "paciente:social" in r.documentos[1].motivos


def test_so_nome_social_tambem_conta(pt):
    docs = [_doc(1, _pag(0, "Nome social: ALEX EXEMPLO", "HEMOGRAMA")),
            _doc(2, _pag(1, "Retorno em 30 dias."))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["ALEX EXEMPLO", "ALEX EXEMPLO"]
    assert r.pacientes == ("ALEX EXEMPLO",)


def test_social_ligado_a_dois_civis_fica_sem_paciente(pt):
    docs = [_doc(1, _pag(0, "Paciente: MARIA EXEMPLO DA SILVA", "Nome social: ALEX EXEMPLO")),
            _doc(2, _pag(1, "Paciente: JOANA TESTE SOUZA", "Nome social: ALEX EXEMPLO")),
            _doc(3, _pag(2, "Nome social: ALEX EXEMPLO"))]
    r = _rodar(docs, pt)
    assert _pacientes(r) == ["MARIA EXEMPLO DA SILVA", "JOANA TESTE SOUZA", None]


def test_social_duvidoso_nao_derruba_documento_com_civil(pt):
    docs = [_doc(1, _pag(0, "Paciente: MARIA EXEMPLO DA SILVA"),
                 _pag(1, "Nome social: ALEX", conf=0.5))]
    assert _pacientes(_rodar(docs, pt)) == ["MARIA EXEMPLO DA SILVA"]


def test_civil_duvidoso_com_so_social_aceito_fica_sem_paciente(pt):
    # O civil nomearia a pasta, mas não deu para aceitá-lo: não se sabe de quem é.
    docs = [_doc(1, _pag(0, "Nome social: ALEX EXEMPLO", "Paciente: JOANA"))]
    assert _pacientes(_rodar(docs, pt)) == [None]
