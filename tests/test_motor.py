"""O motor do app com trabalhador falso: configuração, fila, falhas, log e a tarefa do filho."""
import hashlib
import json
import os
import re
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from scan_to_text import config, registro
from scan_to_text import motor as modulo
from scan_to_text import ciclo as modulo_ciclo
from scan_to_text.ciclo import FalhaDesculpada
from scan_to_text.es import ES
from scan_to_text.motor import MotorApp
from scan_to_text.trabalhador import (AlvoIndisponivel, ErroNaTarefa, TempoEsgotado,
                                      TrabalhadorCaiu, TrabalhadorParou)


class TrabalhadorFalso:
    def __init__(self, respostas=None):
        self.tarefas = []
        self.respostas = list(respostas or [])
        self.vivo = False
        self.ocioso_s = 120
        self.paradas = 0

    def enviar(self, tarefa, tempo_limite_s=None):
        self.tarefas.append((tarefa, tempo_limite_s))
        resposta = self.respostas.pop(0) if self.respostas else {"sha256": "a" * 64,
                                                                 "situacao": "feito"}
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta

    def parar(self, espera_s=10):
        self.paradas += 1


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 100
        return self.t


def _esperar(condicao, limite_s: float = 30) -> None:
    fim = time.monotonic() + limite_s
    while not condicao():
        assert time.monotonic() < fim, "a condição não veio a tempo"
        time.sleep(0.02)


@pytest.fixture
def pastas(tmp_path):
    origem, destino, dados = tmp_path / "scanner", tmp_path / "destino", tmp_path / "dados"
    origem.mkdir()
    destino.mkdir()
    return origem, destino, dados, tmp_path / "app" / "config.json"


MUITA_MEMORIA = 64 * 2**30
_COMMIT_LIVRE = getattr(modulo, "commit_livre", None)


@pytest.fixture(autouse=True)
def memoria_de_sobra(monkeypatch):
    # A estação de teste tem memória de sobra; a máquina que roda os testes, nem sempre.
    monkeypatch.setattr(modulo, "commit_livre", lambda: MUITA_MEMORIA, raising=False)


def _motor(pastas, trabalhador=None, **kw):
    _origem, _destino, dados, caminho = pastas
    kw.setdefault("em_nuvem", lambda p: False)
    return MotorApp(caminho, dados, estacao="ESTACAO_TESTE",
                    trabalhador=trabalhador or TrabalhadorFalso(), **kw)


def _configurar(m, pastas, **extra):
    origem, destino, *_ = pastas
    return m.salvar_config({**m.config(), "origens": [str(origem)], "destinos": [str(destino)],
                            "estabilidade_s": 1, **extra})


def test_sem_configuracao_fica_parado(pastas):
    m = _motor(pastas)
    m.start()
    assert m.estado()["estado"] == "parado"
    assert m.config() == config.PADRAO


def test_salvar_config_valida_e_grava(pastas):
    m = _motor(pastas)
    assert _configurar(m, pastas) == []
    caminho = pastas[3]
    assert config.ler(caminho)["destinos"] == [str(pastas[1])]
    assert m.config()["origens"] == [str(pastas[0])]


def test_destino_dentro_da_origem_e_recusado_e_nada_e_gravado(pastas):
    origem, _destino, _dados, caminho = pastas
    (origem / "saida").mkdir()
    m = _motor(pastas)
    erros = m.salvar_config({**m.config(), "origens": [str(origem)],
                             "destinos": [str(origem / "saida")]})
    assert erros == ["destino_em_origem"]
    assert not caminho.exists()


def test_destino_em_nuvem_e_recusado(pastas):
    m = _motor(pastas, em_nuvem=lambda p: True)
    assert _configurar(m, pastas) == ["destino_em_nuvem"]


def test_mudar_so_um_numero_com_origem_fora_do_ar_grava(pastas):
    origem = pastas[0]
    m = _motor(pastas)
    _configurar(m, pastas)
    origem.rmdir()
    assert m.salvar_config({**m.config(), "ping_min": 5}) == []
    assert m.config()["ping_min"] == 5


def test_pasta_nova_ilegivel_e_recusada(pastas, tmp_path):
    m = _motor(pastas)
    _configurar(m, pastas)
    erros = m.salvar_config({**m.config(), "origens": [str(pastas[0]), str(tmp_path / "nao_existe")]})
    assert erros == ["origem_ilegivel"]


def test_config_ilegivel_vira_aviso_e_e_preservada_ao_salvar(pastas):
    caminho = pastas[3]
    caminho.parent.mkdir(parents=True)
    caminho.write_text("{quebrado", encoding="utf-8")
    m = _motor(pastas)
    assert "aviso.config_ilegivel" in m.estado()["avisos"]
    assert _configurar(m, pastas) == []
    assert caminho.with_name("config.json.ilegivel").read_text(encoding="utf-8") == "{quebrado"
    assert "aviso.config_ilegivel" not in m.estado()["avisos"]


def test_estado_traz_os_avisos_do_preflight(pastas):
    m = _motor(pastas, preflight={"avisos": ["aviso.ram_baixa"]})
    assert m.estado()["avisos"] == ["aviso.ram_baixa"]
    assert m.estado()["versao"] == modulo.VERSAO


def test_processar_manda_a_tarefa_ao_trabalhador(pastas):
    origem, destino, dados, _ = pastas
    falso = TrabalhadorFalso()
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas, idiomas_documentos=["pt-BR"])
    m._preparar()
    arquivo = origem / "scan001.pdf"
    arquivo.write_bytes(b"%PDF-1.7 teste")
    m._processar(arquivo)
    [(tarefa, limite)] = falso.tarefas
    assert tarefa["arquivo"] == str(arquivo)
    assert tarefa["destinos"] == [str(destino)] and tarefa["origens"] == [str(origem)]
    assert tarefa["estacao"] == "ESTACAO_TESTE" and tarefa["idioma"] == "pt-BR"
    assert tarefa["hoje"] == date.today().isoformat() and tarefa["desistir"] is False
    assert Path(tarefa["pasta_temp"]).parent == dados
    assert limite >= modulo.TEMPO_BASE_S


def test_tempo_limite_cobre_30_s_por_pagina_de_50_kb_e_a_carga_dos_modelos(pastas):
    origem = pastas[0]
    falso = TrabalhadorFalso()
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "prontuario.pdf"
    with open(arquivo, "wb") as f:
        f.truncate(6 * 2 ** 20)  # 6 MB: cerca de 120 páginas em preto e branco
    m._processar(arquivo)
    [(_tarefa_enviada, limite)] = falso.tarefas
    paginas = 6 * 2 ** 20 / (50 * 1024)
    assert limite >= 30 * paginas + 10 * 60


def test_arquivo_ja_feito_em_todos_os_destinos_nao_volta_a_fila(pastas):
    origem, destino, *_ = pastas
    arquivo = origem / "scan001.pdf"
    arquivo.write_bytes(b"%PDF-1.7 teste")
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    falso = TrabalhadorFalso([{"sha256": sha, "situacao": "feito"}])
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)
    m._preparar()
    assert m._varrer() == []          # primeira vista: ainda não está estável
    assert m._varrer() == [arquivo]
    m._processar(arquivo)
    registro.marcar_feito(destino, sha, [], ES([origem]), "ESTACAO_TESTE", 1)
    assert m._varrer() == []


def test_arquivo_que_derruba_o_trabalhador_duas_vezes_vai_como_ilegivel(pastas):
    origem = pastas[0]
    falso = TrabalhadorFalso([ErroNaTarefa("ValueError", ""), TempoEsgotado("tempo")])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "ruim.pdf"
    arquivo.write_bytes(b"%PDF-1.7 ruim")
    for _ in range(2):
        with pytest.raises(Exception):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, True]


def test_stop_no_meio_nao_conta_como_falha(pastas):
    origem = pastas[0]
    falso = TrabalhadorFalso([TrabalhadorParou("parar"), TrabalhadorParou("parar")])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for _ in range(2):
        with pytest.raises(TrabalhadorParou):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, False]


def _logs(dados: Path) -> Path:
    pasta = dados / "logs"
    pasta.mkdir(parents=True)
    (pasta / "scan_to_text.log").write_text(
        "2026-10-25 10:00:00,000 INFO scan_to_text 10 app_inicio versao=1\n"
        "2026-10-25 10:00:02,000 WARNING scan_to_text.ciclo 10 ciclo_erro_ao_varrer tipo=OSError\n"
        "  ciclo.py:189 _ping\n", encoding="utf-8")
    (pasta / "trabalhador.log").write_text(
        "2026-10-25 10:00:01,000 INFO scan_to_text 20 item_lido paginas=2\n", encoding="utf-8")
    return pasta


def test_log_recente_junta_os_dois_processos_em_ordem(pastas):
    dados = pastas[2]
    _logs(dados)
    linhas = _motor(pastas).log_recente()
    assert linhas == ["2026-10-25 10:00:00  app_inicio versao=1",
                      "2026-10-25 10:00:01  item_lido paginas=2",
                      "2026-10-25 10:00:02  ciclo_erro_ao_varrer tipo=OSError"]


