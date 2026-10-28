"""Testes do gerador do corpus sintético (tools/gerar_corpus.py).

O corpus é a régua: se o gabarito mente sobre o PDF, toda medida do
avaliador mente junto. Por isso cada cenário é conferido contra o próprio PDF
(páginas, texto, posição) e não só contra o gabarito.
"""
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
import pytest

import gerar_corpus as gc
from corpus import ficticio
from corpus.modelos import MODELOS

ESPERADOS = ("pilha", "embaralhado", "verso_branco", "dois_pacientes", "contaminacao",
            "anotacao_sem_nome", "xerox_repetido", "seriado", "senha", "zip",
            "cabeca_para_baixo", "homonimos", "nome_social", "laboratorio_vr")

# Pessoas públicas e instituições reais: nenhum nome do corpus pode conter uma
# destas sequências de palavras. A lista é de propósito variada (política,
# esporte, música, medicina, hospitais, laboratórios e operadoras).
PROIBIDOS = (
    "Luiz Inacio Lula da Silva", "Lula", "Jair Messias Bolsonaro", "Bolsonaro",
    "Dilma Rousseff", "Rousseff", "Fernando Henrique Cardoso", "Michel Temer", "Temer",
    "Getulio Vargas", "Juscelino Kubitschek", "Kubitschek", "Tancredo Neves",
    "Fernando Collor", "Itamar Franco", "Pele", "Edson Arantes do Nascimento",
    "Neymar", "Ronaldinho", "Ayrton Senna", "Senna", "Zico", "Garrincha",
    "Marta Vieira da Silva", "Xuxa", "Silvio Santos", "Fausto Silva", "Anitta",
    "Gisele Bundchen", "Roberto Carlos", "Caetano Veloso", "Gilberto Gil",
    "Chico Buarque", "Ivete Sangalo", "Maria Bethania", "Elis Regina",
    "Machado de Assis", "Tiradentes", "Santos Dumont", "Paulo Freire",
    "Tarsila do Amaral", "Oscar Niemeyer", "Drauzio Varella", "Oswaldo Cruz",
    "Carlos Chagas", "Vital Brazil", "Adib Jatene", "Euryclides Zerbini", "Ivo Pitanguy",
    "Albert Einstein", "Einstein", "Sirio Libanes", "Fleury", "Hermes Pardini",
    "Pardini", "Dasa", "Delboni", "Lavoisier", "Sabin", "Camargo", "INCA",
    "Beneficencia Portuguesa", "Hospital das Clinicas", "Rede D Or", "Moinhos de Vento",
    "Unimed", "Amil", "Bradesco", "SulAmerica", "Hapvida", "NotreDame", "Intermedica",
    "Porto Seguro", "Samaritano", "Santa Casa", "Mater Dei", "Hcor", "Barretos",
    "Hospital de Amor", "Mae de Deus", "Cassi", "Geap", "Golden Cross", "Prevent Senior",
)


def _chave(texto: str) -> str:
    sem = unicodedata.normalize("NFKD", texto)
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return " " + " ".join(re.sub(r"[^A-Za-z0-9]+", " ", sem).upper().split()) + " "


def _gabarito(pasta: Path) -> dict:
    return json.loads((pasta / "gabarito.json").read_text(encoding="utf-8"))


def _bytes_dos_arquivos(pasta: Path, gab: dict) -> dict[str, bytes]:
    """Conteúdo de cada arquivo legível (id -> bytes), abrindo ZIP dentro de ZIP."""
    saida = {}
    for arq in gab["arquivos"]:
        partes = arq["caminho"].split("/")
        dados = (pasta / gab["origem"] / partes[0]).read_bytes()
        for parte in partes[1:]:
            with zipfile.ZipFile(io.BytesIO(dados)) as z:
                dados = z.read(parte)
        saida[arq["id"]] = dados
    return saida


def _textos(dados: bytes) -> list[str]:
    pdf = pdfium.PdfDocument(dados)
    try:
        return [pagina.get_textpage().get_text_range() for pagina in pdf]
    finally:
        pdf.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("corpus")
    return {c: gc.gerar(c, 1, raiz) for c in gc.CENARIOS}


