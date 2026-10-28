"""Trabalhador de OCR: processo filho sob demanda, Job Object e canal com o pai.

As funções `executar` e `_pai` rodam nos processos filhos que estes testes
criam; o trabalhador as importa pelo nome `test_trabalhador`.

O filho roda abaixo do normal de propósito, e com a CPU cheia ele demora a
nascer. Por isso os testes esperam marcas ("a tarefa começou") em vez de
tempos fixos, e só cronometram os prazos que o trabalhador promete.
"""
import ctypes
import errno
import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from scan_to_text import trabalhador as modulo
from scan_to_text.travas import pid_vivo
from scan_to_text.trabalhador import (AlvoIndisponivel, ErroNaTarefa, TempoEsgotado,
                                      Trabalhador, TrabalhadorParou)

ALVO = "test_trabalhador:executar"
ABAIXO_DO_NORMAL = 0x4000
NORMAL = 0x0020
# Filho e neto sobem abaixo do normal (Python, pytest e o pacote): com a CPU
# cheia de processos normais, só essa subida passou de 8 min. O prazo cobre a
# montagem do pai de teste; o que os testes cronometram vem depois dela.
PRAZO_DA_MONTAGEM_S = 1800
_CREATE_NO_WINDOW = 0x08000000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.TerminateProcess.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
_k32.GetPriorityClass.restype = wintypes.DWORD
_k32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
_k32.IsProcessInJob.restype = wintypes.BOOL
_k32.SetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
_k32.SetProcessAffinityMask.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# O que roda no processo filho
# ---------------------------------------------------------------------------

def executar(tarefa: dict) -> dict:
    acao = tarefa["acao"]
    if tarefa.get("marca"):
        Path(tarefa["marca"]).write_text("comecou", encoding="utf-8")
    if acao == "eco":
        return {"eco": tarefa}
    if acao == "pid":
        return {"pid": os.getpid(),
                "prioridade": _k32.GetPriorityClass(_k32.GetCurrentProcess())}
    if acao == "dormir":
        time.sleep(tarefa["s"])
        return {}
    if acao == "alocar":  # encosta em cada página nova
        bloco = bytearray(tarefa["mb"] * 2**20)
        for i in range(0, len(bloco), 4096):
            bloco[i] = 1
        return {}
    if acao == "revolver":  # aloca, encosta e solta sem parar: tudo fica na RAM
        fim = time.monotonic() + tarefa["s"]
        while time.monotonic() < fim:
            bloco = bytearray(8 * 2**20)
            for i in range(0, len(bloco), 4096):
                bloco[i] = 1
            del bloco
        return {}
    if acao == "girar":  # gasta `s` segundos de CPU, quanto tempo de relógio levar
        inicio = time.process_time()
        while time.process_time() - inicio < tarefa["s"]:
            pass
        return {"cpu": time.process_time() - inicio}
    if acao == "acordar":
        # Threads que acordam de tempos em tempos e quase não gastam CPU, como
        # os pools do torch e do ONNX Runtime no filho de verdade.
        for _ in range(tarefa["threads"]):
            threading.Thread(target=_acordar_sempre, args=(tarefa["s"],), daemon=True).start()
        return {}
    if acao == "segurar_gil":
        # PyDLL não solta o GIL: a thread que lê o canal fica sem rodar
        ctypes.PyDLL("kernel32").Sleep(int(tarefa["s"] * 1000))
        return {}
    if acao == "sujar_stdout":
        print("texto de biblioteca no stdout")
        sys.stdout.flush()
        os.write(1, b"escrita crua no descritor 1\n")
        subprocess.run([sys.executable, "-c", "print('saida do neto')"],
                       creationflags=_CREATE_NO_WINDOW, check=True)
        return {"limpo": True}
    if acao == "neto":
        neto = subprocess.Popen(
            [sys.executable, "-c",
             "import os, sys, time; sys.stdout.write(str(os.getpid()) + chr(10)); "
             "sys.stdout.flush(); time.sleep(120)"],
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW)
        return {"pid": os.getpid(), "neto": int(neto.stdout.readline())}
    if acao == "erro":
        raise ValueError("JOHN DOE, laudo de teste")
    if acao == "erro_com_causa":
        try:
            _falhar_por_dentro()
        except KeyError as causa:
            raise RuntimeError("JOHN DOE, laudo de teste") from causa
    if acao == "disco_cheio":
        raise OSError(None, "JOHN DOE, laudo de teste", None, 112)  # ERROR_DISK_FULL
    if acao == "nao_serializa":
        return {"objeto": object()}
    if acao == "importar_ausente":
        importlib.import_module("modulo_que_nao_existe_no_filho")
    raise KeyError(acao)


