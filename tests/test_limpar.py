import dataclasses
import time
from pathlib import Path

import yaml

from scan_to_text import pacote as modulo_pacote
from scan_to_text.entender import limpar as lp
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, Linha, Pagina, Tabela, texto

PACKS = Path(modulo_pacote.RAIZ_PACKS) / "pt-BR"
PACOTE = Pacote("pt-BR", {s: yaml.safe_load((PACKS / f"{s}.yaml").read_text(encoding="utf-8-sig"))
                          for s in ("paginas", "secoes")})
SHA = "cd" * 32


def L(txt, y, camada="corpo", tipo="texto", grupo=-1):
    return Linha(txt, (0.1, y, 0.9, y + 0.02), camada=camada, tipo=tipo, grupo=grupo)


def T(celulas, cabecalho=1, y=0.4):
    confs = tuple(tuple(0.9 + 0.001 * j for j in range(len(linha))) for linha in celulas)
    return Tabela(celulas=tuple(tuple(l) for l in celulas), confiancas=confs,
                  caixa=(0.1, y, 0.9, y + 0.2), linhas_de_cabecalho=cabecalho)


def pag(indice, *blocos):
    return Pagina(SHA, indice, tuple(blocos))


def doc(*paginas, categoria=5):
    return Documento(id=f"{SHA[:12]}-01", paginas=tuple(paginas), titulo="EXAME", categoria=categoria)


def textos_do_corpo(d):
    return [texto(b) for b in d.corpo]


# rodapé ------------------------------------------------------------------------

def test_rodape_do_layout_sai():
    d = lp.limpar(doc(pag(0, L("laudo", 0.3), L("Rua Exemplo, 100 - Tel 0000-0000", 0.95, camada="rodape"))),
                  PACOTE)
    assert textos_do_corpo(d) == ["laudo"]


def test_rodape_plantado_no_corpo_sai_quando_se_repete_em_metade_das_paginas():
    rodape = "Laboratorio Alfa - Rua Exemplo, 100 - CNPJ 00.000.000/0001-00"
    paginas = [pag(i, L(f"conteudo {i}", 0.3), L(rodape, 0.93), L(f"Página {i + 1} de 4", 0.96))
               for i in range(3)]
    paginas.append(pag(3, L("conteudo 3", 0.3), L("assinatura", 0.93)))
    d = lp.limpar(doc(*paginas), PACOTE)
    assert textos_do_corpo(d) == ["conteudo 0", "conteudo 1", "conteudo 2", "conteudo 3", "assinatura"]


def test_rodape_de_pagina_unica_nao_tem_repeticao_e_fica():
    d = lp.limpar(doc(pag(0, L("conteudo", 0.3), L("Rua Exemplo, 100", 0.95))), PACOTE)
    assert textos_do_corpo(d) == ["conteudo", "Rua Exemplo, 100"]


def test_valor_no_pe_da_pagina_nao_e_rodape():
    p1 = pag(0, L("Hemoglobina 12,4 g/dL", 0.86), L("Página 1 de 2", 0.96))
    p2 = pag(1, L("Leucocitos 5.000 /mm3", 0.86), L("Página 2 de 2", 0.96))
    d = lp.limpar(doc(p1, p2), PACOTE)
    assert textos_do_corpo(d) == ["Hemoglobina 12,4 g/dL", "Leucocitos 5.000 /mm3"]
    # O mesmo analito com valores diferentes no pé de cada página não é rodapé.
    p1 = pag(0, L("conteudo 1", 0.3), L("Hemoglobina 12,4 g/dL", 0.90), L("Página 1 de 2", 0.96))
    p2 = pag(1, L("conteudo 2", 0.3), L("Hemoglobina 13,1 g/dL", 0.90), L("Página 2 de 2", 0.96))
    d = lp.limpar(doc(p1, p2), PACOTE)
    assert textos_do_corpo(d) == ["conteudo 1", "Hemoglobina 12,4 g/dL", "conteudo 2", "Hemoglobina 13,1 g/dL"]


def test_rodape_com_a_numeracao_no_meio_da_linha_sai():
    paginas = [pag(i, L(f"conteudo {i}", 0.3), L(f"Impresso em 10/05/2026 - Página {i + 1} de 3", 0.95))
               for i in range(3)]
    d = lp.limpar(doc(*paginas), PACOTE)
    assert textos_do_corpo(d) == ["conteudo 0", "conteudo 1", "conteudo 2"]


# cabeçalho original -----------------------------------------------------------

