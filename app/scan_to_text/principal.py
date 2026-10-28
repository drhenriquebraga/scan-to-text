"""Abre o app: uma instância por estação, preflight, motor, API local e tela.

Degraus da tela, como o preflight escolher:
  * janela: bloqueia até o X; o fechamento roda a sequência de encerramento
    do motor;
  * navegador: Edge ou Chrome em modo app, mais um console visível. Quem diz
    que a página fechou é o batimento, porque o F5 também fecha a
    página e não pode encerrar o app;
  * console: modo texto, START e STOP pelo teclado.

A porta e o token da API ficam em `agente.json`, no perfil de quem roda.
Uma segunda abertura do app não abre outro motor: traz a janela que
já existe para a frente.

O START automático vem depois da API e do `agente.json`: se a tela não
sobe, nada foi lido. O fechamento (motor, API e `agente.json`) roda uma vez
só, no fim normal, na falha da abertura ou quando o console é fechado, e a
caixa de falha só aparece depois dele.
"""
import ctypes
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from scan_to_text import log, preflight, rede
from scan_to_text.log import evento
from scan_to_text.motor import VERSAO, MotorApp
from scan_to_text.servidor import Servidor
from scan_to_text.tela import api, janela

MUTEX = "Local\\Scan-to-Text"
AGENTE = "agente.json"
PASTA_I18N = Path(__file__).resolve().parent / "tela" / "web" / "i18n"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_CP_UTF8 = 65001
_MB_ICONWARNING = 0x30
_PASSO_S = 1.0
# CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT e CTRL_SHUTDOWN_EVENT: sem tratador, o
# Windows encerra o processo sem passar pelo `finally`.
_CTRL_FECHAR = (2, 5, 6)
# O Windows encerra o processo cerca de 5 s depois de fechar o console.
PRAZO_DO_CONSOLE_S = 4.0
ESPERA_STOP_NO_CONSOLE_S = 3.0

_TRATADOR = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.SetConsoleCtrlHandler.argtypes = (_TRATADOR, wintypes.BOOL)
_k32.SetConsoleCtrlHandler.restype = wintypes.BOOL
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_u32.FindWindowW.restype = wintypes.HWND
_u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_u32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_u32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)

# O mutex vale enquanto o handle existe: guardado até o processo terminar.
_MUTEXES: list[int] = []
# O tratador do console é chamado pelo Windows: a referência o mantém vivo.
_TRATADORES: list = []


def pasta_dados() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Scan-to-Text"


def estacao() -> str:
    return os.environ.get("COMPUTERNAME") or "estacao"


def unica_instancia(nome: str = MUTEX) -> bool:
    """True se este é o único processo do app nesta sessão do Windows."""
    handle = _k32.CreateMutexW(None, False, nome)
    erro = ctypes.get_last_error()
    if not handle:
        return True  # sem como saber, abre: dois motores só disputam as travas
    _MUTEXES.append(handle)
    return erro != _ERROR_ALREADY_EXISTS


def textos(idioma: str) -> dict:
    """Os textos da tela no idioma pedido, com o pt-BR por baixo do que faltar."""
    base = json.loads((PASTA_I18N / "pt-BR.json").read_text(encoding="utf-8-sig"))
    arquivo = PASTA_I18N / f"{idioma}.json"
    if idioma != "pt-BR" and arquivo.is_file():
        for secao, valores in json.loads(arquivo.read_text(encoding="utf-8-sig")).items():
            if isinstance(valores, dict) and isinstance(base.get(secao), dict):
                base[secao] = {**base[secao], **valores}
            else:
                base[secao] = valores
    return base


def console(motor, t: dict, ler=input, escrever=print) -> None:
    """Modo texto: Enter liga e desliga, S sai."""
    c = t["console"]
    escrever(c["titulo"].format(versao=VERSAO))
    while True:
        e = motor.estado()
        escrever(c["estado"].format(estado=t["estado"].get(e["estado"], e["estado"])))
        pastas = e.get("pastas") or {}
        for aviso in e["avisos"]:
            chave = aviso.removeprefix("aviso.")
            modelo = t["aviso"].get(chave)
            if modelo is None:
                escrever("  - " + t["aviso"]["desconhecido"].format(codigo=aviso))
                continue
            pasta = ", ".join(pastas.get(aviso) or []) or t["aviso"]["uma_pasta"]
            acao = t["aviso"].get(chave + "_acao")
            escrever("  - " + modelo.replace("{pasta}", pasta) + (" " + acao if acao else ""))
        try:
            comando = ler(c["menu"]).strip().casefold()
        except EOFError:
            return
        if comando == c["sair"]:
            return
        if e["estado"] == "parado":
            motor.start()
        else:
            motor.stop()


def _abrir_console(ao_fechar: Callable[[], None]) -> None:
    """O app roda sem console (pythonw): o modo texto e o navegador pedem um.

    Fechar esse console no X, sair da sessão ou desligar roda `ao_fechar`.
    """
    if not _k32.GetConsoleWindow():
        _k32.AllocConsole()
        _k32.SetConsoleOutputCP(_CP_UTF8)
        _k32.SetConsoleCP(_CP_UTF8)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    _ao_fechar_console(ao_fechar)


