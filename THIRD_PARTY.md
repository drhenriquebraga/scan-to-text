# Third-party components

Scan-to-Text is mostly other people's work, put together for one job. This file
lists what the release package carries, with its licence. The authoritative
list, with every file pinned by version and SHA-256, is
[`packager/catalogo.json`](packager/catalogo.json); the licence gate
[`tools/checar_licencas.py`](tools/checar_licencas.py) fails the tests when an
installed package has a licence outside the allowed list (MIT, BSD, Apache-2.0,
ISC, PSF, MPL-2.0, LGPL as a library, CDLA-Permissive for model data). GPL,
AGPL, non-commercial and no-derivatives licences are refused.

## What does the work

| Component | Role | Licence |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | Page layout, reading order, tables, DOCX | MIT |
| Docling layout model (`docling-layout-heron`) | Finds text blocks, titles, tables, headers and footers | Apache-2.0 |
| TableFormer (`docling-models`) | Table structure | CDLA-Permissive-2.0 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Character recognition and text-line orientation | Apache-2.0 |
| PaddleOCR models (PP-OCRv6) | The recognition models RapidOCR runs | Apache-2.0 |
| [ONNX Runtime](https://onnxruntime.ai) | Runs the recognition models | MIT |
| [PyTorch](https://pytorch.org) | Runs the layout and table models | BSD-3-Clause and others (see below) |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) / PDFium | Opens, renders and crops PDFs | Apache-2.0 or BSD-3-Clause |
| [pi-heif](https://github.com/bigcat88/pillow_heif) | HEIC photos | BSD-3-Clause; bundled libheif and libde265 under LGPL-3.0 |
| [pywebview](https://pywebview.flowrl.com) + pythonnet | The app window | BSD-3-Clause, MIT |
| [FastAPI](https://fastapi.tiangolo.com) + uvicorn | The local link between the window and the engine, on 127.0.0.1 | MIT, BSD-3-Clause |
| Python 3.14 embeddable package | Runs everything; bundled, nothing to install | PSF-2.0 |
| Transitions.dev: matrix-dot-loader, shimmer-text | The two effects on the home screen, adapted | Transitions.dev terms: use inside products allowed, with credit |
| Language selector and flags | Ported from local-suite (Radioterapia.AI) | Apache-2.0 |

## Microsoft components, in the release package only

| Component | Licence |
|---|---|
| Microsoft Edge WebView2 Runtime, Fixed Version (x64) | Microsoft WebView2 Runtime terms; distributed unmodified, only with Scan-to-Text. Contains AVC/H.264 codec technology (MPEG LA notice in the package). |
| Microsoft Visual C++ runtime (when present) | Visual Studio licence terms, Distributable Code; taken from a Visual Studio `VC\Redist` folder and distributed unmodified, only with Scan-to-Text. |

Neither is in this repository. The package carries `AVISOS-DE-TERCEIROS.txt`
with their terms.

## Every bundled Python package

Read from each package's own metadata by the licence gate.

| Package | Version | Licence |
|---|---|---|
| accelerate | 1.15.0 | Apache-2.0 |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| beautifulsoup4 | 4.15.0 | MIT |
| bottle | 0.13.4 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| clr-loader | 0.3.1 | MIT |
| colorama | 0.4.6 | BSD |
| colorlog | 6.12.0 | MIT |
| defusedxml | 0.7.1 | PSF-2.0 |
| dill | 0.4.1 | BSD-3-Clause |
| doclang | 0.7.3 | Apache-2.0 |
| docling | 2.130.0 | MIT |
| docling-core | 2.99.0 | MIT |
| docling-ibm-models | 4.0.3 | MIT |
| docling-parse | 7.22.0 | MIT |
| docling-slim | 2.130.0 | MIT |
| et-xmlfile | 2.0.0 | MIT |
| faker | 40.39.0 | MIT |
| fastapi | 0.141.1 | MIT |
| filelock | 4.0.5 | MIT |
| filetype | 1.2.0 | MIT |
| flatbuffers | 25.12.19 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.6.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| huggingface-hub | 1.33.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD |
| jsonref | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| langcodes | 3.5.1 | MIT |
| latex2mathml | 3.81.1 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| mail-parser | 4.6.5 | Apache-2.0 |
| markdown-it-py | 4.2.0 | MIT |
| marko | 2.2.4 | MIT |
| markupsafe | 3.0.3 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT |
| mpire | 2.10.2 | MIT |
| mpmath | 1.3.0 | BSD |
| multiprocess | 0.70.19 | BSD-3-Clause |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| olefile | 0.47 | BSD |
| omegaconf | 2.3.1 | BSD |
| onnxruntime | 1.30.0 | MIT |
| opencv-python | 5.0.0.93 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.6 | BSD |
| pi-heif | 1.4.0 | BSD-3-Clause |
| pillow | 12.3.0 | MIT-CMU |
| pluggy | 1.6.0 | MIT |
| polyfactory | 3.3.0 | MIT |
| protobuf | 7.36.2 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| pyclipper | 1.4.0 | MIT |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pylatexenc | 2.11 | MIT |
| pypdfium2 | 5.13.0 | Apache-2.0 OR BSD-3-Clause |
| python-dateutil | 2.9.0.post0 | BSD |
| python-docx | 1.2.0 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| python-oxmsg | 0.0.2 | MIT |
| python-pptx | 1.0.2 | MIT |
| pythonnet | 3.1.0 | MIT |
| pywebview | 6.2.1 | BSD |
| pywin32 | 312 | PSF-2.0 |
| pyyaml | 6.0.3 | MIT |
| rapidfuzz | 3.14.6 | MIT |
| rapidocr | 3.9.2 | Apache-2.0 |
| referencing | 0.37.0 | MIT |
| regex | 2026.9.10 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| rpds-py | 2026.6.3 | MIT |
| rtree | 1.4.1 | MIT |
| safetensors | 0.8.0 | Apache-2.0 |
| scipy | 1.18.1 | BSD-3-Clause AND GPL-3.0-or-later WITH GCC-exception-3.1 |
| semchunk | 3.2.5 | MIT |
| setuptools | 84.0.0 | MIT |
| shapely | 2.1.2 | BSD |
| shellingham | 1.5.4 | ISC |
| six | 1.17.0 | MIT |
| soupsieve | 2.10 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| sympy | 1.14.0 | BSD |
| tabulate | 0.10.0 | MIT |
| tokenizers | 0.23.2 | Apache-2.0 |
| torch | 2.14.0 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| torchvision | 0.29.0 | BSD |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| transformers | 5.17.0 | Apache-2.0 |
| tree-sitter | 0.26.0 | MIT |
| tree-sitter-c | 0.24.2 | MIT |
| tree-sitter-javascript | 0.25.0 | MIT |
| tree-sitter-python | 0.25.0 | MIT |
| tree-sitter-typescript | 0.23.2 | MIT |
| typer | 0.26.8 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.4 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |
| xlsxwriter | 3.2.9 | BSD |
| proxy-tools | 0.1.0 | MIT |
| antlr4-python3-runtime | 4.9.3 | BSD-3-Clause |

Only in tests, not in the package: pytest (MIT) and ReportLab (BSD-3-Clause).
