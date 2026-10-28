"""Testes da régua (tools/avaliar.py).

A régua é testada com uma árvore de destino ideal, montada aqui a partir do
gabarito, e com cópias dela estragadas de propósito: a árvore ideal tem de dar
100% em todos os critérios, e cada violação plantada tem de baixar o critério
certo. A montagem é independente do avaliador, para um não esconder o erro do
outro.
"""
import io
import json
import os
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
import pytest

import avaliar as av
import gerar_corpus as gc
from scan_to_text.pacote import carregar

DIA = "2026-10-01"
TEXTOS = carregar("pt-BR")["textos"]
AVISO = "## " + TEXTOS["markdown"]["aviso"]
SISTEMA = "_scan-to-text"


# --- árvore ideal ----------------------------------------------------------------

def _gab(pasta: Path) -> dict:
    return json.loads((pasta / "gabarito.json").read_text(encoding="utf-8"))


def _bytes(origem: Path, caminho: str) -> bytes:
    partes = caminho.split("/")
    dados = (origem / partes[0]).read_bytes()
    for parte in partes[1:]:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            dados = z.read(parte)
    return dados


def _so_paginas(dados: bytes, paginas: list[int]) -> bytes:
    fonte, novo = pdfium.PdfDocument(dados), pdfium.PdfDocument.new()
    try:
        novo.import_pages(fonte, list(paginas))
        saida = io.BytesIO()
        novo.save(saida)
        return saida.getvalue()
    finally:
        novo.close()
        fonte.close()


def _md(d: dict) -> str:
    linhas = [f"# {d['titulo']}", ""]
    if d["paciente"]:
        linhas += [f"Paciente: {d['paciente']}  ", ""]
    linhas += [f"{v['rotulo']}: {v['valor']}  " for v in d["valores"]]
    if d["categoria"] != 3:
        linhas += [f"Referência: {r}  " for r in d["referencias"]]
    return "\n".join(linhas + ["", AVISO, ""])


def _origem(gab: dict, arquivo: dict, paginas: list[int]) -> dict:
    """Uma entrada de `origens[]` como `escrever/arvore.py` grava.

    O SHA-256 é o do arquivo de origem como chegou (o ZIP inteiro); dentro de
    um ZIP, só o caminho separa um arquivo do outro.
    """
    topo = arquivo["caminho"].split("/")[0]
    sha = next(o["sha256"] for o in gab["origem_arquivos"] if o["nome"] == topo)
    return {"caminho": arquivo["caminho"], "sha256": sha, "paginas": list(paginas)}


def _gravar_json(arquivo: Path, dados: dict) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")


def montar_ideal(cenario: Path, destino: Path) -> Path:
    """A árvore que o app deveria gravar para o cenário, com MD resumido."""
    gab = _gab(cenario)
    origem = cenario / gab["origem"]
    arquivos = {a["id"]: a for a in gab["arquivos"]}
    pacientes = {p["id"]: p for p in gab["pacientes"]}
    docs = {d["id"]: d for d in gab["documentos"]}
    copias = {b for _, b in gab["repetidos"]}
    pastas: dict = {}

    def pasta(chave, nome: str, paciente: dict | None) -> dict:
        if chave not in pastas:
            usados, final, n = {p["nome"] for p in pastas.values()}, nome, 2
            while final in usados:
                final, n = f"{nome} ({n})", n + 1
            pastas[chave] = {"nome": final, "paciente": paciente, "documentos": []}
            (destino / final).mkdir(parents=True)
        return pastas[chave]

    def do_paciente(pid: str) -> dict:
        p = pacientes[pid]
        return pasta(pid, f"{p['nome']} - {DIA}", {"exibido": p["nome"], "variantes": p["nomes"],
                                                  "nascimento": p["nascimento"], "confianca": 1.0})

    def desconhecido(arquivo: dict) -> dict:
        nome = Path(arquivo["caminho"].split("/")[0]).stem.upper()
        return pasta(("?", arquivo["caminho"].split("/")[0]),
                     f"{TEXTOS['pastas']['desconhecido']} - {DIA} - {nome}", None)

    for d in gab["documentos"]:
        if d["id"] in copias:
            continue
        arq = arquivos[d["arquivo"]]
        alvo = do_paciente(d["destino"]) if d["destino"] else desconhecido(arq)
        categoria = TEXTOS["pastas"]["categorias"][d["categoria"]]
        base = f"{categoria}/{d['data']['valor'] if d['data'] else 'SEM DATA'} - {d['id'].upper()}"
        raiz = destino / alvo["nome"]
        (raiz / categoria).mkdir(exist_ok=True)
        (raiz / f"{base}.md").write_text(_md(d), encoding="utf-8")
        (raiz / f"{base}.pdf").write_bytes(_so_paginas(_bytes(origem, arq["caminho"]), d["paginas"]))
        data = d["data"] or {"valor": None, "precisao": None}
        alvo["documentos"].append({
            "id": d["id"], "categoria": d["categoria"], "titulo": d["titulo"],
            "instituicao": d["instituicao"], "md": f"{base}.md", "conferencia": f"{base}.pdf",
            "data": {**data, "papel": "realizacao" if d["data"] else None},
            "origens": [_origem(gab, arq, d["paginas"])],
            "duplicatas": [_origem(gab, arquivos[docs[b]["arquivo"]], docs[b]["paginas"])
                           for a, b in gab["repetidos"] if a == d["id"]],
            "paginas_em_branco": [], "paginas_leitura_incerta": []})

    for arq in gab["arquivos"]:
        if arq["paginas_em_branco"]:
            item = next(e for p in pastas.values() for e in p["documentos"]
                        if e["origens"][0]["caminho"] == arq["caminho"])
            item["paginas_em_branco"] = list(arq["paginas_em_branco"])

    for item in gab["origem_arquivos"]:
        dentro = [a for a in gab["arquivos"]
                  if a["caminho"] == item["nome"] or a["caminho"].startswith(item["nome"] + "/")]
        dados = (origem / item["nome"]).read_bytes()
        destinos = {d["destino"] for d in gab["documentos"] if d["arquivo"] in {a["id"] for a in dentro}}
        if any(a["ilegivel"] for a in dentro) or destinos == {None}:
            raiz = destino / desconhecido(dentro[0])["nome"]
            if any(a["ilegivel"] for a in dentro):
                (raiz / TEXTOS["arquivos"]["leia_me"]).write_text(TEXTOS["leia_me"]["senha"] + "\n",
                                                                 encoding="utf-8")
        elif len(destinos) == 1:
            raiz = destino / do_paciente(destinos.pop())["nome"]
        else:
            mistos = destino / TEXTOS["pastas"]["mais_de_um_paciente"]
            mistos.mkdir(parents=True, exist_ok=True)
            (mistos / item["nome"]).write_bytes(dados)
            for pid in sorted(destinos - {None}):
                for a in dentro:
                    paginas = sorted(p for d in gab["documentos"]
                                     if d["arquivo"] == a["id"] and d["destino"] == pid for p in d["paginas"])
                    sufixo = TEXTOS["arquivos"]["paginas_deste_paciente"]
                    nome = f"{Path(a['caminho']).stem} {sufixo}.pdf"
                    alvo = destino / do_paciente(pid)["nome"] / TEXTOS["pastas"]["originais"] / nome
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    alvo.write_bytes(_so_paginas(_bytes(origem, a["caminho"]), paginas))
            continue
        alvo = raiz / TEXTOS["pastas"]["originais"] / item["nome"]
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(dados)

    for p in pastas.values():
        raiz = destino / p["nome"]
        _gravar_json(raiz / SISTEMA / "manifesto.json", {
            "versao_esquema": 1, "pasta": p["nome"], "primeiro_processamento": DIA,
            "ultima_atualizacao": DIA, "paciente": p["paciente"], "documentos": p["documentos"],
            "originais": [], "versoes": {}})
        if p["paciente"]:
            (raiz / TEXTOS["arquivos"]["completo"]).write_text(
                f"# {p['paciente']['exibido']}\n\n{AVISO}\n\n## {TEXTOS['markdown']['indice']}\n\n---\n",
                encoding="utf-8")
    return destino


