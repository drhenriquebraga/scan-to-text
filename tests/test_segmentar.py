import itertools
import random
import time
from pathlib import Path

import yaml

from scan_to_text import pacote as modulo_pacote
from scan_to_text.entender import segmentar as sg
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Linha, Pagina, Tabela

PACKS = Path(modulo_pacote.RAIZ_PACKS) / "pt-BR"
PACOTE = Pacote("pt-BR", {s: yaml.safe_load((PACKS / f"{s}.yaml").read_text(encoding="utf-8-sig"))
                          for s in ("paginas", "secoes")})
# Com as regras de categoria: título de documento não é seção própria.
REAL = modulo_pacote.carregar("pt-BR")
SHA = "ab12cd34ef56" + "0" * 52


def L(texto, y, camada="corpo", tipo="texto", x=0.1):
    return Linha(texto, (x, y, 0.9, y + 0.02), camada=camada, tipo=tipo)


def pag(indice, *blocos, em_branco=False):
    return Pagina(SHA, indice, tuple(blocos), em_branco=em_branco)


def laudo(indice, x, y_total, inst="LABORATORIO ALFA", titulo=None, nome=None):
    """Página de laudo com timbre no cabeçalho e 'Página X de Y' no rodapé."""
    blocos = [L(inst, 0.03, camada="cabecalho")]
    if nome:
        blocos.append(L(f"Paciente: {nome}", 0.08, camada="cabecalho"))
    if titulo:
        blocos.append(L(titulo, 0.20, tipo="titulo"))
    blocos.append(L(f"conteudo {inst} {titulo} parte {x}", 0.50))
    blocos.append(L(f"Página {x} de {y_total}", 0.95, camada="rodape"))
    return pag(indice, *blocos)


def indices(doc):
    return [p.indice for p in doc.paginas]


def todas_as_paginas(docs):
    return sorted(i for d in docs for i in indices(d))


# numeracao ------------------------------------------------------------------

def test_numeracao_pagina_x_de_y():
    p = pag(0, L("Página 2 de 5", 0.95, camada="rodape"))
    assert sg.numeracao(p, PACOTE) == (2, 5)


def test_numeracao_maiusculas_e_sem_acento():
    assert sg.numeracao(pag(0, L("PAGINA 3 DE 4", 0.96)), PACOTE) == (3, 4)


def test_numeracao_pag_abreviada_com_barra():
    assert sg.numeracao(pag(0, L("Pág. 3/4", 0.04, camada="cabecalho")), PACOTE) == (3, 4)


def test_numeracao_x_barra_y_no_pe():
    assert sg.numeracao(pag(0, L("2/3", 0.95)), PACOTE) == (2, 3)


def test_numeracao_x_barra_y_no_meio_da_pagina_nao_conta():
    assert sg.numeracao(pag(0, L("2/3", 0.50)), PACOTE) is None


def test_numeracao_folha_sem_total():
    assert sg.numeracao(pag(0, L("Folha 4", 0.95, camada="rodape")), PACOTE) == (4, None)


def test_numeracao_incoerente_e_ignorada():
    assert sg.numeracao(pag(0, L("Página 5 de 3", 0.95, camada="rodape")), PACOTE) is None


def test_numeracao_ausente():
    assert sg.numeracao(pag(0, L("Hemoglobina 12,4 g/dL", 0.5)), PACOTE) is None


def test_numeracao_vem_do_pacote_e_nao_do_codigo():
    outro = Pacote("xx", {"paginas": {"numeracao": [
        {"padrao": r"\bpage\s*(?P<x>\d+)\s*of\s*(?P<y>\d+)", "so_margem": False}]}})
    assert sg.numeracao(pag(0, L("Page 2 of 7", 0.95)), outro) == (2, 7)
    assert sg.numeracao(pag(0, L("Página 2 de 7", 0.95)), outro) is None


# titulo e instituicao -----------------------------------------------------------