def test_cenarios_sao_os_esperados():
    assert gc.CENARIOS == ESPERADOS


def test_cenario_desconhecido_e_recusado(tmp_path):
    with pytest.raises(ValueError):
        gc.gerar("inexistente", 1, tmp_path)


@pytest.mark.parametrize("cenario", ESPERADOS)
def test_origem_confere_com_o_gabarito(corpus, cenario):
    pasta = corpus[cenario]
    assert pasta.parent.is_dir() and pasta.name == f"{cenario}-1"
    gab = _gabarito(pasta)
    assert gab["cenario"] == cenario and gab["semente"] == 1 and gab["idioma"] == "pt-BR"
    assert gab["imagem"] is False and gab["ruido"] == 0.0
    origem = pasta / gab["origem"]
    nomes = sorted(p.relative_to(origem).as_posix() for p in origem.rglob("*") if p.is_file())
    assert nomes == sorted(a["nome"] for a in gab["origem_arquivos"])
    for arq in gab["origem_arquivos"]:
        caminho = origem / arq["nome"]
        assert hashlib.sha256(caminho.read_bytes()).hexdigest() == arq["sha256"]
        assert caminho.stat().st_size == arq["tamanho"]
        assert caminho.stat().st_mtime_ns == arq["mtime_ns"]


@pytest.mark.parametrize("cenario", [c for c in ESPERADOS if c != "senha"])
def test_paginas_do_gabarito_batem_com_o_pdf(corpus, cenario):
    pasta = corpus[cenario]
    gab = _gabarito(pasta)
    conteudos = _bytes_dos_arquivos(pasta, gab)
    for arq in gab["arquivos"]:
        dados = conteudos[arq["id"]]
        assert hashlib.sha256(dados).hexdigest() == arq["sha256"]
        textos = _textos(dados)
        assert len(textos) == arq["paginas"]
        for indice in arq["paginas_em_branco"]:
            assert textos[indice].strip() == ""
        for indice, texto in enumerate(textos):
            if indice not in arq["paginas_em_branco"]:
                assert len(texto.strip()) > 20, (cenario, arq["id"], indice)


@pytest.mark.parametrize("cenario", [c for c in ESPERADOS if c != "senha"])
def test_toda_pagina_esta_em_um_documento_ou_em_branco(corpus, cenario):
    gab = _gabarito(corpus[cenario])
    for arq in gab["arquivos"]:
        usadas = [p for d in gab["documentos"] if d["arquivo"] == arq["id"] for p in d["paginas"]]
        assert len(usadas) == len(set(usadas)), "página em dois documentos"
        assert set(usadas).isdisjoint(arq["paginas_em_branco"])
        assert sorted(set(usadas) | set(arq["paginas_em_branco"])) == list(range(arq["paginas"]))


@pytest.mark.parametrize("cenario", ESPERADOS)
def test_campos_de_cada_documento(corpus, cenario):
    gab = _gabarito(corpus[cenario])
    ids_pacientes = {p["id"] for p in gab["pacientes"]}
    ids_arquivos = {a["id"] for a in gab["arquivos"]}
    ids_documentos = {d["id"] for d in gab["documentos"]}
    for d in gab["documentos"]:
        assert d["arquivo"] in ids_arquivos
        assert d["categoria"] in (1, 2, 3, 4, 5)
        assert d["destino"] is None or d["destino"] in ids_pacientes
        if d["data"] is not None:
            valor = date.fromisoformat(d["data"]["valor"])
            assert d["data"]["precisao"] == "dia"
            assert date(2000, 1, 1) <= valor <= date(2026, 9, 1)
            assert d["data"]["valor"] not in d["datas_armadilha"]
        for armadilha in d["datas_armadilha"]:
            date.fromisoformat(armadilha)
        assert d["paciente"] is None or d["rotulado"] is True
        if d["pedido"]:
            assert d["categoria"] == 5
        for valor in d["valores"]:
            assert set(valor) == {"rotulo", "valor"} and re.search(r"\d", valor["valor"])
    for par in gab["repetidos"] + gab["seriados"]:
        assert len(par) == 2 and set(par) <= ids_documentos
    for p in gab["pacientes"]:
        date.fromisoformat(p["nascimento"])
        assert p["nome"] == p["nome"].upper() and p["nomes"]