def _tratador_do_console(ao_fechar: Callable[[], None]) -> Callable[[int], bool]:
    def tratar(evento_do_console: int) -> bool:
        if evento_do_console not in _CTRL_FECHAR:
            return False  # Ctrl+C e Ctrl+Break seguem para o tratador padrão
        # O processo termina quando o tratador volta: o fechamento roda aqui,
        # dentro do prazo que o Windows dá.
        fio = threading.Thread(target=ao_fechar, daemon=True)
        fio.start()
        fio.join(PRAZO_DO_CONSOLE_S)
        return True
    return tratar


def _ao_fechar_console(ao_fechar: Callable[[], None]) -> None:
    tratador = _TRATADOR(_tratador_do_console(ao_fechar))
    _TRATADORES.append(tratador)
    _k32.SetConsoleCtrlHandler(tratador, True)


def _gravar_agente(pasta: Path, porta: int, token: str) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / AGENTE).write_text(json.dumps({"porta": porta, "token": token, "pid": os.getpid()}),
                                encoding="utf-8")


def _apagar_agente(pasta: Path) -> None:
    arquivo = pasta / AGENTE
    try:
        if json.loads(arquivo.read_text(encoding="utf-8")).get("pid") == os.getpid():
            arquivo.unlink()
    except (OSError, ValueError):
        pass


def trazer_para_frente(pasta: Path) -> None:
    janela_aberta = _u32.FindWindowW(None, janela.TITULO)
    if janela_aberta:
        _u32.ShowWindow(janela_aberta, _SW_RESTORE)
        _u32.SetForegroundWindow(janela_aberta)
        return
    try:
        agente = json.loads((pasta / AGENTE).read_text(encoding="utf-8"))
        janela.abrir(f"http://127.0.0.1:{int(agente['porta'])}/#t={agente['token']}",
                     "navegador", lambda: None)
    except (OSError, ValueError, KeyError, janela.TelaIndisponivel):
        evento("app_ja_aberto_sem_tela")


def _mostrar(url: str, degrau: str, motor: MotorApp, app, t: dict,
             encerrar: Callable[..., None]) -> None:
    def no_console():
        encerrar(espera_s=ESPERA_STOP_NO_CONSOLE_S)

    if degrau != "console":
        fechou = threading.Event()
        try:
            janela.abrir(url, degrau, fechou.set)
        except janela.TelaIndisponivel:
            evento("tela_sem_navegador")
        else:
            if fechou.is_set():
                return
            _abrir_console(no_console)
            print(t["console"]["navegador"])
            while not app.state.batimento.abandonada():
                time.sleep(_PASSO_S)
            evento("tela_abandonada")
            return
    _abrir_console(no_console)
    console(motor, t)


def _avisar_falha(t: dict) -> None:
    _u32.MessageBoxW(None, t["console"]["nao_abriu"].format(pasta=pasta_dados() / "logs"),
                     janela.TITULO, _MB_ICONWARNING)


def executar() -> int:
    rede.ligar()
    dados = pasta_dados()
    log.configurar(dados / "logs")
    if not unica_instancia():
        evento("app_ja_aberto")
        trazer_para_frente(dados)
        return 0
    t = textos("pt-BR")
    abertos: dict = {}
    encerrar = _fechamento(dados, abertos)
    falhou = False
    try:
        resultado = preflight.preflight()
        evento("app_inicio", versao=f"v{VERSAO}", degrau=resultado["degrau_tela"],
               windows=resultado["windows_build"], ram_livre_gb=resultado["ram_livre_gb"],
               disco_livre_gb=resultado["disco_livre_gb"],
               avisos=",".join(resultado["avisos"]) or "nenhum")
        caminho_config = Path(os.environ.get("SCAN_TO_TEXT_CONFIG")
                              or preflight.raiz_do_app() / "config.json")
        motor = abertos["motor"] = MotorApp(caminho_config, dados, estacao=estacao(),
                                            preflight=resultado, escolher=janela.escolher_pasta)
        t = textos(motor.config()["idioma_interface"])
        token = secrets.token_urlsafe(32)
        permitidas: set[str] = set()
        app = api.criar_app(motor, token, permitidas)
        servidor = abertos["servidor"] = Servidor(app)
        permitidas.add(servidor.origem)
        servidor.iniciar()
        _gravar_agente(dados, servidor.porta, token)
        motor.abrir()
        _mostrar(f"{servidor.origem}/#t={token}", resultado["degrau_tela"], motor, app, t,
                 encerrar)
    except Exception:
        _registrar_falha()
        falhou = True
    finally:
        encerrar()
    if falhou:
        _avisar_falha(t)
        return 1
    return 0


def _fechamento(dados: Path, abertos: dict) -> Callable[..., None]:
    """A sequência de fechamento, uma vez só, com cada passo independente dos outros."""
    trava = threading.Lock()
    feito: list[bool] = []

    def encerrar(espera_s: float | None = None) -> None:
        with trava:
            if feito:
                return
            feito.append(True)
            passos = [lambda: _apagar_agente(dados)]
            if "motor" in abertos:
                passos.append(lambda: abertos["motor"].fechar(espera_s=espera_s))
            if "servidor" in abertos:
                passos.append(abertos["servidor"].parar)
            for passo in passos:
                try:
                    passo()
                except Exception:
                    _registrar_falha()

    return encerrar


def _registrar_falha() -> None:
    """Tipo e quadros do erro no log; nunca a mensagem, que pode trazer caminho de paciente."""
    from scan_to_text.trabalhador import quadros
    erro = sys.exc_info()[1]
    logging.getLogger(log.NOME).error("app_falhou tipo=%s\n%s", type(erro).__name__, quadros(erro))
