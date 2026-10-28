"""Log técnico da estação, sem nome de paciente e sem texto de documento.

O log é o canal de volta do teste em campo, então leva o que é técnico
(códigos de evento, contagens, tempos, hashes, rastro de erro) e nada que
identifique alguém. A defesa não depende de quem chama lembrar disso. O
filtro do handler:

- troca por `<redigido>` todo argumento que não seja número, booleano, hash
  hexadecimal ou código em minúsculas;
- confere também o texto da mensagem, porque ela pode ter sido montada antes
  da chamada (f-string, `%`): nas nossas, sai toda palavra que não seja
  código, hash, nome de tipo de exceção ou quadro de rastro
  (`arquivo.py:linha`); nas de terceiros, que formatam o texto da exceção e
  podem trazer texto de documento em minúsculas, sai a mensagem inteira e
  fica o lugar do código que a registrou, a não ser que seja um modelo com
  argumentos e só palavras seguras;
- refaz o rastro de erro sem a mensagem livre e sem o nome de arquivo das
  exceções de E/S, que é onde um caminho com o nome do paciente escaparia.

Quem registra evento usa `evento(codigo, **campos)`: o código e cada campo
passam pelo mesmo filtro.
"""
from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

NOME = "scan_to_text"
REDIGIDO = "<redigido>"
TAMANHO_MAXIMO = 8 * 1024 * 1024
COPIAS = 3
_FORMATO = "%(asctime)s %(levelname)s %(name)s %(process)d %(message)s"
_MARCA = "_scan_to_text"

# Código e hash precisam de ao menos uma letra: texto só de dígitos e
# pontuação pode ser data ("02.04.2026") ou número de documento do paciente.
# O código é uma palavra só, com sublinhado: ponto e hífen deixariam passar
# nome de arquivo ("fulana.silva.pdf") e nome composto. Versão, só com "v".
_CODIGO = re.compile(r"(?=[a-z0-9_]*[a-z])[a-z0-9_]+|v\d+(?:\.\d+)+")
_HASH = re.compile(r"(?=[0-9]*[a-fA-F])[0-9a-fA-F]{8,128}")
_QUADRO = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.py:\d+")
_MARCADOR = re.compile(
    r"%(?:\([^)]*\))?[#0 +\-]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsa%]")
_PALAVRA = re.compile(r"[^\s=,;()\[\]{}'\"]+")


def _eh_seguro(valor: object) -> bool:
    if valor is None or isinstance(valor, (bool, int, float)):
        return True
    if isinstance(valor, str):
        return bool(_CODIGO.fullmatch(valor) or _HASH.fullmatch(valor))
    if isinstance(valor, (list, tuple)):
        return all(_eh_seguro(item) for item in valor)
    return False


def _seguro(valor: object) -> object:
    return valor if _eh_seguro(valor) else REDIGIDO


def _tipos_de_excecao() -> set[str]:
    # Nome de classe de exceção carregada vem do código, nunca do documento.
    # Só é consultado para palavra que falhou nas outras regras, que é rara.
    vistos, pilha = set(), [BaseException]
    while pilha:
        tipo = pilha.pop()
        if tipo not in vistos:
            vistos.add(tipo)
            pilha.extend(type.__subclasses__(tipo))
    return {tipo.__name__ for tipo in vistos}


def _palavra_segura(palavra: str) -> bool:
    palavra = palavra.rstrip(":")
    return (not palavra or palavra == REDIGIDO or _eh_seguro(palavra)
            or bool(_QUADRO.fullmatch(palavra)) or palavra in _tipos_de_excecao())


def _redigir_texto(texto: str) -> str:
    return _PALAVRA.sub(lambda m: m[0] if _palavra_segura(m[0]) else REDIGIDO, texto)


def _redigir_modelo(modelo: str, com_argumentos: bool) -> str:
    """Redige palavra a palavra o texto do modelo; os marcadores `%` ficam."""
    if not com_argumentos:
        return _redigir_texto(modelo)
    partes, inicio = [], 0
    for marcador in _MARCADOR.finditer(modelo):
        partes += [_redigir_texto(modelo[inicio:marcador.start()]), marcador[0]]
        inicio = marcador.end()
    partes.append(_redigir_texto(modelo[inicio:]))
    return "".join(partes)