def _falhar_por_dentro() -> None:
    raise KeyError("JOHN DOE")


def _acordar_sempre(intervalo_s: float) -> None:
    while True:
        time.sleep(intervalo_s)
        sum(range(100))


def _pai(modo: str, tarefa_de_fundo: str, marca: str) -> None:
    """Processo principal de mentira: cria o trabalhador e espera ser morto.

    Cada etapa sai numa linha do stdout, antes da linha JSON com os PIDs: a
    montagem que passa do prazo diz onde parou.
    """
    print("pai_importou", flush=True)
    if modo == "sem_job":
        modulo._criar_job = lambda: None
    t = Trabalhador(ALVO, ocioso_min=5)
    ids = t.enviar({"acao": "neto" if tarefa_de_fundo == "segurar_gil" else "pid"})
    print("filho_respondeu", flush=True)
    if tarefa_de_fundo == "segurar_gil":
        def segurar():
            try:
                t.enviar({"acao": "segurar_gil", "s": 20, "marca": marca})
            except Exception as erro:  # noqa: BLE001
                # A marca não viria: o pai sai, e o teste reprova sem esperar o prazo.
                print(f"tarefa_de_fundo_falhou {type(erro).__name__}", flush=True)
                os._exit(1)

        threading.Thread(target=segurar, daemon=True).start()
        _esperar_marca(Path(marca), PRAZO_DA_MONTAGEM_S)
    print(json.dumps({"pai": os.getpid(), "filho": ids["pid"], "neto": ids.get("neto")}),
          flush=True)
    time.sleep(120)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------

def _terminar(pid: int | None) -> None:
    if not pid:
        return
    h = _k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)


def _esperar_morte(pids, limite_s: float) -> float:
    inicio = time.monotonic()
    while any(pid_vivo(p) for p in pids):
        if time.monotonic() - inicio > limite_s:
            break
        time.sleep(0.05)
    return time.monotonic() - inicio


def _esperar_marca(marca: Path, limite_s: float = 90) -> None:
    fim = time.monotonic() + limite_s
    while not marca.exists():
        assert time.monotonic() < fim, "a tarefa não começou"
        time.sleep(0.02)


def _maquina_cheia(pid, job):
    """A medida de verdade do relógio e da CPU do filho, com a máquina sem folga."""
    relogio, cpu, _ocioso, _total = modulo._medir(pid, job)
    return relogio, cpu, 0.0, relogio


def _prioridade(pid: int) -> int:
    h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    assert h, ctypes.get_last_error()
    try:
        return _k32.GetPriorityClass(h)
    finally:
        _k32.CloseHandle(h)


@pytest.fixture
def nucleo_cheio():
    """Prende os processos dados ao último núcleo, junto com um processo normal que gira."""
    nucleo = 1 << (os.cpu_count() - 1)
    queimador = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-c",
                                  "import time\nfim = time.monotonic() + 600\n"
                                  "while time.monotonic() < fim: pass"],
                                 creationflags=_CREATE_NO_WINDOW)
    try:
        _prender_ao_nucleo(queimador.pid, nucleo)
        yield lambda pids: [_prender_ao_nucleo(pid, nucleo) for pid in pids]
    finally:
        queimador.kill()
        queimador.wait()


def _prender_ao_nucleo(pid: int, mascara: int) -> None:
    h = _k32.OpenProcess(0x0200 | 0x0400, False, pid)  # SET_INFORMATION | QUERY_INFORMATION
    assert h, ctypes.get_last_error()
    try:
        assert _k32.SetProcessAffinityMask(h, mascara), ctypes.get_last_error()
    finally:
        _k32.CloseHandle(h)


def _em_segundo_plano(t: Trabalhador, tarefa: dict) -> queue.Queue:
    resultado: queue.Queue = queue.Queue()

    def rodar():
        try:
            t.enviar(tarefa)
            resultado.put("terminou")
        except Exception as e:  # noqa: BLE001
            resultado.put(type(e).__name__)

    threading.Thread(target=rodar, daemon=True).start()
    return resultado


