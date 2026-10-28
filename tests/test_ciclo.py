"""Ciclo: START/STOP, estados, fechamento e limpeza no início."""
import _winapi
import ctypes
import errno
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from scan_to_text import ciclo as modulo
from scan_to_text.ciclo import Ciclo
from scan_to_text.es import ES, OrigemIntocavel
from scan_to_text.trabalhador import (AlvoIndisponivel, ErroNaTarefa, Trabalhador,
                                      TrabalhadorParou)
from scan_to_text.travas import Trava, caminho_da_trava

PID_MORTO = 0x7FFFFFF0
# O filho sobe abaixo do normal: com a CPU cheia de processos normais, ele
# recebe bem menos de 1% dela, e a subida (Python, pytest e o pacote, de 1 a
# 4 s de CPU) passou de 25 min. Este prazo cobre só a montagem; o STOP
# continua cronometrado à parte.
PRAZO_DA_MONTAGEM_S = 3600


def tarefa(t: dict) -> dict:
    """Roda no trabalhador: marca o começo e fica ocupada."""
    if t.get("marca"):
        Path(t["marca"]).write_text("comecou", encoding="utf-8")
    if t.get("gil"):
        ctypes.PyDLL("kernel32").Sleep(int(t["s"] * 1000))
    else:
        time.sleep(t["s"])
    return {}


def _esperar(condicao, limite_s: float = 90) -> None:
    fim = time.monotonic() + limite_s
    while not condicao():
        assert time.monotonic() < fim, "a condição não veio a tempo"
        time.sleep(0.02)


def _enviar_guardando_erro(t: Trabalhador, erros: list):
    """O `processar` do teste: o erro do trabalhador fica em `erros` e segue para o ciclo.

    A parada pedida pelo STOP não é erro.
    """
    def enviar(tarefa: dict):
        try:
            return t.enviar(tarefa)
        except TrabalhadorParou:
            raise
        except Exception as erro:
            erros.append(erro)
            raise
    return enviar


def _esperar_a_montagem(condicao, erros: list) -> None:
    """Espera o filho subir pelo prazo longo; o filho que falha reprova na hora."""
    _esperar(lambda: erros or condicao(), PRAZO_DA_MONTAGEM_S)
    assert not erros, [f"{type(e).__name__}: {getattr(e, 'tipo', '')}" for e in erros]


@pytest.fixture
def sem_parciais(monkeypatch):
    chamadas = []
    monkeypatch.setattr(modulo, "_limpar_parciais",
                        lambda raiz, estacao, es=None: chamadas.append((Path(raiz), estacao)) or 0)
    return chamadas


def _es(tmp_path: Path) -> ES:
    return ES([tmp_path / "origem"])


def test_comeca_parado_e_stop_ou_fechar_sem_start_nao_fazem_nada(tmp_path, sem_parciais):
    c = Ciclo(lambda: [], lambda p: None, ping_min=1)
    assert c.estado == "parado"
    c.stop()
    c.fechar()
    assert c.estado == "parado"


def test_start_liga_o_ping_e_processa_na_ordem_da_fila(tmp_path):
    fila = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    feitos, estados = [], []
    c = Ciclo(lambda: list(fila), feitos.append, ping_min=0.001, ao_mudar=estados.append)
    c.start()
    _esperar(lambda: len(feitos) >= 4)  # dois pings
    c.stop()
    assert feitos[:4] == fila + fila
    assert estados[0] == "ocioso" and "trabalhando" in estados
    assert estados[-2:] == ["parando", "parado"]
    assert c.estado == "parado"


def test_sem_arquivo_fica_ocioso(tmp_path):
    varreduras = []
    c = Ciclo(lambda: varreduras.append(1) or [], lambda p: None, ping_min=0.001)
    c.start()
    _esperar(lambda: len(varreduras) >= 3)
    assert c.estado == "ocioso"
    c.stop()


def _pings() -> set[threading.Thread]:
    """As threads de ping vivas agora. A de um teste anterior pode ainda estar
    terminando: os testes comparam o conjunto, e não a quantidade."""
    return {t for t in threading.enumerate() if t.name == "scan_to_text_ping" and t.is_alive()}


