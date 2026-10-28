import json
import os
from pathlib import Path

import pytest

from scan_to_text import config


def _escrever(caminho: Path, dados, bom: bool = False) -> Path:
    texto = json.dumps(dados, ensure_ascii=False)
    caminho.write_bytes(texto.encode("utf-8-sig" if bom else "utf-8"))
    return caminho


# ─── ler ──────────────────────────────────────────────────────────────────

def test_sem_arquivo_devolve_o_padrao(tmp_path):
    cfg = config.ler(tmp_path / "config.json")
    assert cfg == config.PADRAO


def test_padrao_nao_e_compartilhado(tmp_path):
    cfg = config.ler(tmp_path / "config.json")
    cfg["origens"].append("X")
    assert config.PADRAO["origens"] == []
    assert config.ler(tmp_path / "config.json")["origens"] == []


def test_le_arquivo_com_bom_do_bloco_de_notas(tmp_path):
    arq = _escrever(tmp_path / "config.json", {"origens": ["C:\\scanner"]}, bom=True)
    assert config.ler(arq)["origens"] == ["C:\\scanner"]


def test_campo_faltando_vem_do_padrao_e_campo_extra_fica(tmp_path):
    arq = _escrever(tmp_path / "config.json", {"ping_min": 5, "campo_futuro": {"a": 1}})
    cfg = config.ler(arq)
    assert cfg["ping_min"] == 5
    assert cfg["days_back"] == 7 and cfg["estabilidade_s"] == 60
    assert cfg["idiomas_documentos"] == ["pt-BR"]
    assert cfg["campo_futuro"] == {"a": 1}


def test_valor_de_tipo_errado_volta_ao_padrao(tmp_path):
    arq = _escrever(tmp_path / "config.json", {
        "ping_min": "abc", "days_back": -3, "estabilidade_s": True, "ocioso_min": 0,
        "origens": "C:\\scanner", "destinos": [1, "D:\\saida", ""],
        "idioma_interface": 7, "idiomas_documentos": []})
    cfg = config.ler(arq)
    assert cfg["ping_min"] == 2
    assert cfg["days_back"] == 7
    assert cfg["estabilidade_s"] == 60
    assert cfg["ocioso_min"] == 2
    assert cfg["origens"] == []
    assert cfg["destinos"] == ["D:\\saida"]
    assert cfg["idioma_interface"] == "pt-BR"
    assert cfg["idiomas_documentos"] == ["pt-BR"]


def test_idiomas_de_documento_antigos_viram_o_codigo_canonico(tmp_path):
    arq = _escrever(tmp_path / "config.json",
                    {"idiomas_documentos": ["pt", "zh", "zh-Hans", "pt-BR", "en"]})
    assert config.ler(arq)["idiomas_documentos"] == ["pt-BR", "zh-CN", "en"]


def test_days_back_zero_e_valido(tmp_path):
    arq = _escrever(tmp_path / "config.json", {"days_back": 0})
    assert config.ler(arq)["days_back"] == 0


def test_intervalos_com_fracao_valem_a_partir_do_minimo(tmp_path):
    arq = _escrever(tmp_path / "config.json", {"ping_min": 0.1, "ocioso_min": 0.5})
    cfg = config.ler(arq)
    assert cfg["ping_min"] == 0.1 and cfg["ocioso_min"] == 0.5
    arq = _escrever(tmp_path / "config.json", {"ping_min": 0.09, "ocioso_min": 0.09})
    cfg = config.ler(arq)
    assert cfg["ping_min"] == 2 and cfg["ocioso_min"] == 2


def test_numero_nao_finito_volta_ao_padrao(tmp_path):
    arq = tmp_path / "config.json"
    arq.write_text('{"ping_min": Infinity, "ocioso_min": NaN}', encoding="utf-8")
    cfg = config.ler(arq)
    assert cfg["ping_min"] == 2 and cfg["ocioso_min"] == 2


def test_numero_valido_segue_os_limites():
    assert config.numero_valido("ping_min", 0.1) and not config.numero_valido("ping_min", 0)
    assert config.numero_valido("days_back", 0) and not config.numero_valido("days_back", 2.5)
    assert not config.numero_valido("estabilidade_s", True)
    assert not config.numero_valido("estabilidade_s", "60")
    assert not config.numero_valido("ping_min", float("inf"))
    assert set(config.LIMITES) == {"versao_config", "ping_min", "days_back", "estabilidade_s",
                                   "ocioso_min"}