def test_salvar_log_grava_tudo_com_o_preflight(pastas, tmp_path):
    dados = pastas[2]
    _logs(dados)
    pendrive = tmp_path / "pendrive"
    pendrive.mkdir()
    m = _motor(pastas, preflight={"windows_build": 26200, "avisos": []})
    arquivo = Path(m.salvar_log(str(pendrive)))
    assert arquivo.parent == pendrive and arquivo.suffix == ".txt"
    texto = arquivo.read_text(encoding="utf-8")
    assert "26200" in texto and "ciclo.py:189 _ping" in texto and "item_lido" in texto


def test_salvar_log_dentro_de_uma_origem_e_recusado(pastas):
    origem = pastas[0]
    _logs(pastas[2])
    m = _motor(pastas)
    _configurar(m, pastas)
    with pytest.raises(PermissionError):
        m.salvar_log(str(origem))
    assert list(origem.iterdir()) == []


def test_fechado_nao_liga_mais(pastas):
    falso = TrabalhadorFalso()
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m.fechar()
    m.start()
    assert m.estado()["estado"] == "parado"
    assert falso.paradas >= 1


def test_start_e_stop_de_verdade_com_o_ciclo(pastas):
    m = _motor(pastas)
    _configurar(m, pastas, ping_min=1)
    m.start()
    try:
        assert m.estado()["estado"] in ("ocioso", "trabalhando")
    finally:
        m.stop()
    assert m.estado()["estado"] == "parado"


# a tarefa, do lado do filho -------------------------------------------------------

def test_tarefa_processa_o_arquivo_com_o_leitor_do_processo(pastas, monkeypatch):
    from test_processar import LeitorFalso, _hemograma, _pdf

    origem, destino, dados, _ = pastas
    leitor = LeitorFalso()
    monkeypatch.setitem(modulo._PROCESSO, "leitor", leitor)
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    resumo = modulo.tarefa({"arquivo": str(arquivo), "origens": [str(origem)],
                            "destinos": [str(destino)], "estacao": "ESTACAO_TESTE",
                            "idioma": "pt-BR", "hoje": "2026-10-25",
                            "pasta_temp": str(dados / "temp"), "pasta_logs": str(dados / "logs"),
                            "desistir": False})
    assert resumo["situacao"] == "feito" and resumo["documentos"] == 1
    assert (destino / "FULANA DE TESTE SINTETICA - 2026-10-25" / "3 LABORATORIO").is_dir()
    assert json.dumps(resumo).isascii()


def test_versao_do_app_passa_pelo_filtro_do_log(tmp_path):
    import logging
    from scan_to_text import log
    log.configurar(tmp_path)
    log.evento("app_inicio", versao=f"v{modulo.VERSAO}")
    for h in logging.getLogger().handlers:
        h.flush()
    assert f"versao=v{modulo.VERSAO}" in (tmp_path / "scan_to_text.log").read_text(encoding="utf-8")


def test_leitor_preguicoso_so_carrega_quando_preciso(monkeypatch):
    carregados = []

    class LeitorDeVerdade:
        def __init__(self, pasta):
            carregados.append(pasta)

        def ler_arquivo(self, caminho, sha256):
            return ["pagina"]

    import scan_to_text.ler.layout as layout
    monkeypatch.setattr(layout, "Leitor", LeitorDeVerdade)
    monkeypatch.setattr(modulo.modelos, "pasta_modelos", lambda: Path("modelos"))
    leitor = modulo._LeitorPreguicoso()
    assert carregados == []
    leitor.preparar()
    leitor.preparar()
    assert leitor.ler_arquivo(Path("x.pdf"), "a" * 64) == ["pagina"]
    assert carregados == [Path("modelos")]


# o dia, as pastas que não respondem e a conferência de pasta ---------------------

def test_estado_conta_o_dia(pastas):
    origem = pastas[0]
    falso = TrabalhadorFalso([{"sha256": "a" * 64, "situacao": "feito", "documentos": 3,
                               "desconhecidos": 1, "ilegiveis": 0},
                              {"sha256": "b" * 64, "situacao": "ja_feito"}])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    for nome in ("a.pdf", "b.pdf"):
        (origem / nome).write_bytes(nome.encode())
        m._processar(origem / nome)
    hoje = m.estado()["hoje"]
    assert (hoje["arquivos"], hoje["documentos"], hoje["desconhecidos"]) == (1, 3, 1)
    assert len(hoje["ultimo"]) == 5 and hoje["ultimo"][2] == ":"


def _processar_resumos(pastas, resumos):
    origem = pastas[0]
    m = _motor(pastas, trabalhador=TrabalhadorFalso(resumos))
    _configurar(m, pastas)
    m._preparar()
    for n in range(len(resumos)):
        arquivo = origem / f"scan{n}.pdf"
        arquivo.write_bytes(b"%PDF-1.7 " + bytes([n]))
        m._processar(arquivo)
    return m


def test_ilegivel_tem_conta_propria_e_nao_vira_desconhecido(pastas):
    m = _processar_resumos(pastas, [{"sha256": "a" * 64, "situacao": "feito", "documentos": 0,
                                     "desconhecidos": 0, "ilegiveis": 1}])
    hoje = m.estado()["hoje"]
    assert set(hoje) == {"arquivos", "documentos", "desconhecidos", "ilegiveis", "ultimo"}
    assert (hoje["arquivos"], hoje["documentos"], hoje["desconhecidos"],
            hoje["ilegiveis"]) == (1, 0, 0, 1)


def test_conta_do_dia_fica_em_hoje_json_com_ilegiveis_separados(pastas):
    dados = pastas[2]
    _processar_resumos(pastas, [{"sha256": "a" * 64, "situacao": "feito", "documentos": 3,
                                 "desconhecidos": 1, "ilegiveis": 0},
                                {"sha256": "b" * 64, "situacao": "feito", "documentos": 0,
                                 "desconhecidos": 0, "ilegiveis": 1}])
    gravado = json.loads((dados / "hoje.json").read_text(encoding="utf-8"))
    assert gravado["dia"] == date.today().isoformat()
    assert (gravado["arquivos"], gravado["documentos"], gravado["desconhecidos"],
            gravado["ilegiveis"]) == (2, 3, 1, 1)


def test_destino_ocupado_nao_conta_como_falha_nem_no_dia_e_volta_no_proximo_ping(pastas):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7 inventado")
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    ocupado = {"sha256": sha, "situacao": "destino_ocupado"}
    falso = TrabalhadorFalso([ErroNaTarefa("ValueError", ""), ocupado, ocupado,
                              {"sha256": sha, "situacao": "feito", "documentos": 2,
                               "desconhecidos": 0, "ilegiveis": 0}])
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)
    m._preparar()
    assert m._varrer() == []  # primeira vista: ainda não está estável
    assert m._varrer() == [arquivo]
    with pytest.raises(ErroNaTarefa):
        m._processar(arquivo)  # uma falha de verdade, contada
    for _ in range(2):
        m._processar(arquivo)
        assert m.estado()["hoje"]["arquivos"] == 0
        assert m._varrer() == [arquivo]  # nada foi gravado: volta no próximo ping
    m._processar(arquivo)
    # Contado como falha, o ocupado teria levado o arquivo à entrega sem leitura.
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 4
    hoje = m.estado()["hoje"]
    assert (hoje["arquivos"], hoje["documentos"]) == (1, 2)


@pytest.mark.parametrize("situacao", ["destino_ocupado", "em_outra_estacao"])
def test_nada_lido_aqui_mantem_a_conta_das_falhas_de_verdade(pastas, situacao):
    # Zerada, a conta deixaria o arquivo que falha aqui e, entre uma falha e
    # outra, encontra o destino ocupado ou a trava de outra estação voltar
    # para sempre sem ser entregue.
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7 inventado")
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    falso = TrabalhadorFalso([ErroNaTarefa("ValueError", ""),
                              {"sha256": sha, "situacao": situacao},
                              ErroNaTarefa("ValueError", ""),
                              {"sha256": sha, "situacao": "feito", "documentos": 1,
                               "desconhecidos": 1, "ilegiveis": 1}])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    with pytest.raises(ErroNaTarefa):
        m._processar(arquivo)
    m._processar(arquivo)  # não conta, e não zera
    with pytest.raises(ErroNaTarefa):
        m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, False, True]


def test_trava_perdida_zera_a_conta_so_quando_o_arquivo_foi_lido_aqui(pastas):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7 inventado")
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    perdida = {"sha256": sha, "situacao": "trava_perdida"}
    falso = TrabalhadorFalso([ErroNaTarefa("ValueError", ""), perdida,
                              ErroNaTarefa("ValueError", ""), ErroNaTarefa("ValueError", ""),
                              perdida,
                              {"sha256": sha, "situacao": "feito", "documentos": 1,
                               "desconhecidos": 1, "ilegiveis": 1}])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    with pytest.raises(ErroNaTarefa):
        m._processar(arquivo)
    m._processar(arquivo)  # lido aqui, sem falha: zera
    for _ in range(2):
        with pytest.raises(ErroNaTarefa):
            m._processar(arquivo)
    m._processar(arquivo)  # entregue sem leitura, mas a trava foi de outra: não zera
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, False, False, True, True]