def test_start_duas_vezes_nao_cria_dois_pings(tmp_path):
    antes = _pings()
    c = Ciclo(lambda: [], lambda p: None, ping_min=1)
    c.start()
    c.start()
    try:
        assert len(_pings() - antes) == 1
    finally:
        c.stop()


def test_erro_em_processar_nao_derruba_o_ping_nem_vaza_texto(tmp_path, caplog):
    feitos = []

    def processar(p):
        if p.name == "ruim.pdf":
            raise ValueError("JOHN DOE, texto do documento")
        feitos.append(p)

    c = Ciclo(lambda: [tmp_path / "ruim.pdf", tmp_path / "bom.pdf"], processar, ping_min=0.001)
    with caplog.at_level(logging.DEBUG):
        c.start()
        _esperar(lambda: len(feitos) >= 2)
        c.stop()
    assert "ciclo_erro_ao_processar" in caplog.text
    assert "JOHN" not in caplog.text and "DOE" not in caplog.text


def test_erro_em_varrer_nao_derruba_o_ping(tmp_path):
    chamadas, feitos = [], []

    def varrer():
        chamadas.append(1)
        if len(chamadas) == 1:
            raise OSError("origem caiu")
        return [tmp_path / "a.pdf"]

    c = Ciclo(varrer, feitos.append, ping_min=0.001)
    c.start()
    _esperar(lambda: feitos)
    c.stop()


def test_stop_em_ate_10_s_com_o_trabalhador_ocupado_e_o_arquivo_volta(tmp_path):
    t = Trabalhador("test_ciclo:tarefa", ocioso_min=5)
    marca = tmp_path / "marca"
    chamadas, erros = [], []
    enviar = _enviar_guardando_erro(t, erros)

    def processar(p):
        chamadas.append(p)
        enviar({"s": 60, "marca": str(marca)})

    c = Ciclo(lambda: [tmp_path / "a.pdf"], processar, ping_min=0.001, trabalhador=t)
    try:
        c.start()
        _esperar_a_montagem(marca.exists, erros)
        assert c.estado == "trabalhando"
        inicio = time.monotonic()
        c.stop()
        assert time.monotonic() - inicio < 10
        assert c.estado == "parado" and not t.vivo
        # o arquivo interrompido é refeito no próximo START
        marca.unlink()
        c.start()
        _esperar_a_montagem(marca.exists, erros)
        c.stop()
        assert chamadas[:2] == [tmp_path / "a.pdf"] * 2
    finally:
        c.stop()
        t.parar(espera_s=2)


def test_stop_cabe_em_10_s_mesmo_com_o_filho_preso_no_gil(tmp_path):
    t = Trabalhador("test_ciclo:tarefa", ocioso_min=5)
    marca, erros = tmp_path / "marca", []
    enviar = _enviar_guardando_erro(t, erros)
    c = Ciclo(lambda: [tmp_path / "a.pdf"],
              lambda p: enviar({"s": 60, "gil": True, "marca": str(marca)}),
              ping_min=0.001, trabalhador=t)
    try:
        c.start()
        _esperar_a_montagem(marca.exists, erros)
        inicio = time.monotonic()
        c.stop()
        assert time.monotonic() - inicio < 10
        assert c.estado == "parado" and not t.vivo
    finally:
        c.stop()
        t.parar(espera_s=2)


def test_processar_que_nao_obedece_fica_parando_ate_acabar(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, "ESPERA_STOP_S", 0.5)
    solta = threading.Event()
    estados = []
    c = Ciclo(lambda: [tmp_path / "a.pdf"], lambda p: solta.wait(30), ping_min=0.001,
              ao_mudar=estados.append)
    c.start()
    _esperar(lambda: c.estado == "trabalhando")
    inicio = time.monotonic()
    c.stop()
    assert time.monotonic() - inicio < 5
    assert c.estado == "parando"  # não finge que parou
    c.start()  # ignorado enquanto para
    solta.set()
    _esperar(lambda: c.estado == "parado")
    assert estados[-2:] == ["parando", "parado"]


