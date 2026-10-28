"""Configuração da estação: `config.json`.

Fica na pasta do app, por estação, e sobrevive às atualizações. É lido como
`utf-8-sig`, porque o Bloco de Notas do LTSB grava BOM e o BOM colado na
frente da primeira chave faria o JSON parecer outro. Campo que falta, ou com
valor de tipo errado (digitado à mão), volta ao `PADRAO`: um intervalo
ilegível não pode derrubar o ciclo. JSON quebrado não vira padrão em
silêncio: levanta `ConfigInvalida`, porque gravar por cima dele apagaria o
que o médico configurou.
"""
import copy
import ctypes
import json
import math
import os
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

PADRAO = {"versao_config": 1, "origens": [], "destinos": [], "idioma_interface": "pt-BR",
          "idiomas_documentos": ["pt-BR"], "ping_min": 2, "days_back": 7,
          "estabilidade_s": 60, "ocioso_min": 2}

# A ordem é a da resposta: a tela lista os erros sempre na mesma sequência.
ERROS = ("origem_ilegivel", "destino_ilegivel", "destino_em_origem", "origem_em_destino",
         "destino_em_nuvem")

_TENTATIVAS = 5

# Códigos de idioma gravados à mão ou por versões antigas: sem a troca, o
# motor procuraria um pacote de regras que não existe com esse nome.
ALIAS_IDIOMA = {"pt": "pt-BR", "zh": "zh-CN", "zh-Hans": "zh-CN"}


class ConfigInvalida(ValueError):
    pass


# Mínimo e exigência de inteiro de cada campo numérico. A API da tela recusa
# pelos mesmos limites, para o motor nunca receber o que a leitura do arquivo
# trocaria pelo padrão. Estabilidade zero leria o PDF que o scanner ainda está
# gravando; por isso o mínimo é 1 s, e não 0.
LIMITES = {"versao_config": (1, True), "ping_min": (0.1, False), "days_back": (0, True),
           "estabilidade_s": (1, True), "ocioso_min": (0.1, False)}


def numero_valido(chave: str, valor) -> bool:
    minimo, inteiro = LIMITES[chave]
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return False
    if inteiro and not isinstance(valor, int):
        return False
    # O json do Python aceita Infinity e NaN: um intervalo infinito pararia a
    # varredura sem aviso.
    return math.isfinite(valor) and valor >= minimo


def _textos(valor) -> list[str] | None:
    if not isinstance(valor, list):
        return None
    return [v.strip() for v in valor if isinstance(v, str) and v.strip()]


def _saneado(bruto: dict) -> dict:
    cfg = copy.deepcopy(PADRAO)
    cfg.update(bruto)
    for chave in ("origens", "destinos"):
        cfg[chave] = _textos(cfg[chave]) or []
    idiomas = [ALIAS_IDIOMA.get(i, i) for i in _textos(cfg["idiomas_documentos"]) or []]
    cfg["idiomas_documentos"] = list(dict.fromkeys(idiomas)) or list(PADRAO["idiomas_documentos"])
    if not isinstance(cfg["idioma_interface"], str) or not cfg["idioma_interface"].strip():
        cfg["idioma_interface"] = PADRAO["idioma_interface"]
    for chave in LIMITES:
        if not numero_valido(chave, cfg[chave]):
            cfg[chave] = PADRAO[chave]
    return cfg


def ler(caminho: Path) -> dict:
    caminho = Path(caminho)
    if not caminho.is_file():
        return copy.deepcopy(PADRAO)
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as erro:
        raise ConfigInvalida(str(caminho)) from erro
    if not isinstance(bruto, dict):
        raise ConfigInvalida(str(caminho))
    return _saneado(bruto)


def gravar(caminho: Path, cfg: dict) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
    fd, temporario = tempfile.mkstemp(prefix=f".{caminho.name}.", suffix=".parcial",
                                      dir=caminho.parent)
    try:
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        # Antivírus e indexador seguram o arquivo por instantes: a troca é
        # tentada de novo com espera crescente antes de desistir.
        for tentativa in range(_TENTATIVAS):
            try:
                os.replace(temporario, caminho)
                return
            except PermissionError:
                if tentativa == _TENTATIVAS - 1:
                    raise
                time.sleep(0.1 * 2 ** tentativa)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)


def _chave(caminho: str) -> str:
    # Resolve junção, link simbólico e unidade de `subst`, como a E/S segura e
    # a guarda de nuvem: o destino que entra na origem por um atalho é o mesmo
    # aninhamento que a E/S recusaria arquivo por arquivo na hora de gravar.
    try:
        pleno = os.path.realpath(caminho)
    except (OSError, ValueError):
        pleno = os.path.abspath(caminho)
    return os.path.normcase(pleno).rstrip("\\/").casefold()


def mesma_pasta(a: str, b: str) -> bool:
    """A mesma pasta por outro caminho: letra mapeada, maiúsculas, barra no fim, junção."""
    return _chave(para_unc(a.strip())) == _chave(para_unc(b.strip()))


def _dentro(filho: str, pai: str) -> bool:
    f, p = _chave(filho), _chave(pai)
    return f == p or f.startswith(p + os.sep)


def validar(cfg: dict, pasta_legivel: Callable[[Path], bool],
            pasta_gravavel: Callable[[Path], bool],
            em_nuvem: Callable[[Path], bool]) -> list[str]:
    origens, destinos = cfg.get("origens") or [], cfg.get("destinos") or []
    achados = set()
    if any(not pasta_legivel(Path(o)) for o in origens):
        achados.add("origem_ilegivel")
    # O aninhamento vem antes da gravação: testar a gravação cria um arquivo na
    # pasta, e o destino que fica numa origem nunca recebe nada.
    na_origem = set()
    for d in destinos:
        for o in origens:
            if _dentro(d, o):
                achados.add("destino_em_origem")
                na_origem.add(d)
            elif _dentro(o, d):
                achados.add("origem_em_destino")
        if em_nuvem(Path(d)):
            achados.add("destino_em_nuvem")
    if any(not pasta_gravavel(Path(d)) for d in destinos if d not in na_origem):
        achados.add("destino_ilegivel")
    return [e for e in ERROS if e in achados]


# ERROR_CONNECTION_UNAVAIL: conexão lembrada e desligada. O nome remoto vem
# preenchido assim mesmo, e é ele que interessa: a pasta continua sendo aquela.
_OK_REMOTO = (0, 1201)


def _remoto(letra: str) -> str | None:
    mpr = ctypes.WinDLL("mpr")
    obter = mpr.WNetGetConnectionW
    obter.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    obter.restype = wintypes.DWORD
    tamanho = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(tamanho.value)
    if obter(letra, buffer, ctypes.byref(tamanho)) in _OK_REMOTO and buffer.value:
        return buffer.value
    return None


def para_unc(caminho: str) -> str:
    if len(caminho) < 2 or caminho[1] != ":" or not caminho[0].isalpha():
        return caminho
    remoto = _remoto(caminho[:2].upper())
    if not remoto:
        return caminho
    resto = caminho[2:].replace("/", "\\").strip("\\")
    base = remoto.rstrip("\\")
    return f"{base}\\{resto}" if resto else base
