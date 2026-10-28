"""PDF: abrir, avaliar a camada de texto, rasterizar e recortar páginas.

Usa o PDFium (pypdfium2, Apache-2.0 ou BSD-3-Clause). A camada de texto é
"boa" quando tem texto suficiente e quase nada de lixo de codificação;
senão a página vai para OCR.
"""
import hashlib
import io
import math
import re
import unicodedata
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

MINIMO_DE_CARACTERES = 40
PROPORCAO_MINIMA_LEGIVEL = 0.85


class Ilegivel(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def abrir(caminho: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(caminho))
    except pdfium.PdfiumError as erro:
        texto = str(erro).lower()
        raise Ilegivel("senha" if "password" in texto else "corrompido") from erro


def texto_da_pagina(documento: pdfium.PdfDocument, indice: int) -> str:
    pagina = documento[indice]
    return pagina.get_textpage().get_text_range()


def camada_texto_boa(documento: pdfium.PdfDocument, indice: int) -> bool:
    texto = "".join(texto_da_pagina(documento, indice).split())
    if len(texto) < MINIMO_DE_CARACTERES or chr(0xFFFD) in texto:
        return False
    # Letra, número e pontuação contam; símbolo de controle e de área privada,
    # típicos de fonte sem mapa de caracteres, não.
    legiveis = sum(1 for c in texto if unicodedata.category(c)[0] in "LNP")
    return legiveis / len(texto) >= PROPORCAO_MINIMA_LEGIVEL


def camada_texto_em_pe(documento: pdfium.PdfDocument, indice: int) -> bool:
    """Os caracteres pertencem ao quadrante em pé, considerada a rotação da página."""
    with closing(documento[indice]) as pagina, closing(pagina.get_textpage()) as texto:
        rotacao = math.radians(pagina.get_rotation())
        viu = False
        for i in range(texto.count_chars()):
            if not chr(pdfium.raw.FPDFText_GetUnicode(texto, i)).isalnum():
                continue
            viu = True
            angulo = pdfium.raw.FPDFText_GetCharAngle(texto, i)
            # O giro da página pode compensar o do conteúdo. O orientador
            # corrige quadrantes, não inclinação: o quadrante zero preserva
            # a camada nativa. A tolerância só absorve o float na borda.
            visivel = math.remainder(angulo + rotacao, math.tau)
            if not (angulo >= 0 and abs(visivel) <= math.pi / 4 + 1e-4):
                return False
        return viu


def rasterizar(documento: pdfium.PdfDocument, indice: int, dpi: int = 300) -> Image.Image:
    return documento[indice].render(scale=dpi / 72).to_pil().convert("RGB")


def recortar(origem: Path, paginas: list[int], saida: Path) -> None:
    """Grava em `saida` um PDF só com `paginas`, na ordem pedida.

    A mesma entrada dá os mesmos bytes (ver `_sem_carimbo`).
    """
    fonte = abrir(origem)
    novo = pdfium.PdfDocument.new()
    try:
        for posicao, indice in enumerate(paginas):
            novo.import_pages(fonte, [indice], index=posicao)
        memoria = io.BytesIO()
        novo.save(memoria)
    finally:
        novo.close()
        fonte.close()
    Path(saida).write_bytes(_sem_carimbo(memoria.getvalue()))


_DATA_DE_CRIACAO = re.compile(rb"/CreationDate\s*\((D:[^)]*)\)")
_ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]*)>\s*<([0-9A-Fa-f]*)>\s*\]")


def _sem_carimbo(dados: bytes) -> bytes:
    """Tira do PDF do PDFium a data de criação e o /ID aleatório.

    Quem compara o recorte por bytes (a nova tentativa depois de uma falha)
    precisa que a mesma entrada dê o mesmo arquivo. A troca mantém o tamanho
    de cada campo, para a tabela de referências cruzadas continuar válida: a
    data vira zeros, e o /ID passa a ser o resumo do próprio conteúdo.
    """
    dados = _DATA_DE_CRIACAO.sub(lambda m: re.sub(rb"\d", b"0", m.group(0)), dados)
    achado = _ID.search(dados)
    if achado is None:
        return dados

    def trocar_id(preencher) -> bytes:
        return b"".join((dados[:achado.start(1)], preencher(len(achado.group(1))),
                         dados[achado.end(1):achado.start(2)], preencher(len(achado.group(2))),
                         dados[achado.end(2):]))

    resumo = hashlib.sha256(trocar_id(lambda n: b"0" * n)).hexdigest().upper().encode("ascii")
    return trocar_id(lambda n: (resumo * (n // len(resumo) + 1))[:n])
