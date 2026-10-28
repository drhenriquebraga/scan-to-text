"""E/S segura: a origem é somente leitura, e isto é garantido aqui.

Todo caminho que o app vai escrever passa por `ES.exigir_fora_das_origens`.
A comparação é feita duas vezes, porque uma só deixa buracos:

1. pelo texto normalizado (`normalizar_caminho`): sem `\\\\?\\`, letra mapeada
   trocada pelo UNC, junção e link resolvidos, maiúsculas indiferentes;
2. pela identidade da pasta no disco (volume e número do arquivo) de cada
   ancestral que existe, que pega o que o texto não desvenda, como o
   compartilhamento da própria estação apontando para uma pasta local.

A cópia para a pasta temporária ganha nome neutro `<sha256>.<ext>` antes de
qualquer biblioteca de terceiro tocar no arquivo: o nome de origem pode ser o
nome do paciente, e há biblioteca que registra nome de arquivo.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from scan_to_text.log import evento

_BLOCO = 1024 * 1024
# As extensões que a leitura aceita (`ler/recipientes.py`), que escolhe o
# formato pela extensão da cópia; um teste confere que as duas andam juntas.
_EXTENSOES = frozenset({".pdf", ".docx", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp",
                        ".gif", ".webp", ".heic", ".heif", ".zip"})
_LETRA = re.compile(r"[A-Za-z]:(?:\\|$)")
# `\??\` é o prefixo do espaço de nomes do NT, que o CreateFileW também aceita.
_PREFIXOS_UNC = ("\\\\?\\unc\\", "\\\\.\\unc\\", "\\??\\unc\\")
_PREFIXOS = ("\\\\?\\", "\\\\.\\", "\\??\\")
# Origem que não respondeu (servidor fora do ar) só é consultada de novo
# depois deste intervalo, para não pôr um tempo-limite de rede em cada escrita.
_NOVA_CONSULTA_S = 60.0


class OrigemIntocavel(Exception):
    """Escrita pedida dentro de uma origem.

    A mensagem é só o código: o caminho pode ter nome de paciente e iria
    parar no rastro do log. O caminho fica no atributo.
    """

    def __init__(self, caminho: Path):
        super().__init__("origem_intocavel")
        self.caminho = caminho


def _unc_da_unidade(unidade: str) -> str | None:
    """UNC de uma letra mapeada (`Z:`), pelo `WNetGetConnectionW`."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    funcao = ctypes.WinDLL("mpr").WNetGetConnectionW
    funcao.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    funcao.restype = wintypes.DWORD
    tamanho = wintypes.DWORD(1024)
    for _ in range(2):
        buffer = ctypes.create_unicode_buffer(tamanho.value)
        codigo = funcao(unidade, buffer, ctypes.byref(tamanho))
        if codigo != 234:  # ERROR_MORE_DATA: o tamanho certo voltou em `tamanho`
            break
    # 1201 (ERROR_CONNECTION_UNAVAIL) é conexão persistente ainda não
    # reconectada: o UNC continua valendo para comparar.
    if codigo in (0, 1201) and buffer.value:
        return buffer.value
    return None


def _sem_prefixo(caminho: str) -> str:
    baixo = caminho.lower()
    for prefixo in _PREFIXOS_UNC:
        if baixo.startswith(prefixo):
            return "\\\\" + caminho[len(prefixo):]
    for prefixo in _PREFIXOS:
        if baixo.startswith(prefixo) and _LETRA.match(caminho[len(prefixo):]):
            return caminho[len(prefixo):]
    return caminho


def _resolver(caminho: str) -> str:
    # Resolve junção e link, também no trecho que existe de um caminho novo.
    return os.path.realpath(caminho)


def _sem_fluxo(caminho: str) -> str:
    # `pasta::$INDEX_ALLOCATION` e `arquivo:fluxo` apontam para a mesma pasta
    # ou arquivo; o nome do fluxo alternativo não conta para a comparação.
    unidade, resto = os.path.splitdrive(caminho)
    partes = [parte.split(":")[0] for parte in resto.split("\\")]
    return unidade + "\\".join(partes)


def _normalizar(caminho: Path | str,
                unidade_para_unc: Callable[[str], str | None] | None) -> str:
    texto = _sem_prefixo(os.fspath(caminho).replace("/", "\\"))
    if texto.startswith(_PREFIXOS):
        # Caminho de volume (`\\?\Volume{...}\`): só a resolução diz qual é.
        texto = _sem_prefixo(_resolver(texto))
    texto = _sem_prefixo(os.path.abspath(texto))
    if _LETRA.match(texto):
        unc = (unidade_para_unc or _unc_da_unidade)(texto[:2].upper())
        if unc:
            texto = unc.rstrip("\\") + "\\" + texto[3:]
    texto = _sem_fluxo(_sem_prefixo(_resolver(texto)))
    unidade, resto = os.path.splitdrive(texto)
    resto = resto.rstrip("\\") or "\\"
    return unidade + resto


