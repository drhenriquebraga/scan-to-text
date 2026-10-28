"""Data do exame: formatos, papéis dos rótulos, exclusões e plausibilidade.

Tudo o que é idioma vem da seção `datas` do pacote. A ordem de dia, mês e ano
está nos grupos nomeados dos padrões do pacote; o código nunca a inverte nem a
adivinha. Não se usa `dateparser` (troca dia e mês em texto corrido e aceita
data relativa).

A escolha:
1. cada data achada ganha o papel do rótulo mais perto antes dela, na mesma
   linha (ou célula), na linha de cima ou no cabeçalho da coluna;
2. rótulo de exclusão (nascimento, impressão, validade, retorno, DUM,
   comparação) em qualquer ponto entre a data anterior da linha e a data, e
   seção de comparação, tiram a data; no corpo, a exclusão sem data no fim da
   linha passa para a primeira data da linha seguinte do mesmo parágrafo;
3. data sem rótulo não conta, salvo a linha da carta ("<Cidade>, <data>");
4. fica a de papel mais forte, depois a mais precisa, depois a primeira na
   ordem das páginas; a data só com ano perde para qualquer data com dia ou
   mês, e entre linhas de carta vence a última.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from scan_to_text.pacote import Pacote
from scan_to_text.tipos import DataExame, Documento, Linha

PRECISOES = ("dia", "mes", "ano")
TIPOS_DE_TITULO = ("titulo", "secao")
EXCLUIDO = "excluido"
CARTA = "carta"
_ALFANUMERICO = re.compile(r"[^\W_]")
_NUNCA = "(?!)"


@lru_cache(maxsize=8192)
def _dobrar_letra(c: str) -> str:
    decomposto = unicodedata.normalize("NFD", c)
    if len(decomposto) > 1 and all(unicodedata.combining(x) for x in decomposto[1:]):
        c = decomposto[0]
    minuscula = c.lower()
    return minuscula if len(minuscula) == 1 else c


def dobrar(texto: str) -> str:
    """Minúsculas e sem acento, com o mesmo comprimento do original.

    Uma posição achada no texto dobrado vale no original. Só acentos
    combinantes saem: escrita sem acento (hangul, por exemplo) fica igual.
    """
    return "".join(_dobrar_letra(c) for c in texto)


def _chave(termo: object) -> str:
    return " ".join(dobrar(str(termo)).split())


def _alternativa(termos: Iterable[object]) -> str:
    """Alternância de regex, do termo mais longo ao mais curto; espaço vira `\\s+`."""
    chaves = sorted({_chave(t) for t in termos if _chave(t)}, key=lambda t: (-len(t), t))
    if not chaves:
        return _NUNCA
    return "|".join(r"\s+".join(re.escape(parte) for parte in t.split()) for t in chaves)


@dataclass(frozen=True)
class _Regras:
    formatos: tuple[tuple[re.Pattern[str], str], ...]
    meses: dict[str, int]
    pivo: int
    rotulos: re.Pattern[str]
    papel_de: dict[str, str]
    prioridade: dict[str, int]
    conectores: re.Pattern[str]
    carta: re.Pattern[str]
    comparacao: re.Pattern[str]
    fim_de_secao: re.Pattern[str]


def _compilar(secao: dict) -> _Regras:
    meses = {_chave(nome): int(numero) for nome, numero in (secao.get("meses") or {}).items()}
    lista_de_meses = _alternativa(meses)
    formatos = tuple((re.compile(f["regex"].replace("{meses}", lista_de_meses)), f["precisao"])
                     for f in secao["formatos"])
    papel_de: dict[str, str] = {}
    for papel, rotulos in secao["papeis"].items():
        papel_de.update({_chave(r): papel for r in rotulos})
    # rótulo repetido nas duas listas: a exclusão vence
    for rotulos in (secao.get("excluidos") or {}).values():
        papel_de.update({_chave(r): EXCLUIDO for r in rotulos})
    return _Regras(
        formatos=formatos,
        meses=meses,
        pivo=int(secao["ano_curto_pivo"]),
        # lookahead: acha rótulos sobrepostos ("data da coleta" e "coleta")
        rotulos=re.compile(r"(?=(?<!\w)(" + _alternativa(papel_de) + r")(?!\w))"),
        papel_de=papel_de,
        prioridade={papel: i for i, papel in enumerate(secao["prioridade"])},
        conectores=re.compile(r"(?<!\w)(?:" + _alternativa(secao.get("conectores") or ()) + r")(?!\w)"),
        carta=re.compile(secao.get("carta") or _NUNCA),
        comparacao=re.compile(r"^[\W\d_]*(?:"
                              + _alternativa(secao.get("secoes_de_comparacao") or ()) + r")(?!\w)"),
        fim_de_secao=re.compile(r"^[\W\d_]*(?:" + _alternativa(secao.get("fim_de_secao") or ())
                                + r")\s*(?::|$)"),
    )


def _valor(achado: re.Match[str], regras: _Regras) -> date | None:
    grupos = achado.groupdict()
    ano = int(grupos["a"])
    if len(grupos["a"]) == 2:
        ano += 2000 if ano < regras.pivo else 1900
    if grupos.get("mes"):
        mes = regras.meses.get(_chave(grupos["mes"]))
    else:
        mes = int(grupos["m"]) if grupos.get("m") else 1
    dia = int(grupos["d"]) if grupos.get("d") else 1
    if mes is None:
        return None
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _achar(dobrado: str, regras: _Regras) -> list[tuple[date, str, int, int]]:
    achadas, ocupados = [], []
    for padrao, precisao in regras.formatos:
        for achado in padrao.finditer(dobrado):
            inicio, fim = achado.span()
            if any(inicio < f and i < fim for i, f in ocupados):
                continue
            # trecho inválido (31/02) também ocupa: um pedaço dele não vira outra data
            ocupados.append((inicio, fim))
            valor = _valor(achado, regras)
            if valor is not None:
                achadas.append((valor, precisao, inicio, fim))
    return sorted(achadas, key=lambda a: a[2])


def datas_no_texto(texto: str, pacote: Pacote) -> list[tuple[date, str, int, int]]:
    """(valor, precisão, início, fim) de cada data de `texto`, na ordem do texto.

    Precisão "mes" e "ano" dão o primeiro dia do período. As posições valem no
    texto original.
    """
    return _achar(dobrar(texto), _compilar(pacote["datas"]))


@dataclass(frozen=True)
class _Unidade:
    """Texto onde se procura data e rótulo: uma linha ou uma célula de tabela.

    Na célula, `texto` é a célula da esquerda, a barra e a célula; só contam as
    datas a partir de `inicio`. `acima` é a linha anterior ou a célula de cima.
    `paragrafo` é (página, grupo) da linha do corpo que tem grupo.
    """
    texto: str
    inicio: int
    acima: str | None
    corpo: bool
    tipo: str
    linha: bool
    paragrafo: tuple[int, int] | None = None


def _unidades(documento: Documento) -> Iterator[_Unidade]:
    for n, pagina in enumerate(documento.paginas):
        anterior = None
        for bloco in pagina.blocos:
            if isinstance(bloco, Linha):
                corpo = bloco.camada == "corpo"
                paragrafo = (n, bloco.grupo) if corpo and bloco.grupo >= 0 else None
                yield _Unidade(bloco.texto, 0, anterior, corpo, bloco.tipo, True, paragrafo)
                anterior = bloco.texto
                continue
            for r, celulas in enumerate(bloco.celulas):
                for c, celula in enumerate(celulas):
                    esquerda = celulas[c - 1] + " | " if c else ""
                    acima = next((bloco.celulas[k][c] for k in range(r - 1, -1, -1)
                                  if c < len(bloco.celulas[k]) and bloco.celulas[k][c].strip()),
                                 None)
                    yield _Unidade(esquerda + celula, len(esquerda), acima, True, "texto", False)
            anterior = None


def _rotulos(dobrado: str, inicio: int, fim: int, regras: _Regras) -> list[tuple[str, int, int]]:
    """(papel, início, fim) de cada rótulo entre `inicio` e `fim`, sobrepostos inclusive."""
    return [(regras.papel_de[_chave(achado.group(1))], *achado.span(1))
            for achado in regras.rotulos.finditer(dobrado, inicio, fim)]


def _ultimo_rotulo(rotulos: list[tuple[str, int, int]]) -> tuple[str, int, int] | None:
    """O rótulo que termina mais perto do fim; no empate, o mais longo."""
    return max(rotulos, key=lambda r: (r[2], r[2] - r[1]), default=None)


def _tem_exclusao(rotulos: list[tuple[str, int, int]]) -> bool:
    return any(papel == EXCLUIDO for papel, _, _ in rotulos)


def _liga(entre: str, regras: _Regras) -> bool:
    return not _ALFANUMERICO.search(regras.conectores.sub(" ", entre))


def _trecho(texto: str) -> str:
    return " ".join(texto.split())


def _papel(u: _Unidade, dobrado: str, fim_anterior: int, inicio: int, fim: int,
           regras: _Regras, paciente: str) -> tuple[str | None, str | None]:
    rotulos = _rotulos(dobrado, fim_anterior, inicio, regras)
    # a exclusão vale mesmo longe da data: em "comparado com a TC realizada em
    # <data>" o rótulo mais perto é o de realização
    if _tem_exclusao(rotulos):
        return EXCLUIDO, None
    rotulo = _ultimo_rotulo(rotulos)
    if rotulo is not None:
        papel, r_inicio, r_fim = rotulo
        if _liga(dobrado[r_fim:inicio], regras):
            return papel, _trecho(u.texto[r_inicio:fim])
    # rótulo na linha de cima ou no cabeçalho da coluna: só se a data abre a
    # linha (ou a célula) e a de cima é só o rótulo
    inicio_da_celula = max(fim_anterior, u.inicio)
    if (u.acima and fim_anterior <= u.inicio
            and not _ALFANUMERICO.search(dobrado[inicio_da_celula:inicio])):
        acima = dobrar(u.acima)
        rotulo = _ultimo_rotulo(_rotulos(acima, 0, len(acima), regras))
        if (rotulo is not None and not _ALFANUMERICO.search(acima[:rotulo[1]])
                and _liga(acima[rotulo[2]:], regras)):
            if rotulo[0] == EXCLUIDO:
                return EXCLUIDO, None
            return rotulo[0], _trecho(u.acima + " " + u.texto[inicio:fim])
    # linha da carta; "<nome do paciente>, <data>" tem a mesma forma, e a data
    # dela é o nascimento
    if (u.linha and fim_anterior == 0 and regras.carta.match(dobrado[:inicio])
            and not _ALFANUMERICO.search(dobrado[fim:])
            and _chave(dobrado[:inicio].rstrip().rstrip(",")) != paciente):
        return CARTA, _trecho(u.texto[:fim])
    return None, None


def _candidatas(documento: Documento,
                regras: _Regras) -> Iterator[tuple[date, str, str | None, str | None]]:
    em_comparacao = False
    # parágrafo cuja última linha terminou com uma exclusão ainda sem data
    pendente = None
    paciente = _chave(documento.paciente or "")
    for u in _unidades(documento):
        dobrado = dobrar(u.texto)
        if u.corpo and u.linha:
            if regras.comparacao.match(dobrado):
                em_comparacao = True
            elif u.tipo in TIPOS_DE_TITULO or regras.fim_de_secao.match(dobrado):
                em_comparacao = False
        herdada = u.paragrafo is not None and u.paragrafo == pendente
        fim_anterior = 0
        for valor, precisao, inicio, fim in _achar(dobrado, regras):
            if inicio >= u.inicio:
                if (u.corpo and em_comparacao) or herdada:
                    yield valor, precisao, EXCLUIDO, None
                else:
                    yield valor, precisao, *_papel(u, dobrado, fim_anterior, inicio, fim, regras,
                                                   paciente)
            herdada = False
            fim_anterior = fim
        if herdada or _tem_exclusao(_rotulos(dobrado, fim_anterior, len(dobrado), regras)):
            pendente = u.paragrafo
        else:
            pendente = None


def _plausivel(valor: date, hoje: date, nascimento: date | None) -> bool:
    # `valor` é o primeiro dia do período (dia, mês ou ano): o período não pode
    # começar depois de hoje, nem no nascimento ou antes dele
    if valor.year < 1900 or valor > hoje:
        return False
    return nascimento is None or valor > nascimento


def data_do_exame(documento: Documento, pacote: Pacote, hoje: date,
                  nascimento: date | None = None) -> DataExame:
    """A data do exame do documento, ou `DataExame()` quando não há data plausível.

    Lê todas as camadas de todas as páginas (o rótulo da coleta costuma estar
    no cabeçalho, e a data de impressão, no rodapé). `hoje` é a data do
    processamento, recebida de fora para o resultado ser determinístico.
    """
    regras = _compilar(pacote["datas"])
    melhor: tuple[tuple[bool, int, int, int], DataExame] | None = None
    for ordem, (valor, precisao, papel, trecho) in enumerate(_candidatas(documento, regras)):
        if papel is None or papel == EXCLUIDO or not _plausivel(valor, hoje, nascimento):
            continue
        # o ano solto vem por último: um número de 4 dígitos ("atendimento:
        # 1987") também casa com ele
        so_ano = precisao == PRECISOES[-1]
        if papel == CARTA:
            # a lista "<exame>, <data>" tem a forma da carta, que é datada no fim
            chave = (so_ano, regras.prioridade[papel], 0, -ordem)
        else:
            chave = (so_ano, regras.prioridade[papel], PRECISOES.index(precisao), ordem)
        if melhor is None or chave < melhor[0]:
            melhor = (chave, DataExame(valor, precisao, papel, trecho))
    return melhor[1] if melhor else DataExame()
