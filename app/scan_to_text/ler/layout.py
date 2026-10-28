"""Docling -> Pagina.

As linhas saem do OCR (uma por linha impressa, com a confiança dela) e o
Docling só diz de que tipo cada uma é: o bloco de layout que contém a linha dá
a camada, o tipo e o grupo. O bloco de texto do Docling junta várias linhas
impressas num parágrafo, e a regra de identidade precisa da linha. Linha de OCR
que não cai em bloco nenhum (texto dentro de figura, por exemplo) entra no
corpo pela posição: nada do que foi lido se perde.

A página é endireitada antes (ver `orientacao.py`), e o PDF com camada de texto
boa e em pé em todas as páginas é lido sem OCR.

O Docling monta o pipeline de cada formato (layout, TableFormer e OCR) no
primeiro uso dele, e não na construção do Leitor. Aqui a montagem é feita à
parte, antes de o arquivo ir para a memória, para a falha dela sair como
`FormatoIndisponivel`: ela impede a leitura de todo arquivo do formato e não é
defeito de nenhum. Montada depois da página, ela disputaria a memória com ela.
Só o formato lido é montado: o da imagem e o do PDF com texto, juntos, pesam
na memória. Se um não monta com o outro já montado, o outro sai e a montagem
se repete uma vez.
"""
import gc
from io import BytesIO
from pathlib import Path

from PIL import Image

from scan_to_text.ler import pdf as leitor_pdf
from scan_to_text.ler.orientacao import Orientador
from scan_to_text.modelos import preparar_ambiente
from scan_to_text.tipos import Linha, Pagina, Tabela

TOLERANCIA = 0.004
TINTA_MINIMA = 0.002
EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
EXTENSOES_TIFF = {".tif", ".tiff"}


class FormatoIndisponivel(RuntimeError):
    """O pipeline de um formato não montou: modelo, biblioteca ou memória que não bastam.

    O texto leva o formato e o tipo da causa, nunca a mensagem dela.
    """

    def __init__(self, formato: str, causa: BaseException):
        super().__init__(f"{formato} {type(causa).__name__}")
        self.formato = formato


class LeituraIncompleta(RuntimeError):
    """O Docling devolveu a conversão sem uma das páginas, duas vezes seguidas.

    Ele descarta a página que falhou num estágio e devolve as outras. O texto
    leva só os números das páginas que faltaram.
    """

    def __init__(self, faltam: list[int]):
        super().__init__(",".join(str(numero) for numero in faltam))


def _tipo_e_camada(rotulo) -> tuple[str, str | None]:
    from docling_core.types.doc import DocItemLabel as R

    if rotulo == R.PAGE_HEADER:
        return "texto", "cabecalho"
    if rotulo == R.PAGE_FOOTER:
        return "texto", "rodape"
    tipos = {R.TITLE: "titulo", R.SECTION_HEADER: "secao", R.LIST_ITEM: "lista",
             R.CAPTION: "legenda", R.FOOTNOTE: "nota"}
    return tipos.get(rotulo, "texto"), None


class _Celula:
    __slots__ = ("texto", "l", "t", "r", "b", "confianca", "usada")

    def __init__(self, texto, l, t, r, b, confianca):
        self.texto, self.l, self.t, self.r, self.b = texto, l, t, r, b
        self.confianca = confianca
        self.usada = False

    def centro_em(self, l, t, r, b, folga) -> bool:
        cx, cy = (self.l + self.r) / 2, (self.t + self.b) / 2
        return l - folga <= cx <= r + folga and t - folga <= cy <= b + folga


def _juntar_em_linhas(celulas: list[_Celula]) -> list[list[_Celula]]:
    """Agrupa células da mesma linha impressa (sobreposição vertical)."""
    linhas: list[list[_Celula]] = []
    for c in sorted(celulas, key=lambda c: (c.t, c.l)):
        for linha in linhas:
            ref = linha[0]
            sobreposicao = min(ref.b, c.b) - max(ref.t, c.t)
            if sobreposicao > 0.5 * min(ref.b - ref.t, c.b - c.t):
                linha.append(c)
                break
        else:
            linhas.append([c])
    return [sorted(linha, key=lambda c: c.l) for linha in linhas]