def test_conta_do_dia_sobrevive_ao_reinicio_do_app(pastas):
    m = _processar_resumos(pastas, [{"sha256": "a" * 64, "situacao": "feito", "documentos": 2,
                                     "desconhecidos": 1, "ilegiveis": 0}])
    antes = m.estado()["hoje"]
    assert antes["documentos"] == 2
    assert _motor(pastas).estado()["hoje"] == antes


def test_conta_de_outro_dia_comeca_zerada(pastas):
    dados = pastas[2]
    dados.mkdir(parents=True)
    (dados / "hoje.json").write_text(json.dumps(
        {"dia": "2000-01-01", "arquivos": 5, "documentos": 9, "desconhecidos": 1,
         "ilegiveis": 2, "ultimo": "10:00"}), encoding="utf-8")
    assert _motor(pastas).estado()["hoje"] == {"arquivos": 0, "documentos": 0,
                                                "desconhecidos": 0, "ilegiveis": 0,
                                                "ultimo": None}


def test_conta_do_dia_ilegivel_no_disco_comeca_zerada(pastas):
    dados = pastas[2]
    dados.mkdir(parents=True)
    (dados / "hoje.json").write_text("{quebrado", encoding="utf-8")
    assert _motor(pastas).estado()["hoje"]["arquivos"] == 0


def test_estado_diz_qual_origem_nao_abre(pastas, tmp_path):
    m = _motor(pastas, relogio=Relogio())
    fora = tmp_path / "scanner_fora"
    _configurar(m, pastas)
    fora.mkdir()
    m.salvar_config({**m.config(), "origens": [str(pastas[0]), str(fora)]})
    fora.rmdir()
    m._preparar()
    m._ciclo._estado = "ocioso"
    m._varrer()
    e = m.estado()
    assert "origem_inacessivel" in e["avisos"]
    assert e["pastas"]["origem_inacessivel"] == [str(fora)]


def test_conferir_pasta_nova(pastas, tmp_path):
    origem, destino, *_ = pastas
    m = _motor(pastas)
    assert m.conferir_pasta(str(destino), "destinos", [str(origem)], []) == []
    assert m.conferir_pasta(str(tmp_path / "nao_existe"), "origens", [], []) == ["origem_ilegivel"]
    (origem / "saida").mkdir()
    assert m.conferir_pasta(str(origem / "saida"), "destinos", [str(origem)], []) == ["destino_em_origem"]
    nuvem = _motor(pastas, em_nuvem=lambda p: p == destino)
    assert nuvem.conferir_pasta(str(destino), "destinos", [], []) == ["destino_em_nuvem"]


@pytest.fixture
def gravacoes_de_teste(monkeypatch):
    """As pastas onde o app criou um arquivo de teste de gravação."""
    real, pastas_tocadas = modulo.tempfile.mkstemp, []

    def mkstemp(*a, **k):
        pastas_tocadas.append(Path(k.get("dir") or a[2]))
        return real(*a, **k)

    monkeypatch.setattr(modulo.tempfile, "mkstemp", mkstemp)
    return pastas_tocadas


def test_conferir_destino_dentro_da_origem_nao_grava_nada_nela(pastas, gravacoes_de_teste):
    origem = pastas[0]
    (origem / "saida").mkdir()
    m = _motor(pastas)
    for candidata in (origem, origem / "saida"):
        assert m.conferir_pasta(str(candidata), "destinos", [str(origem)], []) == [
            "destino_em_origem"]
    assert gravacoes_de_teste == []


def test_salvar_destino_igual_a_origem_nao_grava_nada_nela(pastas, gravacoes_de_teste):
    origem = pastas[0]
    m = _motor(pastas)
    assert m.salvar_config({**m.config(), "origens": [str(origem)],
                            "destinos": [str(origem)]}) == ["destino_em_origem"]
    assert gravacoes_de_teste == []
    assert list(origem.iterdir()) == []


def test_teste_de_gravacao_recusa_origem_sem_criar_nada(pastas, gravacoes_de_teste):
    origem, destino = pastas[0], pastas[1]
    assert modulo._pode_gravar(origem / "saida", [str(origem)]) is False
    assert modulo._pode_gravar(destino, [str(origem)]) is True
    assert gravacoes_de_teste == [destino]


def test_escolher_pasta_repassa_sem_seletor_e_cancelado_como_vieram(pastas):
    # None: não há seletor nativo; "": a pessoa cancelou. A tela distingue os dois.
    assert _motor(pastas, escolher=lambda: None).escolher_pasta() is None
    assert _motor(pastas, escolher=lambda: "").escolher_pasta() == ""
    assert _motor(pastas).escolher_pasta() is None


def test_conferir_pasta_que_ja_esta_na_lista(pastas, monkeypatch):
    origem, destino = pastas[0], pastas[1]
    m = _motor(pastas)
    variante = str(destino).upper() + "\\"
    assert m.conferir_pasta(variante, "destinos", [str(origem)], [str(destino)]) == [
        "pasta_repetida"]
    assert m.conferir_pasta(str(origem), "origens", [str(origem)], []) == ["pasta_repetida"]
    # A letra mapeada é a mesma pasta que o caminho de rede já listado.
    monkeypatch.setattr(config, "_remoto", lambda letra: str(destino) if letra == "P:" else None)
    assert m.conferir_pasta("P:\\", "destinos", [], [str(destino)]) == ["pasta_repetida"]
    # A mesma pasta no outro papel não é repetição: é aninhamento.
    assert m.conferir_pasta(str(destino), "origens", [], [str(destino)]) == ["destino_em_origem"]


def test_abrir_liga_sozinho_quando_configurado(pastas):
    m = _motor(pastas)
    m.abrir()
    assert m.estado()["estado"] == "parado"
    _configurar(m, pastas, ping_min=1)
    m.abrir()
    try:
        assert m.estado()["estado"] in ("ocioso", "trabalhando")
    finally:
        m.stop()


# troca de configuração com o motor parando ------------------------------------------

def _pings() -> set[threading.Thread]:
    """As threads de ping vivas agora. A de um teste anterior pode ainda estar
    terminando: os testes comparam o conjunto, e não a quantidade."""
    return {t for t in threading.enumerate() if t.name == "scan_to_text_ping" and t.is_alive()}


def _varredura_presa(m, monkeypatch):
    """A origem de rede não responde: a varredura fica presa até soltar."""
    solta, presa = threading.Event(), threading.Event()

    def varrer():
        presa.set()
        solta.wait(30)
        return []

    monkeypatch.setattr(m._vigia, "varrer", varrer)
    return solta, presa


def test_salvar_config_com_o_motor_parando_nao_religa(pastas, monkeypatch):
    monkeypatch.setattr(modulo_ciclo, "ESPERA_STOP_S", 0.3)
    antes = _pings()
    m = _motor(pastas)
    _configurar(m, pastas)
    m._preparar()
    solta, presa = _varredura_presa(m, monkeypatch)
    m.start()
    try:
        assert presa.wait(10)
        m.stop()
        assert m.estado()["estado"] == "parando"
        assert m.salvar_config({**m.config(), "ping_min": 5}) == []
        assert m.estado()["estado"] == "parando"
        assert len(_pings() - antes) == 1
    finally:
        solta.set()
    _esperar(lambda: m.estado()["estado"] == "parado")
    time.sleep(0.2)
    assert m.estado()["estado"] == "parado" and _pings() - antes == set()
    m.start()  # o START seguinte já usa a configuração nova
    try:
        assert m._ativa["ping_min"] == 5
    finally:
        m.stop()


def test_stop_durante_a_troca_de_configuracao_vence(pastas, monkeypatch):
    monkeypatch.setattr(modulo_ciclo, "ESPERA_STOP_S", 3.0)
    antes = _pings()
    m = _motor(pastas)
    _configurar(m, pastas)
    m._preparar()
    solta, presa = _varredura_presa(m, monkeypatch)
    m.start()
    assert presa.wait(10)
    antigo = m._ciclo
    salvo = []
    troca = threading.Thread(target=lambda: salvo.append(
        m.salvar_config({**m.config(), "ping_min": 5})))
    troca.start()
    try:
        _esperar(lambda: antigo.estado == "parando")  # a troca está parando o ciclo antigo
        threading.Timer(0.3, solta.set).start()
        m.stop()  # a pessoa aperta STOP no meio da troca
    finally:
        solta.set()
        troca.join(15)
    assert salvo == [[]]
    time.sleep(0.2)
    assert m.estado()["estado"] == "parado" and _pings() - antes == set()


# o destino que sumiu não volta a existir pelas mãos do app ------------------------

def test_ponto_da_estacao_nao_recria_o_destino_que_sumiu(pastas):
    destino = pastas[1]
    m = _motor(pastas)
    _configurar(m, pastas)
    m._preparar()
    destino.rmdir()  # a letra passou a apontar para outro disco, ou a pasta foi apagada
    m._varrer()
    assert not destino.exists()


