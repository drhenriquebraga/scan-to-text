import io
import re
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from PIL import Image

from scan_to_text.ler import pdf
from scan_to_text.tipos import Linha, Pagina


def _pdf_de_imagens(caminho, tamanhos):
    imagens = [Image.new("RGB", t, "white") for t in tamanhos]
    imagens[0].save(caminho, save_all=True, append_images=imagens[1:], resolution=72)


def _pdf_nativo(caminho, orientacoes, texto_lateral=False):
    from reportlab.pdfgen.canvas import Canvas

    dados = io.BytesIO()
    c = Canvas(dados, pagesize=(300, 300), invariant=1)
    for conteudo, _pagina in orientacoes:
        c.saveState()
        c.translate(150, 150)
        c.rotate(conteudo)
        c.translate(-150, -150)
        c.drawString(25, 250, "LABORATORIO DE TESTE SINTETICO")
        c.drawString(25, 220, "HEMOGLOBINA 12,4 g/dL - RESULTADO LITERAL")
        c.restoreState()
        if texto_lateral:
            c.saveState()
            c.translate(280, 25)
            c.rotate(90)
            c.drawString(0, 0, "SELO")
            c.restoreState()
        c.showPage()
    c.save()
    with pdfium.PdfDocument(dados.getvalue()) as documento:
        for indice, (_, rotacao) in enumerate(orientacoes):
            pagina = documento[indice]
            pagina.set_rotation(rotacao)
            pagina.close()
        documento.save(caminho)


@pytest.mark.parametrize("conteudo, rotacao, esperado", [
    (0, 0, True), (180, 0, False), (0, 180, False),
    (180, 180, True), (90, 0, False), (270, 0, False),
    (90, 90, True), (270, 270, True),
    (0.3, 0, True), (-0.3, 0, True), (4, 0, True), (-4, 0, True),
    (45, 0, True), (-45, 0, True), (45.1, 0, False), (-45.1, 0, False),
    (87, 90, True), (93, 90, True),
])
def test_camada_nativa_em_pe_considera_conteudo_e_rotacao(tmp_path, conteudo, rotacao, esperado):
    arquivo = tmp_path / "nativo.pdf"
    _pdf_nativo(arquivo, [(conteudo, rotacao)])
    antes, mtime = arquivo.read_bytes(), arquivo.stat().st_mtime_ns
    with pdf.abrir(arquivo) as documento:
        assert pdf.camada_texto_boa(documento, 0)
        assert pdf.camada_texto_em_pe(documento, 0) is esperado
    assert arquivo.read_bytes() == antes and arquivo.stat().st_mtime_ns == mtime


def test_texto_lateral_nao_e_ignorado_pela_maioria_em_pe(tmp_path):
    arquivo = tmp_path / "misto.pdf"
    _pdf_nativo(arquivo, [(0, 0)], texto_lateral=True)
    with pdf.abrir(arquivo) as documento:
        assert pdf.camada_texto_boa(documento, 0)
        assert not pdf.camada_texto_em_pe(documento, 0)


def test_sem_medida_do_angulo_nao_autoriza_layout_nativo(tmp_path, monkeypatch):
    arquivo = tmp_path / "nativo.pdf"
    _pdf_nativo(arquivo, [(0, 0)])
    monkeypatch.setattr(pdfium.raw, "FPDFText_GetCharAngle", lambda texto, i: -1.0)
    with pdf.abrir(arquivo) as documento:
        assert not pdf.camada_texto_em_pe(documento, 0)


@pytest.mark.parametrize("orientacoes, caminho", [
    ([(0, 0), (180, 0)], "imagem"),
    ([(0, 0), (0, 180)], "imagem"),
    ([(0, 0), (0, 0)], "nativo"),
    ([(180, 180), (90, 90)], "nativo"),
    ([(3, 0), (-4, 0)], "nativo"),
])
def test_pdf_nativo_girado_usa_o_caminho_de_imagem_existente(tmp_path, orientacoes, caminho):
    from scan_to_text.ler.layout import Leitor

    arquivo = tmp_path / "nativo.pdf"
    _pdf_nativo(arquivo, orientacoes)
    antes, mtime = arquivo.read_bytes(), arquivo.stat().st_mtime_ns
    leitor = Leitor.__new__(Leitor)
    leitor._formatos = SimpleNamespace(PDF="pdf", IMAGE="imagem")
    leitor._montar = lambda formato: None
    leitor._converter = lambda fonte, total: fonte()
    leitor._pagina = lambda resultado, numero, sha, indice, imagem, rotacao: Pagina(
        sha, indice, (Linha("nativo", (0.1, 0.1, 0.9, 0.2)),))
    # PDFium abre e rasteriza o PDF real; os stubs da conversão pesada deixam
    # o teste medir apenas qual caminho recebe as páginas.
    leitor.ler_imagem = lambda imagem, sha, indice: Pagina(
        sha, indice, (Linha("imagem", (0.1, 0.1, 0.9, 0.2)),))
    paginas = leitor.ler_pdf(arquivo, "a" * 64)
    assert [(p.indice, p.blocos[0].texto) for p in paginas] == [(0, caminho), (1, caminho)]
    assert arquivo.read_bytes() == antes and arquivo.stat().st_mtime_ns == mtime


def test_arquivo_corrompido_e_ilegivel(tmp_path):
    ruim = tmp_path / "ruim.pdf"
    ruim.write_bytes(b"%PDF-1.7 isto nao e um pdf")
    with pytest.raises(pdf.Ilegivel) as erro:
        pdf.abrir(ruim)
    assert erro.value.motivo == "corrompido"


def test_pdf_so_de_imagem_nao_tem_camada_boa(tmp_path):
    arquivo = tmp_path / "escaneado.pdf"
    _pdf_de_imagens(arquivo, [(600, 800)])
    assert not pdf.camada_texto_boa(pdf.abrir(arquivo), 0)


def test_recorte_preserva_a_ordem_pedida(tmp_path):
    origem, saida = tmp_path / "tres.pdf", tmp_path / "recorte.pdf"
    _pdf_de_imagens(origem, [(100, 100), (200, 200), (300, 300)])
    pdf.recortar(origem, [2, 0], saida)
    novo = pdfium.PdfDocument(str(saida))
    assert [round(novo[i].get_width()) for i in range(len(novo))] == [300, 100]


def test_recorte_e_deterministico(tmp_path):
    # A mesma entrada dá os mesmos bytes: sem data de criação e com /ID estável.
    origem = tmp_path / "tres.pdf"
    _pdf_de_imagens(origem, [(100, 100), (200, 200), (300, 300)])
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    pdf.recortar(origem, [2, 0], a)
    pdf.recortar(origem, [2, 0], b)
    assert a.read_bytes() == b.read_bytes()
    assert not re.search(rb"/CreationDate\(D:[^)]*[1-9]", a.read_bytes())
    assert [round(p.get_width()) for p in pdfium.PdfDocument(str(a))] == [300, 100]


def test_rasterizar_devolve_imagem_na_resolucao_pedida(tmp_path):
    arquivo = tmp_path / "um.pdf"
    _pdf_de_imagens(arquivo, [(72, 144)])
    imagem = pdf.rasterizar(pdf.abrir(arquivo), 0, dpi=144)
    assert imagem.size == (144, 288)