def test_fechar_com_prazo_curto_cabe_no_prazo_do_console(tmp_path):
    # Fechar o console dá cerca de 5 s ao processo: o prazo do STOP encolhe.
    solta = threading.Event()
    c = Ciclo(lambda: [tmp_path / "a.pdf"], lambda p: solta.wait(30), ping_min=0.001)
    c.start()
    try:
        _esperar(lambda: c.estado == "trabalhando")
        inicio = time.monotonic()
        c.fechar(espera_s=0.5)
        assert time.monotonic() - inicio < 2
        assert c.estado == "parando"
    finally:
        solta.set()
        _esperar(lambda: c.estado == "parado")


def _trava_escrita(destino: Path, sha: str, estacao: str, pid: int) -> Path:
    lp = caminho_da_trava(destino, sha)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text('{"estacao": "%s", "pid": %d, "token": "%s", "contador": 0, "inicio": 0}'
                  % (estacao, pid, sha[:32]), encoding="utf-8")
    return lp


@pytest.fixture
def processo_vivo():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield p.pid
    p.kill()
    p.wait()


def test_fechar_libera_as_proprias_travas_e_os_parciais(tmp_path, sem_parciais, processo_vivo):
    destino = tmp_path / "destino"
    destino.mkdir()
    propria = Trava(destino, "01" * 32, "ESTACAO_A", os.getpid())
    assert propria.tomar()
    alheia = Trava(destino, "02" * 32, "ESTACAO_B", 222)
    assert alheia.tomar()
    orfa = _trava_escrita(destino, "03" * 32, "ESTACAO_A", PID_MORTO)
    irma_viva = _trava_escrita(destino, "04" * 32, "ESTACAO_A", processo_vivo)
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A")
    c.start()
    c.fechar()
    assert c.estado == "parado"
    assert not propria.caminho.exists() and not orfa.exists()
    assert alheia.caminho.exists() and irma_viva.exists()
    assert sem_parciais == [(destino, "ESTACAO_A")]


def test_limpar_no_inicio_solta_orfas_apaga_parciais_e_temporarios(tmp_path, sem_parciais):
    destino = tmp_path / "destino"
    orfa = _trava_escrita(destino, "01" * 32, "ESTACAO_A", PID_MORTO)
    minha_viva = _trava_escrita(destino, "02" * 32, "ESTACAO_A", os.getpid())
    alheia = _trava_escrita(destino, "03" * 32, "ESTACAO_B", PID_MORTO)
    temp = tmp_path / "temp"
    (temp / "zip_extraido" / "dentro").mkdir(parents=True)
    (temp / "zip_extraido" / "dentro" / "x.pdf").write_bytes(b"x")
    (temp / f"{'ab' * 32}.pdf").write_bytes(b"x")
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "precioso.txt").write_bytes(b"nao apagar")
    _winapi.CreateJunction(str(fora), str(temp / "juncao"))
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
              pasta_temp=temp, es=_es(tmp_path))
    c.limpar_no_inicio()
    assert not orfa.exists()
    assert minha_viva.exists() and alheia.exists()
    assert sem_parciais == [(destino, "ESTACAO_A")]
    assert temp.is_dir() and list(temp.iterdir()) == []
    assert (fora / "precioso.txt").read_bytes() == b"nao apagar"


def test_pasta_temporaria_na_raiz_do_disco_e_recusada(tmp_path):
    with pytest.raises(ValueError, match="pasta_temp_na_raiz"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=Path("C:\\"), es=_es(tmp_path))


def test_pasta_temporaria_que_contem_um_destino_e_recusada(tmp_path):
    with pytest.raises(ValueError, match="pasta_temp_contem_destino"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[tmp_path / "temp" / "DESTINO"],
              estacao="ESTACAO_A", pasta_temp=tmp_path / "TEMP", es=_es(tmp_path))


def test_pasta_temporaria_dentro_de_origem_e_recusada(tmp_path):
    origem = tmp_path / "origem"
    origem.mkdir()
    with pytest.raises(OrigemIntocavel):
        Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=origem / "temp",
              es=ES([origem]))


def test_esvaziar_a_pasta_temporaria_nao_apaga_origem_que_esteja_dentro_dela(tmp_path):
    temp = tmp_path / "temp"
    origem = temp / "pasta" / "origem"
    origem.mkdir(parents=True)
    (origem / "scan001.pdf").write_bytes(b"da origem")
    (temp / "pasta" / "copia.pdf").write_bytes(b"x")
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=temp, es=ES([origem]))
    c.limpar_no_inicio()
    assert (origem / "scan001.pdf").read_bytes() == b"da origem"
    assert not (temp / "pasta" / "copia.pdf").exists()