def test_ponto_da_estacao_grava_no_destino_que_existe(pastas):
    destino = pastas[1]
    m = _motor(pastas)
    _configurar(m, pastas)
    m._preparar()
    m._varrer()
    assert (destino / "_scan-to-text" / "estacoes" / "ESTACAO_TESTE.json").is_file()


def _tarefa(origem, destino, dados, arquivo):
    return {"arquivo": str(arquivo), "origens": [str(origem)], "destinos": [str(destino)],
            "estacao": "ESTACAO_TESTE", "idioma": "pt-BR", "hoje": "2026-10-25",
            "pasta_temp": str(dados / "temp"), "pasta_logs": str(dados / "logs"),
            "desistir": False}


def test_tarefa_recusa_destino_que_nao_existe_sem_cria_lo(pastas, monkeypatch):
    from test_processar import LeitorFalso, _hemograma, _pdf

    origem, destino, dados, _ = pastas
    leitor = LeitorFalso()
    monkeypatch.setitem(modulo._PROCESSO, "leitor", leitor)
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _pdf(origem / "scan001.pdf", 1)
    leitor.registrar(arquivo, [_hemograma()])
    destino.rmdir()
    with pytest.raises(modulo.DestinoInacessivel):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))
    assert not destino.exists()


def test_falha_com_destino_fora_do_ar_nao_conta_contra_o_arquivo(pastas):
    origem, destino = pastas[0], pastas[1]
    falso = TrabalhadorFalso([ErroNaTarefa("DestinoInacessivel", "")] * 3)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    destino.rmdir()
    for _ in range(3):
        with pytest.raises(ErroNaTarefa):
            m._processar(arquivo)
    destino.mkdir()
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 4


# falha do ambiente não é falha do arquivo -----------------------------------------

@pytest.mark.parametrize("erro", [ErroNaTarefa("LeitorIndisponivel", ""),
                                  AlvoIndisponivel("ModuleNotFoundError", ""),
                                  AlvoIndisponivel("MemoryError", "")])
def test_leitor_que_nao_carrega_nao_conta_contra_o_arquivo_e_refaz_o_filho(pastas, erro):
    # A carga dos modelos e a montagem do filho chegam como LeitorIndisponivel;
    # a importação da tarefa no filho, como AlvoIndisponivel.
    origem = pastas[0]
    falso = TrabalhadorFalso([erro] * 3)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for _ in range(3):
        with pytest.raises(ErroNaTarefa) as erro:
            m._processar(arquivo)
        assert modulo_ciclo.falha_de_ambiente(erro.value)  # o ciclo pausa a rodada
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 4
    assert falso.paradas == 3  # cada tentativa nova carrega tudo num filho novo


@pytest.mark.parametrize("tipo", ["MemoryError", "ImportError", "ModuleNotFoundError",
                                  "ConversionError", "LeituraIncompleta"])
def test_memoria_ou_biblioteca_que_falta_na_leitura_conta_contra_o_arquivo(pastas, tipo):
    # A carga do leitor chega como LeitorIndisponivel; isto veio da leitura de
    # um arquivo (um .heic sem a biblioteca, um arquivo enorme).
    origem = pastas[0]
    falso = TrabalhadorFalso([ErroNaTarefa(tipo, "recipientes.py:33 _heic_para_png")] * 2)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "foto.heic"
    arquivo.write_bytes(b"inventado")
    for _ in range(2):
        with pytest.raises(ErroNaTarefa) as erro:
            m._processar(arquivo)
        assert not modulo_ciclo.falha_de_ambiente(erro.value)  # a rodada segue
        assert not modulo_ciclo.disco_cheio(erro.value)
        # O log do ciclo guarda o tipo do filho e onde ele nasceu.
        assert erro.value.tipo == tipo and "_heic_para_png" in erro.value.rastro
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, True]


@pytest.mark.parametrize("tipo, paradas", [("MemoryError", 1), ("ImportError", 0),
                                           ("ConversionError", 1), ("LeituraIncompleta", 1)])
def test_filho_que_ficou_sem_memoria_numa_leitura_e_refeito(pastas, tipo, paradas):
    # Depois de um MemoryError o processo pode ter ficado pela metade; a
    # próxima tarefa começa num filho novo. O Docling devolve a falta de
    # memória num estágio como ConversionError, ou sem a página.
    origem = pastas[0]
    falso = TrabalhadorFalso([ErroNaTarefa(tipo, "processar.py:1 _ler")])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    with pytest.raises(ErroNaTarefa):
        m._processar(arquivo)
    assert falso.paradas == paradas


def test_memoria_que_falta_com_destino_fora_do_ar_nao_conta_e_nao_vira_aviso_de_leitor(pastas):
    origem, destino = pastas[0], pastas[1]
    falso = TrabalhadorFalso([ErroNaTarefa("MemoryError", "")] * 3)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    destino.rmdir()
    for _ in range(3):
        with pytest.raises(ErroNaTarefa) as erro:
            m._processar(arquivo)
        assert not modulo_ciclo.falha_de_ambiente(erro.value)
    destino.mkdir()
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 4


class _TrabalhadorComArquivoRuim(TrabalhadorFalso):
    """O arquivo `ruim` sempre levanta `erro` na leitura; os outros saem feitos."""

    def __init__(self, ruim, erro):
        super().__init__()
        self.ruim, self.erro = ruim, erro

    def enviar(self, tarefa, tempo_limite_s=None):
        self.tarefas.append((tarefa, tempo_limite_s))
        if Path(tarefa["arquivo"]) == self.ruim and not tarefa["desistir"]:
            raise self.erro
        return {"sha256": hashlib.sha256(tarefa["arquivo"].encode()).hexdigest(),
                "situacao": "feito", "documentos": 1}


@pytest.mark.parametrize("erro", [ErroNaTarefa("MemoryError", "processar.py:1 _ler"),
                                  ErroNaTarefa("ModuleNotFoundError", "recipientes.py:33 x")])
def test_arquivo_que_falha_na_leitura_nao_segura_a_fila(pastas, erro):
    origem = pastas[0]
    ruim, bom = origem / "a.heic", origem / "b.pdf"
    ruim.write_bytes(b"inventado-a")
    bom.write_bytes(b"inventado-b")
    antes = time.time() - 3600
    os.utime(ruim, (antes, antes))  # o mais antigo: vem primeiro na rodada
    falso = _TrabalhadorComArquivoRuim(ruim, erro)
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)  # ping de 2 min: o b.pdf tem de sair na mesma rodada
    m._preparar()
    m._varrer()  # primeira vista: no START os dois já estão estáveis
    m.start()
    try:
        _esperar(lambda: m.estado()["hoje"]["arquivos"] == 1)  # o b.pdf saiu
        assert [Path(t["arquivo"]) for t, _ in falso.tarefas] == [ruim, bom]
        assert "leitor_indisponivel" not in m.estado()["avisos"]
    finally:
        m.stop()


def test_tarefa_nao_embrulha_memoria_que_falta_na_leitura_como_leitor_indisponivel(
        pastas, monkeypatch):
    from test_processar import _pdf

    class LeitorSemMemoria:
        def ler_arquivo(self, caminho, sha256):
            raise MemoryError

    origem, destino, dados, _ = pastas
    monkeypatch.setitem(modulo._PROCESSO, "leitor", LeitorSemMemoria())
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _pdf(origem / "scan001.pdf", 1)
    with pytest.raises(MemoryError):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))


class _ConversorDeMentira:
    """Imita o DocumentConverter: o pipeline de cada formato nasce no primeiro uso.

    `carga` é o erro de quem monta o pipeline (modelo, biblioteca ou memória
    que não bastam); `leitura`, o erro da conversão de um arquivo depois disso.
    """

    def __init__(self, carga=None, leitura=None):
        self.carga, self.leitura = carga, leitura
        self.montados = []

    def initialize_pipeline(self, formato):
        if self.carga is not None:
            raise self.carga
        if formato not in self.montados:  # montado uma vez, fica em cache
            self.montados.append(formato)

    def convert(self, fonte):
        if self.carga is not None:
            raise self.carga  # sem pipeline montado antes, o convert o monta
        raise self.leitura


def _leitor_preguicoso_com(conversor):
    from docling.datamodel.base_models import InputFormat

    import scan_to_text.ler.layout as layout

    leitor = object.__new__(layout.Leitor)  # sem modelos: só o caminho até o conversor
    leitor._conversor, leitor._orientador, leitor._formatos = conversor, None, InputFormat
    preguicoso = modulo._LeitorPreguicoso()
    preguicoso._leitor = leitor
    return preguicoso


def _com_tinta():
    from PIL import Image, ImageDraw

    imagem = Image.new("RGB", (800, 1000), "white")
    desenho = ImageDraw.Draw(imagem)
    for i in range(12):
        desenho.rectangle((60, 60 + i * 60, 700, 80 + i * 60), fill="black")
    return imagem


