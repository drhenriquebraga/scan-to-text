"""Gravação atômica no destino: rascunho `.parcial` e depois renomeação.

`os.replace` garante "ou o antigo ou o novo" contra a interrupção, mas não
contra o concorrente: com rascunho de nome fixo, duas estações no mesmo
arquivo escrevem no mesmo `.parcial` e publicam a mistura das duas gravações,
do tamanho certo e sem erro nenhum. Por isso o rascunho leva o nome de quem
escreve (estação e PID), e os nomes finais são determinísticos: uma corrida
perdida regrava bytes idênticos.

Toda operação de arquivo usa o caminho estendido (`\\\\?\\`): o destino pode
ter até 240 caracteres, e o sufixo do rascunho passa do MAX_PATH de 260.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from scan_to_text.es import ES
from scan_to_text.log import evento

_BLOCO = 1024 * 1024
_TENTATIVAS = 5
_ESPERA_INICIAL_S = 0.25
_esperar = time.sleep


def _longo(caminho: Path | str) -> str:
    texto = os.path.abspath(caminho)
    if os.name != "nt" or texto.startswith("\\\\?\\"):
        return texto
    if texto.startswith("\\\\"):
        return "\\\\?\\UNC\\" + texto[2:]
    return "\\\\?\\" + texto


def _quem(estacao: str) -> str:
    # Porte de local-suite, app/radai_sets.py (nome_parcial), commit 55cffa9,
    # Apache-2.0; o nome vazio cai em "estacao", termo do glossário. Só ASCII
    # seguro: `str.isalnum()` sozinho é Unicode e deixaria acento
    # passar, e um `.parcial` que o sistema de arquivos recuse derruba toda
    # gravação. O corte pode colidir, e quatro dígitos do hash do nome
    # original devolvem a unicidade que a limpeza tirou.
    bruto = str(estacao)
    quem = "".join(c for c in bruto if (c.isascii() and c.isalnum()) or c in "-_")[:24]
    if quem != bruto:
        resumo = hashlib.md5(bruto.encode("utf-8", "replace"), usedforsecurity=False)
        quem += resumo.hexdigest()[:4]
    return quem or "estacao"


def nome_parcial(destino: Path, estacao: str, pid: int) -> Path:
    """`<destino>.<estação>.<pid>.parcial`, na mesma pasta do destino.

    O PID separa dois processos da mesma estação.
    """
    destino = Path(destino)
    return destino.with_name(f"{destino.name}.{_quem(estacao)}.{int(pid)}.parcial")


def _apagar(caminho: Path) -> None:
    try:
        os.remove(_longo(caminho))
    except FileNotFoundError:
        pass


def _mesmos_bytes(destino: Path, dados: bytes) -> bool:
    try:
        with open(_longo(destino), "rb") as atual:
            if os.fstat(atual.fileno()).st_size != len(dados):
                return False
            return atual.read() == dados
    except OSError:
        return False


def _mesmo_arquivo(origem: Path, destino: Path) -> bool:
    try:
        with open(_longo(origem), "rb") as a, open(_longo(destino), "rb") as b:
            if os.fstat(a.fileno()).st_size != os.fstat(b.fileno()).st_size:
                return False
            while True:
                bloco = a.read(_BLOCO)
                if bloco != b.read(_BLOCO):
                    return False
                if not bloco:
                    return True
    except OSError:
        return False


def _publicar(parcial: Path, destino: Path, tentativas: int) -> None:
    # Antivírus e indexador seguram o arquivo recém-escrito por instantes:
    # PermissionError na renomeação tenta de novo, com espera crescente.
    tentativas = max(1, int(tentativas))
    for tentativa in range(1, tentativas + 1):
        try:
            os.replace(_longo(parcial), _longo(destino))
            return
        except PermissionError as erro:
            winerror = getattr(erro, "winerror", None)
            if tentativa == tentativas:
                evento("gravar_desistiu", tentativas=tentativa, winerror=winerror)
                raise
            evento("gravar_nova_tentativa", tentativa=tentativa, winerror=winerror)
            _esperar(_ESPERA_INICIAL_S * 2 ** (tentativa - 1))


def gravar(destino: Path, dados: bytes, es: ES, estacao: str, pid: int,
           tentativas: int = _TENTATIVAS) -> None:
    """Escreve no `.parcial` e renomeia; nunca deixa `.parcial`.

    Recusa destino dentro de origem. Conteúdo idêntico ao que já está lá não
    é regravado: substituir um arquivo aberto num visualizador falharia à toa.
    """
    destino = Path(destino)
    es.exigir_fora_das_origens(destino)
    if _mesmos_bytes(destino, dados):
        return
    os.makedirs(_longo(destino.parent), exist_ok=True)
    parcial = nome_parcial(destino, estacao, pid)
    try:
        with open(_longo(parcial), "wb") as saida:
            saida.write(dados)
            saida.flush()
            os.fsync(saida.fileno())
        _publicar(parcial, destino, tentativas)
    finally:
        _apagar(parcial)


def copiar(origem: Path, destino: Path, es: ES, estacao: str, pid: int) -> None:
    """Cópia intacta, com o mesmo protocolo de `gravar`."""
    destino = Path(destino)
    es.exigir_fora_das_origens(destino)
    if _mesmo_arquivo(Path(origem), destino):
        return
    os.makedirs(_longo(destino.parent), exist_ok=True)
    parcial = nome_parcial(destino, estacao, pid)
    try:
        with open(_longo(origem), "rb") as fonte, open(_longo(parcial), "wb") as saida:
            shutil.copyfileobj(fonte, saida, _BLOCO)
            saida.flush()
            os.fsync(saida.fileno())
        _publicar(parcial, destino, _TENTATIVAS)
    finally:
        _apagar(parcial)


def apagar(caminho: Path, es: ES) -> None:
    """Apaga um arquivo do destino; recusa caminho dentro de origem."""
    caminho = Path(caminho)
    es.exigir_fora_das_origens(caminho)
    _apagar(caminho)


def limpar_parciais(raiz: Path, estacao: str, es: ES | None = None) -> int:
    """Apaga os `.parcial` desta estação abaixo de `raiz`; devolve quantos.

    Não entra em junção nem link: eles levam para fora do destino, e lá o
    rascunho não é nosso para apagar. Com `es`, o que estiver dentro de uma
    origem fica. A consulta é feita só no arquivo que casa com o padrão, e
    não em cada pasta: o destino pode ter milhares delas numa pasta de rede.
    """
    padrao = re.compile(r".+\." + re.escape(_quem(estacao)) + r"\.\d+\.parcial",
                        re.IGNORECASE)
    removidos = 0
    pilha = [_longo(raiz)]
    while pilha:
        try:
            entradas = list(os.scandir(pilha.pop()))
        except OSError:
            continue
        for entrada in entradas:
            try:
                if entrada.is_junction() or entrada.is_symlink():
                    continue
                if entrada.is_dir(follow_symlinks=False):
                    pilha.append(entrada.path)
                elif padrao.fullmatch(entrada.name):
                    if es is not None and es.dentro_de_origem(Path(entrada.path)):
                        evento("parcial_na_origem_mantido")
                        continue
                    os.remove(entrada.path)
                    removidos += 1
            except OSError:
                continue
    if removidos:
        evento("parciais_apagados", quantidade=removidos)
    return removidos
