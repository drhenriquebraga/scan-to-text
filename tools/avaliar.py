"""Régua: mede a árvore de destino contra o gabarito do corpus sintético.

Mede os critérios de aceitação 1 a 9, 19 e 20 que se leem na árvore de saída.
O elo entre o gabarito e a saída é o manifesto de cada pasta, pelo
arquivo lido e pelo índice da página, e nunca o texto do MD: o documento é
achado pelas páginas que ele diz conter.

O que a régua supõe do manifesto, além do que `registro.py` valida:

- cada entrada de `origens[]` aponta um arquivo lido pelo `sha256` e pelo
  `caminho`. O SHA-256 pode ser o do próprio arquivo lido ou o do arquivo de
  origem que o contém (o ZIP como chegou, que é o que `escrever/arvore.py`
  grava); o caminho é o do gabarito (`exames.zip/interno.zip/lab.pdf`) e só é
  exigido quando o SHA-256 sozinho não aponta um arquivo só;
- índice de página conta do 0, na ordem do arquivo lido (como `Pagina.indice`);
- `origens[].paginas` vem na ordem do documento (critério 8);
- `duplicatas[]` tem a forma de `origens[]`: a cópia transcrita uma vez só
  fica no mesmo item do documento que ela repete, e só ali (critério 7);
- `paginas_em_branco[]` de um item traz índices do arquivo da primeira origem
  do item, ou entradas na forma de `origens[]`; na raiz do manifesto, só a
  segunda forma;
- `md` e `conferencia` são relativos à pasta do paciente.

A régua só imprime códigos, ids do gabarito e nomes de arquivo do corpus:
nenhum nome de paciente vai para a saída.

Uso: python tools/avaliar.py [PASTA ...] [--destinos RAIZ [RAIZ ...]]

PASTA é um cenário (tem gabarito.json) ou uma pasta de cenários (padrão:
tests/corpus). O destino de um cenário é `<cenário>/destino`, ou
`<RAIZ>/<nome do cenário>` com --destinos; da segunda RAIZ em diante vêm as
repetições da mesma entrada, comparadas byte a byte (critério 9). Sai 0 se
todos os cenários foram medidos e todas as metas atingidas; 1 se alguma meta
não foi atingida ou algum cenário ficou sem destino; 2 se não havia cenário.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "app") not in sys.path:
    sys.path.insert(0, str(RAIZ / "app"))

import pypdfium2 as pdfium  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

from scan_to_text.pacote import Pacote, carregar  # noqa: E402
from scan_to_text.registro import MANIFESTO, PASTA_SISTEMA  # noqa: E402

PASTA_PADRAO = RAIZ / "tests" / "corpus"
GABARITO = "gabarito.json"
DESTINO = "destino"
# Letras que o OCR pode trocar num nome sem que ele deixe de ser o do paciente.
TOLERANCIA_NOME = 2
# Escala da renderização que identifica uma página copiada para outro PDF: a
# página importada desenha os mesmos pixels que a original.
ESCALA_IMPRESSAO = 0.15
SEM_DONO = ""
_DATA_DA_PASTA = re.compile(r" - \d{4}-\d{2}-\d{2}(?: \(\d+\))?$")

# Meta de cada critério, como fração de acertos; None é informativo.
METAS: dict[str, float | None] = {
    "c1_nada_se_perde": 1.0,
    "c1_originais_preservados": 1.0,
    "c2_origem_intocada": 1.0,
    "c3_paciente_certo": 1.0,
    "c3_paginas_na_pasta_certa": 1.0,
    "c4_sem_medico_como_paciente": 1.0,
    "c4_nome_achado": 0.95,
    "c5_sem_troca_dia_mes": 1.0,
    "c5_sem_data_armadilha": 1.0,
    "c5_data_certa": None,
    "c6_precisao_categoria": 0.98,
    "c6_acerto_categoria": None,
    "c6_pedido_em_outros": 1.0,
    "c7_repetidos_unidos": 0.90,
    "c7_seriados_separados": 1.0,
    "c8_ordem_das_paginas": 1.0,
    # A fusão de documentos distintos não tem meta própria; zero, como a
    # de seriados no critério 7, porque o engolido perde título, data e categoria.
    "c8_documentos_separados": 1.0,
    "c9_determinismo": 1.0,
    "c19_aviso_no_fim": 1.0,
    # O critério pede o contrário (toda linha incerta na lista), que exige a
    # confiança do OCR e não se lê na árvore. Aqui só se vê se a lista repete
    # linhas do corpo; a limpeza pode ter tirado do corpo uma linha listada.
    "c19_lista_coerente": None,
    "c20_vr_removido_no_laboratorio": 1.0,
    "c20_vr_mantido_fora_do_laboratorio": 1.0,
}
# O critério pede nome achado em 95% dos documentos limpos: com ruído, só se mede.
SO_SEM_RUIDO = frozenset({"c4_nome_achado"})


@dataclass
class Medida:
    acertos: int = 0
    casos: int = 0
    falhas: list[str] = field(default_factory=list)

    def contar(self, ok: bool, falha: str) -> None:
        self.casos += 1
        if ok:
            self.acertos += 1
        else:
            self.falhas.append(falha)

    @property
    def valor(self) -> float:
        return self.acertos / self.casos


# --- leitura -----------------------------------------------------------------------

def _sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _bytes_de(origem: Path, caminho: str) -> bytes:
    """Bytes de um arquivo da origem; `a.zip/b.zip/c.pdf` abre ZIP dentro de ZIP."""
    partes = caminho.split("/")
    dados = (origem / partes[0]).read_bytes()
    for parte in partes[1:]:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            dados = z.read(parte)
    return dados


def _palavras(nome: object) -> list[str]:
    if not isinstance(nome, str):
        return []
    sem = "".join(c for c in unicodedata.normalize("NFKD", nome) if not unicodedata.combining(c))
    return re.sub(r"[^0-9A-Za-z]+", " ", sem).upper().split()


def _chave(nome: object) -> str:
    """Nome comparável: sem acento nem pontuação, palavras em ordem alfabética.

    A ordem some para "SOBRENOME, Nome" casar com "Nome SOBRENOME".
    """
    return " ".join(sorted(_palavras(nome)))


def _data(valor: object) -> date | None:
    try:
        return date.fromisoformat(valor[:10]) if isinstance(valor, str) else None
    except ValueError:
        return None


def _espacos(texto: str) -> str:
    return " ".join(texto.split())


def _contem(texto: str, trecho: str) -> bool:
    """`trecho` em `texto` sem ser pedaço de outro número ("0 a 500" em "10 a 5000")."""
    inicio = r"(?<![\w.,])" if trecho[:1].isalnum() else ""
    conteudo = r"\s*".join(re.escape(parte) for parte in _espacos(trecho).split())
    padrao = inicio + conteudo + r"(?!\d|[.,]\d)"
    return re.search(padrao, _espacos(texto)) is not None


def _ler_json(arquivo: Path) -> dict | None:
    try:
        dados = json.loads(arquivo.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


def _referencias(itens: object) -> list[tuple[str, str | None, list[int]]]:
    """(sha256, caminho, páginas) de uma lista no formato de `origens[]`."""
    saida = []
    for item in itens if isinstance(itens, list) else []:
        if isinstance(item, dict) and isinstance(item.get("sha256"), str):
            caminho = item.get("caminho")
            paginas = item.get("paginas")
            saida.append((item["sha256"].lower(),
                          caminho.replace("\\", "/") if isinstance(caminho, str) else None,
                          [p for p in paginas if isinstance(p, int)] if isinstance(paginas, list) else []))
    return saida


def _pares(d: dict) -> set[tuple[str, int]]:
    """As páginas de um documento do gabarito, como (id do arquivo, índice)."""
    return {(d["arquivo"], p) for p in d["paginas"]}


def _impressoes(dados: bytes) -> list[str]:
    try:
        pdf = pdfium.PdfDocument(dados)
    except pdfium.PdfiumError:
        return []
    try:
        return [_sha256(pagina.render(scale=ESCALA_IMPRESSAO, grayscale=True).to_pil().tobytes())
                for pagina in pdf]
    finally:
        pdf.close()


def _arquivos_da_pasta(pasta: Path) -> list[Path]:
    """Arquivos de uma pasta do destino, fora a pasta de sistema do app."""
    return sorted(p for p in pasta.rglob("*")
                  if p.is_file() and PASTA_SISTEMA not in p.relative_to(pasta).parts)


@dataclass(eq=False)
class _Pasta:
    caminho: Path
    rotulo: str                       # "pasta<n>": a régua não imprime nome de paciente
    manifesto: dict | None
    candidatos: frozenset[str] = frozenset()

    @property
    def de_paciente(self) -> bool:
        return self.manifesto is not None and isinstance(self.manifesto.get("paciente"), dict)

    def nomes(self) -> list[str]:
        paciente = self.manifesto["paciente"]
        variantes = paciente.get("variantes")
        nomes = [paciente.get("exibido"), *(variantes if isinstance(variantes, list) else [])]
        nomes.append(_DATA_DA_PASTA.sub("", self.caminho.name))
        return [n for n in nomes if isinstance(n, str) and n.strip()]


@dataclass(eq=False)
class _Item:
    """Um documento de saída: um item de `documentos[]` de um manifesto.

    As páginas são `(id do arquivo no gabarito, índices)`; o id é None quando
    a referência do manifesto não aponta um arquivo do gabarito.
    """
    pasta: _Pasta
    doc: dict
    md: Path | None                   # só quando o arquivo existe
    origens: list[tuple[str | None, list[int]]]
    duplicatas: list[tuple[str | None, list[int]]]

    @property
    def paginas(self) -> list[tuple[str | None, list[int]]]:
        return self.origens + self.duplicatas

    @property
    def transcritas(self) -> set[tuple[str | None, int]]:
        """As páginas que o MD transcreve: só as de `origens[]`."""
        return {(aid, p) for aid, paginas in self.origens for p in paginas}

    @property
    def duplicadas(self) -> set[tuple[str | None, int]]:
        return {(aid, p) for aid, paginas in self.duplicatas for p in paginas}


class _Medicao:
    def __init__(self, destino: Path, gab: dict, pacote: Pacote, origem: Path | None):
        textos = pacote["textos"]
        self.destino, self.gab, self.origem = Path(destino), gab, origem
        self.originais = textos["pastas"]["originais"]
        self.mistos = textos["pastas"]["mais_de_um_paciente"]
        self.completo = textos["arquivos"]["completo"]
        self.leia_me = textos["arquivos"]["leia_me"]
        self.aviso = "## " + textos["markdown"]["aviso"]
        self.rotulo_lista = textos["markdown"]["leitura_incerta"]
        self.medidas = {chave: Medida() for chave in METAS}

        self.pacientes = {p["id"]: p for p in gab["pacientes"]}
        self.pessoa = {pid: p.get("pessoa", pid) for pid, p in self.pacientes.items()}
        self.arquivos = {a["id"]: a for a in gab["arquivos"]}
        self.docs = {d["id"]: d for d in gab["documentos"]}
        sha_do_topo = {o["nome"]: o["sha256"].lower() for o in gab["origem_arquivos"]}
        self.por_sha: dict[str, list[str]] = defaultdict(list)
        for a in gab["arquivos"]:
            for sha in dict.fromkeys((a["sha256"].lower(), sha_do_topo[a["caminho"].split("/")[0]])):
                self.por_sha[sha].append(a["id"])

        self.pastas: list[_Pasta] = []
        self.em_branco: set[tuple[str | None, int]] = set()
        self.onde: dict[tuple[str | None, int], list[_Item]] = defaultdict(list)
        self._ler_arvore()

    # --- árvore ---

    def _ler_arvore(self) -> None:
        if not self.destino.is_dir():
            return
        subpastas = sorted(p for p in self.destino.iterdir() if p.is_dir() and not p.name.startswith("_"))
        for n, caminho in enumerate(subpastas, start=1):
            pasta = _Pasta(caminho, f"pasta{n}", _ler_json(caminho / PASTA_SISTEMA / MANIFESTO))
            self.pastas.append(pasta)
            if pasta.manifesto is None:
                continue
            if pasta.de_paciente:
                pasta.candidatos = self._candidatos(pasta)
            for aid, paginas in self._arquivos_de(pasta.manifesto.get("paginas_em_branco")):
                self.em_branco.update((aid, p) for p in paginas)
            documentos = pasta.manifesto.get("documentos")
            for doc in documentos if isinstance(documentos, list) else []:
                if isinstance(doc, dict):
                    self._ler_item(pasta, doc)

    def _arquivo(self, sha: str, caminho: str | None) -> str | None:
        """O arquivo do gabarito que uma referência do manifesto aponta, ou None.

        Os arquivos de dentro de um ZIP podem vir todos com o SHA-256 do ZIP:
        aí o caminho escolhe, e sem caminho que case a referência não aponta
        nenhum, em vez de um palpite.
        """
        candidatos = self.por_sha.get(sha, [])
        pelo_caminho = [aid for aid in candidatos if self.arquivos[aid]["caminho"] == caminho]
        if pelo_caminho:
            return pelo_caminho[0]
        return candidatos[0] if len(candidatos) == 1 else None

    def _arquivos_de(self, itens: object) -> list[tuple[str | None, list[int]]]:
        return [(self._arquivo(sha, caminho), paginas) for sha, caminho, paginas in _referencias(itens)]

    def _ler_item(self, pasta: _Pasta, doc: dict) -> None:
        md = doc.get("md")
        md = pasta.caminho / md if isinstance(md, str) and md else None
        item = _Item(pasta, doc, md if md is not None and md.is_file() else None,
                     self._arquivos_de(doc.get("origens")), self._arquivos_de(doc.get("duplicatas")))
        for aid, paginas in item.paginas:
            for p in paginas:
                self.onde[(aid, p)].append(item)
        brancos = doc.get("paginas_em_branco")
        for p in brancos if isinstance(brancos, list) else []:
            if isinstance(p, int) and item.origens:
                self.em_branco.add((item.origens[0][0], p))
        for aid, paginas in self._arquivos_de(brancos):
            self.em_branco.update((aid, p) for p in paginas)

    def _candidatos(self, pasta: _Pasta) -> frozenset[str]:
        """Pacientes do gabarito que a pasta pode ser, pelo nome e pelo nascimento.

        Primeiro o nome exibido contra o nome da pasta esperada; depois qualquer
        grafia contra qualquer grafia; por último a tolerância do OCR.
        """
        paciente = pasta.manifesto["paciente"]
        nomes = {_chave(n) for n in pasta.nomes()} - {""}
        exibido = _chave(paciente.get("exibido")) or _chave(_DATA_DA_PASTA.sub("", pasta.caminho.name))
        grafias = {pid: {_chave(n) for n in [p["nome"], *p["nomes"]]} for pid, p in self.pacientes.items()}
        achados = {pid for pid, p in self.pacientes.items() if _chave(p["nome"]) == exibido}
        if not achados:
            achados = {pid for pid, g in grafias.items() if g & nomes}
        if not achados:
            achados = {pid for pid, g in grafias.items()
                       if any(Levenshtein.distance(a, b) <= TOLERANCIA_NOME for a in g for b in nomes)}
        if not achados:
            letras = {"".join(_palavras(n)) for n in pasta.nomes()} - {""}
            achados = {pid for pid, p in self.pacientes.items()
                       if any("".join(_palavras(n)) in letras for n in [p["nome"], *p["nomes"]])}
        nascimento = _data(paciente.get("nascimento"))
        if nascimento is not None:
            mesmos = {pid for pid in achados if _data(self.pacientes[pid]["nascimento"]) == nascimento}
            achados = mesmos
        return frozenset(achados)

    # --- ligações entre gabarito e saída ---

    def itens_do_doc(self, d: dict) -> list[_Item]:
        vistos: list[_Item] = []
        for p in d["paginas"]:
            for item in self.onde.get((d["arquivo"], p), []):
                if item not in vistos:
                    vistos.append(item)
        return vistos

    def principal(self, d: dict) -> _Item | None:
        """O item que tem a primeira página do documento."""
        itens = self.onde.get((d["arquivo"], d["paginas"][0]), [])
        return itens[0] if itens else None

    def itens_de(self, d: dict) -> list[_Item]:
        """Os itens que transcrevem alguma página do documento: cada um é julgado por ele.

        Documento partido conta uma vez por pedaço; documento engolido por
        outro continua na conta, julgado pelo item que o levou; a cópia
        xerocada em `duplicatas[]` não conta de novo, porque não é transcrita.
        """
        minhas = _pares(d)
        return [item for item in self.itens_do_doc(d) if minhas & item.transcritas]

    def pessoas(self, pasta: _Pasta) -> set[str]:
        return {self.pessoa[pid] for pid in pasta.candidatos}

    # --- critérios ---

    def medir(self, repeticoes: Sequence[Path]) -> dict[str, Medida]:
        self._nada_se_perde()
        self._originais_preservados()
        if self.origem is not None:
            self._origem_intocada()
        errados = self._paciente_certo()
        self._paginas_na_pasta_certa()
        self._nome(errados)
        self._datas()
        self._categorias()
        self._repetidos()
        self._ordem()
        self._separados()
        if repeticoes:
            self._determinismo(repeticoes)
        self._aviso()
        self._referencias()
        return self.medidas

    def _nada_se_perde(self) -> None:
        """Critério 1: cada página lida num só documento entregue, ou em branco registrada."""
        m = self.medidas["c1_nada_se_perde"]
        for a in self.gab["arquivos"]:
            if a["ilegivel"]:
                continue
            for p in range(a["paginas"]):
                entregue = sum(1 for item in self.onde.get((a["id"], p), []) if item.md is not None)
                branco = (a["id"], p) in self.em_branco
                m.contar(entregue == 1 or (entregue == 0 and branco),
                         f"{a['id']}:p{p}:{'perdida' if entregue == 0 else 'repetida'}")

    def _originais_preservados(self) -> None:
        """Critério 1: o original intacto em ORIGINAIS ou na pasta dos mistos."""
        m = self.medidas["c1_originais_preservados"]
        onde: dict[str, set[Path]] = defaultdict(set)
        for pasta in self.pastas:
            if (pasta.caminho / self.originais).is_dir():
                for arquivo in _arquivos_da_pasta(pasta.caminho / self.originais):
                    onde[_sha256(arquivo.read_bytes())].add(pasta.caminho)
        mistos = self.destino / self.mistos
        if mistos.is_dir():
            for arquivo in _arquivos_da_pasta(mistos):
                onde[_sha256(arquivo.read_bytes())].add(mistos)
        for o in self.gab["origem_arquivos"]:
            dentro = [a for a in self.gab["arquivos"]
                      if a["caminho"] == o["nome"] or a["caminho"].startswith(o["nome"] + "/")]
            pastas = onde.get(o["sha256"].lower(), set())
            if any(a["ilegivel"] for a in dentro):
                ok = any((p / self.leia_me).is_file() for p in pastas)
                m.contar(ok, f"{o['nome']}:ilegivel_sem_original_ou_leia_me")
            else:
                m.contar(bool(pastas), f"{o['nome']}:original_ausente")

    def _origem_intocada(self) -> None:
        """Critério 2: SHA-256 e data de modificação iguais, e nada novo na origem."""
        m = self.medidas["c2_origem_intocada"]
        esperados = {o["nome"]: o for o in self.gab["origem_arquivos"]}
        for nome, o in esperados.items():
            caminho = self.origem / nome
            ok = (caminho.is_file() and caminho.stat().st_mtime_ns == o["mtime_ns"]
                  and _sha256(caminho.read_bytes()) == o["sha256"])
            m.contar(ok, f"{nome}:alterado")
        if self.origem.is_dir():
            for caminho in sorted(p for p in self.origem.rglob("*") if p.is_file()):
                nome = caminho.relative_to(self.origem).as_posix()
                if nome not in esperados:
                    m.contar(False, f"{nome}:novo_na_origem")

    def _paciente_certo(self) -> set[str]:
        """Critério 3: nenhum documento na pasta de outra pessoa; devolve os ids errados.

        DESCONHECIDO nunca é erro aqui (melhor faltar do que errar). Pasta com
        documentos de duas pessoas é misturada mesmo quando o nome não as
        separa (homônimos sem nascimento): erra quem não é a maioria.
        """
        m = self.medidas["c3_paciente_certo"]
        por_pasta: dict[int, Counter] = defaultdict(Counter)
        for d in self.gab["documentos"]:
            if d["destino"]:
                for item in self.itens_do_doc(d):
                    por_pasta[id(item.pasta)][self.pessoa[d["destino"]]] += 1
        maioria = {chave: min(contagem.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                   for chave, contagem in por_pasta.items() if len(contagem) > 1}
        errados = set()
        for d in self.gab["documentos"]:
            erro = None
            for item in self.itens_do_doc(d):
                if not item.pasta.de_paciente:
                    continue
                if d["destino"] is None:
                    erro = "sem_dono_em_pasta_de_paciente"
                elif self.pessoa[d["destino"]] not in self.pessoas(item.pasta):
                    erro = "pasta_de_outra_pessoa"
                elif maioria.get(id(item.pasta), self.pessoa[d["destino"]]) != self.pessoa[d["destino"]]:
                    erro = "pasta_misturada"
                if erro:
                    break
            m.contar(erro is None, f"{d['id']}:{erro}")
            if erro:
                errados.add(d["id"])
        return errados

    def _paginas_na_pasta_certa(self) -> None:
        """Critério 3: nenhuma página de outra pessoa em PDF algum da pasta do paciente.

        Original inteiro é reconhecido pelo SHA-256; PDF montado (as páginas de
        um paciente, o arquivo de conferência) pela impressão de cada página,
        que exige a origem.
        """
        m = self.medidas["c3_paginas_na_pasta_certa"]
        dono = {(d["arquivo"], p): self.pessoa[d["destino"]] if d["destino"] else SEM_DONO
                for d in self.gab["documentos"] for p in d["paginas"]}
        paginas_do_sha: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for a in self.gab["arquivos"]:
            if not a["ilegivel"]:
                paginas = [(a["id"], p) for p in range(a["paginas"])]
                paginas_do_sha[a["sha256"].lower()] += paginas
                topo = a["caminho"].split("/")[0]
                o = next(o for o in self.gab["origem_arquivos"] if o["nome"] == topo)
                if o["sha256"].lower() != a["sha256"].lower():
                    paginas_do_sha[o["sha256"].lower()] += paginas
        impressoes: dict[str, set[tuple[str, int]]] | None = None
        for pasta in self.pastas:
            if not pasta.de_paciente:
                continue
            certas = self.pessoas(pasta)
            for arquivo in _arquivos_da_pasta(pasta.caminho):
                dados = arquivo.read_bytes()
                sha = _sha256(dados)
                if sha in paginas_do_sha:
                    fontes = [{pagina} for pagina in paginas_do_sha[sha]]
                elif arquivo.suffix.lower() == ".pdf" and self.origem is not None:
                    if impressoes is None:
                        impressoes = self._impressoes_da_origem()
                    fontes = [impressoes.get(i, set()) for i in _impressoes(dados)]
                else:
                    continue
                for paginas in fontes:
                    donos = {dono[p] for p in paginas if p in dono}
                    if donos:
                        ok = any(d != SEM_DONO and d in certas for d in donos)
                        quem = min(f"{a}:p{p}" for a, p in paginas if (a, p) in dono)
                        m.contar(ok, f"{quem}:em_{pasta.rotulo}")

    def _impressoes_da_origem(self) -> dict[str, set[tuple[str, int]]]:
        impressoes: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for a in self.gab["arquivos"]:
            if not a["ilegivel"]:
                for p, impressao in enumerate(_impressoes(_bytes_de(self.origem, a["caminho"]))):
                    impressoes[impressao].add((a["id"], p))
        return impressoes

    def _nome(self, errados: set[str]) -> None:
        """Critério 4: médico nunca vira paciente; o nome rotulado leva à pasta certa."""
        m = self.medidas["c4_sem_medico_como_paciente"]
        de_pacientes = {w for p in self.pacientes.values() for n in [p["nome"], *p["nomes"]]
                        for w in _palavras(n)}
        sobrenomes = {w for medico in self.gab["medicos"] for w in _palavras(medico)[1:]} - de_pacientes
        for pasta in self.pastas:
            if pasta.de_paciente:
                palavras = {w for n in pasta.nomes() for w in _palavras(n)}
                m.contar(not palavras & sobrenomes, f"{pasta.rotulo}:nome_de_medico")
        m = self.medidas["c4_nome_achado"]
        for d in self.gab["documentos"]:
            if d["paciente"] and d["destino"]:
                item = self.principal(d)
                ok = (item is not None and item.pasta.de_paciente
                      and d["destino"] in item.pasta.candidatos and d["id"] not in errados)
                m.contar(ok, f"{d['id']}:nome_nao_achado")

    def _datas(self) -> None:
        """Critério 5, por documento de saída: sem troca de dia e mês, sem data de armadilha."""
        troca, armadilha, certa = (self.medidas[k] for k in
                                   ("c5_sem_troca_dia_mes", "c5_sem_data_armadilha", "c5_data_certa"))
        for d in self.gab["documentos"]:
            for item in self.itens_de(d):
                saida = item.doc.get("data") if isinstance(item.doc.get("data"), dict) else {}
                dia = _data(saida.get("valor")) if saida.get("precisao") in ("dia", None) else None
                if dia is not None:
                    armadilha.contar(dia.isoformat() not in d["datas_armadilha"],
                                     f"{d['id']}:data_armadilha")
                if d["data"] is None:
                    continue
                esperado = date.fromisoformat(d["data"]["valor"])
                certa.contar(dia == esperado, f"{d['id']}:data_diferente")
                if dia is not None and esperado.day <= 12 and esperado.day != esperado.month:
                    trocado = date(esperado.year, esperado.day, esperado.month)
                    troca.contar(dia != trocado, f"{d['id']}:dia_e_mes_trocados")

    def _categorias(self) -> None:
        """Critério 6, por documento de saída: precisão entre os classificados (1 a 4); pedido em 5."""
        precisao, acerto, pedido = (self.medidas[k] for k in
                                    ("c6_precisao_categoria", "c6_acerto_categoria", "c6_pedido_em_outros"))
        for d in self.gab["documentos"]:
            for item in self.itens_de(d):
                categoria = item.doc.get("categoria")
                falha = f"{d['id']}:categoria_{categoria}_esperada_{d['categoria']}"
                acerto.contar(categoria == d["categoria"], falha)
                if categoria in (1, 2, 3, 4):
                    precisao.contar(categoria == d["categoria"], falha)
                if d["pedido"]:
                    pedido.contar(categoria == 5, f"{d['id']}:pedido_como_resultado")

    def _copia_unida(self, original: dict, copia: dict) -> bool:
        """A cópia toda em `duplicatas[]` do item que transcreve o original, e em nenhum `origens[]`."""
        primeira = (original["arquivo"], original["paginas"][0])
        item = next((i for i in self.onde.get(primeira, []) if primeira in i.transcritas), None)
        minhas = _pares(copia)
        return (item is not None and minhas <= item.duplicadas
                and not any(minhas & i.transcritas for i in self.itens_do_doc(copia)))

    def _repetidos(self) -> None:
        """Critério 7: cópia xerocada transcrita uma vez; exame seriado nunca fundido.

        Qualquer das duas cópias pode ser a transcrita; a outra só pode estar em
        `duplicatas[]` do mesmo item. Em `origens[]` ela seria transcrita de novo,
        num item próprio ou como continuação do original.
        """
        m = self.medidas["c7_repetidos_unidos"]
        for a, b in self.gab["repetidos"]:
            da, db = self.docs[a], self.docs[b]
            if self._copia_unida(da, db) or self._copia_unida(db, da):
                m.contar(True, "")
                continue
            pa, pb = _pares(da), _pares(db)
            if any(pa & i.transcritas and pb & i.transcritas for i in self.itens_do_doc(da)):
                falha = "transcritos_juntos"
            elif all(any(p & i.transcritas for i in self.itens_do_doc(d)) for p, d in ((pa, da), (pb, db))):
                falha = "transcritos_separados"
            else:
                falha = "copia_fora_de_duplicatas"
            m.contar(False, f"{a}+{b}:{falha}")
        m = self.medidas["c7_seriados_separados"]
        for a, b in self.gab["seriados"]:
            juntos = [i for i in self.itens_do_doc(self.docs[a]) if i in self.itens_do_doc(self.docs[b])]
            m.contar(not juntos, f"{a}+{b}:fundidos")

    def _ordem(self) -> None:
        """Critério 8: documento de várias páginas inteiro num item e na ordem impressa."""
        m = self.medidas["c8_ordem_das_paginas"]
        for d in self.gab["documentos"]:
            if len(d["paginas"]) < 2 or (d["embaralhada"] and not d["numerada"]):
                continue
            itens = self.itens_do_doc(d)
            minhas = set(d["paginas"])
            ok = len(itens) == 1 and any(
                [p for p in paginas if p in minhas] == d["paginas"]
                for aid, paginas in itens[0].paginas if aid == d["arquivo"])
            m.contar(ok, f"{d['id']}:{'partido' if len(itens) > 1 else 'fora_de_ordem'}")

    def _separados(self) -> None:
        """Critério 8: documento distinto nunca transcrito no mesmo item que outro.

        A fusão tira do engolido o título, a data e a categoria próprios. Os
        pares de repetidos e de seriados são do critério 7.
        """
        m = self.medidas["c8_documentos_separados"]
        do_c7 = {frozenset(par) for par in self.gab["repetidos"] + self.gab["seriados"]}
        for d in self.gab["documentos"]:
            itens = self.itens_de(d)
            if not itens:
                continue
            outros = sorted({e["id"] for item in itens for e in self.gab["documentos"]
                             if e is not d and frozenset((d["id"], e["id"])) not in do_c7
                             and _pares(e) & item.transcritas})
            m.contar(not outros, f"{d['id']}:fundido_com_{'+'.join(outros)}")

    def _determinismo(self, repeticoes: Sequence[Path]) -> None:
        """Critério 9: os mesmos MD e manifestos, byte a byte, em todas as repetições."""
        m = self.medidas["c9_determinismo"]

        def resumo(raiz: Path) -> dict[str, str]:
            if not raiz.is_dir():
                return {}
            return {p.relative_to(raiz).as_posix(): _sha256(p.read_bytes()) for p in raiz.rglob("*")
                    if p.is_file() and (p.suffix.lower() == ".md" or p.name == MANIFESTO)}

        arvores = [resumo(self.destino), *(resumo(Path(r)) for r in repeticoes)]
        for n, relativo in enumerate(sorted(set().union(*arvores)), start=1):
            primeiro = arvores[0].get(relativo)
            ok = primeiro is not None and all(a.get(relativo) == primeiro for a in arvores[1:])
            m.contar(ok, f"arquivo{n}:difere")

    def _aviso(self) -> None:
        """Critério 19: todo MD termina com o aviso de números; a lista só repete o corpo."""
        fim, lista = self.medidas["c19_aviso_no_fim"], self.medidas["c19_lista_coerente"]
        for pasta in self.pastas:
            for md in _arquivos_da_pasta(pasta.caminho):
                if md.suffix.lower() != ".md":
                    continue
                linhas = md.read_text(encoding="utf-8-sig").splitlines()
                quem = f"{pasta.rotulo}:{md.name}"
                titulos = [i for i, linha in enumerate(linhas) if linha.startswith("## ")]
                if md.name == self.completo:
                    avisos = [i for i in titulos if linhas[i].rstrip() == self.aviso]
                    fim.contar(len(avisos) == 1 and avisos[0] == titulos[0], f"{quem}:aviso_fora_do_topo")
                    continue
                if not titulos or linhas[titulos[-1]].rstrip() != self.aviso:
                    fim.contar(False, f"{quem}:sem_aviso_no_fim")
                    continue
                resto = [linha.strip() for linha in linhas[titulos[-1] + 1:] if linha.strip()]
                itens = resto[1:] if resto[:1] == [self.rotulo_lista] else resto
                ok = not resto or (resto[0] == self.rotulo_lista and itens
                                   and all(i.startswith("- ") for i in itens))
                fim.contar(ok, f"{quem}:texto_depois_do_aviso")
                corpo = _espacos("\n".join(linhas[:titulos[-1]]))
                for n, item in enumerate(itens, start=1):
                    if item.startswith("- "):
                        texto = _espacos(item[2:])
                        lista.contar(any(c.isdigit() for c in texto) and texto in corpo,
                                     f"{quem}:item{n}_fora_do_corpo")

    def _referencias(self) -> None:
        """Critério 20: referência rotulada sai do laboratório (3) e fica fora dele.

        Cada pedaço em 3 de um documento partido não pode ter referência
        nenhuma; fora de 3, as referências se procuram em todos os pedaços
        juntos, porque cada um só tem as das próprias páginas.
        """
        removido = self.medidas["c20_vr_removido_no_laboratorio"]
        mantido = self.medidas["c20_vr_mantido_fora_do_laboratorio"]
        for d in self.gab["documentos"]:
            itens = [item for item in self.itens_de(d) if item.md is not None]
            if not d["referencias"] or not itens:
                continue
            textos = [item.md.read_text(encoding="utf-8-sig") for item in itens]
            for item, texto in zip(itens, textos):
                if item.doc.get("categoria") == 3:
                    for n, referencia in enumerate(d["referencias"], start=1):
                        removido.contar(not _contem(texto, referencia), f"{d['id']}:ref{n}_sobrou")
            if all(item.doc.get("categoria") != 3 for item in itens):
                juntos = "\n".join(textos)
                for n, referencia in enumerate(d["referencias"], start=1):
                    mantido.contar(_contem(juntos, referencia), f"{d['id']}:ref{n}_tirada")


def medir(destino: Path, gabarito: dict, origem: Path | None = None,
          repeticoes: Sequence[Path] = ()) -> dict[str, Medida]:
    """Acertos, casos e falhas de cada critério de `METAS`.

    Sem `origem` não se mede o critério 2 nem a impressão das páginas de PDF
    montado; sem `repeticoes`, o critério 9.
    """
    pacote = carregar(gabarito.get("idioma", "pt-BR"))
    return _Medicao(destino, gabarito, pacote, Path(origem) if origem is not None else None).medir(
        repeticoes)


def avaliar(destino: Path, gabarito: dict, origem: Path | None = None,
            repeticoes: Sequence[Path] = ()) -> dict[str, float]:
    """Fração de acertos de cada critério que teve casos nesta árvore."""
    return {chave: m.valor for chave, m in medir(destino, gabarito, origem, repeticoes).items()
            if m.casos}


# --- linha de comando --------------------------------------------------------------

def _cenarios(pastas: Sequence[Path]) -> list[Path]:
    achados = []
    for pasta in pastas:
        if (pasta / GABARITO).is_file():
            achados.append(pasta)
        elif pasta.is_dir():
            achados += sorted(p for p in pasta.iterdir() if (p / GABARITO).is_file())
    return achados


def _meta(chave: str, ruido: float) -> float | None:
    return None if ruido and chave in SO_SEM_RUIDO else METAS[chave]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Mede a árvore de destino contra o gabarito do corpus.")
    parser.add_argument("pastas", nargs="*", type=Path, default=[PASTA_PADRAO])
    parser.add_argument("--destinos", nargs="+", type=Path, default=None,
                        help="raízes com uma pasta de destino por cenário; da segunda em diante, "
                             "repetições para o critério 9")
    args = parser.parse_args(argv)

    cenarios = _cenarios(args.pastas)
    if not cenarios:
        print(f"nada a medir: nenhum cenário com {GABARITO}")
        return 2
    placares: dict[float, dict[str, Medida]] = {}
    falhas: list[str] = []
    # Cenário com gabarito e sem destino reprova: o critério 1 vale para 100%
    # do corpus, e um arnês que falhe antes de gravar não pode aprovar o resto.
    sem_destino: list[str] = []
    for cenario in cenarios:
        gab = json.loads((cenario / GABARITO).read_text(encoding="utf-8"))
        if args.destinos:
            destino, repeticoes = args.destinos[0] / cenario.name, [r / cenario.name for r in args.destinos[1:]]
        else:
            destino, repeticoes = cenario / DESTINO, []
        if not destino.is_dir():
            sem_destino.append(f"{cenario.name}: sem_destino")
            continue
        placar = placares.setdefault(gab.get("ruido", 0.0), {chave: Medida() for chave in METAS})
        for chave, m in medir(destino, gab, cenario / gab["origem"], repeticoes).items():
            placar[chave].acertos += m.acertos
            placar[chave].casos += m.casos
            falhas += [f"{cenario.name}: {chave}: {f}" for f in m.falhas]

    aprovado = not sem_destino
    for ruido in sorted(placares):
        print(f"\nPlacar com ruído de {ruido * 100:g}%")
        for chave, m in placares[ruido].items():
            if not m.casos:
                print(f"  {chave:<36} sem casos")
                continue
            meta = _meta(chave, ruido)
            ok = meta is None or m.acertos >= meta * m.casos - 1e-9
            aprovado = aprovado and ok
            alvo = "informativo" if meta is None else f"meta {meta:.0%}"
            veredito = "ok" if ok else "REPROVADO"
            print(f"  {chave:<36} {m.valor:7.1%} {m.acertos:>6}/{m.casos:<6} {alvo:<12} {veredito}")
    if sem_destino:
        print(f"\n{len(sem_destino)} de {len(cenarios)} cenários sem destino: REPROVADO")
    if falhas or sem_destino:
        print("\nFalhas:")
        for falha in sem_destino + falhas:
            print(f"  {falha}")
    return 0 if aprovado else 1


if __name__ == "__main__":
    raise SystemExit(main())