def _linha(celulas: list[_Celula], w: float, h: float, camada: str, tipo: str, grupo: int) -> Linha:
    l = min(c.l for c in celulas) / w
    t = min(c.t for c in celulas) / h
    r = max(c.r for c in celulas) / w
    b = max(c.b for c in celulas) / h
    return Linha(texto=" ".join(c.texto.strip() for c in celulas if c.texto.strip()),
                 caixa=(max(0.0, l), max(0.0, t), min(1.0, r), min(1.0, b)),
                 camada=camada, tipo=tipo,
                 confianca=min(c.confianca for c in celulas), grupo=grupo)


def _tinta(imagem: Image.Image) -> float:
    cinza = imagem.convert("L")
    cinza.thumbnail((600, 600))
    histograma = cinza.histogram()
    return sum(histograma[:128]) / max(1, sum(histograma))


class Leitor:
    def __init__(self, pasta_modelos: Path, modo_tabela: str = "preciso", orientar: bool = True):
        preparar_ambiente()
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (OcrMode, PdfPipelineOptions,
                                                        RapidOcrOptions, TableFormerMode)
        from docling.document_converter import (DocumentConverter, ImageFormatOption,
                                                PdfFormatOption, WordFormatOption)

        def opcoes(ocr: bool) -> PdfPipelineOptions:
            o = PdfPipelineOptions(
                artifacts_path=pasta_modelos, do_ocr=ocr, do_table_structure=True,
                generate_parsed_pages=True,
                accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
                ocr_options=RapidOcrOptions(backend="onnxruntime", mode=OcrMode.FULL_PAGE))
            o.table_structure_options.mode = (TableFormerMode.FAST if modo_tabela == "rapido"
                                              else TableFormerMode.ACCURATE)
            return o

        self._formatos = InputFormat
        self._conversor = DocumentConverter(
            allowed_formats=[InputFormat.IMAGE, InputFormat.PDF, InputFormat.DOCX],
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=opcoes(True)),
                InputFormat.PDF: PdfFormatOption(pipeline_options=opcoes(False)),
                InputFormat.DOCX: WordFormatOption(),
            })
        self._orientador = Orientador(pasta_modelos) if orientar else None

    # ---------------------------------------------------------------- entradas
    def ler_arquivo(self, caminho: Path, sha256: str) -> list[Pagina]:
        sufixo = caminho.suffix.lower()
        if sufixo == ".pdf":
            return self.ler_pdf(caminho, sha256)
        if sufixo == ".docx":
            return self.ler_docx(caminho, sha256)
        if sufixo in EXTENSOES_TIFF:
            self._montar(self._formatos.IMAGE)
            return self._ler_quadros(Image.open(caminho), sha256)
        if sufixo in EXTENSOES_IMAGEM:
            self._montar(self._formatos.IMAGE)
            with Image.open(caminho) as imagem:
                return [self.ler_imagem(imagem.convert("RGB"), sha256, 0)]
        raise ValueError(sufixo)

    def _ler_quadros(self, imagem: Image.Image, sha256: str) -> list[Pagina]:
        paginas = []
        for indice in range(getattr(imagem, "n_frames", 1)):
            imagem.seek(indice)
            paginas.append(self.ler_imagem(imagem.convert("RGB"), sha256, indice))
        return paginas

    def ler_pdf(self, caminho: Path, sha256: str) -> list[Pagina]:
        documento = leitor_pdf.abrir(caminho)
        total = len(documento)
        if total and all(leitor_pdf.camada_texto_boa(documento, i)
                         and leitor_pdf.camada_texto_em_pe(documento, i) for i in range(total)):
            self._montar(self._formatos.PDF)
            resultado = self._converter(lambda: caminho, total)
            return [self._pagina(resultado, i, sha256, i, None, 0) for i in range(total)]
        if total:
            self._montar(self._formatos.IMAGE)
        return [self.ler_imagem(leitor_pdf.rasterizar(documento, i), sha256, i) for i in range(total)]

    def ler_imagem(self, imagem: Image.Image, sha256: str, indice: int) -> Pagina:
        from docling.datamodel.base_models import DocumentStream

        self._montar(self._formatos.IMAGE)  # já montado quando a imagem veio de `ler_arquivo`
        rotacao = self._orientador.angulo(imagem) if self._orientador else 0
        if rotacao:
            imagem = imagem.rotate(rotacao, expand=True)
        if _tinta(imagem) < TINTA_MINIMA:
            return Pagina(arquivo_sha256=sha256, indice=indice, blocos=(), em_branco=True,
                          rotacao=rotacao)
        buffer = BytesIO()
        imagem.save(buffer, format="PNG")
        png = buffer.getvalue()
        del buffer
        resultado = self._converter(
            lambda: DocumentStream(name="pagina.png", stream=BytesIO(png)), 1)
        return self._pagina(resultado, 0, sha256, indice, imagem, rotacao)

    def ler_docx(self, caminho: Path, sha256: str) -> list[Pagina]:
        from docling_core.types.doc import TableItem

        self._montar(self._formatos.DOCX)
        resultado = self._conversor.convert(caminho)
        itens = list(self._itens(resultado.document))
        blocos, total = [], max(1, len(itens))
        for posicao, (item, _nivel) in enumerate(itens):
            faixa = (0.0, posicao / total, 1.0, (posicao + 1) / total)
            tipo, camada = _tipo_e_camada(item.label)
            camada = camada or self._camada_da_furnitura(item, faixa)
            if isinstance(item, TableItem):
                blocos.append(self._tabela_sem_ocr(item, faixa))
            elif getattr(item, "text", "").strip():
                for texto in item.text.splitlines():
                    if texto.strip():
                        blocos.append(Linha(texto=texto.strip(), caixa=faixa, camada=camada,
                                            tipo=tipo, confianca=1.0, grupo=posicao))
        return [Pagina(arquivo_sha256=sha256, indice=0, blocos=tuple(self._ordenar(blocos)),
                       em_branco=not blocos)]

    def _converter(self, fonte, paginas: int):
        """Converte `fonte()` e confere que as páginas 1 a `paginas` vieram.

        Página que falta faz a conversão se repetir uma vez; faltando de novo,
        é `LeituraIncompleta`, e nunca a página de outra posição no lugar dela.
        """
        for _ in range(2):
            resultado = self._conversor.convert(fonte())
            vieram = {pagina.page_no for pagina in resultado.pages}
            faltam = [numero for numero in range(1, paginas + 1) if numero not in vieram]
            if not faltam:
                return resultado
            del resultado  # a repetição precisa da memória
        raise LeituraIncompleta(faltam)

    def _montar(self, formato) -> None:
        """Monta o pipeline do formato, se ainda não está montado."""
        try:
            self._conversor.initialize_pipeline(formato)
            return
        except Exception as erro:
            montados = getattr(self._conversor, "initialized_pipelines", None)
            if not montados:
                raise FormatoIndisponivel(formato.name, erro) from erro
        # Fora do `except`: o rastro da falha segura o que a montagem chegou a
        # carregar, e a segunda tentativa precisa dessa memória.
        montados.clear()
        gc.collect()
        try:
            self._conversor.initialize_pipeline(formato)
        except Exception as erro:
            raise FormatoIndisponivel(formato.name, erro) from erro

    # ---------------------------------------------------------------- montagem
    @staticmethod
    def _itens(documento):
        from docling_core.types.doc import ContentLayer

        return documento.iterate_items(included_content_layers={ContentLayer.BODY,
                                                                ContentLayer.FURNITURE})

    @staticmethod
    def _camada_da_furnitura(item, caixa) -> str:
        from docling_core.types.doc import ContentLayer

        if item.content_layer != ContentLayer.FURNITURE:
            return "corpo"
        return "cabecalho" if (caixa[1] + caixa[3]) / 2 < 0.5 else "rodape"

    @staticmethod
    def _ordenar(blocos: list) -> list:
        """Cabeçalho no topo, depois o corpo na ordem de leitura, rodapé no fim."""
        def camada(b):
            return b.camada if isinstance(b, Linha) else "corpo"
        cabecalho = sorted((b for b in blocos if camada(b) == "cabecalho"), key=lambda b: b.caixa[1])
        rodape = sorted((b for b in blocos if camada(b) == "rodape"), key=lambda b: b.caixa[1])
        return cabecalho + [b for b in blocos if camada(b) == "corpo"] + rodape

    def _tabela_sem_ocr(self, item, caixa) -> Tabela:
        dados = item.data
        grade = [["" for _ in range(dados.num_cols)] for _ in range(dados.num_rows)]
        cabecalho = set()
        for celula in dados.table_cells:
            grade[celula.start_row_offset_idx][celula.start_col_offset_idx] = celula.text.strip()
            if celula.column_header:
                cabecalho.add(celula.start_row_offset_idx)
        return Tabela(celulas=tuple(tuple(l) for l in grade),
                      confiancas=tuple(tuple(1.0 for _ in l) for l in grade),
                      caixa=caixa, linhas_de_cabecalho=self._linhas_de_cabecalho(cabecalho))

    @staticmethod
    def _linhas_de_cabecalho(linhas: set[int]) -> int:
        n = 0
        while n in linhas:
            n += 1
        return n

    def _pagina(self, resultado, posicao: int, sha256: str, indice: int,
                imagem: Image.Image | None, rotacao: int) -> Pagina:
        from docling_core.types.doc import TableItem

        # Pelo número, e não pela posição na lista: o Docling pode devolver a
        # lista sem a página que falhou.
        pagina_docling = next(p for p in resultado.pages if p.page_no == posicao + 1)
        w, h = pagina_docling.size.width, pagina_docling.size.height
        celulas = []
        for c in pagina_docling.cells or []:
            if not c.text.strip():
                continue
            caixa = c.rect.to_bounding_box().to_top_left_origin(page_height=h)
            celulas.append(_Celula(c.text, caixa.l, caixa.t, caixa.r, caixa.b,
                                   float(c.confidence) if c.from_ocr else 1.0))
        folga = TOLERANCIA * max(w, h)
        blocos: list = []
        for grupo, (item, _nivel) in enumerate(self._itens(resultado.document)):
            provas = [p for p in getattr(item, "prov", []) if p.page_no - 1 == posicao]
            if not provas:
                continue
            caixa = provas[0].bbox.to_top_left_origin(page_height=h)
            fracao = (caixa.l / w, caixa.t / h, caixa.r / w, caixa.b / h)
            tipo, camada = _tipo_e_camada(item.label)
            camada = camada or self._camada_da_furnitura(item, fracao)
            if isinstance(item, TableItem):
                blocos.append(self._tabela(item, celulas, fracao, caixa, h, folga))
                continue
            dentro = [c for c in celulas if not c.usada and c.centro_em(caixa.l, caixa.t, caixa.r, caixa.b, folga)]
            if dentro:
                for c in dentro:
                    c.usada = True
                blocos.extend(_linha(linha, w, h, camada, tipo, grupo) for linha in _juntar_em_linhas(dentro))
            elif getattr(item, "text", "").strip():
                blocos.append(Linha(texto=item.text.strip(), caixa=fracao, camada=camada, tipo=tipo,
                                    confianca=1.0, grupo=grupo))
        sobras = [c for c in celulas if not c.usada]
        for linha in _juntar_em_linhas(sobras):
            nova = _linha(linha, w, h, "corpo", "texto", -1)
            posicao_certa = next((i for i, b in enumerate(blocos)
                                  if b.caixa[1] > nova.caixa[1] and
                                  (not isinstance(b, Linha) or b.camada == "corpo")), len(blocos))
            blocos.insert(posicao_certa, nova)
        blocos = self._ordenar(blocos)
        usadas = [c.confianca for c in celulas]
        vazia = not blocos and (imagem is None or _tinta(imagem) < TINTA_MINIMA)
        return Pagina(arquivo_sha256=sha256, indice=indice, blocos=tuple(blocos),
                      em_branco=vazia,
                      confianca_media=sum(usadas) / len(usadas) if usadas else 1.0,
                      rotacao=rotacao)

    def _tabela(self, item, celulas: list[_Celula], fracao, caixa_px, h: float, folga) -> Tabela:
        dados = item.data
        grade = [["" for _ in range(dados.num_cols)] for _ in range(dados.num_rows)]
        confiancas = [[1.0 for _ in range(dados.num_cols)] for _ in range(dados.num_rows)]
        cabecalho = set()
        for celula in dados.table_cells:
            linha, coluna = celula.start_row_offset_idx, celula.start_col_offset_idx
            grade[linha][coluna] = celula.text.strip()
            if celula.column_header:
                cabecalho.add(linha)
            if celula.bbox is not None:
                b = celula.bbox.to_top_left_origin(page_height=h)
                dentro = [c for c in celulas if c.centro_em(b.l, b.t, b.r, b.b, folga)]
                for c in dentro:
                    c.usada = True
                if dentro:
                    confiancas[linha][coluna] = min(c.confianca for c in dentro)
        # Linha de OCR dentro da área da tabela e fora de toda célula já entrou
        # no texto da grade (o Docling montou a grade com ela).
        for c in celulas:
            if not c.usada and c.centro_em(caixa_px.l, caixa_px.t, caixa_px.r, caixa_px.b, 0):
                c.usada = True
        return Tabela(celulas=tuple(tuple(x) for x in grade),
                      confiancas=tuple(tuple(x) for x in confiancas),
                      caixa=fracao, linhas_de_cabecalho=self._linhas_de_cabecalho(cabecalho))
