"""Tipos compartilhados do motor.

Dataclasses congeladas: cada etapa devolve um objeto novo
(`dataclasses.replace`) e nunca altera o que recebeu. Caixas são frações da
página (0 a 1), com origem no canto superior esquerdo, depois da rotação.
Os campos `trecho` e `motivos` podem conter texto do documento: vão para o
manifesto do paciente, nunca para o log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

Caixa = tuple[float, float, float, float]

CAMADAS = ("corpo", "cabecalho", "rodape")
TIPOS_DE_LINHA = ("titulo", "secao", "texto", "lista", "legenda", "nota")


@dataclass(frozen=True)
class Linha:
    """Uma linha de texto como o OCR (ou a camada digital) a leu.

    `grupo` é o índice do bloco do layout a que a linha pertence, em ordem de
    leitura: linhas do mesmo parágrafo têm o mesmo grupo. -1 quando não há.
    """
    texto: str
    caixa: Caixa
    camada: str = "corpo"
    tipo: str = "texto"
    confianca: float = 1.0
    grupo: int = -1


@dataclass(frozen=True)
class Tabela:
    celulas: tuple[tuple[str, ...], ...]
    confiancas: tuple[tuple[float, ...], ...]
    caixa: Caixa
    linhas_de_cabecalho: int = 0


Bloco = Linha | Tabela


@dataclass(frozen=True)
class Pagina:
    arquivo_sha256: str
    indice: int
    blocos: tuple[Bloco, ...]
    em_branco: bool = False
    confianca_media: float = 1.0
    rotacao: int = 0


@dataclass(frozen=True)
class Candidato:
    valor_impresso: str
    chave: str
    rotulo: str
    pagina: int
    confianca: float


@dataclass(frozen=True)
class DataExame:
    valor: date | None = None
    precisao: str | None = None
    papel: str | None = None
    trecho: str | None = None


@dataclass(frozen=True)
class Documento:
    id: str
    paginas: tuple[Pagina, ...]
    titulo: str = ""
    instituicao: str | None = None
    paciente: str | None = None
    nascimento: date | None = None
    categoria: int = 5
    data: DataExame = field(default_factory=DataExame)
    cabecalho_original: tuple[str, ...] = ()
    corpo: tuple[Bloco, ...] = ()
    repetido_de: str | None = None
    motivos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resultado:
    arquivo_sha256: str
    documentos: tuple[Documento, ...]
    pacientes: tuple[str, ...]
    multi_paciente: bool
    ilegivel: str | None = None
    paginas_em_branco: tuple[int, ...] = ()


def texto(bloco: Bloco) -> str:
    if isinstance(bloco, Linha):
        return bloco.texto
    return "\n".join(" | ".join(celula for celula in linha) for linha in bloco.celulas)
