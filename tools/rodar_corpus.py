"""Roda o fluxo inteiro, com o OCR de verdade, sobre o corpus sintético, para a régua.

Uso: python tools/rodar_corpus.py --destinos RAIZ [CENARIO ...] [--corpus PASTA] [--hoje AAAA-MM-DD]

Cada cenário (pasta com `gabarito.json` e `origem/`) grava em
`<RAIZ>/<nome do cenário>`, o lugar onde `tools/avaliar.py --destinos RAIZ`
procura. Sem CENARIO, roda todos. Um processo só: os modelos carregam uma vez.
Os arquivos de cada origem entram em ordem de nome, e a data de hoje é fixa
(`--hoje`), para duas rodadas da mesma entrada poderem ser comparadas byte a
byte.
"""
import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "app") not in sys.path:
    sys.path.insert(0, str(RAIZ / "app"))

from scan_to_text import log, modelos  # noqa: E402
from scan_to_text import pacote as pacotes  # noqa: E402
from scan_to_text.es import ES  # noqa: E402
from scan_to_text.processar import Contexto, processar  # noqa: E402


def cenarios(corpus: Path, nomes: list[str]) -> list[Path]:
    todos = sorted(p for p in corpus.iterdir() if (p / "gabarito.json").is_file())
    return [p for p in todos if not nomes or p.name in nomes]


def rodar(cenario: Path, raiz: Path, leitor, hoje: date) -> tuple[int, float]:
    gabarito = json.loads((cenario / "gabarito.json").read_text(encoding="utf-8"))
    origem = cenario / "origem"
    destino = raiz / cenario.name
    destino.mkdir(parents=True, exist_ok=True)
    ctx = Contexto(pacote=pacotes.carregar(gabarito.get("idioma") or "pt-BR"), leitor=leitor,
                   es=ES([origem]), destinos=[destino], estacao="CORPUS", pid=os.getpid(),
                   hoje=hoje, pasta_temp=raiz / "_temp")
    inicio = time.perf_counter()
    arquivos = sorted(p for p in origem.rglob("*") if p.is_file())
    for arquivo in arquivos:
        processar(arquivo, ctx)
    return len(arquivos), time.perf_counter() - inicio


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    partes = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    partes.add_argument("cenarios", nargs="*")
    partes.add_argument("--destinos", type=Path, required=True)
    partes.add_argument("--corpus", type=Path, default=RAIZ / "tests" / "corpus")
    partes.add_argument("--hoje", type=date.fromisoformat, default=date.today())
    args = partes.parse_args(argv)
    lista = cenarios(args.corpus, args.cenarios)
    if not lista:
        print("nenhum cenário")
        return 2
    log.configurar(args.destinos / "_logs")
    modelos.preparar_ambiente()
    from scan_to_text.ler.layout import Leitor
    leitor = Leitor(modelos.pasta_modelos())
    total = time.perf_counter()
    for n, cenario in enumerate(lista, start=1):
        arquivos, segundos = rodar(cenario, args.destinos, leitor, args.hoje)
        print(f"[{n}/{len(lista)}] {cenario.name}: {arquivos} arquivo(s), {segundos:.0f} s", flush=True)
    print(f"total: {time.perf_counter() - total:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
