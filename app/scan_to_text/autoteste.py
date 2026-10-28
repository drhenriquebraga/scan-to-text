"""Autoteste do motor: o que o kit de diagnóstico mede em cada estação.

Carrega cada biblioteca, lê três páginas sintéticas (laudo corrido, tabela de
laboratório e página de cabeça para baixo), mede tempo por página, pico de
memória do processo e memória livre mínima da máquina, abre e fecha uma janela
de teste, e dá o veredito pelos limites de tempo por página e de memória
livre. Se o modo preciso das tabelas reprovar, mede de novo no modo rápido.
"""
import importlib
import threading
import time

from PIL import Image, ImageDraw, ImageFont

LIMITE_SEGUNDOS_POR_PAGINA = 30.0
LIVRE_MINIMO_MB = 1024
MODULOS = ("onnxruntime", "torch", "docling", "rapidocr", "pypdfium2", "psutil", "webview",
           "pythonnet")


def veredito(segundos_por_pagina: float, livre_minimo_mb: float) -> str:
    if segundos_por_pagina <= LIMITE_SEGUNDOS_POR_PAGINA and livre_minimo_mb >= LIVRE_MINIMO_MB:
        return "docling_completo"
    return "reprovado"


def etapa(nome: str, funcao) -> dict:
    inicio = time.perf_counter()
    try:
        funcao()
        detalhe, ok = "", True
    except BaseException as erro:  # noqa: BLE001 - o autoteste relata qualquer falha
        detalhe, ok = f"{type(erro).__name__}: {erro}", False
    return {"nome": nome, "ok": ok, "detalhe": detalhe,
            "segundos": round(time.perf_counter() - inicio, 2)}


def _fonte(tamanho: int):
    try:
        return ImageFont.truetype("arial.ttf", tamanho)
    except OSError:
        return ImageFont.load_default()


def _pagina(linhas: list[tuple[int, int, str, int]]) -> Image.Image:
    imagem = Image.new("RGB", (2480, 3508), "white")
    desenho = ImageDraw.Draw(imagem)
    for x, y, texto, tamanho in linhas:
        desenho.text((x, y), texto, font=_fonte(tamanho), fill="black")
    return imagem


def paginas_de_teste() -> list[tuple[str, Image.Image]]:
    laudo = _pagina([
        (150, 120, "CLÍNICA DE IMAGEM EXEMPLO", 52),
        (150, 330, "Paciente: FULANA DE TESTE SINTÉTICA", 40),
        (150, 450, "Data do exame: 02/04/2026", 40),
        (150, 600, "RESSONÂNCIA MAGNÉTICA DA PELVE", 56),
        (150, 760, "Técnica: aquisições multiplanares ponderadas em T1 e T2.", 38),
        (150, 840, "Achados: bexiga com paredes regulares e conteúdo homogêneo.", 38),
        (150, 920, "Conclusão: exame dentro dos limites da normalidade.", 38),
    ])
    linhas_tabela = [(150, 120, "LABORATÓRIO EXEMPLO", 52), (150, 600, "HEMOGRAMA", 56)]
    y = 760
    for linha in (("Exame", "Resultado", "Unidade"), ("Hemoglobina", "12,4", "g/dL"),
                  ("Hematócrito", "37,1", "%"), ("Leucócitos", "6.800", "/mm3")):
        linhas_tabela.extend((150 + i * 700, y, celula, 38) for i, celula in enumerate(linha))
        y += 90
    tabela = _pagina(linhas_tabela)
    desenho = ImageDraw.Draw(tabela)
    for i in range(5):
        desenho.line((140, 750 + i * 90, 2300, 750 + i * 90), fill="black", width=2)
    return [("laudo", laudo), ("tabela", tabela), ("invertida", tabela.rotate(180))]


class _Memoria:
    def __init__(self):
        import psutil

        self._psutil = psutil
        self._processo = psutil.Process()
        self.pico = 0
        self.livre_minimo = psutil.virtual_memory().available
        self._parar = threading.Event()
        self._fio = threading.Thread(target=self._amostrar, daemon=True)

    def _amostrar(self):
        while not self._parar.is_set():
            self.pico = max(self.pico, self._processo.memory_info().rss)
            self.livre_minimo = min(self.livre_minimo, self._psutil.virtual_memory().available)
            self._parar.wait(0.2)

    def __enter__(self):
        self._fio.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._fio.join()


def medir_leitura(leitor, paginas: list[tuple[str, Image.Image]]) -> dict:
    tempos, textos, rotacoes = [], [], []
    for indice, (_nome, imagem) in enumerate(paginas):
        inicio = time.perf_counter()
        pagina = leitor.ler_imagem(imagem, "0" * 64, indice)
        tempos.append(time.perf_counter() - inicio)
        rotacoes.append(pagina.rotacao)
        from scan_to_text.tipos import texto
        textos.append("\n".join(texto(b) for b in pagina.blocos))
    return {"paginas": len(paginas),
            "segundos_por_pagina": round(sum(tempos) / len(tempos), 2),
            "texto_conferido": all("Hemoglobina" in t for t in textos[1:]),
            "orientacao_certa": rotacoes[-1] == 180}


def _janela_de_teste(segundos: float = 2.0) -> None:
    import webview

    from scan_to_text.preflight import raiz_do_app

    runtime = raiz_do_app() / "webview2"
    if (runtime / "msedgewebview2.exe").is_file():
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
    janela = webview.create_window("Scan-to-Text", html="<p>ok</p>", width=320, height=200)

    def fechar():
        time.sleep(segundos)
        janela.destroy()

    webview.start(fechar)


def autoteste(janela: bool = True) -> dict:
    from scan_to_text import modelos

    modelos.preparar_ambiente()
    etapas = [etapa(f"importar {m}", lambda m=m: importlib.import_module(m)) for m in MODULOS]
    resultado = {"etapas": etapas, "segundos_por_pagina": None, "pico_mb": None,
                 "livre_minimo_mb": None, "veredito": "reprovado"}
    paginas = paginas_de_teste()
    for modo in ("preciso", "rapido"):
        medida: dict = {}
        with _Memoria() as memoria:
            def carregar_e_ler():
                from scan_to_text.ler.layout import Leitor
                leitor = Leitor(modelos.pasta_modelos(), modo_tabela=modo)
                medida.update(medir_leitura(leitor, paginas))
            etapas.append(etapa(f"ler paginas ({modo})", carregar_e_ler))
        if not medida:
            break
        pico_mb = memoria.pico / 2**20
        livre_mb = memoria.livre_minimo / 2**20
        resultado.update({"segundos_por_pagina": medida["segundos_por_pagina"],
                          "pico_mb": round(pico_mb), "livre_minimo_mb": round(livre_mb),
                          "texto_conferido": medida["texto_conferido"],
                          "orientacao_certa": medida["orientacao_certa"], "modo_tabela": modo})
        if veredito(medida["segundos_por_pagina"], livre_mb) == "docling_completo":
            resultado["veredito"] = "docling_completo" if modo == "preciso" else "tabelas_rapidas"
            break
        resultado["veredito"] = "so_rapidocr"
    if janela:
        etapas.append(etapa("abrir janela", _janela_de_teste))
    resultado["ok"] = all(e["ok"] for e in etapas) and bool(resultado.get("texto_conferido"))
    return resultado