@pytest.mark.parametrize("cenario", ESPERADOS)
def test_mesma_semente_gera_os_mesmos_bytes(tmp_path, cenario):
    a = gc.gerar(cenario, 7, tmp_path / "a")
    b = gc.gerar(cenario, 7, tmp_path / "b")
    ga, gb = _gabarito(a), _gabarito(b)
    for g in (ga, gb):
        for arq in g["origem_arquivos"]:
            arq.pop("mtime_ns")
    assert ga == gb
    for arq in ga["origem_arquivos"]:
        assert (a / "origem" / arq["nome"]).read_bytes() == (b / "origem" / arq["nome"]).read_bytes()


def test_semente_diferente_muda_o_conteudo(tmp_path):
    a = _gabarito(gc.gerar("pilha", 1, tmp_path))
    b = _gabarito(gc.gerar("pilha", 2, tmp_path))
    assert a["origem_arquivos"][0]["sha256"] != b["origem_arquivos"][0]["sha256"]
    assert a["pacientes"][0]["nome"] != b["pacientes"][0]["nome"]


def test_gerar_de_novo_substitui_a_pasta(tmp_path):
    pasta = gc.gerar("pilha", 1, tmp_path)
    (pasta / "origem" / "sobra.pdf").write_bytes(b"%PDF-")
    assert gc.gerar("pilha", 1, tmp_path) == pasta
    assert not (pasta / "origem" / "sobra.pdf").exists()


# --- nomes e instituições inventados ------------------------------------------

def _todos_os_nomes_do_corpus(corpus) -> list[str]:
    nomes = []
    for pasta in corpus.values():
        gab = _gabarito(pasta)
        nomes += [n for p in gab["pacientes"] for n in p["nomes"]]
        nomes += gab["medicos"]
        nomes += [d["instituicao"] for d in gab["documentos"] if d["instituicao"]]
    return nomes


def test_nenhum_nome_de_pessoa_publica_ou_instituicao_real(corpus):
    candidatos = list(ficticio.todos_os_textos()) + _todos_os_nomes_do_corpus(corpus)
    assert len(candidatos) > 100
    for texto in candidatos:
        chave = _chave(texto)
        for proibido in PROIBIDOS:
            assert _chave(proibido) not in chave, (texto, proibido)


def test_medicos_e_pacientes_nao_compartilham_sobrenome():
    pacientes = {_chave(s) for s in ficticio.SOBRENOMES}
    medicos = {_chave(s) for s in ficticio.SOBRENOMES_MEDICOS}
    assert pacientes.isdisjoint(medicos)


def test_texto_do_corpus_so_usa_nomes_das_listas_inventadas(corpus):
    sobrenomes = {_chave(s).strip() for s in ficticio.SOBRENOMES + ficticio.SOBRENOMES_MEDICOS}
    for pasta in corpus.values():
        gab = _gabarito(pasta)
        for p in gab["pacientes"]:
            ultimo = _chave(p["nome"]).split()[-1]
            assert ultimo in sobrenomes, p["nome"]


# --- modelos -------------------------------------------------------------------

def test_pelo_menos_tres_modelos_por_categoria():
    por_categoria = {}
    for modelo in MODELOS.values():
        por_categoria.setdefault(modelo.categoria, []).append(modelo.nome)
    assert sorted(por_categoria) == [1, 2, 3, 4, 5]
    assert all(len(nomes) >= 3 for nomes in por_categoria.values()), por_categoria


