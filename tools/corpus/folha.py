"""Página desenhada como lista de operações, e os blocos de um laudo.

A página é guardada como operações (e não desenhada direto no canvas) para que
o cenário possa embaralhar, girar, repetir como xerox e intercalar versos em
branco depois de o laudo estar pronto. Tudo com as 14 fontes padrão do PDF,
que o PDFium desenha sem arquivo de fonte: o corpus não depende da máquina.
"""
from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from corpus.ficticio import Instituicao, Medico

LARGURA, ALTURA = A4
MARGEM = 50
UTIL = LARGURA - 2 * MARGEM
LIMITE_INFERIOR = 72          # abaixo disto só o rodapé
REGULAR, NEGRITO, ITALICO = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


class PaginaCheia(ValueError):
    """O modelo pôs mais texto do que cabe: erro do modelo, não do cenário."""


@dataclass
class Folha:
    ops: list[tuple] = field(default_factory=list)
    y: float = ALTURA - MARGEM

    def texto(self, x: float, y: float, s: str, fonte: str = REGULAR, tam: float = 9.5,
              alinhamento: str = "esquerda") -> None:
        self.ops.append(("texto", x, y, s, fonte, tam, alinhamento))

    def linha(self, x1: float, y1: float, x2: float, y2: float, esp: float = 0.6,
              cinza: float = 0.0) -> None:
        self.ops.append(("linha", x1, y1, x2, y2, esp, cinza))

    def retangulo(self, x: float, y: float, w: float, h: float, esp: float = 0.6,
                  preenchimento: float | None = None) -> None:
        self.ops.append(("retangulo", x, y, w, h, esp, preenchimento))

    def circulo(self, x: float, y: float, r: float, preenchimento: float | None = None,
                esp: float = 0.8) -> None:
        self.ops.append(("circulo", x, y, r, preenchimento, esp))

    def descer(self, dy: float) -> None:
        self.y -= dy
        if self.y < LIMITE_INFERIOR:
            raise PaginaCheia(f"y={self.y:.0f}")


# --- blocos do laudo -------------------------------------------------------------

def cabecalho(f: Folha, inst: Instituicao, estilo: int) -> None:
    """Três jeitos de imprimir a instituição no topo; o nome é sempre a linha mais alta."""
    topo = ALTURA - 46
    if estilo == 0:
        f.texto(MARGEM, topo, inst.nome.upper(), NEGRITO, 13)
        f.texto(MARGEM, topo - 13, inst.endereco, REGULAR, 7)
        f.texto(MARGEM, topo - 22, f"CNES {inst.cnes}", REGULAR, 7)
        f.linha(MARGEM, topo - 28, LARGURA - MARGEM, topo - 28, 1.2)
        f.y = topo - 46
    elif estilo == 1:
        # marca só com traços: letra dentro do logotipo viraria uma linha a mais
        # no topo, e a instituição do gabarito deixaria de ser a mais alta
        f.circulo(MARGEM + 16, topo - 6, 16, preenchimento=0.25)
        f.linha(MARGEM + 16, topo - 16, MARGEM + 16, topo + 4, 3.0, cinza=1.0)
        f.linha(MARGEM + 6, topo - 6, MARGEM + 26, topo - 6, 3.0, cinza=1.0)
        f.texto(MARGEM + 42, topo, inst.nome, NEGRITO, 14)
        f.texto(MARGEM + 42, topo - 13, f"{inst.cidade} - {inst.uf}", REGULAR, 8)
        f.linha(MARGEM, topo - 28, LARGURA - MARGEM, topo - 28, 0.8)
        f.y = topo - 46
    else:
        f.retangulo(MARGEM, topo - 30, UTIL, 44, 0.8, preenchimento=0.92)
        f.texto(LARGURA / 2, topo - 2, inst.nome.upper(), NEGRITO, 12.5, "centro")
        f.texto(LARGURA / 2, topo - 16, f"Tel. {inst.telefone} - CNES {inst.cnes}", REGULAR, 7.5,
                "centro")
        f.y = topo - 50


def campos(f: Folha, pares: list[tuple[str, str]], colunas: int = 2, tam: float = 9,
           dois_pontos: bool = True, moldura: bool = False) -> None:
    """Rótulo em negrito e valor ao lado; dois pares por linha quando cabem."""
    inicio = f.y + tam
    largura_coluna = UTIL / colunas
    fila: list[tuple[str, str]] = []

    def larg(par: tuple[str, str]) -> float:
        rotulo, valor = par
        return stringWidth(rotulo + (": " if dois_pontos else "  "), NEGRITO, tam) + \
            stringWidth(valor, REGULAR, tam)

    def imprimir(linha: list[tuple[str, str]]) -> None:
        x = MARGEM + 4
        for rotulo, valor in linha:
            texto_rotulo = rotulo + (":" if dois_pontos else "")
            f.texto(x, f.y, texto_rotulo, NEGRITO, tam)
            f.texto(x + stringWidth(texto_rotulo + " ", NEGRITO, tam) + 1, f.y, valor, REGULAR, tam)
            x = max(x + largura_coluna, x + larg((rotulo, valor)) + 18)
        f.descer(tam * 1.55)

    for par in pares:
        cabe = sum(max(larg(p) + 18, largura_coluna) for p in fila + [par]) <= UTIL
        if fila and (len(fila) >= colunas or not cabe):
            imprimir(fila)
            fila = []
        fila.append(par)
    if fila:
        imprimir(fila)
    if moldura:
        f.retangulo(MARGEM, f.y + tam * 0.6, UTIL, inicio - f.y - tam * 0.2, 0.6)
    f.descer(6)