def test_titulo_primeira_linha_de_titulo_do_topo():
    p = pag(0, L("LABORATORIO ALFA", 0.03, camada="cabecalho"),
            L("Paciente: FULANA DE TESTE", 0.08, camada="cabecalho"),
            L("HEMOGRAMA COMPLETO", 0.20, tipo="titulo"),
            L("ERITROGRAMA", 0.30, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "HEMOGRAMA COMPLETO"


def test_titulo_prefere_titulo_a_secao():
    p = pag(0, L("DADOS CLINICOS", 0.12, tipo="secao"), L("MAMOGRAFIA BILATERAL", 0.20, tipo="titulo"))
    assert sg.titulo(p, PACOTE) == "MAMOGRAFIA BILATERAL"


def test_titulo_ignora_campo_e_linha_baixa():
    p = pag(0, L("Paciente: FULANA DE TESTE", 0.10, tipo="titulo"),
            L("CONCLUSAO", 0.70, tipo="secao"))
    assert sg.titulo(p, PACOTE) == ""


def test_titulo_ausente():
    assert sg.titulo(pag(0, L("texto corrido do laudo", 0.3)), PACOTE) == ""


def test_instituicao_mais_alta_do_cabecalho_sem_rotulo_de_campo():
    p = pag(0, L("Paciente: FULANA DE TESTE", 0.02, camada="cabecalho"),
            L("CLINICA DE IMAGEM BETA", 0.05, camada="cabecalho"),
            L("MAMOGRAFIA", 0.2, tipo="titulo"))
    assert sg.instituicao(p, PACOTE) == "CLINICA DE IMAGEM BETA"


def test_instituicao_no_corpo_acima_do_titulo():
    p = pag(0, L("LABORATORIO ALFA", 0.03),
            L("Rua Exemplo, 100", 0.06),
            L("HEMOGRAMA", 0.20, tipo="secao"))
    assert sg.instituicao(p, PACOTE) == "LABORATORIO ALFA"
    assert sg.titulo(p, PACOTE) == "HEMOGRAMA"


def test_timbre_marcado_como_secao_no_corpo_nao_vira_titulo():
    # Forma que o OCR de verdade devolve: o layout marca o nome do laboratório,
    # no topo, como seção do corpo, igual ao título do exame.
    p = pag(0, L("LABORATÓRIO EXEMPLO ANÁLISES CLÍNICAS", 0.031, tipo="secao"),
            L("Rua Fictícia, 100 - Centro - Tel (11) 5555-0000", 0.055),
            L("Paciente: FULANA DE TESTE", 0.092),
            L("Data da coleta: 02/04/2026", 0.129),
            L("HEMOGRAMA COMPLETO", 0.171, tipo="secao"),
            L("Página 1 de 1", 0.941, camada="rodape"))
    assert sg.titulo(p, PACOTE) == "HEMOGRAMA COMPLETO"
    assert sg.instituicao(p, PACOTE) == "LABORATÓRIO EXEMPLO ANÁLISES CLÍNICAS"


def test_timbre_com_razao_social_no_corpo_nao_vira_titulo():
    p = pag(0, L("IMAGEM EXEMPLO S/A", 0.03, tipo="secao"),
            L("TOMOGRAFIA COMPUTADORIZADA DO TÓRAX", 0.18, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "TOMOGRAFIA COMPUTADORIZADA DO TÓRAX"
    assert sg.instituicao(p, PACOTE) == "IMAGEM EXEMPLO S/A"


def test_cara_de_timbre_sozinha_continua_titulo():
    p = pag(0, L("HOSPITAL EXEMPLO", 0.05, tipo="secao"), L("Texto corrido da página.", 0.20))
    assert sg.titulo(p, PACOTE) == "HOSPITAL EXEMPLO"


def test_titulo_do_topo_com_subtitulo_abaixo_continua_o_mesmo():
    p = pag(0, L("HEMOGRAMA COMPLETO", 0.05, tipo="secao"), L("SÉRIE VERMELHA", 0.25, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "HEMOGRAMA COMPLETO"


def test_titulo_curto_de_marcador_vence_a_linha_do_valor():
    p = pag(0, L("BIOLAB EXEMPLO", 0.044, tipo="secao"),
            L("Avenida das Paineiras, 814 - Centro - CEP 88399-373 - Tel. (64) 3548-6244", 0.064),
            L("Nome:Fulana de Teste", 0.102),
            L("CA 15-3", 0.160, tipo="secao"),
            L("Material: Soro Método: Eletroquimioluminescência", 0.192),
            L("CA 15-3: 39,4 U/mL", 0.207, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "CA 15-3"


def test_timbre_seguido_de_endereco_nao_vira_titulo():
    p = pag(0, L("DIAGNOSE EXEMPLAR", 0.04, tipo="secao"),
            L("Rua Fictícia, 100 - Tel. (11) 5555-0000", 0.06),
            L("Paciente: FULANA DE TESTE", 0.10),
            L("ULTRASSONOGRAFIA DE ABDOME", 0.16, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "ULTRASSONOGRAFIA DE ABDOME"
    assert sg.instituicao(p, PACOTE) == "DIAGNOSE EXEMPLAR"


def test_titulo_seguido_da_linha_do_paciente_com_contato_continua_titulo():
    for linha in ("Paciente: FULANA DE TESTE - Rua Inventada, 10",
                  "Paciente: FULANA DE TESTE   Tel: (11) 5555-0000",
                  "Exame feito na unidade da Rua Inventada, 10"):
        p = pag(0, L("HOSPITAL EXEMPLO", 0.03, camada="cabecalho"),
                L("TOMOGRAFIA COMPUTADORIZADA DO TÓRAX", 0.12, tipo="titulo"),
                L(linha, 0.145), L("TÉCNICA", 0.20, tipo="secao"))
        assert sg.titulo(p, PACOTE) == "TOMOGRAFIA COMPUTADORIZADA DO TÓRAX", linha


def test_titulo_fora_do_cabecalho_com_endereco_abaixo_continua_titulo():
    p = pag(0, L("ULTRASSONOGRAFIA DE ABDOME", 0.18, tipo="titulo"),
            L("Rua Inventada, 10 - Unidade Centro", 0.20), L("TÉCNICA", 0.26, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "ULTRASSONOGRAFIA DE ABDOME"


def test_timbre_seguido_da_linha_de_contato_nao_vira_titulo():
    for contato in ("Fone: (11) 3333-0000 - www.exemplo.com.br", "Centro - Tel. (11) 3333-0000"):
        p = pag(0, L("DIAGNOSE EXEMPLAR", 0.04, tipo="secao"), L(contato, 0.06),
                L("ULTRASSONOGRAFIA DE ABDOME", 0.16, tipo="secao"))
        assert sg.titulo(p, PACOTE) == "ULTRASSONOGRAFIA DE ABDOME", contato


def test_titulo_seguido_de_campo_continua_titulo():
    p = pag(0, L("RELATÓRIO MÉDICO", 0.10, tipo="secao"), L("Nome completo:FULANA DE TESTE", 0.13),
            L("História clínica", 0.17, tipo="secao"))
    assert sg.titulo(p, PACOTE) == "RELATÓRIO MÉDICO"


def test_linha_de_valor_nunca_e_titulo():
    p = pag(0, L("CA 125: 35,0 U/mL", 0.10, tipo="secao"), L("texto", 0.3))
    assert sg.titulo(p, PACOTE) == ""


def test_timbre_com_contato_na_mesma_linha_continua_instituicao():
    # O leitor junta as caixas da linha impressa: o timbre traz "Fone: 3333"
    # ou "CNPJ: 12..." e não é linha de resultado.
    for cab in ("LABORATORIO ALFA - Rua Inventada, 100 - Fone: 3333-0000",
                "LABORATORIO ALFA   CNPJ: 12.345.678/0001-00"):
        p0 = pag(0, L(cab, 0.03, camada="cabecalho"), L("HEMOGRAMA COMPLETO", 0.12, tipo="titulo"),
                 L("Hemácias: 4,5 milhões/mm3", 0.30))
        p1 = pag(1, L(cab, 0.03, camada="cabecalho"), L("Leucócitos: 6.000/mm3", 0.20))
        assert sg.instituicao(p0, PACOTE) == cab
        assert [indices(d) for d in sg.segmentar([p0, p1], PACOTE)] == [[0, 1]], cab


def test_laboratorios_diferentes_com_contato_no_timbre_nao_se_juntam():
    alfa = "LABORATORIO ALFA - Rua Inventada, 100 - Fone: 3333-0000"
    beta = "CLINICA BETA - Av. Fictícia, 9 - Fone: 4444-1111"
    b2 = pag(0, L(beta, 0.03, camada="cabecalho"), L("texto beta", 0.20),
             L("Página 2 de 2", 0.96, camada="rodape"))
    a1 = pag(1, L(alfa, 0.03, camada="cabecalho"), L("HEMOGRAMA", 0.12, tipo="titulo"),
             L("Página 1 de 2", 0.96, camada="rodape"))
    a2 = pag(2, L(alfa, 0.03, camada="cabecalho"), L("continuação", 0.20),
             L("Página 2 de 2", 0.96, camada="rodape"))
    assert sorted(indices(d) for d in sg.segmentar([b2, a1, a2], PACOTE)) == [[0], [1, 2]]


def test_numeracao_que_o_pacote_nao_conhece_nao_vira_instituicao():
    for marca in (("1 de 2", "2 de 2"), ("Fl. 1", "Fl. 2"), ("Pg 1/2", "Pg 2/2")):
        p0 = pag(0, L(marca[0], 0.020, camada="cabecalho", x=0.80),
                 L("LABORATORIO ALFA", 0.030, camada="cabecalho"), L("HEMOGRAMA", 0.12, tipo="titulo"))
        p1 = pag(1, L(marca[1], 0.020, camada="cabecalho", x=0.80),
                 L("LABORATORIO ALFA", 0.030, camada="cabecalho"), L("Leucócitos: 6.000/mm3", 0.20))
        assert sg.instituicao(p0, PACOTE) == "LABORATORIO ALFA", marca
        assert [indices(d) for d in sg.segmentar([p0, p1], PACOTE)] == [[0, 1]], marca
    # Com a marca, duas páginas de instituições diferentes continuam separadas.
    a = pag(0, L("Fl. 1", 0.020, camada="cabecalho", x=0.80), L("LABORATORIO ALFA", 0.03, camada="cabecalho"),
            L("HEMOGRAMA", 0.12, tipo="titulo"))
    b = pag(1, L("Fl. 1", 0.020, camada="cabecalho", x=0.80), L("CLINICA GAMA", 0.03, camada="cabecalho"),
            L("texto sem título", 0.20))
    assert [indices(d) for d in sg.segmentar([a, b], PACOTE)] == [[0], [1]]


def test_sem_timbre_o_topo_e_titulo_e_nao_instituicao():
    p = pag(0, L("ENCAMINHAMENTO", 0.05, tipo="secao"), L("Ao colega, encaminho a paciente", 0.12))
    assert sg.titulo(p, PACOTE) == "ENCAMINHAMENTO"
    assert sg.instituicao(p, PACOTE) is None


# segmentar -------------------------------------------------------------------

def test_sem_paginas_sem_documentos():
    assert sg.segmentar([], PACOTE) == []


def test_embaralhadas_voltam_a_ordem_em_todas_as_permutacoes():
    paginas = [laudo(i, i + 1, 5, titulo="HEMOGRAMA COMPLETO" if i == 0 else None) for i in range(5)]
    for ordem in itertools.permutations(paginas):
        docs = sg.segmentar(list(ordem), PACOTE)
        assert len(docs) == 1
        assert indices(docs[0]) == [0, 1, 2, 3, 4]
        assert docs[0].titulo == "HEMOGRAMA COMPLETO"
        assert docs[0].instituicao == "LABORATORIO ALFA"


def test_dois_documentos_embaralhados_voltam_cada_um_a_ordem():
    a = [laudo(i, i + 1, 3, inst="LABORATORIO ALFA", titulo="HEMOGRAMA" if i == 0 else None)
         for i in range(3)]
    b = [laudo(10 + i, i + 1, 4, inst="CLINICA DE IMAGEM BETA",
               titulo="TOMOGRAFIA DE TORAX" if i == 0 else None) for i in range(4)]
    sorteio = random.Random(7)
    for _ in range(50):
        paginas = a + b
        sorteio.shuffle(paginas)
        docs = sg.segmentar(paginas, PACOTE)
        assert sorted(indices(d) for d in docs) == [[0, 1, 2], [10, 11, 12, 13]]


def test_titulo_repetido_em_toda_pagina_nao_atrapalha_a_ordem():
    paginas = [laudo(i, i + 1, 3, titulo="ULTRASSONOGRAFIA") for i in range(3)]
    docs = sg.segmentar([paginas[2], paginas[0], paginas[1]], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1, 2]]


def test_verso_em_branco_nao_gera_documento():
    frente1 = laudo(0, 1, 2, titulo="HEMOGRAMA")
    verso1 = pag(1, em_branco=True)
    frente2 = laudo(2, 2, 2)
    verso2 = pag(3, em_branco=True)
    docs = sg.segmentar([frente1, verso1, frente2, verso2], PACOTE)
    assert [indices(d) for d in docs] == [[0, 2]]


def test_pagina_orfa_vira_documento_proprio():
    doc = [laudo(i, i + 1, 3, titulo="HEMOGRAMA" if i == 0 else None) for i in range(3)]
    orfa = pag(9, L("ANOTACAO", 0.05, tipo="titulo"), L("texto a mao", 0.5))
    docs = sg.segmentar([doc[0], orfa, doc[1], doc[2]], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1, 2], [9]]


def test_numero_repetido_nao_cabe_e_vira_documento_proprio():
    paginas = [laudo(0, 1, 2, titulo="GLICEMIA"), laudo(1, 2, 2), laudo(2, 2, 2)]
    docs = sg.segmentar(paginas, PACOTE)
    assert todas_as_paginas(docs) == [0, 1, 2]
    assert [indices(d) for d in docs] == [[0, 1], [2]]


def test_dois_laudos_do_mesmo_laboratorio_em_sequencia():
    a1 = laudo(0, 1, 2, titulo="HEMOGRAMA")
    a2 = laudo(1, 2, 2)
    b1 = laudo(2, 1, 2, titulo="GLICEMIA")
    b2 = laudo(3, 2, 2)
    docs = sg.segmentar([a1, a2, b1, b2], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1], [2, 3]]
    assert [d.titulo for d in docs] == ["HEMOGRAMA", "GLICEMIA"]


def test_ambiguidade_sem_vizinho_nao_adivinha():
    """Duas páginas 2 sem título, longe das páginas 1: não há como saber de quem são."""
    a1 = laudo(0, 1, 2, titulo="HEMOGRAMA")
    b1 = laudo(1, 1, 2, titulo="GLICEMIA")
    outra = pag(2, L("ANOTACAO", 0.05, tipo="titulo"))
    x = laudo(5, 2, 2)
    y = laudo(6, 2, 2)
    docs = sg.segmentar([a1, b1, outra, x, y], PACOTE)
    assert [indices(d) for d in docs] == [[0], [1], [2], [5], [6]]


def test_pilha_invertida_usa_o_vizinho():
    a1 = laudo(0, 1, 2, titulo="HEMOGRAMA")
    a2 = laudo(1, 2, 2)
    b1 = laudo(2, 1, 2, titulo="GLICEMIA")
    b2 = laudo(3, 2, 2)
    docs = sg.segmentar([a2, a1, b2, b1], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1], [2, 3]]


def test_continuacao_sem_nome_nunca_vai_para_o_documento_de_outro_paciente():
    """Dois laudos do mesmo laboratório, com o mesmo título, de pacientes diferentes,
    e o nome impresso numa página só de cada um: em nenhuma ordem do scan uma página
    entra no documento do outro paciente."""
    dono = {0: "A", 1: "A", 2: "B", 3: "B"}
    for titulo_em_todas in (False, True):
        continuacao = "HEMOGRAMA" if titulo_em_todas else None
        paginas = [laudo(0, 1, 2, titulo="HEMOGRAMA"), laudo(1, 2, 2, titulo=continuacao),
                   laudo(2, 1, 2, titulo="HEMOGRAMA"), laudo(3, 2, 2, titulo=continuacao)]
        for a, b in itertools.product((0, 1), (2, 3)):
            nomes = {a: "FULANA DE TESTE", b: "JOHN DOE"}
            for ordem in itertools.permutations(paginas):
                docs = sg.segmentar(list(ordem), PACOTE, nomes)
                assert todas_as_paginas(docs) == [0, 1, 2, 3]
                for d in docs:
                    assert len({dono[i] for i in indices(d)}) == 1, ([p.indice for p in ordem], nomes)


def test_continuacao_que_abre_com_secao_propria_fica_no_documento():
    # Forma do OCR de verdade: a página 2 de um relatório abre com a seção
    # dela ("Exames complementares"), que o layout marca como título.
    for pacote in (PACOTE, REAL):
        for secoes in (("Exames complementares", "Conduta"), ("MICROSCOPIA", "Impressão")):
            paginas = [laudo(0, 1, 3, titulo="RELATÓRIO MÉDICO"), laudo(1, 2, 3, titulo=secoes[0]),
                       laudo(2, 3, 3, titulo=secoes[1])]
            docs = sg.segmentar(paginas, pacote)
            assert [indices(d) for d in docs] == [[0, 1, 2]], secoes
            assert docs[0].titulo == "RELATÓRIO MÉDICO"
            # Na ordem inversa, sem junção pelo título que mostre a pilha
            # invertida, a seção pode ser de outro documento e fica sozinha.
            docs = sg.segmentar(paginas[::-1], pacote)
            assert sorted(indices(d) for d in docs) == [[0], [1], [2]], secoes
            # Fora da ordem natural, a página que não está colada à anterior fica sozinha.
            for ordem in itertools.permutations(paginas):
                assert todas_as_paginas(sg.segmentar(list(ordem), pacote)) == [0, 1, 2]


def test_dois_seriados_com_continuacao_de_secao_propria_ficam_separados():
    # A seção repetida em outra página do arquivo pode ser título de documento:
    # na dúvida, a continuação fica sozinha e nunca vai para o outro seriado.
    paginas = [laudo(0, 1, 2, titulo="HEMOGRAMA"), laudo(1, 2, 2, titulo="PLAQUETAS"),
               laudo(2, 1, 2, titulo="HEMOGRAMA"), laudo(3, 2, 2, titulo="PLAQUETAS")]
    assert [indices(d) for d in sg.segmentar(paginas, PACOTE)] == [[0], [1], [2], [3]]
    paginas = [laudo(0, 1, 2, titulo="RELATÓRIO MÉDICO"), laudo(1, 2, 2, titulo="Conduta"),
               laudo(2, 1, 2, titulo="RELATÓRIO MÉDICO"), laudo(3, 2, 2, titulo="Impressão")]
    for pacote in (PACOTE, REAL):
        assert [indices(d) for d in sg.segmentar(paginas, pacote)] == [[0, 1], [2, 3]]


def test_pagina_x_que_repete_o_titulo_do_seu_documento_nao_entra_em_outro():
    # A página 2 de B repete o título de B, sem nome; a página 1 de B não tem a
    # numeração lida, ou ficou fora do arquivo. Colada à página 1 de A, ela não
    # é seção própria de A.
    nomes = {0: "JOHN DOE", 2: "FULANA DE TESTE"}
    b1 = _tc(0, None, "TOMOGRAFIA COMPUTADORIZADA", "JOHN DOE")
    b2 = _tc(1, 2, "TOMOGRAFIA COMPUTADORIZADA")
    a1 = _tc(2, 1, "ULTRASSONOGRAFIA", "FULANA DE TESTE")
    a2 = _tc(3, None, "ULTRASSONOGRAFIA")
    for paginas in ([b1, b2, a1], [b1, b2, a1, a2], [b1, a1, b2, a2], [b1, a1, b2]):
        docs = sg.segmentar(paginas, REAL, nomes)
        assert sorted(indices(d) for d in docs) == [[p.indice] for p in sorted(paginas, key=lambda p: p.indice)]
    # Sem as regras de categoria, o título repetido em outra página do arquivo basta.
    assert sorted(indices(d) for d in sg.segmentar([b1, b2, a1], PACOTE, nomes)) == [[0], [1], [2]]
    laudo_b1, laudo_b2 = _tc(0, None, "LAUDO", "JOHN DOE"), _tc(1, 2, "LAUDO")
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar([laudo_b1, laudo_b2, a1], pacote, nomes)
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]
    # Sem a página 1 de B, o título de documento do pacote ainda separa.
    assert sorted(indices(d) for d in sg.segmentar([b2, a1, a2], REAL, nomes)) == [[1], [2], [3]]


def test_pagina_que_repete_o_titulo_nunca_vai_para_outro_paciente():
    nomes = {0: "JOHN DOE", 2: "FULANA DE TESTE"}
    dono = {0: "B", 1: "B", 2: "A", 3: "A"}
    paginas = [_tc(0, None, "TOMOGRAFIA COMPUTADORIZADA", "JOHN DOE"),
               _tc(1, 2, "TOMOGRAFIA COMPUTADORIZADA"),
               _tc(2, 1, "ULTRASSONOGRAFIA", "FULANA DE TESTE"), _tc(3, None, "ULTRASSONOGRAFIA")]
    for conjunto in (paginas, paginas[1:], paginas[:3], [paginas[1], paginas[2]]):
        for ordem in itertools.permutations(conjunto):
            docs = sg.segmentar(list(ordem), REAL, nomes)
            assert todas_as_paginas(docs) == sorted(p.indice for p in conjunto)
            for d in docs:
                assert len({dono[i] for i in indices(d)}) == 1, [p.indice for p in ordem]


def test_pagina_sem_titulo_entre_dois_grupos_volta_depois_da_secao_propria():
    # Na primeira passada, a página 3 cabe no grupo da 1 e no da 2 (que abre
    # com seção própria) e fica sozinha; juntas a 1 e a 2, só sobra um grupo.
    def pg(indice, x, titulo=None, nome=None):
        return laudo(indice, x, 3, titulo=titulo, nome=nome)
    receita = pag(3, L("CLINICA BETA", 0.03, camada="cabecalho"), L("RECEITUÁRIO", 0.12, tipo="titulo"),
                  L("Paciente: JOHN DOE", 0.18))
    nomes = {0: "FULANA DE TESTE", 3: "JOHN DOE"}
    for pacote in (PACOTE, REAL):
        paginas = [pg(0, 1, "RELATÓRIO MÉDICO", "FULANA DE TESTE"), pg(1, 2, "Exames complementares"),
                   pg(2, 3)]
        assert [indices(d) for d in sg.segmentar(paginas, pacote, nomes)] == [[0, 1, 2]]
        assert [indices(d) for d in sg.segmentar(paginas + [receita], pacote, nomes)] == [[0, 1, 2], [3]]
        # Na ordem inversa, sem pilha invertida mostrada pelo título, nada se junta.
        assert sorted(indices(d) for d in sg.segmentar(paginas[::-1], pacote, nomes)) == [[0], [1], [2]]


def test_pagina_na_duvida_nao_vai_para_o_candidato_que_sobrou():
    # A página 2 sem título nem nome cabe no grupo de JOHN e no de CICRANO; o
    # grupo de CICRANO recebe depois a página 2 dele. Isso não prova que ela é
    # de JOHN: é de FULANA, cuja página 1 não tem a numeração lida. A seção
    # "Conduta" também fica sozinha: a página 2 sem título está colada à
    # página 1 de JOHN do outro lado, e a pilha de CICRANO está invertida.
    nomes = {0: "FULANA DE TESTE", 2: "JOHN DOE", 4: "CICRANO EXEMPLO", 5: "CICRANO EXEMPLO"}
    sem_titulo = pag(1, L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L("texto 1", 0.30),
                     L("Página 2 de 2", 0.96, camada="rodape"))
    paginas = [_tc(0, None, "TOMOGRAFIA", "FULANA DE TESTE"), sem_titulo,
               _tc(2, 1, "ULTRASSONOGRAFIA", "JOHN DOE"), _tc(3, 2, "Conduta"),
               _tc(4, 2, "RESSONÂNCIA", "CICRANO EXEMPLO"), _tc(5, 1, "RESSONÂNCIA", "CICRANO EXEMPLO")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, nomes)
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3], [5, 4]]


def test_pagina_sem_titulo_continua_sozinha_entre_dois_pacientes():
    # A única página 3 cabe nos dois relatórios, de pacientes diferentes: em
    # nenhuma ordem ela entra num deles, nem antes nem depois de juntar as seções.
    def pg(indice, x, titulo=None):
        return laudo(indice, x, 3, titulo=titulo)
    paginas = [pg(0, 1, "RELATÓRIO MÉDICO"), pg(1, 2, "Exames complementares"),
               pg(2, 1, "RELATÓRIO MÉDICO"), pg(3, 2, "Conduta"), pg(4, 3)]
    dono = {0: "A", 1: "A", 2: "B", 3: "B", 4: "?"}
    for nomes in ({0: "FULANA DE TESTE", 2: "JOHN DOE"}, {0: "FULANA DE TESTE", 3: "JOHN DOE"}):
        for ordem in itertools.permutations(paginas):
            docs = sg.segmentar(list(ordem), REAL, nomes)
            assert todas_as_paginas(docs) == [0, 1, 2, 3, 4]
            for d in docs:
                assert len({dono[i] for i in indices(d)}) == 1, ([p.indice for p in ordem], nomes)


def test_continuacao_de_secao_propria_nunca_vai_para_outro_paciente():
    dono = {0: "A", 1: "A", 2: "B", 3: "B"}
    paginas = [laudo(0, 1, 2, titulo="HEMOGRAMA"), laudo(1, 2, 2, titulo="PLAQUETAS"),
               laudo(2, 1, 2, titulo="HEMOGRAMA"), laudo(3, 2, 2, titulo="LEUCOGRAMA")]
    for a, b in itertools.product((0, 1), (2, 3)):
        nomes = {a: "FULANA DE TESTE", b: "JOHN DOE"}
        for ordem in itertools.permutations(paginas):
            docs = sg.segmentar(list(ordem), PACOTE, nomes)
            assert todas_as_paginas(docs) == [0, 1, 2, 3]
            for d in docs:
                assert len({dono[i] for i in indices(d)}) == 1, ([p.indice for p in ordem], nomes)


def _tc(indice, x, titulo, nome=None):
    blocos = [L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L(titulo, 0.12, tipo="titulo")]
    if nome:
        blocos.append(L(f"Paciente: {nome}", 0.18))
    blocos.append(L(f"texto {indice}", 0.30))
    if x:
        blocos.append(L(f"Página {x} de 2", 0.96, camada="rodape"))
    return pag(indice, *blocos)


def test_pagina_x_do_titulo_vence_a_continuacao_de_secao_propria():
    # A página 1 de B não tem numeração lida; a página 2 de B abre com a seção
    # dela e vem antes, no scan, da página 2 de A, que tem o título de A.
    nomes = {0: "JOHN DOE", 2: "FULANA DE TESTE"}
    paginas = [_tc(0, None, "TOMOGRAFIA", "JOHN DOE"), _tc(1, 2, "CONCLUSÃO"),
               _tc(2, 1, "ULTRASSONOGRAFIA", "FULANA DE TESTE"), _tc(3, 2, "ULTRASSONOGRAFIA")]
    docs = sg.segmentar(paginas, PACOTE, nomes)
    assert sorted(indices(d) for d in docs) == [[0], [1], [2, 3]]
    sem_b1 = sg.segmentar(paginas[1:], PACOTE, nomes)
    assert sorted(indices(d) for d in sem_b1) == [[1], [2, 3]]
    dono = {0: "B", 1: "B", 2: "A", 3: "A"}
    for conjunto in (paginas, paginas[1:]):
        for ordem in itertools.permutations(conjunto):
            docs = sg.segmentar(list(ordem), PACOTE, nomes)
            assert todas_as_paginas(docs) == sorted(p.indice for p in conjunto)
            for d in docs:
                assert len({dono[i] for i in indices(d)}) == 1, [p.indice for p in ordem]


def test_continuacao_de_secao_propria_longe_da_pagina_anterior_nao_junta():
    paginas = [_tc(0, 1, "ULTRASSONOGRAFIA", "FULANA DE TESTE"),
               pag(1, L("CLINICA BETA", 0.03, camada="cabecalho"), L("RECEITUÁRIO", 0.12, tipo="titulo")),
               _tc(2, 2, "CONCLUSÃO")]
    for ordem in (paginas, paginas[::-1]):
        docs = sg.segmentar(ordem, PACOTE, {0: "FULANA DE TESTE"})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]


def test_continuacao_de_secao_propria_sem_a_pagina_anterior_nao_junta():
    paginas = [laudo(0, 1, 3, titulo="RELATÓRIO MÉDICO"), laudo(1, 3, 3, titulo="Conduta")]
    docs = sg.segmentar(paginas, PACOTE)
    assert [indices(d) for d in docs] == [[0], [1]]


def test_dois_laudos_iguais_do_mesmo_paciente_ainda_se_juntam_pelo_vizinho():
    paginas = [laudo(0, 1, 2, titulo="HEMOGRAMA"), laudo(1, 2, 2),
               laudo(2, 1, 2, titulo="HEMOGRAMA"), laudo(3, 2, 2)]
    for nomes in ({}, {0: "FULANA DE TESTE", 2: "FULANA DE TESTE"},
                  {i: "FULANA DE TESTE" for i in range(4)}):
        docs = sg.segmentar(paginas, PACOTE, nomes)
        assert [indices(d) for d in docs] == [[0, 1], [2, 3]], nomes


def test_sem_numeracao_continuacao_sem_titulo_junta():
    p1 = pag(0, L("CLINICA ONCOLOGICA GAMA", 0.03, camada="cabecalho"),
             L("RELATORIO MEDICO", 0.18, tipo="titulo"), L("texto um", 0.5))
    p2 = pag(1, L("CLINICA ONCOLOGICA GAMA", 0.03, camada="cabecalho"), L("texto dois", 0.3))
    docs = sg.segmentar([p1, p2], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1]]
    assert docs[0].titulo == "RELATORIO MEDICO"


def test_sem_numeracao_titulo_novo_separa_mesmo_que_igual():
    """Exame seriado: mesmo laboratório, mesmo título, sem numeração."""
    p1 = pag(0, L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L("HEMOGRAMA", 0.2, tipo="titulo"),
             L("Hemoglobina 12,4 g/dL", 0.4))
    p2 = pag(1, L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L("HEMOGRAMA", 0.2, tipo="titulo"),
             L("Hemoglobina 13,1 g/dL", 0.4))
    docs = sg.segmentar([p1, p2], PACOTE)
    assert [indices(d) for d in docs] == [[0], [1]]


def test_sem_numeracao_outra_instituicao_separa():
    p1 = pag(0, L("CLINICA ONCOLOGICA GAMA", 0.03, camada="cabecalho"),
             L("RELATORIO MEDICO", 0.18, tipo="titulo"))
    p2 = pag(1, L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L("texto", 0.3))
    assert [indices(d) for d in sg.segmentar([p1, p2], PACOTE)] == [[0], [1]]


def test_sem_numeracao_e_sem_timbre_nao_junta():
    p1 = pag(0, L("ANOTACAO", 0.05, tipo="titulo"), L("texto", 0.3))
    p2 = pag(1, L("mais texto sem cabecalho", 0.05))
    assert [indices(d) for d in sg.segmentar([p1, p2], PACOTE)] == [[0], [1]]


def test_nome_diferente_sempre_separa():
    p1 = pag(0, L("CLINICA ONCOLOGICA GAMA", 0.03, camada="cabecalho"),
             L("RELATORIO MEDICO", 0.18, tipo="titulo"))
    p2 = pag(1, L("CLINICA ONCOLOGICA GAMA", 0.03, camada="cabecalho"), L("texto", 0.3))
    nomes = {0: "FULANA DE TESTE", 1: "JOHN DOE"}
    assert [indices(d) for d in sg.segmentar([p1, p2], PACOTE, nomes)] == [[0], [1]]
    numeradas = [laudo(0, 1, 2, titulo="HEMOGRAMA"), laudo(1, 2, 2)]
    assert [indices(d) for d in sg.segmentar(numeradas, PACOTE, nomes)] == [[0], [1]]
    iguais = {0: "FULANA DE TESTE", 1: "FULANA DE TESTE"}
    assert [indices(d) for d in sg.segmentar(numeradas, PACOTE, iguais)] == [[0, 1]]


def test_timbre_no_corpo_reconhecido_por_posicao_e_repeticao():
    """O layout pôs o timbre no corpo, como seção, em todas as páginas."""
    def pagina(i, x, titulo=None):
        blocos = [L("LABORATORIO ALFA", 0.03, tipo="secao"), L("Paciente: MARIA EXEMPLO", 0.08)]
        if titulo:
            blocos.append(L(titulo, 0.20, tipo="secao"))
        blocos.append(L(f"parte {i}", 0.5))
        if x:
            blocos.append(L(f"Página {x} de 3", 0.95))
        return pag(i, *blocos)

    numeradas = [pagina(0, 1, "HEMOGRAMA"), pagina(1, 2), pagina(2, 3)]
    docs = sg.segmentar([numeradas[1], numeradas[2], numeradas[0]], PACOTE)
    assert [indices(d) for d in docs] == [[0, 1, 2]]
    assert (docs[0].titulo, docs[0].instituicao) == ("HEMOGRAMA", "LABORATORIO ALFA")

    soltas = [pagina(0, None, "HEMOGRAMA"), pagina(1, None)]
    docs = sg.segmentar(soltas, PACOTE)
    assert [indices(d) for d in docs] == [[0, 1]]
    assert (docs[0].titulo, docs[0].instituicao) == ("HEMOGRAMA", "LABORATORIO ALFA")


def test_titulo_repetido_sem_timbre_nao_vira_instituicao():
    """Dois exames seriados sem timbre: o título repetido no topo não junta as páginas."""
    p1 = pag(0, L("HEMOGRAMA", 0.05, tipo="secao"), L("Hemoglobina 12,4 g/dL", 0.3))
    p2 = pag(1, L("HEMOGRAMA", 0.05, tipo="secao"), L("Hemoglobina 13,1 g/dL", 0.3))
    docs = sg.segmentar([p1, p2], PACOTE)
    assert [indices(d) for d in docs] == [[0], [1]]
    assert [d.titulo for d in docs] == ["HEMOGRAMA", "HEMOGRAMA"]
    assert [d.instituicao for d in docs] == [None, None]


def test_ids_e_ordem_pela_primeira_aparicao():
    p0 = pag(0, L("ANOTACAO", 0.05, tipo="titulo"))
    doc = [laudo(i, i, 2, titulo="HEMOGRAMA" if i == 1 else None) for i in (1, 2)]
    docs = sg.segmentar([p0, doc[1], doc[0]], PACOTE)
    assert [d.id for d in docs] == [f"{SHA[:12]}-01", f"{SHA[:12]}-02"]
    assert [indices(d) for d in docs] == [[0], [1, 2]]
    assert all(d.paciente is None and d.categoria == 5 for d in docs)


def test_pagina_so_com_tabela_nao_se_perde():
    t = Tabela(celulas=(("a", "b"),), confiancas=((1.0, 1.0),), caixa=(0.1, 0.1, 0.9, 0.3))
    docs = sg.segmentar([pag(0, t), pag(1)], PACOTE)
    assert todas_as_paginas(docs) == [0, 1]


def test_nenhuma_pagina_se_perde_em_pilhas_aleatorias():
    sorteio = random.Random(20260928)
    insts = ["LABORATORIO ALFA", "CLINICA DE IMAGEM BETA", "HOSPITAL EXEMPLO"]
    for rodada in range(200):
        paginas = []
        for i in range(sorteio.randint(1, 12)):
            tipo = sorteio.random()
            inst = sorteio.choice(insts)
            if tipo < 0.15:
                paginas.append(pag(i, em_branco=True))
            elif tipo < 0.6:
                y = sorteio.randint(1, 4)
                paginas.append(laudo(i, sorteio.randint(1, y), y, inst=inst,
                                     titulo=sorteio.choice([None, "HEMOGRAMA", "PSA"])))
            else:
                paginas.append(pag(i, L(inst, 0.03, camada=sorteio.choice(["cabecalho", "corpo"])),
                                   L(sorteio.choice(["LAUDO", "RELATORIO", "texto"]), 0.2,
                                     tipo=sorteio.choice(["titulo", "texto"]))))
        sorteio.shuffle(paginas)
        nomes = {p.indice: sorteio.choice(["FULANA DE TESTE", "JOHN DOE"])
                 for p in paginas if sorteio.random() < 0.3}
        docs = sg.segmentar(paginas, PACOTE, nomes)
        esperadas = sorted(p.indice for p in paginas if not p.em_branco)
        assert todas_as_paginas(docs) == esperadas, rodada
        assert len({d.id for d in docs}) == len(docs)
        for d in docs:
            ns = {nomes[i] for i in indices(d) if i in nomes}
            assert len(ns) <= 1, rodada


def test_pacote_real_tem_as_secoes():
    real = modulo_pacote.carregar("pt-BR")
    assert real["paginas"]["numeracao"] and real["paginas"]["campos"]


def _rel(indice, x, titulo=None, nome=None):
    blocos = [L("LABORATORIO ALFA", 0.03, camada="cabecalho")]
    if titulo:
        blocos.append(L(titulo, 0.12, tipo="titulo"))
    if nome:
        blocos.append(L(f"Paciente: {nome}", 0.18))
    blocos.append(L(f"texto {indice}", 0.30))
    if x:
        blocos.append(L(f"Página {x} de 3", 0.96, camada="rodape"))
    return pag(indice, *blocos)


def test_pagina_na_duvida_so_volta_colada_a_pagina_anterior():
    # Pilha de FULANA invertida, com a página 1 sem a numeração lida, e JOHN em
    # ordem. A página 3 de FULANA cabia no grupo de JOHN e no da seção dele;
    # ela não está colada à página 2 de JOHN e nunca entra no documento dele.
    nomes = {1: "FULANA DE TESTE", 2: "JOHN DOE"}
    paginas = [_rel(0, 3), _rel(1, None, "TOMOGRAFIA", "FULANA DE TESTE"),
               _rel(2, 1, "ULTRASSONOGRAFIA", "JOHN DOE"), _rel(3, 2, "IMPRESSÃO"), _rel(4, 3)]
    for pacote in (PACOTE, REAL):
        for conjunto in (paginas, paginas[:4]):
            docs = sg.segmentar(conjunto, pacote, nomes)
            assert todas_as_paginas(docs) == [p.indice for p in conjunto]
            assert [0] in [indices(d) for d in docs] and [1] in [indices(d) for d in docs]
    # Com um paciente só, as seções do relatório se juntam e a página 3 longe
    # da página 2 continua sozinha.
    receita = pag(1, L("CLINICA BETA", 0.03, camada="cabecalho"), L("RECEITUÁRIO", 0.12, tipo="titulo"))
    paginas = [_rel(0, 3), receita, _rel(2, 1, "RELATÓRIO MÉDICO", "FULANA DE TESTE"),
               _rel(3, 2, "Exames complementares")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {2: "FULANA DE TESTE"})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2, 3]]


def test_duas_paginas_pela_mesma_vaga_ficam_sozinhas():
    # As duas páginas 2 abrem com seção própria e estão coladas à página 1, uma
    # de cada lado: nenhuma ganha a vaga pela posição no scan.
    nomes = {1: "FULANA DE TESTE"}
    paginas = [laudo(0, 2, 2, titulo="Conduta"),
               laudo(1, 1, 2, titulo="RELATÓRIO MÉDICO", nome="FULANA DE TESTE"),
               laudo(2, 2, 2, titulo="Impressão")]
    for pacote in (PACOTE, REAL):
        for ordem in (paginas, paginas[::-1]):
            docs = sg.segmentar(ordem, pacote, nomes)
            assert sorted(indices(d) for d in docs) == [[0], [1], [2]]


def test_secao_propria_antes_da_pagina_anterior_so_entra_com_pilha_invertida_no_arquivo():
    # A página 1 de JOHN não está no arquivo: a página 2 dele, com seção
    # própria, vem antes da página 1 de FULANA. Sem outra prova de pilha
    # invertida, ela não entra no documento de FULANA.
    nomes = {1: "FULANA DE TESTE"}
    paginas = [laudo(0, 2, 2, titulo="Conduta"), laudo(1, 1, 2, titulo="TOMOGRAFIA", nome="FULANA DE TESTE")]
    for pacote in (PACOTE, REAL):
        assert sorted(indices(d) for d in sg.segmentar(paginas, pacote, nomes)) == [[0], [1]]
    # Com a pilha invertida mostrada pelo título repetido, a seção entra.
    paginas = [laudo(0, 3, 3, titulo="Conduta"), laudo(1, 2, 3, titulo="RELATÓRIO MÉDICO"),
               laudo(2, 1, 3, titulo="RELATÓRIO MÉDICO", nome="FULANA DE TESTE")]
    for pacote in (PACOTE, REAL):
        assert [indices(d) for d in sg.segmentar(paginas, pacote, {2: "FULANA DE TESTE"})] == [[2, 1, 0]]


def test_secao_propria_colada_a_pagina_sem_numeracao_com_titulo_nao_entra_com_pilha_invertida():
    # Com a pilha invertida mostrada pelo título repetido, a página sem
    # numeração lida e com título, do outro lado da seção, pode ser a página 1
    # dela.
    def tomografia(indice, x):
        return laudo(indice, x, 2, titulo="TOMOGRAFIA", nome="FULANA DE TESTE")
    sem_numero = _tc(9, None, "ULTRASSONOGRAFIA")
    relatorio = laudo(5, 1, 2, titulo="RELATÓRIO MÉDICO", nome="FULANA DE TESTE")
    secao = laudo(6, 2, 2, titulo="IMPRESSÃO")
    nomes = {0: "FULANA DE TESTE", 1: "FULANA DE TESTE", 5: "FULANA DE TESTE"}
    invertida = [tomografia(0, 2), tomografia(1, 1)]
    for pacote in (PACOTE, REAL):
        for paginas in ([sem_numero, secao, relatorio], [relatorio, secao, sem_numero]):
            docs = sg.segmentar(invertida + paginas, pacote, nomes)
            assert sorted(indices(d) for d in docs) == [[1, 0], [5], [6], [9]]
        # Sem a página sem numeração colada, a seção entra.
        docs = sg.segmentar(invertida + [relatorio, secao], pacote, nomes)
        assert sorted(indices(d) for d in docs) == [[1, 0], [5, 6]]
    # Em ordem natural e sem pilha invertida no arquivo, a página seguinte é o
    # próximo documento.
    docs = sg.segmentar([relatorio, secao, sem_numero], REAL, nomes)
    assert sorted(indices(d) for d in docs) == [[5, 6], [9]]


def test_duas_paginas_de_secao_soltas_nao_formam_documento():
    # Sem a página 1 no grupo, duas seções vizinhas de numeração compatível
    # podem ser de dois documentos sem a primeira página no arquivo.
    paginas = [laudo(0, 2, 3, titulo="Conduta"), laudo(1, 3, 3, titulo="CONCLUSÃO")]
    for pacote in (PACOTE, REAL):
        assert [indices(d) for d in sg.segmentar(paginas, pacote)] == [[0], [1]]


def test_secao_propria_sem_nome_nao_entra_com_outro_paciente_do_mesmo_laboratorio_no_arquivo():
    # A página 1 de JOHN, do mesmo laboratório, ficou sem a numeração lida e
    # longe da seção. A seção sem nome, colada à página 1 de FULANA, pode ser
    # de JOHN.
    nomes = {0: "FULANA DE TESTE", 2: "FULANA DE TESTE", 4: "JOHN DOE"}
    receita = pag(3, L("CLINICA BETA", 0.03, camada="cabecalho"), L("RECEITUÁRIO", 0.12, tipo="titulo"))
    inicio = [laudo(0, 1, 2, titulo="TOMOGRAFIA", nome="FULANA DE TESTE"), laudo(1, 2, 2, titulo="Conduta"),
              laudo(2, 1, 1, titulo="HEMOGRAMA", nome="FULANA DE TESTE"), receita]
    paginas = inicio + [_tc(4, None, "ULTRASSONOGRAFIA", "JOHN DOE")]
    for pacote in (PACOTE, REAL):
        assert sorted(indices(d) for d in sg.segmentar(paginas, pacote, nomes)) == [[0], [1], [2], [3], [4]]
        # Com o nome de FULANA lido também na seção, o paciente está decidido.
        com_nome = {**nomes, 1: "FULANA DE TESTE"}
        assert sorted(indices(d) for d in sg.segmentar(paginas, pacote, com_nome)) == [[0, 1], [2], [3], [4]]
        # O outro paciente de outro laboratório, ou com outro total de páginas, não pesa.
        outro_lab = inicio + [pag(4, L("CLINICA BETA", 0.03, camada="cabecalho"),
                                  L("ULTRASSONOGRAFIA", 0.12, tipo="titulo"), L("Paciente: JOHN DOE", 0.18))]
        assert sorted(indices(d) for d in sg.segmentar(outro_lab, pacote, nomes)) == [[0, 1], [2], [3], [4]]
        outro_total = inicio + [laudo(4, 1, 3, titulo="ULTRASSONOGRAFIA", nome="JOHN DOE")]
        assert sorted(indices(d) for d in sg.segmentar(outro_total, pacote, nomes)) == [[0, 1], [2], [3], [4]]


def test_secao_propria_nao_toma_a_vaga_de_pagina_sem_numeracao_com_o_titulo_do_documento():
    # A página sem numeração lida que repete o título do documento pode ser a
    # página que falta a ele. Se as vagas não bastam para ela e para a seção,
    # a seção pode ser de outro documento e fica sozinha.
    nomes = {0: "FULANA DE TESTE"}
    tomografia = laudo(0, 1, 2, titulo="TOMOGRAFIA", nome="FULANA DE TESTE")
    secao = laudo(1, 2, 2, titulo="CONCLUSÃO")
    repete = _tc(2, None, "TOMOGRAFIA")
    for pacote in (PACOTE, REAL):
        for paginas in ([tomografia, secao, repete], [repete, tomografia, secao]):
            docs = sg.segmentar(paginas, pacote, nomes)
            assert sorted(indices(d) for d in docs) == [[0], [1], [2]], [p.indice for p in paginas]
        # Com vaga para as duas, a seção entra.
        relatorio = [laudo(0, 1, 3, titulo="TOMOGRAFIA", nome="FULANA DE TESTE"), laudo(1, 2, 3, titulo="CONCLUSÃO"),
                     repete]
        assert sorted(indices(d) for d in sg.segmentar(relatorio, pacote, nomes)) == [[0, 1], [2]]
        # Página sem numeração com outro título não disputa a vaga.
        outra = [tomografia, secao, _tc(2, None, "ULTRASSONOGRAFIA")]
        assert sorted(indices(d) for d in sg.segmentar(outra, pacote, nomes)) == [[0, 1], [2]]


def test_outro_paciente_com_pagina_sem_total_pode_ser_dono_da_secao():
    nomes = {0: "FULANA DE TESTE", 2: "JOHN DOE"}
    joao = pag(2, L("LABORATORIO ALFA", 0.03, camada="cabecalho"), L("ULTRASSONOGRAFIA", 0.12, tipo="titulo"),
               L("Paciente: JOHN DOE", 0.18), L("texto 2", 0.30), L("Página 1", 0.96, camada="rodape"))
    paginas = [laudo(0, 1, 2, titulo="TOMOGRAFIA", nome="FULANA DE TESTE"), laudo(1, 2, 2, titulo="Conduta"), joao]
    assert sg.numeracao(joao, PACOTE) == (1, None)
    for pacote in (PACOTE, REAL):
        assert sorted(indices(d) for d in sg.segmentar(paginas, pacote, nomes)) == [[0], [1], [2]]


def test_pilha_invertida_mostrada_por_qualquer_juncao_pelo_titulo():
    # A junção pelo título do próprio destino tem a página 3 antes da 1 no
    # scan, sem estarem coladas: a pilha está invertida, e a seção colada
    # depois da página 3 é a página 2.
    paginas = [laudo(0, 3, 3, titulo="TOMOGRAFIA"), laudo(1, 2, 3, titulo="Conduta"),
               laudo(2, 1, 3, titulo="TOMOGRAFIA", nome="FULANA DE TESTE")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {2: "FULANA DE TESTE"})
        assert [indices(d) for d in docs] == [[2, 1, 0]]


def test_inversao_longe_no_documento_de_outro_paciente_nao_mostra_a_pilha_do_destino():
    # A pilha de CICRANO está invertida, sem páginas coladas. A seção sem nome
    # de JOHN, logo antes da página 1 de FULANA, não entra no documento dela:
    # a inversão de outro documento não diz em que sentido a pilha de FULANA foi
    # escaneada.
    f, j, c = "FULANA DE TESTE", "JOHN DOE", "CICRANO EXEMPLO"
    beta = "CLINICA BETA"

    def pg(indice, quem, x, total, titulo, inst="LABORATORIO ALFA", rodape=True):
        blocos = [L(inst, 0.03, camada="cabecalho"), L(titulo, 0.12, tipo="titulo"), L(f"texto {indice}", 0.30)]
        if rodape and x:
            blocos.append(L(f"Página {x} de {total}", 0.96, camada="rodape"))
        return pag(indice, *blocos)

    casos = [
        ([pg(0, c, 2, 2, "RESSONANCIA"), pg(1, j, 2, 2, "Conduta"), pg(2, f, 1, 2, "TOMOGRAFIA"),
          pg(3, c, 1, 2, "RESSONANCIA")], {2: f}, [[0, 3], [1], [2]]),
        ([pg(0, c, 2, 3, "RESSONANCIA"), pg(1, j, 2, 2, "Conduta"), pg(2, f, 1, 2, "TOMOGRAFIA"),
          pg(3, c, 1, 3, "RESSONANCIA"), pg(4, c, 3, 3, "RESSONANCIA")], {2: f, 3: c}, [[0, 3, 4], [1], [2]]),
        ([pg(0, c, 2, 2, "RESSONANCIA", beta), pg(1, j, 2, 2, "Conduta"), pg(2, f, 1, 2, "TOMOGRAFIA"),
          pg(3, c, 1, 2, "RESSONANCIA", beta)], {2: f, 3: c}, [[0, 3], [1], [2]]),
        ([pg(0, c, 3, 3, "RESSONANCIA", beta), pg(1, c, 1, 3, "RESSONANCIA", beta),
          pg(2, c, 2, 3, "RESSONANCIA", beta), pg(3, j, 2, 2, "Conduta"), pg(4, f, 1, 2, "TOMOGRAFIA")],
         {1: c, 4: f}, [[0, 1, 2], [3], [4]]),
        ([pg(0, c, 2, 2, "RESSONANCIA", beta), pg(1, c, None, None, "LAUDO", beta),
          pg(2, c, 1, 2, "RESSONANCIA", beta), pg(3, j, 2, 2, "Conduta"), pg(4, f, 1, 2, "TOMOGRAFIA"),
          pg(5, f, 2, 2, "IMPRESSÃO", rodape=False)], {2: c, 4: f}, [[0, 2], [1], [3], [4], [5]]),
    ]
    for pacote in (PACOTE, REAL):
        for paginas, nomes, esperado in casos:
            docs = sg.segmentar(paginas, pacote, nomes)
            assert sorted(sorted(indices(d)) for d in docs) == esperado, [p.indice for p in paginas]


def test_pilha_invertida_colada_de_outra_instituicao_ou_de_outro_paciente_nao_mostra_a_pilha_do_destino():
    # A pilha de CICRANO está invertida, com as páginas coladas. A seção sem
    # nome de JOHN, logo antes da página 1 de FULANA, não entra no documento
    # dela quando a pilha invertida é de outra instituição ou tem o nome de
    # outro paciente: o sentido em que aquela pilha foi escaneada não é prova
    # do sentido da pilha de FULANA.
    f, c = "FULANA DE TESTE", "CICRANO EXEMPLO"
    secao, fulana = laudo(2, 2, 2, titulo="Conduta"), laudo(3, 1, 2, titulo="TOMOGRAFIA", nome=f)
    for inst, nome in (("CLINICA BETA", c), ("CLINICA BETA", None), ("LABORATORIO ALFA", c)):
        paginas = [laudo(0, 2, 2, inst, "RESSONANCIA"), laudo(1, 1, 2, inst, "RESSONANCIA", nome), secao, fulana]
        nomes = {3: f, **({1: nome} if nome else {})}
        for pacote in (PACOTE, REAL):
            docs = sg.segmentar(paginas, pacote, nomes)
            assert sorted(sorted(indices(d)) for d in docs) == [[0, 1], [2], [3]], (inst, nome)
    # A pilha invertida da mesma instituição e da própria FULANA mostra o
    # sentido do scan, e a seção entra.
    paginas = [laudo(0, 2, 2, titulo="RESSONANCIA"), laudo(1, 1, 2, titulo="RESSONANCIA", nome=f), secao, fulana]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {1: f, 3: f})
        assert sorted(sorted(indices(d)) for d in docs) == [[0, 1], [2, 3]]