@pytest.mark.parametrize("nome", sorted(MODELOS))
def test_modelo_cabe_na_pagina_com_varias_sementes(nome):
    import random
    modelo = MODELOS[nome]
    for semente in range(12):
        rng = random.Random(semente)
        for sexo in modelo.sexos:
            pessoa = ficticio.pessoa(rng, sexo)
            doc = modelo.funcao(rng, pessoa, ficticio.data_exame(rng))
            assert doc.categoria == modelo.categoria and doc.folhas
        if modelo.sem_nome:
            doc = modelo.funcao(rng, None, ficticio.data_exame(rng))
            assert doc.paciente is None


def test_armadilhas_presentes_no_corpus(corpus):
    texto = ""
    for cenario, pasta in corpus.items():
        if cenario == "senha":
            continue
        gab = _gabarito(pasta)
        for dados in _bytes_dos_arquivos(pasta, gab).values():
            texto += "\n".join(_textos(dados)) + "\n"
    assert re.search(r"CRM-[A-Z]{2} \d{4,6}", texto)
    assert re.search(r"(?i)solicitante", texto)
    assert re.search(r"Data de nascimento|Nasc\.|DN:", texto)
    assert "Impresso em" in texto
    assert "Assinado eletronicamente por" in texto
    assert re.search(r"Respons.vel t.cnico", texto)
    assert re.search(r"Refer.ncias bibliogr.ficas|Bibliografia", texto)
    assert "CEP" in texto
    for rotulo in ("Valores de referência", "Valor de referência", "VR", "Referência",
                   "Intervalo de referência", "Faixa de referência"):
        assert rotulo in texto, rotulo
    assert re.search(r"Página \d de \d|Pág\. \d/\d", texto)


# --- cenários ------------------------------------------------------------------

def _docs(gab, **filtro):
    return [d for d in gab["documentos"] if all(d.get(k) == v for k, v in filtro.items())]


def test_pilha_tem_as_cinco_categorias_de_um_paciente(corpus):
    gab = _gabarito(corpus["pilha"])
    assert len(gab["pacientes"]) == 1 and len(gab["arquivos"]) == 1
    assert {d["categoria"] for d in gab["documentos"]} == {1, 2, 3, 4, 5}
    assert all(d["destino"] == "p1" for d in gab["documentos"])


def test_embaralhado_tem_documentos_fora_de_ordem_e_numerados(corpus):
    gab = _gabarito(corpus["embaralhado"])
    fora = _docs(gab, embaralhada=True)
    assert len(fora) >= 2
    for d in fora:
        assert d["numerada"] and len(d["paginas"]) >= 2 and d["paginas"] != sorted(d["paginas"])
    textos = _textos(_bytes_dos_arquivos(corpus["embaralhado"], gab)["a1"])
    for d in fora:
        total = len(d["paginas"])
        for ordem, indice in enumerate(d["paginas"], start=1):
            assert re.search(rf"(Página {ordem} de {total}|Pág\. {ordem}/{total}|\b{ordem}/{total}\b"
                             rf"|Folha {ordem}\b)", textos[indice])


def test_verso_branco_tem_uma_pagina_em_branco_depois_de_cada_frente(corpus):
    gab = _gabarito(corpus["verso_branco"])
    arq = gab["arquivos"][0]
    assert arq["paginas_em_branco"] == list(range(1, arq["paginas"], 2))


def test_dois_pacientes_e_anotacao_sem_dono(corpus):
    gab = _gabarito(corpus["dois_pacientes"])
    assert len(gab["pacientes"]) == 2
    assert {d["destino"] for d in gab["documentos"]} == {"p1", "p2", None}
    sem = _docs(gab, destino=None)
    assert len(sem) == 1 and sem[0]["paciente"] is None


def test_contaminacao_vai_para_o_outro_paciente(corpus):
    gab = _gabarito(corpus["contaminacao"])
    intrusos = _docs(gab, destino="p2")
    assert len(intrusos) == 1
    vizinhos = [d for d in gab["documentos"] if d["destino"] == "p1"]
    assert len(vizinhos) >= 3
    # O intruso fica entre documentos do primeiro paciente, com o mesmo título e
    # a mesma instituição de um deles.
    pagina = intrusos[0]["paginas"][0]
    assert any(p < pagina for d in vizinhos for p in d["paginas"])
    assert any(p > pagina for d in vizinhos for p in d["paginas"])
    assert any(d["titulo"] == intrusos[0]["titulo"] and d["instituicao"] == intrusos[0]["instituicao"]
               for d in vizinhos)


