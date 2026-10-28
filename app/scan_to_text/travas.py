"""Trava por arquivo de origem, em cada destino.

`<DESTINO>/_scan-to-text/travas/<sha256>.lock`, com
`{estacao, pid, token, contador, inicio}`:

- criada com `O_CREAT | O_EXCL`, que é atômico também em pasta de rede, só
  dentro de um destino que existe: a raiz sumida não é recriada;
- o dono soma 1 ao `contador` a cada batimento (5 min);
- parada = `(token, contador)` sem mudar por 15 min no relógio monotônico de
  QUEM OBSERVA. `inicio` é só informativo: relógios de estações divergem, e
  nenhuma decisão compara o relógio de uma estação com o de outra. A trava
  pode ter batimento e parada mais curtos, por instância;
- a observação de trava alheia pode ficar num arquivo local da estação
  (`observadas`, ou a variável `SCAN_TO_TEXT_OBSERVADAS`), e assim vale para o
  trabalhador seguinte: o relógio monotônico do Windows é o da máquina, o
  mesmo para todos os processos dela. Relógio que voltou (a máquina
  reiniciou) recomeça a observação. Sem arquivo, ela fica na memória do
  processo;
- a tomada de trava parada é por RENOMEAÇÃO feita pelo mesmo handle que leu o
  conteúdo: só uma renomeação encontra o arquivo, e a trava trocada por uma
  nova entre a leitura e a tomada fica onde está. Apagar e criar de novo, ou
  renomear e devolver, deixa um instante sem arquivo no caminho, em que uma
  terceira estação cria a sua e duas se acham donas;
- o dono relê o token antes de valer (`tomar`) e antes de publicar
  (`e_minha`).

Protocolo portado do local-suite (`app/radai_watcher.py`: `_tomar_lock`,
`claim`, `renovar_lock`), commit 55cffa9, Apache-2.0.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path

BATIMENTO_S = 5 * 60
PARADA_S = 15 * 60
PASTA = ("_scan-to-text", "travas")
VARIAVEL_OBSERVADAS = "SCAN_TO_TEXT_OBSERVADAS"
# Observação mais velha que isto sai do arquivo; a próxima recomeça a contagem.
_HORIZONTE_S = 7 * 24 * 3600

# Observações de travas alheias neste processo: "caminho|estacao" -> [token, contador, desde].
# A chave inclui quem observa porque a parada é medida no relógio dele, que é o
# da máquina: o PID fica fora, para valer de um trabalhador para o seguinte.
_OBSERVADAS: dict[str, list] = {}
# Tokens das travas que ESTE processo segura agora. Uma trava com o nosso PID e
# token fora daqui é sobra de um processamento que já terminou.
_SEGURADAS: set[str] = set()
_MEMORIA = threading.Lock()

_O_BINARIO = getattr(os, "O_BINARY", 0)
_TENTATIVAS_LEITURA = 20
_ESPERA_LEITURA_S = 0.005


def caminho_da_trava(destino: Path, sha256: str) -> Path:
    return Path(destino).joinpath(*PASTA, f"{sha256}.lock")


def arquivo_de_observacoes(padrao: Path | None = None) -> Path | None:
    """O arquivo das observações: o da variável de ambiente, senão `padrao`.

    `None` deixa as observações só na memória do processo.
    """
    valor = os.environ.get(VARIAVEL_OBSERVADAS, "").strip()
    if valor:
        return Path(valor)
    return Path(padrao) if padrao is not None else None


class Trava:
    def __init__(self, destino: Path, sha256: str, estacao: str, pid: int,
                 relogio: Callable[[], float] = time.monotonic,
                 parada_s: float | None = None, observadas: Path | None = None):
        self.destino = Path(destino)
        self.sha256 = sha256
        self.estacao = estacao
        self.pid = pid
        self.caminho = caminho_da_trava(self.destino, sha256)
        self._relogio = relogio
        self._parada_s = parada_s
        self._observadas = observadas if observadas is not None else arquivo_de_observacoes()
        self._token: str | None = None
        self._uma_escrita = threading.Lock()

    def tomar(self) -> bool:
        """True se esta estação passou a ser dona da trava (ou já era)."""
        self._preparar_pasta()
        if self._criar():
            return self._confirmar()
        dado = _ler(self.caminho)
        if dado is None:  # sumiu entre o O_EXCL e a leitura: o dono soltou
            return self._criar() and self._confirmar()
        if self._token is not None and dado.get("token") == self._token:
            return True
        if not self._parada(dado):
            return False
        if not _tomar_por_rename(self.caminho, dado, self.estacao, self.pid):
            return False
        with _MEMORIA:
            _anotar(self._observadas, self._chave(), None)
        return self._criar() and self._confirmar()

    def renovar(self) -> None:
        """Batimento: soma 1 ao contador, só se a trava existe e é nossa."""
        with self._uma_escrita:
            if self._token is None:
                return
            try:
                # Sem O_CREAT: trava que sumiu não é ressuscitada por um batimento.
                # O arquivo aberto sem compartilhar exclusão também impede que
                # alguém o renomeie entre a leitura do token e a escrita.
                fd = os.open(self.caminho, os.O_RDWR | _O_BINARIO)
            except FileNotFoundError:
                self._perder()
                return
            except OSError:
                return  # disputa passageira pelo arquivo; o próximo batimento tenta
            with os.fdopen(fd, "r+b") as f:
                dado = _decodificar(f.read())
                if dado.get("token") != self._token:
                    self._perder()
                    return
                dado["contador"] = int(dado.get("contador") or 0) + 1
                f.seek(0)
                f.truncate()
                f.write(_codificar(dado))

    def soltar(self) -> None:
        """Apaga a trava só se ainda for nossa."""
        with self._uma_escrita:
            if self._token is None:
                return
            dado = _ler(self.caminho)
            if dado and dado.get("token") == self._token:
                _tomar_por_rename(self.caminho, dado, self.estacao, self.pid)
            self._perder()

    def e_minha(self) -> bool:
        """Relê o token no disco. É a pergunta a fazer antes de publicar."""
        with self._uma_escrita:
            if self._token is None:
                return False
            dado = _ler(self.caminho)
            return bool(dado) and dado.get("token") == self._token

    def _preparar_pasta(self) -> None:
        """Cria `_scan-to-text/travas` só dentro de um destino que é pasta."""
        if not self.destino.is_dir():
            raise FileNotFoundError(errno.ENOENT, "destino_ausente")
        pasta = self.destino
        for parte in PASTA:
            pasta = pasta / parte
            pasta.mkdir(exist_ok=True)

    def _criar(self) -> bool:
        token = secrets.token_hex(16)
        dado = {"estacao": self.estacao, "pid": self.pid, "token": token,
                "contador": 0, "inicio": round(time.time(), 3)}
        try:
            fd = os.open(self.caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARIO)
        except FileExistsError:
            return False
        except PermissionError:
            # Arquivo existente em disputa (sendo renomeado ou apagado por outra
            # estação) responde assim no Windows; pasta sem escrita também.
            if self.caminho.exists():
                return False
            raise
        with os.fdopen(fd, "wb") as f:
            f.write(_codificar(dado))
        self._token = token
        return True

    def _confirmar(self) -> bool:
        if self.e_minha():
            with _MEMORIA:
                _SEGURADAS.add(self._token)
            return True
        self._token = None
        return False

    def _perder(self) -> None:
        with _MEMORIA:
            _SEGURADAS.discard(self._token)
        self._token = None

    def _chave(self) -> str:
        return f"{self.caminho}|{self.estacao}"

    def _parada(self, dado: dict) -> bool:
        pid = dado.get("pid")
        if dado.get("estacao") == self.estacao and isinstance(pid, int):
            if pid == self.pid:
                with _MEMORIA:
                    if dado.get("token") not in _SEGURADAS:
                        return True  # sobra deste mesmo processo
            elif not pid_vivo(pid):
                return True  # desta estação, e o processo já não existe
        visto = [dado.get("token"), dado.get("contador")]
        agora = self._relogio()
        parada_s = PARADA_S if self._parada_s is None else self._parada_s
        with _MEMORIA:
            desde = _desde(self._observadas, self._chave(), visto, agora)
            if desde is None:
                _anotar(self._observadas, self._chave(), [*visto, agora], agora)
                return False
            return agora - desde >= parada_s


def _desde(arquivo: Path | None, chave: str, visto: list, agora: float) -> float | None:
    """Desde quando `visto` está parado, pela observação mais antiga da memória ou do arquivo.

    A da memória cobre o arquivo que não pôde ser gravado. Observação no
    futuro do relógio é de antes de a máquina reiniciar e não vale: a conta
    recomeça em vez de esperar o relógio novo alcançar o antigo.
    """
    candidatas = [_OBSERVADAS.get(chave)]
    if arquivo is not None:
        candidatas.append(_ler_observacoes(arquivo).get(chave))
    validas = [c[2] for c in candidatas if c and c[:2] == visto and c[2] <= agora]
    return min(validas) if validas else None


def _anotar(arquivo: Path | None, chave: str, valor: list | None,
            agora: float | None = None) -> None:
    """Guarda (ou, com `None`, esquece) a observação na memória e no arquivo.

    O arquivo é trocado de uma vez. Falha de escrita deixa só a memória, como
    se não houvesse arquivo.
    """
    if valor is None:
        _OBSERVADAS.pop(chave, None)
    else:
        _OBSERVADAS[chave] = valor
    if arquivo is None:
        return
    tabela = _ler_observacoes(arquivo)
    if valor is None:
        if tabela.pop(chave, None) is None:
            return
    else:
        tabela[chave] = valor
    if agora is not None:
        tabela = {k: v for k, v in tabela.items() if v[2] <= agora and agora - v[2] <= _HORIZONTE_S}
    arquivo = Path(arquivo)
    parcial = arquivo.with_name(f"{arquivo.name}.{os.getpid()}.parcial")
    try:
        parcial.write_bytes(_codificar(tabela))
        os.replace(parcial, arquivo)
    except OSError:
        _apagar(parcial)


def _ler_observacoes(arquivo: Path) -> dict[str, list]:
    """As observações do arquivo; ausente ou ilegível dá tabela vazia."""
    try:
        dados = json.loads(Path(arquivo).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return {chave: valor for chave, valor in dados.items()
            if isinstance(valor, list) and len(valor) == 3
            and isinstance(valor[2], (int, float)) and not isinstance(valor[2], bool)}


def soltar_orfas(destino: Path, estacao: str, pid_vivo: Callable[[int], bool]) -> int:
    """Solta as travas desta estação cujo PID não está vivo. Devolve quantas."""
    pasta = Path(destino).joinpath(*PASTA)
    if not pasta.is_dir():
        return 0
    soltas = 0
    for lp in sorted(pasta.glob("*.lock")):
        dado = _ler(lp)
        if not dado or dado.get("estacao") != estacao:
            continue
        pid = dado.get("pid")
        if not isinstance(pid, int) or pid_vivo(pid):
            continue
        if _tomar_por_rename(lp, dado, estacao, os.getpid()):
            soltas += 1
    return soltas


class Batimento:
    """Renova as travas a cada `intervalo_s` numa thread, enquanto o bloco roda.

    A saída espera o batimento em curso terminar: soltar a trava com uma
    renovação no meio falharia, porque a renovação segura o arquivo aberto.
    """

    def __init__(self, travas: Iterable[Trava], intervalo_s: float = BATIMENTO_S):
        self._travas = list(travas)
        self._intervalo_s = intervalo_s
        self._fim = threading.Event()
        self._fio: threading.Thread | None = None

    def __enter__(self) -> Batimento:
        self._fio = threading.Thread(target=self._bater, daemon=True,
                                     name="scan_to_text_batimento")
        self._fio.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._fim.set()
        if self._fio is not None:
            self._fio.join()

    def _bater(self) -> None:
        while not self._fim.wait(self._intervalo_s):
            for trava in self._travas:
                trava.renovar()


def _tomar_por_rename(lp: Path, esperado: dict, estacao: str, pid: int) -> bool:
    """Tira do caminho a trava lida como `esperado`. True se foi esta chamada."""
    rastro = lp.with_name(f"{lp.name}.tomado.{_seguro(estacao)}.{pid}")
    if not _renomear_se(lp, rastro, esperado):
        return False
    _apagar(rastro)
    return True


def _apagar(caminho: Path) -> None:
    try:
        caminho.unlink()
    except OSError:
        pass  # o rastro não termina em .lock e não é lido como trava


def _seguro(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", texto)[:40] or "_"


def _ler(caminho: Path) -> dict | None:
    """None se o arquivo não existe; {} se existe e não é uma trava legível.

    Durante a conferência de uma tomada (`_renomear_se`) a leitura recebe
    violação de compartilhamento por um instante; por isso tenta de novo.
    """
    for tentativa in range(_TENTATIVAS_LEITURA):
        try:
            return _decodificar(Path(caminho).read_bytes())
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(_ESPERA_LEITURA_S)
        except OSError:
            return {}
    return {}


def _decodificar(dados: bytes) -> dict:
    try:
        dado = json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return dado if isinstance(dado, dict) else {}


def _codificar(dado: dict) -> bytes:
    return json.dumps(dado, ensure_ascii=False, sort_keys=True).encode("utf-8")


# ---------------------------------------------------------------------------
# Processo vivo, pela API do Windows
# ---------------------------------------------------------------------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_k32.GetExitCodeProcess.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL


_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_RENAME_INFO = 3
_INVALIDO = wintypes.HANDLE(-1).value
_MAX_TRAVA = 1 << 16

_k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
_k32.ReadFile.restype = wintypes.BOOL
_k32.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD)
_k32.SetFileInformationByHandle.restype = wintypes.BOOL


def _win32(caminho: Path) -> str:
    """Caminho absoluto com prefixo de caminho longo, também para UNC."""
    texto = os.path.abspath(caminho)
    if texto.startswith("\\\\?\\"):
        return texto
    if texto.startswith("\\\\"):
        return "\\\\?\\UNC\\" + texto[2:]
    return "\\\\?\\" + texto


def _renomear_se(lp: Path, rastro: Path, esperado: dict) -> bool:
    """Renomeia `lp` para `rastro` só se o conteúdo for `esperado`, num passo só.

    O handle pede acesso de exclusão e não o compartilha: enquanto ele existe,
    ninguém mais renomeia nem apaga o arquivo, e a renomeação pelo próprio
    handle leva exatamente o arquivo que foi lido. O dono vivo, com a trava
    aberta no batimento, faz a abertura falhar, e a tomada não acontece.
    """
    h = _k32.CreateFileW(_win32(lp), _GENERIC_READ | _DELETE, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                         None, _OPEN_EXISTING, 0, None)
    if not h or h == _INVALIDO:
        return False
    try:
        buf = ctypes.create_string_buffer(_MAX_TRAVA)
        lidos = wintypes.DWORD()
        if not _k32.ReadFile(h, buf, _MAX_TRAVA, ctypes.byref(lidos), None):
            return False
        if _decodificar(buf.raw[:lidos.value]) != esperado:
            return False
        alvo = _win32(rastro)
        unidades = len(alvo.encode("utf-16-le")) // 2

        class _Renomear(ctypes.Structure):
            _fields_ = [("substituir", wintypes.DWORD), ("raiz", wintypes.HANDLE),
                        ("tamanho", wintypes.DWORD), ("nome", wintypes.WCHAR * (unidades + 1))]

        info = _Renomear(1, None, unidades * 2, alvo)
        return bool(_k32.SetFileInformationByHandle(h, _FILE_RENAME_INFO, ctypes.byref(info),
                                                    ctypes.sizeof(info)))
    finally:
        _k32.CloseHandle(h)


def pid_vivo(pid: int) -> bool:
    """O processo existe e ainda não saiu? Na dúvida, vivo."""
    if not isinstance(pid, int) or pid <= 0 or pid > 0xFFFFFFFF:
        return False
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        # Sem acesso é processo de outro usuário, e existe.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        codigo = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(h, ctypes.byref(codigo)):
            return True
        return codigo.value == _STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)