# --- apoio aos testes ------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("corpus")
    pastas = {}
    for cenario in gc.CENARIOS:
        pasta = gc.gerar(cenario, 1, raiz)
        montar_ideal(pasta, pasta / av.DESTINO)
        pastas[cenario] = pasta
    return pastas


@pytest.fixture
def copia(corpus, tmp_path):
    def fazer(cenario: str) -> Path:
        alvo = tmp_path / corpus[cenario].name
        shutil.copytree(corpus[cenario], alvo)
        return alvo
    return fazer


def _medir(pasta: Path, **kw) -> dict:
    return av.medir(pasta / av.DESTINO, _gab(pasta), origem=pasta / "origem", **kw)


def _falhou(medidas: dict, chave: str) -> bool:
    m = medidas[chave]
    return m.casos > 0 and m.acertos < m.casos


def _so_falham(medidas: dict, *chaves: str) -> None:
    falhas = {k for k in medidas if _falhou(medidas, k)}
    assert falhas == set(chaves), {k: medidas[k].falhas for k in falhas}


def _manifestos(destino: Path) -> dict[str, Path]:
    return {p.parent.parent.name: p for p in sorted(destino.glob(f"*/{SISTEMA}/manifesto.json"))}


def _ler(arquivo: Path) -> dict:
    return json.loads(arquivo.read_text(encoding="utf-8"))


def _achar(destino: Path, did: str) -> tuple[Path, dict, dict]:
    for nome, arquivo in _manifestos(destino).items():
        m = _ler(arquivo)
        for item in m["documentos"]:
            if item["id"] == did:
                return destino / nome, m, item
    raise LookupError(did)


def _alterar(destino: Path, did: str, funcao) -> Path:
    pasta, m, item = _achar(destino, did)
    funcao(item)
    _gravar_json(pasta / SISTEMA / "manifesto.json", m)
    return pasta


def _pasta_de(destino: Path, gab: dict, pid: str) -> Path:
    p = next(p for p in gab["pacientes"] if p["id"] == pid)
    for nome, arquivo in _manifestos(destino).items():
        paciente = _ler(arquivo)["paciente"]
        if paciente and paciente["exibido"] == p["nome"] and paciente["nascimento"] == p["nascimento"]:
            return destino / nome
    raise LookupError(pid)


def _nova_desconhecido(destino: Path) -> Path:
    pasta = destino / f"DESCONHECIDO - {DIA} - EXTRA"
    _gravar_json(pasta / SISTEMA / "manifesto.json", {
        "versao_esquema": 1, "pasta": pasta.name, "primeiro_processamento": DIA,
        "ultima_atualizacao": DIA, "paciente": None, "documentos": [], "originais": [], "versoes": {}})
    return pasta


def _mover(destino: Path, did: str, para: Path) -> None:
    """Move o item do documento (manifesto, MD e conferência) para outra pasta."""
    pasta, m, item = _achar(destino, did)
    m["documentos"].remove(item)
    _gravar_json(pasta / SISTEMA / "manifesto.json", m)
    for chave in ("md", "conferencia"):
        alvo = para / item[chave]
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(pasta / item[chave], alvo)
    outro = _ler(para / SISTEMA / "manifesto.json")
    outro["documentos"].append(item)
    _gravar_json(para / SISTEMA / "manifesto.json", outro)


def _md_de(destino: Path, did: str) -> Path:
    pasta, _, item = _achar(destino, did)
    return pasta / item["md"]


# --- a árvore ideal dá 100% ------------------------------------------------------

@pytest.mark.parametrize("cenario", gc.CENARIOS)
def test_arvore_ideal_da_100_em_tudo(corpus, cenario):
    medidas = _medir(corpus[cenario])
    assert set(medidas) == set(av.METAS)
    _so_falham(medidas)
    valores = av.avaliar(corpus[cenario] / av.DESTINO, _gab(corpus[cenario]),
                         origem=corpus[cenario] / "origem")
    assert valores and set(valores.values()) == {1.0}