def test_esvaziar_apaga_arquivo_somente_leitura(tmp_path, sem_parciais):
    temp = tmp_path / "temp"
    temp.mkdir()
    preso = temp / "extraido.pdf"
    preso.write_bytes(b"x")
    os.chmod(preso, 0o444)
    Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=temp,
          es=_es(tmp_path)).limpar_no_inicio()
    assert list(temp.iterdir()) == []


def test_limpar_no_inicio_com_o_ping_ligado_nao_apaga_nada(tmp_path, sem_parciais):
    temp = tmp_path / "temp"
    temp.mkdir()
    (temp / "em_uso.pdf").write_bytes(b"x")
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[tmp_path / "destino"],
              estacao="ESTACAO_A", pasta_temp=temp, es=_es(tmp_path))
    c.start()
    try:
        c.limpar_no_inicio()
    finally:
        c.stop()
    assert (temp / "em_uso.pdf").exists()
    assert sem_parciais == []


def test_stop_derruba_tambem_o_trabalhador_ocioso(tmp_path):
    t = Trabalhador("test_ciclo:tarefa", ocioso_min=5)
    feitos, erros = [], []
    enviar = _enviar_guardando_erro(t, erros)
    c = Ciclo(lambda: [tmp_path / "a.pdf"], lambda p: feitos.append(enviar({"s": 0})),
              ping_min=1, trabalhador=t)
    try:
        c.start()
        _esperar_a_montagem(lambda: feitos and c.estado == "ocioso", erros)
        assert t.vivo  # sem trabalho, mas ainda dentro de ocioso_min
        c.stop()
        assert not t.vivo and c.estado == "parado"
    finally:
        c.stop()
        t.parar(espera_s=2)


def test_depois_de_fechar_o_start_e_ignorado(tmp_path, sem_parciais):
    estados = []
    antes = _pings()
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, ao_mudar=estados.append)
    c.start()
    c.fechar()
    c.start()
    assert c.estado == "parado"
    assert _pings() - antes == set()
    assert estados == ["ocioso", "parando", "parado"]


def test_erro_no_aviso_de_estado_nao_derruba_o_ping(tmp_path):
    feitos = []

    def avisar(estado):
        raise RuntimeError("tela fora do ar")

    c = Ciclo(lambda: [tmp_path / "a.pdf"], feitos.append, ping_min=0.001, ao_mudar=avisar)
    c.start()
    _esperar(lambda: len(feitos) >= 2)
    c.stop()
    assert c.estado == "parado"


def test_destino_inacessivel_nao_impede_limpar_os_outros(tmp_path, monkeypatch):
    ruim, bom = tmp_path / "ruim", tmp_path / "bom"
    orfa = _trava_escrita(bom, "01" * 32, "ESTACAO_A", PID_MORTO)
    limpos = []

    def limpar(raiz, estacao, es=None):
        if Path(raiz) == ruim:
            raise OSError("destino fora do ar")
        limpos.append(Path(raiz))
        return 0

    monkeypatch.setattr(modulo, "_limpar_parciais", limpar)
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[ruim, bom], estacao="ESTACAO_A")
    c.limpar_no_inicio()
    c.fechar()
    assert not orfa.exists()
    assert limpos == [bom, bom]


def test_parciais_da_estacao_somem_no_inicio_com_o_gravador_real(tmp_path):
    atomico = pytest.importorskip("scan_to_text.escrever.atomico")
    destino = tmp_path / "destino"
    alvo = destino / "PACIENTE DE TESTE - 2026-09-28" / "COMPLETO.md"
    alvo.parent.mkdir(parents=True)
    parcial = atomico.nome_parcial(alvo, "ESTACAO_A", PID_MORTO)
    parcial.write_bytes(b"pela metade")
    Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino],
          estacao="ESTACAO_A").limpar_no_inicio()
    assert not parcial.exists()