def _subir_pai(tmp_path: Path, modo: str, tarefa_de_fundo: str):
    codigo = ("import json, os, sys; sys.path[:0] = json.loads(os.environ['CAMINHOS_TESTE']); "
              "import test_trabalhador; "
              "test_trabalhador._pai(sys.argv[1], sys.argv[2], sys.argv[3])")
    env = dict(os.environ, CAMINHOS_TESTE=json.dumps(sys.path))
    pai = subprocess.Popen([sys.executable, "-c", codigo, modo, tarefa_de_fundo,
                            str(tmp_path / "marca")],
                           stdout=subprocess.PIPE, env=env, creationflags=_CREATE_NO_WINDOW)
    linhas: queue.Queue = queue.Queue()

    def ler():
        for linha in pai.stdout:
            linhas.put(linha)
        linhas.put(b"")  # o pai saiu

    threading.Thread(target=ler, daemon=True).start()
    etapa, fim = "python_subindo", time.monotonic() + PRAZO_DA_MONTAGEM_S
    while True:
        try:
            linha = linhas.get(timeout=max(0.0, fim - time.monotonic()))
        except queue.Empty:
            pai.kill()
            pytest.fail(f"o processo pai de teste não ficou pronto em "
                        f"{PRAZO_DA_MONTAGEM_S} s; última etapa: {etapa}")
        if not linha:
            pytest.fail(f"o processo pai de teste saiu depois da etapa {etapa}, "
                        f"código {pai.wait()}")
        texto = linha.decode("utf-8").strip()
        if texto.startswith("{"):
            return pai, json.loads(texto)
        etapa = texto


@pytest.fixture
def trabalhadores():
    criados = []

    def novo(ocioso_min: float = 5, **kw) -> Trabalhador:
        t = Trabalhador(ALVO, ocioso_min=ocioso_min, **kw)
        criados.append(t)
        return t

    yield novo
    for t in criados:
        t.parar(espera_s=2)


@pytest.fixture(scope="module")
def compartilhado():
    t = Trabalhador(ALVO, ocioso_min=5)
    yield t
    t.parar(espera_s=2)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def test_nasce_so_com_a_primeira_tarefa_e_responde(trabalhadores):
    t = trabalhadores()
    assert not t.vivo and t.pid is None
    assert t.enviar({"acao": "eco", "x": [1, "dois"]}) == {"eco": {"acao": "eco", "x": [1, "dois"]}}
    assert t.vivo
    pid = t.pid
    assert t.enviar({"acao": "eco", "y": 3}) == {"eco": {"acao": "eco", "y": 3}}
    assert t.pid == pid  # o mesmo processo atende a fila


def test_roda_com_prioridade_abaixo_do_normal(compartilhado):
    assert compartilhado.enviar({"acao": "pid"})["prioridade"] == ABAIXO_DO_NORMAL


