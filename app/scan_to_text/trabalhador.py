"""Trabalhador de OCR: processo filho sob demanda.

O principal manda uma tarefa (dict) por linha JSON no stdin do filho e lê a
resposta numa linha JSON do stdout dele. O filho:

- nasce na primeira tarefa e é encerrado depois de `ocioso_min` sem trabalho,
  o que devolve a memória dos modelos;
- roda com prioridade abaixo do normal, herdada pelos netos, e sobe para a
  normal só na prova do estouro do prazo (e volta, se a resposta chega nela)
  e ao ser encerrado, para sair a tempo com a CPU cheia;
- fica preso a um Job Object com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: quando o
  principal morre por qualquer caminho, o Windows fecha o handle do job e mata
  o filho e os netos. Não exige administrador;
- sai sozinho quando o canal do pai fecha (fim de arquivo no stdin), que é a
  reserva do Job Object e também o sinal de `parar()`.

Uma tarefa interrompida não deixa nada pela metade no destino: a gravação é
por `.parcial` mais renomeação, e o arquivo volta no próximo ping.

No envio de uma tarefa com prazo e no estouro dele, o principal mede o relógio,
a CPU que o filho usou e a CPU ociosa da máquina. Abaixo do normal, o filho
pode ficar minutos sem CPU quando processos normais ocupam todos os núcleos; a
medida separa esse caso (`TempoEsgotado.sem_cpu`) do arquivo pesado, que gastou
CPU. O filho travado também quase não gasta CPU, e o que espera a memória com
a estação paginando gasta menos de um núcleo: no estouro com o filho abaixo de
um núcleo, ele sobe para a prioridade normal por `PROVA_S` antes de ser
encerrado. Só é faminto o que gasta CPU quando ganha a vez, e só espera a
memória o que segue buscando páginas com a estação sem RAM livre
(`TempoEsgotado.esperou_paginas`). No fim da prova, ainda com o filho vivo,
mede-se a memória física disponível da estação, sozinha e somada à dele.
"""
from __future__ import annotations

import ctypes
import importlib
import json
import logging
import msvcrt
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

_VAR_CAMINHOS = "SCAN_TO_TEXT_CAMINHOS"
# O Python embutido ignora PYTHONPATH (por causa do ._pth): o sys.path do pai
# viaja numa variável própria e entra por sys.path.insert antes de qualquer
# import do pacote.
_PARTIDA = (
    "import json, os, sys; "
    f"sys.path[:0] = [p for p in json.loads(os.environ.pop({_VAR_CAMINHOS!r}, '[]')) "
    "if p not in sys.path]; "
    "from scan_to_text.trabalhador import principal_do_filho; "
    "principal_do_filho(sys.argv[1])"
)
_ESPERA_OCIOSO_S = 5
# Falta de CPU, e não arquivo pesado: o filho teve menos de um quarto de núcleo
# em média, e a máquina menos da metade da CPU ociosa. A leitura ocupa ao menos
# um núcleo inteiro enquanto roda. Com a máquina ocupada, o filho abaixo do
# normal fica quase sem CPU mesmo com parte dela ociosa (núcleo estacionado,
# irmão de hyperthreading).
CPU_MINIMA = 0.25
FOLGA_MAXIMA = 0.5
# Abaixo de um núcleo, o filho pode ter esperado alguma coisa: a CPU, a memória
# ou, se travou, nada. Também fica aí o arquivo pesado que lê numa thread só
# com a máquina ocupada (medido, de 0,75 a 0,82). É o estouro que passa pela
# prova, e só ela separa um caso do outro. A estação paginando gasta CPU na
# própria busca das páginas (a memória comprimida volta pela CPU), e o filho
# fica entre um quarto e um núcleo: medido, de 0,1 a 0,8.
NUCLEO_DA_LEITURA = 1.0
# A prova do estouro: na prioridade normal, o filho faminto disputa os núcleos
# de igual para igual e gasta CPU na hora. O travado fica perto de zero mesmo
# com as threads nativas que acordam de tempos em tempos (pools do torch e do
# ONNX Runtime, monitor do tqdm): elas aparecem prontas numa máquina cheia, mas
# quase não gastam CPU. O limiar é uma fração de um núcleo ao longo da prova.
PROVA_S = 15.0
CPU_NA_PROVA = 0.05
_FATIA_DA_PROVA_S = 0.1
# Na prova, o filho que espera a memória segue buscando páginas: medido com a
# estação paginando, dezenas de milhares de falhas de página por segundo. O
# parado e o de threads que só acordam ficam em zero. O que aloca e solta
# memória também chega a centenas de milhares por segundo sem esperar nada:
# as falhas sozinhas não provam a espera.
FALHAS_DE_PAGINA_NA_PROVA = 10.0
# Com a estação paginando, a memória física disponível (livre e em espera, sem
# a do filho) cai a poucas centenas de MB: as páginas em espera já foram
# reaproveitadas, e o que sai da RAM volta do disco ou da memória comprimida.
# Medido, com o filho abaixo de um núcleo e buscando páginas: de 190 MB a 1 GB
# em quase toda medida com a estação paginando; de 1,3 a 3 GB com o leitor
# lendo ou o filho alocando sem paginar. A memória privada fora do conjunto de
# trabalho não serve de prova: o leitor sadio tem de 0,6 a 1 GB comprometidos
# e ainda não tocados.
FISICA_LIVRE_PAGINANDO = 1024 * 2**20