def test_cabecalho_uma_vez_so_sem_repeticoes_identicas():
    def p(i, extra):
        return pag(i, L("LABORATORIO ALFA", 0.02, camada="cabecalho"),
                   L("Convênio: Plano Exemplo", 0.05, camada="cabecalho"),
                   *extra, L(f"corpo {i}", 0.4))
    d = lp.limpar(doc(p(0, [L("Pedido: 123", 0.08, camada="cabecalho")]), p(1, [])), PACOTE)
    assert d.cabecalho_original == ("LABORATORIO ALFA", "Convênio: Plano Exemplo", "Pedido: 123")
    assert textos_do_corpo(d) == ["corpo 0", "corpo 1"]


def test_cabecalho_posto_no_corpo_reconhecido_por_posicao_e_repeticao():
    def p(i, x):
        return pag(i, L("LABORATORIO ALFA", 0.02), L("Paciente: MARIA EXEMPLO", 0.06),
                   L(f"Página {x} de 2", 0.10), L(f"corpo {i}", 0.4))
    d = lp.limpar(doc(p(0, 1), p(1, 2)), PACOTE)
    assert d.cabecalho_original == ("LABORATORIO ALFA", "Paciente: MARIA EXEMPLO", "Página 1 de 2")
    assert textos_do_corpo(d) == ["corpo 0", "corpo 1"]


def test_linha_do_topo_que_nao_se_repete_fica_no_corpo():
    p1 = pag(0, L("LABORATORIO ALFA", 0.02), L("texto que so aparece aqui", 0.08), L("corpo", 0.4))
    p2 = pag(1, L("LABORATORIO ALFA", 0.02), L("corpo 2", 0.4))
    d = lp.limpar(doc(p1, p2), PACOTE)
    assert d.cabecalho_original == ("LABORATORIO ALFA",)
    assert textos_do_corpo(d) == ["texto que so aparece aqui", "corpo", "corpo 2"]


def test_corpo_segue_a_ordem_das_paginas_do_documento():
    t = T([("Exame", "Resultado"), ("PSA", "0,5")])
    d = lp.limpar(doc(pag(7, L("b", 0.3), t), pag(2, L("c", 0.3))), PACOTE)
    assert textos_do_corpo(d) == ["b", texto(t), "c"]


def test_limpar_so_preenche_cabecalho_e_corpo():
    original = doc(pag(0, L("x", 0.3)))
    d = lp.limpar(original, PACOTE)
    assert dataclasses.replace(d, cabecalho_original=(), corpo=()) == original


# bibliografia -----------------------------------------------------------------

def test_bibliografia_sai_ate_o_fim():
    p = pag(0, L("Relatorio de radioterapia", 0.2), L("Conduta proposta.", 0.3),
            L("Referências bibliográficas", 0.5, tipo="secao"),
            L("1. Silva J, Souza A, et al. Hipofracionamento. Rev Exemplo. 2020;12(3):45-50.", 0.55),
            L("2. Doe J. Radiotherapy. J Example Oncol. 2019;7:1-9.", 0.6))
    d = lp.limpar(doc(p, pag(1, L("continua a lista", 0.1), T([("a", "b")]))), PACOTE)
    assert textos_do_corpo(d) == ["Relatorio de radioterapia", "Conduta proposta."]


def test_titulo_referencias_sem_citacao_nao_e_bibliografia():
    p = pag(0, L("Referências", 0.3), L("Encaminhado pela unidade basica", 0.35))
    d = lp.limpar(doc(p), PACOTE)
    assert textos_do_corpo(d) == ["Referências", "Encaminhado pela unidade basica"]


def test_valores_de_referencia_nao_sao_bibliografia_e_ficam_fora_do_laboratorio():
    p = pag(0, L("Ecocardiograma", 0.2),
            L("Valores de referência", 0.3),
            L("Fração de ejeção: 55 a 70 % (2019; diretriz)", 0.35),
            L("Bibliografia", 0.6),
            L("Lang RM et al. Recommendations. J Am Soc Echocardiogr. 2015;28:1-39.", 0.65))
    d = lp.limpar(doc(p, categoria=5), PACOTE)
    assert textos_do_corpo(d) == ["Ecocardiograma", "Valores de referência",
                                  "Fração de ejeção: 55 a 70 % (2019; diretriz)"]


def _laudo_que_cita_a_diretriz(categoria):
    p1 = pag(0, L("Colesterol total 180 mg/dL", 0.2), L("Referências:", 0.25),
             L("Desejavel: < 190 mg/dL (Diretriz Brasileira de Dislipidemias, 2017; SBC)", 0.3),
             L("Glicose 105 mg/dL", 0.4))
    p2 = pag(1, L("Hemoglobina glicada 5,6 %", 0.2), T([("Exame", "Resultado"), ("TSH", "2,1 mUI/L")]))
    return doc(p1, p2, categoria=categoria)


