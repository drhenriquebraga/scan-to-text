import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "packager"))

import montar_kit  # noqa: E402
import montar_pacote as mp  # noqa: E402
import publicar as pub  # noqa: E402

CRLF_ASCII = b"@echo off\r\nREM teste\r\npause\r\nexit /b 0\r\n"


def _sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _zip(caminho: Path, arquivos: dict[str, bytes]) -> bytes:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho, "w") as z:
        for nome, dados in arquivos.items():
            z.writestr(nome, dados)
    return caminho.read_bytes()


def _roda(caminho: Path, pacote: str, licenca: str = "MIT") -> bytes:
    nome = caminho.name.split("-")[0]
    versao = caminho.name.split("-")[1]
    info = f"{nome}-{versao}.dist-info"
    return _zip(caminho, {
        f"{pacote}/__init__.py": b"X = 1\n",
        f"{nome}-{versao}.data/purelib/{pacote}_extra.py": b"Y = 2\n",
        f"{nome}-{versao}.data/scripts/{pacote}-cli": b"#!python\n",
        f"{info}/METADATA": (f"Metadata-Version: 2.4\nName: {nome}\nVersion: {versao}\n"
                             f"License-Expression: {licenca}\n").encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\n",
        f"{info}/RECORD": b"",
    })


def _fonte(pasta: Path, versao: str = "1.2.3") -> Path:
    (pasta / "app" / "scan_to_text").mkdir(parents=True)
    (pasta / "app" / "scan_to_text" / "__init__.py").write_text('"""x."""\n', encoding="utf-8")
    (pasta / "app" / "scan_to_text" / "__main__.py").write_text("print(1)\n", encoding="utf-8")
    (pasta / "app" / "scan_to_text" / "__pycache__").mkdir()
    (pasta / "app" / "scan_to_text" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (pasta / "app" / "packs" / "pt-BR").mkdir(parents=True)
    (pasta / "app" / "packs" / "pt-BR" / "textos.yaml").write_text("a: b\n", encoding="utf-8")
    (pasta / "packager").mkdir()
    (pasta / "packager" / "INSTALL.cmd").write_bytes(CRLF_ASCII)
    (pasta / "packager" / "LEIA-ME.txt").write_bytes(b"Leia-me\r\n")
    (pasta / "diagnostico").mkdir()
    (pasta / "diagnostico" / "TESTAR.cmd").write_bytes(CRLF_ASCII)
    (pasta / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{versao}"\n',
                                          encoding="utf-8")
    # A cópia pública de verdade só sai selada da varredura do publicar.py.
    pub.selar(pasta)
    return pasta


def _cenario(tmp_path: Path) -> dict:
    terceiros = tmp_path / "terceiros"
    embed = _zip(terceiros / "python-embed.zip", {
        "python.exe": b"MZ python", "pythonw.exe": b"MZ pythonw", "python314.dll": b"MZ dll",
        "python314.zip": b"PK", "vcruntime140.dll": b"MZ vc do python",
        "python314._pth": b"python314.zip\r\n.\r\n\r\n#import site\r\n", "LICENSE.txt": b"PSF",
    })
    cab = b"MSCF falso"
    (terceiros / "wv2.x64.cab").write_bytes(cab)
    roda = _roda(terceiros / "rodas" / "falso-1.0-py3-none-any.whl", "falso")
    fonte_local = b"codigo-fonte"
    (terceiros / "rodas_locais").mkdir(parents=True)
    (terceiros / "rodas_locais" / "proxy_tools-0.1.0.tar.gz").write_bytes(fonte_local)
    _roda(terceiros / "rodas_locais" / "proxy_tools-0.1.0-py3-none-any.whl", "proxy_tools")
    modelos = tmp_path / "modelos"
    (modelos / "RapidOcr").mkdir(parents=True)
    (modelos / "RapidOcr" / "det.onnx").write_bytes(b"onnx")
    componentes = [
        {"id": "python", "versao": "3.14.7", "url": "https://x/python-embed.zip",
         "arquivo": "python-embed.zip", "sha256": _sha(embed), "licenca": "PSF-2.0"},
        {"id": "webview2", "versao": "154.0", "url": "https://x/wv2.x64.cab",
         "arquivo": "wv2.x64.cab", "sha256": _sha(cab), "licenca": "termos da Microsoft"},
    ]
    catalogo = {
        "componentes": componentes,
        "rodas": [{"nome": "falso", "versao": "1.0", "arquivo": "falso-1.0-py3-none-any.whl",
                   "url": "https://x/f.whl", "sha256": _sha(roda)}],
        "rodas_locais": [{"nome": "proxy-tools", "versao": "0.1.0",
                          "arquivo": "proxy_tools-0.1.0.tar.gz", "url": "https://x/p.tar.gz",
                          "sha256": _sha(fonte_local), "licenca": "MIT"}],
        "modelos": [{"caminho": "RapidOcr/det.onnx", "tamanho": 4, "sha256": _sha(b"onnx"),
                     "licenca": "Apache-2.0", "origem": "teste"}],
    }
    return {"fonte": _fonte(tmp_path / "publica"), "terceiros": terceiros, "modelos": modelos,
            "catalogo": catalogo, "destino": tmp_path / "montagem" / "Scan-to-Text"}


def _extrair_cab_falso(cab: Path, pasta: Path) -> None:
    alvo = pasta / "Microsoft.WebView2.FixedVersionRuntime.154.0.x64"
    alvo.mkdir(parents=True)
    (alvo / "msedgewebview2.exe").write_bytes(b"MZ webview2")
    (alvo / "EBWebView").mkdir()


def _montar_arvore(c: dict, **extra) -> list[str]:
    return mp.montar_arvore(c["fonte"], c["destino"], c["catalogo"], c["terceiros"], c["modelos"],
                            vs_raizes=extra.pop("vs_raizes", []), extrair_cab=_extrair_cab_falso,
                            dizer=lambda *_: None, **extra)


def _visual_studio(raiz: Path, versao: str) -> Path:
    crt = raiz / "2022" / "Community" / "VC" / "Redist" / "MSVC" / versao / "x64" / "Microsoft.VC143.CRT"
    crt.mkdir(parents=True)
    for dll in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "concrt140.dll"):
        (crt / dll).write_bytes(f"MZ {versao} {dll}".encode())
    return crt