_log = logging.getLogger(__name__)


class TrabalhadorCaiu(RuntimeError):
    """O processo filho saiu sem responder à tarefa."""


class TrabalhadorParou(TrabalhadorCaiu):
    """`parar()` interrompeu a tarefa; o arquivo é refeito no próximo ping."""


class TempoEsgotado(TrabalhadorCaiu):
    """A tarefa passou do tempo-limite e o filho foi encerrado.

    Traz a medida da janela da tarefa: o relógio, a CPU que o filho usou (todas
    as threads) e a fração ociosa da CPU da máquina. Com o filho abaixo de um
    núcleo, traz também a prova: a fração de um núcleo que o filho usou na
    prioridade normal e se isso passou de `CPU_NA_PROVA`, as falhas de página
    dele por segundo e, no fim dela, a memória física disponível da estação,
    sozinha e somada ao conjunto de trabalho do filho, em bytes. None no que
    não se mediu.
    """

    def __init__(self, mensagem: str = "", *, relogio_s: float | None = None,
                 cpu_s: float | None = None, folga: float | None = None,
                 quer_cpu: bool | None = None, cpu_na_prova: float | None = None,
                 falhas_de_pagina_por_s: float | None = None,
                 fisica_livre: int | None = None, fisica_disponivel: int | None = None):
        super().__init__(mensagem)
        self.relogio_s = relogio_s
        self.cpu_s = cpu_s
        self.folga = folga
        self.quer_cpu = quer_cpu
        self.cpu_na_prova = cpu_na_prova
        self.falhas_de_pagina_por_s = falhas_de_pagina_por_s
        self.fisica_livre = fisica_livre
        self.fisica_disponivel = fisica_disponivel

    @property
    def esperou_paginas(self) -> bool:
        """O filho lento seguiu buscando páginas na prova com a estação sem RAM livre."""
        return (self.falhas_de_pagina_por_s is not None
                and self.falhas_de_pagina_por_s >= FALHAS_DE_PAGINA_NA_PROVA
                and self.fisica_livre is not None
                and self.fisica_livre < FISICA_LIVRE_PAGINANDO)

    @property
    def sem_cpu(self) -> bool:
        """O filho queria rodar e não teve CPU: o prazo acabou pela máquina, e não pelo arquivo."""
        return self.quer_cpu is True and _pouca_cpu(self.relogio_s, self.cpu_s, self.folga)


