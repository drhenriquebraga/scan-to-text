"""Instalação, atualização e desinstalação na estação.

Roda com o Python do pacote extraído, chamado pelo `INSTALL.cmd`:

    python -m scan_to_text.instalar [--log <arquivo>] [desinstalar]

A pasta do pacote é a que contém `app/` (a deste módulo); o `entrega.txt` dela
diz o projeto e a versão. Instala em `<raiz>/SCAN_TO_TEXT`, com a raiz em
`RADAI_RAIZ_INSTALACAO` ou `<unidade do sistema>/RADIOTERAPIA_AI`; se essa
pasta não puder ser criada sem administrador, recua para
`%LOCALAPPDATA%/RADIOTERAPIA_AI`.

A troca é feita por renomeação de pastas, e nunca por cima da instalação
anterior: a cópia nova nasce em `SCAN_TO_TEXT.novo`, recebe o `config.json`
da anterior e só então troca de lugar com ela. Com o app aberto, o Windows
recusa renomear a pasta, e a instalação para sem mexer em nada. Queda no meio
da troca deixa a instalação anterior em `SCAN_TO_TEXT.antigo`, que a próxima
execução põe de volta antes de começar.

O registro compartilhado `<raiz>/_instalados.json` é lido, recebe só a própria
chave e é gravado por arquivo temporário seguido de troca, com trava por
criação exclusiva. Registro ilegível não é sobrescrito: guarda instalações de
outros apps.

Os destinos dos pacientes nunca são tocados: ficam fora da pasta do app.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

PROJETO = "SCAN_TO_TEXT"
REGISTRO = "_instalados.json"
PRESERVADOS = ("config.json",)
ATALHO = "Scan-to-Text.lnk"
PASTA_DO_MENU = "Braga HealthLab"
SIDS_WEBVIEW2 = ("S-1-15-2-1", "S-1-15-2-2")
VARIAVEL_RAIZ = "RADAI_RAIZ_INSTALACAO"
TENTATIVAS = 5
ESPERA_S = 0.5
TRAVA_VELHA_S = 120
MARGEM_BYTES = 200 * 1024 ** 2

_CLSID_SHELLLINK = "00021401-0000-0000-C000-000000000046"
_IID_ISHELLLINKW = "000214F9-0000-0000-C000-000000000046"
_IID_IPERSISTFILE = "0000010B-0000-0000-C000-000000000046"
_FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
_FOLDERID_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"


class InstalacaoRecusada(Exception):
    pass


# ---------------------------------------------------------------- pacote e raiz

def ler_entrega(pasta: Path) -> dict[str, str]:
    """Os campos `CHAVE=valor` do `entrega.txt`; vazio se não houver."""
    try:
        texto = (Path(pasta) / "entrega.txt").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}
    campos = {}
    for linha in texto.splitlines():
        chave, igual, valor = linha.partition("=")
        if igual:
            campos[chave.strip()] = valor.strip()
    return campos


def e_do_scan_to_text(pasta: Path) -> bool:
    return ler_entrega(pasta).get("PROJETO") == PROJETO


def raiz_padrao(ambiente=os.environ) -> Path:
    variavel = (ambiente.get(VARIAVEL_RAIZ) or "").strip().strip('"')
    if variavel:
        return Path(variavel)
    return Path((ambiente.get("SystemDrive") or "C:") + "\\") / "RADIOTERAPIA_AI"


def raiz_reserva(ambiente=os.environ) -> Path:
    return Path(ambiente.get("LOCALAPPDATA") or Path.home()) / "RADIOTERAPIA_AI"


def raiz_de_instalacao(ambiente=os.environ) -> Path:
    padrao = raiz_padrao(ambiente)
    try:
        padrao.mkdir(parents=True, exist_ok=True)
        return padrao
    except PermissionError:
        return raiz_reserva(ambiente)


def _normal(caminho: Path) -> str:
    return os.path.normcase(os.path.abspath(caminho)).rstrip("\\/")


def _dentro(caminho: Path, pasta: Path) -> bool:
    c, p = _normal(caminho), _normal(pasta)
    return c == p or c.startswith(p + os.sep)


def _tamanho(pasta: Path) -> int:
    return sum(p.stat().st_size for p in pasta.rglob("*") if p.is_file())


def _espaco_livre(pasta: Path) -> int:
    return shutil.disk_usage(pasta).free


# ---------------------------------------------------------------- pastas

def _somente_leitura(funcao, caminho, _erro):
    # Arquivo marcado como somente leitura faz o rmtree parar no primeiro.
    os.chmod(caminho, 0o666)
    funcao(caminho)


def _apagar(pasta: Path) -> None:
    if pasta.exists():
        shutil.rmtree(pasta, onexc=_somente_leitura)


def _renomear(de: Path, para: Path) -> None:
    """Renomeia com novas tentativas: o antivírus segura arquivo recém-copiado por instantes."""
    for tentativa in range(TENTATIVAS):
        try:
            de.rename(para)
            return
        except PermissionError:
            if tentativa == TENTATIVAS - 1:
                raise
            time.sleep(ESPERA_S * (tentativa + 1))


def _irmao(pasta: Path, sufixo: str) -> Path:
    return pasta.with_name(pasta.name + sufixo)


def copiar_app(pacote: Path, destino: Path) -> None:
    novo, antigo = _irmao(destino, ".novo"), _irmao(destino, ".antigo")
    if not destino.exists() and e_do_scan_to_text(antigo):
        _renomear(antigo, destino)
    if destino.exists() and not e_do_scan_to_text(destino):
        raise InstalacaoRecusada(f"{destino} existe e nao e uma instalacao do {PROJETO}: "
                                 "nada foi apagado; mova essa pasta e rode de novo")
    try:
        _apagar(novo)
        _apagar(antigo)
    except OSError as erro:
        raise InstalacaoRecusada(f"nao consegui limpar a sobra de uma instalacao anterior: {erro}")
    try:
        shutil.copytree(pacote, novo, ignore=shutil.ignore_patterns("__pycache__"))
        for nome in PRESERVADOS:
            if (destino / nome).is_file():
                shutil.copy2(destino / nome, novo / nome)
    except OSError as erro:
        _apagar(novo)
        raise InstalacaoRecusada(f"a copia parou ({erro}); a instalacao anterior ficou como "
                                 "estava") from erro
    if destino.exists():
        try:
            _renomear(destino, antigo)
        except PermissionError:
            _apagar(novo)
            raise InstalacaoRecusada("o Scan-to-Text esta aberto: feche a janela dele e rode o "
                                     "INSTALL.cmd de novo") from None
    try:
        _renomear(novo, destino)
    except OSError:
        if antigo.exists():
            _renomear(antigo, destino)
        _apagar(novo)
        raise
    _apagar(antigo)


# ---------------------------------------------------------------- registro compartilhado

def atualizar_registro(raiz: Path, mudar: Callable[[dict], None]) -> str:
    """Lê `_instalados.json`, aplica `mudar` e grava. Devolve "ok", "ilegivel" ou "ocupado"."""
    caminho, trava = raiz / REGISTRO, raiz / (REGISTRO + ".trava")
    for tentativa in range(TENTATIVAS):
        try:
            os.close(os.open(trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            try:
                # Trava de um instalador que morreu no meio não pode bloquear para sempre.
                if time.time() - trava.stat().st_mtime > TRAVA_VELHA_S:
                    trava.unlink()
                    continue
            except OSError:
                pass
            time.sleep(ESPERA_S * (tentativa + 1))
            continue
        temporario = caminho.with_name(f"{REGISTRO}.{os.getpid()}.tmp")
        try:
            dados = {}
            if caminho.is_file():
                try:
                    dados = json.loads(caminho.read_text(encoding="utf-8-sig"))
                except (UnicodeError, json.JSONDecodeError):
                    return "ilegivel"
                if not isinstance(dados, dict):
                    return "ilegivel"
            mudar(dados)
            temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
            os.replace(temporario, caminho)
            return "ok"
        except PermissionError:
            time.sleep(ESPERA_S * (tentativa + 1))
        finally:
            temporario.unlink(missing_ok=True)
            trava.unlink(missing_ok=True)
    return "ocupado"


# ---------------------------------------------------------------- WebView2

def liberar_webview2(destino: Path, executar=subprocess.run) -> bool:
    """Leitura e execução para os SIDs de pacote de aplicativo, sem administrador.

    O WebView2 Fixed não abre no Windows 10 sem isso. Quem instalou é dono da
    pasta, e o ACE herdável se propaga para o que já está dentro.
    """
    pasta = destino / "webview2"
    if not pasta.is_dir():
        return False
    icacls = Path(os.environ.get("SystemRoot") or "C:\\Windows") / "System32" / "icacls.exe"
    comando = [str(icacls), str(pasta), "/grant"] + [f"*{sid}:(OI)(CI)(RX)" for sid in SIDS_WEBVIEW2]
    try:
        return executar(comando, capture_output=True).returncode == 0
    except OSError:
        return False


# ---------------------------------------------------------------- atalhos (COM por ctypes)

def _guid(texto: str):
    return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(texto).bytes_le)


def _metodo(interface: ctypes.c_void_p, indice: int, *argumentos, retorno=ctypes.HRESULT):
    """O método de número `indice` da tabela virtual de uma interface COM."""
    tabela = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    funcao = ctypes.WINFUNCTYPE(retorno, ctypes.c_void_p, *argumentos)(tabela[indice])
    return lambda *valores: funcao(interface, *valores)


def criar_atalho(lnk: Path, alvo: Path, argumentos: str, pasta_de_trabalho: Path,
                 descricao: str = "") -> None:
    """Grava um `.lnk` pelo IShellLinkW, sem PowerShell e sem pacote de terceiros."""
    ole32 = ctypes.OleDLL("ole32")
    try:
        ole32.CoInitialize(None)
        iniciou = True
    except OSError:
        # COM já iniciado nesta thread em outro modo: o ShellLink funciona nos dois.
        iniciou = False
    try:
        link = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_SHELLLINK)), None, 1,
                               ctypes.byref(_guid(_IID_ISHELLLINKW)), ctypes.byref(link))
        try:
            # Índices da tabela do IShellLinkW: 7 SetDescription, 9 SetWorkingDirectory,
            # 11 SetArguments, 20 SetPath.
            _metodo(link, 20, ctypes.c_wchar_p)(str(alvo))
            _metodo(link, 11, ctypes.c_wchar_p)(argumentos)
            _metodo(link, 9, ctypes.c_wchar_p)(str(pasta_de_trabalho))
            _metodo(link, 7, ctypes.c_wchar_p)(descricao)
            arquivo = ctypes.c_void_p()
            iid = _guid(_IID_IPERSISTFILE)
            _metodo(link, 0, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.addressof(iid), ctypes.byref(arquivo))
            try:
                # IPersistFile: 6 Save(nome, lembrar).
                _metodo(arquivo, 6, ctypes.c_wchar_p, ctypes.c_int)(str(lnk), 1)
            finally:
                _metodo(arquivo, 2, retorno=ctypes.c_ulong)()
        finally:
            _metodo(link, 2, retorno=ctypes.c_ulong)()
    finally:
        if iniciou:
            ole32.CoUninitialize()


def _pasta_conhecida(folderid: str) -> Path:
    caminho = ctypes.c_wchar_p()
    ctypes.OleDLL("shell32").SHGetKnownFolderPath(ctypes.byref(_guid(folderid)), 0, None,
                                                  ctypes.byref(caminho))
    try:
        return Path(caminho.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(caminho)


def pastas_de_atalho() -> list[Path]:
    """Área de trabalho e a pasta do ecossistema no Menu Iniciar, do usuário que instala."""
    return [_pasta_conhecida(_FOLDERID_DESKTOP), _pasta_conhecida(_FOLDERID_PROGRAMS) / PASTA_DO_MENU]


def _criar_atalhos(destino: Path, pastas: Sequence[Path]) -> list[Path]:
    criados = []
    for pasta in pastas:
        pasta.mkdir(parents=True, exist_ok=True)
        lnk = pasta / ATALHO
        criar_atalho(lnk, destino / "python" / "pythonw.exe", "-m scan_to_text", destino,
                     "Scan-to-Text")
        criados.append(lnk)
    return criados


def _abrir_app(destino: Path) -> None:
    subprocess.Popen([str(destino / "python" / "pythonw.exe"), "-m", "scan_to_text"], cwd=destino,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)


# ---------------------------------------------------------------- instalar e desinstalar

def exigir_pacote(pacote: Path) -> str:
    """A versão do pacote; recusa pasta que não seja um Scan-to-Text extraído inteiro."""
    entrega = ler_entrega(pacote)
    versao = entrega.get("VERSAO")
    if (entrega.get("PROJETO") != PROJETO or not versao
            or not (pacote / "python" / "pythonw.exe").is_file()):
        raise InstalacaoRecusada(f"{pacote} nao e um pacote do Scan-to-Text completo: "
                                 "extraia o .zip de novo, com Extrair tudo")
    return versao


def instalar(pacote: Path, raiz: Path, pastas_atalho: Sequence[Path], hoje: date, *,
             executar=subprocess.run, abrir=_abrir_app, espaco_livre=_espaco_livre,
             dizer=print) -> Path:
    versao = exigir_pacote(pacote)
    destino = raiz / PROJETO
    if _dentro(pacote, destino) or _dentro(destino, pacote):
        raise InstalacaoRecusada("o .zip foi extraido dentro da pasta de instalacao: extraia "
                                 "em outro lugar, como a pasta Downloads")
    raiz.mkdir(parents=True, exist_ok=True)
    preciso = _tamanho(pacote) + MARGEM_BYTES
    livre = espaco_livre(raiz)
    if livre < preciso:
        raise InstalacaoRecusada(f"falta espaco no disco: precisa de {preciso / 1024 ** 3:.1f} GB "
                                 f"livres e ha {livre / 1024 ** 3:.1f} GB")
    dizer(f"Instalando o Scan-to-Text {versao} em {destino}")
    dizer("  copiando os arquivos (pode levar alguns minutos)")
    copiar_app(pacote, destino)
    if not liberar_webview2(destino, executar):
        dizer("  AVISO: nao consegui ajustar a permissao do webview2; se a janela nao abrir, "
              "o app usa o navegador")
    try:
        for lnk in _criar_atalhos(destino, pastas_atalho):
            dizer(f"  atalho: {lnk}")
    except OSError as erro:
        dizer(f"  AVISO: nao consegui criar os atalhos ({erro}); o app abre por "
              f"{destino / 'python' / 'pythonw.exe'} -m scan_to_text")
    estado = atualizar_registro(raiz, lambda dados: dados.__setitem__(PROJETO, {
        "versao": versao, "data": hoje.isoformat(), "caminho": str(destino)}))
    if estado != "ok":
        dizer(f"  AVISO: registro {raiz / REGISTRO} {estado}; ele nao foi alterado. "
              "O app funciona; so o diagnostico fica sem esta entrada")
    abrir(destino)
    dizer("PRONTO. O Scan-to-Text foi instalado e esta abrindo.")
    return destino


def desinstalar(raiz: Path, pastas_atalho: Sequence[Path], *, dizer=print) -> bool:
    """Apaga a pasta do app, os atalhos e a própria chave do registro. False se não havia nada."""
    destino = raiz / PROJETO
    if not e_do_scan_to_text(destino):
        return False
    removendo = _irmao(destino, ".removendo")
    _apagar(removendo)
    try:
        _renomear(destino, removendo)
    except PermissionError:
        raise InstalacaoRecusada("o Scan-to-Text esta aberto: feche a janela dele e rode de "
                                 "novo") from None
    for pasta in pastas_atalho:
        (pasta / ATALHO).unlink(missing_ok=True)
    estado = atualizar_registro(raiz, lambda dados: dados.pop(PROJETO, None))
    if estado != "ok":
        dizer(f"  AVISO: registro {raiz / REGISTRO} {estado}; a entrada do {PROJETO} ficou nele")
    try:
        _apagar(removendo)
    except OSError:
        dizer(f"  AVISO: sobrou a pasta {removendo}; apague-a quando puder")
    dizer(f"PRONTO. O Scan-to-Text foi removido de {destino}. Os destinos nao foram tocados.")
    return True


# ---------------------------------------------------------------- entrada

def _dizer_com_log(log: Path | None):
    def dizer(texto: str = "") -> None:
        print(texto, flush=True)
        if log is not None:
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(texto + "\n")
            except OSError:
                pass
    return dizer


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    log = Path(args[args.index("--log") + 1]) if "--log" in args[:-1] else None
    dizer = _dizer_com_log(log)
    pacote = Path(__file__).resolve().parents[2]
    try:
        if "desinstalar" in args:
            for raiz in dict.fromkeys((raiz_padrao(), raiz_reserva())):
                if desinstalar(raiz, pastas_de_atalho(), dizer=dizer):
                    return 0
            dizer("Nada a desinstalar: o Scan-to-Text nao esta instalado neste usuario.")
            return 0
        exigir_pacote(pacote)
        instalar(pacote, raiz_de_instalacao(), pastas_de_atalho(), date.today(), dizer=dizer)
        return 0
    except InstalacaoRecusada as erro:
        dizer(f"PAROU: {erro}")
        return 1
    except Exception:
        dizer("ERRO inesperado. Detalhe tecnico:")
        dizer(traceback.format_exc())
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