def _png_com_tinta(caminho: Path) -> Path:
    _com_tinta().save(caminho)
    return caminho


@pytest.mark.parametrize("carga", [ModuleNotFoundError("tf_predictor"), ImportError("dll"),
                                   MemoryError(), OSError(126, "dll")])
def test_pipeline_do_formato_que_nao_carrega_na_primeira_leitura_e_leitor_indisponivel(
        pastas, monkeypatch, carga):
    # O Docling monta layout, TableFormer e OCR no primeiro convert de cada
    # formato, depois da construção do Leitor.
    origem, destino, dados, _ = pastas
    monkeypatch.setitem(modulo._PROCESSO, "leitor",
                        _leitor_preguicoso_com(_ConversorDeMentira(carga=carga)))
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _png_com_tinta(origem / "scan001.png")
    with pytest.raises(modulo_ciclo.LeitorIndisponivel) as erro:
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))
    assert isinstance(erro.value.__cause__.__cause__, type(carga))
    assert [p.name for p in destino.iterdir() if not p.name.startswith("_")] == []


def test_memoria_que_falta_na_leitura_depois_da_carga_e_do_arquivo(pastas, monkeypatch):
    origem, destino, dados, _ = pastas
    conversor = _ConversorDeMentira(leitura=MemoryError())
    monkeypatch.setitem(modulo._PROCESSO, "leitor", _leitor_preguicoso_com(conversor))
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _png_com_tinta(origem / "scan001.png")
    with pytest.raises(MemoryError):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))
    # Só o pipeline do formato lido: o do PDF com texto fica para quando vier um.
    assert [f.name for f in conversor.montados] == ["IMAGE"]


class _ConversorComCache:
    """Imita o DocumentConverter com o cache dos pipelines montados.

    `falha(formato)` é o erro da montagem daquele formato naquela hora, ou
    None; `eventos` guarda a ordem de montagens, cargas e conversões.
    """

    def __init__(self, eventos, falha=lambda formato: None, leitura=None):
        self.eventos, self.falha, self.leitura = eventos, falha, leitura
        self.initialized_pipelines = {}

    def initialize_pipeline(self, formato):
        if formato in self.initialized_pipelines:
            return
        erro = self.falha(formato)
        if erro is not None:
            raise erro
        self.eventos.append(("montar", formato.name))
        self.initialized_pipelines[formato] = object()

    def convert(self, fonte):
        from docling.datamodel.base_models import DocumentStream, InputFormat

        formato = (InputFormat.IMAGE if isinstance(fonte, DocumentStream)
                   else InputFormat.PDF if Path(fonte).suffix == ".pdf" else InputFormat.DOCX)
        self.initialize_pipeline(formato)  # o Docling monta no convert o que faltar
        self.eventos.append(("converter", formato.name))
        if self.leitura is not None:
            raise self.leitura
        return _Resultado([1])


class _Resultado:
    """O que o `convert` devolve, só com os números das páginas que vieram."""

    def __init__(self, numeros):
        from types import SimpleNamespace

        self.pages = [SimpleNamespace(page_no=n) for n in numeros]


def _leitor_com(conversor):
    from docling.datamodel.base_models import InputFormat

    import scan_to_text.ler.layout as layout
    from scan_to_text.tipos import Pagina

    leitor = object.__new__(layout.Leitor)
    leitor._conversor, leitor._orientador, leitor._formatos = conversor, None, InputFormat
    leitor._pagina = lambda resultado, posicao, sha, indice, imagem, rotacao: Pagina(
        arquivo_sha256=sha, indice=indice, blocos=(), em_branco=False)
    return leitor


def _registrar_cargas(monkeypatch, eventos, texto: bool) -> None:
    """A abertura da imagem e a rasterização entram nos eventos; o PDF tem uma página."""
    import PIL.Image

    import scan_to_text.ler.layout as layout

    class ImagemQueRegistra:
        def __getattr__(self, nome):
            return getattr(PIL.Image, nome)

        def open(self, *args, **kw):
            eventos.append(("carregar",))
            return PIL.Image.open(*args, **kw)

    def rasterizar(documento, indice):
        eventos.append(("carregar",))
        return _com_tinta()

    monkeypatch.setattr(layout, "Image", ImagemQueRegistra())
    monkeypatch.setattr(layout.leitor_pdf, "abrir", lambda caminho: [None])
    monkeypatch.setattr(layout.leitor_pdf, "camada_texto_boa", lambda documento, i: texto)
    monkeypatch.setattr(layout.leitor_pdf, "camada_texto_em_pe", lambda documento, i: True)
    monkeypatch.setattr(layout.leitor_pdf, "rasterizar", rasterizar)


@pytest.mark.parametrize("nome, texto, formato", [
    ("scan.png", False, "IMAGE"), ("scan.tif", False, "IMAGE"),
    ("escaneado.pdf", False, "IMAGE"), ("com_texto.pdf", True, "PDF")])
def test_pipeline_do_formato_e_montado_antes_de_o_arquivo_ir_para_a_memoria(
        tmp_path, monkeypatch, nome, texto, formato):
    # Montado depois, ele disputaria a memória com a página do arquivo.
    eventos = []
    _registrar_cargas(monkeypatch, eventos, texto)
    leitor = _leitor_com(_ConversorComCache(eventos))
    arquivo = tmp_path / nome
    if arquivo.suffix == ".pdf":
        arquivo.write_bytes(b"%PDF-1.7 inventado")
    else:
        _com_tinta().save(arquivo)
    leitor.ler_arquivo(arquivo, "a" * 64)
    assert eventos[0] == ("montar", formato)
    assert ("converter", formato) in eventos


def test_memoria_que_a_pagina_ocupa_conta_contra_o_arquivo_e_nao_para_a_fila(pastas,
                                                                             monkeypatch):
    # Com a página enorme na memória, nem a montagem caberia. Montado antes, o
    # pipeline sobe, e a falta de memória vem na leitura: é do arquivo, que é
    # contado, e a rodada segue para o próximo.
    origem, destino, dados, _ = pastas
    eventos = []
    _registrar_cargas(monkeypatch, eventos, texto=False)
    conversor = _ConversorComCache(
        eventos, falha=lambda formato: MemoryError() if ("carregar",) in eventos else None,
        leitura=MemoryError())
    preguicoso = modulo._LeitorPreguicoso()
    preguicoso._leitor = _leitor_com(conversor)
    monkeypatch.setitem(modulo._PROCESSO, "leitor", preguicoso)
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _png_com_tinta(origem / "enorme.png")
    with pytest.raises(MemoryError):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))


def test_formato_que_so_cabe_sozinho_entra_no_lugar_do_outro(tmp_path, monkeypatch):
    # O pipeline da imagem e o do PDF com texto, juntos, pesam: na estação em
    # que só um cabe, o arquivo misto (scan e PDF com texto) é lido inteiro.
    from docling.datamodel.base_models import InputFormat

    eventos = []
    _registrar_cargas(monkeypatch, eventos, texto=True)
    conversor = _ConversorComCache(
        eventos, falha=lambda formato: MemoryError() if conversor.initialized_pipelines else None)
    leitor = _leitor_com(conversor)
    leitor.ler_arquivo(_png_com_tinta(tmp_path / "scan.png"), "a" * 64)
    pdf = tmp_path / "com_texto.pdf"
    pdf.write_bytes(b"%PDF-1.7 inventado")
    leitor.ler_arquivo(pdf, "b" * 64)
    assert [e for e in eventos if e[0] != "carregar"] == [
        ("montar", "IMAGE"), ("converter", "IMAGE"), ("montar", "PDF"), ("converter", "PDF")]
    assert list(conversor.initialized_pipelines) == [InputFormat.PDF]


def test_formato_que_nao_monta_nem_sozinho_e_formato_indisponivel_com_o_tipo_da_causa(
        tmp_path, monkeypatch):
    from scan_to_text.ler.layout import FormatoIndisponivel

    eventos = []
    _registrar_cargas(monkeypatch, eventos, texto=True)
    conversor = _ConversorComCache(
        eventos, falha=lambda formato: MemoryError() if formato.name == "PDF" else None)
    leitor = _leitor_com(conversor)
    leitor.ler_arquivo(_png_com_tinta(tmp_path / "scan.png"), "a" * 64)
    pdf = tmp_path / "com_texto.pdf"
    pdf.write_bytes(b"%PDF-1.7 inventado")
    with pytest.raises(FormatoIndisponivel) as erro:
        leitor.ler_arquivo(pdf, "b" * 64)
    assert isinstance(erro.value.__cause__, MemoryError)
    assert erro.value.formato == "PDF"
    assert str(erro.value) == "PDF MemoryError"  # o que o autoteste mostra