class ErroNaTarefa(RuntimeError):
    """A função do filho levantou.

    Leva o tipo da exceção e os quadros do rastro (arquivo, linha, função),
    nunca a mensagem: ela pode conter texto de documento ou nome de paciente.
    `errno` e `winerror` vêm do OSError do filho (None nos outros erros), para
    o principal reconhecer o disco cheio sem a mensagem.
    """

    def __init__(self, tipo: str, rastro: str, errno: int | None = None,
                 winerror: int | None = None):
        super().__init__(f"{tipo}\n{rastro}")
        self.tipo = tipo
        self.rastro = rastro
        self.errno = errno
        self.winerror = winerror


class AlvoIndisponivel(ErroNaTarefa):
    """O filho não importou a função da tarefa: nenhuma tarefa roda nele.

    É do processo, e não da tarefa que a pediu, mesmo quando o tipo é o de um
    erro que a própria tarefa também poderia levantar (ImportError, MemoryError).
    """


@dataclass
class _Filho:
    proc: subprocess.Popen
    job: int | None
    motivo: str | None = None
    inicio: tuple | None = None  # medida no envio da tarefa com prazo
    fim: tuple | None = None     # medida no estouro do prazo
    prova: dict = field(default_factory=dict)  # os campos da prova de `TempoEsgotado`
    tarefas: int = 0
    em_curso: int | None = None  # a tarefa que espera resposta


