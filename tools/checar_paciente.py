"""Varredura do stage: recusa o commit que leva dado de paciente.

Roda sobre o CONTEÚDO DO STAGE, e não o do disco: é o stage que vai para o
commit. Recusa documento (por extensão, pela assinatura dos primeiros bytes ou
por imagem embutida em base64) fora de `tests/fixtures/`, e qualquer saída do
app (`COMPLETO.md`, `manifesto.json`, pasta `_scan-to-text`, log). O laudo
nunca imprime o caminho, porque nome de pasta pode ser nome de paciente: sai
um hash curto.
Na dúvida, recusa: sem conseguir ler o stage, o commit não passa.

Adaptado de `tools/check_project.py` (conteudo_do_stage e main_do_stage) do
local-suite, Apache-2.0.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PASTAS_SINTETICAS = ("tests/fixtures/",)
EXTENSOES_DOCUMENTO = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
                       ".gif", ".webp", ".heic", ".heif", ".docx", ".doc", ".rtf",
                       ".zip", ".svg"}
ASSINATURAS = (b"%PDF-", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00",
               b"MM\x00*", b"PK\x03\x04", b"GIF8")
# Página escaneada colada num texto (SVG, Markdown, HTML) vai como data URI.
IMAGEM_EMBUTIDA = re.compile(rb"data:image/(?:png|jpe?g|tiff?|bmp|gif|webp|heic|heif)\s*;\s*base64",
                             re.IGNORECASE)
NOMES_DE_SAIDA = {"COMPLETO.md": "completo", "manifesto.json": "manifesto",
                  "trabalhando.json": "saida"}
# Imagens do README: passam só as listadas, pelo SHA-256 do conteúdo, e só em
# `img/`. Imagem nova exige editar a lista, e a edição aparece no diff. Na
# oficina elas moram em `publico/`; na cópia pública, na raiz.
LISTA_DE_IMAGENS = "img/IMAGENS.sha256"
PREFIXO_PUBLICO = "publico/"
_IMAGEM_LISTAVEL = {".png", ".jpg", ".jpeg", ".svg"}
# SVG é texto: o checkout com `core.autocrlf` o entrega em CRLF, e o hash da
# lista é o do conteúdo em LF, que é o do blob.
_IMAGEM_TEXTO = {".svg"}


def _publico(caminho: str) -> str:
    return caminho[len(PREFIXO_PUBLICO):] if caminho.startswith(PREFIXO_PUBLICO) else caminho


def imagens_liberadas(texto: str) -> frozenset[tuple[str, str]]:
    """Pares (caminho, sha256) de `<sha256>  img/<nome>`, uma linha cada."""
    pares = set()
    for linha in texto.splitlines():
        partes = linha.split()
        if len(partes) == 2 and len(partes[0]) == 64:
            pares.add((partes[1], partes[0].lower()))
    return frozenset(pares)


def _liberada(caminho: str, conteudo: bytes | None, liberadas) -> bool:
    rel = _publico(caminho)
    sufixo = PurePosixPath(rel).suffix.lower()
    if conteudo is None or not rel.startswith("img/") or sufixo not in _IMAGEM_LISTAVEL:
        return False
    if sufixo in _IMAGEM_TEXTO:
        conteudo = conteudo.replace(b"\r\n", b"\n")
    return (rel, hashlib.sha256(conteudo).hexdigest()) in liberadas


def categoria(caminho: str, conteudo: bytes | None,
              liberadas: frozenset[tuple[str, str]] = frozenset()) -> str | None:
    p = PurePosixPath(caminho)
    if p.name in NOMES_DE_SAIDA:
        return NOMES_DE_SAIDA[p.name]
    if "_scan-to-text" in p.parts:
        return "saida"
    if p.suffix.lower() == ".log":
        return "log"
    if caminho.startswith(PASTAS_SINTETICAS) or _liberada(caminho, conteudo, liberadas):
        return None
    if p.suffix.lower() in EXTENSOES_DOCUMENTO:
        return "documento"
    if conteudo and (conteudo.startswith(ASSINATURAS) or conteudo[4:8] == b"ftyp"):
        return "documento"
    if conteudo and IMAGEM_EMBUTIDA.search(conteudo):
        return "documento"
    return None


def varrer(itens: list[tuple[str, bytes | None]],
           liberadas: frozenset[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Achados (caminho, tipo). Sem `liberadas`, a lista de imagens vem dos próprios itens."""
    if liberadas is None:
        liberadas = frozenset().union(*(imagens_liberadas(c.decode("utf-8", "replace"))
                                        for n, c in itens
                                        if c is not None and _publico(n) == LISTA_DE_IMAGENS))
    achados = []
    for caminho, conteudo in itens:
        tipo = categoria(caminho, conteudo, liberadas)
        if tipo:
            achados.append((caminho, tipo))
    return achados


def laudo(achados: list[tuple[str, str]]) -> list[str]:
    return [f"<#{hashlib.sha1(c.encode('utf-8')).hexdigest()[:8]}> [{t}]"
            for c, t in achados]


def _git(args: list[str], raiz: Path | None, entrada: bytes | None = None):
    r = subprocess.run(["git", *args], cwd=raiz, input=entrada, capture_output=True)
    return r.returncode, r.stdout


def conteudo_do_stage(raiz: Path | None = None) -> list[tuple[str, bytes | None]] | None:
    cod, saida = _git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT"], raiz)
    if cod != 0:
        return None
    nomes = [n.decode("utf-8") for n in saida.split(b"\0") if n]
    if not nomes:
        return []
    entrada = b"".join(f":{n}\n".encode("utf-8") for n in nomes)
    cod, saida = _git(["cat-file", "--batch"], raiz, entrada)
    if cod != 0:
        return None
    itens, pos = [], 0
    for nome in nomes:
        fim = saida.index(b"\n", pos)
        cabecalho = saida[pos:fim].split()
        pos = fim + 1
        if len(cabecalho) < 3 or cabecalho[-1] == b"missing":
            itens.append((nome, None))
            continue
        tamanho = int(cabecalho[2])
        itens.append((nome, saida[pos:pos + tamanho]))
        pos += tamanho + 1
    return itens


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--stage" not in argv:
        print("uso: checar_paciente.py --stage")
        return 2
    itens = conteudo_do_stage()
    if itens is None:
        print("pre-commit: nao consegui ler o stage do git. Commit RECUSADO.")
        return 1
    liberadas = None
    if not any(_publico(n) == LISTA_DE_IMAGENS for n, _ in itens):
        # Lista fora do stage: vale a do índice, que é a do próximo commit.
        cod, lista = _git(["show", ":" + PREFIXO_PUBLICO + LISTA_DE_IMAGENS], None)
        liberadas = imagens_liberadas(lista.decode("utf-8", "replace")) if cod == 0 else frozenset()
    achados = varrer(itens, liberadas)
    if not achados:
        print(f"pre-commit: {len(itens)} arquivo(s) no stage, nenhum parece dado de paciente.")
        return 0
    print(f"pre-commit: COMMIT RECUSADO. {len(achados)} achado(s) que parecem dado de paciente:")
    for linha in laudo(achados):
        print("   " + linha)
    print("  Localize com: git diff --cached --name-only")
    print("  Nao use --no-verify para passar isto: o que entra num commit fica no historico.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
