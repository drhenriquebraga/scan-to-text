"""Onde estão os modelos e como apontá-los, sem download.

No pacote, os modelos ficam em `<raiz>/modelos`; no código-fonte, em
`<raiz>/_montagem/modelos`. A pasta é achada pela marca (tem `RapidOcr/`),
subindo a partir deste arquivo, ou pela variável `SCAN_TO_TEXT_MODELOS`.
`preparar_ambiente()` tem de rodar antes de importar o Docling: o Hugging Face
lê o modo offline na importação.
"""
import os
from pathlib import Path

PASTA_RAPIDOCR = "RapidOcr"
ARQUIVOS_RAPIDOCR = {
    "Det.model_path": "PP-OCRv6_det_small.onnx",
    "Rec.model_path": "PP-OCRv6_rec_small.onnx",
    "Cls.model_path": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}
CANDIDATAS = ("modelos", "_montagem/modelos")


class ModelosAusentes(FileNotFoundError):
    pass


def pasta_modelos() -> Path:
    variavel = os.environ.get("SCAN_TO_TEXT_MODELOS")
    if variavel:
        return Path(variavel)
    for pasta in Path(__file__).resolve().parents:
        for relativa in CANDIDATAS:
            candidata = pasta / relativa
            if (candidata / PASTA_RAPIDOCR).is_dir():
                return candidata
    raise ModelosAusentes("modelos")


def parametros_rapidocr(pasta: Path) -> dict[str, str]:
    return {chave: str(pasta / PASTA_RAPIDOCR / arquivo) for chave, arquivo in ARQUIVOS_RAPIDOCR.items()}


def preparar_ambiente() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