def test_anotacao_sem_nome_fica_com_o_unico_paciente(corpus):
    gab = _gabarito(corpus["anotacao_sem_nome"])
    sem = _docs(gab, paciente=None)
    assert {d["categoria"] for d in sem} == {3, 5}
    assert all(d["destino"] == "p1" for d in sem)
    nota = next(d for d in sem if d["categoria"] == 5)
    assert nota["valores"]


def test_xerox_repetido_tem_pares_com_o_mesmo_texto(corpus):
    pasta = corpus["xerox_repetido"]
    gab = _gabarito(pasta)
    assert len(gab["repetidos"]) >= 2
    textos = _textos(_bytes_dos_arquivos(pasta, gab)["a1"])
    por_id = {d["id"]: d for d in gab["documentos"]}
    for a, b in gab["repetidos"]:
        da, db = por_id[a], por_id[b]
        assert (da["titulo"], da["data"], da["instituicao"], da["valores"]) == \
               (db["titulo"], db["data"], db["instituicao"], db["valores"])
        # a cópia é deslocada e levemente girada: a ordem de extração muda, as palavras não
        assert [sorted(textos[i].split()) for i in da["paginas"]] == \
               [sorted(textos[i].split()) for i in db["paginas"]]


def test_seriado_tem_valores_diferentes(corpus):
    gab = _gabarito(corpus["seriado"])
    por_id = {d["id"]: d for d in gab["documentos"]}
    assert len(gab["seriados"]) >= 3
    mesma_data = 0
    for a, b in gab["seriados"]:
        da, db = por_id[a], por_id[b]
        assert da["titulo"] == db["titulo"] and da["instituicao"] == db["instituicao"]
        assert da["valores"] != db["valores"]
        mesma_data += da["data"] == db["data"]
    assert mesma_data >= 1, "falta o laudo retificado (mesma data, um valor diferente)"


def test_senha_nao_abre_sem_senha(corpus):
    pasta = corpus["senha"]
    gab = _gabarito(pasta)
    arq = gab["arquivos"][0]
    assert arq["ilegivel"] == "pdf_senha" and arq["paginas"] is None
    assert gab["documentos"] == [] and gab["pacientes"] == []
    with pytest.raises(pdfium.PdfiumError):
        pdfium.PdfDocument(str(pasta / "origem" / arq["caminho"]))


def test_zip_tem_zip_aninhado(corpus):
    pasta = corpus["zip"]
    gab = _gabarito(pasta)
    assert [a["nome"] for a in gab["origem_arquivos"]] == ["exames.zip"]
    caminhos = sorted(a["caminho"] for a in gab["arquivos"])
    assert caminhos == ["exames.zip/interno.zip/laboratorio.pdf", "exames.zip/laudos.pdf"]
    with zipfile.ZipFile(pasta / "origem" / "exames.zip") as z:
        assert sorted(z.namelist()) == ["interno.zip", "laudos.pdf"]


def test_cabeca_para_baixo_desenha_o_titulo_embaixo(corpus):
    pasta = corpus["cabeca_para_baixo"]
    gab = _gabarito(pasta)
    arq = gab["arquivos"][0]
    assert arq["rotacoes"] and set(arq["rotacoes"].values()) == {180}
    pdf = pdfium.PdfDocument(_bytes_dos_arquivos(pasta, gab)["a1"])
    for indice in range(arq["paginas"]):
        pagina = pdf[indice]
        texto = pagina.get_textpage()
        altura = pagina.get_size()[1]
        primeiro = texto.get_charbox(0)
        em_cima = primeiro[1] > altura / 2
        assert em_cima == (str(indice) not in arq["rotacoes"]), indice
    pdf.close()


