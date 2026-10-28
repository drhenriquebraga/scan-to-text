"""Monta o pacote único do Scan-to-Text e entrega o zip.

A fonte é SEMPRE a cópia pública (`publicar.py`), nunca a oficina: o zip leva
código legível, e o que sai da oficina sem passar pela publicação leva junto o
que não é público. A cópia só é aceita com o selo da varredura conferido: cópia
interrompida, mexida depois da varredura ou pasta qualquer com `pyproject.toml`
não servem.

    _montagem/pacote/Scan-to-Text/
      python/        Python embutido, `._pth` com `..\\app` e `Lib\\site-packages`,
                     as rodas do catálogo e o runtime Visual C++ (se houver)
      app/           scan_to_text/ e packs/
      modelos/       os modelos do catálogo, conferidos pelo hash
      webview2/      WebView2 Fixed Version (onde fica o msedgewebview2.exe)
      AVISOS-DE-TERCEIROS.txt, LEIA-ME.txt, entrega.txt

O zip tem na raiz só o `INSTALL.cmd` e a pasta `Scan-to-Text`, e sai
determinístico (ordem fixa e data fixa): a mesma entrada dá os mesmos bytes, e o
`SHA256SUMS` serve de prova. Vai para `<raiz de entrega>/SCAN_TO_TEXT/v<versão>/`
com dois nomes: `SCAN_TO_TEXT_v<versão>.zip` e a cópia idêntica
`SCAN_TO_TEXT_LATEST.zip`, cujo nome não muda entre versões, para o link
`releases/latest/download/SCAN_TO_TEXT_LATEST.zip` valer sempre.

O runtime Visual C++ só sai de `VC\\Redist` de um Visual Studio instalado nesta
máquina (a licença das cópias do Windows e do `vc_redist` proíbe distribuir).
Sem Visual Studio, a montagem relata `vcpp_ausente` e segue sem ele.
"""
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(RAIZ / "tools"))

import baixar  # noqa: E402
import publicar  # noqa: E402

NOME = "Scan-to-Text"
PROJETO = "SCAN_TO_TEXT"
NOME_LATEST = f"{PROJETO}_LATEST.zip"
MONTAGEM = RAIZ / "_montagem"
MODELOS = MONTAGEM / "modelos"
VARIAVEL_ENTREGA = "RADAI_RAIZ_DEPLOY"
POLITICA = "ENTREGA.md"
LIMITE_GITHUB = 2 * 1024 ** 3
CRT_MINIMO = (14, 44)
DATA_FIXA = (1980, 1, 1, 0, 0, 0)
JA_COMPRIMIDOS = {".zip", ".whl", ".onnx", ".safetensors", ".pak", ".cab", ".png", ".jpg",
                  ".gz", ".zst", ".7z", ".dat", ".bin"}
FONTE_DO_APP = ("app/scan_to_text", "app/packs")
IGNORAR = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
_LINHA_DEPLOY = re.compile(r"^\s*deploy\s*->\s*(.+?)[\\/]<PROJETO>", re.I | re.M)

