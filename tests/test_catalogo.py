import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "packager"))

import baixar  # noqa: E402

CATALOGO = RAIZ / "packager" / "catalogo.json"
SECOES = ("componentes", "rodas", "rodas_locais", "modelos")
PROIBIDOS = {"pymupdf", "pymupdf4llm", "fitz", "ghostscript", "marker-pdf", "unidecode",
             "pillow-heif", "onnxruntime-gpu", "dateparser"}


def _catalogo() -> dict:
    return json.loads(CATALOGO.read_text(encoding="utf-8"))


def _nome(texto: str) -> str:
    return re.sub(r"[-_.]+", "-", texto).lower()


# ---------------------------------------------------------------- catálogo

def test_nenhuma_entrada_sem_sha256():
    cat = _catalogo()
    for secao in SECOES:
        assert cat[secao], f"seção vazia: {secao}"
        for entrada in cat[secao]:
            assert re.fullmatch(r"[0-9a-f]{64}", entrada.get("sha256", "")), (secao, entrada)


def test_urls_sao_https_e_arquivos_nao_se_repetem():
    cat = _catalogo()
    vistos = set()
    for secao in ("componentes", "rodas", "rodas_locais"):
        for entrada in cat[secao]:
            assert entrada["url"].startswith("https://"), entrada
            chave = (secao, entrada["arquivo"])
            assert chave not in vistos, chave
            vistos.add(chave)


def test_python_embutido_e_webview2_fixados():
    por_id = {c["id"]: c for c in _catalogo()["componentes"]}
    python = por_id["python"]
    assert python["versao"] == "3.14.7"
    assert python["url"].endswith("/python-3.14.7-embed-amd64.zip")
    assert python["url"].startswith("https://www.python.org/")
    webview2 = por_id["webview2"]
    assert webview2["arquivo"].startswith("Microsoft.WebView2.FixedVersionRuntime.")
    assert webview2["arquivo"].endswith(".x64.cab")
    assert not python.get("opcional") and not webview2.get("opcional")


def test_rodas_cobrem_cada_pino_do_requirements():
    cat = _catalogo()
    fixadas = {(_nome(r["nome"]), r["versao"]) for r in cat["rodas"] + cat["rodas_locais"]}
    for linha in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip()
        if not linha:
            continue
        nome, versao = linha.split("==")
        assert (_nome(nome.split("[")[0]), versao.strip()) in fixadas, linha


def test_rodas_locais_sao_so_as_duas_excecoes_em_codigo_fonte():
    locais = {_nome(r["nome"]) for r in _catalogo()["rodas_locais"]}
    assert locais == {"proxy-tools", "antlr4-python3-runtime"}
    for r in _catalogo()["rodas_locais"]:
        assert r["arquivo"].endswith(".tar.gz")


def test_rodas_sao_para_windows_x64_e_python_314():
    for r in _catalogo()["rodas"]:
        arquivo = r["arquivo"]
        assert arquivo.endswith(".whl"), arquivo
        tags = arquivo[:-4].split("-")[-3:]
        # Tag comprimida ("py2.py3", "cp310.cp311", "win32.win_amd64") vale por qualquer parte.
        pythons, abis, plataformas = (set(t.split(".")) for t in tags)
        assert plataformas & {"any", "win_amd64"}, arquivo
        assert any(p.startswith(("py3", "cp3")) for p in pythons), arquivo
        assert abis & {"none", "abi3"} or any(a.startswith("cp314") for a in abis), arquivo


def test_nada_proibido_no_catalogo():
    nomes = {_nome(r["nome"]) for r in _catalogo()["rodas"] + _catalogo()["rodas_locais"]}
    assert not nomes & PROIBIDOS


def test_versoes_das_rodas_sao_unicas():
    nomes = [_nome(r["nome"]) for r in _catalogo()["rodas"] + _catalogo()["rodas_locais"]]
    assert len(nomes) == len(set(nomes))


def test_componentes_sao_so_python_e_webview2():
    # A orientação da página ficou com o RapidOCR: o Tesseract não vai no pacote.
    componentes = _catalogo()["componentes"]
    assert [c["id"] for c in componentes] == ["python", "webview2"]
    texto = CATALOGO.read_text(encoding="utf-8").lower()
    assert "tesseract" not in texto and "tessdata" not in texto