# ---------------------------------------------------------------- árvore

def test_monta_a_arvore_do_pacote(tmp_path):
    c = _cenario(tmp_path)
    _montar_arvore(c)
    d = c["destino"]
    assert (d / "python" / "python.exe").read_bytes() == b"MZ python"
    pth = (d / "python" / "python314._pth").read_bytes()
    assert b"..\\app\r\n" in pth and b"Lib\\site-packages\r\n" in pth
    assert b"\r\nimport site\r\n" in pth and b"#import site" not in pth
    site = d / "python" / "Lib" / "site-packages"
    assert (site / "falso" / "__init__.py").is_file()
    assert (site / "falso_extra.py").is_file()
    assert (site / "proxy_tools" / "__init__.py").is_file()
    assert not list(site.rglob("*-cli")) and not (site / "bin").exists()
    assert (d / "app" / "scan_to_text" / "__main__.py").is_file()
    assert (d / "app" / "packs" / "pt-BR" / "textos.yaml").is_file()
    assert not list(d.rglob("__pycache__"))
    assert (d / "modelos" / "RapidOcr" / "det.onnx").read_bytes() == b"onnx"
    assert (d / "webview2" / "msedgewebview2.exe").is_file()
    assert (d / "LEIA-ME.txt").is_file()
    assert (d / "entrega.txt").read_bytes() == b"PROJETO=SCAN_TO_TEXT\r\nVERSAO=1.2.3\r\n"
    avisos = (d / "AVISOS-DE-TERCEIROS.txt").read_text(encoding="utf-8")
    assert "WebView2" in avisos and "H.264" in avisos and "AVC" in avisos
    assert "PSF-2.0" in avisos and "RapidOcr/det.onnx" in avisos