def test_homonimos_tem_mesmo_nome_e_nascimentos_diferentes(corpus):
    gab = _gabarito(corpus["homonimos"])
    p1, p2 = gab["pacientes"]
    assert p1["nome"] == p2["nome"] and p1["nascimento"] != p2["nascimento"]
    assert len(gab["arquivos"]) == 3
    por_arquivo = {a["id"]: {d["destino"] for d in _docs(gab, arquivo=a["id"])} for a in gab["arquivos"]}
    assert por_arquivo == {"a1": {"p1"}, "a2": {"p2"}, "a3": {"p1"}}


def test_pessoa_separa_homonimos_e_junta_nome_social(corpus):
    # O avaliador distingue contaminação (outra pessoa) de pasta trocada da
    # mesma pessoa (nome social sozinho e par civil e social).
    for cenario in ESPERADOS:
        for p in _gabarito(corpus[cenario])["pacientes"]:
            assert p["pessoa"].startswith("h")
    p1, p2 = _gabarito(corpus["homonimos"])["pacientes"]
    assert p1["pessoa"] != p2["pessoa"]
    social, civil = _gabarito(corpus["nome_social"])["pacientes"]
    assert social["pessoa"] == civil["pessoa"]
    p1, p2 = _gabarito(corpus["contaminacao"])["pacientes"]
    assert p1["pessoa"] != p2["pessoa"]


def test_nome_social_sozinho_e_com_o_civil(corpus):
    pasta = corpus["nome_social"]
    gab = _gabarito(pasta)
    social, civil = gab["pacientes"]
    assert social["nome"] in civil["nomes"] and civil["nome"] != social["nome"]
    assert {d["destino"] for d in _docs(gab, arquivo="a1")} == {social["id"]}
    assert {d["destino"] for d in _docs(gab, arquivo="a2")} == {civil["id"]}
    textos = _textos(_bytes_dos_arquivos(pasta, gab)["a2"])
    assert any("Nome social" in t for t in textos)


def test_laboratorio_vr_planta_referencias_dentro_e_fora_do_laboratorio(corpus):
    pasta = corpus["laboratorio_vr"]
    gab = _gabarito(pasta)
    textos = "\n".join(_textos(_bytes_dos_arquivos(pasta, gab)["a1"]))
    dentro = [d for d in gab["documentos"] if d["categoria"] == 3 and d["referencias"]]
    fora = [d for d in gab["documentos"] if d["categoria"] != 3 and d["referencias"]]
    assert len(dentro) >= 3 and fora
    for d in dentro + fora:
        for ref in d["referencias"]:
            assert ref in textos, ref
    for rotulo in ("Valores de referência", "Valor de referência", "VR:", "Referência:",
                   "Intervalo de referência", "Faixa de referência"):
        assert rotulo in textos, rotulo


def test_datas_ambiguas_suficientes_no_corpus_padrao(tmp_path):
    total = 0
    for semente in gc.SEMENTES_PADRAO:
        for cenario in gc.CENARIOS:
            gab = _gabarito(gc.gerar(cenario, semente, tmp_path))
            for d in gab["documentos"]:
                if d["data"]:
                    dia = date.fromisoformat(d["data"]["valor"])
                    total += dia.day <= 12 and dia.day != dia.month
    assert total >= 200


# --- rasterização e variantes com ruído ----------------------------------------

@pytest.fixture(scope="module")
def pdf_pequeno(tmp_path_factory):
    """Duas páginas (uma de cabeça para baixo): rasterizar o corpus inteiro seria lento."""
    import random
    from corpus.folha import Impressao, pdf_em_bytes
    rng = random.Random(3)
    doc = MODELOS["bioquimica"].funcao(rng, ficticio.pessoa(rng, "F"), date(2026, 4, 2))
    caminho = tmp_path_factory.mktemp("pdf") / "laudo.pdf"
    caminho.write_bytes(pdf_em_bytes([Impressao(doc.folhas[0]), Impressao(doc.folhas[0], rotacao=180)]))
    return caminho


