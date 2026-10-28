"""Guarda de rede em tempo de execução: num processo à parte, porque o gancho não sai.

O processo de teste abre uma porta no loopback, que faz o papel de um programa
alheio, como um proxy local; o processo guardado nasce com as variáveis de
proxy apontando para ela.
"""
import json
import os
import socket
import subprocess
import sys

_INICIO = r'''
import asyncio, json, os, socket, sys, urllib.request
sys.path[:0] = json.loads(os.environ["CAMINHOS_TESTE"])
from scan_to_text import rede
rede.ligar()
saida = {}
def tentar(nome, f):
    try:
        f()
        saida[nome] = "passou"
    except rede.RedeBloqueada:
        saida[nome] = "bloqueada"
    except OSError as e:
        saida[nome] = type(e).__name__
def assincrono(corrotina):
    return lambda: asyncio.run(asyncio.wait_for(corrotina(), 5))
alheia = ("127.0.0.1", int(os.environ["PORTA_ALHEIA"]))
servidor = socket.socket()
servidor.bind(("127.0.0.1", 0))
servidor.listen(8)
'''
_FIM = "\nprint(json.dumps(saida))\n"


def _rodar(corpo: str) -> dict:
    with socket.socket() as alheio:
        alheio.bind(("127.0.0.1", 0))
        alheio.listen(8)
        porta = alheio.getsockname()[1]
        proxy = f"http://127.0.0.1:{porta}"
        env = {k: v for k, v in os.environ.items() if k.upper() != "NO_PROXY"}
        env.update(CAMINHOS_TESTE=json.dumps(sys.path), PORTA_ALHEIA=str(porta),
                   HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
        feito = subprocess.run([sys.executable, "-c", _INICIO + corpo + _FIM],
                               capture_output=True, text=True, env=env, timeout=60)
    assert feito.returncode == 0, feito.stderr
    return json.loads(feito.stdout)


def test_so_o_proprio_computador_passa():
    saida = _rodar(r'''
tentar("fora", lambda: socket.create_connection(("192.0.2.10", 80), timeout=2))
tentar("nome", lambda: socket.getaddrinfo("example.com", 443))
tentar("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.10", 53)))
tentar("local", lambda: socket.create_connection(servidor.getsockname(), timeout=2).close())
tentar("localhost", lambda: socket.getaddrinfo("localhost", 80))
receptor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receptor.bind(("127.0.0.1", 0))
tentar("udp_local", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", receptor.getsockname()))
tentar("par", lambda: [s.close() for s in socket.socketpair()])
''')
    assert saida == {"fora": "bloqueada", "nome": "bloqueada", "udp": "bloqueada",
                     "local": "passou", "localhost": "passou", "udp_local": "passou",
                     "par": "passou"}


def test_porta_de_outro_programa_no_loopback_e_recusada():
    saida = _rodar(r'''
tentar("tcp", lambda: socket.create_connection(alheia, timeout=2).close())
tentar("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", alheia))
saida["proxies"] = sorted(urllib.request.getproxies())
''')
    assert saida == {"tcp": "bloqueada", "udp": "bloqueada", "proxies": ["no"]}


def test_api_local_responde_com_a_guarda_ligada():
    saida = _rodar(r'''
from scan_to_text.servidor import Servidor
async def app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})
api = Servidor(app)
api.iniciar()
try:
    tentar("api", lambda: urllib.request.urlopen(api.origem + "/", timeout=5).read())
finally:
    api.parar()
''')
    assert saida == {"api": "passou"}


def test_laco_assincrono_passa_pela_guarda():
    # 0.0.0.0 não é o loopback e o sistema o recusa sem pacote: se a guarda
    # falhar, o erro é do sistema, e nada sai do computador.
    saida = _rodar(r'''
async def tcp_local():
    _, escritor = await asyncio.open_connection(*servidor.getsockname())
    escritor.close()
async def tcp_fora():
    await asyncio.open_connection("0.0.0.0", 80)
async def udp_fora():
    transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("0.0.0.0", 9))
    transporte.close()
async def envio_fora():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        await asyncio.get_running_loop().sock_sendto(s, b"x", ("0.0.0.0", 9))
for nome, corrotina in (("tcp_local", tcp_local), ("tcp_fora", tcp_fora),
                        ("udp_fora", udp_fora), ("envio_fora", envio_fora)):
    tentar(nome, assincrono(corrotina))
''')
    assert saida == {"tcp_local": "passou", "tcp_fora": "bloqueada", "udp_fora": "bloqueada",
                     "envio_fora": "bloqueada"}


def test_resolucao_de_nome_por_qualquer_funcao():
    # Um gancho posto depois do da guarda para a chamada antes do sistema: se a
    # guarda deixar passar, o teste não consulta DNS nenhum.
    saida = _rodar(r'''
class ChegouAoSistema(Exception):
    pass
def sentinela(nome, args):
    if nome in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
        raise ChegouAoSistema(nome)
sys.addaudithook(sentinela)
def resolver(nome, f):
    try:
        f()
        saida[nome] = "passou"
    except rede.RedeBloqueada:
        saida[nome] = "bloqueada"
    except ChegouAoSistema:
        saida[nome] = "chegou_ao_sistema"
resolver("gethostbyname", lambda: socket.gethostbyname("example.com"))
resolver("gethostbyname_ex", lambda: socket.gethostbyname_ex("example.com"))
resolver("gethostbyaddr", lambda: socket.gethostbyaddr("192.0.2.10"))
resolver("getnameinfo", lambda: socket.getnameinfo(("192.0.2.10", 80), 0))
resolver("localhost", lambda: socket.gethostbyname("localhost"))
''')
    assert saida == {"gethostbyname": "bloqueada", "gethostbyname_ex": "bloqueada",
                     "gethostbyaddr": "bloqueada", "getnameinfo": "bloqueada",
                     "localhost": "chegou_ao_sistema"}