def test_formato_indisponivel_deixa_no_log_do_filho_o_formato_e_o_tipo_da_causa(
        pastas, monkeypatch, caplog):
    import logging

    origem, destino, dados, _ = pastas
    falta = OSError(None, "dll", None, 126)
    monkeypatch.setitem(modulo._PROCESSO, "leitor",
                        _leitor_preguicoso_com(_ConversorDeMentira(carga=falta)))
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _png_com_tinta(origem / "scan001.png")
    with caplog.at_level(logging.INFO, logger="scan_to_text"):
        with pytest.raises(modulo_ciclo.LeitorIndisponivel):
            modulo.tarefa(_tarefa(origem, destino, dados, arquivo))
    assert "formato_indisponivel formato=image causa=oserror winerror=126" in caplog.text


def test_modelos_que_nao_carregam_sao_leitor_indisponivel(monkeypatch):
    import scan_to_text.ler.layout as layout

    def sem_dll(_pasta):
        raise ImportError("DLL load failed")

    monkeypatch.setattr(layout, "Leitor", sem_dll)
    monkeypatch.setattr(modulo.modelos, "pasta_modelos", lambda: Path("modelos"))
    with pytest.raises(modulo_ciclo.LeitorIndisponivel):
        modulo._LeitorPreguicoso().preparar()


def test_tarefa_com_modelos_que_nao_carregam_nao_entrega_nada(pastas, monkeypatch):
    from test_processar import _pdf

    import scan_to_text.ler.layout as layout

    def sem_dll(_pasta):
        raise OSError(126, "modulo nao encontrado")

    origem, destino, dados, _ = pastas
    monkeypatch.setattr(layout, "Leitor", sem_dll)
    monkeypatch.setattr(modulo.modelos, "pasta_modelos", lambda: Path("modelos"))
    monkeypatch.setitem(modulo._PROCESSO, "leitor", modulo._LeitorPreguicoso())
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = _pdf(origem / "scan001.pdf", 1)
    with pytest.raises(modulo_ciclo.LeitorIndisponivel):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))
    assert not any(p.name.startswith("DESCONHECIDO") for p in destino.iterdir())


def test_ambiente_do_filho_que_nao_sobe_e_leitor_indisponivel(pastas, monkeypatch):
    origem, destino, dados, _ = pastas

    def sem_modelos():
        raise OSError(2, "pasta de modelos ausente")

    monkeypatch.delitem(modulo._PROCESSO, "leitor", raising=False)
    monkeypatch.setattr(modulo.rede, "ligar", lambda: None)
    monkeypatch.setattr(modulo.log, "configurar", lambda *a, **k: None)
    monkeypatch.setattr(modulo.modelos, "preparar_ambiente", sem_modelos)
    arquivo = origem / "scan001.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    with pytest.raises(modulo_ciclo.LeitorIndisponivel):
        modulo.tarefa(_tarefa(origem, destino, dados, arquivo))


def test_pacote_de_regras_que_nao_carrega_e_leitor_indisponivel(pastas, monkeypatch):
    origem, destino, dados, _ = pastas
    monkeypatch.setitem(modulo._PROCESSO, "leitor", object())
    monkeypatch.setitem(modulo._PROCESSO, "versoes", {"app": modulo.VERSAO})
    arquivo = origem / "scan001.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    with pytest.raises(modulo_ciclo.LeitorIndisponivel):
        modulo.tarefa({**_tarefa(origem, destino, dados, arquivo), "idioma": "xx-SEM-PACOTE"})


@pytest.mark.parametrize("aviso", ["aviso.vcpp_ausente", "aviso.windows_nao_suportado"])
def test_abrir_nao_liga_sozinho_com_aviso_bloqueante_do_preflight(pastas, aviso):
    m = _motor(pastas, preflight={"avisos": [aviso]})
    _configurar(m, pastas, ping_min=1)
    m.abrir()
    try:
        assert m.estado()["estado"] == "parado"
    finally:
        m.stop()


def test_leitor_indisponivel_aparece_nos_avisos_do_estado(pastas):
    origem = pastas[0]
    (origem / "scan.pdf").write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([ErroNaTarefa("LeitorIndisponivel", "")] * 50)
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)
    m._preparar()
    m._varrer()  # primeira vista: no START o arquivo já está estável
    m.start()
    try:
        _esperar(lambda: "leitor_indisponivel" in m.estado()["avisos"])
    finally:
        m.stop()


@pytest.mark.parametrize("winerror", [53, 59, 64, 67])
def test_falha_de_rede_nao_conta_contra_o_arquivo(pastas, winerror):
    origem = pastas[0]
    falso = TrabalhadorFalso([ErroNaTarefa("OSError", "", winerror=winerror)] * 3)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for _ in range(3):
        with pytest.raises(ErroNaTarefa):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 4


# tempo esgotado: arquivo pesado ou filho sem CPU ----------------------------------

_PARADO_COM_CPU_LIVRE = {"relogio_s": 1500, "cpu_s": 9, "folga": 0.9, "quer_cpu": False}
_PAGINANDO = {**_PARADO_COM_CPU_LIVRE, "falhas_de_pagina_por_s": 2000.0,
              "fisica_livre": 600 * 2**20, "fisica_disponivel": 2**30}
# O arquivo pesado de uma thread numa máquina ocupada, alocando e soltando
# memória: abaixo de um núcleo, centenas de milhares de falhas de página por
# segundo, e a estação com quase 3 GB de RAM livre. Abaixo do pico do leitor,
# mas nada espera a memória.
_ALOCANDO_COM_RAM_LIVRE = {"relogio_s": 1500, "cpu_s": 1197, "folga": 0.12, "quer_cpu": True,
                           "cpu_na_prova": 0.98, "falhas_de_pagina_por_s": 310565.0,
                           "fisica_livre": 2904 * 2**20, "fisica_disponivel": 2940 * 2**20}


@pytest.mark.parametrize("medida, conta", [
    pytest.param({"relogio_s": 1500, "cpu_s": 9, "folga": 0.01, "quer_cpu": True}, False,
                 id="sem_cpu"),
    pytest.param({"relogio_s": 1500, "cpu_s": 1400, "folga": 0.01, "quer_cpu": True}, True,
                 id="pesado"),
    pytest.param({"relogio_s": 1500, "cpu_s": 9, "folga": 0.9, "quer_cpu": True}, True,
                 id="parado_com_cpu_livre"),
    # O filho travado também tem pouca CPU numa máquina cheia, mas não a gasta
    # nem na prova: sem contar, o arquivo voltaria para sempre sem entrega.
    pytest.param({"relogio_s": 1500, "cpu_s": 9, "folga": 0.01, "quer_cpu": False}, True,
                 id="travado_na_maquina_cheia"),
    pytest.param({"relogio_s": 1500, "cpu_s": 9, "folga": 0.01}, True,
                 id="prova_sem_medida"),
    pytest.param({}, True, id="sem_medida"),
    # A estação paginando: o filho espera o disco, com pouca CPU e a CPU livre,
    # e segue buscando páginas na prova, com a RAM da estação quase no fim. A
    # leitura não cabe na memória física.
    pytest.param(_PAGINANDO, False, id="paginando"),
    # A memória comprimida volta pela CPU: o filho fica a meio núcleo, e a
    # máquina, ocupada com a busca das páginas.
    pytest.param({"relogio_s": 1500, "cpu_s": 750, "folga": 0.05, "quer_cpu": True,
                  "falhas_de_pagina_por_s": 30000.0, "fisica_livre": 400 * 2**20,
                  "fisica_disponivel": 2**31}, False, id="paginando_com_a_cpu_ocupada"),
    pytest.param({**_PAGINANDO, "falhas_de_pagina_por_s": 0.0}, True,
                 id="travado_com_pouca_memoria"),
    pytest.param({**_PAGINANDO, "fisica_disponivel": MUITA_MEMORIA}, True,
                 id="buscando_paginas_com_memoria"),
    pytest.param(_ALOCANDO_COM_RAM_LIVRE, True, id="alocando_com_ram_livre"),
    pytest.param({**_PARADO_COM_CPU_LIVRE, "falhas_de_pagina_por_s": 2000.0}, True,
                 id="paginas_sem_medida_da_memoria"),
])
def test_tempo_esgotado_so_conta_contra_o_arquivo_quando_o_filho_teve_cpu(pastas, medida, conta):
    origem = pastas[0]
    # Contado duas vezes, o terceiro envio já é a entrega sem leitura.
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **medida) for _ in range(2 if conta else 3)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for _ in range(2):
        with pytest.raises(TempoEsgotado if conta else FalhaDesculpada):
            m._processar(arquivo)
    esperado = [False, False, True] if conta else [False, False, False]
    if conta:
        m._processar(arquivo)
    else:
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)  # volta no próximo ping, ainda com leitura
    assert [t["desistir"] for t, _ in falso.tarefas] == esperado


# leitor que não carrega: o arquivo vai para o fim da fila ----------------------------

def _rodada(m) -> None:
    """Uma rodada do ping: processa na ordem da varredura e para no erro de ambiente."""
    for caminho in m._varrer():
        try:
            m._processar(caminho)
        except Exception as erro:  # noqa: BLE001
            if modulo_ciclo.falha_de_ambiente(erro):
                break


