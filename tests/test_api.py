import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from scan_to_text import config, pacote
from scan_to_text.tela import api

BASE = "http://127.0.0.1:8123"
TOKEN = "t" * 43


class MotorFalso:
    def __init__(self):
        self.chamadas = []
        self.cfg = dict(config.PADRAO)
        self.erros = []
        self.pasta = "D:\\pacientes"
        self.situacao = {"estado": "parado", "configurado": False, "avisos": []}

    def estado(self):
        return dict(self.situacao)

    def start(self):
        self.chamadas.append("start")
        self.situacao["estado"] = "ocioso"

    def stop(self):
        self.chamadas.append("stop")
        self.situacao["estado"] = "parado"

    def config(self):
        return dict(self.cfg)

    def salvar_config(self, cfg):
        self.chamadas.append(("salvar", cfg))
        if not self.erros:
            self.cfg = dict(cfg)
        return list(self.erros)

    def escolher_pasta(self):
        return self.pasta

    def log_recente(self):
        return ["evento_a", "evento_b"]

    def salvar_log(self, destino):
        self.chamadas.append(("log", destino))
        return destino + "\\scan-to-text-log.txt"


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


@pytest.fixture
def motor():
    return MotorFalso()


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def cliente(motor, relogio):
    app = api.criar_app(motor, TOKEN, {BASE}, relogio=relogio)
    return TestClient(app, base_url=BASE)


def _h(**extra):
    return {api.CABECALHO_TOKEN: TOKEN, **extra}


# ─── portões ──────────────────────────────────────────────────────────────

def test_sem_token_e_403(cliente):
    assert cliente.get("/api/estado").status_code == 403
    assert cliente.post("/api/start").status_code == 403


def test_token_errado_e_403(cliente, motor):
    r = cliente.post("/api/start", headers={api.CABECALHO_TOKEN: "x" * 43})
    assert r.status_code == 403 and motor.chamadas == []


def test_token_certo_passa(cliente):
    r = cliente.get("/api/estado", headers=_h())
    assert r.status_code == 200 and r.json()["estado"] == "parado"


def test_origin_estranho_e_403_mesmo_com_token(cliente, motor):
    r = cliente.post("/api/start", headers=_h(Origin="http://atacante.exemplo"))
    assert r.status_code == 403 and motor.chamadas == []


def test_origin_da_propria_tela_passa(cliente, motor):
    r = cliente.post("/api/start", headers=_h(Origin=BASE))
    assert r.status_code == 200 and motor.chamadas == ["start"]


def test_host_estranho_e_403_rebinding_de_dns(cliente):
    r = cliente.get("/api/estado", headers=_h(Host="atacante.exemplo:8123"))
    assert r.status_code == 403
    assert cliente.get("/", headers={"Host": "atacante.exemplo:8123"}).status_code == 403


def test_origens_permitidas_podem_chegar_depois(motor):
    permitidas = set()
    cliente = TestClient(api.criar_app(motor, TOKEN, permitidas), base_url=BASE)
    assert cliente.get("/api/estado", headers=_h()).status_code == 403
    permitidas.add(BASE)
    assert cliente.get("/api/estado", headers=_h()).status_code == 200


def test_corpo_grande_demais_e_413(cliente):
    r = cliente.post("/api/config", headers=_h(), content=b"{" + b" " * (api.TETO_CORPO + 1) + b"}")
    assert r.status_code == 413


def test_sem_documentacao_automatica(cliente):
    for rota in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(rota, headers=_h()).status_code == 404


def test_caminho_nunca_na_url(cliente):
    for rota in cliente.app.routes:
        if isinstance(rota, APIRoute):
            assert rota.path.startswith("/api/"), rota.path
            assert not rota.dependant.query_params, rota.path
            assert not rota.dependant.path_params, rota.path


def test_cabecalhos_de_seguranca_em_toda_resposta(cliente):
    for r in (cliente.get("/api/estado", headers=_h()), cliente.get("/"),
              cliente.get("/api/estado")):
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp
        assert "unsafe" not in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store"


