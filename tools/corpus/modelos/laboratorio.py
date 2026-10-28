"""Categoria 3: laudos de laboratório, com valores de referência em coluna e em bloco.

Os valores de referência vão para `DocGerado.referencias` exatamente como
impressos (sem a unidade), para o avaliador procurá-los no MD: num documento
de 3 LABORATORIO eles têm de sumir; fora dele, de ficar.
"""
from __future__ import annotations

import random
from datetime import date

from corpus import ficticio
from corpus.folha import (Folha, assinatura, cabecalho, campos, paragrafo, rodape, secao, tabela,
                          titulo)
from corpus.modelos import (DocGerado, dmy, estilo_numeracao, formato_data, hora, identificacao,
                            modelo, num, numeracao)

ROTULOS_VR_COLUNA = ("Valores de referência", "Valor de referência", "VR", "Referência")
ROTULOS_VR = ("Valores de referência", "Valor de referência", "VR", "Referência",
              "Intervalo de referência", "Faixa de referência")

# (rótulo, unidade, referência F, referência M, (mínimo, máximo, casas))
ERITROGRAMA = (
    ("Hemácias", "milhões/mm³", "3,90 a 5,03", "4,32 a 5,72", (3.6, 5.6, 2)),
    ("Hemoglobina", "g/dL", "12,0 a 15,5", "13,5 a 17,5", (10.5, 16.5, 1)),
    ("Hematócrito", "%", "35,0 a 45,0", "39,0 a 50,0", (32.0, 49.0, 1)),
    ("VCM", "fL", "80,0 a 100,0", "80,0 a 100,0", (78.0, 99.0, 1)),
    ("HCM", "pg", "27,0 a 33,0", "27,0 a 33,0", (26.0, 34.0, 1)),
    ("CHCM", "g/dL", "32,0 a 36,0", "32,0 a 36,0", (31.0, 36.0, 1)),
    ("RDW", "%", "11,5 a 14,5", "11,5 a 14,5", (11.8, 15.2, 1)),
)
LEUCOGRAMA = (
    ("Leucócitos", "/mm³", "4.000 a 11.000", (3500, 11500)),
    ("Neutrófilos", "/mm³", "1.800 a 7.700", (1500, 7900)),
    ("Linfócitos", "/mm³", "1.000 a 4.800", (900, 4200)),
    ("Monócitos", "/mm³", "100 a 1.000", (150, 950)),
    ("Eosinófilos", "/mm³", "0 a 500", (20, 480)),
    ("Basófilos", "/mm³", "0 a 200", (0, 150)),
)
# nome da seção, material, método, rótulo do resultado, unidade, sorteio, faixas de referência
BLOCOS = {
    "glicose": ("GLICOSE", "Soro (jejum de 8 horas)", "Enzimático (hexoquinase)", "Glicose", "mg/dL",
                (78, 132, 0), (("Normal", "70 a 99"), ("Glicemia de jejum alterada", "100 a 125"),
                               ("Diabetes", "igual ou superior a 126"))),
    "creatinina": ("CREATININA", "Soro", "Cinético (Jaffé)", "Creatinina", "mg/dL", (0.55, 1.3, 2),
                   (("Mulheres", "0,51 a 0,95"), ("Homens", "0,67 a 1,17"))),
    "ureia": ("UREIA", "Soro", "Enzimático (urease)", "Ureia", "mg/dL", (15, 52, 0),
              ((None, "15 a 45"),)),
    "tsh": ("TSH - HORMÔNIO TIREOESTIMULANTE", "Soro", "Quimioluminescência", "TSH", "µUI/mL",
            (0.6, 5.2, 2), ((None, "0,45 a 4,50"),)),
    "potassio": ("POTÁSSIO", "Soro", "Eletrodo íon-seletivo", "Potássio", "mEq/L", (3.4, 5.3, 1),
                 ((None, "3,5 a 5,1"),)),
    "calcio": ("CÁLCIO TOTAL", "Soro", "Colorimétrico (arsenazo III)", "Cálcio total", "mg/dL",
               (8.2, 10.6, 1), (("Adultos", "8,6 a 10,3"), ("Acima de 60 anos", "8,8 a 10,0"))),
}
MARCADORES = {
    "F": (("CEA", "ng/mL", "até 5,0", (0.5, 6.0, 1)), ("CA 15-3", "U/mL", "até 31,3", (5.0, 40.0, 1)),
          ("CA 125", "U/mL", "até 35,0", (5.0, 45.0, 1)), ("CA 19-9", "U/mL", "até 37,0", (2.0, 45.0, 1))),
    "M": (("PSA total", "ng/mL", "até 4,0", (0.4, 9.0, 2)), ("CEA", "ng/mL", "até 5,0", (0.5, 6.0, 1)),
          ("CA 19-9", "U/mL", "até 37,0", (2.0, 45.0, 1)), ("AFP", "ng/mL", "até 8,0", (1.0, 9.0, 1))),
}
PSA_POR_IDADE = (("40 a 49 anos", "até 2,5"), ("50 a 59 anos", "até 3,5"),
                 ("60 a 69 anos", "até 4,5"), ("70 a 79 anos", "até 6,5"))