def test_referencias_que_citam_a_diretriz_nao_sao_bibliografia():
    d = lp.limpar(_laudo_que_cita_a_diretriz(3), PACOTE)
    assert textos_do_corpo(d) == ["Colesterol total 180 mg/dL", "Glicose 105 mg/dL",
                                  "Hemoglobina glicada 5,6 %", "Exame | Resultado\nTSH | 2,1 mUI/L"]
    original = _laudo_que_cita_a_diretriz(5)
    d = lp.limpar(original, PACOTE)
    assert list(d.corpo) == [b for p in original.paginas for b in p.blocos]


def test_bibliografia_seguida_de_resultado_nao_e_cortada():
    p = pag(0, L("Conduta proposta.", 0.2), L("Referências bibliográficas", 0.5),
            L("1. Doe J et al. Radiotherapy. J Example Oncol. 2019;7:1-9.", 0.55),
            L("Glicose 105 mg/dL", 0.7))
    original = doc(p)
    assert list(lp.limpar(original, PACOTE).corpo) == list(p.blocos)


def test_bibliografia_com_citacao_quebrada_em_linhas_sai():
    p = pag(0, L("Conduta proposta.", 0.2), L("Referências", 0.5),
            L("1. Silva J, Souza A, Pereira B, Lima C,", 0.55),
            L("Hipofracionamento em mama. Rev Exemplo.", 0.57),
            L("2020;12(3):45-50.", 0.59))
    assert textos_do_corpo(lp.limpar(doc(p), PACOTE)) == ["Conduta proposta."]


# valores de referência ------------------------------------------------------------

def _laudo_de_laboratorio(categoria):
    tabela = T([("Exame", "Resultado", "Unidade", "Valores de referência"),
                ("Hemoglobina", "12,4", "g/dL", "12,0 a 15,5"),
                ("Hematocrito", "38", "%", "35 a 45")])
    return doc(pag(0,
                   L("HEMOGRAMA", 0.15, tipo="titulo"),
                   tabela,
                   L("Plaquetas: 250.000 /mm3", 0.62),
                   L("Valores de referência:", 0.65),
                   L("Homens: 150.000 a 400.000 /mm3", 0.67),
                   L("Mulheres: 150.000 a 450.000 /mm3", 0.69),
                   L("Método: Automatizado", 0.71),
                   L("Glicose 105 mg/dL   VR: 70 a 99 mg/dL", 0.74),
                   L("Colesterol 180 mg/dL (VR: até 190) Material: Soro", 0.76),
                   L("Resultado anterior: 11,9 g/dL", 0.78),
                   L("Referência", 0.80),
                   L("< 5 anos: até 10", 0.82),
                   L("PSA livre < 0,1 ng/mL", 0.84)),
               categoria=categoria)


def test_laboratorio_tira_coluna_bloco_e_trecho_de_referencia():
    original = _laudo_de_laboratorio(3)
    d = lp.limpar(original, PACOTE)
    tabela = d.corpo[1]
    assert tabela.celulas == (("Exame", "Resultado", "Unidade"),
                              ("Hemoglobina", "12,4", "g/dL"),
                              ("Hematocrito", "38", "%"))
    assert tabela.confiancas == tuple(c[:3] for c in original.paginas[0].blocos[1].confiancas)
    assert tabela.linhas_de_cabecalho == 1
    assert [texto(b) for b in d.corpo if isinstance(b, Linha)] == [
        "HEMOGRAMA",
        "Plaquetas: 250.000 /mm3",
        "Método: Automatizado",
        "Glicose 105 mg/dL",
        "Colesterol 180 mg/dL Material: Soro",
        "Resultado anterior: 11,9 g/dL",
        "PSA livre < 0,1 ng/mL",
    ]
    tudo = " ".join(textos_do_corpo(d)).casefold()
    assert "refer" not in tudo and "vr" not in tudo.split()


def test_fora_do_laboratorio_nada_de_referencia_sai():
    original = _laudo_de_laboratorio(2)
    d = lp.limpar(original, PACOTE)
    assert list(d.corpo) == list(original.paginas[0].blocos)


