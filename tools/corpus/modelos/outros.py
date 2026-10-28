"""Categoria 5: guias, pedidos, cardiologia, receitas e anotação manuscrita."""
from __future__ import annotations

import random
from datetime import date

from corpus import ficticio
from corpus.folha import (ALTURA, LARGURA, MARGEM, NEGRITO, REGULAR, UTIL, Folha, assinatura,
                          cabecalho, campos, manuscrito, paragrafo, pautas, rodape, secao, tabela,
                          titulo)
from corpus.modelos import (DocGerado, dmy, estilo_numeracao, extenso, formato_data, identificacao,
                            idade, modelo, num, numeracao)

EXAMES_PEDIDOS = ("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX", "RESSONÂNCIA MAGNÉTICA DA PELVE",
                  "PET-CT COM 18F-FDG", "CINTILOGRAFIA ÓSSEA", "MAMOGRAFIA BILATERAL",
                  "HEMOGRAMA COMPLETO", "ULTRASSONOGRAFIA DE ABDOME TOTAL")


def _caixas(f: Folha, linha: list[tuple[str, str, float]], altura: float = 25) -> None:
    """Uma fila de campos da guia TISS: rótulo pequeno em cima, valor embaixo."""
    x = MARGEM
    topo = f.y + 9
    for rotulo, valor, fracao in linha:
        largura = fracao * UTIL
        f.retangulo(x, topo - altura, largura, altura, 0.6)
        f.texto(x + 3, topo - 8, rotulo, REGULAR, 6.3)
        f.texto(x + 3, topo - 20, valor, NEGRITO, 8.8)
        x += largura
    f.descer(altura + 3)


@modelo(5)
def guia_sadt(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    operadora = ficticio.instituicao(rng, "hospital")
    convenio = rng.choice([c for c in ficticio.CONVENIOS if c != "Particular"])
    operadora = ficticio.Instituicao(convenio, operadora.rua, operadora.numero, operadora.bairro,
                                     operadora.cidade, operadora.uf, operadora.cep, operadora.telefone,
                                     operadora.cnes)
    contratado = ficticio.instituicao(rng, "hospital")
    solicitante = ficticio.medico(rng, operadora.uf)
    exames = rng.sample(EXAMES_PEDIDOS, rng.randrange(1, 3))
    autorizacao = ficticio.outra_data(rng, dia, 1, 10)
    validade_senha = ficticio.outra_data(rng, dia, 30, 60)
    validade_carteira = ficticio.outra_data(rng, dia, 200, 700)
    pares, principal, nomes, _ = identificacao(rng, pessoa, op, ("Nome",), chance_nascimento=0)
    nome = pares[0][1] if pares else ""
    tit = "GUIA DE SERVIÇO PROFISSIONAL / SERVIÇO AUXILIAR DE DIAGNÓSTICO E TERAPIA - SP/SADT"
    f = Folha()
    cabecalho(f, operadora, 0)
    titulo(f, tit, tam=10.5)
    _caixas(f, [("1 - Registro ANS", str(rng.randrange(300000, 420000)), 0.2),
                ("2 - Nº Guia no Prestador", str(rng.randrange(10 ** 8, 10 ** 9)), 0.4),
                ("3 - Número da Guia Principal", "", 0.4)])
    _caixas(f, [("4 - Data da Autorização", dmy(autorizacao), 0.3),
                ("5 - Senha", str(rng.randrange(10 ** 7, 10 ** 8)), 0.3),
                ("6 - Data de Validade da Senha", dmy(validade_senha), 0.4)])
    secao(f, "Dados do Beneficiário", tam=8)
    _caixas(f, [("8 - Número da Carteira", str(rng.randrange(10 ** 12, 10 ** 13)), 0.35),
                ("9 - Validade da Carteira", dmy(validade_carteira), 0.25),
                ("12 - Atendimento a RN", "N", 0.4)])
    _caixas(f, [("10 - Nome", nome, 1.0)])
    secao(f, "Dados do Solicitante", tam=8)
    _caixas(f, [("13 - Nome do Contratado", contratado.nome, 0.55),
                ("14 - Código na Operadora", str(rng.randrange(10 ** 5, 10 ** 6)), 0.45)])
    _caixas(f, [("15 - Nome do Profissional Solicitante", solicitante.tratado, 0.55),
                ("16 - Conselho", "CRM", 0.12), ("17 - Número no Conselho",
                                                 solicitante.conselho.split()[-1], 0.2),
                ("18 - UF", operadora.uf, 0.13)])
    secao(f, "Dados da Solicitação / Procedimentos e Exames Solicitados", tam=8)
    _caixas(f, [("21 - Caráter do Atendimento", "1 - Eletiva", 0.3),
                ("22 - Data da Solicitação", dmy(dia), 0.3),
                ("23 - Indicação Clínica", "Estadiamento oncológico", 0.4)])
    tabela(f, [("24 - Tabela", "25 - Código do Procedimento", "26 - Descrição", "27 - Qtde.")] +
           [("22", str(rng.randrange(40000000, 41999999)), exame, "1") for exame in exames],
           (0.12, 0.26, 0.5, 0.12), tam=8)
    f.descer(20)
    f.linha(MARGEM, f.y, MARGEM + 200, f.y, 0.5)
    f.linha(LARGURA - MARGEM - 200, f.y, LARGURA - MARGEM, f.y, 0.5)
    f.descer(10)
    f.texto(MARGEM, f.y, "Assinatura do Profissional Solicitante", REGULAR, 7)
    f.texto(LARGURA - MARGEM - 200, f.y, "Assinatura do Beneficiário ou Responsável", REGULAR, 7)
    rodape(f, None, None, None)
    return DocGerado("guia_sadt", tit, 5, convenio, None, [validade_senha, validade_carteira], principal,
                     nomes, [f], False, pedido=True, medicos=[solicitante.nome])


@modelo(5)
def pedido_exames(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    medico = ficticio.medico(rng, inst.uf)
    exames = rng.sample(EXAMES_PEDIDOS, rng.randrange(2, 5))
    tit = rng.choice(("SOLICITAÇÃO DE EXAMES", "PEDIDO DE EXAMES", "REQUISIÇÃO DE EXAMES",
                      f"PEDIDO DE EXAME - {exames[0]}"))
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    titulo(f, tit)
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares.append(("Convênio", rng.choice(ficticio.CONVENIOS)))
    campos(f, pares, moldura=True)
    secao(f, "Solicito:")
    for exame in exames:
        paragrafo(f, f"- {exame.capitalize()}", recuo=10)
    secao(f, "Indicação clínica")
    paragrafo(f, "Estadiamento e seguimento de neoplasia maligna em tratamento.")
    f.descer(8)
    paragrafo(f, f"{inst.cidade}, {extenso(dia)}.", alinhamento="direita")
    assinatura(f, medico, "Oncologia Clínica", rng)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1))
    return DocGerado("pedido_exames", tit, 5, inst.nome, dia, [nasc] if nasc else [], principal, nomes,
                     [f], False, pedido=True, medicos=[medico.nome])