def _sorteio(rng: random.Random, minimo: float, maximo: float, casas: int) -> str:
    return num(rng.uniform(minimo, maximo), casas)


def _responsavel(rng: random.Random, uf: str) -> ficticio.Medico:
    base = ficticio.medico(rng, uf)
    conselho = rng.choice(("CRBM", "CRF"))
    return ficticio.Medico(base.nome, base.sexo, f"{conselho}-{uf} {rng.randrange(1000, 29999)}")


class _Cabecalho:
    """Identificação que todo laudo de laboratório repete: paciente, pedido, coleta."""

    def __init__(self, rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, op: dict,
                 inst: ficticio.Instituicao):
        self.solicitante = ficticio.medico(rng, inst.uf)
        self.pares, self.principal, self.nomes, nasc = identificacao(
            rng, pessoa, op, ("Paciente", "Cliente", "Nome", "Pcte"))
        self.armadilhas = [nasc] if nasc else []
        self.liberacao = ficticio.outra_data(rng, dia, 1, 5)
        self.pedido = (rng.choice(("Pedido", "Protocolo")), str(rng.randrange(1000000, 9999999)))
        self.pares += [
            (rng.choice(("Médico solicitante", "Solicitante", "Requisitante")), self.solicitante.tratado),
            ("Convênio", rng.choice(ficticio.CONVENIOS)), self.pedido,
            (rng.choice(("Data da coleta", "Coleta")), f"{formato_data(rng, dia)} {hora(rng)}"),
            (rng.choice(("Data de liberação", "Liberado em")), dmy(self.liberacao)),
        ]
        self.estilo = rng.randrange(3)
        self.moldura = rng.random() < 0.5

    def primeira(self, f: Folha) -> None:
        campos(f, self.pares, moldura=self.moldura)

    def continuacao(self, f: Folha, rng: random.Random) -> None:
        curtos = ([self.pares[0]] if self.principal and rng.random() < 0.6 else []) + [self.pedido]
        campos(f, curtos)


def _fecho(f: Folha, rng: random.Random, inst: ficticio.Instituicao, dia: date, n: int, total: int,
           estilo_num: str | None) -> tuple[ficticio.Medico, list[date]]:
    responsavel = _responsavel(rng, inst.uf)
    paragrafo(f, f"Responsável técnico: {responsavel.tratado} - {responsavel.conselho}", tam=8)
    armadilhas = []
    if rng.random() < 0.5:
        esquerda = f"Assinado eletronicamente por {responsavel.tratado}"
    else:
        impresso = ficticio.outra_data(rng, dia, 1, 60)
        esquerda = f"Impresso em {dmy(impresso)} {hora(rng)}"
        armadilhas.append(impresso)
    rodape(f, inst, numeracao(estilo_num, n, total), esquerda)
    return responsavel, armadilhas


def _valor(op: dict, rotulo: str, sorteado: str) -> str:
    """Laudo retificado: `op["ajuste"] = (rótulo, valor)` troca um único resultado."""
    if "ajuste" in op and op["ajuste"][0] == rotulo:
        return op["ajuste"][1]
    return sorteado


