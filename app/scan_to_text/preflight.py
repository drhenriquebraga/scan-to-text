"""Preflight: mede a estação e escolhe o degrau da tela.

`coletar` lê registro, arquivos e memória só com biblioteca padrão, para rodar
mesmo quando uma biblioteca compilada não carrega. `avaliar` é pura: decide o
degrau e os avisos (chaves de i18n) a partir dos fatos.
"""
import ctypes
import dataclasses
import os
import shutil
import sys
from ctypes import wintypes
from pathlib import Path

WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# Release do .NET Framework 4.x -> versão (maior release conhecido <= o lido).
DOTNET = ((533320, "4.8.1"), (528040, "4.8"), (461808, "4.7.2"), (461308, "4.7.1"),
          (460798, "4.7"), (394802, "4.6.2"), (394254, "4.6.1"), (393295, "4.6"),
          (379893, "4.5.2"), (378675, "4.5.1"), (378389, "4.5"))
DOTNET_MINIMO = 394254          # 4.6.1, exigido pelo pythonnet
VCPP_MINIMO = (14, 44)          # onnxruntime 1.30 foi compilado com 14.44
BUILD_MINIMO = 10240            # Windows 10
DISCO_MINIMO_GB = 3.0
RAM_LIVRE_MINIMA_GB = 1.0


@dataclasses.dataclass(frozen=True)
class Fatos:
    windows_build: int
    edicao: str
    ram_total_gb: float
    ram_livre_gb: float
    disco_livre_gb: float
    webview2_pacote: bool
    webview2_sistema: str | None
    edge: bool
    chrome: bool
    vcpp_sistema: str | None
    vcpp_pacote: str | None
    dotnet_release: int | None
    mfplat: bool


def _versao(texto: str | None) -> tuple[int, ...]:
    if not texto:
        return ()
    return tuple(int(p) for p in texto.lstrip("v").split(".") if p.isdigit())


def _dotnet(release: int | None) -> str | None:
    if release is None:
        return None
    for minimo, nome in DOTNET:
        if release >= minimo:
            return nome
    return None


def avaliar(f: Fatos) -> dict:
    avisos = []
    if f.windows_build < BUILD_MINIMO:
        avisos.append("aviso.windows_nao_suportado")
    dotnet_ok = f.dotnet_release is not None and f.dotnet_release >= DOTNET_MINIMO
    if not dotnet_ok:
        avisos.append("aviso.dotnet_antigo")
    if f.vcpp_pacote is None:
        if f.vcpp_sistema is None:
            avisos.append("aviso.vcpp_ausente")
        elif _versao(f.vcpp_sistema)[:2] < VCPP_MINIMO:
            avisos.append("aviso.vcpp_antigo")
    if not f.mfplat:
        avisos.append("aviso.mfplat_ausente")
    if f.disco_livre_gb < DISCO_MINIMO_GB:
        avisos.append("aviso.disco_baixo")
    if f.ram_livre_gb < RAM_LIVRE_MINIMA_GB:
        avisos.append("aviso.ram_baixa")
    if (f.webview2_pacote or f.webview2_sistema) and dotnet_ok:
        degrau = "janela"
    elif f.edge or f.chrome:
        degrau = "navegador"
    else:
        degrau = "console"
    return {**dataclasses.asdict(f), "dotnet": _dotnet(f.dotnet_release),
            "degrau_tela": degrau, "avisos": avisos}


def _registro(raiz, chave: str, valor: str):
    import winreg
    try:
        with winreg.OpenKey(raiz, chave) as k:
            return winreg.QueryValueEx(k, valor)[0]
    except OSError:
        return None


def _memoria() -> tuple[float, float]:
    class Estado(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    estado = Estado()
    estado.dwLength = ctypes.sizeof(Estado)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))
    return estado.ullTotalPhys / 2**30, estado.ullAvailPhys / 2**30


def versao_de_arquivo(caminho: Path) -> str | None:
    if not caminho.is_file():
        return None
    ver = ctypes.windll.version
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    tamanho = ver.GetFileVersionInfoSizeW(str(caminho), None)
    if not tamanho:
        return None
    dados = ctypes.create_string_buffer(tamanho)
    if not ver.GetFileVersionInfoW(str(caminho), 0, tamanho, dados):
        return None
    ponteiro, comprimento = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(dados, "\\", ctypes.byref(ponteiro), ctypes.byref(comprimento)):
        return None
    info = ctypes.cast(ponteiro, ctypes.POINTER(wintypes.DWORD * 13)).contents
    ms, ls = info[2], info[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def coletar(raiz: Path) -> Fatos:
    import winreg
    hklm, hkcu = winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER
    nt = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    edicao = " ".join(str(v) for v in (_registro(hklm, nt, "ProductName"),
                                       _registro(hklm, nt, "EditionID"),
                                       _registro(hklm, nt, "DisplayVersion")) if v)
    pv = None
    for base, chave in ((hklm, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                        (hkcu, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")):
        valor = _registro(base, chave, "pv")
        if valor and valor != "0.0.0.0":
            pv = valor
            break

    def app_path(exe: str) -> bool:
        chave = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
        return any(_registro(b, chave, "") for b in (hklm, hkcu))

    runtimes = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    partes = [_registro(hklm, runtimes, v) for v in ("Major", "Minor", "Bld")]
    vcpp_sistema = ".".join(str(p) for p in partes) if all(p is not None for p in partes) else None
    total, livre = _memoria()
    sistema = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return Fatos(
        windows_build=sys.getwindowsversion().build,
        edicao=edicao,
        ram_total_gb=round(total, 2),
        ram_livre_gb=round(livre, 2),
        disco_livre_gb=round(shutil.disk_usage(raiz).free / 2**30, 2),
        webview2_pacote=(raiz / "webview2" / "msedgewebview2.exe").is_file(),
        webview2_sistema=pv,
        edge=app_path("msedge.exe"),
        chrome=app_path("chrome.exe"),
        vcpp_sistema=vcpp_sistema,
        vcpp_pacote=versao_de_arquivo(raiz / "python" / "msvcp140.dll"),
        dotnet_release=_registro(hklm, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
                                 "Release"),
        mfplat=(sistema / "mfplat.dll").is_file(),
    )


def raiz_do_app() -> Path:
    return Path(__file__).resolve().parents[2]


def preflight(raiz: Path | None = None) -> dict:
    return avaliar(coletar(raiz or raiz_do_app()))