def test_pagina_sem_titulo_nem_nome_longe_do_unico_candidato_nao_entra_nele():
    # A página 3 sem título e sem nome só cabe, pela numeração, no grupo de
    # JOHN, mas não está colada à página 2 dele, e FULANA, do mesmo
    # laboratório, tem página sem numeração lida: pode ser dela.
    f, j = "FULANA DE TESTE", "JOHN DOE"
    paginas = [_rel(0, 3), _rel(1, None, "TOMOGRAFIA", f), _rel(2, 1, "ULTRASSONOGRAFIA", j),
               _rel(3, 2, "ULTRASSONOGRAFIA")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {1: f, 2: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2, 3]]


def test_duas_paginas_sem_titulo_nem_nome_pela_mesma_vaga_ficam_sozinhas():
    # As duas páginas 2 sem título e sem nome cabem no grupo de JOHN, e só uma
    # está colada à página 1 dele. A colada pode ser a de FULANA: nenhuma entra.
    f, j = "FULANA DE TESTE", "JOHN DOE"
    casos = [([_rel(0, 2), _rel(1, None, "TOMOGRAFIA", f), _rel(2, 2), _rel(3, 1, "ULTRASSONOGRAFIA", j)],
              {1: f, 3: j}),
             ([_rel(0, 1, "ULTRASSONOGRAFIA", j), _rel(1, 2), _rel(2, None, "TOMOGRAFIA", f), _rel(3, 2)],
              {0: j, 2: f})]
    for pacote in (PACOTE, REAL):
        for paginas, nomes in casos:
            docs = sg.segmentar(paginas, pacote, nomes)
            assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3]]