@modelo(5)
def ecocardiograma(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "cardiologia")
    cardiologista, solicitante = ficticio.medico(rng, inst.uf), ficticio.medico(rng, inst.uf)
    tit = rng.choice(("ECOCARDIOGRAMA TRANSTORÁCICO", "ECODOPPLERCARDIOGRAMA TRANSTORÁCICO"))
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares += [("Data do exame", formato_data(rng, dia)), ("Solicitante", solicitante.tratado),
              ("Peso", f"{rng.randrange(50, 98)} kg"), ("Altura", f"{num(rng.randrange(150, 190) / 100, 2)} m")]
    campos(f, pares)
    titulo(f, tit)
    medidas = (("Raiz da aorta", "mm", 24, 36, "20 a 37"), ("Átrio esquerdo", "mm", 28, 40, "19 a 40"),
               ("Septo interventricular", "mm", 7, 11, "6 a 11"),
               ("Parede posterior", "mm", 7, 11, "6 a 10"),
               ("Fração de ejeção (Teichholz)", "%", 55, 72, "52 a 72"))
    linhas = [(nome, str(rng.randrange(a, b + 1)), unidade, ref) for nome, unidade, a, b, ref in medidas]
    tabela(f, [("Medida", "Valor", "Unidade", "Referência")] + linhas, (0.42, 0.16, 0.14, 0.28))
    secao(f, "Conclusão")
    paragrafo(f, "Função sistólica global do ventrículo esquerdo preservada. Ausência de disfunções "
                 "valvares significativas. Pericárdio sem alterações.")
    assinatura(f, cardiologista, "Cardiologia - Ecocardiografia", rng)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1))
    return DocGerado("ecocardiograma", tit, 5, inst.nome, dia, [nasc] if nasc else [], principal, nomes,
                     [f], False, valores=[{"rotulo": n, "valor": v} for n, v, _, _ in linhas],
                     referencias=[linha[3] for linha in linhas],
                     medicos=[cardiologista.nome, solicitante.nome])