def test_laboratorio_linha_de_tabela_rotulada_e_faixas_seguintes_saem():
    tabela = T([("Exame", "Resultado", "Unidade"),
                ("Hemoglobina", "12,4", "g/dL"),
                ("Valor de referência", "12,0 a 15,5", "g/dL"),
                ("Homens", "13,5 a 17,5", "g/dL"),
                ("Leucocitos", "5.000", "/mm3"),
                ("Obs", "VR: 4.000 a 11.000", "")])
    d = lp.limpar(doc(pag(0, tabela), categoria=3), PACOTE)
    assert d.corpo[0].celulas == (("Exame", "Resultado", "Unidade"),
                                  ("Hemoglobina", "12,4", "g/dL"),
                                  ("Leucocitos", "5.000", "/mm3"),
                                  ("Obs", "", ""))
    assert len(d.corpo[0].confiancas) == 4


def test_laboratorio_rotulo_sozinho_antes_de_tabela_de_faixas():
    faixas = T([("Homens", "13,5 a 17,5 g/dL"), ("Mulheres", "12,0 a 15,5 g/dL")], cabecalho=0)
    d = lp.limpar(doc(pag(0, L("Hemoglobina 12,4 g/dL", 0.2), L("Valores de Referência", 0.3), faixas,
                          L("Hematocrito 38 %", 0.6)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Hemoglobina 12,4 g/dL", "Hematocrito 38 %"]


def test_laboratorio_rotulo_com_igual_ou_traco_e_timbre_com_a_palavra():
    p = pag(0, L("Laboratorio de Referência Exemplo", 0.2),
            L("Ureia 30 mg/dL VR = 15 a 40", 0.3),
            L("Creatinina 0,9 mg/dL - VR - 0,6 a 1,1", 0.35))
    d = lp.limpar(doc(p, categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Laboratorio de Referência Exemplo", "Ureia 30 mg/dL",
                                  "Creatinina 0,9 mg/dL"]


def test_laboratorio_de_referencia_no_fim_da_linha_nao_e_rotulo():
    linhas = ["Glicose 105 mg/dL",
              "Amostra encaminhada ao laboratorio de referência",
              "Acima de 60 anos o resultado deve ser repetido",
              "Referência do pedido: 12345"]
    p = pag(0, *(L(t, 0.2 + 0.05 * i) for i, t in enumerate(linhas)))
    assert textos_do_corpo(lp.limpar(doc(p, categoria=3), PACOTE)) == linhas


def test_laboratorio_referencia_como_campo_de_pedido_na_tabela_fica():
    t = T([("Referência do pedido", "12345"), ("Glicose", "105 mg/dL")], cabecalho=0)
    d = lp.limpar(doc(pag(0, t), categoria=3), PACOTE)
    assert d.corpo[0].celulas == t.celulas


def test_laboratorio_rotulo_que_fecha_a_linha_antes_de_faixa_numerica():
    p = pag(0, L("Glicose 105 mg/dL   Valores de referência", 0.2),
            L("70 a 99 mg/dL", 0.25), L("Ureia 30 mg/dL", 0.3))
    assert textos_do_corpo(lp.limpar(doc(p, categoria=3), PACOTE)) == ["Glicose 105 mg/dL", "Ureia 30 mg/dL"]


def test_laboratorio_coluna_de_referencia_sem_cabecalho_declarado_e_com_data():
    t = T([("Exame", "Resultado 02/04/2026", "Valores de referência"),
           ("Hemoglobina", "12,4 g/dL", "12,0 a 15,5 g/dL")], cabecalho=0)
    d = lp.limpar(doc(pag(0, t), categoria=3), PACOTE)
    assert d.corpo[0].celulas == (("Exame", "Resultado 02/04/2026"), ("Hemoglobina", "12,4 g/dL"))
    assert d.corpo[0].confiancas == tuple(c[:2] for c in t.confiancas)


def test_laboratorio_rotulo_na_primeira_coluna_sem_cabecalho_e_linha_e_nao_coluna():
    for primeira in [("Valor de referência", "70 a 99 mg/dL"), ("Valor de referência", "Negativo")]:
        t = T([primeira, ("Resultado", "Positivo")], cabecalho=0)
        d = lp.limpar(doc(pag(0, t), categoria=3), PACOTE)
        assert d.corpo[0].celulas == (("Resultado", "Positivo"),), primeira


def test_referencias_no_laboratorio_so_e_bibliografia_com_citacao_de_fato():
    p = pag(0, L("PSA total 1,2 ng/mL", 0.2), L("Referências", 0.5),
            L("1. Catalona WJ et al. N Engl J Med. 1991;324:1156-61.", 0.55))
    assert textos_do_corpo(lp.limpar(doc(p, categoria=3), PACOTE)) == ["PSA total 1,2 ng/mL"]
    p = pag(0, L("PSA total 1,2 ng/mL", 0.2), L("Referências", 0.5),
            L("Ate 4,0 ng/mL (Diretriz Exemplo, 2017; SBU)", 0.55), L("Ureia 30 mg/dL", 0.6))
    assert textos_do_corpo(lp.limpar(doc(p, categoria=3), PACOTE)) == ["PSA total 1,2 ng/mL", "Ureia 30 mg/dL"]


def test_bibliografia_no_laboratorio_nao_vira_bloco_de_referencia():
    p = pag(0, L("PSA total 1,2 ng/mL", 0.2),
            L("Referências bibliográficas", 0.5),
            L("Catalona WJ et al. N Engl J Med. 1991;324:1156-61.", 0.55))
    d = lp.limpar(doc(p, categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["PSA total 1,2 ng/mL"]


def test_faixa_com_rotulo_livre_dentro_do_bloco_de_referencia_sai():
    # Forma do OCR de verdade: as faixas de glicemia vêm com o nome do nível
    # antes dos dois-pontos, numa linha cada, depois do rótulo do bloco.
    d = lp.limpar(doc(pag(0, L("Glicose: 87 mg/dL", 0.20), L("Faixa de referência:", 0.22),
                          L("Normal: 70 a 99 mg/dL", 0.24),
                          L("Glicemia de jejum alterada: 100 a 125 mg/dL", 0.26),
                          L("Diabetes: igual ou superior a 126 mg/dL", 0.28),
                          L("CREATININA", 0.32, tipo="secao"), L("Creatinina: 0,93 mg/dL", 0.34)),
                  categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Glicose: 87 mg/dL", "CREATININA", "Creatinina: 0,93 mg/dL"]


def test_resultado_com_dois_pontos_depois_do_bloco_fica():
    d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20), L("Normal: 70 a 99 mg/dL", 0.22),
                          L("Glicose: 87 mg/dL", 0.24)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Glicose: 87 mg/dL"]


def test_resultado_com_comparador_depois_de_referencia_fica():
    # Resultado abaixo do limite de detecção se escreve com comparador ou com
    # "inferior a" logo depois dos dois-pontos: é resultado, não faixa.
    d = lp.limpar(doc(pag(0, L("TSH: 2,1 µUI/mL   VR: 0,4 a 4,0", 0.20),
                          L("Anti-TPO: < 10 UI/mL   VR: inferior a 35", 0.22),
                          L("Tireoglobulina: inferior a 0,1 ng/mL", 0.24),
                          L("Beta HCG: inferior a 5,0 mUI/mL", 0.26),
                          L("Proteína C reativa: < 0,5 mg/dL", 0.28)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["TSH: 2,1 µUI/mL", "Anti-TPO: < 10 UI/mL",
                                  "Tireoglobulina: inferior a 0,1 ng/mL",
                                  "Beta HCG: inferior a 5,0 mUI/mL", "Proteína C reativa: < 0,5 mg/dL"]
    d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20), L("Normal: inferior a 4,0 ng/mL", 0.22),
                          L("PSA livre: < 0,01 ng/mL", 0.24)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["PSA livre: < 0,01 ng/mL"]


def test_resultado_com_faixa_ou_data_dentro_do_bloco_de_referencia_fica():
    # Sedimento de urina escreve o resultado como faixa ("0 a 1 por campo"); a
    # data leva hífen. Só a linha de nível conhecido do pacote, com faixa de
    # verdade depois dos dois-pontos, continua o bloco.
    d = lp.limpar(doc(pag(0, L("Leucócitos: 2 a 4 por campo   VR: até 5 por campo", 0.20),
                          L("Hemácias: 0 a 1 por campo   VR: até 3 por campo", 0.22),
                          L("Células epiteliais: 3 a 5 por campo", 0.24)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Leucócitos: 2 a 4 por campo", "Hemácias: 0 a 1 por campo",
                                  "Células epiteliais: 3 a 5 por campo"]
    ficam = ["Hemácias: 0 a 1 por campo", "Células epiteliais: 3 a 5 por campo",
             "Tolerância oral à glicose: 140 mg/dL", "Risco cardiovascular: baixo",
             "Risco calculado: 1/5000", "Data da coleta: 01-02-2026",
             "Anti-HBs: igual ou superior a 10 mUI/mL", "Bastões: de 0 a 1 %"]
    for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
        for resultado in ficam:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(resultado, 0.22)), categoria=3), PACOTE)
            assert textos_do_corpo(d)[-1] == resultado, (abre, resultado)


def test_coluna_de_referencia_no_cabecalho_nao_leva_o_resultado_com_faixa():
    d = lp.limpar(doc(pag(0, L("SEDIMENTOSCOPIA   Resultado   Valores de referência", 0.18),
                          L("Leucócitos: 2 a 4 por campo   até 5 por campo", 0.20),
                          L("Hemácias: 0 a 1 por campo   até 3 por campo", 0.22),
                          L("Células epiteliais: raras", 0.24)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["SEDIMENTOSCOPIA   Resultado",
                                  "Leucócitos: 2 a 4 por campo   até 5 por campo",
                                  "Hemácias: 0 a 1 por campo   até 3 por campo",
                                  "Células epiteliais: raras"]


def test_nivel_conhecido_com_faixa_depois_de_referencia_em_linha_sai():
    d = lp.limpar(doc(pag(0, L("Glicose: 87 mg/dL   VR: 70 a 99", 0.20),
                          L("Glicemia de jejum alterada: 100 a 125 mg/dL", 0.22),
                          L("Tolerância diminuída à glicose: 140 a 199 mg/dL", 0.24),
                          L("Diabetes: igual ou superior a 126 mg/dL", 0.26),
                          L("Risco intermediário: 10 a 20 %", 0.28),
                          L("Ureia: 30 mg/dL", 0.30)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Glicose: 87 mg/dL", "Ureia: 30 mg/dL"]


def test_nivel_conhecido_com_comparador_dentro_do_bloco_sai():
    d = lp.limpar(doc(pag(0, L("Hemoglobina glicada: 5,4 %", 0.20), L("Valores de referência:", 0.22),
                          L("Normal: < 5,7 %", 0.24), L("Pré-diabetes: 5,7 a 6,4 %", 0.26),
                          L("Diabetes: ≥ 6,5 %", 0.28), L("Deficiência: < 20 ng/mL", 0.30),
                          L("Insuficiência: 20 a 29 ng/mL", 0.32)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Hemoglobina glicada: 5,4 %"]


def test_palavra_de_faixa_com_resultado_depois_dos_dois_pontos_fica():
    # "Idade", "Fase" e "Acima" abrem faixa por idade ou nível, mas depois dos
    # dois-pontos o que vem é resultado, sem intervalo nem comparação.
    ficam = ["Idade gestacional: 12 semanas", "Fase do ciclo: lútea", "Acima do peso: sim",
             "Idade: 45 anos"]
    saem = ["Normal: 70 a 99 mg/dL", "Homens: 13,5 a 17,5", "Desejável: < 200",
            "Fase folicular: 2 a 10", "Adultos: até 200", "Mulheres (pós-menopausa): 2 a 10",
            "Homens acima de 50 anos: até 4,0", "Normal: inferior a 4,0 ng/mL", "Normal", "Ate 4,0 ng/mL"]
    for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
        for resultado in ficam:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(resultado, 0.22)), categoria=3), PACOTE)
            assert textos_do_corpo(d)[-1] == resultado, (abre, resultado)
        for faixa in saem:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(faixa, 0.22), L("Ureia: 30 mg/dL", 0.24)),
                              categoria=3), PACOTE)
            assert faixa not in textos_do_corpo(d), (abre, faixa)
            assert textos_do_corpo(d)[-1] == "Ureia: 30 mg/dL", (abre, faixa)


def test_idade_escrita_com_letra_ou_com_intervalo_de_tempo_fica_depois_de_referencia():
    # "45a 2m" é idade em anos e meses, não o intervalo "45 a 2"; o intervalo
    # seguido de unidade de tempo, depois de "Idade", é o resultado. A faixa
    # por idade com outro dois-pontos depois ("1 a 10 anos: até 300") sai.
    ficam = ["Idade: 45a 2m", "Idade: 45a 2m 10d", "Idade gestacional: 12-13 semanas",
             "Idade gestacional: 12 a 13 semanas", "Idade: 2-3 meses", "Idade: 45a 2m   Coleta: 01-10-2026",
             "Idade gestacional: 12-13 semanas   Data da coleta: 01-10-2026"]
    saem = ["Idade: 1 a 10 anos: até 300", "40 a 49 anos: até 2,5", "Idade: 70 a 99", "Homens: 45 a 52"]
    for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
        for resultado in ficam:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(resultado, 0.22), L("Ureia: 30 mg/dL", 0.24)),
                              categoria=3), PACOTE)
            assert textos_do_corpo(d)[-2:] == [resultado, "Ureia: 30 mg/dL"], (abre, resultado)
        for faixa in saem:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(faixa, 0.22), L("Ureia: 30 mg/dL", 0.24)),
                              categoria=3), PACOTE)
            assert textos_do_corpo(d)[-1:] == ["Ureia: 30 mg/dL"] and faixa not in textos_do_corpo(d), (abre, faixa)
    # A idade na linha seguinte não faz do "referência" do fim da linha um
    # rótulo de valores de referência.
    linhas = ["Amostra encaminhada ao laboratorio de referência", "Idade: 45a 2m", "Ureia: 30 mg/dL"]
    p = pag(0, *(L(t, 0.2 + 0.05 * i) for i, t in enumerate(linhas)))
    assert textos_do_corpo(lp.limpar(doc(p, categoria=3), PACOTE)) == linhas


def test_faixa_depois_de_outros_dois_pontos_ou_antes_deles_sai():
    # A faixa pode vir depois de um segundo rótulo (sexo e fase, gestação e
    # trimestre) ou já no começo, com o comparador. A linha que sai não fecha
    # o bloco: a faixa seguinte também sai.
    saem = ["Gestantes: 1º trimestre: 0,1 a 2,5 mUI/L", "Mulheres: Fase folicular: 3,5 a 12,5 mUI/mL",
            "Mulheres: pós-menopausa: 25,8 a 134,8", "Crianças: 1 a 10 anos: até 300",
            "Inferior a 0,9: Não reagente", "Superior a 1,1: Reagente", "Acima de 1,1: Reagente",
            "Até 1,0: não reagente", "Normal até 1:80", "Fase lútea: 1,7 — 15,0",
            "Gestantes: 1º trimestre: 0.1 - 2.5 mUI/L", "Gestantes: semana 12: 1 a 5"]
    ficam = ["Idade gestacional: 12 semanas e 3 dias", "Fase: lútea", "Acima do esperado para a idade: sim"]
    for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
        for faixa in saem:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(faixa, 0.22), L("Homens: 13,5 a 17,5 g/dL", 0.24),
                                  L("Ureia: 30 mg/dL", 0.26)), categoria=3), PACOTE)
            assert textos_do_corpo(d)[-1:] == ["Ureia: 30 mg/dL"], (abre, faixa)
            assert faixa not in textos_do_corpo(d), (abre, faixa)
            assert "Homens: 13,5 a 17,5 g/dL" not in textos_do_corpo(d), (abre, faixa)
        for resultado in ficam:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(resultado, 0.22)), categoria=3), PACOTE)
            assert textos_do_corpo(d)[-1] == resultado, (abre, resultado)