def test_sem_visual_studio_relata_e_segue(tmp_path):
    c = _cenario(tmp_path)
    avisos = _montar_arvore(c)
    assert avisos == ["vcpp_ausente"]
    assert not (c["destino"] / "python" / "msvcp140.dll").exists()
    assert (c["destino"] / "python" / "vcruntime140.dll").read_bytes() == b"MZ vc do python"
    assert "VISUAL C++" not in (c["destino"] / "AVISOS-DE-TERCEIROS.txt").read_text(encoding="utf-8")


def test_runtime_visual_cpp_sai_do_redist_mais_novo(tmp_path):
    c = _cenario(tmp_path)
    vs = tmp_path / "Microsoft Visual Studio"
    _visual_studio(vs, "14.38.33130")
    _visual_studio(vs, "14.44.35112")
    avisos = _montar_arvore(c, vs_raizes=[vs])
    assert "vcpp_ausente" not in avisos
    python = c["destino"] / "python"
    assert (python / "msvcp140.dll").read_bytes() == b"MZ 14.44.35112 msvcp140.dll"
    assert (python / "msvcp140_1.dll").is_file() and (python / "concrt140.dll").is_file()
    assert (python / "vcruntime140.dll").read_bytes() == b"MZ 14.44.35112 vcruntime140.dll"
    assert "VISUAL C++" in (c["destino"] / "AVISOS-DE-TERCEIROS.txt").read_text(encoding="utf-8")


def test_redist_antigo_demais_nao_serve(tmp_path):
    vs = tmp_path / "vs"
    _visual_studio(vs, "14.29.30133")
    assert mp.achar_crt([vs]) is None


def test_hash_divergente_recusa_a_montagem(tmp_path):
    c = _cenario(tmp_path)
    (c["terceiros"] / "rodas" / "falso-1.0-py3-none-any.whl").write_bytes(b"adulterada")
    with pytest.raises(mp.MontagemRecusada, match="falso-1.0"):
        _montar_arvore(c)


def test_licenca_proibida_recusa_a_montagem(tmp_path):
    c = _cenario(tmp_path)
    roda = _roda(c["terceiros"] / "rodas" / "falso-1.0-py3-none-any.whl", "falso", "GPL-3.0-only")
    c["catalogo"]["rodas"][0]["sha256"] = _sha(roda)
    with pytest.raises(mp.MontagemRecusada, match="GPL"):
        _montar_arvore(c)


def test_leia_me_sai_ascii_e_crlf_mesmo_de_checkout_em_lf(tmp_path):
    # O Bloco de Notas do LTSB 2016 não quebra linha em LF.
    c = _cenario(tmp_path)
    (c["fonte"] / "packager" / "LEIA-ME.txt").write_bytes(b"Linha 1\nLinha 2\r\nLinha 3\n")
    _montar_arvore(c)
    assert (c["destino"] / "LEIA-ME.txt").read_bytes() == b"Linha 1\r\nLinha 2\r\nLinha 3\r\n"
    (c["fonte"] / "packager" / "LEIA-ME.txt").write_bytes("Instalação\r\n".encode("utf-8"))
    with pytest.raises(mp.MontagemRecusada, match="LEIA-ME"):
        _montar_arvore(c)


def test_cmd_com_lf_recusa(tmp_path):
    c = _cenario(tmp_path)
    (c["fonte"] / "packager" / "INSTALL.cmd").write_bytes(b"@echo off\npause\n")
    with pytest.raises(mp.MontagemRecusada, match="INSTALL.cmd"):
        mp.exigir_cmd(c["fonte"] / "packager" / "INSTALL.cmd")