def test_pagina_sem_titulo_nem_nome_recusada_nao_recebe_as_paginas_de_outro_documento():
    # A página 2 de JOHN, longe da página 1, fica sozinha; a página 3 de
    # FULANA, colada a ela e com o título do documento de FULANA, não entra nela.
    f, j = "FULANA DE TESTE", "JOHN DOE"
    paginas = [_rel(0, 1, "ULTRASSONOGRAFIA", j), _rel(1, 3, "ULTRASSONOGRAFIA"),
               _rel(2, None, "TOMOGRAFIA", f), _rel(3, 3, "TOMOGRAFIA", f), _rel(4, 2)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: j, 2: f, 3: f})
        assert sorted(indices(d) for d in docs) == [[0, 1], [2], [3], [4]]


def test_pagina_sem_titulo_nem_nome_entra_colada_ou_sem_outro_paciente():
    f, j = "FULANA DE TESTE", "JOHN DOE"
    colada = [_rel(0, 1, "ULTRASSONOGRAFIA", j), _rel(1, 2), _rel(2, None, "TOMOGRAFIA", f)]
    sozinho = [_rel(0, 3), _rel(1, 1, "ULTRASSONOGRAFIA", j), _rel(2, 2, "ULTRASSONOGRAFIA")]
    for pacote in (PACOTE, REAL):
        assert sorted(indices(d) for d in sg.segmentar(colada, pacote, {0: j, 2: f})) == [[0, 1], [2]]
        assert [indices(d) for d in sg.segmentar(sozinho, pacote, {1: j})] == [[1, 2, 0]]