def test_variante_so_de_imagem_tambem_da_100(corpus, tmp_path):
    # A impressão de página tem de reconhecer página rasterizada copiada para
    # outro PDF, ou a variante com ruído não mede contaminação.
    pasta = gc.variante(shutil.copytree(corpus["dois_pacientes"], tmp_path / "dois_pacientes-1"), 0.01, 1)
    montar_ideal(pasta, pasta / av.DESTINO)
    medidas = _medir(pasta)
    assert medidas["c3_paginas_na_pasta_certa"].casos > 0
    _so_falham(medidas)
    gab, destino = _gab(pasta), pasta / av.DESTINO
    dados = (pasta / "origem" / gab["origem_arquivos"][0]["nome"]).read_bytes()
    de_p2 = next(d for d in gab["documentos"] if d["destino"] == "p2")["paginas"]
    pdf = next((_pasta_de(destino, gab, "p1") / TEXTOS["pastas"]["originais"]).glob("*.pdf"))
    pdf.write_bytes(_so_paginas(dados, de_p2))
    _so_falham(_medir(pasta), "c3_paginas_na_pasta_certa")


def test_todo_criterio_tem_casos_no_corpus(corpus):
    com_casos = set()
    for pasta in corpus.values():
        com_casos |= {k for k, m in _medir(pasta).items() if m.casos}
    assert com_casos == set(av.METAS) - {"c9_determinismo", "c19_lista_coerente"}


def test_avaliar_so_devolve_criterio_com_casos(corpus):
    pasta = corpus["pilha"]
    sem_origem = av.avaliar(pasta / av.DESTINO, _gab(pasta))
    assert "c2_origem_intocada" not in sem_origem and "c9_determinismo" not in sem_origem
    assert "c1_nada_se_perde" in sem_origem
    assert all(0.0 <= v <= 1.0 for v in sem_origem.values())


# --- critério 1: nada se perde ---------------------------------------------------

def test_pagina_fora_do_manifesto_e_perdida(copia):
    pasta = copia("embaralhado")
    did = _gab(pasta)["documentos"][0]["id"]
    _alterar(pasta / av.DESTINO, did, lambda item: item["origens"][0]["paginas"].pop())
    medidas = _medir(pasta)
    assert any("perdida" in f for f in medidas["c1_nada_se_perde"].falhas)
    _so_falham(medidas, "c1_nada_se_perde", "c8_ordem_das_paginas")


def test_pagina_em_dois_documentos(copia):
    pasta = copia("pilha")
    d1, d2 = _gab(pasta)["documentos"][:2]
    _alterar(pasta / av.DESTINO, d2["id"], lambda item: item["origens"][0]["paginas"].append(d1["paginas"][0]))
    medidas = _medir(pasta)
    assert any("repetida" in f for f in medidas["c1_nada_se_perde"].falhas)
    assert _falhou(medidas, "c1_nada_se_perde")


def test_verso_em_branco_sem_registro_e_perdido(copia):
    pasta = copia("verso_branco")
    destino = pasta / av.DESTINO
    for arquivo in _manifestos(destino).values():
        m = _ler(arquivo)
        for item in m["documentos"]:
            item["paginas_em_branco"] = []
        _gravar_json(arquivo, m)
    _so_falham(_medir(pasta), "c1_nada_se_perde")


def test_branco_registrado_por_sha256_vale(copia):
    pasta = copia("verso_branco")
    destino = pasta / av.DESTINO
    arq = _gab(pasta)["arquivos"][0]
    for arquivo in _manifestos(destino).values():
        m = _ler(arquivo)
        for item in m["documentos"]:
            item["paginas_em_branco"] = []
        m["paginas_em_branco"] = [{"sha256": arq["sha256"], "paginas": arq["paginas_em_branco"]}]
        _gravar_json(arquivo, m)
    _so_falham(_medir(pasta))


def _referencias_de_origem(destino: Path, funcao) -> None:
    """Aplica `funcao` a toda entrada de `origens[]` e `duplicatas[]` dos manifestos."""
    for arquivo in _manifestos(destino).values():
        m = _ler(arquivo)
        for item in m["documentos"]:
            for ref in item["origens"] + item["duplicatas"]:
                funcao(ref)
        _gravar_json(arquivo, m)


def test_zip_ligado_pelo_caminho_com_o_sha_do_zip(corpus):
    # Os dois PDFs de dentro do ZIP têm, no manifesto, o SHA-256 do ZIP, e os
    # dois têm página 0: só o caminho separa uma página da outra.
    pasta = corpus["zip"]
    gab = _gab(pasta)
    shas = set()
    _referencias_de_origem(pasta / av.DESTINO, lambda ref: shas.add(ref["sha256"]))
    assert shas == {gab["origem_arquivos"][0]["sha256"]}
    assert len(gab["arquivos"]) == 2 and all(any(0 in d["paginas"] for d in gab["documentos"]
                                                 if d["arquivo"] == a["id"]) for a in gab["arquivos"])
    medidas = _medir(pasta)
    assert medidas["c1_nada_se_perde"].casos == sum(a["paginas"] for a in gab["arquivos"])
    _so_falham(medidas)


def test_sha_do_proprio_arquivo_lido_tambem_liga(copia):
    pasta = copia("zip")
    sha_de = {a["caminho"]: a["sha256"] for a in _gab(pasta)["arquivos"]}
    _referencias_de_origem(pasta / av.DESTINO, lambda ref: ref.update(sha256=sha_de[ref["caminho"]]))
    _so_falham(_medir(pasta))


def test_sha_sem_caminho_so_liga_quando_aponta_um_arquivo(copia):
    pasta = copia("pilha")
    _referencias_de_origem(pasta / av.DESTINO, lambda ref: ref.pop("caminho"))
    _so_falham(_medir(pasta))
    pasta = copia("zip")
    _referencias_de_origem(pasta / av.DESTINO, lambda ref: ref.pop("caminho"))
    medidas = _medir(pasta)
    assert medidas["c1_nada_se_perde"].casos > 0 and medidas["c1_nada_se_perde"].acertos == 0


