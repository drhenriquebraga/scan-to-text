"""Monta a árvore de um destino a partir do resultado de um arquivo.

Pasta do paciente (a que `registro.achar_pasta` acha, ou nova com a data de
hoje), categoria, documento com o arquivo de conferência ao lado, ORIGINAIS,
COMPLETO.md regenerado e manifesto. Documento sem paciente vai para a pasta
DESCONHECIDO do arquivo; arquivo com mais de um paciente deixa o original em
`_ARQUIVOS COM MAIS DE UM PACIENTE` e dá a cada paciente só o que é dele.
Toda gravação passa por `atomico`, que recusa caminho dentro de origem.

As pastas de todas as unidades de um arquivo de origem são escolhidas antes
do primeiro documento, uma por pessoa no arquivo inteiro; o arquivo é misto
quando essas pastas são mais de uma.

Uma estação por vez grava no destino (`escrita_exclusiva`), e os manifestos
dele são lidos uma vez, no começo (`registro.Retrato`). A nova tentativa
depois de uma falha no meio dá a árvore de uma passada limpa:

- toda pasta que vai receber documento fica marcada com o arquivo de origem
  (`pendentes` no manifesto) antes do primeiro, e a marca sai no registro.
  A pasta nova nasce com o manifesto, que diz de quem ela é (nome e
  nascimento). A existente sem nascimento registrado só ganha o desta
  pessoa no registro do documento: a tentativa que cai antes não a deixa
  com o nascimento de quem não foi entregue ali;
- o JSON do documento é gravado antes do MD e guarda o nome do MD, o da
  conferência e a origem (caminho e sha256 do arquivo). O que uma tentativa
  anterior deste mesmo arquivo deixou é reconhecido por ela e regravado nos
  mesmos nomes, e nenhum outro arquivo toma esses nomes. O que ela deixou
  fora de manifesto, em qualquer pasta marcada onde esta tentativa não põe
  aquele documento, é apagado (`_apagar_fora_do_manifesto`), também quando
  o arquivo vai sem leitura;
- a página que um manifesto já registra não é entregue de novo: o paciente
  dela volta à pasta que a registra, mesmo que o destino tenha mudado entre
  as tentativas, se a pasta é de quem a unidade lê ali (`_ja_entregues`);
- só conta como documento da pasta, para achar repetido, o que está no
  manifesto;
- a pasta DESCONHECIDO, e a nova de paciente sem nascimento, guardam no
  manifesto a origem que as criou: entre homônimas, sem nascimento para
  separar, a nova tentativa volta à pasta que abriu (`registro.achar_pasta`).
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from PIL import Image

from scan_to_text import registro
from scan_to_text.entender import identidade, repetidos
from scan_to_text.escrever import atomico, markdown, nomes, serial
from scan_to_text.ler import pdf as leitor_pdf
from scan_to_text.ler.recipientes import Item
from scan_to_text.log import evento
from scan_to_text.tipos import Documento, Resultado
from scan_to_text.travas import Batimento, Trava

DOCUMENTOS = "documentos"
TRABALHANDO = "trabalhando.json"
TRAVA_DE_ESCRITA = "escrita"
PRAZO_DA_TRAVA_S = 120.0
# A trava de escrita segura o destino inteiro por segundos: batimento e parada
# curtos, para a de uma estação que caiu não parar as outras por 15 min.
BATIMENTO_DA_ESCRITA_S = 5.0
PARADA_DA_ESCRITA_S = 60.0
_ESPERA_DA_TRAVA_S = 0.25


class DestinoOcupado(Exception):
    """Outra estação segura a escrita do destino além do prazo."""


@dataclass(frozen=True)
class Origem:
    arquivo: Path          # cópia local do arquivo de origem, como chegou
    nome: str              # nome com que chegou (só para ORIGINAIS e manifesto)
    sha256: str            # do arquivo de origem
    item: Item | None      # o que foi lido dele (None: sem leitura, ou recipiente que não abriu)
    item_sha256: str


@dataclass(frozen=True)
class Gravador:
    pacote: object
    es: object
    estacao: str
    pid: int
    hoje: object
    limiar: float
    versoes: dict
    pasta_temp: Path
    observadas: Path | None = None   # arquivo local das observações de travas alheias
    escrita: Trava | None = None     # a trava de escrita do destino, dentro de `escrita_exclusiva`

    def conferir_escrita(self) -> None:
        """Levanta `DestinoOcupado` se a trava de escrita deixou de ser desta estação.

        A trava parada além do prazo é tomada por outra estação, que passa a
        gravar no destino: quem a perdeu não publica manifesto nenhum, e o
        arquivo volta inteiro depois.
        """
        if self.escrita is not None and not self.escrita.e_minha():
            evento("trava_de_escrita_perdida")
            raise DestinoOcupado("trava_de_escrita_perdida")

    def gravar(self, destino: Path, dados: bytes) -> None:
        self.conferir_escrita()
        atomico.gravar(destino, dados, self.es, self.estacao, self.pid)

    def copiar(self, origem: Path, destino: Path) -> None:
        self.conferir_escrita()
        atomico.copiar(origem, destino, self.es, self.estacao, self.pid)

    def apagar(self, caminho: Path) -> None:
        self.conferir_escrita()
        atomico.apagar(caminho, self.es)

    def textos(self) -> dict:
        return self.pacote["textos"]


@dataclass(frozen=True)
class _Guardado:
    doc: Documento
    saida: dict            # md, conferencia e origem; vazio em JSON sem esse registro


@contextmanager
def escrita_exclusiva(destino: Path, g: Gravador):
    """Uma estação por vez grava no destino, da escolha da pasta ao manifesto.

    A trava é do destino, e não do paciente: a pasta é achada por variante do
    nome e pelo nascimento (`registro.achar_pasta`), e uma chave por paciente
    que divergisse dessa regra deixaria duas estações na mesma pasta. Ela
    serializa também a escolha de nome livre em DESCONHECIDO e na pasta dos
    arquivos com mais de um paciente. A leitura fica fora dela. Sem a trava no
    prazo, levanta `DestinoOcupado` antes de gravar qualquer coisa. Devolve o
    gravador que confere a trava antes de cada manifesto (`conferir_escrita`).
    """
    trava = Trava(destino, TRAVA_DE_ESCRITA, g.estacao, g.pid, parada_s=PARADA_DA_ESCRITA_S,
                  observadas=g.observadas)
    limite = time.monotonic() + PRAZO_DA_TRAVA_S
    while not trava.tomar():
        if time.monotonic() >= limite:
            evento("destino_ocupado")
            raise DestinoOcupado("destino_ocupado")
        time.sleep(_ESPERA_DA_TRAVA_S)
    try:
        with Batimento([trava], intervalo_s=BATIMENTO_DA_ESCRITA_S):
            yield replace(g, escrita=trava)
    finally:
        trava.soltar()


@contextmanager
def _trabalhando(pasta: Path, g: Gravador):
    """`_scan-to-text/trabalhando.json` na pasta do paciente enquanto esta estação grava nela."""
    sinal = pasta / registro.PASTA_SISTEMA / TRABALHANDO
    dono = {"estacao": g.estacao, "pid": g.pid}
    g.gravar(sinal, json.dumps(dono).encode("utf-8"))
    try:
        yield
    finally:
        g.es.exigir_fora_das_origens(sinal)
        try:
            if json.loads(sinal.read_text(encoding="utf-8")) == dono:
                os.remove(sinal)
        except (FileNotFoundError, ValueError):
            pass


def _com_sinal(pasta: Path) -> bool:
    """Alguma estação grava na pasta agora (`trabalhando.json`); erro de acesso conta como sim."""
    try:
        os.stat(pasta / registro.PASTA_SISTEMA / TRABALHANDO)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def limpar_sinais(destino: Path, estacao: str) -> int:
    """Apaga os `trabalhando.json` desta estação que uma queda deixou; devolve quantos."""
    removidos = 0
    for sinal in Path(destino).glob(f"*/{registro.PASTA_SISTEMA}/{TRABALHANDO}"):
        try:
            if json.loads(sinal.read_text(encoding="utf-8")).get("estacao") == estacao:
                os.remove(sinal)
                removidos += 1
        except (OSError, ValueError, AttributeError):
            continue
    return removidos


def _nomes_em(pasta: Path) -> set[str]:
    try:
        return {p.name for p in pasta.iterdir()}
    except FileNotFoundError:
        return set()


def _da_serie(nome: str, base: str) -> bool:
    """`nome` é `base` ou `base (n)`, sem diferenciar maiúsculas."""
    return re.fullmatch(re.escape(base) + r"(?: \(\d+\))?", nome, re.IGNORECASE) is not None


def _vazia(pasta: Path) -> bool:
    """Pasta de verdade (não junção nem link) sem nenhum arquivo dentro."""
    try:
        if pasta.is_junction() or pasta.is_symlink() or not pasta.is_dir():
            return False
        return not any(p.is_junction() or p.is_symlink() or p.is_file() for p in pasta.rglob("*"))
    except OSError:
        return False


def _pasta_livre(destino: Path, base: str) -> Path:
    """`base`, ou `base (n)`, a primeira livre.

    A da série que não tem arquivo nenhum é de uma tentativa que caiu antes
    de gravar o primeiro, e é reaproveitada: nada nela é de ninguém.
    """
    ocupados = {n for n in _nomes_em(destino) if not (_da_serie(n, base) and _vazia(destino / n))}
    return destino / nomes.com_sufixo(base, ocupados)


def _mesmo_conteudo(a: Path, b: Path) -> bool:
    try:
        return a.is_file() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _arquivo_livre(pasta: Path, base: str, sufixo: str, conteudo: Path) -> Path:
    """`base+sufixo`, ou `base (n)+sufixo`: o primeiro livre ou já com este conteúdo."""
    existentes = {n.casefold() for n in _nomes_em(pasta)}
    n = 1
    while True:
        nome = f"{base}{sufixo}" if n == 1 else f"{base} ({n}){sufixo}"
        if nome.casefold() not in existentes or _mesmo_conteudo(pasta / nome, conteudo):
            return pasta / nome
        n += 1


def _documentos_guardados(pasta: Path) -> list[_Guardado]:
    guardados = pasta / registro.PASTA_SISTEMA / DOCUMENTOS
    if not guardados.is_dir():
        return []
    lista = []
    for arquivo in sorted(guardados.glob("*.json")):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        saida = dados.get("saida")
        lista.append(_Guardado(serial.de_dict(dados), saida if isinstance(saida, dict) else {}))
    return lista


def _pendentes(manifesto: dict) -> list[str]:
    lista = manifesto.get("pendentes")
    return [s for s in lista if isinstance(s, str)] if isinstance(lista, list) else []


def _dentro_da_categoria(pasta: Path, relativo: object) -> Path | None:
    """`pasta/<categoria>/<arquivo>`, ou `None`: o JSON não leva para fora da pasta."""
    if not isinstance(relativo, str):
        return None
    alvo = pasta / relativo
    if (alvo.parent.parent == pasta and alvo.name not in ("", ".", "..")
            and alvo.parent.name not in ("", ".", "..", registro.PASTA_SISTEMA)):
        return alvo
    return None


def _limpar_pasta(pasta: Path, manifesto: dict, limpar: dict[str, set[str]],
                  g: Gravador) -> set[str]:
    """Apaga o documento dos arquivos de origem de `limpar` que o manifesto da pasta não registra.

    É o que uma tentativa anterior gravou (JSON, MD e conferência) antes de
    cair, e que a atual não põe na pasta; fica o de id em `limpar[sha256]`,
    que ela grava ali. O que o manifesto usa fica. A conferência sai
    primeiro, porque é a que um visualizador segura aberta, e o JSON por
    último, porque enquanto existe ele reserva os nomes: a primeira recusa
    deixa o resto do documento. Com um desses na pasta, o COMPLETO.md volta a
    ter só os registrados. Devolve os arquivos de origem que não deixaram
    nada fora do manifesto na pasta.
    """
    documentos = manifesto.get("documentos")
    entradas = [e for e in (documentos if isinstance(documentos, list) else [])
                if isinstance(e, dict)]
    registrados = {e.get("id") for e in entradas if isinstance(e.get("id"), str)}
    em_uso = {e.get(campo) for e in entradas for campo in ("md", "conferencia")
              if isinstance(e.get(campo), str)}
    achou, mantidos = False, set()
    for arquivo in sorted((pasta / registro.PASTA_SISTEMA / DOCUMENTOS).glob("*.json")):
        if arquivo.stem in registrados:
            continue
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except OSError:
            # Preso por outro programa: pode ser de qualquer um deles.
            mantidos |= set(limpar)
            continue
        except ValueError:
            continue
        saida = dados.get("saida") if isinstance(dados, dict) else None
        marca = saida.get("origem") if isinstance(saida, dict) else None
        sha256 = marca.get("sha256") if isinstance(marca, dict) else None
        if not isinstance(sha256, str) or sha256 not in limpar or arquivo.stem in limpar[sha256]:
            continue
        achou = True
        alvos = [alvo for relativo in (saida.get("conferencia"), saida.get("md"))
                 if (alvo := _dentro_da_categoria(pasta, relativo)) is not None
                 and relativo not in em_uso]
        try:
            for alvo in (*alvos, arquivo):
                g.apagar(alvo)
        except OSError:
            evento("fora_do_manifesto_mantido")
            mantidos.add(sha256)
            continue
        evento("fora_do_manifesto_apagado")
    paciente = manifesto.get("paciente")
    if (achou and isinstance(paciente, dict) and isinstance(paciente.get("exibido"), str)
            and paciente["exibido"]):
        _gravar_completo(pasta, paciente["exibido"],
                         [gd.doc for gd in _documentos_guardados(pasta)
                          if gd.doc.id in registrados], g)
    return set(limpar) - mantidos


def _apagar_fora_do_manifesto(retrato: registro.Retrato, sha256: str,
                              ficam: dict[Path, set[str]], planejadas: set[Path],
                              g: Gravador) -> None:
    """Nada que uma tentativa anterior gravou fica fora de manifesto no destino.

    Uma pasta só é listada quando pode ter o que limpar: a que esta tentativa
    planeja, a que registra este arquivo, e a que o manifesto marca como
    pendente dele ou de outro arquivo de origem (`pendentes`), porque a
    tentativa marca a pasta antes do primeiro documento e só desmarca no
    último registro dela ali. Assim a pasta que a anterior usou e esta nem
    planeja também é limpa, e o que uma recusa de apagar deixou sai na
    passada seguinte de qualquer arquivo. O de outro arquivo fica enquanto
    a pasta tem `trabalhando.json`: é de uma estação que grava ali agora,
    porque a trava dela, parada além do prazo, foi tomada. A marca sai quando
    a pasta fica sem nada fora; a deste arquivo nas pastas que ele planeja
    sai no registro. O manifesto inteiro só é lido na pasta que vai ser
    listada. O que dá errado numa pasta fica no log e não impede a entrega.
    """
    for pasta in retrato.pastas():
        try:
            resumo = retrato.resumo(pasta)
            if resumo is None:
                continue
            alheios = [s for s in _pendentes(resumo) if s != sha256]
            limpar = {} if not alheios or _com_sinal(pasta) else {s: set() for s in alheios}
            if (pasta in planejadas or retrato.registros(pasta)
                    or sha256 in _pendentes(resumo)):
                limpar[sha256] = ficam.get(pasta, set())
            if not limpar:
                continue
            manifesto = registro.ler_manifesto(pasta)
            if manifesto is None:
                continue
            pendentes = _pendentes(manifesto)
            limpos = _limpar_pasta(pasta, manifesto, limpar, g)
            if pasta in planejadas:
                limpos.discard(sha256)
            restam = [s for s in pendentes if s not in limpos]
            if restam != pendentes:
                novo = {k: v for k, v in manifesto.items() if k != "pendentes"}
                if restam:
                    novo["pendentes"] = restam
                _publicar(pasta, novo, g)
                retrato.gravado(pasta, novo)
        except DestinoOcupado:
            raise
        except Exception:  # noqa: BLE001 - a limpeza não impede a entrega
            evento("fora_do_manifesto_mantido")


def _pdf_de_quadros(imagem: Path, indices: list[int], saida: Path) -> None:
    with Image.open(imagem) as fonte:
        quadros = []
        for indice in indices:
            fonte.seek(indice)
            quadros.append(fonte.convert("RGB"))
    # Sem título (o nome do arquivo temporário) e sem datas: a mesma entrada
    # dá os mesmos bytes, e a nova tentativa reconhece o que já gravou.
    quadros[0].save(saida, save_all=True, append_images=quadros[1:], resolution=300,
                    title=None, creationDate=None, modDate=None)


def _indices(doc: Documento) -> list[int]:
    return [p.indice for p in doc.paginas]


def _paginas_em_pdf(origem: Origem, indices: list[int], g: Gravador) -> Path | None:
    """Um PDF só com `indices` do item lido, numa pasta de trabalho."""
    item = origem.item
    if item.tipo not in ("pdf", "tiff", "imagem"):
        return None
    descritor, nome = tempfile.mkstemp(suffix=".pdf", dir=g.pasta_temp)
    os.close(descritor)
    saida = Path(nome)
    if item.tipo == "pdf":
        leitor_pdf.recortar(item.caminho, indices, saida)
    else:
        _pdf_de_quadros(item.caminho, indices, saida)
    return saida


def _sufixo_da_conferencia(doc: Documento, origem: Origem) -> str | None:
    item = origem.item
    if item.tipo == "docx":
        return ".docx"
    if item.tipo == "imagem" and _indices(doc) == [0]:
        return item.caminho.suffix.lower()
    if item.tipo in ("pdf", "tiff", "imagem"):
        return ".pdf"
    return None


def _gravar_conferencia(doc: Documento, origem: Origem, destino: Path, g: Gravador) -> None:
    item = origem.item
    if item.tipo == "docx" or (item.tipo == "imagem" and _indices(doc) == [0]):
        g.copiar(item.caminho, destino)
    else:
        g.copiar(_paginas_em_pdf(origem, _indices(doc), g), destino)


def caminho_de_origem(origem: Origem) -> str:
    """O caminho da unidade no manifesto: o do arquivo, com o de dentro do recipiente."""
    if origem.item is not None and origem.item.dentro:
        return f"{origem.nome}/{origem.item.dentro}"
    return origem.nome


def _marca(origem: Origem) -> dict:
    """A origem de uma unidade: o caminho (com o de dentro do recipiente) e o sha256."""
    return {"caminho": caminho_de_origem(origem), "sha256": origem.sha256}


def _entrada(doc: Documento, origem: Origem, pasta: Path, md: Path,
             conferencia: Path | None, brancos: list[int], g: Gravador) -> dict:
    return {
        "id": doc.id, "categoria": doc.categoria,
        "data": {"valor": doc.data.valor.isoformat() if doc.data.valor else None,
                 "precisao": doc.data.precisao, "papel": doc.data.papel},
        "titulo": doc.titulo, "instituicao": doc.instituicao,
        "md": md.relative_to(pasta).as_posix(),
        "conferencia": conferencia.relative_to(pasta).as_posix() if conferencia else None,
        "origens": [{"caminho": caminho_de_origem(origem), "sha256": origem.sha256,
                     "paginas": _indices(doc)}],
        "duplicatas": [],
        "paginas_em_branco": brancos,
        "paginas_leitura_incerta": [p.indice for p in doc.paginas
                                    if p.confianca_media < g.limiar],
    }


def _pasta_desconhecido(destino: Path, origem: Origem, g: Gravador) -> Path:
    """A pasta DESCONHECIDO desta unidade: a que uma tentativa anterior criou, ou uma livre."""
    base = nomes.pasta_desconhecido(g.hoje, origem.nome, g.pacote)
    marca = _marca(origem)
    for nome in sorted(_nomes_em(destino)):
        if not _da_serie(nome, base):
            continue
        try:
            manifesto = registro.ler_manifesto(destino / nome)
        except (registro.ManifestoInvalido, OSError):
            continue
        if manifesto and manifesto.get("paciente") is None and manifesto.get("origem") == marca:
            return destino / nome
    return _pasta_livre(destino, base)


def _compativeis(g: Gravador):
    return lambda a, b: identidade.compativeis(a, b, pacote=g.pacote)


def _pasta_do_paciente(destino: Path, paciente: str, docs: list[Documento], origem: Origem,
                       g: Gravador, no_arquivo: frozenset, retrato: registro.Retrato) -> Path:
    nascimento = next((d.nascimento for d in docs if d.nascimento), None)
    achada = registro.achar_pasta(destino, paciente, nascimento, _compativeis(g), identidade.chave,
                                  origem=_marca(origem), no_arquivo=no_arquivo, retrato=retrato)
    return achada or _pasta_livre(destino, nomes.pasta_paciente(paciente, g.hoje))


def _nome_do_item(origem: Origem) -> tuple[str, str]:
    """Nome base e sufixo do item lido: o do arquivo, ou o de dentro do recipiente."""
    if not origem.item.dentro:
        nome = Path(origem.nome)
        return nome.stem, nome.suffix
    dentro = PurePosixPath(origem.item.dentro)
    return (nomes.normalizar(dentro.stem) or origem.item.caminho.stem), origem.item.caminho.suffix


def _originais(pasta: Path, origem: Origem, docs: list[Documento], arquivo_misto: bool,
               item_misto: bool, g: Gravador) -> str | None:
    """O original em ORIGINAIS: o arquivo inteiro, só o item, ou só as páginas desta pasta."""
    destino_originais = pasta / g.textos()["pastas"]["originais"]
    if not arquivo_misto:
        nome = Path(origem.nome)
        alvo = _arquivo_livre(destino_originais, nome.stem, nome.suffix, origem.arquivo)
        g.copiar(origem.arquivo, alvo)
        return alvo.name
    if origem.item is None:
        return None
    base, sufixo = _nome_do_item(origem)
    fonte = origem.item.caminho
    if item_misto:
        if origem.item.tipo != "docx":
            fonte = _paginas_em_pdf(origem, sorted({p.indice for d in docs for p in d.paginas}), g)
            sufixo = ".pdf"
        base = f"{base} {g.textos()['arquivos']['paginas_deste_paciente']}"
    alvo = _arquivo_livre(destino_originais, base, sufixo, fonte)
    g.copiar(fonte, alvo)
    return alvo.name


def _paciente(antes: dict, paciente: str, docs: list[Documento], confianca: float) -> dict:
    """O paciente do manifesto depois de receber `docs` com o nome lido `paciente`."""
    nascimento = next((d.nascimento for d in docs if d.nascimento), None)
    return {
        "exibido": antes.get("exibido") or paciente,
        "variantes": sorted({*antes.get("variantes", []), paciente}),
        "nascimento": antes.get("nascimento") or (nascimento.isoformat() if nascimento else None),
        "confianca": max(float(antes.get("confianca", 0.0)), round(confianca, 2)),
    }


def _publicar(pasta: Path, manifesto: dict, g: Gravador) -> None:
    g.conferir_escrita()
    registro.gravar_manifesto(pasta, manifesto, g.es, g.estacao, g.pid)


def _gravar_manifesto(pasta: Path, paciente: str | None, docs: list[Documento],
                      entradas: list[dict], duplicatas: dict[str, list[dict]],
                      original: str | None, g: Gravador, confianca: float = 0.0,
                      brancos: dict | None = None, origem: Origem | None = None,
                      pendente: bool = False) -> dict:
    """Grava o manifesto da pasta com o que a unidade `origem` põe nela; devolve o gravado.

    Com `pendente`, mantém a marca do arquivo de origem: outra unidade ainda
    vai gravar na pasta; sem, é o último registro, e a marca dele sai.
    """
    m = registro.ler_manifesto(pasta)
    if m is None:
        m = {"pasta": pasta.name, "primeiro_processamento": g.hoje.isoformat(),
             "documentos": [], "originais": [], "paciente": None}
        if origem is not None and (paciente is None or not any(d.nascimento for d in docs)):
            m["origem"] = _marca(origem)
    m["ultima_atualizacao"] = g.hoje.isoformat()
    m["versoes"] = g.versoes
    if paciente is not None:
        m["paciente"] = _paciente(m.get("paciente") or {}, paciente, docs, confianca)
    if origem is not None:
        pendentes = set(_pendentes(m))
        if pendente:
            pendentes.add(origem.sha256)
        else:
            pendentes.discard(origem.sha256)
        m.pop("pendentes", None)
        if pendentes:
            m["pendentes"] = sorted(pendentes)
    existentes = {e["id"] for e in m["documentos"]}
    m["documentos"] = [e for e in m["documentos"]] + [e for e in entradas if e["id"] not in existentes]
    # Toda cópia fica registrada no documento que ela repete; sem ele no
    # manifesto, a gravação para em vez de perder a página em silêncio.
    if set(duplicatas) - {e["id"] for e in m["documentos"]}:
        raise ValueError("duplicata_sem_documento")
    for entrada in m["documentos"]:
        for extra in duplicatas.get(entrada["id"], []):
            if extra not in entrada["duplicatas"]:
                entrada["duplicatas"].append(extra)
    if original and original not in m["originais"]:
        m["originais"] = sorted([*m["originais"], original])
    # Sem documento novo na pasta (só repetidos, ou só páginas em branco), as
    # páginas em branco ficam na raiz, na forma de `origens[]`.
    if brancos and brancos not in m.setdefault("paginas_em_branco", []):
        m["paginas_em_branco"].append(brancos)
    _publicar(pasta, m, g)
    return m


def _manifesto_inicial(pasta: Path, paciente: str | None, docs: list[Documento],
                       origem: Origem, g: Gravador, confianca: float,
                       retrato: registro.Retrato) -> None:
    """A pasta fica marcada com o arquivo de origem antes de qualquer documento dele.

    A pasta nova nasce assim, com o manifesto que diz de quem ela é. A
    existente sem nascimento registrado mostra logo o desta pessoa à escolha
    da pasta da unidade seguinte (`registro.Retrato.adotar`), e a homônima
    do mesmo arquivo vai para outra; no disco, o nascimento só entra no
    registro do documento.
    """
    m = retrato.resumo(pasta)
    if m is None:
        retrato.gravado(pasta, _gravar_manifesto(pasta, paciente, docs, [], {}, None, g,
                                                 confianca, origem=origem, pendente=True))
        return
    visto = retrato.visto(pasta) or {}
    if (paciente is not None and not (visto.get("paciente") or {}).get("nascimento")
            and any(d.nascimento for d in docs)):
        retrato.adotar(pasta, _paciente(visto.get("paciente") or {}, paciente, docs, confianca))
    if origem.sha256 not in _pendentes(m):
        inteiro = registro.ler_manifesto(pasta)
        marcado = {**inteiro, "pendentes": sorted({*_pendentes(inteiro), origem.sha256})}
        _publicar(pasta, marcado, g)
        retrato.gravado(pasta, marcado)


def _sem_documento(codigo: str, origem: Origem, pasta: Path, g: Gravador, misto: bool,
                   brancos: dict | None = None, pendente: bool = False) -> list[str]:
    """Item ilegível ou só com páginas em branco: DESCONHECIDO, LEIA-ME e o original."""
    textos = g.textos()
    motivo = textos["leia_me"].get(codigo, codigo)
    g.gravar(pasta / textos["arquivos"]["leia_me"], (motivo + "\n").encode("utf-8"))
    original = _originais(pasta, origem, [], misto, False, g)
    _gravar_manifesto(pasta, None, [], [], {}, original, g, brancos=brancos, origem=origem,
                      pendente=pendente)
    return [pasta.name]


def _relativo(caminho: Path | None, pasta: Path) -> str | None:
    return caminho.relative_to(pasta).as_posix() if caminho is not None else None


def _nomes_do_documento(pasta: Path, doc: Documento, origem: Origem, anterior: dict | None,
                        reservados: set[str], g: Gravador) -> tuple[Path, Path | None]:
    """O MD e a conferência do documento: os que a tentativa anterior reservou, ou novos.

    Nome novo evita os que estão na pasta e os reservados no JSON de outro
    documento, inclusive o de uma escrita que caiu antes de gravar o MD.
    """
    categoria = nomes.pasta_categoria(doc.categoria, g.pacote)
    pasta_categoria = pasta / categoria
    sufixo = _sufixo_da_conferencia(doc, origem)
    md_anterior = PurePosixPath((anterior or {}).get("md") or "")
    if md_anterior.parent.as_posix() == categoria and md_anterior.suffix == ".md":
        base = md_anterior.stem
    else:
        base = nomes.nome_documento(doc.data, doc.titulo, g.pacote)
        if not nomes.cabe_no_caminho(pasta_categoria, base + ".md"):
            base = nomes.nome_documento(doc.data, "", g.pacote)
        ocupados = {Path(n).stem for n in _nomes_em(pasta_categoria)}
        ocupados |= {PurePosixPath(r).stem for r in reservados
                     if PurePosixPath(r).parent.as_posix() == categoria}
        base = nomes.com_sufixo(base, ocupados)
    return pasta_categoria / f"{base}.md", (pasta_categoria / f"{base}{sufixo}" if sufixo else None)


def _escrever_pasta(pasta: Path, paciente: str | None, docs: list[Documento], origem: Origem,
                    g: Gravador, confianca: float, arquivo_misto: bool, item_misto: bool,
                    brancos: dict | None, pendente: bool) -> str:
    with _trabalhando(pasta, g):
        manifesto = registro.ler_manifesto(pasta) or {}
        registrados = {e.get("id") for e in manifesto.get("documentos", []) if isinstance(e, dict)}
        marca, meus = _marca(origem), {d.id for d in docs}
        todos = _documentos_guardados(pasta)
        anteriores = {gd.doc.id: gd.saida for gd in todos
                      if gd.doc.id in meus and gd.saida.get("origem") == marca}
        guardados = [gd.doc for gd in todos
                     if gd.doc.id in registrados and gd.doc.id not in anteriores]
        reservados = {r for gd in todos for r in (gd.saida.get("md"), gd.saida.get("conferencia"))
                      if isinstance(r, str)}
        entradas, duplicatas, novos, alvo = [], {}, [], {}
        for doc in docs:
            if doc.id in anteriores:
                original = None  # o que esta origem já gravou aqui continua dela
            elif doc.repetido_de:
                original = alvo.get(doc.repetido_de, doc.repetido_de)
            else:
                # A pasta já decidiu que é o mesmo paciente: a grafia do nome
                # lido não separa duas cópias do mesmo exame.
                original = next((a.id for a in guardados
                                 if repetidos.mesmo_exame(replace(a, paciente=doc.paciente), doc,
                                                         pacote=g.pacote)),
                                None)
            if original:
                alvo[doc.id] = original
                duplicatas.setdefault(original, []).append(
                    {"caminho": caminho_de_origem(origem), "sha256": origem.sha256,
                     "paginas": _indices(doc)})
                continue
            md, conferencia = _nomes_do_documento(pasta, doc, origem, anteriores.get(doc.id),
                                                  reservados, g)
            saida = {"md": _relativo(md, pasta), "conferencia": _relativo(conferencia, pasta),
                     "origem": marca}
            reservados |= {r for r in (saida["md"], saida["conferencia"]) if r}
            g.gravar(pasta / registro.PASTA_SISTEMA / DOCUMENTOS / f"{doc.id}.json",
                     json.dumps({**serial.para_dict(doc), "saida": saida}, ensure_ascii=False,
                                indent=1, sort_keys=True).encode("utf-8"))
            g.gravar(md, markdown.md_documento(doc, g.pacote, g.limiar).encode("utf-8"))
            if conferencia is not None:
                _gravar_conferencia(doc, origem, conferencia, g)
            # As páginas em branco do arquivo vão no primeiro documento novo da pasta.
            entradas.append(_entrada(doc, origem, pasta, md, conferencia,
                                     [] if entradas or not brancos else brancos["paginas"], g))
            novos.append(doc)
        original = _originais(pasta, origem, docs, arquivo_misto, item_misto, g)
        if paciente is not None:
            todos_docs = guardados + [d for d in novos if d.id not in {a.id for a in guardados}]
            exibido = (manifesto.get("paciente") or {}).get("exibido") or paciente
            _gravar_completo(pasta, exibido, todos_docs, g)
        _gravar_manifesto(pasta, paciente, docs, entradas, duplicatas, original, g, confianca,
                          None if entradas else brancos, origem=origem, pendente=pendente)
    return pasta.name


def _gravar_completo(pasta: Path, exibido: str, docs: list[Documento], g: Gravador) -> None:
    docs = [replace(d, paciente=exibido) for d in docs]
    g.gravar(pasta / g.textos()["arquivos"]["completo"],
             markdown.md_completo(exibido, docs, g.pacote, g.limiar).encode("utf-8"))


def _guardar_misto(origem: Origem, destino: Path, g: Gravador) -> None:
    pasta_mista = destino / g.textos()["pastas"]["mais_de_um_paciente"]
    nome = Path(origem.nome)
    g.copiar(origem.arquivo, _arquivo_livre(pasta_mista, nome.stem, nome.suffix, origem.arquivo))


def _grupos(resultado: Resultado) -> dict[str | None, list[Documento]]:
    grupos: dict[str | None, list[Documento]] = {}
    for doc in resultado.documentos:
        grupos.setdefault(doc.paciente, []).append(doc)
    return grupos


def _pessoa(paciente: str, docs: list[Documento]) -> tuple:
    return identidade.chave(paciente), next((d.nascimento for d in docs if d.nascimento), None)


def _ja_entregues(unidades: Sequence[tuple[Resultado, Origem, dict[str, float]]],
                  registradas: dict, retrato: registro.Retrato,
                  g: Gravador) -> list[dict[str, Path]]:
    """A pasta de cada paciente de cada unidade com página que um manifesto já registra.

    Uma tentativa anterior entregou ali, e o destino pode ter mudado depois
    (chegou uma homônima): a nova tentativa não entrega a mesma página noutra
    pasta. Vale só a pasta que continua sendo de quem a unidade lê ali, pela
    regra de `registro.achar_pasta` (`registro.e_da_pessoa`): com o
    nascimento da pessoa, ou com qualquer um para a pessoa sem nascimento. A
    leitura que mudou entre as tentativas, com outro nome ou nascimento, não
    segue a pasta da anterior. O que não foi entregue, mesmo da mesma pessoa
    noutra unidade, não segue a pasta: vai pela regra de sempre.
    """
    entregues: list[dict[str, Path]] = []
    for resultado, origem, _ in unidades:
        caminho = caminho_de_origem(origem)
        da_unidade: dict[str, Path] = {}
        for paciente, docs in _grupos(resultado).items():
            if paciente is None:
                continue
            nascimento = _pessoa(paciente, docs)[1]
            for doc in docs:
                ja = registradas.get((caminho, tuple(_indices(doc))))
                if (ja and (nascimento is None or ja[1] == nascimento.isoformat())
                        and registro.e_da_pessoa(ja[0], paciente, nascimento, _compativeis(g),
                                                 identidade.chave, retrato)):
                    da_unidade[paciente] = ja[0]
                    break
        entregues.append(da_unidade)
    return entregues


def _preparar(resultado: Resultado, origem: Origem, destino: Path, g: Gravador,
              confiancas: dict[str, float], escolhidas: dict, nascimentos: dict,
              entregues: dict[str, Path], retrato: registro.Retrato) -> dict[str | None, Path]:
    """A pasta de cada paciente da unidade, já marcada; `None` é a DESCONHECIDO.

    O paciente com página já registrada fica na pasta que a registra
    (`entregues`). Para os outros, `escolhidas` vale para o arquivo de
    origem inteiro: o mesmo nome, pela chave, com o mesmo nascimento, fica na
    mesma pasta em todas as unidades.
    """
    grupos = {None: []} if resultado.ilegivel or not resultado.documentos else _grupos(resultado)
    pastas: dict[str | None, Path] = {}
    for paciente, docs in grupos.items():
        if paciente is None:
            pasta = _pasta_desconhecido(destino, origem, g)
        elif paciente in entregues:
            pasta = entregues[paciente]
            resumo = retrato.resumo(pasta) or {}
            # A pasta da dúvida foi aberta por este arquivo inteiro: as
            # unidades ainda não entregues continuam juntas nela. Uma pasta
            # anterior, com ou sem nascimento, não transmite a decisão.
            if (not (resumo.get("paciente") or {}).get("nascimento")
                    and (resumo.get("origem") or {}).get("sha256") == origem.sha256):
                escolhidas.setdefault(_pessoa(paciente, docs), pasta)
        else:
            pessoa = _pessoa(paciente, docs)
            if pessoa not in escolhidas:
                escolhidas[pessoa] = _pasta_do_paciente(
                    destino, paciente, docs, origem, g,
                    frozenset(nascimentos.get(identidade.chave(paciente), ())), retrato)
            pasta = escolhidas[pessoa]
        _manifesto_inicial(pasta, paciente, docs, origem, g,
                           confiancas.get(paciente, 0.0) if paciente else 0.0, retrato)
        pastas[paciente] = pasta
    return pastas


def _escrever_unidade(resultado: Resultado, origem: Origem, destino: Path, g: Gravador,
                      confiancas: dict[str, float], misto: bool,
                      pastas: dict[str | None, Path], pendentes: set[Path]) -> list[str]:
    if misto:
        _guardar_misto(origem, destino, g)
    if resultado.ilegivel:
        return _sem_documento(resultado.ilegivel, origem, pastas[None], g, misto,
                               pendente=pastas[None] in pendentes)
    brancos = ({"caminho": caminho_de_origem(origem), "sha256": origem.sha256,
                "paginas": list(resultado.paginas_em_branco)}
               if resultado.paginas_em_branco else None)
    if not resultado.documentos:
        return _sem_documento("em_branco", origem, pastas[None], g, misto, brancos,
                               pendente=pastas[None] in pendentes)
    grupos = _grupos(resultado)
    item_misto = len(grupos) > 1 or resultado.multi_paciente
    if item_misto and not misto:
        _guardar_misto(origem, destino, g)
    return [_escrever_pasta(pastas[paciente], paciente, docs, origem, g,
                            confiancas.get(paciente, 0.0) if paciente else 0.0,
                            misto or item_misto, item_misto, brancos, pastas[paciente] in pendentes)
            for paciente, docs in grupos.items()]


def escrever_arquivo(unidades: Sequence[tuple[Resultado, Origem, dict[str, float]]],
                     destino: Path, g: Gravador, nascimentos: dict | None = None,
                     registradas: dict | None = None,
                     retrato: registro.Retrato | None = None) -> list[str]:
    """Grava no destino as unidades de um arquivo de origem; devolve as pastas tocadas.

    Roda dentro de `escrita_exclusiva` do mesmo destino. Cada unidade leva o
    resultado, a origem e as confianças: o paciente à fração das páginas dele
    em que o nome foi lido com rótulo, que vai para o manifesto.
    `nascimentos` leva a chave de cada nome aos nascimentos que o arquivo
    inteiro mostra para ele: com mais de um, nenhuma unidade desse nome
    adota pasta sem nascimento registrado (`registro.achar_pasta`).
    `registradas` são as páginas do arquivo que um manifesto do destino já
    registra (`registro.paginas_registradas`): o paciente delas fica na
    pasta que as registra (`_ja_entregues`). `retrato` são os manifestos do
    destino lidos no começo da escrita exclusiva.

    Primeiro as pastas de todas as unidades são escolhidas e marcadas, e as
    novas nascem com o manifesto. O arquivo é misto quando essas pastas são
    mais de uma (todas as DESCONHECIDO contam como uma, porque nenhuma diz de
    quem é): o original vai inteiro só para `_ARQUIVOS COM MAIS DE UM
    PACIENTE`, e cada pasta recebe em ORIGINAIS só o próprio item, ou as
    páginas dele.
    """
    retrato = retrato if retrato is not None else registro.Retrato(destino)
    entregues = _ja_entregues(unidades, registradas or {}, retrato, g)
    escolhidas: dict = {}
    planos = [_preparar(resultado, origem, destino, g, confiancas or {}, escolhidas,
                        nascimentos or {}, da_unidade, retrato)
              for (resultado, origem, confiancas), da_unidade in zip(unidades, entregues)]
    ficam: dict[Path, set[str]] = {}
    for (resultado, _, _), plano in zip(unidades, planos):
        for doc in resultado.documentos:
            ficam.setdefault(plano[doc.paciente], set()).add(doc.id)
    _apagar_fora_do_manifesto(retrato, unidades[0][1].sha256, ficam,
                              {pasta for plano in planos for pasta in plano.values()}, g)
    usadas = {pasta if paciente is not None else None
              for plano in planos for paciente, pasta in plano.items()}
    misto = len(usadas) > 1 or any(resultado.multi_paciente for resultado, _, _ in unidades)
    tocadas: list[str] = []
    ultimas = {pasta: i for i, plano in enumerate(planos) for pasta in plano.values()}
    for i, ((resultado, origem, confiancas), plano) in enumerate(zip(unidades, planos)):
        pendentes = {pasta for pasta in plano.values() if ultimas[pasta] != i}
        tocadas += _escrever_unidade(resultado, origem, destino, g, confiancas or {}, misto,
                                     plano, pendentes)
    return tocadas