def test_filho_fica_preso_ao_job_object(compartilhado):
    compartilhado.enviar({"acao": "eco"})
    job = compartilhado._filho.job
    assert job
    h = _k32.OpenProcess(0x1000, False, compartilhado.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    try:
        dentro = wintypes.BOOL()
        assert _k32.IsProcessInJob(h, job, ctypes.byref(dentro))
        assert dentro.value
    finally:
        _k32.CloseHandle(h)


def test_saida_de_terceiros_no_stdout_nao_corrompe_o_canal(compartilhado):
    assert compartilhado.enviar({"acao": "sujar_stdout"}) == {"limpo": True}
    assert compartilhado.enviar({"acao": "eco"}) == {"eco": {"acao": "eco"}}


def test_erro_na_tarefa_levanta_sem_texto_do_documento(compartilhado):
    with pytest.raises(ErroNaTarefa) as erro:
        compartilhado.enviar({"acao": "erro"})
    assert erro.value.tipo == "ValueError"
    assert "executar" in erro.value.rastro
    assert "JOHN" not in str(erro.value) and "DOE" not in erro.value.rastro
    assert compartilhado.enviar({"acao": "eco"}) == {"eco": {"acao": "eco"}}  # segue atendendo


def test_erro_de_disco_no_filho_chega_com_errno_e_winerror(compartilhado):
    # O principal reconhece o disco cheio por estes códigos, sem a mensagem.
    with pytest.raises(ErroNaTarefa) as erro:
        compartilhado.enviar({"acao": "disco_cheio"})
    assert erro.value.tipo == "OSError"
    assert (erro.value.errno, erro.value.winerror) == (errno.ENOSPC, 112)
    assert "JOHN" not in str(erro.value)
    with pytest.raises(ErroNaTarefa) as outro:
        compartilhado.enviar({"acao": "erro"})
    assert (outro.value.errno, outro.value.winerror) == (None, None)


def test_rastro_traz_o_tipo_e_os_quadros_da_causa_sem_a_mensagem(compartilhado):
    # A causa diz o que faltou (memória, DLL, modelo) quando o erro de fora é só
    # o embrulho.
    with pytest.raises(ErroNaTarefa) as erro:
        compartilhado.enviar({"acao": "erro_com_causa"})
    assert erro.value.tipo == "RuntimeError"
    assert "causa=keyerror" in erro.value.rastro
    assert "_falhar_por_dentro" in erro.value.rastro
    assert "JOHN" not in str(erro.value) and "DOE" not in erro.value.rastro


def test_resposta_que_nao_vira_json_e_erro_da_tarefa(compartilhado):
    with pytest.raises(ErroNaTarefa) as erro:
        compartilhado.enviar({"acao": "nao_serializa"})
    assert erro.value.tipo == "TypeError"


def test_alvo_inexistente_e_erro_da_tarefa(trabalhadores):
    t = trabalhadores()
    t.alvo = "modulo_que_nao_existe:funcao"
    with pytest.raises(AlvoIndisponivel) as erro:
        t.enviar({"acao": "eco"})
    assert erro.value.tipo == "ModuleNotFoundError"


def test_import_que_falha_dentro_da_tarefa_nao_e_alvo_indisponivel(compartilhado):
    # Só a importação da função da tarefa é do filho inteiro; o resto é da tarefa.
    with pytest.raises(ErroNaTarefa) as erro:
        compartilhado.enviar({"acao": "importar_ausente"})
    assert erro.value.tipo == "ModuleNotFoundError"
    assert not isinstance(erro.value, AlvoIndisponivel)
    assert compartilhado.enviar({"acao": "eco"}) == {"eco": {"acao": "eco"}}


def test_morre_depois_de_ocioso_e_renasce_com_a_fila(trabalhadores):
    t = trabalhadores(ocioso_min=0.02)  # 1,2 s
    real = t.enviar({"acao": "pid"})["pid"]
    assert _esperar_morte([real], 30) < 30
    assert not t.vivo
    outro = t.enviar({"acao": "pid"})["pid"]
    assert outro != real and t.vivo


def test_parar_interrompe_a_tarefa_em_andamento(trabalhadores, tmp_path):
    t = trabalhadores()
    real = t.enviar({"acao": "pid"})["pid"]
    resultado = _em_segundo_plano(t, {"acao": "dormir", "s": 60, "marca": str(tmp_path / "m")})
    _esperar_marca(tmp_path / "m")
    inicio = time.monotonic()
    t.parar(espera_s=10)
    assert time.monotonic() - inicio < 10
    assert resultado.get(timeout=30) == "TrabalhadorParou"
    assert not pid_vivo(real) and not t.vivo


def test_parar_poe_o_filho_na_prioridade_normal_enquanto_espera_a_saida(trabalhadores, tmp_path):
    # Abaixo do normal, com a CPU cheia, a saída pedida e a morte pelo Job
    # Object esperam a vez de rodar e passam do prazo do STOP.
    t = trabalhadores()
    real = t.enviar({"acao": "pid"})["pid"]
    resultado = _em_segundo_plano(t, {"acao": "segurar_gil", "s": 60,
                                      "marca": str(tmp_path / "m")})
    _esperar_marca(tmp_path / "m", PRAZO_DA_MONTAGEM_S)
    parada = threading.Thread(target=t.parar, kwargs={"espera_s": 4}, daemon=True)
    parada.start()
    time.sleep(1.5)  # dentro da espera pela saída, antes de o Job Object matar
    h = _k32.OpenProcess(0x1000, False, real)  # PROCESS_QUERY_LIMITED_INFORMATION
    assert h, ctypes.get_last_error()
    try:
        assert _k32.GetPriorityClass(h) == NORMAL
    finally:
        _k32.CloseHandle(h)
    parada.join(30)
    assert resultado.get(timeout=30) == "TrabalhadorParou"


def test_parar_mata_o_filho_que_nao_solta_o_gil(trabalhadores, tmp_path):
    t = trabalhadores()
    real = t.enviar({"acao": "pid"})["pid"]
    resultado = _em_segundo_plano(t, {"acao": "segurar_gil", "s": 60,
                                      "marca": str(tmp_path / "m")})
    _esperar_marca(tmp_path / "m")
    inicio = time.monotonic()
    t.parar(espera_s=2)
    assert time.monotonic() - inicio < 8  # 2 s de espera, depois o Job Object mata
    assert resultado.get(timeout=30) == "TrabalhadorParou"
    assert _esperar_morte([real], 5) < 5


def test_tempo_limite_por_arquivo(trabalhadores):
    t = trabalhadores()
    real = t.enviar({"acao": "pid"})["pid"]
    inicio = time.monotonic()
    with pytest.raises(TempoEsgotado):
        t.enviar({"acao": "dormir", "s": 60}, tempo_limite_s=1)
    # Com a máquina ocupada, o filho que dorme tem cara de faminto, e a prova
    # entra antes do fim.
    assert time.monotonic() - inicio < 15 + modulo.PROVA_S
    assert _esperar_morte([real], 5) < 5
    assert t.enviar({"acao": "eco"}) == {"eco": {"acao": "eco"}}


def test_tempo_esgotado_traz_a_cpu_do_filho_a_folga_da_maquina_e_a_prova(trabalhadores,
                                                                         monkeypatch):
    # Relógio, CPU do filho, CPU ociosa e CPU total da máquina, em segundos:
    # no envio da tarefa, no estouro do prazo e no começo e no fim da prova.
    monkeypatch.setattr(modulo, "PROVA_S", 0.05)
    medidas = iter([(10.0, 1.0, 100.0, 200.0), (1510.0, 10.0, 104.0, 18200.0),
                    (1511.0, 10.0, 104.0, 18212.0), (1526.0, 17.5, 104.0, 18392.0)])
    chamadas = []

    def medir(pid, job):
        chamadas.append((pid, job))
        return next(medidas)

    t = trabalhadores(medir=medir)
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "dormir", "s": 60}, tempo_limite_s=1)
    e = erro.value
    assert (e.relogio_s, e.cpu_s, e.folga) == (1500.0, 9.0, 4.0 / 18000.0)
    assert (e.quer_cpu, e.cpu_na_prova) == (True, 0.5)
    assert e.sem_cpu  # o filho queria rodar, e a máquina não tinha CPU livre
    # As medidas são do mesmo filho, pelo Job Object dele.
    assert len(chamadas) == 4 and len(set(chamadas)) == 1 and chamadas[0][1]