class Trabalhador:
    def __init__(self, alvo: str, ocioso_min: float, medir=None, medir_memoria=None):
        self.alvo = alvo  # "modulo:funcao", importado no filho
        self.ocioso_s = ocioso_min * 60
        # (pid, job) -> (relógio, CPU do filho, CPU ociosa e CPU total da máquina), em segundos
        self._medir = medir or _medir
        # (pid, job) -> (falhas de página do filho, física disponível, conjunto do filho)
        self._medir_memoria = medir_memoria or _medir_memoria
        self._uma_por_vez = threading.Lock()
        self._estado = threading.Lock()
        self._filho: _Filho | None = None
        self._cronometro: threading.Timer | None = None

    @property
    def vivo(self) -> bool:
        filho = self._filho
        return filho is not None and filho.proc.poll() is None

    @property
    def pid(self) -> int | None:
        filho = self._filho
        return filho.proc.pid if filho is not None else None

    def enviar(self, tarefa: dict, tempo_limite_s: float | None = None) -> dict:
        """Manda a tarefa e bloqueia até a resposta. Uma tarefa por vez.

        Levanta `ErroNaTarefa` se a função do filho levantou (`AlvoIndisponivel`
        se nem foi importada), `TrabalhadorParou` se `parar()` a interrompeu,
        `TempoEsgotado` se passou de `tempo_limite_s` e `TrabalhadorCaiu` se o
        filho morreu sozinho.
        """
        with self._uma_por_vez:
            self._desarmar_ocioso()
            try:
                return self._enviar(tarefa, tempo_limite_s)
            finally:
                self._armar_ocioso()

    def parar(self, espera_s: float = 10) -> None:
        """Fecha o canal (o filho sai), espera até `espera_s` e então mata."""
        self._desarmar_ocioso()
        with self._estado:
            filho = self._filho
        if filho is not None:
            self._encerrar(filho, "parar", espera_s)

    def _enviar(self, tarefa: dict, tempo_limite_s: float | None) -> dict:
        dados = (json.dumps(tarefa, ensure_ascii=False) + "\n").encode("utf-8")
        filho = self._vivo_ou_nascer()
        with self._estado:
            filho.tarefas += 1
            vez = filho.em_curso = filho.tarefas
        limite = None
        if tempo_limite_s is not None:
            filho.inicio, filho.fim, filho.prova = self._medida(filho), None, {}
            limite = threading.Timer(tempo_limite_s, self._esgotar, args=(filho, vez))
            limite.daemon = True
            limite.start()
        try:
            filho.proc.stdin.write(dados)
            filho.proc.stdin.flush()
            linha = filho.proc.stdout.readline()
        except (OSError, ValueError):
            linha = b""
        finally:
            with self._estado:
                filho.em_curso = None
            if limite is not None:
                limite.cancel()
        if not linha:
            self._encerrar(filho, None, _ESPERA_OCIOSO_S)
            mensagem = f"trabalhador_saiu codigo={filho.proc.returncode}"
            if filho.motivo == "tempo":
                raise TempoEsgotado(mensagem, **_janela(filho.inicio, filho.fim), **filho.prova)
            raise (TrabalhadorParou if filho.motivo == "parar" else TrabalhadorCaiu)(mensagem)
        mensagem = json.loads(linha)
        if not mensagem.get("ok"):
            erro = AlvoIndisponivel if mensagem.get("ao_importar") else ErroNaTarefa
            raise erro(str(mensagem.get("tipo")), str(mensagem.get("rastro") or ""),
                       _inteiro(mensagem.get("errno")), _inteiro(mensagem.get("winerror")))
        return mensagem.get("resposta")

    def _vivo_ou_nascer(self) -> _Filho:
        with self._estado:
            filho = self._filho
        if filho is not None and filho.proc.poll() is None:
            return filho
        if filho is not None:
            self._encerrar(filho, None, 0)  # morreu sozinho entre tarefas
        return self._nascer()

    def _nascer(self) -> _Filho:
        env = dict(os.environ)
        env[_VAR_CAMINHOS] = json.dumps([p for p in sys.path if p])
        proc = subprocess.Popen(
            [sys.executable, "-c", _PARTIDA, self.alvo],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
        job = _criar_job()
        if job is not None and not _prender(job, proc.pid):
            _k32.CloseHandle(job)
            job = None
        if job is None:
            _log.warning("trabalhador_sem_job_object")
        filho = _Filho(proc, job)
        with self._estado:
            self._filho = filho
        return filho

    def _esgotar(self, filho: _Filho, vez: int) -> None:
        """O prazo da tarefa acabou: mede antes de encerrar, com o filho ainda vivo.

        A resposta pode chegar durante a medida ou a prova: aí a tarefa acabou
        bem, e o filho, que talvez já atenda a tarefa seguinte, fica, de volta
        abaixo do normal.
        """
        fim = self._medida(filho)
        prova, subiu = {}, False
        if _filho_lento(**_janela(filho.inicio, fim)):
            with self._estado:
                if filho.em_curso == vez and filho.motivo is None:
                    # Sob a trava, como em `_medida`: o handle do job não fecha no meio.
                    _dar_prioridade(_processos_do_filho(filho.proc.pid, filho.job),
                                    _NORMAL_PRIORITY_CLASS)
                    subiu = True
            if subiu:
                try:
                    prova = self._provar(filho, vez)
                except Exception:  # noqa: BLE001 - sem prova, o tempo esgotado conta
                    prova = {}
        with self._estado:
            if filho.em_curso != vez:
                if subiu and filho.motivo is None and filho.proc.poll() is None:
                    _dar_prioridade(_processos_do_filho(filho.proc.pid, filho.job),
                                    _BELOW_NORMAL_PRIORITY_CLASS)
                return
            filho.fim, filho.prova = fim, prova
            if filho.motivo is None:
                filho.motivo = "tempo"
            if self._filho is filho:
                self._filho = None  # a próxima tarefa já nasce noutro filho
        self._encerrar(filho, "tempo", 0)

    def _provar(self, filho: _Filho, vez: int) -> dict:
        """Mede o filho, já na prioridade normal, por `PROVA_S`.

        Devolve os campos da prova de `TempoEsgotado`: se gastou mais que
        `CPU_NA_PROVA` e a fração de um núcleo, as falhas de página por segundo
        e, no fim, a memória física disponível, sozinha e somada ao conjunto de
        trabalho do filho. Vazio quando a tarefa acabou no meio; sem o que não
        se mediu.
        """
        antes, paginas_antes = self._medida(filho), self._medida_da_memoria(filho)
        fim = time.monotonic() + PROVA_S
        while filho.em_curso == vez and time.monotonic() < fim:
            time.sleep(min(_FATIA_DA_PROVA_S, max(0.0, fim - time.monotonic())))
        depois, (paginas, livre, conjunto) = self._medida(filho), self._medida_da_memoria(filho)
        janela = _janela(antes, depois)
        if filho.em_curso != vez or janela.get("relogio_s", 0) <= 0:
            return {}
        prova = {}
        if janela.get("cpu_s") is not None:
            fracao = janela["cpu_s"] / janela["relogio_s"]
            prova.update(quer_cpu=fracao >= CPU_NA_PROVA, cpu_na_prova=fracao)
        if paginas_antes[0] is not None and paginas is not None:
            # O contador do Windows tem 32 bits.
            prova["falhas_de_pagina_por_s"] = ((paginas - paginas_antes[0]) % 2**32
                                               / janela["relogio_s"])
        if livre is not None:
            prova["fisica_livre"] = livre
            if conjunto is not None:
                prova["fisica_disponivel"] = livre + conjunto
        return prova

    def _medida(self, filho: _Filho) -> tuple | None:
        # Sob a trava: `_encerrar` só fecha o job depois de tirá-lo do filho com ela.
        with self._estado:
            try:
                return self._medir(filho.proc.pid, filho.job)
            except Exception:  # noqa: BLE001 - sem medida, o tempo esgotado conta como antes
                return None

    def _medida_da_memoria(self, filho: _Filho) -> tuple:
        with self._estado:  # como em `_medida`
            try:
                return self._medir_memoria(filho.proc.pid, filho.job)
            except Exception:  # noqa: BLE001 - sem medida, não há paginação a desculpar
                return None, None, None

    def _encerrar(self, filho: _Filho, motivo: str | None, espera_s: float) -> None:
        with self._estado:
            if filho.motivo is None:
                filho.motivo = motivo
            if self._filho is filho:
                self._filho = None
            if filho.proc.poll() is None:
                # Abaixo do normal, com a CPU cheia, até a saída pedida e a morte
                # pelo Job Object esperam a vez de rodar: cada thread tem de
                # rodar para sair.
                _dar_prioridade(_processos_do_filho(filho.proc.pid, filho.job),
                                _NORMAL_PRIORITY_CLASS)
        try:
            filho.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            filho.proc.wait(timeout=max(0.0, espera_s))
        except subprocess.TimeoutExpired:
            with self._estado:
                job = filho.job
            if job is not None:
                _k32.TerminateJobObject(job, 1)
            else:
                filho.proc.kill()
            filho.proc.wait()
        with self._estado:
            job, filho.job = filho.job, None
        if job is not None:
            # Fechar o job leva junto o neto que ainda estiver vivo.
            _k32.CloseHandle(job)
        try:
            filho.proc.stdout.close()
        except (OSError, ValueError):
            pass

    def _armar_ocioso(self) -> None:
        with self._estado:
            if self._cronometro is not None:
                self._cronometro.cancel()
            self._cronometro = None
            if self._filho is None:
                return
            cronometro = threading.Timer(self.ocioso_s, self._se_ocioso)
            cronometro.daemon = True
            self._cronometro = cronometro
        cronometro.start()

    def _desarmar_ocioso(self) -> None:
        with self._estado:
            cronometro, self._cronometro = self._cronometro, None
        if cronometro is not None:
            cronometro.cancel()

    def _se_ocioso(self) -> None:
        if not self._uma_por_vez.acquire(blocking=False):
            return  # uma tarefa acabou de chegar; o fim dela arma de novo
        try:
            with self._estado:
                filho = self._filho
            if filho is not None:
                self._encerrar(filho, "ocioso", _ESPERA_OCIOSO_S)
        finally:
            self._uma_por_vez.release()


# ---------------------------------------------------------------------------
# O lado do filho
# ---------------------------------------------------------------------------

def principal_do_filho(alvo: str) -> None:
    entrada = os.fdopen(os.dup(0), "rb")
    saida = os.fdopen(os.dup(1), "wb")
    _isolar_descritores_padrao()
    tarefas: queue.SimpleQueue = queue.SimpleQueue()
    threading.Thread(target=_ler_canal, args=(entrada, tarefas), daemon=True).start()
    funcao = None
    while True:
        linha = tarefas.get()
        ao_importar = funcao is None
        try:
            if funcao is None:
                funcao = _importar(alvo)
            ao_importar = False
            dados = json.dumps({"ok": True, "resposta": funcao(json.loads(linha))},
                               ensure_ascii=False)
        except BaseException as e:  # noqa: BLE001 - toda falha vira resposta
            dados = json.dumps({"ok": False, "tipo": type(e).__name__, "rastro": quadros(e),
                                "ao_importar": ao_importar,
                                "errno": _inteiro(getattr(e, "errno", None)),
                                "winerror": _inteiro(getattr(e, "winerror", None))})
        try:
            saida.write(dados.encode("utf-8") + b"\n")
            saida.flush()
        except OSError:
            os._exit(0)


def _ler_canal(entrada, tarefas: queue.SimpleQueue) -> None:
    try:
        for linha in entrada:
            tarefas.put(linha)
    except OSError:
        pass
    # Fim do canal: o pai fechou (parar, ocioso) ou morreu. Sai na hora, sem
    # esperar a tarefa em curso, que não tem mais a quem responder.
    os._exit(0)


def _isolar_descritores_padrao() -> None:
    """Os descritores 0 e 1 passam a ser NUL; o canal fica só nas cópias.

    Biblioteca que imprime no stdout corromperia o protocolo. E, em processo
    sem console, o CRT não repassa o dup2 ao handle padrão do Windows: um neto
    herdaria o canal, escreveria nele e o seguraria aberto depois da morte do
    filho. Por isso o handle padrão também é trocado.
    """
    nulo = os.open(os.devnull, os.O_RDWR)
    os.dup2(nulo, 0)
    os.dup2(nulo, 1)
    os.close(nulo)
    _k32.SetStdHandle(_STD_INPUT_HANDLE, msvcrt.get_osfhandle(0))
    _k32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1))


