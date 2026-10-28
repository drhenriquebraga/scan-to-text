"""Categoria 2: exames de imagem e de medicina nuclear."""
from __future__ import annotations

import random
from datetime import date

from corpus import ficticio
from corpus.folha import (Folha, assinatura, cabecalho, campos, paragrafo, rodape, secao, tabela,
                          titulo)
from corpus.modelos import (DocGerado, dmy, estilo_numeracao, formato_data, hora, identificacao,
                            idade, modelo, num, numeracao)


def _impresso(rng: random.Random, dia: date) -> tuple[str | None, list[date]]:
    if rng.random() < 0.5:
        return None, []
    quando = ficticio.outra_data(rng, dia, 1, 60)
    return f"Impresso em {dmy(quando)} às {hora(rng)}", [quando]


@modelo(2, sexos=("F",))
def mamografia(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "imagem")
    radiologista, solicitante = ficticio.medico(rng, inst.uf), ficticio.medico(rng, inst.uf)
    anos = idade(pessoa.nascimento, dia) if pessoa else rng.randrange(40, 75)
    tit = rng.choice(("MAMOGRAFIA DIGITAL BILATERAL", "MAMOGRAFIA BILATERAL"))
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome do paciente", "Pac."))
    pares += [("Idade", f"{anos} anos"), ("Data do exame", formato_data(rng, dia)),
              (rng.choice(("Médico solicitante", "Solicitante")), solicitante.tratado),
              ("Convênio", rng.choice(ficticio.CONVENIOS))]
    campos(f, pares, moldura=rng.random() < 0.5)
    titulo(f, tit)
    armadilhas = [nasc] if nasc else []
    secao(f, "Técnica")
    paragrafo(f, "Exame realizado em equipamento digital, com incidências craniocaudais e "
                 "médio-laterais oblíquas bilaterais.")
    if anos < 56:
        dum = ficticio.outra_data(rng, dia, -40, -5)
        paragrafo(f, f"DUM: {dmy(dum)}")
        armadilhas.append(dum)
    anterior = ficticio.outra_data(rng, dia, -500, -300)
    armadilhas.append(anterior)
    secao(f, "Comparação")
    paragrafo(f, f"Exame anterior de {dmy(anterior)}, realizado nesta instituição.")
    secao(f, "Relatório")
    suspeito = rng.random() < 0.6
    paragrafo(f, "Mamas com composição fibroglandular esparsa (padrão B do ACR).")
    if suspeito:
        paragrafo(f, "Nódulo irregular, de margens espiculadas, no quadrante superolateral da mama "
                     f"esquerda, medindo {num(rng.randrange(8, 30) / 10)} x "
                     f"{num(rng.randrange(6, 20) / 10)} cm, não caracterizado no exame anterior.")
    else:
        paragrafo(f, "Calcificações esparsas de aspecto benigno bilateralmente. Ausência de nódulos "
                     "ou distorções arquiteturais.")
    paragrafo(f, "Pele e complexos areolopapilares sem alterações. Linfonodos axilares de aspecto "
                 "habitual.")
    secao(f, "Impressão diagnóstica")
    paragrafo(f, "Achado suspeito na mama esquerda. BI-RADS 4C." if suspeito else
              "Achados benignos. BI-RADS 2.")
    liberado = ficticio.outra_data(rng, dia, 1, 5)
    assinatura(f, radiologista, "Médica radiologista" if radiologista.sexo == "F" else "Médico radiologista",
               rng, eletronica=True)
    paragrafo(f, f"Laudo liberado por {radiologista.tratado} em {dmy(liberado)} às {hora(rng)}.",
              tam=7.5)
    esquerda, impresso = _impresso(rng, dia)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1), esquerda)
    return DocGerado("mamografia", tit, 2, inst.nome, dia, armadilhas + impresso, principal, nomes, [f],
                     False, medicos=[radiologista.nome, solicitante.nome])