# ---------------------------------------------------------------- zip e entrega

def test_recusa_montar_da_oficina(tmp_path):
    with pytest.raises(mp.MontagemRecusada, match="oficina"):
        mp.exigir_copia_publica(RAIZ)
    with pytest.raises(mp.MontagemRecusada, match="oficina"):
        mp.exigir_copia_publica(RAIZ / "app")
    # Cópia recusada pela publicação fica vazia e não serve de fonte.
    with pytest.raises(mp.MontagemRecusada, match="publicar.py"):
        mp.exigir_copia_publica(tmp_path)
    mp.exigir_copia_publica(_fonte(tmp_path / "publica"))


def test_zip_unico_deterministico_com_copia_latest_e_sha256sums(tmp_path, monkeypatch):
    c = _cenario(tmp_path)
    monkeypatch.setenv(mp.VARIAVEL_ENTREGA, str(tmp_path / "deploy"))
    kw = dict(catalogo=c["catalogo"], terceiros=c["terceiros"], modelos=c["modelos"],
              montagem=tmp_path / "montagem", vs_raizes=[], extrair_cab=_extrair_cab_falso,
              dizer=lambda *_: None)
    zip1 = mp.montar(c["fonte"], **kw)
    assert zip1 == tmp_path / "deploy" / "SCAN_TO_TEXT" / "v1.2.3" / "SCAN_TO_TEXT_v1.2.3.zip"
    with zipfile.ZipFile(zip1) as z:
        nomes = z.namelist()
    raiz_do_zip = {n.split("/")[0] for n in nomes}
    assert raiz_do_zip == {"INSTALL.cmd", "Scan-to-Text"}
    assert "Scan-to-Text/python/python.exe" in nomes
    # A cópia de nome fixo é o alvo de releases/latest/download/SCAN_TO_TEXT_LATEST.zip.
    latest = zip1.parent / "SCAN_TO_TEXT_LATEST.zip"
    assert latest.read_bytes() == zip1.read_bytes()
    soma = _sha(zip1.read_bytes())
    somas = (zip1.parent / "SHA256SUMS").read_bytes()
    assert somas == f"{soma} *SCAN_TO_TEXT_v1.2.3.zip\n{soma} *SCAN_TO_TEXT_LATEST.zip\n".encode()
    assert sorted(p.name for p in zip1.parent.iterdir()) == [
        "SCAN_TO_TEXT_LATEST.zip", "SCAN_TO_TEXT_v1.2.3.zip", "SHA256SUMS"]
    primeiro = zip1.read_bytes()
    mp.montar(c["fonte"], **kw)
    assert zip1.read_bytes() == primeiro


def test_entrega_nao_sobrescreve_versao_ja_entregue_com_outro_conteudo(tmp_path):
    zip1 = tmp_path / "SCAN_TO_TEXT_v1.0.zip"
    zip1.write_bytes(b"primeiro")
    mp.entregar(zip1, tmp_path / "deploy", "1.0")
    zip1.write_bytes(b"segundo")
    with pytest.raises(mp.MontagemRecusada, match="ja entregue"):
        mp.entregar(zip1, tmp_path / "deploy", "1.0")
    mp.entregar(zip1, tmp_path / "deploy", "1.0", substituir=True)
    pasta = tmp_path / "deploy" / "SCAN_TO_TEXT" / "v1.0"
    assert (pasta / zip1.name).read_bytes() == b"segundo"
    assert (pasta / "SCAN_TO_TEXT_LATEST.zip").read_bytes() == b"segundo"


