"""Travas por arquivo no destino: O_EXCL, contador, parada e tomada por renomeação."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scan_to_text import travas
from scan_to_text.travas import Batimento, Trava, soltar_orfas

SHA = "ab" * 32
# PID que o Windows nunca entrega: OpenProcess falha com parâmetro inválido.
PID_MORTO = 0x7FFFFFF0


class Relogio:
    def __init__(self, inicio: float = 1000.0):
        self.t = inicio

    def __call__(self) -> float:
        return self.t

    def andar(self, s: float) -> None:
        self.t += s


def _caminho(destino: Path, sha: str = SHA) -> Path:
    return destino / "_scan-to-text" / "travas" / f"{sha}.lock"


def _ler(destino: Path, sha: str = SHA) -> dict:
    return json.loads(_caminho(destino, sha).read_text(encoding="utf-8"))


def _escrever(destino: Path, dado: dict, sha: str = SHA) -> Path:
    lp = _caminho(destino, sha)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(dado), encoding="utf-8")
    return lp


def test_tomar_cria_a_trava_com_os_campos_do_protocolo(tmp_path):
    t = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    assert t.tomar() is True
    d = _ler(tmp_path)
    assert d["estacao"] == "ESTACAO_A" and d["pid"] == 111 and d["contador"] == 0
    assert len(d["token"]) >= 16 and isinstance(d["inicio"], (int, float))
    assert t.e_minha()


def test_trava_viva_de_outra_estacao_nao_e_tomada(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    assert a.tomar()
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222)
    assert b.tomar() is False
    assert _ler(tmp_path)["estacao"] == "ESTACAO_A"
    assert not b.e_minha()


def test_tomar_de_novo_a_propria_trava_vale_sem_regravar(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    assert a.tomar()
    token = _ler(tmp_path)["token"]
    assert a.tomar() is True
    assert _ler(tmp_path)["token"] == token


def test_renovar_soma_um_ao_contador(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    a.renovar()
    a.renovar()
    assert _ler(tmp_path)["contador"] == 2


def test_e_minha_espera_o_batimento_da_propria_instancia(tmp_path, monkeypatch):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    assert a.tomar()
    truncou, liberar = threading.Event(), threading.Event()
    consultando, terminou = threading.Event(), threading.Event()
    resultado = []
    codificar = travas._codificar

    def depois_do_truncate(dado):
        codificado = codificar(dado)
        truncou.set()
        assert liberar.wait(5)
        return codificado

    def consultar():
        consultando.set()
        resultado.append(a.e_minha())
        terminou.set()

    monkeypatch.setattr(travas, "_codificar", depois_do_truncate)
    renovacao = threading.Thread(target=a.renovar)
    consulta = threading.Thread(target=consultar)
    try:
        renovacao.start()
        assert truncou.wait(5)
        assert _caminho(tmp_path).is_file() and _caminho(tmp_path).read_bytes() == b""
        consulta.start()
        assert consultando.wait(5)
        assert not terminou.wait(0.1)
        liberar.set()
        renovacao.join(5)
        consulta.join(5)
        assert not renovacao.is_alive() and not consulta.is_alive()
        assert resultado == [True]
        assert _ler(tmp_path)["contador"] == 1
    finally:
        liberar.set()
        renovacao.join(5)
        if consulta.ident is not None:
            consulta.join(5)
        a.soltar()


def test_renovar_nao_ressuscita_trava_apagada(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    _caminho(tmp_path).unlink()
    a.renovar()
    assert not _caminho(tmp_path).exists()
    assert not a.e_minha()


def test_renovar_nao_escreve_na_trava_de_outro(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    alheia = {"estacao": "ESTACAO_B", "pid": 222, "token": "b" * 32, "contador": 4,
              "inicio": 0}
    _escrever(tmp_path, alheia)
    a.renovar()
    assert _ler(tmp_path) == alheia
    assert not a.e_minha()


def test_soltar_apaga_so_a_propria(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222)
    b.soltar()
    assert _caminho(tmp_path).exists()
    a.soltar()
    assert not _caminho(tmp_path).exists()
    assert not a.e_minha()
    assert list(_caminho(tmp_path).parent.iterdir()) == []


def test_parada_conta_15_min_no_relogio_de_quem_observa(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    r = Relogio()
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222, relogio=r)
    assert not b.tomar()
    r.andar(15 * 60 - 1)
    assert not b.tomar()
    r.andar(1)
    assert b.tomar() is True
    d = _ler(tmp_path)
    assert d["estacao"] == "ESTACAO_B" and d["contador"] == 0
    # o dono antigo, se voltar, não sobrescreve nem apaga a trava do novo dono
    assert not a.e_minha()
    a.renovar()
    a.soltar()
    assert _ler(tmp_path)["estacao"] == "ESTACAO_B"
    assert b.e_minha()


def test_batimento_pelo_contador_impede_a_tomada(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    r = Relogio()
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222, relogio=r)
    for _ in range(12):  # uma hora no relógio de B, com batimento a cada 5 min
        assert not b.tomar()
        r.andar(5 * 60)
        a.renovar()
    assert not b.tomar()
    assert _ler(tmp_path)["estacao"] == "ESTACAO_A"


def test_relogio_adiantado_de_outra_estacao_nao_toma_trava_viva(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    # o relógio de parede de A e o de B divergem em anos: "inicio" parece velho
    d = _ler(tmp_path)
    d["inicio"] = d["inicio"] - 10 * 365 * 86400
    _escrever(tmp_path, d)
    r = Relogio(inicio=9e9)
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222, relogio=r)
    assert not b.tomar()  # a primeira observação nunca toma
    r.andar(14 * 60)
    a.renovar()
    assert not b.tomar()
    r.andar(14 * 60)
    assert not b.tomar()
    assert _ler(tmp_path)["estacao"] == "ESTACAO_A"
    # controle: sem batimento, os 15 min no relógio de B bastam
    r.andar(15 * 60)
    assert b.tomar() is True


def test_orfa_da_mesma_estacao_com_pid_morto_e_tomada_na_hora(tmp_path):
    _escrever(tmp_path, {"estacao": "ESTACAO_A", "pid": PID_MORTO, "token": "e" * 32,
                         "contador": 9, "inicio": 0})
    nova = Trava(tmp_path, SHA, "ESTACAO_A", os.getpid())
    assert nova.tomar() is True
    assert _ler(tmp_path)["pid"] == os.getpid()


def test_orfa_do_proprio_processo_e_retomada_na_hora(tmp_path):
    _escrever(tmp_path, {"estacao": "ESTACAO_A", "pid": os.getpid(), "token": "f" * 32,
                         "contador": 3, "inicio": 0})
    t = Trava(tmp_path, SHA, "ESTACAO_A", os.getpid())
    assert t.tomar() is True
    assert _ler(tmp_path)["token"] != "f" * 32


def test_trava_segurada_por_este_processo_nao_e_retomada(tmp_path):
    a1 = Trava(tmp_path, SHA, "ESTACAO_A", os.getpid())
    assert a1.tomar()
    a2 = Trava(tmp_path, SHA, "ESTACAO_A", os.getpid())
    assert a2.tomar() is False
    assert a1.e_minha()


def test_trava_vazia_parada_e_tomada_depois_de_15_min(tmp_path):
    lp = _caminho(tmp_path)
    lp.parent.mkdir(parents=True)
    lp.write_bytes(b"")  # criada pelo O_EXCL e nunca escrita: o dono caiu no meio
    r = Relogio()
    b = Trava(tmp_path, SHA, "ESTACAO_B", 222, relogio=r)
    assert not b.tomar()
    r.andar(15 * 60)
    assert b.tomar() is True


def test_tomada_devolve_a_trava_nova_que_trocou_no_caminho(tmp_path):
    nova = {"estacao": "ESTACAO_C", "pid": 1, "token": "c" * 32, "contador": 0, "inicio": 0}
    lp = _escrever(tmp_path, nova)
    velha_observada = dict(nova, token="d" * 32, contador=7)
    assert travas._tomar_por_rename(lp, velha_observada, "ESTACAO_B", 222) is False
    assert json.loads(lp.read_text(encoding="utf-8")) == nova
    assert list(lp.parent.iterdir()) == [lp]


def test_soltar_orfas_libera_so_as_desta_estacao_com_pid_morto(tmp_path):
    _escrever(tmp_path, {"estacao": "ESTACAO_A", "pid": PID_MORTO, "token": "1" * 32,
                         "contador": 0, "inicio": 0}, sha="01" * 32)
    _escrever(tmp_path, {"estacao": "ESTACAO_A", "pid": os.getpid(), "token": "2" * 32,
                         "contador": 0, "inicio": 0}, sha="02" * 32)
    _escrever(tmp_path, {"estacao": "ESTACAO_B", "pid": PID_MORTO, "token": "3" * 32,
                         "contador": 0, "inicio": 0}, sha="03" * 32)
    assert soltar_orfas(tmp_path, "ESTACAO_A", travas.pid_vivo) == 1
    restantes = sorted(p.name for p in _caminho(tmp_path).parent.iterdir())
    assert restantes == [f"{'02' * 32}.lock", f"{'03' * 32}.lock"]


def test_soltar_orfas_sem_pasta_de_travas_devolve_zero(tmp_path):
    assert soltar_orfas(tmp_path, "ESTACAO_A", travas.pid_vivo) == 0


def test_pid_vivo():
    assert travas.pid_vivo(os.getpid()) is True
    assert travas.pid_vivo(PID_MORTO) is False
    assert travas.pid_vivo(0) is False
    assert travas.pid_vivo(-5) is False


def test_destino_inacessivel_levanta_em_vez_de_calar(tmp_path):
    arquivo = tmp_path / "nao_e_pasta"
    arquivo.write_bytes(b"x")
    with pytest.raises(OSError):
        Trava(arquivo, SHA, "ESTACAO_A", 111).tomar()


def test_tomar_nao_recria_destino_que_sumiu(tmp_path):
    destino = tmp_path / "sumiu"
    with pytest.raises(OSError):
        Trava(destino, SHA, "ESTACAO_A", 111).tomar()
    assert not destino.exists()


# parada por instância e observação fora do processo ------------------------------

ALHEIA = {"estacao": "ESTACAO_MORTA", "pid": 1, "token": "9" * 32, "contador": 5, "inicio": 0}


def test_parada_por_instancia_nao_muda_o_padrao(tmp_path):
    _escrever(tmp_path, ALHEIA)
    r = Relogio()
    curta = Trava(tmp_path, SHA, "ESTACAO_B", 222, relogio=r, parada_s=60)
    padrao = Trava(tmp_path, SHA, "ESTACAO_C", 333, relogio=r)
    assert not curta.tomar() and not padrao.tomar()
    r.andar(59)
    assert not curta.tomar()
    r.andar(1)
    assert not padrao.tomar()
    assert curta.tomar() is True


def test_observacao_guardada_em_arquivo_vale_para_o_trabalhador_seguinte(tmp_path):
    destino, arquivo = tmp_path / "destino", tmp_path / "local" / "observadas.json"
    destino.mkdir()
    (tmp_path / "local").mkdir()
    _escrever(destino, ALHEIA)
    r = Relogio()
    assert not Trava(destino, SHA, "ESTACAO_B", 222, relogio=r, observadas=arquivo).tomar()
    travas._OBSERVADAS.clear()
    r.andar(15 * 60)
    # Trabalhador novo: outro PID, memória vazia, o mesmo relógio da máquina.
    novo = Trava(destino, SHA, "ESTACAO_B", 333, relogio=r, observadas=arquivo)
    assert novo.tomar() is True
    assert _ler(destino)["estacao"] == "ESTACAO_B"


def test_observacao_vem_da_variavel_de_ambiente(tmp_path, monkeypatch):
    destino, arquivo = tmp_path / "destino", tmp_path / "observadas.json"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    monkeypatch.setenv(travas.VARIAVEL_OBSERVADAS, str(arquivo))
    r = Relogio()
    assert not Trava(destino, SHA, "ESTACAO_B", 222, relogio=r).tomar()
    assert arquivo.is_file()
    travas._OBSERVADAS.clear()
    r.andar(15 * 60)
    assert Trava(destino, SHA, "ESTACAO_B", 333, relogio=r).tomar() is True


def test_sem_variavel_nem_arquivo_a_observacao_fica_so_na_memoria(tmp_path, monkeypatch):
    monkeypatch.delenv(travas.VARIAVEL_OBSERVADAS, raising=False)
    destino = tmp_path / "destino"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    r = Relogio()
    assert not Trava(destino, SHA, "ESTACAO_B", 222, relogio=r).tomar()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["destino"]
    travas._OBSERVADAS.clear()
    r.andar(15 * 60)
    assert not Trava(destino, SHA, "ESTACAO_B", 333, relogio=r).tomar()


def test_relogio_que_voltou_recomeca_a_observacao(tmp_path):
    arquivo = tmp_path / "observadas.json"
    destino = tmp_path / "destino"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    antes = Relogio(inicio=50_000.0)
    assert not Trava(destino, SHA, "ESTACAO_B", 222, relogio=antes, observadas=arquivo).tomar()
    depois = Relogio(inicio=10.0)  # a máquina reiniciou: o relógio monotônico recomeçou
    b = Trava(destino, SHA, "ESTACAO_B", 333, relogio=depois, observadas=arquivo)
    assert not b.tomar()
    depois.andar(15 * 60 - 1)
    assert not b.tomar()
    depois.andar(1)
    assert b.tomar() is True


def test_arquivo_de_observacoes_ilegivel_recomeca_sem_erro(tmp_path):
    arquivo = tmp_path / "observadas.json"
    arquivo.write_bytes(b"\xff{nao e json")
    destino = tmp_path / "destino"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    r = Relogio()
    b = Trava(destino, SHA, "ESTACAO_B", 222, relogio=r, observadas=arquivo)
    assert not b.tomar()
    r.andar(15 * 60)
    assert b.tomar() is True


def test_arquivo_de_observacoes_que_nao_grava_cai_na_memoria(tmp_path):
    arquivo = tmp_path / "pasta_que_nao_existe" / "observadas.json"
    destino = tmp_path / "destino"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    r = Relogio()
    b = Trava(destino, SHA, "ESTACAO_B", 222, relogio=r, observadas=arquivo)
    assert not b.tomar()
    r.andar(15 * 60)
    assert b.tomar() is True
    assert not arquivo.parent.exists()


_OBSERVADOR = r'''
import json, os, sys
from pathlib import Path
sys.path[:0] = json.loads(os.environ["CAMINHOS_TESTE"])
from scan_to_text.travas import Trava
destino, sha, arquivo = sys.argv[1:4]
t = Trava(Path(destino), sha, "ESTACAO_B", os.getpid(), parada_s=1.0, observadas=Path(arquivo))
print(json.dumps({"ok": t.tomar()}), flush=True)
'''


def test_observacao_vale_entre_processos_pelo_relogio_monotonico_da_maquina(tmp_path):
    destino, arquivo = tmp_path / "destino", tmp_path / "observadas.json"
    destino.mkdir()
    _escrever(destino, ALHEIA)
    env = dict(os.environ, CAMINHOS_TESTE=json.dumps(sys.path))

    def observar() -> bool:
        saida = subprocess.run([sys.executable, "-c", _OBSERVADOR, str(destino), SHA,
                                str(arquivo)], capture_output=True, env=env, timeout=60)
        return json.loads(saida.stdout)["ok"]

    assert observar() is False
    time.sleep(1.2)
    assert observar() is True
    assert _ler(destino)["estacao"] == "ESTACAO_B"


def test_batimento_renova_em_segundo_plano_e_para_ao_sair(tmp_path):
    a = Trava(tmp_path, SHA, "ESTACAO_A", 111)
    a.tomar()
    with Batimento([a], intervalo_s=0.05):
        time.sleep(0.5)
    contador = _ler(tmp_path)["contador"]
    assert contador >= 2
    time.sleep(0.2)
    assert _ler(tmp_path)["contador"] == contador
    a.soltar()
    assert not _caminho(tmp_path).exists()


# ---------------------------------------------------------------------------
# Disputa entre processos de verdade
# ---------------------------------------------------------------------------

_DISPUTANTE = r'''
import json, os, sys, time
from pathlib import Path
sys.path[:0] = json.loads(os.environ["CAMINHOS_TESTE"])
from scan_to_text.travas import Trava

destino, sha, estacao, pasta_sinal, modo, indice = sys.argv[1:7]
sinal = Path(pasta_sinal)

def esperar(nome):
    limite = time.monotonic() + 60
    while not (sinal / nome).exists():
        if time.monotonic() > limite:
            raise SystemExit("largada nao veio")
        time.sleep(0.001)

if modo == "livre":
    t = Trava(Path(destino), sha, estacao, os.getpid())
    (sinal / f"pronto_{indice}").write_text("")
    esperar("largada")
    ok = t.tomar()
else:
    tempos = iter([0.0, 10_000.0])
    t = Trava(Path(destino), sha, estacao, os.getpid(), relogio=lambda: next(tempos))
    esperar("largada")
    primeira = t.tomar()
    (sinal / f"pronto_{indice}").write_text("")
    esperar("largada2")
    ok = (not primeira) and t.tomar()
print(json.dumps({"ok": ok, "minha": t.e_minha()}), flush=True)
'''


def _disputar(tmp_path: Path, modo: str, n: int = 8) -> list[dict]:
    destino = tmp_path / "destino"
    sinal = tmp_path / "sinal"
    destino.mkdir()
    sinal.mkdir()
    if modo == "parada":
        _escrever(destino, {"estacao": "ESTACAO_MORTA", "pid": 1, "token": "9" * 32,
                            "contador": 5, "inicio": 0})
    env = dict(os.environ, CAMINHOS_TESTE=json.dumps(sys.path))
    procs = [subprocess.Popen([sys.executable, "-c", _DISPUTANTE, str(destino), SHA,
                               f"ESTACAO_{i}", str(sinal), modo, str(i)],
                              stdout=subprocess.PIPE, env=env)
             for i in range(n)]
    try:
        if modo == "livre":
            _esperar_arquivos(sinal, n)
            (sinal / "largada").write_text("")
        else:
            (sinal / "largada").write_text("")
            _esperar_arquivos(sinal, n)
            (sinal / "largada2").write_text("")
        saidas = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    return saidas


def _esperar_arquivos(pasta: Path, n: int) -> None:
    limite = time.monotonic() + 60
    while len(list(pasta.glob("pronto_*"))) < n:
        assert time.monotonic() < limite, "os disputantes não ficaram prontos"
        time.sleep(0.02)


def test_processos_disputando_trava_livre_um_so_vence(tmp_path):
    saidas = _disputar(tmp_path, "livre")
    assert sum(s["ok"] for s in saidas) == 1
    assert sum(s["minha"] for s in saidas) == 1


def test_processos_disputando_trava_parada_um_so_vence(tmp_path):
    saidas = _disputar(tmp_path, "parada")
    assert sum(s["ok"] for s in saidas) == 1
    assert sum(s["minha"] for s in saidas) == 1
    assert _ler(tmp_path / "destino")["estacao"] != "ESTACAO_MORTA"
    sobras = [p.name for p in _caminho(tmp_path / "destino").parent.iterdir()]
    assert sobras == [f"{SHA}.lock"]


# tomada por handle ------------------------------------------------------------

PARADA = {"estacao": "ESTACAO_MORTA", "pid": 1, "token": "9" * 32, "contador": 5, "inicio": 0}


def test_tomada_leva_a_trava_que_foi_lida(tmp_path):
    lp = _escrever(tmp_path, PARADA)
    assert travas._tomar_por_rename(lp, PARADA, "ESTACAO_A", 7)
    assert list(lp.parent.iterdir()) == []


def test_trava_trocada_depois_da_leitura_fica_no_lugar(tmp_path):
    nova = dict(PARADA, estacao="ESTACAO_B", token="1" * 32, contador=0)
    lp = _escrever(tmp_path, nova)
    assert not travas._tomar_por_rename(lp, PARADA, "ESTACAO_A", 7)
    assert _ler(tmp_path) == nova
    assert [p.name for p in lp.parent.iterdir()] == [lp.name]


def test_dono_com_a_trava_aberta_impede_a_tomada(tmp_path):
    lp = _escrever(tmp_path, PARADA)
    fd = os.open(lp, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        assert not travas._tomar_por_rename(lp, PARADA, "ESTACAO_A", 7)
    finally:
        os.close(fd)
    assert _ler(tmp_path) == PARADA


def test_tomada_em_caminho_longo(tmp_path):
    fundo = tmp_path.joinpath(*(["pasta_comprida_" + "x" * 30] * 6))
    lp = _escrever(fundo, PARADA)
    assert len(str(lp)) > 260
    assert travas._tomar_por_rename(lp, PARADA, "ESTACAO_A", 7)
    assert not lp.exists()