def test_sinal_de_trabalhando_da_estacao_some_no_inicio(tmp_path):
    destino = tmp_path / "destino"
    proprio = destino / "PACIENTE DE TESTE - 2026-09-28" / "_scan-to-text" / "trabalhando.json"
    alheio = destino / "OUTRO PACIENTE - 2026-09-28" / "_scan-to-text" / "trabalhando.json"
    for sinal, estacao in ((proprio, "ESTACAO_A"), (alheio, "ESTACAO_B")):
        sinal.parent.mkdir(parents=True)
        sinal.write_text(json.dumps({"estacao": estacao, "pid": PID_MORTO}), encoding="utf-8")
    Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino],
          estacao="ESTACAO_A").limpar_no_inicio()
    assert not proprio.exists() and alheio.exists()



# ---------------------------------------------------------------------------
# Guardas da pasta temporária
# ---------------------------------------------------------------------------

def test_pasta_temporaria_sem_es_e_recusada_e_a_origem_dentro_dela_fica(tmp_path):
    temp = tmp_path / "temp"
    origem = temp / "origem"
    origem.mkdir(parents=True)
    (origem / "scan.pdf").write_bytes(b"da origem")
    with pytest.raises(ValueError, match="pasta_temp_sem_es"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=temp)
    assert (origem / "scan.pdf").read_bytes() == b"da origem"


def _destino_com_paciente(raiz: Path) -> Path:
    destino = raiz / "DESTINO"
    (destino / "PACIENTE TESTE").mkdir(parents=True)
    (destino / "PACIENTE TESTE" / "exame.md").write_bytes(b"laudo")
    return destino


def test_pasta_temporaria_que_e_juncao_para_o_destino_e_recusada(tmp_path):
    destino = _destino_com_paciente(tmp_path)
    temp = tmp_path / "temp"
    _winapi.CreateJunction(str(destino), str(temp))
    with pytest.raises(ValueError, match="pasta_temp_e_link"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
              pasta_temp=temp, es=_es(tmp_path))
    assert (destino / "PACIENTE TESTE" / "exame.md").read_bytes() == b"laudo"


def test_pasta_temporaria_que_e_juncao_para_pasta_acima_do_destino_e_recusada(tmp_path):
    acima = tmp_path / "acima"
    destino = _destino_com_paciente(acima)
    temp = tmp_path / "temp"
    _winapi.CreateJunction(str(acima), str(temp))
    with pytest.raises(ValueError):
        Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
              pasta_temp=temp, es=_es(tmp_path))
    assert (destino / "PACIENTE TESTE" / "exame.md").read_bytes() == b"laudo"


def test_pasta_temporaria_que_contem_o_destino_por_juncao_no_caminho_e_recusada(tmp_path):
    # A própria pasta não é junção; uma pasta acima dela é. Pelo texto, a
    # temporária não contém o destino; pelo caminho real, contém.
    real = tmp_path / "real"
    destino = _destino_com_paciente(real / "temp")
    atalho = tmp_path / "atalho"
    _winapi.CreateJunction(str(real), str(atalho))
    with pytest.raises(ValueError, match="pasta_temp_contem_destino"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
              pasta_temp=atalho / "temp", es=_es(tmp_path))


def test_pasta_temporaria_que_vira_juncao_depois_nao_e_esvaziada(tmp_path, sem_parciais):
    destino = _destino_com_paciente(tmp_path)
    temp = tmp_path / "temp"
    temp.mkdir()
    c = Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
              pasta_temp=temp, es=_es(tmp_path))
    temp.rmdir()
    _winapi.CreateJunction(str(destino), str(temp))
    c.limpar_no_inicio()
    assert (destino / "PACIENTE TESTE" / "exame.md").read_bytes() == b"laudo"


@pytest.mark.parametrize("pasta", [
    pytest.param(lambda: Path.home(), id="perfil"),
    pytest.param(lambda: Path.home().parent, id="acima_do_perfil"),
    pytest.param(lambda: Path(os.environ["LOCALAPPDATA"]), id="localappdata"),
    pytest.param(lambda: Path(tempfile.gettempdir()), id="temporaria_do_sistema"),
    pytest.param(lambda: Path(modulo.__file__).resolve().parents[1], id="pasta_do_app"),
])
def test_pasta_temporaria_que_contem_pasta_alheia_e_recusada(tmp_path, pasta):
    # Só a construção: se a guarda falhar, nada é apagado.
    with pytest.raises(ValueError, match="pasta_temp_contem_pasta_alheia"):
        Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=pasta(), es=_es(tmp_path))


