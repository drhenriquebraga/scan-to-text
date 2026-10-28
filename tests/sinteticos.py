"""Páginas sintéticas para os testes de leitura. Nomes e instituições inventados."""
from PIL import Image, ImageDraw, ImageFont

FONTE = r"C:\Windows\Fonts\arial.ttf"


def _fonte(tamanho: int):
    return ImageFont.truetype(FONTE, tamanho)


def laudo_laboratorio() -> Image.Image:
    img = Image.new("RGB", (2480, 3508), "white")
    d = ImageDraw.Draw(img)
    d.text((150, 120), "LABORATÓRIO EXEMPLO ANÁLISES CLÍNICAS", font=_fonte(52), fill="black")
    d.text((150, 200), "Rua Fictícia, 100 - Centro - Tel (11) 5555-0000", font=_fonte(34), fill="black")
    d.text((150, 330), "Paciente: FULANA DE TESTE SINTÉTICA", font=_fonte(40), fill="black")
    d.text((150, 390), "Médico solicitante: Dr. Beltrano Exemplar  CRM 00000", font=_fonte(40), fill="black")
    d.text((150, 450), "Data da coleta: 02/04/2026", font=_fonte(40), fill="black")
    d.text((150, 600), "HEMOGRAMA COMPLETO", font=_fonte(60), fill="black")
    linhas = [("Exame", "Resultado", "Unidade", "Valores de referência"),
              ("Hemoglobina", "12,4", "g/dL", "12,0 a 16,0"),
              ("Hematócrito", "37,1", "%", "36,0 a 46,0"),
              ("Leucócitos", "6.800", "/mm³", "4.000 a 11.000")]
    y = 760
    for linha in linhas:
        for i, celula in enumerate(linha):
            d.text((150 + i * 560, y), celula, font=_fonte(38), fill="black")
        d.line((140, y + 60, 2340, y + 60), fill="black", width=2)
        y += 90
    d.text((150, 3300), "Página 1 de 1 - Documento assinado eletronicamente", font=_fonte(30), fill="black")
    return img


def pagina_em_branco() -> Image.Image:
    return Image.new("RGB", (2480, 3508), "white")