def test_pagina_sai_sem_token(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


# ─── rotas ────────────────────────────────────────────────────────────────

def test_start_e_stop(cliente, motor):
    assert cliente.post("/api/start", headers=_h()).json()["estado"] == "ocioso"
    assert cliente.post("/api/stop", headers=_h()).json()["estado"] == "parado"
    assert motor.chamadas == ["start", "stop"]


def test_estado_ganha_configurado_quando_o_motor_nao_diz(cliente, motor):
    motor.situacao = {"estado": "parado"}
    assert cliente.get("/api/estado", headers=_h()).json()["configurado"] is False
    motor.cfg.update(origens=["C:\\scanner"], destinos=["D:\\pacientes"])
    assert cliente.get("/api/estado", headers=_h()).json()["configurado"] is True


def test_config_le_e_grava_mesclando(cliente, motor, monkeypatch):
    monkeypatch.setattr(config, "_remoto", lambda letra: "\\\\srv\\scan" if letra == "Z:" else None)
    assert cliente.get("/api/config", headers=_h()).json() == config.PADRAO
    r = cliente.post("/api/config", headers=_h(),
                     json={"origens": ["Z:\\recepcao"], "destinos": ["D:\\pacientes"]})
    assert r.status_code == 200 and r.json()["erros"] == []
    salvo = motor.chamadas[-1][1]
    assert salvo["origens"] == ["\\\\srv\\scan\\recepcao"]
    assert salvo["destinos"] == ["D:\\pacientes"]
    assert salvo["ping_min"] == 2
    assert r.json()["config"]["origens"] == ["\\\\srv\\scan\\recepcao"]


def test_config_devolve_erros_do_motor(cliente, motor):
    motor.erros = ["destino_em_nuvem"]
    r = cliente.post("/api/config", headers=_h(), json={"destinos": ["C:\\nuvem"]})
    assert r.json()["erros"] == ["destino_em_nuvem"]
    assert r.json()["config"]["destinos"] == []


def test_config_corpo_que_nao_e_objeto_e_400(cliente, motor):
    for corpo in (b"[1]", b"nao json", b""):
        r = cliente.post("/api/config", headers=_h(), content=corpo)
        assert r.status_code == 400, corpo
    assert motor.chamadas == []


def test_config_idioma_apelido_vira_canonico_e_desconhecido_e_recusado(cliente, motor):
    r = cliente.post("/api/config", headers=_h(), json={"idioma_interface": "zh"})
    assert r.json()["erros"] == [] and motor.chamadas[-1][1]["idioma_interface"] == "zh-CN"
    n = len(motor.chamadas)
    r = cliente.post("/api/config", headers=_h(), json={"idiomas_documentos": ["pt-BR", "xx"]})
    assert r.json()["erros"] == ["idioma_desconhecido"] and len(motor.chamadas) == n


@pytest.mark.parametrize("corpo", [
    '{"ping_min": 0}', '{"ping_min": "abc"}', '{"ping_min": null}', '{"ping_min": Infinity}',
    '{"ping_min": NaN}', '{"days_back": -5}', '{"days_back": 2.5}', '{"estabilidade_s": 0}',
    '{"estabilidade_s": 1.5}', '{"ocioso_min": true}', '{"ocioso_min": 0.09}'])
def test_config_recusa_numero_invalido(cliente, motor, corpo):
    r = cliente.post("/api/config", headers=_h(), content=corpo)
    assert r.status_code == 200 and r.json()["erros"] == ["numero_invalido"], corpo
    assert motor.chamadas == []


def test_config_aceita_numero_na_borda(cliente, motor):
    r = cliente.post("/api/config", headers=_h(), json={
        "ping_min": 0.1, "ocioso_min": 0.1, "days_back": 0, "estabilidade_s": 1})
    assert r.json()["erros"] == []
    salvo = motor.chamadas[-1][1]
    assert (salvo["ping_min"], salvo["ocioso_min"], salvo["days_back"], salvo["estabilidade_s"]) \
        == (0.1, 0.1, 0, 1)


def test_config_descarta_chave_desconhecida_e_nao_muda_versao(cliente, motor):
    r = cliente.post("/api/config", headers=_h(),
                     json={"versao_config": 99, "lixo": {"a": 1}, "ping_min": 3})
    assert r.json()["erros"] == []
    salvo = motor.chamadas[-1][1]
    assert salvo["ping_min"] == 3 and salvo["versao_config"] == 1 and "lixo" not in salvo
    assert set(salvo) == set(config.PADRAO)


def test_config_guarda_campo_extra_que_ja_estava_no_arquivo(cliente, motor):
    motor.cfg["campo_futuro"] = {"a": 1}
    cliente.post("/api/config", headers=_h(), json={"ping_min": 3})
    assert motor.chamadas[-1][1]["campo_futuro"] == {"a": 1}


@pytest.mark.parametrize("chave", ["origens", "destinos"])
@pytest.mark.parametrize("valor", ["C:\\scan", None, {"a": "C:\\scan"}, ["C:\\scan", 7]])
def test_config_pastas_fora_de_lista_de_textos_e_400(cliente, motor, chave, valor):
    motor.cfg[chave] = ["C:\\ja_configurada"]
    r = cliente.post("/api/config", headers=_h(), json={chave: valor})
    assert r.status_code == 400 and r.json() == {"erro": "corpo_invalido"}
    assert motor.chamadas == []


def test_config_lista_vazia_remove_todas_as_pastas(cliente, motor):
    motor.cfg["origens"] = ["C:\\scan"]
    r = cliente.post("/api/config", headers=_h(), json={"origens": [], "destinos": ["  "]})
    assert r.json()["erros"] == []
    assert motor.chamadas[-1][1]["origens"] == [] and motor.chamadas[-1][1]["destinos"] == []


def test_escolher_pasta(cliente, motor):
    assert cliente.post("/api/escolher-pasta", headers=_h()).json() == \
        {"caminho": "D:\\pacientes", "sem_seletor": False}


def test_escolher_pasta_distingue_sem_seletor_de_cancelado(cliente, motor):
    # Sem seletor nativo (None), a tela oferece colar o caminho; cancelado
    # (""), a tela não abre nada nem diz que o seletor falhou.
    motor.pasta = None
    assert cliente.post("/api/escolher-pasta", headers=_h()).json() == \
        {"caminho": None, "sem_seletor": True}
    motor.pasta = ""
    assert cliente.post("/api/escolher-pasta", headers=_h()).json() == \
        {"caminho": None, "sem_seletor": False}


def test_config_tira_pasta_repetida_depois_da_forma_unc(cliente, motor, monkeypatch):
    # O seletor devolve a letra mapeada e a lista guarda o UNC: as duas formas
    # são a mesma pasta, e a configuração não pode ganhar a linha duas vezes.
    monkeypatch.setattr(config, "_remoto", lambda letra: "\\\\srv\\pacientes" if letra == "P:" else None)
    r = cliente.post("/api/config", headers=_h(), json={
        "origens": ["C:\\scan", "c:\\SCAN\\"],
        "destinos": ["\\\\srv\\pacientes", "P:\\", "\\\\SRV\\Pacientes"]})
    assert r.json()["erros"] == []
    salvo = motor.chamadas[-1][1]
    assert salvo["destinos"] == ["\\\\srv\\pacientes"]
    assert salvo["origens"] == ["C:\\scan"]


def test_log_e_salvar_log(cliente, motor):
    assert cliente.get("/api/log", headers=_h()).json() == {"linhas": ["evento_a", "evento_b"]}
    r = cliente.post("/api/salvar-log", headers=_h(), json={"destino": "E:\\"})
    assert r.json() == {"arquivo": "E:\\\\scan-to-text-log.txt"}
    assert motor.chamadas[-1] == ("log", "E:\\")


def test_salvar_log_sem_destino_e_400(cliente):
    assert cliente.post("/api/salvar-log", headers=_h(), json={}).status_code == 400


def test_salvar_log_que_falha_devolve_codigo(cliente, motor):
    def falha(_destino):
        raise PermissionError("somente leitura")
    motor.salvar_log = falha
    r = cliente.post("/api/salvar-log", headers=_h(), json={"destino": "E:\\"})
    assert r.status_code == 409 and r.json() == {"erro": "log_nao_gravado"}


def test_idiomas_na_ordem_fixa_com_bandeira_svg(cliente):
    idiomas = cliente.get("/api/idiomas", headers=_h()).json()["idiomas"]
    assert [i["codigo"] for i in idiomas] == ["de", "en", "es", "fr", "it", "pl", "pt-BR",
                                               "ar", "bn", "ko", "zh-CN", "ja"]
    for i in idiomas:
        svg = i["bandeira"]
        assert svg.startswith('<svg viewBox="0 0 30 20"'), i["codigo"]
        assert " id=" not in svg and "href" not in svg and "<script" not in svg
        assert not re.search("[\U0001F1E6-\U0001F1FF]", svg + i["nome"])
    rtl = {i["codigo"] for i in idiomas if i["rtl"]}
    assert rtl == {"ar"}
    traduzidos = {i["codigo"] for i in idiomas if i["traduzido"]}
    assert "pt-BR" in traduzidos
    assert next(i for i in idiomas if i["codigo"] == "pt-BR")["nome"] == "Português (Brasil)"


def _com_packs(motor, tmp_path, *idiomas):
    for cod in idiomas:
        (tmp_path / "packs" / cod).mkdir(parents=True)
    app = api.criar_app(motor, TOKEN, {BASE}, raiz_packs=tmp_path / "packs")
    return TestClient(app, base_url=BASE)


def test_idiomas_dizem_quais_tem_regras_de_leitura(motor, tmp_path):
    cliente = _com_packs(motor, tmp_path, "pt-BR")
    idiomas = cliente.get("/api/idiomas", headers=_h()).json()["idiomas"]
    assert {i["codigo"] for i in idiomas if i["regras"]} == {"pt-BR"}


def test_config_recusa_idioma_de_documento_sem_regras(motor, tmp_path):
    # Sem o pacote de regras, o motor não acha rótulo de nome nem de data:
    # o idioma não pode virar idioma de documento.
    cliente = _com_packs(motor, tmp_path, "pt-BR")
    r = cliente.post("/api/config", headers=_h(), json={"idiomas_documentos": ["pt-BR", "de"]})
    assert r.json()["erros"] == ["idioma_sem_regras"] and motor.chamadas == []
    r = cliente.post("/api/config", headers=_h(), json={"idioma_interface": "de"})
    assert r.json()["erros"] == [] and motor.chamadas[-1][1]["idioma_interface"] == "de"


def test_packs_padrao_sao_os_do_app():
    assert api.criar_app.__kwdefaults__["raiz_packs"] == pacote.RAIZ_PACKS


def test_bandeira_de_codigo_antigo_e_desconhecido():
    assert api.bandeira("zh") == api.bandeira("zh-CN") != ""
    assert api.bandeira("xx") == ""
    assert api.canonico("pt") == "pt-BR" and api.canonico("xx") == ""


# ─── navegador de pastas (modo reserva) ───────────────────────────────────

def test_pastas_lista_subpastas_sem_ocultas(cliente, tmp_path):
    for nome in ("b", "A", "c"):
        (tmp_path / nome).mkdir()
    (tmp_path / "arquivo.txt").write_text("x", encoding="utf-8")
    oculta = tmp_path / "oculta"
    oculta.mkdir()
    import ctypes
    ctypes.windll.kernel32.SetFileAttributesW(str(oculta), 0x2)
    r = cliente.post("/api/pastas", headers=_h(), json={"caminho": str(tmp_path)})
    corpo = r.json()
    assert corpo["pastas"] == ["A", "b", "c"]
    assert corpo["caminho"] == str(tmp_path)
    assert corpo["pai"] == str(tmp_path.parent)


def test_pastas_sem_caminho_lista_unidades(cliente):
    corpo = cliente.post("/api/pastas", headers=_h(), json={"caminho": None}).json()
    assert corpo["caminho"] == "" and corpo["pai"] is None
    assert any(p.upper().startswith("C:") for p in corpo["pastas"])


def test_pastas_inacessivel_devolve_codigo(cliente, tmp_path):
    r = cliente.post("/api/pastas", headers=_h(), json={"caminho": str(tmp_path / "nao_existe")})
    assert r.status_code == 409 and r.json() == {"erro": "pasta_inacessivel"}


# ─── batimento e carência ─────────────────────────────────────────────────

def test_sem_nenhum_pulso_a_espera_inicial_tem_teto(cliente, relogio):
    # Navegador fechado ou morto antes do primeiro pulso: o motor não pode
    # ficar rodando sem janela.
    relogio.agora += api.ESPERA_INICIAL_S - 1
    assert cliente.app.state.batimento.abandonada() is False
    relogio.agora += 1
    assert cliente.app.state.batimento.abandonada() is True


def test_pagina_carregando_renova_a_espera_inicial(cliente, relogio):
    relogio.agora += api.ESPERA_INICIAL_S - 1
    assert cliente.get("/").status_code == 200
    relogio.agora += api.ESPERA_INICIAL_S - 1
    assert cliente.app.state.batimento.abandonada() is False
    relogio.agora += 1
    assert cliente.app.state.batimento.abandonada() is True


def test_aviso_de_saida_durante_a_carga_da_carencia_de_20_s(cliente, relogio):
    r = cliente.post("/api/batimento", content=f'{{"token":"{TOKEN}","saindo":true}}',
                     headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert r.status_code == 200
    relogio.agora += api.CARENCIA_S
    assert cliente.app.state.batimento.abandonada() is True


def test_batimento_sem_token_nao_renova_a_espera_inicial(cliente, relogio):
    relogio.agora += api.ESPERA_INICIAL_S - 1
    assert cliente.post("/api/batimento", content='{"token":"errado"}').status_code == 403
    relogio.agora += 1
    assert cliente.app.state.batimento.abandonada() is True


def test_batimento_parado_da_carencia_de_20_s_depois_do_esperado(cliente, relogio):
    assert cliente.post("/api/batimento", headers=_h()).status_code == 200
    relogio.agora += api.BATIMENTO_S + api.CARENCIA_S - 1
    assert cliente.app.state.batimento.abandonada() is False
    relogio.agora += 1
    assert cliente.app.state.batimento.abandonada() is True


def test_aviso_de_saida_por_beacon_com_token_no_corpo(cliente, relogio):
    cliente.post("/api/batimento", headers=_h())
    r = cliente.post("/api/batimento", content=f'{{"token":"{TOKEN}","saindo":true}}',
                     headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert r.status_code == 200
    relogio.agora += api.CARENCIA_S - 1
    assert cliente.app.state.batimento.abandonada() is False
    relogio.agora += 1
    assert cliente.app.state.batimento.abandonada() is True


def test_f5_volta_dentro_da_carencia(cliente, relogio):
    cliente.post("/api/batimento", headers=_h(), json={"saindo": True})
    relogio.agora += 3
    cliente.post("/api/batimento", headers=_h())
    relogio.agora += api.CARENCIA_S
    assert cliente.app.state.batimento.abandonada() is False


def test_batimento_sem_token_e_403_e_nao_conta(cliente, relogio):
    cliente.post("/api/batimento", headers=_h())
    r = cliente.post("/api/batimento", content='{"token":"errado","saindo":true}')
    assert r.status_code == 403
    relogio.agora += api.CARENCIA_S
    assert cliente.app.state.batimento.abandonada() is False


# ─── conferir pasta ───────────────────────────────────────────────────────

def test_conferir_pasta_repassa_ao_motor(cliente, motor):
    motor.conferir_pasta = lambda caminho, papel, origens, destinos: (
        motor.chamadas.append(("conferir", caminho, papel, origens, destinos)) or ["destino_em_nuvem"])
    r = cliente.post("/api/conferir-pasta", headers=_h(),
                     json={"caminho": "G:\\x", "papel": "destinos", "origens": ["A"], "destinos": []})
    assert r.status_code == 200 and r.json()["erros"] == ["destino_em_nuvem"]
    assert motor.chamadas == [("conferir", "G:\\x", "destinos", ["A"], [])]


@pytest.mark.parametrize("corpo", [{"caminho": 3, "papel": "origens"},
                                   {"caminho": "C:\\x", "papel": "outro"},
                                   {"caminho": "C:\\x", "papel": "origens", "origens": "C:\\y"}])
def test_conferir_pasta_corpo_invalido_e_400(cliente, motor, corpo):
    motor.conferir_pasta = lambda *a: []
    assert cliente.post("/api/conferir-pasta", headers=_h(), json=corpo).status_code == 400


def test_unidades_vem_com_rotulo(cliente):
    r = cliente.post("/api/pastas", headers=_h(), json={}).json()
    assert r["caminho"] == "" and set(r["rotulos"]) == set(r["pastas"])
    assert all(isinstance(v, str) and v for v in r["rotulos"].values())


@pytest.mark.parametrize("raizes, na_nuvem", [((), []),
                                              (("\\\\NAS\\scanner",), ["Z:\\"]),
                                              (("Z:",), ["Z:\\"])])
def test_unidade_de_rede_nao_e_aberta_na_lista(cliente, monkeypatch, raizes, na_nuvem):
    # Abrir a raiz de uma unidade de rede com o servidor fora do ar espera o
    # tempo-limite do SMB: a guarda de nuvem compara o nome dela só como texto.
    import ntpath
    import os
    abertas, guarda_completa = [], []
    original = ntpath.realpath

    def espia(caminho, *a, **k):
        abertas.append(str(caminho))
        return original(caminho, *a, **k)

    monkeypatch.setattr(os, "listdrives", lambda: ["C:\\", "Z:\\"])
    monkeypatch.setattr(ntpath, "realpath", espia)
    monkeypatch.setattr(os.path, "realpath", espia)
    monkeypatch.setattr(api, "_de_rede", lambda unidade: unidade.upper().startswith("Z"))
    monkeypatch.setattr(config, "_remoto", lambda letra: "\\\\nas\\scanner" if letra == "Z:" else None)
    monkeypatch.setattr(api.nuvem, "raizes_sincronizadas", lambda: raizes)
    monkeypatch.setattr(api.nuvem, "em_pasta_sincronizada",
                        lambda caminho: guarda_completa.append(str(caminho)) or False)
    r = cliente.post("/api/pastas", headers=_h(), json={}).json()
    assert guarda_completa == ["C:\\"]
    assert abertas == []
    assert r["nuvem"] == na_nuvem
    assert r["rotulos"]["Z:\\"] == "\\\\nas\\scanner (Z:)"