def test_esvaziar_apaga_pasta_somente_leitura(tmp_path, sem_parciais):
    temp = tmp_path / "temp"
    presa = temp / "extraido"
    presa.mkdir(parents=True)
    (presa / "x.pdf").write_bytes(b"x")
    os.chmod(presa, 0o444)
    Ciclo(lambda: [], lambda p: None, ping_min=1, pasta_temp=temp,
          es=_es(tmp_path)).limpar_no_inicio()
    assert list(temp.iterdir()) == []


def test_parcial_dentro_de_origem_no_destino_fica(tmp_path):
    atomico = pytest.importorskip("scan_to_text.escrever.atomico")
    destino = tmp_path / "destino"
    origem = destino / "origem"
    origem.mkdir(parents=True)
    na_origem = atomico.nome_parcial(origem / "x.md", "ESTACAO_A", PID_MORTO)
    na_origem.write_bytes(b"da origem")
    no_destino = atomico.nome_parcial(destino / "y.md", "ESTACAO_A", PID_MORTO)
    no_destino.write_bytes(b"pela metade")
    Ciclo(lambda: [], lambda p: None, ping_min=1, destinos=[destino], estacao="ESTACAO_A",
          es=ES([origem])).limpar_no_inicio()
    assert na_origem.read_bytes() == b"da origem"
    assert not no_destino.exists()


# ---------------------------------------------------------------------------
# Disco cheio e destino inacessível
# ---------------------------------------------------------------------------

def _encadeado() -> BaseException:
    try:
        raise OSError(errno.ENOSPC, "sem_espaco")
    except OSError as causa:
        try:
            raise RuntimeError("falha_ao_gravar") from causa
        except RuntimeError as erro:
            return erro


@pytest.mark.parametrize("erro", [
    pytest.param(lambda: OSError(errno.ENOSPC, "sem_espaco"), id="enospc"),
    pytest.param(lambda: OSError(None, "sem_espaco", None, 112), id="error_disk_full"),
    pytest.param(lambda: OSError(None, "sem_espaco", None, 39), id="error_handle_disk_full"),
    pytest.param(lambda: ErroNaTarefa("OSError", "", errno=errno.ENOSPC, winerror=112),
                 id="no_trabalhador"),
    pytest.param(_encadeado, id="encadeado"),
])
def testdisco_cheio_pausa_a_rodada_com_aviso_e_tenta_de_novo_no_proximo_ping(tmp_path, erro):
    fila = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    tentativas = []
    cheio = threading.Event()
    cheio.set()

    def processar(p):
        tentativas.append(p.name)
        if cheio.is_set():
            raise erro()

    c = Ciclo(lambda: list(fila), processar, ping_min=0.001)
    c.start()
    try:
        _esperar(lambda: tentativas.count("a.pdf") >= 3)  # tenta de novo a cada ping
        assert "b.pdf" not in tentativas  # a rodada para no primeiro
        assert c.avisos == ["disco_cheio"]
        cheio.clear()
        _esperar(lambda: "b.pdf" in tentativas)
        _esperar(lambda: c.avisos == [])
    finally:
        c.stop()
    assert c.estado == "parado" and c.avisos == []


@pytest.mark.parametrize("erro", [
    pytest.param(lambda: ErroNaTarefa("LeitorIndisponivel", ""), id="modelos_no_trabalhador"),
    pytest.param(lambda: AlvoIndisponivel("ModuleNotFoundError", ""), id="tarefa_que_nao_importa"),
    pytest.param(lambda: AlvoIndisponivel("SyntaxError", ""), id="tarefa_que_nao_compila"),
    pytest.param(lambda: modulo.LeitorIndisponivel("leitor_indisponivel"), id="local"),
])
def test_leitor_indisponivel_pausa_a_rodada_com_aviso_e_tenta_de_novo(tmp_path, erro):
    fila = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    tentativas = []
    quebrado = threading.Event()
    quebrado.set()

    def processar(p):
        tentativas.append(p.name)
        if quebrado.is_set():
            raise erro()

    c = Ciclo(lambda: list(fila), processar, ping_min=0.001)
    c.start()
    try:
        _esperar(lambda: tentativas.count("a.pdf") >= 3)
        assert "b.pdf" not in tentativas  # a leitura para: os seguintes falhariam igual
        assert c.avisos == ["leitor_indisponivel"]
        quebrado.clear()
        _esperar(lambda: "b.pdf" in tentativas)
        _esperar(lambda: c.avisos == [])
    finally:
        c.stop()