def test_md_que_nao_existe_nao_entrega_a_pagina(copia):
    pasta = copia("pilha")
    _md_de(pasta / av.DESTINO, "d1").unlink()
    medidas = _medir(pasta)
    assert _falhou(medidas, "c1_nada_se_perde")


def test_original_que_sumiu(copia):
    pasta = copia("pilha")
    for arquivo in (pasta / av.DESTINO).glob(f"*/{TEXTOS['pastas']['originais']}/*"):
        arquivo.unlink()
    _so_falham(_medir(pasta), "c1_originais_preservados")


def test_arquivo_ilegivel_precisa_de_leia_me(copia):
    pasta = copia("senha")
    _so_falham(_medir(pasta))
    for leia_me in (pasta / av.DESTINO).glob(f"*/{TEXTOS['arquivos']['leia_me']}"):
        leia_me.unlink()
    _so_falham(_medir(pasta), "c1_originais_preservados")


def test_manifesto_ilegivel_nao_derruba_a_regua(copia):
    pasta = copia("pilha")
    for arquivo in _manifestos(pasta / av.DESTINO).values():
        arquivo.write_bytes(b"{nao e json")
    medidas = _medir(pasta)
    assert medidas["c1_nada_se_perde"].acertos == 0 and _falhou(medidas, "c1_nada_se_perde")


# --- critério 2: origem intocada -------------------------------------------------

def test_origem_com_bytes_alterados(copia):
    pasta = copia("pilha")
    arquivo = pasta / "origem" / "scan001.pdf"
    estado = arquivo.stat()
    arquivo.write_bytes(arquivo.read_bytes() + b"\n")
    os.utime(arquivo, ns=(estado.st_atime_ns, estado.st_mtime_ns))
    _so_falham(_medir(pasta), "c2_origem_intocada")


def test_origem_com_data_alterada(copia):
    pasta = copia("pilha")
    arquivo = pasta / "origem" / "scan001.pdf"
    estado = arquivo.stat()
    os.utime(arquivo, ns=(estado.st_atime_ns, estado.st_mtime_ns + 10 ** 9))
    _so_falham(_medir(pasta), "c2_origem_intocada")


def test_arquivo_novo_na_origem(copia):
    pasta = copia("pilha")
    (pasta / "origem" / "scan001.pdf.parcial").write_bytes(b"x")
    medidas = _medir(pasta)
    assert any("novo" in f for f in medidas["c2_origem_intocada"].falhas)
    _so_falham(medidas, "c2_origem_intocada")


# --- critério 3: paciente certo --------------------------------------------------

def test_contaminante_na_pasta_do_primeiro(copia):
    pasta = copia("contaminacao")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    intruso = next(d for d in gab["documentos"] if d["destino"] == "p2")
    _mover(destino, intruso["id"], _pasta_de(destino, gab, "p1"))
    medidas = _medir(pasta)
    assert medidas["c3_paciente_certo"].falhas == [f"{intruso['id']}:pasta_de_outra_pessoa"]
    _so_falham(medidas, "c3_paciente_certo", "c3_paginas_na_pasta_certa", "c4_nome_achado")


def test_contaminante_em_desconhecido_nao_e_contaminacao(copia):
    pasta = copia("contaminacao")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    intruso = next(d for d in gab["documentos"] if d["destino"] == "p2")
    _mover(destino, intruso["id"], _nova_desconhecido(destino))
    _so_falham(_medir(pasta), "c4_nome_achado")


def test_anotacao_sem_dono_na_pasta_de_um_paciente(copia):
    pasta = copia("dois_pacientes")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    sem_dono = next(d for d in gab["documentos"] if d["destino"] is None)
    _mover(destino, sem_dono["id"], _pasta_de(destino, gab, "p1"))
    medidas = _medir(pasta)
    assert medidas["c3_paciente_certo"].falhas == [f"{sem_dono['id']}:sem_dono_em_pasta_de_paciente"]
    _so_falham(medidas, "c3_paciente_certo", "c3_paginas_na_pasta_certa")


def test_homonimos_na_mesma_pasta_sem_nascimento(copia):
    pasta = copia("homonimos")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    primeira = _pasta_de(destino, gab, "p1")
    for d in gab["documentos"]:
        if d["destino"] == "p2":
            _mover(destino, d["id"], primeira)
    m = _ler(primeira / SISTEMA / "manifesto.json")
    m["paciente"]["nascimento"] = None
    _gravar_json(primeira / SISTEMA / "manifesto.json", m)
    medidas = _medir(pasta)
    assert medidas["c3_paciente_certo"].falhas
    assert all(f.endswith("pasta_misturada") for f in medidas["c3_paciente_certo"].falhas)
    assert _falhou(medidas, "c4_nome_achado")


def test_homonimo_com_nascimento_de_outro(copia):
    pasta = copia("homonimos")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    d = next(d for d in gab["documentos"] if d["destino"] == "p2")
    _mover(destino, d["id"], _pasta_de(destino, gab, "p1"))
    medidas = _medir(pasta)
    assert medidas["c3_paciente_certo"].falhas == [f"{d['id']}:pasta_de_outra_pessoa"]


def test_homonimo_com_nascimento_fora_do_gabarito(copia):
    pasta = copia("homonimos")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    arquivo = _pasta_de(destino, gab, "p1") / SISTEMA / "manifesto.json"
    m = _ler(arquivo)
    m["paciente"]["nascimento"] = "1901-02-03"
    _gravar_json(arquivo, m)
    medidas = _medir(pasta)
    assert medidas["c3_paciente_certo"].falhas == [
        f"{d['id']}:pasta_de_outra_pessoa" for d in gab["documentos"] if d["destino"] == "p1"]
    assert _falhou(medidas, "c4_nome_achado")


