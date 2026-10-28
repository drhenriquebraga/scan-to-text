"""Modelos de laudo: cada um devolve as folhas desenhadas e a verdade sobre elas.

Um modelo recebe `(rng, pessoa, dia, **op)`: o sorteador, o paciente (ou None
para documento sem nome), a data do exame e opções do cenário (`inst` para
fixar a instituição, `nomes` para fixar os rótulos de nome, `forma` para a
grafia do nome, `com_nascimento` para imprimir a data de nascimento). A
verdade volta em `DocGerado` e vira o gabarito.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from corpus.ficticio import Pessoa
from corpus.folha import Folha

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro")
ROTULOS_NASCIMENTO = ("Data de nascimento", "DN", "Nasc.", "Nascimento")
REVISTAS = ("Rev Bras Patol Sintet", "Arq Oncol Exemplar", "J Genet Ficticia",
            "Rev Serranova Med", "Cad Patol Alvorelo")


@dataclass
class DocGerado:
    modelo: str
    titulo: str
    categoria: int
    instituicao: str | None
    data: date | None                   # data do exame esperada; None = sem papel claro
    datas_armadilha: list[date]         # nascimento, impressão, validade, DUM, anterior
    paciente: str | None                # nome principal, como impresso
    nomes: list[str]                    # todas as grafias impressas do paciente
    folhas: list[Folha]
    numerada: bool
    pedido: bool = False
    valores: list[dict] = field(default_factory=list)       # {"rotulo", "valor"}
    referencias: list[str] = field(default_factory=list)    # textos de valor de referência
    medicos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Modelo:
    nome: str
    categoria: int
    funcao: Callable[..., DocGerado]
    sexos: tuple[str, ...]
    sem_nome: bool            # aceita pessoa None
    sorteavel: bool           # pode ser sorteado para compor pilhas
    varias_paginas: bool


MODELOS: dict[str, Modelo] = {}


def modelo(categoria: int, sexos: tuple[str, ...] = ("F", "M"), sem_nome: bool = False,
           sorteavel: bool = True, varias_paginas: bool = False):
    def registrar(funcao: Callable[..., DocGerado]) -> Callable[..., DocGerado]:
        MODELOS[funcao.__name__] = Modelo(funcao.__name__, categoria, funcao, sexos, sem_nome,
                                          sorteavel, varias_paginas)
        return funcao
    return registrar


def sortear(rng: random.Random, categoria: int, sexo: str, excluir: tuple[str, ...] = ()) -> Modelo:
    candidatos = [m for m in MODELOS.values()
                  if m.categoria == categoria and m.sorteavel and sexo in m.sexos
                  and m.nome not in excluir]
    return rng.choice(sorted(candidatos, key=lambda m: m.nome))


# --- formatos ----------------------------------------------------------------

def dmy(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def formato_data(rng: random.Random, d: date, ano_curto: bool = True) -> str:
    sorteio = rng.random()
    if sorteio < 0.7:
        return dmy(d)
    if sorteio < 0.8:
        return f"{d.day:02d}.{d.month:02d}.{d.year}"
    if sorteio < 0.9 or not ano_curto:
        return f"{d.day:02d}-{d.month:02d}-{d.year}"
    return f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}"


def extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def hora(rng: random.Random) -> str:
    return f"{rng.randrange(7, 19):02d}:{rng.randrange(60):02d}"


def num(valor: float, casas: int = 1) -> str:
    """Número no formato brasileiro: vírgula decimal, ponto de milhar."""
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def idade(nascimento: date, dia: date) -> int:
    return dia.year - nascimento.year - ((dia.month, dia.day) < (nascimento.month, nascimento.day))


def numeracao(estilo: str | None, n: int, total: int) -> str | None:
    if estilo is None:
        return None
    return {"pagina": f"Página {n} de {total}", "pag": f"Pág. {n}/{total}",
            "barra": f"{n}/{total}", "folha": f"Folha {n}"}[estilo]


def estilo_numeracao(rng: random.Random, paginas: int) -> str | None:
    if paginas > 1:
        return rng.choice(("pagina", "pag", "barra"))
    return "pagina" if rng.random() < 0.4 else None


# --- nome do paciente ----------------------------------------------------------

def forma_do_nome(rng: random.Random, pessoa: Pessoa, forca: str | None = None) -> str:
    partes = pessoa.nome.split()
    particulas = {"da", "de", "do", "dos", "das", "e"}
    meio = [i for i, p in enumerate(partes[1:-1], start=1) if p not in particulas]
    sorteio = rng.random()
    escolha = forca or ("maiusculo" if sorteio < 0.7 else "titulo" if sorteio < 0.85
                        else "abreviado" if sorteio < 0.95 else "invertido")
    if escolha == "abreviado" and meio:
        return " ".join(p[0] + "." if i == meio[0] else p for i, p in enumerate(partes)).upper()
    if escolha == "invertido":
        return f"{partes[-1]}, {' '.join(partes[:-1])}".upper()
    if escolha == "titulo":
        return pessoa.nome
    return pessoa.maiusculo


def identificacao(rng: random.Random, pessoa: Pessoa | None, op: dict,
                  rotulos_nome: tuple[str, ...], chance_nascimento: float = 0.7
                  ) -> tuple[list[tuple[str, str]], str | None, list[str], date | None]:
    """Pares (rótulo, valor) do paciente, nome principal, grafias e nascimento impresso."""
    if "nomes" in op:
        pares = list(op["nomes"])
        nomes = [valor for _, valor in pares]
        principal: str | None = nomes[0]
    elif pessoa is None:
        return [], None, [], None
    else:
        principal = forma_do_nome(rng, pessoa, op.get("forma"))
        pares, nomes = [(rng.choice(rotulos_nome), principal)], [principal]
    nascimento = None
    if pessoa is not None and (op.get("com_nascimento") or rng.random() < chance_nascimento):
        nascimento = pessoa.nascimento
        pares.append((rng.choice(ROTULOS_NASCIMENTO), formato_data(rng, nascimento, ano_curto=False)))
    return pares, principal, nomes, nascimento


def citacao(rng: random.Random, sobrenomes: tuple[str, ...]) -> str:
    autores = rng.sample(sobrenomes, 3)
    iniciais = ["".join(rng.choice("ABCDEFGHJLMPRST") for _ in range(2)) for _ in autores]
    temas = ("Avaliação imuno-histoquímica de marcadores prognósticos em série institucional",
             "Concordância interobservador na graduação histológica",
             "Painéis multigênicos na prática oncológica: experiência de um centro",
             "Padronização da leitura de receptores hormonais em biópsias por agulha",
             "Variantes de significado incerto: reclassificação ao longo de cinco anos")
    return (f"{autores[0]} {iniciais[0]}, {autores[1]} {iniciais[1]}, {autores[2]} {iniciais[2]}, "
            f"et al. {rng.choice(temas)}. {rng.choice(REVISTAS)}. {rng.randrange(2012, 2025)};"
            f"{rng.randrange(8, 60)}({rng.randrange(1, 12)}):{rng.randrange(100, 400)}-"
            f"{rng.randrange(401, 600)}.")


# O registro acontece na importação dos módulos de cada categoria.
from corpus.modelos import relatorios, imagem, laboratorio, patologia, outros  # noqa: E402,F401
