"""Limpeza: cabeçalho original e corpo de um documento.

Tira só isto:

- rodapé: o que o layout marcou como rodapé, e as linhas do pé que se repetem
  em pelo menos metade das páginas do documento, com a numeração da página
  normalizada ("Página 2 de 5"); os outros números têm de ser iguais;
- bibliografia: de um título de bibliografia seguido de linhas com cara de
  citação até o fim do documento. Se o trecho que sairia tem cara de resultado
  (número com unidade, comparação), nada sai. Em 3 LABORATORIO, "Referências"
  é rótulo de valores de referência, e só abre bibliografia seguido de cara
  forte de citação ("et al.", "doi");
- valores de referência, só em documentos de 3 LABORATORIO: a coluna rotulada,
  o bloco que começa com o rótulo (com as faixas por idade e sexo) e o trecho
  rotulado dentro de uma linha. No meio da linha, o rótulo só vale seguido de
  dois-pontos, igual, número ou comparação, ou fechando a linha antes de uma
  faixa numérica. Unidade, método, material e resultado anterior ficam.

O cabeçalho original é o que o layout marcou como cabeçalho mais as linhas do
topo que se repetem no topo de outra página do documento (o layout às vezes
põe o cabeçalho no corpo). Entra uma vez só: linhas idênticas repetidas nas
páginas seguintes saem.

Rótulos e padrões vêm de `secoes.yaml` do pacote de idioma.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Sequence

from scan_to_text.entender.segmentar import (ZONA_CABECALHO, ZONA_PE, alternativa, chave_texto,
                                             dobrar, e_numeracao, sem_numeracao)
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Bloco, Documento, Linha, Pagina, Tabela, texto

CATEGORIA_LABORATORIO = 3
LINHAS_PARA_CITACAO = 3
# Separadores que introduzem a referência e ficariam soltos no fim do recorte.
_SEPARADORES = " \t-|;,"
_DIGITOS = re.compile(r"\d+")
# O que, logo depois de um rótulo de referência, mostra que vem o valor dele.
_VALOR_DEPOIS = re.compile(r"\s*(?:[:=]|-?\s*[\d<>≤≥])")
_SO_PONTUACAO = re.compile(r"[\W_]*")


@dataclass(frozen=True)
class _Regras:
    titulos_bib: frozenset[str]
    nunca_bib: frozenset[str]
    rotulos_vr: frozenset[str]
    ambiguos: frozenset[str]
    citacao: tuple[re.Pattern, ...]
    citacao_fraca: tuple[re.Pattern, ...]
    resultado: tuple[re.Pattern, ...]
    vr_inicio: re.Pattern | None
    vr_meio: re.Pattern | None
    fica_inicio: re.Pattern | None
    fica_meio: re.Pattern | None
    faixas: tuple[re.Pattern, ...]
    faixa_numerica: tuple[re.Pattern, ...]


@lru_cache(maxsize=32)
def _montar(titulos: tuple[str, ...], nunca: tuple[str, ...], citacao: tuple[str, ...],
            citacao_fraca: tuple[str, ...], resultado: tuple[str, ...], rotulos: tuple[str, ...],
            ambiguos: tuple[str, ...], ficam: tuple[str, ...], faixas: tuple[str, ...],
            faixa_numerica: tuple[str, ...]) -> _Regras:
    # Rótulo em fronteira de palavra. O que vem depois dele decide se é valor
    # de referência (`_rotulo`): "laboratório de referência" não é.
    fim = r"(?![^\W\d_])"
    vr = alternativa(rotulos) if rotulos else None
    fica = alternativa(ficam) if ficam else None

    def compilar(padroes: tuple[str, ...]) -> tuple[re.Pattern, ...]:
        return tuple(re.compile(p) for p in padroes)

    return _Regras(
        titulos_bib=frozenset(chave_texto(t) for t in titulos),
        nunca_bib=frozenset(chave_texto(t) for t in nunca),
        rotulos_vr=frozenset(chave_texto(t) for t in rotulos),
        ambiguos=frozenset(chave_texto(t) for t in ambiguos),
        citacao=compilar(citacao),
        citacao_fraca=compilar(citacao_fraca),
        resultado=compilar(resultado),
        vr_inicio=re.compile(r"^[\W_]*" + vr + fim) if vr else None,
        vr_meio=re.compile(r"(?<![^\W\d_])" + vr + fim) if vr else None,
        fica_inicio=re.compile(r"^[\W_]*" + fica + fim) if fica else None,
        fica_meio=re.compile(r"(?<![^\W\d_])" + fica + fim + r"(?=\s*:)") if fica else None,
        faixas=compilar(faixas),
        faixa_numerica=compilar(faixa_numerica),
    )


def _regras(pacote: Pacote) -> _Regras:
    secoes = pacote.secoes.get("secoes") or {}
    bib = secoes.get("bibliografia") or {}
    vr = secoes.get("valores_de_referencia") or {}

    def lista(d: dict, k: str) -> tuple[str, ...]:
        return tuple(str(x) for x in d.get(k) or ())

    return _montar(lista(bib, "titulos"), lista(bib, "nunca"), lista(bib, "citacao"),
                   lista(bib, "citacao_fraca"), lista(bib, "resultado"), lista(vr, "rotulos"),
                   lista(vr, "ambiguos"), lista(vr, "ficam"), lista(vr, "faixas"),
                   lista(vr, "faixa_numerica"))


# cabeçalho e rodapé -----------------------------------------------------------

def _identica(texto: str) -> str:
    return " ".join(texto.split())


def _com_numeros_normalizados(texto: str) -> str:
    return _DIGITOS.sub("#", " ".join(dobrar(texto).split()))


def _linhas_de(pagina: Pagina, filtro) -> list[tuple[int, Linha]]:
    return [(i, b) for i, b in enumerate(pagina.blocos)
            if isinstance(b, Linha) and b.camada == "corpo" and filtro(b)]


def _no_topo(linha: Linha) -> bool:
    return linha.caixa[1] < ZONA_CABECALHO


def _no_pe(linha: Linha) -> bool:
    return linha.caixa[1] >= 1 - ZONA_PE


def _pe_repetido(paginas: Sequence[Pagina], pacote: Pacote) -> frozenset[str]:
    """Linhas do pé que aparecem em pelo menos metade das páginas (e em duas).

    Só a numeração da página é normalizada: "Hemoglobina 12,4" e "Hemoglobina
    13,1" no pé de duas páginas são resultados, não rodapé.
    """
    n = len(paginas)
    contagem: Counter[str] = Counter()
    for p in paginas:
        contagem.update({sem_numeracao(l.texto, pacote) for _, l in _linhas_de(p, _no_pe)})
    return frozenset(k for k, c in contagem.items() if c >= 2 and 2 * c >= n)


def _sufixo_do_pe(pagina: Pagina, repetidas: frozenset[str], pacote: Pacote) -> set[int]:
    """Do fim da página para cima, as linhas repetidas do pé; para na primeira
    que não se repete, para não levar um resultado que caiu no pé."""
    tirar = set()
    for i, linha in sorted(_linhas_de(pagina, _no_pe), key=lambda il: (-il[1].caixa[1], -il[1].caixa[0])):
        if sem_numeracao(linha.texto, pacote) not in repetidas:
            break
        tirar.add(i)
    return tirar


def _prefixo_do_topo(pagina: Pagina, outras: set[str], pacote: Pacote) -> set[int]:
    """Do topo para baixo, as linhas de corpo que se repetem no topo de outra
    página (ou são só a numeração); para na primeira que não se repete."""
    marcar = set()
    for i, linha in sorted(_linhas_de(pagina, _no_topo), key=lambda il: (il[1].caixa[1], il[1].caixa[0])):
        if _identica(linha.texto) not in outras and not e_numeracao(linha.texto, pacote):
            break
        marcar.add(i)
    return marcar


def _sem_repeticao(linhas: list[str], pacote: Pacote) -> list[str]:
    vistas, saida = set(), []
    for t in linhas:
        k = _com_numeros_normalizados(t) if e_numeracao(t, pacote) else _identica(t)
        if k and k not in vistas:
            vistas.add(k)
            saida.append(t)
    return saida


# bibliografia -------------------------------------------------------------------

def _casa(d: str, padroes: tuple[re.Pattern, ...]) -> bool:
    return any(p.search(d) for p in padroes)


def _e_citacao(d: str, regras: _Regras) -> bool:
    """Linha dobrada com cara de citação. A cara fraca (só o ano) não vale numa
    linha com cara de resultado: é valor de referência que cita a diretriz."""
    return _casa(d, regras.citacao) or (_casa(d, regras.citacao_fraca) and not _casa(d, regras.resultado))


def _sem_bibliografia(corpo: list[Bloco], regras: _Regras, categoria: int) -> list[Bloco]:
    for k, bloco in enumerate(corpo):
        if not isinstance(bloco, Linha):
            continue
        chave = chave_texto(bloco.texto)
        if chave not in regras.titulos_bib or chave in regras.nunca_bib:
            continue
        resto = [dobrar(texto(b)) for b in corpo[k + 1:]]
        seguintes = [d for b, d in zip(corpo[k + 1:], resto) if isinstance(b, Linha) and d.strip()]
        janela = seguintes[:LINHAS_PARA_CITACAO]
        # Em 3 LABORATORIO, "Referências" é também rótulo de valores de
        # referência: só abre bibliografia com cara forte de citação ("et al.").
        if categoria == CATEGORIA_LABORATORIO and chave in regras.rotulos_vr:
            if not any(_casa(d, regras.citacao) for d in janela):
                continue
        elif not any(_e_citacao(d, regras) for d in janela):
            continue
        # A bibliografia vai até o fim do documento: se algo nesse trecho tem
        # cara de resultado, o título não abria bibliografia, e nada sai.
        if any(_casa(d, regras.resultado) and not _casa(d, regras.citacao) for d in resto):
            continue
        return corpo[:k]
    return corpo


# valores de referência ------------------------------------------------------------

def _abre_bloco(d: str, regras: _Regras) -> bool:
    """O texto dobrado começa com rótulo de referência (e não é bibliografia)."""
    if regras.vr_inicio is None or not regras.vr_inicio.match(d):
        return False
    chave = chave_texto(d)
    return not (chave in regras.titulos_bib and chave not in regras.rotulos_vr)


def _e_faixa(d: str, regras: _Regras) -> bool:
    return _casa(d, regras.faixas)


def _juntar(antes: str, depois: str) -> str:
    antes = antes.rstrip(_SEPARADORES)
    depois = depois.strip()
    return f"{antes} {depois}".strip() if antes and depois else (antes or depois).strip()


def _rotulo(d: str, regras: _Regras, antes_de_faixa: bool) -> tuple[int, int] | None:
    """Início e fim do rótulo de referência na linha dobrada, ou None.

    No começo da linha vale o rótulo sozinho ou seguido do valor; um rótulo
    sem ambiguidade ("Valores de referência para adultos:") vale também
    seguido de texto. No meio da linha, só seguido do valor, ou fechando a
    linha quando a seguinte é uma faixa numérica (`antes_de_faixa`).
    """
    if _abre_bloco(d, regras):
        fim = regras.vr_inicio.match(d).end()
        resto = d[fim:]
        if (_SO_PONTUACAO.fullmatch(resto) or _VALOR_DEPOIS.match(resto)
                or chave_texto(d[:fim]) not in regras.ambiguos):
            return 0, fim
    for achado in regras.vr_meio.finditer(d) if regras.vr_meio else ():
        resto = d[achado.end():]
        if _VALOR_DEPOIS.match(resto) or (antes_de_faixa and not resto.strip()):
            return achado.span()
    return None


def _cortar(t: str, regras: _Regras, antes_de_faixa: bool = False) -> tuple[str, bool] | None:
    """Tira da linha o trecho rotulado como referência.

    Devolve (o que fica, se o bloco continua nas linhas seguintes), ou None
    quando não há rótulo. O trecho vai do rótulo até um rótulo que fica
    (método, material...), até o parêntese que o fecha, ou até o fim da linha.
    """
    d = dobrar(t)
    achado = _rotulo(d, regras, antes_de_faixa)
    if achado is None:
        return None
    ini, fim_rotulo = achado
    k = ini - 1
    while k >= 0 and d[k].isspace():
        k -= 1
    if k >= 0 and d[k] in "([":
        fecha = d.find(")" if d[k] == "(" else "]", fim_rotulo)
        if fecha >= 0:
            return _juntar(t[:k], t[fecha + 1:]), False
        ini = k
    fica = regras.fica_meio.search(d, fim_rotulo) if regras.fica_meio else None
    if fica:
        return _juntar(t[:ini], t[fica.start():]), False
    return _juntar(t[:ini], ""), True


def _limpar_trecho(t: str, regras: _Regras, antes_de_faixa: bool = False) -> tuple[str | None, bool]:
    """Aplica `_cortar` até não sobrar rótulo. (None, False) se nada mudou."""
    atual, continua, mudou = t, False, False
    for _ in range(8):
        corte = _cortar(atual, regras, antes_de_faixa)
        if corte is None:
            break
        atual, continua = corte
        mudou = True
        if not atual:
            break
    return (atual, continua) if mudou else (None, False)


def _continua_bloco(linha: Linha, regras: _Regras) -> bool:
    d = dobrar(linha.texto)
    return (linha.tipo not in ("titulo", "secao") and _e_faixa(d, regras)
            and not (regras.fica_inicio and regras.fica_inicio.match(d)))


def _tabela_sem_referencia(tabela: Tabela, regras: _Regras, apos_rotulo: bool) -> Tabela | None:
    linhas = [list(l) for l in tabela.celulas]
    confs = [list(tabela.confiancas[i]) if i < len(tabela.confiancas) else [] for i in range(len(linhas))]
    ncab = tabela.linhas_de_cabecalho
    if ncab:
        colunas = {j for i in range(ncab) for j, c in enumerate(linhas[i])
                   if _abre_bloco(dobrar(c.strip()), regras)}
    else:
        # Sem cabeçalho declarado, a célula da primeira linha que é só o rótulo
        # (sem número) nomeia a coluna, mesmo com data em outra célula. Na
        # primeira coluna o rótulo nomeia a linha, e com número é trecho.
        colunas = {j for j, c in enumerate(linhas[0] if linhas else ()) if j > 0
                   and not any(ch.isdigit() for ch in c) and _abre_bloco(dobrar(c.strip()), regras)}
    if colunas:
        linhas = [[c for j, c in enumerate(l) if j not in colunas] for l in linhas]
        confs = [[c for j, c in enumerate(l) if j not in colunas] for l in confs]
    novas, novas_confs = [], []
    ativo = apos_rotulo
    for i, (linha, conf) in enumerate(zip(linhas, confs)):
        if i >= ncab:
            primeira = dobrar(next((c for c in linha if c.strip()), "").strip())
            rotulo = _rotulo(primeira, regras, False)
            if rotulo is not None and rotulo[0] == 0:
                ativo = True
                continue
            if ativo and _e_faixa(primeira, regras):
                continue
            ativo = False
            linha = [c if (novo := _limpar_trecho(c, regras)[0]) is None else novo for c in linha]
        novas.append(tuple(linha))
        novas_confs.append(tuple(conf))
    if not novas or not any(novas):
        return None
    return Tabela(celulas=tuple(novas), confiancas=tuple(novas_confs), caixa=tabela.caixa,
                  linhas_de_cabecalho=min(ncab, len(novas)))


def _sem_referencia(corpo: list[Bloco], regras: _Regras) -> list[Bloco]:
    saida: list[Bloco] = []
    ativo = False
    for k, bloco in enumerate(corpo):
        if isinstance(bloco, Tabela):
            tabela = _tabela_sem_referencia(bloco, regras, ativo)
            if tabela is not None:
                saida.append(tabela)
            ativo = False
            continue
        if ativo:
            if _continua_bloco(bloco, regras):
                fica = regras.fica_meio.search(dobrar(bloco.texto)) if regras.fica_meio else None
                if fica:
                    saida.append(replace(bloco, texto=bloco.texto[fica.start():].strip()))
                    ativo = False
                continue
            ativo = False
        seguinte = corpo[k + 1] if k + 1 < len(corpo) else None
        antes_de_faixa = isinstance(seguinte, Linha) and _casa(dobrar(seguinte.texto), regras.faixa_numerica)
        novo, ativo = _limpar_trecho(bloco.texto, regras, antes_de_faixa)
        if novo is None:
            saida.append(bloco)
        elif novo:
            saida.append(replace(bloco, texto=novo))
    return saida


def limpar(documento: Documento, pacote: Pacote) -> Documento:
    """Preenche `cabecalho_original` e `corpo` do documento."""
    regras = _regras(pacote)
    paginas = documento.paginas
    pe = _pe_repetido(paginas, pacote)
    topos = [{_identica(l.texto) for _, l in _linhas_de(p, _no_topo)} for p in paginas]
    cabecalho: list[str] = []
    corpo: list[Bloco] = []
    for n, pagina in enumerate(paginas):
        outras = set().union(*(t for m, t in enumerate(topos) if m != n))
        do_topo = _prefixo_do_topo(pagina, outras, pacote)
        do_pe = _sufixo_do_pe(pagina, pe, pacote)
        for i, bloco in enumerate(pagina.blocos):
            if isinstance(bloco, Linha):
                if bloco.camada == "rodape" or i in do_pe:
                    continue
                if bloco.camada == "cabecalho" or i in do_topo:
                    cabecalho.append(bloco.texto)
                    continue
            corpo.append(bloco)
    corpo = _sem_bibliografia(corpo, regras, documento.categoria)
    if documento.categoria == CATEGORIA_LABORATORIO:
        corpo = _sem_referencia(corpo, regras)
    return replace(documento, cabecalho_original=tuple(_sem_repeticao(cabecalho, pacote)),
                   corpo=tuple(corpo))