def test_arquivo_em_que_o_leitor_nao_carregou_vai_para_o_fim_da_fila(pastas):
    # A falha pode ser só do formato dele (o pipeline que não monta): os
    # arquivos de trás não ficam presos atrás dele, e ele não é contado.
    origem = pastas[0]
    primeiro, segundo = origem / "a.png", origem / "b.docx"
    primeiro.write_bytes(b"inventado-a")
    segundo.write_bytes(b"inventado-b")
    antes = time.time() - 3600
    os.utime(primeiro, (antes, antes))  # o mais antigo: vem primeiro na rodada
    falso = _TrabalhadorComArquivoRuim(primeiro, ErroNaTarefa("LeitorIndisponivel", ""))
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)
    m._preparar()
    m._varrer()  # primeira vista
    for _ in range(3):
        _rodada(m)
    assert [Path(t["arquivo"]).name for t, _ in falso.tarefas] == [
        "a.png", "b.docx", "a.png", "b.docx", "a.png"]
    assert not any(t["desistir"] for t, _ in falso.tarefas)
    falso.ruim = None  # o leitor voltou: a ordem também
    _rodada(m)
    assert m._varrer()[0] == primeiro


# a estação explica a falha: o arquivo espera, com teto ---------------------------

_SEM_CPU = {"relogio_s": 1500, "cpu_s": 9, "folga": 0.01, "quer_cpu": True}


def test_estouro_sem_cpu_espera_tres_vezes_e_o_seguinte_conta(pastas):
    # Numa estação sempre cheia, o arquivo que trava o leitor e passasse por
    # faminto em todo estouro nunca seria entregue.
    origem = pastas[0]
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU) for _ in range(5)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for vez in range(5):
        with pytest.raises(FalhaDesculpada if vez < modulo.DESCULPAS_ATE_CONTAR
                           else TempoEsgotado):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 5 + [True]


@pytest.mark.parametrize("erro, memoria", [
    pytest.param(lambda: TempoEsgotado("tempo", **_SEM_CPU), MUITA_MEMORIA, id="sem_cpu"),
    pytest.param(lambda: ErroNaTarefa("ConversionError", ""), 2**30, id="sem_memoria"),
])
def test_desculpa_encerra_a_rodada_e_o_arquivo_volta_depois_dos_outros(pastas, monkeypatch,
                                                                        erro, memoria):
    # Os de trás dariam com a mesma estação, e cada um gastaria uma desculpa
    # sua: só um arquivo por ping gasta desculpa. Nenhum aviso pausa a leitura.
    monkeypatch.setattr(modulo, "commit_livre", lambda: memoria)
    origem = pastas[0]
    ruim, bom = origem / "a.png", origem / "b.pdf"
    ruim.write_bytes(b"inventado-a")
    bom.write_bytes(b"inventado-b")
    antes = time.time() - 3600
    os.utime(ruim, (antes, antes))  # o mais antigo: vem primeiro na rodada
    falso = _TrabalhadorComArquivoRuim(ruim, erro())
    m = _motor(pastas, trabalhador=falso, relogio=Relogio())
    _configurar(m, pastas)  # ping de 2 min: a rodada seguinte não vem durante o teste
    m._preparar()
    m._varrer()  # primeira vista: no START os dois já estão estáveis
    m.start()
    try:
        _esperar(lambda: falso.tarefas and m.estado()["estado"] == "ocioso")
        assert [Path(t["arquivo"]) for t, _ in falso.tarefas] == [ruim]
        avisos = m.estado()["avisos"]
        assert "leitor_indisponivel" not in avisos and "disco_cheio" not in avisos
        assert {modulo.AVISO_ESTACAO_OCUPADA, modulo.AVISO_MEMORIA_BAIXA} & set(avisos)
        assert m._varrer() == [bom, ruim]
    finally:
        m.stop()


def test_falha_desculpada_leva_a_falha_da_estacao_como_causa(pastas):
    origem = pastas[0]
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    with pytest.raises(modulo_ciclo.FalhaDesculpada) as erro:
        m._processar(arquivo)
    assert isinstance(erro.value.__cause__, TempoEsgotado)


def _motor_ligado_sem_fila(pastas, monkeypatch, falso, **kw):
    """Motor ligado, com o ping preso na primeira varredura: quem processa é o
    teste, e nenhuma varredura do ping mexe nos avisos no meio dele."""
    m = _motor(pastas, trabalhador=falso, **kw)
    _configurar(m, pastas)
    m._preparar()
    solta, presa = _varredura_presa(m, monkeypatch)
    m.start()
    assert presa.wait(10)
    return m, solta


def test_estouro_sem_cpu_avisa_que_a_estacao_esta_ocupada_ate_a_proxima_leitura(pastas,
                                                                               monkeypatch):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU)])
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso)
    try:
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)
        assert modulo.AVISO_ESTACAO_OCUPADA in m.estado()["avisos"]
        m._processar(arquivo)  # lido
        assert modulo.AVISO_ESTACAO_OCUPADA not in m.estado()["avisos"]
    finally:
        solta.set()
        m.stop()


def test_estouro_com_a_estacao_paginando_avisa_que_falta_memoria(pastas, monkeypatch):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_PAGINANDO)])
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso)
    try:
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)
        avisos = m.estado()["avisos"]
        assert modulo.AVISO_MEMORIA_BAIXA in avisos
        assert modulo.AVISO_ESTACAO_OCUPADA not in avisos
    finally:
        solta.set()
        m.stop()


@pytest.mark.parametrize("pesado", [
    pytest.param({**_SEM_CPU, "cpu_s": 1400}, id="sem_prova"),
    pytest.param(_ALOCANDO_COM_RAM_LIVRE, id="alocando_com_ram_livre"),
])
def test_estouro_do_arquivo_pesado_nao_avisa(pastas, monkeypatch, pesado):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **pesado)])
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso)
    try:
        with pytest.raises(TempoEsgotado):
            m._processar(arquivo)
        avisos = m.estado()["avisos"]
        assert "aviso.estacao_ocupada" not in avisos and "aviso.memoria_baixa" not in avisos
    finally:
        solta.set()
        m.stop()


def test_aviso_da_estacao_sai_quando_nada_mais_espera_na_fila(pastas, monkeypatch):
    # O arquivo que esperou pode sair da fila sem leitura (entregue depois das
    # esperas, ou apagado da origem): o aviso diria que há arquivos esperando.
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU)])
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso)
    try:
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)
        monkeypatch.setattr(m._vigia, "varrer", lambda: [arquivo])
        assert m._varrer() == [arquivo]
        assert modulo.AVISO_ESTACAO_OCUPADA in m.estado()["avisos"]  # ainda espera
        monkeypatch.setattr(m._vigia, "varrer", lambda: [])
        assert m._varrer() == []
        assert modulo.AVISO_ESTACAO_OCUPADA not in m.estado()["avisos"]
    finally:
        solta.set()
        m.stop()


def test_aviso_da_estacao_sai_quando_so_resta_arquivo_que_ficou_de_fora(pastas, monkeypatch):
    # A própria entrega sem leitura falhou: o arquivo segue na varredura, mas
    # nada mais vai ao trabalhador até o app abrir de novo, e ninguém espera.
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU)]
                             + [ErroNaTarefa("ValueError", "")] * 3)
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso)
    try:
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)
        for _ in range(3):
            with pytest.raises(ErroNaTarefa):
                m._processar(arquivo)
        assert falso.tarefas[-1][0]["desistir"]
        m._processar(arquivo)
        assert len(falso.tarefas) == 4  # ficou de fora
        monkeypatch.setattr(m._vigia, "varrer", lambda: [arquivo])
        assert m._varrer() == [arquivo]
        assert modulo.AVISO_ESTACAO_OCUPADA not in m.estado()["avisos"]
    finally:
        solta.set()
        m.stop()


