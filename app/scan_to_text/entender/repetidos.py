"""Exame repetido: duas cópias do mesmo exame são transcritas uma vez.

Mesmo exame é mesmo título, mesma data, mesma instituição, mesmos números com
a unidade e texto normalizado quase idêntico. Qualquer valor diferente, ou um
laudo retificado, mantém os dois.

A comparação do texto tem duas camadas, e as duas precisam passar:

- os dois textos têm a mesma sequência de termos, termo a termo: números (com
  o sinal), palavras e os sinais de resultado (+, <, >). Número tem de ser
  idêntico, e a unidade é o termo que vem depois dele. Palavra pode diferir só
  por ruído de OCR: forma confundível ("rn" e "m", "0" e "o" dentro da palavra)
  ou uma letra trocada numa palavra longa. Palavra a mais de um lado
  ("retificado", "não reagente"), resultado qualitativo trocado ("positivo" e
  "negativo"), letra trocada em palavra curta ("A" e "B") ou outra unidade
  separam;
- o texto normalizado inteiro tem similaridade de pelo menos `limiar`.

Ficam de fora só datas e horas, que mudam numa reimpressão; a data do exame já
foi comparada à parte. Hora é hh:mm com dois dígitos e data tem dia e mês
válidos: "1:40" e "1/1280" são titulações e contam como números.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from scan_to_text.entender.segmentar import chave_texto, dobrar, numeracao
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Documento, texto

_TRACOS = str.maketrans("–—−", "---")
_DATA = re.compile(r"(?<![\d.,/:])(?:(?:0?[1-9]|[12]\d|3[01])([/.-])(?:0?[1-9]|1[0-2])\1(?:\d{4}|\d{2})"
                   r"|(?:0[1-9]|1[0-2])/(?:19|20)\d{2})(?![\d/])")
_HORA = re.compile(r"(?<![\d:])(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?![\d:])")
# Dígito confundível no meio de uma palavra ("esperad0s") é ruído de OCR: vira
# a letra que ele imita. Fora disso, todo dígito é número, inclusive grudado
# em letra ("Hemoglobina12,4").
_DIGITO_NA_PALAVRA = re.compile(r"(?<=[^\W\d_])[015](?=[^\W\d_])")
_LETRA_DO_DIGITO = {"0": "o", "1": "l", "5": "s"}
# Sinal só quando não continua outro termo: em "0,6-1,1" o traço é intervalo.
_TERMO = re.compile(r"(?:(?<![\w.,])[+-])?\d+(?:[.,]\d+)*|[^\W\d_]+|[+<>≤≥]+")
# Confusões de OCR que trocam o comprimento da palavra; "i" e "l" se confundem
# entre si e com "1", que dentro da palavra já virou "l".
_CONFUSOES = (("rn", "m"), ("cl", "d"), ("vv", "w"))
_I_E_L = str.maketrans("i", "l")
# Palavra mais curta que isto não admite letra trocada: "IgA" e "IgG", "IIA" e
# "IIB" são resultados diferentes.
MIN_LETRA_TROCADA = 6


def _texto(documento: Documento) -> str:
    if documento.corpo or documento.cabecalho_original:
        partes = [*documento.cabecalho_original, *(texto(b) for b in documento.corpo)]
    else:
        partes = [texto(b) for p in documento.paginas for b in p.blocos]
    return "\n".join(partes)


def _termos(t: str) -> list[str]:
    d = dobrar(t.translate(_TRACOS))
    d = _HORA.sub(" ", _DATA.sub(" ", d))
    d = _DIGITO_NA_PALAVRA.sub(lambda m: _LETRA_DO_DIGITO[m.group()], d)
    return _TERMO.findall(d)


def _forma(palavra: str) -> str:
    for antes, depois in _CONFUSOES:
        palavra = palavra.replace(antes, depois)
    return palavra.translate(_I_E_L)


def _mesmo_termo(a: str, b: str) -> bool:
    """Igual, ou a mesma palavra a menos de ruído de OCR. Número e sinal: só igual."""
    if a == b:
        return True
    if not (a.isalpha() and b.isalpha()):
        return False
    fa, fb = _forma(a), _forma(b)
    if fa == fb:
        return True
    if len(fa) != len(fb) or len(fa) < MIN_LETRA_TROCADA:
        return False
    trocas = [k for k, (x, y) in enumerate(zip(fa, fb)) if x != y]
    # A primeira letra fica de fora: troca nela muda a palavra ("basal", "nasal").
    return len(trocas) == 1 and trocas[0] > 0


def _normal(t: str) -> str:
    return " ".join(dobrar(t).split())


def _numeracao_completa(documento: Documento, pacote: Pacote) -> bool:
    numeros = [numeracao(p, pacote) for p in documento.paginas]
    totais = {n[1] for n in numeros if n is not None and n[1] is not None}
    if not totais:
        return True
    if len(totais) != 1:
        return False
    total = next(iter(totais))
    return {n[0] for n in numeros if n is not None} == set(range(1, total + 1))


def mesmo_exame(a: Documento, b: Documento, limiar: float = 0.97, *, pacote: Pacote) -> bool:
    """True quando `b` é cópia de `a` (ou o contrário) e pode ser transcrito uma vez."""
    if chave_texto(a.titulo) != chave_texto(b.titulo):
        return False
    if (a.data.valor, a.data.precisao) != (b.data.valor, b.data.precisao):
        return False
    if chave_texto(a.instituicao or "") != chave_texto(b.instituicao or ""):
        return False
    # Guarda: cópia de outro paciente nunca é repetido.
    if a.paciente != b.paciente:
        return False
    # Continuações iguais não provam que as páginas ausentes também são iguais.
    # A numeração permanece nas páginas brutas mesmo após a limpeza do rodapé.
    if not _numeracao_completa(a, pacote) or not _numeracao_completa(b, pacote):
        return False
    ta, tb = _texto(a), _texto(b)
    termos_a, termos_b = _termos(ta), _termos(tb)
    if len(termos_a) != len(termos_b) or not all(map(_mesmo_termo, termos_a, termos_b)):
        return False
    return fuzz.ratio(_normal(ta), _normal(tb)) >= limiar * 100