def test_resultado_com_intervalo_ou_data_no_meio_da_linha_fica_depois_de_referencia():
    # A faixa só conta logo depois dos dois-pontos, colada à palavra de sexo ou
    # nível, ou no comparador que fecha o rótulo. Intervalo, comparação ou data
    # no meio do resultado não fazem dele faixa.
    ficam = ["Fase do ciclo: lútea (dia 21 a 23)", "Idade gestacional: 12 semanas (11 a 14 pela DUM)",
             "Fase do ciclo: lútea, até 14 dias do ciclo", "Mulheres: sim, gestante de 10 a 12 semanas",
             "Idade materna: 35 anos (risco < 1:250)", "Idade: 45 anos   Data da coleta: 2026-10-01",
             "Idade: 45 anos   Coleta: 01-10-2026", "Idade: 45 anos  Hora: 08:30 - 09:15",
             "Acima de 40 anos: sim", "Gestante de 10 a 12 semanas: sim",
             "Idade: 45 anos; Glicose: 70 a 99 mg/dL", "Gestante: sim   Data da coleta: 01-10-2026",
             "Gestante: não   Coleta: 2026-10-01", "Feminino   Idade: 45 anos   Coleta: 01-10-2026",
             "Mulheres: sim   Hora: 08:30 - 09:15"]
    for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
        for resultado in ficam:
            d = lp.limpar(doc(pag(0, L(abre, 0.20), L(resultado, 0.22), L("Ureia: 30 mg/dL", 0.24)),
                              categoria=3), PACOTE)
            assert textos_do_corpo(d)[-2:] == [resultado, "Ureia: 30 mg/dL"], (abre, resultado)