def test_raiz_de_entrega_pela_variavel_ou_pela_marca(tmp_path):
    eco = tmp_path / "eco"
    projeto = eco / "PROJETO_X"
    projeto.mkdir(parents=True)
    (eco / "ENTREGA.md").write_text(
        "# Politica\n\n```\ndeploy    ->  X:\\DEPLOY_TESTE\\<PROJETO>\\v<VERSAO>\\\n```\n", encoding="utf-8")
    assert mp.raiz_de_entrega(projeto, ambiente={}) == Path("X:\\DEPLOY_TESTE")
    assert mp.raiz_de_entrega(projeto, ambiente={mp.VARIAVEL_ENTREGA: str(tmp_path)}) == tmp_path
    with pytest.raises(mp.MontagemRecusada, match=mp.VARIAVEL_ENTREGA):
        mp.raiz_de_entrega(tmp_path / "sem_marca", ambiente={})


@pytest.mark.skipif(not shutil.which("makecab"), reason="sem makecab")
def test_extrai_cab_de_verdade(tmp_path):
    import subprocess
    origem = tmp_path / "origem"
    origem.mkdir()
    # Com um arquivo só, o expand trata o .cab como arquivo comprimido avulso;
    # o .cab do WebView2 tem centenas, então o teste usa dois.
    (origem / "msedgewebview2.exe").write_bytes(b"MZ webview2")
    (origem / "outro.dll").write_bytes(b"MZ outro")
    diretivas = tmp_path / "d.ddf"
    diretivas.write_text(
        f".Set CabinetNameTemplate=wv2.cab\n.Set DiskDirectoryTemplate={tmp_path}\n"
        f".Set DestinationDir=Microsoft.WebView2.FixedVersionRuntime.1.x64\n"
        f"\"{origem / 'msedgewebview2.exe'}\"\n\"{origem / 'outro.dll'}\"\n", encoding="ascii")
    subprocess.run(["makecab", "/F", str(diretivas)], cwd=tmp_path, check=True, capture_output=True)
    pasta = tmp_path / "extraido"
    mp.extrair_cab(tmp_path / "wv2.cab", pasta)
    assert list(pasta.rglob("msedgewebview2.exe"))


# ---------------------------------------------------------------- kit

def test_kit_leva_testar_na_raiz_e_nao_leva_instalador(tmp_path):
    c = _cenario(tmp_path)
    kit = montar_kit.montar_kit(c["fonte"], saida=tmp_path / "kit", catalogo=c["catalogo"],
                                terceiros=c["terceiros"], modelos=c["modelos"], vs_raizes=[],
                                extrair_cab=_extrair_cab_falso, dizer=lambda *_: None)
    assert kit == tmp_path / "kit" / "SCAN_TO_TEXT_KIT_v1.2.3.zip"
    with zipfile.ZipFile(kit) as z:
        nomes = z.namelist()
    assert {n.split("/")[0] for n in nomes} == {"TESTAR.cmd", "Scan-to-Text"}
    assert "Scan-to-Text/python/python.exe" in nomes
    assert "Scan-to-Text/LEIA-ME.txt" not in nomes


def _cmd_de_ambiente(caminho: Path) -> str:
    bruto = caminho.read_bytes()
    assert bruto.isascii(), caminho
    assert b"\n" not in bruto.replace(b"\r\n", b""), f"{caminho} precisa ser CRLF"
    assert not bruto.endswith(b"\r") and bruto.endswith(b"\r\n")
    texto = bruto.decode("ascii")
    assert "chcp" not in texto.lower()
    return texto


def _saidas_pausadas(texto: str) -> None:
    linhas = [l.strip().lower() for l in texto.splitlines()]
    for i, linha in enumerate(linhas):
        if linha.startswith("exit"):
            anteriores = [l for l in linhas[:i] if l and not l.startswith("rem")]
            assert anteriores and anteriores[-1] == "pause", f"saída sem pause na linha {i + 1}"


def test_testar_cmd_segue_a_regra_de_ambiente():
    texto = _cmd_de_ambiente(RAIZ / "diagnostico" / "TESTAR.cmd")
    _saidas_pausadas(texto)
    assert "preflight" in texto and "autoteste" in texto
    assert "resultados" in texto and "%COMPUTERNAME%" in texto
    assert "powershell" not in texto.lower()