def titulo(f: Folha, s: str, tam: float = 12.5) -> None:
    f.descer(4)
    for parte in simpleSplit(s, NEGRITO, tam, UTIL):
        f.texto(LARGURA / 2, f.y, parte, NEGRITO, tam, "centro")
        f.descer(tam * 1.4)
    f.descer(6)


def secao(f: Folha, s: str, tam: float = 9.5) -> None:
    f.descer(3)
    f.texto(MARGEM, f.y, s, NEGRITO, tam)
    f.descer(tam * 1.5)


def paragrafo(f: Folha, s: str, tam: float = 9.5, recuo: float = 0, fonte: str = REGULAR,
              alinhamento: str = "esquerda") -> None:
    for parte in simpleSplit(s, fonte, tam, UTIL - recuo):
        x = LARGURA - MARGEM if alinhamento == "direita" else MARGEM + recuo
        f.texto(x, f.y, parte, fonte, tam, alinhamento)
        f.descer(tam * 1.38)
    f.descer(3)


def tabela(f: Folha, linhas: list[tuple[str, ...]], larguras: tuple[float, ...],
           tam: float = 8.8, cabecalho_linhas: int = 1, grade: bool = False) -> None:
    """Tabela com filetes; `larguras` são frações da largura útil."""
    xs = [MARGEM]
    for fracao in larguras:
        xs.append(xs[-1] + fracao * UTIL)
    topo = f.y + tam + 3
    f.linha(MARGEM, topo, LARGURA - MARGEM, topo, 0.8)
    for n, linha in enumerate(linhas):
        fonte = NEGRITO if n < cabecalho_linhas else REGULAR
        quebras = [simpleSplit(celula, fonte, tam, (xs[i + 1] - xs[i]) - 6) or [""]
                   for i, celula in enumerate(linha)]
        altura = max(len(q) for q in quebras)
        for i, partes in enumerate(quebras):
            for k, parte in enumerate(partes):
                f.texto(xs[i] + 3, f.y - k * tam * 1.25, parte, fonte, tam)
        base = f.y - (altura - 1) * tam * 1.25 - tam * 0.55
        if n < cabecalho_linhas or grade or n == len(linhas) - 1:
            f.linha(MARGEM, base, LARGURA - MARGEM, base, 0.8 if n < cabecalho_linhas else 0.4)
        f.descer(altura * tam * 1.25 + tam * 0.55)
    if grade:
        for x in xs:
            f.linha(x, topo, x, f.y + tam * 1.25, 0.4)
    f.descer(5)


def assinatura(f: Folha, medico: Medico, funcao: str, rng: random.Random,
               eletronica: bool = False) -> None:
    f.descer(18)
    centro = LARGURA - MARGEM - 120
    if not eletronica:
        # rubrica: meia dúzia de traços, desenhados com a semente do cenário
        x, y = centro - 55, f.y + 10
        for _ in range(7):
            nx, ny = x + rng.uniform(10, 20), f.y + rng.uniform(2, 20)
            f.linha(x, y, nx, ny, 0.9)
            x, y = nx, ny
    f.linha(centro - 110, f.y, centro + 110, f.y, 0.5)
    f.descer(11)
    f.texto(centro, f.y, medico.tratado, NEGRITO, 9, "centro")
    f.descer(11)
    f.texto(centro, f.y, f"{medico.conselho} - {funcao}", REGULAR, 8, "centro")
    f.descer(12)


def rodape(f: Folha, inst: Instituicao | None, numeracao: str | None = None,
           esquerda: str | None = None) -> None:
    """Rodapé com endereço (a armadilha de limpeza), numeração e nota à esquerda."""
    if inst is not None:
        f.linha(MARGEM, 58, LARGURA - MARGEM, 58, 0.5)
        f.texto(LARGURA / 2, 47, inst.endereco, REGULAR, 6.8, "centro")
    if numeracao:
        f.texto(LARGURA - MARGEM, 34, numeracao, REGULAR, 7.5, "direita")
    if esquerda:
        f.texto(MARGEM, 34, esquerda, REGULAR, 7)