def test_sexo_ou_nivel_sozinho_com_dois_pontos_abre_a_faixa_da_linha_seguinte():
    d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20), L("Homens:", 0.22), L("13,5 a 17,5 g/dL", 0.24),
                          L("Mulheres (pós-menopausa):", 0.26), L("12,0 a 15,5 g/dL", 0.28),
                          L("Ureia: 30 mg/dL", 0.30)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Ureia: 30 mg/dL"]
    # Rótulo de resultado sozinho fica, e o valor da linha seguinte também.
    d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20), L("Homens: 13,5 a 17,5 g/dL", 0.22),
                          L("Idade:", 0.24), L("45 anos", 0.26)), categoria=3), PACOTE)
    assert textos_do_corpo(d) == ["Idade:", "45 anos"]


def test_celula_de_faixa_com_dois_pontos_sai_da_tabela_depois_do_rotulo():
    for linhas in ([("Homens:", "13,5 a 17,5"), ("Mulheres:", "12,0 a 15,5")],
                   [("Inferior a 0,9:", "Não reagente"), ("Superior a 1,1:", "Reagente")]):
        d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20), T(linhas, cabecalho=0)), categoria=3),
                      PACOTE)
        assert textos_do_corpo(d) == [], linhas
    d = lp.limpar(doc(pag(0, L("Valores de referência:", 0.20),
                          T([("Idade gestacional:", "12 semanas")], cabecalho=0)), categoria=3), PACOTE)
    assert "Idade gestacional:" in textos_do_corpo(d)[0]