def test_json_quebrado_levanta_config_invalida(tmp_path):
    arq = tmp_path / "config.json"
    arq.write_text('{"origens": [', encoding="utf-8")
    with pytest.raises(config.ConfigInvalida):
        config.ler(arq)


def test_json_que_nao_e_objeto_levanta_config_invalida(tmp_path):
    arq = _escrever(tmp_path / "config.json", ["origens"])
    with pytest.raises(config.ConfigInvalida):
        config.ler(arq)


# ─── gravar ───────────────────────────────────────────────────────────────

def test_gravar_e_ler_devolve_o_mesmo(tmp_path):
    cfg = dict(config.PADRAO, origens=["\\\\servidor\\scanner\\recepção"], ping_min=3)
    arq = tmp_path / "sub" / "config.json"
    config.gravar(arq, cfg)
    assert config.ler(arq) == cfg
    assert "recepção" in arq.read_text(encoding="utf-8")
    assert sorted(p.name for p in arq.parent.iterdir()) == ["config.json"]


def test_gravar_com_falha_mantem_o_anterior_e_nao_deixa_temporario(tmp_path, monkeypatch):
    arq = tmp_path / "config.json"
    config.gravar(arq, dict(config.PADRAO, ping_min=4))

    def falha(*_a, **_k):
        raise PermissionError("antivirus segurando o arquivo")

    monkeypatch.setattr(config.os, "replace", falha)
    monkeypatch.setattr(config.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        config.gravar(arq, dict(config.PADRAO, ping_min=9))
    assert config.ler(arq)["ping_min"] == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_gravar_tenta_de_novo_quando_o_arquivo_esta_preso(tmp_path, monkeypatch):
    arq = tmp_path / "config.json"
    original = os.replace
    tentativas = []

    def preso_uma_vez(a, b):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise PermissionError("preso")
        return original(a, b)

    monkeypatch.setattr(config.os, "replace", preso_uma_vez)
    monkeypatch.setattr(config.time, "sleep", lambda _s: None)
    config.gravar(arq, dict(config.PADRAO, ping_min=6))
    assert config.ler(arq)["ping_min"] == 6 and len(tentativas) == 2


# ─── validar ──────────────────────────────────────────────────────────────

def _validar(cfg, legiveis=(), gravaveis=(), nuvem=()):
    def dentro(caminho, conjunto):
        return any(str(caminho).casefold() == c.casefold() for c in conjunto)
    return config.validar(
        dict(config.PADRAO, **cfg),
        pasta_legivel=lambda p: dentro(p, legiveis),
        pasta_gravavel=lambda p: dentro(p, gravaveis),
        em_nuvem=lambda p: dentro(p, nuvem))


def test_config_valida_nao_tem_erro():
    assert _validar({"origens": ["C:\\scanner"], "destinos": ["D:\\pacientes"]},
                    legiveis=["C:\\scanner"], gravaveis=["D:\\pacientes"]) == []


def test_lista_vazia_nao_e_erro_de_validacao():
    assert _validar({}) == []


def test_origem_ilegivel():
    assert _validar({"origens": ["\\\\servidor\\scanner"]}) == ["origem_ilegivel"]


def test_destino_nao_gravavel():
    assert _validar({"destinos": ["D:\\pacientes"]}) == ["destino_ilegivel"]


def test_destino_dentro_de_origem():
    erros = _validar({"origens": ["C:\\Scanner"], "destinos": ["c:\\scanner\\saida"]},
                     legiveis=["C:\\Scanner"], gravaveis=["c:\\scanner\\saida"])
    assert erros == ["destino_em_origem"]


def test_destino_igual_a_origem_conta_uma_vez():
    erros = _validar({"origens": ["C:\\scanner"], "destinos": ["C:\\scanner\\"]},
                     legiveis=["C:\\scanner"], gravaveis=["C:\\scanner"])
    assert erros == ["destino_em_origem"]


def test_origem_dentro_de_destino():
    erros = _validar({"origens": ["D:\\pacientes\\scanner"], "destinos": ["D:\\pacientes"]},
                     legiveis=["D:\\pacientes\\scanner"], gravaveis=["D:\\pacientes"])
    assert erros == ["origem_em_destino"]


def test_prefixo_de_nome_nao_e_aninhamento():
    erros = _validar({"origens": ["C:\\AI_IMPORT"], "destinos": ["C:\\AI_IMPORTANTE"]},
                     legiveis=["C:\\AI_IMPORT"], gravaveis=["C:\\AI_IMPORTANTE"])
    assert erros == []


def test_destino_em_nuvem():
    erros = _validar({"destinos": ["C:\\Users\\x\\Meu Drive\\pacientes"]},
                     gravaveis=["C:\\Users\\x\\Meu Drive\\pacientes"],
                     nuvem=["C:\\Users\\x\\Meu Drive\\pacientes"])
    assert erros == ["destino_em_nuvem"]


def _juncao(alvo: Path, link: Path) -> None:
    _winapi = pytest.importorskip("_winapi")
    _winapi.CreateJunction(str(alvo), str(link))


def _validar_real(origens, destinos):
    return config.validar(dict(config.PADRAO, origens=[str(o) for o in origens],
                               destinos=[str(d) for d in destinos]),
                          pasta_legivel=lambda p: True, pasta_gravavel=lambda p: True,
                          em_nuvem=lambda p: False)


def test_destino_que_entra_na_origem_por_juncao(tmp_path):
    origem = tmp_path / "origem"
    origem.mkdir()
    _juncao(origem, tmp_path / "atalho")
    assert _validar_real([origem], [tmp_path / "atalho" / "saida"]) == ["destino_em_origem"]


def test_origem_que_entra_no_destino_por_juncao(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    _juncao(destino, tmp_path / "atalho")
    assert _validar_real([tmp_path / "atalho" / "scanner"], [destino]) == ["origem_em_destino"]


def test_juncao_para_pasta_vizinha_nao_e_aninhamento(tmp_path):
    for nome in ("origem", "destino"):
        (tmp_path / nome).mkdir()
    _juncao(tmp_path / "destino", tmp_path / "atalho")
    assert _validar_real([tmp_path / "origem"], [tmp_path / "atalho"]) == []


def test_destino_dentro_de_origem_nao_tem_a_gravacao_testada():
    # Testar a gravação cria um arquivo na pasta: dentro de uma origem, nunca.
    testados = []
    erros = config.validar(
        dict(config.PADRAO, origens=["C:\\scanner"],
             destinos=["C:\\scanner", "c:\\scanner\\saida", "D:\\pacientes"]),
        pasta_legivel=lambda p: True,
        pasta_gravavel=lambda p: testados.append(str(p)) or True,
        em_nuvem=lambda p: False)
    assert erros == ["destino_em_origem"]
    assert testados == ["D:\\pacientes"]


def test_varios_erros_saem_uma_vez_cada_na_ordem_fixa():
    erros = _validar({"origens": ["C:\\a", "C:\\b"], "destinos": ["C:\\a\\x", "E:\\n"]},
                     gravaveis=["C:\\a\\x", "E:\\n"], nuvem=["E:\\n"])
    assert erros == ["origem_ilegivel", "destino_em_origem", "destino_em_nuvem"]


# ─── para_unc ─────────────────────────────────────────────────────────────

def test_letra_mapeada_vira_unc(monkeypatch):
    monkeypatch.setattr(config, "_remoto", lambda letra: "\\\\servidor\\scanner"
                        if letra.upper() == "Z:" else None)
    assert config.para_unc("Z:\\recepcao\\hoje") == "\\\\servidor\\scanner\\recepcao\\hoje"
    assert config.para_unc("z:\\") == "\\\\servidor\\scanner"
    assert config.para_unc("Z:") == "\\\\servidor\\scanner"


def test_letra_local_e_unc_ficam_como_estao(monkeypatch):
    monkeypatch.setattr(config, "_remoto", lambda letra: None)
    assert config.para_unc("C:\\scanner") == "C:\\scanner"
    assert config.para_unc("\\\\servidor\\scanner") == "\\\\servidor\\scanner"
    assert config.para_unc("") == ""


def test_remoto_real_de_letra_local_e_none():
    assert config._remoto("C:") is None
