"""A janela da tela e as reservas.

Degraus, na ordem do preflight:
  * "janela": pywebview com o WebView2. O runtime Fixed do pacote é achado
    pela marca `msedgewebview2.exe` numa pasta `webview2`, subindo a partir
    deste arquivo; sem ele, vale o WebView2 do sistema. Bloqueia até o X
    fechar a janela e então chama `ao_fechar`. Se a janela não abre, a falha
    vai para o log e o degrau cai para o navegador;
  * "navegador": Edge ou Chrome com `--app`, num perfil próprio. Volta logo
    depois de abrir: quem percebe o fechamento é o batimento da página
    (`app.state.batimento` da API), porque o F5 também fecha a página e não
    pode encerrar o app;
  * "console": não abre nada; o modo texto é de `principal.console`.

`webview.start` precisa rodar na thread principal: o servidor da API sobe
antes, noutra thread.
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

TITULO = "Scan-to-Text"
MARCA_WEBVIEW2 = "msedgewebview2.exe"
_NAVEGADORES = ("msedge.exe", "chrome.exe")
_PASTAS_NAVEGADOR = (
    ("ProgramFiles(x86)", "Microsoft\\Edge\\Application\\msedge.exe"),
    ("ProgramFiles", "Microsoft\\Edge\\Application\\msedge.exe"),
    ("ProgramFiles", "Google\\Chrome\\Application\\chrome.exe"),
    ("ProgramFiles(x86)", "Google\\Chrome\\Application\\chrome.exe"),
    ("LOCALAPPDATA", "Google\\Chrome\\Application\\chrome.exe"),
)
_ESTADO = {"janela": None}
_log = logging.getLogger(__name__)


class TelaIndisponivel(RuntimeError):
    pass


def _pasta_dados() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Scan-to-Text"


def raiz_webview2(inicio: Path | None = None) -> Path | None:
    atual = Path(inicio or __file__).resolve()
    for pasta in (atual, *atual.parents):
        base = pasta / "webview2"
        if (base / MARCA_WEBVIEW2).is_file():
            return base
        if base.is_dir():
            # A .cab do Fixed Version extrai numa subpasta com a versão no nome.
            for sub in sorted(base.iterdir()):
                if (sub / MARCA_WEBVIEW2).is_file():
                    return sub
    return None


def achar_navegador() -> str | None:
    import winreg
    for exe in _NAVEGADORES:
        chave = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{exe}"
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(raiz, chave) as k:
                    caminho = str(winreg.QueryValueEx(k, "")[0]).strip('"')
            except OSError:
                continue
            if os.path.isfile(caminho):
                return caminho
    for variavel, resto in _PASTAS_NAVEGADOR:
        base = os.environ.get(variavel)
        if base and os.path.isfile(os.path.join(base, resto)):
            return os.path.join(base, resto)
    return None


def comando_navegador(exe: str, url: str, perfil: Path) -> list[str]:
    # Perfil próprio: sem ele, o navegador já aberto adota a janela e o
    # processo lançado sai na hora. As chaves de rede calam o que o
    # navegador faria sozinho (atualização de componente, sincronia).
    return [exe, f"--app={url}", f"--user-data-dir={perfil}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            "--disable-component-update", "--disable-sync", "--disable-default-apps",
            "--disable-extensions", "--window-size=1100,760"]


def _abrir_janela(url: str) -> None:
    import webview
    runtime = raiz_webview2()
    if runtime is not None:
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    janela = webview.create_window(TITULO, url, width=1100, height=760, min_size=(720, 560),
                                   background_color="#060D1A", text_select=True,
                                   zoomable=True)
    _ESTADO["janela"] = janela
    try:
        webview.start(gui="edgechromium", private_mode=True,
                      storage_path=str(_pasta_dados() / "webview"))
    finally:
        _ESTADO["janela"] = None


def _abrir_navegador(url: str) -> None:
    exe = achar_navegador()
    if not exe:
        raise TelaIndisponivel("sem_navegador")
    perfil = _pasta_dados() / "navegador"
    perfil.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(comando_navegador(exe, url, perfil), close_fds=True)


def abrir(url: str, degrau: str, ao_fechar: Callable[[], None]) -> None:
    if degrau == "console":
        return
    if degrau == "janela":
        try:
            _abrir_janela(url)
        except Exception:
            _log.warning("tela_janela_falhou", exc_info=True)
            degrau = "navegador"
        else:
            ao_fechar()
            return
    if degrau == "navegador":
        _abrir_navegador(url)
        return
    raise ValueError(degrau)


def escolher_pasta() -> str | None:
    """Seletor de pastas nativo (o de Vista em diante), na janela aberta.

    Sem janela (modo navegador), devolve None e a tela oferece colar o
    caminho ou navegar pelas pastas da estação. No Cancelar, devolve "": a
    tela não abre nada.
    """
    janela = _ESTADO["janela"]
    if janela is None:
        return None
    import webview
    escolha = janela.create_file_dialog(webview.FileDialog.FOLDER)
    return str(escolha[0]) if escolha else ""