# tempo -------------------------------------------------------------------------

# Um padrão com retrocesso exponencial leva segundos ou horas numa linha de
# pontilhado lida como dois-pontos, e o arquivo inteiro estoura o prazo.
LIMITE_POR_LINHA_S = 0.05


def _melhor_tempo(funcao, vezes=3):
    melhor = float("inf")
    for _ in range(vezes):
        t0 = time.perf_counter()
        funcao()
        melhor = min(melhor, time.perf_counter() - t0)
        if melhor < LIMITE_POR_LINHA_S:
            break
    return melhor


def test_linha_de_sexo_ou_nivel_com_muitos_dois_pontos_depois_de_referencia_e_rapida_e_fica():
    # Em ordem crescente: o padrão exponencial reprova na primeira, sem travar.
    for n in (20, 40, 200):
        for linha in ("Normal " + ": " * n + "ver laudo anexo", "Homens" + ": a" * n + " fim",
                      "Mulheres:" + " x:" * n + " y", "Normal " + ":" * n + " nada",
                      "Desejável" + " 1:" * n + " x"):
            for abre in ("Glicose: 87 mg/dL   VR: 70 a 99", "Valores de referência:"):
                documento = doc(pag(0, L(abre, 0.20), L(linha, 0.22), L("Ureia: 30 mg/dL", 0.24)), categoria=3)
                tempo = _melhor_tempo(lambda: lp.limpar(documento, PACOTE))
                assert tempo < LIMITE_POR_LINHA_S, (n, linha[:30], abre, tempo)
                assert textos_do_corpo(lp.limpar(documento, PACOTE))[-2:] == [linha, "Ureia: 30 mg/dL"], (n, linha[:30])
            documento = doc(pag(0, L("Valores de referência:", 0.20), T([(linha, "x")], cabecalho=0)), categoria=3)
            tempo = _melhor_tempo(lambda: lp.limpar(documento, PACOTE))
            assert tempo < LIMITE_POR_LINHA_S, (n, linha[:30], "tabela", tempo)


