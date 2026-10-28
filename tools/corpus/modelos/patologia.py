"""Categoria 4: anatomopatológico, imuno-histoquímica e genética, com bibliografia."""
from __future__ import annotations

import random
from datetime import date

from corpus import ficticio
from corpus.folha import (Folha, assinatura, cabecalho, campos, paragrafo, rodape, secao, tabela,
                          titulo)
from corpus.modelos import (DocGerado, citacao, dmy, formato_data, hora, identificacao, modelo, num,
                            numeracao)


class _Cabecalho:
    """Identificação do laudo de patologia: número do exame, recebimento e liberação."""

    def __init__(self, rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, op: dict,
                 inst: ficticio.Instituicao, prefixo: str, papel: str):
        self.requisitante = ficticio.medico(rng, inst.uf)
        self.pares, self.principal, self.nomes, nasc = identificacao(
            rng, pessoa, op, ("Paciente", "Nome", "Nome do paciente"))
        self.armadilhas = [nasc] if nasc else []
        self.numero = ("Nº do exame", f"{prefixo} {dia.year % 100:02d}-{rng.randrange(1000, 99999):05d}")
        self.liberacao = liberacao = ficticio.outra_data(rng, dia, 4, 12)
        self.pares += [self.numero,
                       (rng.choice(("Médico requisitante", "Médico solicitante")),
                        self.requisitante.tratado),
                       (papel, formato_data(rng, dia)), ("Data de liberação", dmy(liberacao))]
        self.estilo = rng.randrange(3)

    def continuacao(self, f: Folha, rng: random.Random) -> None:
        campos(f, ([self.pares[0]] if self.principal and rng.random() < 0.6 else []) + [self.numero])


def _bibliografia(f: Folha, rng: random.Random) -> None:
    secao(f, rng.choice(("Referências bibliográficas", "Bibliografia")))
    for n in range(1, 4):
        paragrafo(f, f"{n}. {citacao(rng, ficticio.SOBRENOMES_MEDICOS)}", tam=7.5, recuo=6)


def _assinatura_eletronica(cab: _Cabecalho, medico: ficticio.Medico) -> str:
    return f"Assinado digitalmente por {medico.tratado} em {dmy(cab.liberacao)}"


@modelo(4, varias_paginas=True)
def anatomopatologico(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "patologia")
    patologista = ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    papel = rng.choice(("Data de recebimento", "Data da coleta"))
    cab = _Cabecalho(rng, pessoa, dia, op, inst, "AP", papel)
    tit = rng.choice(("LAUDO ANATOMOPATOLÓGICO", "EXAME ANATOMOPATOLÓGICO"))
    estilo_num = rng.choice(("pagina", "pag", "barra"))
    if sexo == "F":
        material = "Produto de quadrantectomia da mama esquerda, com fio de reparo orientando a margem superior."
        macro = (f"Segmento de mama medindo {num(rng.randrange(50, 90) / 10)} x "
                 f"{num(rng.randrange(35, 60) / 10)} x {num(rng.randrange(20, 40) / 10)} cm e pesando "
                 f"{rng.randrange(30, 90)} g. Aos cortes, lesão nodular firme, esbranquiçada, de limites "
                 "imprecisos. Representado em 12 blocos.")
        tamanho = num(rng.randrange(8, 32) / 10)
        diagnostico = [("Tipo histológico", "carcinoma invasivo de tipo não especial"),
                       ("Tamanho do tumor", f"{tamanho} cm"),
                       ("Grau histológico (Nottingham)", str(rng.randrange(1, 4))),
                       ("Margens cirúrgicas", f"livres; a mais próxima a {rng.randrange(2, 9)} mm"),
                       ("Linfonodos", f"0/{rng.randrange(1, 4)} (sentinela)")]
        valores = [{"rotulo": "Tamanho do tumor", "valor": tamanho}]
    else:
        material = "Biópsia de próstata por agulha, 12 fragmentos identificados por sextante."
        macro = (f"Doze fragmentos filiformes, pardo-claros, medindo de 0,6 a "
                 f"{num(rng.randrange(12, 20) / 10)} cm. Incluídos na totalidade.")
        extensao = str(rng.randrange(2, 12))
        diagnostico = [("Tipo histológico", "adenocarcinoma acinar"),
                       ("Escore de Gleason", "7 (3 + 4)"), ("Grupo prognóstico (ISUP)", "2"),
                       ("Fragmentos comprometidos", f"{rng.randrange(2, 7)} de 12"),
                       ("Maior extensão tumoral", f"{extensao} mm")]
        valores = [{"rotulo": "Maior extensão tumoral", "valor": extensao}]
    f1 = Folha()
    cabecalho(f1, inst, cab.estilo)
    campos(f1, cab.pares)
    titulo(f1, tit)
    secao(f1, "MATERIAL")
    paragrafo(f1, material)
    secao(f1, "MACROSCOPIA")
    paragrafo(f1, macro)
    rodape(f1, inst, numeracao(estilo_num, 1, 2))
    f2 = Folha()
    cabecalho(f2, inst, cab.estilo)
    cab.continuacao(f2, rng)
    secao(f2, "MICROSCOPIA")
    paragrafo(f2, "Os cortes histológicos mostram neoplasia epitelial maligna infiltrativa, arranjada "
                  "em ninhos e cordões, com atipias nucleares moderadas e figuras de mitose. "
                  "Estroma desmoplásico. Não se identifica invasão angiolinfática.")
    secao(f2, "DIAGNÓSTICO")
    for rotulo, texto in diagnostico:
        paragrafo(f2, f"- {rotulo}: {texto}", recuo=6)
    if rng.random() < 0.3:
        _bibliografia(f2, rng)
    assinatura(f2, patologista, "Patologista", rng)
    rodape(f2, inst, numeracao(estilo_num, 2, 2), _assinatura_eletronica(cab, patologista))
    return DocGerado("anatomopatologico", tit, 4, inst.nome, dia, cab.armadilhas, cab.principal, cab.nomes,
                     [f1, f2], True, valores=valores, medicos=[cab.requisitante.nome, patologista.nome])


