"""Abertura do app: textos, console, instância única, servidor local e a entrada."""
import ctypes
import json
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import pytest

from scan_to_text import __main__ as entrada
from scan_to_text import principal
from scan_to_text.servidor import Servidor
from scan_to_text.tela import api


class MotorFalso:
    def __init__(self):
        self.estados = ["parado", "ocioso", "parado"]
        self.chamadas = []

    def estado(self):
        return {"estado": self.estados[0], "avisos": ["disco_cheio", "aviso.ram_baixa", "xyz",
                                                      "origem_inacessivel"],
                "pastas": {"origem_inacessivel": ["\\\\servidor\\scanner"]}}

    def start(self):
        self.chamadas.append("start")
        self.estados.pop(0)

    def stop(self):
        self.chamadas.append("stop")
        self.estados.pop(0)

    def config(self):
        return {"origens": [], "destinos": [], "idioma_interface": "pt-BR"}


def test_idioma_sem_traducao_cai_no_pt_br():
    assert principal.textos("ja") == principal.textos("pt-BR")
    assert "{versao}" in principal.textos("pt-BR")["console"]["titulo"]


def test_console_liga_desliga_e_sai():
    motor, saida = MotorFalso(), []
    comandos = iter(["", "", "S"])
    principal.console(motor, principal.textos("pt-BR"), ler=lambda _: next(comandos),
                      escrever=saida.append)
    assert motor.chamadas == ["start", "stop"]
    texto = "\n".join(saida)
    assert "Um disco ficou cheio" in texto and "Pouca memória livre" in texto and "xyz" in texto
    assert "\\\\servidor\\scanner" in texto and "{pasta}" not in texto
    assert "Avise o suporte" in texto


def test_console_sai_no_fim_da_entrada():
    def fim(_):
        raise EOFError
    principal.console(MotorFalso(), principal.textos("pt-BR"), ler=fim, escrever=lambda _: None)


def test_uma_instancia_por_sessao():
    nome = "Local\\Scan-to-Text-teste-" + secrets.token_hex(4)
    assert principal.unica_instancia(nome)
    assert not principal.unica_instancia(nome)


def test_servidor_local_responde_so_com_o_token():
    token = secrets.token_urlsafe(16)
    permitidas = set()
    servidor = Servidor(api.criar_app(MotorFalso(), token, permitidas))
    permitidas.add(servidor.origem)
    servidor.iniciar()
    try:
        pedido = urllib.request.Request(servidor.origem + "/api/estado", headers={"X-Token": token})
        with urllib.request.urlopen(pedido, timeout=5) as resposta:
            assert json.loads(resposta.read())["estado"] == "parado"
        try:
            urllib.request.urlopen(servidor.origem + "/api/estado", timeout=5)
            raise AssertionError("respondeu sem token")
        except urllib.error.HTTPError as erro:
            assert erro.code == 403
    finally:
        servidor.parar()


# a abertura que falha não deixa o motor ligado -----------------------------------

@pytest.fixture
def abertura(monkeypatch, tmp_path):
    """`executar` sem janela, sem rede, sem mutex e sem o motor de verdade."""
    registro, motores = [], []

    class MotorDeMentira:
        def __init__(self, *_a, **_k):
            self.ligado = False

        def config(self):
            return {"idioma_interface": "pt-BR"}

        def abrir(self):
            self.ligado = True
            registro.append("motor.abrir")

        def fechar(self, espera_s=None):
            self.ligado = False
            registro.append("motor.fechar")

    def fabrica(*a, **k):
        motores.append(MotorDeMentira(*a, **k))
        return motores[-1]

    class ServidorDeMentira:
        origem, porta, falha = "http://127.0.0.1:1", 1, None

        def __init__(self, _app):
            pass

        def iniciar(self):
            if ServidorDeMentira.falha is not None:
                raise ServidorDeMentira.falha

        def parar(self):
            registro.append("servidor.parar")

    monkeypatch.setattr(principal.rede, "ligar", lambda: None)
    monkeypatch.setattr(principal.log, "configurar", lambda *a, **k: None)
    monkeypatch.setattr(principal, "pasta_dados", lambda: tmp_path / "dados")
    monkeypatch.setattr(principal, "unica_instancia", lambda nome=principal.MUTEX: True)
    monkeypatch.setattr(principal.preflight, "preflight", lambda: {
        "degrau_tela": "console", "windows_build": 0, "ram_livre_gb": 1, "disco_livre_gb": 1,
        "avisos": []})
    monkeypatch.setattr(principal, "MotorApp", fabrica)
    monkeypatch.setattr(principal.api, "criar_app", lambda motor, token, permitidas: object())
    monkeypatch.setattr(principal, "Servidor", ServidorDeMentira)
    monkeypatch.setattr(principal, "_avisar_falha", lambda t: registro.append(
        f"caixa motor_ligado={motores[-1].ligado}"))
    monkeypatch.setattr(principal, "_mostrar", lambda *a, **k: registro.append("_mostrar"))
    return registro, ServidorDeMentira, tmp_path / "dados"


def test_servidor_que_nao_sobe_nao_deixa_o_motor_ligado(abertura):
    registro, servidor, _dados = abertura
    servidor.falha = TimeoutError("api_nao_subiu")
    assert principal.executar() == 1
    assert "motor.fechar" in registro
    assert registro[-1] == "caixa motor_ligado=False"  # a caixa vem depois do fechamento


def test_agente_que_nao_grava_fecha_o_motor_e_avisa(abertura, monkeypatch):
    registro, _servidor, _dados = abertura

    def preso(*_a):
        raise PermissionError(13, "agente.json preso")

    monkeypatch.setattr(principal, "_gravar_agente", preso)
    assert principal.executar() == 1
    assert "motor.fechar" in registro and "servidor.parar" in registro
    assert registro[-1] == "caixa motor_ligado=False"


