"""Nomes de pasta e de arquivo no destino.

A normalização é a do photoid-rt, para as pastas dos dois apps seguirem o mesmo
padrão, mais as regras que o Windows exige e o photoid-rt não precisava:
caracteres de formatação fora, NFC, nome reservado com `_`, parte do nome com
até 80 caracteres e caminho completo com até 240.

Porte de `removerAcentosMaiusculas`, `nomePastaPaciente`, `chaveNome` e
`pastaCasaPaciente` (sem a chave solta; ver `casa_pasta`), de
`app/src/main/java/com/radioterapia/ai/util/StorageLocal.kt` do photoid-rt
(commit aa53fef, Apache-2.0).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from datetime import date
from os import PathLike
from pathlib import PureWindowsPath

from scan_to_text.pacote import Pacote
from scan_to_text.tipos import DataExame

# Mapa da rotina VBA do photoid-rt, na mesma ordem:
#   ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑáàâãäéèêëíìîïóòôõöúùûüçñ
#   AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn
# Em pontos de código porque o motor não guarda letra acentuada em literal.
_COM_ACENTOS = (
    0xC1, 0xC0, 0xC2, 0xC3, 0xC4, 0xC9, 0xC8, 0xCA, 0xCB, 0xCD, 0xCC, 0xCE, 0xCF,
    0xD3, 0xD2, 0xD4, 0xD5, 0xD6, 0xDA, 0xD9, 0xDB, 0xDC, 0xC7, 0xD1,
    0xE1, 0xE0, 0xE2, 0xE3, 0xE4, 0xE9, 0xE8, 0xEA, 0xEB, 0xED, 0xEC, 0xEE, 0xEF,
    0xF3, 0xF2, 0xF4, 0xF5, 0xF6, 0xFA, 0xF9, 0xFB, 0xFC, 0xE7, 0xF1,
)
_SEM_ACENTOS = "AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn"
_MAPA = dict(zip(_COM_ACENTOS, _SEM_ACENTOS, strict=True))

# Proibidos em nome de arquivo (FAT, exFAT, NTFS) mais os controles.
_ILEGAIS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# `\s` do Python pega também o espaço Unicode (sem quebra, ideográfico), que
# no nome de pasta parece espaço e não é: duas pastas iguais à vista.
_ESPACOS = re.compile(r"\s+")
# O Windows toma como dispositivo o nome reservado seguido de espaços e de
# ponto ou do fim: `NUL.TXT` também abre o dispositivo.
_RESERVADO = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?= *(?:\.|\Z))")

LIMITE_NOME = 80
LIMITE_CAMINHO = 240


def _remover_acentos_maiusculas(texto: str) -> str:
    """Porte exato de `StorageLocal.removerAcentosMaiusculas`."""
    t = texto.translate(_MAPA)
    t = _ESPACOS.sub(" ", _ILEGAIS.sub(" ", t))
    return t.strip().rstrip(". ").upper()


def _base(texto: str | None) -> str:
    # NFC antes do mapa: "a" + til combinante só vira "A" depois de composto.
    t = "".join(c for c in (texto or "") if unicodedata.category(c) != "Cf")
    t = _remover_acentos_maiusculas(unicodedata.normalize("NFC", t))
    return unicodedata.normalize("NFC", t)


def _cortar(nome: str, limite: int = LIMITE_NOME) -> str:
    if len(nome) <= limite:
        return nome
    corte = nome[:limite]
    if nome[limite] != " ":
        espaco = corte.rfind(" ")
        if espaco > 0:
            corte = corte[:espaco]
    return corte.rstrip(". ")


def _sem_reservado(nome: str) -> str:
    achado = _RESERVADO.match(nome)
    if not achado:
        return nome
    return nome[:achado.end()] + "_" + nome[achado.end():]


def normalizar(texto: str) -> str:
    """Nome de pasta ou de arquivo: porte do photoid-rt mais as regras de Windows."""
    return _sem_reservado(_cortar(_base(texto)))


def _iso(dia: date) -> str:
    return f"{dia.year:04d}-{dia.month:02d}-{dia.day:02d}"


def pasta_paciente(nome: str, dia: date) -> str:
    """`<NOME> - <AAAA-MM-DD>`, com a data do primeiro processamento."""
    n = _ESPACOS.sub(" ", normalizar(nome))
    if not n:
        raise ValueError("nome_vazio")
    return f"{n} - {_iso(dia)}"


def pasta_desconhecido(dia: date, nome_arquivo: str, pacote: Pacote,
                       existentes: Collection[str] = ()) -> str:
    """`DESCONHECIDO - <AAAA-MM-DD> - <ARQUIVO SEM EXTENSAO>`.

    Sem nada aproveitável no nome do arquivo, fica sem a última parte. O nome
    sozinho não garante pasta própria: dois arquivos do mesmo dia com o mesmo
    nome base (scanner que reinicia a numeração, subpastas da origem) dão o
    mesmo nome. Por isso quem chama passa em `existentes` as pastas que já
    existem no destino, e o nome volta com ` (2)`, ` (3)`... quando colide;
    sem `existentes`, a colisão fica por conta de quem chama (`com_sufixo`).
    """
    rotulo = pacote["textos"]["pastas"]["desconhecido"]
    arquivo = normalizar(PureWindowsPath(nome_arquivo).stem)
    pasta = f"{rotulo} - {_iso(dia)}"
    return com_sufixo(f"{pasta} - {arquivo}" if arquivo else pasta, existentes)


def pasta_categoria(categoria: int, pacote: Pacote) -> str:
    nomes = pacote["textos"]["pastas"]["categorias"]
    if categoria not in nomes:
        raise ValueError("categoria_invalida")
    return nomes[categoria]


def prefixo_data(data: DataExame | None, pacote: Pacote) -> str:
    """`2026-04-02`, `2026-04`, `2026` ou o texto de sem data, pela precisão."""
    if data is None or data.valor is None:
        return pacote["textos"]["documento"]["sem_data"]
    valor = data.valor
    precisao = data.precisao or "dia"
    if precisao == "dia":
        return _iso(valor)
    if precisao == "mes":
        return f"{valor.year:04d}-{valor.month:02d}"
    if precisao == "ano":
        return f"{valor.year:04d}"
    raise ValueError("precisao_desconhecida")


def nome_documento(data: DataExame, titulo: str, pacote: Pacote) -> str:
    """`<data> - <TITULO>`, sem extensão; o título é normalizado (até 80)."""
    titulo_n = normalizar(titulo) or pacote["textos"]["documento"]["sem_titulo"]
    return f"{prefixo_data(data, pacote)} - {titulo_n}"


def com_sufixo(nome: str, existentes: Collection[str]) -> str:
    """`nome`, ou `nome (2)`, `nome (3)`... o primeiro livre.

    `existentes` são nomes base, sem extensão, porque o MD e o arquivo de
    conferência dividem o mesmo nome base. A comparação ignora maiúsculas,
    como o sistema de arquivos do Windows.
    """
    ocupados = {e.casefold() for e in existentes}
    if nome.casefold() not in ocupados:
        return nome
    n = 2
    while f"{nome} ({n})".casefold() in ocupados:
        n += 1
    return f"{nome} ({n})"


def cabe_no_caminho(base: str | PathLike[str], *partes: str, limite: int = LIMITE_CAMINHO) -> bool:
    """O caminho completo cabe no limite (240 dá margem sobre o MAX_PATH de 260).

    Conta em unidades UTF-16, que é como o Windows mede o caminho.
    """
    caminho = str(PureWindowsPath(base, *partes))
    return len(caminho.encode("utf-16-le")) // 2 <= limite


def _chave_nome(texto: str | None) -> str:
    """Porte de `StorageLocal.chaveNome`."""
    return _ESPACOS.sub(" ", _base(texto).replace("_", " ")).strip()


def casa_pasta(nome_pasta: str, nome_paciente: str) -> bool:
    """Porte de `StorageLocal.pastaCasaPaciente`, sem a chave solta.

    Casa só com a chave do nome idêntica, seguida ou não de um
    complemento depois de ` - `, `_` ou espaço. Nome que é prefixo de outro
    nunca casa ("ANA" não adota "ANA MARIA"), e nome vazio não casa com nada.

    Divergência do porte: o photoid-rt tentava ainda a chave só com A-Z e 0-9
    (`chaveNomeSolta`), para achar pasta antiga gravada sem apóstrofo. Aqui não
    há pasta antiga, e toda pasta termina em data, que a chave solta reduz a
    dígitos: qualquer par de nomes com o mesmo esqueleto ASCII casaria
    ("王 SILVA" e "李 SILVA", "ANA PAULA" e "ANAPAULA").
    """
    alvo = _chave_nome(nome_paciente)
    if not alvo:
        return False
    k = _chave_nome(nome_pasta)
    if k == alvo:
        return True
    if _chave_nome((nome_pasta or "").rsplit(" - ", 1)[0]) == alvo:
        return True
    if k.startswith(alvo + " "):
        resto = k[len(alvo) + 1:].lstrip("- _").strip()
        if resto and resto.isdecimal():
            return True
    return False
