"""Ping: varre as origens e devolve os arquivos prontos para ler.

Só lê: `stat` e uma abertura para leitura. Nada é escrito numa origem.

A estabilidade é medida no relógio monotônico desta estação, entre duas
varreduras: a data de modificação vem do relógio do servidor da origem, que
pode divergir do nosso, e por isso só serve para o `days_back` e para a ordem.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

IGNORAR = ("~$*", "*.tmp", "*.part", "*.crdownload", "Thumbs.db", "desktop.ini")

_IGNORAR_CASEFOLD = tuple(p.casefold() for p in IGNORAR)
_OCULTO = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
_log = logging.getLogger(__name__)


class Vigia:
    def __init__(self, origens: Sequence[Path], estabilidade_s: int, days_back: int,
                 relogio: Callable[[], float] = time.monotonic,
                 agora: Callable[[], float] = time.time):
        self.origens = [Path(o) for o in origens]
        self.estabilidade_s = estabilidade_s
        self.days_back = days_back
        self.inacessiveis: list[Path] = []
        self._relogio = relogio
        self._agora = agora
        # caminho -> (tamanho, mtime_ns, desde): a última assinatura vista e
        # desde quando, no relógio desta estação, ela não muda
        self._vistos: dict[str, tuple[int, int, float]] = {}
        # caminho -> (tamanho, mtime_ns, sha256): evita recalcular o SHA-256
        # de arquivo que não mudou
        self._shas: dict[str, tuple[int, int, str]] = {}

    def varrer(self) -> list[Path]:
        agora = self._relogio()
        corte = self._agora() - self.days_back * 86400 if self.days_back > 0 else None
        vistos: dict[str, tuple[int, int, float]] = {}
        prontos: list[tuple[int, str, Path]] = []
        inacessiveis: list[Path] = []
        for origem in self.origens:
            try:
                entradas = list(_percorrer(origem))
            except OSError:
                inacessiveis.append(origem)
                continue
            for caminho, st in entradas:
                if corte is not None and st.st_mtime < corte:
                    continue
                chave = str(caminho)
                assinatura = (st.st_size, st.st_mtime_ns)
                anterior = self._vistos.get(chave)
                desde = anterior[2] if anterior and anterior[:2] == assinatura else agora
                vistos[chave] = (*assinatura, desde)
                if agora - desde >= self.estabilidade_s and _abre_para_leitura(caminho):
                    prontos.append((st.st_mtime_ns, chave, caminho))
        self._vistos = vistos
        self._shas = {c: v for c, v in self._shas.items() if c in vistos}
        self.inacessiveis = inacessiveis
        if inacessiveis:
            _log.warning("vigia_origem_inacessivel %d", len(inacessiveis))
        prontos.sort()
        return [caminho for _, _, caminho in prontos]

    def lembrar(self, caminho: Path, sha256: str) -> None:
        """Guarda o SHA-256 da cópia feita depois da última varredura.

        A chave é a assinatura (tamanho, data) que a varredura viu. Se o
        arquivo mudou antes da cópia, a assinatura atual já não casa e o
        SHA-256 é recalculado: o erro possível é recalcular, nunca pular.
        """
        visto = self._vistos.get(str(caminho))
        if visto is not None:
            self._shas[str(caminho)] = (visto[0], visto[1], sha256)

    def sha256_conhecido(self, caminho: Path) -> str | None:
        guardado = self._shas.get(str(caminho))
        if guardado is None:
            return None
        try:
            st = os.stat(caminho)
        except OSError:
            return None
        return guardado[2] if (st.st_size, st.st_mtime_ns) == guardado[:2] else None


def _percorrer(raiz: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Arquivos regulares abaixo de `raiz`, sem ocultos, de sistema e temporários.

    Só a raiz inacessível levanta. Pasta que é ponto de reanálise (junção ou
    link) não é seguida: uma junção apontando para cima faria laço.
    """
    pilha = [raiz]
    while pilha:
        pasta = pilha.pop()
        try:
            with os.scandir(pasta) as it:
                entradas = list(it)
        except OSError:
            if pasta is raiz:
                raise
            _log.warning("vigia_subpasta_inacessivel")
            continue
        for entrada in entradas:
            try:
                st = entrada.stat(follow_symlinks=False)
                atributos = getattr(st, "st_file_attributes", 0)
                if atributos & _OCULTO:
                    continue
                if stat.S_ISDIR(st.st_mode) or entrada.is_dir():
                    if not atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        pilha.append(Path(entrada.path))
                    continue
                if _ignorado(entrada.name):
                    continue
                if atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    st = entrada.stat()
                if stat.S_ISREG(st.st_mode):
                    yield Path(entrada.path), st
            except OSError:
                continue


def _ignorado(nome: str) -> bool:
    nome = nome.casefold()
    return any(fnmatch.fnmatchcase(nome, padrao) for padrao in _IGNORAR_CASEFOLD)


def _abre_para_leitura(caminho: Path) -> bool:
    try:
        with open(caminho, "rb"):
            return True
    except OSError:
        return False