EXAMES_TC = (
    ("TOMOGRAFIA COMPUTADORIZADA DE TÓRAX",
     "Exame realizado em aparelho multidetectores, com cortes axiais de 1,0 mm, antes e após a "
     "injeção endovenosa de meio de contraste iodado não iônico.",
     ("Nódulo sólido no segmento apical do lobo superior direito, medindo {a} cm, de contornos "
      "lobulados.", "Ausência de derrame pleural. Traqueia e brônquios principais pérvios.",
      "Linfonodos mediastinais de dimensões normais. Coração de dimensões normais."),
     "Nódulo pulmonar no lobo superior direito, suspeito. Sugere-se correlação histológica."),
    ("TOMOGRAFIA COMPUTADORIZADA DO ABDOME TOTAL",
     "Exame realizado com cortes axiais de 2,5 mm, antes e após a administração de contraste "
     "endovenoso, nas fases arterial e portal.",
     ("Fígado de dimensões normais, com lesão hipodensa no segmento VII medindo {a} cm, "
      "sugestiva de cisto simples.", "Vesícula biliar normodistendida, sem cálculos.",
      "Rins tópicos, de dimensões normais, sem hidronefrose."),
     "Cisto hepático simples. Demais achados sem alterações significativas."),
    ("RESSONÂNCIA MAGNÉTICA DA PELVE",
     "Exame realizado em aparelho de 1,5 T, com sequências ponderadas em T1, T2 e difusão, antes "
     "e após a injeção de gadolínio.",
     ("Lesão expansiva medindo {a} cm, com restrição à difusão.",
      "Ausência de linfonodomegalias pélvicas. Bexiga com paredes regulares.",
      "Estruturas ósseas com sinal habitual."),
     "Lesão expansiva pélvica com características suspeitas. Correlacionar com estudo histológico."),
)


@modelo(2)
def tomografia(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "imagem")
    radiologista, solicitante = ficticio.medico(rng, inst.uf), ficticio.medico(rng, inst.uf)
    tit, tecnica, achados, conclusao = rng.choice(EXAMES_TC)
    tit = op.get("titulo") or tit
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Nome do paciente", "Paciente"))
    pares += [("Sexo", {"F": "Feminino", "M": "Masculino"}[pessoa.sexo if pessoa else "F"]),
              ("Solicitante", solicitante.tratado),
              ("Data de realização", formato_data(rng, dia)),
              ("Atendimento", str(rng.randrange(1000000, 9999999)))]
    campos(f, pares)
    titulo(f, tit)
    secao(f, "Técnica")
    paragrafo(f, tecnica)
    secao(f, "Análise")
    for achado in achados:
        paragrafo(f, achado.format(a=f"{num(rng.randrange(6, 35) / 10)} x {num(rng.randrange(5, 25) / 10)}"))
    secao(f, "Conclusão")
    paragrafo(f, conclusao)
    liberado = ficticio.outra_data(rng, dia, 1, 4)
    paragrafo(f, f"Liberado em: {dmy(liberado)}")
    assinatura(f, radiologista, "Radiologia e Diagnóstico por Imagem", rng, eletronica=True)
    esquerda = f"Assinado eletronicamente por {radiologista.tratado}"
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1), esquerda)
    return DocGerado("tomografia", tit, 2, inst.nome, dia, [nasc] if nasc else [], principal, nomes, [f],
                     False, medicos=[radiologista.nome, solicitante.nome])