def test_pagina_com_o_titulo_repetido_nao_toma_a_vaga_disputada_pela_pagina_sem_titulo():
    # FULANA e JOHN com o mesmo exame do mesmo laboratório. A página 2 sem
    # título e sem nome disputa a vaga do grupo de FULANA com a página 2 de
    # JOHN, que repete o título e não tem nome: nenhuma das duas fica com ela.
    f, j, t = "FULANA DE TESTE", "JOHN DOE", "TOMOGRAFIA"
    casos = [([_rel(0, 1, t, f), _rel(1, 2), _rel(2, None, t, j), _rel(3, 2, t)], {0: f, 2: j}),
             ([_rel(0, 1, t, f), _rel(1, 2), _rel(2, 3), _rel(3, None, t, j), _rel(4, 2, t), _rel(5, 3, t)],
              {0: f, 3: j})]
    for pacote in (PACOTE, REAL):
        for paginas, nomes in casos:
            docs = sg.segmentar(paginas, pacote, nomes)
            assert sorted(indices(d) for d in docs) == [[i] for i in range(len(paginas))]


def test_pagina_seguinte_a_vaga_disputada_nao_entra_no_unico_grupo_com_nome():
    # As páginas 2 sem título e sem nome ficam fora pela disputa. A página 3 de
    # JOHN, com o título repetido e sem nome, cabe no grupo de FULANA e caberia
    # no da página 2 que ficou fora: não entra no de FULANA.
    f, j, t = "FULANA DE TESTE", "JOHN DOE", "TOMOGRAFIA"
    paginas = [_rel(0, 1, t, f), _rel(1, 2), _rel(2, None, t, j), _rel(3, 2), _rel(4, 3, t)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: f, 2: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3], [4]]