AVISO_WEBVIEW2 = """\
MICROSOFT EDGE WEBVIEW2 RUNTIME, FIXED VERSION {versao} (x64)
  Baixado direto da Microsoft e distribuido sem modificacao, so junto com o
  Scan-to-Text, sob os termos de licenca da Microsoft para o WebView2 Runtime:
  https://developer.microsoft.com/microsoft-edge/webview2/
  O runtime nao se atualiza sozinho: atualiza-se com o release do Scan-to-Text.
  Licencas dos componentes de terceiros do proprio runtime:
  webview2\\show_third_party_software_licenses.bat

  H.264/AVC. O WebView2 contem tecnologia de codec AVC. Aviso exigido pela
  MPEG LA, L.L.C.:
  THIS PRODUCT IS LICENSED UNDER THE AVC PATENT PORTFOLIO LICENSE FOR THE
  PERSONAL AND NON-COMMERCIAL USE OF A CONSUMER TO (i) ENCODE VIDEO IN
  COMPLIANCE WITH THE AVC STANDARD ("AVC VIDEO") AND/OR (ii) DECODE AVC VIDEO
  THAT WAS ENCODED BY A CONSUMER ENGAGED IN A PERSONAL AND NON-COMMERCIAL
  ACTIVITY AND/OR WAS OBTAINED FROM A VIDEO PROVIDER LICENSED TO PROVIDE AVC
  VIDEO. NO LICENSE IS GRANTED OR SHALL BE IMPLIED FOR ANY OTHER USE.
  ADDITIONAL INFORMATION MAY BE OBTAINED FROM MPEG LA, L.L.C.
  SEE HTTPS://WWW.MPEGLA.COM
"""
AVISO_VCPP = """\
MICROSOFT VISUAL C++ RUNTIME {versao} (x64)
  Arquivos: {arquivos}
  "Distributable Code" do Microsoft Visual Studio, copiado da pasta VC\\Redist
  de uma instalacao do Visual Studio, sem modificacao, e distribuido so junto
  com o Scan-to-Text, sob os termos de licenca do Visual Studio. A copia na
  pasta do app nao recebe Windows Update: atualiza-se com o release.
"""


class MontagemRecusada(Exception):
    pass


# ---------------------------------------------------------------- fonte e versão

def exigir_copia_publica(fonte: Path, oficina: Path = RAIZ) -> None:
    fonte, oficina = fonte.resolve(), oficina.resolve()
    if fonte == oficina or oficina in fonte.parents:
        raise MontagemRecusada("a fonte do pacote e a copia publica, nunca a oficina")
    if not (fonte / "pyproject.toml").is_file() or not publicar.varredura_confere(fonte):
        raise MontagemRecusada(f"{fonte} nao e uma copia publica varrida e intacta: "
                               "rode packager/publicar.py")


