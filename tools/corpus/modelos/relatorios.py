"""Categoria 1: relatórios médicos, encaminhamentos e resumos de tratamento."""
from __future__ import annotations

import random
from datetime import date

from corpus import ficticio
from corpus.folha import (Folha, assinatura, cabecalho, campos, paragrafo, rodape, secao, tabela,
                          titulo)
from corpus.modelos import (DocGerado, dmy, estilo_numeracao, extenso, formato_data, hora,
                            identificacao, idade, modelo, num, numeracao)

DIAGNOSTICOS = {
    "F": (("carcinoma ductal invasivo da mama esquerda, estádio clínico IIA (cT2 cN0 M0)",
           "quadrantectomia com biópsia de linfonodo sentinela"),
          ("carcinoma ductal invasivo da mama direita, estádio clínico IIB (cT2 cN1 M0)",
           "mastectomia radical modificada"),
          ("adenocarcinoma endometrioide de endométrio, estádio IB",
           "histerectomia total com salpingo-ooforectomia bilateral"),
          ("carcinoma epidermoide do colo uterino, estádio IIB", None)),
    "M": (("adenocarcinoma de próstata, escore de Gleason 7 (3 + 4), grupo prognóstico 2",
           "prostatectomia radical"),
          ("adenocarcinoma de reto médio, estádio clínico cT3 cN1 M0", None),
          ("carcinoma epidermoide de laringe, estádio III", "laringectomia parcial"),
          ("adenocarcinoma de pulmão, estádio IIIA", "lobectomia superior direita")),
}
ESPECIALIDADES = ("Oncologia Clínica", "Cirurgia Oncológica", "Mastologia", "Urologia",
                  "Ginecologia Oncológica")
HISTORIAS = (
    "Em bom estado geral, ECOG 1, sem comorbidades descompensadas. Hipertensão arterial em uso "
    "de losartana 50 mg ao dia.",
    "Sem queixas no momento. Nega tabagismo e etilismo. Sem alergias medicamentosas conhecidas.",
    "Refere dor leve em região operada, com melhora progressiva. Deambula sem auxílio.",
    "Diabetes mellitus tipo 2 controlado com metformina 850 mg duas vezes ao dia. ECOG 0.",
)


def _genero(sexo: str, feminino: str, masculino: str) -> str:
    return feminino if sexo == "F" else masculino


@modelo(1, sem_nome=True)
def encaminhamento(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    medico = ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    tit = op.get("titulo") or rng.choice(("ENCAMINHAMENTO", "ENCAMINHAMENTO MÉDICO",
                                          "CARTA DE ENCAMINHAMENTO"))
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    titulo(f, tit)
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome", "Sr(a)."))
    pares.append(("Convênio", rng.choice(ficticio.CONVENIOS)))
    campos(f, pares, moldura=True)
    diagnostico, cirurgia = rng.choice(DIAGNOSTICOS[sexo])
    anos = idade(pessoa.nascimento, dia) if pessoa else rng.randrange(40, 80)
    paragrafo(f, "Ao Serviço de Radioterapia,")
    paragrafo(f, f"Encaminho {_genero(sexo, 'a paciente acima identificada', 'o paciente acima identificado')}, "
                 f"{anos} anos, com diagnóstico de {diagnostico}, para avaliação quanto à "
                 f"indicação de radioterapia.")
    if cirurgia and rng.random() < 0.5:
        quando = ficticio.outra_data(rng, dia, -120, -20)
        paragrafo(f, f"{_genero(sexo, 'Submetida', 'Submetido')} a {cirurgia} em {dmy(quando)}, "
                     "com boa evolução pós-operatória e cicatrização adequada.")
    paragrafo(f, rng.choice(HISTORIAS))
    paragrafo(f, "Seguem exames em anexo. Agradeço a atenção e coloco-me à disposição para "
                 "esclarecimentos.")
    f.descer(8)
    paragrafo(f, f"{inst.cidade}, {extenso(dia)}.", alinhamento="direita")
    assinatura(f, medico, rng.choice(ESPECIALIDADES), rng)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1))
    return DocGerado("encaminhamento", tit, 1, inst.nome, dia, [nasc] if nasc else [], principal,
                     nomes, [f], False, medicos=[medico.nome])


