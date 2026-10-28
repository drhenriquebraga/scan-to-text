"""Baixa os componentes de terceiros do catálogo e confere o SHA-256.

Tudo vai para `_montagem/terceiros/` (gitignorado). Arquivo que já está lá e
confere não é baixado de novo; arquivo que não confere é baixado outra vez.
O download vai para `<arquivo>.baixando` e só vira o nome final depois do
hash conferido: um arquivo com o nome final é sempre um arquivo bom.

Uso, na máquina de desenvolvimento:
  python packager/baixar.py              baixa tudo o que o catálogo lista
  python packager/baixar.py --travar     refaz a seção `rodas` do catálogo

As duas dependências que só existem como código-fonte (`proxy_tools` e
`antlr4-python3-runtime`) entram como roda local: o código-fonte é baixado e
conferido, e a roda é gerada aqui (`pip wheel`, sem rede). Sem elas no
`--find-links`, a regra de só aceitar roda pronta faz o pip voltar em silêncio
a versões antigas do pywebview e do omegaconf.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CATALOGO = Path(__file__).with_name("catalogo.json")
TERCEIROS = RAIZ / "_montagem" / "terceiros"
PASTA_RODAS = Path("rodas")
PASTA_LOCAIS = Path("rodas_locais")
PLATAFORMA = "win_amd64"
VERSAO_PYTHON = "3.14"
BLOCO = 1 << 20


class HashDivergente(Exception):
    pass


def nome_canonico(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def sha256_de(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(BLOCO), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_catalogo(caminho: Path = CATALOGO) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def itens(catalogo: dict) -> list[tuple[dict, Path]]:
    """(entrada, subpasta de `terceiros`) de tudo o que se baixa."""
    saida = [(c, Path()) for c in catalogo.get("componentes", [])]
    saida += [(r, PASTA_RODAS) for r in catalogo.get("rodas", [])]
    saida += [(r, PASTA_LOCAIS) for r in catalogo.get("rodas_locais", [])]
    return saida


def baixar_item(item: dict, pasta: Path, abrir=urllib.request.urlopen) -> Path:
    destino = pasta / item["arquivo"]
    if destino.is_file() and sha256_de(destino) == item["sha256"]:
        return destino
    pasta.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".baixando")
    h = hashlib.sha256()
    try:
        with abrir(item["url"], timeout=120) as resposta, open(parcial, "wb") as saida:
            for bloco in iter(lambda: resposta.read(BLOCO), b""):
                h.update(bloco)
                saida.write(bloco)
        if h.hexdigest() != item["sha256"]:
            raise HashDivergente(f"{item['arquivo']}: esperado {item['sha256']}, veio {h.hexdigest()}")
        parcial.replace(destino)
    finally:
        parcial.unlink(missing_ok=True)
    return destino


def construir_rodas_locais(catalogo: dict, terceiros: Path = TERCEIROS,
                           executar=subprocess.run) -> list[Path]:
    """Gera a roda de cada código-fonte de `rodas_locais`, já baixado e conferido."""
    pasta = terceiros / PASTA_LOCAIS
    rodas = []
    for r in catalogo.get("rodas_locais", []):
        fonte = pasta / r["arquivo"]
        if sha256_de(fonte) != r["sha256"]:
            raise HashDivergente(r["arquivo"])
        existentes = roda_local(pasta, r["nome"], r["versao"])
        if existentes is None:
            executar([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                      "--no-index", "--quiet", "-w", str(pasta), str(fonte)], check=True)
            existentes = roda_local(pasta, r["nome"], r["versao"])
        if existentes is None:
            raise FileNotFoundError(f"roda de {r['nome']} {r['versao']}")
        rodas.append(existentes)
    return rodas


def roda_local(pasta: Path, nome: str, versao: str) -> Path | None:
    prefixo = re.sub(r"[-_.]+", "_", nome).lower() + "-" + versao + "-"
    for roda in sorted(pasta.glob("*.whl")):
        if roda.name.lower().startswith(prefixo):
            return roda
    return None


def travar_rodas(requisitos: Path, locais: Path, executar=subprocess.run) -> list[dict]:
    """Resolve o requirements para Windows x64 e Python 3.14, sem instalar nada.

    O relatório do pip dá versão, URL e SHA-256 de cada roda; as rodas locais
    (vindas do `--find-links`) ficam de fora, porque já estão em `rodas_locais`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        relatorio = Path(tmp) / "relatorio.json"
        executar([sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                  "--quiet", "--report", str(relatorio), "--only-binary=:all:",
                  "--platform", PLATAFORMA, "--python-version", VERSAO_PYTHON,
                  "--target", str(Path(tmp) / "alvo"), "--find-links", str(locais),
                  "-r", str(requisitos)], check=True)
        dados = json.loads(relatorio.read_text(encoding="utf-8"))
    rodas = []
    for item in dados["install"]:
        url = item["download_info"]["url"]
        if not url.startswith("https://"):
            continue
        arquivo = item["download_info"].get("archive_info", {})
        sha = arquivo.get("hashes", {}).get("sha256") or arquivo.get("hash", "").partition("=")[2]
        rodas.append({"nome": nome_canonico(item["metadata"]["name"]),
                      "versao": item["metadata"]["version"],
                      "arquivo": url.rsplit("/", 1)[-1], "url": url, "sha256": sha})
    return sorted(rodas, key=lambda r: r["nome"])


def baixar(catalogo: dict, terceiros: Path = TERCEIROS, abrir=urllib.request.urlopen,
           dizer=print) -> list[Path]:
    feitos = []
    for item, sub in itens(catalogo):
        feitos.append(baixar_item(item, terceiros / sub, abrir=abrir))
        dizer(f"  ok  {sub.as_posix() + '/' if sub.parts else ''}{item['arquivo']}")
    return feitos


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    catalogo = ler_catalogo()
    if "--travar" in argv:
        for r in catalogo.get("rodas_locais", []):
            baixar_item(r, TERCEIROS / PASTA_LOCAIS)
        construir_rodas_locais(catalogo)
        catalogo["rodas"] = travar_rodas(RAIZ / "requirements.txt", TERCEIROS / PASTA_LOCAIS)
        CATALOGO.write_text(json.dumps(catalogo, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        print(f"rodas: {len(catalogo['rodas'])} fixadas em {CATALOGO.name}")
        return 0
    try:
        baixar(catalogo)
        construir_rodas_locais(catalogo)
    except HashDivergente as erro:
        print(f"PAROU: hash divergente: {erro}")
        return 1
    print("baixar: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