@modelo(4)
def imunohistoquimica(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "patologia")
    patologista = ficticio.medico(rng, inst.uf)
    sexo = pessoa.sexo if pessoa else rng.choice("FM")
    cab = _Cabecalho(rng, pessoa, dia, op, inst, "IHQ", "Data de recebimento")
    tit = rng.choice(("IMUNO-HISTOQUÍMICA", "LAUDO DE IMUNO-HISTOQUÍMICA"))
    ki67 = str(rng.randrange(5, 60))
    if sexo == "F":
        linhas = [("Receptor de estrogênio", "SP1", f"Positivo em {rng.randrange(50, 100)}% das células"),
                  ("Receptor de progesterona", "1E2", f"Positivo em {rng.randrange(5, 90)}% das células"),
                  ("HER2", "4B5", f"Escore {rng.choice(('0', '1+', '2+'))}"),
                  ("Ki-67", "30-9", f"{ki67}%")]
        conclusao = "Perfil imuno-histoquímico compatível com carcinoma de mama do subtipo luminal."
    else:
        linhas = [("P63", "4A4", "Negativo nas glândulas atípicas"),
                  ("AMACR (P504S)", "13H4", "Positivo nas glândulas atípicas"),
                  ("Ki-67", "30-9", f"{ki67}%")]
        conclusao = "Perfil imuno-histoquímico favorável a adenocarcinoma de próstata."
    f = Folha()
    cabecalho(f, inst, cab.estilo)
    campos(f, cab.pares)
    titulo(f, tit)
    paragrafo(f, f"Material: bloco de parafina nº {rng.randrange(1, 12)} do exame anatomopatológico "
                 "de referência.")
    tabela(f, [("Anticorpo", "Clone", "Resultado")] + linhas, (0.34, 0.16, 0.5))
    secao(f, "Conclusão")
    paragrafo(f, conclusao)
    _bibliografia(f, rng)
    assinatura(f, patologista, "Patologista", rng, eletronica=True)
    rodape(f, inst, None, _assinatura_eletronica(cab, patologista))
    return DocGerado("imunohistoquimica", tit, 4, inst.nome, dia, cab.armadilhas, cab.principal,
                     cab.nomes, [f], False, valores=[{"rotulo": "Ki-67", "valor": ki67}],
                     medicos=[cab.requisitante.nome, patologista.nome])


@modelo(4, varias_paginas=True)
def painel_genetico(rng: random.Random, pessoa: ficticio.Pessoa | None, dia: date, **op) -> DocGerado:
    inst = op.get("inst") or ficticio.instituicao(rng, "genetica")
    geneticista = ficticio.medico(rng, inst.uf)
    cab = _Cabecalho(rng, pessoa, dia, op, inst, "NGS", "Data da coleta")
    tit = rng.choice(("PAINEL GENÉTICO PARA CÂNCER HEREDITÁRIO", "TESTE GENÉTICO - BRCA1 E BRCA2 (NGS)"))
    estilo_num = rng.choice(("pagina", "pag", "barra"))
    genes = (("BRCA1", "NM_007294.4"), ("BRCA2", "NM_000059.4"), ("PALB2", "NM_024675.4"),
             ("TP53", "NM_000546.6"), ("ATM", "NM_000051.4"), ("CHEK2", "NM_007194.4"))
    coberturas = [(gene, transcrito, f"{rng.randrange(180, 900)}x", "100%") for gene, transcrito in genes]
    f1 = Folha()
    cabecalho(f1, inst, cab.estilo)
    campos(f1, cab.pares)
    titulo(f1, tit)
    secao(f1, "Resultado")
    paragrafo(f1, "NEGATIVO. Não foram identificadas variantes patogênicas ou provavelmente "
                  "patogênicas nos genes analisados.", fonte="Helvetica-Bold")
    secao(f1, "Genes analisados")
    tabela(f1, [("Gene", "Transcrito", "Cobertura média", "Região coberta")] + coberturas,
           (0.2, 0.3, 0.25, 0.25))
    rodape(f1, inst, numeracao(estilo_num, 1, 2))
    f2 = Folha()
    cabecalho(f2, inst, cab.estilo)
    cab.continuacao(f2, rng)
    secao(f2, "Metodologia")
    paragrafo(f2, "DNA extraído de sangue periférico. Captura das regiões codificantes e junções "
                  "éxon-íntron por sondas de hibridização, sequenciamento de nova geração e análise "
                  "de variações no número de cópias por profundidade de leitura.")
    secao(f2, "Limitações")
    paragrafo(f2, "O método não detecta rearranjos complexos, variantes em regiões intrônicas "
                  "profundas nem mosaicismo de baixa frequência.")
    _bibliografia(f2, rng)
    assinatura(f2, geneticista, "Genética Médica", rng)
    rodape(f2, inst, numeracao(estilo_num, 2, 2), f"Emitido em {inst.cidade} - {hora(rng)}")
    return DocGerado("painel_genetico", tit, 4, inst.nome, dia, cab.armadilhas, cab.principal, cab.nomes,
                     [f1, f2], True, medicos=[cab.requisitante.nome, geneticista.nome])
