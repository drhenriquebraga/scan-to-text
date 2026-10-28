"""Categoria do documento: regras do pacote e, na dúvida, 5.

Pedido, solicitação e guia nunca são resultado: as exclusões de título e as
marcas fortes do corpo são conferidas antes de qualquer regra de exame, e as
marcas que só tiram de algumas categorias (`tira_de`), depois da decisão. A
ordem de decisão está descrita no `categorias.yaml`. O motivo devolvido é um
código ("regra:<id>"), que pode ir para o log.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from scan_to_text.entender.datas import TIPOS_DE_TITULO, dobrar
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, Linha, texto

DUVIDA = 5
_TRACOS = re.compile(r"[\-‐-―_]")


def _normalizar(t: str) -> str:
    return " ".join(_TRACOS.sub(" ", dobrar(t)).split())


def _padrao(termos: Iterable[object], abrindo_a_linha: bool = False) -> re.Pattern[str] | None:
    chaves = sorted({_normalizar(str(t)) for t in termos} - {""}, key=lambda c: (-len(c), c))
    if not chaves:
        return None
    inicio = r"(?m)^[\W\d_]*" if abrindo_a_linha else r"(?<!\w)"
    return re.compile(inicio + "(?:" + "|".join(re.escape(c) for c in chaves) + r")(?:s|es)?(?!\w)")


@dataclass(frozen=True)
class _Regra:
    id: str
    categoria: int
    termos: re.Pattern[str] | None
    todos: tuple[re.Pattern[str] | None, ...]
    abrindo: re.Pattern[str] | None
    tira_de: frozenset[int]

    def casa(self, t: str) -> bool:
        if self.termos is None and not self.todos:
            return False
        if self.termos is not None and not self.termos.search(t):
            return False
        return all(grupo is not None and grupo.search(t) for grupo in self.todos)

    def abre(self, t: str) -> bool:
        """Um dos termos abre alguma linha de `t` (depois de pontuação ou numeração)."""
        return self.abrindo is not None and self.abrindo.search(t) is not None


def _regras(itens: Iterable[dict] | None) -> tuple[_Regra, ...]:
    return tuple(_Regra(str(r["id"]), int(r.get("categoria", DUVIDA)),
                        _padrao(r.get("termos") or ()),
                        tuple(_padrao(grupo) for grupo in r.get("todos") or ()),
                        _padrao(r.get("termos") or (), abrindo_a_linha=True),
                        frozenset(int(c) for c in r.get("tira_de") or ()))
                 for r in itens or ())


def _zonas(documento: Documento) -> tuple[str, str]:
    """O título achado pela segmentação e as linhas de título da 1ª página ("" se não há).

    A linha igual à instituição fica de fora: o nome de um laboratório "de
    patologia e análises clínicas" não diz o tipo do exame.
    """
    instituicao = _normalizar(documento.instituicao or "")
    titulo = _normalizar(documento.titulo)
    if titulo == instituicao:
        titulo = ""
    linhas = []
    if documento.paginas:
        linhas = [_normalizar(b.texto) for b in documento.paginas[0].blocos
                  if isinstance(b, Linha) and b.tipo in TIPOS_DE_TITULO]
    return titulo, "\n".join(linha for linha in linhas if linha and linha != instituicao)


def _motivo(regra: _Regra) -> str:
    return f"regra:{regra.id}"


def _decidir(titulo: str, linhas: str, corpo: str, regras: tuple[_Regra, ...],
             estrutura: tuple[_Regra, ...]) -> tuple[int, str]:
    for zona in (titulo, linhas):
        for regra in regras:
            if zona and regra.casa(zona):
                return regra.categoria, _motivo(regra)
    for regra in estrutura:
        if regra.casa(corpo):
            return regra.categoria, _motivo(regra)
    return DUVIDA, "regra:duvida"


def e_titulo_de_documento(titulo: str, pacote: Pacote) -> bool:
    """O título casa com uma regra de categoria ou com uma exclusão de título do
    pacote: nomeia um documento ("TOMOGRAFIA", "RECEITUÁRIO", "PEDIDO"), e não
    uma seção dele. Pacote sem `categorias` não reconhece nenhum."""
    secao = pacote.secoes.get("categorias") or {}
    t = _normalizar(titulo)
    return bool(t) and any(regra.casa(t) for chave in ("regras", "exclusoes_titulo")
                           for regra in _regras(secao.get(chave)))


def classificar(documento: Documento, pacote: Pacote) -> tuple[int, str]:
    """(categoria de 1 a 5, motivo "regra:<id>"). Na dúvida, (5, "regra:duvida")."""
    secao = pacote["categorias"]
    titulo, linhas = _zonas(documento)
    corpo = _normalizar("\n".join(texto(b) for p in documento.paginas for b in p.blocos))
    regras = _regras(secao.get("regras"))
    titulo_decide = bool(titulo) and any(regra.casa(titulo) for regra in regras)
    for regra in _regras(secao.get("exclusoes_titulo")):
        if regra.casa(titulo) or (regra.abre(linhas) if titulo_decide else regra.casa(linhas)):
            return DUVIDA, _motivo(regra)
    exclusoes = _regras(secao.get("exclusoes_texto"))
    for regra in exclusoes:
        if not regra.tira_de and regra.casa(corpo):
            return DUVIDA, _motivo(regra)
    categoria, motivo = _decidir(titulo, linhas, corpo, regras, _regras(secao.get("estrutura")))
    for regra in exclusoes:
        if categoria in regra.tira_de and regra.casa(corpo):
            return DUVIDA, _motivo(regra)
    return categoria, motivo