def test_nome_com_espaco_perdido_nao_e_pasta_de_outra_pessoa(copia):
    pasta = copia("anotacao_sem_nome")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    original = _pasta_de(destino, gab, "p1")
    m = _ler(original / SISTEMA / "manifesto.json")
    colado = m["paciente"]["exibido"].replace(" ", "", 1)
    nova = original.rename(destino / f"{colado} - {DIA}")
    m["paciente"].update(exibido=colado, variantes=[colado])
    m["pasta"] = nova.name
    _gravar_json(nova / SISTEMA / "manifesto.json", m)
    _so_falham(_medir(pasta))


def test_original_misturado_na_pasta_de_um_paciente(copia):
    pasta = copia("dois_pacientes")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    nome = gab["origem_arquivos"][0]["nome"]
    misto = destino / TEXTOS["pastas"]["mais_de_um_paciente"] / nome
    shutil.copy(misto, _pasta_de(destino, gab, "p1") / TEXTOS["pastas"]["originais"] / nome)
    _so_falham(_medir(pasta), "c3_paginas_na_pasta_certa")


def test_pdf_com_pagina_de_outro_paciente(copia):
    pasta = copia("dois_pacientes")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    dados = (pasta / "origem" / gab["origem_arquivos"][0]["nome"]).read_bytes()
    de_p2 = next(d for d in gab["documentos"] if d["destino"] == "p2")["paginas"]
    pdfs = sorted((_pasta_de(destino, gab, "p1") / TEXTOS["pastas"]["originais"]).glob("*.pdf"))
    assert len(pdfs) == 1
    pdfs[0].write_bytes(_so_paginas(dados, [0, *de_p2]))
    medidas = _medir(pasta)
    assert len(medidas["c3_paginas_na_pasta_certa"].falhas) == len(de_p2)
    _so_falham(medidas, "c3_paginas_na_pasta_certa")


def test_sem_origem_so_confere_original_inteiro(copia):
    pasta = copia("dois_pacientes")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    medidas = av.medir(destino, gab)
    assert medidas["c3_paginas_na_pasta_certa"].casos == 0
    nome = gab["origem_arquivos"][0]["nome"]
    shutil.copy(destino / TEXTOS["pastas"]["mais_de_um_paciente"] / nome,
                _pasta_de(destino, gab, "p2") / TEXTOS["pastas"]["originais"] / nome)
    assert _falhou(av.medir(destino, gab), "c3_paginas_na_pasta_certa")


def test_nome_social_na_pasta_da_mesma_pessoa_nao_e_contaminacao(copia):
    pasta = copia("nome_social")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    d = next(d for d in gab["documentos"] if d["destino"] == "p2")
    _mover(destino, d["id"], _pasta_de(destino, gab, "p1"))
    _so_falham(_medir(pasta), "c4_nome_achado")


# --- critério 4: nome --------------------------------------------------------------

def test_medico_escolhido_como_paciente(copia):
    pasta = copia("pilha")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    medico = gab["medicos"][0]
    nova = _pasta_de(destino, gab, "p1").rename(destino / f"{medico} - {DIA}")
    arquivo = nova / SISTEMA / "manifesto.json"
    m = _ler(arquivo)
    m["paciente"].update(exibido=medico, variantes=[medico])
    m["pasta"] = nova.name
    _gravar_json(arquivo, m)
    medidas = _medir(pasta)
    assert medidas["c4_sem_medico_como_paciente"].acertos == 0
    _so_falham(medidas, "c4_sem_medico_como_paciente", "c4_nome_achado", "c3_paciente_certo",
               "c3_paginas_na_pasta_certa")


def test_variante_com_sobrenome_de_medico(copia):
    pasta = copia("pilha")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    arquivo = _pasta_de(destino, gab, "p1") / SISTEMA / "manifesto.json"
    m = _ler(arquivo)
    m["paciente"]["variantes"].append(gab["medicos"][-1])
    _gravar_json(arquivo, m)
    _so_falham(_medir(pasta), "c4_sem_medico_como_paciente")


def test_nome_com_erro_de_ocr_ainda_e_o_paciente(copia):
    pasta = copia("pilha")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    arquivo = _pasta_de(destino, gab, "p1") / SISTEMA / "manifesto.json"
    m = _ler(arquivo)
    nome = m["paciente"]["exibido"]
    m["paciente"].update(exibido=nome[:-1] + ("X" if nome[-1] != "X" else "Y"), variantes=[])
    _gravar_json(arquivo, m)
    _so_falham(_medir(pasta))


# --- critério 5: datas -------------------------------------------------------------

def _ambigua(gab: dict) -> dict:
    return next(d for d in gab["documentos"] if d["data"]
                and date.fromisoformat(d["data"]["valor"]).day <= 12
                and date.fromisoformat(d["data"]["valor"]).day != date.fromisoformat(d["data"]["valor"]).month)


def test_troca_de_dia_e_mes(copia):
    pasta = copia("pilha")
    d = _ambigua(_gab(pasta))
    certo = date.fromisoformat(d["data"]["valor"])
    trocado = date(certo.year, certo.day, certo.month).isoformat()
    _alterar(pasta / av.DESTINO, d["id"], lambda item: item["data"].update(valor=trocado))
    _so_falham(_medir(pasta), "c5_sem_troca_dia_mes", "c5_data_certa")


def test_data_de_armadilha_como_data_do_exame(copia):
    for cenario in gc.CENARIOS:
        pasta = copia(cenario)
        d = next((d for d in _gab(pasta)["documentos"] if d["datas_armadilha"] and d["data"]), None)
        if d:
            break
    _alterar(pasta / av.DESTINO, d["id"],
             lambda item: item["data"].update(valor=d["datas_armadilha"][0], precisao="dia"))
    medidas = _medir(pasta)
    assert _falhou(medidas, "c5_sem_data_armadilha") and _falhou(medidas, "c5_data_certa")


def test_precisao_de_mes_nao_conta_como_dia(copia):
    pasta = copia("pilha")
    d = _ambigua(_gab(pasta))
    _alterar(pasta / av.DESTINO, d["id"],
             lambda item: item["data"].update(valor=d["data"]["valor"][:7] + "-01", precisao="mes"))
    medidas = _medir(pasta)
    _so_falham(medidas, "c5_data_certa")