def test_modelos_do_catalogo_existem_na_montagem():
    pasta = RAIZ / "_montagem" / "modelos"
    if not pasta.is_dir():
        pytest.skip("sem _montagem/modelos nesta máquina")
    for m in _catalogo()["modelos"]:
        arquivo = pasta / m["caminho"]
        assert arquivo.is_file(), m["caminho"]
        assert arquivo.stat().st_size == m["tamanho"], m["caminho"]
        if m["tamanho"] < 5_000_000:
            assert hashlib.sha256(arquivo.read_bytes()).hexdigest() == m["sha256"], m["caminho"]


def test_modelos_nao_levam_cache_do_hugging_face():
    for m in _catalogo()["modelos"]:
        assert ".cache" not in Path(m["caminho"]).parts


# ---------------------------------------------------------------- baixar

def _fonte(tmp_path: Path, conteudo: bytes = b"conteudo de teste") -> tuple[str, str]:
    origem = tmp_path / "servidor" / "arquivo.bin"
    origem.parent.mkdir(parents=True, exist_ok=True)
    origem.write_bytes(conteudo)
    return origem.as_uri(), hashlib.sha256(conteudo).hexdigest()


class _Contador:
    def __init__(self):
        self.chamadas = 0

    def __call__(self, url, timeout=None):
        import urllib.request
        self.chamadas += 1
        return urllib.request.urlopen(url, timeout=timeout)


def test_baixa_confere_e_nao_baixa_de_novo(tmp_path):
    url, sha = _fonte(tmp_path)
    item = {"id": "x", "url": url, "arquivo": "arquivo.bin", "sha256": sha}
    abrir = _Contador()
    destino = baixar.baixar_item(item, tmp_path / "terceiros", abrir=abrir)
    assert destino.read_bytes() == b"conteudo de teste"
    assert abrir.chamadas == 1
    assert baixar.baixar_item(item, tmp_path / "terceiros", abrir=abrir) == destino
    assert abrir.chamadas == 1


def test_hash_divergente_nao_deixa_arquivo(tmp_path):
    url, _ = _fonte(tmp_path)
    item = {"id": "x", "url": url, "arquivo": "arquivo.bin", "sha256": "0" * 64}
    with pytest.raises(baixar.HashDivergente):
        baixar.baixar_item(item, tmp_path / "terceiros", abrir=_Contador())
    assert list((tmp_path / "terceiros").iterdir()) == []


def test_arquivo_local_corrompido_e_baixado_de_novo(tmp_path):
    url, sha = _fonte(tmp_path)
    (tmp_path / "terceiros").mkdir()
    (tmp_path / "terceiros" / "arquivo.bin").write_bytes(b"pela metade")
    item = {"id": "x", "url": url, "arquivo": "arquivo.bin", "sha256": sha}
    abrir = _Contador()
    destino = baixar.baixar_item(item, tmp_path / "terceiros", abrir=abrir)
    assert abrir.chamadas == 1
    assert baixar.sha256_de(destino) == sha


def test_itens_listam_componentes_e_rodas_com_a_subpasta():
    cat = {"componentes": [{"id": "a", "arquivo": "a", "url": "u", "sha256": "0" * 64}],
           "rodas": [{"nome": "r", "versao": "1", "arquivo": "r.whl", "url": "u", "sha256": "1" * 64}],
           "rodas_locais": [{"nome": "l", "versao": "1", "arquivo": "l.tar.gz", "url": "u",
                             "sha256": "2" * 64}],
           "modelos": []}
    assert [(i["arquivo"], p) for i, p in baixar.itens(cat)] == [
        ("a", Path()), ("r.whl", Path("rodas")), ("l.tar.gz", Path("rodas_locais"))]


def test_travar_rodas_le_o_relatorio_do_pip(tmp_path):
    relatorio = {"install": [
        {"metadata": {"name": "PyYAML", "version": "6.0.3"},
         "download_info": {"url": "https://files.pythonhosted.org/x/pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
                           "archive_info": {"hashes": {"sha256": "a" * 64}}}},
        {"metadata": {"name": "proxy_tools", "version": "0.1.0"},
         "download_info": {"url": "file:///c:/local/proxy_tools-0.1.0-py3-none-any.whl",
                           "archive_info": {}}},
    ]}

    def executar(cmd, check):
        assert "--only-binary=:all:" in cmd and "win_amd64" in cmd and "3.14" in cmd
        assert "--find-links" in cmd
        destino = Path(cmd[cmd.index("--report") + 1])
        destino.write_text(json.dumps(relatorio), encoding="utf-8")

    rodas = baixar.travar_rodas(tmp_path / "requirements.txt", tmp_path / "locais", executar=executar)
    assert rodas == [{"nome": "pyyaml", "versao": "6.0.3",
                      "arquivo": "pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
                      "url": "https://files.pythonhosted.org/x/pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
                      "sha256": "a" * 64}]
