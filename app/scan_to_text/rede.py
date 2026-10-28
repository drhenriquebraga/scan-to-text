"""Guarda de rede em tempo de execução: só o próprio computador.

Um gancho de auditoria do Python recusa conexão, envio e resolução de nome
para fora de 127.0.0.1 antes que aconteçam. No próprio computador, conexão e
envio só vão a porta que este processo abriu: a de outro programa, como um
proxy local, levaria o pedido para fora. As variáveis de proxy são apagadas, e
`NO_PROXY=*` faz as bibliotecas HTTP ignorarem também o proxy do sistema.

No Windows, o laço Proactor do asyncio conecta e envia sem evento de
auditoria, então o `connect` e o `sendto` dele passam pela mesma checagem.
A API local da tela escuta em 127.0.0.1 e continua funcionando.
O gancho não sai depois de ligado: vale até o processo terminar.
"""
import ipaddress
import os
import sys
import weakref

_NOMES_LOCAIS = frozenset({"localhost", ""})
_LIGADO = {"sim": False}
_VARIAVEIS_DE_PROXY = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_RESOLUCAO = ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
              "socket.getnameinfo")
# Soquetes que este processo prendeu a uma porta. A porta é lida na hora do
# connect, porque o bind na porta 0 só a conhece depois de feito.
_PROPRIOS: set = set()


class RedeBloqueada(PermissionError):
    pass


def _local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str):
        return False
    if host.lower() in _NOMES_LOCAIS:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _guardar_soquete(sock) -> None:
    try:
        _PROPRIOS.add(weakref.ref(sock, _PROPRIOS.discard))
    except TypeError:
        # `_socket.socket` cru não aceita weakref: a porta dele fica de fora, e
        # o connect a ela é recusado.
        pass


def _porta_propria(porta) -> bool:
    # Cópia antes de iterar: outra thread pode prender um soquete agora.
    for ref in _PROPRIOS.copy():
        sock = ref()
        try:
            nome = sock.getsockname() if sock is not None else None
        except OSError:
            continue
        if isinstance(nome, tuple) and len(nome) > 1 and nome[1] == porta:
            return True
    return False


def _checar_destino(endereco) -> None:
    if not isinstance(endereco, tuple) or not endereco:
        return
    if not (_local(endereco[0]) and len(endereco) > 1 and _porta_propria(endereco[1])):
        raise RedeBloqueada("rede_bloqueada")


def _gancho(nome: str, args: tuple) -> None:
    if nome == "socket.bind":
        _guardar_soquete(args[0])
    elif nome in ("socket.connect", "socket.sendto"):
        _checar_destino(args[1] if len(args) > 1 else None)
    elif nome in _RESOLUCAO and args:
        alvo = args[0]
        if isinstance(alvo, tuple):  # getnameinfo recebe o endereço inteiro
            alvo = alvo[0] if alvo else None
        if not _local(alvo):
            raise RedeBloqueada("rede_bloqueada")


def _guardar_proactor() -> None:
    if sys.platform != "win32":
        return
    from asyncio import windows_events

    proactor = windows_events.IocpProactor
    conectar, enviar = proactor.connect, proactor.sendto

    def connect(self, conn, address):
        _checar_destino(address)
        return conectar(self, conn, address)

    def sendto(self, conn, buf, flags=0, addr=None):
        if addr is not None:
            _checar_destino(addr)
        return enviar(self, conn, buf, flags, addr)

    proactor.connect, proactor.sendto = connect, sendto


def ligar() -> None:
    if _LIGADO["sim"]:
        return
    for variavel in _VARIAVEIS_DE_PROXY:
        os.environ.pop(variavel, None)
        os.environ.pop(variavel.lower(), None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    _guardar_proactor()
    sys.addaudithook(_gancho)
    _LIGADO["sim"] = True