_ESTOURO_SEM_CPU = (1510.0, 10.0, 104.0, 18200.0)


@pytest.mark.parametrize("fim, prova, quer_cpu, sem_cpu", [
    pytest.param(_ESTOURO_SEM_CPU, [(2000.0, 10.0, 0.0, 1.0), (2015.0, 17.5, 0.0, 2.0)],
                 True, True, id="faminto"),
    # O filho travado numa máquina cheia mede tão pouca CPU quanto o faminto,
    # mas não a gasta nem na prioridade normal. Conta, para o arquivo não
    # voltar para sempre sem ser entregue.
    pytest.param(_ESTOURO_SEM_CPU, [(2000.0, 10.0, 0.0, 1.0), (2015.0, 10.1, 0.0, 2.0)],
                 False, False, id="travado_na_maquina_cheia"),
    pytest.param(_ESTOURO_SEM_CPU, [(2000.0, None, 0.0, 1.0), (2015.0, None, 0.0, 2.0)],
                 None, False, id="prova_sem_medida"),
    # A leitura ocupa ao menos um núcleo: o arquivo pesado não passa pela prova.
    pytest.param((1510.0, 3001.0, 104.0, 18200.0), [], None, False, id="arquivo_pesado"),
    # Abaixo de um núcleo passa, mas a CPU gasta na prova só desculpa o faminto.
    pytest.param((1510.0, 1201.0, 104.0, 18200.0),
                 [(2000.0, 10.0, 0.0, 1.0), (2015.0, 17.5, 0.0, 2.0)], True, False,
                 id="lento_sem_ser_faminto"),
    # Parado com a CPU livre também passa pela prova: ela diz se o filho
    # esperava a memória. Com folga, a CPU que ele gasta não o desculpa.
    pytest.param((1510.0, 10.0, 16000.0, 18200.0),
                 [(2000.0, 10.0, 0.0, 1.0), (2015.0, 17.5, 0.0, 2.0)], True, False,
                 id="parado_com_cpu_livre"),
    pytest.param((1510.0, None, None, None), [], None, False, id="sem_medida"),
])
def test_so_e_falta_de_cpu_com_pouca_cpu_a_maquina_sem_folga_e_cpu_gasta_na_prova(
        trabalhadores, monkeypatch, fim, prova, quer_cpu, sem_cpu):
    monkeypatch.setattr(modulo, "PROVA_S", 0.05)
    medidas = [(10.0, 1.0, 100.0, 200.0), fim, *prova]
    chamadas = []

    def medir(pid, job):
        chamadas.append(pid)
        return medidas[len(chamadas) - 1]

    t = trabalhadores(medir=medir, medir_memoria=lambda pid, job: (None, None, None))
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "dormir", "s": 60}, tempo_limite_s=1)
    assert erro.value.quer_cpu is quer_cpu
    assert erro.value.sem_cpu is sem_cpu
    assert len(chamadas) == len(medidas)  # a prova só roda com pouca CPU do filho


MB = 2**20


