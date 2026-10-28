"""Pessoas, instituições e lugares inventados para o corpus sintético.

Os sobrenomes são cunhados para não apontar pessoa real, e os de médicos vêm de
uma lista separada da de pacientes: assim o avaliador sabe, pela chave do nome,
se uma pasta recebeu o nome de um médico. Os primeiros nomes são comuns de
propósito, porque o validador de nomes trata primeiro nome comum como sinal.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

PRIMEIROS_FEMININOS = (
    "Maria", "Ana", "Francisca", "Antônia", "Adriana", "Juliana", "Márcia", "Luzia",
    "Raimunda", "Sebastiana", "Terezinha", "Rosângela", "Cleonice", "Irani", "Zuleica",
    "Odete", "Neide", "Doralice", "Edileuza", "Marlene", "Ivone", "Lucimar", "Rosimeire",
    "Valdirene",
)
PRIMEIROS_MASCULINOS = (
    "José", "João", "Antônio", "Francisco", "Carlos", "Paulo", "Pedro", "Luiz",
    "Sebastião", "Raimundo", "Benedito", "Geraldo", "Joaquim", "Osvaldo", "Valdemar",
    "Juvenal", "Anselmo", "Dorival", "Ernesto", "Nivaldo", "Edvaldo", "Gervásio",
    "Arlindo", "Manoel",
)
MEIOS_FEMININOS = ("Aparecida", "Helena", "Cristina", "Cecília", "Rosa", "Lúcia", "Beatriz")
MEIOS_MASCULINOS = ("Augusto", "Roberto", "Eduardo", "Donizete", "Aparecido", "Vicente", "Rafael")
PARTICULAS = ("da", "de", "do", "dos")
SOBRENOMES = (
    "Albarrino", "Bentivolo", "Castanhedo", "Dornellino", "Esterçal", "Gravatinho",
    "Ilharco", "Lagoeiro", "Mirandelo", "Nogueirão", "Orvalhense", "Quintarelo",
    "Sabiazal", "Tamarindeiro", "Valverdino", "Zimbrão", "Arrudela", "Coutinhal",
    "Figueiral", "Pereirinha", "Macedônio", "Brasilino", "Carvalhedo", "Serrazul",
    "Jaqueirão", "Limoeiral", "Pitangal", "Umbuzeiro", "Cajazeiro", "Oiticica",
)
MEDICAS = ("Silvana", "Beatriz", "Letícia", "Mirela", "Renata", "Priscila", "Tatiana", "Viviane")
MEDICOS = ("Heitor", "Otávio", "Vicente", "Laércio", "Rômulo", "Frederico", "Gustavo", "Marcelo")
SOBRENOMES_MEDICOS = (
    "Corvanha", "Esmerindo", "Farolfi", "Gouvarte", "Heringuel", "Montesquino",
    "Nobreval", "Pestanaro", "Salvaterro", "Tavernim", "Urbanelo", "Zambretti",
    "Aldovrande", "Belcastro", "Crivellaro", "Dalcorso",
)
CIDADES = (("Vale Sereno", "MG"), ("Porto Aurelino", "SC"), ("Cerranova", "GO"),
           ("Alvorelo", "BA"), ("Pedravela", "SP"), ("Lagoa Serrana", "PR"))
RUAS = ("Rua das Quaresmeiras", "Avenida Tamarineira", "Rua Doutor Anselmo Farolfi",
        "Travessa dos Ipês Amarelos", "Alameda dos Sabiás", "Rua Coronel Juvenal Esterçal",
        "Avenida das Paineiras", "Rua Professora Doralice Mirandelo")
BAIRROS = ("Centro", "Jardim Primavera", "Vila Nova Aurora", "Alto da Serra", "Bela Vista do Lago")
INSTITUICOES = {
    "laboratorio": ("Laboratório Quintalba", "Análises Clínicas Veridião",
                    "Laboratório Pedravela Diagnósticos", "Biolab Serra Clara"),
    "imagem": ("Clínica de Imagem Luzvale", "Centro de Diagnóstico por Imagem Marabel",
               "Radiologia Cerranova", "Imagem Alvorelo Diagnósticos"),
    "patologia": ("Instituto de Patologia Cerravia", "Laboratório de Anatomia Patológica Serralume",
                  "Patologia Diagnóstica Vilarosa"),
    "hospital": ("Hospital Santa Evarista", "Centro Oncológico Porto Aurelino",
                 "Clínica Oncológica Luzvale", "Hospital Geral de Lagoa Serrana"),
    "cardiologia": ("Cardioclínica Pedravela", "Instituto do Coração Alvorelo"),
    "genetica": ("Genolab Cerranova Medicina Genômica", "Genômica Vilarosa"),
}
CONVENIOS = ("Plano Quintalba Saúde", "Serra Clara Assistência Médica", "Aurelino Saúde",
             "Particular")


@dataclass(frozen=True)
class Pessoa:
    nome: str                 # como na certidão, em caixa de título
    sexo: str                 # "F" ou "M"
    nascimento: date
    social: str | None = None

    @property
    def maiusculo(self) -> str:
        return self.nome.upper()


@dataclass(frozen=True)
class Medico:
    nome: str
    sexo: str
    conselho: str             # "CRM-MG 104233"

    @property
    def tratado(self) -> str:
        return ("Dra. " if self.sexo == "F" else "Dr. ") + self.nome


@dataclass(frozen=True)
class Instituicao:
    nome: str
    rua: str
    numero: int
    bairro: str
    cidade: str
    uf: str
    cep: str
    telefone: str
    cnes: str

    @property
    def endereco(self) -> str:
        return (f"{self.rua}, {self.numero} - {self.bairro} - {self.cidade}/{self.uf} - "
                f"CEP {self.cep} - Tel. {self.telefone}")


def todos_os_textos() -> tuple[str, ...]:
    listas = (PRIMEIROS_FEMININOS, PRIMEIROS_MASCULINOS, MEIOS_FEMININOS, MEIOS_MASCULINOS,
              SOBRENOMES, MEDICAS, MEDICOS, SOBRENOMES_MEDICOS, RUAS, BAIRROS, CONVENIOS,
              tuple(c for c, _ in CIDADES), *INSTITUICOES.values())
    return tuple(texto for lista in listas for texto in lista)


def _sobrenome(rng: random.Random, lista: tuple[str, ...], evitar: str = "") -> str:
    escolha = rng.choice([s for s in lista if s != evitar])
    return escolha


def pessoa(rng: random.Random, sexo: str | None = None, nascimento: date | None = None) -> Pessoa:
    sexo = sexo or rng.choice("FM")
    primeiro = rng.choice(PRIMEIROS_FEMININOS if sexo == "F" else PRIMEIROS_MASCULINOS)
    partes = [primeiro]
    if rng.random() < 0.6:
        partes.append(rng.choice(MEIOS_FEMININOS if sexo == "F" else MEIOS_MASCULINOS))
    primeiro_sobrenome = _sobrenome(rng, SOBRENOMES)
    if rng.random() < 0.4:
        partes.append(rng.choice(PARTICULAS))
    partes.append(primeiro_sobrenome)
    if rng.random() < 0.5:
        partes.append(_sobrenome(rng, SOBRENOMES, evitar=primeiro_sobrenome))
    if nascimento is None:
        nascimento = date(1940, 1, 1) + timedelta(days=rng.randrange(365 * 48))
    return Pessoa(" ".join(partes), sexo, nascimento)


def medico(rng: random.Random, uf: str = "MG") -> Medico:
    sexo = rng.choice("FM")
    primeiro = rng.choice(MEDICAS if sexo == "F" else MEDICOS)
    a = rng.choice(SOBRENOMES_MEDICOS)
    b = rng.choice([s for s in SOBRENOMES_MEDICOS if s != a])
    return Medico(f"{primeiro} {a} {b}", sexo, f"CRM-{uf} {rng.randrange(10000, 199999)}")


def instituicao(rng: random.Random, tipo: str) -> Instituicao:
    cidade, uf = rng.choice(CIDADES)
    return Instituicao(
        nome=rng.choice(INSTITUICOES[tipo]), rua=rng.choice(RUAS), numero=rng.randrange(10, 2400),
        bairro=rng.choice(BAIRROS), cidade=cidade, uf=uf,
        cep=f"{rng.randrange(10, 99)}{rng.randrange(100, 999)}-{rng.randrange(100, 999)}",
        telefone=f"({rng.randrange(11, 99)}) 3{rng.randrange(100, 999)}-{rng.randrange(1000, 9999)}",
        cnes=f"{rng.randrange(1000000, 9999999)}")


def data_exame(rng: random.Random, antes_de: date = date(2026, 6, 30)) -> date:
    """Data de exame plausível, quase sempre ambígua (dia <= 12 e dia != mês).

    O corpus precisa de pelo menos 200 datas ambíguas; com dia
    livre, só 40% delas seriam.
    """
    while True:
        dia = date(2024, 1, 1) + timedelta(days=rng.randrange((antes_de - date(2024, 1, 1)).days))
        if rng.random() < 0.1 or (dia.day <= 12 and dia.day != dia.month):
            return dia


def outra_data(rng: random.Random, perto_de: date, dias_min: int, dias_max: int,
               evitar: tuple[date, ...] = ()) -> date:
    """Data a uma distância em dias de outra (negativa = antes), diferente das evitadas."""
    while True:
        candidata = perto_de + timedelta(days=rng.randint(dias_min, dias_max))
        if candidata != perto_de and candidata not in evitar:
            return candidata
