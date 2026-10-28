"""Identidade do paciente.

Nome de paciente só existe depois de rótulo explícito do pacote de regras, em
qualquer camada da página, e passa pelas rejeições descritas abaixo. Linha em
maiúsculas sem rótulo nunca é nome. O código não tem texto de idioma: rótulos,
partículas e listas de palavras vêm de `packs/<idioma>/rotulos.yaml`.

Cada valor achado depois de um rótulo de paciente vira uma `Leitura` com um
efeito:

- "aceito": nome válido; vira `Candidato` quando a confiança basta;
- "duvida": pode ser o paciente, mas não deu para aceitar (validação,
  confiança, minúsculas depois de dois-pontos, profissional depois de rótulo
  de paciente);
- "reserva": rótulo genérico junto de rótulo de outra pessoa ou de registro de
  conselho, ou rótulo de paciente cujo valor não se pôde ler.

Valor que certamente não é o paciente (profissional depois de rótulo genérico,
instituição, frase) não vira leitura. A atribuição usa dúvidas e reservas para
nunca dar a um documento o paciente de outro.

Portado do photoid-rt (commit aa53fef, Apache-2.0):
`util/StorageLocal.kt` (removerAcentosMaiusculas e chaveNome, em `chave`),
`patient/SuspectNameValidator.kt` (em `validar`) e a regra dos rótulos de campo
de `scan/EtiquetaParser.kt` (ROTULOS_DE_CAMPO), que encerram o valor do nome.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import yaml
from rapidfuzz.distance import Levenshtein

from scan_to_text.pacote import RAIZ_PACKS, Pacote
from scan_to_text.tipos import Caixa, Candidato, Linha, Pagina, Tabela

IDIOMA_PADRAO = "pt-BR"
SOCIAL = "nome_social"

# Letras do mapa da rotina VBA que o photoid-rt replica (StorageLocal.COM_ACENTOS),
# em código de ponto porque o motor não guarda literal não ASCII: A, E, I, O e U
# com agudo, grave, circunflexo, til e trema, mais Ç e Ñ; a minúscula de cada
# uma fica 0x20 adiante. Cada letra vira a letra-base.
_COM_ACENTOS = (0xC1, 0xC0, 0xC2, 0xC3, 0xC4, 0xC9, 0xC8, 0xCA, 0xCB, 0xCD, 0xCC, 0xCE,
                0xCF, 0xD3, 0xD2, 0xD4, 0xD5, 0xD6, 0xDA, 0xD9, 0xDB, 0xDC, 0xC7, 0xD1)
_MAPA = {c: unicodedata.normalize("NFD", chr(c))[0]
         for letra in _COM_ACENTOS for c in (letra, letra + 0x20)}
_ILEGAIS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ESPACOS = re.compile(r"\s+")
_DIACRITICOS = re.compile(r"[̀-ͯ]+")
_SO_LETRAS = re.compile(r"[^A-Za-z ]")
_DIGITO = re.compile(r"\d")
_PALAVRA = re.compile(r"\w")
_ALNUM = re.compile(r"[^\W_]")
_APOSTROFOS = "'" + chr(0x2019)
_PONTAS = "\"'()[]{}<>*"
_ABRE = "([{"

# Dois-pontos (com pontilhado antes) ou travessão seguido de espaço.
_SEPARADOR = re.compile(r"\s*(?:[._]{2,}\s*)?(?::|[-–—](?=\s|$))")
_VAO_ANTES = re.compile(r"(?:\s{2,}|\t|\|\s*)$")
_DIGITO_DEPOIS = re.compile(r"\s*\d")
# Vão de coluna, barra vertical e ponto e vírgula encerram o campo.
_VAO = re.compile(r"\s{2,}|\t|\||;")
# Distância horizontal máxima, em fração da largura da página, entre um rótulo
# e o valor numa caixa separada da mesma linha impressa.
_VAO_MAXIMO_A_DIREITA = 0.25
_DATA = re.compile(r"(\d{1,2})\s?([./-])\s?(\d{1,2})\s?\2\s?(\d{4}|\d{2})(?!\d)")


# ---------- chave e validação ----------

def _limpar_unicode(texto: str) -> str:
    """NFC e sem caracteres de formatação (categoria Cf), que falsificam nomes."""
    texto = unicodedata.normalize("NFC", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Cf")


def _sem_acento_maiusculas(texto: str) -> str:
    """Porte de StorageLocal.removerAcentosMaiusculas."""
    t = _ESPACOS.sub(" ", _ILEGAIS.sub(" ", texto.translate(_MAPA)))
    return t.strip().rstrip(". ").upper()


def chave(nome: str) -> str:
    """A chave de comparação do photoid-rt (StorageLocal.chaveNome).

    Antes do porte, NFC e remoção de caracteres Cf, para que a mesma grafia
    escrita de dois jeitos dê a mesma chave."""
    t = _sem_acento_maiusculas(_limpar_unicode(nome)).replace("_", " ")
    return _ESPACOS.sub(" ", t).strip()


def _normalizar_validador(nome: str) -> str:
    """Porte de SuspectNameValidator.normalizar."""
    sem = _DIACRITICOS.sub("", unicodedata.normalize("NFD", nome))
    return _ESPACOS.sub(" ", _SO_LETRAS.sub("", sem)).strip().upper()


def _validar(nome: str, comuns: frozenset[str]) -> tuple[bool, str]:
    bruto = _ESPACOS.sub(" ", nome or "").strip()
    if _DIGITO.search(bruto):
        return False, "digito"
    norm = _normalizar_validador(bruto)
    if len(norm) < 5:
        return False, "curto"
    palavras = norm.split(" ")
    if len(palavras) < 2:
        return False, "uma_palavra"
    if len(palavras) == 2:
        p1, p2 = palavras
        if (p1 in comuns and len(p2) <= 2) or (p2 in comuns and len(p1) <= 2):
            return False, "sobrenome_curto"
    if all(len(p) == 1 for p in palavras):
        return False, "so_iniciais"
    visiveis = [c for c in bruto if not c.isspace()]
    if sum(c.isalpha() for c in visiveis) / len(visiveis) < 0.7:
        return False, "poucas_letras"
    if len(bruto) > 80:
        return False, "longo"
    return True, "ok"


def validar(nome: str, *, pacote: Pacote | None = None) -> tuple[bool, str]:
    """(ok, motivo). As regras do SuspectNameValidator (curto, uma palavra,
    sobrenome truncado, só iniciais) e mais três (sem dígito, 70% letras, até
    80 caracteres). Sem pacote, os nomes comuns vêm do pacote pt-BR."""
    return _validar(nome, _regras(pacote).comuns)


# ---------- comparação ----------

def _palavras(nome: str, particulas: frozenset[str]) -> list[str]:
    k = chave(nome)
    antes, virgula, depois = k.partition(",")
    if virgula:
        k = depois + " " + antes
    for apostrofo in _APOSTROFOS:
        k = k.replace(apostrofo, "")
    k = k.replace(".", " ").replace("-", " ").replace(",", " ")
    return [p for p in k.split() if p not in particulas]


def _fronteiras(palavras: Sequence[str]) -> set[int]:
    """Onde termina cada palavra, menos a última, nas letras coladas."""
    fim, saida = 0, set()
    for palavra in palavras[:-1]:
        fim += len(palavra)
        saida.add(fim)
    return saida


def _mesma_cola(a: str, b: str, particulas: frozenset[str]) -> bool:
    """As duas formas só diferem pelo espaço que o OCR colou entre palavras.

    As fronteiras de palavra de uma estão todas na outra, e cada fronteira que
    some separa duas palavras de duas letras ou mais, sem partícula nem
    inicial: "GIL DA SILVA" e "GILDA SILVA", "DANIEL A. SILVA" e "DANIELA
    SILVA" são pessoas diferentes. A partícula colada ao começo da palavra
    seguinte vale quando essa palavra tem três letras ou mais ("MARIA
    DASILVA")."""
    pa, pb = _palavras(a, frozenset()), _palavras(b, frozenset())
    if not pa or "".join(pa) != "".join(pb):
        return False
    fina, grossa = sorted((pa, pb), key=len, reverse=True)
    ficam = _fronteiras(grossa)
    if not ficam <= _fronteiras(fina):
        return False
    fim = 0
    for antes, depois in zip(fina, fina[1:]):
        fim += len(antes)
        if fim in ficam:
            continue
        if depois in particulas:
            return False
        minimo = 3 if antes in particulas else 2
        if len(depois) < minimo or (antes not in particulas and len(antes) < 2):
            return False
    return True


def _mesmo_sobrenome(a: str, b: str, tolerancia: int) -> bool:
    if a == b:
        return True
    # Inicial nunca casa com sobrenome, e sobrenome curto não recebe tolerância.
    if min(len(a), len(b)) < 4:
        return False
    return Levenshtein.distance(a, b) <= tolerancia


def _mesma_palavra(a: str, b: str) -> bool:
    return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def compativeis(a: str, b: str, tolerancia_ocr: int = 1, *, pacote: Pacote | None = None) -> bool:
    """Variantes do mesmo nome.

    Mesmo primeiro nome, mesmo último sobrenome (com `tolerancia_ocr` de
    distância de edição) e os nomes do meio do mais curto presentes, em ordem,
    no mais longo, por inteiro ou como inicial. Partículas não contam, e
    "SOBRENOME, Nome" vira "Nome SOBRENOME". "ANA" nunca casa com "ANA MARIA".
    Duas formas que só diferem pelo espaço entre palavras inteiras são o mesmo
    nome: o OCR cola palavras vizinhas ("ODETE LUCIAVALVERDINO", ver
    `_mesma_cola`)."""
    regras = _regras(pacote)
    if _mesma_cola(a, b, regras.particulas):
        return True
    pa, pb = _palavras(a, regras.particulas), _palavras(b, regras.particulas)
    if not pa or not pb or pa[0] != pb[0]:
        return False
    if not _mesmo_sobrenome(pa[-1], pb[-1], tolerancia_ocr):
        return False
    curto, longo = sorted((pa[1:-1], pb[1:-1]), key=len)
    resto = iter(longo)
    return all(any(_mesma_palavra(p, q) for q in resto) for p in curto)


def forma_exibida(nomes: Sequence[str], *, pacote: Pacote | None = None) -> str:
    """A variante mais completa: mais palavras inteiras sem contar partículas
    (a forma com o espaço que o OCR colou vence), depois a que não é a forma
    colada de outra variante da lista ("MARIA DASILVA" empata com "MARIA DA
    SILVA" em palavras inteiras), depois a mais frequente, depois mais
    palavras contando partículas e iniciais, mais letras, a ordem natural (sem
    vírgula) e a primeira.

    "SOBRENOME, Nome" sai como "Nome SOBRENOME": a pasta de outro dia só é
    reaproveitada pela chave idêntica, e a ordem natural é a que se repete."""
    regras = _regras(pacote)
    limpos = [_ESPACOS.sub(" ", n).strip() for n in nomes if n and n.strip()]
    if not limpos:
        return ""
    vezes = Counter(limpos)

    def colada(nome: str) -> bool:
        n = len(_palavras(nome, frozenset()))
        return any(len(_palavras(outro, frozenset())) > n and _mesma_cola(nome, outro, regras.particulas)
                   for outro in vezes)

    def peso(item: tuple[int, str]) -> tuple:
        i, nome = item
        palavras = _palavras(nome, regras.particulas)
        return (sum(len(p) > 1 for p in palavras), not colada(nome), vezes[nome],
                len(_palavras(nome, frozenset())), sum(map(len, palavras)), "," not in nome, -i)

    escolhido = max(enumerate(limpos), key=peso)[1]
    sobrenome, virgula, nome = escolhido.partition(",")
    return f"{nome.strip()} {sobrenome.strip()}" if virgula and nome.strip() else escolhido


# ---------- regras do pacote ----------

@dataclass(frozen=True)
class _Rotulo:
    tipo: str        # "positivo", "negativo", "campo" ou "nascimento"
    codigo: str
    generico: bool
    forte: bool      # mais de uma palavra ou abreviado com ponto
    ambiguo: bool    # também é sobrenome comum
    livre: bool      # vale no meio da frase, sem dois-pontos


@dataclass(frozen=True)
class _Regras:
    rotulos: tuple[_Rotulo, ...]
    padrao: re.Pattern | None
    prefixos: frozenset[str]
    conselhos: frozenset[str]
    particulas: frozenset[str]
    comuns: frozenset[str]
    nao_nome: frozenset[str]
    ambiguos: frozenset[str]
    abreviacoes: frozenset[str]
    ordem: str
    pivo: int | None
    adjetivos: re.Pattern | None = None


def _dobrar(texto: str) -> str:
    """Sem os acentos do mapa e em minúsculas, caractere a caractere: as
    posições continuam as do texto de entrada."""
    return "".join(m if len(m := c.lower()) == 1 else c for c in texto.translate(_MAPA))


def _padrao_do_rotulo(dobrado: str) -> str:
    fichas = dobrado.split()
    partes = []
    for i, ficha in enumerate(fichas):
        ultima = i == len(fichas) - 1
        if ficha.endswith("."):
            partes.append(re.escape(ficha[:-1]) + (r"(?:\.|(?!\w))" if ultima else r"\.?\s*"))
        else:
            partes.append(re.escape(ficha) + (r"(?!\w)" if ultima else r"\s+"))
    return r"(?<!\w)" + "".join(partes)


def _textos(valor, onde: str) -> list[str]:
    valores = list(valor or [])
    ruins = [v for v in valores if not isinstance(v, str)]
    if ruins:
        raise ValueError(f"{onde}: {ruins!r}")
    return valores


def _regras_de(secao: dict) -> _Regras:
    paciente = secao.get("paciente") or {}
    nasc = secao.get("nascimento") or {}
    livres = set(_textos(paciente.get("em_qualquer_posicao"), "em_qualquer_posicao"))
    ambiguos = _textos(nasc.get("ambiguos"), "ambiguos")
    ambiguos_dobrados = {_dobrar(t).strip() for t in ambiguos}
    itens: dict[str, _Rotulo] = {}

    def incluir(tipo: str, codigo: str, lista, generico: bool = False) -> None:
        for texto in _textos(lista, codigo or tipo):
            dobrado = _ESPACOS.sub(" ", _dobrar(texto)).strip()
            if dobrado and dobrado not in itens:
                forte = " " in dobrado or dobrado.endswith(".")
                itens[dobrado] = _Rotulo(tipo, codigo, generico, forte,
                                         dobrado in ambiguos_dobrados, codigo in livres)

    for generico, grupo in ((False, "especificos"), (True, "genericos")):
        for codigo, lista in (paciente.get(grupo) or {}).items():
            incluir("positivo", str(codigo), lista, generico)
    incluir("negativo", "", secao.get("negativos"))
    incluir("campo", "", secao.get("outros_campos"))
    incluir("nascimento", "", nasc.get("rotulos"))
    # O mais longo primeiro: na mesma posição, a alternativa mais longa vence.
    ordenados = sorted(itens.items(), key=lambda item: -len(item[0]))
    padrao = (re.compile("|".join(f"(?P<r{i}>{_padrao_do_rotulo(t)})"
                                  for i, (t, _) in enumerate(ordenados)))
              if ordenados else None)

    def palavras(nome: str) -> frozenset[str]:
        return frozenset(chave(t) for t in _textos(secao.get(nome), nome))

    pivo = nasc.get("pivo_seculo")
    adjetivos = sorted({_ESPACOS.sub(" ", _dobrar(t)).strip()
                        for t in _textos(secao.get("negativos_adjetivos"), "negativos_adjetivos")} - {""},
                       key=lambda t: -len(t))
    return _Regras(
        rotulos=tuple(r for _, r in ordenados), padrao=padrao,
        prefixos=palavras("prefixos_profissionais"), conselhos=palavras("conselhos"),
        particulas=palavras("particulas"), comuns=palavras("nomes_comuns"),
        nao_nome=palavras("nao_nome"), ambiguos=frozenset(chave(t) for t in ambiguos),
        abreviacoes=palavras("abreviacoes_de_nome"),
        ordem=str(nasc.get("ordem") or "dma"), pivo=None if pivo is None else int(pivo),
        adjetivos=(re.compile("|".join(_padrao_do_rotulo(t) for t in adjetivos))
                   if adjetivos else None))


_CACHE: dict[int, tuple[Pacote, _Regras]] = {}


def _regras(pacote: Pacote | None) -> _Regras:
    if pacote is None:
        return _regras_padrao()
    item = _CACHE.get(id(pacote))
    if item is None or item[0] is not pacote:
        item = (pacote, _regras_de(pacote.secoes.get("rotulos") or {}))
        _CACHE[id(pacote)] = item
    return item[1]


@lru_cache(maxsize=1)
def _regras_padrao() -> _Regras:
    # Só o arquivo de rótulos: validar e comparar nomes não dependem do resto.
    caminho = RAIZ_PACKS / IDIOMA_PADRAO / "rotulos.yaml"
    return _regras_de(yaml.safe_load(caminho.read_text(encoding="utf-8-sig")) or {})


# ---------- rótulos numa linha ----------

@dataclass(frozen=True)
class _Achado:
    rotulo: _Rotulo
    inicio: int
    fim: int          # depois do separador
    separador: bool


def _achados(texto: str, regras: _Regras) -> list[_Achado]:
    """Rótulos que valem como rótulo na linha.

    Negativo e outro campo valem em qualquer posição. Rótulo de paciente vale
    no começo, depois de um vão, com separador, abreviado ou com mais de uma
    palavra; sozinho no meio do valor é palavra do nome ("MARIA NOME DA
    SILVA"). Rótulo ambíguo de nascimento ("NASCIMENTO") só com separador, no
    começo ou depois de vão."""
    if regras.padrao is None:
        return []
    dobrado = _dobrar(texto)
    # "RECEITA MÉDICA", "Assistência Médica": a palavra é adjetivo, não rótulo.
    adjetivos = ([a.span() for a in regras.adjetivos.finditer(dobrado)]
                 if regras.adjetivos is not None else [])
    saida = []
    for m in regras.padrao.finditer(dobrado):
        rotulo = regras.rotulos[int(m.lastgroup[1:])]
        if rotulo.tipo == "negativo" and any(a <= m.start() and m.end() <= b for a, b in adjetivos):
            continue
        sep = _SEPARADOR.match(dobrado, m.end())
        fim = sep.end() if sep else m.end()
        no_inicio = not _PALAVRA.search(dobrado, 0, m.start())
        apos_vao = bool(_VAO_ANTES.search(dobrado, 0, m.start()))
        if rotulo.tipo in ("negativo", "campo"):
            vale = True
        elif rotulo.tipo == "positivo":
            vale = no_inicio or apos_vao or bool(sep) or rotulo.livre or rotulo.forte
        else:
            digito = bool(_DIGITO_DEPOIS.match(dobrado, fim))
            vale = (no_inicio or apos_vao or bool(sep)
                    or (not rotulo.ambiguo and (rotulo.forte or digito)))
        if vale:
            saida.append(_Achado(rotulo, m.start(), fim, bool(sep)))
    return saida


def _no_inicio(texto: str, achado: _Achado) -> bool:
    return not _PALAVRA.search(texto, 0, achado.inicio)


def _segmento(texto: str, achados: Sequence[_Achado], j: int) -> str:
    fim = achados[j + 1].inicio if j + 1 < len(achados) else len(texto)
    return texto[achados[j].fim:fim]


def _corte(segmento: str) -> str:
    return _VAO.split(segmento.strip(), maxsplit=1)[0].strip()


def _antes_do_primeiro(texto: str, achados: Sequence[_Achado]) -> str:
    return texto[:achados[0].inicio] if achados else texto


# ---------- valor do nome ----------

def _cara_de_nome(ficha: str, k: str, regras: _Regras) -> bool:
    letras = [c for c in ficha if c.isalpha()]
    if not letras:
        return False
    if k in regras.particulas:
        return True
    primeira = letras[0]
    # Escrita sem caixa (chinês, árabe) conta como inicial maiúscula.
    if primeira.isupper() or not primeira.islower():
        return True
    return len(ficha) > 2 and ficha[1] in _APOSTROFOS and ficha[2].isupper()


def _virgula_de_sobrenome(antes: list[str], resto: list[str], regras: _Regras) -> bool:
    """"SOBRENOME, Nome": até três palavras antes da vírgula e, depois dela,
    de uma a quatro palavras de nome até o fim do campo."""
    if sum(chave(n) not in regras.particulas for n in antes) > 3 or not 1 <= len(resto) <= 4:
        return False
    for bruta in resto:
        ficha = bruta.strip(_PONTAS)
        k = chave(ficha)
        if (not ficha or bruta[0].isdigit() or _DIGITO.search(ficha) or "," in bruta
                or k in regras.nao_nome or k in regras.prefixos
                or not _cara_de_nome(ficha, k, regras)):
            return False
    return True


def _juntar(nome: list[str], regras: _Regras) -> str:
    nome = list(nome)
    while nome and chave(nome[-1].rstrip(",")) in regras.particulas:
        nome.pop()
    if not nome:
        return ""
    ultimo = nome[-1].rstrip(",")
    # Ponto de fim de frase sai; o de inicial ("J.") fica.
    if ultimo.endswith(".") and len(ultimo.rstrip(".")) > 1:
        ultimo = ultimo.rstrip(".")
    nome[-1] = ultimo
    return " ".join(nome)


def _rotulo_solto(bruta: str) -> bool:
    """Palavra seguida de dois-pontos tem forma de rótulo, mesmo fora da lista
    do pacote ("OS:", "Posto:"): nome não leva dois-pontos."""
    return bruta.endswith(":") and bool(_ALNUM.search(bruta))


def _abreviada(ficha: str) -> bool:
    """Palavra de duas letras ou mais terminada em ponto: fim de frase
    ("TESTE.") ou rótulo abreviado ("Req."). Inicial ("J.", "M.J.") não é."""
    corpo = ficha[:-1]
    return ficha.endswith(".") and "." not in corpo and sum(c.isalpha() for c in corpo) >= 2


def _valor(campo: str, regras: _Regras) -> tuple[str, str]:
    """(valor, motivo). O nome termina no primeiro sinal de que acabou:
    número, parêntese, pontuação solta, palavra com dois-pontos, palavra
    abreviada, tratamento profissional, palavra que não é de nome ou palavra
    minúscula. Se o sinal abre o campo, não há nome."""
    fichas = campo.split()
    nome: list[str] = []
    virgula_usada = False
    for i, bruta in enumerate(fichas):
        if bruta[0].isdigit():
            if nome and chave(nome[-1]) in regras.ambiguos:
                return _juntar(nome, regras), "rotulo_ambiguo"
            break
        if (nome and bruta[0] in _ABRE) or _rotulo_solto(bruta):
            break
        ficha = bruta.rstrip(",;:").strip(_PONTAS)
        if not _ALNUM.search(ficha):
            if nome:
                break
            continue
        k = chave(ficha)
        if k in regras.prefixos:
            if nome:
                break
            return "", "profissional"
        if k in regras.nao_nome:
            if nome:
                break
            return "", "termo"
        if not nome and k in regras.particulas:
            return "", "particula"
        if not _cara_de_nome(ficha, k, regras):
            if nome:
                break
            return "", "minusculas"
        nome.append(ficha)
        if len(nome) > 1 and _abreviada(ficha) and k not in regras.abreviacoes:
            # Seguida de número, a palavra abreviada é o rótulo do campo
            # seguinte ("Req. 99") e sai; senão é a última do nome.
            if i + 1 < len(fichas) and fichas[i + 1][0].isdigit():
                nome.pop()
            break
        if bruta.endswith(","):
            if virgula_usada or not _virgula_de_sobrenome(nome, fichas[i + 1:], regras):
                break
            virgula_usada = True
            nome[-1] += ","
    valor = _juntar(nome, regras)
    return valor, ("ok" if valor else "vazio")


def _valor_bruto(campo: str) -> str:
    saida = []
    for bruta in campo.split():
        if bruta[0].isdigit() or (saida and bruta[0] in _ABRE) or _rotulo_solto(bruta):
            break
        ficha = bruta.rstrip(",;:").strip(_PONTAS)
        if not _ALNUM.search(ficha):
            if saida:
                break
            continue
        saida.append(ficha)
    return " ".join(saida)


def _sem_prefixo(campo: str, regras: _Regras) -> str:
    fichas = campo.split()
    while fichas and chave(fichas[0].strip(_PONTAS)) in regras.prefixos:
        fichas.pop(0)
    return " ".join(fichas)


def _tem_nome(texto: str, regras: _Regras) -> bool:
    valor, motivo = _valor(_sem_prefixo(texto, regras), regras)
    return motivo == "ok" and _validar(valor, regras.comuns)[0]


def _tem_conselho(texto: str, regras: _Regras) -> bool:
    for ficha in re.findall(r"[^\W_]+", chave(texto)):
        letras = re.match(r"[A-Z]+", ficha)
        if letras and (letras.group() in regras.conselhos
                       or (len(letras.group()) > 2 and letras.group()[:-2] in regras.conselhos)):
            return True
    return False


# ---------- campos da página ----------

@dataclass(frozen=True)
class _Campo:
    rotulo: _Rotulo
    campo: str          # valor já cortado no vão ("" quando não se leu)
    segmento: str       # do rótulo ao próximo rótulo, para achar conselho
    confianca: float
    separador: bool
    contexto: frozenset[str]


def _mesma_altura(a: Caixa, b: Caixa) -> bool:
    """Caixas da mesma linha impressa: sobreposição vertical de pelo menos
    metade da mais baixa."""
    menor = min(a[3] - a[1], b[3] - b[1])
    sobre = min(a[3], b[3]) - max(a[1], b[1])
    return menor > 0 and sobre >= 0.5 * menor


def _vizinhas(linhas: Sequence[Linha], i: int) -> set[int]:
    """A anterior e a seguinte na ordem de leitura, mais as da mesma altura."""
    viz = {j for j in (i - 1, i + 1) if 0 <= j < len(linhas)}
    for j, outra in enumerate(linhas):
        if j != i and _mesma_altura(linhas[i].caixa, outra.caixa):
            viz.add(j)
    return viz


def _linha_a_direita(linhas: Sequence[Linha], i: int) -> int | None:
    """A caixa mais próxima à direita, na mesma linha impressa. O leitor
    separa rótulo e valor quando o layout os põe em itens diferentes. Mais
    longe que `_VAO_MAXIMO_A_DIREITA` é outra coluna da página."""
    x1 = linhas[i].caixa[2]
    melhor = None
    for j, outra in enumerate(linhas):
        ox0, _, ox1, _ = outra.caixa
        if (j == i or not _mesma_altura(linhas[i].caixa, outra.caixa)
                or (ox0 + ox1) / 2 <= x1 or ox0 - x1 > _VAO_MAXIMO_A_DIREITA):
            continue
        if melhor is None or ox0 < linhas[melhor].caixa[0]:
            melhor = j
    return melhor


def _linha_abaixo(linhas: Sequence[Linha], i: int) -> int | None:
    """A linha logo abaixo, com sobreposição horizontal, a no máximo duas
    alturas de distância. Sem geometria, a seguinte na ordem de leitura."""
    x0, y0, x1, y1 = linhas[i].caixa
    altura = y1 - y0
    if altura <= 0:
        return i + 1 if i + 1 < len(linhas) else None
    melhor = None
    for j, outra in enumerate(linhas):
        ox0, oy0, ox1, _ = outra.caixa
        if (j == i or oy0 < y0 + altura / 2 or oy0 - y1 > 2 * altura
                or min(x1, ox1) - max(x0, ox0) <= 0):
            continue
        if melhor is None or oy0 < linhas[melhor].caixa[1]:
            melhor = j
    return melhor


def _abre_com_rotulo(k: int, textos, achados) -> bool:
    return bool(achados[k]) and _no_inicio(textos[k], achados[k][0])


def _valor_de_linha(x: int, idx: int, linhas, textos, achados) -> tuple[str, str, int, bool]:
    """(campo, segmento, linha de onde veio, é título de seção). Rótulo
    sozinho no começo da linha pega o valor da caixa ao lado, na mesma
    altura, ou da linha de baixo, se ela não começa com outro rótulo."""
    segmento = _segmento(textos[x], achados[x], idx)
    campo = _corte(segmento)
    if campo or len(achados[x]) != 1 or not _no_inicio(textos[x], achados[x][idx]):
        return campo, segmento, x, False
    k = _linha_a_direita(linhas, x)
    if k is not None and textos[k].strip() and not _abre_com_rotulo(k, textos, achados):
        segmento = _antes_do_primeiro(textos[k], achados[k])
        return _corte(segmento), segmento, k, False
    k = _linha_abaixo(linhas, x)
    if k is None:
        return "", segmento, x, False
    if _abre_com_rotulo(k, textos, achados):
        return "", segmento, x, achados[k][0].rotulo.tipo == "positivo"
    segmento = _antes_do_primeiro(textos[k], achados[k])
    return _corte(segmento), segmento, k, False


def _contexto_de_linhas(i: int, fonte: int, linhas, textos, achados, regras) -> frozenset[str]:
    ctx = set()
    proprias = {i, fonte}
    vizinhas = (_vizinhas(linhas, i) | _vizinhas(linhas, fonte)) - proprias
    # Duas colunas só quando o valor veio de baixo: na caixa ao lado, na mesma
    # altura, o valor é o primeiro texto depois do rótulo, sem ambiguidade.
    abaixo = fonte != i and not _mesma_altura(linhas[i].caixa, linhas[fonte].caixa)
    for x in sorted(proprias | vizinhas):
        for idx, achado in enumerate(achados[x]):
            if achado.rotulo.tipo != "negativo":
                continue
            if x in proprias and abaixo:
                ctx.add("duas_colunas")
            proprio = _valor_de_linha(x, idx, linhas, textos, achados)[0]
            if not _tem_nome(proprio, regras):
                ctx.add("negativo")
        if x in vizinhas and _tem_conselho(_antes_do_primeiro(textos[x], achados[x]), regras):
            ctx.add("conselho")
    return frozenset(ctx)


def _campos_de_linhas(linhas: Sequence[Linha], regras: _Regras) -> Iterator[_Campo]:
    textos = [_limpar_unicode(linha.texto) for linha in linhas]
    achados = [_achados(t, regras) for t in textos]
    for i, linha in enumerate(linhas):
        for j, achado in enumerate(achados[i]):
            if achado.rotulo.tipo not in ("positivo", "nascimento"):
                continue
            campo, segmento, fonte, titulo = _valor_de_linha(i, j, linhas, textos, achados)
            if titulo:
                continue
            contexto = (_contexto_de_linhas(i, fonte, linhas, textos, achados, regras)
                        if achado.rotulo.tipo == "positivo" else frozenset())
            yield _Campo(achado.rotulo, campo, segmento, linhas[fonte].confianca,
                         achado.separador, contexto)


def _celula(textos, r: int, c: int) -> str:
    return textos[r][c] if 0 <= r < len(textos) and 0 <= c < len(textos[r]) else ""


def _confianca_da_celula(tabela: Tabela, r: int, c: int) -> float:
    # Sem confiança registrada para a célula, não há como afirmar a leitura.
    try:
        return tabela.confiancas[r][c]
    except IndexError:
        return 0.0


def _valor_de_celula(r: int, c: int, idx: int, textos, achados) -> tuple[str, str, tuple[int, int]]:
    """Rótulo sozinho numa célula pega o valor da célula à direita ou, se ela
    é outro rótulo, da célula de baixo."""
    texto = textos[r][c]
    segmento = _segmento(texto, achados[r][c], idx)
    campo = _corte(segmento)
    if campo or len(achados[r][c]) != 1 or not _no_inicio(texto, achados[r][c][idx]):
        return campo, segmento, (r, c)
    for rr, cc in ((r, c + 1), (r + 1, c)):
        alvo = _celula(textos, rr, cc)
        if not alvo.strip():
            continue
        if achados[rr][cc] and _no_inicio(alvo, achados[rr][cc][0]):
            continue
        segmento = _antes_do_primeiro(alvo, achados[rr][cc])
        return _corte(segmento), segmento, (rr, cc)
    return "", segmento, (r, c)


def _contexto_de_tabela(r: int, c: int, fonte, textos, achados, regras) -> frozenset[str]:
    """Na tabela, vizinhas são as células da mesma linha e as de cima e de
    baixo na mesma coluna, fora a do valor."""
    ctx = set()
    lugares = {(r, cc) for cc in range(len(textos[r])) if cc != c}
    lugares |= {(rr, c) for rr in (r - 1, r + 1) if 0 <= rr < len(textos) and c < len(textos[rr])}
    lugares.discard(fonte)
    for x, y in sorted(lugares):
        for idx, achado in enumerate(achados[x][y]):
            if (achado.rotulo.tipo == "negativo"
                    and not _tem_nome(_valor_de_celula(x, y, idx, textos, achados)[0], regras)):
                ctx.add("negativo")
        if _tem_conselho(_antes_do_primeiro(textos[x][y], achados[x][y]), regras):
            ctx.add("conselho")
    return frozenset(ctx)


def _campos_de_tabela(tabela: Tabela, regras: _Regras) -> Iterator[_Campo]:
    textos = [[_limpar_unicode(c) for c in linha] for linha in tabela.celulas]
    achados = [[_achados(t, regras) for t in linha] for linha in textos]
    for r, linha in enumerate(textos):
        for c in range(len(linha)):
            for j, achado in enumerate(achados[r][c]):
                if achado.rotulo.tipo not in ("positivo", "nascimento"):
                    continue
                campo, segmento, fonte = _valor_de_celula(r, c, j, textos, achados)
                contexto = (_contexto_de_tabela(r, c, fonte, textos, achados, regras)
                            if achado.rotulo.tipo == "positivo" else frozenset())
                yield _Campo(achado.rotulo, campo, segmento,
                             _confianca_da_celula(tabela, *fonte), achado.separador, contexto)


def _campos(pagina: Pagina, regras: _Regras) -> Iterator[_Campo]:
    yield from _campos_de_linhas([b for b in pagina.blocos if isinstance(b, Linha)], regras)
    for bloco in pagina.blocos:
        if isinstance(bloco, Tabela):
            yield from _campos_de_tabela(bloco, regras)


# ---------- leituras e candidatos ----------

@dataclass(frozen=True)
class Leitura:
    """Um valor depois de rótulo de paciente e o que a regra decidiu dele.

    `valor` pode ser nome de paciente: vai para o manifesto, nunca para o log."""
    valor: str
    chave: str
    rotulo: str
    pagina: int
    confianca: float
    motivo: str
    efeito: str     # "aceito", "duvida" ou "reserva"


def _ler(campo: _Campo, pagina: int, regras: _Regras) -> Leitura | None:
    rotulo = campo.rotulo

    def nova(valor: str, motivo: str, efeito: str) -> Leitura:
        return Leitura(valor, chave(valor), rotulo.codigo, pagina, campo.confianca, motivo, efeito)

    if not campo.campo:
        return nova("", "sem_valor", "reserva")
    valor, motivo = _valor(campo.campo, regras)
    if motivo in ("vazio", "termo", "particula"):
        return None
    if motivo == "minusculas":
        # Sem dois-pontos é frase ("Paciente refere..."); com eles, pode ser nome.
        return nova(_valor_bruto(campo.campo), motivo, "duvida") if campo.separador else None
    if motivo == "profissional":
        if rotulo.generico:
            return None
        return nova(_valor(_sem_prefixo(campo.campo, regras), regras)[0], motivo, "duvida")
    if _tem_conselho(campo.segmento, regras):
        return None if rotulo.generico else nova(valor, "conselho", "duvida")
    if "duas_colunas" in campo.contexto:
        return nova(valor, "duas_colunas", "reserva")
    if rotulo.generico:
        for motivo_contexto in ("negativo", "conselho"):
            if motivo_contexto in campo.contexto:
                return nova(valor, motivo_contexto, "reserva")
    if motivo == "rotulo_ambiguo":
        return nova(valor, motivo, "duvida")
    ok, motivo = _validar(valor, regras.comuns)
    return nova(valor, motivo, "aceito" if ok else "duvida")


def leituras(paginas: Sequence[Pagina], pacote: Pacote) -> list[Leitura]:
    """Todas as leituras depois de rótulo de paciente, na ordem das páginas."""
    regras = _regras(pacote)
    saida = []
    for pagina in paginas:
        for campo in _campos(pagina, regras):
            if campo.rotulo.tipo == "positivo":
                leitura = _ler(campo, pagina.indice, regras)
                if leitura is not None:
                    saida.append(leitura)
    return saida


def candidatos(paginas: Sequence[Pagina], pacote: Pacote, limiar_confianca: float = 0.80
               ) -> list[Candidato]:
    """Nomes aceitos com confiança de pelo menos `limiar_confianca`, um por
    página, chave e rótulo."""
    vistos = set()
    saida = []
    for leitura in leituras(paginas, pacote):
        marca = (leitura.pagina, leitura.chave, leitura.rotulo)
        if leitura.efeito != "aceito" or leitura.confianca < limiar_confianca or marca in vistos:
            continue
        vistos.add(marca)
        saida.append(Candidato(leitura.valor, leitura.chave, leitura.rotulo, leitura.pagina,
                               leitura.confianca))
    return saida


# ---------- data de nascimento ----------

def _data(texto: str, regras: _Regras, hoje: date | None) -> date | None:
    m = _DATA.match(texto)
    if not m:
        return None
    a, b, ano = int(m.group(1)), int(m.group(3)), m.group(4)
    dia, mes = (a, b) if regras.ordem == "dma" else (b, a)
    if len(ano) == 2:
        if regras.pivo is None:
            return None
        ano_n = int(ano) + (2000 if int(ano) <= regras.pivo else 1900)
    else:
        ano_n = int(ano)
    if ano_n < 1900:
        return None
    try:
        lida = date(ano_n, mes, dia)
        # Ninguém nasce no futuro: o ano de dois dígitos é do século anterior.
        if len(ano) == 2 and hoje is not None and lida > hoje:
            lida = date(ano_n - 100, mes, dia)
        return lida
    except ValueError:
        return None


def nascimento(paginas: Sequence[Pagina], pacote: Pacote, hoje: date | None = None) -> date | None:
    """A data de nascimento depois de rótulo do pacote. Datas diferentes nas
    páginas dadas: None, porque não dá para saber qual vale. Com `hoje`, o ano
    de dois dígitos que cairia no futuro recua um século; sem ele, vale só o
    pivô do pacote."""
    regras = _regras(pacote)
    achadas = set()
    for pagina in paginas:
        for campo in _campos(pagina, regras):
            if campo.rotulo.tipo == "nascimento" and (d := _data(campo.campo, regras, hoje)):
                achadas.add(d)
    return next(iter(achadas)) if len(achadas) == 1 else None
