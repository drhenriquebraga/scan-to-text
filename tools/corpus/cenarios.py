"""Os cenários do corpus: que documentos entram, de quem, em que arquivo e em que ordem.

Cada cenário devolve a `Montagem` (pacientes e documentos, com o destino
esperado de cada documento) e a lista de itens da origem (`Arquivo` ou `Zip`).
Quem grava o PDF e o gabarito é `gerar_corpus.py`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from corpus import ficticio
from corpus.folha import Impressao, verso
from corpus.modelos import MODELOS, DocGerado, sortear


@dataclass
class Entrada:
    """Uma página do arquivo: o que se imprime e a que documento pertence."""
    impressao: Impressao
    doc: str | None          # None = verso em branco
    indice: int = 0          # posição da página dentro do documento


@dataclass
class Arquivo:
    nome: str
    entradas: list[Entrada]
    senha: str | None = None


@dataclass
class Zip:
    nome: str
    itens: list  # Arquivo | Zip


@dataclass
class Plano:
    gerado: DocGerado
    destino: str | None
    xerox: bool = False


@dataclass
class Montagem:
    rng: random.Random
    pacientes: list[dict] = field(default_factory=list)
    pessoas: list[ficticio.Pessoa] = field(default_factory=list)
    docs: dict[str, Plano] = field(default_factory=dict)
    repetidos: list[tuple[str, str]] = field(default_factory=list)
    seriados: list[tuple[str, str]] = field(default_factory=list)

    def paciente(self, pessoa: ficticio.Pessoa, nome: str | None = None) -> str:
        """Pasta esperada de um paciente; `pessoa` diz quem é o ser humano por trás.

        Duas pastas da mesma pessoa (nome social sozinho e civil) não são
        contaminação; homônimos têm nascimentos diferentes, logo pessoas
        diferentes.
        """
        pid = f"p{len(self.pacientes) + 1}"
        if pessoa not in self.pessoas:
            self.pessoas.append(pessoa)
        self.pacientes.append({"id": pid, "nome": nome or pessoa.maiusculo, "nomes": [],
                               "nascimento": pessoa.nascimento.isoformat(),
                               "pessoa": f"h{self.pessoas.index(pessoa) + 1}"})
        return pid

    def registrar(self, gerado: DocGerado, destino: str | None, xerox: bool = False) -> str:
        did = f"d{len(self.docs) + 1}"
        self.docs[did] = Plano(gerado, destino, xerox)
        if destino is not None:
            registro = next(p for p in self.pacientes if p["id"] == destino)
            for nome in gerado.nomes:
                if nome not in registro["nomes"]:
                    registro["nomes"].append(nome)
        return did

    def novo(self, nome_modelo: str, pessoa: ficticio.Pessoa | None, destino: str | None,
             rng: random.Random | None = None, dia=None, **op) -> str:
        rng = rng or self.rng
        dia = dia or ficticio.data_exame(self.rng)
        return self.registrar(MODELOS[nome_modelo].funcao(rng, pessoa, dia, **op), destino)

    def sorteado(self, categoria: int, pessoa: ficticio.Pessoa, destino: str | None,
                 excluir: tuple[str, ...] = (), **op) -> str:
        return self.novo(sortear(self.rng, categoria, pessoa.sexo, excluir).nome, pessoa, destino, **op)

    def copia_xerox(self, did: str) -> str:
        plano = self.docs[did]
        return self.registrar(plano.gerado, plano.destino, xerox=True)

    def entradas(self, did: str) -> list[Entrada]:
        plano = self.docs[did]
        saida = []
        for indice, folha in enumerate(plano.gerado.folhas):
            xerox = None
            if plano.xerox:
                xerox = (self.rng.uniform(-6, 6), self.rng.uniform(-6, 6), self.rng.uniform(-0.4, 0.4))
            saida.append(Entrada(Impressao(folha, xerox=xerox), did, indice))
        return saida

    def arquivo(self, nome: str, docs: list[str]) -> Arquivo:
        return Arquivo(nome, [e for did in docs for e in self.entradas(did)])


def _fora_de_ordem(entradas: list[Entrada], did: str) -> bool:
    ordem = [e.indice for e in entradas if e.doc == did]
    return ordem != sorted(ordem)


# --- os cenários ---------------------------------------------------------------

def pilha(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    docs = [m.sorteado(c, p, pid) for c in (1, 2, 3, 5)]
    # a imuno-histoquímica garante bibliografia no corpus
    docs.append(m.novo("imunohistoquimica", p, pid))
    docs.append(m.sorteado(rng.choice((1, 2, 3, 5)), p, pid))
    rng.shuffle(docs)
    return m, [m.arquivo("scan001.pdf", docs)]


def embaralhado(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    varias = sorted(n for n, md in MODELOS.items() if md.varias_paginas and p.sexo in md.sexos)
    docs = [m.novo(n, p, pid, paginas=3) for n in rng.sample(varias, 3)]
    docs.append(m.novo("encaminhamento", p, pid))
    entradas = [e for did in docs for e in m.entradas(did)]
    if rng.random() < 0.5:
        entradas.reverse()      # a pilha inteira entrou de cabeça para cima ao contrário
    else:
        while not all(_fora_de_ordem(entradas, did) for did in docs[:3]):
            rng.shuffle(entradas)
    return m, [Arquivo("scan002.pdf", entradas)]


def verso_branco(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    docs = [m.sorteado(c, p, pid) for c in rng.sample((1, 2, 3, 4, 5), 3)]
    entradas = []
    for did in docs:
        for entrada in m.entradas(did):
            entradas += [entrada, Entrada(Impressao(verso(rng, rng.random() < 0.5)), None)]
    return m, [Arquivo("scan003.pdf", entradas)]


def dois_pacientes(rng: random.Random):
    m = Montagem(rng)
    p1, p2 = ficticio.pessoa(rng, "F"), ficticio.pessoa(rng, "M")
    id1, id2 = m.paciente(p1), m.paciente(p2)
    docs = [m.sorteado(c, p1, id1) for c in rng.sample((1, 2, 3, 4), 2)]
    # com dois pacientes no arquivo, a anotação sem nome não tem dono presumível
    docs.append(m.novo("anotacao_manuscrita", None, None))
    docs += [m.sorteado(c, p2, id2) for c in rng.sample((1, 2, 3, 4), 2)]
    return m, [m.arquivo("scan004.pdf", docs)]


def contaminacao(rng: random.Random):
    m = Montagem(rng)
    p1, p2 = ficticio.pessoa(rng), ficticio.pessoa(rng)
    id1, id2 = m.paciente(p1), m.paciente(p2)
    laboratorio = ficticio.instituicao(rng, "laboratorio")
    a = m.sorteado(rng.choice((1, 2)), p1, id1)
    b = m.novo("bioquimica", p1, id1, inst=laboratorio)
    # a página de outro paciente, do mesmo laboratório e com o mesmo título
    intruso = m.novo("bioquimica", p2, id2, inst=laboratorio, titulo=m.docs[b].gerado.titulo)
    c = m.sorteado(4, p1, id1)
    return m, [m.arquivo("scan005.pdf", [a, b, intruso, c])]


def anotacao_sem_nome(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    a = m.sorteado(rng.choice((1, 2)), p, pid)
    nota = m.novo("anotacao_manuscrita", None, pid)
    laboratorio_sem_nome = m.novo(rng.choice(("hemograma", "bioquimica")), None, pid)
    c = m.sorteado(4, p, pid)
    return m, [m.arquivo("scan006.pdf", [a, nota, laboratorio_sem_nome, c])]


def xerox_repetido(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    e = m.sorteado(1, p, pid)
    t = m.novo("tomografia", p, pid)
    h = m.novo("hemograma", p, pid)
    t2, h2 = m.copia_xerox(t), m.copia_xerox(h)
    m.repetidos += [(t, t2), (h, h2)]
    return m, [m.arquivo("scan007.pdf", [e, t, h, t2, h2])]


def seriado(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    laboratorio = ficticio.instituicao(rng, "laboratorio")
    semente = rng.random()
    dias = set()
    while len(dias) < 3:
        dias.add(ficticio.data_exame(rng))
    escala = (0.5, 9.0, 2) if p.sexo == "M" else (6.0, 48.0, 1)
    valores = set()
    while len(valores) < 3:
        valores.add(f"{rng.uniform(escala[0], escala[1]):.{escala[2]}f}".replace(".", ","))
    serie = [m.novo("marcador_unico", p, pid, rng=random.Random(semente), dia=d, inst=laboratorio,
                    valor=v) for d, v in zip(sorted(dias), sorted(valores))]
    # laudo retificado: mesma data, mesmo laboratório, um único valor diferente
    semente_h, dia_h = rng.random(), ficticio.data_exame(rng)
    h1 = m.novo("hemograma", p, pid, rng=random.Random(semente_h), dia=dia_h, inst=laboratorio)
    original = next(v["valor"] for v in m.docs[h1].gerado.valores if v["rotulo"] == "Hemoglobina")
    corrigido = f"{float(original.replace(',', '.')) - 0.6:.1f}".replace(".", ",")
    h2 = m.novo("hemograma", p, pid, rng=random.Random(semente_h), dia=dia_h, inst=laboratorio,
                ajuste=("Hemoglobina", corrigido))
    m.seriados += [(serie[0], serie[1]), (serie[0], serie[2]), (serie[1], serie[2]), (h1, h2)]
    docs = serie + [h1, h2]
    rng.shuffle(docs)
    return m, [m.arquivo("scan008.pdf", docs)]


def senha(rng: random.Random):
    """PDF protegido: nada dele é lido, então nada vai para o gabarito além do arquivo."""
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    entradas = []
    for categoria in (3, 2):
        modelo = sortear(rng, categoria, p.sexo)
        gerado = modelo.funcao(rng, p, ficticio.data_exame(rng))
        entradas += [Entrada(Impressao(folha), None) for folha in gerado.folhas]
    return m, [Arquivo("scan009.pdf", entradas, senha=f"clinica{rng.randrange(1000, 9999)}")]


def zip_aninhado(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    laudos = m.arquivo("laudos.pdf", [m.sorteado(1, p, pid), m.sorteado(2, p, pid)])
    laboratorio = m.arquivo("laboratorio.pdf", [m.novo(rng.choice(("hemograma", "bioquimica")), p, pid)])
    return m, [Zip("exames.zip", [laudos, Zip("interno.zip", [laboratorio])])]


def cabeca_para_baixo(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    docs = [m.sorteado(c, p, pid) for c in rng.sample((1, 2, 3, 4, 5), 3)]
    entradas = [e for did in docs for e in m.entradas(did)]
    quantas = max(1, len(entradas) // 3)
    for posicao in rng.sample(range(len(entradas)), quantas):
        e = entradas[posicao]
        entradas[posicao] = Entrada(Impressao(e.impressao.folha, rotacao=180), e.doc, e.indice)
    return m, [Arquivo("scan010.pdf", entradas)]


def homonimos(rng: random.Random):
    m = Montagem(rng)
    p1 = ficticio.pessoa(rng)
    p2 = ficticio.Pessoa(p1.nome, p1.sexo, ficticio.outra_data(rng, p1.nascimento, 400, 4000))
    id1, id2 = m.paciente(p1), m.paciente(p2)
    # sem data de nascimento impressa não há como separar homônimos: a guia não a traz
    op = {"forma": "maiusculo", "com_nascimento": True, "excluir": ("guia_sadt",)}

    def docs(pessoa, pid, quantos):
        return [m.sorteado(c, pessoa, pid, **op) for c in rng.sample((1, 2, 3, 4), quantos)]

    return m, [m.arquivo("scan011.pdf", docs(p1, id1, 2)), m.arquivo("scan012.pdf", docs(p2, id2, 2)),
               m.arquivo("scan013.pdf", docs(p1, id1, 1))]


def nome_social(rng: random.Random):
    m = Montagem(rng)
    civil = ficticio.pessoa(rng, "M")
    social = f"{rng.choice(ficticio.PRIMEIROS_FEMININOS)} {civil.nome.split()[-1]}".upper()
    pessoa = ficticio.Pessoa(civil.nome, "F", civil.nascimento, social)
    so_social = m.paciente(pessoa, nome=social)
    com_civil = m.paciente(pessoa, nome=civil.maiusculo)
    neutros = ("tomografia", "bioquimica", "encaminhamento", "hemograma", "ecocardiograma",
               "eletrocardiograma")
    a, b, c, d = rng.sample(neutros, 4)
    apenas = [("Nome social", social)]
    ambos = [("Paciente", civil.maiusculo), ("Nome social", social)]
    primeiro = [m.novo(a, pessoa, so_social, nomes=apenas), m.novo(b, pessoa, so_social, nomes=apenas)]
    segundo = [m.novo(c, pessoa, com_civil, nomes=ambos), m.novo(d, pessoa, com_civil, nomes=apenas)]
    return m, [m.arquivo("scan014.pdf", primeiro), m.arquivo("scan015.pdf", segundo)]


def laboratorio_vr(rng: random.Random):
    m = Montagem(rng)
    p = ficticio.pessoa(rng)
    pid = m.paciente(p)
    docs = [
        m.novo("hemograma", p, pid, rotulo_vr="Valores de referência"),
        m.novo("bioquimica", p, pid, blocos=["glicose", "creatinina", "ureia", "tsh"],
               rotulos_vr=["Valor de referência", "Faixa de referência", "VR", "Referência"]),
        m.novo("marcadores", p, pid, rotulo_vr="Intervalo de referência"),
        # fora de 3 LABORATORIO a referência tem de ficar
        m.novo("densitometria", p, pid),
        m.novo("ecocardiograma", p, pid),
    ]
    return m, [m.arquivo("scan016.pdf", docs)]


CONSTRUTORES = {
    "pilha": pilha, "embaralhado": embaralhado, "verso_branco": verso_branco,
    "dois_pacientes": dois_pacientes, "contaminacao": contaminacao,
    "anotacao_sem_nome": anotacao_sem_nome, "xerox_repetido": xerox_repetido, "seriado": seriado,
    "senha": senha, "zip": zip_aninhado, "cabeca_para_baixo": cabeca_para_baixo,
    "homonimos": homonimos, "nome_social": nome_social, "laboratorio_vr": laboratorio_vr,
}