def test_falha_desculpada_encerra_a_rodada_sem_aviso_que_pausa(tmp_path, caplog):
    # A estação sem CPU ou sem memória: os de trás dariam com ela também, e cada
    # um gastaria uma desculpa sua. O aviso da estação é do motor e não pausa.
    fila = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    tentativas = []

    def processar(p):
        tentativas.append(p.name)
        if p.name == "a.pdf":
            try:
                raise ErroNaTarefa("ConversionError", "document_converter.py:1 convert")
            except ErroNaTarefa as causa:
                raise modulo.FalhaDesculpada("falha_desculpada") from causa

    c = Ciclo(lambda: list(fila), processar, ping_min=0.001)
    with caplog.at_level(logging.WARNING, logger="scan_to_text.ciclo"):
        c.start()
        try:
            _esperar(lambda: tentativas.count("a.pdf") >= 3)
            assert "b.pdf" not in tentativas
            assert c.avisos == []
        finally:
            c.stop()
    registros = [r.getMessage() for r in caplog.records
                 if "ciclo_falha_desculpada" in r.getMessage()]
    # O log diz qual falha a estação explicou, com os quadros do filho.
    assert registros and "tipo_no_trabalhador=ConversionError" in registros[0]
    assert "document_converter.py:1 convert" in registros[0]
    assert not modulo.falha_de_ambiente(modulo.FalhaDesculpada("falha_desculpada"))


def test_filho_que_falha_reprova_a_espera_da_montagem_sem_esperar_o_prazo(tmp_path):
    # O prazo da montagem é longo para o filho lento, não para o quebrado.
    t = Trabalhador("modulo_que_nao_existe:tarefa", ocioso_min=5)
    erros = []
    enviar = _enviar_guardando_erro(t, erros)
    c = Ciclo(lambda: [tmp_path / "a.pdf"], lambda p: enviar({"s": 0}), ping_min=0.001,
              trabalhador=t)
    try:
        c.start()
        # Sai pelo erro do filho, e não pelo prazo ("a condição não veio a tempo").
        with pytest.raises(AssertionError, match="AlvoIndisponivel"):
            _esperar_a_montagem(lambda: False, erros)
    finally:
        c.stop()
        t.parar(espera_s=2)


@pytest.mark.parametrize("erro", [
    pytest.param(lambda: ErroNaTarefa("MemoryError", ""), id="memoria_no_trabalhador"),
    pytest.param(lambda: ErroNaTarefa("ImportError", ""), id="biblioteca_no_trabalhador"),
    pytest.param(lambda: ErroNaTarefa("ModuleNotFoundError", ""), id="modulo_no_trabalhador"),
    pytest.param(lambda: MemoryError(), id="memoria_local"),
    pytest.param(lambda: ImportError("biblioteca"), id="biblioteca_local"),
])
def test_memoria_ou_biblioteca_que_falta_num_arquivo_nao_pausa_a_rodada(tmp_path, erro):
    # A carga do leitor chega como LeitorIndisponivel; estes nasceram na
    # leitura de um arquivo e não impedem a dos outros.
    feitos = []

    def processar(p):
        if p.name == "a.pdf":
            raise erro()
        feitos.append(p.name)

    c = Ciclo(lambda: [tmp_path / "a.pdf", tmp_path / "b.pdf"], processar, ping_min=0.001)
    c.start()
    try:
        _esperar(lambda: feitos, 30)  # o b.pdf sai na mesma rodada
        assert c.avisos == []
    finally:
        c.stop()
    assert not modulo.falha_de_ambiente(erro())


def testdisco_cheio_sem_arquivo_na_fila_retira_o_aviso(tmp_path):
    fila = [tmp_path / "a.pdf"]

    def processar(p):
        raise OSError(errno.ENOSPC, "sem_espaco")

    c = Ciclo(lambda: list(fila), processar, ping_min=0.001)
    c.start()
    try:
        _esperar(lambda: c.avisos == ["disco_cheio"])
        fila.clear()  # o arquivo saiu da origem: não há mais o que pausar
        _esperar(lambda: c.avisos == [])
    finally:
        c.stop()