# --- critério 6: categoria ---------------------------------------------------------

def test_categoria_errada(copia):
    pasta = copia("pilha")
    d = next(d for d in _gab(pasta)["documentos"] if d["categoria"] == 3)
    _alterar(pasta / av.DESTINO, d["id"], lambda item: item.update(categoria=2))
    _so_falham(_medir(pasta), "c6_precisao_categoria", "c6_acerto_categoria",
               "c20_vr_mantido_fora_do_laboratorio")


def test_cinco_por_duvida_nao_entra_na_precisao(copia):
    pasta = copia("pilha")
    d = next(d for d in _gab(pasta)["documentos"] if d["categoria"] == 4)
    _alterar(pasta / av.DESTINO, d["id"], lambda item: item.update(categoria=5))
    _so_falham(_medir(pasta), "c6_acerto_categoria")


def test_pedido_classificado_como_resultado(copia):
    pasta = copia("cabeca_para_baixo")
    d = next(d for d in _gab(pasta)["documentos"] if d["pedido"])
    _alterar(pasta / av.DESTINO, d["id"], lambda item: item.update(categoria=2))
    _so_falham(_medir(pasta), "c6_pedido_em_outros", "c6_precisao_categoria", "c6_acerto_categoria")


# --- critério 7: repetidos ---------------------------------------------------------

def test_xerox_transcrito_duas_vezes(copia):
    pasta = copia("xerox_repetido")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    original, copia_xerox = gab["repetidos"][0]
    duplicatas = []

    def separar(item):
        duplicatas.extend(item["duplicatas"])
        item["duplicatas"] = []
    pasta_paciente = _alterar(destino, original, separar)
    m = _ler(pasta_paciente / SISTEMA / "manifesto.json")
    _, _, item = _achar(destino, original)
    m["documentos"].append({**item, "id": copia_xerox, "origens": duplicatas})
    _gravar_json(pasta_paciente / SISTEMA / "manifesto.json", m)
    _so_falham(_medir(pasta), "c7_repetidos_unidos")


def test_xerox_colado_como_mais_paginas_do_original(copia):
    # Em origens[] a cópia é transcrita de novo, como continuação do original;
    # só em duplicatas[] ela é transcrita uma vez.
    pasta = copia("xerox_repetido")
    original, _ = _gab(pasta)["repetidos"][0]

    def colar(item):
        for dup in item["duplicatas"]:
            assert dup["caminho"] == item["origens"][0]["caminho"]
            item["origens"][0]["paginas"].extend(dup["paginas"])
        item["duplicatas"] = []
    _alterar(pasta / av.DESTINO, original, colar)
    medidas = _medir(pasta)
    assert medidas["c7_repetidos_unidos"].falhas == [f"{original}+{_gab(pasta)['repetidos'][0][1]}:transcritos_juntos"]
    _so_falham(medidas, "c7_repetidos_unidos")


def test_xerox_em_duplicatas_e_tambem_transcrito(copia):
    pasta = copia("xerox_repetido")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    original, copia_xerox = gab["repetidos"][0]
    pasta_doc, m, item = _achar(destino, original)
    paginas = next(d for d in gab["documentos"] if d["id"] == copia_xerox)["paginas"]
    md = pasta_doc / "copia.md"
    md.write_text(f"# COPIA\n\n{AVISO}\n", encoding="utf-8")
    m["documentos"].append({**item, "id": "x", "md": md.name, "duplicatas": [],
                            "origens": [{**item["origens"][0], "paginas": paginas}]})
    _gravar_json(pasta_doc / SISTEMA / "manifesto.json", m)
    medidas = _medir(pasta)
    assert f"{original}+{copia_xerox}:transcritos_separados" in medidas["c7_repetidos_unidos"].falhas
    assert _falhou(medidas, "c1_nada_se_perde")


def test_seriado_fundido(copia):
    pasta = copia("seriado")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    a, b = gab["seriados"][-1]
    pasta_b, m, item_b = _achar(destino, b)
    m["documentos"].remove(item_b)
    _gravar_json(pasta_b / SISTEMA / "manifesto.json", m)
    _alterar(destino, a, lambda item: item["duplicatas"].extend(item_b["origens"]))
    _so_falham(_medir(pasta), "c7_seriados_separados")


# --- critério 8: ordem -------------------------------------------------------------

def test_paginas_fora_da_ordem_impressa(copia):
    pasta = copia("embaralhado")
    d = next(d for d in _gab(pasta)["documentos"] if d["embaralhada"])
    _alterar(pasta / av.DESTINO, d["id"], lambda item: item["origens"][0]["paginas"].sort())
    _so_falham(_medir(pasta), "c8_ordem_das_paginas")


def test_documento_partido_em_dois_itens(copia):
    pasta = copia("embaralhado")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    d = next(d for d in gab["documentos"] if d["embaralhada"])
    resto = []

    def partir(item):
        resto.extend(item["origens"][0]["paginas"][1:])
        del item["origens"][0]["paginas"][1:]
    pasta_doc = _alterar(destino, d["id"], partir)
    m = _ler(pasta_doc / SISTEMA / "manifesto.json")
    _, _, item = _achar(destino, d["id"])
    m["documentos"].append({**item, "id": "x", "origens": [{**item["origens"][0], "paginas": resto}]})
    _gravar_json(pasta_doc / SISTEMA / "manifesto.json", m)
    _so_falham(_medir(pasta), "c8_ordem_das_paginas")
    # A continuação que virou documento próprio também é classificada e datada.
    m = _ler(pasta_doc / SISTEMA / "manifesto.json")
    m["documentos"][-1].update(categoria=d["categoria"] % 4 + 1, data={"valor": None, "precisao": None,
                                                                     "papel": None})
    _gravar_json(pasta_doc / SISTEMA / "manifesto.json", m)
    medidas = _medir(pasta)
    assert medidas["c6_acerto_categoria"].casos == len(_gab(pasta)["documentos"]) + 1
    _so_falham(medidas, "c8_ordem_das_paginas", "c6_precisao_categoria", "c6_acerto_categoria",
               "c5_data_certa")