def normalizar_caminho(caminho: Path,
                       unidade_para_unc: Callable[[str], str | None] | None = None) -> str:
    """Forma única de comparar caminhos no Windows.

    Absoluto, sem `\\\\?\\` (e `\\\\?\\UNC\\` vira `\\\\`), letra mapeada trocada
    pelo UNC (`WNetGetConnectionW` por padrão), junção resolvida, casefold.
    `unidade_para_unc` recebe a unidade como `"Z:"` e devolve o UNC da
    raiz (`\\\\servidor\\pasta`) ou `None` quando a letra não é mapeada.
    """
    return _normalizar(caminho, unidade_para_unc).casefold()


def _identidade(caminho: str) -> tuple[int, int] | None:
    try:
        estado = os.stat(caminho)
    except (OSError, ValueError):
        return None
    return (estado.st_dev, estado.st_ino) if estado.st_ino else None


def _extensao(arquivo: Path) -> str:
    # Só extensão de lista fechada entra no nome neutro: o que vem depois do
    # último ponto é texto de fora e pode ser nome de gente ("scan.Fulana").
    sufixo = arquivo.suffix.lower()
    return sufixo if sufixo in _EXTENSOES else ""


def _apagar(caminho: str | Path) -> None:
    try:
        os.remove(caminho)
    except FileNotFoundError:
        pass


class ES:
    def __init__(self, origens: Sequence[Path],
                 unidade_para_unc: Callable[[str], str | None] | None = None):
        self._unidade_para_unc = unidade_para_unc
        self._origens = [_normalizar(o, unidade_para_unc) for o in origens]
        self._textos = [o.casefold() for o in self._origens]
        self._identidades: dict[str, tuple[int, int]] = {}
        self._consultadas: dict[str, float] = {}

    def _identidades_das_origens(self) -> set[tuple[int, int]]:
        agora = time.monotonic()
        for origem in self._origens:
            if origem in self._identidades:
                continue
            ultima = self._consultadas.get(origem)
            if ultima is not None and agora - ultima < _NOVA_CONSULTA_S:
                continue
            self._consultadas[origem] = agora
            identidade = _identidade(origem)
            if identidade:
                self._identidades[origem] = identidade
        return set(self._identidades.values())

    def dentro_de_origem(self, caminho: Path) -> bool:
        normal = _normalizar(caminho, self._unidade_para_unc)
        texto = normal.casefold()
        for origem in self._textos:
            base = origem if origem.endswith("\\") else origem + "\\"
            if texto == origem or texto.startswith(base):
                return True
        identidades = self._identidades_das_origens()
        if not identidades:
            return False
        atual = normal
        while True:
            if _identidade(atual) in identidades:
                return True
            acima = os.path.dirname(atual)
            if acima == atual:
                return False
            atual = acima

    def exigir_fora_das_origens(self, caminho: Path) -> None:
        if self.dentro_de_origem(caminho):
            evento("escrita_recusada_na_origem")
            raise OrigemIntocavel(Path(caminho))

    def copiar_para_temp(self, arquivo: Path, pasta_temp: Path) -> tuple[Path, str]:
        """Copia para `<pasta_temp>/<sha256>.<ext minúscula>`; devolve (cópia, sha256).

        O SHA-256 é o dos bytes gravados na cópia local, que é a identidade do
        arquivo. A origem é aberta só para leitura.
        """
        arquivo, pasta_temp = Path(arquivo), Path(pasta_temp)
        self.exigir_fora_das_origens(pasta_temp)
        pasta_temp.mkdir(parents=True, exist_ok=True)
        resumo = hashlib.sha256()
        tamanho = 0
        with open(arquivo, "rb") as fonte:
            descritor, rascunho = tempfile.mkstemp(suffix=".parcial", dir=pasta_temp)
            try:
                with os.fdopen(descritor, "wb") as saida:
                    while bloco := fonte.read(_BLOCO):
                        resumo.update(bloco)
                        saida.write(bloco)
                        tamanho += len(bloco)
                sha256 = resumo.hexdigest()
                copia = pasta_temp / (sha256 + _extensao(arquivo))
                if copia.exists():
                    # Mesmo nome é mesmo conteúdo; a cópia antiga pode estar
                    # aberta por quem lê, e substituir falharia no Windows.
                    _apagar(rascunho)
                else:
                    os.replace(rascunho, copia)
            except BaseException:
                _apagar(rascunho)
                raise
        evento("copia_temporaria", sha256=sha256, bytes=tamanho)
        return copia, sha256