def versao_do_projeto(fonte: Path) -> str:
    with open(fonte / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def exigir_cmd(caminho: Path) -> None:
    """Regra de ambiente: `.cmd` em ASCII puro, CRLF e sem `chcp`."""
    bruto = caminho.read_bytes()
    if (not bruto.isascii() or b"\n" in bruto.replace(b"\r\n", b"")
            or b"chcp" in bruto.lower()):
        raise MontagemRecusada(f"{caminho.name}: precisa ser ASCII, CRLF e sem chcp")


def texto_ascii_crlf(caminho: Path) -> bytes:
    """O texto em CRLF, que o checkout pode ter deixado em LF.

    O Bloco de Notas do LTSB 2016 não quebra linha em LF.
    """
    bruto = caminho.read_bytes()
    if not bruto.isascii():
        raise MontagemRecusada(f"{caminho.name}: precisa ser ASCII")
    return bruto.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


# ---------------------------------------------------------------- entrega

def raiz_de_entrega(de: Path, ambiente=os.environ) -> Path:
    """Variável `RADAI_RAIZ_DEPLOY`, ou a linha `deploy ->` da política do ecossistema.

    Porte simplificado de `build/destino_de_entrega.py` do local-suite
    (Apache-2.0). Sem nenhum dos dois, para: adivinhar a raiz cria a pasta
    errada e segue sem erro.
    """
    variavel = (ambiente.get(VARIAVEL_ENTREGA) or "").strip().strip('"')
    if variavel:
        return Path(variavel)
    for pasta in (de.resolve(), *de.resolve().parents):
        politica = pasta / POLITICA
        if politica.is_file():
            achado = _LINHA_DEPLOY.search(politica.read_text(encoding="utf-8-sig", errors="replace"))
            if achado:
                return Path(achado.group(1).strip())
    raise MontagemRecusada(f"sem raiz de entrega: defina {VARIAVEL_ENTREGA}")


def entregar(zip_: Path, raiz: Path, versao: str, substituir: bool = False) -> Path:
    """Copia o zip para `v<versão>/` com o nome versionado e o `_LATEST`, e grava o SHA256SUMS."""
    pasta = raiz / PROJETO / f"v{versao}"
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / zip_.name
    soma = baixar.sha256_de(zip_)
    if alvo.is_file() and not substituir and baixar.sha256_de(alvo) != soma:
        raise MontagemRecusada(f"v{versao} ja entregue com outro conteudo: suba a versao")
    latest = pasta / NOME_LATEST
    shutil.copyfile(zip_, alvo)
    shutil.copyfile(zip_, latest)
    (pasta / "SHA256SUMS").write_text(f"{soma} *{alvo.name}\n{soma} *{latest.name}\n",
                                      encoding="ascii", newline="\n")
    return alvo


# ---------------------------------------------------------------- terceiros

def _conferido(pasta: Path, item: dict) -> Path:
    arquivo = pasta / item["arquivo"]
    if not arquivo.is_file():
        raise MontagemRecusada(f"falta {item['arquivo']}: rode packager/baixar.py")
    if baixar.sha256_de(arquivo) != item["sha256"]:
        raise MontagemRecusada(f"hash divergente: {item['arquivo']}")
    return arquivo


def _componente(catalogo: dict, id_: str) -> dict:
    for c in catalogo["componentes"]:
        if c["id"] == id_:
            return c
    raise MontagemRecusada(f"catalogo sem {id_}")


def raizes_visual_studio(ambiente=os.environ) -> list[Path]:
    return [Path(ambiente[v]) / "Microsoft Visual Studio"
            for v in ("ProgramFiles", "ProgramFiles(x86)") if ambiente.get(v)]


def _versao_crt(pasta: Path) -> tuple[int, ...]:
    return tuple(int(p) for p in pasta.name.split(".") if p.isdigit())


def achar_crt(raizes) -> Path | None:
    """A pasta `Microsoft.VC14*.CRT` x64 do redist mais novo, a partir de 14.44."""
    candidatas = []
    for raiz in raizes:
        for crt in Path(raiz).glob("*/*/VC/Redist/MSVC/*/x64/Microsoft.VC14*.CRT"):
            versao = _versao_crt(crt.parents[1])
            if versao[:2] >= CRT_MINIMO and (crt / "msvcp140.dll").is_file():
                candidatas.append((versao, crt))
    return max(candidatas)[1] if candidatas else None


def instalar_roda(roda: Path, site_packages: Path) -> None:
    """Desempacota a roda sem pip: nada de script com caminho desta máquina."""
    with zipfile.ZipFile(roda) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            partes = PurePosixPath(info.filename).parts
            if partes[0].endswith(".data"):
                if len(partes) < 3 or partes[1] not in ("purelib", "platlib"):
                    continue
                partes = partes[2:]
            if ".." in partes or PurePosixPath(*partes).is_absolute():
                raise MontagemRecusada(f"{roda.name}: caminho fora da roda")
            alvo = site_packages.joinpath(*partes)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as origem, open(alvo, "wb") as destino:
                shutil.copyfileobj(origem, destino)


def extrair_cab(cab: Path, pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    expand = Path(os.environ["SystemRoot"]) / "System32" / "expand.exe"
    subprocess.run([str(expand), str(cab), "-F:*", str(pasta)], check=True,
                   capture_output=True)


def _conteudo_pth(pth_original: Path) -> str:
    primeira = pth_original.read_text(encoding="utf-8-sig").splitlines()[0].strip()
    return "\r\n".join((primeira, ".", "Lib\\site-packages", "..\\app", "import site", ""))


# ---------------------------------------------------------------- partes da árvore

def _python(destino: Path, catalogo: dict, terceiros: Path) -> Path:
    python = destino / "python"
    with zipfile.ZipFile(_conferido(terceiros, _componente(catalogo, "python"))) as z:
        z.extractall(python)
    pths = list(python.glob("python3*._pth"))
    if len(pths) != 1:
        raise MontagemRecusada("o Python embutido precisa ter um ._pth")
    pths[0].write_bytes(_conteudo_pth(pths[0]).encode("ascii"))
    site = python / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for r in catalogo.get("rodas", []):
        instalar_roda(_conferido(terceiros / baixar.PASTA_RODAS, r), site)
    locais = terceiros / baixar.PASTA_LOCAIS
    for r in catalogo.get("rodas_locais", []):
        _conferido(locais, r)
        roda = baixar.roda_local(locais, r["nome"], r["versao"])
        if roda is None:
            raise MontagemRecusada(f"falta a roda local de {r['nome']}: rode packager/baixar.py")
        instalar_roda(roda, site)
    import checar_licencas
    problemas = checar_licencas.verificar([str(site)])
    if problemas:
        raise MontagemRecusada("licenca fora da lista: " + "; ".join(problemas))
    return python


def _app(fonte: Path, destino: Path) -> None:
    for relativo in FONTE_DO_APP:
        origem = fonte / relativo
        if not origem.is_dir():
            raise MontagemRecusada(f"a fonte nao tem {relativo}")
        shutil.copytree(origem, destino / relativo, ignore=IGNORAR)


def _modelos(destino: Path, catalogo: dict, modelos: Path) -> None:
    for m in catalogo.get("modelos", []):
        origem = modelos / m["caminho"]
        if not origem.is_file() or baixar.sha256_de(origem) != m["sha256"]:
            raise MontagemRecusada(f"modelo ausente ou divergente: {m['caminho']}")
        alvo = destino / "modelos" / m["caminho"]
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origem, alvo)


def _webview2(destino: Path, catalogo: dict, terceiros: Path, extrair) -> None:
    cab = _conferido(terceiros, _componente(catalogo, "webview2"))
    temporaria = destino / "_webview2_extraido"
    extrair(cab, temporaria)
    exes = sorted(temporaria.rglob("msedgewebview2.exe"))
    if not exes:
        raise MontagemRecusada("o .cab do WebView2 nao tem msedgewebview2.exe")
    shutil.move(str(exes[0].parent), str(destino / "webview2"))
    shutil.rmtree(temporaria)


def _vcpp(destino: Path, vs_raizes) -> list[str]:
    crt = achar_crt(vs_raizes)
    if crt is None:
        return []
    dlls = sorted(crt.glob("*.dll"))
    for dll in dlls:
        shutil.copyfile(dll, destino / "python" / dll.name)
    return [crt.parents[1].name] + [d.name for d in dlls]


def avisos_de_terceiros(catalogo: dict, versao: str, vcpp: list[str]) -> str:
    python = _componente(catalogo, "python")
    webview2 = _componente(catalogo, "webview2")
    partes = [f"Scan-to-Text {versao} - avisos de terceiros\n\n"
              "Este pacote leva componentes de terceiros, cada um sob a propria licenca.\n"
              "As licencas das bibliotecas Python estao nas pastas *.dist-info de\n"
              "python\\Lib\\site-packages.\n",
              f"PYTHON {python['versao']} (embutido, x64) - {python['licenca']}\n"
              "  Texto da licenca: python\\LICENSE.txt\n",
              AVISO_WEBVIEW2.format(versao=webview2["versao"])]
    if vcpp:
        partes.append(AVISO_VCPP.format(versao=vcpp[0], arquivos=", ".join(vcpp[1:])))
    linhas = ["MODELOS (pasta modelos)"]
    for m in catalogo.get("modelos", []):
        revisao = f" @ {m['revisao']}" if m.get("revisao") else ""
        linhas.append(f"  {m['caminho']} - {m['licenca']} - {m['origem']}{revisao}")
    partes.append("\n".join(linhas) + "\n")
    return "\n".join(partes)


def montar_arvore(fonte: Path, destino: Path, catalogo: dict, terceiros: Path, modelos: Path, *,
                  vs_raizes=None, extrair_cab=extrair_cab, leia_me: bool = True,
                  dizer=print) -> list[str]:
    """Monta a pasta `Scan-to-Text`. Devolve os avisos (códigos) do que ficou de fora."""
    versao = versao_do_projeto(fonte)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    avisos = []
    dizer("python e rodas")
    _python(destino, catalogo, terceiros)
    dizer("app")
    _app(fonte, destino)
    dizer("modelos")
    _modelos(destino, catalogo, modelos)
    dizer("webview2")
    _webview2(destino, catalogo, terceiros, extrair_cab)
    vcpp = _vcpp(destino, raizes_visual_studio() if vs_raizes is None else vs_raizes)
    if not vcpp:
        avisos.append("vcpp_ausente")
        dizer("AVISO: runtime Visual C++ ausente: sem Visual Studio com VC\\Redist 14.44+ "
              "nesta maquina; o pacote segue sem ele")
    (destino / "AVISOS-DE-TERCEIROS.txt").write_text(
        avisos_de_terceiros(catalogo, versao, vcpp), encoding="utf-8", newline="\r\n")
    if leia_me:
        (destino / "LEIA-ME.txt").write_bytes(texto_ascii_crlf(fonte / "packager" / "LEIA-ME.txt"))
    (destino / "entrega.txt").write_bytes(f"PROJETO={PROJETO}\r\nVERSAO={versao}\r\n".encode("ascii"))
    return avisos


# ---------------------------------------------------------------- zip

def gerar_zip(pasta_app: Path, na_raiz: Path, zip_: Path) -> Path:
    """Zip determinístico: `na_raiz` e a pasta do app, em ordem e com data fixas."""
    exigir_cmd(na_raiz)
    zip_.parent.mkdir(parents=True, exist_ok=True)
    parcial = zip_.with_name(zip_.name + ".parcial")
    entradas = [(na_raiz, na_raiz.name)]
    for arquivo in sorted(p for p in pasta_app.rglob("*") if p.is_file()):
        entradas.append((arquivo, f"{pasta_app.name}/{arquivo.relative_to(pasta_app).as_posix()}"))
    try:
        with zipfile.ZipFile(parcial, "w", allowZip64=True) as z:
            for arquivo, nome in entradas:
                info = zipfile.ZipInfo(nome, DATA_FIXA)
                info.external_attr = 0o644 << 16
                info.compress_type = (zipfile.ZIP_STORED if arquivo.suffix.lower() in JA_COMPRIMIDOS
                                      else zipfile.ZIP_DEFLATED)
                with open(arquivo, "rb") as origem, z.open(info, "w", force_zip64=True) as destino:
                    shutil.copyfileobj(origem, destino, 1 << 20)
        if parcial.stat().st_size >= LIMITE_GITHUB:
            raise MontagemRecusada("o zip passou de 2 GiB, o limite do GitHub por arquivo")
        parcial.replace(zip_)
    finally:
        parcial.unlink(missing_ok=True)
    return zip_


def montar(fonte: Path, *, catalogo: dict | None = None, terceiros: Path = baixar.TERCEIROS,
           modelos: Path = MODELOS, montagem: Path = MONTAGEM, vs_raizes=None,
           extrair_cab=extrair_cab, substituir: bool = False, entregar_em: Path | None = None,
           dizer=print) -> Path:
    exigir_copia_publica(fonte)
    versao = versao_do_projeto(fonte)
    raiz = entregar_em or raiz_de_entrega(RAIZ)
    pasta_app = montagem / "pacote" / NOME
    avisos = montar_arvore(fonte, pasta_app, catalogo or baixar.ler_catalogo(), terceiros, modelos,
                           vs_raizes=vs_raizes, extrair_cab=extrair_cab, dizer=dizer)
    zip_ = gerar_zip(pasta_app, fonte / "packager" / "INSTALL.cmd",
                     montagem / "pacote" / f"{PROJETO}_v{versao}.zip")
    entregue = entregar(zip_, raiz, versao, substituir=substituir)
    dizer(f"pacote: {entregue} ({entregue.stat().st_size / 2**30:.2f} GiB)")
    for aviso in avisos:
        dizer(f"  aviso: {aviso}")
    return entregue


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    fonte = Path(argv[argv.index("--fonte") + 1]) if "--fonte" in argv else publicar.destino_padrao()
    try:
        montar(fonte, substituir="--substituir" in argv)
    except MontagemRecusada as erro:
        print(f"PAROU: {erro}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