@pytest.mark.parametrize("memorias, falhas_por_s, livre, fisica, esperou", [
    # 30 000 falhas de página nos 15 s da prova, com a CPU do filho parada e a
    # memória física da estação quase no fim.
    pytest.param([(1000, 900 * MB, 300 * MB), (31000, 700 * MB, 250 * MB)],
                 2000.0, 700 * MB, 950 * MB, True, id="buscando_paginas"),
    pytest.param([(1000, 900 * MB, 300 * MB), (1000, 900 * MB, 300 * MB)],
                 0.0, 900 * MB, 1200 * MB, False, id="parado_sem_buscar_paginas"),
    # O filho que aloca e solta memória também busca páginas novas, aos
    # milhares, mas com RAM livre na estação nada o faz esperar.
    pytest.param([(1000, 2900 * MB, 30 * MB), (31000, 2904 * MB, 36 * MB)],
                 2000.0, 2904 * MB, 2940 * MB, False, id="alocando_com_ram_livre"),
    # O contador do Windows tem 32 bits e pode dar a volta no meio da prova.
    pytest.param([(2**32 - 1000, 900 * MB, 300 * MB), (29000, 700 * MB, 250 * MB)],
                 2000.0, 700 * MB, 950 * MB, True, id="contador_que_da_a_volta"),
    pytest.param([(None, None, None), (None, None, None)], None, None, None, False,
                 id="sem_medida"),
])
def test_prova_mede_as_falhas_de_pagina_e_a_memoria_fisica_com_o_filho_ainda_vivo(
        trabalhadores, monkeypatch, memorias, falhas_por_s, livre, fisica, esperou):
    # O filho que espera o disco com a estação paginando gasta pouca CPU, como o
    # travado; só ele segue buscando páginas, e a estação está sem RAM livre.
    # A memória física disponível conta a do próprio filho, que volta para a
    # estação quando ele sai.
    monkeypatch.setattr(modulo, "PROVA_S", 0.05)
    medidas = iter([(10.0, 1.0, 100.0, 200.0), (1510.0, 10.0, 16000.0, 18200.0),
                    (2000.0, 10.0, 0.0, 1.0), (2015.0, 10.3, 0.0, 2.0)])
    memorias = iter(memorias)
    vivo = []

    def medir_memoria(pid, job):
        vivo.append(t._filho is not None and t._filho.proc.poll() is None)
        return next(memorias)

    t = trabalhadores(medir=lambda pid, job: next(medidas), medir_memoria=medir_memoria)
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "dormir", "s": 60}, tempo_limite_s=1)
    e = erro.value
    assert e.falhas_de_pagina_por_s == falhas_por_s
    assert e.fisica_livre == livre
    assert e.fisica_disponivel == fisica
    assert e.esperou_paginas is esperou
    assert not e.sem_cpu  # a máquina tinha CPU livre
    assert vivo == [True, True]


def test_medida_de_verdade_do_filho_que_dorme(trabalhadores, monkeypatch):
    # O travado que dorme não busca páginas: com pouca memória ou não, ele conta.
    monkeypatch.setattr(modulo, "PROVA_S", 2.0)
    t = trabalhadores()
    t.enviar({"acao": "eco"})  # o filho já de pé: a janela medida é só a da tarefa
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "dormir", "s": 60}, tempo_limite_s=2)
    e = erro.value
    assert e.relogio_s >= 1.9
    assert 0 <= e.cpu_s < 0.5
    assert 0 <= e.folga <= 1
    assert not e.sem_cpu
    assert 0 <= e.falhas_de_pagina_por_s < modulo.FALHAS_DE_PAGINA_NA_PROVA
    assert not e.esperou_paginas
    assert e.fisica_disponivel > 0
    assert 0 < e.fisica_livre <= e.fisica_disponivel


def test_medida_de_verdade_da_memoria_do_filho(trabalhadores):
    t = trabalhadores()
    t.enviar({"acao": "eco"})
    falhas, livre, conjunto = modulo._medir_memoria(t.pid, t._filho.job)
    t.enviar({"acao": "alocar", "mb": 64})
    depois, _livre, _conjunto = modulo._medir_memoria(t.pid, t._filho.job)
    assert depois - falhas >= 64 * 2**20 // 4096  # uma falha por página nova, ao menos
    assert livre > 0 and conjunto > 0


def test_sem_job_a_memoria_medida_e_a_do_processo():
    falhas, livre, conjunto = modulo._medir_memoria(os.getpid(), None)
    assert falhas > 0 and livre > 0 and conjunto > 0