@modelo(2, varias_paginas=True)
def pet_ct(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "imagem")
    nuclear, solicitante = ficticio.medico(rng, inst.uf), ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    psma = sexo == "M" and rng.random() < 0.5
    tit = "PET-CT COM PSMA-68Ga" if psma else "PET-CT COM 18F-FDG"
    estilo = rng.randrange(3)
    num_estilo = rng.choice(("pagina", "pag", "barra"))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    glicemia = str(rng.randrange(78, 118))
    atividade = num(rng.randrange(60, 110) / 10)
    pares += [("Data do exame", formato_data(rng, dia)), ("Solicitante", solicitante.tratado),
              ("Peso", f"{rng.randrange(52, 96)} kg"), ("Glicemia", f"{glicemia} mg/dL"),
              ("Atividade administrada", f"{atividade} mCi")]
    lesoes = (("Linfonodo axilar esquerdo", "Lesão pulmonar no lobo superior direito", "Linfonodo "
               "mediastinal subcarinal") if not psma else
              ("Próstata, lobo esquerdo", "Linfonodo ilíaco externo direito", "Arco costal posterior"))
    linhas = [(local, f"{num(rng.randrange(8, 40) / 10)} x {num(rng.randrange(6, 30) / 10)}",
               num(rng.randrange(25, 180) / 10)) for local in lesoes]
    folhas = []
    f = Folha()
    cabecalho(f, inst, estilo)
    campos(f, pares)
    titulo(f, tit)
    secao(f, "Técnica")
    paragrafo(f, "Imagens adquiridas do vértice do crânio até a raiz das coxas, cerca de 60 minutos "
                 "após a administração endovenosa do radiofármaco, com tomografia de baixa dose "
                 "para correção de atenuação e localização anatômica.")
    secao(f, "Achados")
    tabela(f, [("Localização", "Dimensões (cm)", "SUVmax")] + linhas, (0.5, 0.28, 0.22))
    paragrafo(f, "Demais estruturas com distribuição fisiológica do radiofármaco.")
    rodape(f, inst, numeracao(num_estilo, 1, 2))
    folhas.append(f)
    f = Folha()
    cabecalho(f, inst, estilo)
    if principal and rng.random() < 0.6:
        campos(f, [pares[0]], colunas=1)
    secao(f, "Impressão")
    paragrafo(f, "Lesões hipermetabólicas compatíveis com doença em atividade, conforme descrito. "
                 "Ausência de outras lesões com captação anômala do radiofármaco.")
    assinatura(f, nuclear, "Medicina Nuclear", rng)
    esquerda, impresso = _impresso(rng, dia)
    rodape(f, inst, numeracao(num_estilo, 2, 2), esquerda)
    folhas.append(f)
    valores = [{"rotulo": "Glicemia", "valor": glicemia}] + \
              [{"rotulo": local, "valor": suv} for local, _, suv in linhas]
    return DocGerado("pet_ct", tit, 2, inst.nome, dia, ([nasc] if nasc else []) + impresso, principal,
                     nomes, folhas, True, valores=valores, medicos=[nuclear.nome, solicitante.nome])


@modelo(2)
def densitometria(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "imagem")
    radiologista, solicitante = ficticio.medico(rng, inst.uf), ficticio.medico(rng, inst.uf)
    tit = "DENSITOMETRIA ÓSSEA"
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares += [("Data do exame", formato_data(rng, dia)), ("Solicitante", solicitante.tratado)]
    campos(f, pares)
    titulo(f, tit)
    paragrafo(f, "Exame realizado por absorciometria de raios X de dupla energia (DXA), com análise "
                 "da coluna lombar e do fêmur proximal esquerdo.")
    regioes = ("Coluna lombar (L1-L4)", "Colo do fêmur", "Fêmur total")
    linhas = [(r, num(rng.randrange(700, 1100) / 1000, 3), num(-rng.randrange(0, 30) / 10),
               num(-rng.randrange(0, 15) / 10)) for r in regioes]
    tabela(f, [("Região", "DMO (g/cm²)", "T-score", "Z-score")] + linhas, (0.4, 0.2, 0.2, 0.2))
    referencias = ["T-score até -1,0", "entre -1,0 e -2,5", "igual ou inferior a -2,5"]
    secao(f, "Valores de referência (OMS)")
    paragrafo(f, f"Normal: {referencias[0]}.", recuo=12)
    paragrafo(f, f"Osteopenia: T-score {referencias[1]}.", recuo=12)
    paragrafo(f, f"Osteoporose: T-score {referencias[2]}.", recuo=12)
    secao(f, "Conclusão")
    paragrafo(f, "Densidade mineral óssea reduzida para a faixa etária em coluna lombar.")
    assinatura(f, radiologista, "Radiologia", rng)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1))
    valores = [{"rotulo": r, "valor": dmo} for r, dmo, _, _ in linhas]
    return DocGerado("densitometria", tit, 2, inst.nome, dia, [nasc] if nasc else [], principal, nomes,
                     [f], False, valores=valores, referencias=referencias,
                     medicos=[radiologista.nome, solicitante.nome])
