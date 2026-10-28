"""Gera o corpus sintético de laudos em pt-BR, com gabarito, para a régua (`tools/avaliar.py`).

Cada cenário vira `<saida>/<cenario>-<semente>/`, com os arquivos de origem em
`origem/` e a verdade em `gabarito.json`. Nomes, instituições e endereços são
inventados (`corpus/ficticio.py`): nenhum dado real entra aqui. A mesma
semente produz os mesmos bytes, para o determinismo poder ser medido.

`variante()` refaz um cenário só com imagem (rasterização por pypdfium2) e
ruído controlado; o gabarito acompanha, com os novos SHA-256.

Uso: python tools/gerar_corpus.py [--saida tests/corpus] [--cenarios ...]
     [--sementes 1 2 3 4 5] [--ruidos 0 0.01 0.03 0.05] [--sementes-ruido 1]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import shutil
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

from corpus.cenarios import CONSTRUTORES, Arquivo, Montagem, Zip
from corpus.folha import pdf_em_bytes

CENARIOS = ("pilha", "embaralhado", "verso_branco", "dois_pacientes", "contaminacao",
            "anotacao_sem_nome", "xerox_repetido", "seriado", "senha", "zip",
            "cabeca_para_baixo", "homonimos", "nome_social", "laboratorio_vr")
SEMENTES_PADRAO = (1, 2, 3, 4, 5)
RUIDOS_PADRAO = (0.0, 0.01, 0.03, 0.05)
RUIDO_MAXIMO = 0.2
DPI = 200
PASTA_PADRAO = Path(__file__).resolve().parents[1] / "tests" / "corpus"
DATA_FIXA_ZIP = (2026, 1, 1, 0, 0, 0)
GABARITO = "gabarito.json"


def _sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _pasta_nova(pasta: Path) -> None:
    """Recria a pasta de um cenário; recusa apagar pasta que não foi o gerador que fez."""
    if pasta.exists():
        if not (pasta / GABARITO).is_file():
            raise FileExistsError(f"{pasta} existe e não tem {GABARITO}")
        shutil.rmtree(pasta)
    (pasta / "origem").mkdir(parents=True)


def _zip_em_bytes(membros: list[tuple[str, bytes]]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, dados in membros:
            info = zipfile.ZipInfo(nome, date_time=DATA_FIXA_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, dados)
    return saida.getvalue()


def _escrever(m: Montagem, itens: list, pasta: Path) -> tuple[list[dict], list[dict], dict]:
    """Grava a origem; devolve origem_arquivos, arquivos e as páginas de cada documento."""
    arquivos: list[dict] = []
    paginas_do_doc: dict[str, list[tuple[int, int, str]]] = defaultdict(list)

    def pdf(arq: Arquivo, caminho: str) -> bytes:
        dados = pdf_em_bytes([e.impressao for e in arq.entradas], arq.senha)
        aid = f"a{len(arquivos) + 1}"
        registro = {"id": aid, "caminho": caminho, "sha256": _sha256(dados), "paginas": None,
                    "paginas_em_branco": [], "rotacoes": {}, "ilegivel": None}
        if arq.senha:
            registro["ilegivel"] = "pdf_senha"
        else:
            registro["paginas"] = len(arq.entradas)
            for posicao, entrada in enumerate(arq.entradas):
                if entrada.doc is None:
                    registro["paginas_em_branco"].append(posicao)
                else:
                    paginas_do_doc[entrada.doc].append((entrada.indice, posicao, aid))
                if entrada.impressao.rotacao:
                    registro["rotacoes"][str(posicao)] = entrada.impressao.rotacao
        arquivos.append(registro)
        return dados

    def empacotar(item, caminho: str) -> bytes:
        if isinstance(item, Arquivo):
            return pdf(item, caminho)
        return _zip_em_bytes([(filho.nome, empacotar(filho, f"{caminho}/{filho.nome}"))
                              for filho in item.itens])

    origem_arquivos = []
    for item in itens:
        dados = empacotar(item, item.nome)
        destino = pasta / "origem" / item.nome
        destino.write_bytes(dados)
        origem_arquivos.append({"nome": item.nome, "sha256": _sha256(dados), "tamanho": len(dados),
                                "mtime_ns": destino.stat().st_mtime_ns})
    return origem_arquivos, arquivos, paginas_do_doc


def _gabarito(cenario: str, semente: int, m: Montagem, origem_arquivos: list[dict],
              arquivos: list[dict], paginas_do_doc: dict) -> dict:
    documentos = []
    for did, plano in m.docs.items():
        paginas = sorted(paginas_do_doc[did])
        if not paginas or len({aid for _, _, aid in paginas}) != 1:
            raise AssertionError(f"{cenario}: documento {did} fora de um arquivo só")
        g = plano.gerado
        posicoes = [posicao for _, posicao, _ in paginas]
        documentos.append({
            "id": did, "arquivo": paginas[0][2], "paginas": posicoes, "titulo": g.titulo,
            "categoria": g.categoria, "instituicao": g.instituicao,
            "data": {"valor": g.data.isoformat(), "precisao": "dia"} if g.data else None,
            "datas_armadilha": sorted({d.isoformat() for d in g.datas_armadilha}),
            "paciente": g.paciente, "rotulado": g.paciente is not None, "destino": plano.destino,
            "numerada": g.numerada, "embaralhada": posicoes != sorted(posicoes),
            "pedido": g.pedido, "valores": g.valores, "referencias": g.referencias,
        })
    pacientes = [dict(p, nomes=p["nomes"] or [p["nome"]]) for p in m.pacientes]
    medicos = sorted({nome.upper() for plano in m.docs.values() for nome in plano.gerado.medicos})
    return {
        "versao_gabarito": 1, "cenario": cenario, "semente": semente, "idioma": "pt-BR",
        "imagem": False, "ruido": 0.0, "origem": "origem",
        "origem_arquivos": origem_arquivos, "arquivos": arquivos, "pacientes": pacientes,
        "documentos": documentos, "repetidos": [list(par) for par in m.repetidos],
        "seriados": [list(par) for par in m.seriados], "medicos": medicos,
    }


def _gravar_gabarito(pasta: Path, gabarito: dict) -> None:
    (pasta / GABARITO).write_text(json.dumps(gabarito, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")


def gerar(cenario: str, semente: int, saida: Path) -> Path:
    """Gera `<saida>/<cenario>-<semente>/` com `origem/` e `gabarito.json`; devolve a pasta."""
    if cenario not in CENARIOS:
        raise ValueError(f"cenário desconhecido: {cenario}")
    pasta = Path(saida) / f"{cenario}-{semente}"
    _pasta_nova(pasta)
    m, itens = CONSTRUTORES[cenario](random.Random(f"{cenario}:{semente}"))
    origem_arquivos, arquivos, paginas_do_doc = _escrever(m, itens, pasta)
    _gravar_gabarito(pasta, _gabarito(cenario, semente, m, origem_arquivos, arquivos, paginas_do_doc))
    return pasta


# --- rasterização e ruído ------------------------------------------------------

def _degradar(imagem: Image.Image, ruido: float, sorteio: np.random.Generator) -> Image.Image:
    """Ruído de digitalização controlado por `ruido`.

    `ruido` é a fração dos pixels de tinta apagados (falhas no traço, o que
    mais confunde dígito no OCR). Com ele crescem a inclinação (até ruido x 30
    graus), o desfoque (raio ruido x 12, a partir de 0,25 pixel), a sujeira no fundo (ruido / 20 dos
    pixels claros), o toner fraco e a compressão JPEG. Granulação em toda a
    página ficou de fora: multiplica o tamanho do JPEG sem piorar o OCR.
    """
    angulo = float(sorteio.uniform(-1, 1)) * ruido * 30
    # vizinho mais próximo e desfoque de caixa: o bicúbico e o gaussiano custam
    # cinco vezes mais e não mudam o que o OCR vê
    imagem = imagem.rotate(angulo, resample=Image.Resampling.NEAREST, fillcolor=255)
    if ruido * 12 >= 0.25:
        imagem = imagem.filter(ImageFilter.BoxBlur(ruido * 12))
    matriz = np.asarray(imagem).astype(np.int16)
    sorteado = sorteio.random(matriz.shape, dtype=np.float32)
    tinta = matriz < 160
    matriz[tinta & (sorteado < ruido)] = 255
    matriz[~tinta & (sorteado < ruido / 20)] = 40
    matriz = 255 - (255 - matriz) * round(100 - ruido * 400) // 100 - round(ruido * 250)
    return Image.fromarray(np.clip(matriz, 0, 255).astype(np.uint8))


def _rasterizar_bytes(dados: bytes, ruido: float, semente: int) -> bytes:
    if not 0 <= ruido <= RUIDO_MAXIMO:
        raise ValueError(f"ruído fora de 0..{RUIDO_MAXIMO}: {ruido}")
    documento = pdfium.PdfDocument(dados)
    try:
        imagens = []
        for n, pagina in enumerate(documento):
            imagem = pagina.render(scale=DPI / 72, grayscale=True).to_pil().convert("L")
            if ruido > 0:
                imagem = _degradar(imagem, ruido, np.random.default_rng([semente, n]))
            imagens.append(imagem)
    finally:
        documento.close()
    saida = io.BytesIO()
    fixa = time.gmtime(0)
    imagens[0].save(saida, "PDF", save_all=True, append_images=imagens[1:], resolution=float(DPI),
                    title="", creationDate=fixa, modDate=fixa, quality=round(90 - ruido * 400))
    return saida.getvalue()


def rasterizar(pdf: Path, saida: Path, ruido: float, semente: int) -> Path:
    """PDF só de imagem (200 dpi, cinza), com ruído controlado pela semente."""
    saida = Path(saida)
    if saida.is_dir():
        saida = saida / Path(pdf).name
    saida.write_bytes(_rasterizar_bytes(Path(pdf).read_bytes(), ruido, semente))
    return saida


def variante(pasta: Path, ruido: float, semente: int) -> Path:
    """Copia um cenário trocando cada PDF legível pela versão só de imagem com ruído."""
    pasta = Path(pasta)
    gabarito = json.loads((pasta / GABARITO).read_text(encoding="utf-8"))
    nova = pasta.parent / f"{pasta.name}-r{round(ruido * 100):02d}"
    _pasta_nova(nova)
    por_caminho = {a["caminho"]: a for a in gabarito["arquivos"]}
    contador = iter(range(10 ** 6))

    def converter(dados: bytes, caminho: str) -> bytes:
        if caminho.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(dados)) as z:
                membros = [(nome, converter(z.read(nome), f"{caminho}/{nome}")) for nome in z.namelist()]
            return _zip_em_bytes(membros)
        registro = por_caminho[caminho]
        if registro["ilegivel"]:
            return dados
        novo = _rasterizar_bytes(dados, ruido, semente * 1000 + next(contador))
        registro["sha256"] = _sha256(novo)
        return novo

    for item in gabarito["origem_arquivos"]:
        dados = converter((pasta / gabarito["origem"] / item["nome"]).read_bytes(), item["nome"])
        destino = nova / "origem" / item["nome"]
        destino.write_bytes(dados)
        item.update(sha256=_sha256(dados), tamanho=len(dados), mtime_ns=destino.stat().st_mtime_ns)
    gabarito.update(imagem=True, ruido=ruido, semente_ruido=semente)
    _gravar_gabarito(nova, gabarito)
    return nova


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gera o corpus sintético com gabarito.")
    parser.add_argument("--saida", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--cenarios", nargs="+", choices=CENARIOS, default=list(CENARIOS))
    parser.add_argument("--sementes", nargs="+", type=int, default=list(SEMENTES_PADRAO))
    parser.add_argument("--ruidos", nargs="*", type=float, default=list(RUIDOS_PADRAO))
    parser.add_argument("--sementes-ruido", nargs="*", type=int, default=None,
                        help="sementes que ganham as variantes só de imagem (padrão: a primeira)")
    args = parser.parse_args(argv)
    args.saida.mkdir(parents=True, exist_ok=True)
    com_ruido = set(args.sementes[:1] if args.sementes_ruido is None else args.sementes_ruido)
    for semente in args.sementes:
        for cenario in args.cenarios:
            pasta = gerar(cenario, semente, args.saida)
            print(pasta.name, flush=True)
            if semente in com_ruido:
                for ruido in args.ruidos:
                    print(variante(pasta, ruido, semente).name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