def _fundir(destino: Path, hospedeiro: dict, engolido: dict) -> None:
    """Junta o documento `engolido` ao item do `hospedeiro`, como faria uma segmentação errada."""
    pasta_doc, m, item = _achar(destino, engolido["id"])
    m["documentos"].remove(item)
    _gravar_json(pasta_doc / SISTEMA / "manifesto.json", m)
    corpo = (pasta_doc / item["md"]).read_text(encoding="utf-8").split(AVISO)[0]
    (pasta_doc / item["md"]).unlink()
    (pasta_doc / item["conferencia"]).unlink()
    md = _md_de(destino, hospedeiro["id"])
    md.write_text(md.read_text(encoding="utf-8").replace(AVISO, corpo + AVISO), encoding="utf-8")
    _alterar(destino, hospedeiro["id"],
             lambda alvo: alvo["origens"][0]["paginas"].extend(engolido["paginas"]))


def test_documentos_distintos_fundidos_num_item(copia):
    pasta = copia("pilha")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    hospedeiro = next(d for d in gab["documentos"] if d["categoria"] in (1, 2, 4))
    engolido = next(d for d in gab["documentos"]
                    if d["categoria"] in (1, 2, 4) and d["categoria"] != hospedeiro["categoria"])
    _fundir(destino, hospedeiro, engolido)
    medidas = _medir(pasta)
    assert sorted(medidas["c8_documentos_separados"].falhas) == [
        f"{hospedeiro['id']}:fundido_com_{engolido['id']}", f"{engolido['id']}:fundido_com_{hospedeiro['id']}"]
    # o engolido continua na conta da categoria, julgado pelo item que o levou
    assert medidas["c6_acerto_categoria"].casos == len(gab["documentos"])
    _so_falham(medidas, "c8_documentos_separados", "c6_precisao_categoria", "c6_acerto_categoria",
               "c5_data_certa")


def test_documento_engolido_por_laboratorio_perde_a_referencia(copia):
    # O engolido é julgado pelo item que o levou: em 3 LABORATORIO, a
    # referência dele também tem de sair.
    pasta = copia("pilha")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    hospedeiro = next(d for d in gab["documentos"] if d["categoria"] == 3)
    engolido = next(d for d in gab["documentos"] if d["categoria"] != 3 and d["referencias"])
    _fundir(destino, hospedeiro, engolido)
    medidas = _medir(pasta)
    assert any(f.startswith(f"{engolido['id']}:ref") for f in medidas["c20_vr_removido_no_laboratorio"].falhas)
    assert _falhou(medidas, "c8_documentos_separados")


def test_continuacao_de_laboratorio_partida_tambem_perde_a_referencia(copia):
    pasta = copia("laboratorio_vr")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    d = next(d for d in gab["documentos"] if d["categoria"] == 3 and len(d["paginas"]) > 1)
    pasta_doc, m, item = _achar(destino, d["id"])
    item["origens"][0]["paginas"] = d["paginas"][:1]
    md = pasta_doc / "continuacao.md"
    md.write_text(f"# CONTINUACAO\n\nVR: {d['referencias'][0]}\n\n{AVISO}\n", encoding="utf-8")
    m["documentos"].append({**item, "id": "x", "md": md.name,
                            "origens": [{**item["origens"][0], "paginas": d["paginas"][1:]}]})
    _gravar_json(pasta_doc / SISTEMA / "manifesto.json", m)
    medidas = _medir(pasta)
    assert medidas["c20_vr_removido_no_laboratorio"].falhas == [f"{d['id']}:ref1_sobrou"]
    _so_falham(medidas, "c8_ordem_das_paginas", "c20_vr_removido_no_laboratorio")


# --- critério 9: determinismo ------------------------------------------------------

def test_repeticao_identica_e_diferente(copia):
    pasta = copia("pilha")
    destino = pasta / av.DESTINO
    segunda, terceira = pasta / "destino-2", pasta / "destino-3"
    shutil.copytree(destino, segunda)
    shutil.copytree(destino, terceira)
    medidas = _medir(pasta, repeticoes=[segunda, terceira])
    assert medidas["c9_determinismo"].casos > 0
    _so_falham(medidas)
    md = next(terceira.rglob("*.md"))
    md.write_text(md.read_text(encoding="utf-8") + " ", encoding="utf-8")
    medidas = _medir(pasta, repeticoes=[segunda, terceira])
    assert medidas["c9_determinismo"].casos - medidas["c9_determinismo"].acertos == 1
    next(segunda.rglob("manifesto.json")).unlink()
    medidas = _medir(pasta, repeticoes=[segunda, terceira])
    assert medidas["c9_determinismo"].casos - medidas["c9_determinismo"].acertos == 2


# --- critério 19: aviso de números -------------------------------------------------

def test_md_sem_aviso(copia):
    pasta = copia("pilha")
    md = _md_de(pasta / av.DESTINO, "d1")
    md.write_text(md.read_text(encoding="utf-8").replace(AVISO, ""), encoding="utf-8")
    _so_falham(_medir(pasta), "c19_aviso_no_fim")


def test_texto_depois_do_aviso(copia):
    pasta = copia("pilha")
    md = _md_de(pasta / av.DESTINO, "d1")
    md.write_text(md.read_text(encoding="utf-8") + "\nmais texto\n", encoding="utf-8")
    _so_falham(_medir(pasta), "c19_aviso_no_fim")


def test_completo_sem_aviso_no_topo(copia):
    pasta = copia("pilha")
    completo = next((pasta / av.DESTINO).glob(f"*/{TEXTOS['arquivos']['completo']}"))
    texto = completo.read_text(encoding="utf-8").replace(AVISO + "\n\n", "")
    completo.write_text(texto + f"\n{AVISO}\n", encoding="utf-8")
    _so_falham(_medir(pasta), "c19_aviso_no_fim")