@modelo(1, varias_paginas=True)
def relatorio_medico(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    medico = ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    total = op.get("paginas") or rng.choice((1, 2, 2, 3))
    estilo = rng.randrange(3)
    num_estilo = rng.choice(("pagina", "pag", "barra", "folha")) if total > 1 else None
    tit = rng.choice(("RELATÓRIO MÉDICO", "RELATÓRIO CLÍNICO"))
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Nome", "Paciente", "Nome completo"))
    pares.append(("Prontuário", str(rng.randrange(100000, 999999))))
    diagnostico, cirurgia = rng.choice(DIAGNOSTICOS[sexo])
    retorno = ficticio.outra_data(rng, dia, 20, 60)
    blocos = [
        ("História clínica", f"Paciente com diagnóstico de {diagnostico}. " + rng.choice(HISTORIAS)
                             + " Relata perda ponderal de 3 kg nos últimos dois meses, sem outros "
                               "sintomas constitucionais."),
        ("Exame físico", "Bom estado geral, corado, hidratado, eupneico. Pressão arterial de "
                         f"{rng.randrange(110, 145)} x {rng.randrange(70, 95)} mmHg, frequência "
                         f"cardíaca de {rng.randrange(62, 96)} bpm. Sem linfonodomegalias palpáveis "
                         "em cadeias cervicais, supraclaviculares e axilares."),
        ("Exames complementares", "Exames de estadiamento sem evidência de doença a distância. "
                                  "Função renal e hepática preservadas nos exames laboratoriais "
                                  "mais recentes."),
        ("Tratamentos realizados", (f"{_genero(sexo, 'Submetida', 'Submetido')} a {cirurgia}, "
                                    "sem intercorrências.") if cirurgia else
         "Ainda sem tratamento oncológico específico até a presente data."),
        ("Conduta", "Indicada avaliação pela radioterapia. Solicitados exames de controle. "
                    f"Retorno agendado para {dmy(retorno)}."),
    ]
    folhas = []
    por_pagina = -(-len(blocos) // total)
    for n in range(total):
        f = Folha()
        cabecalho(f, inst, estilo)
        if n == 0:
            titulo(f, tit)
            campos(f, pares)
        elif rng.random() < 0.5 and principal:
            campos(f, [pares[0]], colunas=1)
        for nome_secao, texto in blocos[n * por_pagina:(n + 1) * por_pagina]:
            secao(f, nome_secao)
            paragrafo(f, texto)
        if n == total - 1:
            f.descer(8)
            paragrafo(f, f"{inst.cidade}, {extenso(dia)}.", alinhamento="direita")
            assinatura(f, medico, rng.choice(ESPECIALIDADES), rng)
        rodape(f, inst, numeracao(num_estilo, n + 1, total))
        folhas.append(f)
    armadilhas = [retorno] + ([nasc] if nasc else [])
    return DocGerado("relatorio_medico", tit, 1, inst.nome, dia, armadilhas, principal, nomes, folhas,
                     total > 1, medicos=[medico.nome])


@modelo(1)
def resumo_tratamento(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    medico = ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    radio = rng.random() < 0.5
    tit = "RESUMO DE TRATAMENTO RADIOTERÁPICO" if radio else "RESUMO DE TRATAMENTO QUIMIOTERÁPICO"
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    titulo(f, tit)
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares.append(("Registro", str(rng.randrange(10000, 99999))))
    campos(f, pares, moldura=True)
    secao(f, "Diagnóstico")
    paragrafo(f, rng.choice(DIAGNOSTICOS[sexo])[0].capitalize() + ".")
    valores = []
    if radio:
        volumes = (("Mama esquerda", "40,05", "15", "2,67", "3D conformacional"),
                   ("Leito tumoral (reforço)", "10,00", "5", "2,00", "Elétrons")) if sexo == "F" else \
                  (("Próstata e vesículas seminais", "70,00", "28", "2,50", "IMRT"),
                   ("Linfonodos pélvicos", "50,40", "28", "1,80", "IMRT"))
        secao(f, "Tratamento realizado")
        tabela(f, [("Volume", "Dose total (Gy)", "Frações", "Dose/fração (Gy)", "Técnica")] +
               list(volumes), (0.34, 0.16, 0.12, 0.18, 0.20))
        valores = [{"rotulo": v[0], "valor": v[1]} for v in volumes]
        paragrafo(f, "Tratamento concluído sem interrupções. Toxicidade aguda: radiodermite grau 1, "
                     "manejada com cuidados locais.")
    else:
        ciclos = rng.randrange(4, 7)
        secao(f, "Tratamento realizado")
        tabela(f, [("Protocolo", "Ciclos", "Intervalo", "Resposta")] +
               [("AC (doxorrubicina + ciclofosfamida)", "4", "21 dias", "Parcial"),
                ("Paclitaxel semanal", str(ciclos * 2), "7 dias", "Completa clínica")],
               (0.42, 0.14, 0.18, 0.26))
        paragrafo(f, "Neutropenia grau 2 no terceiro ciclo, sem necessidade de adiamento. Neuropatia "
                     "periférica grau 1 ao final do tratamento.")
    secao(f, "Situação atual")
    paragrafo(f, "Em acompanhamento ambulatorial, sem evidência de doença em atividade.")
    f.descer(8)
    paragrafo(f, f"{inst.cidade}, {extenso(dia)}.", alinhamento="direita")
    assinatura(f, medico, "Radioterapia" if radio else "Oncologia Clínica", rng)
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1))
    return DocGerado("resumo_tratamento", tit, 1, inst.nome, dia, [nasc] if nasc else [], principal,
                     nomes, [f], False, valores=valores, medicos=[medico.nome])


@modelo(1)
def descricao_cirurgica(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "hospital")
    cirurgiao, auxiliar, anestesista = (ficticio.medico(rng, inst.uf) for _ in range(3))
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    tit = rng.choice(("DESCRIÇÃO CIRÚRGICA", "RELATÓRIO DE DESCRIÇÃO CIRÚRGICA"))
    f = Folha()
    cabecalho(f, inst, rng.randrange(3))
    titulo(f, tit)
    pares, principal, nomes, nasc = identificacao(rng, pessoa, op, ("Paciente", "Nome"))
    pares += [("Registro", str(rng.randrange(100000, 999999))),
              ("Leito", f"{rng.randrange(100, 520)}-{rng.choice('AB')}"),
              ("Data do procedimento", formato_data(rng, dia)),
              ("Cirurgião", cirurgiao.tratado), ("Auxiliar", auxiliar.tratado),
              ("Anestesista", anestesista.tratado), ("Anestesia", rng.choice(("Geral", "Raquidiana")))]
    campos(f, pares)
    diagnostico, cirurgia = rng.choice([d for d in DIAGNOSTICOS[sexo] if d[1]])
    secao(f, "Diagnóstico pré-operatório")
    paragrafo(f, diagnostico.capitalize() + ".")
    secao(f, "Procedimento realizado")
    paragrafo(f, cirurgia.capitalize() + ".")
    secao(f, "Descrição")
    paragrafo(f, "Paciente em decúbito dorsal, sob anestesia, com antissepsia e colocação de campos "
                 "estéreis. Incisão conforme planejamento, dissecção por planos com hemostasia "
                 "rigorosa. Identificação e preservação das estruturas nobres. Retirada da peça "
                 "cirúrgica em monobloco, orientada com fios de reparo. Revisão da hemostasia, "
                 "fechamento por planos e curativo oclusivo.")
    secao(f, "Achados")
    paragrafo(f, f"Lesão endurecida medindo cerca de {num(rng.randrange(15, 45) / 10)} cm, sem "
                 "invasão macroscópica de estruturas adjacentes.")
    secao(f, "Material enviado")
    paragrafo(f, "Peça cirúrgica enviada para exame anatomopatológico.")
    f.linha(50, f.y + 4, 545, f.y + 4, 0.3)
    f.descer(10)
    campos(f, [("Início", hora(rng)), ("Término", hora(rng)), ("Sangramento", "mínimo")], colunas=3)
    assinatura(f, cirurgiao, "Cirurgião responsável", rng)
    impresso = ficticio.outra_data(rng, dia, 1, 40)
    esquerda = f"Impresso em {dmy(impresso)} {hora(rng)}" if rng.random() < 0.5 else None
    rodape(f, inst, numeracao(estilo_numeracao(rng, 1), 1, 1), esquerda)
    armadilhas = ([impresso] if esquerda else []) + ([nasc] if nasc else [])
    return DocGerado("descricao_cirurgica", tit, 1, inst.nome, dia, armadilhas, principal, nomes, [f],
                     False, medicos=[cirurgiao.nome, auxiliar.nome, anestesista.nome])