def manuscrito(f: Folha, x: float, y: float, s: str, rng: random.Random, tam: float = 17) -> None:
    """Letra de mão simulada: linha de base que deriva devagar e letras de tamanho e giro irregulares."""
    deriva = 0.0
    for caractere in s:
        deriva = max(-1.8, min(1.8, deriva + rng.uniform(-0.45, 0.45)))
        if caractere != " ":
            f.ops.append(("letra", x, y + deriva, caractere, tam * rng.uniform(0.95, 1.05),
                          rng.uniform(-4, 4)))
        x += stringWidth(caractere, ITALICO, tam) * rng.uniform(1.0, 1.12)


def pautas(f: Folha, inicio: float, fim: float, passo: float = 26) -> None:
    y = inicio
    while y > fim:
        f.linha(MARGEM - 10, y, LARGURA - MARGEM + 10, y, 0.4, cinza=0.72)
        y -= passo


def verso(rng: random.Random, sujeira: bool) -> Folha:
    """Verso de folha: sem texto; às vezes com poucos pontos de tinta."""
    f = Folha()
    if sujeira:
        for _ in range(rng.randrange(2, 6)):
            f.circulo(rng.uniform(40, LARGURA - 40), rng.uniform(40, ALTURA - 40),
                      rng.uniform(0.4, 1.2), preenchimento=0.2, esp=0)
    return f


# --- saída em PDF ----------------------------------------------------------------

@dataclass(frozen=True)
class Impressao:
    """Como a folha entra no arquivo: girada, como xerox (deslocada e com bordas)."""
    folha: Folha
    rotacao: int = 0
    xerox: tuple[float, float, float] | None = None     # (dx, dy, graus)


def _desenhar_ops(c: rl_canvas.Canvas, folha: Folha) -> None:
    for op in folha.ops:
        tipo = op[0]
        if tipo == "texto":
            _, x, y, s, fonte, tam, alinhamento = op
            c.setFont(fonte, tam)
            if alinhamento == "centro":
                c.drawCentredString(x, y, s)
            elif alinhamento == "direita":
                c.drawRightString(x, y, s)
            else:
                c.drawString(x, y, s)
        elif tipo == "linha":
            _, x1, y1, x2, y2, esp, cinza = op
            c.setStrokeGray(cinza)
            c.setLineWidth(esp)
            c.line(x1, y1, x2, y2)
            c.setStrokeGray(0)
        elif tipo == "retangulo":
            _, x, y, w, h, esp, preenchimento = op
            c.setLineWidth(esp)
            if preenchimento is not None:
                c.setFillGray(preenchimento)
            c.rect(x, y, w, h, stroke=1, fill=preenchimento is not None)
            c.setFillGray(0)
        elif tipo == "circulo":
            _, x, y, r, preenchimento, esp = op
            c.setLineWidth(esp)
            if preenchimento is not None:
                c.setFillGray(preenchimento)
            c.circle(x, y, r, stroke=1 if esp else 0, fill=preenchimento is not None)
            c.setFillGray(0)
        elif tipo == "letra":
            _, x, y, caractere, tam, graus = op
            c.saveState()
            c.translate(x, y)
            c.rotate(graus)
            t = c.beginText(0, 0)
            t.setTextRenderMode(2)
            t.setFont(ITALICO, tam)
            c.setLineWidth(0.45)
            t.textOut(caractere)
            c.drawText(t)
            c.restoreState()


def _desenhar(c: rl_canvas.Canvas, impressao: Impressao) -> None:
    c.saveState()
    if impressao.xerox is not None:
        dx, dy, graus = impressao.xerox
        c.setFillGray(0.95)
        c.rect(0, 0, LARGURA, ALTURA, stroke=0, fill=1)
        c.setFillGray(0.55)
        for x, y, w, h in ((0, 0, 9, ALTURA), (LARGURA - 7, 0, 7, ALTURA), (0, 0, LARGURA, 6)):
            c.rect(x, y, w, h, stroke=0, fill=1)
        c.setFillGray(0)
        c.translate(LARGURA / 2 + dx, ALTURA / 2 + dy)
        c.rotate(graus)
        c.translate(-LARGURA / 2, -ALTURA / 2)
    if impressao.rotacao == 180:
        c.translate(LARGURA, ALTURA)
        c.rotate(180)
    _desenhar_ops(c, impressao.folha)
    c.restoreState()
    c.showPage()


def pdf_em_bytes(paginas: list[Impressao], senha: str | None = None) -> bytes:
    """PDF determinístico (`invariant`): a mesma entrada dá os mesmos bytes."""
    saida = io.BytesIO()
    cifra = StandardEncryption(senha, canPrint=0, strength=128) if senha else None
    c = rl_canvas.Canvas(saida, pagesize=A4, invariant=1, encrypt=cifra)
    c.setTitle("")
    c.setAuthor("")
    for impressao in paginas:
        _desenhar(c, impressao)
    c.save()
    return saida.getvalue()


def gravar_pdf(caminho: Path, paginas: list[Impressao], senha: str | None = None) -> None:
    caminho.write_bytes(pdf_em_bytes(paginas, senha))