def test_pagina_com_nome_nao_entra_no_grupo_sem_nome_quando_a_pagina_que_ficou_fora_tambem_cabia():
    # A página 3 de JOHN abre com a seção "Conduta" e cabe no grupo da
    # "Conduta" sem nome de FULANA, mas também no das páginas 2 sem título que
    # ficaram fora pela disputa: sem como escolher, fica sozinha.
    f, j = "FULANA DE TESTE", "JOHN DOE"
    paginas = [_rel(0, 3, "Conduta", j), _rel(1, 1, "RESSONANCIA", f), _rel(2, 2, "Conduta"), _rel(3, 2),
               _rel(4, None, "ULTRASSONOGRAFIA", j), _rel(5, 2)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: j, 1: f, 4: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3], [4], [5]]


def test_inversao_longe_em_outro_documento_ainda_guarda_a_pagina_do_outro_lado_da_secao():
    # A pilha de CICRANO está invertida. A seção sem nome de JOHN, logo depois
    # da página 1 de FULANA, tem do outro lado a página sem numeração e com
    # título que pode ser a primeira do documento dela: não entra no de FULANA.
    f, c = "FULANA DE TESTE", "CICRANO EXEMPLO"
    for inst in ("LABORATORIO ALFA", "CLINICA BETA"):
        paginas = [laudo(0, 3, 3, inst, "RESSONANCIA"), laudo(1, 1, 3, inst, "RESSONANCIA", c),
                   laudo(2, 2, 3, inst, "RESSONANCIA"), laudo(3, 1, 2, titulo="TOMOGRAFIA", nome=f),
                   laudo(4, 2, 2, titulo="Conduta"), _tc(5, None, "RELATÓRIO MÉDICO")]
        for pacote in (PACOTE, REAL):
            docs = sg.segmentar(paginas, pacote, {1: c, 3: f})
            assert sorted(sorted(indices(d)) for d in docs) == [[0, 1, 2], [3], [4], [5]], inst