def test_start_comeca_sem_os_avisos_da_estacao_da_vez_anterior(pastas, monkeypatch):
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([TempoEsgotado("tempo", **_SEM_CPU)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    with pytest.raises(FalhaDesculpada):
        m._processar(arquivo)  # antes do STOP
    solta, presa = _varredura_presa(m, monkeypatch)  # a fila ainda não esvaziou
    m.start()
    try:
        assert presa.wait(10)
        assert modulo.AVISO_ESTACAO_OCUPADA not in m.estado()["avisos"]
    finally:
        solta.set()
        m.stop()


_FALHAS_DE_MEMORIA = [
    pytest.param(lambda: ErroNaTarefa("ConversionError", "document_converter.py:661 convert_all"),
                 id="conversao"),
    pytest.param(lambda: ErroNaTarefa("MemoryError", "rapid_ocr_model.py:686 __call__"),
                 id="memoria"),
    pytest.param(lambda: ErroNaTarefa("LeituraIncompleta", "layout.py:1 _converter"),
                 id="pagina_que_falta"),
    pytest.param(lambda: TrabalhadorCaiu("trabalhador_saiu codigo=3221225477"),
                 id="filho_que_cai"),
]


@pytest.mark.parametrize("erro", _FALHAS_DE_MEMORIA)
def test_falha_de_memoria_com_a_estacao_sem_memoria_espera_tres_vezes_e_o_seguinte_conta(
        pastas, monkeypatch, erro):
    monkeypatch.setattr(modulo, "commit_livre", lambda: 2**30)
    origem = pastas[0]
    falso = TrabalhadorFalso([erro() for _ in range(5)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for vez in range(5):
        with pytest.raises(FalhaDesculpada if vez < modulo.DESCULPAS_ATE_CONTAR
                           else type(erro())):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False] * 5 + [True]


@pytest.mark.parametrize("erro", _FALHAS_DE_MEMORIA)
def test_falha_de_memoria_com_a_estacao_com_memoria_conta(pastas, erro):
    origem = pastas[0]
    falso = TrabalhadorFalso([erro() for _ in range(2)])
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    for _ in range(2):
        with pytest.raises(type(erro())):
            m._processar(arquivo)
    m._processar(arquivo)
    assert [t["desistir"] for t, _ in falso.tarefas] == [False, False, True]


@pytest.mark.parametrize("erro, medida", [
    pytest.param(ErroNaTarefa("ConversionError", ""), [1], id="conversao"),
    pytest.param(TrabalhadorCaiu("trabalhador_saiu"), [1], id="filho_que_cai"),
    pytest.param(ErroNaTarefa("ValueError", ""), [], id="erro_comum"),
    pytest.param(TempoEsgotado("tempo", **_SEM_CPU), [], id="tempo_esgotado"),
    pytest.param(ErroNaTarefa("LeitorIndisponivel", ""), [], id="leitor_indisponivel"),
])
def test_memoria_da_estacao_e_medida_so_quando_explica_a_falha_e_com_o_filho_ja_fora(
        pastas, monkeypatch, erro, medida):
    origem = pastas[0]
    falso = TrabalhadorFalso([erro])
    medidas = []
    monkeypatch.setattr(modulo, "commit_livre",
                        lambda: medidas.append(falso.paradas) or MUITA_MEMORIA)
    m = _motor(pastas, trabalhador=falso)
    _configurar(m, pastas)
    m._preparar()
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    with pytest.raises((type(erro), FalhaDesculpada)):
        m._processar(arquivo)
    assert medidas == medida  # as paradas do filho na hora da medida


def test_memoria_baixa_na_leitura_tem_aviso_proprio_no_lugar_do_do_preflight(pastas,
                                                                            monkeypatch):
    # O do preflight, da abertura, diz só que a leitura pode ficar lenta; na
    # leitura, os arquivos esperam. Os dois juntos seriam o mesmo assunto duas vezes.
    monkeypatch.setattr(modulo, "commit_livre", lambda: 2**30)
    origem = pastas[0]
    arquivo = origem / "scan.pdf"
    arquivo.write_bytes(b"%PDF-1.7")
    falso = TrabalhadorFalso([ErroNaTarefa("ConversionError", "")])
    m, solta = _motor_ligado_sem_fila(pastas, monkeypatch, falso,
                                      preflight={"avisos": ["aviso.ram_baixa"]})
    try:
        assert modulo.AVISO_MEMORIA_BAIXA != "aviso.ram_baixa"
        with pytest.raises(FalhaDesculpada):
            m._processar(arquivo)
        avisos = m.estado()["avisos"]
        assert avisos.count(modulo.AVISO_MEMORIA_BAIXA) == 1
        assert "aviso.ram_baixa" not in avisos
        assert "leitor_indisponivel" not in avisos  # não pausa a leitura
        m._processar(arquivo)  # lido
        avisos = m.estado()["avisos"]
        assert modulo.AVISO_MEMORIA_BAIXA not in avisos and "aviso.ram_baixa" in avisos
    finally:
        solta.set()
        m.stop()


def test_avisos_da_estacao_tem_texto_e_proximo_passo_e_nao_bloqueiam():
    web = Path(modulo.__file__).parent / "tela" / "web"
    i18n = json.loads((web / "i18n" / "pt-BR.json").read_text(encoding="utf-8"))
    js = (web / "app.js").read_text(encoding="utf-8")
    bloqueiam = "".join(re.search(r"var " + nome + r" = \{(.*?)\};", js, re.S).group(1)
                        for nome in ("AVISO_BLOQUEIA", "AVISO_PAUSA"))
    for codigo in (modulo.AVISO_ESTACAO_OCUPADA, modulo.AVISO_MEMORIA_BAIXA):
        grupo, chave = codigo.split(".")
        assert isinstance(i18n[grupo].get(chave), str), codigo
        assert isinstance(i18n[grupo].get(chave + "_acao"), str), codigo
        assert chave not in bloqueiam, codigo


def test_memoria_livre_de_verdade():
    livre = _COMMIT_LIVRE()
    assert isinstance(livre, int) and livre > 0


def test_nomes_das_falhas_de_memoria_sao_os_das_classes():
    from docling.exceptions import ConversionError

    from scan_to_text.ler.layout import LeituraIncompleta

    assert set(modulo._FALHAS_DE_MEMORIA) == {MemoryError.__name__, ConversionError.__name__,
                                               LeituraIncompleta.__name__}


# página que o Docling não devolve ---------------------------------------------------

class _ConversorQuePerdePaginas:
    """A cada `convert`, devolve as páginas da próxima lista de `vezes`."""

    def __init__(self, vezes):
        self.vezes = list(vezes)
        self.conversoes = 0
        self.initialized_pipelines = {}

    def initialize_pipeline(self, formato):
        self.initialized_pipelines[formato] = object()

    def convert(self, fonte):
        self.conversoes += 1
        return _Resultado(self.vezes.pop(0))


def _pdf_com_texto(monkeypatch, paginas: int) -> None:
    import scan_to_text.ler.layout as layout

    monkeypatch.setattr(layout.leitor_pdf, "abrir", lambda caminho: [None] * paginas)
    monkeypatch.setattr(layout.leitor_pdf, "camada_texto_boa", lambda documento, i: True)
    monkeypatch.setattr(layout.leitor_pdf, "camada_texto_em_pe", lambda documento, i: True)


def test_pagina_que_o_docling_nao_devolve_faz_a_conversao_se_repetir(tmp_path):
    conversor = _ConversorQuePerdePaginas([[], [1]])
    paginas = _leitor_com(conversor).ler_arquivo(_png_com_tinta(tmp_path / "scan.png"), "a" * 64)
    assert len(paginas) == 1 and conversor.conversoes == 2


@pytest.mark.parametrize("nome, vezes", [("scan.png", [[], []]),
                                         ("com_texto.pdf", [[2], [2]])])
def test_pagina_que_falta_de_novo_e_leitura_incompleta(tmp_path, monkeypatch, nome, vezes):
    import scan_to_text.ler.layout as layout

    _pdf_com_texto(monkeypatch, 2)
    arquivo = tmp_path / nome
    if arquivo.suffix == ".pdf":
        arquivo.write_bytes(b"%PDF-1.7 inventado")
    else:
        _png_com_tinta(arquivo)
    conversor = _ConversorQuePerdePaginas(vezes)
    with pytest.raises(Exception) as erro:
        _leitor_com(conversor).ler_arquivo(arquivo, "a" * 64)
    assert type(erro.value).__name__ == "LeituraIncompleta"
    assert isinstance(erro.value, layout.LeituraIncompleta)
    assert str(erro.value) == "1"  # só o número da página que faltou
    assert conversor.conversoes == 2


def test_pagina_do_pdf_com_texto_e_a_do_mesmo_numero_e_nao_a_da_mesma_posicao(tmp_path,
                                                                              monkeypatch):
    # O Docling pode devolver a lista fora de ordem ou sem uma página: a
    # posição na lista não diz de que página o texto é.
    from types import SimpleNamespace

    from docling.datamodel.base_models import InputFormat

    import scan_to_text.ler.layout as layout

    def pagina(numero, texto):
        caixa = SimpleNamespace(l=10, t=10, r=90, b=20)
        retangulo = SimpleNamespace(to_bounding_box=lambda: SimpleNamespace(
            to_top_left_origin=lambda page_height: caixa))
        celula = SimpleNamespace(text=texto, confidence=1.0, from_ocr=False, rect=retangulo)
        return SimpleNamespace(page_no=numero, size=SimpleNamespace(width=100, height=100),
                               cells=[celula])

    documento = SimpleNamespace(iterate_items=lambda included_content_layers=None: iter(()))
    resultado = SimpleNamespace(pages=[pagina(2, "SEGUNDA"), pagina(1, "PRIMEIRA")],
                                document=documento)
    conversor = _ConversorQuePerdePaginas([])
    conversor.convert = lambda fonte: resultado
    leitor = object.__new__(layout.Leitor)
    leitor._conversor, leitor._orientador, leitor._formatos = conversor, None, InputFormat
    _pdf_com_texto(monkeypatch, 2)
    arquivo = tmp_path / "com_texto.pdf"
    arquivo.write_bytes(b"%PDF-1.7 inventado")
    paginas = leitor.ler_arquivo(arquivo, "a" * 64)
    assert [[b.texto for b in p.blocos] for p in paginas] == [["PRIMEIRA"], ["SEGUNDA"]]