@modelo(5)
def eletrocardiograma(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "cardiologia")
    cardiologista = ficticio.medico(rng, inst.uf)
    tit = "ELETROCARDIOGRAMA"
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares.append(("Data do exame", formato_data(rng, dia)))
    campos(f, pares)
    titulo(f, tit)
    medidas = [("Frequência cardíaca", str(rng.randrange(56, 98)), "bpm"),
               ("Intervalo PR", str(rng.randrange(130, 200)), "ms"),
               ("Duração do QRS", str(rng.randrange(80, 110)), "ms"),
               ("QTc", str(rng.randrange(380, 450)), "ms")]
    # traçado: onda simulada com traços, para a página ter tinta além do texto
    base = f.y - 30
    x = MARGEM
    while x < LARGURA - MARGEM - 20:
        f.linha(x, base, x + 18, base, 0.7)
        f.linha(x + 18, base, x + 21, base + 28, 0.7)
        f.linha(x + 21, base + 28, x + 24, base - 8, 0.7)
        f.linha(x + 24, base - 8, x + 27, base, 0.7)
        x += 40
    f.descer(60)
    paragrafo(f, "Ritmo: sinusal.")
    for rotulo, valor, unidade in medidas:
        paragrafo(f, f"{rotulo}: {valor} {unidade}")
    paragrafo(f, f"Eixo elétrico: +{rng.randrange(10, 80)}°")
    secao(f, "Conclusão")
    paragrafo(f, "Eletrocardiograma dentro dos limites da normalidade.")
    assinatura(f, cardiologista, "Cardiologia", rng)
    rodape(f, inst, None)
    return DocGerado("eletrocardiograma", tit, 5, inst.nome, dia, [nasc] if nasc else [], principal, nomes,
                     [f], False, valores=[{"rotulo": r, "valor": v} for r, v, _ in medidas],
                     medicos=[cardiologista.nome])


@modelo(5)
def receituario(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    medico = ficticio.medico(rng, inst.uf)
    tit = rng.choice(("RECEITUÁRIO", "RECEITA MÉDICA"))
    validade = ficticio.outra_data(rng, dia, 25, 35)
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    titulo(f, tit)
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome", "Sr(a)."),
                                                  chance_nascimento=0.2)
    campos(f, pares)
    secao(f, "Uso oral")
    itens = rng.sample((("Anastrozol 1 mg", "30 comprimidos", "Tomar 1 comprimido ao dia."),
                        ("Ondansetrona 8 mg", "10 comprimidos", "Tomar 1 comprimido de 8/8 horas se náusea."),
                        ("Dipirona 500 mg", "20 comprimidos", "Tomar 1 comprimido de 6/6 horas se dor."),
                        ("Omeprazol 20 mg", "28 cápsulas", "Tomar 1 cápsula em jejum.")), 2)
    for n, (remedio, quantidade, posologia) in enumerate(itens, start=1):
        paragrafo(f, f"{n}. {remedio} ........................ {quantidade}", recuo=6)
        paragrafo(f, posologia, recuo=22)
    f.descer(10)
    paragrafo(f, f"Válida até {dmy(validade)}.", tam=8.5)
    paragrafo(f, f"{inst.cidade}, {extenso(dia)}.", alinhamento="direita")
    assinatura(f, medico, "Oncologia Clínica", rng)
    rodape(f, inst, None)
    return DocGerado("receituario", tit, 5, inst.nome, dia, [validade] + ([nasc] if nasc else []),
                     principal, nomes, [f], False, medicos=[medico.nome])


@modelo(5, sem_nome=True, sorteavel=False)
def anotacao_manuscrita(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    """Anotação livre em papel pautado, sem nome, rótulo nem instituição.

    A data vem solta, sem rótulo: o gabarito não espera data dela.
    """
    pa, fc = f"{rng.randrange(11, 15)}0x{rng.randrange(7, 9)}0", str(rng.randrange(62, 96))
    peso = num(rng.randrange(480, 950) / 10)
    linhas = [f"{dia.day:02d}/{dia.month:02d}/{dia.year % 100:02d}",
              f"PA {pa}   FC {fc}",
              f"Peso {peso} kg",
              rng.choice(("Refere dor em mama E ha 2 sem.", "Refere cansaco aos esforcos.",
                          "Sem queixas. Boa aceitacao alimentar.")),
              rng.choice(("Trazer mamo e USG", "Trazer exames de sangue", "Pedir TC de torax")),
              "Retorno 30 dias"]
    f = Folha()
    pautas(f, ALTURA - 90, 90)
    y = ALTURA - 86
    for linha in linhas:
        manuscrito(f, MARGEM + rng.uniform(0, 14), y + 3, linha, rng)
        y -= 52
    return DocGerado("anotacao_manuscrita", "", 5, None, None, [], None, [], [f], False,
                     valores=[{"rotulo": "Peso", "valor": peso}, {"rotulo": "FC", "valor": fc}])