@modelo(3, sem_nome=True, varias_paginas=True)
def hemograma(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "laboratorio")
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    cab = _Cabecalho(rng, pessoa, dia, op, inst)
    rotulo_vr = op.get("rotulo_vr") or rng.choice(ROTULOS_VR_COLUNA)
    estilo_num = rng.choice(("pagina", "pag", "barra"))
    tit = op.get("titulo") or rng.choice(("HEMOGRAMA COMPLETO", "HEMOGRAMA"))
    eritro = [{"rotulo": r, "valor": _valor(op, r, _sorteio(rng, *faixa)), "unidade": u,
               "vr": vr_f if sexo == "F" else vr_m} for r, u, vr_f, vr_m, faixa in ERITROGRAMA]
    leuco = [{"rotulo": r, "valor": _valor(op, r, num(rng.randrange(a, b, 10), 0)), "unidade": u,
              "vr": vr}
             for r, u, vr, (a, b) in LEUCOGRAMA]
    plaq = [{"rotulo": "Plaquetas", "valor": num(rng.randrange(140, 420) * 1000, 0), "unidade": "/mm³",
             "vr": "150.000 a 450.000"}]
    valores = eritro + leuco + plaq

    def linhas(itens):
        return [("Exame", "Resultado", "Unidade", rotulo_vr)] + \
               [(v["rotulo"], v["valor"], v["unidade"], v["vr"]) for v in itens]

    larguras = (0.3, 0.2, 0.18, 0.32)
    f1 = Folha()
    cabecalho(f1, inst, cab.estilo)
    cab.primeira(f1)
    titulo(f1, tit)
    paragrafo(f1, "Material: Sangue total (EDTA)     Método: Automatizado (citometria de fluxo e "
                  "impedância)", tam=8)
    secao(f1, "ERITROGRAMA")
    tabela(f1, linhas(eritro), larguras)
    rodape(f1, inst, numeracao(estilo_num, 1, 2))
    f2 = Folha()
    cabecalho(f2, inst, cab.estilo)
    cab.continuacao(f2, rng)
    secao(f2, "LEUCOGRAMA")
    tabela(f2, linhas(leuco), larguras)
    secao(f2, "PLAQUETAS")
    tabela(f2, linhas(plaq), larguras)
    paragrafo(f2, "Observação: morfologia eritrocitária e leucocitária sem alterações dignas de nota.",
              tam=8.5)
    responsavel, impresso = _fecho(f2, rng, inst, dia, 2, 2, estilo_num)
    return DocGerado("hemograma", tit, 3, inst.nome, dia, cab.armadilhas + impresso, cab.principal,
                     cab.nomes, [f1, f2], True,
                     valores=[{"rotulo": v["rotulo"], "valor": v["valor"]} for v in valores],
                     referencias=sorted({v["vr"] for v in valores}),
                     medicos=[cab.solicitante.nome, responsavel.nome])


@modelo(3, sem_nome=True)
def bioquimica(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "laboratorio")
    cab = _Cabecalho(rng, pessoa, dia, op, inst)
    tit = op.get("titulo") or rng.choice(("BIOQUÍMICA", "EXAMES BIOQUÍMICOS", "BIOQUÍMICA SÉRICA"))
    nomes_blocos = op.get("blocos") or rng.sample(sorted(BLOCOS), rng.randrange(3, 5))
    rotulos = op.get("rotulos_vr") or [rng.choice(ROTULOS_VR) for _ in nomes_blocos]
    f = Folha()
    cabecalho(f, inst, cab.estilo)
    cab.primeira(f)
    titulo(f, tit)
    valores, referencias, armadilhas = [], [], list(cab.armadilhas)
    for nome_bloco, rotulo_vr in zip(nomes_blocos, rotulos):
        secao_nome, material, metodo, rotulo, unidade, faixa, faixas_vr = BLOCOS[nome_bloco]
        valor = _valor(op, rotulo, _sorteio(rng, *faixa))
        valores.append({"rotulo": rotulo, "valor": valor})
        secao(f, secao_nome)
        paragrafo(f, f"Material: {material}     Método: {metodo}", tam=8)
        paragrafo(f, f"{rotulo}: {valor} {unidade}", fonte="Helvetica-Bold")
        if len(faixas_vr) == 1:
            paragrafo(f, f"{rotulo_vr}: {faixas_vr[0][1]} {unidade}", tam=8.5)
        else:
            paragrafo(f, f"{rotulo_vr}:", tam=8.5)
            for faixa_nome, texto in faixas_vr:
                paragrafo(f, f"{faixa_nome}: {texto} {unidade}", tam=8.5, recuo=14)
        referencias += [texto for _, texto in faixas_vr]
        if rng.random() < 0.25:
            anterior = ficticio.outra_data(rng, dia, -400, -60)
            armadilhas.append(anterior)
            paragrafo(f, f"Exame anterior: {_sorteio(rng, *faixa)} {unidade} em {dmy(anterior)}", tam=8)
    responsavel, impresso = _fecho(f, rng, inst, dia, 1, 1, estilo_numeracao(rng, 1))
    return DocGerado("bioquimica", tit, 3, inst.nome, dia, armadilhas + impresso, cab.principal,
                     cab.nomes, [f], False, valores=valores, referencias=referencias,
                     medicos=[cab.solicitante.nome, responsavel.nome])


