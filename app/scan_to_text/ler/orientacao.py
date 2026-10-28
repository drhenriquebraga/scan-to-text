"""Orientação da página antes do layout.

O Docling lê o texto de uma página de cabeça para baixo, mas devolve linhas e
colunas na ordem invertida; por isso a página é endireitada antes. Página
deitada se reconhece pelas caixas de texto altas e finas; de cabeça para baixo,
pelo classificador de linha do RapidOCR (rótulo "180"). A imagem é reduzida
antes, porque só a forma e o sentido das linhas importam aqui.
"""
from pathlib import Path

import numpy as np
from PIL import Image

LADO_MAXIMO = 1000
AMOSTRA = 20
MAIORIA = 0.6


class Orientador:
    def __init__(self, pasta_modelos: Path):
        from rapidocr import RapidOCR

        from scan_to_text.modelos import parametros_rapidocr

        self._motor = RapidOCR(params=parametros_rapidocr(pasta_modelos))

    def _caixas(self, matriz: np.ndarray) -> list:
        saida = self._motor(matriz, use_det=True, use_cls=False, use_rec=False)
        return [] if saida.boxes is None else list(saida.boxes)

    @staticmethod
    def _medidas(caixa) -> tuple[int, int, int, int]:
        xs = [int(p[0]) for p in caixa]
        ys = [int(p[1]) for p in caixa]
        return min(xs), min(ys), max(xs), max(ys)

    def _proporcao_invertida(self, matriz: np.ndarray, caixas: list) -> float:
        maiores = sorted(caixas, key=lambda c: -(self._medidas(c)[2] - self._medidas(c)[0]))
        recortes = []
        for caixa in maiores[:AMOSTRA]:
            x0, y0, x1, y1 = self._medidas(caixa)
            if x1 - x0 > 4 and y1 - y0 > 4:
                recortes.append(matriz[max(0, y0):y1, max(0, x0):x1])
        if not recortes:
            return 0.0
        rotulos = [str(r[0]) for r in self._motor.text_cls(recortes).cls_res]
        return rotulos.count("180") / len(rotulos)

    def angulo(self, imagem: Image.Image) -> int:
        """Graus para `Image.rotate(angulo, expand=True)` deixar o texto em pé."""
        reduzida = imagem.convert("RGB")
        reduzida.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
        matriz = np.array(reduzida)
        caixas = self._caixas(matriz)
        if not caixas:
            return 0
        altas = sum(1 for c in caixas
                    if (self._medidas(c)[3] - self._medidas(c)[1]) > (self._medidas(c)[2] - self._medidas(c)[0]))
        base = 0
        if altas / len(caixas) >= MAIORIA:
            base = 90
            matriz = np.array(reduzida.rotate(90, expand=True))
            caixas = self._caixas(matriz)
        if self._proporcao_invertida(matriz, caixas) >= MAIORIA:
            return (base + 180) % 360
        return base
