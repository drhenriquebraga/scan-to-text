import dataclasses
import sys
from pathlib import Path

import pytest

from scan_to_text.preflight import Fatos, avaliar, coletar

LTSB = Fatos(windows_build=14393, edicao="EnterpriseS", ram_total_gb=7.75,
             ram_livre_gb=2.0, disco_livre_gb=50.0, webview2_pacote=True,
             webview2_sistema=None, edge=False, chrome=False, vcpp_sistema=None,
             vcpp_pacote="14.44.35211.0", dotnet_release=394802, mfplat=True)


def _com(**mudancas) -> Fatos:
    return dataclasses.replace(LTSB, **mudancas)


def test_ltsb_com_pacote_completo_abre_janela_sem_aviso():
    r = avaliar(LTSB)
    assert r["degrau_tela"] == "janela"
    assert r["dotnet"] == "4.6.2"
    assert r["avisos"] == []


def test_sem_webview2_com_chrome_vai_para_navegador():
    assert avaliar(_com(webview2_pacote=False, chrome=True))["degrau_tela"] == "navegador"


def test_sem_nada_vai_para_console():
    assert avaliar(_com(webview2_pacote=False))["degrau_tela"] == "console"


def test_dotnet_antigo_impede_a_janela():
    r = avaliar(_com(dotnet_release=393295, edge=True))
    assert r["degrau_tela"] == "navegador"
    assert "aviso.dotnet_antigo" in r["avisos"]


def test_visual_cpp_ausente_ou_antigo_avisa():
    assert "aviso.vcpp_ausente" in avaliar(_com(vcpp_pacote=None))["avisos"]
    antigo = avaliar(_com(vcpp_pacote=None, vcpp_sistema="14.29.30133"))
    assert "aviso.vcpp_antigo" in antigo["avisos"]
    assert avaliar(_com(vcpp_pacote=None, vcpp_sistema="14.44.35211"))["avisos"] == []


def test_limites_de_memoria_disco_windows_e_mfplat():
    r = avaliar(_com(ram_livre_gb=0.8, disco_livre_gb=2.0, windows_build=9600, mfplat=False))
    assert {"aviso.ram_baixa", "aviso.disco_baixo", "aviso.windows_nao_suportado",
            "aviso.mfplat_ausente"} <= set(r["avisos"])


@pytest.mark.skipif(sys.platform != "win32", reason="lê o registro do Windows")
def test_coletar_nesta_maquina(tmp_path):
    f = coletar(tmp_path)
    assert f.windows_build >= 10240
    assert f.ram_total_gb > 0 and f.disco_livre_gb > 0
    assert f.webview2_pacote is False
    assert f.vcpp_pacote is None