@modelo(3)
def marcadores(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "laboratorio")
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    cab = _Cabecalho(rng, pessoa, dia, op, inst)
    rotulo_vr = op.get("rotulo_vr") or rng.choice(("Intervalo de referência", "Faixa de referência"))
    anterior = ficticio.outra_data(rng, dia, -300, -90)
    tit = "MARCADORES TUMORAIS"
    linhas, valores = [], []
    for rotulo, unidade, vr, faixa in MARCADORES[sexo]:
        valor = _valor(op, rotulo, _sorteio(rng, *faixa))
        valores.append({"rotulo": rotulo, "valor": valor})
        linhas.append((rotulo, valor, unidade, _sorteio(rng, *faixa), vr))
    f = Folha()
    cabecalho(f, inst, cab.estilo)
    cab.primeira(f)
    titulo(f, tit)
    paragrafo(f, "Material: Soro     Método: Eletroquimioluminescência", tam=8)
    tabela(f, [("Exame", "Resultado", "Unidade", f"Anterior ({dmy(anterior)})", rotulo_vr)] + linhas,
           (0.18, 0.15, 0.13, 0.25, 0.29))
    paragrafo(f, "Nota: resultados de marcadores tumorais devem ser interpretados em conjunto com "
                 "os dados clínicos. Mudança de método pode alterar os valores.", tam=8)
    responsavel, impresso = _fecho(f, rng, inst, dia, 1, 1, estilo_numeracao(rng, 1))
    return DocGerado("marcadores", tit, 3, inst.nome, dia, cab.armadilhas + [anterior] + impresso,
                     cab.principal, cab.nomes, [f], False, valores=valores,
                     referencias=[linha[4] for linha in linhas],
                     medicos=[cab.solicitante.nome, responsavel.nome])


@modelo(3)
def marcador_unico(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    """Um marcador só (PSA ou CA 15-3): o laudo típico de exame seriado."""
    inst = op.get("inst") or ficticio.instituicao(rng, "laboratorio")
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    cab = _Cabecalho(rng, pessoa, dia, op, inst)
    rotulo_vr = op.get("rotulo_vr") or rng.choice(ROTULOS_VR)
    if sexo == "M":
        tit, rotulo, unidade = "PSA TOTAL", "PSA total", "ng/mL"
        valor = op.get("valor") or _sorteio(rng, 0.5, 9.0, 2)
        faixas = PSA_POR_IDADE
    else:
        tit, rotulo, unidade = "CA 15-3", "CA 15-3", "U/mL"
        valor = op.get("valor") or _sorteio(rng, 6.0, 48.0, 1)
        faixas = ((None, "até 31,3"),)
    f = Folha()
    cabecalho(f, inst, cab.estilo)
    cab.primeira(f)
    titulo(f, tit)
    paragrafo(f, "Material: Soro     Método: Eletroquimioluminescência", tam=8)
    paragrafo(f, f"{rotulo}: {valor} {unidade}", fonte="Helvetica-Bold")
    if len(faixas) == 1:
        paragrafo(f, f"{rotulo_vr}: {faixas[0][1]} {unidade}", tam=8.5)
    else:
        paragrafo(f, f"{rotulo_vr}:", tam=8.5)
        for faixa_nome, texto in faixas:
            paragrafo(f, f"{faixa_nome}: {texto} {unidade}", tam=8.5, recuo=14)
    paragrafo(f, "Nota: valores obtidos por métodos diferentes não são intercambiáveis.", tam=8)
    responsavel, impresso = _fecho(f, rng, inst, dia, 1, 1, estilo_numeracao(rng, 1))
    return DocGerado("marcador_unico", tit, 3, inst.nome, dia, cab.armadilhas + impresso, cab.principal,
                     cab.nomes, [f], False, valores=[{"rotulo": rotulo, "valor": valor}],
                     referencias=[texto for _, texto in faixas],
                     medicos=[cab.solicitante.nome, responsavel.nome])