def test_lista_de_leitura_incerta(copia):
    pasta = copia("pilha")
    d = next(d for d in _gab(pasta)["documentos"] if d["valores"])
    md = _md_de(pasta / av.DESTINO, d["id"])
    rotulo = TEXTOS["markdown"]["leitura_incerta"]
    valor = d["valores"][0]
    linha = f"{valor['rotulo']}: {valor['valor']}"
    md.write_text(md.read_text(encoding="utf-8") + f"\n{rotulo}\n- {linha}\n", encoding="utf-8")
    medidas = _medir(pasta)
    assert medidas["c19_lista_coerente"].casos == 1
    _so_falham(medidas)
    md.write_text(md.read_text(encoding="utf-8") + "- linha que o documento nao tem 12,4\n",
                  encoding="utf-8")
    _so_falham(_medir(pasta), "c19_lista_coerente")
    md.write_text(md.read_text(encoding="utf-8").replace(rotulo + "\n", ""), encoding="utf-8")
    assert _falhou(_medir(pasta), "c19_aviso_no_fim")


# --- critério 20: valores de referência --------------------------------------------

def test_referencia_que_sobrou_no_laboratorio(copia):
    pasta = copia("laboratorio_vr")
    d = next(d for d in _gab(pasta)["documentos"] if d["categoria"] == 3)
    md = _md_de(pasta / av.DESTINO, d["id"])
    md.write_text(md.read_text(encoding="utf-8").replace(AVISO, f"VR: {d['referencias'][0]}\n\n{AVISO}"),
                  encoding="utf-8")
    medidas = _medir(pasta)
    assert len(medidas["c20_vr_removido_no_laboratorio"].falhas) == 1
    _so_falham(medidas, "c20_vr_removido_no_laboratorio")


def test_referencia_tirada_fora_do_laboratorio(copia):
    pasta = copia("laboratorio_vr")
    d = next(d for d in _gab(pasta)["documentos"] if d["categoria"] != 3 and d["referencias"])
    md = _md_de(pasta / av.DESTINO, d["id"])
    md.write_text(md.read_text(encoding="utf-8").replace(d["referencias"][0], ""), encoding="utf-8")
    _so_falham(_medir(pasta), "c20_vr_mantido_fora_do_laboratorio")


def test_referencia_dentro_de_outro_numero_nao_conta():
    assert av._contem("leucocitos 10 a 5000 /mm3", "0 a 500") is False
    assert av._contem("eosinofilos 0 a 500 /mm3", "0 a 500") is True
    assert av._contem("ate 5,05 ng/mL", "ate 5,0") is False
    assert av._contem("CEA ate 5,0 ng/mL", "ate 5,0") is True


@pytest.mark.parametrize("texto", ["6a11", "6a 11", "6 a11"])
def test_referencia_com_espacos_perdidos_pelo_ocr_continua_presente(texto):
    assert av._contem(f"Referência: {texto} mm", "6 a 11") is True


@pytest.mark.parametrize("texto", ["16a11", "6a110", "6a11,5", "6a1 1"])
def test_referencia_sem_espacos_nao_vira_outro_numero(texto):
    assert av._contem(f"Referência: {texto} mm", "6 a 11") is False


# --- linha de comando --------------------------------------------------------------

def test_linha_de_comando_aprova_a_arvore_ideal(corpus, capsys):
    raiz = corpus["pilha"].parent
    assert av.main([str(raiz)]) == 0
    saida = capsys.readouterr().out
    for chave in av.METAS:
        assert chave in saida
    assert "REPROVADO" not in saida


def test_linha_de_comando_reprova_e_lista_a_falha(copia, capsys):
    pasta = copia("contaminacao")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    intruso = next(d for d in gab["documentos"] if d["destino"] == "p2")
    _mover(destino, intruso["id"], _pasta_de(destino, gab, "p1"))
    assert av.main([str(pasta)]) == 1
    saida = capsys.readouterr().out
    assert "REPROVADO" in saida and f"{intruso['id']}:pasta_de_outra_pessoa" in saida
    # a falha cita códigos e ids, nunca o nome do paciente
    for p in gab["pacientes"]:
        assert p["nome"] not in saida


def test_meta_de_nome_achado_so_vale_sem_ruido(copia):
    pasta = copia("contaminacao")
    gab, destino = _gab(pasta), pasta / av.DESTINO
    d = next(d for d in gab["documentos"] if d["destino"] == "p1")
    _mover(destino, d["id"], _nova_desconhecido(destino))
    assert av.main([str(pasta)]) == 1
    gab["ruido"] = 0.03
    (pasta / "gabarito.json").write_text(json.dumps(gab), encoding="utf-8")
    assert av.main([str(pasta)]) == 0


def test_linha_de_comando_com_raizes_de_destino(copia, tmp_path, capsys):
    pasta = copia("pilha")
    primeira, segunda = tmp_path / "r1", tmp_path / "r2"
    shutil.copytree(pasta / av.DESTINO, primeira / pasta.name)
    shutil.copytree(pasta / av.DESTINO, segunda / pasta.name)
    shutil.rmtree(pasta / av.DESTINO)
    assert av.main([str(pasta), "--destinos", str(primeira), str(segunda)]) == 0
    assert "c9_determinismo" in capsys.readouterr().out


def test_linha_de_comando_sem_destino_reprova(copia, capsys):
    pasta = copia("pilha")
    shutil.rmtree(pasta / av.DESTINO)
    assert av.main([str(pasta)]) == 1
    assert f"{pasta.name}: sem_destino" in capsys.readouterr().out


def test_cenario_sem_destino_reprova_mesmo_com_outro_aprovado(copia, capsys):
    # Se o arnês falha antes de gravar justo a contaminação, a régua reprova.
    medido, sem_destino = copia("pilha"), copia("contaminacao")
    shutil.rmtree(sem_destino / av.DESTINO)
    assert av.main([str(medido), str(sem_destino)]) == 1
    saida = capsys.readouterr().out
    assert f"{sem_destino.name}: sem_destino" in saida and "REPROVADO" in saida


def test_linha_de_comando_sem_cenario(tmp_path):
    assert av.main([str(tmp_path)]) == 2