def test_pagina_sem_nome_com_o_titulo_repetido_so_entra_colada_e_sem_disputa():
    # FULANA e JOHN com o mesmo exame do mesmo laboratório; a página 1 de JOHN
    # não tem a numeração lida. A página 2 de JOHN, sem nome lido, repete o
    # título e só cabe no grupo de FULANA: longe da página 1 dela, fica
    # sozinha.
    f, j, t = "FULANA DE TESTE", "JOHN DOE", "HEMOGRAMA"
    longe = [_rel(0, 1, t, f), _rel(1, None, t, j), _rel(2, 2, t)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(longe, pacote, {0: f, 1: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]
    # Colada à página 1, sem disputa, entra; com outra página 2 sem nome que
    # também cabe, nenhuma das duas entra.
    colada = [_rel(0, 1, t, f), _rel(1, 2, t), _rel(2, None, t, j)]
    disputa = [_rel(0, 1, t, f), _rel(1, 2, t), _rel(2, None, t, j), _rel(3, 2, t)]
    for pacote in (PACOTE, REAL):
        assert sorted(indices(d) for d in sg.segmentar(colada, pacote, {0: f, 2: j})) == [[0, 1], [2]]
        assert sorted(indices(d) for d in sg.segmentar(disputa, pacote, {0: f, 2: j})) == [[0], [1], [2], [3]]
    # Sem outro paciente no arquivo, o título basta.
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar([_rel(0, 1, t, f), _rel(1, None, "RECEITUÁRIO", f), _rel(2, 2, t)], pacote, {0: f, 1: f})
        assert sorted(indices(d) for d in docs) == [[0, 2], [1]]


def test_pagina_sem_nome_com_titulo_que_so_a_numeracao_poe_no_grupo_sem_titulo_da_a_mesma_prova():
    # A página 3 de CICRANO abre com a seção "CONCLUSÃO" e só cabe, pela
    # numeração, no grupo sem título de FULANA, longe da página 2 dela.
    f, c = "FULANA DE TESTE", "CICRANO EXEMPLO"
    paginas = [_rel(0, 2, None, f), _rel(1, 1, "HEMOGRAMA", c), _rel(2, 3, "CONCLUSÃO")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: f, 1: c})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]