def _mensagem(erro: BaseException) -> str:
    # A mensagem de exceção é texto livre e pode carregar o que a exceção
    # recebeu (título, caminho). Das de E/S sobram só os números do sistema.
    if isinstance(erro, OSError):
        return f"errno={_seguro(erro.errno)} winerror={_seguro(getattr(erro, 'winerror', None))}"
    if erro.args and all(_eh_seguro(a) for a in erro.args):
        return " ".join(str(a) for a in erro.args)
    return REDIGIDO if erro.args else ""


def _rastro(erro: BaseException) -> str:
    cadeia, vistos = [], set()
    while erro is not None and id(erro) not in vistos:
        vistos.add(id(erro))
        cadeia.append(erro)
        erro = erro.__cause__ or (None if erro.__suppress_context__ else erro.__context__)
    partes = []
    for atual in reversed(cadeia):
        partes.append("Traceback (most recent call last):\n")
        partes.extend(traceback.format_tb(atual.__traceback__))
        tipo = type(atual)
        partes.append(f"{tipo.__module__}.{tipo.__qualname__}: {_mensagem(atual)}\n")
    return "".join(partes).rstrip("\n")


class _Redacao(logging.Filter):
    """Redige mensagem, argumentos e rastro antes de qualquer formatação."""

    def filter(self, record: logging.LogRecord) -> bool:
        nosso = record.name == NOME or record.name.startswith(NOME + ".")
        # Terceiros só de WARNING para cima, mesmo quando a biblioteca
        # baixou o nível do próprio logger.
        if not nosso and record.levelno < logging.WARNING:
            return False
        if isinstance(record.args, Mapping):
            record.args = {chave: _seguro(valor) for chave, valor in record.args.items()}
        elif record.args:
            record.args = tuple(_seguro(a) for a in record.args)
        if not isinstance(record.msg, str):
            if not _eh_seguro(record.msg):
                record.msg, record.args = REDIGIDO, ()
        elif nosso:
            record.msg = _redigir_modelo(record.msg, bool(record.args))
        elif not (record.args and _redigir_modelo(record.msg, True) == record.msg):
            record.msg, record.args = f"{REDIGIDO} {record.filename}:{record.lineno}", ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = _rastro(record.exc_info[1])
        return True


def configurar(pasta_logs: Path, nivel: int = logging.INFO,
               arquivo: str = "scan_to_text.log") -> logging.Logger:
    """Liga o arquivo rotativo de 8 MB no logger raiz e devolve o logger do app.

    `arquivo` separa processos: dois processos rodando o mesmo arquivo
    rotativo falham na rotação no Windows, então o trabalhador usa o seu.
    Chamar de novo troca o handler anterior em vez de duplicar linhas.
    """
    pasta_logs = Path(pasta_logs)
    pasta_logs.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger()
    for antigo in list(raiz.handlers):
        if getattr(antigo, _MARCA, False):
            raiz.removeHandler(antigo)
            antigo.close()
    handler = RotatingFileHandler(pasta_logs / arquivo, maxBytes=TAMANHO_MAXIMO,
                                  backupCount=COPIAS, encoding="utf-8", delay=True)
    setattr(handler, _MARCA, True)
    handler.setFormatter(logging.Formatter(_FORMATO))
    handler.addFilter(_Redacao())
    raiz.addHandler(handler)
    raiz.setLevel(logging.WARNING)
    logger = logging.getLogger(NOME)
    logger.setLevel(nivel)
    return logger


def evento(codigo: str, **campos: object) -> None:
    """Registra `codigo chave=valor ...` em INFO; tudo passa pelo filtro."""
    formato = " ".join(["%s"] + ["%s=%s"] * len(campos))
    argumentos = [codigo]
    for chave, valor in campos.items():
        argumentos += [chave, valor]
    logging.getLogger(NOME).info(formato, *argumentos)