def _sempre_lento(pid, job):
    """O relógio de verdade e o filho sem CPU nenhuma: toda tarefa passa pela prova."""
    relogio = time.monotonic()
    return relogio, 0.0, 0.0, relogio


def test_prova_de_verdade_do_filho_que_aloca_e_solta(trabalhadores, monkeypatch):
    # O arquivo pesado que aloca e solta memória sem parar busca páginas novas
    # na prova, aos milhares, como o que espera a estação paginando: as falhas
    # sozinhas não separam os dois. Só a estação sem RAM livre faz a espera.
    monkeypatch.setattr(modulo, "PROVA_S", 2.0)
    t = trabalhadores(medir=_sempre_lento)
    t.enviar({"acao": "eco"})
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "revolver", "s": 60}, tempo_limite_s=1)
    e = erro.value
    assert e.falhas_de_pagina_por_s >= modulo.FALHAS_DE_PAGINA_NA_PROVA
    assert 0 < e.fisica_livre <= e.fisica_disponivel
    assert e.esperou_paginas is (e.fisica_livre < modulo.FISICA_LIVRE_PAGINANDO)


def test_prova_de_verdade_do_filho_travado_com_threads_que_acordam(trabalhadores, nucleo_cheio,
                                                                  monkeypatch):
    # Abaixo do normal, preso a um núcleo cheio, o filho travado tem threads que
    # acordam e ficam prontas, esperando a vez. Na prioridade normal elas
    # rodam, e quase não gastam CPU: o estouro conta contra o arquivo.
    monkeypatch.setattr(modulo, "PROVA_S", 3.0)
    t = trabalhadores(medir=_maquina_cheia)
    t.enviar({"acao": "acordar", "threads": 6, "s": 0.05})
    nucleo_cheio(modulo._pids_do_job(t._filho.job))
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "dormir", "s": 600}, tempo_limite_s=3)
    e = erro.value
    assert e.quer_cpu is False and e.cpu_na_prova < modulo.CPU_NA_PROVA
    assert not e.sem_cpu


def test_prova_de_verdade_do_filho_faminto(trabalhadores, nucleo_cheio, monkeypatch):
    # O filho que tem trabalho gasta CPU assim que ganha a vez na prioridade normal.
    monkeypatch.setattr(modulo, "PROVA_S", 3.0)
    t = trabalhadores(medir=_maquina_cheia)
    t.enviar({"acao": "eco"})
    nucleo_cheio(modulo._pids_do_job(t._filho.job))
    with pytest.raises(TempoEsgotado) as erro:
        t.enviar({"acao": "girar", "s": 600}, tempo_limite_s=3)
    e = erro.value
    assert e.quer_cpu is True and e.cpu_na_prova >= modulo.CPU_NA_PROVA
    assert e.sem_cpu


def test_resposta_que_chega_na_prova_devolve_o_filho_abaixo_do_normal(trabalhadores,
                                                                      monkeypatch):
    # O faminto que termina na prioridade normal entrega a tarefa, e o filho
    # segue atendendo abaixo do normal.
    monkeypatch.setattr(modulo, "PROVA_S", PRAZO_DA_MONTAGEM_S)
    t = trabalhadores(medir=lambda pid, job: (time.monotonic(), 0.0, 0.0, time.monotonic()))
    pid = t.enviar({"acao": "pid"})["pid"]
    assert t.enviar({"acao": "dormir", "s": 1.5}, tempo_limite_s=0.3) == {}
    fim = time.monotonic() + 30
    while _prioridade(pid) != ABAIXO_DO_NORMAL:
        assert time.monotonic() < fim, "o filho ficou na prioridade normal"
        time.sleep(0.05)
    assert t.enviar({"acao": "pid"})["pid"] == pid


def test_parar_no_meio_da_prova_cabe_no_prazo(trabalhadores, tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, "PROVA_S", PRAZO_DA_MONTAGEM_S)
    t = trabalhadores(medir=lambda pid, job: (time.monotonic(), 0.0, 0.0, time.monotonic()))
    real = t.enviar({"acao": "pid"})["pid"]
    resultado: queue.Queue = queue.Queue()

    def rodar():
        try:
            t.enviar({"acao": "dormir", "s": 600}, tempo_limite_s=0.3)
        except Exception as e:  # noqa: BLE001
            resultado.put(type(e).__name__)

    threading.Thread(target=rodar, daemon=True).start()
    fim = time.monotonic() + 60
    while _prioridade(real) != NORMAL:  # a prova começou
        assert time.monotonic() < fim, "a prova não começou"
        time.sleep(0.05)
    inicio = time.monotonic()
    t.parar(espera_s=2)
    assert time.monotonic() - inicio < 8
    assert resultado.get(timeout=30) == "TrabalhadorParou"
    assert _esperar_morte([real], 5) < 5