def test_pagina_com_nome_nao_decide_o_grupo_sem_nome_disputado_por_outro_paciente():
    # A página 2 sem título e sem nome cabia nos grupos de FULANA e de JOHN e
    # ficou sozinha. As páginas 3 de JOHN e de FULANA, com nome, cabem nela:
    # a primeira no scan não decide de quem ela é.
    f, j = "FULANA DE TESTE", "JOHN DOE"
    paginas = [_rel(0, 1, "HEMOGRAMA", f), _rel(1, 1, "ULTRASSONOGRAFIA", j), _rel(2, 3, "Conduta", j),
               _rel(3, 2), _rel(4, 3, "Conduta", f)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: f, 1: j, 2: j, 4: f})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3], [4]]
    # Nem a página 2 colada à página 1 sem nome decide, nem a página 3 de um
    # terceiro paciente, que só ela cabe na vaga seguinte.
    paginas = [_rel(0, 2, "HEMOGRAMA", f), _rel(1, 2, "HEMOGRAMA", j), _rel(2, 1, "HEMOGRAMA")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: f, 1: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]
        docs = sg.segmentar(paginas + [_rel(3, 3, "HEMOGRAMA", "CICRANO EXEMPLO")], pacote,
                            {0: f, 1: j, 3: "CICRANO EXEMPLO"})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3]]
    # A outra página 2 que cabe no grupo pode também não ter nome lido: a de
    # FULANA, sem título, não toma a vaga da que repete o título do grupo.
    paginas = [_rel(0, 1, "TOMOGRAFIA"), _rel(1, 2, None, f), _rel(2, 2, "TOMOGRAFIA")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {1: f})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2]]
    # A página sem nome que disputava a vaga também não recebe a seguinte.
    paginas = [_rel(0, 1, "HEMOGRAMA"), _rel(1, 2, "HEMOGRAMA", f), _rel(2, 1, "HEMOGRAMA", j), _rel(3, 2),
               _rel(4, 3, "Exames complementares")]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {1: f, 2: j})
        assert sorted(indices(d) for d in docs) == [[0], [1], [2], [3], [4]]


def test_variante_do_nome_do_mesmo_paciente_nao_tira_a_pagina_sem_titulo_do_grupo():
    # O nome lido na receita difere do da página 1 só por uma letra do OCR: é
    # o mesmo paciente, e a página 3 sem título entra pela numeração.
    f, t = "FULANA DE TESTE", "TOMOGRAFIA"
    paginas = [_rel(0, 1, t, f), _rel(1, 2), _rel(2, None, "RECEITUÁRIO", "FULANA DE TESTF"), _rel(3, 3)]
    for pacote in (PACOTE, REAL):
        docs = sg.segmentar(paginas, pacote, {0: f, 2: "FULANA DE TESTF"})
        assert sorted(indices(d) for d in docs) == [[0, 1, 3], [2]]
        # Outro paciente, com nome que não é variante, ainda tira a página.
        docs = sg.segmentar(paginas, pacote, {0: f, 2: "JOHN DOE"})
        assert sorted(indices(d) for d in docs) == [[0, 1], [2], [3]]


def test_padroes_da_segmentacao_sao_rapidos_em_linhas_adversarias():
    # Pontilhado, tracejado e números repetidos que o OCR devolve, até 200
    # repetições: nenhum padrão do pacote ou da segmentação leva mais de 50 ms
    # por linha. Em ordem crescente, o padrão exponencial reprova sem travar.
    regras = sg._regras(REAL)
    padroes = [p for p, _ in regras.numeracao] + [p for p in (regras.campo, regras.campo_forte, regras.instituicao,
                                                             regras.endereco_abre, regras.endereco_depois) if p]
    padroes += [sg._LINHA_DE_VALOR, sg._SEM_LETRA, sg._DIGITOS]
    unidades = (":", " ", "-", ".", "/", "1", "a", ": ", "1:", "1 ", "1/", "/1", "a ", "1-", "- ", ". ", "de 1 ")
    prefixos = ("", "pagina ", "pag. 1", "folha ", "1/", "paciente", "data da coleta", "laboratorio ", "rua ",
                "x - ", "ca 15-3")
    for n in (20, 40, 200):
        for linha in (p + u * n + f for p in prefixos for u in unidades for f in ("", "!")):
            for padrao in padroes:
                melhor = float("inf")
                for _ in range(3):
                    t0 = time.perf_counter()
                    padrao.search(linha), padrao.fullmatch(linha), padrao.sub("#", linha)
                    melhor = min(melhor, time.perf_counter() - t0)
                    if melhor < 0.05:
                        break
                assert melhor < 0.05, (padrao.pattern[:40], linha[:40], n, melhor)
