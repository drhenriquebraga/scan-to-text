"""Registro no destino: manifesto do paciente, feitos e reaproveitamento de pasta.

O manifesto (`<pasta do paciente>/_scan-to-text/manifesto.json`) é o único
lugar com nome de arquivo de origem e datas de processamento. É gravado de
forma atômica e determinística (chaves ordenadas, UTF-8, datas em ISO), para
a mesma entrada dar os mesmos bytes em qualquer estação.

Campos exigidos, além de `versao_esquema`, que é posto aqui:

- `pasta`, `primeiro_processamento`, `ultima_atualizacao`, `originais`, `versoes`;
- `paciente`: `None` (pasta DESCONHECIDO) ou `exibido`, `variantes`,
  `nascimento` (ISO ou `None`) e `confianca`;
- `documentos[]`: `id`, `categoria`, `data` (`valor`, `precisao`, `papel`),
  `titulo`, `instituicao`, `md`, `conferencia`, `origens[]` (`caminho`,
  `sha256`, `paginas`), `duplicatas`, `paginas_em_branco`,
  `paginas_leitura_incerta`.

Campo a mais é aceito; campo faltando recusa a gravação. A pasta DESCONHECIDO,
e a de paciente aberta sem nascimento, levam também `origem` (`caminho`,
`sha256`), a unidade que a criou: é por ela que a nova tentativa depois de uma
falha acha a pasta, em vez de abrir outra. `pendentes` leva o sha256 de cada
arquivo de origem que começou a gravar documento na pasta e ainda não o
registrou: é por ele que uma passada seguinte acha o que ficou fora do
manifesto sem listar todas as pastas do destino.

Dentro da trava de escrita, os manifestos do destino são lidos uma vez só
(`Retrato`), e a escolha da pasta, as páginas já registradas e a limpeza usam
essa leitura. Dela fica só o resumo de cada manifesto; quem grava relê o
manifesto inteiro da pasta.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path, PurePath

from scan_to_text.es import ES
from scan_to_text.escrever.atomico import gravar
from scan_to_text.log import evento

PASTA_SISTEMA = "_scan-to-text"
MANIFESTO = "manifesto.json"
FEITOS = "feitos"
VERSAO_ESQUEMA = 1

_PACIENTE = ("exibido", "variantes", "nascimento", "confianca")
_DOCUMENTO = ("id", "categoria", "data", "titulo", "instituicao", "md", "conferencia",
              "origens", "duplicatas", "paginas_em_branco", "paginas_leitura_incerta")
_DATA = ("valor", "precisao", "papel")
_ORIGEM = ("caminho", "sha256", "paginas")
_RAIZ = ("versao_esquema", "pasta", "primeiro_processamento", "ultima_atualizacao",
         "paciente", "documentos", "originais", "versoes")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PASTA_PACIENTE = re.compile(r"(?P<nome>.+) - \d{4}-\d{2}-\d{2}(?: \(\d+\))?")
_HOMONIMO = 3


class ManifestoInvalido(ValueError):
    """Manifesto ilegível ou incompleto; `faltando` lista os campos ausentes."""

    def __init__(self, codigo: str, faltando: Sequence[str] = ()):
        super().__init__(codigo)
        self.faltando = list(faltando)


def _faltando(dados: object, campos: Sequence[str], prefixo: str) -> list[str]:
    if not isinstance(dados, dict):
        return [prefixo.rstrip(".") or "manifesto"]
    return [prefixo + campo for campo in campos if campo not in dados]


def _validar(m: dict) -> list[str]:
    faltam = _faltando(m, _RAIZ, "")
    paciente = m.get("paciente")
    if paciente is not None:
        faltam += _faltando(paciente, _PACIENTE, "paciente.")
    documentos = m.get("documentos", [])
    if not isinstance(documentos, list):
        return faltam + ["documentos"]
    for i, doc in enumerate(documentos):
        base = f"documentos[{i}]."
        faltam += _faltando(doc, _DOCUMENTO, base)
        if not isinstance(doc, dict):
            continue
        if "data" in doc:
            faltam += _faltando(doc["data"], _DATA, base + "data.")
        origens = doc.get("origens", [])
        for j, origem in enumerate(origens if isinstance(origens, list) else []):
            faltam += _faltando(origem, _ORIGEM, f"{base}origens[{j}].")
    return faltam


def _para_json(valor: object) -> object:
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, PurePath):
        return valor.as_posix()
    raise TypeError(type(valor).__name__)


def _serializar(dados: dict) -> bytes:
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True, default=_para_json)
    return (texto + "\n").encode("utf-8")


def ler_manifesto(pasta_paciente: Path) -> dict | None:
    """O manifesto da pasta, `None` se não houver; ilegível levanta `ManifestoInvalido`."""
    arquivo = Path(pasta_paciente) / PASTA_SISTEMA / MANIFESTO
    try:
        bruto = arquivo.read_bytes()
    except FileNotFoundError:
        return None
    try:
        manifesto = json.loads(bruto.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ManifestoInvalido("manifesto_ilegivel") from erro
    if not isinstance(manifesto, dict):
        raise ManifestoInvalido("manifesto_ilegivel")
    return manifesto


def gravar_manifesto(pasta_paciente: Path, manifesto: dict, es: ES, estacao: str,
                     pid: int) -> None:
    """Grava `_scan-to-text/manifesto.json`, no esquema do módulo, `versao_esquema` = 1."""
    completo = {**manifesto, "versao_esquema": VERSAO_ESQUEMA}
    faltam = _validar(completo)
    if faltam:
        evento("manifesto_incompleto", campos=len(faltam))
        raise ManifestoInvalido("manifesto_incompleto", faltam)
    gravar(Path(pasta_paciente) / PASTA_SISTEMA / MANIFESTO, _serializar(completo), es,
           estacao, pid)


def _data_iso(valor: object) -> date | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        return date.fromisoformat(valor)
    raise ValueError("nascimento_invalido")


def _tem_area_do_sistema(pasta: Path) -> bool:
    """`_scan-to-text/` existe na pasta; erro de acesso conta como sim."""
    try:
        os.stat(Path(pasta) / PASTA_SISTEMA)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


_ILEGIVEL = object()
_RESUMO = ("paciente", "origem", "pendentes")


class Retrato:
    """O resumo dos manifestos das pastas de paciente de um destino, lidos uma vez.

    Tirado dentro da trava de escrita, quando nenhuma outra estação grava no
    destino; o que esta tentativa grava depois entra por `gravado`. De cada
    manifesto ficam só o paciente, a origem, as marcas `pendentes` e o que
    ele registra do arquivo de origem `sha256`: o destino só cresce, e os
    manifestos inteiros de todas as pastas não cabem na memória da estação.
    A pasta sem nascimento que uma pessoa adota mostra o nascimento dela à
    escolha da pasta seguinte (`adotar`), mas o disco só o recebe junto com o
    documento: a tentativa que cai antes não deixa a pasta com o nascimento
    de quem não foi entregue ali.
    """

    def __init__(self, destino: Path, sha256: str | None = None):
        self.sha256 = sha256
        self._pastas = pastas(destino)
        self._resumos: dict[Path, object] = {}
        self._registros: dict[Path, list[dict]] = {}
        self._adotados: dict[Path, dict] = {}
        for pasta in self._pastas:
            self._ler(pasta)

    def _ler(self, pasta: Path) -> None:
        try:
            self.gravado(pasta, ler_manifesto(pasta), nova=False)
        except (ManifestoInvalido, OSError):
            self._resumos[pasta] = _ILEGIVEL

    def pastas(self) -> list[Path]:
        """As pastas do retrato em ordem de nome, com as que esta tentativa criou."""
        return list(self._pastas)

    def resumo(self, pasta: Path) -> dict | None:
        """Paciente, origem e `pendentes` da pasta; o ilegível levanta `ManifestoInvalido`."""
        pasta = Path(pasta)
        if pasta not in self._resumos:
            self._ler(pasta)
        resumo = self._resumos[pasta]
        if resumo is _ILEGIVEL:
            raise ManifestoInvalido("manifesto_ilegivel")
        return resumo

    def registros(self, pasta: Path) -> list[dict]:
        """As `origens[]` e `duplicatas[]` do manifesto da pasta que são do arquivo `sha256`."""
        self.resumo(pasta)
        return self._registros.get(Path(pasta), [])

    def visto(self, pasta: Path) -> dict | None:
        """O resumo como a escolha da pasta o vê: com a pessoa que a adotou."""
        resumo = self.resumo(pasta)
        paciente = self._adotados.get(Path(pasta))
        return {**resumo, "paciente": paciente} if paciente and resumo else resumo

    def gravado(self, pasta: Path, manifesto: dict | None, nova: bool = True) -> None:
        pasta = Path(pasta)
        if manifesto is None:
            self._resumos[pasta], self._registros[pasta] = None, []
        else:
            self._resumos[pasta] = {campo: manifesto[campo] for campo in _RESUMO
                                    if campo in manifesto}
            self._registros[pasta] = (list(_registros(manifesto, self.sha256))
                                      if self.sha256 else [])
        if nova and pasta not in self._pastas:
            self._pastas = sorted([*self._pastas, pasta], key=lambda p: p.name)

    def adotar(self, pasta: Path, paciente: dict) -> None:
        self._adotados[Path(pasta)] = paciente


def _do_destino(destino: Path, retrato: Retrato | None) -> Retrato:
    return retrato if retrato is not None else Retrato(destino)


def _avaliar(pasta: Path, retrato: Retrato, nome_exibido: str, alvo: str,
             nascimento: date | None, compativeis: Callable[[str, str], bool],
             chave: Callable[[str], str]) -> tuple[int, date | None] | None:
    """(nível, nascimento registrado) de uma pasta candidata, ou `None`.

    Nível 0: chave idêntica e nascimento confirmado; 1: chave idêntica;
    2: variante compatível, ou registrada, com nascimento igual;
    `_HOMONIMO`: chave idêntica e nascimento registrado diferente, que não é
    candidata, mas diz que o nome é de mais de uma pessoa.

    A chave idêntica é só a do nome que nomeia a pasta: o exibido e o tirado
    do nome da pasta. As variantes registradas entraram pelo nascimento, e
    sem ele uma abreviação pode ser de outra pessoa.
    """
    casado = _PASTA_PACIENTE.fullmatch(pasta.name)
    try:
        manifesto = retrato.visto(pasta)
    except (ManifestoInvalido, OSError):
        # Na dúvida, pasta nova: melhor faltar do que misturar pacientes.
        return None
    variantes: list[object] = []
    if manifesto is None:
        if not casado or _tem_area_do_sistema(pasta):
            # A pasta que o app cria nasce com o manifesto. Área do sistema sem
            # ele é escrita interrompida: não diz de quem é nem o nascimento.
            return None
        principais, registrado = [casado["nome"]], None
    else:
        paciente = manifesto.get("paciente")
        if not isinstance(paciente, dict):
            return None
        try:
            registrado = _data_iso(paciente.get("nascimento"))
        except ValueError:
            return None
        principais = [paciente.get("exibido")] + ([casado["nome"]] if casado else [])
        if isinstance(paciente.get("variantes"), list):
            variantes = paciente["variantes"]
    principais = [n for n in principais if isinstance(n, str) and n.strip()]
    todos = principais + [n for n in variantes if isinstance(n, str) and n.strip()]
    ambos = registrado is not None and nascimento is not None
    identica = any(chave(n) == alvo for n in principais)
    if ambos and registrado != nascimento:
        return (_HOMONIMO, registrado) if identica else None
    if identica:
        return (0 if ambos else 1), registrado
    if ambos and any(chave(n) == alvo or compativeis(nome_exibido, n) for n in todos):
        return 2, registrado
    return None


def pastas(destino: Path) -> list[Path]:
    """As pastas do destino em ordem de nome, sem as de `_`, junção nem link."""
    try:
        entradas = sorted(os.scandir(destino), key=lambda e: e.name)
    except FileNotFoundError:
        return []
    achadas = []
    for entrada in entradas:
        try:
            if (entrada.name.startswith("_") or entrada.is_junction() or entrada.is_symlink()
                    or not entrada.is_dir(follow_symlinks=False)):
                continue
        except OSError:
            continue
        achadas.append(Path(entrada.path))
    return achadas


def _avaliadas(retrato: Retrato, nome_exibido: str, nascimento: date | None,
               compativeis: Callable[[str, str], bool], chave: Callable[[str], str]
               ) -> list[tuple[int, date | None, str]]:
    """(nível, nascimento registrado, caminho) das pastas de paciente que casam com o nome."""
    alvo = chave(nome_exibido)
    achadas: list[tuple[int, date | None, str]] = []
    for pasta in retrato.pastas():
        avaliacao = _avaliar(pasta, retrato, nome_exibido, alvo, nascimento, compativeis, chave)
        if avaliacao:
            achadas.append((*avaliacao, str(pasta)))
    return achadas


def e_da_pessoa(pasta: Path, nome_exibido: str, nascimento: date | None,
                compativeis: Callable[[str, str], bool], chave: Callable[[str], str],
                retrato: Retrato) -> bool:
    """Se a pasta é de quem tem esse nome e nascimento, pela regra de `achar_pasta`.

    Chave idêntica à do nome da pasta, ou variante com o mesmo nascimento;
    o homônimo, de nascimento registrado diferente, não é.
    """
    avaliacao = _avaliar(Path(pasta), retrato, nome_exibido, chave(nome_exibido), nascimento,
                         compativeis, chave)
    return avaliacao is not None and avaliacao[0] != _HOMONIMO


def _registros(manifesto: dict, sha256: str):
    """As `origens[]` e `duplicatas[]` do manifesto que são do arquivo de origem `sha256`."""
    documentos = manifesto.get("documentos")
    for documento in documentos if isinstance(documentos, list) else []:
        if not isinstance(documento, dict):
            continue
        for campo in ("origens", "duplicatas"):
            lista = documento.get(campo)
            for o in lista if isinstance(lista, list) else []:
                if isinstance(o, dict) and o.get("sha256") == sha256:
                    yield o


def paginas_registradas(destino: Path, sha256: str, retrato: Retrato | None = None
                        ) -> dict[tuple[str, tuple[int, ...]], tuple[Path, str | None]]:
    """As páginas do arquivo de origem `sha256` que uma pasta de paciente já registra.

    Leva (caminho, páginas) de `origens[]` e de `duplicatas[]` à pasta que as
    registra e ao nascimento registrado nela. É o que já foi entregue: a nova
    tentativa depois de uma falha não o entrega de novo noutra pasta, se a
    pasta continua sendo de quem ela lê ali (`e_da_pessoa`).
    """
    if retrato is None or retrato.sha256 != sha256:
        retrato = Retrato(destino, sha256)
    registradas: dict[tuple[str, tuple[int, ...]], tuple[Path, str | None]] = {}
    for pasta in retrato.pastas():
        try:
            resumo = retrato.resumo(pasta) or {}
        except (ManifestoInvalido, OSError):
            continue
        paciente = resumo.get("paciente")
        if not isinstance(paciente, dict):
            continue
        nascimento = paciente.get("nascimento")
        nascimento = nascimento if isinstance(nascimento, str) else None
        for o in retrato.registros(pasta):
            if (isinstance(o.get("caminho"), str) and isinstance(o.get("paginas"), list)
                    and all(type(p) is int for p in o["paginas"])):
                registradas.setdefault((o["caminho"], tuple(o["paginas"])), (pasta, nascimento))
    return registradas


def tem_homonimo(destino: Path, nome_exibido: str, nascimento: date,
                 compativeis: Callable[[str, str], bool], chave: Callable[[str], str],
                 retrato: Retrato | None = None) -> bool:
    """Se o destino tem pasta de chave idêntica com nascimento registrado diferente."""
    return any(nivel == _HOMONIMO for nivel, _, _ in
               _avaliadas(_do_destino(destino, retrato), nome_exibido, nascimento,
                          compativeis, chave))


def achar_pasta(destino: Path, nome_exibido: str, nascimento: date | None,
                compativeis: Callable[[str, str], bool], chave: Callable[[str], str],
                origem: dict | None = None, no_arquivo: frozenset = frozenset(),
                retrato: Retrato | None = None) -> Path | None:
    """Pasta existente do mesmo paciente no destino, ou `None`.

    Reaproveita com a chave do nome idêntica, ou com variante compatível e
    nascimento igual. Nascimento registrado diferente é homônimo.
    `no_arquivo` são os nascimentos que o arquivo de origem inteiro mostra
    para o nome.

    O nome é de mais de uma pessoa quando o destino tem homônimo, ou quando
    `no_arquivo` tem nascimento diferente do do documento: o documento com
    nascimento só reaproveita então a pasta do mesmo nascimento, nunca uma
    sem nascimento registrado. O documento sem nascimento não separa
    candidatas de chave idêntica com nascimentos diferentes, registrados ou
    em `no_arquivo`: `None`, e quem chama abre pasta nova. Entre essas, a que
    continua sem nascimento e guarda no manifesto a mesma `origem` é a que a
    tentativa anterior desta unidade abriu, e volta a ser a dela.
    """
    retrato = _do_destino(destino, retrato)

    def origem_registrada(caminho: str) -> object:
        try:
            return (retrato.visto(Path(caminho)) or {}).get("origem")
        except (ManifestoInvalido, OSError):
            return None

    achadas = _avaliadas(retrato, nome_exibido, nascimento, compativeis, chave)
    homonimo = any(nivel == _HOMONIMO for nivel, _, _ in achadas)
    achadas = [a for a in achadas if a[0] != _HOMONIMO]
    if nascimento is not None and (homonimo or any(n != nascimento for n in no_arquivo)):
        # O nome é de mais de uma pessoa: a pasta sem nascimento registrado
        # não diz de qual delas é.
        achadas = [a for a in achadas if a[1] is not None]
    if not achadas:
        return None
    melhor = min(nivel for nivel, _, _ in achadas)
    empatadas = [(registrado, caminho) for nivel, registrado, caminho in achadas
                 if nivel == melhor]
    lidos = set(no_arquivo) if nascimento is None else set()
    if melhor == 1 and len({registrado for registrado, _ in empatadas} | lidos) > 1:
        # A desta origem que ganhou nascimento depois foi tomada por uma
        # homônima: já não é a pasta da dúvida.
        aberta = next((caminho for registrado, caminho in empatadas
                       if registrado is None and origem is not None
                       and origem_registrada(caminho) == origem), None)
        if aberta is None:
            evento("pasta_ambigua", candidatas=len(empatadas))
            return None
        evento("pasta_reaberta", candidatas=len(empatadas))
        return Path(aberta)
    evento("pasta_reaproveitada", nivel=melhor, candidatas=len(empatadas))
    return Path(empatadas[0][1])


def _arquivo_feito(destino: Path, sha256: str) -> Path:
    sha256 = str(sha256).lower()
    if not _SHA256.fullmatch(sha256):
        raise ValueError("sha256_invalido")
    return Path(destino) / PASTA_SISTEMA / FEITOS / f"{sha256}.json"


def ja_feito(destino: Path, sha256: str) -> bool:
    """Se o arquivo já foi feito neste destino.

    Só a ausência dá `False`. Erro de acesso sobe: tratar destino oscilando
    como "não feito" recriaria a pasta que alguém moveu ou apagou.
    """
    try:
        os.stat(_arquivo_feito(destino, sha256))
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def marcar_feito(destino: Path, sha256: str, pastas: Sequence[str], es: ES,
                 estacao: str, pid: int) -> None:
    """Registra que pastas receberam o arquivo; soma às já registradas."""
    arquivo = _arquivo_feito(destino, sha256)
    anteriores: list[str] = []
    # Só ausente ou corrompido recomeça do zero. Arquivo que existe e não deu
    # para ler (preso por outro processo) levanta: sobrescrever perderia as
    # pastas já registradas.
    try:
        bruto = arquivo.read_bytes()
    except FileNotFoundError:
        bruto = b""
    try:
        lidos = json.loads(bruto.decode("utf-8-sig")).get("pastas", []) if bruto else []
        anteriores = [p for p in lidos if isinstance(p, str)]
    except (ValueError, AttributeError, TypeError):
        pass
    dados = {"versao_esquema": VERSAO_ESQUEMA, "sha256": arquivo.stem,
             "pastas": sorted(set(anteriores) | {str(p) for p in pastas})}
    gravar(arquivo, _serializar(dados), es, estacao, pid)