def _pixels(caminho: Path):
    import numpy as np
    pdf = pdfium.PdfDocument(str(caminho))
    try:
        return np.asarray(pdf[0].render(scale=0.5).to_pil().convert("L"), dtype=np.int16)
    finally:
        pdf.close()


def test_rasterizar_gera_pdf_so_de_imagem(pdf_pequeno, tmp_path):
    saida = gc.rasterizar(pdf_pequeno, tmp_path / "limpo.pdf", 0.0, 1)
    assert saida == tmp_path / "limpo.pdf"
    original, imagem = pdfium.PdfDocument(str(pdf_pequeno)), pdfium.PdfDocument(str(saida))
    assert len(original) == len(imagem) == 2
    for a, b in zip(original, imagem):
        assert b.get_textpage().get_text_range().strip() == ""
        assert abs(a.get_size()[0] - b.get_size()[0]) < 2 and abs(a.get_size()[1] - b.get_size()[1]) < 2
    original.close()
    imagem.close()
    assert gc.rasterizar(pdf_pequeno, tmp_path, 0.0, 1) == tmp_path / "laudo.pdf"


def test_rasterizar_e_deterministico_e_o_ruido_degrada(pdf_pequeno, tmp_path):
    a = gc.rasterizar(pdf_pequeno, tmp_path / "a.pdf", 0.03, 5).read_bytes()
    b = gc.rasterizar(pdf_pequeno, tmp_path / "b.pdf", 0.03, 5).read_bytes()
    c = gc.rasterizar(pdf_pequeno, tmp_path / "c.pdf", 0.03, 6).read_bytes()
    assert a == b and a != c
    limpo = _pixels(gc.rasterizar(pdf_pequeno, tmp_path / "l.pdf", 0.0, 5))
    diferencas = []
    for ruido in (0.01, 0.03, 0.05):
        sujo = _pixels(gc.rasterizar(pdf_pequeno, tmp_path / f"s{ruido}.pdf", ruido, 5))
        diferencas.append(float((abs(limpo - sujo) > 40).mean()))
    assert 0.001 < diferencas[0] < diferencas[1] < diferencas[2]


def test_rasterizar_recusa_ruido_fora_da_faixa(pdf_pequeno, tmp_path):
    with pytest.raises(ValueError):
        gc.rasterizar(pdf_pequeno, tmp_path / "x.pdf", 0.5, 1)


def test_variante_com_ruido_atualiza_o_gabarito(corpus):
    for cenario in ("zip", "senha"):
        pasta = corpus[cenario]
        variante = gc.variante(pasta, 0.01, 3)
        assert variante.name == f"{pasta.name}-r01"
        gab, original = _gabarito(variante), _gabarito(pasta)
        assert gab["imagem"] is True and gab["ruido"] == 0.01
        assert gab["documentos"] == original["documentos"]
        for arq in gab["origem_arquivos"]:
            caminho = variante / "origem" / arq["nome"]
            assert hashlib.sha256(caminho.read_bytes()).hexdigest() == arq["sha256"]
        if cenario == "senha":
            assert gab["arquivos"] == original["arquivos"]
            continue
        conteudos = _bytes_dos_arquivos(variante, gab)
        for arq, antes in zip(gab["arquivos"], original["arquivos"]):
            assert arq["sha256"] != antes["sha256"]
            assert hashlib.sha256(conteudos[arq["id"]]).hexdigest() == arq["sha256"]
            assert all(t.strip() == "" for t in _textos(conteudos[arq["id"]]))


def test_linha_de_comando_gera_limpo_e_com_ruido(tmp_path, capsys):
    codigo = gc.main(["--saida", str(tmp_path), "--cenarios", "zip", "senha",
                      "--sementes", "4", "9", "--ruidos", "0.03"])
    assert codigo == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["senha-4", "senha-4-r03", "senha-9",
                                                          "zip-4", "zip-4-r03", "zip-9"]
    assert "zip-4-r03" in capsys.readouterr().out