def _importar(alvo: str):
    modulo, _, nome = alvo.partition(":")
    return getattr(importlib.import_module(modulo), nome)


def _inteiro(valor: object) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def quadros(erro: BaseException) -> str:
    """Os quadros do rastro como `arquivo:linha função`, sem a mensagem do erro.

    Cada causa vem depois, numa linha `causa=<tipo em minúsculas>` seguida dos
    quadros dela: o tipo de fora pode ser só o embrulho do que faltou.
    """
    linhas, vistos, atual = [], set(), erro
    while atual is not None and id(atual) not in vistos:
        vistos.add(id(atual))
        if atual is not erro:
            linhas.append(f"causa={type(atual).__name__.lower()}")
        linhas += [f"{Path(q.filename).name}:{q.lineno} {q.name}"
                   for q in traceback.extract_tb(atual.__traceback__)]
        atual = atual.__cause__ or (None if atual.__suppress_context__ else atual.__context__)
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Job Object (ctypes, sem administrador)
# ---------------------------------------------------------------------------

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_STD_INPUT_HANDLE = -10 & 0xFFFFFFFF
_STD_OUTPUT_HANDLE = -11 & 0xFFFFFFFF


class _LimiteBasico(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ContadoresES(ctypes.Structure):
    _fields_ = [(nome, ctypes.c_uint64) for nome in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _LimiteEstendido(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _LimiteBasico),
                ("IoInfo", _ContadoresES),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                         wintypes.DWORD)
_k32.SetInformationJobObject.restype = wintypes.BOOL
_k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_k32.AssignProcessToJobObject.restype = wintypes.BOOL
_k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.TerminateJobObject.restype = wintypes.BOOL
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL
_k32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
_k32.SetStdHandle.restype = wintypes.BOOL


class _ContabilidadeDoJob(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_FILETIME_POR_S = 10_000_000  # FILETIME conta em unidades de 100 ns
_ptempo = ctypes.POINTER(ctypes.c_uint64)
_k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_k32.QueryInformationJobObject.restype = wintypes.BOOL
_k32.GetProcessTimes.argtypes = (wintypes.HANDLE, _ptempo, _ptempo, _ptempo, _ptempo)
_k32.GetProcessTimes.restype = wintypes.BOOL
_k32.GetSystemTimes.argtypes = (_ptempo, _ptempo, _ptempo)
_k32.GetSystemTimes.restype = wintypes.BOOL


def _medir(pid: int, job: int | None) -> tuple:
    """Relógio, CPU do filho e CPU ociosa e total da máquina, em segundos.

    Com o Job Object, a CPU é a de todos os processos dele: o Python de um
    ambiente virtual põe um lançador entre o pai e o interpretador, e o pid
    do Popen é o do lançador, que quase não usa CPU. Sem job, a do processo.
    A CPU da máquina soma todos os núcleos, e no GetSystemTimes o tempo de
    núcleo já inclui o ocioso. None no que não se pôde ler.
    """
    relogio = time.monotonic()
    cpu = _cpu_do_job(job) if job else _cpu_do_processo(pid)
    ocioso = total = None
    parado, nucleo, usuario = (ctypes.c_uint64() for _ in range(3))
    if _k32.GetSystemTimes(ctypes.byref(parado), ctypes.byref(nucleo), ctypes.byref(usuario)):
        ocioso = parado.value / _FILETIME_POR_S
        total = (nucleo.value + usuario.value) / _FILETIME_POR_S
    return relogio, cpu, ocioso, total


def _medir_memoria(pid: int, job: int | None) -> tuple:
    """Falhas de página do filho desde que nasceu, memória física disponível da
    estação e conjunto de trabalho do filho, em bytes.

    Com o Job Object, as falhas e o conjunto são os de todos os processos dele;
    sem job, os do processo. None no que não se pôde ler.
    """
    falhas = conjunto = None
    if job:
        conta = _conta_do_job(job)
        falhas = None if conta is None else conta.TotalPageFaultCount
    contadores = [_memoria_do_processo(p) for p in _processos_do_filho(pid, job)]
    if contadores and None not in contadores:
        conjunto = sum(c.WorkingSetSize for c in contadores)
        if not job:
            falhas = contadores[0].PageFaultCount
    estado = estado_da_memoria()
    return falhas, None if estado is None else estado.ullAvailPhys, conjunto


def _conta_do_job(job: int) -> _ContabilidadeDoJob | None:
    conta = _ContabilidadeDoJob()
    if not _k32.QueryInformationJobObject(job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                                          ctypes.byref(conta), ctypes.sizeof(conta), None):
        return None
    return conta


def _cpu_do_job(job: int) -> float | None:
    conta = _conta_do_job(job)
    if conta is None:
        return None
    return (conta.TotalUserTime + conta.TotalKernelTime) / _FILETIME_POR_S


def _cpu_do_processo(pid: int) -> float | None:
    processo = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not processo:
        return None
    try:
        criacao, saida, nucleo, usuario = (ctypes.c_uint64() for _ in range(4))
        if not _k32.GetProcessTimes(processo, ctypes.byref(criacao), ctypes.byref(saida),
                                    ctypes.byref(nucleo), ctypes.byref(usuario)):
            return None
        return (nucleo.value + usuario.value) / _FILETIME_POR_S
    finally:
        _k32.CloseHandle(processo)


class _ContadoresDeMemoria(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS."""
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class _EstadoDaMemoria(ctypes.Structure):
    """MEMORYSTATUSEX."""
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


_PROCESS_VM_READ = 0x0010
_k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ContadoresDeMemoria),
                                         wintypes.DWORD)
_k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
_k32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_EstadoDaMemoria),)
_k32.GlobalMemoryStatusEx.restype = wintypes.BOOL


def _memoria_do_processo(pid: int) -> _ContadoresDeMemoria | None:
    processo = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not processo:
        return None
    try:
        contadores = _ContadoresDeMemoria()
        contadores.cb = ctypes.sizeof(contadores)
        if not _k32.K32GetProcessMemoryInfo(processo, ctypes.byref(contadores), contadores.cb):
            return None
        return contadores
    finally:
        _k32.CloseHandle(processo)


def estado_da_memoria() -> _EstadoDaMemoria | None:
    """A memória da estação agora (física e comprometível); None sem leitura."""
    estado = _EstadoDaMemoria()
    estado.dwLength = ctypes.sizeof(estado)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(estado)):
        return None
    return estado


_PIDS_NO_JOB = 64


class _ListaDoJob(ctypes.Structure):
    """JOBOBJECT_BASIC_PROCESS_ID_LIST com espaço para `_PIDS_NO_JOB` processos."""
    _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * _PIDS_NO_JOB)]


_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_PROCESS_SET_INFORMATION = 0x0200
_NORMAL_PRIORITY_CLASS = 0x0020
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000
_k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_k32.SetPriorityClass.restype = wintypes.BOOL


def _pids_do_job(job: int) -> set[int]:
    lista = _ListaDoJob()
    if not _k32.QueryInformationJobObject(job, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                                          ctypes.byref(lista), ctypes.sizeof(lista), None):
        return set()
    return {int(lista.ProcessIdList[i])
            for i in range(min(lista.NumberOfProcessIdsInList, _PIDS_NO_JOB))}


def _dar_prioridade(pids: set[int], classe: int) -> None:
    for pid in pids:
        processo = _k32.OpenProcess(_PROCESS_SET_INFORMATION, False, pid)
        if processo:
            try:
                _k32.SetPriorityClass(processo, classe)
            finally:
                _k32.CloseHandle(processo)


def _processos_do_filho(pid: int, job: int | None) -> set[int]:
    """Com o Job Object, todos os processos dele (o lançador do ambiente virtual
    e o interpretador); sem job, o do pid."""
    return (_pids_do_job(job) if job else set()) or {pid}


def _filho_lento(relogio_s: float | None = None, cpu_s: float | None = None,
                 folga: float | None = None) -> bool:
    """O filho usou menos de um núcleo na janela, com ou sem folga na máquina."""
    if relogio_s is None or cpu_s is None:
        return False
    return relogio_s > 0 and cpu_s < NUCLEO_DA_LEITURA * relogio_s


def _pouca_cpu(relogio_s: float | None = None, cpu_s: float | None = None,
               folga: float | None = None) -> bool:
    """A janela tem cara de falta de CPU: o filho quase sem CPU, e a máquina sem folga."""
    if relogio_s is None or cpu_s is None or folga is None:
        return False
    return relogio_s > 0 and cpu_s < CPU_MINIMA * relogio_s and folga < FOLGA_MAXIMA


def _janela(inicio: tuple | None, fim: tuple | None) -> dict:
    """A diferença entre duas medidas, nos nomes de `TempoEsgotado`."""
    if inicio is None or fim is None:
        return {}
    (r0, c0, o0, t0), (r1, c1, o1, t1) = inicio, fim
    janela = {"relogio_s": r1 - r0}
    if c0 is not None and c1 is not None:
        janela["cpu_s"] = c1 - c0
    if None not in (o0, t0, o1, t1) and t1 > t0:
        janela["folga"] = (o1 - o0) / (t1 - t0)
    return janela


def _criar_job() -> int | None:
    """Job com KILL_ON_JOB_CLOSE, um por filho. O handle não é herdável."""
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _LimiteEstendido()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                        ctypes.byref(info), ctypes.sizeof(info)):
        _k32.CloseHandle(job)
        return None
    return job


def _prender(job: int, pid: int) -> bool:
    processo = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not processo:
        return False
    try:
        return bool(_k32.AssignProcessToJobObject(job, processo))
    finally:
        _k32.CloseHandle(processo)