def _linhas_adversarias(prefixos, n):
    unidades = (":", " ", "-", ".", "1", "a", ": ", " :", "1:", ":1", "1 ", "a ", "1-", "- ", ". ", "1.", ":-",
                "a:", "1 a ", "1,5 ", "de 1 ", "(1) ")
    return [p + u * n + f for p in prefixos for u in unidades for f in ("", "!")]


def test_padroes_da_limpeza_sao_rapidos_em_linhas_adversarias():
    # Pontilhado, tracejado e números repetidos que o OCR devolve: até 200
    # repetições, depois de cada palavra que abre uma faixa ou um rótulo.
    regras = lp._regras(PACOTE)
    padroes = [p for campo in dataclasses.fields(regras) for p in
               (getattr(regras, campo.name) if isinstance(getattr(regras, campo.name), tuple)
                else (getattr(regras, campo.name),)) if hasattr(p, "search")]
    assert len(padroes) >= 15
    prefixos = ("", "normal", "homens ", "idade", "idade gestacional:", "gestantes: ", "ate ", "acima de ",
                "inferior a 1", "diabetes", "risco", "1 a 2 ", "valores de referencia", "resultado", "doi ", "2020")
    for n in (20, 40, 200):
        for linha in _linhas_adversarias(prefixos, n):
            for padrao in padroes:
                tempo = _melhor_tempo(lambda: (padrao.search(linha), padrao.match(linha)))
                assert tempo < LIMITE_POR_LINHA_S, (padrao.pattern[:40], linha[:40], n, tempo)