def test_abertura_normal_liga_depois_da_api_e_fecha_no_fim(abertura):
    registro, _servidor, dados = abertura
    assert principal.executar() == 0
    assert registro.index("motor.abrir") < registro.index("_mostrar") < registro.index(
        "motor.fechar")
    assert "servidor.parar" in registro
    assert not (dados / principal.AGENTE).exists()


# fechar o console no X roda a sequência de fechamento -----------------------------

_FILHO_DO_CONSOLE = """
import sys, time
from pathlib import Path
from scan_to_text import principal
marca = Path(sys.argv[1])
principal._ao_fechar_console(lambda: marca.with_suffix(".fechou").write_text("ok"))
marca.with_suffix(".inicio").write_text("ok")
while True:
    time.sleep(0.05)
"""


class _Coord(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _Inicio(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                *[(n, wintypes.DWORD) for n in ("dwX", "dwY", "dwXSize", "dwYSize",
                                                "dwXCountChars", "dwYCountChars",
                                                "dwFillAttribute", "dwFlags")],
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _InicioEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _Inicio), ("lpAttributeList", ctypes.c_void_p)]


class _Processo(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _no_pseudoconsole(comando: str, marca: Path) -> None:
    """Roda `comando` num pseudoconsole (sem janela) e fecha o console como o X."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreatePseudoConsole.argtypes = (_Coord, wintypes.HANDLE, wintypes.HANDLE,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    k32.CreatePseudoConsole.restype = ctypes.c_long
    k32.ClosePseudoConsole.argtypes = (wintypes.HANDLE,)
    k32.CreatePipe.argtypes = (ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
                               ctypes.c_void_p, wintypes.DWORD)
    k32.InitializeProcThreadAttributeList.argtypes = (ctypes.c_void_p, wintypes.DWORD,
                                                      wintypes.DWORD,
                                                      ctypes.POINTER(ctypes.c_size_t))
    k32.UpdateProcThreadAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                              ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                              ctypes.c_void_p)
    k32.CreateProcessW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                   ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
                                   ctypes.POINTER(_Processo))
    k32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ent_r, ent_w, sai_r, sai_w = (wintypes.HANDLE() for _ in range(4))
    assert k32.CreatePipe(ctypes.byref(ent_r), ctypes.byref(ent_w), None, 0)
    assert k32.CreatePipe(ctypes.byref(sai_r), ctypes.byref(sai_w), None, 0)
    console = wintypes.HANDLE()
    assert k32.CreatePseudoConsole(_Coord(80, 25), ent_r, sai_w, 0, ctypes.byref(console)) == 0
    k32.CloseHandle(ent_r)
    k32.CloseHandle(sai_w)

    def drenar():
        buf, lido = ctypes.create_string_buffer(4096), wintypes.DWORD()
        while k32.ReadFile(sai_r, buf, 4096, ctypes.byref(lido), None) and lido.value:
            pass

    threading.Thread(target=drenar, daemon=True).start()
    tamanho = ctypes.c_size_t()
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(tamanho))
    lista = ctypes.create_string_buffer(tamanho.value)
    assert k32.InitializeProcThreadAttributeList(lista, 1, 0, ctypes.byref(tamanho))
    assert k32.UpdateProcThreadAttribute(lista, 0, 0x00020016, console,  # PSEUDOCONSOLE
                                         ctypes.sizeof(wintypes.HANDLE), None, None)
    inicio = _InicioEx()
    inicio.StartupInfo.cb = ctypes.sizeof(_InicioEx)
    inicio.lpAttributeList = ctypes.cast(lista, ctypes.c_void_p)
    processo = _Processo()
    assert k32.CreateProcessW(None, ctypes.create_unicode_buffer(comando), None, None, False,
                              0x00080000, None, None, ctypes.byref(inicio),  # EXTENDED_STARTUPINFO
                              ctypes.byref(processo))
    try:
        fim = time.monotonic() + 60
        while not marca.with_suffix(".inicio").exists():
            assert time.monotonic() < fim, "o filho não começou"
            assert k32.WaitForSingleObject(processo.hProcess, 0) != 0, "o filho saiu antes"
            time.sleep(0.05)
        k32.ClosePseudoConsole(console)  # o X do console: CTRL_CLOSE_EVENT
        k32.WaitForSingleObject(processo.hProcess, 15000)
    finally:
        k32.CloseHandle(processo.hProcess)
        k32.CloseHandle(processo.hThread)


def test_fechar_o_console_no_x_roda_o_fechamento(tmp_path):
    filho = tmp_path / "filho.py"
    filho.write_text(_FILHO_DO_CONSOLE, encoding="utf-8")
    marca = tmp_path / "marca"
    _no_pseudoconsole(f'"{sys.executable}" "{filho}" "{marca}"', marca)
    assert marca.with_suffix(".fechou").exists()


def test_ctrl_c_segue_para_o_tratador_padrao():
    chamadas = []
    tratar = principal._tratador_do_console(lambda: chamadas.append(1))
    assert tratar(0) is False and tratar(1) is False  # Ctrl+C e Ctrl+Break
    assert chamadas == []
    assert tratar(2) is True and chamadas == [1]


def test_entrada_sem_stdout_nao_quebra(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    assert entrada.main(["preflight"]) == 0


def test_entrada_sem_argumento_abre_o_app(monkeypatch):
    monkeypatch.setattr(principal, "executar", lambda: 7)
    assert entrada.main([]) == 7