def test_cpu_medida_e_a_do_interpretador_e_nao_so_a_do_lancador(trabalhadores):
    # No ambiente virtual, o pid do Popen é o de um lançador que quase não usa
    # CPU; quem gira é o interpretador, que está no mesmo Job Object.
    t = trabalhadores()
    t.enviar({"acao": "eco"})
    antes = modulo._medir(t.pid, t._filho.job)
    gasto = t.enviar({"acao": "girar", "s": 1.0})["cpu"]
    depois = modulo._medir(t.pid, t._filho.job)
    assert depois[1] - antes[1] >= 0.9 * gasto >= 0.9


def test_resposta_que_chega_durante_a_prova_do_estouro_nao_derruba_a_tarefa_seguinte(
        trabalhadores, tmp_path, monkeypatch):
    # A prova espera fora da trava: a resposta chega, e a tarefa seguinte
    # começa no mesmo filho antes de o estouro olhar de novo.
    seguinte, esperas = threading.Event(), []

    def esperar(segundos):
        esperas.append(segundos)
        seguinte.wait(PRAZO_DA_MONTAGEM_S)

    monkeypatch.setattr(modulo, "time", SimpleNamespace(monotonic=time.monotonic, sleep=esperar))
    t = trabalhadores(medir=lambda pid, job: (time.monotonic(), 0.0, 0.0, time.monotonic()))
    try:
        pid = t.enviar({"acao": "pid"})["pid"]
        assert t.enviar({"acao": "dormir", "s": 3}, tempo_limite_s=0.2) == {}
        assert esperas  # a resposta chegou com a prova em curso
        marca = tmp_path / "segunda"
        segunda = _em_segundo_plano(t, {"acao": "dormir", "s": 3, "marca": str(marca)})
        _esperar_marca(marca, PRAZO_DA_MONTAGEM_S)
    finally:
        seguinte.set()
    assert segunda.get(timeout=PRAZO_DA_MONTAGEM_S) == "terminou"
    assert t.enviar({"acao": "pid"}) == {"pid": pid, "prioridade": ABAIXO_DO_NORMAL}


def test_sem_job_a_cpu_medida_e_a_do_processo():
    _relogio, cpu, ocioso, total = modulo._medir(os.getpid(), None)
    assert abs(cpu - time.process_time()) < 1
    assert 0 <= ocioso <= total


def test_parar_sem_filho_nao_faz_nada(trabalhadores):
    t = trabalhadores()
    t.parar()
    assert not t.vivo


def test_morte_forcada_do_pai_derruba_o_filho_e_o_neto_pelo_job_object(tmp_path):
    pai, ids = _subir_pai(tmp_path, "com_job", "segurar_gil")
    try:
        assert pid_vivo(ids["filho"]) and pid_vivo(ids["neto"])
        _terminar(ids["pai"])
        tempo = _esperar_morte([ids["filho"], ids["neto"]], 5)
        assert not pid_vivo(ids["filho"]), "o trabalhador sobreviveu ao pai"
        assert not pid_vivo(ids["neto"]), "o neto sobreviveu ao pai"
        assert tempo <= 5
    finally:
        for pid in (ids["filho"], ids["neto"], ids["pai"]):
            _terminar(pid)
        pai.wait(timeout=30)


def test_sem_job_object_o_canal_do_pai_derruba_o_filho(tmp_path):
    pai, ids = _subir_pai(tmp_path, "sem_job", "ocioso")
    try:
        assert pid_vivo(ids["filho"])
        _terminar(ids["pai"])
        # A reserva não tem prazo prometido; o filho abaixo do normal, com a CPU
        # cheia, pode esperar alguns segundos pela vez de ler o fim do canal.
        _esperar_morte([ids["filho"]], 30)
        assert not pid_vivo(ids["filho"])
    finally:
        _terminar(ids["filho"])
        _terminar(ids["pai"])
        pai.wait(timeout=30)


def test_controle_sem_job_o_filho_preso_no_gil_sobrevive(tmp_path):
    """Sem o Job Object, um filho preso em código nativo não sai pelo canal.

    Prova que, no teste com Job Object, quem derruba o filho é o Windows.
    """
    pai, ids = _subir_pai(tmp_path, "sem_job", "segurar_gil")
    try:
        _terminar(ids["pai"])
        time.sleep(3)
        assert pid_vivo(ids["filho"])
    finally:
        _terminar(ids["filho"])
        _terminar(ids["neto"])
        pai.wait(timeout=30)