def test_destino_inacessivel_avisa_e_os_arquivos_esperam_na_fila(tmp_path):
    destino = tmp_path / "destino"  # ainda não existe: fora do ar
    varreduras, feitos = [], []
    c = Ciclo(lambda: varreduras.append(1) or [tmp_path / "a.pdf"], feitos.append,
              ping_min=0.001, destinos=[destino], estacao="ESTACAO_A")
    c.start()
    try:
        _esperar(lambda: len(varreduras) >= 3)
        assert c.avisos == ["destino_inacessivel"]
        assert feitos == []
        destino.mkdir()
        _esperar(lambda: feitos)
        _esperar(lambda: c.avisos == [])
    finally:
        c.stop()


def test_destino_que_cai_no_meio_da_rodada_para_a_rodada_com_aviso(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    varreduras, tentativas = [], []

    def processar(p):
        tentativas.append(p.name)
        if destino.exists():
            destino.rmdir()
        raise OSError(None, "rede_caiu", None, 64)  # ERROR_NETNAME_DELETED

    c = Ciclo(lambda: varreduras.append(1) or [tmp_path / "a.pdf", tmp_path / "b.pdf"],
              processar, ping_min=0.001, destinos=[destino], estacao="ESTACAO_A")
    c.start()
    try:
        _esperar(lambda: c.avisos == ["destino_inacessivel"])
        ja = len(varreduras)
        _esperar(lambda: len(varreduras) >= ja + 3)
        assert tentativas == ["a.pdf"]
    finally:
        c.stop()


def test_destino_que_some_entre_dois_arquivos_para_a_rodada(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    varreduras, tentativas = [], []

    def processar(p):
        tentativas.append(p.name)
        if p.name == "a.pdf" and destino.exists():
            destino.rmdir()  # o primeiro termina bem; o destino cai logo depois

    c = Ciclo(lambda: varreduras.append(1) or [tmp_path / "a.pdf", tmp_path / "b.pdf"],
              processar, ping_min=0.001, destinos=[destino], estacao="ESTACAO_A")
    c.start()
    try:
        _esperar(lambda: c.avisos == ["destino_inacessivel"])
        ja = len(varreduras)
        _esperar(lambda: len(varreduras) >= ja + 3)
        assert tentativas == ["a.pdf"]
    finally:
        c.stop()


def test_erro_comum_ao_processar_nao_vira_aviso(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    feitos = []

    def processar(p):
        if p.name == "ruim.pdf":
            raise OSError(errno.EINVAL, "arquivo_corrompido")
        feitos.append(p)

    c = Ciclo(lambda: [tmp_path / "ruim.pdf", tmp_path / "bom.pdf"], processar, ping_min=0.001,
              destinos=[destino], estacao="ESTACAO_A")
    c.start()
    try:
        _esperar(lambda: len(feitos) >= 2)  # o bom segue na mesma rodada
        assert c.avisos == []
    finally:
        c.stop()


# ---------------------------------------------------------------------------
# Fechar com `processar` que não obedece
# ---------------------------------------------------------------------------

def test_fechar_com_processar_que_nao_obedece_nao_solta_a_trava_em_uso(tmp_path, sem_parciais,
                                                                       monkeypatch):
    monkeypatch.setattr(modulo, "ESPERA_STOP_S", 0.5)
    destino = tmp_path / "destino"
    destino.mkdir()
    sha = "05" * 32
    trava = Trava(destino, sha, "ESTACAO_A", os.getpid())
    solta = threading.Event()

    def processar(p):
        assert trava.tomar()
        try:
            solta.wait(30)
        finally:
            trava.soltar()

    c = Ciclo(lambda: [tmp_path / "a.pdf"], processar, ping_min=0.001, destinos=[destino],
              estacao="ESTACAO_A")
    c.start()
    try:
        _esperar(trava.e_minha)
        c.fechar()
        assert c.estado == "parando"
        assert trava.e_minha()
        assert not Trava(destino, sha, "ESTACAO_B", 999).tomar()
        assert sem_parciais == []  # o `.parcial` do arquivo em curso fica
    finally:
        solta.set()
        _esperar(lambda: c.estado == "parado")
    assert not trava.caminho.exists()
