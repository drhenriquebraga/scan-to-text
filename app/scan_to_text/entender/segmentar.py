"""Segmentação: as páginas de um arquivo viram documentos, em qualquer ordem.

De cada página se levantam instituição, título e numeração
impressa. Páginas com a mesma instituição, o mesmo título e numeração
compatível ("X de Y" com o mesmo Y) formam um documento, ordenado por X. Sem
numeração, páginas seguidas da mesma instituição e sem título formam um
documento. Página que não se encaixa vira documento próprio: nenhuma página se
perde. Páginas em branco ficam fora, e quem chama as registra.

Rótulos e padrões vêm de `paginas.yaml` do pacote de idioma. Os padrões são
aplicados ao texto dobrado (minúsculas e sem acento).

O layout às vezes põe o timbre da instituição no corpo, e não no cabeçalho.
Por isso o timbre também se reconhece por posição (acima do título, no topo) e
por repetição (a mesma linha no topo de várias páginas do arquivo).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Mapping, Sequence

from rapidfuzz import fuzz

from scan_to_text.entender import identidade
from scan_to_text.entender.classificar import e_titulo_de_documento
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, Linha, Pagina

# Faixas da página, em fração da altura, com a origem no topo.
ZONA_CABECALHO = 0.15
ZONA_TITULO = 0.40
ZONA_PE = 0.15
# Instituição e linha repetida no topo pedem três letras: "1 de 2" e "Fl. 1",
# numeração que o pacote não reconhece, não são timbre.
MIN_LETRAS = 3
# O título pede duas: "CA 15-3" e "CA 125" são títulos de exame.
MIN_LETRAS_TITULO = 2
# Linha de corpo mais longa que isto é texto corrido, não timbre.
MAX_INSTITUICAO = 80
# Semelhança mínima (rapidfuzz.ratio, 0 a 100) para duas instituições ou dois
# títulos lidos por OCR serem o mesmo. Os dígitos têm de ser idênticos.
SEMELHANCA = 90
MAX_PAGINAS = 999
# Até onde, abaixo do timbre, fica a linha de endereço ou contato dele.
DISTANCIA_ENDERECO = 0.035

_TITULOS = ("titulo", "secao")
_SEM_LETRA = re.compile(r"[\W_]+")
# "CA 15-3: 39,4 U/mL": rótulo e número é linha de resultado, não título. Não
# vale para instituição: o timbre traz "Fone: 3333-0000" na mesma linha.
_LINHA_DE_VALOR = re.compile(r"^[^:]{1,60}:\s*[<>=≤≥]?\s*\d")
_DIGITOS = re.compile(r"\d+")


def dobrar(texto: str) -> str:
    """Minúsculas e sem acento, caractere a caractere.

    O resultado tem o mesmo comprimento do original, para que um trecho achado
    no texto dobrado possa ser cortado no original pelas mesmas posições.
    """
    saida = []
    for c in texto:
        base = unicodedata.normalize("NFD", c)[0].lower()
        saida.append(base if len(base) == 1 else c)
    return "".join(saida)


def chave_texto(texto: str) -> str:
    """Forma de comparação: dobrada, sem pontuação, espaços colapsados."""
    return " ".join(_SEM_LETRA.sub(" ", dobrar(texto)).split())


def alternativa(rotulos: Sequence[str]) -> str:
    """Rótulos do pacote viram uma alternância de regex sobre o texto dobrado."""
    formas = {r"\s+".join(re.escape(p) for p in dobrar(str(r)).split()) for r in rotulos}
    formas.discard("")
    return "(?:" + "|".join(sorted(formas, key=lambda f: (-len(f), f))) + ")"


@dataclass(frozen=True)
class _Regras:
    numeracao: tuple[tuple[re.Pattern, bool], ...]
    campo: re.Pattern | None
    campo_forte: re.Pattern | None
    instituicao: re.Pattern | None
    endereco_abre: re.Pattern | None
    endereco_depois: re.Pattern | None


@lru_cache(maxsize=32)
def _montar(numeracao: tuple[tuple[str, bool], ...], campos: tuple[str, ...],
            fortes: tuple[str, ...], abre: tuple[str, ...], contem: tuple[str, ...],
            endereco: tuple[str, ...]) -> _Regras:
    # Rótulo de campo: no começo da linha, fechado em fronteira de palavra.
    campo = (re.compile(r"^[\W_]*" + alternativa(campos) + r"(?![^\W\d_])[^:]{0,25}:")
             if campos else None)
    forte = re.compile(r"^[\W_]*" + alternativa(fortes) + r"(?![^\W\d_])") if fortes else None
    marcas = ([r"^[\W_]*" + alternativa(abre) + r"(?![^\W\d_])"] if abre else []) + \
             ([r"(?<![^\W\d_])" + alternativa(contem) + r"(?![^\W\d_])"] if contem else [])
    instituicao = re.compile("|".join(f"(?:{m})" for m in marcas)) if marcas else None
    # Palavra de endereço ou contato abrindo a linha, ou depois de " - ".
    contato = alternativa(endereco) + r"(?![^\W\d_])" if endereco else None
    abre_contato = re.compile(r"^[\W_]*" + contato) if contato else None
    depois_contato = re.compile(r"\s[-–]\s*" + contato) if contato else None
    return _Regras(tuple((re.compile(p), m) for p, m in numeracao), campo, forte, instituicao,
                   abre_contato, depois_contato)


def _regras(pacote: Pacote) -> _Regras:
    secao = pacote.secoes.get("paginas") or {}
    numeracao = tuple((str(item["padrao"]), bool(item.get("so_margem", False)))
                      for item in secao.get("numeracao") or [])
    marcas = secao.get("instituicao") or {}
    return _montar(numeracao, tuple(secao.get("campos") or ()),
                   tuple(secao.get("campos_fortes") or ()),
                   tuple(marcas.get("abre") or ()), tuple(marcas.get("contem") or ()),
                   tuple(marcas.get("endereco") or ()))


def _topo(linha: Linha) -> float:
    return linha.caixa[1]


def _linhas(pagina: Pagina) -> list[Linha]:
    return [b for b in pagina.blocos if isinstance(b, Linha)]


def _na_margem(linha: Linha) -> bool:
    return (linha.camada in ("cabecalho", "rodape") or _topo(linha) < ZONA_CABECALHO
            or _topo(linha) >= 1 - ZONA_PE)


def _coerente(x: int, y: int | None) -> bool:
    return 1 <= x <= MAX_PAGINAS and (y is None or x <= y <= MAX_PAGINAS)


def numeracao(pagina: Pagina, pacote: Pacote) -> tuple[int, int | None] | None:
    """Numeração impressa da página: (X, Y), (X, None) ou None.

    Padrões na ordem do pacote; para cada um, as linhas da margem primeiro
    (rodapé, cabeçalho, faixas do pé e do topo) e depois as demais. Número
    incoerente (X > Y, zero) não conta.
    """
    linhas = _linhas(pagina)
    ordem = {"rodape": 0, "cabecalho": 1}
    margem = sorted((l for l in linhas if _na_margem(l)), key=lambda l: ordem.get(l.camada, 2))
    resto = [l for l in linhas if not _na_margem(l)]
    for padrao, so_margem in _regras(pacote).numeracao:
        for linha in margem if so_margem else margem + resto:
            for achado in padrao.finditer(dobrar(linha.texto)):
                x = int(achado.group("x"))
                y = achado.groupdict().get("y")
                y = int(y) if y is not None else None
                if _coerente(x, y):
                    return x, y
    return None


def e_numeracao(texto: str, pacote: Pacote) -> bool:
    """A linha inteira é só a numeração da página ("Página 2 de 5")."""
    d = dobrar(texto).strip()
    return any(p.fullmatch(d) for p, _ in _regras(pacote).numeracao)


def sem_numeracao(texto: str, pacote: Pacote) -> str:
    """Texto dobrado, com espaços colapsados e a numeração da página trocada por "#".

    Só a numeração muda; os outros números ficam, para que um resultado no pé
    da página não pareça repetição de outro.
    """
    d = " ".join(dobrar(texto).split())
    for padrao, _ in _regras(pacote).numeracao:
        d = padrao.sub("#", d)
    return d


def _e_campo(d: str, regras: _Regras) -> bool:
    return bool((regras.campo and regras.campo.search(d))
                or (regras.campo_forte and regras.campo_forte.search(d)))


def _util(linha: Linha, regras: _Regras, min_letras: int = MIN_LETRAS) -> bool:
    """Linha que pode ser instituição: tem letras e não é campo."""
    t = linha.texto.strip()
    if sum(c.isalpha() for c in t) < min_letras:
        return False
    d = dobrar(t)
    if _e_campo(d, regras):
        return False
    return not any(p.fullmatch(d) for p, _ in regras.numeracao)


def _pode_ser_titulo(linha: Linha, regras: _Regras) -> bool:
    return (_util(linha, regras, MIN_LETRAS_TITULO)
            and not _LINHA_DE_VALOR.match(linha.texto.strip()))


def _e_endereco(linha: Linha, regras: _Regras) -> bool:
    """Linha de endereço ou contato do timbre: a palavra abre a linha ("Rua...",
    "Fone: ..."), ou vem depois de " - " numa linha que não é campo. A linha do
    paciente ("Paciente: ... - Rua ...", "... Tel: ...") não é do timbre."""
    d = dobrar(linha.texto)
    if regras.endereco_abre is not None and regras.endereco_abre.match(d):
        return True
    return (regras.endereco_depois is not None and bool(regras.endereco_depois.search(d))
            and not _e_campo(d, regras))


def _cara_de_timbre(linha: Linha, regras: _Regras, linhas: Sequence[Linha]) -> bool:
    """Nome com marca de instituição, ou linha do cabeçalho com o endereço logo abaixo."""
    if regras.instituicao is not None and regras.instituicao.search(dobrar(linha.texto)):
        return True
    return _topo(linha) < ZONA_CABECALHO and any(
        0 < _topo(outra) - _topo(linha) <= DISTANCIA_ENDERECO and _e_endereco(outra, regras)
        for outra in linhas if outra is not linha)


@dataclass(frozen=True)
class _Perfil:
    pagina: Pagina
    pos: int
    num: tuple[int, int | None] | None
    titulo: str
    inst: str | None
    nome: str | None


def _titulo_e_instituicao(pagina: Pagina, pacote: Pacote,
                          repetidas: frozenset[str]) -> tuple[Linha | None, Linha | None]:
    regras = _regras(pacote)
    todas = _linhas(pagina)
    uteis = sorted((l for l in todas if l.camada != "rodape" and _util(l, regras)),
                   key=lambda l: (_topo(l), l.caixa[0]))
    cabecalho = [l for l in uteis if l.camada == "cabecalho"]
    topo = [l for l in uteis if l.camada == "corpo" and _topo(l) < ZONA_CABECALHO]
    timbre = [l for l in topo if chave_texto(l.texto) in repetidas]
    # Linha com cara de timbre perde para qualquer outra linha de título: o
    # layout marca o nome da instituição no topo como seção do corpo.
    candidatos = sorted((l for l in todas if l.camada == "corpo" and l.tipo in _TITULOS
                         and _topo(l) < ZONA_TITULO and _pode_ser_titulo(l, regras)),
                        key=lambda l: (_cara_de_timbre(l, regras, todas), l.tipo != "titulo",
                                       _topo(l), l.caixa[0]))
    # Linha repetida no topo é timbre, a menos que seja o único título da
    # página: o título de exames seriados também se repete, e tirá-lo juntaria
    # dois exames diferentes.
    sem_timbre = [l for l in candidatos if l not in timbre]
    titulo_ = (sem_timbre or candidatos or [None])[0]
    if cabecalho:
        return titulo_, cabecalho[0]
    timbre = [l for l in timbre if l is not titulo_ and len(l.texto.strip()) <= MAX_INSTITUICAO]
    if timbre:
        return titulo_, timbre[0]
    if titulo_ is None:
        return None, None
    # Sem cabeçalho nem repetição: o timbre é o que está acima do título.
    acima = [l for l in topo if l.tipo != "titulo" and _topo(l) < _topo(titulo_)
             and len(l.texto.strip()) <= MAX_INSTITUICAO]
    return titulo_, (acima[0] if acima else None)


def titulo(pagina: Pagina, pacote: Pacote) -> str:
    """A linha de título do topo da página, sem cara de timbre ("" se não houver)."""
    linha, _ = _titulo_e_instituicao(pagina, pacote, frozenset())
    return linha.texto.strip() if linha else ""


def instituicao(pagina: Pagina, pacote: Pacote) -> str | None:
    """A linha mais alta do cabeçalho que não seja rótulo de campo."""
    _, linha = _titulo_e_instituicao(pagina, pacote, frozenset())
    return linha.texto.strip() if linha else None


def _repetidas_no_topo(paginas: Sequence[Pagina], pacote: Pacote) -> frozenset[str]:
    regras = _regras(pacote)
    contagem: Counter[str] = Counter()
    for p in paginas:
        contagem.update({chave_texto(l.texto) for l in _linhas(p)
                         if l.camada == "corpo" and _topo(l) < ZONA_CABECALHO and _util(l, regras)})
    return frozenset(k for k, n in contagem.items() if n >= 2 and k)


def _parecidos(a: str | None, b: str | None) -> bool:
    ka, kb = chave_texto(a or ""), chave_texto(b or "")
    if not ka or not kb:
        return ka == kb
    return _DIGITOS.findall(ka) == _DIGITOS.findall(kb) and fuzz.ratio(ka, kb) >= SEMELHANCA


@dataclass
class _Grupo:
    numerado: bool
    y: int | None
    inst: str | None
    titulo: str
    paginas: list[_Perfil] = field(default_factory=list)
    nomes: set[str] = field(default_factory=set)

    def acrescentar(self, perfil: _Perfil, titulo_: str) -> None:
        self.paginas.append(perfil)
        if perfil.nome:
            self.nomes.add(perfil.nome)
        if not self.titulo and titulo_:
            self.titulo = titulo_


def _leitura(perfil: _Perfil, grupo: _Grupo) -> tuple[str | None, str]:
    """Instituição e título da página vistos contra um grupo.

    Numa página de continuação sem título, o timbre posto no corpo é a única
    linha de título do topo; se ele é a instituição do grupo, é timbre.
    """
    if perfil.inst is None and perfil.titulo and grupo.inst and _parecidos(perfil.titulo, grupo.inst):
        return grupo.inst, ""
    return perfil.inst, perfil.titulo


def _nome_cabe(perfil: _Perfil, grupo: _Grupo) -> bool:
    return perfil.nome is None or not grupo.nomes or perfil.nome in grupo.nomes


def _cabe_numerada(perfil: _Perfil, grupo: _Grupo, estrito: bool = True) -> bool:
    """Mesmo total, mesma instituição e título compatível.

    Fora do modo estrito, o título não conta: a página de continuação que abre
    com uma seção própria ("Conduta", "IMPRESSÃO", que o layout marca como
    título) cabe no grupo que já tem a página anterior a ela.
    """
    x, y = perfil.num
    if (not grupo.numerado or grupo.y != y or not _nome_cabe(perfil, grupo)
            or any(q.num[0] == x for q in grupo.paginas)):
        return False
    inst, titulo_ = _leitura(perfil, grupo)
    if (inst is None) != (grupo.inst is None):
        return False
    if inst is not None and not _parecidos(inst, grupo.inst):
        return False
    if estrito:
        return not titulo_ or not grupo.titulo or _parecidos(titulo_, grupo.titulo)
    return x > 1 and any(q.num[0] == x - 1 for q in grupo.paginas)


def _colada(perfil: _Perfil, grupo: _Grupo) -> bool:
    """A página anterior a `perfil` no grupo está ao lado dela no scan, num
    sentido ou no outro da pilha."""
    return any(q.num[0] == perfil.num[0] - 1 and abs(q.pos - perfil.pos) == 1 for q in grupo.paginas)


@dataclass
class _Pendente:
    """Grupo que ainda pode entrar em outro: pelo título (`estrito`), quando a
    página cabia em mais de um grupo sem como escolher ou só a numeração a
    punha no único grupo, longe da página anterior, ou como página que abre
    com seção própria. `candidatos` são os grupos que podiam recebê-lo na
    primeira vez que ele foi avaliado, contadas as páginas que ficaram fora
    da primeira passada e também o recebiam."""
    grupo: _Grupo
    estrito: bool
    candidatos: list[_Grupo] | None = None


def _continua(grupo: _Grupo, outro: _Grupo, estrito: bool) -> bool:
    """`grupo` é continuação de `outro`: pelo título (`estrito`) ou, fora do
    modo estrito, como página que abre com seção própria."""
    if grupo is outro or (grupo.nomes and outro.nomes and grupo.nomes != outro.nomes):
        return False
    if {q.num[0] for q in grupo.paginas} & {q.num[0] for q in outro.paginas}:
        return False
    return _cabe_numerada(grupo.paginas[0], outro, estrito)


def _abre_outro_documento(grupo: _Grupo, destino: _Grupo, perfis: Sequence[_Perfil],
                          pacote: Pacote) -> bool:
    """Alguma página de `grupo` tem título de documento, e não de seção: casa
    com as regras de categoria do pacote, ou é parecido com o título de uma
    página de fora do grupo (o documento repete o título nas páginas)."""
    proprias = {id(q.pagina) for q in grupo.paginas}
    outros = [q.titulo for q in perfis if id(q.pagina) not in proprias and q.titulo]
    for q in grupo.paginas:
        titulo_ = _leitura(q, destino)[1]
        if titulo_ and (e_titulo_de_documento(titulo_, pacote)
                        or any(_parecidos(titulo_, t) for t in outros)):
            return True
    return False


def _atual(grupo: _Grupo, unido: Mapping[int, _Grupo]) -> _Grupo:
    """O grupo em que `grupo` está agora, depois das junções."""
    while id(grupo) in unido:
        grupo = unido[id(grupo)]
    return grupo


def _pilha_invertida(paginas: Sequence[_Perfil]) -> bool:
    """A página seguinte vem logo antes da anterior no scan. A inversão
    longe, num documento, não diz em que sentido os outros documentos do
    arquivo foram escaneados."""
    return any(q.num[0] == r.num[0] + 1 and q.pos == r.pos - 1 for q in paginas for r in paginas)


def _invertida(paginas: Sequence[_Perfil]) -> bool:
    """Uma página seguinte vem antes de uma anterior no scan, colada ou não."""
    return any(q.num[0] > r.num[0] and q.pos < r.pos for q in paginas for r in paginas)


def _outro_paciente_pode_ser_dono(abre: _Perfil, grupos: Sequence[_Grupo],
                                  perfis: Sequence[_Perfil]) -> bool:
    """Há no arquivo, fora de `grupos`, página de outro paciente que pode ser
    do mesmo documento que `abre`: da mesma instituição (ou sem instituição
    lida) e sem numeração lida, sem total lido ou com o mesmo total de
    páginas."""
    proprias = {id(q.pagina) for g in grupos for q in g.paginas}
    donos = set().union(*(g.nomes for g in grupos))
    for q in perfis:
        if id(q.pagina) in proprias or q.nome is None or q.nome in donos:
            continue
        if q.inst is not None and abre.inst is not None and not _parecidos(q.inst, abre.inst):
            continue
        if q.num is None or q.num[1] in (None, abre.num[1]):
            return True
    return False


def _outro_paciente(perfil: _Perfil, grupo: _Grupo, perfis: Sequence[_Perfil], pacote: Pacote) -> bool:
    """`grupo` tem nome, e outro paciente do arquivo pode ser o dono de
    `perfil`. Variante do nome do grupo (`identidade.compativeis`) é o mesmo
    paciente."""
    if not grupo.nomes:
        return False
    outros = [q for q in perfis if q.nome is None
              or not any(identidade.compativeis(q.nome, n, pacote=pacote) for n in grupo.nomes)]
    return _outro_paciente_pode_ser_dono(perfil, (grupo,), outros)


def _titulo_de_outro_documento(perfil: _Perfil, grupo: _Grupo, perfis: Sequence[_Perfil], pacote: Pacote) -> bool:
    """Outra página do arquivo, fora de `grupo` e sem nome do paciente dele,
    repete o título de `perfil`: o mesmo exame pode estar em outro documento,
    e o título não diz de qual deles `perfil` é."""
    proprias = {id(q.pagina) for q in grupo.paginas}
    return any(q is not perfil and id(q.pagina) not in proprias and q.titulo
               and (q.nome is None or not any(identidade.compativeis(q.nome, n, pacote=pacote) for n in grupo.nomes))
               and _parecidos(q.titulo, perfil.titulo) for q in perfis)


def _vaga_de_outro_paciente(perfil: _Perfil, grupo: _Grupo, perfis: Sequence[_Perfil], pacote: Pacote) -> bool:
    """`grupo` não tem nome, e outra página do arquivo, com o mesmo número,
    sem nome ou com o nome de outro paciente, também cabe nele: a ordem do
    scan não decide de quem são as páginas sem nome do grupo."""
    return not grupo.nomes and any(
        q is not perfil and q.num and q.num[0] == perfil.num[0]
        and (q.nome is None or not identidade.compativeis(q.nome, perfil.nome, pacote=pacote))
        and _cabe_numerada(q, grupo)
        for q in perfis)


def _numero_disputado(perfil: _Perfil, grupo: _Grupo, perfis: Sequence[_Perfil]) -> bool:
    """Outra página do arquivo, com o mesmo número, também cabe em `grupo`."""
    return any(q is not perfil and q.num and q.num[0] == perfil.num[0] and _cabe_numerada(q, grupo)
               for q in perfis)


def _vaga_disputada_pelo_titulo(grupo: _Grupo, destino: _Grupo, perfis: Sequence[_Perfil]) -> bool:
    """Páginas sem numeração lida, fora dos dois grupos e sem nome de outro
    paciente, repetem o título do destino: podem ser as páginas que faltam a
    ele, e as vagas que sobram não bastam para elas e para `grupo`."""
    if not destino.titulo:
        return False
    proprias = {id(q.pagina) for q in destino.paginas} | {id(q.pagina) for q in grupo.paginas}
    repetem = sum(1 for q in perfis if q.num is None and id(q.pagina) not in proprias and q.titulo
                  and (q.nome is None or q.nome in destino.nomes) and _parecidos(q.titulo, destino.titulo))
    if not repetem:
        return False
    return destino.y is None or repetem + len(grupo.paginas) > destino.y - len(destino.paginas)


def _secao_propria_cabe(abre: _Perfil, grupo: _Grupo, destino: _Grupo, perfis: Sequence[_Perfil],
                        pilhas: Sequence[_Grupo], larga: bool) -> bool:
    """A página que abre com seção própria só tem a vizinhança como prova, e
    a página anterior pode ser de outro documento cuja primeira página não
    está no arquivo ou não tem a numeração lida. Quando o nome não foi lido
    dos dois lados, ela só entra:

    - logo depois da página anterior no scan, ou logo antes quando a pilha
      está invertida: uma junção pelo título da mesma instituição do
      destino, sem nome de outro paciente, tem a página seguinte colada antes
      da anterior (`pilhas`), ou o próprio destino tem uma página seguinte
      antes de uma anterior;
    - num grupo que tem a página 1: duas seções soltas não formam documento;
    - sem página sem numeração e com título do outro lado dela, quando a
      junção é na ordem invertida ou alguma junção pelo título do arquivo tem
      uma página seguinte antes de uma anterior, mesmo longe (`larga`): essa
      página pode ser a primeira do documento dela. Na ordem natural, sem
      inversão no arquivo, a página seguinte é o próximo documento;
    - sem disputa pela vaga com páginas sem numeração que repetem o título do
      destino (`_vaga_disputada_pelo_titulo`);
    - sem página de outro paciente que possa ser do documento dela.

    Com o nome lido nos dois grupos (o mesmo, ou `_continua` os separa), o
    paciente já está decidido."""
    if grupo.nomes and destino.nomes:
        return True
    proprio = _invertida(destino.paginas)
    invertida = proprio or any(_parecidos(g.inst, destino.inst) and g.nomes <= destino.nomes for g in pilhas)
    x, pos = abre.num[0], abre.pos
    anteriores = {q.pos for q in destino.paginas if q.num[0] == x - 1}
    natural = pos - 1 in anteriores
    if not natural and not (invertida and pos + 1 in anteriores):
        return False
    if not any(q.num[0] == 1 for q in destino.paginas):
        return False
    if larga or proprio or not natural:
        outro_lado = pos + 1 if natural else pos - 1
        if any(q.pos == outro_lado and q.num is None and q.titulo for q in perfis):
            return False
    if _vaga_disputada_pelo_titulo(grupo, destino, perfis):
        return False
    return not _outro_paciente_pode_ser_dono(abre, (grupo, destino), perfis)


def _destino(pendente: _Pendente, grupos: Sequence[_Grupo], unido: Mapping[int, _Grupo],
             perfis: Sequence[_Perfil], pacote: Pacote, pilhas: Sequence[_Grupo],
             larga: bool) -> _Grupo | None:
    """O grupo que recebe o grupo pendente inteiro, ou None.

    Na primeira avaliação, a escolha é a da primeira passada. Depois, a dúvida
    só se desfaz quando os candidatos de antes viraram um grupo só: candidato
    que deixou de caber (recebeu a página do mesmo número) não prova que a
    página é do outro, que pode ser de um documento cuja página 1 não tem a
    numeração lida. A página anterior tem de estar colada no scan: o grupo em
    que os candidatos se juntaram pode ser de outro paciente. Como seção
    própria, o título da página também não pode ser título de documento, e
    valem as condições de `_secao_propria_cabe`."""
    grupo, estrito = pendente.grupo, pendente.estrito
    # O nome lido em qualquer página do grupo vale na escolha.
    abre = replace(grupo.paginas[0], nome=next(iter(grupo.nomes), None))
    if pendente.candidatos is None:
        pendente.candidatos = [g for g in grupos if _continua(grupo, g, estrito)]
        destino = _escolher(abre, pendente.candidatos) if pendente.candidatos else None
    else:
        atuais = {id(a): a for a in (_atual(g, unido) for g in pendente.candidatos)}
        destino = next(iter(atuais.values())) if len(atuais) == 1 else None
        if destino is not None and not _continua(grupo, destino, estrito):
            destino = None
    if destino is None or not _colada(abre, destino):
        return None
    if not estrito and (_abre_outro_documento(grupo, destino, perfis, pacote)
                        or not _secao_propria_cabe(abre, grupo, destino, perfis, pilhas, larga)):
        return None
    return destino


def _disputada(pendente: _Pendente, destino: _Grupo, pendentes: Sequence[_Pendente]) -> bool:
    """Outro grupo pendente com uma página do mesmo número também cabe em
    `destino` e está colado a ele: a posição no scan não decide de quem é a
    vaga."""
    numeros = {q.num[0] for q in pendente.grupo.paginas}
    return any(outro is not pendente and numeros & {q.num[0] for q in outro.grupo.paginas}
               and _continua(outro.grupo, destino, outro.estrito)
               and _colada(outro.grupo.paginas[0], destino)
               for outro in pendentes)


def _cabe_em_sequencia(perfil: _Perfil, grupo: _Grupo) -> bool:
    if grupo.numerado or not _nome_cabe(perfil, grupo):
        return False
    inst, titulo_ = _leitura(perfil, grupo)
    return inst is not None and grupo.inst is not None and _parecidos(inst, grupo.inst) and not titulo_


def _escolher(perfil: _Perfil, candidatos: list[_Grupo]) -> _Grupo | None:
    """Entre grupos compatíveis, o vizinho no scan; na dúvida, nenhum.

    A vizinhança é palpite: só decide entre grupos que levariam a página ao
    mesmo paciente. Se os candidatos têm nomes diferentes (ou um tem nome e o
    outro não), ou se a página tem nome e nenhum candidato tem, a escolha
    poderia pôr a página no documento de outro paciente, e ela fica sozinha.
    """
    if len(candidatos) == 1:
        return candidatos[0]
    nomes = {frozenset(g.nomes) for g in candidatos}
    if len(nomes) > 1 or (perfil.nome is not None and not next(iter(nomes))):
        return None
    x = perfil.num[0]
    criterios = (
        lambda q: q.num[0] == x - 1 and q.pos == perfil.pos - 1,   # ordem natural
        lambda q: q.num[0] == x - 1 and q.pos == perfil.pos + 1,   # pilha invertida
        lambda q: abs(q.pos - perfil.pos) == 1,
    )
    for criterio in criterios:
        perto = [g for g in candidatos if any(criterio(q) for q in g.paginas)]
        if len(perto) == 1:
            return perto[0]
    return None


def segmentar(paginas: Sequence[Pagina], pacote: Pacote,
              nomes_por_pagina: Mapping[int, str] | None = None) -> list[Documento]:
    """Agrupa as páginas em documentos.

    `nomes_por_pagina` leva o índice da página ao nome lido nela; nome
    diferente sempre separa. Devolve os documentos na ordem da primeira página
    de cada um no scan, com `titulo` e `instituicao` preenchidos.
    """
    nomes = nomes_por_pagina or {}
    validas = [p for p in paginas if not p.em_branco]
    repetidas = _repetidas_no_topo(validas, pacote)
    perfis = []
    for pos, p in enumerate(validas):
        linha_titulo, linha_inst = _titulo_e_instituicao(p, pacote, repetidas)
        nome = nomes.get(p.indice)
        perfis.append(_Perfil(p, pos, numeracao(p, pacote),
                              linha_titulo.texto.strip() if linha_titulo else "",
                              linha_inst.texto.strip() if linha_inst else None,
                              nome.strip() if isinstance(nome, str) and nome.strip() else None))

    grupos: list[_Grupo] = []
    # Grupos abertos por página X > 1 sem nenhum grupo de título compatível, e
    # por página que cabia em mais de um grupo, sem como escolher, ou só num
    # grupo fechado.
    pendentes: list[_Pendente] = []
    # Página sem nome lido que cabe num único grupo possível, com nome, só pela
    # numeração (ela ou o grupo sem título) ou por um título que outra página
    # do arquivo, sem o nome do grupo, também tem, quando outro paciente do
    # arquivo pode ser o dono dela, fica fora da primeira passada, num grupo só
    # dela: longe da página anterior, ou disputando o número com outra página
    # que também cabe no grupo. Na disputa, a vaga fica reservada, e outra
    # página sem nome lido também não a toma pelo título. Fora da passada, ela
    # não recebe páginas de outro documento, mas pesa na escolha das seguintes
    # como o grupo que seria: a disputada sempre, as outras quando a página tem
    # nome e nenhum candidato tem. Sem disputa, ela ainda entra se ficar
    # colada depois.
    fora: list[tuple[_Grupo, list[_Grupo] | None]] = []
    reservadas: set[tuple[int, int]] = set()
    # Grupo sem nome em que cabem páginas com nomes de pacientes diferentes
    # pelo mesmo número (`_vaga_de_outro_paciente`): de quem são as páginas
    # dele, nem a ordem do scan nem a vizinhança decidem. Ele não recebe mais
    # páginas, mas continua candidato, para que a dúvida apareça. A página que
    # cabia nele fica num grupo também fechado: ela disputa a mesma vaga.
    fechados: set[int] = set()
    # Numeradas: por X crescente, para que a página 1 de cada documento semeie
    # o grupo antes que as seguintes procurem onde entrar.
    for perfil in sorted((q for q in perfis if q.num), key=lambda q: (q.num[0], q.pos)):
        candidatos = [g for g in grupos if _cabe_numerada(perfil, g)]
        # O grupo fechado não recebe a página, mas a dúvida que ele cria não
        # dispensa a prova pedida pelo único grupo aberto.
        abertos = [g for g in candidatos if id(g) not in fechados]
        adiar, recebe = False, None
        if perfil.nome is None and any((id(g), perfil.num[0]) in reservadas for g in candidatos):
            adiar = True
        elif (len(abertos) == 1 and perfil.nome is None
              and (not perfil.titulo or not abertos[0].titulo
                   or _titulo_de_outro_documento(perfil, abertos[0], perfis, pacote))
              and _outro_paciente(perfil, abertos[0], perfis, pacote)):
            if _numero_disputado(perfil, abertos[0], perfis):
                reservadas.add((id(abertos[0]), perfil.num[0]))
                adiar = True
            elif not _colada(perfil, abertos[0]):
                adiar, recebe = True, candidatos
        if adiar:
            grupo = _Grupo(True, perfil.num[1], perfil.inst, perfil.titulo)
            grupo.acrescentar(perfil, _leitura(perfil, grupo)[1])
            fora.append((grupo, recebe))
            continue
        sem_nome = perfil.nome is not None and not any(g.nomes for g in candidatos)
        sombras = [g for g, cands in fora if (cands is None or sem_nome) and _cabe_numerada(perfil, g)]
        grupo = _escolher(perfil, candidatos + sombras) if candidatos else None
        sombra = any(grupo is g for g in sombras)
        if (grupo is not None and not sombra and perfil.nome is not None
                and _vaga_de_outro_paciente(perfil, grupo, perfis, pacote)):
            fechados.add(id(grupo))
        if grupo is None or sombra or id(grupo) in fechados:
            grupo = _Grupo(True, perfil.num[1], perfil.inst, perfil.titulo)
            grupos.append(grupo)
            if any(id(g) in fechados for g in candidatos):
                fechados.add(id(grupo))
            if candidatos:
                pendentes.append(_Pendente(grupo, True, candidatos + sombras))
            elif perfil.num[0] > 1:
                pendentes.append(_Pendente(grupo, False))
        grupo.acrescentar(perfil, _leitura(perfil, grupo)[1])
    for grupo, candidatos in fora:
        grupos.append(grupo)
        if candidatos:
            pendentes.append(_Pendente(grupo, True, candidatos))

    # A continuação que abre com seção própria só entra depois que o título
    # decidiu todas as páginas: senão a página X de outro documento, vinda
    # antes no scan, toma a vaga da página X verdadeira. A escolha olha todos
    # os grupos possíveis, para que a dúvida entre dois pacientes apareça.
    # Uma junção pode fazer de dois candidatos de uma página pendente um grupo
    # só, e as voltas se repetem até nada mudar.
    unido: dict[int, _Grupo] = {}
    pilhas = [g for g in grupos if _pilha_invertida(g.paginas)]
    larga = any(_invertida(g.paginas) for g in grupos)
    mudou = True
    while mudou:
        mudou = False
        for pendente in list(pendentes):
            destino = _destino(pendente, grupos, unido, perfis, pacote, pilhas, larga)
            if destino is None or id(destino) in fechados or _disputada(pendente, destino, pendentes):
                continue
            for q in pendente.grupo.paginas:
                destino.acrescentar(q, _leitura(q, destino)[1])
            grupos.remove(pendente.grupo)
            unido[id(pendente.grupo)] = destino
            pendentes.remove(pendente)
            mudou = True

    por_posicao = {q.pos: g for g in grupos for q in g.paginas}
    for perfil in perfis:
        if perfil.num:
            continue
        anterior = por_posicao.get(perfil.pos - 1)
        if anterior is not None and _cabe_em_sequencia(perfil, anterior):
            grupo = anterior
        else:
            grupo = _Grupo(False, None, perfil.inst, perfil.titulo)
            grupos.append(grupo)
        grupo.acrescentar(perfil, _leitura(perfil, grupo)[1])
        por_posicao[perfil.pos] = grupo

    grupos.sort(key=lambda g: min(q.pos for q in g.paginas))
    documentos = []
    for n, grupo in enumerate(grupos, start=1):
        ordem = sorted(grupo.paginas, key=lambda q: ((q.num[0] if q.num else 0), q.pos))
        documentos.append(Documento(
            id=f"{ordem[0].pagina.arquivo_sha256[:12]}-{n:02d}",
            paginas=tuple(q.pagina for q in ordem),
            titulo=grupo.titulo,
            instituicao=grupo.inst,
        ))
    return documentos
